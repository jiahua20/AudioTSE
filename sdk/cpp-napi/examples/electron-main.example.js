// Electron 主进程接线示例（C++ N-API 版）：初始化一次全应用复用，
// 渲染进程经 IPC 把 VAD 切好的段喂进来（nodeIntegration 关闭也能用，本文件在主进程）。
// 完整最小 Demo 见仓库 sdk/electron-demo-napi（含 preload/renderer 与麦克风采集）。
//
// main.js（Electron 主进程）：
const { app } = require('electron')
const path = require('node:path')

async function bootstrap() {
  // 路径按实际摆放调整（交付包布局：gate-napi/ 下 index.js + native/）
  const { VoiceFilter } = require('../vendor/gate-napi')

  // 应用启动时初始化一次（模型加载 ~0.5s，在 libuv 工作线程执行不阻塞主进程）
  const filter = await VoiceFilter.create({
    speakerModel: path.join(__dirname, '..', 'vendor', 'models', 'speaker.onnx'),
    threshold: 0.5,
    shortEnrollThresholdFactor: 0.7,
  })

  // 唤醒词模式：每次唤醒都重新注册（这句语音同时是注册语音）
  // filter.enroll(wakeWordSamples)   // Float32Array @16k，唤醒词整段

  // 唤醒之后的提问段：只过滤、不注册
  // const speech = await filter.filter(segment)          // 主讲人语音（原引用）或 null
  // const { similarity, accepted } = await filter.judge(segment) // 要相似度时用

  app.on('before-quit', () => filter.dispose())
  // … 其余为常规窗口/IPC 代码，见 sdk/electron-demo-napi/main/index.js
}

if (require.main === module) {
  console.log('本文件是接线示例，完整可跑版本见 sdk/electron-demo-napi；bootstrap() 里的路径按实际布局调整')
} else {
  void bootstrap
}
