# 声纹门控（降级链路）：不做真正的声源分离，只做「说话人筛选」。
# VAD 切出语音段 → 对每段算声纹嵌入 → 与注册声纹比相似度 →
# 达标的段才送 ASR 并回传播放；不达标的整段静音。
# 局限：多人同时说话（重叠语音）时无法分离，只能整段放行或拒绝。
from __future__ import annotations

from pathlib import Path

import numpy as np

from .asr import SAMPLE_RATE, StreamingAsr


# 累计语音达到该时长后，才开始做声纹相似度判定
MIN_DECISION_SECONDS = 0.6
# 相邻两次增量判定之间至少要间隔的语音时长
DECISION_INTERVAL_SECONDS = 0.3


class SpeakerEmbedder:
    """说话人嵌入提取器：3D-Speaker ER2Net，音频片段 → 定长声纹向量。"""

    def __init__(self, model_path: Path) -> None:
        import sherpa_onnx

        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(model_path),
            num_threads=2,       # 小模型，两线程足够
            provider="cpu",
        )
        self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)

    def embed(self, samples: np.ndarray) -> np.ndarray:
        # 一段音频 → 一个声纹向量（L2 范数未归一，相似度计算时再处理）
        stream = self._extractor.create_stream()
        stream.accept_waveform(SAMPLE_RATE, np.ascontiguousarray(samples, dtype=np.float32))
        return np.asarray(self._extractor.compute(stream), dtype=np.float32)

    @staticmethod
    def cosine(left: np.ndarray, right: np.ndarray) -> float:
        # 余弦相似度：两个声纹向量方向的接近程度，范围 [-1, 1]
        denominator = np.linalg.norm(left) * np.linalg.norm(right)
        return float(np.dot(left, right) / denominator) if denominator > 1e-8 else 0.0


class SpeakerGate:
    """基于 VAD + 声纹相似度的目标说话人门控。

    用 silero VAD 切分语音段，对每段提取声纹 embedding 并与注册声纹比对，
    仅放行相似度达标的语音进入下游 ASR。
    """

    def __init__(self, asr: StreamingAsr, vad_model: Path, speaker_model: Path, threshold: float = 0.5) -> None:
        import sherpa_onnx

        # 静音 VAD：用于切分语音段（与 TSE 里的 VAD 参数略有不同——
        # 这里 min_silence 稍长，避免一句话中间的小停顿被切成两段）
        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(vad_model)
        config.silero_vad.threshold = 0.5
        config.silero_vad.min_silence_duration = 0.3
        config.silero_vad.min_speech_duration = 0.1
        config.silero_vad.max_speech_duration = 15.0
        config.sample_rate = SAMPLE_RATE
        config.provider = "cpu"
        config.num_threads = 1
        self._vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=30)
        self._embedder = SpeakerEmbedder(speaker_model)  # 声纹提取器
        self._asr = asr                                  # 下游流式 ASR（每段一个 stream）
        self._threshold = threshold                      # 放行所需的相似度阈值
        self._enrollment: np.ndarray | None = None       # 注册声纹（L2 归一化后的）
        self._reset_utterance()                          # 初始化段内状态

    def enroll_pcm16(self, audio: bytes) -> None:
        # 注册：整段注册音频 → 归一化声纹向量，之后每段语音都和它比
        samples = np.frombuffer(audio, dtype="<i2").astype(np.float32) / 32768.0
        embedding = self._embedder.embed(samples)
        self._enrollment = embedding / (np.linalg.norm(embedding) + 1e-8)

    def accept_pcm16(self, chunk: bytes) -> tuple[list[dict[str, object]], list[bytes]]:
        """喂入一段 PCM16 音频，驱动 VAD 状态机，返回 (转写事件, 可回放音频)。

        可回放音频 = 「门控接受」的语音：本段累计相似度一旦判定为接受（约 0.6 秒
        预热后），先把此前已累积的整段（前缀）一次性补发，之后每个接受的帧原样
        流式回传。这样听到的是完整的接受语音，且只在判定通过的段发声——便于和
        TSE/直通横向对比实时性；被拒绝的整段始终静音。
        """
        samples = np.frombuffer(chunk, dtype="<i2").astype(np.float32) / 32768.0
        self._vad.accept_waveform(np.ascontiguousarray(samples))  # 驱动 VAD 状态机
        speech = self._vad.is_speech_detected()  # 当前是否处于语音段内
        was_accepted = self._accepted           # 记住进入本帧前的接受状态（用于补发前缀）
        if speech and not self._speech_active:
            # 语音段开始：新开一段（新 ASR 流 + 清空段内状态）
            self._begin_utterance(samples)
            events = self._emit_partial()
        elif speech and self._speech_active:
            # 语音段进行中：累积采样、喂 ASR、出增量转写
            self._chunks.append(samples)
            self._asr.feed(self._stream, samples)
            events = self._emit_partial()
        elif not speech and self._speech_active:
            # 语音段结束：把静音尾部喂给 ASR（帮助 endpoint 判定），随后收尾
            self._asr.feed(self._stream, samples)
            events, _ = self._finish_utterance()  # 音频已流式发完，句末不再补
        else:
            events = []  # 纯静音：什么都不做
        # 接受的语音原样回传：首次翻为接受时先补发已累积的前缀，之后逐帧流式
        if self._accepted and self._speech_active:
            if not was_accepted and self._chunks:
                # 刚翻为接受：把此前累积的整段一次性补发（此前按未定/拒绝处理没发声）
                wav = np.concatenate(self._chunks)
                audio = [(np.clip(wav, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()]
            else:
                audio = [chunk]  # 已在接受状态：本帧原样转发
        else:
            audio = []  # 未接受 / 已结束：静音（不回传）
        return events, audio

    def _begin_utterance(self, samples: np.ndarray) -> None:
        # 新语音段：开一个新的 ASR 流（与上一段的上下文隔离）
        self._speech_active = True
        self._stream = self._asr.create_stream()
        self._chunks = [samples]                    # 段内音频帧列表（供声纹判定/补发）
        self._accepted = self._enrollment is None   # 未注册则不筛，全接受
        self._last_partial = ""                     # 上次发出的增量文本（去重用）
        self._last_decision_samples = 0             # 上次做声纹判定时的累计采样数
        self._asr.feed(self._stream, samples)       # 首帧也进 ASR

    def _emit_partial(self) -> list[dict[str, object]]:
        """输出增量识别结果；累计语音达到最短判定时长后，按固定间隔做声纹相似度判定。"""
        total = sum(len(chunk) for chunk in self._chunks)  # 本段累计采样数
        similarity: float | None = None
        # 判定条件：累计 ≥0.6 s 才首次判定；之后每多 0.3 s 语音可再判一次
        # （未接受时段继续判，一旦接受就不再反复判，避免抖动）
        if not self._accepted and total >= int(MIN_DECISION_SECONDS * SAMPLE_RATE) and (
            self._last_decision_samples == 0
            or total - self._last_decision_samples >= int(DECISION_INTERVAL_SECONDS * SAMPLE_RATE)
        ):
            similarity = self._similarity()
            self._last_decision_samples = total
            self._accepted = similarity >= self._threshold  # 达标即锁定为接受
        if not self._accepted:
            return []  # 尚未通过判定：不出文本（等定了再一次性出）
        text = self._asr.result(self._stream)
        if not text or text == self._last_partial:
            return []  # 无新文本：不重复发
        self._last_partial = text
        return [{"text": text, "final": False, "similarity": similarity}]

    def _finish_utterance(self) -> tuple[list[dict[str, object]], list[bytes]]:
        """语音段结束：做最终相似度判定，决定放行或丢弃并复位段内状态。
        音频已在接受时流式回传（见 accept_pcm16），这里只回事件。"""
        text = self._asr.finish(self._stream)  # 冲刷 ASR 流拿最终文本
        # 段末最终判定（整段语音比前缀更可靠）；无注册或无文本则免判
        similarity = self._similarity() if self._enrollment is not None and text else 1.0
        accepted = bool(text) and similarity >= self._threshold
        self._reset_utterance()  # 复位段内状态，等待下一段
        events: list[dict[str, object]] = []
        if accepted:
            events.append({"text": text, "final": True, "similarity": round(similarity, 3)})
        # 无论接受与否都发一条空 partial：通知前端清掉未定稿的气泡
        events.append({"text": "", "final": False, "similarity": None})
        return events, []

    def _similarity(self) -> float:
        # 整段累计语音 → 声纹 → 与注册声纹的余弦相似度
        embedding = self._embedder.embed(np.concatenate(self._chunks))
        return SpeakerEmbedder.cosine(embedding, self._enrollment) if self._enrollment is not None else 1.0

    def _reset_utterance(self) -> None:
        # 段内状态全部复位（下一段从零开始）
        self._speech_active = False
        self._stream = None
        self._chunks: list[np.ndarray] = []
        self._accepted = False
        self._last_partial = ""
        self._last_decision_samples = 0
