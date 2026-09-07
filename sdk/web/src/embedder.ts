// 说话人声纹提取器：onnxruntime-node 直接跑 3D-Speaker ER2Net ONNX，
// 前端特征用本包的 kaldi 风格 fbank（与 sherpa-onnx 对拍一致）。
import * as ort from 'onnxruntime-node'
import { computeFbank, type FbankOptions } from './fbank'

/** 特征归一化方式：该模型 ONNX 元数据 feature_normalize_type=global-mean。 */
export type FeatureNormalize = 'none' | 'global-mean'

export class SpeakerEmbedder {
  private constructor(
    private readonly session: ort.InferenceSession,
    private readonly inputName: string,
    private readonly outputName: string,
    private readonly featureDim: number,
    private readonly normalize: FeatureNormalize,
  ) {}

  /** 加载声纹模型（输入名/输出名从会话自发现，不硬编码）。 */
  static async create(modelPath: string, normalize: FeatureNormalize = 'global-mean'): Promise<SpeakerEmbedder> {
    const session = await ort.InferenceSession.create(modelPath, {
      executionProviders: ['cpu'],
      graphOptimizationLevel: 'all',
    })
    if (session.inputNames.length < 1 || session.outputNames.length < 1) {
      throw new Error('speaker model has no input/output: ' + modelPath)
    }
    return new SpeakerEmbedder(session, session.inputNames[0], session.outputNames[0], 80, normalize)
  }

  /** 一段音频（float32 [-1,1] @16k）→ 定长声纹向量。 */
  async embed(samples: Float32Array, fbankOpts?: FbankOptions): Promise<Float32Array> {
    const numMel = fbankOpts?.numMelBins ?? 80
    const feats = computeFbank(samples, fbankOpts)
    const width = numMel + (fbankOpts?.appendEnergy ? 1 : 0)
    const frames = feats.length / width
    if (frames < 1) throw new Error('audio too short for one frame')
    if (this.normalize === 'global-mean') {
      // 整段逐维减均值（sherpa SubtractGlobalMean：m.rowwise() - m.colwise().mean()）
      for (let d = 0; d < width; d++) {
        let mean = 0
        for (let t = 0; t < frames; t++) mean += feats[t * width + d]
        mean /= frames
        for (let t = 0; t < frames; t++) feats[t * width + d] -= mean
      }
    }
    const tensor = new ort.Tensor('float32', feats, [1, frames, width])
    const results = await this.session.run({ [this.inputName]: tensor })
    const output = results[this.outputName]
    const data = output.data as Float32Array
    return data.slice()
  }

  async free(): Promise<void> {
    await this.session.release()
  }
}
