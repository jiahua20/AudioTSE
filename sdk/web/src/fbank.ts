// kaldi 风格 fbank 前端，对齐 sherpa-onnx（kaldi-native-fbank）给 3D-Speaker 声纹
// 模型用的特征配置：16 kHz、25 ms 窗 / 10 ms 移、povey 窗、预加重 0.97、去直流、
// 512 点 FFT、80 个对数 mel 滤波器（kaldi mel 标度）。
// 逐位一致性由 test/validate-embedding.ts 对拍 Python 参考声纹验证。

export interface FbankOptions {
  sampleRate?: number
  frameLengthMs?: number
  frameShiftMs?: number
  numMelBins?: number
  preemphCoeff?: number
  removeDcOffset?: boolean
  /** 最低 mel 滤波频率（sherpa 默认 20 Hz） */
  melLowHz?: number
  /**
   * 最高 mel 滤波频率（Hz）。负值 = Nyquist + highFreq（sherpa 语义，默认 -400 → 7600 Hz）。
   * 注意 kaldi/torchaudio 原生默认是 0（即 Nyquist），sherpa-onnx 改成了 -400。
   */
  highFreq?: number
  /**
   * false（sherpa 默认）：帧居中对齐、边缘反射填充、帧数 = (N + shift/2) / shift；
   * true（kaldi 默认）：帧首对齐、不填充、帧数 = 1 + (N - frameLen) / shift。
   */
  snipEdges?: boolean
  /** mel 滤波器归一化方式（对拍裁决为 raw，kaldi 原样三角权重不归一） */
  melNorm?: 'raw' | 'sum' | 'slope'
  /** 是否在特征前拼接原始能量（kaldi use_energy；sherpa fbank 未用） */
  appendEnergy?: boolean
}

const FLT_EPSILON = 1.1920929e-7 // kaldi ApplyFloor(FLT_EPSILON)

const melOf = (hz: number): number => 1127.0 * Math.log(1.0 + hz / 700.0)
const melInv = (m: number): number => 700.0 * (Math.exp(m / 1127.0) - 1.0)

function nextPow2(n: number): number {
  let p = 1
  while (p < n) p <<= 1
  return p
}

/** 单帧实数 FFT 的功率谱（|X_k|^2）。输入长度为 2 的幂。 */
function powerSpectrum(frame: Float64Array): Float64Array {
  const n = frame.length
  const re = Float64Array.from(frame)
  const im = new Float64Array(n)

  // 位反转排列
  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1
    for (; j & bit; bit >>= 1) j ^= bit
    j ^= bit
    if (i < j) {
      const tr = re[i]; re[i] = re[j]; re[j] = tr
      const ti = im[i]; im[i] = im[j]; im[j] = ti
    }
  }

  // 迭代 radix-2 蝶形
  for (let len = 2; len <= n; len <<= 1) {
    const ang = (-2 * Math.PI) / len
    const half = len >> 1
    for (let i = 0; i < n; i += len) {
      for (let k = 0; k < half; k++) {
        const wr = Math.cos(ang * k)
        const wi = Math.sin(ang * k)
        const ur = re[i + k], ui = im[i + k]
        const xr = re[i + k + half], xi = im[i + k + half]
        const vr = xr * wr - xi * wi
        const vi = xr * wi + xi * wr
        re[i + k] = ur + vr; im[i + k] = ui + vi
        re[i + k + half] = ur - vr; im[i + k + half] = ui - vi
      }
    }
  }

  const bins = new Float64Array(n / 2 + 1)
  for (let k = 0; k <= n / 2; k++) bins[k] = re[k] * re[k] + im[k] * im[k]
  return bins
}

/**
 * 计算 fbank 特征矩阵（行优先，每帧 numMelBins(+能量) 维）。
 * snip_edges=true：帧数 = 1 + floor((N - frameLen) / frameShift)。
 */
export function computeFbank(samples: Float32Array, opts: FbankOptions = {}): Float32Array {
  const sr = opts.sampleRate ?? 16000
  const frameLen = Math.round((sr * (opts.frameLengthMs ?? 25)) / 1000) // 400
  const frameShift = Math.round((sr * (opts.frameShiftMs ?? 10)) / 1000) // 160
  const numMel = opts.numMelBins ?? 80
  const preemph = opts.preemphCoeff ?? 0.97
  const removeDc = opts.removeDcOffset ?? true
  const melLow = opts.melLowHz ?? 20
  const highFreq = opts.highFreq ?? -400
  const effectiveHigh = highFreq >= 0 ? highFreq : sr / 2 + highFreq // sherpa：负值从 Nyquist 回退
  const snipEdges = opts.snipEdges ?? false
  const melNorm = opts.melNorm ?? 'raw'
  const appendEnergy = opts.appendEnergy ?? false
  const width = numMel + (appendEnergy ? 1 : 0)

  // 帧数（kaldi NumFrames 两种模式）
  const numFrames = snipEdges
    ? samples.length < frameLen
      ? 0
      : 1 + Math.floor((samples.length - frameLen) / frameShift)
    : Math.floor((samples.length + frameShift / 2) / frameShift)

  /** snipEdges=false 时的反射取样本（kaldi ExtractWindow 语义，可多重折叠）。 */
  const sampleAt = (index: number): number => {
    let i = index
    while (i < 0 || i >= samples.length) {
      i = i < 0 ? -i - 1 : 2 * samples.length - 1 - i
    }
    return samples[i]
  }

  const fftSize = nextPow2(frameLen) // 400 -> 512
  const numFftBins = fftSize / 2 + 1 // 257
  const binHz = sr / fftSize

  // povey 窗：(0.5 - 0.5cos(2πn/(N-1)))^0.85
  const window = new Float64Array(frameLen)
  for (let i = 0; i < frameLen; i++) {
    window[i] = Math.pow(0.5 - 0.5 * Math.cos((2 * Math.PI * i) / (frameLen - 1)), 0.85)
  }

  // kaldi mel 滤波器组：mel 域等间距取点，三角滤波也在 mel 域线性插值
  // （kaldi 把每个 FFT bin 频率转成 mel 后与 left/center/right 比较，不是 Hz 域）
  const melLo = melOf(melLow)
  const melHi = melOf(effectiveHigh)
  const melPoints = new Float64Array(numMel + 2)
  for (let i = 0; i < numMel + 2; i++) {
    melPoints[i] = melLo + ((melHi - melLo) * i) / (numMel + 1)
  }
  const melFilters: Array<{ first: number; weights: Float64Array }> = []
  const binMel = new Float64Array(numFftBins)
  for (let j = 0; j < numFftBins; j++) binMel[j] = melOf(j * binHz)
  for (let m = 0; m < numMel; m++) {
    const left = melPoints[m], center = melPoints[m + 1], right = melPoints[m + 2]
    // bin 覆盖范围用 Hz 域边界（mel 点反变换回 Hz），三角插值在 mel 域进行
    const leftHz = melInv(left), rightHz = melInv(right)
    const first = Math.min(numFftBins - 1, Math.max(0, Math.ceil(leftHz / binHz)))
    const last = Math.max(first - 1, Math.min(numFftBins - 1, Math.floor(rightHz / binHz)))
    const weights = new Float64Array(last - first + 1)
    let sum = 0
    for (let j = first; j <= last; j++) {
      const mel = binMel[j]
      const w =
        mel >= left && mel < center
          ? (mel - left) / (center - left)
          : mel >= center && mel < right
            ? (right - mel) / (right - center)
            : 0
      weights[j - first] = w
      sum += w
    }
    if (melNorm === 'sum' && sum > 0) {
      for (let i = 0; i < weights.length; i++) weights[i] /= sum
    } else if (melNorm === 'slope') {
      const scale = 1.0 / (center - left)
      for (let i = 0; i < weights.length; i++) weights[i] *= scale
    }
    melFilters.push({ first, weights })
  }

  const out = new Float32Array(numFrames * width)
  const frame = new Float64Array(fftSize) // 零填充到 FFT 尺寸

  for (let t = 0; t < numFrames; t++) {
    // 帧起点：snipEdges=true 首对齐；false 居中对齐（kaldi FirstSampleOfFrame，整除）
    const offset = snipEdges
      ? t * frameShift
      : t * frameShift + Math.floor(frameShift / 2) - Math.floor(frameLen / 2)

    // 取窗内样本（snipEdges=false 时边缘反射）
    let mean = 0
    for (let i = 0; i < frameLen; i++) {
      const v = sampleAt(offset + i)
      frame[i] = v
      mean += v
    }
    mean /= frameLen

    let rawEnergy = 0
    for (let i = 0; i < frameLen; i++) {
      let v = frame[i]
      if (removeDc) v -= mean
      if (appendEnergy) rawEnergy += v * v
      frame[i] = v
    }

    // 预加重（窗内独立，kaldi ProcessWindow 语义）
    for (let i = frameLen - 1; i > 0; i--) frame[i] -= preemph * frame[i - 1]
    frame[0] -= preemph * frame[0]

    // 加窗
    for (let i = 0; i < frameLen; i++) frame[i] *= window[i]
    for (let i = frameLen; i < fftSize; i++) frame[i] = 0

    const power = powerSpectrum(frame)

    // mel 滤波 + 取对数
    const row = t * width
    for (let m = 0; m < numMel; m++) {
      const { first, weights } = melFilters[m]
      let e = 0
      for (let k = 0; k < weights.length; k++) e += weights[k] * power[first + k]
      out[row + m] = Math.log(Math.max(e, FLT_EPSILON))
    }
    if (appendEnergy) out[row + numMel] = Math.log(Math.max(rawEnergy, FLT_EPSILON))
  }

  return out
}
