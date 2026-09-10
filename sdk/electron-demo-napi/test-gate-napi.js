// N-API SDK 验证：直连 @audiotse/gate-napi（不走 Electron，纯 Node 即可跑），
// 用 100ms 块流式注册 enroll_target.wav → 流式监听 mixed.wav → flush 冲尾，
// 校验分段与判定是否与 web SDK 流式基准一致（sdk/web/script/compare-streaming.ts
// 的对拍值）。
// 用法：node test-gate-napi.js（在任意目录）
const fs = require('node:fs')
const path = require('node:path')

const ROOT = path.resolve(__dirname, '..', '..')
const { SpeakerGate } = require(path.join(ROOT, 'sdk', 'cpp-napi'))
const VAD = path.join(ROOT, 'app', 'models', 'silero_vad', 'silero_vad_v4.onnx')
const SPEAKER = path.join(
  ROOT,
  'app',
  'models',
  'sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k',
  'model.onnx',
)

function readWav(file) {
  const buf = fs.readFileSync(file)
  let offset = 12
  let data = null
  while (offset + 8 <= buf.length) {
    const id = buf.toString('ascii', offset, offset + 4)
    const size = buf.readUInt32LE(offset + 4)
    if (id === 'data') data = buf.subarray(offset + 8, offset + 8 + size)
    offset += 8 + size + (size % 2)
  }
  const pcm = new Int16Array(data.buffer, data.byteOffset, Math.floor(data.length / 2))
  const out = new Float32Array(pcm.length)
  for (let i = 0; i < pcm.length; i++) out[i] = pcm[i] / 32768
  return out
}

;(async () => {
  console.log('[0] 加载模型（原生 N-API addon）')
  const gate = await SpeakerGate.create({ vadModel: VAD, speakerModel: SPEAKER })
  console.log(`    ready，未注册时 enrolled=${gate.enrolled}`)

  // 段事件收集：accept/flush 返回 + 进度日志
  const segments = []

  console.log('[1] 注册 enroll_target.wav（流式 100ms 块，够量自动完成）')
  gate.beginEnroll()
  const enroll = readWav(path.join(ROOT, 'app', 'samples', 'enroll_target.wav'))
  let enrolled = null
  let progress = null
  for (let off = 0; off < enroll.length; off += 1600) {
    progress = await gate.enrollChunk(enroll.subarray(off, Math.min(off + 1600, enroll.length)))
    if (progress.enough) break // 净语音够量即收尾
  }
  if (progress?.enough) {
    enrolled = await gate.finishEnroll()
  } else {
    try {
      enrolled = await gate.finishEnroll() // 整段喂完仍未达目标也正常收尾
    } catch (e) {
      throw new Error(`注册失败：${e.message}`)
    }
  }
  console.log(`    enrolled ${enrolled.speechSeconds.toFixed(2)}s，effectiveThreshold=${gate.effectiveThreshold}`)

  console.log('[2] 监听 mixed.wav（流式 100ms 块 + flush 冲尾）')
  const mixed = readWav(path.join(ROOT, 'app', 'samples', 'mixed.wav'))
  for (let off = 0; off < mixed.length; off += 1600) {
    for (const event of await gate.accept(mixed.subarray(off, Math.min(off + 1600, mixed.length)))) {
      segments.push(event)
    }
  }
  for (const event of await gate.flush()) segments.push(event)
  for (const s of segments) {
    console.log(
      `    段 start=${s.start} ${s.durationSeconds.toFixed(2)}s sim=${s.similarity.toFixed(3)} ` +
        `${s.accepted ? '✅ 放行' : '❌ 拦截'}（${s.samples.length} 样本）`,
    )
  }

  gate.dispose()

  // 基准 = web SDK 同样以 100ms 块流式注册/监听的结果（sdk/web/script/compare-streaming.ts）
  const ok =
    Math.abs(enrolled.speechSeconds - 3.78) < 0.02 &&
    segments.length === 2 &&
    segments[0].start === 1984 &&
    Math.abs(segments[0].similarity - 0.188) < 0.003 &&
    segments[0].accepted === false &&
    segments[1].start === 97728 &&
    Math.abs(segments[1].similarity - 0.569) < 0.003 &&
    segments[1].accepted === true &&
    segments[0].samples.length > 70000
  console.log(
    `\n${ok ? 'PASS' : 'FAIL'}：enrolled(${enrolled.speechSeconds.toFixed(2)}s)/` +
      `segments(${segments.map((s) => s.similarity.toFixed(3)).join(', ')}) 与 web 版流式基准一致`,
  )
  setTimeout(() => process.exit(ok ? 0 : 1), 200)
})().catch((e) => {
  console.error('FAIL：', e)
  process.exit(1)
})
