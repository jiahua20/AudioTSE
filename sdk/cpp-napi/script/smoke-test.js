// 内网交付包（napi 版）离线自检：验证 Node 环境 + 原生 addon + sherpa 运行时 + 模型。
// 在本包根目录运行：node smoke-test.js
// 通过标准：四步 ✅ + PASS（模型加载 ~0.5s）；与 web 版交付包同输入同基准。
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
  const filter = await VoiceFilter.create({ speakerModel: path.join(__dirname, 'models', 'sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k.onnx') })
  console.log(`[1/4] 模型加载成功（${Date.now() - t0} ms）`)

  const enroll = readWav(path.join(__dirname, 'samples', 'enroll_target.wav'))
  const target = readWav(path.join(__dirname, 'samples', 'target_clean.wav'))
  const other = readWav(path.join(__dirname, 'samples', 'other_clean.wav'))

  const r = await filter.enroll(enroll)
  console.log(`[2/4] 注册成功（${r.speechSeconds.toFixed(2)}s 语音，生效阈值 ${filter.effectiveThreshold}）`)

  const cases = [
    { label: '主讲人语音判定', samples: target.subarray(sec(2.8), sec(4.5)), expect: true },
    { label: '陌生人语音判定', samples: other.subarray(0, sec(2.0)), expect: false },
  ]
  for (const c of cases) {
    const { similarity, accepted } = await filter.judge(c.samples)
    const mark = accepted === c.expect ? '✅' : '❌'
    console.log(`[3/4] ${mark} ${c.label}：相似度 ${similarity.toFixed(3)} → ${accepted ? '放行' : '拒绝'}`)
    if (accepted !== c.expect) throw new Error(`判定与预期不符：${c.label}（期望${c.expect ? '放行' : '拒绝'}）`)
  }

  // [4/4] 流式窗口门控（StreamGate，打字机/ASR 场景）：500ms 块逐块判定
  const { StreamGate } = require('./gate-napi')
  const sg = await StreamGate.create({
    speakerModel: path.join(__dirname, 'models', 'sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k.onnx'),
  })
  await sg.enroll(enroll)
  async function streamTally(x) {
    let pass = 0, block = 0
    for (let i = 0; i + sec(0.5) <= x.length; i += sec(0.5)) {
      const v = await sg.push(x.subarray(i, i + sec(0.5)))
      if (v.silent) continue
      v.accepted ? pass++ : block++
    }
    return { pass, block }
  }
  const t = await streamTally(target)
  const o = await streamTally(other)
  const tRate = t.pass / (t.pass + t.block)
  const oRate = o.block / (o.pass + o.block)
  console.log(`[4/4] ${tRate >= 0.8 ? '✅' : '❌'} 流式窗口门控：主讲人 ${t.pass}/${t.pass + t.block} 块放行，陌生人 ${o.block}/${o.pass + o.block} 块拒绝`)
  if (tRate < 0.8 || oRate < 0.8) throw new Error('窗口门控放行/拒绝率异常')
  await sg.dispose()

  filter.dispose()
  console.log('\nPASS：内网环境就绪，可接入业务（判定基准与 web 版 SDK 完全一致）')
}

main().catch((error) => {
  console.error('FAIL：', error.message)
  process.exit(1)
})
