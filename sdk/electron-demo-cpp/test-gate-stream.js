// gate_stream 协议级验证：用与 electron-demo-cpp/main 完全相同的 spawn + 帧协议，
// 喂 enroll_target.wav 注册 → mixed.wav 监听 → 'T' 冲尾，校验 JSON 事件序列。
// 用法：node test-gate-stream.js（在任意目录）
const { spawn } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')
const readline = require('node:readline')

const ROOT = path.resolve(__dirname, '..', '..')
const EXE = path.join(ROOT, 'sdk', 'cpp', 'build', 'gate_stream.exe')
const VAD = path.join(ROOT, 'app', 'models', 'silero_vad', 'silero_vad.onnx')
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

const gate = spawn(EXE, [VAD, SPEAKER], { stdio: ['pipe', 'pipe', 'inherit'] })

function sendAudio(samples) {
  const header = Buffer.allocUnsafe(5)
  header.writeUInt8(0x41, 0)
  header.writeUInt32LE(samples.length, 1)
  gate.stdin.write(header)
  gate.stdin.write(Buffer.from(samples.buffer, samples.byteOffset, samples.byteLength))
}

const events = []
let segmentCount = 0
const done = new Promise((resolve, reject) => {
  const t = setTimeout(() => reject(new Error('超时：事件序列未完成')), 120000)
  readline.createInterface({ input: gate.stdout }).on('line', (line) => {
    if (!line) return
    const msg = JSON.parse(line)
    events.push(msg)
    if (msg.type === 'enrollProgress') process.stdout.write(`  进度 ${msg.speechSeconds}s\r`)
    else
      console.log(
        `  <- ${JSON.stringify({ ...msg, samples: msg.samples ? `[${msg.samples.length} int16]` : undefined })}`,
      )
    if (msg.type === 'segment' && ++segmentCount === 2) {
      // mixed.wav 的第二段（目标说话人段）即完成
      clearTimeout(t)
      gate.stdin.write('Q')
      gate.on('close', (code) => resolve(code))
    }
  })
  gate.on('close', (code) => {
    clearTimeout(t)
    code === 0 ? resolve(code) : reject(new Error(`gate_stream 退出码 ${code}`))
  })
})

;(async () => {
  await new Promise((r) => setTimeout(r, 1500)) // 等模型加载（ready 事件）
  console.log('[1] 注册 enroll_target.wav（流式 100ms 块 + B/F 命令）')
  const enroll = readWav(path.join(ROOT, 'app', 'samples', 'enroll_target.wav'))
  gate.stdin.write('B')
  for (let off = 0; off < enroll.length; off += 1600) {
    sendAudio(enroll.subarray(off, Math.min(off + 1600, enroll.length)))
    await new Promise((r) => setTimeout(r, 5)) // 模拟实时节流
  }
  gate.stdin.write('F')
  await new Promise((r) => setTimeout(r, 2000))

  console.log('[2] 监听 mixed.wav')
  gate.stdin.write('S')
  const mixed = readWav(path.join(ROOT, 'app', 'samples', 'mixed.wav'))
  for (let off = 0; off < mixed.length; off += 1600) {
    sendAudio(mixed.subarray(off, Math.min(off + 1600, mixed.length)))
    await new Promise((r) => setTimeout(r, 5))
  }
  gate.stdin.write('T')
  await done

  const enrolled = events.find((e) => e.type === 'enrolled')
  const segments = events.filter((e) => e.type === 'segment')
  // 基准 = web SDK 同样以 100ms 块流式注册/监听的结果（script/compare-streaming.ts），
  // 流式路径两侧逐位一致：3.78s / start 1984+97728 / sim 0.188、0.569
  const ok =
    events[0].type === 'ready' &&
    enrolled &&
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
    `\n${ok ? 'PASS' : 'FAIL'}：ready/enrolled(${enrolled.speechSeconds}s)/` +
      `segments(${segments.map((s) => s.similarity).join(', ')}) 与 web 版流式基准一致`,
  )
  process.exit(ok ? 0 : 1)
})().catch((e) => {
  console.error('FAIL：', e.message)
  process.exit(1)
})
