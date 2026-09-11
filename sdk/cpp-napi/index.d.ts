// 类型定义与 @audiotse/gate（sdk/web）的主入口 / core 入口同名接口一一对应，
// 宿主代码 import type 切换后端时无需改动。
export declare const SAMPLE_RATE = 16000

/** VAD 参数（与 app 后端 / sdk/web 默认值一致） */
export interface SileroVadOptions {
  threshold: number
  minSilenceDuration: number
  minSpeechDuration: number
  maxSpeechDuration: number
}

export interface SpeakerGateConfig {
  /** silero_vad.onnx 路径（必填） */
  vadModel: string
  /** 3D-Speaker ER2Net onnx 路径（必填） */
  speakerModel: string
  /** 放行所需的相似度阈值基准，默认 0.5 */
  threshold?: number
  /** 注册的净语音目标（秒），够了自动完成。默认 1.2 */
  enrollTargetSpeechSeconds?: number
  /** 注册允许的最少净语音（秒），不足则 finishEnroll 抛错。默认 0.6 */
  enrollMinSpeechSeconds?: number
  /** 短注册阈值补偿系数，默认 0.7，设 1 禁用 */
  shortEnrollThresholdFactor?: number
  /** VAD 参数覆盖（同时作用于主 VAD 与注册 VAD） */
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

export interface EnrollResult {
  speechSeconds: number
}

/** core 版配置（无 VAD，内网场景），与 sdk/web 的 VoiceFilterConfig 同名同义 */
export interface VoiceFilterConfig {
  /** 3D-Speaker ER2Net onnx 路径（必填） */
  speakerModel: string
  /** 放行所需的相似度阈值基准，默认 0.5 */
  threshold?: number
  /** 短注册阈值补偿系数，默认 0.7，设 1 禁用 */
  shortEnrollThresholdFactor?: number
}

/** 一段语音的判定结果（core 版） */
export interface JudgeResult {
  /** 与注册声纹的余弦相似度；未注册时为 1.0 */
  similarity: number
  /** 是否目标说话人；未注册时恒为 true */
  accepted: boolean
  /** 输入语音时长（秒） */
  durationSeconds: number
}

export declare class SpeakerGate {
  /** 加载模型并创建实例（约 0.5s，在工作线程执行不阻塞 JS）。 */
  static create(config: SpeakerGateConfig): Promise<SpeakerGate>
  /** 是否已有注册声纹（未注册时所有段放行）。 */
  get enrolled(): boolean
  /** 当前实际生效的放行阈值（短注册补偿后）。 */
  get effectiveThreshold(): number
  /** 开始一次流式注册：复位累计状态。 */
  beginEnroll(): void
  /** 便捷注册：整段音频内部剥静音后提声纹（= begin + chunk + finish）。 */
  enroll(samples: Float32Array): Promise<EnrollResult>
  /** 喂入一段注册音频，返回净语音进度；enough=true 表示已达目标。 */
  enrollChunk(samples: Float32Array): Promise<EnrollProgress>
  /** 结束注册并提声纹；净语音低于下限时 reject（注册状态保持未完成）。 */
  finishEnroll(): Promise<EnrollResult>
  /** 喂入一段流式音频，返回自此完结的语音段及其门控判定。 */
  accept(samples: Float32Array): Promise<GateSegmentEvent[]>
  /** 流结束：冲出尾部未完结的语音段并判定。 */
  flush(): Promise<GateSegmentEvent[]>
  /** 复位 VAD 与段状态（注册声纹保留）。 */
  reset(): void
  /** 释放原生资源（排到队列末尾执行）。 */
  dispose(): void
}

/** core 版（无 VAD）：外部 VAD 已切好段，只做「注册 → 逐段判定/过滤」。 */
export declare class VoiceFilter {
  /** 加载声纹模型并创建实例（约 0.5s，在工作线程执行不阻塞 JS）。 */
  static create(config: VoiceFilterConfig): Promise<VoiceFilter>
  /** 是否已注册（未注册时 judge 全部 accepted）。 */
  get enrolled(): boolean
  /** 当前实际生效的放行阈值（短注册补偿后）。 */
  get effectiveThreshold(): number
  /** 注册目标说话人：一段完整语音（float32 [-1,1] @16k）。 */
  enroll(samples: Float32Array): Promise<EnrollResult>
  /** 判定一段语音是否目标说话人。 */
  judge(samples: Float32Array): Promise<JudgeResult>
  /** 便捷过滤：目标说话人语音原样返回（同引用），非目标返回 null；未注册全放行。 */
  filter(samples: Float32Array): Promise<Float32Array | null>
  /** 释放原生资源（排到队列末尾执行）。 */
  dispose(): void
}

/**
 * 窗口门控配置（流式，无 VAD），与 sdk/web 的 StreamGateConfig 同名同义：
 * 给「ASR 边收边转写」的场景——每积累 hopMs 新音频判一次，过则该块立即放行送 ASR。
 */
export interface StreamGateConfig {
  /** 3D-Speaker ER2Net onnx 路径（必填） */
  speakerModel: string
  /**
   * 窗口判定阈值，默认 0.25。短窗相似度整体低于整句（实测 500ms 裸窗主讲人中位
   * 0.41、1s 滑窗 0.52，陌生人 ≤0.09），整句判定的 0.5 在窗口模式下会大量误拒
   * 本人，两种粒度的阈值不可混用。
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

/** 一个喂入块的窗口门控判定。 */
export interface StreamGateVerdict {
  /** 该块是否放行（送 ASR）。未注册时放行；静音块拒绝 */
  accepted: boolean
  /** 本窗原始相似度；未注册或静音（未推理）时为 null */
  similarity: number | null
  /** 平滑后的判定分（与 threshold 比较）；未推理时为 null */
  score: number | null
  /** 该块是否被判为近静音（跳过了推理，无需送 ASR） */
  silent: boolean
}

/** 窗口门控（流式，无 VAD）：不等整句，按 hopMs 节奏逐块放行/拒绝（打字机场景）。 */
export declare class StreamGate {
  /** 加载声纹模型并创建实例（约 0.5s，在工作线程执行不阻塞 JS）。 */
  static create(config: StreamGateConfig): Promise<StreamGate>
  /** 是否已注册（未注册时 push 全放行）。 */
  get enrolled(): boolean
  /** 窗口判定阈值（窗口模式的独立阈值语义，与整句判定的 0.5 无关）。 */
  get effectiveThreshold(): number
  /** 整段注册（唤醒词整段，建议净语音 ≥1s）；注册后窗口判定立即生效。 */
  enroll(samples: Float32Array): Promise<EnrollResult>
  /**
   * 喂入一块音频（float32 [-1,1] @16k，典型 500ms），立刻返回该块的放行判定：
   * accepted=true 即可转发 ASR。静音块跳过推理且不送 ASR；凑步中沿用最近结论。
   */
  push(chunk: Float32Array): Promise<StreamGateVerdict>
  /** 释放原生资源（排到队列末尾执行）。 */
  dispose(): void
}
