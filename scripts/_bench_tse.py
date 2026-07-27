"""Benchmark WeSep TSE single-window extract cost on THIS CPU, sweeping window
length and thread count, so we can pick the smallest window that holds RTF<1.
Run with PYTHONPATH pointing at backend/.
"""
from __future__ import annotations

import os
import statistics
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

import audio_tse.tse as tsemod
from audio_tse.tse import BufferedWeSepTse, SAMPLE_RATE, TseModel

ROOT = Path(__file__).resolve().parents[1]
TSE_MODEL = TseModel("wesep_bsrnn", "bench", ROOT / "models" / "wesep-bsrnn-ecapa-vox1")


def pcm16(name: str, seconds: float) -> bytes:
    data, _sr = sf.read(ROOT / "samples" / name, dtype="int16")
    if data.ndim > 1:
        data = data[:, 0]
    need = int(SAMPLE_RATE * seconds)
    if len(data) < need:
        data = np.pad(data, (0, need - len(data)))
    return data[:need].astype("<i2").tobytes()


def bench(window_s: float, enroll: bytes, mix_seg: bytes, n: int = 5) -> tuple[float, float]:
    tsemod.WINDOW_SECONDS = window_s
    tse = BufferedWeSepTse(TSE_MODEL, enroll)
    list(tse.accept_pcm16(mix_seg))  # warmup (first run loads kernels / is slow)
    samples = []
    for _ in range(n):
        t0 = time.perf_counter()
        list(tse.accept_pcm16(mix_seg))
        samples.append((time.perf_counter() - t0) * 1000.0)
    return statistics.median(samples), tse.last_extract_ms


def main() -> None:
    if not TSE_MODEL.available:
        raise SystemExit(f"TSE not available: {TSE_MODEL.unavailable_reason}")

    enroll = pcm16("enroll_target.wav", 5.0)
    cpus = os.cpu_count() or 1
    initial = torch.get_num_threads()
    print(f"torch={torch.__version__}  cpu_count={cpus}  initial_threads={initial}")
    print(f"{'threads':>7} {'window':>6} {'median_ms':>10} {'RTF':>7}")
    for threads in sorted({initial, cpus}):
        torch.set_num_threads(threads)
        for w in (3.0, 2.5, 2.0, 1.5, 1.0):
            med, _last = bench(w, enroll, pcm16("mixed.wav", w))
            print(f"{threads:>7} {w:>6.1f} {med:>10.0f} {med / (w * 1000.0):>7.2f}")


if __name__ == "__main__":
    main()
