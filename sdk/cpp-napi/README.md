# @audiotse/gate-napi —— C++ 声纹门控 SDK 的 N-API addon

把 `sdk/cpp` 的 C++ 声纹门控（sherpa-onnx 原生 VAD + 声纹）封装为 **N-API addon**
（`.node`），Node / Electron 主进程直接 `require()`，**免重编译、免子进程**——替代
`sdk/cpp/examples/gate_stream.cc` 的 stdin/stdout 桥接进程方式（该方式仍保留，供
非 Node 宿主使用）。

JS API 与 `sdk/web`（@audiotse/gate）的 `SpeakerGate` **同名同签名**，宿主代码在
web 版与 C++ 原生版之间无感切换：

```js
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
```

差异与约束：

- **推理不阻塞 JS 线程**：create/enrollChunk/finishEnroll/accept/flush 走
  AsyncWorker（libuv 工作线程）；`beginEnroll/reset/dispose` 排队执行、
  `enrolled/effectiveThreshold` 为同步 getter。
- **串行调用**：C++ SDK 要求串行（主/注册 VAD 与声纹推理共享状态），JS 包装层
  （index.js）已用 promise 链保证；直接 require `.node` 裸用时调用方自行串行。
- 判定语义（阈值 0.5、短注册 ×0.7 补偿、剥静音流式注册 1.2s/0.6s）与
  web / Python 版完全一致；`test-gate-napi.js`（electron-demo-napi）对拍通过。

## 构建

前置：VS 2019+（含 C++ 与 CMake/Ninja 组件）、Node 18.20+；sherpa-onnx 预编译包
与 `sdk/cpp` 共用 `sdk/cpp/third_party/`（缺失则先跑 `sdk\cpp\build.ps1`）。

```powershell
cd sdk\cpp-napi
npm install          # 首次：cmake-js + node-addon-api
.\build.ps1          # 激活 VS → cmake-js 编译（Ninja）
```

产物：`build\Release\audiotse_gate_napi.node` + 同目录 4 个运行时 dll
（sherpa-onnx-cxx-api/c-api、onnxruntime、providers_shared）。libuv `dlopen` 用
`LOAD_WITH_ALTERED_SEARCH_PATH`，同目录 dll 自动解析；index.js 还会额外把该目录
注入 `PATH` 兜底。

## Windows 关键点：/DELAYLOAD + 钩子（改这里必读）

addon 链接 `node.lib` 取 napi 符号，必须满足两件事，缺一不可：

1. **`/DELAYLOAD:NODE.EXE` 链接选项**（CMakeLists 显式设置，不依赖 cmake-js
   各版本的行为差异）：否则 node.exe 进普通导入表，Electron 里加载 `.node` 时
   就去 LoadLibrary("node.exe")，把 PATH 里的系统 Node 当库加载 → 版本错配
   **段错误**（Node 里正常，Electron 里必崩，症状很隐蔽）。
2. **`napi/win_delay_load_hook.cc`**（node-gyp 同款延迟加载钩子）：把 NODE.EXE
   的符号解析重定向到宿主进程模块（node.exe 或 electron.exe）。

`.node` 用 NAPI_VERSION=8（ABI 稳定），同一份产物在 Node 18+ 与 Electron
（Electron 41 / Node 24 实测）直接加载，无需 electron-rebuild。

## 结构

```
CMakeLists.txt        构建根：编 ../cpp 三个源文件 + addon；sherpa 依赖经
                      ../cpp/cmake/sherpa-onnx.cmake 共享导入
napi/addon.cc         node-addon-api 绑定层（Gate ObjectWrap + AsyncWorker）
napi/win_delay_load_hook.cc  延迟加载钩子（Node/Electron 通吃的关键）
index.js              JS 包装：SpeakerGate 类，API 对齐 @audiotse/gate + 串行化
index.d.ts            类型定义（与 web 版同名接口）
build.ps1             一键构建（激活 VS → 检查 sherpa 包 → cmake-js）
```

坑位备忘（都踩过）：

- cmake-js 的 CLI：`-G` 才是生成器（`-g` 是偏好 GNU 编译器）、`-B` 是构建配置、
  自定义 CMake 参数必须用 `--CD键=值` 长格式（`-D` 是它的 debug 开关）。
- 别经 `npx` 调 cmake-js：npx 会把 `node_modules\.bin` 塞进 PATH，npm 的 `rc`
  包会被 CMake 误认成 Windows 资源编译器 rc.exe 导致链接失败（build.ps1 直接
  `node node_modules\cmake-js\bin\cmake-js`）。
- `node -p "require('node-addon-api').include_dir"` 返回相对路径，须转绝对路径。
- PowerShell 里裸的 `-DX=$v` **不展开变量**（要写成 `"-DX=$v"`）。
