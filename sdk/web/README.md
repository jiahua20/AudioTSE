# @audiotse/gate — 声纹门控 Web SDK（TS）

silero VAD 切段 + 3D-Speaker ER2Net 声纹相似度门控，Node/Electron 主进程可用，
与 `app/` 的 Python 后端同模型、同参数、**声纹已对拍验证逐位一致**（cos ≥ 0.9999，
见下方「验证」）。

## 分工

| 环节 | 实现 | 说明 |
|---|---|---|
| VAD 切段 | `sherpa-onnx` npm（WASM） | 官方 JS 包未暴露独立声纹提取器，但 VAD 有 |
| 声纹提取 | `onnxruntime-node` 直接跑 ER2Net ONNX | 前端 fbank 为本包自实现（见下） |

自实现 fbank 的代价换来两点能力：不依赖 sherpa 的原生构建；后续可换
`onnxruntime-web` 进纯浏览器。前端参数已按 sherpa-onnx 源码（`csrc/features.h`）
固定：25/10ms、povey 窗、dither=0、**high_freq=-400（7600Hz 上限）**、
**snip_edges=false（居中+反射填充）**、80 mel、mel 域三角滤波不归一、
fbank 后整段减均值（模型元数据 `feature_normalize_type=global-mean`）。

## 使用

```ts
import { SpeakerGate } from '@audiotse/gate'

const gate = await SpeakerGate.create({
  vadModel: 'app/models/silero_vad/silero_vad.onnx',
  speakerModel: 'app/models/sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k/model.onnx',
  threshold: 0.5,                      // 放行阈值基准（默认同 app 后端）
  enrollTargetSpeechSeconds: 1.2,      // 注册净语音目标：够了自动完成（≈四个字）
  shortEnrollThresholdFactor: 0.7,     // 短注册阈值补偿（<1.5s 净语音时生效，设 1 禁用）
})

// 流式注册：边说边喂，净语音达标（enough=true）后停麦收尾
gate.beginEnroll()
for (const chunk of micChunks) {
  const p = await gate.enrollChunk(chunk)   // {speechSeconds, enough}
  if (p.enough) break
}
await gate.finishEnroll()               // 净语音 <0.6s 抛错；返回 {speechSeconds}

for (const seg of await gate.accept(chunk)) {   // 监听：流式喂 100ms 块
  // seg.similarity / seg.accepted / seg.samples / seg.start / seg.durationSeconds
}
// 流结束时：for (const seg of await gate.flush()) { ... }
```

批式便捷入口 `await gate.enroll(samples)` 等价于上面三步（内部剥静音，静音不参与
特征统计）。

### 短注册（四~六个字）实测

注册内部用独立 VAD 剥静音、只累计净语音；净语音 <1.5s 时实际阈值自动降为
`threshold × shortEnrollThresholdFactor`（实测数据，样例见 `npm test`）：

| 注册净语音 | 生效阈值 | 目标段相似度 | 陌生人 | 判定 |
|---|---|---|---|---|
| 3.72s | 0.50 | 0.586~0.711 | -0.072 | 全对 |
| 2.24s | 0.50 | 0.523~0.903 | -0.038 | 全对 |
| 1.03s（四字级） | 0.35 | 0.480~0.791 | -0.166 | 全对 |
| 0.64s | 0.35 | 0.383~0.683 | -0.216 | 全对（余量仅 0.03，悬崖边） |

规律：注册越短目标相似度整体下移、陌生人始终贴 0。**≥1s 净语音可用**；0.6s 是
悬崖。以上为一对说话人的样例数据，接入真实业务前建议用真实现场录音复测（尤其
同性/相似音色的陌生人）。若出现误拒/误收，优先调 `shortEnrollThresholdFactor`
或引导用户多说两个字。

判定语义与 `app/backend/audio_tse/speaker_gate.py` 一致（同模型、同 VAD 参数）；
区别是 v0 按段整段判定，app 另有段内 0.6s 预热增量判定与前缀补发（ASR 联动的
实时性优化，后续版本再补）。

## 验证（对拍 Python 引擎）

```powershell
cd sdk/web
npm run validate   # 两层对拍：fbank vs torchaudio（max|diff|<1e-3）+ 声纹 vs sherpa Python（cos≥0.999）
npm test           # 端到端：注册后轮流喂目标/陌生人/目标 → 拒绝陌生人；RTF ~0.08
```

参考文件由 `test/tools/*.py` 用 app 的 conda 环境生成（需重跑时见文件头注释）。
`test/tools/` 下另有对拍调试期的诊断/消融脚本，保留作排查参考。

## 结构

```
src/fbank.ts         kaldi 风格 fbank（参数对齐 sherpa-onnx 前端）
src/vad.ts           silero VAD 封装（sherpa-onnx WASM，段式 API；内部统一按 160ms
                     切块喂入——sherpa WASM 对大块单次喂入会丢段边界，封装后行为与
                     调用方块大小无关）
src/embedder.ts      onnxruntime-node 声纹提取（global-mean 归一）
src/speaker-gate.ts  门控主类（VAD→声纹→余弦→放行/拒绝）+ 短注册（剥静音/自动完成/阈值补偿）
test/                对拍验证 + 端到端测试 + Python 参考生成工具
```
