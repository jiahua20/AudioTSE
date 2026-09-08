// 内网「唤醒词注册 + 提问过滤」流程演示（纯 Node，无 Electron 依赖，可直接跑）。
//
// 业务场景：
//   1. 客户向大屏喊「小耘小耘」→ 唤醒，这句语音同时做声纹注册（每次唤醒都重新注册）
//   2. 之后客户的提问/其他语音逐段过滤，只把主讲人（注册者）的语音回传给业务
//
// 本 demo 用仓库样例 wav 扮演内网音频流并自校验判定是否正确；接内网时把
// readWav/切段换成你们 VAD 输出的 Float32Array（16k 单声道 [-1,1]）即可。
//
// 运行（在 sdk/web 下，先 npm run build）：
//   node examples/intranet-wake-flow.js
// 可用环境变量换模型/音频目录：AUDIOTSE_SPEAKER_MODEL、AUDIOTSE_SAMPLES
const fs = require('node:fs')
const path = require('node:path')
const { VoiceFilter } = require('../dist/core')

const REPO_ROOT = path.resolve(__dirname, '..', '..', '..') // 仓库根（demo 借用 app 的模型与样例）
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
  if (!data || channels !== 1 || sampleRate !== SR) {
    throw new Error(`需要 16k 单声道 PCM16 wav: ${file}`)
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
  const t = Date.now()
  const { similarity, accepted } = await filter.judge(samples)
  // 要语音回传时直接用 filter()：返回主讲人语音（原引用）或 null，判定逻辑相同
  // const targetSpeech = await filter.filter(samples)
  const mark = accepted === expect ? '✅' : '❌'
  console.log(
    `   ${mark} ${label}：相似度 ${similarity.toFixed(3)} → ${accepted ? '放行（回传语音）' : '拒绝（返回 null）'}  [${Date.now() - t} ms]`,
  )
  if (accepted !== expect) throw new Error(`判定与预期不符：${label}`)
}

async function main() {
  const t0 = Date.now()
  const filter = await VoiceFilter.create({ speakerModel: SPEAKER_MODEL })
  console.log(`[init] 声纹模型加载 ${Date.now() - t0} ms\n`)

  // 音频源（扮演内网 VAD 切好的段）：target_clean = 说话人 A，other_clean = 说话人 B
  const speakerA = readWav(path.join(SAMPLES_DIR, 'target_clean.wav'))
  const speakerB = readWav(path.join(SAMPLES_DIR, 'other_clean.wav'))

  console.log('── 唤醒轮次 1：客户 A 喊唤醒词 ──')
  await wake(filter, 'A 喊「小耘小耘」', speakerA.subarray(0, sec(1.2)))
  await question(filter, 'A 提问', speakerA.subarray(sec(2.8), sec(4.5)), true)
  await question(filter, 'B 插话', speakerB.subarray(0, sec(2.0)), false)
  await question(filter, 'A 再问', speakerA.subarray(sec(6.0), sec(8.0)), true)

  console.log('\n── 唤醒轮次 2：换 B 喊唤醒词 → 每次唤醒都重新注册，声纹刷新为新说话人 ──')
  await wake(filter, 'B 喊「小耘小耘」', speakerB.subarray(0, sec(1.2)))
  await question(filter, 'B 提问', speakerB.subarray(sec(3.0), sec(5.0)), true)
  await question(filter, 'A 说话（此刻已成为旁人）', speakerA.subarray(sec(2.8), sec(4.5)), false)

  await filter.dispose()
  console.log('\nPASS：两轮唤醒-过滤流程全部判定正确')
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
