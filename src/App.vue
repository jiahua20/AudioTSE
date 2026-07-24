<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { CircleStop, Mic, Radio, RotateCcw, ShieldAlert, UserRound } from '@lucide/vue'
import './App.css'

type SessionState = 'idle' | 'enrolling' | 'ready' | 'extracting'
type ModelOption = { id: string; name: string; description?: string; available: boolean; reason?: string | null }
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
}

const state = ref<SessionState>('idle')
const connected = ref(false)
const asrReady = ref(false)
const tseReady = ref(false)
const bypass = ref(false)
const notice = ref('正在连接本地音频服务…')
const finalText = ref('')
const partialText = ref('')
const asrModels = ref<ModelOption[]>([])
const processors = ref<ModelOption[]>([])
const selectedAsr = ref('')
const selectedProcessor = ref('')
const similarity = ref<number | null>(null)
let socket: WebSocket | null = null
let audio: { context: AudioContext; stream: MediaStream } | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let mounted = false

function floatToPcm16(samples: Float32Array) {
  const pcm = new Int16Array(samples.length)
  samples.forEach((sample, index) => {
    const limited = Math.max(-1, Math.min(1, sample))
    pcm[index] = limited < 0 ? limited * 0x8000 : limited * 0x7fff
  })
  return pcm.buffer
}

function connectBackend() {
  socket = new WebSocket('ws://127.0.0.1:8765')
  socket.onopen = () => { connected.value = true }
  socket.onclose = () => {
    connected.value = false
    asrReady.value = false
    notice.value = '本地音频服务未启动'
    if (mounted) reconnectTimer = setTimeout(connectBackend, 2000)
  }
  socket.onmessage = ({ data }) => {
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
    }
    if (event.event === 'error') {
      selectedAsr.value = event.selectedAsr || selectedAsr.value
      selectedProcessor.value = event.selectedProcessor || selectedProcessor.value
      notice.value = event.message || '处理失败'
    }
    if (event.event === 'transcript') {
      if (typeof event.similarity === 'number') similarity.value = event.similarity
      if (event.final) {
        finalText.value += `${event.text || ''}。`
        partialText.value = ''
      } else {
        partialText.value = event.text || ''
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
    if (socket?.readyState === WebSocket.OPEN) socket.send(floatToPcm16(event.inputBuffer.getChannelData(0)))
  }
  source.connect(processor)
  processor.connect(context.destination)
  audio = { context, stream }
}

async function stopCapture() {
  audio?.stream.getTracks().forEach((track) => track.stop())
  await audio?.context.close()
  audio = null
}

async function toggleEnrollment() {
  if (state.value === 'enrolling') {
    await stopCapture()
    sendCommand('finishEnrollment')
  } else {
    sendCommand('startEnrollment')
    await startCapture()
  }
}

async function toggleExtraction() {
  if (state.value === 'extracting') {
    await stopCapture()
    sendCommand('stopExtraction')
  } else {
    sendCommand('startExtraction')
    similarity.value = null
    await startCapture()
  }
}

function clearTranscript() {
  finalText.value = ''
  partialText.value = ''
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
      <div><strong>AudioTSE</strong><span>目标说话人实时提取台</span></div>
      <div class="connection" :class="{ online: connected }"><i />{{ connected ? '本地服务已连接' : '服务离线' }}</div>
    </header>
    <section class="workspace">
      <aside>
        <div class="section-label">会话流程</div>
        <div class="step" :class="{ active: state === 'enrolling' }"><span>01</span><div><b>注册目标声音</b><small>保持自然说话 5–10 秒</small></div></div>
        <div class="step" :class="{ active: state === 'ready' || state === 'extracting' }"><span>02</span><div><b>实时提取与转写</b><small>混合语音进入处理链路</small></div></div>
        <div class="engine-status">
          <div><span>中文流式 ASR</span><b :class="{ ok: asrReady }">{{ asrReady ? '就绪' : '缺少模型' }}</b></div>
          <div><span>纯音频 TSE</span><b :class="{ ok: tseReady }">{{ tseReady ? '实验就绪' : '待安装' }}</b></div>
          <div><span>当前链路</span><b :class="{ ok: selectedProcessor === 'tse' }">{{ processorLabel(selectedProcessor) }}</b></div>
          <div><span>运行设备</span><b>CPU</b></div>
        </div>
      </aside>
      <div class="content">
        <div class="warning"><ShieldAlert :size="20" /><span><b>模型状态</b>{{ notice }}</span></div>
        <section class="model-switcher">
          <div class="model-row">
            <span><b>处理模式</b><small>决定哪些语音进入识别</small></span>
            <div class="segments">
              <button
                v-for="processor in processors"
                :key="processor.id"
                :class="{ selected: selectedProcessor === processor.id }"
                :disabled="!processor.available || state === 'enrolling' || state === 'extracting'"
                :title="processor.available ? processor.description : processor.reason || processor.description"
                @click="selectModels(selectedAsr, processor.id)"
              >{{ processor.name }}</button>
            </div>
          </div>
          <div class="model-row">
            <span><b>识别模型</b><small>切换后重新开始提取生效</small></span>
            <div class="segments">
              <button
                v-for="model in asrModels"
                :key="model.id"
                :class="{ selected: selectedAsr === model.id }"
                :disabled="!model.available || state === 'enrolling' || state === 'extracting'"
                @click="selectModels(model.id, selectedProcessor)"
              >{{ model.name }}</button>
            </div>
          </div>
        </section>
        <section class="enrollment-panel">
          <div class="portrait" :class="{ recording: state === 'enrolling' }"><UserRound :size="35" /></div>
          <div class="enrollment-copy">
            <div class="eyebrow">TARGET VOICE</div>
            <h1>{{ state === 'ready' || state === 'extracting' ? '目标声音已注册' : '先让系统认识你的声音' }}</h1>
            <p>内容不限。建议在实际使用环境中录制，避免他人同时说话。</p>
          </div>
          <button class="primary" :class="{ stop: state === 'enrolling' }" :disabled="!connected || state === 'extracting'" @click="toggleEnrollment">
            <CircleStop v-if="state === 'enrolling'" :size="18" /><Mic v-else :size="18" />{{ state === 'enrolling' ? '完成注册' : '开始注册' }}
          </button>
        </section>
        <section class="transcript-panel">
          <div class="panel-title"><div><i :class="{ pulse: state === 'extracting' }" /><b>实时转写</b></div><button title="清空转写" @click="clearTranscript"><RotateCcw :size="17" /></button></div>
          <div class="transcript">
            <p v-if="finalText || partialText">{{ finalText }}<span>{{ partialText }}</span><em /></p>
            <div v-else class="empty"><div class="wave"><i v-for="bar in 7" :key="bar" /></div><b>等待目标语音</b><span>完成注册后即可开始处理</span></div>
          </div>
          <div class="transcript-footer"><span>16 kHz · 单声道 · PCM16<span v-if="similarity !== null"> · 声纹相似度 {{ similarity.toFixed(3) }}</span></span><button class="listen" :disabled="state !== 'ready' && state !== 'extracting'" @click="toggleExtraction"><CircleStop v-if="state === 'extracting'" :size="16" /><Radio v-else :size="16" />{{ state === 'extracting' ? '停止' : '开始提取' }}</button></div>
        </section>
      </div>
    </section>
  </main>
</template>

<style scoped>
.model-switcher {
  margin: 22px 0;
  border-block: 1px solid var(--line);
  background: color-mix(in srgb, var(--paper) 72%, transparent);
}
.model-row {
  min-height: 66px;
  display: grid;
  grid-template-columns: 180px 1fr;
  align-items: center;
  gap: 20px;
}
.model-row + .model-row { border-top: 1px solid var(--line); }
.model-row > span b, .model-row > span small { display: block; }
.model-row > span b { font-size: 12px; }
.model-row > span small { margin-top: 3px; color: var(--muted); font-size: 10px; }
.segments {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  border: 1px solid #cfd3cd;
  border-radius: 6px;
  overflow: hidden;
}
.segments button {
  min-height: 34px;
  padding: 6px 12px;
  border: 0;
  background: transparent;
  color: var(--muted);
  font-size: 11px;
  cursor: pointer;
}
.segments button + button { border-left: 1px solid #cfd3cd; }
.segments button.selected { background: var(--green); color: white; }
.segments button:disabled { cursor: not-allowed; opacity: .42; }
.segments button.selected:disabled { opacity: .72; }
@media (max-width: 760px) {
  .model-row { grid-template-columns: 1fr; gap: 8px; padding: 12px 0; }
}
</style>