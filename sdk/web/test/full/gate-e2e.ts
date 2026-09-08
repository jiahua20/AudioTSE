// 端到端门控测试：
//   场景 A：六个字量级短注册（target_clean 首个语音段，静音已剥）→ 轮流发言应正确放行/拒绝
//   场景 B：更极限的 ~1.2s 注册（打印对照，不断言）
//   场景 C：完整 5s 注册（对照）
//   场景 D：重叠语音（已知局限演示，不断言）
// 用法：npm test（在 sdk/web 下）
import * as path from 'node:path'

import { SpeakerGate, SAMPLE_RATE } from '../../src/full/speaker-gate'
import { createSileroVad } from '../../src/full/vad'

const ROOT = path.resolve(__dirname, '..', '..', '..', '..') // 仓库根
const MODELS = path.join(ROOT, 'app/models')
const VAD_MODEL = path.join(MODELS, 'silero_vad/silero_vad.onnx')
const SPEAKER_MODEL = path.join(
  MODELS,
  'sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k/model.onnx',
)
const SAMPLES = path.join(ROOT, 'app/samples')

// eslint-disable-next-line @typescript-eslint/no-var-requires
const sherpa = require('sherpa-onnx')

function loadWav(name: string): Float32Array {
  const wave = sherpa.readWave(path.join(SAMPLES, `${name}.wav`))
  if (wave.sampleRate !== 16000) throw new Error(`expect 16k, got ${wave.sampleRate}`)
  return wave.samples
}

function silence(seconds: number): Float32Array {
  return new Float32Array(Math.round(seconds * SAMPLE_RATE))
}

function concat(parts: Float32Array[]): Float32Array {
  const total = parts.reduce((n, p) => n + p.length, 0)
  const out = new Float32Array(total)
  let offset = 0
  for (const p of parts) {
    out.set(p, offset)
    offset += p.length
  }
  return out
}

/** 用 VAD 取一段音频里的首个语音段（剥静音，模拟用户「说六个字」的真实注册输入）。 */
function firstSpeechSegment(samples: Float32Array): Float32Array {
  const vad = createSileroVad(VAD_MODEL)
  try {
    vad.acceptWaveform(samples)
    vad.flush()
    if (vad.isEmpty()) throw new Error('音频中没有语音段')
    return vad.front().samples
  } finally {
    vad.free()
  }
}

// 交替发言流：目标(8s) / 陌生人(8s) / 目标(8s)，间隔 0.7s 静音
const target = loadWav('target_clean')
const other = loadWav('other_clean')
const STREAM = concat([silence(0.5), target, silence(0.7), other, silence(0.7), target, silence(0.5)])

/** 段起点 → 说话人标签（按构造流的时间轴：8.5~17.5s 之间是陌生人） */
function labelOf(start: number): 'target' | 'other' {
  const seconds = start / SAMPLE_RATE
  return seconds > 9.0 && seconds < 17.5 ? 'other' : 'target'
}

async function runStream(gate: SpeakerGate) {
  gate.reset()
  const events = []
  const chunk = 1600 // 100 ms 流式喂入
  const t0 = Date.now()
  for (let offset = 0; offset < STREAM.length; offset += chunk) {
    events.push(...(await gate.accept(STREAM.subarray(offset, Math.min(offset + chunk, STREAM.length)))))
  }
  events.push(...(await gate.flush()))
  return { events, wallMs: Date.now() - t0 }
}

/** 断言：陌生人段全部拒绝（相似度 < 阈值）、目标段全部放行；返回两组相似度 */
function assertGating(events: Awaited<ReturnType<typeof runStream>>['events']) {
  const targetSims: number[] = []
  const otherSims: number[] = []
  for (const event of events) {
    const sim = event.similarity
    if (sim === null) throw new Error('已注册状态下 similarity 不应为 null')
    if (labelOf(event.start) === 'other') {
      otherSims.push(sim)
      if (event.accepted) throw new Error(`陌生人段被放行：start=${event.start} sim=${sim.toFixed(3)}`)
    } else {
      targetSims.push(sim)
      if (!event.accepted) throw new Error(`目标段被拒绝：start=${event.start} sim=${sim.toFixed(3)}`)
    }
  }
  if (targetSims.length === 0 || otherSims.length === 0) throw new Error('流中没有同时出现目标和陌生人的段')
  return { targetSims, otherSims }
}

function report(label: string, sims: number[], wallMs: number): void {
  const min = Math.min(...sims).toFixed(3)
  const max = Math.max(...sims).toFixed(3)
  console.log(`  ${label}：${sims.length} 段，相似度 ${min} ~ ${max}（处理 ${wallMs} ms）`)
}

async function main(): Promise<void> {
  const t0 = Date.now()
  const gate = await SpeakerGate.create({ vadModel: VAD_MODEL, speakerModel: SPEAKER_MODEL })
  console.log(`SDK 初始化 ${Date.now() - t0} ms（VAD WASM + 声纹 ONNX）\n`)

  // 场景 A：四~六个字量级短注册（VAD 剥静音后的首个语音段）
  const shortSegment = firstSpeechSegment(target)
  const enrollA = await gate.enroll(shortSegment)
  console.log(
    `场景 A 短注册：净语音 ${enrollA.speechSeconds.toFixed(2)} s（阈值 ${gate.effectiveThreshold}）`,
  )
  const runA = await runStream(gate)
  const gatingA = assertGating(runA.events)
  report('目标段  ', gatingA.targetSims, runA.wallMs)
  report('陌生人段', gatingA.otherSims, runA.wallMs)

  // 场景 B：四个字极限注册 ~1.0s（短注册阈值补偿后应全部正确）
  const tiny = shortSegment.subarray(0, Math.round(1.2 * SAMPLE_RATE))
  const enrollB = await gate.enroll(tiny)
  console.log(
    `\n场景 B 四字级注册 ${enrollB.speechSeconds.toFixed(2)} s（阈值补偿为 ${gate.effectiveThreshold}）`,
  )
  const runB = await runStream(gate)
  const gatingB = assertGating(runB.events)
  report('目标段  ', gatingB.targetSims, runB.wallMs)
  report('陌生人段', gatingB.otherSims, runB.wallMs)

  // 场景 B2：更极限的 ~0.7s（只看数字，不做断言）
  const tinier = shortSegment.subarray(0, Math.round(0.8 * SAMPLE_RATE))
  const enrollB2 = await gate.enroll(tinier)
  const runB2 = await runStream(gate)
  const b2Target: number[] = []
  const b2Other: number[] = []
  let b2Ok = true
  for (const event of runB2.events) {
    if (event.similarity === null) continue
    if (labelOf(event.start) === 'other') {
      b2Other.push(event.similarity)
      if (event.accepted) b2Ok = false
    } else {
      b2Target.push(event.similarity)
      if (!event.accepted) b2Ok = false
    }
  }
  console.log(
    `\n场景 B2 极限注册 ${enrollB2.speechSeconds.toFixed(2)} s（阈值 ${gate.effectiveThreshold}）：` +
      `${b2Ok ? '仍全部正确判定' : '出现误判 ⚠️'}`,
  )
  report('目标段  ', b2Target, runB2.wallMs)
  report('陌生人段', b2Other, runB2.wallMs)

  // 场景 C：完整 5s 注册（对照）
  const enrollC = await gate.enroll(loadWav('enroll_target'))
  console.log(`\n场景 C 完整注册：净语音 ${enrollC.speechSeconds.toFixed(2)} s / 5.0 s 音频`)
  const runC = await runStream(gate)
  const gatingC = assertGating(runC.events)
  report('目标段  ', gatingC.targetSims, runC.wallMs)
  report('陌生人段', gatingC.otherSims, runC.wallMs)

  // 场景 D：重叠语音 —— 门控的固有局限演示
  const mixed = loadWav('mixed')
  gate.reset()
  const mixedEvents = []
  for (let offset = 0; offset < mixed.length; offset += 1600) {
    mixedEvents.push(...(await gate.accept(mixed.subarray(offset, Math.min(offset + 1600, mixed.length)))))
  }
  mixedEvents.push(...(await gate.flush()))
  console.log('\n场景 D 重叠语音 mixed.wav（门控无法分离，相似度介于中间，属已知局限）')
  for (const event of mixedEvents) {
    const sim = event.similarity === null ? 'null' : event.similarity.toFixed(3)
    console.log(
      `  [${(event.start / SAMPLE_RATE).toFixed(2)}s +${event.durationSeconds.toFixed(2)}s] ` +
        `相似度 ${sim} → ${event.accepted ? '✅ 放行' : '❌ 拒绝'}`,
    )
  }

  gate.dispose()
  const rtf = runC.wallMs / 1000 / (STREAM.length / SAMPLE_RATE)
  console.log(`\nPASS（RTF ${rtf.toFixed(3)}）`)
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
