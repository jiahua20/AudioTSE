<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Activity, CircleStop, FileMusic, FlaskConical, Mic, Radio, RotateCcw, ShieldAlert, Upload, UserRound, Volume2, VolumeX } from '@lucide/vue'
import './App.css'

type SessionState = 'idle' | 'enrolling' | 'ready' | 'extracting'
type ModelOption = { id: string; name: string; description?: string; available: boolean; reason?: string | null }
type TranscriptEntry = { time: string; text: string }
type BannerTone = 'info' | 'ok' | 'warn' | 'error'
type ServerEvent = {
  event: string
  state?: SessionState
  text?: string
  final?: boolean
  message?: string
  asrReady?: boolean
  tseReady?: boolean
  bypassEnabled?: boolean
  asrModels?: ModelOption[]
  processors?: ModelOption[]
  selectedAsr?: string
  selectedProcessor?: string
  similarity?: number | null
  processor?: string
  wallSec?: number
  audioSec?: number
  tseMs?: number | null
  asrMs?: number | null
  backlogSec?: number
  rtf?: number | null
  e2eFirstMs?: number | null
}

const state = ref<SessionState>('idle')
const connected = ref(false)
const asrReady = ref(false)
const tseReady = ref(false)
const bypass = ref(false)
const notice = ref('正在连接本地音频服务…')
const noticeTone = ref<BannerTone>('info')
const transcript = ref<TranscriptEntry[]>([])
const partialText = ref('')
const partialTarget = ref('')
const liveTime = ref('')
const playback = ref(false)  // play separated audio; mic mode defaults off (howl), file mode auto-on
const typePending: string[] = []
let typeTimer: ReturnType<typeof setInterval> | null = null
let playCtx: AudioContext | null = null
let nextStartTime = 0
const asrModels = ref<ModelOption[]>([])
const processors = ref<ModelOption[]>([])
const selectedAsr = ref('')
const selectedProcessor = ref('')
const similarity = ref<number | null>(null)
type Metrics = {
  processor: string
  wallSec: number
  audioSec: number
  tseMs: number | null
  asrMs: number | null
  backlogSec: number
  rtf: number | null
  e2eFirstMs: number | null
}
const metrics = ref<Metrics>({
  processor: '', wallSec: 0, audioSec: 0,
  tseMs: null, asrMs: null, backlogSec: 0, rtf: null, e2eFirstMs: null,
})
const rtfTone = computed<'ok' | 'warn' | 'bad' | 'na'>(() => {
  const r = metrics.value.rtf
  if (r == null) return 'na'
  if (r < 0.85) return 'ok'
  if (r < 1) return 'warn'
  return 'bad'
})
const micLevel = ref(0)
let socket: WebSocket | null = null
let audio: { context: AudioContext; stream: MediaStream } | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let mounted = false

// --- file-source mode (test the pipeline without a microphone) ---
type SourceMode = 'mic' | 'file'
const sourceMode = ref<SourceMode>('mic')
const enrollBuffer = ref<Float32Array | null>(null)
const enrollFileName = ref('')
const mixBuffer = ref<Float32Array | null>(null)
const mixFileName = ref('')
const enrollProgress = ref(0)
const mixProgress = ref(0)
const FILE_FRAME = 2048
const FILE_INTERVAL_MS = (FILE_FRAME / 16000) * 1000
let fileTimer: ReturnType<typeof setInterval> | null = null
let fileKind: 'enroll' | 'mix' | null = null

const busy = computed(() => state.value === 'enrolling' || state.value === 'extracting')
const bannerTitle = computed(() => ({
  info: '系统状态',
  ok: '已就绪',
  warn: '请注意',
  error: '处理出错',
} as const)[noticeTone.value])

function floatToPcm16(samples: Float32Array) {
  const pcm = new Int16Array(samples.length)
  samples.forEach((sample, index) => {
    const limited = Math.max(-1, Math.min(1, sample))
    pcm[index] = limited < 0 ? limited * 0x8000 : limited * 0x7fff
  })
  return pcm.buffer
}

function nowstamp() {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

function withPunct(s: string) {
  return /[。，！？；,!?;.]$/.test(s.trim()) ? s : `${s}。`
}

function toneForProcessor(processor: string): BannerTone {
  if (processor === 'tse') return 'ok'
  if (processor === 'passthrough') return 'warn'
  return 'info'
}

function connectBackend() {
  socket = new WebSocket('ws://127.0.0.1:8765')
  socket.onopen = () => { connected.value = true }
  socket.onclose = () => {
    connected.value = false
    asrReady.value = false
    notice.value = '本地音频服务未启动，正在重试连接…'
    noticeTone.value = 'warn'
    if (mounted) reconnectTimer = setTimeout(connectBackend, 2000)
  }
  socket.onmessage = async ({ data }) => {
    if (data instanceof Blob) {
      if (playback.value) enqueueSeparatedAudio(await data.arrayBuffer())
      return
    }
    const event = JSON.parse(data) as ServerEvent
    if (event.state) state.value = event.state
    if (event.event === 'hello') {
      asrReady.value = Boolean(event.asrReady)
      tseReady.value = Boolean(event.tseReady)
      bypass.value = Boolean(event.bypassEnabled)
      asrModels.value = event.asrModels || []
      processors.value = event.processors || []
      selectedAsr.value = event.selectedAsr || ''
      selectedProcessor.value = event.selectedProcessor || ''
      notice.value = event.message || ''
      noticeTone.value = event.tseReady ? 'ok' : toneForProcessor(selectedProcessor.value)
    }
    if (event.event === 'modelsChanged') {
      bypass.value = Boolean(event.bypassEnabled)
      selectedAsr.value = event.selectedAsr || selectedAsr.value
      selectedProcessor.value = event.selectedProcessor || selectedProcessor.value
      notice.value = selectedProcessor.value === 'tse'
        ? '纯音频 TSE 已启用：WeSep BSRNN 约 3 秒缓冲，中文效果待实测'
        : selectedProcessor.value === 'speaker_gate'
          ? '声纹门控降级已启用：适合轮流说话，不支持重叠语音分离'
          : '原音诊断已启用：所有说话人都会进入识别'
      noticeTone.value = toneForProcessor(selectedProcessor.value)
    }
    if (event.event === 'error') {
      selectedAsr.value = event.selectedAsr || selectedAsr.value
      selectedProcessor.value = event.selectedProcessor || selectedProcessor.value
      notice.value = event.message || '处理失败'
      noticeTone.value = 'error'
    }
    if (event.event === 'metrics') {
      metrics.value = {
        processor: typeof event.processor === 'string' ? event.processor : metrics.value.processor,
        wallSec: event.wallSec ?? 0,
        audioSec: event.audioSec ?? 0,
        tseMs: event.tseMs ?? null,
        asrMs: event.asrMs ?? null,
        backlogSec: event.backlogSec ?? 0,
        rtf: event.rtf ?? null,
        e2eFirstMs: event.e2eFirstMs ?? null,
      }
    }
    if (event.event === 'transcript') {
      if (typeof event.similarity === 'number') similarity.value = event.similarity
      if (event.final) {
        if (typeTimer) { clearInterval(typeTimer); typeTimer = null }
        typePending.length = 0
        const text = (event.text || '').trim()
        if (text) transcript.value.push({ time: liveTime.value || nowstamp(), text: withPunct(text) })
        partialText.value = ''
        partialTarget.value = ''
        liveTime.value = ''
      } else {
        if (!liveTime.value) liveTime.value = nowstamp()
        feedPartial(event.text || '')
      }
    }
  }
}

onMounted(() => {
  mounted = true
  connectBackend()
})

onBeforeUnmount(() => {
  mounted = false
  if (reconnectTimer) clearTimeout(reconnectTimer)
  if (typeTimer) { clearInterval(typeTimer); typeTimer = null }
  stopFileStream()
  stopPlayback()
  socket?.close()
  void stopCapture()
})

function sendCommand(command: string) {
  socket?.send(JSON.stringify({ command }))
}

function selectModels(asrModel: string, processor: string) {
  selectedAsr.value = asrModel
  selectedProcessor.value = processor
  socket?.send(JSON.stringify({ command: 'setModels', asrModel, processor }))
}

async function startCapture() {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } })
  const context = new AudioContext({ sampleRate: 16000 })
  const source = context.createMediaStreamSource(stream)
  const processor = context.createScriptProcessor(2048, 1, 1)
  processor.onaudioprocess = (event) => {
    const data = event.inputBuffer.getChannelData(0)
    let sum = 0
    for (let i = 0; i < data.length; i++) sum += data[i] * data[i]
    micLevel.value = Math.min(1, Math.sqrt(sum / data.length) * 3)
    if (socket?.readyState === WebSocket.OPEN) socket.send(floatToPcm16(data))
  }
  source.connect(processor)
  processor.connect(context.destination)
  audio = { context, stream }
}

async function stopCapture() {
  audio?.stream.getTracks().forEach((track) => track.stop())
  await audio?.context.close()
  audio = null
  micLevel.value = 0
}

async function decodeAudioFile(file: File): Promise<Float32Array> {
  const arrayBuffer = await file.arrayBuffer()
  const context = new AudioContext({ sampleRate: 16000 })
  try {
    const decoded = await context.decodeAudioData(arrayBuffer)
    const channels = decoded.numberOfChannels
    if (channels === 1) return new Float32Array(decoded.getChannelData(0))
    const out = new Float32Array(decoded.length)
    for (let c = 0; c < channels; c++) {
      const data = decoded.getChannelData(c)
      for (let i = 0; i < data.length; i++) out[i] += data[i] / channels
    }
    return out
  } finally {
    await context.close()
  }
}

// --- separated-audio playback + typewriter (so sound and text stream together) ---
watch(sourceMode, (m) => {
  if (m === 'file') { playback.value = true; ensurePlayCtx() }
  else { playback.value = false; stopPlayback() }
})

function ensurePlayCtx(): AudioContext {
  if (!playCtx) {
    playCtx = new AudioContext({ sampleRate: 16000 })
    nextStartTime = playCtx.currentTime
  }
  if (playCtx.state === 'suspended') void playCtx.resume()
  return playCtx
}

function enqueueSeparatedAudio(pcm16: ArrayBuffer) {
  const ctx = ensurePlayCtx()
  const i16 = new Int16Array(pcm16)
  const f32 = new Float32Array(i16.length)
  for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 32768
  const buf = ctx.createBuffer(1, f32.length, 16000)
  buf.copyToChannel(f32, 0)
  const src = ctx.createBufferSource()
  src.buffer = buf
  src.connect(ctx.destination)
  const now = ctx.currentTime
  if (nextStartTime < now) nextStartTime = now  // fell behind: resync to avoid a growing pile-up
  src.start(nextStartTime)
  nextStartTime += buf.duration
}

function stopPlayback() {
  if (playCtx) {
    void playCtx.close().catch(() => {})
    playCtx = null
    nextStartTime = 0
  }
}

function togglePlayback() {
  playback.value = !playback.value
  if (!playback.value) { stopPlayback(); return }
  if (sourceMode.value === 'mic') {
    notice.value = '正在播放分离音频：请戴耳机，否则扬声器声音会被麦克风收回造成啸叫'
    noticeTone.value = 'warn'
  }
  ensurePlayCtx()
}

function feedPartial(text: string) {
  if (text.startsWith(partialText.value)) {
    for (const ch of text.slice(partialText.value.length)) typePending.push(ch)
    partialTarget.value = text
    ensureTypeTimer()
  } else {
    // ASR revised earlier text: snap to it instead of continuing the typewriter
    if (typeTimer) { clearInterval(typeTimer); typeTimer = null }
    typePending.length = 0
    partialText.value = text
    partialTarget.value = text
  }
}

function ensureTypeTimer() {
  if (typeTimer) return
  typeTimer = setInterval(() => {
    if (typePending.length === 0) {
      clearInterval(typeTimer!)
      typeTimer = null
      partialText.value = partialTarget.value
      return
    }
    partialText.value += typePending.shift()
  }, 90)
}

function stopFileStream() {
  if (fileTimer !== null) {
    clearInterval(fileTimer)
    fileTimer = null
  }
  fileKind = null
  micLevel.value = 0
}

// Feed the file into the same WS path the mic uses, at real-time pace, so the
// streaming TSE/ASR behaves as if it were a live capture.
function startFileStream(buffer: Float32Array, kind: 'enroll' | 'mix', onDone: () => void) {
  stopFileStream()
  fileKind = kind
  const progress = kind === 'enroll' ? enrollProgress : mixProgress
  progress.value = 0
  let pos = 0
  fileTimer = setInterval(() => {
    const end = Math.min(pos + FILE_FRAME, buffer.length)
    const chunk = buffer.subarray(pos, end)
    if (socket?.readyState === WebSocket.OPEN) socket.send(floatToPcm16(chunk))
    let sum = 0
    for (let i = 0; i < chunk.length; i++) sum += chunk[i] * chunk[i]
    micLevel.value = Math.min(1, Math.sqrt(sum / (chunk.length || 1)) * 3)
    pos = end
    progress.value = buffer.length ? pos / buffer.length : 1
    if (pos >= buffer.length) {
      stopFileStream()
      onDone()
    }
  }, FILE_INTERVAL_MS)
}

function waitForState(target: SessionState, timeoutMs = 2000): Promise<boolean> {
  return new Promise((resolve) => {
    if (state.value === target) return resolve(true)
    const deadline = Date.now() + timeoutMs
    const timer = setInterval(() => {
      if (state.value === target) {
        clearInterval(timer)
        resolve(true)
      } else if (Date.now() >= deadline) {
        clearInterval(timer)
        resolve(false)
      }
    }, 40)
  })
}

async function onEnrollFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  try {
    enrollBuffer.value = await decodeAudioFile(file)
    enrollFileName.value = file.name
    enrollProgress.value = 0
  } catch {
    notice.value = `无法解析注册音频：${file.name}`
    noticeTone.value = 'error'
  }
  input.value = ''
}

async function onMixFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  try {
    mixBuffer.value = await decodeAudioFile(file)
    mixFileName.value = file.name
    mixProgress.value = 0
  } catch {
    notice.value = `无法解析混合音频：${file.name}`
    noticeTone.value = 'error'
  }
  input.value = ''
}

async function toggleEnrollment() {
  if (state.value === 'enrolling') {
    if (fileKind === 'enroll') stopFileStream()
    else await stopCapture()
    sendCommand('finishEnrollment')
    return
  }
  if (sourceMode.value === 'file' && !enrollBuffer.value) {
    notice.value = '请先选择注册音频文件（测试模式）'
    noticeTone.value = 'warn'
    return
  }
  sendCommand('startEnrollment')
  if (sourceMode.value === 'file') {
    await waitForState('enrolling')
    startFileStream(enrollBuffer.value!, 'enroll', () => sendCommand('finishEnrollment'))
  } else {
    await startCapture()
  }
}

async function toggleExtraction() {
  if (state.value === 'extracting') {
    if (fileKind === 'mix') stopFileStream()
    else await stopCapture()
    sendCommand('stopExtraction')
    return
  }
  if (sourceMode.value === 'file' && !mixBuffer.value) {
    notice.value = '请先选择混合音频文件（测试模式）'
    noticeTone.value = 'warn'
    return
  }
  sendCommand('startExtraction')
  similarity.value = null
  if (sourceMode.value === 'file') {
    await waitForState('extracting')
    startFileStream(mixBuffer.value!, 'mix', () => sendCommand('stopExtraction'))
  } else {
    await startCapture()
  }
}

function clearTranscript() {
  if (typeTimer) { clearInterval(typeTimer); typeTimer = null }
  typePending.length = 0
  transcript.value = []
  partialText.value = ''
  partialTarget.value = ''
  liveTime.value = ''
}

function meterStyle(bar: number) {
  const on = micLevel.value >= (bar - 0.5) / 24
  if (!on) return {}
  const color = bar > 20 ? 'var(--error)' : bar > 16 ? 'var(--warn)' : 'var(--brand)'
  return { backgroundColor: color }
}

function processorLabel(processor: string) {
  if (processor === 'tse') return '纯音频 TSE'
  if (processor === 'speaker_gate') return '声纹降级'
  return '原音诊断'
}
</script>

<template>
  <main class="app-shell">
    <header>
      <div class="brand-mark"><Radio :size="20" /></div>
      <div class="title">
        <strong>AudioTSE</strong>
        <small>目标说话人实时提取台</small>
      </div>
      <div class="connection" :class="{ online: connected }"><i />{{ connected ? '本地服务已连接' : '服务离线' }}</div>
    </header>
    <section class="workspace">
      <aside>
        <div class="section-label">会话流程</div>
        <div class="step" :class="{ active: state === 'enrolling' || state === 'ready' || state === 'extracting', current: state === 'enrolling' }">
          <span class="step-no">01</span>
          <div class="step-body"><b>注册目标声音</b><small>保持自然说话 5–10 秒</small></div>
          <i v-if="state === 'ready' || state === 'extracting'" class="step-done">✓</i>
        </div>
        <div class="step" :class="{ active: state === 'extracting', current: state === 'extracting' }">
          <span class="step-no">02</span>
          <div class="step-body"><b>实时提取与转写</b><small>混合语音进入处理链路</small></div>
        </div>

        <div class="section-label engine-head">引擎状态</div>
        <div class="engine-status">
          <div class="engine-row"><span>中文流式 ASR</span><b :class="{ ok: asrReady }">{{ asrReady ? '就绪' : '缺少模型' }}</b></div>
          <div class="engine-row"><span>纯音频 TSE</span><b :class="{ ok: tseReady }">{{ tseReady ? '实验就绪' : '待安装' }}</b></div>
          <div class="engine-row"><span>当前链路</span><b :class="{ ok: selectedProcessor === 'tse' }">{{ processorLabel(selectedProcessor) }}</b></div>
          <div class="engine-row"><span>运行设备</span><b>CPU</b></div>
        </div>
      </aside>
      <div class="content">
        <div class="banner" :class="{ ok: noticeTone === 'ok', warn: noticeTone === 'warn', error: noticeTone === 'error' }">
          <ShieldAlert :size="18" />
          <div class="banner-text">
            <b>{{ bannerTitle }}</b>
            <span>{{ notice }}</span>
          </div>
        </div>

        <section class="card model-switcher">
          <div class="model-row">
            <div class="model-label"><b>处理模式</b><small>决定哪些语音进入识别</small></div>
            <div class="segments">
              <button
                v-for="processor in processors"
                :key="processor.id"
                :class="{ selected: selectedProcessor === processor.id }"
                :disabled="!processor.available || busy"
                :title="processor.available ? processor.description : processor.reason || processor.description"
                @click="selectModels(selectedAsr, processor.id)"
              >{{ processor.name }}</button>
            </div>
          </div>
          <div class="model-row">
            <div class="model-label"><b>识别模型</b><small>切换后重新开始提取生效</small></div>
            <div class="segments">
              <button
                v-for="model in asrModels"
                :key="model.id"
                :class="{ selected: selectedAsr === model.id }"
                :disabled="!model.available || busy"
                @click="selectModels(model.id, selectedProcessor)"
              >{{ model.name }}</button>
            </div>
          </div>
        </section>

        <section class="card file-panel">
          <div class="file-head">
            <div class="file-title"><FlaskConical :size="15" /><b>测试模式</b><small>用音频文件代替麦克风</small></div>
            <div class="source-toggle">
              <button :class="{ sel: sourceMode === 'mic' }" :disabled="busy" @click="sourceMode = 'mic'">麦克风</button>
              <button :class="{ sel: sourceMode === 'file' }" :disabled="busy" @click="sourceMode = 'file'">音频文件</button>
            </div>
          </div>
          <div v-if="sourceMode === 'file'" class="file-body">
            <p class="file-hint">本地音频按真实速度灌入同一条链路：先用「目标人独唱」注册，再用「混合语音」提取。</p>
            <div class="file-row">
              <label class="file-pick" :class="{ disabled: busy }">
                <Upload :size="13" /> 注册音频
                <input type="file" accept="audio/*,.wav" :disabled="busy" @change="onEnrollFile" />
              </label>
              <span class="file-name">
                <FileMusic v-if="enrollFileName" :size="13" />
                {{ enrollFileName || '未选择 · 目标人独唱 ≥3 秒' }}
              </span>
              <span v-if="fileKind === 'enroll'" class="file-prog">{{ Math.round(enrollProgress * 100) }}%</span>
            </div>
            <div class="file-row">
              <label class="file-pick" :class="{ disabled: busy }">
                <Upload :size="13" /> 混合音频
                <input type="file" accept="audio/*,.wav" :disabled="busy" @change="onMixFile" />
              </label>
              <span class="file-name">
                <FileMusic v-if="mixFileName" :size="13" />
                {{ mixFileName || '未选择 · 目标人 + 他人混合' }}
              </span>
              <span v-if="fileKind === 'mix'" class="file-prog">{{ Math.round(mixProgress * 100) }}%</span>
            </div>
            <p class="file-tip">内置示例：<code>samples/enroll_target.wav</code> + <code>samples/mixed.wav</code>（真实中文：雷军为目标人，混合了另一位说话人；可用 <code>scripts/make-test-audio-zh.py</code> 重新生成）。</p>
          </div>
        </section>

        <section class="card enrollment-panel">
          <div class="portrait" :class="{ recording: state === 'enrolling', ready: state === 'ready' || state === 'extracting' }">
            <UserRound :size="34" />
            <span v-if="state === 'enrolling'" class="portrait-badge"><Mic :size="12" /></span>
          </div>
          <div class="enrollment-copy">
            <div class="eyebrow">TARGET VOICE</div>
            <h1>{{ state === 'ready' || state === 'extracting' ? '目标声音已注册' : '先让系统认识你的声音' }}</h1>
            <p>内容不限。建议在实际使用环境中录制，避免他人同时说话。</p>
            <div class="meter" :class="{ active: busy }">
              <i v-for="bar in 24" :key="bar" :style="meterStyle(bar)" />
            </div>
          </div>
          <button class="btn-primary" :class="{ stop: state === 'enrolling' }" :disabled="!connected || state === 'extracting'" @click="toggleEnrollment">
            <CircleStop v-if="state === 'enrolling'" :size="18" /><Mic v-else :size="18" />{{ state === 'enrolling' ? '完成注册' : '开始注册' }}
          </button>
        </section>

        <section class="card transcript-panel">
          <div class="panel-title">
            <div class="panel-title-left">
              <i class="status-dot" :class="{ pulse: state === 'extracting' }" />
              <b>实时转写</b>
              <span v-if="state === 'extracting'" class="panel-sub">正在监听…</span>
            </div>
            <button class="icon-btn" title="清空转写" @click="clearTranscript"><RotateCcw :size="16" /></button>
          </div>
          <div class="transcript">
            <div v-if="transcript.length || partialText" class="transcript-stream">
              <div v-for="(item, idx) in transcript" :key="idx" class="line">
                <span class="ts">{{ item.time }}</span>
                <span class="line-text">{{ item.text }}</span>
              </div>
              <div v-if="partialText" class="line partial">
                <span class="ts">{{ liveTime }}</span>
                <span class="line-text">{{ partialText }}<em /></span>
              </div>
            </div>
            <div v-else class="empty">
              <div class="wave"><i v-for="bar in 7" :key="bar" /></div>
              <b>等待目标语音</b>
              <span>完成注册后即可开始处理</span>
            </div>
          </div>
          <div class="transcript-footer">
            <span class="meta">16 kHz · 单声道 · PCM16<span v-if="similarity !== null"> · 相似度 <b>{{ similarity.toFixed(2) }}</b></span></span>
            <div class="footer-actions">
              <button class="play-toggle" :class="{ on: playback }" :disabled="!tseReady" @click="togglePlayback" :title="playback ? '关闭分离音频播放' : '播放分离出的目标人声音'">
                <Volume2 v-if="playback" :size="14" /><VolumeX v-else :size="14" />{{ playback ? '播放分离' : '已静音' }}
              </button>
              <button class="btn-listen" :class="{ stop: state === 'extracting' }" :disabled="state !== 'ready' && state !== 'extracting'" @click="toggleExtraction">
                <CircleStop v-if="state === 'extracting'" :size="16" /><Radio v-else :size="16" />{{ state === 'extracting' ? '停止' : '开始提取' }}
              </button>
            </div>
          </div>
        </section>

        <section v-if="metrics.rtf !== null" class="card metrics-panel">
          <div class="metrics-head">
            <div class="metrics-title"><Activity :size="15" /><b>实时性</b><small>RTF &lt; 1 表示处理快于真实速率</small></div>
            <div class="rtf-badge" :class="rtfTone">
              <span class="rtf-label">RTF</span>
              <strong>{{ metrics.rtf != null ? metrics.rtf.toFixed(3) : '—' }}</strong>
            </div>
          </div>
          <div class="metrics-grid">
            <div class="metric"><span>端到端首字</span><b>{{ metrics.e2eFirstMs != null ? Math.round(metrics.e2eFirstMs) + ' ms' : '—' }}</b></div>
            <div class="metric"><span>TSE 单窗耗时</span><b>{{ metrics.tseMs != null ? Math.round(metrics.tseMs) + ' ms' : '—' }}</b></div>
            <div class="metric"><span>ASR 单次耗时</span><b>{{ metrics.asrMs != null ? Math.round(metrics.asrMs) + ' ms' : '—' }}</b></div>
            <div class="metric"><span>缓冲积压</span><b :class="{ bad: metrics.backlogSec > 3.2 }">{{ metrics.backlogSec.toFixed(2) }} s</b></div>
            <div class="metric"><span>已处理音频</span><b>{{ metrics.audioSec.toFixed(1) }} s</b></div>
            <div class="metric"><span>运行墙钟</span><b>{{ metrics.wallSec.toFixed(1) }} s</b></div>
          </div>
        </section>
      </div>
    </section>
  </main>
</template>
