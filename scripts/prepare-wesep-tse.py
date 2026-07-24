from __future__ import annotations

import tarfile
import urllib.request
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "models" / "wesep-bsrnn-ecapa-vox1"
ARCHIVE = ROOT / "models" / "bsrnn_ecapa_vox1.tar.gz"
MODEL_URL = (
    "https://www.modelscope.cn/datasets/wenet/wesep_pretrained_models/"
    "resolve/master/bsrnn_ecapa_vox1.tar.gz"
)


def download() -> None:
    request = urllib.request.Request(MODEL_URL, headers={"User-Agent": "AudioTSE/0.1"})
    print(f"Downloading {MODEL_URL}")
    with urllib.request.urlopen(request, timeout=180) as response, ARCHIVE.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)


def extract() -> Path:
    unpacked = ROOT / "models" / ".wesep-bsrnn-unpacked"
    if unpacked.exists():
        shutil.rmtree(unpacked)
    unpacked.mkdir(parents=True)
    unpacked_root = unpacked.resolve()
    with tarfile.open(ARCHIVE, "r:gz") as archive:
        for member in archive.getmembers():
            target = (unpacked / member.name).resolve()
            if unpacked_root not in target.parents and target != unpacked_root:
                raise RuntimeError(f"Unsafe model archive path: {member.name}")
        archive.extractall(unpacked)
    config = next(unpacked.rglob("config.yaml"), None)
    model = next(unpacked.rglob("avg_model.pt"), None)
    if config is None or model is None or config.parent != model.parent:
        raise RuntimeError("WeSep archive does not contain config.yaml and avg_model.pt together")
    return config.parent


def main() -> None:
    if all((DESTINATION / filename).exists() for filename in ("config.yaml", "avg_model.pt")):
        print(f"WeSep TSE ready: {DESTINATION.relative_to(ROOT)}")
        return
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    if not ARCHIVE.exists():
        download()
    source = extract()
    DESTINATION.mkdir(parents=True, exist_ok=True)
    for filename in ("config.yaml", "avg_model.pt"):
        source_file = source / filename
        if not source_file.exists():
            raise RuntimeError(f"WeSep model is missing {filename}: {source}")
        shutil.copy2(source_file, DESTINATION / filename)
    shutil.rmtree(ROOT / "models" / ".wesep-bsrnn-unpacked")
    ARCHIVE.unlink()
    print(f"WeSep TSE ready: {DESTINATION.relative_to(ROOT)}")


if __name__ == "__main__":
    main()