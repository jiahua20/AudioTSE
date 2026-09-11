// 内网交付包离线自检：验证 Node 环境 + onnxruntime 原生库 + 声纹模型 + 三种模块格式入口。
// 在本包根目录运行：node smoke-test.js
// 通过标准：五行 ✅ + PASS（任何一步失败会打印原因并以非零码退出）。
const fs = require('node:fs')
const path = require('node:path')
const { VoiceFilter } = require('./gate')

console.log(`SDK 入口：${require.resolve('./gate')}（多文件 CommonJS：gate/cjs，包 main）`)

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
  const filter = await VoiceFilter.create({ speakerModel: path.join(__dirname, 'models', 'sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k.onnx') })
  console.log(`[1/5] 模型加载成功（${Date.now() - t0} ms）`)

  const enroll = readWav(path.join(__dirname, 'samples', 'enroll_target.wav'))
  const target = readWav(path.join(__dirname, 'samples', 'target_clean.wav'))
  const other = readWav(path.join(__dirname, 'samples', 'other_clean.wav'))

  const r = await filter.enroll(enroll)
  console.log(`[2/5] 注册成功（${r.speechSeconds.toFixed(2)}s 语音，生效阈值 ${filter.effectiveThreshold}）`)

  const cases = [
    { label: '主讲人语音判定', samples: target.subarray(sec(2.8), sec(4.5)), expect: true },
    { label: '陌生人语音判定', samples: other.subarray(0, sec(2.0)), expect: false },
  ]
  for (const c of cases) {
    const { similarity, accepted } = await filter.judge(c.samples)
    const mark = accepted === c.expect ? '✅' : '❌'
    console.log(`[3/5] ${mark} ${c.label}：相似度 ${similarity.toFixed(3)} → ${accepted ? '放行' : '拒绝'}`)
    if (accepted !== c.expect) throw new Error(`判定与预期不符：${c.label}（期望${c.expect ? '放行' : '拒绝'}）`)
  }

  // [4/5] 流式窗口门控（StreamGate，打字机/ASR 场景）：500ms 块逐块判定
  const { StreamGate } = require('./gate')
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
  console.log(`[4/5] ${tRate >= 0.8 ? '✅' : '❌'} 流式窗口门控：主讲人 ${t.pass}/${t.pass + t.block} 块放行，陌生人 ${o.block}/${o.pass + o.block} 块拒绝`)
  if (tRate < 0.8 || oRate < 0.8) throw new Error('窗口门控放行/拒绝率异常')
  await sg.dispose()

  // [5/5] 三种模块格式入口：cjs 已由包入口验证；UMD require 验证；esm 检查文件与
  //       export 语句（多文件 esm 的相对导入不带 .js 后缀——Vite/webpack/Rollup 可
  //       解析，Node 直跑 ESM 不行，Node 环境用 gate/cjs）
  const umd = require('./gate/umd/audiotse-gate.umd.js')
  if (typeof umd.VoiceFilter !== 'function' || typeof umd.StreamGate !== 'function') {
    throw new Error('UMD 入口缺少导出')
  }
  for (const f of ['index.js', 'embedder.js', 'voice-filter.js', 'stream-gate.js']) {
    if (!fs.existsSync(path.join(__dirname, 'gate', 'esm', f))) throw new Error(`缺 gate/esm/${f}`)
  }
  const esmIndex = fs.readFileSync(path.join(__dirname, 'gate', 'esm', 'index.js'), 'utf8')
  if (!esmIndex.includes('export')) throw new Error('gate/esm/index.js 缺少 export 语句')
  console.log('[5/5] ✅ 三种格式入口就绪：gate/cjs（require ✓）gate/umd（require ✓）gate/esm（具名 export ✓，构建器接入面）')

  await filter.dispose()
  console.log('\nPASS：内网环境就绪，可接入业务')
}

main().catch((error) => {
  console.error('FAIL：', error.message)
  process.exit(1)
})
