from __future__ import annotations

import argparse
import tarfile
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = ROOT / "models"
BASE_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
MODELS = {
    "zipformer": {
        "directory": "sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23",
        "url": f"{BASE_URL}/asr-models/sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23.tar.bz2",
        "marker": "tokens.txt",
    },
    "paraformer": {
        "directory": "sherpa-onnx-streaming-paraformer-bilingual-zh-en",
        "url": f"{BASE_URL}/asr-models/sherpa-onnx-streaming-paraformer-bilingual-zh-en.tar.bz2",
        "marker": "tokens.txt",
    },
    "vad": {
        "directory": "silero_vad",
        "url": f"{BASE_URL}/asr-models/silero_vad.onnx",
        "marker": "silero_vad.onnx",
    },
    "speaker": {
        "directory": "sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k",
        "url": f"{BASE_URL}/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx",
        "marker": "model.onnx",
    },
}


def download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "AudioTSE/0.1"})
    print(f"Downloading {url}")
    with urllib.request.urlopen(request, timeout=180) as response, destination.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)


def safe_extract(archive_path: Path) -> None:
    models_root = MODELS_DIR.resolve()
    with tarfile.open(archive_path, "r:bz2") as archive:
        for member in archive.getmembers():
            destination = (MODELS_DIR / member.name).resolve()
            if models_root not in destination.parents and destination != models_root:
                raise RuntimeError(f"Unsafe model archive path: {member.name}")
        archive.extractall(MODELS_DIR)


def fetch(model_id: str) -> None:
    model = MODELS[model_id]
    directory = MODELS_DIR / model["directory"]
    marker = directory / model["marker"]
    if marker.exists():
        print(f"[{model_id}] ready: {marker.relative_to(ROOT)}")
        return
    url = model["url"]
    if url.endswith(".tar.bz2"):
        archive_path = MODELS_DIR / Path(url).name
        if not archive_path.exists():
            download(url, archive_path)
        safe_extract(archive_path)
        archive_path.unlink()
    else:
        download(url, marker)
    if not marker.exists():
        raise RuntimeError(f"Model setup failed: {marker}")
    print(f"[{model_id}] installed: {marker.relative_to(ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download AudioTSE runtime models")
    parser.add_argument("models", nargs="*", metavar="MODEL")
    args = parser.parse_args()
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    selected = args.models or list(MODELS)
    unknown = set(selected) - set(MODELS)
    if unknown:
        parser.error(f"unknown models: {', '.join(sorted(unknown))}")
    for model_id in selected:
        fetch(model_id)


if __name__ == "__main__":
    main()