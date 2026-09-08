# 检查参考声纹是否逐次一致（判定 dither 是否为 0）+ 打印 sherpa 版本
import numpy as np
import sherpa_onnx
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
MODEL = ROOT / "app/models/sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k/model.onnx"
FIX = Path(__file__).resolve().parents[1] / "fixtures"
print("sherpa_onnx:", sherpa_onnx.__version__)

with wave.open(str(ROOT / "app/samples/enroll_target.wav"), "rb") as w:
    x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0

cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(MODEL), num_threads=2, provider="cpu")
ex = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
ref = np.load(FIX / "ref_emb_enroll_target.npy")
for run in range(2):
    s = ex.create_stream()
    s.accept_waveform(16000, x)
    e = np.asarray(ex.compute(s), dtype=np.float32)
    print(f"run{run}: 与参考 cos={float(np.dot(e, ref) / (np.linalg.norm(e) * np.linalg.norm(ref))):.8f}")
