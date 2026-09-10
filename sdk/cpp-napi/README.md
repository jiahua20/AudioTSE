# @audiotse/gate-napi —— C++ 声纹门控 SDK 的 N-API addon（自包含构建根）

把 C++ 声纹门控（sherpa-onnx 原生 VAD + 声纹）封装为 **N-API addon**（`.node`），
Node / Electron 主进程直接 `require()`，**免重编译、免子进程、零 npm 运行时依赖**
（native 二进制随包自带）。C++ 源码（src/）与 sherpa 预编译包（third_party/）都在
本目录——旧 `sdk/cpp`（子进程桥接方式）已并入此处移除。

JS API 与 `sdk/web`（@audiotse/gate）**双入口同名同签名**，宿主代码在 web 版与
C++ 原生版之间无感切换：

```js
// 完整版（VAD 切段 + 门控），对应 @audiotse/gate 主入口
const { SpeakerGate } = require('@audiotse/gate-napi')
const gate = await SpeakerGate.create({ vadModel, speakerModel })
gate.beginEnroll()
const progress = await gate.enrollChunk(chunk)   // {speechSeconds, enough}
if (progress.enough) await gate.finishEnroll()
for (const event of await gate.accept(chunk)) {  // GateSegmentEvent[]
  // event: {type:'segment', start, durationSeconds, similarity|null, accepted, samples:Float32Array}
}
const tail = await gate.flush()
gate.dispose()

// core 版（无 VAD，内网场景），对应 @audiotse/gate/core
const { VoiceFilter } = require('@audiotse/gate-napi')
const filter = await VoiceFilter.create({ speakerModel, threshold: 0.5 })
await filter.enroll(wakeWordSamples)             // 每次唤醒都重新注册
const { similarity, accepted } = await filter.judge(segment)
const speech = await filter.filter(segment)      // 主讲人语音（原引用）或 null
```

差异与约束：

- **推理不阻塞 JS 线程**：create/enrollChunk/finishEnroll/accept/flush/enroll/judge
  走 AsyncWorker（libuv 工作线程）；`beginEnroll/reset/dispose` 排队执行、
  `enrolled/effectiveThreshold` 为同步 getter。
- **串行调用**：C++ SDK 要求串行（主/注册 VAD 与声纹推理共享状态），JS 包装层
  （index.js）已用 promise 链保证；直接 require `.node` 裸用时调用方自行串行。
- 判定语义（阈值 0.5、短注册 ×0.7 补偿、剥静音流式注册 1.2s/0.6s）与
  web / Python 版完全一致；`test-gate-napi.js`（electron-demo-napi）对拍通过。

## sherpa 版本选型（重要）

预编译包用 **1.12.1**，与内网项目 / sdk/web 的 sherpa-onnx-node 严格同版本：
Windows 进程内 dll 按模块名去重，同版本时任何加载顺序都无冲突；链别的版本
（如 1.13.7）会在宿主已加载 1.12.1 时发生同名 dll 错配（Windows 193）。
1.12.1 的 cxx-api 尚无 SpeakerEmbeddingExtractor 封装，`src/core/embedder.cpp`
直接用 C API；包内不带 onnxruntime.lib 导入库，`build.ps1` 会用 dumpbin+lib
从 dll 自动生成。

## 构建

前置：VS 2019+（含 C++ 与 CMake/Ninja 组件）、Node 18.20+；
`third_party/` 需有 sherpa-onnx 1.12.1 预编译包
（`sherpa-onnx-v1.12.1-win-x64-shared.tar.bz2` 解压放入，目录名保持官方全名）。

```powershell
cd sdk\cpp-napi
npm install          # 首次：cmake-js + node-addon-api
.\build.ps1          # 激活 VS → 生成缺失的 onnxruntime.lib → cmake-js 编译（Ninja）
```

产物：`build\Release\audiotse_gate_napi.node` + 同目录 4 个运行时 dll
（sherpa-onnx-cxx-api/c-api、onnxruntime、providers_shared）。libuv `dlopen` 用
`LOAD_WITH_ALTERED_SEARCH_PATH`，同目录 dll 自动解析；index.js 还会额外把该目录
注入 `PATH` 兜底。

## 内网交付

```powershell
powershell -File sdk\cpp-napi\script\package-intranet-napi.ps1
# → script\out\audiotse-intranet-napi-<时间戳>.zip（与 web 版双包对照，见包内 README）
```

## Windows 关键点：/DELAYLOAD + 钩子（改这里必读）

addon 链接 `node.lib` 取 napi 符号，必须满足两件事，缺一不可：

1. **`/DELAYLOAD:NODE.EXE` 链接选项**（CMakeLists 显式设置，不依赖 cmake-js
   各版本的行为差异）：否则 node.exe 进普通导入表，Electron 里加载 `.node` 时
   就去 LoadLibrary("node.exe")，把 PATH 里的系统 Node 当库加载 → 版本错配
   **段错误**（Node 里正常，Electron 里必崩，症状很隐蔽）。
2. **`napi/win_delay_load_hook.cc`**（node-gyp 同款延迟加载钩子）：把 NODE.EXE
   的符号解析重定向到宿主进程模块（node.exe 或 electron.exe）。

`.node` 用 NAPI_VERSION=8（ABI 稳定），同一份产物在 Node 18+ 与 Electron
（Electron 28 / 41 实测）直接加载，无需 electron-rebuild。

## 结构

```
src/core/              C++ 源码：embedder（sherpa C API 声纹提取）+ voice_filter
src/full/              C++ 源码：speaker_gate（VAD 切段 + 门控）
include/               C++ 公共头
third_party/           sherpa-onnx 1.12.1 预编译包（gitignore，手动放置）
cmake/sherpa-onnx.cmake sherpa 探测/导入（c-api/cxx-api/onnxruntime 三个 IMPORTED 目标）
napi/addon.cc          node-addon-api 绑定层（Gate + Filter ObjectWrap + AsyncWorker）
napi/win_delay_load_hook.cc  延迟加载钩子（Node/Electron 通吃的关键）
index.js / index.d.ts  JS 包装：SpeakerGate + VoiceFilter 双入口，API 对齐 @audiotse/gate + 串行化
examples/              intranet-wake-flow.js / electron-main.example.js
script/                内网打包 + env-check + smoke-test（out/ 为产物，gitignore）
build.ps1              一键构建（激活 VS → 检查/补齐 sherpa 依赖 → cmake-js）
```

坑位备忘（都踩过）：

- cmake-js 的 CLI：`-G` 才是生成器（`-g` 是偏好 GNU 编译器）、`-B` 是构建配置、
  自定义 CMake 参数必须用 `--CD键=值` 长格式（`-D` 是它的 debug 开关）。
- 别经 `npx` 调 cmake-js：npx 会把 `node_modules\.bin` 塞进 PATH，npm 的 `rc`
  包会被 CMake 误认成 Windows 资源编译器 rc.exe 导致链接失败（build.ps1 直接
  `node node_modules\cmake-js\bin\cmake-js`）。
- `node -p "require('node-addon-api').include_dir"` 返回相对路径，须转绝对路径。
- PowerShell 里裸的 `-DX=$v` **不展开变量**（要写成 `"-DX=$v"`）。
- Windows PowerShell 5.1 读无 BOM 的 UTF-8 脚本按 GBK 解析：.ps1 必须带 UTF-8 BOM。
