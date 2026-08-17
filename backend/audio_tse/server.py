# 后端入口：一个单连接单会话的 WebSocket 服务器（ws://127.0.0.1:8765）。
# 职责：协议编排 —— 接收前端发来的命令（JSON 文本）与音频帧（二进制），
# 按「纯音频 TSE / 声纹门控 / 原音直通」三条链路之一处理音频，
# 把分离音频（二进制）、转写（transcript 事件）和性能指标（metrics 事件）推回前端。
import asyncio
import json
import time
from pathlib import Path

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from .asr import AsrModel, StreamingAsr
from .session import AudioSession, SessionError, SessionState
from .speaker_gate import SpeakerGate
from .tse import (
    CROSSFADE_SECONDS,
    WINDOW_SECONDS,
    BufferedWeSepTse,
    TseModel,
    WeSepTseEngine,
)


# 项目根目录（backend/ 的上一级），模型统一放在 <root>/models/
ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = ROOT / "models"
# 可选的两套流式中文 ASR：Zipformer 轻量、Paraformer 中英双语更准
ASR_MODELS = {
    "zipformer": AsrModel(
        "zipformer",
        "Zipformer 14M 中文",
        MODELS_DIR / "sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23",
        "transducer",
    ),
    "paraformer": AsrModel(
        "paraformer",
        "Paraformer 中英双语",
        MODELS_DIR / "sherpa-onnx-streaming-paraformer-bilingual-zh-en",
        "paraformer",
    ),
}
# 声纹门控 / TSE 静音跳过共用的 Silero VAD 模型文件
VAD_MODEL = MODELS_DIR / "silero_vad" / "silero_vad.onnx"
# 声纹门控用的说话人嵌入模型（3D-Speaker ER2Net，中文）
SPEAKER_MODEL = (
    MODELS_DIR
    / "sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k"
    / "model.onnx"
)
# 实验性纯音频 TSE：WeSep BSRNN + ECAPA，权重约 262 MB（需单独安装）
TSE_MODEL = TseModel(
    "wesep_bsrnn",
    "WeSep BSRNN 纯音频 TSE（实验）",
    MODELS_DIR / "wesep-bsrnn-ecapa-vox1",
)


async def send(websocket: ServerConnection, event: str, **payload: object) -> None:
    """把 {event: 名字, ...载荷} 序列化成一条 JSON 文本消息发回前端。"""
    await websocket.send(json.dumps({"event": event, **payload}, ensure_ascii=False))


def model_options() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """构造 hello 事件里的两个选项列表：ASR 模型列表 + 处理链路列表。

    每项带 available 标志（缺模型/缺依赖时前端置灰按钮）。"""
    asr_models = [
        {"id": model.id, "name": model.name, "available": model.available}
        for model in ASR_MODELS.values()
    ]
    # 声纹门控同时依赖 VAD 和说话人模型，两者都得在
    gate_available = VAD_MODEL.exists() and SPEAKER_MODEL.exists()
    processors = [
        {
            "id": "tse",
            "name": "纯音频 TSE（实验）",
            "description": "WeSep BSRNN + 注册语音；约 1.7 秒缓冲，英语训练权重，中文效果待验证",
            "available": TSE_MODEL.available,
            "reason": TSE_MODEL.unavailable_reason,  # 不可用时的提示（如何安装）
        },
        {
            "id": "speaker_gate",
            "name": "声纹门控（降级）",
            "description": "适合轮流说话，重叠语音不能分离",
            "available": gate_available,
        },
        {
            "id": "passthrough",
            "name": "原音直通（诊断）",
            "description": "不区分说话人，用于比较 ASR 效果",
            "available": True,  # 直通只依赖 ASR，永远可选
        },
    ]
    return asr_models, processors


# 一个 WebSocket 二进制帧 == 2048 个 int16 采样 == 128 ms 音频（与前端采集块一致）
FRAME_MS = (2048 / 16_000) * 1000.0
# 一次 TSE hop == 窗长减去交叉淡入淡出（0.9 s）；RTF 以此为分母
HOP_MS = (WINDOW_SECONDS - CROSSFADE_SECONDS) * 1000.0


def rtf_for(processor: str, tse_ms: float, asr_ms: float, gate_ms: float) -> float | None:
    """Real-Time Factor（实时因子）= 处理时间 / 音频时间。小于 1 表示能跟上实时音频。

    分母按链路取值：TSE 每 hop(0.9s) 处理一次，门控/直通每帧(128ms) 处理一次；
    没有任何处理耗时数据时返回 None（前端显示「—」）。"""
    if processor == "tse":
        return (tse_ms + asr_ms) / HOP_MS if (tse_ms or asr_ms) else None
    # 门控链路的瓶颈是 VAD+声纹（ASR 已包含在 gate 内部）；直通只有 ASR
    cost = gate_ms if processor == "speaker_gate" else asr_ms
    return cost / FRAME_MS if cost else None


def metrics_payload(rt: dict, tse, asr, processor: str) -> dict:
    """组装 metrics 事件：墙钟/音频时长、各阶段耗时、积压与丢弃量、RTF、首字延时。"""
    now = time.perf_counter()
    tse_ms = tse.last_extract_ms if tse else 0.0   # 最近一次 BSRNN 单窗分离耗时
    asr_ms = asr.last_feed_ms if asr else 0.0      # 最近一次 ASR 喂入耗时
    e2e = None
    # 端到端首字延时 = 从 startExtraction 到第一条转写出现的墙钟差
    if rt["first_text"] is not None:
        e2e = (rt["first_text"] - rt["start"]) * 1000.0
    rtf = rtf_for(processor, tse_ms, asr_ms, rt["gate_ms"])
    return {
        "processor": processor,                              # 当前链路
        "wallSec": round(now - rt["start"], 2),              # 提取已运行的墙钟秒数
        "audioSec": round(rt["audio"], 2),                   # 已喂入的音频秒数
        "tseMs": round(tse_ms, 1) if tse and tse_ms else None,
        "asrMs": round(asr_ms, 1) if asr and asr_ms else None,
        "backlogSec": round(tse.buffered_seconds, 2) if tse else 0.0,  # TSE 内部积压
        "droppedSec": round(tse.dropped_seconds, 2) if tse else 0.0,   # 保实时丢掉的音频
        "silentWindows": tse.silent_windows if tse else 0,   # 静音直通跳过 BSRNN 的窗口数
        "rtf": round(rtf, 3) if rtf is not None else None,
        "e2eFirstMs": round(e2e, 0) if e2e is not None else None,
    }


async def handle(websocket: ServerConnection) -> None:
    """单个 WebSocket 连接的完整生命周期：每个连接一套独立的会话/模型实例。"""
    session = AudioSession()  # 本连接的状态机 + 注册音频缓冲
    # rt：本轮提取的实时统计（start 起点时间、首字时间、累计音频秒数、上次 metrics 发送时刻、门控耗时）
    rt = {"start": 0.0, "first_text": None, "audio": 0.0, "last_send": 0.0, "gate_ms": 0.0}
    # 连接建立时的默认选择：优先 Paraformer；TSE 可用选 TSE，否则声纹门控，最后直通
    selected_asr = "paraformer" if ASR_MODELS["paraformer"].available else "zipformer"
    selected_processor = (
        "tse"
        if TSE_MODEL.available
        else "speaker_gate" if VAD_MODEL.exists() and SPEAKER_MODEL.exists() else "passthrough"
    )
    asr: StreamingAsr | None = None            # 当前活跃的 ASR（startExtraction 时创建）
    gate: SpeakerGate | None = None            # 声纹门控链路实例（同上）
    tse: BufferedWeSepTse | None = None        # TSE 链路实例（同上）
    # 会话级共享引擎：BSRNN 权重（约 262 MB）只加载一次，之后换人 / 停止
    # 再提取都复用，省掉每次 startExtraction 重新 load_model_local 的开销。
    tse_engine: WeSepTseEngine | None = None
    tse_engine_task: asyncio.Task[WeSepTseEngine] | None = None  # 进行中的后台加载任务
    bg_tasks: set[asyncio.Task] = set()        # 后台任务集合（强引用，防止被 GC）

    async def ensure_tse_engine() -> WeSepTseEngine:
        """确保分离引擎已（或正在）加载并返回就绪实例。
        连上后由 kick_tse_preload 后台预加载；startExtraction 时若还没好就等它收尾。"""
        nonlocal tse_engine, tse_engine_task
        if tse_engine is not None:
            return tse_engine  # 已加载完成，直接复用
        if tse_engine_task is None:
            # 权重加载约 5 秒且是 CPU 密集，丢线程池跑，别卡事件循环
            tse_engine_task = asyncio.create_task(asyncio.to_thread(WeSepTseEngine, TSE_MODEL))
        try:
            tse_engine = await tse_engine_task
        except Exception:
            tse_engine_task = None  # 失败则允许下次重试
            raise
        return tse_engine

    def kick_tse_preload() -> None:
        """后台预加载分离引擎 + 预热一次前向，完成后通知前端启用「开始注册」。
        幂等：引擎已加载则立即通知，正在加载则等它收尾。这样把权重加载那约 5 秒
        从「点开始提取」挪到「连上服务 / 切到 TSE 之后」的空闲时段。"""
        async def _run() -> None:
            ready, reason = True, ""
            try:
                engine = await ensure_tse_engine()
                try:
                    await asyncio.to_thread(engine.warmup)  # 预热首次推理
                except Exception:
                    pass  # 预热失败不影响可用性，首次真实提取时再付那点代价
            except Exception as error:
                ready, reason = False, str(error)  # 加载失败：把原因带回给前端
            try:
                await send(websocket, "tseEngineReady", ready=ready,
                           **({"reason": reason} if reason else {}))
            except Exception:
                pass  # 连接可能已断开

        task = asyncio.create_task(_run())
        bg_tasks.add(task)                     # 持引用
        task.add_done_callback(bg_tasks.discard)  # 完成后自动移除

    asr_models, processors = model_options()
    # 连接握手：告知前端模型可用性、选项列表与默认选择
    await send(
        websocket,
        "hello",
        asrReady=ASR_MODELS[selected_asr].available,
        tseReady=TSE_MODEL.available,
        bypassEnabled=selected_processor == "passthrough",
        asrModels=asr_models,
        processors=processors,
        selectedAsr=selected_asr,
        selectedProcessor=selected_processor,
        message=(
            "纯音频 TSE 正在后台加载分离模型（约 5 秒），完成后即可注册"
            if TSE_MODEL.available
            else f"纯音频 TSE 尚未就绪：{TSE_MODEL.unavailable_reason}；当前使用降级模式"
        ),
    )
    # TSE 是主路径：连上就后台预加载分离引擎，让「开始注册」按钮在引擎就绪前
    # 保持禁用。这样点「开始提取」时引擎已热，不再卡在权重加载那约 5 秒上。
    if selected_processor == "tse":
        kick_tse_preload()
    try:
        # 主循环：逐条处理前端消息（文本 = 命令 JSON，二进制 = 音频帧）
        async for message in websocket:
            try:
                if isinstance(message, bytes):
                    # ---- 音频帧分支 ----
                    try:
                        session.accept_pcm16(message)  # 注册中则存入 enrollment
                    except SessionError:
                        pass  # idle/ready 期的音频帧:前端状态机无法与后端完美同步,静默丢弃而非刷 error
                    if session.state == SessionState.EXTRACTING:
                        rt["audio"] += len(message) / 32_000.0  # 累计音频秒数（32000 字节/秒）
                        text_emitted = False
                        if tse and asr:
                            # 链路 1：纯音频 TSE。音频进 OLA 缓冲，凑满 1 秒窗就
                            # 分离一次；输出的目标 PCM 既回传前端播放、又喂 ASR 出字幕
                            for target_pcm16 in await asyncio.to_thread(tse.accept_pcm16, message):
                                await websocket.send(target_pcm16)  # 二进制帧 = 分离音频
                                text, final = await asyncio.to_thread(asr.accept_pcm16, target_pcm16)
                                await send(websocket, "transcript", text=text, final=final)
                                text_emitted = text_emitted or bool(text)
                        elif gate:
                            # 链路 2：声纹门控。VAD 切段 + 声纹判定，只把「像目标人」
                            # 的段喂 ASR；返回增量转写事件 + 放行的音频块
                            g_start = time.perf_counter()
                            events, audio_chunks = await asyncio.to_thread(gate.accept_pcm16, message)
                            for pcm in audio_chunks:
                                await websocket.send(pcm)  # 放行的音频回传播放
                            for tr in events:
                                await send(websocket, "transcript", **tr)
                                text_emitted = text_emitted or bool(tr.get("text"))
                            rt["gate_ms"] = (time.perf_counter() - g_start) * 1000.0
                        elif asr:
                            # 链路 3：原音直通。不筛选，原帧回传 + 直接识别
                            await websocket.send(message)  # 原音直通：回传原始帧供前端播放（听实时性）
                            text, final = await asyncio.to_thread(asr.accept_pcm16, message)
                            await send(websocket, "transcript", text=text, final=final)
                            text_emitted = text_emitted or bool(text)
                        if text_emitted and rt["first_text"] is None:
                            rt["first_text"] = time.perf_counter()  # 记录首字时刻（算首字延时）
                        now = time.perf_counter()
                        # metrics 节流至每 0.4 s 一条，避免刷爆前端
                        if now - rt["last_send"] >= 0.4:
                            rt["last_send"] = now
                            await send(websocket, "metrics",
                                       **metrics_payload(rt, tse, asr, selected_processor))
                    continue  # 音频帧处理完，回到循环头等下一条消息
                # ---- 文本命令分支 ----
                payload = json.loads(message)
                command = payload.get("command")
                if command == "startEnrollment":
                    # 开始注册：清空旧注册音频，进入 ENROLLING
                    session.start_enrollment()
                elif command == "finishEnrollment":
                    # 结束注册：校验时长（≥3 s），通过则进入 READY
                    session.finish_enrollment()
                elif command == "setModels":
                    # 切换 ASR / 处理链路。进行中不允许切换，先停再说
                    if session.state in (SessionState.ENROLLING, SessionState.EXTRACTING):
                        raise SessionError("请先停止当前操作再切换模型")
                    requested_asr = str(payload.get("asrModel", ""))
                    requested_processor = str(payload.get("processor", ""))
                    # 校验：ASR 必须是已知且已安装的
                    if requested_asr not in ASR_MODELS or not ASR_MODELS[requested_asr].available:
                        raise SessionError("所选 ASR 模型尚未安装")
                    # 校验：链路必须在 hello 里报告过 available
                    available_processors = {item["id"] for item in processors if item["available"]}
                    if requested_processor not in available_processors:
                        raise SessionError("所选处理模型尚未安装")
                    selected_asr = requested_asr
                    selected_processor = requested_processor
                    asr = None   # 丢弃旧链路实例，下次 startExtraction 按新选择重建
                    gate = None
                    tse = None
                    await send(
                        websocket,
                        "modelsChanged",
                        selectedAsr=selected_asr,
                        selectedProcessor=selected_processor,
                        bypassEnabled=selected_processor == "passthrough",
                    )
                    # 切到 TSE 时也开始预加载（若之前没在跑）；引擎已加载则会立即通知就绪
                    if requested_processor == "tse":
                        kick_tse_preload()
                elif command == "startExtraction":
                    # 开始实时提取：按当前选择创建链路实例
                    model = ASR_MODELS[selected_asr]
                    if not model.available:
                        raise SessionError("所选 ASR 模型尚未安装")
                    asr = StreamingAsr(model)  # 所有链路都需要 ASR
                    gate = None
                    tse = None
                    if selected_processor == "speaker_gate":
                        # 门控链路：注册声纹来自本会话累积的 enrollment
                        gate = SpeakerGate(asr, VAD_MODEL, SPEAKER_MODEL)
                        gate.enroll_pcm16(bytes(session.enrollment))
                    elif selected_processor == "tse":
                        try:
                            # 引擎连接级预加载（见 kick_tse_preload）；这里多半已就绪，
                            # 万一还没好（注册特别快、加载还没跑完）就等它收尾。
                            engine = await ensure_tse_engine()
                            # 构造要算 ECAPA 注册嵌入(数秒),丢线程池避免阻塞事件循环
                            # (否则 websocket ping 收不到回包、连接被超时掐断)
                            tse = await asyncio.to_thread(
                                BufferedWeSepTse, engine, bytes(session.enrollment), VAD_MODEL)
                        except Exception as error:
                            raise SessionError(f"WeSep TSE 加载失败：{error}") from error
                    session.start_extraction()  # 状态机进入 EXTRACTING
                    rt.update(start=time.perf_counter(), first_text=None,  # 重置本轮指标
                              audio=0.0, last_send=0.0, gate_ms=0.0)
                elif command == "stopExtraction":
                    # 停止提取：状态机回 READY（注册声纹保留，可再开）
                    session.stop_extraction()
                    if tse and asr:
                        # 排空 TSE 不足 1 秒的尾部窗口，使尾部音频不被
                        # 丢弃，随后刷新 ASR 的尾部上下文
                        for target_pcm16 in await asyncio.to_thread(tse.flush):
                            await websocket.send(target_pcm16)
                            text, final = await asyncio.to_thread(asr.accept_pcm16, target_pcm16)
                            await send(websocket, "transcript", text=text, final=final)
                        # 收尾：ASR 补尾部静音/冲刷解码，出最后一句 final 文本
                        tail = await asyncio.to_thread(asr.finish, asr._stream)
                        if tail:
                            await send(websocket, "transcript", text=tail, final=True)
                    asr = None   # 释放本轮链路实例（引擎保留，供下次复用）
                    gate = None
                    tse = None
                else:
                    raise SessionError("未知命令")
                # 每条命令处理完都同步一次状态给前端（状态机值 + 注册时长）
                await send(websocket, "state", state=session.state.value,
                           enrollmentSeconds=round(session.enrollment_seconds, 1))
            except (SessionError, json.JSONDecodeError) as error:
                # 业务错误：发 error 事件（带当前状态），连接保持不断
                await send(
                    websocket,
                    "error",
                    message=str(error),
                    state=session.state.value,
                    selectedAsr=selected_asr,
                    selectedProcessor=selected_processor,
                )
    except ConnectionClosed:
        pass  # 前端断开：会话对象随连接一起销毁，无需额外清理


async def main() -> None:
    # 只监听本机回环地址：这个服务只给本机桌面端用，不对外
    # max_size=1 MiB：允许最大的单条消息（音频帧远小于此，留裕量）
    async with serve(handle, "127.0.0.1", 8765, max_size=2**20):
        print("AudioTSE backend: ws://127.0.0.1:8765", flush=True)
        await asyncio.Future()  # 永不完成的 Future：服务一直跑到进程退出


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass  # Ctrl+C 正常退出
