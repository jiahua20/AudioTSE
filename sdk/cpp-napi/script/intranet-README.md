# AudioTSE 声纹过滤 SDK（C++ N-API 版）— 内网交付包

与 **web 版交付包**（audiotse-intranet-web-\*.zip）同一套 API、同一批模型与判定基准的
**C++ 原生实现**：注册目标说话人 → 逐段判定/过滤，把主讲人的语音挑出来回传。
外部 VAD 已由内网业务负责，本 SDK 输入即为切好的语音段（`Float32Array`，16 kHz 单声道，
取值 [-1,1]）。两包可同时接入同一项目对照使用（推理同源 sherpa-onnx 1.12.1 运行时，
判定结果逐位一致，差异只在宿主侧包装层：TypeScript vs C++ addon，可对比速度/内存）。

## 包内容

```
gate-napi/             SDK 本体（require('<本包>/gate-napi') 即入口）
  index.js             JS 包装（VoiceFilter 整段判定 + StreamGate 流式窗口门控，API 与 web 版 core 入口同签名）
  index.d.ts           配套类型声明
  native/              audiotse_gate_napi.node + sherpa-onnx 1.12.1 运行时 dll
                       + app-local VC++ 运行库（免安装）
examples/              electron-main.example.js（Electron 主进程接线示例，路径按本包布局可直接用）
models/sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k.onnx
                       3D-Speaker ER2Net 声纹模型（38 MB，与 web 包同一份）
samples/               样例音频（自检脚本用，接入后可删）
env-check.js           离线自检（第一步）：node env-check.js
smoke-test.js          离线自检（第二步）：node smoke-test.js
README.md              本文件
```

## 快速自检

```powershell
node env-check.js    # 运行环境架构 + addon 二进制完整性 + 依赖 DLL 逐个解析
node smoke-test.js   # 全链路自检（与 web 包同输入同基准：0.582 放行 / -0.044 拒绝；含 500ms 流式窗口门控）
```

## 接入（Electron 主进程）

```js
// 与 web 版唯一区别：require 路径（API 完全同名同签名，业务代码可一行切换）。
// 下方以包根为视角；拷入业务工程后改成实际摆放路径，如 vendor 布局：require('../vendor/gate-napi')
const { VoiceFilter } = require('./gate-napi')

const filter = await VoiceFilter.create({ speakerModel: '…/models/sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k.onnx', threshold: 0.5 })

await filter.enroll(wakeWordSamples)                    // 每次唤醒都重新注册
const speech = await filter.filter(samples)             // 主讲人语音（原引用）或 null
const { similarity, accepted } = await filter.judge(samples)  // 要相似度时用
```

### 流式窗口门控（StreamGate）—— ASR 打字机场景

ASR 边收边转写（打字机效果）等不了整句说完，用 **StreamGate：每 500ms 判一次，过则
该块立即送 ASR**（API 与 web 版同名同签名，窗口逻辑在 JS 包装层实现，推理走 C++ addon）：

```js
const { StreamGate } = require('./gate-napi')

const sg = await StreamGate.create({
  speakerModel: '…/models/sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k.onnx',
  threshold: 0.25,   // 窗口模式独立阈值（默认即 0.25）：短窗相似度整体低于整句，
                     // 整句判定的 0.5 用在窗口上会把本人大量误拒，两种阈值不可混用
})
await sg.enroll(wakeWordSamples)               // 注册不变：唤醒词整段

// 之后每个 500ms 音频块到达即判即转：
const verdict = await sg.push(chunk500ms)      // { accepted, similarity, score, silent }
if (verdict.accepted) asrFeed(chunk500ms)      // 过 → 立即喂 ASR；不过 → 丢弃该块
```

行为与调参（与 web 版完全一致）：滑窗判定（hopMs 500ms 出结论、contextMs 最近 1s 作
判定上下文，不重复发送、不增加延迟）；EMA 平滑（smoothing 0.5，说话人切换约 1~2 窗
翻转，0 = 每窗硬判）；静音直拒（RMS < silenceRms 0.01 跳过推理）；未注册全放行。
实测单窗判定约 60ms（libuv 工作线程，不阻塞主进程）；包内 `node smoke-test.js`
第 4 步即此模式。

Electron IPC 接线（唤醒词注册 + 提问过滤）见 `examples/electron-main.example.js`，
路径已按本包布局写好；端到端验证直接跑包内 `node smoke-test.js`；仓库 `sdk/electron-demo-napi`
为含 preload/renderer 与麦克风采集的完整最小 Demo。

## web 版 / napi 版对照说明

| | web 版（audiotse-intranet-web） | napi 版（本包） |
|---|---|---|
| 实现 | TypeScript，推理走 sherpa-onnx-node（npm 包） | C++ addon（本包自带 .node + dll） |
| 推理运行时 | sherpa-onnx 1.12.1（进程内共用宿主的） | sherpa-onnx 1.12.1（本包 native/ 自带） |
| 判定基准 | 0.582 放行 / -0.044 拒绝 | **逐位一致** |
| 依赖形态 | 宿主需可 require 到 sherpa-onnx-node（或用包内闭包） | 零 npm 依赖（native/ 自包含） |
| 推理线程 | sherpa 内部线程 | libuv 工作线程（AsyncWorker，不阻塞主进程） |
| 调用约束 | 串行（JS 侧已用 promise 链保证） | 同左 |

两包可对照测：同输入分别计时 `filter.enroll/judge`，或看内存占用。
注意 sherpa 运行时同版本（1.12.1），同进程先后加载两包不会 dll 冲突。

## 注意事项

1. **输入必须 16 kHz 单声道 float32 [-1,1]**：内网 VAD 若输出 48k/8k 先重采样；
   Int16 先除以 32768。每次喂**一段完整语音**（VAD 切好的整句）。
2. 若贵方用 webpack 等打包主进程代码：**不要把 `gate-napi`（含 .node）塞进 bundle**，
   保持 `require('../vendor/gate-napi')` 运行时解析（或配置 externals 指向该路径）。
3. 唤醒词四个音节 ≈1s，属短注册安全区但余量较薄：现场若偶发误拒主讲人，
   把 `shortEnrollThresholdFactor` 调低（如 0.65），或让用户唤醒词多说一个字。
4. 多人**同时**说话（重叠语音）只能整段放行/拒绝，无法分离——真分离等 TSE 版本。
5. `create()` 加载模型 ~0.5s，应用启动时初始化一次；注册/判定已在内部串行排队。

## 故障排查

| 报错 | 原因 | 处置 |
|---|---|---|
| 加载 `audiotse_gate_napi.node` 报 `The specified module could not be found`（Windows 126） | 机器没装 VC++ 2015-2022 运行库 | **包已把 5 个运行库 DLL 放进 `gate-napi/native/`（app-local 免安装）**，报此错说明用的是旧包或文件被移动；跑 `node env-check.js` 逐个 DLL 看解析来源 |
| 加载 addon 报 `The operating system cannot run %1`（Windows 193） | 二进制传输/解压/入库损坏（git 换行转换最常见），或进程不是 x64 | `node env-check.js`：哈希 ❌ → 用官方 zip 重新解压；架构 ❌ → 换 64 位 Node/Electron |
| Electron 28+ 下 sherpa 的 `readWave()` 报 `External buffers are not allowed` | Electron 禁用 external ArrayBuffer（sherpa 便利函数受影响，**本 SDK 不用它**） | 业务侧读 wav 自备 PCM 解析（包内 smoke-test 即示例） |
| `was compiled against a different Node.js version` | 混入了非 N-API 原生模块（本包为 N-API v8，无此问题） | 确认加载的是本包 native/ 下的 .node |
