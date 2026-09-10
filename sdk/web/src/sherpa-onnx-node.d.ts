// sherpa-onnx-node npm 包（原生 N-API 绑定，不带类型声明）：只声明 SDK 用到的
// 最小 API 面。注意该包只是 JS 壳，原生二进制（sherpa-onnx.node / dll）在平台包
// sherpa-onnx-win-x64 等里，安装时需一并装上。
declare module 'sherpa-onnx-node' {
  export interface SpeakerEmbeddingExtractorConfig {
    model: string
    numThreads?: number
    provider?: string
    debug?: number
  }

  export interface OnlineStream {
    acceptWaveform(obj: { samples: Float32Array; sampleRate: number }): void
    inputFinished(): void
  }

  export class SpeakerEmbeddingExtractor {
    constructor(config: SpeakerEmbeddingExtractorConfig)
    /** 声纹维度（ER2Net 为 512） */
    readonly dim: number
    createStream(): OnlineStream
    isReady(stream: OnlineStream): boolean
    /** 取声纹；enableExternalBuffer=false 返回拷贝。音频不足一帧时返回空数组 */
    compute(stream: OnlineStream, enableExternalBuffer?: boolean): Float32Array
  }

  export interface SileroVadConfig {
    model: string
    threshold?: number
    minSilenceDuration?: number
    minSpeechDuration?: number
    maxSpeechDuration?: number
    windowSize?: number
  }

  export interface VadConfig {
    sileroVad: SileroVadConfig
    sampleRate?: number
    numThreads?: number
    provider?: string
    debug?: number
  }

  export interface SpeechSegment {
    /** 段内音频，float32 [-1, 1] */
    samples: Float32Array
    /** 段起点在整条流中的采样序号 */
    start: number
  }

  /** 段式 VAD：acceptWaveform 喂流 → 完结的语音段进入内部队列 → front()/pop() 取出 */
  export class Vad {
    constructor(config: VadConfig, bufferSizeInSeconds?: number)
    acceptWaveform(samples: Float32Array): void
    /** 内部段队列是否为空 */
    isEmpty(): boolean
    /** 当前是否处于语音段内（轮询模式，SDK 未用） */
    isDetected(): boolean
    front(enableExternalBuffer?: boolean): SpeechSegment
    pop(): void
    clear(): void
    reset(): void
    /** 流结束：把尾部未完结的语音段冲进队列 */
    flush(): void
  }

  export function readWave(filename: string): { samples: Float32Array; sampleRate: number }
}
