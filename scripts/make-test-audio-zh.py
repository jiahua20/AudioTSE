#!/usr/bin/env python3
"""Build REAL Chinese test fixtures for the file-source UI.

Uses genuinely different speakers (verified with the backend's 3D-Speaker
ER2Net zh model):
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

SR = 16_000                                   # 目标采样率
ROOT = Path(__file__).resolve().parents[1]    # 项目根
OUT = ROOT / "samples"                        # 输出目录
RAW = ROOT / "samples" / "_raw"               # 原始素材缓存目录
PARA = (
    ROOT
    / "models"
    / "sherpa-onnx-streaming-paraformer-bilingual-zh-en"
    / "test_wavs"
)

LEIJUN_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/lei-jun-test.wav"
OTHER_SRC = PARA / "3.wav"              # distinct speaker; ships with the paraformer model

ENROLL_DUR = 5.0    # 注册音频时长（秒）
MIX_DUR = 8.0       # 混合音频时长（秒）
TARGET_GAIN = 0.70  # 混音中目标人的增益
OTHER_GAIN = 0.60   # 混音中另一个人的增益


def _ensure_leijun() -> Path:
    """The target clip is not committed; fetch it from the sherpa-onnx release."""
    p = RAW / "lei-jun-test.wav"
    if p.exists() and p.stat().st_size > 1_000_000:  # 已有且大小合理 → 复用
        return p
    RAW.mkdir(parents=True, exist_ok=True)
    print("downloading lei-jun-test.wav from the sherpa-onnx release ...")
    urllib.request.urlretrieve(LEIJUN_URL, p)
    return p


def load_mono16k(p: Path) -> np.ndarray:
    # 任意 wav → float32 单声道 16 kHz（多声道取平均，采样率不符则重采样）
    data, sr = sf.read(str(p), dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != SR:
        data = resample_poly(data, SR, sr)
    return np.ascontiguousarray(data, dtype=np.float32)


def rms(x: np.ndarray) -> float:
    # 均方根能量：衡量一段音频的响度
    return float(np.sqrt(np.mean(x * x) + 1e-12))


def best_segment(x: np.ndarray, dur: float,
                 avoid: tuple[int, int] | None = None) -> tuple[int, np.ndarray]:
    """Highest-RMS `dur`-s window. Optionally skip windows that overlap `avoid`
    (start, end sample) by more than 30%, so the mix target differs from enroll."""
    n = int(SR * dur)                      # 目标段的采样数
    if len(x) <= n:                        # 整条音频都不够长 → 全用 + 补零
        return 0, np.pad(x, (0, max(0, n - len(x))))
    step = max(1, int(SR * 0.2))           # 每 0.2 s 滑动一次搜索窗
    best_i, best_e = 0, -1.0
    for i in range(0, len(x) - n + 1, step):
        if avoid is not None:              # 与注册段重叠 >30% 的窗口跳过
            ov = min(i + n, avoid[1]) - max(i, avoid[0])
            if ov > 0.3 * n:
                continue
        e = rms(x[i:i + n])                # 能量最高的窗 = 内容最饱满的段
        if e > best_e:
            best_e, best_i = e, i
    return best_i, x[best_i:best_i + n]


def norm(x: np.ndarray, peak: float = 0.92) -> np.ndarray:
    # 峰值归一化到 0.92（留一点余量防削波）
    return x / (np.max(np.abs(x)) + 1e-9) * peak


def write(name: str, x: np.ndarray) -> None:
    # 写 16 kHz PCM16 wav 并打印时长
    sf.write(OUT / name, x.astype(np.float32), SR, subtype="PCM_16")
    print(f"  {name:<20} {len(x) / SR:5.1f}s  {SR} Hz mono PCM16")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    target_src = _ensure_leijun()          # 目标人原始音频（雷军演讲片段）
    if not OTHER_SRC.exists():             # 另一人素材随 paraformer 模型附带
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

    # 注册段：目标人最响的 5 秒
    enr_off, enroll = best_segment(target, ENROLL_DUR)
    enroll = norm(enroll)

    # 混合段：目标人再选 8 秒（避开注册段用过的内容），另一人也选最响 8 秒
    mix_t_off, mix_target = best_segment(
        target, MIX_DUR, avoid=(enr_off, enr_off + int(SR * ENROLL_DUR))
    )
    _, mix_other = best_segment(other, MIX_DUR)

    # 线性叠加成混合信号（两人不同增益），再各自存干净参考
    mixed = norm(mix_target) * TARGET_GAIN + norm(mix_other) * OTHER_GAIN
    mixed = norm(mixed)
    target_clean = norm(mix_target)
    other_clean = norm(mix_other)

    write("enroll_target.wav", enroll)   # 注册用：目标人独唱
    write("mixed.wav", mixed)            # 提取用：两人重叠
    write("target_clean.wav", target_clean)  # 评估用：混合段里目标人的干净版
    write("other_clean.wav", other_clean)    # 评估用：另一人的干净版
    print(f"\nWrote real-Chinese test audio into {OUT.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
