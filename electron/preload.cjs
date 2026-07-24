const { contextBridge } = require('electron')
contextBridge.exposeInMainWorld('audioTSEDesktop', { platform: process.platform })