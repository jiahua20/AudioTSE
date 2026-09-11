# AudioTSE 声纹过滤 SDK — 内网交付包

给内网 Electron/Node 业务的离线交付：**注册目标说话人 → 逐段判定/过滤**，
把主讲人的语音挑出来回传。外部 VAD 已由内网业务负责，本 SDK 输入即为切好的
语音段（`Float32Array`，16 kHz 单声道，取值 [-1,1]）。

## 包内容

```
gate/                  SDK 本体（三种模块格式按目录区分，同一套 API，按宿主环境选）
  cjs/                 CommonJS 多文件（含 .d.ts）：require 直用，Node/Electron 主进程默认
  esm/                 ES Modules 多文件（含 .d.ts）：import 具名导入，Vite/webpack/Rollup 等构建器
  umd/                 UMD 单文件（audiotse-gate.umd.js）：<script> 全局 / AMD / require 亦兼容
examples/              electron-main.example.js（Electron 接线示例，路径按本包布局可直接用）
node_modules/          sherpa-onnx 原生运行时闭包（sherpa-onnx-node + win-x64 二进制
                       + app-local VC++ 运行库；宿主项目已带 sherpa-onnx-node 时共用）
models/sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k.onnx
                       3D-Speaker ER2Net 声纹模型（38 MB）
samples/               样例音频（自检脚本用，接入后可删）
env-check.js           离线自检（第一步）：node env-check.js
smoke-test.js          离线自检（第二步）：node smoke-test.js（走 cjs 包入口，三种格式都查）
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
| Vite/Rollup 报「既没有具名导出也没有默认导出」/ `does not provide an export named 'VoiceFilter'` | 引的是 `gate/cjs/`（纯 CommonJS），构建器做 ESM 静态分析不认 `exports.X = …` | 改引 `gate/esm/`（import 具名导出）；`gate/cjs/`、`gate/umd/` 只用于 require 直引 |

## 接入（Electron 主进程 / Node）

SDK 同一套 API 提供三种模块格式（`gate/` 下按目录区分），按宿主环境选：

| 格式 | 位置 | 接法 | 适用 |
|---|---|---|---|
| CommonJS | gate/cjs/（包 main） | `require('../vendor/gate')` | Node / Electron 主进程直用，多文件代码可读 |
| ES Modules | gate/esm/ | `import { VoiceFilter } from '../vendor/gate/esm'` | Vite / Rollup / webpack 等构建器 |
| UMD | gate/umd/ 单文件 | `<script>` 全局 `AudioTSEGate` / AMD / require | 浏览器直引、AMD、require 亦兼容 |

### 方式 A：CommonJS（gate/cjs/，多文件）

```js
const { VoiceFilter, StreamGate } = require('../vendor/gate')   // 包 main → cjs/index.js
// 或指名目录：require('../vendor/gate/cjs')
```

### 方式 B：ES Modules（gate/esm/，多文件）—— Vite/Rollup/webpack 构建器用这个

```js
import { VoiceFilter, StreamGate } from '../vendor/gate/esm'
// 或包入口：import { VoiceFilter } from '../vendor/gate'（exports 的 import 条件 → esm/index.js）
```

- **为什么构建器不能用 cjs/**：纯 CommonJS（`exports.X = …`）会被 Vite/Rollup 的 ESM
  静态分析判「既无具名导出也无默认导出」直接报错；esm/ 是原生 `import`/`export` 语法。
- **esm/ 面向构建器**：内部相对导入不带 `.js` 后缀（tsc 不改写），Vite/webpack/Rollup
  可解析；**Node 直跑 ESM 不行**，Node 环境请用 gate/cjs。
- sherpa-onnx-node 保持 external 不入包（三种格式共同的约束：运行时需可解析到，见下）。

若贵方用 webpack 等打包主进程代码：**把 `sherpa-onnx-node` 标记为 external**
（`externals: { 'sherpa-onnx-node': 'commonjs sherpa-onnx-node' }`），并把本包的
`node_modules/` 放到打包产物能解析到的位置——它是原生模块（.node + dll），任何
构建器都无法把它打进 js bundle，这正是之前源码编译失败的根源。

> 与旧版（onnxruntime-node 依赖）的区别：SDK 现与贵方项目共用同一份 sherpa-onnx /
> onnxruntime 运行时，**无论加载顺序如何都不会再出现两份 onnxruntime.dll 冲突**
> （旧版在贵方 sherpa-onnx-node 先加载时报 Windows 193「无法运行 %1」）。

### 方式 C：UMD（gate/umd/audiotse-gate.umd.js，单文件）

```js
const { VoiceFilter } = require('../vendor/gate/umd/audiotse-gate.umd.js')   // require 亦兼容
// <script src="../vendor/gate/umd/audiotse-gate.umd.js"></script> 直引挂全局变量：
// const { VoiceFilter } = AudioTSEGate   // 仅限带 require 的环境（nodeIntegration 渲染进程）
```

### 关于 cjs/ 文件里的 `__esModule` 标记

cjs/ 目录的文件开头有 `Object.defineProperty(exports, "__esModule", { value: true })`：
这是 TypeScript/Babel 把 ESM 源码编译成 CommonJS 时加的**互操作标记**（告诉其他转译器
require 时别再包一层 `.default`），**不代表该文件是 ES 模块**——构建器的静态分析不认它。
需要 ESM 导入就用 gate/esm/。

### 初始化与调用（三种方式相同）

```js
const filter = await VoiceFilter.create({
  speakerModel: '…/models/sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k.onnx',
  threshold: 0.5,                   // 放行阈值基准
  shortEnrollThresholdFactor: 0.7,  // 短注册（<1.5s）时实际阈值 0.5×0.7=0.35
})

// 唤醒词模式：客户喊「小耘小耘」唤醒大屏，这句语音同时注册——每次唤醒都调一次
await filter.enroll(wakeWordSamples)            // Float32Array @16k，唤醒词整段

// 唤醒之后的提问段：只过滤、不注册
const targetSpeech = await filter.filter(samples)   // 主讲人语音（原引用）或 null
const { similarity, accepted } = await filter.judge(samples)   // 要相似度时用
```

### 流式窗口门控（StreamGate）—— ASR 打字机场景

整段判定（judge/filter）要等一句话说完才出结果；ASR 边收边转写（打字机效果）的链路
等不了整句，用 **StreamGate：每 500ms 判一次，过则该块立即送 ASR**。

```js
const sg = await StreamGate.create({
  speakerModel: '…/models/sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k.onnx',
  threshold: 0.25,   // 窗口模式独立阈值（默认即 0.25）：短窗相似度整体低于整句，
                     // 整句判定的 0.5 用在窗口上会把本人大量误拒，两种阈值不可混用
})
await sg.enroll(wakeWordSamples)               // 注册不变：唤醒词整段（VoiceFilter 同款）

// 之后每个 500ms 音频块到达即判即转（打字机节奏不被打断）：
const verdict = await sg.push(chunk500ms)      // { accepted, similarity, score, silent }
if (verdict.accepted) asrFeed(chunk500ms)      // 过 → 立即喂 ASR；不过 → 丢弃该块
```

行为与调参：

- **滑窗判定**：每 500ms（hopMs）出一次结论，判定依据是最近 1s（contextMs）音频——
  历史块只作判定上下文不重复发送，相比裸 500ms 窗不增加延迟，相似度更稳（实测
  主讲人中位 0.41→0.52，陌生人 ≤0.09，两类无重叠）。
- **EMA 平滑**（smoothing，默认 0.5）：吸收半句话声纹漂移导致的单窗抖动，说话人
  切换约 1~2 窗翻转；设 0 则每窗独立硬判（更跟手、更抖）。
- **静音直拒**：RMS 低于 silenceRms（默认 0.01）的块跳过推理并拒绝送出（本就无需
  转写，且静音的声纹是乱数会污染平滑）。
- 未注册时全放行（与 VoiceFilter 一致）；实测单窗判定约 60ms，远低于 500ms 步进。
- 包内 `node smoke-test.js` 第 4 步即此模式：主讲人块全放行、陌生人块全拒绝。

Electron IPC 接线（唤醒词注册 + 提问过滤）见 `examples/electron-main.example.js`（与
`gate/`、`models/` 平级），路径已按本包布局写好，可直接参照；端到端验证跑包内
`node smoke-test.js`。

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
