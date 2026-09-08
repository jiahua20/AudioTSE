// 内网交付包离线自检：验证 Node 环境 + onnxruntime 原生库 + 声纹模型三者就绪。
// 在本包根目录运行：node smoke-test.js
// 通过标准：三行 ✅ + PASS（任何一步失败会打印原因并以非零码退出）。
const fs = require('node:fs')
const path = require('node:path')
const { VoiceFilter } = require('./gate')

/** 极简 16k 单声道 PCM16 wav 读取 */
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

  await filter.dispose()
  console.log('\nPASS：内网环境就绪，可接入业务')
}

main().catch((error) => {
  console.error('FAIL：', error.message)
  process.exit(1)
})
