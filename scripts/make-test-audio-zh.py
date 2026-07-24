#!/usr/bin/env python3
"""Build REAL Chinese test fixtures for the file-source UI.

Uses genuinely different speakers (verified with the backend's 3D-Speaker
ER2Net zh model; see scripts/_verify_zh_speakers.py):
  * target = lei-jun-test.wav  (Lei Jun -- cosine <= 0.14 to every other clip)
  * other  = paraformer test_wavs/3.wav  (cosine 0.09 to lei-jun)

Produces, all 16 kHz mono PCM16, overwriting the synthetic samples so the
documented file names are real speech:
  samples/enroll_target.wav   ~5 s of the target alone            (registration)
  samples/mixed.wav           target + other overlapping, ~8 s     (the "live" feed)
  samples/target_clean.wav    target segment used in the mix       (reference)
  samples/other_clean.wav     other segment used in the mix        (reference)

Run:  python scripts/make-test-audio-zh.py
"""
from __future__ import annotations

from pathlib import Path
import urllib.request

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

SR = 16_000
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "samples"
RAW = ROOT / "samples" / "_raw"
PARA = (
    ROOT
    / "models"
    / "sherpa-onnx-streaming-paraformer-bilingual-zh-en"
    / "test_wavs"
)

LEIJUN_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/lei-jun-test.wav"
OTHER_SRC = PARA / "3.wav"              # distinct speaker; ships with the paraformer model

ENROLL_DUR = 5.0
MIX_DUR = 8.0
TARGET_GAIN = 0.70
OTHER_GAIN = 0.60


def _ensure_leijun() -> Path:
    """The target clip is not committed; fetch it from the sherpa-onnx release."""
    p = RAW / "lei-jun-test.wav"
    if p.exists() and p.stat().st_size > 1_000_000:
        return p
    RAW.mkdir(parents=True, exist_ok=True)
    print("downloading lei-jun-test.wav from the sherpa-onnx release ...")
    urllib.request.urlretrieve(LEIJUN_URL, p)
    return p


def load_mono16k(p: Path) -> np.ndarray:
    data, sr = sf.read(str(p), dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != SR:
        data = resample_poly(data, SR, sr)
    return np.ascontiguousarray(data, dtype=np.float32)


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x * x) + 1e-12))


def best_segment(x: np.ndarray, dur: float,
                 avoid: tuple[int, int] | None = None) -> tuple[int, np.ndarray]:
    """Highest-RMS `dur`-s window. Optionally skip windows that overlap `avoid`
    (start, end sample) by more than 30%, so the mix target differs from enroll."""
    n = int(SR * dur)
    if len(x) <= n:
        return 0, np.pad(x, (0, max(0, n - len(x))))
    step = max(1, int(SR * 0.2))
    best_i, best_e = 0, -1.0
    for i in range(0, len(x) - n + 1, step):
        if avoid is not None:
            ov = min(i + n, avoid[1]) - max(i, avoid[0])
            if ov > 0.3 * n:
                continue
        e = rms(x[i:i + n])
        if e > best_e:
            best_e, best_i = e, i
    return best_i, x[best_i:best_i + n]


def norm(x: np.ndarray, peak: float = 0.92) -> np.ndarray:
    return x / (np.max(np.abs(x)) + 1e-9) * peak


def write(name: str, x: np.ndarray) -> None:
    sf.write(OUT / name, x.astype(np.float32), SR, subtype="PCM_16")
    print(f"  {name:<20} {len(x) / SR:5.1f}s  {SR} Hz mono PCM16")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    target_src = _ensure_leijun()
    if not OTHER_SRC.exists():
        raise SystemExit(
            "Missing other-speaker clip. Run the model setup first so the "
            "paraformer test wavs are present:\n"
            "  python scripts/download-models.py\n"
            f"  (expected {OTHER_SRC.relative_to(ROOT)})"
        )

    target = load_mono16k(target_src)
    other = load_mono16k(OTHER_SRC)
    print(f"target {target_src.name}: {len(target) / SR:.1f}s")
    print(f"other  {OTHER_SRC.name}: {len(other) / SR:.1f}s")

    enr_off, enroll = best_segment(target, ENROLL_DUR)
    enroll = norm(enroll)

    mix_t_off, mix_target = best_segment(
        target, MIX_DUR, avoid=(enr_off, enr_off + int(SR * ENROLL_DUR))
    )
    _, mix_other = best_segment(other, MIX_DUR)

    mixed = norm(mix_target) * TARGET_GAIN + norm(mix_other) * OTHER_GAIN
    mixed = norm(mixed)
    target_clean = norm(mix_target)
    other_clean = norm(mix_other)

    write("enroll_target.wav", enroll)
    write("mixed.wav", mixed)
    write("target_clean.wav", target_clean)
    write("other_clean.wav", other_clean)
    print(f"\nWrote real-Chinese test audio into {OUT.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
