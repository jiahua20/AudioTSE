from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np


SAMPLE_RATE = 16_000
# paraformer/transducer streaming decode is largely autoregressive; benchmarking
# showed more threads make it slower, so keep this small.
_ASR_THREADS = 2


@dataclass(frozen=True)
class AsrModel:
    id: str
    name: str
    model_dir: Path
    kind: str

    @property
    def available(self) -> bool:
        if self.kind == "paraformer":
            required = ["tokens.txt", "encoder.int8.onnx", "decoder.int8.onnx"]
        else:
            required = [
                "tokens.txt",
                "encoder-epoch-99-avg-1.int8.onnx",
                "decoder-epoch-99-avg-1.onnx",
                "joiner-epoch-99-avg-1.int8.onnx",
            ]
        return all((self.model_dir / filename).exists() for filename in required)


class StreamingAsr:
    def __init__(self, model: AsrModel) -> None:
        import sherpa_onnx

        self.model = model
        if model.kind == "paraformer":
            self._recognizer = sherpa_onnx.OnlineRecognizer.from_paraformer(
                tokens=str(model.model_dir / "tokens.txt"),
                encoder=str(model.model_dir / "encoder.int8.onnx"),
                decoder=str(model.model_dir / "decoder.int8.onnx"),
                num_threads=_ASR_THREADS,
                sample_rate=SAMPLE_RATE,
                feature_dim=80,
                decoding_method="greedy_search",
                provider="cpu",
            )
            self._tail_padding = 0.66
        else:
            self._recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                tokens=str(model.model_dir / "tokens.txt"),
                encoder=str(model.model_dir / "encoder-epoch-99-avg-1.int8.onnx"),
                decoder=str(model.model_dir / "decoder-epoch-99-avg-1.onnx"),
                joiner=str(model.model_dir / "joiner-epoch-99-avg-1.int8.onnx"),
                num_threads=_ASR_THREADS,
                sample_rate=SAMPLE_RATE,
                feature_dim=80,
                decoding_method="greedy_search",
                enable_endpoint_detection=True,
                provider="cpu",
            )
            self._tail_padding = 0.0
        self._stream = self.create_stream()
        self.last_feed_ms = 0.0

    def create_stream(self):
        return self._recognizer.create_stream()

    def feed(self, stream, samples: np.ndarray) -> str:
        samples = np.ascontiguousarray(samples, dtype=np.float32)
        stream.accept_waveform(SAMPLE_RATE, samples)
        while self._recognizer.is_ready(stream):
            self._recognizer.decode_stream(stream)
        return self.result(stream)

    def result(self, stream) -> str:
        return self._recognizer.get_result(stream).strip()

    def finish(self, stream) -> str:
        if self._tail_padding:
            stream.accept_waveform(
                SAMPLE_RATE,
                np.zeros(int(SAMPLE_RATE * self._tail_padding), dtype=np.float32),
            )
        stream.input_finished()
        while self._recognizer.is_ready(stream):
            self._recognizer.decode_stream(stream)
        return self.result(stream)

    def accept_pcm16(self, chunk: bytes) -> tuple[str, bool]:
        samples = np.frombuffer(chunk, dtype="<i2").astype(np.float32) / 32768.0
        start = time.perf_counter()
        text = self.feed(self._stream, samples)
        self.last_feed_ms = (time.perf_counter() - start) * 1000.0
        final = self._recognizer.is_endpoint(self._stream)
        if final:
            self._recognizer.reset(self._stream)
        return text, final