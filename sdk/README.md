# AudioTSE SDK

把 `app/` 原型中验证过的链路封装为可端侧部署的 SDK。v0 从**声纹门控**开始
（silero VAD + 3D-Speaker ER2Net 相似度门控），TSE 真分离后续接入，三条线共用
`app/models/` 的同一份模型文件。

```
web/                 @audiotse/gate —— 单包双入口 TS SDK：
                     主入口 = VAD+声纹门控完整版；'@audiotse/gate/core' = 无 VAD 声纹过滤核心（内网：外部 VAD 切好段 → 注册/判定/过滤）
cpp/                 C++ SDK：直接用 sherpa-onnx 原生库（VAD+声纹）
cpp-napi/            @audiotse/gate-napi —— cpp SDK 的 N-API addon：Node/Electron
                     主进程 require('.node') 直载（免重编译/免子进程），API 与 web 版同名
electron-demo-web/   最小 Electron demo：mic → web SDK 门控 → 只回放目标说话人
electron-demo-cpp/   对照版：后端换 spawn gate_stream.exe 子进程桥接（stdin 帧/stdout JSON）
electron-demo-napi/  推荐接入：后端 require cpp-napi 直载（进程内原生推理，不阻塞主进程）
```

- 各目录**各自独立** `package.json` + `node_modules`，sdk/ 根没有 package.json。
- 四条宿主接入线 API 语义一一对应（`create/enroll/judge(accept)/filter`），
  后续接 TSE 时宿主代码可无感切换。
- 封装路线之争（TS 直接封装 vs C++ 桥接 vs C++ 进程内）由本结构承接：按端侧
  部署目标（浏览器可达性 vs 原生性能/体积 vs Electron 集成形态）再取舍。

## 状态（2026-09-09）

| 目录 | 状态 |
|---|---|
| web（core 入口） | ✅ 内网形态（无 VAD）：注册 + judge/filter，四字注册实测全对，初始化 ~0.5s |
| web（主入口） | ✅ 声纹与 sherpa Python 对拍 cos≥0.9999；端到端通过（含**四字级短注册**：剥静音 + 阈值自适应），RTF~0.06 |
| electron-demo-web | ✅ 冒烟通过（相对路径引 web 包，独立 node_modules）；注册为「说四个字自动完成」 |
| cpp | ✅ 已编译验证（gate_demo/gate_stream）；短注册 API（剥静音/阈值补偿）已对齐 web 版 |
| cpp-napi | ✅ Node 18 与 Electron 41（Node 24）双端加载验证；test-gate-napi 与 web 流式基准逐位一致 |
| electron-demo-napi | ✅ Electron 直载原生推理验证通过；preload/renderer 与 web demo 相同 |
