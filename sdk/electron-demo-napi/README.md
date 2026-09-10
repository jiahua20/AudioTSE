# 声纹门控最小 Electron Demo（C++ N-API SDK）

与 `sdk/electron-demo-web` 界面与交互完全相同的版本：后端从 web SDK 换成
**C++ SDK 的 N-API addon**（`sdk/cpp-napi`，自包含构建根），主进程直接 `require()`
加载原生 `.node`，推理在工作线程执行不阻塞主进程。N-API ABI 稳定，无需
electron-rebuild。

架构：渲染进程麦克风采集（AudioWorklet 100ms 块）→ IPC → 主进程 →
`@audiotse/gate-napi`（原生 VAD+声纹门控）；renderer 与 preload 与 web demo
逐字节相同——两个 demo 后端可互换。

## 运行

前置：模型已由 `app/start.ps1` 下载到 `app/models/`（直接复用；VAD 用
`silero_vad_v4.onnx`——sherpa 1.12.1 不支持 v5，切段结果与 v5 一致）。

```powershell
# 1. 构建 N-API addon（首次会 npm install；sherpa 1.12.1 包在 cpp-napi\third_party\）
cd sdk\cpp-napi
.\build.ps1               # 产出 build\Release\audiotse_gate_napi.node + dll

# 2. 启动 demo（node_modules 已就位，只装了 electron）
cd ..\electron-demo-napi
npm start
```

用法：点「注册」说**四个字左右**（如「我是本人」），说完自动完成（净语音够
1.2 秒即停）；然后点「开始监听」——目标说话人绿色放行并回放，其他人红色拒绝。
可与 `electron-demo-web` 同时启动共用麦克风，肉眼对比两个后端的判定一致性。

## 离线自检（不用麦克风）

```powershell
cd sdk\electron-demo-napi
node test-gate-napi.js    # PASS = 与 web SDK 流式基准逐位一致
                          #（enrolled 3.78s / 段 start 1984、97728 / sim 0.188、0.569）
```

## 结构

```
main/index.js            主进程：require cpp-napi 直载、enrolling/monitoring 路由、IPC
preload.js               同 web demo（contextBridge 暴露 window.gateApi）
renderer/                同 web demo（AudioWorklet 采麦、判定展示、放行段排播）
test-gate-napi.js        SDK 级自检（样例 wav 全链路，纯 Node 即可跑）
```

- 与 web demo 主进程的唯一区别：`require('../../web')` → `require('../../cpp-napi')`。
- 段音频以 float32 原始精度回传。
- 已知局限同 web demo：无回声消除（可戴耳机）；重叠语音只能整段放行/拒绝。
