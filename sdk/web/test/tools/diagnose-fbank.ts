// 诊断：误差在特征矩阵里的分布结构（哪些帧/频带、是否常数偏移、是否集中在低能量区）
import * as fs from 'node:fs'
import * as path from 'node:path'

import { computeFbank } from '../../src/fbank'

const ROOT = path.resolve(__dirname, '..', '..', '..', '..')
const SAMPLES = path.join(ROOT, 'app/samples')
const FIXTURES = path.join(__dirname, '..', 'fixtures')

function readNpy(file: string): Float32Array {
  const buf = fs.readFileSync(file)
  let offset = 8
  const major = buf[6]
  const headerLength = major <= 1 ? (offset += 2, buf.readUInt16LE(6 + 2)) : (offset += 4, buf.readUInt32LE(6 + 2))
  offset = major <= 1 ? 10 : 12
  const data = buf.subarray(offset + headerLength)
  return new Float32Array(data.buffer, data.byteOffset, data.byteLength / 4)
}

// eslint-disable-next-line @typescript-eslint/no-var-requires
const sherpa = require('sherpa-onnx')
const wave = sherpa.readWave(path.join(SAMPLES, 'enroll_target.wav'))
const mine = computeFbank(wave.samples, { melNorm: 'raw' })
const ref = readNpy(path.join(FIXTURES, 'ref_fbank_enroll_target.npy'))
const frames = ref.length / 80

// 每帧的最大偏差 & 每频带的最大偏差
const frameWorst: Array<{ t: number; d: number; bin: number; refVal: number }> = []
const binWorst = new Float64Array(80)
for (let t = 0; t < frames; t++) {
  let worst = 0, worstBin = 0
  for (let b = 0; b < 80; b++) {
    const d = Math.abs(mine[t * 80 + b] - ref[t * 80 + b])
    if (d > binWorst[b]) binWorst[b] = d
    if (d > worst) { worst = d; worstBin = b }
  }
  frameWorst.push({ t, d: worst, bin: worstBin, refVal: ref[t * 80 + worstBin] })
}

frameWorst.sort((a, b) => b.d - a.d)
console.log('偏差最大的 10 帧（帧号 / max|diff| / 所在频带 / 该处参考值）:')
for (const f of frameWorst.slice(0, 10)) {
  console.log(`  t=${f.t} diff=${f.d.toExponential(3)} bin=${f.bin} ref=${f.refVal.toFixed(2)}`)
}
console.log('\n频带维度（0-79）每带 max|diff| 分布（找出集中区）:')
const bands = Array.from(binWorst).map((d, b) => ({ b, d })).sort((x, y) => y.d - x.d)
for (const { b, d } of bands.slice(0, 10)) console.log(`  bin=${b} max|diff|=${d.toExponential(3)}`)
console.log(`  ... 最小频带误差 ${bands[79].d.toExponential(3)}`)

// 信息频带（参考值 > -10，即能量不接近地板）的误差
let infoWorst = 0, infoCount = 0, allWorst = 0, sumDiff = 0
for (let i = 0; i < ref.length; i++) {
  const d = Math.abs(mine[i] - ref[i])
  allWorst = Math.max(allWorst, d)
  sumDiff += d
  if (ref[i] > -10) { infoWorst = Math.max(infoWorst, d); infoCount++ }
}
console.log(`\n全部元素: max=${allWorst.toExponential(3)} mean=${(sumDiff / ref.length).toExponential(3)}`)
console.log(`信息频带(ref>-10, ${infoCount}/${ref.length}): max=${infoWorst.toExponential(3)}`)
