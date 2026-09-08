// 声纹过滤核心（无 VAD 版，内网场景）：外部 VAD 已把语音切好成段，
// 这里只做「注册声纹 → 每段判定/过滤」：
//   enroll(samples)  注册目标说话人（一段完整语音）
//   judge(samples)   判定一段语音是否目标说话人（返回相似度 + accepted）
//   filter(samples)  便捷过滤：目标说话人的语音原样返回，否则返回 null
// 特征前端与推理和 full 版完全同源（fbank 对齐 sherpa-onnx、ER2Net ONNX、
// 声纹与 Python 引擎对拍 cos≥0.9999），只是不含 VAD。
import { SpeakerEmbedder } from './embedder'

export const SAMPLE_RATE = 16000

/** 净语音低于该秒数时启用短注册阈值补偿 */
const SHORT_ENROLL_BOUNDARY_SECONDS = 1.5

export interface VoiceFilterConfig {
  /** 3D-Speaker ER2Net onnx 路径 */
  speakerModel: string
  /** 放行所需的相似度阈值基准，默认 0.5（与 app 后端一致） */
  threshold?: number
  /**
   * 短注册阈值补偿系数：注册语音 < 1.5s 时实际阈值 = threshold × 该系数（默认 0.7）。
   * 短注册的目标段相似度整体下移（实测 1s 注册下限 ~0.48，5s 注册 ~0.59），阈值
   * 不降会误拒贴线目标段。设为 1 可禁用自适应。
   */
  shortEnrollThresholdFactor?: number
}

/** 一段语音的判定结果。 */
export interface JudgeResult {
  /** 与注册声纹的余弦相似度；未注册时为 1.0 */
  similarity: number
  /** 是否目标说话人；未注册时恒为 true */
  accepted: boolean
  /** 输入语音时长（秒） */
  durationSeconds: number
}

/** 注册结果。 */
export interface EnrollResult {
  /** 用于声纹的语音时长（秒）。core 版不做静音剥离，等于输入时长 */
  speechSeconds: number
}

/** 两个向量的余弦相似度（与 app 后端 SpeakerEmbedder.cosine 同实现）。 */
export function cosine(left: Float32Array, right: Float32Array): number {
  let dot = 0, ln = 0, rn = 0
  for (let i = 0; i < left.length; i++) {
    dot += left[i] * right[i]
    ln += left[i] * left[i]
    rn += right[i] * right[i]
  }
  const denominator = Math.sqrt(ln) * Math.sqrt(rn)
  return denominator > 1e-8 ? dot / denominator : 0.0
}

export class VoiceFilter {
  private constructor(
    /** 放行所需的相似度阈值基准 */
    readonly threshold: number,
    private readonly shortEnrollFactor: number,
    private readonly embedder: SpeakerEmbedder,
    private enrollment: Float32Array | null,
    private activeThreshold: number,
  ) {}

  /** 加载声纹模型（ER2Net ONNX，CPU 推理）。 */
  static async create(config: VoiceFilterConfig): Promise<VoiceFilter> {
    const embedder = await SpeakerEmbedder.create(config.speakerModel)
    const threshold = config.threshold ?? 0.5
    return new VoiceFilter(threshold, config.shortEnrollThresholdFactor ?? 0.7, embedder, null, threshold)
  }

  /** 是否已注册（未注册时 judge 全部 accepted）。 */
  get enrolled(): boolean {
    return this.enrollment !== null
  }

  /** 当前实际生效的放行阈值（短注册补偿后，未注册时等于基准阈值）。 */
  get effectiveThreshold(): number {
    return this.activeThreshold
  }

  /**
   * 注册目标说话人：一段完整语音（float32 [-1,1] @16k，外部 VAD 已切好，
   * 建议净语音 ≥1s/四个字；<1.5s 自动启用阈值补偿）。
   */
  async enroll(samples: Float32Array): Promise<EnrollResult> {
    const embedding = await this.embedder.embed(samples)
    let norm = 0
    for (const v of embedding) norm += v * v
    norm = Math.sqrt(norm) + 1e-8
    const normalized = new Float32Array(embedding.length)
    for (let i = 0; i < embedding.length; i++) normalized[i] = embedding[i] / norm
    this.enrollment = normalized
    const speechSeconds = samples.length / SAMPLE_RATE
    this.activeThreshold =
      speechSeconds < SHORT_ENROLL_BOUNDARY_SECONDS ? this.threshold * this.shortEnrollFactor : this.threshold
    return { speechSeconds }
  }

  /** 判定一段语音是否目标说话人。 */
  async judge(samples: Float32Array): Promise<JudgeResult> {
    const durationSeconds = samples.length / SAMPLE_RATE
    if (this.enrollment === null) {
      return { similarity: 1.0, accepted: true, durationSeconds }
    }
    const similarity = cosine(await this.embedder.embed(samples), this.enrollment)
    return { similarity, accepted: similarity >= this.activeThreshold, durationSeconds }
  }

  /**
   * 便捷过滤：目标说话人的语音**原样返回**（同一引用，不拷贝），非目标返回 null；
   * 未注册时原样返回（全放行）。适合「把主讲人的语音挑出来回传」的内网场景。
   */
  async filter(samples: Float32Array): Promise<Float32Array | null> {
    const result = await this.judge(samples)
    return result.accepted ? samples : null
  }

  async dispose(): Promise<void> {
    await this.embedder.free()
  }
}
