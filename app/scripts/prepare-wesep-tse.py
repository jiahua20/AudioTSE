# WeSep BSRNN TSE 权重准备脚本：从 ModelScope 下载约 262 MB 的
# bsrnn_ecapa_vox1.tar.gz，解出 config.yaml + avg_model.pt 放到
# models/wesep-bsrnn-ecapa-vox1/。由 install-wesep-tse.ps1 第 3 步调用。
from __future__ import annotations

import tarfile
import urllib.request
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]                     # 项目根目录
DESTINATION = ROOT / "models" / "wesep-bsrnn-ecapa-vox1"       # 权重最终存放目录
ARCHIVE = ROOT / "models" / "bsrnn_ecapa_vox1.tar.gz"          # 下载的压缩包（临时）
MODEL_URL = (
    "https://www.modelscope.cn/datasets/wenet/wesep_pretrained_models/"
    "resolve/master/bsrnn_ecapa_vox1.tar.gz"
)


def download() -> None:
    # 单线程流式下载（大文件但没有断点续传逻辑，一次性下载）
    request = urllib.request.Request(MODEL_URL, headers={"User-Agent": "AudioTSE/0.1"})
    print(f"Downloading {MODEL_URL}")
    with urllib.request.urlopen(request, timeout=180) as response, ARCHIVE.open("wb") as output:
        while chunk := response.read(1024 * 1024):  # 1 MiB 块流式写盘
            output.write(chunk)


def extract() -> Path:
    """解压到临时目录并定位「config.yaml 与 avg_model.pt 同在一层」的目录。"""
    unpacked = ROOT / "models" / ".wesep-bsrnn-unpacked"  # 临时解压目录
    if unpacked.exists():
        shutil.rmtree(unpacked)  # 清掉上次可能残留的解压
    unpacked.mkdir(parents=True)
    unpacked_root = unpacked.resolve()
    # 先校验所有成员路径都在解压目录内（防目录穿越），再真正解压
    with tarfile.open(ARCHIVE, "r:gz") as archive:
        for member in archive.getmembers():
            target = (unpacked / member.name).resolve()
            if unpacked_root not in target.parents and target != unpacked_root:
                raise RuntimeError(f"Unsafe model archive path: {member.name}")
        archive.extractall(unpacked)
    # 压缩包内层级不定：搜索 config.yaml 和 avg_model.pt，要求两者在同一目录
    config = next(unpacked.rglob("config.yaml"), None)
    model = next(unpacked.rglob("avg_model.pt"), None)
    if config is None or model is None or config.parent != model.parent:
        raise RuntimeError("WeSep archive does not contain config.yaml and avg_model.pt together")
    return config.parent


def main() -> None:
    # 已就绪则直接退出（幂等，start.ps1 反复跑也不重复下载）
    if all((DESTINATION / filename).exists() for filename in ("config.yaml", "avg_model.pt")):
        print(f"WeSep TSE ready: {DESTINATION.relative_to(ROOT)}")
        return
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    if not ARCHIVE.exists():
        download()  # 无缓存压缩包则先下载
    source = extract()  # 解压并找到权重所在层
    DESTINATION.mkdir(parents=True, exist_ok=True)
    # 只拷贝需要的两个文件到最终位置（丢弃压缩包里其余的训练产物）
    for filename in ("config.yaml", "avg_model.pt"):
        source_file = source / filename
        if not source_file.exists():
            raise RuntimeError(f"WeSep model is missing {filename}: {source}")
        shutil.copy2(source_file, DESTINATION / filename)
    shutil.rmtree(ROOT / "models" / ".wesep-bsrnn-unpacked")  # 清理临时解压目录
    ARCHIVE.unlink()  # 删除压缩包，省 262 MB 磁盘
    print(f"WeSep TSE ready: {DESTINATION.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
