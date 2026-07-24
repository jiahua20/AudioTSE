from __future__ import annotations

from pathlib import Path

import numpy as np

from .asr import SAMPLE_RATE, StreamingAsr


MIN_DECISION_SECONDS = 0.6
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

    def accept_pcm16(self, chunk: bytes) -> list[dict[str, object]]:
        samples = np.frombuffer(chunk, dtype="<i2").astype(np.float32) / 32768.0
        self._vad.accept_waveform(np.ascontiguousarray(samples))
        speech = self._vad.is_speech_detected()
        if speech and not self._speech_active:
            self._begin_utterance(samples)
            return self._emit_partial()
        if speech and self._speech_active:
            self._chunks.append(samples)
            self._asr.feed(self._stream, samples)
            return self._emit_partial()
        if not speech and self._speech_active:
            self._asr.feed(self._stream, samples)
            return self._finish_utterance()
        return []

    def _begin_utterance(self, samples: np.ndarray) -> None:
        self._speech_active = True
        self._stream = self._asr.create_stream()
        self._chunks = [samples]
        self._accepted = self._enrollment is None
        self._last_partial = ""
        self._last_decision_samples = 0
        self._asr.feed(self._stream, samples)

    def _emit_partial(self) -> list[dict[str, object]]:
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

    def _finish_utterance(self) -> list[dict[str, object]]:
        text = self._asr.finish(self._stream)
        similarity = self._similarity() if self._enrollment is not None and text else 1.0
        accepted = bool(text) and similarity >= self._threshold
        self._reset_utterance()
        events: list[dict[str, object]] = []
        if accepted:
            events.append({"text": text, "final": True, "similarity": round(similarity, 3)})
        events.append({"text": "", "final": False, "similarity": None})
        return events

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