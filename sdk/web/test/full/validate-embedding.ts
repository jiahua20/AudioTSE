// 声纹级对拍验证：sherpa-onnx-node 的 SpeakerEmbeddingExtractor（SDK 实际路径）
// 跑 ER2Net，与 sherpa_onnx Python 引擎的参考声纹（test/fixtures/ref_emb_*.npy，
// 由 test/full/tools/make_reference_embeddings.py 用 sherpa_onnx Python 生成）
// 比余弦，应 ≈1.0——即 SDK 判定路径与官方引擎一致。
// 用法：npm run validate（在 sdk/web 下）
import * as fs from 'node:fs'
import * as path from 'node:path'

import { SpeakerEmbedder } from '../../src/core/index'

const ROOT = path.resolve(__dirname, '..', '..', '..', '..') // 仓库根
const SPEAKER_MODEL = path.join(
  ROOT,
  'app/models/sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k/model.onnx',
)
const SAMPLES = path.join(ROOT, 'app/samples')
const FIXTURES = path.join(__dirname, 'fixtures')
const EMBED_NAMES = ['enroll_target', 'other_clean', 'target_clean'] as const

/** 读 npy（小端 float32 矩阵，行优先）。npy 头 64 字节对齐，数据必 4 字节对齐。 */
function readNpy(file: string): Float32Array {
  const buf = fs.readFileSync(file)
  if (buf[0] !== 0x93 || buf.subarray(1, 6).toString('ascii') !== 'NUMPY') throw new Error('not npy: ' + file)
  const major = buf[6]
  let offset = 8
  let headerLength: number
  if (major <= 1) {
    headerLength = buf.readUInt16LE(offset)
    offset += 2
  } else {
    headerLength = buf.readUInt32LE(offset)
    offset += 4
  }
  const header = buf.toString('ascii', offset, offset + headerLength)
  if (!header.includes("'<f4'")) throw new Error('expect float32 npy: ' + header)
  const data = buf.subarray(offset + headerLength)
  return new Float32Array(data.buffer, data.byteOffset, data.byteLength / 4)
}

function dot(a: Float32Array, b: Float32Array): number {
  let s = 0
  for (let i = 0; i < a.length; i++) s += a[i] * b[i]
  return s
}
function norm(a: Float32Array): number {
  return Math.sqrt(dot(a, a))
}
function cosine(a: Float32Array, b: Float32Array): number {
  return dot(a, b) / (norm(a) * norm(b))
}

async function main(): Promise<void> {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const sherpa = require('sherpa-onnx-node')

  function loadWav(name: string): Float32Array {
    const wave = sherpa.readWave(path.join(SAMPLES, `${name}.wav`))
    if (wave.sampleRate !== 16000) throw new Error(`expect 16k, got ${wave.sampleRate}`)
    return wave.samples
  }

  console.log('== 声纹级对拍（sherpa extractor vs sherpa_onnx Python 参考声纹）==')
  const embedder = await SpeakerEmbedder.create(SPEAKER_MODEL)
  const sims: number[] = []
  for (const name of EMBED_NAMES) {
    const t0 = Date.now()
    const embedding = await embedder.embed(loadWav(name))
    const ms = Date.now() - t0
    const ref = readNpy(path.join(FIXTURES, `ref_emb_${name}.npy`))
    const cos = cosine(embedding, ref)
    sims.push(cos)
    console.log(`${name.padEnd(14)} cos=${cos.toFixed(6)}  (${ms} ms / ${(loadWav(name).length / 16000).toFixed(1)} s 音频)`)
  }
  const worst = Math.min(...sims)
  if (worst >= 0.999) {
    console.log(`\nPASS：与 sherpa_onnx Python 引擎一致（最差 cos=${worst.toFixed(6)}）`)
  } else {
    throw new Error(`声纹级对拍失败：最差 cos=${worst.toFixed(6)}（应 ≥0.999）`)
  }
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
