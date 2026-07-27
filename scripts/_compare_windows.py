"""Extract mixed.wav with different TSE window lengths and save each result as a
wav, so you can A/B-listen how window length (3s training default vs 1s) affects
separation quality. Run with conda env + PYTHONPATH=backend/.

Outputs samples/out_tse_<w>s.wav next to the inputs.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

import audio_tse.tse as tsemod
from audio_tse.tse import BufferedWeSepTse, SAMPLE_RATE, TseModel

ROOT = Path(__file__).resolve().parents[1]
TSE_MODEL = TseModel("wesep_bsrnn", "cmp", ROOT / "models" / "wesep-bsrnn-ecapa-vox1")


def pcm16(name: str) -> bytes:
    data, _sr = sf.read(ROOT / "samples" / name, dtype="int16")
    if data.ndim > 1:
        data = data[:, 0]
    return data.astype("<i2").tobytes()


def extract(window_s: float, enroll: bytes, mix: bytes) -> np.ndarray:
    tsemod.WINDOW_SECONDS = window_s
    tse = BufferedWeSepTse(TSE_MODEL, enroll)
    out = b"".join(tse.accept_pcm16(mix)) + b"".join(tse.flush())
    return np.frombuffer(out, dtype="<i2").astype(np.float32) / 32768.0


def main() -> None:
    if not TSE_MODEL.available:
        raise SystemExit(f"TSE not available: {TSE_MODEL.unavailable_reason}")
    enroll = pcm16("enroll_target.wav")
    mix = pcm16("mixed.wav")
    print(f"enroll {len(enroll)//2/SAMPLE_RATE:.1f}s, mix {len(mix)//2/SAMPLE_RATE:.1f}s")
    for w in (3.0, 2.0, 1.0):
        s = extract(w, enroll, mix)
        out = ROOT / "samples" / f"out_tse_{int(w)}s.wav"
        sf.write(out, s, SAMPLE_RATE)
        print(f"  window={w:.0f}s -> {out.name}  ({len(s)/SAMPLE_RATE:.2f}s)")


if __name__ == "__main__":
    main()
