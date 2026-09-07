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

SR = 16_000                                # 目标采样率
ROOT = Path(__file__).resolve().parents[1] # 项目根
OUT = ROOT / "samples"                     # 输出目录

# (formant freq Hz, bandwidth Hz). Two clearly different vowels/voices.
# 用「基频 f0 + 共振峰列表 + 音节速率」区分两个虚拟说话人：
# A 低嗓门元音 a（目标人），B 高嗓门元音 i（干扰人）
SPEAKER_A = {"f0": 120.0, "rate": 3.5, "formants": [(730, 130), (1090, 130), (2440, 140)], "vowel": "a"}  # target
SPEAKER_B = {"f0": 210.0, "rate": 4.2, "formants": [(290, 110), (2300, 200), (3000, 260)], "vowel": "i"}  # other


def _glottal_source(f0_env: np.ndarray) -> np.ndarray:
    """Impulse train with an instantaneously varying f0 (rich harmonic spectrum)."""
    # 声门脉冲串：按逐时刻基频间隔放置单位冲激，产生丰富的谐波（模拟声带振动）
    n = len(f0_env)
    src = np.zeros(n)
    i = 0
    while i < n:
        src[i] += 1.0                              # 放一个冲激
        i += int(round(SR / max(f0_env[i], 50.0)))  # 下一个冲激间隔 = 采样率/基频
    return src


def _formant_cascade(src: np.ndarray, formants: list[tuple[float, float]]) -> np.ndarray:
    # 共振峰级联：每个 (频率, 带宽) 转成一个二阶谐振器，依次滤波
    out = src
    for freq, bw in formants:
        r = np.exp(-np.pi * bw / SR)               # 极点半径由带宽决定（带宽大 → 阻尼大）
        a = [1.0, -2.0 * r * np.cos(2.0 * np.pi * freq / SR), r * r]  # 二阶 IIR 系数
        out = ss.lfilter([1.0], a, out)
    return out


def _syllable_envelope(n: int, rate: float, seed: int) -> np.ndarray:
    """Gaussian syllable nuclei ~`rate` per second with jitter -> speech rhythm."""
    # 音节包络：每秒约 rate 个高斯核，带随机抖动，模拟说话的节奏感
    rng = np.random.default_rng(seed)
    env = np.zeros(n)
    spacing = SR / rate                  # 平均音节间隔（采样数）
    center = rng.uniform(0, spacing)
    width = SR * 0.06  # 60 ms nucleus   # 每个音节核的宽度
    while center < n:
        lo = np.arange(max(0, int(center - 3 * width)), min(n, int(center + 3 * width)))
        env[lo] += np.exp(-((lo - center) ** 2) / (2 * width * width))  # 高斯核叠加
        center += spacing * rng.uniform(0.8, 1.25)  # 间隔抖动 ±20~25%
    return np.tanh(env)                  # 压缩到 (0,1)，避免叠加过响


def synth(spec: dict, dur: float, seed: int) -> np.ndarray:
    # 一段完整的合成语音：声门源 × 共振峰滤波 × 音节包络
    n = int(SR * dur)
    rng = np.random.default_rng(seed)
    # gentle intonation so it is not a pure monotone
    # 基频加 ±3% 的缓慢起伏，避免纯单调嗡嗡声
    f0_env = spec["f0"] * (1.0 + 0.03 * np.sin(2.0 * np.pi * 0.7 * np.arange(n) / SR))
    src = _glottal_source(f0_env) + 0.04 * rng.standard_normal(n)  # 声门源 + 少量噪声（气声）
    vow = _formant_cascade(src, spec["formants"])                   # 过共振峰 → 元音音色
    peak = np.max(np.abs(vow)) + 1e-9
    return (vow / peak) * _syllable_envelope(n, spec["rate"], seed)  # 峰值归一 + 音节包络


def _write(name: str, sig: np.ndarray) -> None:
    sig = np.tanh(sig)  # soft-clip        # tanh 软限幅防削波
    sf.write(OUT / name, sig.astype(np.float32), SR, subtype="PCM_16")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    mix_dur = 6.0                            # 混合音频时长

    target_clean = synth(SPEAKER_A, mix_dur, seed=1)  # 目标人单独说 6 s
    other_clean = synth(SPEAKER_B, mix_dur, seed=2)   # 另一人单独说 6 s
    length = min(len(target_clean), len(other_clean)) # 对齐长度
    target_clean, other_clean = target_clean[:length], other_clean[:length]

    mixed = target_clean * 0.62 + other_clean * 0.58   # 线性叠加成「两人同时说」
    mixed = mixed / (np.max(np.abs(mixed)) + 1e-9) * 0.92      # 各自峰值归一
    target_clean = target_clean / (np.max(np.abs(target_clean)) + 1e-9) * 0.92
    other_clean = other_clean / (np.max(np.abs(other_clean)) + 1e-9) * 0.92

    enroll = synth(SPEAKER_A, 3.2, seed=10)  # 注册音频：目标人另说 3.2 s（内容与混合段不同）
    enroll = enroll / (np.max(np.abs(enroll)) + 1e-9) * 0.92

    _write("enroll_target.wav", enroll)      # 注册用
    _write("mixed.wav", mixed)               # 提取用（模拟实时流）
    _write("target_clean.wav", target_clean) # 评估参考：混合段里目标人的干净版
    _write("other_clean.wav", other_clean)   # 评估参考：另一人的干净版

    print(f"Wrote synthetic test audio into {OUT.relative_to(ROOT)}:")
    for name in ("enroll_target.wav", "mixed.wav", "target_clean.wav", "other_clean.wav"):
        secs = sf.info(OUT / name).frames / SR
        print(f"  {name:<20} {secs:.1f} s  16 kHz mono PCM16")


if __name__ == "__main__":
    main()
