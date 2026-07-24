#!/usr/bin/env python3
"""Synthesize small synthetic voice-like fixtures for offline TSE testing.

Real microphones are not always available, so this generates two *distinct*
"speakers" (different pitch + vowel spectrum + syllable rhythm) and writes:
  samples/enroll_target.wav   ~3.2 s of the target speaker alone  (registration)
  samples/mixed.wav           target + other speaker overlapping  (the "live" feed)
  samples/target_clean.wav    target alone, same length as the mix (reference)
  samples/other_clean.wav     other speaker alone                  (reference)

These are voiced buzzes, NOT real speech: the pipeline (decode -> stream ->
TSE -> ASR) runs end to end, but Chinese ASR will not transcribe anything
meaningful. Drop in real recordings through the UI's file-input mode for a
proper quality test.

Run:  python scripts/make-test-audio.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import scipy.signal as ss
import soundfile as sf

SR = 16_000
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "samples"

# (formant freq Hz, bandwidth Hz). Two clearly different vowels/voices.
SPEAKER_A = {"f0": 120.0, "rate": 3.5, "formants": [(730, 130), (1090, 130), (2440, 140)], "vowel": "a"}  # target
SPEAKER_B = {"f0": 210.0, "rate": 4.2, "formants": [(290, 110), (2300, 200), (3000, 260)], "vowel": "i"}  # other


def _glottal_source(f0_env: np.ndarray) -> np.ndarray:
    """Impulse train with an instantaneously varying f0 (rich harmonic spectrum)."""
    n = len(f0_env)
    src = np.zeros(n)
    i = 0
    while i < n:
        src[i] += 1.0
        i += int(round(SR / max(f0_env[i], 50.0)))
    return src


def _formant_cascade(src: np.ndarray, formants: list[tuple[float, float]]) -> np.ndarray:
    out = src
    for freq, bw in formants:
        r = np.exp(-np.pi * bw / SR)
        a = [1.0, -2.0 * r * np.cos(2.0 * np.pi * freq / SR), r * r]
        out = ss.lfilter([1.0], a, out)
    return out


def _syllable_envelope(n: int, rate: float, seed: int) -> np.ndarray:
    """Gaussian syllable nuclei ~`rate` per second with jitter -> speech rhythm."""
    rng = np.random.default_rng(seed)
    env = np.zeros(n)
    spacing = SR / rate
    center = rng.uniform(0, spacing)
    width = SR * 0.06  # 60 ms nucleus
    while center < n:
        lo = np.arange(max(0, int(center - 3 * width)), min(n, int(center + 3 * width)))
        env[lo] += np.exp(-((lo - center) ** 2) / (2 * width * width))
        center += spacing * rng.uniform(0.8, 1.25)
    return np.tanh(env)


def synth(spec: dict, dur: float, seed: int) -> np.ndarray:
    n = int(SR * dur)
    rng = np.random.default_rng(seed)
    # gentle intonation so it is not a pure monotone
    f0_env = spec["f0"] * (1.0 + 0.03 * np.sin(2.0 * np.pi * 0.7 * np.arange(n) / SR))
    src = _glottal_source(f0_env) + 0.04 * rng.standard_normal(n)
    vow = _formant_cascade(src, spec["formants"])
    peak = np.max(np.abs(vow)) + 1e-9
    return (vow / peak) * _syllable_envelope(n, spec["rate"], seed)


def _write(name: str, sig: np.ndarray) -> None:
    sig = np.tanh(sig)  # soft-clip
    sf.write(OUT / name, sig.astype(np.float32), SR, subtype="PCM_16")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    mix_dur = 6.0

    target_clean = synth(SPEAKER_A, mix_dur, seed=1)
    other_clean = synth(SPEAKER_B, mix_dur, seed=2)
    length = min(len(target_clean), len(other_clean))
    target_clean, other_clean = target_clean[:length], other_clean[:length]

    mixed = target_clean * 0.62 + other_clean * 0.58
    mixed = mixed / (np.max(np.abs(mixed)) + 1e-9) * 0.92
    target_clean = target_clean / (np.max(np.abs(target_clean)) + 1e-9) * 0.92
    other_clean = other_clean / (np.max(np.abs(other_clean)) + 1e-9) * 0.92

    enroll = synth(SPEAKER_A, 3.2, seed=10)
    enroll = enroll / (np.max(np.abs(enroll)) + 1e-9) * 0.92

    _write("enroll_target.wav", enroll)
    _write("mixed.wav", mixed)
    _write("target_clean.wav", target_clean)
    _write("other_clean.wav", other_clean)

    print(f"Wrote synthetic test audio into {OUT.relative_to(ROOT)}:")
    for name in ("enroll_target.wav", "mixed.wav", "target_clean.wav", "other_clean.wav"):
        secs = sf.info(OUT / name).frames / SR
        print(f"  {name:<20} {secs:.1f} s  16 kHz mono PCM16")


if __name__ == "__main__":
    main()
