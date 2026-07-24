const { app, BrowserWindow, session } = require('electron')
const path = require('node:path')

function createWindow() {
  session.defaultSession.setPermissionRequestHandler((_webContents, permission, callback) => callback(permission === 'media'))
  const window = new BrowserWindow({
    width: 1180,
    height: 780,
    minWidth: 920,
    minHeight: 640,
    backgroundColor: '#f2f0ea',
    titleBarStyle: 'hiddenInset',
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, nodeIntegration: false },
  })
  window.loadURL(process.env.VITE_DEV_SERVER_URL || 'http://localhost:5173')
}

app.whenReady().then(createWindow)
app.on('window-all-closed', () => app.quit())