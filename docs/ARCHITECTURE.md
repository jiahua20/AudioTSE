# AudioTSE 架构与原理

本文按「数据流」自顶向下讲清整套系统：声音怎么从麦克风进来、怎么被分离、怎么变成字幕。每个关键点都标注了源码位置（`文件:行号`），方便对照阅读。

> 阅读顺序建议：先看「端到端数据流」建立全局画面，再按需深入「核心原理」各节。

---

## 1. 这是什么

一个 **CPU 优先的实时目标说话人提取（Target Speaker Extraction, TSE）桌面原型**。给定一段「目标人的注册语音」，系统从**包含多个人的混合音频**里把目标人的声音分离出来，再做中文流式 ASR 转写成字幕。

- **前端**：Electron + Vue 3，负责麦克风采集、字幕界面、分离音频回放。
- **后端**：Python WebSocket 服务，编排「分离 / 门控 / ASR」三条处理链路。
- **通信**：单一 WebSocket（`ws://127.0.0.1:8765`），音频走二进制帧、控制走 JSON。

## 2. 组件与技术栈

| 层 | 技术 | 角色 |
|---|---|---|
| 桌面壳 | Electron (`electron/main.cjs`) | 加载 Vite 开发服务器、授予麦克风权限 |
| 界面 | Vue 3 + TypeScript + Vite (`src/`) | 采集、WS 客户端、字幕、播放 |
| 后端 | Python + `websockets` (`backend/audio_tse/`) | 会话状态机、链路编排、模型推理 |
| 分离 | WeSep BSRNN + ECAPA（PyTorch, CPU） | 纯音频 TSE（实验主路径） |
| 门控 | Silero VAD + 3D-Speaker ER2Net（Sherpa-ONNX） | 声纹门控（降级路径） |
| 识别 | Sherpa-ONNX 流式 Zipformer / Paraformer | 中文（中英）流式 ASR |
| 音频格式 | 16 kHz · 单声道 · PCM16 | 全链路统一 |

## 3. 目录结构（清理后）

```text
backend/audio_tse/
  server.py          WebSocket 协议与会话编排（链路的“指挥”）
  session.py         会话状态机（idle→enrolling→ready→extracting）
  tse.py             WeSep BSRNN 的流式封装 + 重叠相加（OLA）
  asr.py             Sherpa-ONNX 流式 ASR 封装
  speaker_gate.py    VAD + 声纹相似度门控（降级路径）
  wesep_loader.py    让 `import wesep` 在精简安装下可用的运行时垫片
  _wesep_utils/      供 wesep_loader 注入的 wesep.utils 本地副本
backend/tests/       模型可用性与会话状态测试
electron/            主进程与 preload
scripts/             下载/安装脚本与测试音频生成
src/                 Vue 界面（App.vue 是主体）
models/              运行时模型权重（gitignore，脚本下载）
samples/             测试用输入 wav（注册 + 混合）
```

## 4. 端到端数据流

### 4.1 全景

```text
  ┌──────────── Electron (electron/main.cjs) ────────────┐
  │  BrowserWindow ──loadURL──▶ Vite dev (localhost:5173) │
  └────────────────────────┬─────────────────────────────┘
                           │ 渲染进程 = Vue 应用 (src/App.vue)
  ┌────────────────────────▼─────────────────────────────┐
  │  getUserMedia(16k,mono,AEC,NS) → ScriptProcessor(2048)│
  │   每 128 ms 一帧 Float32 → floatToPcm16 → WS.send(二进制) │
  │                                       App.vue:229,104  │
  └────────────────────────┬─────────────────────────────┘
                           │ WebSocket  ws://127.0.0.1:8765
  ┌────────────────────────▼─────────────────────────────┐
  │  server.handle()  (server.py:110)                     │
  │   二进制帧 → session.accept_pcm16                      │
  │   状态=EXTRACTING 时按当前链路处理：                     │
  │     • tse:        分离→回放帧+ASR→字幕                  │
  │     • speaker_gate: VAD分段→声纹门限→ASR→字幕            │
  │     • passthrough: 直接 ASR→字幕                        │
  └──────┬───────────────────────┬────────────────────────┘
   二进制(分离音频)            JSON(transcript/metrics/...)
         │                            │
         ▼                            ▼
  enqueueSeparatedAudio         feedPartial → 打字机字幕
  (App.vue:286)                 (App.vue:320)
```

### 4.2 两阶段：先注册，再提取

系统是**两阶段**的：第一阶段采集目标人的「参考语音」（enrollment），第二阶段才用它从混合音频里提取目标人。

**阶段一：注册**（`session.py:31`）

1. 用户点「开始注册」→ 前端发 `{command:"startEnrollment"}`，状态进入 `ENROLLING`。
2. 麦克风帧照常以二进制发上来，后端 `session.accept_pcm16` 把它**累加进 `enrollment` 缓冲**（`session.py:40`）——此阶段不做任何识别。
3. 用户点「完成注册」→ `{command:"finishEnrollment"}`；若不足 3 秒拒绝（`session.py:48`），否则进入 `READY`。
4. 注册语音整段保存在会话内存里（`session.enrollment`），供下一步条件分离/门控使用。

> 测试模式（`sourceMode==='file'`）用本地 wav 代替麦克风，按真实速率（每 128 ms 一帧）灌进**同一条** WS 路径（`App.vue:358`），所以流式行为与麦克风完全一致。

**阶段二：提取**（`server.py:199`）

收到 `{command:"startExtraction"}` 后，按所选处理模式实例化链路：

- **tse**：`BufferedWeSepTse(TSE_MODEL, enrollment)`（`server.py:211`）——把注册语音算成说话人嵌入缓存起来。
- **speaker_gate**：`SpeakerGate(asr, vad, speaker)` 并 `gate.enroll_pcm16(enrollment)`（`server.py:207`）。
- 之后状态进入 `EXTRACTING`，每一帧音频走 4.3 的分支。

### 4.3 每帧音频的处理分支（`server.py:142`）

每收到一个 128 ms 的二进制帧：

```python
session.accept_pcm16(message)            # 任何状态都接收
if state == EXTRACTING:
    if tse and asr:                       # 主路径：分离
        for target in tse.accept_pcm16(message):   # 可能 0 或多个输出窗
            ws.send(target)                # ① 分离出的目标音频 → 前端回放
            text, final = asr.accept_pcm16(target)  # ② 对“干净”的目标音频做 ASR
            ws.send(transcript, text, final)        # ③ 字幕
    elif gate:                            # 降级路径：门控
        for tr in gate.accept_pcm16(message): ws.send(transcript, **tr)
    elif asr:                             # 诊断路径：原音直通
        text, final = asr.accept_pcm16(message); ws.send(transcript,...)
    # 每 0.4 s 推一次 metrics（RTF / 缓冲积压 / 首字延时）
```

**关键设计**：TSE 路径下，**ASR 拿到的是分离后的目标音频**，而不是混合音频。这把「鸡尾酒会问题」从 ASR 身上卸了下来——ASR 只需识别干净的单人语音。

## 5. WebSocket 协议

| 方向 | 类型 | 内容 |
|---|---|---|
| C→S | 二进制 | 16k mono PCM16 音频帧（128 ms / 4096 字节） |
| C→S | JSON | `command`: `startEnrollment` / `finishEnrollment` / `setModels{asrModel,processor}` / `startExtraction` / `stopExtraction` |
| S→C | `hello` | `asrReady, tseReady, processors[], asrModels[], selectedProcessor, message`（连上即发，`server.py:123`） |
| S→C | `state` | `state, enrollmentSeconds`（每次命令后回送，`server.py:234`） |
| S→C | `transcript` | `text, final, similarity?`（`server.py:151`） |
| S→C | `metrics` | RTF、单窗耗时、缓冲积压、首字延时等（`server.py:90`，每 0.4 s） |
| S→C | `modelsChanged` | 切换链路后回送（`server.py:192`） |
| S→C | `error` | `message` + 当前状态（`server.py:237`） |
| S→C | 二进制 | **仅 TSE 路径**：分离出的目标 PCM16，供前端回放（`server.py:149`） |

前端在 `onmessage` 里按 `data instanceof Blob` 区分二进制与 JSON（`App.vue:139`）：二进制进回放队列，JSON 走事件分支。断线每 2 s 自动重连（`App.vue:137`）。

## 6. 会话状态机（`session.py:11`）

```text
idle ──startEnrollment──▶ enrolling ──finishEnrollment(≥3s)──▶ ready
                               ▲                                  │
                               │                          startExtraction
                               │                                  ▼
                               └──────── stopExtraction ──── extracting
```

注册与提取互斥（提取中不能注册，反之亦然）；切换模型要求先停下当前操作（`server.py:178`）。

---

## 7. 核心原理

> BSRNN 单窗分离与重叠相加（OLA）的**逐步图解 + 公式**见 [docs/TSE-DEEP-DIVE.md](TSE-DEEP-DIVE.md)。

### 7.1 TSE 是什么，和“分离/门控”有何不同

- **盲源分离（BSS）**：不知道有几个人、是谁，把混合信号拆成若干路。难，且拆出来不知道哪路是目标。
- **声纹门控**：不分离波形，只判断“这段语音像不像目标人”，像就转写、不像就丢。无法处理**重叠**语音（两人在同一时刻说话时，声纹是混合的）。
- **目标说话人提取（TSE）**：用一段**参考语音（注册）**作为条件，从混合波形里**直接估出目标人的波形**。既有“是谁”的先验，又真正改写了波形。

本工程的 TSE = WeSep BSRNN（频域掩蔽网络）+ ECAPA（用注册语音算说话人嵌入作为条件）。

### 7.2 注册嵌入：算一次，复用到每一窗（`tse.py:97`）

注册语音只在 `startExtraction` 时被处理一次：

```python
feats = compute_fbank(enroll)            # 梅尔滤波器组特征 + CMN
emb   = model.spk_model(feats)           # ECAPA 编码器 → 说话人向量
spk   = model.spk_transform(emb)         # 投影到 BSRNN 期望的维度/形状
```

这个 `spk_embedding` 被缓存（`tse.py:80`）。之后每个窗口分离时，`_forward_cached`（`tse.py:110`）把它**直接喂给 BSRNN 的 separator**，跳过原本每窗都要跑一遍的 ECAPA——既省算力，又保证条件一致。

> `_forward_cached` 实质是手动重写了 `wesep.models.bsrnn.BSRNN.forward`：STFT → 分子带 → `separator(subband_feature, spk_embedding)` → 复数比例掩蔽（real/imag 两路）→ iSTFT。读这一段就能看懂 BSRNN 的标准前向。

### 7.3 重叠相加（OLA）：为什么 2 秒的窗能无缝拼起来

BSRNN 是**非因果、整窗**模型：必须凑满一个窗口才能推理一次。这里窗口 = **2 秒**（`tse.py:13` `WINDOW_SECONDS=2`）。直接把相邻两窗的输出首尾相接，会在拼接处产生可听的断裂（跨在边界上的那个字会被丢/畸变）——因为**每个窗是独立估计掩蔽的**，两窗的掩蔽在边界不一致。

解决办法是**重叠相加 + 合成窗**（`tse.py:82`）：

```python
CROSSFADE_SECONDS = 0.1           # 相邻窗重叠 0.1 s
hop = window - crossfade = 1.9 s  # 每次前进 1.9 s（非 50% 重叠）

ramp = linspace(0, 1, cf_samples)       # 0→1 的线性斜坡
win[:cf]   = ramp        # 窗头：0→1 渐入
win[-cf:]  = 1 - ramp    # 窗尾：1→0 渐出
```

相邻两窗在 0.1 s 的重叠区里：上一窗的尾巴（`1-ramp`）与下一窗的头部（`ramp`）相加：

```
(1 − ramp) + ramp = 1     ←  重叠区恒等于 1（COLA：Constant Overlap-Add）
```

而非重叠的中段（1.8 s）窗值恒为 1，输出即分离结果本身。于是整体重构出**无失真**的目标波形，只在接缝处把两个不同掩蔽**平滑交叉淡化**过去。这就是注释里“overlap-add reconstructs unity”（`tse.py:85`）的含义。

**为什么 hop = window − crossfade，而不是常见的 50% 重叠？** 50% 重叠会把每个采样处理两遍，RTF 直接翻倍。这里 hop=1.9 s，每个窗的算力摊到 1.9 s 上，`RTF ≈ window/hop ≈ 1.05`，开销几乎可忽略，却仍拿到无缝拼接（`tse.py:14` 注释）。

收尾 `flush`（`tse.py:202`）把不足一窗的尾部零填到整窗再分离，只取真实音频占据的输出段，避免末尾的字被丢或被静音填充。

### 7.4 延迟模型与 RTF：为什么是“约 3 秒缓冲”

TSE 的算法延迟下限 = **必须先攒满一个 2 秒窗口**才能出第一段输出。加上 128 ms 帧的累积与推理耗时，端到端首字延时约在 2 秒以上——UI 里说的「约 3 秒缓冲」即指这个（`tse.py`/`server.py` 注释）。因此它是**可验证效果的 CPU 实验模式，不是低延迟真流式**。

实时性用 **RTF（Real-Time Factor）= 处理耗时 / 音频时长**衡量（`server.py:82`）：

- TSE：`(单窗 tse 耗时 + asr 耗时) / hop(1.9s)`，**< 1 表示跟得上实时的 1.9 s 节奏**。
- 门控/直通：`单帧耗时 / 128ms`。

界面「缓冲积压」> 3.2 s 会标红（`App.vue:665`），提示 CPU 跟不上了。

### 7.5 声纹门控：为什么只是降级（`speaker_gate.py`）

链路很简单：

1. **Silero VAD** 把连续音频切成一句句的语音段（`speaker_gate.py:40`）。
2. 每段用 **3D-Speaker ER2Net** 算说话人嵌入，与注册嵌入做**余弦相似度**（`speaker_gate.py:115`）。
3. 相似度 ≥ 0.5（`speaker_gate.py:52`）才把这段送进 ASR 转写，否则丢弃。

它**不改写波形**，只决定“这段要不要转写”。所以：

- 适合**轮流发言**（同一时刻只有一人说话，声纹干净）。
- **无法处理重叠语音**：两人同时说话时，一段语音里混着两个声纹，相似度失真，既可能误收他人、也可能漏收目标。

因此当 TSE 不可用时它作为降级，能力边界明确（README 也强调）。

### 7.6 流式 ASR（`asr.py`）

Sherpa-ONNX 的 `OnlineRecognizer`，两种模型：

- **Zipformer transducer**（中文 14M INT8）：开 `enable_endpoint_detection`，靠端点检测切句（`asr.py:64`）。
- **Paraformer**（中英双语 INT8）：流式版需 **0.66 秒尾部静音 padding** 才能把 trailing 上下文冲出来（`asr.py:53`，`finish` 里补零）。

每帧 `accept_pcm16`（`asr.py:95`）：`accept_waveform` 喂入 → `decode_stream` 直到 `is_ready` 为假 → 取增量文本 + `is_endpoint` 标志；端点到了就 `reset` 开新句。线程数固定 2（`asr.py:13` 注释：实测多线程反而更慢）。

---

## 8. 工程细节备忘（踩过的坑）

- **`wesep_loader.py`**：wesep 的 wheel 缺 `wesep.utils` 包，且 `--no-deps` 安装会让若干可选子模块（speaker CLI、s3prl/whisper/w2vbert 前端等）的 `import` 失败。本模块在 `import wesep` **之前**把本地 `_wesep_utils` 注册成 `wesep.utils`、把那些可选子模块预置为惰性桩（`wesep_loader.py:106`），全程不改 site-packages。
- **新装依赖要重启后端**：`TseModel.dependency_ready` 用 `importlib.find_spec`，其目录查找缓存在进程启动时建立；运行中 `pip install` 新包后，旧后端进程未必能发现——装完 WeSep 需重启 `start.ps1`（`tse.py:33`）。
- **环境隔离**：后端跑在 `AudioTSE` conda 环境里；用工具脚本操作依赖时务必指向 `…\envs\AudioTSE\python.exe`，别误装进 base。
- **模型下载**：`scripts/download-models.py` 已带断点续传 + 完整性校验 + 重试，应对国内直连 GitHub 超时；WeSep 模型走 ModelScope（国内 CDN）。

## 9. 源码速查

| 想看 | 去这里 |
|---|---|
| 协议与会话编排 | `server.py:110`（handle） |
| 每帧三分支处理 | `server.py:142` |
| RTF/指标计算 | `server.py:82`、`server.py:90` |
| 会话状态机 | `session.py:11` |
| TSE 模型可用性判定 | `tse.py:37` |
| 注册嵌入（ECAPA） | `tse.py:97` |
| BSRNN 前向（掩蔽） | `tse.py:110` |
| OLA 合成窗与拼接 | `tse.py:82`、`tse.py:175` |
| ASR 流式封装 | `asr.py:37`、`asr.py:95` |
| 声纹门控 | `speaker_gate.py:36`、`speaker_gate.py:115` |
| 前端 WS 客户端 | `App.vue:129` |
| 前端音频采集 | `App.vue:229` |
| 分离音频回放 | `App.vue:286` |
| Electron 壳 | `electron/main.cjs:4` |

## 10. 运行与扩展

- **启动**：`.\start.ps1`（首次自动装依赖、下轻量模型、起后端 + 桌面端）；仅准备不启动用 `-SetupOnly`。
- **启用实验 TSE**：`.\scripts\install-wesep-tse.ps1`（装 CPU PyTorch / WeSep、下 ~262 MB 权重），再重启。
- **换模型**：界面“处理模式 / 识别模型”分段按钮即时切换（`App.vue:536`）；后端 `setModels` 校验可用性（`server.py:177`）。
- **生产接入思路**（见 README「TSE 生产接入」）：当前是 3 秒整窗实验；下一步应测中文重叠语音的 SI-SDR/可懂度与 CPU RTF，再决定改造成有状态分块推理，或训练小型因果 SpEx+/SpeakerBeam。端侧可保持同一协议：TSE 走 ONNX Runtime/LibTorch，ASR 走 Sherpa-ONNX C++，Electron 不变。
