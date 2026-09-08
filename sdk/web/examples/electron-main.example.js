// 内网 Electron 接入示例：唤醒词注册 + 提问过滤。
//
// 业务闭环：
//   1. 客户向大屏喊「小耘小耘」→ 唤醒；这句唤醒语音同时做声纹注册
//      （每次唤醒都调用一次 enroll 重新注册，声纹始终是最近唤醒的人）
//   2. 唤醒之后的提问/其他语音走过滤（judge/filter），只回传主讲人语音，不再注册
//
// 引入方式二选一（详见 README 交付清单）：
//   1) 目录拷贝：const { VoiceFilter } = require('../../vendor/audiotse-gate/dist/core')
//   2) npm 安装本包：const { VoiceFilter } = require('@audiotse/gate/core')
// 可跑的完整流程演示（不依赖 Electron）见同目录 intranet-wake-flow.js。
const path = require('node:path')
const { VoiceFilter } = require('../../vendor/audiotse-gate/dist/core')
const { app, ipcMain } = require('electron')

let filter = null

app.whenReady().then(async () => {
  // 初始化一次，全应用复用（加载模型约 0.5s）
  filter = await VoiceFilter.create({
    speakerModel: path.join(__dirname, '..', 'vendor', 'models', 'speaker.onnx'),
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
