from __future__ import annotations

import importlib.util
import os
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np


SAMPLE_RATE = 16_000
WINDOW_SECONDS = 3


@dataclass(frozen=True)
class TseModel:
    id: str
    name: str
    directory: Path

    @property
    def model_files_ready(self) -> bool:
        return all((self.directory / filename).exists() for filename in ("config.yaml", "avg_model.pt"))

    @property
    def dependency_ready(self) -> bool:
        return importlib.util.find_spec("torch") is not None and importlib.util.find_spec("wesep") is not None

    @property
    def available(self) -> bool:
        return self.model_files_ready and self.dependency_ready

    @property
    def unavailable_reason(self) -> str | None:
        if not self.model_files_ready:
            return "缺少 WeSep BSRNN 权重；运行 scripts/install-wesep-tse.ps1 安装"
        if not self.dependency_ready:
            return "缺少 PyTorch/WeSep；运行 scripts/install-wesep-tse.ps1 安装"
        return None


class BufferedWeSepTse:
    """Experimental audio-reference TSE using non-causal fixed windows."""

    def __init__(self, model: TseModel, enrollment_pcm16: bytes) -> None:
        if not model.available:
            raise RuntimeError(model.unavailable_reason or "WeSep TSE 不可用")

        from .wesep_loader import ensure_wesep_runtime

        ensure_wesep_runtime()  # make `import wesep` work on a pristine install

        import torch
        from wesep import load_model_local

        self._torch = torch
        # torch defaults to half the logical cores; BSRNN extract is compute-bound
        # and benchmarks ~2x faster (RTF 1.42 -> 0.74 on a 3s window) using all of them.
        self._torch.set_num_threads(os.cpu_count() or 4)
        self._extractor = load_model_local(str(model.directory))
        self._extractor.set_device("cpu")
        self._extractor.set_resample_rate(SAMPLE_RATE)
        self._extractor.set_vad(False)
        self._extractor.set_output_norm(True)
        self._enrollment = self._to_tensor(enrollment_pcm16)
        self._buffer = bytearray()
        self._window_bytes = int(SAMPLE_RATE * WINDOW_SECONDS * 2)
        self.last_extract_ms = 0.0

    @property
    def buffered_seconds(self) -> float:
        return len(self._buffer) / (SAMPLE_RATE * 2)

    def _to_tensor(self, pcm16: bytes):
        samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0
        return self._torch.from_numpy(samples).unsqueeze(0)

    def accept_pcm16(self, chunk: bytes) -> list[bytes]:
        self._buffer.extend(chunk)
        outputs: list[bytes] = []
        while len(self._buffer) >= self._window_bytes:
            window = bytes(self._buffer[:self._window_bytes])
            del self._buffer[:self._window_bytes]
            start = time.perf_counter()
            outputs.append(self._extract(window))
            self.last_extract_ms = (time.perf_counter() - start) * 1000.0
        return outputs

    def _extract(self, pcm16: bytes) -> bytes:
        target = self._extractor.extract_speech_from_pcm(
            self._to_tensor(pcm16),
            SAMPLE_RATE,
            self._enrollment,
            SAMPLE_RATE,
        )
        samples = target.detach().cpu().numpy().reshape(-1)
        samples = np.nan_to_num(samples, nan=0.0, posinf=1.0, neginf=-1.0)
        return (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()