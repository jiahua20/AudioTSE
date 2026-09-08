# AudioTSE

实时目标说话人提取（Target Speaker Extraction）工程仓。

- [`app/`](app/README.md) — 现有桌面原型：Electron/Vue 桌面端 + Python WebSocket 后端（Sherpa-ONNX 中文流式 ASR、可选 WeSep 纯音频 TSE、声纹门控降级）。入口 `app/start.ps1`，架构详见 [app/docs/ARCHITECTURE.md](app/docs/ARCHITECTURE.md)。
- [`sdk/`](sdk/README.md) — 端侧 SDK 封装（v0 声纹门控）：`web/`（@audiotse/gate 单包双入口：`/core` 无 VAD 内网版 + VAD 完整版）、`cpp/` C++ SDK、`electron-demo/` 演示。
