// 声纹门控完整版：VAD 切段 + 声纹判定。声纹注册/比对/短注册阈值补偿全部复用
// 同包 ../core 的 VoiceFilter，本类只负责「VAD 切段 → 逐段喂给 VoiceFilter」
// 以及注册音频的静音剥离。
// 与 app 后端 speaker_gate.py 同一套模型和判定语义；区别是 v0 按段整段判定
// （app 另有段内 0.6s 预热的增量判定和接受前缀补发，属于 ASR 联动的实时性优化，
// SDK 后续版本再做）。
import { VoiceFilter, type EnrollResult } from '../core/voice-filter'
import { createSileroVad, type SileroVadOptions, type Vad } from './vad'

export const SAMPLE_RATE = 16000

export interface SpeakerGateConfig {
  /** silero_vad.onnx 路径 */
  vadModel: string
  /** 3D-Speaker ER2Net onnx 路径 */
  speakerModel: string
  /** 放行所需的相似度阈值基准，默认 0.5（与 app 后端一致） */
  threshold?: number
  /**
   * 注册的净语音目标（秒）：流式注册累计到该时长即视为足够，说完自动完成。
   * 默认 1.2 秒 ≈ 四~六个汉字的正常语速。
   */
  enrollTargetSpeechSeconds?: number
  /** 注册允许的最少净语音（秒），不足则 finishEnroll 抛错。默认 0.6 */
  enrollMinSpeechSeconds?: number
  /** 短注册阈值补偿系数（透传给 VoiceFilter），默认 0.7，设 1 禁用 */
  shortEnrollThresholdFactor?: number
  /** VAD 参数覆盖，默认同 app 后端（同时作用于主 VAD 与注册 VAD） */
  vad?: Partial<SileroVadOptions>
}

export interface GateSegmentEvent {
  type: 'segment'
  /** 段起点在整条流中的采样序号 */
  start: number
  /** 段时长（秒） */
  durationSeconds: number
  /** 与注册声纹的余弦相似度；未注册时为 null（全放行） */
  similarity: number | null
  accepted: boolean
  /** 段内音频（float32 [-1,1] @16k），宿主可自行播放/送 ASR */
  samples: Float32Array
}

/** 流式注册进度：已累计的净语音时长、是否已达目标。 */
export interface EnrollProgress {
  speechSeconds: number
  enough: boolean
}

export type { EnrollResult }

function concatFloat32(parts: Float32Array[]): Float32Array {
  const total = parts.reduce((n, p) => n + p.length, 0)
  const out = new Float32Array(total)
  let offset = 0
  for (const p of parts) {
    out.set(p, offset)
    offset += p.length
  }
  return out
}

export class SpeakerGate {
  private constructor(
    /** 放行所需的相似度阈值基准 */
    readonly threshold: number,
    private readonly vadModelPath: string,
    private readonly vadOverrides: Partial<SileroVadOptions>,
    private readonly enrollTarget: number,
    private readonly enrollMin: number,
    private readonly vad: Vad,
    private readonly filter: VoiceFilter,
    private enrollVad: Vad | null,
    private enrollChunks: Float32Array[],
    private enrollSpeechSamples: number,
  ) {}

  static async create(config: SpeakerGateConfig): Promise<SpeakerGate> {
    const vad = createSileroVad(config.vadModel, config.vad)
    const filter = await VoiceFilter.create({
      speakerModel: config.speakerModel,
      threshold: config.threshold,
      shortEnrollThresholdFactor: config.shortEnrollThresholdFactor,
    })
    return new SpeakerGate(
      config.threshold ?? 0.5,
      config.vadModel,
      config.vad ?? {},
      config.enrollTargetSpeechSeconds ?? 1.2,
      config.enrollMinSpeechSeconds ?? 0.6,
      vad,
      filter,
      null,
      [],
      0,
    )
  }

  /** 是否已有注册声纹（未注册时所有段放行）。 */
  get enrolled(): boolean {
    return this.filter.enrolled
  }

  /** 当前实际生效的放行阈值（短注册补偿后）。 */
  get effectiveThreshold(): number {
    return this.filter.effectiveThreshold
  }

  // ── 批式注册（一次性给整段音频）────────────────────────────
  /**
   * 便捷注册：整段音频内部剥静音后提声纹（静音不参与特征统计，短注册更稳）。
   * 返回实际使用的净语音时长；未检测到足够语音时抛错。
   */
  async enroll(samples: Float32Array): Promise<EnrollResult> {
    this.beginEnroll()
    await this.enrollChunk(samples)
    return this.finishEnroll()
  }

  // ── 流式注册（边说边收，够量自动完成）──────────────────────
  /** 开始一次流式注册：复位累计状态。 */
  beginEnroll(): void {
    this.enrollVad?.free()
    this.enrollVad = createSileroVad(this.vadModelPath, this.vadOverrides)
    this.enrollChunks = []
    this.enrollSpeechSamples = 0
  }

  /**
   * 喂入一段注册音频（可与 accept 并行使用，注册走独立 VAD 不影响主流）。
   * 返回当前净语音进度；enough=true 表示已达目标，宿主应停麦并 finishEnroll()。
   */
  async enrollChunk(samples: Float32Array): Promise<EnrollProgress> {
    if (!this.enrollVad) this.beginEnroll()
    this.enrollVad!.acceptWaveform(samples)
    this.collectEnrollSegments()
    const speechSeconds = this.enrollSpeechSamples / SAMPLE_RATE
    return { speechSeconds, enough: speechSeconds >= this.enrollTarget }
  }

  /**
   * 结束注册（冲出尾部语音段后）提声纹。
   * 净语音低于下限时抛错（调用方提示「没听清/再说一句」），注册状态保持未完成。
   */
  async finishEnroll(): Promise<EnrollResult> {
    if (!this.enrollVad) throw new Error('注册未开始（先 beginEnroll）')
    this.enrollVad.flush()
    this.collectEnrollSegments()
    this.enrollVad.free()
    this.enrollVad = null
    const speechSeconds = this.enrollSpeechSamples / SAMPLE_RATE
    if (speechSeconds < this.enrollMin) {
      throw new Error(`净语音不足：${speechSeconds.toFixed(2)}s < ${this.enrollMin}s，请再说一句`)
    }
    // VoiceFilter.enroll 负责声纹提取与短注册阈值补偿（输入已是剥好静音的纯语音）
    return this.filter.enroll(concatFloat32(this.enrollChunks))
  }

  /** 喂入一段流式音频，返回自此完结的语音段及其门控判定。 */
  async accept(samples: Float32Array): Promise<GateSegmentEvent[]> {
    this.vad.acceptWaveform(samples)
    return this.drain()
  }

  /** 流结束：冲出尾部未完结的语音段并判定。 */
  async flush(): Promise<GateSegmentEvent[]> {
    this.vad.flush()
    return this.drain()
  }

  /** 复位 VAD 与段状态（注册声纹保留）。 */
  reset(): void {
    this.vad.reset()
  }

  dispose(): void {
    this.vad.free()
    this.enrollVad?.free()
    this.enrollVad = null
  }

  private collectEnrollSegments(): void {
    while (!this.enrollVad!.isEmpty()) {
      const segment = this.enrollVad!.front()
      this.enrollVad!.pop()
      this.enrollChunks.push(segment.samples)
      this.enrollSpeechSamples += segment.samples.length
    }
  }

  private async drain(): Promise<GateSegmentEvent[]> {
    const events: GateSegmentEvent[] = []
    while (!this.vad.isEmpty()) {
      const segment = this.vad.front()
      this.vad.pop()
      const judge = await this.filter.judge(segment.samples)
      events.push({
        type: 'segment',
        start: segment.start,
        durationSeconds: judge.durationSeconds,
        similarity: this.filter.enrolled ? judge.similarity : null,
        accepted: judge.accepted,
        samples: segment.samples,
      })
    }
    return events
  }
}
