// 消融实验：定位 TS 与 Python 参考声纹之间 0.87 余弦的来源。
// 分别试：不做 global-mean / 掐头 / 掐尾 / 换 dither 假设，看哪种组合跳到 ≈1.0。
import * as fs from 'node:fs'
import * as path from 'node:path'

import { SpeakerEmbedder } from '../../../src/core/index'
import { computeFbank } from '../../../src/core/index'
import * as ort from 'onnxruntime-node'

const ROOT = path.resolve(__dirname, '..', '..', '..', '..', '..')
const SPEAKER_MODEL = path.join(
  ROOT,
  'app/models/sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k/model.onnx',
)
const SAMPLES = path.join(ROOT, 'app/samples')
const FIXTURES = path.join(__dirname, '..', 'fixtures')

function readNpy(file: string): Float32Array {
  const buf = fs.readFileSync(file)
  const headerLength = buf[6] <= 1 ? buf.readUInt16LE(8) : buf.readUInt32LE(8)
  const offset = buf[6] <= 1 ? 10 : 12
  const data = buf.subarray(offset + headerLength)
  return new Float32Array(data.buffer, data.byteOffset, data.byteLength / 4)
}
function cos(a: Float32Array, b: Float32Array): number {
  let d = 0, na = 0, nb = 0
  for (let i = 0; i < a.length; i++) { d += a[i] * b[i]; na += a[i] * a[i]; nb += b[i] * b[i] }
  return d / (Math.sqrt(na) * Math.sqrt(nb))
}
async function embedRaw(
  session: ort.InferenceSession,
  feats: Float32Array,
  frames: number,
  width: number,
  meanNormalize: boolean,
): Promise<Float32Array> {
  if (meanNormalize) {
    for (let d = 0; d < width; d++) {
      let m = 0
      for (let t = 0; t < frames; t++) m += feats[t * width + d]
      m /= frames
      for (let t = 0; t < frames; t++) feats[t * width + d] -= m
    }
  }
  const tensor = new ort.Tensor('float32', feats, [1, frames, width])
  const out = await session.run({ [session.inputNames[0]]: tensor })
  return (out[session.outputNames[0]].data as Float32Array).slice()
}

async function main() {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const sherpa = require('sherpa-onnx')
  console.log('sherpa-onnx npm 版本:', sherpa.version)
  const session = await ort.InferenceSession.create(SPEAKER_MODEL, { executionProviders: ['cpu'] })
  console.log('模型输入:', session.inputNames, session.inputMetadata ? '' : '')

  const name = 'enroll_target'
  const wave = sherpa.readWave(path.join(SAMPLES, `${name}.wav`))
  const ref = readNpy(path.join(FIXTURES, `ref_emb_${name}.npy`))
  const feats = computeFbank(wave.samples, { melNorm: 'raw' })
  const width = 80
  const totalFrames = feats.length / width

  const variants: Array<{ label: string; from: number; to: number; mean: boolean }> = [
    { label: '全帧 + global-mean', from: 0, to: totalFrames, mean: true },
    { label: '全帧 无归一', from: 0, to: totalFrames, mean: false },
    { label: '掐头1帧 + global-mean', from: 1, to: totalFrames, mean: true },
    { label: '掐尾1帧 + global-mean', from: 0, to: totalFrames - 1, mean: true },
    { label: '掐头尾各1帧 + mean', from: 1, to: totalFrames - 1, mean: true },
    { label: '掐尾2帧 + mean', from: 0, to: totalFrames - 2, mean: true },
  ]
  for (const v of variants) {
    const frames = v.to - v.from
    const slice = feats.slice(v.from * width, v.to * width)
    const embedding = await embedRaw(session, slice, frames, width, v.mean)
    console.log(`${v.label.padEnd(24)} 帧数=${frames} cos=${cos(embedding, ref).toFixed(6)}`)
  }
  void SpeakerEmbedder
}
main().catch((e) => { console.error(e); process.exit(1) })
