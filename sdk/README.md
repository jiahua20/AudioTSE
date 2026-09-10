# AudioTSE SDK

把 `app/` 原型中验证过的链路封装为可端侧部署的 SDK。v0 从**声纹门控**开始
（silero VAD + 3D-Speaker ER2Net 相似度门控），TSE 真分离后续接入，三条线共用
`app/models/` 的同一份模型文件。

```
web/                 @audiotse/gate —— 单包双入口 TS SDK（sherpa-onnx-node 1.12.1）：
                     主入口 = VAD+声纹门控完整版；'@audiotse/gate/core' = 无 VAD 声纹过滤核心（内网：外部 VAD 切好段 → 注册/判定/过滤）
cpp-napi/            @audiotse/gate-napi —— C++ SDK 的 N-API addon（自包含构建根：
                     src/ = C++ 源码，third_party/ = sherpa 1.12.1 预编译包）。
                     Node/Electron 主进程 require('.node') 直载（免重编译），双入口 API 与 web 版同名
electron-demo-web/   最小 Electron demo：mic → web SDK 门控 → 只回放目标说话人
electron-demo-napi/  对照版：后端 require cpp-napi 直载（进程内原生推理，不阻塞主进程）
```

- 各目录**各自独立** `package.json` + `node_modules`，sdk/ 根没有 package.json。
- 三条宿主接入线 API 语义一一对应（`create/enroll/judge(accept)/filter`），
  后续接 TSE 时宿主代码可无感切换。
- 推理运行时统一 sherpa-onnx **1.12.1**（与内网项目同版本）：进程内 dll 按模块名
  去重后是同一版本，web/napi 两 SDK 与宿主自带的 sherpa-onnx-node 以任意顺序共存。
  （完整版 VAD 用 silero v4 模型：1.12.1 不支持 v5，两者切段结果实测逐位一致。）

## 内网交付（web / napi 双包对照）

```powershell
powershell -File sdk\web\script\package-intranet.ps1         # → sdk\web\script\out\audiotse-intranet-web-*.zip
powershell -File sdk\cpp-napi\script\package-intranet-napi.ps1  # → sdk\cpp-napi\script\out\audiotse-intranet-napi-*.zip
```

两包同 API（VoiceFilter）、同模型、判定基准逐位一致（0.582 放行 / -0.044 拒绝），
均自带 VC++ 运行库（内网机器免安装），供内网对照使用（效果/速度择优）。

## 状态（2026-09-10）

| 目录 | 状态 |
|---|---|
| web（core 入口） | ✅ 内网形态（无 VAD）：注册 + judge/filter；推理已迁移 sherpa-onnx-node 1.12.1，声纹对拍 cos≥0.9999 |
| web（主入口） | ✅ 端到端通过（四字级短注册 + 阈值自适应），RTF~0.06；VAD 换 silero v4 后流式基准不变 |
| cpp-napi | ✅ 自包含构建（sherpa 1.12.1，C API 声纹提取）；新增 VoiceFilter（core）入口；全量流式基准逐位一致 |
| electron-demo-web | ✅ sherpa-onnx-node 原生推理冒烟通过；注册为「说四个字自动完成」 |
| electron-demo-napi | ✅ Electron 直载原生推理验证通过；preload/renderer 与 web demo 相同 |
| 内网双包 | ✅ Electron 28 + 宿主 sherpa-onnx-node 同进程共存终验通过（任意加载顺序，判定基准一致） |

> 旧子进程方案（sdk/cpp + gate_stream.exe + electron-demo-cpp）已于 2026-09-10
> 移除：C++ 源码并入 cpp-napi 自包含构建，宿主接入统一走 N-API。
