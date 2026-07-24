import asyncio
import json
from pathlib import Path

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from .asr import AsrModel, StreamingAsr
from .session import AudioSession, SessionError, SessionState
from .speaker_gate import SpeakerGate
from .tse import BufferedWeSepTse, TseModel


ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = ROOT / "models"
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
VAD_MODEL = MODELS_DIR / "silero_vad" / "silero_vad.onnx"
SPEAKER_MODEL = (
    MODELS_DIR
    / "sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k"
    / "model.onnx"
)
TSE_MODEL = TseModel(
    "wesep_bsrnn",
    "WeSep BSRNN 纯音频 TSE（实验）",
    MODELS_DIR / "wesep-bsrnn-ecapa-vox1",
)


async def send(websocket: ServerConnection, event: str, **payload: object) -> None:
    await websocket.send(json.dumps({"event": event, **payload}, ensure_ascii=False))


def model_options() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    asr_models = [
        {"id": model.id, "name": model.name, "available": model.available}
        for model in ASR_MODELS.values()
    ]
    gate_available = VAD_MODEL.exists() and SPEAKER_MODEL.exists()
    processors = [
        {
            "id": "tse",
            "name": "纯音频 TSE（实验）",
            "description": "WeSep BSRNN + 注册语音；约 3 秒缓冲，英语训练权重，中文效果待验证",
            "available": TSE_MODEL.available,
            "reason": TSE_MODEL.unavailable_reason,
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
            "available": True,
        },
    ]
    return asr_models, processors


async def handle(websocket: ServerConnection) -> None:
    session = AudioSession()
    selected_asr = "paraformer" if ASR_MODELS["paraformer"].available else "zipformer"
    selected_processor = (
        "tse"
        if TSE_MODEL.available
        else "speaker_gate" if VAD_MODEL.exists() and SPEAKER_MODEL.exists() else "passthrough"
    )
    asr: StreamingAsr | None = None
    gate: SpeakerGate | None = None
    tse: BufferedWeSepTse | None = None
    asr_models, processors = model_options()
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
            "纯音频 TSE 已就绪（3 秒缓冲实验模式）"
            if TSE_MODEL.available
            else f"纯音频 TSE 尚未就绪：{TSE_MODEL.unavailable_reason}；当前使用降级模式"
        ),
    )
    try:
        async for message in websocket:
            try:
                if isinstance(message, bytes):
                    session.accept_pcm16(message)
                    if session.state == SessionState.EXTRACTING and tse and asr:
                        for target_pcm16 in tse.accept_pcm16(message):
                            text, final = asr.accept_pcm16(target_pcm16)
                            await send(websocket, "transcript", text=text, final=final)
                    elif session.state == SessionState.EXTRACTING and gate:
                        for transcript in gate.accept_pcm16(message):
                            await send(websocket, "transcript", **transcript)
                    elif session.state == SessionState.EXTRACTING and asr:
                        text, final = asr.accept_pcm16(message)
                        await send(websocket, "transcript", text=text, final=final)
                    continue
                payload = json.loads(message)
                command = payload.get("command")
                if command == "startEnrollment":
                    session.start_enrollment()
                elif command == "finishEnrollment":
                    session.finish_enrollment()
                elif command == "setModels":
                    if session.state in (SessionState.ENROLLING, SessionState.EXTRACTING):
                        raise SessionError("请先停止当前操作再切换模型")
                    requested_asr = str(payload.get("asrModel", ""))
                    requested_processor = str(payload.get("processor", ""))
                    if requested_asr not in ASR_MODELS or not ASR_MODELS[requested_asr].available:
                        raise SessionError("所选 ASR 模型尚未安装")
                    available_processors = {item["id"] for item in processors if item["available"]}
                    if requested_processor not in available_processors:
                        raise SessionError("所选处理模型尚未安装")
                    selected_asr = requested_asr
                    selected_processor = requested_processor
                    asr = None
                    gate = None
                    tse = None
                    await send(
                        websocket,
                        "modelsChanged",
                        selectedAsr=selected_asr,
                        selectedProcessor=selected_processor,
                        bypassEnabled=selected_processor == "passthrough",
                    )
                elif command == "startExtraction":
                    model = ASR_MODELS[selected_asr]
                    if not model.available:
                        raise SessionError("所选 ASR 模型尚未安装")
                    asr = StreamingAsr(model)
                    gate = None
                    tse = None
                    if selected_processor == "speaker_gate":
                        gate = SpeakerGate(asr, VAD_MODEL, SPEAKER_MODEL)
                        gate.enroll_pcm16(bytes(session.enrollment))
                    elif selected_processor == "tse":
                        try:
                            tse = BufferedWeSepTse(TSE_MODEL, bytes(session.enrollment))
                        except Exception as error:
                            raise SessionError(f"WeSep TSE 加载失败：{error}") from error
                    session.start_extraction()
                elif command == "stopExtraction":
                    session.stop_extraction()
                    asr = None
                    gate = None
                    tse = None
                else:
                    raise SessionError("未知命令")
                await send(websocket, "state", state=session.state.value,
                           enrollmentSeconds=round(session.enrollment_seconds, 1))
            except (SessionError, json.JSONDecodeError) as error:
                await send(
                    websocket,
                    "error",
                    message=str(error),
                    state=session.state.value,
                    selectedAsr=selected_asr,
                    selectedProcessor=selected_processor,
                )
    except ConnectionClosed:
        pass


async def main() -> None:
    async with serve(handle, "127.0.0.1", 8765, max_size=2**20):
        print("AudioTSE backend: ws://127.0.0.1:8765", flush=True)
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass