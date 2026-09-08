// 对拍验证（两层）：
// 1. 特征级：本包 computeFbank vs torchaudio 参考特征（test/fixtures/ref_fbank_*.npy，
//    由 test/tools/make_reference_fbank.py 生成），逐元素比 max|diff|。参数已按
//    sherpa-onnx 源码固定（25/10ms、povey、dither=0、低频 20Hz、无能量维），
//    唯一待裁决的是 mel 滤波器归一化方式，这里扫三种。
// 2. 声纹级：特征级胜出组合 + global-mean 归一（模型元数据要求）跑 ONNX，
//    与 sherpa_onnx Python 参考声纹比余弦，应 ≈1.0。
// 用法：npm run validate（在 sdk/web 下）
import * as fs from 'node:fs'
import * as path from 'node:path'

import { SpeakerEmbedder } from '../../src/core/index'
import { computeFbank, type FbankOptions } from '../../src/core/index'

const ROOT = path.resolve(__dirname, '..', '..', '..', '..') // 仓库根
const SPEAKER_MODEL = path.join(
  ROOT,
  'app/models/sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k/model.onnx',
)
const SAMPLES = path.join(ROOT, 'app/samples')
const FIXTURES = path.join(__dirname, 'fixtures')
const EMBED_NAMES = ['enroll_target', 'other_clean', 'target_clean'] as const
const FBANK_NAMES = ['enroll_target', 'other_clean'] as const

/** 读 npy（小端 float32 矩阵，行优先）。npy 头 64 字节对齐，数据必 4 字节对齐。 */
function readNpy(file: string): Float32Array {
  const buf = fs.readFileSync(file)
  if (buf[0] !== 0x93 || buf.subarray(1, 6).toString('ascii') !== 'NUMPY') throw new Error('not npy: ' + file)
  const major = buf[6]
  let offset = 8
  let headerLength: number
  if (major <= 1) {
    headerLength = buf.readUInt16LE(offset)
    offset += 2
  } else {
    headerLength = buf.readUInt32LE(offset)
    offset += 4
  }
  const header = buf.toString('ascii', offset, offset + headerLength)
  if (!header.includes("'<f4'")) throw new Error('expect float32 npy: ' + header)
  const data = buf.subarray(offset + headerLength)
  return new Float32Array(data.buffer, data.byteOffset, data.byteLength / 4)
}

function dot(a: Float32Array, b: Float32Array): number {
  let s = 0
  for (let i = 0; i < a.length; i++) s += a[i] * b[i]
  return s
}
function norm(a: Float32Array): number {
  return Math.sqrt(dot(a, a))
}
function cosine(a: Float32Array, b: Float32Array): number {
  return dot(a, b) / (norm(a) * norm(b))
}
function maxAbsDiff(a: Float32Array, b: Float32Array): number {
  let worst = 0
  for (let i = 0; i < a.length; i++) {
    const d = Math.abs(a[i] - b[i])
    if (d > worst) worst = d
  }
  return worst
}

async function main(): Promise<void> {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const sherpa = require('sherpa-onnx')

  function loadWav(name: string): Float32Array {
    const wave = sherpa.readWave(path.join(SAMPLES, `${name}.wav`))
    if (wave.sampleRate !== 16000) throw new Error(`expect 16k, got ${wave.sampleRate}`)
    return wave.samples
  }

  // ── 第一层：特征级逐元素对拍 ────────────────────────────────────────────
  console.log('== 特征级对拍（computeFbank vs torchaudio kaldi.fbank）==')
  console.log('melNorm | enroll_target max|diff|  other_clean max|diff|')
  const normChoices = ['raw', 'sum', 'slope'] as const
  const featureScores: Array<{ melNorm: (typeof normChoices)[number]; worst: number }> = []
  for (const melNorm of normChoices) {
    const opts: FbankOptions = { melNorm }
    const diffs = FBANK_NAMES.map((name) => {
      const mine = computeFbank(loadWav(name), opts)
      const ref = readNpy(path.join(FIXTURES, `ref_fbank_${name}.npy`))
      if (mine.length !== ref.length) {
        throw new Error(`${name}: 帧数不一致 mine=${mine.length} ref=${ref.length}`)
      }
      return maxAbsDiff(mine, ref)
    })
    const worst = Math.max(...diffs)
    featureScores.push({ melNorm, worst })
    console.log(`${melNorm.padEnd(7)} | ${diffs[0].toExponential(3).padEnd(20)} ${diffs[1].toExponential(3)}`)
  }
  featureScores.sort((a, b) => a.worst - b.worst)
  const winner = featureScores[0]
  // 浮点累加顺序差异下，逐元素误差应在 1e-3 量级以内
  if (winner.worst > 2e-3) {
    throw new Error(
      `特征级对拍失败：最好的 melNorm=${winner.melNorm} 也有 max|diff|=${winner.worst.toExponential(3)}，` +
        '需对照 torchaudio.compliance.kaldi 源码核对实现',
    )
  }
  console.log(`胜出 melNorm=${winner.melNorm}（max|diff|=${winner.worst.toExponential(3)}，浮点抖动范围）\n`)

  // ── 第二层：声纹级余弦对拍（global-mean + ONNX）─────────────────────────
  console.log('== 声纹级对拍（TS embedder vs sherpa_onnx Python 参考声纹）==')
  const embedder = await SpeakerEmbedder.create(SPEAKER_MODEL) // 默认 global-mean
  const sims: number[] = []
  for (const name of EMBED_NAMES) {
    const t0 = Date.now()
    const embedding = await embedder.embed(loadWav(name), { melNorm: winner.melNorm })
    const ms = Date.now() - t0
    const ref = readNpy(path.join(FIXTURES, `ref_emb_${name}.npy`))
    const cos = cosine(embedding, ref)
    sims.push(cos)
    console.log(`${name.padEnd(14)} cos=${cos.toFixed(6)}  (${ms} ms / ${(loadWav(name).length / 16000).toFixed(1)} s 音频)`)
  }
  const worst = Math.min(...sims)
  if (worst >= 0.999) {
    console.log(`\nPASS：两层对拍全部通过（声纹最差 cos=${worst.toFixed(6)}）`)
  } else {
    throw new Error(`声纹级对拍失败：最差 cos=${worst.toFixed(6)}（应 ≥0.999）`)
  }
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
