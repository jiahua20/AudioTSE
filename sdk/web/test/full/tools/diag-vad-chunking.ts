import * as path from 'node:path'
import { createSileroVad } from '../../../src/full/vad'

const ROOT = path.resolve(__dirname, '..', '..', '..', '..', '..')
const VAD_MODEL = path.join(ROOT, 'app', 'models', 'silero_vad', 'silero_vad.onnx')
const sherpa = require('sherpa-onnx')
const target = sherpa.readWave(path.join(ROOT, 'app/samples/target_clean.wav')).samples

function drain(v: ReturnType<typeof createSileroVad>): number[] {
  const lens: number[] = []
  while (!v.isEmpty()) { lens.push(v.front().samples.length); v.pop() }
  return lens
}

// A: 一次性喂 8s
const a = createSileroVad(VAD_MODEL)
a.acceptWaveform(target)
console.log('A 一次喂 8s     → 完结段:', JSON.stringify(drain(a)), 'flush 尾段:', (() => { a.flush(); return drain(a) })())
a.free()

// B: 100ms 流式喂
const b = createSileroVad(VAD_MODEL)
const completed: number[] = []
for (let o = 0; o < target.length; o += 1600) {
  b.acceptWaveform(target.subarray(o, Math.min(o + 1600, target.length)))
  completed.push(...drain(b))
}
console.log('B 100ms 流式喂   → 完结段:', JSON.stringify(completed), 'flush 尾段:', (() => { b.flush(); return drain(b) })())
b.free()

// C: 一次喂 8s，再补 1s 静音
const c = createSileroVad(VAD_MODEL)
c.acceptWaveform(target)
c.acceptWaveform(new Float32Array(16000))
console.log('C 一次喂+1s静音  → 完结段:', JSON.stringify(drain(c)), 'flush 尾段:', (() => { c.flush(); return drain(c) })())
c.free()
