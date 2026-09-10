// 主进程：spawn C++ 桥接进程 gate_stream.exe（sdk/cpp/build/，stdin 二进制帧喂音频、
// stdout JSON 行回事件），渲染进程通过 IPC 与之交互。事件语义与 electron-demo-web 的
// web SDK 版一一对应，renderer/preload 与 web demo 完全相同——后端实现可互换。
//
// 协议见 examples/gate_stream.cc 头部注释。
// 模型默认取仓库 app/models/，可用 AUDIOTSE_MODELS 覆盖目录；
// gate_stream 路径可用 AUDIOTSE_GATE_EXE 覆盖。
const { app, BrowserWindow, ipcMain } = require('electron')
const path = require('node:path')
const { spawn } = require('node:child_process')
const readline = require('node:readline')

const REPO_ROOT = path.resolve(__dirname, '..', '..', '..')
const GATE_EXE =
  process.env.AUDIOTSE_GATE_EXE || path.join(REPO_ROOT, 'sdk', 'cpp', 'build', 'gate_stream.exe')
const MODELS_DIR = process.env.AUDIOTSE_MODELS || path.join(REPO_ROOT, 'app', 'models')
const VAD_MODEL = path.join(MODELS_DIR, 'silero_vad', 'silero_vad.onnx')
const SPEAKER_MODEL = path.join(
  MODELS_DIR,
  'sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k',
  'model.onnx',
)

/** @type {BrowserWindow | null} */
let win = null
/** @type {import('node:child_process').ChildProcess | null} */
let gate = null
// renderer 加载完 app.js（挂好 IPC 监听）之前发的消息会被直接丢弃——IPC 不缓存，
// 初始化类消息必须等 did-finish-load 之后再发，两处以先到者为准补发
let pageLoaded = false
let gateReady = false

function sendInit(channel, payload) {
  if (win && !win.isDestroyed() && pageLoaded) win.webContents.send(channel, payload)
}

function createWindow() {
  pageLoaded = false
  win = new BrowserWindow({
    width: 980,
    height: 720,
    title: 'AudioTSE 声纹门控 Demo（C++ SDK）',
    webPreferences: {
      preload: path.join(__dirname, '..', 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })
  // 页面（重新）加载完后若子进程已 ready 则补发 gate:ready（reload 后 renderer 需要重新同步）
  win.webContents.on('did-finish-load', () => {
    pageLoaded = true
    if (gateReady) sendInit('gate:ready', { models: MODELS_DIR, backend: 'cpp' })
  })
  win.loadFile(path.join(__dirname, '..', 'renderer', 'index.html'))
}

/** stdout JSON 行 → 渲染进程事件（字段与 web demo 的 IPC 事件保持一致） */
function onMessage(msg) {
  if (!win || win.isDestroyed()) return
  switch (msg.type) {
    case 'ready':
      gateReady = true
      sendInit('gate:ready', { models: MODELS_DIR, backend: 'cpp' })
      break
    case 'enrollProgress':
      win.webContents.send('gate:enrollProgress', {
        speechSeconds: msg.speechSeconds,
        enough: msg.enough,
      })
      break
    case 'enrolled':
      win.webContents.send('gate:enrolled', { speechSeconds: msg.speechSeconds })
      break
    case 'error':
      // 子进程初始化失败可能早于页面加载完（与 ready 同一竞态），统一走 sendInit
      sendInit('gate:error', msg.message)
      break
    case 'segment': {
      // int16 量化样本 → float32，交给渲染进程回放
      const samples = new Float32Array(msg.samples.length)
      for (let i = 0; i < msg.samples.length; i++) samples[i] = msg.samples[i] / 32768
      win.webContents.send('gate:event', {
        similarity: msg.similarity, // 未注册时为 null
        accepted: msg.accepted,
        durationSeconds: msg.durationSeconds,
        samples,
      })
      break
    }
  }
}

app.whenReady().then(() => {
  createWindow()
  console.log('[demo] window created, gate exe:', GATE_EXE)
  console.log('[demo] models dir:', MODELS_DIR)

  gate = spawn(GATE_EXE, [VAD_MODEL, SPEAKER_MODEL], { stdio: ['pipe', 'pipe', 'inherit'] })
  gate.on('error', (error) => {
    console.error('[demo] spawn failed（先构建 sdk\\cpp\\build\\gate_stream.exe）:', error)
    win?.webContents.send('gate:error', `无法启动 gate_stream.exe：${error.message}`)
  })
  gate.on('close', (code) => {
    console.error('[demo] gate_stream exited:', code)
    win?.webContents.send('gate:error', `gate_stream 已退出（code ${code}）`)
  })
  readline.createInterface({ input: gate.stdout }).on('line', (line) => {
    if (!line) return
    try {
      onMessage(JSON.parse(line))
    } catch (error) {
      console.warn('[demo] bad json from gate_stream:', line.slice(0, 200))
    }
  })

  ipcMain.on('gate:enrollBegin', () => gate?.stdin.write('B'))
  ipcMain.on('gate:enrollFinish', () => gate?.stdin.write('F'))
  ipcMain.on('gate:start', () => gate?.stdin.write('S'))
  ipcMain.on('gate:stop', () => gate?.stdin.write('T'))
  ipcMain.on('gate:audio', (_event, samples) => {
    // Float32Array [-1,1] @16k（100ms 块）→ 'A' + u32 N + float32 LE
    if (!gate || !gate.stdin.writable || !(samples instanceof Float32Array)) return
    const header = Buffer.allocUnsafe(5)
    header.writeUInt8(0x41, 0)
    header.writeUInt32LE(samples.length, 1)
    gate.stdin.write(header)
    gate.stdin.write(Buffer.from(samples.buffer, samples.byteOffset, samples.byteLength))
  })

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('before-quit', () => {
  if (gate && gate.stdin.writable) gate.stdin.write('Q')
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
