# AudioTSE 声纹过滤 SDK — 内网交付包

给内网 Electron/Node 业务的离线交付：**注册目标说话人 → 逐段判定/过滤**，
把主讲人的语音挑出来回传。外部 VAD 已由内网业务负责，本 SDK 输入即为切好的
语音段（`Float32Array`，16 kHz 单声道，取值 [-1,1]）。

## 包内容

```
gate/                  SDK 本体（require('<本包>/gate') 即 core 入口）
  dist/core/           编译产物（含 .d.ts 类型）
  examples/            electron-main.example.js（Electron 接入）
                       intranet-wake-flow.js（唤醒词注册+提问过滤可跑演示）
node_modules/          onnxruntime 运行时闭包（全平台二进制）
models/speaker.onnx    3D-Speaker ER2Net 声纹模型（38 MB）
samples/               样例音频（自检脚本用，接入后可删）
smoke-test.js          离线自检：node smoke-test.js
README.md              本文件
```

## 快速自检

```powershell
node smoke-test.js
# 期望输出：三行 ✅ + PASS（模型加载 ~0.5s，全程 ~2s）
```

自检通过说明 Node/Electron 运行环境、onnxruntime 原生库、模型文件三者就绪。

## 接入（Electron 主进程）

```js
// 路径按实际摆放调整；初始化一次全应用复用
const { VoiceFilter } = require('../vendor/gate')   // 即 gate/package.json main → dist/core

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
6. `node_modules` 内含全平台 onnxruntime 二进制；确认大屏是 Windows 且想减小体积，
   可删 `node_modules/onnxruntime-node/bin/napi-v6/` 下除 `win32/x64` 外的目录。
