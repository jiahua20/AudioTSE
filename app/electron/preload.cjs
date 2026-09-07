// preload 脚本：在页面与主进程隔离（contextIsolation）的前提下，
// 向页面暴露一个极小的 API —— 只告诉页面「当前操作系统平台」。
// 桌面版据此启用一些浏览器版没有的行为（如麦克风直采）。
const { contextBridge } = require('electron')
contextBridge.exposeInMainWorld('audioTSEDesktop', { platform: process.platform })
