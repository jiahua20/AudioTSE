from __future__ import annotations

import argparse
import tarfile
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = ROOT / "models"
BASE_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
USER_AGENT = "AudioTSE/0.1"
CHUNK_SIZE = 1024 * 1024
MAX_ATTEMPTS = 6
RETRY_BACKOFF_SECONDS = 2.0
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


def _remote_size(url: str) -> int | None:
    """Return the remote resource size in bytes via HEAD, or None if unknown."""
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            length = response.headers.get("Content-Length")
    except Exception as error:  # network/HTTP hiccup — fall back to streaming without a size guard
        print(f"  HEAD probe failed ({type(error).__name__}: {error}); streaming without size check")
        return None
    return int(length) if length else None


def _fetch_once(url: str, destination: Path, expected: int | None) -> None:
    """Download ``url`` to ``destination``, resuming any existing partial file.

    Raises RuntimeError if the final size does not match expectations, so the
    caller can retry (resuming again) instead of trusting a truncated file.
    """
    have = destination.stat().st_size if destination.exists() else 0
    headers = {"User-Agent": USER_AGENT}
    resume = bool(expected) and 0 < have < expected
    if resume:
        headers["Range"] = f"bytes={have}-"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=180) as response:
        status = response.getcode()
        if resume and status == 206:
            mode = "ab"
        else:  # server ignored the range — restart from scratch
            mode = "wb"
            have = 0
        total = expected
        content_range = response.headers.get("Content-Range")
        if content_range and "/" in content_range:
            try:
                total = int(content_range.rsplit("/", 1)[1])
            except ValueError:
                pass
        elif mode == "wb":
            length = response.headers.get("Content-Length")
            if length:
                total = int(length)
        with destination.open(mode) as output:
            while True:
                chunk = response.read(CHUNK_SIZE)
                if not chunk:
                    break
                output.write(chunk)
    final = destination.stat().st_size
    if total and final != total:
        raise RuntimeError(f"truncated download: got {final} of {total} bytes")


def download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    expected = _remote_size(url)
    if expected and destination.exists() and destination.stat().st_size == expected:
        print(f"Already complete: {destination.name}")
        return
    print(f"Downloading {url}")
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            _fetch_once(url, destination, expected)
            print(f"  completed ({destination.stat().st_size} bytes)")
            return
        except Exception as error:
            last_error = error
            current = destination.stat().st_size if destination.exists() else 0
            print(f"  attempt {attempt}/{MAX_ATTEMPTS} failed at {current} bytes: {type(error).__name__}: {error}")
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise RuntimeError(f"Download failed after {MAX_ATTEMPTS} attempts: {last_error}")


def safe_extract(archive_path: Path) -> None:
    models_root = MODELS_DIR.resolve()
    with tarfile.open(archive_path, "r:bz2") as archive:
        for member in archive.getmembers():
            destination = (MODELS_DIR / member.name).resolve()
            if models_root not in destination.parents and destination != models_root:
                raise RuntimeError(f"Unsafe model archive path: {member.name}")
        archive.extractall(MODELS_DIR, filter="data")


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
        download(url, archive_path)
        try:
            safe_extract(archive_path)
        except Exception as error:
            archive_path.unlink(missing_ok=True)  # never leave a corrupt archive that traps later runs
            raise RuntimeError(f"Extraction failed for {archive_path.name}: {error}") from error
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
