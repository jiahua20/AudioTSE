// 流式窗口门控示例（② ES Modules 接法）：唤醒词整段注册 + 500ms 块逐块放行送 ASR。
// 本文件 import 风格——放进 Vite/TS 工程的源码里（打包前的语法），由构建器处理。
// 注意：esm/ 目录的内部相对导入不带 .js 后缀，Vite/webpack/Rollup 可解析，
// **Node 不经构建器直跑不行**——直跑场景请用同目录 electron-main.example.cjs。
// 另一种接法见 electron-main.example.umd.js（UMD 单文件）。三个示例 API 与流程完全一致。
//
// 本文件位于交付包 examples/ 下（与 gate/、models/ 平级），路径即按此布局书写；
// 拷进业务工程时改导入路径和模型路径两处即可（如 vendor 布局）。
//
// main.ts / main.mjs（Electron 主进程，ES Modules）：
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { app, ipcMain } from 'electron'
import { StreamGate } from '../gate/esm' // 也可包入口：import { StreamGate } from '../gate'（exports 的 import 条件自动选 esm/）

// ESM 没有 __dirname（CJS 全局），用 import.meta.url 推导等价目录；
// 走 electron-vite 等打包器的主进程通常仍注入 __dirname，两种来源二选一即可。
const __dirname = fileURLToPath(new URL('.', import.meta.url))

const SPEAKER_MODEL = path.join(
  __dirname,
  '..',
  'models',
  'sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k.onnx',
)

/** @type {import('../gate/esm').StreamGate | null} */
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
