// 说话人声纹提取器：sherpa-onnx-node 的 SpeakerEmbeddingExtractor（原生 N-API）。
// 与内网项目的 sherpa-onnx-node 共用同一 onnxruntime 运行时，进程内无 dll 冲突
// （迁移前用 onnxruntime-node，与 sherpa-onnx-win-x64 自带的 onnxruntime.dll 同名
// 相撞会报 Windows 193）。声纹与 sherpa_onnx Python 引擎对拍 cos≥0.9999
// （见 test/full/validate-embedding.ts）；调用序列与 cpp 版 embedder.cpp 同源。
import { SpeakerEmbeddingExtractor } from 'sherpa-onnx-node'

/** embed 输入下限（0.1s）：低于它的音频声纹无意义，且过短输入可能在原生层直接崩进程 */
const MIN_EMBED_SAMPLES = 1600

/** 跨 realm 判定 Float32Array：Electron 多上下文（如 IPC 对端）下 instanceof 会误判 */
function isFloat32Array(value: unknown): value is Float32Array {
  return Object.prototype.toString.call(value) === '[object Float32Array]'
}

/** 报错时描述实际收到的值，帮助定位「IPC 序列化成了普通数组」这类问题 */
function describeValue(value: unknown): string {
  if (value === null || typeof value !== 'object') return `${typeof value} ${JSON.stringify(value)}`
  const tag = Object.prototype.toString.call(value).slice(8, -1)
  return tag === 'Object' && value.constructor ? value.constructor.name : tag
}

/**
 * 输入校验（类型）：把「原生层直接崩进程」变成带说明的 JS 异常。
 * push 流式块允许短于 0.1s，只做类型校验。
 */
export function assertFloat32(samples: unknown, what: string): asserts samples is Float32Array {
  if (!isFloat32Array(samples)) {
    throw new TypeError(
      `${what}必须是 Float32Array（[-1,1] @16k 单声道），实际收到 ${describeValue(samples)}；` +
        `经 IPC/序列化传输时请确认对端没有把音频变成普通数组`,
    )
  }
}

/** 输入校验（类型 + 最短时长）：整段送推理的入口（enroll/judge）用。 */
export function assertEmbeddable(samples: unknown, what: string): asserts samples is Float32Array {
  assertFloat32(samples, what)
  if (samples.length < MIN_EMBED_SAMPLES) {
    throw new RangeError(
      `${what}太短：${samples.length} 样本（约 ${Math.round(samples.length / 16)}ms），` +
        `至少需要 ${MIN_EMBED_SAMPLES} 样本（0.1s）；长度为 0 多为缓冲区被 transfer 后 detached`,
    )
  }
}

export class SpeakerEmbedder {
  private constructor(private readonly extractor: SpeakerEmbeddingExtractor) {}

  /** 加载声纹模型（3D-Speaker ER2Net，CPU 推理）。 */
  static async create(modelPath: string): Promise<SpeakerEmbedder> {
    const extractor = new SpeakerEmbeddingExtractor({
      model: modelPath,
      numThreads: 1,
      provider: 'cpu',
      debug: 0,
    })
    return new SpeakerEmbedder(extractor)
  }

  /** 一段音频（float32 [-1,1] @16k）→ 定长声纹向量；类型/长度不合法时抛带说明异常。 */
  async embed(samples: Float32Array): Promise<Float32Array> {
    assertEmbeddable(samples, 'embed 的音频')
    const stream = this.extractor.createStream()
    stream.acceptWaveform({ samples, sampleRate: 16000 })
    stream.inputFinished()
    // enableExternalBuffer=false：取拷贝，避免 sherpa 内部缓冲被后续调用复用
    const embedding = this.extractor.compute(stream, false)
    if (!embedding || embedding.length === 0) throw new Error('audio too short for one frame')
    return embedding
  }

  /** 声纹维度（ER2Net 为 512）。 */
  get dim(): number {
    return this.extractor.dim
  }

  /** 原生句柄由 GC 回收（sherpa-onnx-node 未暴露显式释放）；保留空实现兼容旧 API。 */
  async free(): Promise<void> {}
}
