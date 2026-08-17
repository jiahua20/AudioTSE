// Electron 主进程：创建桌面窗口并自动放行麦克风权限。
// 用 Electron 而非纯浏览器的两个原因：
//   1) 浏览器 getUserMedia 要求 HTTPS/localhost，Electron 本地页面视同 localhost；
//   2) 这里可以静默批准 media 权限，不弹系统对话框。
const { app, BrowserWindow, session } = require('electron')
const path = require('node:path')

function createWindow() {
  // 权限总开关：只自动批准 media（麦克风/摄像头）请求，其余一律拒绝
  session.defaultSession.setPermissionRequestHandler((_webContents, permission, callback) => callback(permission === 'media'))
  const window = new BrowserWindow({
    width: 1180,          // 初始窗口尺寸
    height: 780,
    minWidth: 920,        // 最小尺寸约束
    minHeight: 640,
    backgroundColor: '#f2f0ea',   // 底色与页面一致，避免启动白闪
    titleBarStyle: 'hiddenInset', // 隐藏标题栏（内容区自己画 header）
    // 安全配置：preload 隔离注入、禁用 Node 集成（页面里跑不了 require）
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, nodeIntegration: false },
  })
  // 加载 Vite 开发服务器（npm run desktop 先起 vite 再起 electron）；
  // 打包场景下由打包器注入 VITE_DEV_SERVER_URL 环境变量
  window.loadURL(process.env.VITE_DEV_SERVER_URL || 'http://localhost:5173')
}

app.whenReady().then(createWindow)             // Electron 就绪后开窗
app.on('window-all-closed', () => app.quit())  // 关掉所有窗口 = 退出应用
