"""Parity check: the cached-enroll forward path must match wesep's end-to-end
BSRNN.forward numerically. Compares raw (pre-output-norm) speaker output for the
same mix+enrollment. Run with PYTHONPATH pointing at backend/.
"""
from __future__ import annotations

from pathlib import Path

import soundfile as sf
import torch

from audio_tse.tse import BufferedWeSepTse, SAMPLE_RATE, TseModel

ROOT = Path(__file__).resolve().parents[1]
TSE_MODEL = TseModel("wesep_bsrnn", "cache", ROOT / "models" / "wesep-bsrnn-ecapa-vox1")


def pcm16(name: str) -> bytes:
    data, _sr = sf.read(ROOT / "samples" / name, dtype="int16")
    if data.ndim > 1:
        data = data[:, 0]
    return data.astype("<i2").tobytes()


def main() -> None:
    if not TSE_MODEL.available:
        raise SystemExit(f"TSE not available: {TSE_MODEL.unavailable_reason}")

    tse = BufferedWeSepTse(TSE_MODEL, pcm16("enroll_target.wav"))
    tse._extractor.output_norm = False  # compare raw output, before normalization

    mix = pcm16("mixed.wav")[: SAMPLE_RATE * 2 * 2]  # 2 seconds
    mix_t = tse._to_tensor(mix).to(tse._device)

    # original end-to-end forward (recomputes enroll embedding inside)
    fbank = tse._extractor.compute_fbank(
        tse._enrollment.to(tse._device), sample_rate=SAMPLE_RATE, cmn=True
    ).unsqueeze(0)
    with torch.no_grad():
        s_orig, _label = tse._extractor.model(mix_t, fbank)

    # cached path (enroll embedding precomputed once in __init__)
    with torch.no_grad():
        s_cached = tse._forward_cached(mix_t)

    diff = (s_orig - s_cached).abs().max().item()
    rel = diff / (s_orig.abs().max().item() + 1e-8)
    print(f"shapes orig={tuple(s_orig.shape)} cached={tuple(s_cached.shape)}")
    print(f"max abs diff = {diff:.3e}   relative = {rel:.3e}")
    if diff < 1e-4:
        print("CACHE_OK: cached forward matches end-to-end forward")
    else:
        print("CACHE_MISMATCH")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
