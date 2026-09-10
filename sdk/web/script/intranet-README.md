# AudioTSE 声纹过滤 SDK — 内网交付包

给内网 Electron/Node 业务的离线交付：**注册目标说话人 → 逐段判定/过滤**，
把主讲人的语音挑出来回传。外部 VAD 已由内网业务负责，本 SDK 输入即为切好的
语音段（`Float32Array`，16 kHz 单声道，取值 [-1,1]）。

## 包内容

```
gate/                  SDK 本体
  audiotse-gate.umd.js UMD 单文件库（推荐接入面：一个 js 直引，免编译免打包配置）
  audiotse-gate.d.ts   配套类型声明
  dist/core/           多文件 CommonJS 版（API 相同，作为备选入口）
  examples/            electron-main.example.js（Electron 接入）
                       intranet-wake-flow.js（唤醒词注册+提问过滤可跑演示）
node_modules/          sherpa-onnx 原生运行时闭包（sherpa-onnx-node + win-x64 二进制
                       + app-local VC++ 运行库；宿主项目已带 sherpa-onnx-node 时共用）
models/speaker.onnx    3D-Speaker ER2Net 声纹模型（38 MB）
samples/               样例音频（自检脚本用，接入后可删）
env-check.js           离线自检（第一步）：node env-check.js
smoke-test.js          离线自检（第二步）：node smoke-test.js（走 UMD 入口）
README.md              本文件
```

## 快速自检

```powershell
node env-check.js    # 第一步：查运行环境架构 + 原生二进制完整性（不加载模型，~1s）
node smoke-test.js   # 第二步：全链路自检
# 期望输出：各行 ✅ + PASS（模型加载 ~0.5s，全程 ~2s）
```

自检通过说明 Node/Electron 运行环境、onnxruntime 原生库、模型文件三者就绪。

## 故障排查

| 报错 | 原因 | 处置 |
|---|---|---|
| 加载 `sherpa-onnx.node` 报 `The specified module could not be found`（Windows 126 / `ERR_DLOPEN_FAILED`） | 机器没装 VC++ 2015-2022 运行库（msvcp140/vcruntime140 等） | **交付包已把 5 个运行库 DLL 放进 `node_modules\sherpa-onnx-win-x64\`（app-local 免安装）**，报此错说明用的是旧包——换最新 zip；跑 `node env-check.js` 可逐个 DLL 看解析来源 |
| 加载原生模块报 `The operating system cannot run %1`（Windows 错误 193） | ① 二进制在传输/解压/入库时损坏——**node_modules 走 git 传输会被换行转换毁掉**（autocrlf 把二进制当文本）② 进程不是 x64（32 位 Electron/Node 装不上 x64 原生库）③ 进程里已先加载了另一份**不同版本**的 onnxruntime.dll（若贵方自行换了 sherpa-onnx-node 版本） | 跑 `node env-check.js`：哈希 ❌ → 用官方 zip 重新解压（换 7-Zip/WinRAR），node_modules 一律走 zip 交付、禁走 git；架构 ❌ → 换 64 位 Node/Electron；版本混装 → 统一用 1.12.1 |
| Electron 28+ 下 sherpa 的 `readWave()` 报 `External buffers are not allowed` | Electron 禁用了 external ArrayBuffer（sherpa 便利函数受影响，**SDK 自身路径不受影响**：内部全用拷贝 buffer） | 业务侧读 wav 别用 sherpa 的 `readWave`（自备 PCM 解析，或用支持外部 buffer 的加载器）；SDK 的 enroll/judge/filter 不受影响 |
| `was compiled against a different Node.js version` | 用了非 N-API 的普通 Node 原生模块（本包无此问题，sherpa-onnx-node 为 N-API） | 确认引用的是本包 node_modules，而非贵方自行安装的其它原生包 |

## 接入（Electron 主进程）

### 方式 A：UMD 单文件（推荐，自有构建管线编不过源码时用）

```js
// 直接引一个 js，不需要 TypeScript 编译，也不需要把 SDK 源码塞进构建管线
const { VoiceFilter } = require('../vendor/gate/audiotse-gate.umd.js')

// <script src="../vendor/gate/audiotse-gate.umd.js"></script> 直引时挂全局变量：
// const { VoiceFilter } = AudioTSEGate   // 仅限带 require 的环境（nodeIntegration 渲染进程）
```

若贵方用 webpack 等打包主进程代码：**把 `sherpa-onnx-node` 标记为 external**
（`externals: { 'sherpa-onnx-node': 'commonjs sherpa-onnx-node' }`），并把本包的
`node_modules/` 放到打包产物能解析到的位置——它是原生模块（.node + dll），任何
构建器都无法把它打进 js bundle，这正是之前源码编译失败的根源。

> 与旧版（onnxruntime-node 依赖）的区别：SDK 现与贵方项目共用同一份 sherpa-onnx /
> onnxruntime 运行时，**无论加载顺序如何都不会再出现两份 onnxruntime.dll 冲突**
> （旧版在贵方 sherpa-onnx-node 先加载时报 Windows 193「无法运行 %1」）。

### 方式 B：包入口（多文件 CommonJS，与 UMD 等 API）

```js
const { VoiceFilter } = require('../vendor/gate')   // gate/package.json main → UMD
```

### 初始化与调用（两种方式相同）

```js
const filter = await VoiceFilter.create({
  speakerModel: '…/models/speaker.onnx',
  threshold: 0.5,                   // 放行阈值基准
  shortEnrollThresholdFactor: 0.7,  // 短注册（<1.5s）时实际阈值 0.5×0.7=0.35
})

// 唤醒词模式：客户喊「小耘小耘」唤醒大屏，这句语音同时注册——每次唤醒都调一次
await filter.enroll(wakeWordSamples)            // Float32Array @16k，唤醒词整段

// 唤醒之后的提问段：只过滤、不注册
const targetSpeech = await filter.filter(samples)   // 主讲人语音（原引用）或 null
const { similarity, accepted } = await filter.judge(samples)   // 要相似度时用
```

完整可跑流程见 `gate/examples/intranet-wake-flow.js`（把里面的路径换成本包布局即可）；
Electron IPC 接线见 `gate/examples/electron-main.example.js`。

## 注意事项

1. **输入必须 16 kHz 单声道 float32 [-1,1]**：内网 VAD 若输出 48k/8k 先重采样；
   Int16 先除以 32768。
2. 每次喂**一段完整语音**（VAD 切好的整句）；SDK 不切分、不剥静音。
3. 唤醒词四个音节 ≈1s，属短注册安全区但余量较薄：现场若偶发误拒主讲人，
   把 `shortEnrollThresholdFactor` 调低（如 0.65），或让用户唤醒词多说一个字。
4. 多人**同时**说话（重叠语音）只能整段放行/拒绝，无法分离——真分离等 TSE 版本。
5. `create()` 加载模型 ~0.5s，应用启动时初始化一次；判定耗时 ~100ms/2s 语音
   （CPU），建议注册/判定在主进程串行调用，避免并发多 session。
6. 运行时闭包仅 `sherpa-onnx-node` + `sherpa-onnx-win-x64` 两个包（win-x64 二进制
   仅 ~13 MB，无平台裁剪需要）；贵方项目若已自带同版本 sherpa-onnx-node，
   SDK 直接共用，无需本包的 node_modules 也能跑。
