# @audiotse/gate — AudioTSE 声纹门控 SDK（单包双入口）

一个 npm 包，两个入口，同一套声纹内核（sherpa-onnx-node 的 SpeakerEmbeddingExtractor
+ ER2Net，与 sherpa-onnx Python 引擎对拍 cos≥0.9999）：

| 入口 | 用途 | 额外依赖 |
|---|---|---|
| `@audiotse/gate`（主入口） | 完整版：自带 silero VAD 切段 + 声纹门控，从原始音频流开始处理 | 无（与 core 共用 sherpa-onnx-node） |
| `@audiotse/gate/core` | **内网场景**：外部已有 VAD，只要「注册 + 声纹判定/过滤」 | 无（仅 sherpa-onnx-node 1.12.1） |

> 推理运行时统一为 **sherpa-onnx-node 1.12.1**（原生 N-API，与内网项目同版本，
> 进程内共用同一份 onnxruntime，无 dll 冲突；完整版的 VAD 用 silero **v4** 模型——
> 1.12.1 不支持 v5，两者切分结果实测逐位一致）。迁移前用 onnxruntime-node +
> sherpa-onnx WASM，因与内网项目自带的 onnxruntime.dll 同名冲突（Windows 193）而替换。

```bash
# 本包独立安装与构建（本目录）
npm install
npm run build       # src → dist（dist/index.js 完整版；dist/core/ 核心版）
npm run test:core   # core 端到端
npm run validate    # 声纹对拍（sherpa extractor vs sherpa Python 引擎，cos≥0.999）
npm test            # full 端到端（短注册/轮流发言/重叠语音）
```

---

## core：无 VAD 声纹过滤（内网接入指南）

给**外部已有 VAD** 的业务用：业务侧 VAD 把语音切成段喂进来，SDK 做
「注册目标说话人 → 逐段判定/过滤」，把主讲人的语音挑出来回传。

- 输入：`Float32Array`，16 kHz 单声道，取值 [-1,1]（业务 VAD 已切好的语音段）
- 运行环境：Node.js ≥18.20 / Electron 主进程（CommonJS `require`）
- 推理：sherpa-onnx-node（CPU），声纹模型 3D-Speaker ER2Net（38 MB）

### API

```ts
const filter = await VoiceFilter.create({
  speakerModel: '.../model.onnx',   // ER2Net 声纹模型路径
  threshold: 0.5,                   // 放行阈值基准（默认 0.5）
  shortEnrollThresholdFactor: 0.7,  // 短注册阈值补偿（默认 0.7；设 1 禁用）
})

await filter.enroll(samples)                 // 注册：一段完整语音 → {speechSeconds}
const { similarity, accepted } = await filter.judge(samples)  // 判定一段
const out = await filter.filter(samples)     // 目标语音原样返回，非目标返回 null
```

- `enroll` 前所有 `judge/filter` 全放行；`filter()` 返回**同一引用**（不拷贝）。
- 注册语音 <1.5s 时实际阈值自动降为 `threshold × 0.7`（短注册相似度整体下移，
  不补偿会误拒；`filter.effectiveThreshold` 读当前生效值）。

Electron 主进程接入示例见 [`examples/electron-main.example.js`](examples/electron-main.example.js)；
完整可跑的流程演示（纯 Node，不依赖 Electron）见
[`examples/intranet-wake-flow.js`](examples/intranet-wake-flow.js)——在 `sdk/web` 下
`node examples/intranet-wake-flow.js` 即可复现。

### 唤醒词模式（内网典型接入）

客户喊唤醒词（如「小耘小耘」）唤醒大屏：**这句唤醒语音同时做注册**，每次唤醒都
重新调用一次 `enroll`（注册声纹随唤醒人刷新）；唤醒之后的提问只走
`judge/filter`，不再注册。唤醒词四个音节 ≈1s，正好落在短注册安全区（<1.5s 自动
阈值补偿生效，实测同人相似度 0.35~0.51、陌生人 ~0，注意 1s 注册下同人相似度余量
较薄，若现场实测偶发误拒可把 `shortEnrollThresholdFactor` 从 0.7 再调低，或让唤醒
词多一个音节）。

### 内网交付清单

一键打包（推荐）：

```powershell
powershell -File sdk\web\script\package-intranet.ps1    # zip ~42 MB（sherpa-onnx 运行时 + VC++ 库 + 模型）
```

产出 `sdk/web/script/out/audiotse-intranet-web-<时间戳>.zip`，内含 SDK 产物（**含 UMD
单文件 `gate/audiotse-gate.umd.js`**，宿主免编译直引一个 js 即可接入，适合自有
构建管线编不过源码的场景）、sherpa-onnx 原生运行时闭包（**含 app-local 的 VC++
运行库 DLL，内网机器免装任何运行库**；宿主项目已带 sherpa-onnx-node 时直接共用，
不会引入第二份 onnxruntime）、38 MB 声纹模型、样例音频和
离线自检脚本（解压后 `node env-check.js` 查环境、`node smoke-test.js` 验证全链路）。
手动清点的话需要：

| 内容 | 位置 | 大小 |
|---|---|---|
| SDK 产物 | `audiotse-gate.umd.js`（或 `dist/core/`） | ~20 KB |
| 推理运行时 | `node_modules/sherpa-onnx-node/` + `sherpa-onnx-win-x64/`（均无传递依赖） | ~13 MB |
| VC++ 运行库 | `node_modules/sherpa-onnx-win-x64/` 内 5 个 DLL（app-local） | ~1 MB |
| 声纹模型 | `app/models/sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k/model.onnx` | 38 MB |

目录摆法（示例）：

```
内网项目/
├── node_modules/sherpa-onnx-win-x64/     ← 拷贝（原生模块，构建器需标 external；项目已有则共用）
├── vendor/audiotse-gate/                 ← audiotse-gate.umd.js（或 dist/core 多文件版）
│   └── models/speaker.onnx               ← 38MB 模型
└── electron/main.js                      ← require('../../vendor/audiotse-gate/audiotse-gate.umd.js')
```

npm 安装方式（有私服/离线 tgz 时）：`require('@audiotse/gate/core')` 子路径入口；
纯路径引用则 `require('<web包>/dist/core')`。

### 注意事项

1. **采样率必须是 16k**：内网 VAD 若输出 48k/8k 需先重采样到 16k（相似度对采样率
   敏感）。取值范围 [-1,1]；若是 Int16 先 `/32768`。
2. **每次喂「一段」完整语音**（VAD 切好的整段，如一句话）。SDK 不切分、不剥静音。
3. 注册建议 ≥1s（四个字）；0.6s 是实测悬崖边。
4. 多人**同时**说话（重叠语音）时只能整段放行/拒绝，无法分离——真分离等 TSE 接入。
5. 推理耗时：2s 语音 ~250 ms（RTF ~0.12，CPU）；注册/判定建议在主进程串行调用。
6. `create()` 加载模型 ~0.5s，启动时初始化一次复用。

---

## full：VAD + 声纹门控（完整版）

| 环节 | 实现 | 说明 |
|---|---|---|
| VAD 切段 | sherpa-onnx-node 原生 `Vad` | 封装层统一按 160ms 切块喂入（sherpa 对大块单次喂入会丢段边界），行为与调用方块大小无关；模型用 silero **v4**（1.12.1 不支持 v5，切段结果实测一致） |
| 声纹判定 | 本包 core（sherpa `SpeakerEmbeddingExtractor`） | 特征前端（fbank/归一化）与 ER2Net 推理均在 sherpa 原生代码内完成，与 Python 引擎对拍 cos≥0.9999 |

### 使用

```ts
import { SpeakerGate } from '@audiotse/gate'

const gate = await SpeakerGate.create({
  vadModel: 'app/models/silero_vad/silero_vad_v4.onnx',
  speakerModel: 'app/models/sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k/model.onnx',
  threshold: 0.5,                      // 放行阈值基准（默认同 app 后端）
  enrollTargetSpeechSeconds: 1.2,      // 注册净语音目标：够了自动完成（≈四个字）
  shortEnrollThresholdFactor: 0.7,     // 短注册阈值补偿（<1.5s 生效，设 1 禁用）
})

// 流式注册：边说边喂，净语音达标（enough=true）后停麦收尾
gate.beginEnroll()
for (const chunk of micChunks) {
  const p = await gate.enrollChunk(chunk)   // {speechSeconds, enough}
  if (p.enough) break
}
await gate.finishEnroll()               // 净语音 <0.6s 抛错

for (const seg of await gate.accept(chunk)) {   // 监听：流式喂 100ms 块
  // seg.similarity / seg.accepted / seg.samples / seg.start / seg.durationSeconds
}
```

批式 `await gate.enroll(samples)` = 上面三步的便捷入口（内部剥静音）。

### 短注册（四~六个字）实测

| 注册净语音 | 生效阈值 | 目标段相似度 | 陌生人 | 判定 |
|---|---|---|---|---|
| 3.72s | 0.50 | 0.586~0.711 | -0.072 | 全对 |
| 2.24s | 0.50 | 0.523~0.903 | -0.038 | 全对 |
| 1.03s（四字级） | 0.35 | 0.480~0.791 | -0.166 | 全对 |
| 0.64s | 0.35 | 0.383~0.683 | -0.216 | 全对（余量仅 0.03，悬崖边） |

规律：注册越短目标相似度整体下移、陌生人始终贴 0。**≥1s 净语音可用**；0.6s 是
悬崖。以上为一对说话人的样例数据，接入真实业务前建议用现场录音复测（尤其同性/
相似音色的陌生人）。出现误拒/误收时优先调 `shortEnrollThresholdFactor`。

判定语义与 `app/backend/audio_tse/speaker_gate.py` 一致（同模型、同 VAD 参数）；
区别是 v0 按段整段判定，app 另有段内 0.6s 预热增量判定与前缀补发（后续版本再补）。

参考特征/声纹由 `test/tools/*.py` 用 app 的 conda 环境生成（重跑见文件头注释）；
`test/tools/` 下另保留对拍调试期的诊断/消融脚本。

## 结构

```
src/core/   embedder.ts / voice-filter.ts（无 VAD，内网入口）
src/full/   vad.ts / speaker-gate.ts（组合 core 的 VoiceFilter + VAD 切段 + 注册剥静音）
src/index.ts 包主入口 = full；'@audiotse/gate/core' 子路径入口 = src/core
test/core/  core 端到端（自带极简 wav 读取，不依赖 sherpa）
test/full/  对拍验证 + 完整版端到端 + Python 参考生成工具
examples/   intranet-wake-flow.js（唤醒词注册+提问过滤可跑演示）、electron-main.example.js（Electron 接入示例）
```
