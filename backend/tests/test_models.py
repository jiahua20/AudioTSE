import tempfile
import unittest
from pathlib import Path

import numpy as np

from audio_tse.asr import AsrModel
from audio_tse.speaker_gate import SpeakerEmbedder
from audio_tse.tse import TseModel


class ModelConfigurationTest(unittest.TestCase):
    def test_zipformer_requires_all_runtime_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            model_dir = Path(directory)
            model = AsrModel("zipformer", "Zipformer", model_dir, "transducer")
            self.assertFalse(model.available)
            for filename in (
                "tokens.txt",
                "encoder-epoch-99-avg-1.int8.onnx",
                "decoder-epoch-99-avg-1.onnx",
                "joiner-epoch-99-avg-1.int8.onnx",
            ):
                (model_dir / filename).touch()
            self.assertTrue(model.available)

    def test_paraformer_requires_encoder_decoder_and_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            model_dir = Path(directory)
            model = AsrModel("paraformer", "Paraformer", model_dir, "paraformer")
            for filename in ("tokens.txt", "encoder.int8.onnx", "decoder.int8.onnx"):
                (model_dir / filename).touch()
            self.assertTrue(model.available)

    def test_tse_requires_weights_and_optional_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            model = TseModel("wesep", "WeSep", Path(directory))
            self.assertFalse(model.model_files_ready)
            self.assertIn("权重", model.unavailable_reason or "")
            (model.directory / "config.yaml").touch()
            (model.directory / "avg_model.pt").touch()
            self.assertTrue(model.model_files_ready)
            self.assertEqual(model.available, model.dependency_ready)

    def test_cosine_similarity(self) -> None:
        target = np.array([1.0, 0.0], dtype=np.float32)
        self.assertAlmostEqual(SpeakerEmbedder.cosine(target, target), 1.0)
        self.assertAlmostEqual(
            SpeakerEmbedder.cosine(target, np.array([0.0, 1.0], dtype=np.float32)),
            0.0,
        )


if __name__ == "__main__":
    unittest.main()