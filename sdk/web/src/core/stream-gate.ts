// 窗口门控（流式，无 VAD）：给「ASR 边收边转写」的场景——不等整句说完，
// 每积累 hopMs 新音频就判一次，过则该块立即放行送 ASR（打字机效果）。
//   sg.push(chunk500ms) → { accepted, similarity, score, silent }
//   accepted 为 true 的块直接转发 ASR，判定与转发同节奏，不引入整句级等待。
//
// 设计依据（实测，样例音频 + ER2Net，注册 5s）：
//   500ms 裸窗    主讲人 0.20~0.57（中位 0.41），陌生人 ≤0.08
//   1s 滑窗       主讲人 0.35~0.62（中位 0.52），陌生人 ≤0.09
// 两个结论：① 窗口级判定可行，区间无重叠；② 整句判定的阈值 0.5 不能直接
// 用于短窗（主讲人四分之三的 500ms 窗相似度低于 0.5），窗口模式必须用独立
// 低阈值，默认 0.25 落在两类间隙中央。
//
// 判定用「最近 contextMs 音频」（滑窗）：历史块只作判定上下文，不重复发送；
// 出结果的节奏仍是每 hopMs 一次，相比裸窗不增加延迟，但相似度更稳（见上）。
// EMA 平滑吸收单窗抖动（半句话的声纹会漂）：score = a×本窗 + (1-a)×历史，
// accepted = score ≥ threshold，说话人切换约 1~2 窗内翻转。
import { VoiceFilter, SAMPLE_RATE } from './voice-filter'

export interface StreamGateConfig {
  /** 声纹模型路径（与 VoiceFilter 同一份 ER2Net onnx） */
  speakerModel: string
  /**
   * 窗口判定阈值，默认 0.25。注意：短窗相似度整体低于整句（实测中位 0.41~0.52），
   * 整句判定的 0.5 在窗口模式下会把本人大量窗口误拒，两种粒度的阈值不可混用。
   */
  threshold?: number
  /** 判定上下文长度（毫秒）：每次判定看最近这么长的音频，默认 1000 */
  contextMs?: number
  /** 判定步长（毫秒）：每积累这么长新音频判一次，默认 500（与喂入块大小一致） */
  hopMs?: number
  /** EMA 平滑系数（0~1）：越大越跟手、越小越稳；0 关闭平滑，默认 0.5 */
  smoothing?: number
  /** 静音块 RMS 门限：低于它的块跳过推理（静音的声纹是乱数），默认 0.01 */
  silenceRms?: number
}

/** 一个喂入块的门控判定。 */
export interface StreamGateVerdict {
  /** 该块是否放行（送 ASR）。未注册时放行（与 VoiceFilter「未注册全放行」一致）；静音块拒绝 */
  accepted: boolean
  /** 本窗原始相似度；未注册或静音（未推理）时为 null */
  similarity: number | null
  /** 平滑后的判定分（与 threshold 比较）；未推理时为 null */
  score: number | null
  /** 该块是否被判为近静音（跳过了推理，无需送 ASR） */
  silent: boolean
}

function rms(x: Float32Array): number {
  let acc = 0
  for (const v of x) acc += v * v
  return Math.sqrt(acc / x.length)
}

export class StreamGate {
  private constructor(
    private readonly filter: VoiceFilter,
    private readonly threshold: number,
    private readonly contextSamples: number,
    private readonly hopSamples: number,
    private readonly smoothing: number,
    private readonly silenceRms: number,
    private buf: Float32Array = new Float32Array(0),
    private pending: number = 0,
    private score: number | null = null,
    private lastSimilarity: number | null = null,
  ) {}

  /** 加载声纹模型并创建实例（模型加载约 0.5s，应用启动时创建一次）。 */
  static async create(config: StreamGateConfig): Promise<StreamGate> {
    const filter = await VoiceFilter.create({ speakerModel: config.speakerModel })
    return new StreamGate(
      filter,
      config.threshold ?? 0.25,
      Math.round(((config.contextMs ?? 1000) / 1000) * SAMPLE_RATE),
      Math.round(((config.hopMs ?? 500) / 1000) * SAMPLE_RATE),
      config.smoothing ?? 0.5,
      config.silenceRms ?? 0.01,
    )
  }

  /** 是否已注册（未注册时 push 全放行）。 */
  get enrolled(): boolean {
    return this.filter.enrolled
  }

  /** 窗口判定阈值（StreamGate 自己的阈值语义，与整句判定的 0.5 无关）。 */
  get effectiveThreshold(): number {
    return this.threshold
  }

  /** 整段注册（唤醒词整段，建议净语音 ≥1s）；注册后窗口判定立即生效。 */
  async enroll(samples: Float32Array): Promise<{ speechSeconds: number }> {
    const result = await this.filter.enroll(samples)
    this.score = null // 换了声纹，平滑状态作废重来
    return result
  }

  /**
   * 喂入一块音频（float32 [-1,1] @16k，典型 500ms），立刻返回该块的放行判定：
   * accepted=true 即可转发 ASR。块大小可与 hopMs 不同（内部按新到样本量计步），
   * 极端大于 hop 的块只做一次判定，判定结论对该块整体生效。
   */
  async push(chunk: Float32Array): Promise<StreamGateVerdict> {
    const silent = rms(chunk) < this.silenceRms
    // 追加进滑窗缓冲，只保留最近 contextMs；静音也照常滚动（上下文跨越停顿）
    const merged = new Float32Array(this.buf.length + chunk.length)
    merged.set(this.buf)
    merged.set(chunk, this.buf.length)
    this.buf =
      merged.length > this.contextSamples ? merged.subarray(merged.length - this.contextSamples) : merged
    this.pending += chunk.length

    if (silent) {
      // 静音块：不推理（结果无意义）、不动平滑状态；pending 继续累计，
      // 说话恢复后下一个块立刻补一次判定
      return { accepted: false, similarity: null, score: this.score, silent: true }
    }
    if (!this.filter.enrolled) {
      this.pending = 0
      return { accepted: true, similarity: null, score: null, silent: false }
    }
    if (this.pending < this.hopSamples) {
      // 凑步中：沿用最近一次判定结论，保证放行决策连续
      const accepted = this.score === null ? true : this.score >= this.threshold
      return { accepted, similarity: this.lastSimilarity, score: this.score, silent: false }
    }
    this.pending = 0
    const { similarity } = await this.filter.judge(this.buf)
    this.lastSimilarity = similarity
    this.score =
      this.smoothing > 0 && this.score !== null
        ? this.smoothing * similarity + (1 - this.smoothing) * this.score
        : similarity
    return { accepted: this.score >= this.threshold, similarity, score: this.score, silent: false }
  }

  async dispose(): Promise<void> {
    await this.filter.dispose()
  }
}
