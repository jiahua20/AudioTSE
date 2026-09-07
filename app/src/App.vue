<script setup lang="ts">
// 前端唯一根组件：WebSocket 客户端 + 麦克风/文件采集 + 字幕界面。
// 职责：把音频帧（二进制）发给后端、接收后端事件（hello/state/transcript/
// metrics/…）驱动 UI，并把后端回传的分离音频排队播放。
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Activity, CircleStop, FileMusic, FlaskConical, Mic, Radio, RotateCcw, ShieldAlert, Upload, UserRound, Volume2, VolumeX } from '@lucide/vue'
import './App.css'

// ---- 与后端协议对应的类型定义 ----
type SessionState = 'idle' | 'enrolling' | 'ready' | 'extracting'  // 会话状态机的四个值
type ModelOption = { id: string; name: string; description?: string; available: boolean; reason?: string | null }  // hello 事件里的模型/链路选项
type TranscriptEntry = { time: string; text: string }  // 一条已定稿的字幕（时间戳 + 文本）
type BannerTone = 'info' | 'ok' | 'warn' | 'error'     // 顶部通知条的四种语气
type ServerEvent = {   // 后端所有事件字段的并集（按事件只用到其中一部分）
  event: string
  state?: SessionState
  text?: string
  final?: boolean
  message?: string
  asrReady?: boolean
  tseReady?: boolean
  ready?: boolean
  reason?: string
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
  droppedSec?: number
  silentWindows?: number
}

// ---- 响应式状态 ----
const state = ref<SessionState>('idle')          // 镜像后端状态机
const connected = ref(false)                     // WebSocket 是否连上
const asrReady = ref(false)                      // ASR 模型是否就绪（hello 事件）
const tseReady = ref(false)                      // TSE 权重/依赖是否已安装（hello 事件）
const tseEngineReady = ref(false)  // 分离引擎(262MB 权重)是否已在后台加载+预热完成
const bypass = ref(false)                        // 是否处于原音直通模式（UI 微调用）
const notice = ref('正在连接本地音频服务…')       // 顶部通知文本
const noticeTone = ref<BannerTone>('info')       // 通知语气（决定配色）
const transcript = ref<TranscriptEntry[]>([])    // 已定稿字幕列表
const partialText = ref('')                      // 打字机动画中已显示的草稿文本
const partialTarget = ref('')                    // 草稿的目标文本（动画追赶的终点）
const liveTime = ref('')                         // 当前草稿行的时间戳
const playback = ref(false)  // 播放分离后的音频；麦克风模式默认关闭（防止啸叫），文件模式自动开启
const typePending: string[] = []                 // 打字机待吐出的字符队列
let typeTimer: ReturnType<typeof setInterval> | null = null  // 打字机定时器句柄
let playCtx: AudioContext | null = null          // 分离音频回放的 AudioContext
let nextStartTime = 0                            // 回放队列里下一个块的计划开播时刻（无缝衔接）
const asrModels = ref<ModelOption[]>([])         // 可选 ASR 列表（hello 填充）
const processors = ref<ModelOption[]>([])        // 可选处理链路列表（hello 填充）
const selectedAsr = ref('')                      // 当前选中的 ASR
const selectedProcessor = ref('')                // 当前选中的处理链路
const similarity = ref<number | null>(null)      // 门控链路最近一次声纹相似度
type Metrics = {   // metrics 事件的镜像（性能面板）
  processor: string
  wallSec: number
  audioSec: number
  tseMs: number | null
  asrMs: number | null
  backlogSec: number
  droppedSec: number
  silentWindows: number
  rtf: number | null
  e2eFirstMs: number | null
}
const metrics = ref<Metrics>({
  processor: '', wallSec: 0, audioSec: 0,
  tseMs: null, asrMs: null, backlogSec: 0, droppedSec: 0, silentWindows: 0, rtf: null, e2eFirstMs: null,
})
// RTF 颜色分级：<0.85 绿（轻松实时）、<1 黄（勉强跟上）、≥1 红（跟不上）、无数据灰
const rtfTone = computed<'ok' | 'warn' | 'bad' | 'na'>(() => {
  const r = metrics.value.rtf
  if (r == null) return 'na'
  if (r < 0.85) return 'ok'
  if (r < 1) return 'warn'
  return 'bad'
})
const micLevel = ref(0)                          // 麦克风/文件灌入的音量电平（0~1，驱动音量条）
let socket: WebSocket | null = null              // 后端连接
let audio: { context: AudioContext; stream: MediaStream } | null = null  // 麦克风采集句柄
let reconnectTimer: ReturnType<typeof setTimeout> | null = null          // 断线重连定时器
let mounted = false                              // 组件是否仍挂载（防卸载后重启定时器）

// --- 文件音源模式（无需麦克风即可测试整条流水线） ---
type SourceMode = 'mic' | 'file'                 // 音源：真实麦克风 / 本地音频文件
const sourceMode = ref<SourceMode>('mic')
const enrollBuffer = ref<Float32Array | null>(null)  // 解码后的注册音频（目标人独唱）
const enrollFileName = ref('')                       // 注册文件名（UI 展示）
const mixBuffer = ref<Float32Array | null>(null)     // 解码后的混合音频（提取阶段灌入）
const mixFileName = ref('')                          // 混合文件名
const enrollProgress = ref(0)                    // 注册文件灌入进度（0~1）
const mixProgress = ref(0)                       // 混合文件灌入进度（0~1）
const FILE_FRAME = 2048                          // 与麦克风一致的每块采样数（128 ms）
const FILE_INTERVAL_MS = (FILE_FRAME / 16000) * 1000  // 灌入间隔：按真实速率回放
let fileTimer: ReturnType<typeof setInterval> | null = null  // 灌入定时器
let fileKind: 'enroll' | 'mix' | null = null     // 当前正在灌入的文件类型

// 注册中或提取中 = 「忙」：禁用切换模型/文件等操作
const busy = computed(() => state.value === 'enrolling' || state.value === 'extracting')
// TSE 引擎在后台加载时禁用「开始注册」：把权重加载那约 5 秒挡在注册之前，
// 这样点「开始提取」时引擎已热，不再卡在加载上。
const tseLoading = computed(() => selectedProcessor.value === 'tse' && !tseEngineReady.value)
const enrollDisabled = computed(() => !connected.value || state.value === 'extracting' || tseLoading.value)
// 注册按钮的三态文案：注册中→完成 / 引擎加载中→加载模型 / 否则→开始注册
const enrollLabel = computed(() => {
  if (state.value === 'enrolling') return '完成注册'
  if (tseLoading.value) return '加载模型中…'
  return '开始注册'
})
// 通知条标题按语气映射
const bannerTitle = computed(() => ({
  info: '系统状态',
  ok: '已就绪',
  warn: '请注意',
  error: '处理出错',
} as const)[noticeTone.value])

// float32 采样 → PCM16 ArrayBuffer（与后端约定的二进制帧格式）
function floatToPcm16(samples: Float32Array) {
  const pcm = new Int16Array(samples.length)
  samples.forEach((sample, index) => {
    const limited = Math.max(-1, Math.min(1, sample))      // 限幅到 [-1,1]
    pcm[index] = limited < 0 ? limited * 0x8000 : limited * 0x7fff  // 负值满偏 32768，正值 32767
  })
  return pcm.buffer
}

// 当前时刻的 HH:MM:SS（字幕时间戳用）
function nowstamp() {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

// 定稿字幕若不以标点结尾则补句号（视觉统一）
function withPunct(s: string) {
  return /[。，！？；,!?;.]$/.test(s.trim()) ? s : `${s}。`
}

// 链路对应的默认通知语气：TSE 绿 / 直通黄（警告不筛选）/ 门控蓝
function toneForProcessor(processor: string): BannerTone {
  if (processor === 'tse') return 'ok'
  if (processor === 'passthrough') return 'warn'
  return 'info'
}

// 建立并维护到后端的 WebSocket 连接（断线 2 秒后自动重连）
function connectBackend() {
  socket = new WebSocket('ws://127.0.0.1:8765')
  socket.onopen = () => { connected.value = true }
  socket.onclose = () => {
    connected.value = false
    asrReady.value = false
    tseEngineReady.value = false
    notice.value = '本地音频服务未启动，正在重试连接…'
    noticeTone.value = 'warn'
    if (mounted) reconnectTimer = setTimeout(connectBackend, 2000)  // 组件还活着才重连
  }
  socket.onmessage = async ({ data }) => {
    // 二进制消息 = 后端回传的音频帧（分离音/放行段/原音），只用于回放
    if (data instanceof Blob) {
      if (playback.value) enqueueSeparatedAudio(await data.arrayBuffer())
      return
    }
    // 文本消息 = JSON 事件，统一在此分发
    const event = JSON.parse(data) as ServerEvent
    if (event.state) state.value = event.state  // 任何带 state 的事件都同步状态机
    if (event.event === 'hello') {
      // 连接握手：拿到模型可用性、选项列表与默认选择
      asrReady.value = Boolean(event.asrReady)
      tseReady.value = Boolean(event.tseReady)
      tseEngineReady.value = false  // 新连接：引擎尚未加载，等 tseEngineReady 事件
      bypass.value = Boolean(event.bypassEnabled)
      asrModels.value = event.asrModels || []
      processors.value = event.processors || []
      selectedAsr.value = event.selectedAsr || ''
      selectedProcessor.value = event.selectedProcessor || ''
      notice.value = event.message || ''
      noticeTone.value = event.tseReady ? 'ok' : toneForProcessor(selectedProcessor.value)
    }
    if (event.event === 'tseEngineReady') {
      // TSE 引擎后台预加载结果：ready 才允许注册
      tseEngineReady.value = Boolean(event.ready)
      if (event.ready) {
        notice.value = '分离模型已加载完成，可以开始注册了'
        noticeTone.value = 'ok'
      } else if (event.reason) {
        notice.value = `分离模型加载失败：${event.reason}`
        noticeTone.value = 'error'
      }
    }
    if (event.event === 'modelsChanged') {
      // setModels 成功：同步新选择并重置引擎就绪标志（切到 TSE 时后端会重新预加载）
      bypass.value = Boolean(event.bypassEnabled)
      selectedAsr.value = event.selectedAsr || selectedAsr.value
      selectedProcessor.value = event.selectedProcessor || selectedProcessor.value
      tseEngineReady.value = false  // 切换链路后重置；切到 TSE 时后端会重新预加载并通知
      notice.value = selectedProcessor.value === 'tse'
        ? '纯音频 TSE 已启用：WeSep BSRNN 约 1.7 秒缓冲，正在后台加载分离模型…'
        : selectedProcessor.value === 'speaker_gate'
          ? '声纹门控降级已启用：适合轮流说话，不支持重叠语音分离'
          : '原音诊断已启用：所有说话人都会进入识别'
      noticeTone.value = toneForProcessor(selectedProcessor.value)
    }
    if (event.event === 'error') {
      // 后端业务错误：展示消息并同步当前选择（可能被回滚）
      selectedAsr.value = event.selectedAsr || selectedAsr.value
      selectedProcessor.value = event.selectedProcessor || selectedProcessor.value
      notice.value = event.message || '处理失败'
      noticeTone.value = 'error'
    }
    if (event.event === 'metrics') {
      // 性能面板数据整体替换
      metrics.value = {
        processor: typeof event.processor === 'string' ? event.processor : metrics.value.processor,
        wallSec: event.wallSec ?? 0,
        audioSec: event.audioSec ?? 0,
        tseMs: event.tseMs ?? null,
        asrMs: event.asrMs ?? null,
        backlogSec: event.backlogSec ?? 0,
        droppedSec: event.droppedSec ?? 0,
        silentWindows: event.silentWindows ?? 0,
        rtf: event.rtf ?? null,
        e2eFirstMs: event.e2eFirstMs ?? null,
      }
    }
    if (event.event === 'transcript') {
      // 转写事件：final=true 定稿入列；否则进打字机草稿
      if (typeof event.similarity === 'number') similarity.value = event.similarity
      if (event.final) {
        if (typeTimer) { clearInterval(typeTimer); typeTimer = null }  // 停掉打字机
        typePending.length = 0
        const text = (event.text || '').trim()
        if (text) transcript.value.push({ time: liveTime.value || nowstamp(), text: withPunct(text) })
        partialText.value = ''   // 清空草稿区
        partialTarget.value = ''
        liveTime.value = ''
      } else {
        if (!liveTime.value) liveTime.value = nowstamp()  // 草稿行首次出现时定格时间戳
        feedPartial(event.text || '')
      }
    }
  }
}

onMounted(() => {
  mounted = true
  connectBackend()      // 组件挂载即连接后端
})

onBeforeUnmount(() => {
  // 卸载清理：停掉所有定时器/采集/回放/连接
  mounted = false
  if (reconnectTimer) clearTimeout(reconnectTimer)
  if (typeTimer) { clearInterval(typeTimer); typeTimer = null }
  stopFileStream()
  stopPlayback()
  socket?.close()
  void stopCapture()
})

// 发送无参数命令（startEnrollment / finishEnrollment / startExtraction / stopExtraction）
function sendCommand(command: string) {
  socket?.send(JSON.stringify({ command }))
}

// 切换 ASR + 处理链路（乐观更新本地选择，后端确认后以 modelsChanged 为准）
function selectModels(asrModel: string, processor: string) {
  selectedAsr.value = asrModel
  selectedProcessor.value = processor
  socket?.send(JSON.stringify({ command: 'setModels', asrModel, processor }))
}

// 开启麦克风采集：getUserMedia → 16kHz AudioContext → ScriptProcessor 逐帧发送
async function startCapture() {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } })
  const context = new AudioContext({ sampleRate: 16000 })             // 强制 16 kHz
  const source = context.createMediaStreamSource(stream)
  const processor = context.createScriptProcessor(2048, 1, 1)         // 每块 2048 采样 = 128 ms（与后端 FRAME_MS 对齐）
  processor.onaudioprocess = (event) => {
    const data = event.inputBuffer.getChannelData(0)
    let sum = 0
    for (let i = 0; i < data.length; i++) sum += data[i] * data[i]    // RMS 音量
    micLevel.value = Math.min(1, Math.sqrt(sum / data.length) * 3)    // ×3 放大显示灵敏度
    if (socket?.readyState === WebSocket.OPEN) socket.send(floatToPcm16(data))  // 帧发后端
  }
  source.connect(processor)
  processor.connect(context.destination)  // Safari 需要连 destination 才回调；输出已被静音策略处理
  audio = { context, stream }
}

// 停止麦克风采集并释放设备
async function stopCapture() {
  audio?.stream.getTracks().forEach((track) => track.stop())
  await audio?.context.close()
  audio = null
  micLevel.value = 0
}

// 解码任意音频文件 → 16 kHz 单声道 float32（多声道取平均）
async function decodeAudioFile(file: File): Promise<Float32Array> {
  const arrayBuffer = await file.arrayBuffer()
  const context = new AudioContext({ sampleRate: 16000 })
  try {
    const decoded = await context.decodeAudioData(arrayBuffer)
    const channels = decoded.numberOfChannels
    if (channels === 1) return new Float32Array(decoded.getChannelData(0))
    const out = new Float32Array(decoded.length)
    for (let c = 0; c < channels; c++) {           // 多声道 → 逐采样求平均（下混）
      const data = decoded.getChannelData(c)
      for (let i = 0; i < data.length; i++) out[i] += data[i] / channels
    }
    return out
  } finally {
    await context.close()  // 用完即关，释放音频资源
  }
}

// --- 分离音频播放 + 打字机效果（让声音和文字同步流出） ---
// 音源切到文件模式自动开回放（无啸叫风险）；切回麦克风默认静音
watch(sourceMode, (m) => {
  if (m === 'file') { playback.value = true; ensurePlayCtx() }
  else { playback.value = false; stopPlayback() }
})

// 惰性创建回放 AudioContext；被浏览器自动挂起时（如未交互）尝试恢复
function ensurePlayCtx(): AudioContext {
  if (!playCtx) {
    playCtx = new AudioContext({ sampleRate: 16000 })
    nextStartTime = playCtx.currentTime
  }
  if (playCtx.state === 'suspended') void playCtx.resume()
  return playCtx
}

// 把一段 PCM16 分离音频排进播放队列（按时间无缝衔接，落后时重新对齐）
function enqueueSeparatedAudio(pcm16: ArrayBuffer) {
  const ctx = ensurePlayCtx()
  const i16 = new Int16Array(pcm16)
  const f32 = new Float32Array(i16.length)
  for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 32768  // PCM16 → float32
  const buf = ctx.createBuffer(1, f32.length, 16000)
  buf.copyToChannel(f32, 0)
  const src = ctx.createBufferSource()
  src.buffer = buf
  src.connect(ctx.destination)
  const now = ctx.currentTime
  if (nextStartTime < now) nextStartTime = now  // 已落后：重新对齐到当前时间，避免积压越堆越多
  src.start(nextStartTime)          // 排在队列尾（或立即，若已落后）
  nextStartTime += buf.duration     // 下一个块的计划开播点顺延本块时长
}

// 关闭回放上下文（停止一切排队播放）
function stopPlayback() {
  if (playCtx) {
    void playCtx.close().catch(() => {})
    playCtx = null
    nextStartTime = 0
  }
}

// 手动开关回放；麦克风模式下打开时提醒戴耳机（防啸叫）
function togglePlayback() {
  playback.value = !playback.value
  if (!playback.value) { stopPlayback(); return }
  if (sourceMode.value === 'mic') {
    notice.value = '正在播放分离音频：请戴耳机，否则扬声器声音会被麦克风收回造成啸叫'
    noticeTone.value = 'warn'
  }
  ensurePlayCtx()
}

// 打字机：新文本是旧文本的延伸 → 把新增字符逐个排队；否则（ASR 改稿）直接跳变
function feedPartial(text: string) {
  if (text.startsWith(partialText.value)) {
    for (const ch of text.slice(partialText.value.length)) typePending.push(ch)
    partialTarget.value = text
    ensureTypeTimer()
  } else {
    // ASR 修订了之前的文本：直接跳到最新结果，不再继续打字机动画
    if (typeTimer) { clearInterval(typeTimer); typeTimer = null }
    typePending.length = 0
    partialText.value = text
    partialTarget.value = text
  }
}

// 启动打字机定时器：每 90 ms 吐一个字符，追平后自动停表
function ensureTypeTimer() {
  if (typeTimer) return
  typeTimer = setInterval(() => {
    if (typePending.length === 0) {
      clearInterval(typeTimer!)
      typeTimer = null
      partialText.value = partialTarget.value  // 队列空：直接对齐目标
      return
    }
    partialText.value += typePending.shift()
  }, 90)
}

// 停止文件灌入定时器并复位进度条
function stopFileStream() {
  if (fileTimer !== null) {
    clearInterval(fileTimer)
    fileTimer = null
  }
  fileKind = null
  micLevel.value = 0
}

// 把文件按真实速度灌入麦克风所用的同一条 WebSocket 路径，
// 让流式 TSE/ASR 像处理实时采集一样工作。
function startFileStream(buffer: Float32Array, kind: 'enroll' | 'mix', onDone: () => void) {
  stopFileStream()
  fileKind = kind
  const progress = kind === 'enroll' ? enrollProgress : mixProgress  // 对应的进度条引用
  progress.value = 0
  let pos = 0                       // 已灌入的采样位置
  fileTimer = setInterval(() => {
    const end = Math.min(pos + FILE_FRAME, buffer.length)
    const chunk = buffer.subarray(pos, end)   // 取一帧（不拷贝）
    if (socket?.readyState === WebSocket.OPEN) socket.send(floatToPcm16(chunk))  // 与麦克风同格式发送
    let sum = 0
    for (let i = 0; i < chunk.length; i++) sum += chunk[i] * chunk[i]
    micLevel.value = Math.min(1, Math.sqrt(sum / (chunk.length || 1)) * 3)  // 驱动音量条
    pos = end
    progress.value = buffer.length ? pos / buffer.length : 1
    if (pos >= buffer.length) {     // 灌完：停表并回调（通常是发 finish/stop 命令）
      stopFileStream()
      onDone()
    }
  }, FILE_INTERVAL_MS)
}

// 轮询等待后端状态机到达目标态（带超时），用于命令发送后的同步点
function waitForState(target: SessionState, timeoutMs = 2000): Promise<boolean> {
  return new Promise((resolve) => {
    if (state.value === target) return resolve(true)  // 已到位
    const deadline = Date.now() + timeoutMs
    const timer = setInterval(() => {
      if (state.value === target) {
        clearInterval(timer)
        resolve(true)
      } else if (Date.now() >= deadline) {
        clearInterval(timer)
        resolve(false)  // 超时：调用方决定后续（一般是不开始采集）
      }
    }, 40)
  })
}

// 选择注册音频文件：解码暂存，等点「开始注册」时再灌入
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
  input.value = ''  // 允许重复选择同一文件
}

// 选择混合音频文件：同上
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

// 「开始/完成注册」按钮：注册中 → 收尾；否则开始（文件灌入或开麦）
async function toggleEnrollment() {
  if (state.value === 'enrolling') {
    // 正在注册：停掉音源（文件定时器或麦克风），通知后端收尾
    if (fileKind === 'enroll') stopFileStream()
    else await stopCapture()
    sendCommand('finishEnrollment')
    return
  }
  // 文件模式必须先选好注册文件
  if (sourceMode.value === 'file' && !enrollBuffer.value) {
    notice.value = '请先选择注册音频文件（测试模式）'
    noticeTone.value = 'warn'
    return
  }
  sendCommand('startEnrollment')
  if (sourceMode.value === 'file') {
    // 文件模式：等后端进入 enrolling 再开始灌（否则开头几帧会被丢弃）
    await waitForState('enrolling')
    startFileStream(enrollBuffer.value!, 'enroll', () => sendCommand('finishEnrollment'))
  } else {
    await startCapture()  // 麦克风模式：直接开麦
  }
}

// 「开始/停止提取」按钮：提取中 → 停止；否则开始（文件灌入或开麦）
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
  // 等后端确认进入 extracting 再开始发音频(TSE 构造注册嵌入要数秒);
  // 失败/超时则不启动采集——后端若失败会另发 error 事件提示原因
  if (await waitForState('extracting', 10000)) {
    if (sourceMode.value === 'file') {
      // 文件模式：灌混合音频，灌完自动停
      startFileStream(mixBuffer.value!, 'mix', () => sendCommand('stopExtraction'))
    } else {
      await startCapture()
    }
  }
}

// 清空字幕区（定稿列表 + 草稿 + 打字机状态）
function clearTranscript() {
  if (typeTimer) { clearInterval(typeTimer); typeTimer = null }
  typePending.length = 0
  transcript.value = []
  partialText.value = ''
  partialTarget.value = ''
  liveTime.value = ''
}

// 音量条第 bar 根柱子的着色：未达电平返回空样式；高中低三段配色
function meterStyle(bar: number) {
  const on = micLevel.value >= (bar - 0.5) / 24   // 24 根柱子映射 0~1 电平
  if (!on) return {}
  const color = bar > 20 ? 'var(--error)' : bar > 16 ? 'var(--warn)' : 'var(--brand)'
  return { backgroundColor: color }
}

// 链路 id → 侧栏短名
function processorLabel(processor: string) {
  if (processor === 'tse') return '纯音频 TSE'
  if (processor === 'speaker_gate') return '声纹降级'
  return '原音诊断'
}
</script>

<template>
  <!-- 整体布局：顶栏 + 左侧流程/引擎状态栏 + 右侧主内容区 -->
  <main class="app-shell">
    <!-- 顶栏：品牌 + 连接状态灯 -->
    <header>
      <div class="brand-mark"><Radio :size="20" /></div>
      <div class="title">
        <strong>AudioTSE</strong>
        <small>目标说话人实时提取台</small>
      </div>
      <div class="connection" :class="{ online: connected }"><i />{{ connected ? '本地服务已连接' : '服务离线' }}</div>
    </header>
    <section class="workspace">
      <!-- 左栏：两步会话流程 + 各引擎就绪状态 -->
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
          <div class="engine-row"><span>纯音频 TSE</span><b :class="{ ok: tseReady && !tseLoading }" :title="tseLoading ? '正在后台加载 262MB 分离模型' : ''">{{ tseLoading ? '加载中…' : tseReady ? '实验就绪' : '待安装' }}</b></div>
          <div class="engine-row"><span>当前链路</span><b :class="{ ok: selectedProcessor === 'tse' }">{{ processorLabel(selectedProcessor) }}</b></div>
          <div class="engine-row"><span>运行设备</span><b>CPU</b></div>
        </div>
      </aside>
      <div class="content">
        <!-- 通知条：notice/noticeTone 驱动的全局提示（连接状态/错误/链路说明） -->
        <div class="banner" :class="{ ok: noticeTone === 'ok', warn: noticeTone === 'warn', error: noticeTone === 'error' }">
          <ShieldAlert :size="18" />
          <div class="banner-text">
            <b>{{ bannerTitle }}</b>
            <span>{{ notice }}</span>
          </div>
        </div>

        <!-- 处理模式 + 识别模型切换：不可用的置灰并提示原因；忙时禁切 -->
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

        <!-- 测试模式面板：麦克风 ↔ 音频文件切换；文件模式下选注册/混合文件 -->
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

        <!-- 注册面板：头像/录音状态 + 音量条 + 「开始/完成注册」主按钮 -->
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
          <button class="btn-primary" :class="{ stop: state === 'enrolling' }" :disabled="enrollDisabled" @click="toggleEnrollment">
            <CircleStop v-if="state === 'enrolling'" :size="18" /><Mic v-else :size="18" />{{ enrollLabel }}
          </button>
        </section>

        <!-- 实时转写面板：定稿字幕列表 + 打字机草稿行 + 回放/开始提取按钮 -->
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
              <button class="play-toggle" :class="{ on: playback }" :disabled="!connected" @click="togglePlayback" :title="playback ? '关闭后端音频回放' : '回放后端正在处理的音频，听实时性（TSE=分离音 / 门控=放行的目标段 / 直通=原音）；麦克风模式请戴耳机防啸叫'">
                <Volume2 v-if="playback" :size="14" /><VolumeX v-else :size="14" />{{ playback ? '回放音频' : '已静音' }}
              </button>
              <button class="btn-listen" :class="{ stop: state === 'extracting' }" :disabled="state !== 'ready' && state !== 'extracting'" @click="toggleExtraction">
                <CircleStop v-if="state === 'extracting'" :size="16" /><Radio v-else :size="16" />{{ state === 'extracting' ? '停止' : '开始提取' }}
              </button>
            </div>
          </div>
        </section>

        <!-- 性能面板：仅收到 metrics 后显示；RTF 徽章 + 8 项实时指标 -->
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
            <div class="metric"><span>跳过保实时</span><b :class="{ bad: metrics.droppedSec > 0 }" :title="metrics.droppedSec > 0 ? 'CPU 跟不上实时时丢弃的中间音频，这段不会被转写' : ''">{{ metrics.droppedSec.toFixed(2) }} s</b></div>
            <div class="metric"><span>静音跳过</span><b :title="'未检测到人声、原声直通、跳过 TSE 的窗口数'">{{ metrics.silentWindows }}</b></div>
            <div class="metric"><span>已处理音频</span><b>{{ metrics.audioSec.toFixed(1) }} s</b></div>
            <div class="metric"><span>运行墙钟</span><b>{{ metrics.wallSec.toFixed(1) }} s</b></div>
          </div>
        </section>
      </div>
    </section>
  </main>
</template>
