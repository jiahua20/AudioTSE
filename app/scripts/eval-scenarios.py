#!/usr/bin/env python3
"""多场景交叉验证反向 TSE 的增益对齐减法（eval-suppress.py 结论的稳健性检查）。

用 samples/ 的两路干净源合成不同场景的混音，比较：
  基线（直接输出混音）/ 直接减法（当前生产实现）/ 增益对齐减法 / +维纳
场景：全程重叠、50% 部分重叠、目标偏轻、目标偏响。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS))
import importlib.util as _ilu

_spec = _ilu.spec_from_file_location("eval_suppress", _SCRIPTS / "eval-suppress.py")
_m = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_m)          # 执行顶层（含 backend 的 sys.path 注入）

EnginePool = _m.EnginePool
SAMPLE_RATE = _m.SAMPLE_RATE
evaluate = _m.evaluate
gain_aligned_subtract = _m.gain_aligned_subtract
load = _m.load
project = _m.project
run_tse = _m.run_tse
wiener_postfilter = _m.wiener_postfilter

TRIM = int(SAMPLE_RATE * 0.6)
WINDOW = 2.0


def build_mix(target: np.ndarray, other: np.ndarray, target_gain: float,
              other_gain: float, target_span: tuple[float, float] | None
              ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """合成混音。target_span=None 表示全程重叠；否则目标只在 [a,b) 秒出现。"""
    n = len(other)
    t = np.zeros(n, dtype=np.float32)
    if target_span is None:
        t[:] = target[:n]
    else:
        a, b = int(target_span[0] * SAMPLE_RATE), int(target_span[1] * SAMPLE_RATE)
        t[a:b] = target[a:b]
    mix = target_gain * t + other_gain * other
    mix = mix / (np.max(np.abs(mix)) + 1e-9) * 0.92          # 与测试集同様峰值归一
    scale = 0.92 / (np.max(np.abs(target_gain * t + other_gain * other)) + 1e-9)
    return mix.astype(np.float32), (target_gain * scale * t).astype(np.float32), \
        (other_gain * scale * other).astype(np.float32)


def main() -> None:
    enroll = load("enroll_target.wav")
    target = load("target_clean.wav")
    other = load("other_clean.wav")
    pool = EnginePool()
    pool.set_window(WINDOW, 0.25)

    scenarios = [
        ("全程重叠 0.7/0.6", 0.70, 0.60, None),
        ("50%重叠 0.7/0.6", 0.70, 0.60, (2.0, 6.0)),
        ("全程重叠 目标轻 0.35", 0.35, 0.70, None),
        ("全程重叠 目标响 0.85", 0.85, 0.45, None),
    ]
    t0 = time.perf_counter()
    for name, tg, og, span in scenarios:
        mix, t_ref, o_ref = build_mix(target, other, tg, og, span)
        a0, b0, _ = project(mix[TRIM:-TRIM], t_ref[TRIM:-TRIM], o_ref[TRIM:-TRIM])
        base_ab = (a0, b0)
        print(f"\n=== {name}（相关 corr(y,t_ref) 占比见投影 α={a0:+.3f} β={b0:+.3f}）===")
        evaluate("基线：直接输出混音", mix, mix, o_ref, t_ref, TRIM, base_ab)
        s = run_tse(pool, enroll, mix, "extract")
        evaluate("直接减法（现状）", mix - s, mix, o_ref, t_ref, TRIM, base_ab)
        out, gamma = gain_aligned_subtract(mix, s, 0.5, 0.4)
        evaluate("增益对齐减法", out, mix, o_ref, t_ref, TRIM, base_ab)
        g_pos = float(np.mean(np.abs(gamma[gamma != 0])) if np.any(gamma) else 0.0)
        print(f"    γ 非零均值 |γ|={g_pos:.2f}，非零占比 {float(np.mean(gamma != 0)) * 100:.0f}%")
        for scale in (0.92, 1.0):
            evaluate(f"  增益对齐 γ×{scale:.2f}", mix - scale * gamma * s, mix,
                     o_ref, t_ref, TRIM, base_ab)
        out_w = wiener_postfilter(mix, gamma * s, 1.0, 0.0)
        evaluate("  增益对齐+维纳 β=1", out_w, mix, o_ref, t_ref, TRIM, base_ab)
    print(f"\n总耗时 {time.perf_counter() - t0:.0f}s")


if __name__ == "__main__":
    main()
