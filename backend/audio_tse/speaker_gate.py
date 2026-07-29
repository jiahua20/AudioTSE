from __future__ import annotations

from pathlib import Path

import numpy as np

from .asr import SAMPLE_RATE, StreamingAsr


# 累计语音达到该时长后，才开始做声纹相似度判定
MIN_DECISION_SECONDS = 0.6
# 相邻两次增量判定之间至少要间隔的语音时长
DECISION_INTERVAL_SECONDS = 0.3


class SpeakerEmbedder:
    def __init__(self, model_path: Path) -> None:
        import sherpa_onnx

        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(model_path),
            num_threads=2,
            provider="cpu",
        )
        self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)

    def embed(self, samples: np.ndarray) -> np.ndarray:
        stream = self._extractor.create_stream()
        stream.accept_waveform(SAMPLE_RATE, np.ascontiguousarray(samples, dtype=np.float32))
        return np.asarray(self._extractor.compute(stream), dtype=np.float32)

    @staticmethod
    def cosine(left: np.ndarray, right: np.ndarray) -> float:
        denominator = np.linalg.norm(left) * np.linalg.norm(right)
        return float(np.dot(left, right) / denominator) if denominator > 1e-8 else 0.0


class SpeakerGate:
    """基于 VAD + 声纹相似度的目标说话人门控。

    用 silero VAD 切分语音段，对每段提取声纹 embedding 并与注册声纹比对，
    仅放行相似度达标的语音进入下游 ASR。
    """

    def __init__(self, asr: StreamingAsr, vad_model: Path, speaker_model: Path, threshold: float = 0.5) -> None:
        import sherpa_onnx

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
        self._embedder = SpeakerEmbedder(speaker_model)
        self._asr = asr
        self._threshold = threshold
        self._enrollment: np.ndarray | None = None
        self._reset_utterance()

    def enroll_pcm16(self, audio: bytes) -> None:
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
        self._vad.accept_waveform(np.ascontiguousarray(samples))
        speech = self._vad.is_speech_detected()
        was_accepted = self._accepted
        if speech and not self._speech_active:
            self._begin_utterance(samples)
            events = self._emit_partial()
        elif speech and self._speech_active:
            self._chunks.append(samples)
            self._asr.feed(self._stream, samples)
            events = self._emit_partial()
        elif not speech and self._speech_active:
            self._asr.feed(self._stream, samples)
            events, _ = self._finish_utterance()  # 音频已流式发完，句末不再补
        else:
            events = []
        # 接受的语音原样回传：首次翻为接受时先补发已累积的前缀，之后逐帧流式
        if self._accepted and self._speech_active:
            if not was_accepted and self._chunks:
                wav = np.concatenate(self._chunks)
                audio = [(np.clip(wav, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()]
            else:
                audio = [chunk]
        else:
            audio = []
        return events, audio

    def _begin_utterance(self, samples: np.ndarray) -> None:
        self._speech_active = True
        self._stream = self._asr.create_stream()
        self._chunks = [samples]
        self._accepted = self._enrollment is None
        self._last_partial = ""
        self._last_decision_samples = 0
        self._asr.feed(self._stream, samples)

    def _emit_partial(self) -> list[dict[str, object]]:
        """输出增量识别结果；累计语音达到最短判定时长后，按固定间隔做声纹相似度判定。"""
        total = sum(len(chunk) for chunk in self._chunks)
        similarity: float | None = None
        if not self._accepted and total >= int(MIN_DECISION_SECONDS * SAMPLE_RATE) and (
            self._last_decision_samples == 0
            or total - self._last_decision_samples >= int(DECISION_INTERVAL_SECONDS * SAMPLE_RATE)
        ):
            similarity = self._similarity()
            self._last_decision_samples = total
            self._accepted = similarity >= self._threshold
        if not self._accepted:
            return []
        text = self._asr.result(self._stream)
        if not text or text == self._last_partial:
            return []
        self._last_partial = text
        return [{"text": text, "final": False, "similarity": similarity}]

    def _finish_utterance(self) -> tuple[list[dict[str, object]], list[bytes]]:
        """语音段结束：做最终相似度判定，决定放行或丢弃并复位段内状态。
        音频已在接受时流式回传（见 accept_pcm16），这里只回事件。"""
        text = self._asr.finish(self._stream)
        similarity = self._similarity() if self._enrollment is not None and text else 1.0
        accepted = bool(text) and similarity >= self._threshold
        self._reset_utterance()
        events: list[dict[str, object]] = []
        if accepted:
            events.append({"text": text, "final": True, "similarity": round(similarity, 3)})
        events.append({"text": "", "final": False, "similarity": None})
        return events, []

    def _similarity(self) -> float:
        embedding = self._embedder.embed(np.concatenate(self._chunks))
        return SpeakerEmbedder.cosine(embedding, self._enrollment) if self._enrollment is not None else 1.0

    def _reset_utterance(self) -> None:
        self._speech_active = False
        self._stream = None
        self._chunks: list[np.ndarray] = []
        self._accepted = False
        self._last_partial = ""
        self._last_decision_samples = 0