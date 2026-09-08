// silero VAD 封装：参数与 app 后端 speaker_gate.py 保持一致
// （threshold 0.5、静音 0.3s、最短语音 0.1s、最长语音 15s）。
import { createVad, type Vad } from 'sherpa-onnx'

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
  const inner = createVad({
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
    bufferSizeInSeconds: 30,
  })

  // sherpa WASM 的 VAD 对大块单次喂入行为异常（内部段边界丢失、状态错乱），
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
    front: () => inner.front(),
    pop: () => inner.pop(),
    clear: () => inner.clear(),
    reset: () => inner.reset(),
    flush: () => inner.flush(),
    free: () => inner.free(),
  }
  return wrapper
}

export type { Vad }
