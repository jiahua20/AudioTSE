// 说话人声纹提取器：sherpa-onnx-node 的 SpeakerEmbeddingExtractor（原生 N-API）。
// 与内网项目的 sherpa-onnx-node 共用同一 onnxruntime 运行时，进程内无 dll 冲突
// （迁移前用 onnxruntime-node，与 sherpa-onnx-win-x64 自带的 onnxruntime.dll 同名
// 相撞会报 Windows 193）。声纹与迁移前 fbank+onnxruntime 路径逐位一致（对拍
// cos=1.0，见 test/full/validate-embedding.ts）；调用序列与 cpp 版 embedder.cpp 同源。
import { SpeakerEmbeddingExtractor } from 'sherpa-onnx-node'

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

  /** 一段音频（float32 [-1,1] @16k）→ 定长声纹向量；不足一帧时抛错。 */
  async embed(samples: Float32Array): Promise<Float32Array> {
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
