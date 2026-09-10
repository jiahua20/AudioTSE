// 内网「唤醒词注册 + 提问过滤」流程演示（C++ N-API 版，纯 Node 可直接跑）。
// 与 web 版 SDK 的同名示例步骤完全一致（API 同签名），方便两边对照测速。
//
//   1. 客户向大屏喊「小耘小耘」→ 唤醒，这句语音同时做声纹注册（每次唤醒都重新注册）
//   2. 之后客户的提问/其他语音逐段过滤，只把主讲人的语音回传给业务
//
// 接内网时把 readWav/切段换成你们 VAD 输出的 Float32Array（16k 单声道 [-1,1]）。
//
// 运行（在 sdk/cpp-napi 下，先 .\build.ps1）：
//   node examples/intranet-wake-flow.js
const fs = require('node:fs')
const path = require('node:path')
const { VoiceFilter } = require('..')

const REPO_ROOT = path.resolve(__dirname, '..', '..', '..')
const SPEAKER_MODEL =
  process.env.AUDIOTSE_SPEAKER_MODEL ||
  path.join(REPO_ROOT, 'app/models/sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k/model.onnx')
const SAMPLES_DIR = process.env.AUDIOTSE_SAMPLES || path.join(REPO_ROOT, 'app/samples')
const SR = 16000
const sec = (n) => Math.round(n * SR)

/** 极简 16k 单声道 PCM16 wav 读取（内网 demo 自用，替换成你们的音频源） */
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
  const samples = new Float32Array(pcm.length)
  for (let i = 0; i < pcm.length; i++) samples[i] = pcm[i] / 32768
  return samples
}

/** 唤醒：这句「小耘小耘」语音重新注册声纹（每次唤醒都调用） */
async function wake(filter, label, samples) {
  const result = await filter.enroll(samples)
  console.log(`🔊 ${label}：注册 ${result.speechSeconds.toFixed(2)}s → 生效阈值 ${filter.effectiveThreshold}`)
}

/** 提问：判定是否主讲人。expect = 预期结果（自校验用） */
async function question(filter, label, samples, expect) {
  const { similarity, accepted } = await filter.judge(samples)
  const mark = accepted === expect ? '✅' : '❌'
  console.log(`${mark} ${label}：相似度 ${similarity.toFixed(3)} → ${accepted ? '放行' : '拒绝'}`)
  if (accepted !== expect) throw new Error(`判定与预期不符：${label}`)
}

async function main() {
  console.log(`声纹模型：${SPEAKER_MODEL}\n`)
  const filter = await VoiceFilter.create({ speakerModel: SPEAKER_MODEL })

  // 场景 1：唤醒词注册（短注册，阈值自动补偿生效）
  await wake(filter, '唤醒「小耘小耘」（主讲人）', readWav(path.join(SAMPLES_DIR, 'enroll_target.wav')).subarray(0, sec(1.2)))
  console.log('')
  // 场景 2：提问段判定（主讲人放行、陌生人拒绝）
  const target = readWav(path.join(SAMPLES_DIR, 'target_clean.wav'))
  const other = readWav(path.join(SAMPLES_DIR, 'other_clean.wav'))
  await question(filter, '主讲人提问（放行回传）', target.subarray(sec(2.8), sec(4.5)), true)
  await question(filter, '陌生人插话（拒绝）', other.subarray(0, sec(2.0)), false)

  filter.dispose()
  console.log('\nPASS：唤醒词流程就绪（内网接入时把 readWav 换成你们 VAD 的输出即可）')
}

main().catch((error) => {
  console.error('FAIL：', error.message)
  process.exit(1)
})
