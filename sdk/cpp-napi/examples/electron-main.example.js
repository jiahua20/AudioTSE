// Electron 主进程接线示例（C++ N-API 版）：初始化一次全应用复用，
// 渲染进程经 IPC 把 VAD 切好的段喂进来（nodeIntegration 关闭也能用，本文件在主进程）。
// 完整最小 Demo 见仓库 sdk/electron-demo-napi（含 preload/renderer 与麦克风采集）。
//
// 业务闭环：
//   1. 客户向大屏喊「小耘小耘」→ 唤醒；这句唤醒语音同时做声纹注册
//      （每次唤醒都调用一次 enroll 重新注册，声纹始终是最近唤醒的人）
//   2. 唤醒之后的提问/其他语音走过滤（judge/filter），只回传主讲人语音，不再注册
//
// 本文件位于交付包 examples/ 下（与 gate-napi/、models/ 平级），路径即按此布局书写，
// 包内直接可用：
//   const { VoiceFilter } = require('../gate-napi')                // 上一级 = SDK 包体
//   path.join(__dirname, '..', 'models', 'sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k.onnx')
// 拷进业务工程时改这两处即可（如 vendor 布局：require('<app>/vendor/gate-napi')）。
//
// main.js（Electron 主进程）：
const path = require('node:path')
const { app, ipcMain } = require('electron')
const { VoiceFilter } = require('../gate-napi')

let filter = null

app.whenReady().then(async () => {
  // 应用启动时初始化一次（模型加载 ~0.5s，在 libuv 工作线程执行不阻塞主进程）
  filter = await VoiceFilter.create({
    speakerModel: path.join(__dirname, '..', 'models', 'sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k.onnx'),
    threshold: 0.5,
    shortEnrollThresholdFactor: 0.7, // 唤醒词四个音节 ~1s，短注册自动降阈值为 0.35
  })
  console.log('声纹过滤就绪，初始阈值', filter.effectiveThreshold)
})

// 唤醒事件：内网侧检测到「小耘小耘」后，把这句唤醒语音（VAD 切好的
// Float32Array @16k）发过来 → 注册。每次唤醒都调用，注册声纹随唤醒人刷新。
ipcMain.handle('voice:wakeWord', async (_event, wakeWordSamples) => {
  const result = await filter.enroll(wakeWordSamples) // {speechSeconds}
  return { ok: true, speechSeconds: result.speechSeconds, threshold: filter.effectiveThreshold }
})

// 提问/其他语音段：唤醒之后的每段语音走这里，只过滤、不注册。
ipcMain.on('voice:segment', async (_event, samples) => {
  // 方式一：直接拿主讲人语音（非目标返回 null）
  const targetSpeech = await filter.filter(samples)
  if (targetSpeech) {
    // 回传主讲人语音给业务（原引用，不拷贝）
    // sendBackToBusiness(targetSpeech)
  }

  // 方式二：要相似度自己定策略
  // const { similarity, accepted, durationSeconds } = await filter.judge(samples)
})

// 应用退出前释放
app.on('before-quit', () => {
  filter?.dispose()
})

// ── ASR 打字机场景：流式窗口门控（StreamGate）──────────────────────
// ASR 边收边转写（打字机）等不了整句说完时，改用 StreamGate：每 500ms 判一次，
// 过则该块立即送 ASR；注册不变（仍是唤醒词整段）。窗口阈值默认 0.25——短窗相似度
// 整体低于整句，整句判定的 0.5 不可用于窗口模式。详见包内 README「流式窗口门控」。
//
// const { StreamGate } = require('../gate-napi')
// const sg = await StreamGate.create({
//   speakerModel: path.join(__dirname, '..', 'models', 'sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k.onnx'),
// })
// await sg.enroll(wakeWordSamples)
// ipcMain.on('voice:chunk', async (_event, chunk500ms) => {
//   const verdict = await sg.push(chunk500ms)   // { accepted, similarity, score, silent }
//   if (verdict.accepted) asrFeed(chunk500ms)   // 过 → 立即喂 ASR；不过 → 丢弃该块
// })
