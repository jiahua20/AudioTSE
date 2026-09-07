# AudioTSE

实时目标说话人提取（Target Speaker Extraction）工程仓。

- [`app/`](app/README.md) — 现有桌面原型：Electron/Vue 桌面端 + Python WebSocket 后端（Sherpa-ONNX 中文流式 ASR、可选 WeSep 纯音频 TSE、声纹门控降级）。入口 `app/start.ps1`，架构详见 [app/docs/ARCHITECTURE.md](app/docs/ARCHITECTURE.md)。
- [`sdk/`](sdk/README.md) — 端侧 SDK 封装（v0 声纹门控）：`web/` TS SDK（对拍验证通过）、`cpp/` C++ SDK（sherpa-onnx 原生）、`electron-demo/` 最小演示。
