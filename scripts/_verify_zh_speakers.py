"""One-off: embed every candidate Chinese clip with the backend's 3D-Speaker
ER2Net (zh) model and report pairwise cosine similarity, so we can pick a
target + other pair that are genuinely DIFFERENT speakers for the TSE mix.

Run:  python scripts/_verify_zh_speakers.py
"""
from __future__ import annotations

import sys
from itertools import combinations
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from audio_tse.asr import SAMPLE_RATE  # noqa: E402
from audio_tse.speaker_gate import SpeakerEmbedder  # noqa: E402

SPK = (
    ROOT
    / "models"
    / "sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k"
    / "model.onnx"
)

RAW = ROOT / "samples" / "_raw"
PARA = ROOT / "models" / "sherpa-onnx-streaming-paraformer-bilingual-zh-en" / "test_wavs"
ZIP = ROOT / "models" / "sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23" / "test_wavs"

CANDIDATES = {
    "lei-jun": RAW / "lei-jun-test.wav",
    "zh": RAW / "zh.wav",
    "int16-zh": RAW / "int16-1-channel-zh.wav",
    "itn-num": RAW / "itn-zh-number.wav",
    "para-0": PARA / "0.wav",
    "para-1": PARA / "1.wav",
    "para-2": PARA / "2.wav",
    "para-3": PARA / "3.wav",
    "zip-0": ZIP / "0.wav",
    "zip-1": ZIP / "1.wav",
}


def load_mono16k(p: Path, max_sec: float = 12.0) -> np.ndarray:
    data, sr = sf.read(str(p), dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != SAMPLE_RATE:
        data = resample_poly(data, SAMPLE_RATE, sr)
    return np.ascontiguousarray(data[: int(SAMPLE_RATE * max_sec)], dtype=np.float32)


def main() -> None:
    emb = SpeakerEmbedder(SPK)
    vecs: dict[str, np.ndarray] = {}
    for name, p in CANDIDATES.items():
        if not p.exists():
            print(f"  {name:9s} MISSING {p}")
            continue
        s = load_mono16k(p)
        dur = len(s) / SAMPLE_RATE
        if dur < 1.5:
            print(f"  {name:9s} {dur:.1f}s  too short, skip")
            continue
        v = emb.embed(s)
        vecs[name] = v / (np.linalg.norm(v) + 1e-8)
        print(f"  {name:9s} {dur:5.1f}s  ok")

    names = list(vecs)
    print("\npairwise cosine (lower = more distinct):")
    print("            " + "".join(f"{n:>9s}" for n in names))
    for a in names:
        print(f"  {a:9s} " + "".join(f"{float(np.dot(vecs[a], vecs[b])):9.2f}" for b in names))

    best = min(((a, b, float(np.dot(vecs[a], vecs[b]))) for a, b in combinations(names, 2)),
               key=lambda t: t[2])
    print(f"\nMOST DISTINCT pair: {best[0]} vs {best[1]}  cosine={best[2]:.3f}")


if __name__ == "__main__":
    main()
