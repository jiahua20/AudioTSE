// 渲染进程与主进程之间的最小 API 面
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('gateApi', {
  /** 开始流式注册：之后经 sendAudio 喂的音频会计入注册，净语音够量自动完成 */
  enrollBegin: () => ipcRenderer.send('gate:enrollBegin'),
  /** 提前结束注册（用户主动停 / 超时兜底） */
  enrollFinish: () => ipcRenderer.send('gate:enrollFinish'),
  start: () => ipcRenderer.send('gate:start'),
  stop: () => ipcRenderer.send('gate:stop'),
  /** 流式喂 100ms 音频块（Float32Array [-1,1] @16k），注册/监听模式共用 */
  sendAudio: (samples) => ipcRenderer.send('gate:audio', samples),
  onReady: (callback) => ipcRenderer.on('gate:ready', (_e, info) => callback(info)),
  onError: (callback) => ipcRenderer.on('gate:error', (_e, message) => callback(message)),
  /** 注册进度 {speechSeconds, enough} */
  onEnrollProgress: (callback) => ipcRenderer.on('gate:enrollProgress', (_e, p) => callback(p)),
  /** 注册完成 {speechSeconds} */
  onEnrolled: (callback) => ipcRenderer.on('gate:enrolled', (_e, r) => callback(r)),
  /** 收到完结语音段的门控判定 {similarity, accepted, durationSeconds, samples} */
  onEvent: (callback) => ipcRenderer.on('gate:event', (_e, event) => callback(event)),
})
