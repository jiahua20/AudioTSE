// sherpa-onnx npm 包（WASM 版）不带类型声明，这里只声明 SDK 用到的最小 API 面。
// VAD 的段式用法：acceptWaveform 喂流 → 完结的语音段进入内部队列 → front()/pop() 取出。
declare module 'sherpa-onnx' {
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
    tenVad?: object
    sampleRate?: number
    numThreads?: number
    provider?: string
    debug?: number
    bufferSizeInSeconds?: number
  }

  export interface SpeechSegment {
    /** 段内音频，float32 [-1, 1] */
    samples: Float32Array
    /** 段起点在整条流中的采样序号 */
    start: number
  }

  export interface Vad {
    acceptWaveform(samples: Float32Array): void
    /** 内部段队列是否为空 */
    isEmpty(): boolean
    /** 当前是否处于语音段内（轮询模式，SDK 未用） */
    isDetected(): boolean
    front(): SpeechSegment
    pop(): void
    clear(): void
    reset(): void
    /** 流结束：把尾部未完结的语音段冲进队列 */
    flush(): void
    free(): void
  }

  export function createVad(config: VadConfig): Vad
  export function readWave(filename: string): { samples: Float32Array; sampleRate: number }
  export const version: string
}
