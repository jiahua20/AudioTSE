#!/usr/bin/env python3
"""纯 WeSep 提取：只跑 WeSep BSRNN 前向，输出注册人估计 ŝ，无任何后处理。

与 suppress-target-v2.py 的关系：v2 = 本脚本的 ŝ ＋ 增益对齐相减 ＋ 三重门控
（能量/相关/增益帽）＋ 维纳后滤波。本脚本把后处理全部剥掉——连实时链路原有的
Silero VAD 静音旁路也不加载——用于：

  · 试听模型裸输出的质量和响度起伏（判断残留段是「模型没提到」还是
    「提到了但响度极低 / 被后处理门控拦掉」）；
  · 为调 v2 的门控参数提供参照。

推理设置与 v2 完全一致（2 s 提取窗、0.25 s 交叉淡化、2048 样本小块流式喂入），
输出可直接与 v2 各版本对照。

用法（app/ 目录，AudioTSE 环境的 python）：
  python scripts/wesep-extract-only.py 注册音 混音 输出.wav        # CPU
  python scripts/wesep-extract-only.py 注册音 混音 输出.wav gpu    # 末尾设备词
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]    # app/
sys.path.insert(0, str(ROOT / "backend"))     # 复用后端 audio_tse 包

SAMPLE_RATE = 16_000
TSE_DIR = ROOT / "models" / "wesep-bsrnn-ecapa-vox1"
CHUNK_SAMPLES = 2048        # 与实时链路一致的小块喂入（大块触发积压丢帧保护）
WINDOW_SECONDS = 2.0        # 与 v2 相同：2 s 窗提取质量 ~15 dB
CROSSFADE_SECONDS = 0.25
PROGRESS_SECONDS = 30


# ── 音频编解码（与 v2 相同）───────────────────────────────────────────────
def decode_pcm16(path: Path) -> bytes:
    """任意音频 → 16 kHz 单声道 s16le 原始字节。"""
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path),
         "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"],
        capture_output=True, check=True)
    raw = result.stdout
    if len(raw) % 2:
        raw = raw[:-1]
    return raw


def to_float(pcm16: bytes) -> np.ndarray:
    return np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0


def write_wav(path: Path, samples: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(np.nan_to_num(samples, nan=0.0, posinf=1.0, neginf=-1.0),
                   -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    import wave
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)


def main() -> None:
    argv = sys.argv[1:]
    device = "cpu"
    if argv and argv[-1].lower() in ("cpu", "gpu", "auto"):
        device = argv.pop().lower()
    if device in ("gpu", "auto"):
        import torch
        if torch.cuda.is_available():
            device = "cuda"
            print(f"计算设备：cuda（{torch.cuda.get_device_name(0)}）")
        elif device == "gpu":
            raise SystemExit("✗ 请求 GPU 但 torch 无 CUDA 支持，安装指引见 "
                             "suppress-target-v2.py 的报错信息")
        else:
            device = "cpu"
            print("计算设备：auto → cpu（未检测到 CUDA）")
    else:
        print("计算设备：cpu")

    if len(argv) != 3:
        raise SystemExit("用法：python scripts/wesep-extract-only.py 注册音 混音 输出.wav [cpu/gpu/auto]")
    enrollment, mixture, output = (Path(a) for a in argv)
    for path in (enrollment, mixture):
        if not path.exists():
            raise SystemExit(f"输入文件不存在：{path}")

    wall_t0 = time.perf_counter()
    enroll_pcm = decode_pcm16(enrollment)
    mix_pcm = decode_pcm16(mixture)
    mix_sec = len(mix_pcm) / (SAMPLE_RATE * 2)
    print(f"注册音频 {enrollment.name}: {len(enroll_pcm) / (SAMPLE_RATE * 2):.1f}s")
    print(f"混音音频 {mixture.name}: {mix_sec:.1f}s")

    from audio_tse.tse import BufferedWeSepTse, TseModel, WeSepTseEngine

    model = TseModel("wesep_bsrnn", "WeSep BSRNN", TSE_DIR)
    if not model.available:
        raise SystemExit(f"WeSep TSE 不可用：{model.unavailable_reason}")
    print("加载 WeSep BSRNN 引擎（约 5 秒）…")
    engine = WeSepTseEngine(model, window_seconds=WINDOW_SECONDS,
                            crossfade_seconds=CROSSFADE_SECONDS, device=device)
    # vad_model=None：不加载 Silero VAD，静音窗也照常过模型——纯 WeSep
    tse = BufferedWeSepTse(engine, enroll_pcm, None, "extract")

    chunk_bytes = CHUNK_SAMPLES * 2
    outputs: list[bytes] = []
    start = time.perf_counter()
    next_report = PROGRESS_SECONDS
    total = len(mix_pcm)
    for offset in range(0, total, chunk_bytes):
        outputs.extend(tse.accept_pcm16(mix_pcm[offset:offset + chunk_bytes]))
        done = min(offset + chunk_bytes, total) / (SAMPLE_RATE * 2)
        if done >= next_report or done >= total / (SAMPLE_RATE * 2):
            next_report += PROGRESS_SECONDS
            print(f"  提取 {done:6.1f}s（已处理 {time.perf_counter() - start:.0f}s）")
    outputs.extend(tse.flush())
    s = to_float(b"".join(outputs))
    write_wav(output, s)

    print(f"\n完成：{output}")
    print(f"  总耗时 {time.perf_counter() - wall_t0:.1f}s（音频 {mix_sec:.1f}s），"
          f"输出时长 {len(s) / SAMPLE_RATE:.1f}s，"
          f"RMS {float(np.sqrt(np.mean(s * s) + 1e-15)):.4f}，"
          f"峰值 {float(np.max(np.abs(s))) if len(s) else 0.0:.3f}")


if __name__ == "__main__":
    main()
