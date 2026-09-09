// 渲染进程：16k AudioContext 采麦（AudioWorklet 聚成 100ms 块）→ IPC → 主进程 SDK；
// 收到判定后展示相似度，并把「放行」的段排入本地播放队列（只听目标说话人）。
const SAMPLE_RATE = 16000
const CHUNK = 1600 // 100 ms
const ENROLL_HARD_CAP_MS = 5000 // 注册墙钟兜底：净语音没够也最多录这么久

const statusEl = document.getElementById('status')
const enrollBtn = document.getElementById('enrollBtn')
const monitorBtn = document.getElementById('monitorBtn')
const hintEl = document.getElementById('hint')
const logEl = document.getElementById('log')

let audioCtx = null
let workletNode = null
let micStream = null
let mode = 'idle' // idle | recording | monitoring
let enrollDeadline = null
let nextPlayTime = 0 // 已接受段的无缝排播游标

// ── SDK 事件 ─────────────────────────────────────────────
window.gateApi.onReady((info) => {
  statusEl.textContent = `SDK 就绪（模型：${info.models}）`
  enrollBtn.disabled = false
})
window.gateApi.onEvent((event) => {
  showSegment(event)
  if (event.accepted) playSamples(event.samples)
})

// ── 麦克风采集（AudioWorklet：聚 100ms 块后 postMessage）──
const WORKLET_CODE = `
class CaptureProcessor extends AudioWorkletProcessor {
  constructor() { super(); this._buf = new Float32Array(${CHUNK}); this._n = 0 }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0]
    if (!ch) return true
    for (let i = 0; i < ch.length; i++) {
      this._buf[this._n++] = ch[i]
      if (this._n === ${CHUNK}) {
        this.port.postMessage(this._buf.slice())
        this._n = 0
      }
    }
    return true
  }
}
registerProcessor('capture', CaptureProcessor)
`

async function startMic() {
  if (audioCtx) return
  micStream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false },
  })
  audioCtx = new AudioContext({ sampleRate: SAMPLE_RATE })
  const url = URL.createObjectURL(new Blob([WORKLET_CODE], { type: 'application/javascript' }))
  await audioCtx.audioWorklet.addModule(url)
  URL.revokeObjectURL(url)
  workletNode = new AudioWorkletNode(audioCtx, 'capture')
  workletNode.port.onmessage = ({ data }) => onChunk(data)
  audioCtx.createMediaStreamSource(micStream).connect(workletNode)
  // 不连 destination：只回放门控接受的段
}

function stopMic() {
  if (workletNode) workletNode.disconnect()
  if (micStream) micStream.getTracks().forEach((t) => t.stop())
  if (audioCtx) audioCtx.close()
  audioCtx = null
  workletNode = null
  micStream = null
}

function onChunk(chunk) {
  if (mode === 'recording' || mode === 'monitoring') window.gateApi.sendAudio(chunk)
}

// ── 注册（说四个字，净语音够量自动完成）──────────────────
function resetEnrollUi() {
  clearTimeout(enrollDeadline)
  enrollDeadline = null
  mode = 'idle'
  stopMic()
  enrollBtn.classList.remove('recording')
  enrollBtn.textContent = '🎤 注册（说四个字）'
  enrollBtn.disabled = false
}

enrollBtn.addEventListener('click', async () => {
  if (mode !== 'idle') return
  await startMic()
  mode = 'recording'
  enrollBtn.classList.add('recording')
  enrollBtn.disabled = true
  monitorBtn.disabled = true
  hintEl.textContent = '请说一句话（四个字左右，如「我是本人」，说完自动完成）'
  window.gateApi.enrollBegin()
  // 墙钟兜底：净语音迟迟不够（没说话/太吵）时主动收尾
  enrollDeadline = setTimeout(() => window.gateApi.enrollFinish(), ENROLL_HARD_CAP_MS)
})

window.gateApi.onEnrollProgress((progress) => {
  enrollBtn.textContent = `⏺ 已收语音 ${progress.speechSeconds.toFixed(1)}s`
})

window.gateApi.onEnrolled((result) => {
  resetEnrollUi()
  hintEl.textContent = `注册完成（净语音 ${result.speechSeconds.toFixed(1)}s），点「开始监听」后只放行你的声音`
  monitorBtn.disabled = false
})

window.gateApi.onError((message) => {
  if (mode === 'recording') {
    resetEnrollUi()
    hintEl.textContent = `注册失败：${message}（点注册再试一次）`
  } else {
    statusEl.textContent = `出错了：${message}`
  }
})

// ── 监听 ─────────────────────────────────────────────────
monitorBtn.addEventListener('click', async () => {
  if (mode === 'idle') {
    await startMic()
    mode = 'monitoring'
    monitorBtn.textContent = '⏸ 停止监听'
    monitorBtn.classList.add('on')
    window.gateApi.start()
    hintEl.textContent = '监听中：绿色=放行（会回放），红色=拒绝（静音）'
  } else if (mode === 'monitoring') {
    mode = 'idle'
    monitorBtn.textContent = '▶ 开始监听'
    monitorBtn.classList.remove('on')
    window.gateApi.stop()
    stopMic()
  }
})

// ── 展示与回放 ───────────────────────────────────────────
function showSegment(event) {
  const row = document.createElement('div')
  row.className = `seg ${event.accepted ? 'accept' : 'reject'}`
  const similarity = event.similarity === null ? '—' : event.similarity.toFixed(3)
  const percent = event.similarity === null ? 100 : Math.max(0, Math.min(1, event.similarity)) * 100
  row.innerHTML = `
    <span class="tag">${event.accepted ? '放行' : '拒绝'}</span>
    <div class="bar"><div class="fill" style="width:${percent}%"></div><div class="thresh" style="left:50%"></div></div>
    <span class="meta">相似度 ${similarity}<br/>${event.durationSeconds.toFixed(2)}s</span>`
  logEl.prepend(row)
}

function playSamples(samples) {
  if (!audioCtx) return
  const buffer = audioCtx.createBuffer(1, samples.length, SAMPLE_RATE)
  buffer.copyToChannel(samples, 0)
  const source = audioCtx.createBufferSource()
  source.buffer = buffer
  source.connect(audioCtx.destination)
  // 段与段之间无缝排队，避免重叠
  const now = audioCtx.currentTime
  nextPlayTime = Math.max(now + 0.05, nextPlayTime)
  source.start(nextPlayTime)
  nextPlayTime += buffer.duration
}
