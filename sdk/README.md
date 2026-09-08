# AudioTSE SDK

把 `app/` 原型中验证过的链路封装为可端侧部署的 SDK。v0 从**声纹门控**开始
（silero VAD + 3D-Speaker ER2Net 相似度门控），TSE 真分离后续接入，三条线共用
`app/models/` 的同一份模型文件。

```
web/           @audiotse/gate —— 单包双入口 TS SDK：
                 主入口 = VAD+声纹门控完整版；'@audiotse/gate/core' = 无 VAD 声纹过滤核心（内网：外部 VAD 切好段 → 注册/判定/过滤）
electron-demo/ 最小 Electron demo：mic → 门控 → 只回放目标说话人（相对路径引 ../web，不共享 node_modules）
cpp/           C++ SDK：直接用 sherpa-onnx 原生库（VAD+声纹），源码就绪待 MSVC 环境编译
```

- web 与 electron-demo **各自独立** `package.json` + `node_modules`，sdk/ 根没有 package.json。
- 三条线 API 语义一一对应（`create/enroll/judge(accept)/filter`），后续接 TSE 时宿主代码可无感切换。
- 封装路线之争（TS 直接封装 vs C++ 桥接）由本结构承接：两条都保留，按端侧
  部署目标（浏览器可达性 vs 原生性能/体积）再取舍。

## 状态（2026-09-07）

| 目录 | 状态 |
|---|---|
| web（core 入口） | ✅ 内网形态（无 VAD）：注册 + judge/filter，四字注册实测全对，初始化 ~0.5s |
| web（主入口） | ✅ 声纹与 sherpa Python 对拍 cos≥0.9999；端到端通过（含**四字级短注册**：剥静音 + 阈值自适应），RTF~0.06 |
| electron-demo | ✅ 冒烟通过（相对路径引 web 包，独立 node_modules）；注册为「说四个字自动完成」 |
| cpp | ⚠️ 源码+CMake 就绪，本机无 MSVC 未编译验证；短注册 API（剥静音/阈值补偿）待对齐 web 版后补 |
