# 声纹门控最小 Electron Demo（web SDK）

演示 `@audiotse/gate`（`sdk/web`）在 Electron 里的最小接入：麦克风采集 → 主进程
VAD+声纹门控 → 界面实时显示每段相似度 → **只回放被放行（目标说话人）的语音**。
（C++ SDK 的对照版见 `sdk/electron-demo-cpp`，界面与交互完全相同，后端可互换。）

## 运行

前置：模型已由 `app/start.ps1` 下载到 `app/models/`（demo 直接复用，不重复下载）。
本 demo 与 `sdk/web` 各自独立 node_modules，不共享：

```powershell
cd sdk\web
npm install
npm run build        # 生成 dist/（demo 通过相对路径 ../../web 引用）

cd ..\electron-demo-web
npm install          # 只装 electron
npm start
```

用法：点「注册」说**四个字左右**（如「我是本人」），说完自动完成——SDK 内部剥
静音只计净语音，够 1.2 秒即停，不再固定录 5 秒；净语音过少会提示重试。然后点
「开始监听」，每个人说话会实时出一段判定条：达到阈值线（短注册时阈值自动按
0.7 系数下调）绿色放行并回放，否则红色拒绝（听不到）。

## 结构

```
main/index.js     主进程：加载 SDK、IPC（enroll/start/stop/audio→event）
preload.js        contextBridge 暴露 window.gateApi
renderer/         纯静态页面（无构建链）：AudioWorklet 采麦 16k、判定展示、放行段排播
```

- 采集：`AudioContext({sampleRate:16000})` 让浏览器重采样到 16k，AudioWorklet 聚
  100ms 块经 IPC 发主进程（Float32Array 结构化克隆直达）。
- 推理在主进程（onnxruntime-node 原生 + sherpa-onnx WASM），渲染进程零依赖。
- 已知局限：扬声器回放会被麦克风再次拾入（演示未做回声消除，可戴耳机）；
  重叠语音时门控只能整段放行/拒绝，真分离要等 TSE 接入。

## 换模型目录

```powershell
$env:AUDIOTSE_MODELS = "D:\somewhere\models"; npm start -w electron-demo
```
