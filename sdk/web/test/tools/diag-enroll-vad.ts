import * as path from 'node:path'
import { createSileroVad } from '../../src/vad'

const ROOT = path.resolve(__dirname, '..', '..', '..', '..')
const VAD_MODEL = path.join(ROOT, 'app', 'models', 'silero_vad', 'silero_vad.onnx')
const sherpa = require('sherpa-onnx')

const target = sherpa.readWave(path.join(ROOT, 'app/samples/target_clean.wav')).samples
console.log('target_clean 长度:', target.length, '采样')

// 1) 8s 原始音频 → 取首段
const v1 = createSileroVad(VAD_MODEL)
v1.acceptWaveform(target)
console.log('accept 8s 后 isEmpty:', v1.isEmpty())
if (!v1.isEmpty()) console.log('首段长度:', v1.front().samples.length)
v1.flush()
console.log('flush 后 isEmpty:', v1.isEmpty())
const seg = v1.front().samples
console.log('flush 取出的段长:', seg.length)
v1.free()

// 2) 把首段（纯语音 2.49s）再喂新 VAD
const v2 = createSileroVad(VAD_MODEL)
v2.acceptWaveform(seg)
console.log('\n纯语音段 accept 后 isEmpty:', v2.isEmpty())
v2.flush()
console.log('flush 后 isEmpty:', v2.isEmpty())
if (!v2.isEmpty()) console.log('段长:', v2.front().samples.length)
v2.free()
