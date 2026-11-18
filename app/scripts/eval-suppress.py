#!/usr/bin/env python3
"""评估反向 TSE（消除注册目标人、保留其他人）在重叠语音上的客观效果。

使用 samples/ 下的真值测试集（make-test-audio-zh.py 生成：两人全程重叠）：
  enroll_target.wav / mixed.wav / target_clean.wav / other_clean.wav

mixed 是两次归一化后的线性叠加，混音里目标/另一人的真实贡献比例未知，
因此先用最小二乘从 (mixed, target_clean, other_clean) 反解出两者的实际
幅度，得到严格对齐的参考信号，再对各种抑制变体计算 SI-SDR。

评估的变体：
  1. 窗长扫描：1 s（=当前实时链路） vs 2/4/8 s（离线可用更长窗）
  2. 维纳后滤波：residual = y − ŝ_target 后，逐时频点做
     g = |R|²/(|R|² + β|S|²)，压制 ŝ 有能量而残差弱的时频点（目标残留）
  3. 二次提取迭代：把残差再喂一遍 TSE，减掉漏出的第二轮目标估计

用法（app/ 目录，AudioTSE 环境的 python）：
  python scripts/eval-suppress.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]    # app/
sys.path.insert(0, str(ROOT / "backend"))

SAMPLE_RATE = 16_000
TSE_DIR = ROOT / "models" / "wesep-bsrnn-ecapa-vox1"
VAD_MODEL = ROOT / "models" / "silero_vad" / "silero_vad.onnx"
SAMPLES = ROOT / "samples"
# 评估时掐掉首尾各 0.6 s：OLA 合成窗在流头/流尾有淡入淡出，且不同窗长的
# 掐头量不同——统一掐固定长度保证各变体在完全同一段音频上比较。
TRIM_SECONDS = 0.6


def pcm16(x: np.ndarray) -> bytes:
    return (np.clip(x, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def f32(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype="<i2").astype(np.float32) / 32768.0


def load(name: str) -> np.ndarray:
    data, sr = sf.read(str(SAMPLES / name), dtype="float32", always_2d=False)
    assert sr == SAMPLE_RATE, f"{name}: {sr} Hz"
    return np.ascontiguousarray(data, dtype=np.float32)


def si_sdr(est: np.ndarray, ref: np.ndarray) -> float:
    """尺度不变信噪比（dB）。对齐参考允许整体缩放（评估抑制输出 vs 另一人）。"""
    est = est - est.mean()
    ref = ref - ref.mean()
    a = float(np.dot(est, ref) / (np.dot(ref, ref) + 1e-12))
    noise = est - a * ref
    return float(10.0 * np.log10((np.dot(a * ref, a * ref) + 1e-12)
                                 / (np.dot(noise, noise) + 1e-12)))


def project(out: np.ndarray, target_ref: np.ndarray, other_ref: np.ndarray
            ) -> tuple[float, float, float]:
    """把输出投影到 (目标参考, 另一人参考) 上：out ≈ a·目标 + b·另一人 + 误差。

    返回 (a, b, 误差占比)。a 相对混音里的目标系数下降多少 = 真实抑制量；
    b 的变化 = 对另一人的损伤。"""
    mat = np.stack([target_ref, other_ref], axis=1)
    coef, *_ = np.linalg.lstsq(mat, out, rcond=None)
    resid = out - mat @ coef
    rel = float(np.sqrt(np.mean(resid * resid)) / (np.sqrt(np.mean(out * out)) + 1e-12))
    return float(coef[0]), float(coef[1]), rel


def decompose(mixed: np.ndarray, target: np.ndarray, other: np.ndarray
              ) -> tuple[np.ndarray, np.ndarray]:
    """最小二乘反解 mixed ≈ α·target + β·other，返回缩放后的两路参考。"""
    a_mat = np.stack([target, other], axis=1)
    coef, *_ = np.linalg.lstsq(a_mat, mixed, rcond=None)
    alpha, beta = float(coef[0]), float(coef[1])
    resid = mixed - (alpha * target + beta * other)
    rel = float(np.sqrt(np.mean(resid * resid)) / (np.sqrt(np.mean(mixed * mixed)) + 1e-12))
    print(f"  最小二乘分解：α(目标)={alpha:+.3f}  β(另一人)={beta:+.3f}  拟合残差 {rel * 100:.2f}%")
    return alpha * target, beta * other


class EnginePool:
    """只加载一次 BSRNN 权重，通过改写引擎上的窗口常量来切换窗长。

    （_window_samples 等是普通属性，BufferedWeSepTse 每次构造时经 property
    读取，因此改属性即可生效，无需为每个窗长重新加载 262 MB 权重。）"""

    def __init__(self) -> None:
        from audio_tse.tse import TseModel, WeSepTseEngine
        model = TseModel("wesep_bsrnn", "WeSep BSRNN", TSE_DIR)
        if not model.available:
            raise SystemExit(f"WeSep TSE 不可用：{model.unavailable_reason}")
        print("加载 WeSep BSRNN 引擎（约 5 秒）…")
        self.engine = WeSepTseEngine(model)

    def set_window(self, seconds: float, crossfade: float) -> None:
        e = self.engine
        e._window_samples = int(SAMPLE_RATE * seconds)
        e._cf_samples = max(1, int(SAMPLE_RATE * crossfade))
        e._hop_samples = e._window_samples - e._cf_samples
        ramp = np.linspace(0.0, 1.0, e._cf_samples, endpoint=False, dtype=np.float32)
        win = np.ones(e._window_samples, dtype=np.float32)
        win[: e._cf_samples] = ramp
        win[-e._cf_samples:] = 1.0 - ramp
        e._synth_win = win
        e._max_buf_samples = e._window_samples + int(SAMPLE_RATE * 0.5)


def run_tse(pool: EnginePool, enroll: np.ndarray, mix: np.ndarray,
            mode: str) -> np.ndarray:
    """整条混音过一遍流式 TSE，返回与输入等长的输出波形。"""
    from audio_tse.tse import BufferedWeSepTse
    tse = BufferedWeSepTse(pool.engine, pcm16(enroll),
                           VAD_MODEL if VAD_MODEL.exists() else None, mode)
    mb = pcm16(mix)
    outs: list[bytes] = []
    for off in range(0, len(mb), 4096):
        outs.extend(tse.accept_pcm16(mb[off:off + 4096]))
    outs.extend(tse.flush())
    out = f32(b"".join(outs))
    if len(out) < len(mix):       # 兜底对齐
        out = np.pad(out, (0, len(mix) - len(out)))
    return out[:len(mix)]


def wiener_postfilter(y: np.ndarray, s: np.ndarray, beta: float, floor: float
                      ) -> np.ndarray:
    """STFT 域维纳式后滤波：g = |R|²/(|R|² + β|S|²)，下限 floor。

    R = Y − S 是残差（其他人+目标残留），S 是目标估计。目标残留强的时频点
    （|S| 大而 |R| 小）被压低，其他人主导的时频点几乎原样通过。"""
    import torch
    n_fft, hop = 512, 128
    window = torch.hann_window(n_fft)

    def stft_(x: np.ndarray):
        return torch.stft(torch.from_numpy(np.ascontiguousarray(x, dtype=np.float32)),
                          n_fft, hop, window=window, return_complex=True)

    z, zs = stft_(y), stft_(s)
    r = z - zs
    g = torch.abs(r) ** 2 / (torch.abs(r) ** 2 + beta * torch.abs(zs) ** 2 + 1e-12)
    g = torch.clamp(g, min=floor)
    out = torch.istft(g * r, n_fft, hop, window=window, length=len(y))
    return out.numpy()


def evaluate(label: str, out: np.ndarray, mix: np.ndarray,
             other_ref: np.ndarray, target_ref: np.ndarray,
             trim: int, base_ab: tuple[float, float] | None = None) -> dict:
    o, m, r_o, r_t = out[trim:-trim], mix[trim:-trim], other_ref[trim:-trim], target_ref[trim:-trim]
    a, b, rel = project(o, r_t, r_o)
    supp = dmg = float("nan")
    if base_ab is not None:
        supp = 20.0 * np.log10(base_ab[0] / (abs(a) + 1e-12))   # 正值 = 目标被压了多少 dB
        dmg = 20.0 * np.log10(base_ab[1] / (abs(b) + 1e-12))    # 正值 = 另一人被伤了多少 dB
    res = {
        "label": label,
        "si_sdr_other": si_sdr(o, r_o),          # 越高越好：另一人保真度
        "si_sdr_target": si_sdr(o, r_t),         # 越低越好：目标残留量
        "delta": si_sdr(o, r_o) - si_sdr(m, r_o),  # 相对「不处理直接输出混音」的改善
        "a": a, "b": b, "err": rel, "supp_db": supp, "dmg_db": dmg,
    }
    print(f"  {label:<30}SI-SDR {res['si_sdr_other']:>6.2f}  目标抑制 {supp:>5.1f} dB"
          f"  另一人损伤 {dmg:>5.1f} dB  伪影 {rel * 100:>4.1f}%", flush=True)
    return res


def gain_aligned_subtract(y: np.ndarray, s: np.ndarray, sub_win: float,
                          corr_gate: float, smooth_seconds: float = 0.15
                          ) -> tuple[np.ndarray, np.ndarray]:
    """带符号增益对齐后相减：r = y − γ(t)·ŝ。

    WeSep 以 SI-SDR 损失训练，输出相对真实目标可以有任意整体极性与幅度
    （听感无差别，但对减法致命：实测 ŝ ≈ −0.19·目标）。逐子窗用最小二乘
    估计带符号增益 γ = ⟨ŝ,y⟩/⟨ŝ,ŝ⟩——ŝ 与 y 的相关部分正是目标在混音中的
    真实贡献；归一化相关低于 corr_gate 的子窗（目标没在说/估计不可信）
    置 γ=0 不减（宁可不减也不错减）。γ 曲线平滑插值到逐采样，避免块边界
    的增益跳变（可听咔嗒）。返回 (残差, 逐采样 γ)。"""
    n = len(y)
    w = max(1, int(SAMPLE_RATE * sub_win))
    centers: list[float] = []
    gammas: list[float] = []
    for i in range(0, n, w):
        ys, ss = y[i:i + w], s[i:i + w]
        centers.append(min(i + w / 2, n - 0.5))
        ss_norm = float(np.sqrt(np.dot(ss, ss)))
        if ss_norm < 1e-6 * np.sqrt(len(ss)):     # ŝ 近静音：无目标可减
            gammas.append(0.0)
            continue
        nc = float(np.dot(ys, ss) / (np.linalg.norm(ys) * ss_norm + 1e-12))
        gammas.append(float(np.dot(ys, ss) / (np.dot(ss, ss) + 1e-12))
                      if abs(nc) >= corr_gate else 0.0)
    gamma_t = np.interp(np.arange(n, dtype=np.float64), centers, gammas).astype(np.float32)
    k = max(1, int(SAMPLE_RATE * smooth_seconds)) | 1               # 奇数长滑窗
    kernel = np.ones(k, dtype=np.float32) / k
    gamma_t = np.convolve(np.pad(gamma_t, (k // 2, k // 2), mode="edge"),
                          kernel, mode="valid").astype(np.float32)
    return (y - gamma_t * s).astype(np.float32), gamma_t


def main() -> None:
    enroll = load("enroll_target.wav")
    mix = load("mixed.wav")
    target_clean = load("target_clean.wav")
    other_clean = load("other_clean.wav")
    print(f"混音 {len(mix) / SAMPLE_RATE:.1f}s（两人全程重叠）")
    target_ref, other_ref = decompose(mix, target_clean, other_clean)

    pool = EnginePool()
    trim = int(SAMPLE_RATE * TRIM_SECONDS)
    a0, b0, _ = project(mix[trim:-trim], target_ref[trim:-trim], other_ref[trim:-trim])
    base_ab = (a0, b0)
    t0 = time.perf_counter()

    # 基线：不做任何处理
    results = [evaluate("基线：直接输出混音", mix, mix, other_ref, target_ref, trim, base_ab)]

    # 窗长扫描：先看单人估计 ŝ 本身的提取质量（这是减法效果的上限），
    # 再看减法输出。混合两步在一个循环里做。
    est_by_window: dict[float, np.ndarray] = {}
    for win, cf in ((1.0, 0.1), (2.0, 0.25), (4.0, 0.5)):
        pool.set_window(win, cf)
        s = run_tse(pool, enroll, mix, "extract")
        est_by_window[win] = s
        p, q, _ = project(s[trim:-trim], target_ref[trim:-trim], other_ref[trim:-trim])
        print(f"  [窗 {win:.0f}s] ŝ 提取质量：SI-SDR(ŝ,目标) {si_sdr(s[trim:-trim], target_ref[trim:-trim]):+.2f} dB"
              f"  SI-SDR(ŝ,另一人) {si_sdr(s[trim:-trim], other_ref[trim:-trim]):+.2f} dB"
              f"  投影 (目标 {p:+.3f}, 另一人 {q:+.3f})", flush=True)
        results.append(evaluate(f"wesep 减法  窗 {win:.0f}s", mix - s, mix,
                                other_ref, target_ref, trim, base_ab))

    # 多相位集成：同一窗长、错开窗口相位各跑一遍，平均后减——不同相位的
    # 估计误差部分去相关，平均能降低伪影（代价是 RTF×3）
    pool.set_window(1.0, 0.1)
    pad = pool.engine._hop_samples // 2
    s_a = run_tse(pool, enroll, mix, "extract")
    s_b = run_tse(pool, enroll, np.concatenate([np.zeros(pad, np.float32), mix]), "extract")[pad:]
    s_ens = (s_a[:len(s_b)] + s_b[:len(s_a)]) / 2.0
    results.append(evaluate("wesep 减法  双相位集成", mix - s_ens, mix,
                            other_ref, target_ref, trim, base_ab))

    # 关键修正：带符号增益对齐（ŝ 可能是目标的反相缩小副本，直接减会不减反增）。
    # 对每个窗长的估计都做一遍增益对齐——之前的窗长排序被极性/尺度问题污染。
    print(f"  全局相关 corr(y, ŝ₁ₛ) = "
          f"{float(np.dot(mix, est_by_window[1.0]) / (np.linalg.norm(mix) * np.linalg.norm(est_by_window[1.0]))):+.3f}",
          flush=True)
    aligned_by_window: dict[float, tuple[np.ndarray, np.ndarray]] = {}
    for win, s_est in est_by_window.items():
        out, gamma = gain_aligned_subtract(mix, s_est, 0.5, 0.4)
        aligned_by_window[win] = (out, gamma)
        results.append(evaluate(f"  +增益对齐 窗{win:.0f}s", out, mix,
                                other_ref, target_ref, trim, base_ab))

    def aligned_score(w: float) -> float:
        r = results[[r["label"] == f"  +增益对齐 窗{w:.0f}s" for r in results].index(True)]
        return r["si_sdr_other"]

    best_win = max(aligned_by_window, key=aligned_score)
    out, gamma = aligned_by_window[best_win]
    s_aligned = gamma * est_by_window[best_win]
    print(f"\n增益对齐后最优窗长 {best_win:.0f}s，细化：", flush=True)

    # 轻度欠减：γ 乘 0.85/0.92——30+ dB 抑制远超听感需要，少减一点换更少伪影
    for scale in (0.85, 0.92):
        results.append(evaluate(f"  +增益对齐 γ×{scale:.2f}",
                                mix - scale * s_aligned, mix,
                                other_ref, target_ref, trim, base_ab))

    # 增益对齐 + 维纳后滤波（ŝ 先乘回 γ 曲线再算 |S|，β 才有正确含义）
    for beta in (1.0, 2.0):
        out_w = wiener_postfilter(mix, s_aligned, beta, 0.0)
        results.append(evaluate(f"  +增益对齐+维纳 β={beta:.0f}", out_w, mix,
                                other_ref, target_ref, trim, base_ab))

    # 增益对齐后的二次迭代：残差再提一轮（同样对齐）
    pool.set_window(best_win, 0.5 if best_win > 1 else 0.1)
    s2g = run_tse(pool, enroll, out, "extract")
    r2g, _ = gain_aligned_subtract(out, s2g, 0.5, 0.4)
    results.append(evaluate("  +增益对齐+二次迭代", r2g, mix, other_ref, target_ref, trim, base_ab))

    print(f"\n总耗时 {time.perf_counter() - t0:.0f}s")


if __name__ == "__main__":
    main()
