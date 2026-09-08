// core 端到端：模拟内网场景——外部 VAD 已把语音切好成段（这里直接取 wav 的
// 语音区间），SDK 只做「注册 → 逐段判定/过滤」。
// 断言：目标说话人的段 accepted，陌生人的段 rejected；顺带打印重叠语音（已知局限）。
// 用法：npm test（在 sdk/web/core 下）
import * as path from 'node:path'

import { VoiceFilter, SAMPLE_RATE } from '../../src/core/voice-filter'
import { readWavPcm16Mono16k } from './wav'

const ROOT = path.resolve(__dirname, '..', '..', '..', '..') // 仓库根
const SPEAKER_MODEL = path.join(
  ROOT,
  'app/models/sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k/model.onnx',
)
const SAMPLES = path.join(ROOT, 'app/samples')

async function main(): Promise<void> {
  const target = readWavPcm16Mono16k(path.join(SAMPLES, 'target_clean.wav'))
  const other = readWavPcm16Mono16k(path.join(SAMPLES, 'other_clean.wav'))
  const mixed = readWavPcm16Mono16k(path.join(SAMPLES, 'mixed.wav'))

  const t0 = Date.now()
  const filter = await VoiceFilter.create({ speakerModel: SPEAKER_MODEL })
  console.log(`SDK 初始化 ${Date.now() - t0} ms（仅 ER2Net ONNX，无 VAD）`)

  // 注册：取目标说话人开头 ~1.2s（四个字量级，模拟内网 VAD 切出的注册语音）
  const enrollSamples = target.subarray(0, Math.round(1.2 * SAMPLE_RATE))
  const enroll = await filter.enroll(enrollSamples)
  console.log(`注册完成：${enroll.speechSeconds.toFixed(2)} s（生效阈值 ${filter.effectiveThreshold}）\n`)

  // 判定：目标后续语音 / 陌生人语音 / 重叠语音
  const cases: Array<{ label: string; samples: Float32Array; expectAccepted: boolean | null }> = [
    { label: '目标说话人（后续语句）', samples: target.subarray(Math.round(2.8 * SAMPLE_RATE), Math.round(4.5 * SAMPLE_RATE)), expectAccepted: true },
    { label: '目标说话人（另一句）', samples: target.subarray(Math.round(6.0 * SAMPLE_RATE), Math.round(8.0 * SAMPLE_RATE)), expectAccepted: true },
    { label: '陌生人', samples: other.subarray(0, Math.round(2.0 * SAMPLE_RATE)), expectAccepted: false },
    { label: '陌生人（另一段）', samples: other.subarray(Math.round(3.0 * SAMPLE_RATE), Math.round(5.0 * SAMPLE_RATE)), expectAccepted: false },
    { label: '重叠语音（已知局限，只看数字）', samples: mixed.subarray(0, Math.round(3.0 * SAMPLE_RATE)), expectAccepted: null },
  ]

  for (const c of cases) {
    const t1 = Date.now()
    const judge = await filter.judge(c.samples)
    const filtered = await filter.filter(c.samples)
    const ms = Date.now() - t1
    const ok = c.expectAccepted === null ? '' : judge.accepted === c.expectAccepted ? '✅' : '❌'
    console.log(
      `${ok}${c.label}：相似度 ${judge.similarity.toFixed(3)} → ${judge.accepted ? '放行' : '拒绝'}` +
        `（filter 返回 ${filtered === c.samples ? '原语音' : 'null'}，${ms} ms / ${judge.durationSeconds.toFixed(1)} s）`,
    )
    if (c.expectAccepted !== null && judge.accepted !== c.expectAccepted) {
      throw new Error(`判定与预期不符：${c.label} 期望 ${c.expectAccepted ? '放行' : '拒绝'}`)
    }
  }

  await filter.dispose()
  console.log('\nPASS')
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
