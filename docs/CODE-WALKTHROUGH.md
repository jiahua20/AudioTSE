# AudioTSE 源码导读（Code Walkthrough）

本文按「逐文件、逐函数」的方式带读整个工程的源码：每个文件在做什么、关键代码为什么这么写、设计要点在哪。每处都标注了源码位置（`文件:行号`），方便对照阅读。

> **与另外两份文档的关系**
> - [ARCHITECTURE.md](ARCHITECTURE.md)：按数据流自顶向下讲**架构与原理**（声音怎么进来、怎么分离、怎么变字幕）。
> - [TSE-DEEP-DIVE.md](TSE-DEEP-DIVE.md)：BSRNN 单窗分离与重叠相加（OLA）的**逐步图解 + 公式推导**。
> - 本文（CODE-WALKTHROUGH.md）：**逐文件的源码导读**，回答「这段代码为什么这么写」。
>
> 建议先看 ARCHITECTURE.md 建立全局画面，再用本文对照源码深入。

---

## 阅读路线图

本文按数据流，分 5 章、由外到内逐层剥开：

| 章 | 模块 | 文件 | 搞懂什么 |
|---|---|---|---|
| 1 | 外壳与启动 | `electron/main.cjs`、`preload.cjs`、`src/main.ts` | 桌面壳如何加载 Vue、麦克风权限怎么来 |
| 2 | 后端中枢 + 状态机 | `server.py`、`session.py` | WS 协议、三分支编排、状态机为何禁止「边提取边注册」 |
| 3 | 三条处理链路 | `tse.py`、`asr.py`、`speaker_gate.py` | BSRNN 整窗+OLA、流式 ASR、声纹门控降级 |
| 4 | 工程垫片 | `wesep_loader.py`、`_wesep_utils/` | 让「缺胳膊少腿」的 wesep wheel 不改 site-packages 也能跑 |
| 5 | 前端主体 | `src/App.vue` | WS 客户端、采集、文件灌入、回放、打字机字幕 |

第 6 章给出全工程闭环的全景图；附录 A 单独拆解「为什么开始提取后会有 ~3 秒延迟」。

---

## 第 1 章 外壳与启动

三个文件加起来才 21 行，但决定了「程序怎么起来、麦克风权限怎么来」。

### 1.1 `electron/main.cjs` — Electron 主进程

```js
session.defaultSession.setPermissionRequestHandler(
  (_webContents, permission, callback) => callback(permission === 'media')
)
```

**这是整个项目用 Electron 而非纯网页的关键一行。** 它给所有权限请求装了个总开关：只有 `media`（麦克风/摄像头）请求会被**自动批准**，其它一律拒绝。

为什么不能直接用浏览器跑？

- 浏览器的 `getUserMedia` 要求 **HTTPS 或 `localhost`** 才给麦克风；Electron 把本地加载的页面视同 localhost，放行了。
- 浏览器会**弹权限对话框**；这里静默放行，体验顺。

```js
const window = new BrowserWindow({
  width: 1180, height: 780, minWidth: 920, minHeight: 640,
  backgroundColor: '#f2f0ea',
  titleBarStyle: 'hiddenInset',
  webPreferences: {
    preload: path.join(__dirname, 'preload.cjs'),
    contextIsolation: true,
    nodeIntegration: false,
  },
})
window.loadURL(process.env.VITE_DEV_SERVER_URL || 'http://localhost:5173')
```

- 窗口尺寸 + `backgroundColor: '#f2f0ea'`（暖灰底色，与 `App.vue` 的 CSS 对齐，避免白屏闪烁）。
- `titleBarStyle: 'hiddenInset'`：隐藏式标题栏（本是 macOS 风格，在 Windows 上呈现为隐藏标题栏文字）。
- **`webPreferences` 是标准安全配置**：`contextIsolation: true`（主世界与隔离世界分开）+ `nodeIntegration: false`（渲染进程拿不到 Node API）。代价是 Vue 代码里**不能直接 `require('fs')` 之类**，必须靠 preload 显式开洞。
- `loadURL(...)`：连 Vite dev server（默认 5173 端口）。注意 fallback 仍是 `localhost:5173`——说明**当前这个壳面向开发态**（没有 `loadFile` 加载打包产物的生产分支），`start.ps1` 就是「起 Vite + 起 Electron」。

```js
app.whenReady().then(createWindow)
app.on('window-all-closed', () => app.quit())
```

Electron 就绪 → 建窗口；所有窗口关闭 → 退出 App。

### 1.2 `electron/preload.cjs` — 预加载桥（只有 2 行）

```js
const { contextBridge } = require('electron')
contextBridge.exposeInMainWorld('audioTSEDesktop', { platform: process.platform })
```

因为主进程把 Node 能力全关了，渲染进程要碰主进程只能靠 `contextBridge` 显式暴露。这里只开了一个**最小的洞**：把 `process.platform`（Windows 上是 `'win32'`）挂到全局 `window.audioTSEDesktop.platform`。

> `App.vue` 里可直接读 `window.audioTSEDesktop?.platform` 来判断「是否跑在 Electron 桌面壳里 / 在什么平台」。这是渲染进程能拿到的**唯一**来自主世界的信息。

### 1.3 `src/main.ts` — Vue 入口

```js
import { createApp } from 'vue'
import './index.css'
import App from './App.vue'
createApp(App).mount('#root')
```

最朴素的 Vite + Vue 3 入口：挂载到 `#root`。整个前端的肉全在 `App.vue`（第 5 章）。

### 1.4 启动链总览

```
start.ps1
  ├─ 起 Python 后端  ws://127.0.0.1:8765   (server.py)
  └─ 起 Vite dev      http://localhost:5173
        └─ electron main.cjs  loadURL → 5173
              └─ main.ts 挂载 App.vue
                    └─ App.vue 一启动就连 WS → 后端
```

到这里，前端窗口和后端服务都活着了，但还没收任何声音。

---

## 第 2 章 后端中枢与状态机

先讲被依赖的 `session.py`（状态机），再讲编排中枢 `server.py`。

### 2.1 会话状态机 `session.py`

61 行、零依赖，是后端的「规则书」。它管两件事：**当前处于哪个阶段**（状态），以及**注册语音存在哪**（enrollment 缓冲）。

#### 四个状态 + 一个缓冲

```python
class SessionState(str, Enum):           # session.py:11
    IDLE = "idle"
    ENROLLING = "enrolling"
    READY = "ready"
    EXTRACTING = "extracting"

@dataclass
class AudioSession:
    state: SessionState = SessionState.IDLE
    enrollment: bytearray = field(default_factory=bytearray)
```

`enrollment` 是个 `bytearray`——注册阶段的目标人语音，以**原始 PCM16 字节**存在内存里（注意是字节，不是浮点样本）。

```python
@property
def enrollment_seconds(self) -> float:    # session.py:27
    return len(self.enrollment) / (SAMPLE_RATE * 2)
```

**这个除法值得看清**：PCM16 = 每采样 **2 字节**，采样率 16000/秒，所以 1 秒音频 = 16000 × 2 = **32000 字节**。`len / 32000` 就是秒数。「至少 3 秒」= 至少 96000 字节。全链路任何地方看到「秒↔字节」换算，都是这个系数。

#### 状态转换

```
        startEnrollment            finishEnrollment(≥3s)
  IDLE ─────────────────▶ ENROLLING ──────────────────▶ READY
   ▲                          │                            │
   │  (<3s 时回退)             │                     startExtraction
   │                          │                            ▼
   └────────────────────  stopExtraction ◀──────────  EXTRACTING
                            (回 READY，不是 IDLE！)
```

每个动作里的「护栏」：

```python
def start_enrollment(self) -> None:       # session.py:31
    if self.state == SessionState.EXTRACTING:
        raise SessionError("请先停止实时提取")
    self.enrollment.clear()               # ← 重新注册会清空旧的
    self.state = SessionState.ENROLLING
```

**互斥保护**：提取中不能注册。且 `clear()` 是关键设计——重新注册会丢弃上一份 enrollment，不会串味。

```python
def accept_pcm16(self, chunk: bytes) -> None:    # session.py:37
    if len(chunk) % 2:
        raise SessionError("PCM16 数据长度必须是偶数")
    if self.state == SessionState.ENROLLING:
        self.enrollment.extend(chunk)            # ← 注册阶段：累加进缓冲
    elif self.state != SessionState.EXTRACTING:
        raise SessionError("当前状态不接收音频")
```

**这是状态机最微妙的地方，也是理解延迟问题的钥匙：**

- `ENROLLING`：音频 `extend` 进 `enrollment` 缓冲——**纯累积，不做任何识别**。这就是为什么注册阶段感觉「瞬间完成」：本质只是往 bytearray 里 append 字节。
- `EXTRACTING`：**落到这里什么都不做就 return**。实时提取时，音频的真正处理发生在 `server.py` 的 `handle` 里，`session` 只负责「放行不报错」。提取阶段的音频**不进 enrollment，只在内存里被即时消费掉**。
- `IDLE` / `READY`：报错拒绝。READY 是「注册完了但还没开始提取」的待命态，这时也不该收音频。

```python
def finish_enrollment(self) -> None:      # session.py:45
    if self.state != SessionState.ENROLLING:
        raise SessionError("当前没有正在进行的声纹注册")
    if self.enrollment_seconds < MIN_ENROLLMENT_SECONDS:   # < 3.0
        self.state = SessionState.IDLE                     # ← 失败：回 IDLE
        raise SessionError("注册语音至少需要 3 秒")
    self.state = SessionState.READY
```

不足 3 秒时**回退到 `IDLE`**（不是停在 ENROLLING），所以失败后必须重新 `start_enrollment`——故意把不完整的注册「作废」。

```python
def stop_extraction(self) -> None:        # session.py:58
    if self.state != SessionState.EXTRACTING:
        raise SessionError("实时提取尚未启动")
    self.state = SessionState.READY       # ← 回 READY，保留 enrollment!
```

**第二个设计点**：停止提取后回到 `READY` 而非 `IDLE`，意味着 **enrollment 还在**——可以立刻再点「开始提取」复用同一份注册语音反复测试，不用重录。直到主动重新「开始注册」（会 `clear()`）才会换人。

#### 职责边界

| 职责 | 归谁 |
|---|---|
| 状态机 + 合法性护栏 | `session.py` |
| 注册语音的累积与存储 | `session.py`（enrollment 缓冲） |
| **实时提取时音频的真正处理** | `server.py`（2.2 节） |

状态机本身**不碰任何模型、不做推理**——它只是个「交通灯」。

### 2.2 后端中枢 `server.py`

258 行，按「模块级辅助 → 指标 → 主循环」三段拆。

#### ① 模块级：模型注册表（`server.py:15-41`）

```python
ROOT = Path(__file__).resolve().parents[2]     # 工程根目录
MODELS_DIR = ROOT / "models"
ASR_MODELS = {
    "zipformer":  AsrModel("zipformer",  "Zipformer 14M 中文",   MODELS_DIR / "sherpa-onnx-...", "transducer"),
    "paraformer": AsrModel("paraformer", "Paraformer 中英双语", MODELS_DIR / "sherpa-onnx-...", "paraformer"),
}
VAD_MODEL     = MODELS_DIR / "silero_vad" / "silero_vad.onnx"
SPEAKER_MODEL = MODELS_DIR / "sherpa-onnx-3dspeaker-..." / "model.onnx"
TSE_MODEL = TseModel("wesep_bsrnn", "WeSep BSRNN ...", MODELS_DIR / "wesep-bsrnn-ecapa-vox1")
```

这是「能力清单」：两个 ASR 模型 + 三个处理器要用的权重。注意它们都只是**路径 + 元信息**——`available` 属性会去磁盘查文件在不在（`tse.py` 里还查 torch/wesep 是否装了）。连上服务时，缺哪些权重前端立刻知道。

#### ② `send` / `model_options`：协议辅助（`server.py:44-75`）

```python
async def send(websocket, event, **payload):
    await websocket.send(json.dumps({"event": event, **payload}, ensure_ascii=False))
```

所有发往前端的 JSON 都是 `{"event": "xxx", ...}` 这个壳。`ensure_ascii=False` 让中文直接发。

`model_options()` 把能力清单打包成两个数组（`asrModels` + `processors`）回给前端，每个处理器带 `description` / `available` / `reason`——前端据此渲染「哪些按钮能点、灰掉的写明原因」。这就是为什么没装 WeSep 时界面会显示「运行 install-wesep-tse.ps1」。

#### ③ 指标三件套（`server.py:78-107`）—— UI 上那些数字怎么来

```python
FRAME_MS = (2048 / 16_000) * 1000.0          # 一个 WS 帧 = 128 ms 音频
HOP_MS   = (WINDOW_SECONDS - CROSSFADE_SECONDS) * 1000.0   # 一个 TSE hop = 1900 ms
```

两个时间基准：

- `FRAME_MS = 128`：麦克风/文件每帧 2048 采样 = 128ms（第 5 章讲采集时会再碰到）。
- `HOP_MS = 1900`：TSE 每出一个窗前进 1.9 秒（= 2 秒窗 − 0.1 秒重叠，第 3 章讲 OLA 时细讲）。

```python
def rtf_for(processor, tse_ms, asr_ms, gate_ms):    # server.py:82
    if processor == "tse":
        return (tse_ms + asr_ms) / HOP_MS           # 处理耗时 / 1.9s 音频
    cost = gate_ms if processor == "speaker_gate" else asr_ms
    return cost / FRAME_MS                          # 处理耗时 / 128ms 音频
```

**RTF（实时因子）= 处理耗时 ÷ 音频时长。< 1 表示跟得上实时。** 分母不同：TSE 按「窗」节奏（1.9s），门控/直通按「帧」节奏（128ms）。

`metrics_payload` 把 RTF、墙钟、已处理音频时长、单窗耗时、**缓冲积压 backlogSec**、**首字延时 e2eFirstMs** 打包，每 0.4 秒推一次。两个对调试最有用：

- `backlogSec = tse.buffered_seconds`：还没攒满窗的音频量，> 3.2s 前端标红（CPU 跟不上了，窗口越积越多）。
- `e2eFirstMs`：从 `startExtraction` 到第一个字出现的墙钟差——附录 A 讲的「延迟」，后端就是这么量的。

#### ④ `handle` 开头：每个连接一套全新会话（`server.py:110-138`）

```python
async def handle(websocket):
    session = AudioSession()        # ← 每条 WS 连接 = 一个独立会话
    rt = {"start":0.0, "first_text":None, "audio":0.0, "last_send":0.0, "gate_ms":0.0}
    selected_asr = "paraformer" if ASR_MODELS["paraformer"].available else "zipformer"
    selected_processor = "tse" if TSE_MODEL.available else ("speaker_gate" if ...) else "passthrough"
    asr = gate = tse = None
    await send(websocket, "hello", asrReady=..., tseReady=..., asrModels=..., ...)
```

要点：

- **`session` 是函数局部变量**——每条连接独立，断线重连就是新会话（注册语音也丢了，所以前端重连后要重新注册）。没有跨连接的全局状态。
- `selected_processor` 的**默认优先级**：`tse` > `speaker_gate` > `passthrough`。
- 连接一建立就发 `hello`：把当前能力 + 默认选择一次性告诉前端。
- `tse`/`gate`/`asr` 此时都是 `None`——**真正的处理对象要等 `startExtraction` 才创建**。

#### ⑤ 主循环：二进制帧的三分支（`server.py:140-170`）★核心★

```python
async for message in websocket:
    if isinstance(message, bytes):
        session.accept_pcm16(message)              # ① 任何状态都先喂给状态机
        if session.state == SessionState.EXTRACTING:
            rt["audio"] += len(message) / 32_000.0  # 累计已处理音频秒数
            if tse and asr:                         # 分支A:主路径 TSE
                for target in await asyncio.to_thread(tse.accept_pcm16, message):
                    await websocket.send(target)                        # ② 分离出的目标音频→前端回放
                    text, final = await asyncio.to_thread(asr.accept_pcm16, target)  # ③ 对干净音频做ASR
                    await send(websocket, "transcript", text=text, final=final)
            elif gate:                              # 分支B:降级门控
                for tr in await asyncio.to_thread(gate.accept_pcm16, message):
                    await send(websocket, "transcript", **tr)
            elif asr:                               # 分支C:直通诊断
                text, final = await asyncio.to_thread(asr.accept_pcm16, message)
                await send(websocket, "transcript", text=text, final=final)
        continue
```

这段是整个系统的「心脏」，几个设计必须看懂：

**a) 音频先过状态机，再决定要不要处理。** `session.accept_pcm16(message)` 永远调（注册阶段它就负责累积 enrollment）；只有 `state == EXTRACTING` 才进入三分支。

**b) 三分支是互斥的**，由 `startExtraction` 时创建了谁决定。`tse and asr` 优先 → 否则 `gate` → 否则 `asr`（passthrough）。三条路径同时只有一条活着。

**c) TSE 分支的精髓——ASR 拿到的是「分离后」的音频，不是原始混合音频。** 看 ②③ 的顺序：`tse.accept_pcm16(message)` 把混合音频分离成 `target`（目标人干净语音）→ **先发给前端回放** → **再把这个干净 target 喂给 ASR**。这就是「把鸡尾酒会问题从 ASR 身上卸下来」，ASR 只需识别单人干净语音。

**d) `asyncio.to_thread`——阻塞推理不卡事件循环。** `tse.accept_pcm16` / `asr.accept_pcm16` 是 CPU 密集的同步调用（BSRNN、ONNX 推理），直接 `await` 会阻塞整个 asyncio 循环。用 `to_thread` 扔到线程池跑，主循环保持响应。这是「CPU 推理 + 异步 IO」混合的标准手法。

**e) TSE 一次调用可能出 0 个或多个窗。** `for target in tse.accept_pcm16(...)`——128ms 一帧喂进来，大部分时候攒不满 2 秒窗，返回空列表（不进 for）；攒满时可能一次吐出 1 个窗甚至多个（积压追赶时）。

**f) 首字延时与节流推送：**

```python
if text_emitted and rt["first_text"] is None:
    rt["first_text"] = time.perf_counter()       # 记下第一个字出现的时刻
if now - rt["last_send"] >= 0.4:
    await send(websocket, "metrics", ...)         # 每 0.4s 推一次指标，不刷屏
```

#### ⑥ 主循环：JSON 命令（`server.py:171-235`）

二进制是音频，JSON 是控制。命令分发对应状态机：

- `startEnrollment` → `session.start_enrollment()`
- `finishEnrollment` → `session.finish_enrollment()`（校验 ≥3s）
- `setModels` → 先查「没在 ENROLLING/EXTRACTING」，再校验所选模型 available，然后**把 asr/gate/tse 全置 None**（强制下次 startExtraction 重建链路）
- `startExtraction`（`server.py:199-216`）★**搭链路的地方**★

```python
elif command == "startExtraction":
    asr = StreamingAsr(model)            # 永远建 ASR
    gate = None; tse = None
    if selected_processor == "speaker_gate":
        gate = SpeakerGate(asr, VAD_MODEL, SPEAKER_MODEL)
        gate.enroll_pcm16(bytes(session.enrollment))      # 门控:用注册语音建门限
    elif selected_processor == "tse":
        tse = BufferedWeSepTse(TSE_MODEL, bytes(session.enrollment))  # ← 延迟①就在这
    session.start_extraction()
    rt.update(start=time.perf_counter(), first_text=None, ...)   # 重置计时基准
```

**这一段解释了附录 A 的全部延迟**：点「开始提取」瞬间——

1. `StreamingAsr(model)` 建 ASR；
2. `BufferedWeSepTse(...)` 加载 BSRNN 权重 + 用 enrollment 算 ECAPA 声纹嵌入（几百 ms ~ 1s，**延迟①**）；
3. 然后 `rt["start"]` 才被设为「现在」，后面所有 RTF/首字延时都从这里计时；
4. 进入 EXTRACTING 后，音频要再攒满 2 秒窗才出第一段（**延迟②**）。

`bytes(session.enrollment)` 把 bytearray 拷一份传出去，避免被后续修改影响。

- `stopExtraction`（`server.py:217-231`）：停提取时要**冲尾巴**——

```python
session.stop_extraction()
if tse and asr:
    for target in await asyncio.to_thread(tse.flush):    # 不足一窗的尾部零填到整窗再分离
        await websocket.send(target)
        text, final = await asyncio.to_thread(asr.accept_pcm16, target)
        await send(websocket, "transcript", text=text, final=final)
    tail = await asyncio.to_thread(asr.finish, asr._stream)   # ASR 冲尾部上下文
    if tail: await send(websocket, "transcript", text=tail, final=True)
asr = None; gate = None; tse = None      # 释放链路
```

`tse.flush()`（第 3 章）把不足 2 秒的尾巴零填到整窗再分离，避免最后几个字被丢。`asr.finish` 给 Paraformer 补那段 0.66s 尾部静音。

#### ⑦ 错误处理与连接关闭（`server.py:236-259`）

```python
except (SessionError, json.JSONDecodeError) as error:
    await send(websocket, "error", message=str(error), state=session.state.value, ...)
```

状态机的所有「护栏报错」都在这被接住，转成 `error` 事件回前端——前端弹个提示，**会话不崩**。只有 `ConnectionClosed`（断线）才跳出循环。

```python
async def main():
    async with serve(handle, "127.0.0.1", 8765, max_size=2**20):   # 单帧上限 1MB
        await asyncio.Future()                                      # 永久挂起
```

监听 `127.0.0.1:8765`（只本机），`max_size=2**20`=1MB（单帧上限，128ms 的 PCM16 才 4KB，绰绰有余）。`await asyncio.Future()` 是「永久阻塞」的惯用法。

---

## 第 3 章 三条处理链路

### 3.1 TSE 主路径 `tse.py`

算法含金量最高的文件（223 行）。按**对象的生命周期**讲：点「开始提取」时 `__init__` 做了什么 → 之后每一帧 `accept_pcm16` 怎么流动 → 停止时 `flush` 怎么收尾。

#### ① 常量 + 可用性判定（`tse.py:12-46`）

```python
WINDOW_SECONDS = 2          # BSRNN 必须凑满 2 秒才推理一次
CROSSFADE_SECONDS = 0.1     # 相邻窗重叠 0.1 秒做交叉淡化
```

这两个常量决定了**整个系统的延迟下限和实时性**。`2` 是整窗模型的固有代价，`0.1` 是接缝平滑的宽度。

```python
@dataclass(frozen=True)
class TseModel:
    @property
    def model_files_ready(self):  return (config.yaml 和 avg_model.pt 都存在)
    @property
    def dependency_ready(self):   return importlib 能找到 torch 和 wesep
    @property
    def available(self):          return model_files_ready and dependency_ready
```

`dependency_ready` 用 `importlib.util.find_spec` 探测，**不是真的 import**（避免探测时触发 wesep 的 import 问题，第 4 章解决）。

#### ② `__init__`：点「开始提取」瞬间做的三件事（`tse.py:58-95`）

```python
def __init__(self, model, enrollment_pcm16):
    from .wesep_loader import ensure_wesep_runtime   # ① 先打 wesep 的运行时补丁
    ensure_wesep_runtime()
    import torch
    from wesep import load_model_local
    self._torch.set_num_threads(os.cpu_count() or 4)
    self._extractor = load_model_local(str(model.directory))   # ② 加载 BSRNN 权重
    self._extractor.set_device("cpu"); set_resample_rate(16000); set_vad(False)
    self._extractor.set_output_norm(False)             # 关掉逐窗归一化(OLA 需要幅度一致)
    self._enrollment = self._to_tensor(enrollment_pcm16)
    self._cached_spk_embedding = self._compute_enroll_embedding()   # ③ 算注册声纹(只算这一次)
    self._window_samples = int(16000 * 2)              # = 32000 采样
    self._cf_samples     = int(16000 * 0.1)            # = 1600 采样
    self._hop_samples    = self._window_samples - self._cf_samples   # = 30400 采样(1.9s)
    ...build synth_win...                              # 合成窗
    self._in_buf = np.array([], dtype=np.float32)      # 输入累积缓冲
    self._out_overlap = np.array([], dtype=np.float32) # OLA 输出重叠缓冲
```

三件事对应三种开销：① 打补丁（快）→ ② **加载权重到内存（主要耗时）** → ③ 算声纹嵌入（一次 ECAPA 推理）。

关键开关：

- `set_vad(False)`：TSE 自己就是「按目标人提取」，不需要 WeSep 内置 VAD 再切。
- `set_output_norm(False)`：**OLA 的命根子**。若每窗输出各自归一化到 0.9 峰值，相邻窗在接缝处增益会跳变，拼起来就是「咔哒」声。关掉它让幅度跨窗连续，靠后面 clip 兜底。

#### ③ `_compute_enroll_embedding`：注册声纹只算一次（`tse.py:97-108`）

```python
def _compute_enroll_embedding(self):
    enroll = self._enrollment.to(self._device)
    feats = self._extractor.compute_fbank(enroll, sample_rate=16000, cmn=True)  # 梅尔滤波器组 + CMN
    feats = feats.unsqueeze(0)
    with torch.no_grad():
        tmp = model.spk_model(feats)              # ECAPA 编码器 → 说话人向量
        emb = tmp[-1] if isinstance(tmp, tuple) else tmp
        spk_embedding = model.spk_transform(emb)  # 投影到 BSRNN 期望的维度/形状
        spk_embedding = spk_embedding.unsqueeze(1).unsqueeze(3)
    return spk_embedding
```

注册语音在这被「凝固」成一个说话人向量，存进 `self._cached_spk_embedding`。**之后每个窗分离时直接拿缓存用，不再跑 ECAPA**——既省 CPU 又保证条件一致。`compute_fbank` 的 `cmn=True` 是倒谱均值归一，去掉信道色染。

#### ④ 每一帧：`accept_pcm16` 的累积循环（`tse.py:188-200`）★

这是 TSE 的节拍器：

```python
def accept_pcm16(self, chunk: bytes) -> list[bytes]:
    samples = np.frombuffer(chunk, dtype="<i2").astype(np.float32) / 32768.0   # PCM16→float
    self._in_buf = np.concatenate([self._in_buf, samples])    # 往输入缓冲里攒
    outputs = []
    while len(self._in_buf) >= self._window_samples:          # 攒满 32000(2秒)才进
        start = time.perf_counter()
        window = self._in_buf[: self._window_samples]         # 取一个 2 秒窗
        seg = self._extract_float(window) * self._synth_win   # 分离 × 合成窗
        self._ola_add(seg)                                     # 叠进 OLA 输出缓冲
        outputs.append(self._to_pcm16(self._ola_take(self._hop_samples)))  # 取走 1.9s 输出
        self.last_extract_ms = (time.perf_counter() - start) * 1000.0       # 记单窗耗时
        self._in_buf = self._in_buf[self._hop_samples:]       # 输入缓冲前进 1.9s
    return outputs
```

逐行拆：

1. **PCM16 → float**：`np.frombuffer(dtype="<i2")` 小端 16 位整数，除 32768 归一化到 [-1, 1]。
2. **累积**：`_in_buf` 不断 append。**没攒满 32000 采样，while 不进，返回空 list**——这就是「点完提取后还要等 ~2 秒才出第一个字」的根因。
3. **攒满后取一个 2 秒窗** → `_extract_float` 分离 → 乘合成窗 → OLA 叠加。
4. **取走 `_hop_samples = 30400`（1.9 秒）** 作为输出，转回 PCM16 发出去。
5. **输入缓冲前进 1.9 秒**（不是 2 秒）——留下的 0.1 秒是下一窗要重叠的区域。

`while` 一次调用可能连续出多个窗——CPU 积压、`_in_buf` 已攒好几窗时，这里一口气追赶。正常实时跟随时一次出 0 或 1 个窗。

#### ⑤ `_forward_cached`：BSRNN 前向（`tse.py:110-152`）— 最硬核的一段

这是**手动重写**了 `wesep.models.bsrnn.BSRNN.forward`，唯一改动是「把缓存的注册嵌入直接喂进去，跳过每窗 ECAPA」。信号流是标准频域分离五步（逐步图解 + 公式见 [TSE-DEEP-DIVE.md](TSE-DEEP-DIVE.md)）：

```
wav(2s时域)
  │ ① torch.stft                      → 复数频谱 spec
  │ ② 拆子带 (real/imag 两路)          → subband_spec
  │ ③ 每个 BN 层降维                   → subband_feature
  │ ④ separator(subband_feature,      → est_real/est_imag
  │              cached_spk_embedding)   ↑注册声纹作条件
  │ ⑤ 复数比例掩蔽 × 原始频谱          → est_spec(目标人频谱)
  │ ⑥ torch.istft                     → 目标人时域波形 s
  ▼
```

代码要点：

```python
# ④ 分离核:子带特征 + 注册声纹 → 掩蔽
sep_output = model.separator(subband_feature, self._cached_spk_embedding, torch.tensor(nch))

# ⑤ 复数比例掩蔽 CRM(real/imag 两路)
this_mask = this_output[:, 0] * torch.sigmoid(this_output[:, 1])   # 门控:幅值×sigmoid(掩蔽)
est_real = mix.real * mask_r - mix.imag * mask_i                   # 复数乘法:掩蔽×原频谱
est_imag = mix.real * mask_i + mix.imag * mask_r
```

要理解的设计：

- **频域而非时域**：BSRNN 在频谱上估一个「掩蔽(mask)」，表示「每个时频点要保留多少目标人能量」。`mask = A · sigmoid(B)` 是「幅值门 + sigmoid 压缩」的经典 CRM 形式。
- **掩蔽 × 原始混合频谱**：`est = mask ⊗ mix`——掩蔽只是「比例」，真正改写波形的是拿它去乘原始混合信号的频谱。所以输出是「从混合里抠出目标」。
- **第 ④ 步的 `self._cached_spk_embedding`**：这就是「注册声纹当条件」的注入点。没有它，separator 不知道该抠谁。

#### ⑥ OLA：为什么相邻两窗能无缝拼起来（`tse.py:82-91, 175-186`）

问题：每个 2 秒窗是**独立**估掩蔽的，两窗在接缝处的掩蔽不一致，直接首尾相接会在边界丢/畸变一个字。解决办法是**重叠相加 + 合成窗**。

**合成窗的构造**（`__init__` 里）：

```python
ramp = np.linspace(0.0, 1.0, self._cf_samples, endpoint=False)   # 0→1 的 1600 点斜坡
win = np.ones(self._window_samples)            # 全 1
win[:self._cf_samples]  = ramp                 # 窗头:0→1 渐入
win[-self._cf_samples:] = 1.0 - ramp           # 窗尾:1→0 渐出
self._synth_win = win
```

形状是一个「平顶梯形」：中间 1.8 秒恒为 1，两头各 0.1 秒线性渐变。

**OLA 的叠加规则**（`_ola_add` / `_ola_take`）：

```python
def _ola_add(self, seg):        # 把本窗输出(已乘 synth_win)叠到输出缓冲
    ...self._out_overlap[:n] += seg

def _ola_take(self, hop):       # 从输出缓冲取走前 hop(1.9s),剩下的留作下窗重叠
    ready = self._out_overlap[:hop].copy()
    self._out_overlap = self._out_overlap[hop:]
    return ready
```

**为什么这样能无缝？** 重叠区（那 0.1 秒）：上一窗的尾巴 = `1 - ramp`（1→0），下一窗的头部 = `ramp`（0→1），叠加：`(1 - ramp) + ramp = 1` —— **恒等于 1！**

这就是注释里说的 **「overlap-add reconstructs unity」(COLA 常数重叠相加)**：重叠区两窗窗值之和正好是 1，输出 = 两窗分离结果的**加权平均**（交叉淡化）；非重叠的中段窗值恒 1，输出 = 分离结果本身。整体重构出无失真波形，只在接缝处把两个不一致的掩蔽平滑过渡过去。

**为什么 hop = window − crossfade（1.9s），不是常见的 50% 重叠？** 50% 重叠每个采样处理两遍，RTF 翻倍；这里 `RTF ≈ window/hop ≈ 2/1.9 ≈ 1.05`，几乎零额外开销却仍无缝。这是**用最小重叠换最大算力效率**的精算。

#### ⑦ `flush`：停止时把尾巴冲干净（`tse.py:202-223`）

```python
def flush(self):
    tail_len = len(self._in_buf)
    take = len(self._out_overlap)                    # 默认:吐出已攒的重叠(渐出)
    if tail_len >= int(16000 * 2 * 0.3):             # 尾巴 ≥0.6s 才值得再分离一次
        window = np.zeros(self._window_samples)      # 零填到整窗
        window[:n] = self._in_buf[:n]
        seg = self._extract_float(window) * self._synth_win
        self._ola_add(seg)
        take = min(len(self._out_overlap), max(self._cf_samples, n))   # 只取真实音频占据的部分
    outputs.append(self._to_pcm16(self._out_overlap[:take]))
```

停止提取时，`_in_buf` 里通常还剩不到 2 秒。`flush` 把它**零填到整窗再分离**，但**只取真实音频占据的输出段**，避免末尾被静音填充或丢字。

#### `tse.py` 设计要点速查

| 设计 | 解决的问题 |
|---|---|
| 整窗 2s + OLA | BSRNN 是非因果整窗模型，OLA 让相邻窗无缝拼接 |
| 注册嵌入算一次缓存 | 跳过每窗 ECAPA，省算力 + 条件一致 |
| `_forward_cached` 重写 forward | 注入缓存的声纹条件，复刻 BSRNN 信号流 |
| 合成窗 ramp + (1-ramp) | COLA:重叠区恒为 1，无失真重构 |
| hop = window − crossfade | 最小重叠换最高算力效率，RTF≈1.05 |
| 关 output_norm | 跨窗幅度连续，避免接缝「咔哒」 |

代价：**2 秒整窗 = 固有延迟**。这类频域分离模型在 CPU 上的本质限制。

### 3.2 流式 ASR `asr.py`

每条路径都用，102 行。

#### ① 两个模型，一个壳（`asr.py:16-69`）

```python
@dataclass(frozen=True)
class AsrModel:
    kind: str   # "paraformer" 或 "transducer"
    @property
    def available(self):
        if self.kind == "paraformer":
            required = ["tokens.txt", "encoder.int8.onnx", "decoder.int8.onnx"]      # 2 个 onnx
        else:
            required = [..., "encoder-...int8.onnx", "decoder-....onnx", "joiner-....int8.onnx"]  # 3 个
```

**架构差异决定文件数**：Transducer 是 RNN-T 架构，要 encoder + decoder + **joiner** 三件套；Paraformer 是非自回归，只要 encoder + decoder 两件套。

```python
if model.kind == "paraformer":
    self._recognizer = sherpa_onnx.OnlineRecognizer.from_paraformer(...)
    self._tail_padding = 0.66        # ← Paraformer 需要 0.66s 尾部静音
else:
    self._recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
        ..., enable_endpoint_detection=True, ...     # ← 只有 Transducer 开端点检测
    )
    self._tail_padding = 0.0
```

**两个模型在「怎么断句」上策略不同，这是最重要的点：**

| | Transducer | Paraformer |
|---|---|---|
| 端点检测 | `enable_endpoint_detection=True` ✅ | 未传该参数 ❌ |
| 尾部 padding | 0 | 0.66s |
| 断句靠 | 自身端点检测（检测到停顿自动断） | 主要靠外部切段 + 停止时 `finish` 冲尾部 |

> 为什么 Paraformer 要尾部 padding？它是双向模型的流式截断版，trailing 上下文没喂够时最后几个 token 解不出来。补 0.66s 静音等于「假装后面还有静音」，把残余上下文冲出来。Transducer 是因果模型，不需要。

**一个连带的行为差异**：在 TSE 主路径下（`server.py` 每窗调一次 `asr.accept_pcm16`），Transducer 会靠端点检测中途产出 `final=True` 自动断句；Paraformer 分支没开端点检测，`final` 更依赖停止时的 `finish`。

```python
_ASR_THREADS = 2
# paraformer/transducer streaming decode is largely autoregressive; benchmarking
# showed more threads make it slower, so keep this small.
```

**反直觉的优化**：解码是自回归的，实测线程越多反而越慢（调度/竞争开销 > 收益），所以固定 2 线程。这种「实测推翻常识」的注释说明作者真的 benchmark 过。

#### ② `feed`：流式解码的标准三步（`asr.py:74-79`）

```python
def feed(self, stream, samples):
    stream.accept_waveform(SAMPLE_RATE, samples)       # ① 喂入新音频
    while self._recognizer.is_ready(stream):           # ② 还能解就继续解(自回归)
        self._recognizer.decode_stream(stream)
    return self.result(stream)                          # ③ 取当前累积文本
```

每来一段音频：喂入 → 把所有**当前能解出的 token** 都解出来 → 取累积结果。这是流式 ASR 的核心模式：**增量解码**，文本随音频增长而变长（打字机效果的来源）。

#### ③ `accept_pcm16`：final 的真正含义（`asr.py:95-103`）

```python
def accept_pcm16(self, chunk) -> tuple[str, bool]:
    text = self.feed(self._stream, samples)             # 增量文本
    self.last_feed_ms = ...                             # 记耗时给 metrics
    final = self._recognizer.is_endpoint(self._stream)  # 检测到句子边界?
    if final:
        self._recognizer.reset(self._stream)            # 开新句:清空流
    return text, final
```

**`final` 不是「识别完成」，而是「检测到一个句子边界（停顿）」**。一旦 final：前端把当前这句**定稿**；后端 `reset` 清空流，**下一句从零开始**。`text` 是「到目前为止这句的累积文本」，`final=True` 那次是「这句的最终文本」。

### 3.3 声纹门控 `speaker_gate.py`

降级路径（124 行）：不改写波形，只判「这段语音像不像目标人，像就转写、不像就丢」。

#### ① 三个零件（`speaker_gate.py:14-54`）

```python
class SpeakerEmbedder:                      # 声纹提取器
    def __init__(self, model_path):         # 3D-Speaker ER2Net
        ...SpeakerEmbeddingExtractorConfig(model=..., num_threads=2, provider="cpu")
    def embed(self, samples):               # 音频 → 声纹向量
        ...
    @staticmethod
    def cosine(left, right):                # 余弦相似度
        return dot(left,right) / (norm(left)*norm(right))

class SpeakerGate:
    def __init__(self, asr, vad_model, speaker_model, threshold=0.5):
        config.silero_vad.threshold = 0.5
        config.silero_vad.min_silence_duration = 0.3    # 静音满 0.3s 才算「话说完了」
        config.silero_vad.min_speech_duration = 0.1     # 短于 0.1s 的不算一句话
        config.silero_vad.max_speech_duration = 15.0    # 最长 15s 强制切
        self._vad = VoiceActivityDetector(config, buffer_size_in_seconds=30)
        self._embedder = SpeakerEmbedder(speaker_model)
        self._asr = asr                    # 复用外面传进来的 ASR
        self._threshold = threshold        # 相似度门槛 0.5
```

三个零件：**Silero VAD**（切语音段）+ **ER2Net 声纹**（算嵌入）+ **复用的 ASR**（转写）。ASR 是构造时传进来的（`server.py:207`），门控自己不建 ASR，共用同一个。

`enroll_pcm16` 把注册语音算成声纹并**归一化**：`self._enrollment = embedding / (norm(embedding) + 1e-8)`（存单位向量，后面 cosine 更快更稳）。

#### ② `accept_pcm16`：VAD 驱动的「一句话」状态机（`speaker_gate.py:61-75`）★

```python
def accept_pcm16(self, chunk):
    self._vad.accept_waveform(samples)
    speech = self._vad.is_speech_detected()
    if speech and not self._speech_active:    # ① 话开始(静→有声)
        self._begin_utterance(samples); return self._emit_partial()
    if speech and self._speech_active:        # ② 话进行中(声→声)
        self._chunks.append(samples); self._asr.feed(...); return self._emit_partial()
    if not speech and self._speech_active:    # ③ 话结束(声→静)
        self._asr.feed(...); return self._finish_utterance()
    return []                                  # ④ 持续静默:啥也不做
```

这是个由 VAD 驱动的「一句话生命周期」：**开始 → 进行 → 结束**，四态分明。`min_silence_duration=0.3` 决定要多长的停顿才算「这句话讲完了」。

#### ③ `_emit_partial`：边说边判声纹的早判定（`speaker_gate.py:86-102`）★最聪明的设计★

```python
def _emit_partial(self):
    total = sum(len(chunk) for chunk in self._chunks)    # 这句话已累积多长
    if not self._accepted and total >= 0.6s and (距上次判定 ≥ 0.3s):
        similarity = self._similarity()                  # 算一次声纹相似度
        self._accepted = similarity >= 0.5               # 像 → 认定是目标人
    if not self._accepted:
        return []                                        # 还没认出来 → 憋着不说
    text = self._asr.result(self._stream)
    ...
    return [{"text": text, "final": False, "similarity": similarity}]
```

**这是门控路径的精髓——不用等一句话说完才知道是不是目标人：**

- 累积满 **0.6s**（`MIN_DECISION_SECONDS`）才第一次判声纹（太短的样本声纹不稳）；
- 之后每 **0.3s**（`DECISION_INTERVAL_SECONDS`）复判一次；
- **一旦某次相似度 ≥ 0.5，`_accepted = True` 永久置位**——这句话后面一路放行，不再复判；
- 在 `_accepted` 之前，即使 ASR 已经认出字，**也不输出**——「还没确认是目标人，先别出字幕」。

效果：目标人开口 0.6s 内就能「认出」并开始出字幕，延迟远低于 TSE 的 2 秒整窗——这是门控路径**唯一比 TSE 快的地方**（代价是处理不了重叠）。

#### ④ `_finish_utterance`：收尾判定（`speaker_gate.py:104-113`）

```python
def _finish_utterance(self):
    text = self._asr.finish(self._stream)                # 冲尾部(Paraformer 的 0.66s padding 在这生效)
    similarity = self._similarity() if text else 1.0
    accepted = bool(text) and similarity >= 0.5
    self._reset_utterance()                              # 清空,等下一句
    if accepted:
        events.append({"text": text, "final": True, "similarity": round(similarity, 3)})
    events.append({"text": "", "final": False, "similarity": None})   # 清前端 partial 显示
    return events
```

一句话结束（VAD 检测到静音）：冲完尾部 → 用**整句**的声纹做最终判定 → 像目标人就发 `final=True` 定稿，不像就丢弃。末尾空 partial 用来**清掉前端正在显示的 partial 字幕**。

### 3.4 三链路对比

| 路径 | 做什么 | 改波形? | 重叠语音 | 首字延时 |
|---|---|---|---|---|
| **tse** | BSRNN 把目标人波形从混合里抠出来，再 ASR | ✅ | ✅ 能分离 | ~2s（整窗） |
| **speaker_gate** | VAD 切段 → 声纹门控决定转不转写 | ❌ | ❌ 失真 | ~0.6s（早判定） |
| **passthrough** | 原音直接 ASR | ❌ | —（不区分人） | 最快（诊断用） |

三条链路共用同一个 `asr`，只是「喂给 ASR 之前的预处理」不同：TSE 喂分离后的干净音频、门控喂通过声纹筛选的段、直通喂原始音频。`server.py` 的三分支就是这三条路的开关。

---

## 第 4 章 工程垫片

整个工程最 hacky、也最值钱的部分：它解决了「`import wesep` 会炸，但我们其实只需要它的一小块」这个矛盾，且**不改动任何已安装文件**。

### 4.1 `wesep_loader.py`

#### ① 痛点：为什么 `import wesep` 会炸

`install-wesep-tse.ps1` 用 `--no-deps` 装 wesep（故意不拉依赖）。这带来两个 import 时就崩的问题，而 TSE 模型（BSRNN+ECAPA）**根本用不到**引发崩溃的那些东西：

```python
# 问题1:wesep wheel 漏发了 wesep.utils 包
#   但 wesep.cli.extractor 在「模块加载时」就执行:
from wesep.utils.checkpoint import load_pretrained_model, set_seed   # ← ImportError:wesep.utils 不存在

# 问题2:一堆可选子模块拉重依赖(--no-deps 没装)
#   wespeaker.cli.speaker      → umap/kaldiio/diar
#   wespeaker.frontend.s3prl   → s3prl
#   wespeaker.frontend.whisper_encoder → whisper
#   wespeaker.frontend.w2vbert → transformers
```

关键矛盾：TSE 推理只走 `wesep.models.bsrnn`，碰都不碰上面那些。但 Python 的 import 是「加载包就执行所有子模块的顶层代码」，`import wesep` 的瞬间这些炸弹就被引爆了。

#### ② 旧办法 vs 新办法

```python
# 旧做法:直接改 site-packages 里的源文件
#   问题:脏、难维护、wesep 一升级就没了
# 新做法(本模块):纯进程内,在 `import wesep` 之前打补丁,不碰任何已安装文件
```

核心思路：在 Python 真正去执行 `import wesep` **之前**，先往 `sys.modules`（模块缓存）里塞好两样东西，让后续那些会失败的 import 解析到「准备好的替身」。

#### ③ 第一招：`_install_vendored_utils` —— 补全缺失的包（`wesep_loader.py:76-103`）

```python
_VENDOR = Path(__file__).resolve().parent / "_wesep_utils"   # 同目录下的 vendored 副本

def _install_vendored_utils():
    if sys.modules.get("wesep.utils.utils") is not None: return   # 幂等

    pkg = types.ModuleType("wesep.utils")
    pkg.__path__ = [str(_VENDOR)]        # ★ 关键:设 __path__ 才算「包」
    pkg.__package__ = "wesep.utils"
    sys.modules["wesep.utils"] = pkg     # 注册为 wesep.utils 包

    # 注意顺序:schedulers/utils 先,checkpoint 后
    for name in ("schedulers", "utils", "checkpoint"):
        dotted = f"wesep.utils.{name}"
        spec = importlib.util.spec_from_file_location(dotted, _VENDOR / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[dotted] = mod                 # ★ 先占位
        setattr(pkg, name, mod)
        spec.loader.exec_module(mod)              # ★ 后执行模块体
```

逐点拆：

- **`pkg.__path__ = [str(_VENDOR)]` 是这一招的灵魂。** 普通 `types.ModuleType` 不带 `__path__`，Python 不认为它是「包」，`from wesep.utils.X import Y` 找不到子模块 X。手动设 `__path__` 指向 `_wesep_utils/`，它就成了一个**真正的包**，子模块 import 能往下解析。
- **为什么不能用 `PYTHONPATH` 覆盖？** `wesep` 是个有 `__init__.py` 的常规包，「常规包不能在另一个 sys.path 条目上扩展」——没法在别处放 `wesep/utils/` 去 shadow 已安装的那个。所以只能直接往 `sys.modules` 塞并伪造 `__path__`。
- **加载顺序 `schedulers → utils → checkpoint`** 不是随便排的：`checkpoint.py` 第 5 行就 `from wesep.utils.schedulers import BaseClass`，所以 schedulers 必须先就位。
- **「先 `sys.modules[dotted] = mod`，再 `exec_module(mod)`」** 是处理循环/自引用 import 的标准手法：模块体执行时若引用到自己，得能在 `sys.modules` 里找到占位。

#### ④ 第二招：`_seed_stub` —— 惰性桩可选子模块（`wesep_loader.py:53-73`）

```python
_OPTIONAL_STUBS = {
    "wespeaker.cli.speaker":          {"load_model": None, "load_model_pt": None},
    "wespeaker.frontend.s3prl":       {"S3prlFrontend": None},
    "wespeaker.frontend.whisper_encoder": {"whisper_encoder": None},
    "wespeaker.frontend.w2vbert":     {"W2VBertFrontend": None},
    "wesep.models.bsrnn_feats":       {},      # 空桩:另一个后端,永不路由到
}

def _seed_stub(dotted, attrs):
    if sys.modules.get(dotted) is not None: return    # 幂等
    mod = types.ModuleType(dotted)
    for key, value in attrs.items():
        setattr(mod, key, value)        # 暴露被 from X import Y 引用的符号(值为 None)
    sys.modules[dotted] = mod
```

原理：这些可选子模块的**模块体**会 `import transformers` 之类然后崩。但上游代码往往只是 `from wespeaker.cli.speaker import load_model`——它要的只是 `load_model` 这个名字。往 `sys.modules` 塞一个**空壳模块**，预先把 `load_model` 设成 `None`。这样 `from X import Y` 直接从 `sys.modules[X]` 拿到 stub，拿到 `Y=None`，**根本不执行那个会崩的模块体**。

> `bsrnn_feats` 的桩是空 `{}`：另一个分离后端，需要 `wesep.utils.funcs`（也被 wheel 漏了）。但本模型只用 `bsrnn`，代码路径永远走不到，所以给它个空桩占位即可。

#### ⑤ 两条「纪律」（踩坑换来的注释）

```python
# 规则:只 stub 真正会失败的模块。
#   如果你 stub 了一个「其实能正常 import」的嵌套叶子(比如 wesep.modules.metric_gan.discriminator),
#   而它的父包还没被 import,会污染父包:"cannot import name '<leaf>' from '<parent>'"
#   其它后端(convtasinet/dpccn/tfgridnet/...)在 --no-deps 下能干净 import,所以「故意不动」。
```

**最容易踩的雷**：stub 不是越多越安全。给一个能正常加载的模块塞桩，反而会破坏它的父包 import。作者明确列出「哪些故意不 stub」，说明他一个个 probe 过。

#### ⑥ 调用点与幂等

```python
def ensure_wesep_runtime():       # 唯一公开入口
    _install_vendored_utils()
    for dotted, attrs in _OPTIONAL_STUBS.items():
        _seed_stub(dotted, attrs)
```

在 `tse.py:62-64`，`import wesep` 之前调一次。三个内部函数开头都有 `if sys.modules.get(...) is not None: return`，所以**幂等**。

> 提醒：`importlib.find_spec` 的目录查找在进程启动时就建好缓存，所以**装新依赖后必须重启后端**，旧进程发现不了。

### 4.2 `_wesep_utils/`

```
_wesep_utils/
  __init__.py     (1 行注释: "ships the wesep.utils files the wheel omits")
  checkpoint.py   (105 行: load_pretrained_model / load_checkpoint — 后者训练用,TSE 用不到)
  schedulers.py   (323 行: 学习率调度器,提供 BaseClass)
  utils.py        (266 行: 杂项工具)
```

从 wesep 上游源码原样拷贝来的本地副本，不是本工程原创逻辑——角色是「补全 wheel 漏发的 `wesep.utils` 包」。`checkpoint.py` 的 `load_pretrained_model` 就是标准的 `torch.load(path) → model.load_state_dict(state["models"][0])`，加载那个 `avg_model.pt`。

### 4.3 垫片价值速查

| 痛点 | 解法 |
|---|---|
| wheel 漏 `wesep.utils` | vendored 本地副本 + 伪造 `__path__` 注册成真包 |
| 可选子模块拉重依赖 | `sys.modules` 预置惰性 stub，让 `from X import Y` 拿到空壳 |
| 改 site-packages 太脏 | 全程进程内，启动前打补丁，不碰已安装文件 |
| stub 污染父包 | 只 stub 真正失败的模块，能干净 import 的一律不动 |

一句话：**用 `sys.modules` 的导入机制，在不改任何已安装代码的前提下，让一个「缺胳膊少腿」的 wheel 跑起来。** 任何「上游包 import 链太重、但我只用一小块」的场景都通用。

---

## 第 5 章 前端主体 `App.vue`

673 行（`<script setup>` 1–490 是逻辑，`<template>` 492–673 是结构）。设计哲学：**它是个「瘦客户端」**——所有重活都在后端，前端只做三件事：采集/灌入音频、分发后端事件、把结果好看地渲染出来。

### 5.1 全局状态（`App.vue:34-94`）

```ts
const state = ref<SessionState>('idle')     // 跟后端 session.py 的四态一一对应
const socket: WebSocket                     // 唯一的 WS 连接
const transcript = ref<TranscriptEntry[]>([])   // 已定稿的字幕行
const partialText = ref('')                     // 正在打字的当前句
const metrics = ref<Metrics>({...})             // 对应后端 metrics 事件
// 文件模式:
const FILE_FRAME = 2048
const FILE_INTERVAL_MS = (FILE_FRAME / 16000) * 1000   // = 128 ms —— 和麦克风帧完全同节拍
```

关键常量对齐：**`FILE_INTERVAL_MS = 128ms`**，和麦克风的 `ScriptProcessor(2048)` 一模一样。这就是文件模式能「完美模拟麦克风」的根——两者往 WS 灌数据的节拍完全相同。

### 5.2 WS 客户端 `connectBackend`（`App.vue:129-202`）★

```ts
socket = new WebSocket('ws://127.0.0.1:8765')
socket.onclose = () => { ... if (mounted) reconnectTimer = setTimeout(connectBackend, 2000) }  // 断线2s重连
socket.onmessage = async ({ data }) => {
  if (data instanceof Blob) {                                   // ① 二进制 = 分离音频(仅TSE路径)
    if (playback.value) enqueueSeparatedAudio(await data.arrayBuffer())
    return
  }
  const event = JSON.parse(data)                                // ② JSON = 控制事件
  if (event.state) state.value = event.state                    //    每条都带 state,同步状态机
  ...按 event.event 分发:hello / modelsChanged / error / metrics / transcript
}
```

前端用 `data instanceof Blob` 区分二进制和 JSON——这正是第 2 章讲的「同一条 WS 既走音频又走控制」。`onmessage` 是整个前端的「事件总线」，五个事件分支一一对应后端 `send()` 发出的东西。

**`transcript` 事件的处理最精巧**（`App.vue:186-200`）：

```ts
if (event.final) {                          // 后端检测到句子边界
  transcript.push({ time: liveTime.value || nowstamp(), text: withPunct(text) })  // 定稿进列表
  partialText = partialTarget = liveTime = ''            // 清空当前句
} else {                                    // partial:句子还在长
  if (!liveTime.value) liveTime.value = nowstamp()        // 记这句的「开始时间」
  feedPartial(event.text || '')                            // 喂给打字机
}
```

设计点：**字幕行的时间戳 = 这句话第一个字到达的时刻**（`liveTime`），一句的所有 partial 共享同一个开始时间，final 时用它定稿。`withPunct` 给没标点的句子补个句号。

### 5.3 麦克风采集 `startCapture`（`App.vue:229-244`）★

```ts
const stream = await navigator.mediaDevices.getUserMedia({
  audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true }   // 单声道 + AEC + NS
})
const context = new AudioContext({ sampleRate: 16000 })
const processor = context.createScriptProcessor(2048, 1, 1)     // 每 2048 采样回调一次 = 128ms
processor.onaudioprocess = (event) => {
  const data = event.inputBuffer.getChannelData(0)              // Float32 [-1,1]
  let sum = 0; for (...) sum += data[i]*data[i]
  micLevel.value = Math.min(1, Math.sqrt(sum / data.length) * 3) // RMS→电平条
  if (socket?.readyState === WebSocket.OPEN) socket.send(floatToPcm16(data))  // 转 PCM16 发后端
}
```

- `getUserMedia` 要单声道 16k，开 AEC（回声消除）和 NS（降噪）。
- `ScriptProcessor(2048)` 每攒满 2048 采样（128ms）回调一次。这就是「128ms 一帧」的源头，与后端 `FRAME_MS=128`、文件模式 `FILE_INTERVAL_MS=128` 三处对齐。

**`floatToPcm16` 里有个容易看漏的正确性细节**（`App.vue:104-111`）：

```ts
pcm[index] = limited < 0 ? limited * 0x8000 : limited * 0x7fff
//                       ↑ 32768          ↑ 32767
```

**非对称量化**：PCM16 范围是 `[-32768, 32767]`，负数能到 -32768，正数最大 32767。所以负样本乘 32768、正样本乘 32767，避免正值溢出。这是正确的 PCM16 转换，不是随手写的。

### 5.4 文件解码与真实速率灌入（`App.vue:253-269, 358-378`）

```ts
async function decodeAudioFile(file): Promise<Float32Array> {
  const decoded = await context.decodeAudioData(arrayBuffer)    // 浏览器解码任意格式→16k
  if (channels === 1) return decoded.getChannelData(0)
  for (c) for (i) out[i] += data[i] / channels                  // 多声道:取平均混成单声道
}
```

任意音频文件经浏览器 `decodeAudioData` 解到 16k 单声道 Float32。

```ts
function startFileStream(buffer, kind, onDone) {
  let pos = 0
  fileTimer = setInterval(() => {                              // 每 128ms
    const chunk = buffer.subarray(pos, pos + 2048)             //   取 2048 采样
    socket.send(floatToPcm16(chunk))                           //   转PCM16发后端(和麦克风完全一样)
    micLevel.value = ...RMS...                                 //   顺便驱动电平条
    pos += 2048; progress = pos / buffer.length                //   进度条
    if (pos >= buffer.length) { stopFileStream(); onDone() }    //   灌完→回调
  }, FILE_INTERVAL_MS)                                          // 128ms
}
```

**这就是「文件按真实速率灌入」**。它和 `startCapture` 走的是**同一条 WS 路径、同一个节拍**——后端完全分不清来的是麦克风还是文件，流式行为（窗口累积、首字延时）完全一致。这就是为什么能用文件稳定复现线上的延迟。

### 5.5 分离音频回放 `enqueueSeparatedAudio`（`App.vue:286-300`）★巧设计★

后端 TSE 路径会把「分离出的目标人音频」二进制发回来。难点：片段是**异步、不定期**到达的，怎么播得连续无断裂？

```ts
function enqueueSeparatedAudio(pcm16) {
  const ctx = ensurePlayCtx()
  const src = ctx.createBufferSource(); src.buffer = buf; src.connect(ctx.destination)
  const now = ctx.currentTime
  if (nextStartTime < now) nextStartTime = now      // ★ 落后了:重同步,避免片段越堆越多
  src.start(nextStartTime)                          //   排在「下一个该播的时刻」
  nextStartTime += buf.duration                     //   推进指针
}
```

**「调度播放」而非「立即播放」**：`nextStartTime` 记着「下一段该从几秒开始播」。每个新片段排在 `nextStartTime`，然后指针前移 `buf.duration`。相邻片段**首尾精确相接、无间隙无重叠**——Web Audio API 的标准无缝拼接手法。

`if (nextStartTime < now) nextStartTime = now` 是兜底：如果某段时间片段来得太慢，直接重同步到 now，避免历史片段堆积成一长串延迟回放。

> 回放开关策略：文件模式**自动开**回放（显然想听分离效果），麦克风模式**默认关**（扬声器声会被麦克风收回去造成啸叫，`togglePlayback` 在这时弹「请戴耳机」警告）。

### 5.6 打字机字幕 `feedPartial`（`App.vue:320-345`）★

```ts
function feedPartial(text) {
  if (text.startsWith(partialText.value)) {                    // ① 正常增长:新text是旧的扩展
    for (const ch of text.slice(partialText.value.length)) typePending.push(ch)
    partialTarget.value = text
    ensureTypeTimer()                                          //   打字机每90ms吐一字
  } else {                                                     // ② ASR修订了前面的字(非前缀)
    clearInterval(typeTimer); typePending.length = 0           //   清空打字机
    partialText.value = partialTarget.value = text             //   直接 snap 到新文本
  }
}
```

两个分支：

- **正常增长**（新文本是旧文本的前缀扩展）：新增字符推进 `typePending` 队列，打字机每 90ms 吐一个字——「一个字一个字蹦出来」，比瞬变柔和。
- **ASR 修订**（新文本不是旧文本的前缀，前面某字被改写）：**放弃打字机，直接 snap** 到最新文本。否则打字机会吐出已作废的字。

`ensureTypeTimer` 是个「按需启动、空了自停」的单例定时器。

### 5.7 两个主按钮 `toggleEnrollment` / `toggleExtraction`（`App.vue:426-467`）

结构对称。以提取为例：

```ts
async function toggleExtraction() {
  if (state.value === 'extracting') {              // 已在提取→停止
    if (fileKind === 'mix') stopFileStream() else await stopCapture()
    sendCommand('stopExtraction'); return
  }
  if (sourceMode === 'file' && !mixBuffer.value) { notice = '请先选择混合音频文件'; return }
  sendCommand('startExtraction')                    // ① 发命令
  if (sourceMode === 'file') {
    await waitForState('extracting')                // ② 等后端把链路搭好(延迟①)
    startFileStream(mixBuffer.value!, 'mix', () => sendCommand('stopExtraction'))  // ③ 开始按速率灌
  } else {
    await startCapture()                            // 麦克风:直接采集
  }
}
```

**`waitForState('extracting')` 对应延迟①**：发完 `startExtraction`，前端要等后端搭好链路（加载 BSRNN + 算嵌入 + 回 `state=extracting`）才开始喂文件。每 40ms 轮询 `state.value`，2 秒超时。这正是「点完提取、进度条迟迟不动」的那一下。

### 5.8 模板结构（`App.vue:492-673`，简要）

纯声明式渲染，把状态绑到 DOM，没什么逻辑，布局如下：

```
header   连接状态点
workspace
  aside(左栏)
    会话流程:01 注册 / 02 提取(按 state 高亮当前步)
    引擎状态:ASR就绪?/ TSE就绪?/ 当前链路 / CPU
  content(右栏)
    banner            系统提示(notice + tone 配色)
    model-switcher    处理模式 / 识别模型 分段按钮(按 available 灰化)
    file-panel        测试模式:麦克风/文件切换 + 注册/混合文件选择
    enrollment-panel  注册按钮 + 24格电平条
    transcript-panel  字幕区(定稿行 + partial 打字机行)+ 开始提取/停止 + 播放开关
    metrics-panel     RTF徽章 + 6个指标格(首字/单窗/积压/...)
```

几个绑定细节：`metrics-panel` 用 `v-if="metrics.rtf !== null"`（还没收到 metrics 就不显示整块）；`backlogSec > 3.2` 时积压数字标红（`App.vue:665`）；`rtfTone` 把 RTF 映射成三色（`<0.85` 绿 / `<1` 黄 / `≥1` 红）。

---

## 第 6 章 全工程闭环

### 一帧音频的完整旅程

五个回合串起来，就是这张图（回想整个系统该记的全景）：

```
┌──────────── 前端 App.vue ────────────┐    ┌──────── 后端 server.py ────────┐
│                                       │    │                                 │
│ 麦克风 startCapture(2048/128ms)      │    │  session.accept_pcm16 [状态机]  │
│   或 文件 startFileStream(同节拍) ────┼──▶│        │                        │
│   floatToPcm16 → WS.send(二进制)     │    │   EXTRACTING?                   │
│                                       │    │     ├─ tse:  分离→target ──┐    │
│                                       │ ◀──│     │       (BSRNN整窗+OLA) │    │
│   ←─ 二进制(target)enqueue回放 ──────┼─── │     │                  ASR ──┤    │
│   ←─ JSON transcript ─────────────────┼─── │     │                        │ │
│   ←─ JSON metrics ────────────────────┼─── │     ├─ gate: VAD→声纹门控→ASR │
│                                       │    │     └─ pass: 直接 ASR         │
│   partial→打字机 / final→定稿         │    │                                 │
│   metrics→RTF/首字/积压 渲染          │    │  每0.4s 推 metrics              │
└───────────────────────────────────────┘    └─────────────────────────────────┘
```

### 全工程一句话总结

| 层 | 文件 | 一句话 |
|---|---|---|
| 壳 | `electron/*`、`main.ts` | 把 Vue 装进桌面、放行麦克风、连 Vite |
| 编排 | `server.py` | 单循环、每连接一会话、三分支分发、推指标 |
| 规则 | `session.py` | 四态状态机，注册/提取互斥，enrollment 缓冲 |
| 分离 | `tse.py` | BSRNN 整窗 2s + OLA 无缝拼接 + 声纹缓存 |
| 识别 | `asr.py` | Sherpa 流式，Transducer 靠端点 / Paraformer 靠尾部 padding |
| 降级 | `speaker_gate.py` | VAD 切段 + 声纹相似度门控，边说边判 |
| 垫片 | `wesep_loader.py` | 启动前给 `sys.modules` 打补丁，让残缺 wheel 能跑 |
| 界面 | `App.vue` | 瘦客户端：采集/灌入 + 事件分发 + 渲染 |

**整套系统的本质**：用一段注册语音当「条件」，从混合音频里**先分离出目标人波形**（TSE），再对**干净波形**做流式 ASR——把「鸡尾酒会问题」从识别器身上卸下来。代价是 2 秒整窗的固有延迟，这是这类频域分离模型在 CPU 上的硬限制。

---

## 附录 A：为什么「开始提取」后有 ~3 秒延迟

> 这是初次阅读代码时最容易困惑的现象：注册阶段感觉「瞬间完成」，但点完「开始提取」后，要等一下才开始出字幕。根因不是 bug，是 BSRNN 整窗分离的固有代价。

### 根本原因：与注册无关，是「整窗分离」要攒满 2 秒

感觉到的「等一下」其实由**两段独立延迟叠加**而成，都发生在「提取」阶段。

#### 延迟①:点击「开始提取」那一刻的后端初始化（几百 ms ~ 1 秒）

前端流程（`App.vue:459-463`）：

```ts
sendCommand('startExtraction')
await waitForState('extracting')   // ← 卡在这里,等后端准备好
startFileStream(...)               // 准备好了才开始喂文件
```

后端收到 `startExtraction` 后，在回 `state=extracting` **之前**要做这些（`server.py:209-214` → `tse.py:58-80`）：

1. `load_model_local()` —— 加载 WeSep BSRNN 权重到内存；
2. `_compute_enroll_embedding()` —— 用 ECAPA 把注册语音**算成说话人向量**，缓存起来当分离条件；
3. `set_num_threads`、第一次 PyTorch 推理的预热开销。

这一步是**首次加载模型 + 首次推理**，CPU 上通常几百毫秒到 1 秒。这段就是「点完按钮、文件进度条迟迟不动」的那一下。

#### 延迟②:必须攒满一个 2 秒窗口，才出第一段音频（实打实的 ~2 秒）← 主要原因

BSRNN 是**非因果、整窗模型**：必须凑满一整个窗口才能做一次分离推理。窗口写死在 `tse.py:13`：

```python
WINDOW_SECONDS = 2      # = 32000 个采样
```

看 `accept_pcm16` 的累积逻辑（`tse.py:188-200`）：

```python
self._in_buf = np.concatenate([self._in_buf, samples])   # 先往缓冲里攒
outputs = []
while len(self._in_buf) >= self._window_samples:         # ← 攒满 32000(2秒)才进
    ...分离、产出 target 音频...
    outputs.append(...)
return outputs      # 没攒满时,返回空 list
```

没攒满 2 秒，返回**空列表**，ASR 收不到分离音频，自然出不了字幕。而文件模式又是按**真实速率**喂的（`App.vue:364`，每 128ms 一帧），所以「攒满 2 秒」需要约 **2 秒墙钟时间**——这就是「进度条已经在动、字幕却迟迟不出来」的那一段。

#### 还有 ASR 自己的一点尾巴

第一段目标音频终于出来后，送进 ASR，第一个字还要等一下：

- **Paraformer** 需要 **0.66 秒尾部静音 padding** 才能把尾部上下文冲出来（`asr.py:53`）；
- **Zipformer** 靠端点检测切句，也要等到一个停顿。

### 加起来 ≈ 2~3 秒

```
点「开始提取」
   │
   ├─ 延迟① 模型加载 + 算注册嵌入(ECAPA)   ~0.5–1 s   (waitForState 等的就是它)
   │
   └─ 延迟② 攒满 2 秒 BSRNN 窗口            ~2 s      (文件已按真实速率在喂)
            │
            └─ 第一段分离音频 → ASR → 首字    (+端点/padding)
```

正好对应 UI 里写的「约 3 秒缓冲」。这是**可验证效果的 CPU 实验模式，不是低延迟真流式**。

### 为什么注册阶段没这种感觉

注册阶段（`session.py:40`）只是把音频**原样累加进 `enrollment` 缓冲**，全程不做识别、不做模型推理，所以注册完是「瞬间」的（本质是存了一段 bytes）。延迟全在提取阶段才暴露。

### 一句话总结

**不是 bug，是 BSRNN 整窗分离的固有代价**——点提取时要先花 ~1 秒加载模型 + 算声纹，然后必须再等 2 秒攒满第一个窗口，才出得了第一个字。

要压低这个延迟，方向是算法层：把 2 秒整窗换成有状态分块 / 因果推理（SpEx+、SpeakerBeam 之类），或退到 `passthrough` / `speaker_gate` 模式（门控路径按帧处理，首字快很多，代价是不做真正的波形分离）。
