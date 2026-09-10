// silero VAD 封装：sherpa-onnx-node 原生版，参数与 app 后端 speaker_gate.py 保持
// 一致（threshold 0.5、静音 0.3s、最短语音 0.1s、最长语音 15s）。
// 迁移前用 sherpa-onnx WASM 包，现与声纹共用同一原生运行时（内网进程无 dll 冲突）。
import { Vad as NativeVad } from 'sherpa-onnx-node'

export interface SileroVadOptions {
  /** 语音概率阈值 */
  threshold: number
  /** 判定语音段结束所需的最短静音时长（秒） */
  minSilenceDuration: number
  /** 成段所需的最短语音时长（秒） */
  minSpeechDuration: number
  /** 单段最长语音时长（秒），超过强制切段 */
  maxSpeechDuration: number
}

export const DEFAULT_VAD_OPTIONS: SileroVadOptions = {
  threshold: 0.5,
  minSilenceDuration: 0.3,
  minSpeechDuration: 0.1,
  maxSpeechDuration: 15.0,
}

/** 创建 silero VAD（段式：完结的语音段从队列 front()/pop() 取）。 */
export function createSileroVad(modelPath: string, overrides: Partial<SileroVadOptions> = {}): Vad {
  const opts = { ...DEFAULT_VAD_OPTIONS, ...overrides }
  const inner = new NativeVad(
    {
      sileroVad: {
        model: modelPath,
        threshold: opts.threshold,
        minSilenceDuration: opts.minSilenceDuration,
        minSpeechDuration: opts.minSpeechDuration,
        maxSpeechDuration: opts.maxSpeechDuration,
        windowSize: 512,
      },
      sampleRate: 16000,
      numThreads: 1,
      provider: 'cpu',
      debug: 0,
    },
    30, // bufferSizeInSeconds：段缓冲上限（与 cpp 版一致）
  )

  // sherpa 的 VAD 对大块单次喂入行为异常（内部段边界丢失、状态错乱），
  // 这里统一切成 160ms 小块喂入，使行为与调用方的块大小无关（已实测对齐流式行为）。
  const CHUNK = 2560
  const wrapper: Vad = {
    acceptWaveform(samples: Float32Array): void {
      for (let offset = 0; offset < samples.length; offset += CHUNK) {
        inner.acceptWaveform(samples.subarray(offset, Math.min(offset + CHUNK, samples.length)))
      }
    },
    isEmpty: () => inner.isEmpty(),
    isDetected: () => inner.isDetected(),
    front: () => inner.front(false), // 取拷贝，段样本不被后续复用
    pop: () => inner.pop(),
    clear: () => inner.clear(),
    reset: () => inner.reset(),
    flush: () => inner.flush(),
    free: () => {}, // 原生句柄由 GC 回收，保留空实现兼容旧 API
  }
  return wrapper
}

export interface Vad {
  acceptWaveform(samples: Float32Array): void
  /** 内部段队列是否为空 */
  isEmpty(): boolean
  /** 当前是否处于语音段内（轮询模式，SDK 未用） */
  isDetected(): boolean
  front(): { samples: Float32Array; start: number }
  pop(): void
  clear(): void
  reset(): void
  /** 流结束：把尾部未完结的语音段冲进队列 */
  flush(): void
  free(): void
}
