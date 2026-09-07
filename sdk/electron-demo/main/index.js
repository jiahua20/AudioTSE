// 主进程：加载 web SDK（@audiotse/gate）做 VAD+声纹门控，渲染进程通过 IPC 喂音频。
// 模型默认取仓库 app/models/（与原型共用一份模型文件），可用 AUDIOTSE_MODELS 覆盖目录。
const { app, BrowserWindow, ipcMain } = require('electron')
const path = require('node:path')

const REPO_ROOT = path.resolve(__dirname, '..', '..', '..')
const MODELS_DIR = process.env.AUDIOTSE_MODELS || path.join(REPO_ROOT, 'app', 'models')

/** @type {BrowserWindow | null} */
let win = null
/** @type {import('@audiotse/gate').SpeakerGate | null} */
let gate = null
let monitoring = false
let enrolling = false

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
  win = new BrowserWindow({
    width: 980,
    height: 720,
    title: 'AudioTSE 声纹门控 Demo',
    webPreferences: {
      preload: path.join(__dirname, '..', 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })
  win.loadFile(path.join(__dirname, '..', 'renderer', 'index.html'))
}

app.whenReady().then(async () => {
  createWindow()
  console.log('[demo] window created, models dir:', MODELS_DIR)
  try {
    const { SpeakerGate } = require('@audiotse/gate')
    gate = await SpeakerGate.create({
      vadModel: path.join(MODELS_DIR, 'silero_vad', 'silero_vad.onnx'),
      speakerModel: path.join(
        MODELS_DIR,
        'sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k',
        'model.onnx',
      ),
    })
    console.log('[demo] speaker gate ready')
    win.webContents.send('gate:ready', { models: MODELS_DIR })
  } catch (error) {
    console.error('[demo] gate init failed:', error)
    win.webContents.send('gate:error', String(error))
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
        // 注册模式：SDK 内部剥静音累计净语音，够量（默认 1.8s）自动完成
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

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
