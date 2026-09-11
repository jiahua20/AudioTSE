// 主进程：require @audiotse/gate-napi（C++ SDK 的 N-API addon，Electron 免重编译直载，
// 推理在 libuv 工作线程执行不阻塞主进程），结构与 electron-demo-web 的接入方式一致，
// renderer/preload 也与 web demo 完全相同——两个 demo 的后端可互换。
// 模型默认取仓库 app/models/，可用 AUDIOTSE_MODELS 覆盖目录。
const { app, BrowserWindow, ipcMain } = require('electron')
const path = require('node:path')

const REPO_ROOT = path.resolve(__dirname, '..', '..', '..')
const NAPI_SDK = path.resolve(__dirname, '..', '..', 'cpp-napi') // ../cpp-napi 包（需先 .\build.ps1）
const MODELS_DIR = process.env.AUDIOTSE_MODELS || path.join(REPO_ROOT, 'app', 'models')

/** @type {BrowserWindow | null} */
let win = null
/** @type {import('../../cpp-napi').SpeakerGate | null} */
let gate = null
let monitoring = false
let enrolling = false
// renderer 加载完 app.js（挂好 IPC 监听）之前发的消息会被直接丢弃——IPC 不缓存，
// 初始化类消息必须等 did-finish-load 之后再发，两处以先到者为准补发
let pageLoaded = false

function sendInit(channel, payload) {
  if (win && !win.isDestroyed() && pageLoaded) win.webContents.send(channel, payload)
}

async function completeEnroll() {
  if (!enrolling || !gate) return
  enrolling = false
  try {
    const result = await gate.finishEnroll()
    win?.webContents.send('gate:enrolled', result)
  } catch (error) {
    win?.webContents.send('gate:error', String(error))
  }
}

function forward(event) {
  if (win && !win.isDestroyed()) win.webContents.send('gate:event', event)
}

function createWindow() {
  pageLoaded = false
  win = new BrowserWindow({
    width: 980,
    height: 720,
    title: 'AudioTSE 声纹门控 Demo（C++ NAPI SDK）',
    webPreferences: {
      preload: path.join(__dirname, '..', 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })
  // 页面（重新）加载完后若 SDK 已就绪则补发 gate:ready（reload 后 renderer 需要重新同步）
  win.webContents.on('did-finish-load', () => {
    pageLoaded = true
    if (gate) sendInit('gate:ready', { models: MODELS_DIR, backend: 'napi' })
  })
  win.loadFile(path.join(__dirname, '..', 'renderer', 'index.html'))
}

app.whenReady().then(async () => {
  createWindow()
  console.log('[demo] window created, models dir:', MODELS_DIR)
  try {
    const { SpeakerGate } = require(NAPI_SDK)
    gate = await SpeakerGate.create({
      vadModel: path.join(MODELS_DIR, 'silero_vad', 'silero_vad_v4.onnx'),
      speakerModel: path.join(
        MODELS_DIR,
        'sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k',
        'model.onnx',
      ),
    })
    console.log('[demo] speaker gate ready (native napi)')
    sendInit('gate:ready', { models: MODELS_DIR, backend: 'napi' })
  } catch (error) {
    console.error('[demo] gate init failed（先在 sdk\\cpp-napi 运行 .\\build.ps1）:', error)
    sendInit('gate:error', String(error))
  }

  ipcMain.on('gate:enrollBegin', () => {
    if (!gate) return
    enrolling = true
    gate.beginEnroll()
  })
  // 用户提前停止（或超时兜底）时主动收尾；净语音不足会以 gate:error 报回
  ipcMain.on('gate:enrollFinish', () => {
    completeEnroll()
  })
  ipcMain.on('gate:start', () => {
    monitoring = true
  })
  ipcMain.on('gate:stop', async () => {
    monitoring = false
    if (gate) for (const event of await gate.flush()) forward(event)
  })
  ipcMain.on('gate:audio', async (_event, samples) => {
    if (!gate) return
    try {
      if (enrolling) {
        // 注册模式：SDK 内部剥静音累计净语音，够量自动完成
        const progress = await gate.enrollChunk(samples)
        win?.webContents.send('gate:enrollProgress', progress)
        if (progress.enough) await completeEnroll()
        return
      }
      if (!monitoring) return
      for (const event of await gate.accept(samples)) forward(event)
    } catch (error) {
      win?.webContents.send('gate:error', String(error))
    }
  })

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('before-quit', () =>
   {
  gate?.dispose()
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
