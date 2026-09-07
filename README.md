# AudioTSE

实时目标说话人提取（Target Speaker Extraction）工程仓。

- [`app/`](app/README.md) — 现有桌面原型：Electron/Vue 桌面端 + Python WebSocket 后端（Sherpa-ONNX 中文流式 ASR、可选 WeSep 纯音频 TSE、声纹门控降级）。入口 `app/start.ps1`，架构详见 [app/docs/ARCHITECTURE.md](app/docs/ARCHITECTURE.md)。
- [`sdk/`](sdk/README.md) — 端侧 SDK 封装工程 + Electron demo（新建，封装路线待定：TS 直接封装 or C++ 封装桥接）。
