// 内网 Electron 接入示例：主进程初始化 SDK → 业务注册 → 逐段过滤。
// 假设交付目录摆法见 README（vendor/audiotse-gate + models/speaker.onnx）。
// 主进程 CommonJS，渲染进程通过 ipcRenderer 把 VAD 切好的 Float32Array 发过来。
//
// 引入方式二选一：
//   1) 目录拷贝（推荐，见 README 交付清单）：
//      const { VoiceFilter } = require('../../vendor/audiotse-gate/dist/core')
//   2) npm 安装了本包（私服/离线 tgz）：
//      const { VoiceFilter } = require('@audiotse/gate/core')
const path = require('node:path')
const { VoiceFilter } = require('../../vendor/audiotse-gate/dist/core')
const { app, ipcMain } = require('electron')

let filter = null

app.whenReady().then(async () => {
  // 初始化一次，全应用复用（加载模型约 0.5s）
  filter = await VoiceFilter.create({
    speakerModel: path.join(__dirname, '..', 'vendor', 'models', 'speaker.onnx'),
    threshold: 0.5,
    shortEnrollThresholdFactor: 0.7, // 短注册（<1.5s）时实际阈值 0.5×0.7=0.35
  })
  console.log('声纹过滤就绪，阈值', filter.effectiveThreshold)
})

// 注册：业务问答环节，用户说四个字（外部 VAD 切好的一段 float32 @16k）
ipcMain.handle('voice:enroll', async (_event, samples) => {
  const result = await filter.enroll(samples) // {speechSeconds}
  return { ok: true, speechSeconds: result.speechSeconds, threshold: filter.effectiveThreshold }
})

// 过滤：外部 VAD 每切出一段语音就发过来；返回主讲人的语音或 null
ipcMain.on('voice:segment', async (_event, samples) => {
  // 方式一：直接要结果
  const target = await filter.filter(samples)
  if (target) {
    // 回传主讲人语音给业务（原引用，不拷贝）
    // sendBackToBusiness(target)
  }

  // 方式二：要相似度自己定策略
  // const { similarity, accepted, durationSeconds } = await filter.judge(samples)
})

// 应用退出前释放
app.on('before-quit', () => {
  filter?.dispose()
})
