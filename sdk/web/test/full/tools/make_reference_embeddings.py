# 重新生成 test/fixtures/ 下的参考声纹（对拍基准）。
# 用 app 的 conda 环境运行：
#   D:\App\miniconda3\envs\AudioTSE\python.exe test/make_reference_embeddings.py
# 输出 sherpa_onnx Python（与 app 后端同引擎）在 app/samples 三个 wav 上的声纹。
from pathlib import Path

import numpy as np
import sherpa_onnx
import wave

ROOT = Path(__file__).resolve().parents[5]  # 仓库根
MODEL = ROOT / "app/models/sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k/model.onnx"
SAMPLES = ROOT / "app/samples"
OUT = Path(__file__).resolve().parents[1] / "fixtures"

NAMES = ["enroll_target", "other_clean", "target_clean"]


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0


def main() -> None:
    config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(MODEL), num_threads=2, provider="cpu")
    extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
    OUT.mkdir(parents=True, exist_ok=True)
    for name in NAMES:
        stream = extractor.create_stream()
        stream.accept_waveform(16000, read_wav(SAMPLES / f"{name}.wav"))
        embedding = np.asarray(extractor.compute(stream), dtype=np.float32)
        np.save(OUT / f"ref_emb_{name}.npy", embedding)
        print(f"{name}: dim={len(embedding)} norm={np.linalg.norm(embedding):.4f}")


if __name__ == "__main__":
    main()
