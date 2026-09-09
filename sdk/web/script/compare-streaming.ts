// 流式路径严格对拍：web SDK 与 C++ gate_stream 用完全相同的喂入模式
// （注册/监听都按 100ms 块流式），数值应一致。
import * as path from 'node:path'
import { SpeakerGate } from '../src/full/speaker-gate'

const ROOT = path.resolve(__dirname, '..', '..', '..')

function loadWav(p: string): Float32Array {
  // @ts-expect-error sherpa-onnx 无类型声明
  const sherpa = require('sherpa-onnx')
  const wave = sherpa.readWave(p)
  return wave.samples
}

async function main() {
  const gate = await SpeakerGate.create({
    vadModel: path.join(ROOT, 'app/models/silero_vad/silero_vad.onnx'),
    speakerModel:
      path.join(ROOT,
        'app/models/sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k/model.onnx'),
  })

  console.log('[1] 流式注册 enroll_target.wav（100ms 块）')
  gate.beginEnroll()
  const enroll = loadWav(path.join(ROOT, 'app/samples/enroll_target.wav'))
  let progress
  for (let off = 0; off < enroll.length; off += 1600) {
    progress = await gate.enrollChunk(enroll.subarray(off, Math.min(off + 1600, enroll.length)))
  }
  const enrolled = await gate.finishEnroll()
  console.log(`  enrolled ${enrolled.speechSeconds.toFixed(2)}s（进度计数 ${progress!.speechSeconds.toFixed(2)}s）`)

  console.log('[2] 流式监听 mixed.wav（100ms 块）')
  const mixed = loadWav(path.join(ROOT, 'app/samples/mixed.wav'))
  gate.reset()
  const events = []
  for (let off = 0; off < mixed.length; off += 1600) {
    events.push(...(await gate.accept(mixed.subarray(off, Math.min(off + 1600, mixed.length)))))
  }
  events.push(...(await gate.flush()))
  for (const e of events) {
    console.log(`  [start=${e.start}] ${e.durationSeconds.toFixed(2)}s sim=${e.similarity!.toFixed(3)} ${e.accepted ? '放行' : '拒绝'}`)
  }
  await gate.dispose()
}

main().catch((e) => {
  console.error(e)
  process.exit(1)
})
