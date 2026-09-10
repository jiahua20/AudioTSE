// 内网交付包（napi 版）离线自检：验证 Node 环境 + 原生 addon + sherpa 运行时 + 模型。
// 在本包根目录运行：node smoke-test.js
// 通过标准：各行 ✅ + PASS（模型加载 ~0.5s，全程 ~2s）；与 web 版交付包同输入同基准。
const fs = require('node:fs')
const path = require('node:path')
const { VoiceFilter } = require('./gate-napi')

console.log(`SDK 入口：${require.resolve('./gate-napi')}（C++ N-API 版）`)

/** 极简 16k 单声道 PCM16 wav 读取（自备解析：sherpa 的 readWave 在 Electron 28+ 不可用） */
function readWav(file) {
  const buf = fs.readFileSync(file)
  let offset = 12
  let data = null
  let channels = 0
  let sampleRate = 0
  while (offset + 8 <= buf.length) {
    const id = buf.toString('ascii', offset, offset + 4)
    const size = buf.readUInt32LE(offset + 4)
    const body = buf.subarray(offset + 8, offset + 8 + size)
    if (id === 'fmt ') {
      channels = body.readUInt16LE(2)
      sampleRate = body.readUInt32LE(4)
    } else if (id === 'data') {
      data = body
    }
    offset += 8 + size + (size % 2)
  }
  if (!data || channels !== 1 || sampleRate !== 16000) throw new Error('需要 16k 单声道 PCM16 wav: ' + file)
  const pcm = new Int16Array(data.buffer, data.byteOffset, Math.floor(data.length / 2))
  const samples = new Float32Array(pcm.length)
  for (let i = 0; i < pcm.length; i++) samples[i] = pcm[i] / 32768
  return samples
}

const SR = 16000
const sec = (n) => Math.round(n * SR)

async function main() {
  const t0 = Date.now()
  const filter = await VoiceFilter.create({ speakerModel: path.join(__dirname, 'models', 'speaker.onnx') })
  console.log(`[1/3] 模型加载成功（${Date.now() - t0} ms）`)

  const enroll = readWav(path.join(__dirname, 'samples', 'enroll_target.wav'))
  const target = readWav(path.join(__dirname, 'samples', 'target_clean.wav'))
  const other = readWav(path.join(__dirname, 'samples', 'other_clean.wav'))

  const r = await filter.enroll(enroll)
  console.log(`[2/3] 注册成功（${r.speechSeconds.toFixed(2)}s 语音，生效阈值 ${filter.effectiveThreshold}）`)

  const cases = [
    { label: '主讲人语音判定', samples: target.subarray(sec(2.8), sec(4.5)), expect: true },
    { label: '陌生人语音判定', samples: other.subarray(0, sec(2.0)), expect: false },
  ]
  for (const c of cases) {
    const { similarity, accepted } = await filter.judge(c.samples)
    const mark = accepted === c.expect ? '✅' : '❌'
    console.log(`[3/3] ${mark} ${c.label}：相似度 ${similarity.toFixed(3)} → ${accepted ? '放行' : '拒绝'}`)
    if (accepted !== c.expect) throw new Error(`判定与预期不符：${c.label}（期望${c.expect ? '放行' : '拒绝'}）`)
  }

  filter.dispose()
  console.log('\nPASS：内网环境就绪，可接入业务（判定基准与 web 版 SDK 完全一致）')
}

main().catch((error) => {
  console.error('FAIL：', error.message)
  process.exit(1)
})
