"""Verify the overlap-add framework reconstructs unity when the per-window
"separation" is the identity. This checks the analysis/synthesis window + hop
satisfy COLA, independent of the BSRNN model (fast, no torch load).

Run with conda env + PYTHONPATH=backend/.
"""
from __future__ import annotations

import numpy as np

from audio_tse.tse import CROSSFADE_SECONDS, SAMPLE_RATE, WINDOW_SECONDS


def run_ola(mix: np.ndarray, extract) -> np.ndarray:
    w = int(SAMPLE_RATE * WINDOW_SECONDS)
    cf = max(1, int(SAMPLE_RATE * CROSSFADE_SECONDS))
    hop = w - cf
    ramp = np.linspace(0.0, 1.0, cf, endpoint=False, dtype=np.float32)
    win = np.ones(w, dtype=np.float32)
    win[:cf] = ramp
    win[-cf:] = 1.0 - ramp
    in_buf = mix.copy()
    out_overlap = np.array([], dtype=np.float32)
    outs = []
    while len(in_buf) >= w:
        seg = extract(in_buf[:w].copy()) * win
        if len(out_overlap) < w:
            out_overlap = np.concatenate([out_overlap, np.zeros(w - len(out_overlap), np.float32)])
        out_overlap[:w] += seg
        outs.append(out_overlap[:hop].copy())
        out_overlap = out_overlap[hop:]
        in_buf = in_buf[hop:]
    return np.concatenate(outs) if outs else np.array([], dtype=np.float32)


def main() -> None:
    # 5s of multi-tone signal; identity separation must reconstruct it exactly
    # (modulo the leading crossfade-in).
    t = np.arange(SAMPLE_RATE * 5) / SAMPLE_RATE
    mix = (0.5 * np.sin(2 * np.pi * 440 * t) + 0.3 * np.sin(2 * np.pi * 880 * t)).astype(np.float32)
    out = run_ola(mix, lambda s: s.copy())
    cf = max(1, int(SAMPLE_RATE * CROSSFADE_SECONDS))
    # skip the leading crossfade-in (ramp starts at 0) and compare the steady span
    a, b = cf, min(len(out), len(mix))
    seg = min(len(out), len(mix)) - cf - int(SAMPLE_RATE * WINDOW_SECONDS)
    diff = np.abs(out[a:a + seg] - mix[a:a + seg]).max() if seg > 0 else float("nan")
    print(f"W={int(SAMPLE_RATE*WINDOW_SECONDS)} cf={cf} hop={int(SAMPLE_RATE*WINDOW_SECONDS)-cf} "
          f"out={len(out)} mix={len(mix)} compared={seg}")
    print(f"steady max abs diff = {diff:.3e}")
    if diff < 1e-5:
        print("OLA_OK: overlap-add reconstructs identity (COLA satisfied)")
    else:
        print("OLA_MISMATCH")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
