// 流式窗口门控示例（③ UMD 接法）：唤醒词整段注册 + 500ms 块逐块放行送 ASR。
// 本文件 require UMD 单文件——一个 js 直引，不需要 TypeScript 编译，也不依赖
// gate/cjs、gate/esm 目录（宿主只需保住这一个文件 + node_modules 运行时 + 模型）。
// <script> 直引场景挂全局 AudioTSEGate（仅限带 require 的环境，如 nodeIntegration
// 的渲染进程）。另两种接法见同目录 electron-main.example.cjs / .mjs。三个示例
// API 与流程完全一致。
//
// 本文件位于交付包 examples/ 下（与 gate/、models/ 平级），路径即按此布局书写；
// 拷进业务工程时改导入路径和模型路径两处即可（如 vendor 布局）。
//
// main.js（Electron 主进程，CommonJS）：
const path = require('node:path')
const { app, ipcMain } = require('electron')
const { StreamGate } = require('../gate/umd/audiotse-gate.umd.js') // UMD 单文件（require 兼容）

const SPEAKER_MODEL = path.join(
  __dirname,
  '..',
  'models',
  'sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k.onnx',
)

/** @type {import('../gate/umd/audiotse-gate').StreamGate | null} */
let sg = null

app.whenReady().then(async () => {
  // 初始化一次，全应用复用（加载模型约 0.5s）。窗口阈值默认 0.25——短窗相似度
  // 整体低于整句，整句判定的 0.5 不可用于窗口模式（详见 README「流式窗口门控」）。
  sg = await StreamGate.create({ speakerModel: SPEAKER_MODEL })
  console.log('流式窗口门控就绪，窗口阈值', sg.effectiveThreshold)
})

// 唤醒事件：内网侧 KWS 检测到「小耘小耘」后，把这句唤醒语音（VAD 切好的整段
// Float32Array @16k）发过来 → 注册。每次唤醒都调用，注册声纹随唤醒人刷新。
// 注册仍用唤醒词整段；流式门控只管唤醒之后的 500ms 块。
ipcMain.handle('voice:wakeWord', async (_event, wakeWordSamples) => {
  const result = await sg.enroll(wakeWordSamples) // {speechSeconds}
  return { ok: true, speechSeconds: result.speechSeconds, threshold: sg.effectiveThreshold }
})

// 流式块：唤醒之后每 500ms 音频块到达即判即转（ASR 打字机节奏不被打断）。
// 静音块会返回 { silent: true, accepted: false }，本就无需送 ASR。
ipcMain.on('voice:chunk', async (_event, chunk) => {
  const verdict = await sg.push(chunk) // { accepted, similarity, score, silent }
  if (verdict.accepted) {
    asrFeed(chunk) // 过 → 立即喂 ASR；不过 → 丢弃该块（陌生人说话不出字）
  }
})

// 占位：把放行的块喂给贵方流式 ASR（500ms 一块的打字机链路）
function asrFeed(chunk500ms) {
  /* …接贵方 ASR 的流式喂入接口… */
}

// 应用退出前释放
app.on('before-quit', () => {
  sg?.dispose()
})

// 备注：需要「等整句说完出一次判定」的非打字机接法，用同一入口的
// VoiceFilter.judge / filter（见 README 接入章节），注册语音与模型与本示例通用。
