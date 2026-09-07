# AudioTSE SDK

把 `app/` 原型中验证过的链路封装为可端侧部署的 SDK。v0 从**声纹门控**开始
（silero VAD + 3D-Speaker ER2Net 相似度门控），TSE 真分离后续接入，三条线共用
`app/models/` 的同一份模型文件。

```
web/           TS SDK（@audiotse/gate）：Node/Electron 可用，声纹已对拍 Python 引擎逐位验证
cpp/           C++ SDK：直接用 sherpa-onnx 原生库（VAD+声纹），源码就绪待 MSVC 环境编译
electron-demo/ 最小 Electron demo：mic → 门控 → 只回放目标说话人（冒烟通过）
```

- 三条线 API 语义一一对应（`create/enroll/accept/flush`，段式事件含
  `similarity/accepted/samples`），后续接 TSE 时宿主代码可无感切换。
- npm workspaces：在 `sdk/` 下统一 `npm install`，`npm start -w electron-demo` 跑 demo。
- 封装路线之争（TS 直接封装 vs C++ 桥接）由本结构承接：两条都保留，按端侧
  部署目标（浏览器可达性 vs 原生性能/体积）再取舍。

## 状态（2026-09-07）

| 目录 | 状态 |
|---|---|
| web | ✅ 声纹与 sherpa Python 对拍 cos≥0.9999；端到端通过（含**四字级短注册**：剥静音 + 阈值自适应），RTF~0.06 |
| electron-demo | ✅ 冒烟通过（SDK 在 Electron 主进程加载、窗口存活）；注册已改为「说四个字自动完成」 |
| cpp | ⚠️ 源码+CMake 就绪，本机无 MSVC 未编译验证；短注册 API（剥静音/阈值补偿）待对齐 web 版后补 |
