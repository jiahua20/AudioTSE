# 用 torchaudio 的 kaldi fbank 生成参考特征（逐元素对拍用）。
# 参数对齐 sherpa-onnx 的前端默认（sherpa-onnx/csrc/features.h FeatureExtractorConfig）：
#   25ms 窗 / 10ms 移、povey 窗、预加重 0.97、去直流、dither=0、低频 20Hz、
#   high_freq=-400（Nyquist 回退 400Hz → 7600Hz）、snip_edges=False（居中+反射填充）、
#   80 mel bins、use_energy=False。
# 注意：high_freq 与 snip_edges 与 kaldi/torchaudio 原生默认不同，须显式传入。
# 用 app 的 conda 环境运行：
#   D:\App\miniconda3\envs\AudioTSE\python.exe sdk/web/test/tools/make_reference_fbank.py
from pathlib import Path

import numpy as np
import torch
import torchaudio.compliance.kaldi as kaldi
import wave

ROOT = Path(__file__).resolve().parents[5]  # 仓库根
SAMPLES = ROOT / "app/samples"
OUT = Path(__file__).resolve().parents[1] / "fixtures"

NAMES = ["enroll_target", "other_clean"]


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name in NAMES:
        x = torch.from_numpy(read_wav(SAMPLES / f"{name}.wav")).unsqueeze(0)  # (1, T)
        feats = kaldi.fbank(
            x,
            num_mel_bins=80,
            sample_frequency=16000.0,
            high_freq=7600.0,   # sherpa high_freq=-400 → 8000-400
            snip_edges=False,   # sherpa 默认（kaldi 原生默认为 True）
        )
        np.save(OUT / f"ref_fbank_{name}.npy", feats.numpy().astype(np.float32))
        print(f"{name}: shape={tuple(feats.shape)} -> {OUT / f'ref_fbank_{name}.npy'}")


if __name__ == "__main__":
    main()
