# 模型下载器：首次启动时把 ASR / VAD / 声纹模型从 sherpa-onnx 的 GitHub
# release 下载到 models/ 目录（支持断点续传 + 重试 + 安全校验）。
# start.ps1 每次启动都会跑一遍：已就绪的模型秒过，缺的才下载。
from __future__ import annotations

import argparse
import tarfile
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]     # 项目根目录
MODELS_DIR = ROOT / "models"                   # 所有模型的统一存放处
BASE_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download"  # sherpa-onnx 官方模型库
USER_AGENT = "AudioTSE/0.1"                    # 自报身份，避免被 GitHub 拦截
CHUNK_SIZE = 1024 * 1024                       # 流式下载的块大小（1 MiB）
MAX_ATTEMPTS = 6                               # 下载失败最多重试次数
RETRY_BACKOFF_SECONDS = 2.0                    # 重试退避基数（第 n 次失败等 n*2 秒）
# 模型清单：id -> {目录名, 下载 URL, 就绪标志文件}
# marker 文件存在即视为该模型已安装，无需重复下载
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
    have = destination.stat().st_size if destination.exists() else 0  # 本地已有的字节数
    headers = {"User-Agent": USER_AGENT}
    # 已有部分文件且小于远端总长 → 发 Range 请求从断点继续
    resume = bool(expected) and 0 < have < expected
    if resume:
        headers["Range"] = f"bytes={have}-"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=180) as response:
        status = response.getcode()
        if resume and status == 206:
            mode = "ab"  # 206 Partial Content：服务器支持续传，追加写
        else:  # server ignored the range — restart from scratch
            mode = "wb"  # 200 全量返回：从头写
            have = 0
        total = expected
        # 优先用 Content-Range 里的总长（续传响应没有 Content-Length 全量值）
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
                chunk = response.read(CHUNK_SIZE)  # 流式读取，不占大内存
                if not chunk:
                    break
                output.write(chunk)
    final = destination.stat().st_size
    if total and final != total:
        raise RuntimeError(f"truncated download: got {final} of {total} bytes")  # 尺寸不符 → 触发重试


def download(url: str, destination: Path) -> None:
    """带重试的下载入口：完整文件已存在则直接跳过。"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    expected = _remote_size(url)  # 先探远端大小（用于跳过判断 + 断点续传 + 完整性校验）
    if expected and destination.exists() and destination.stat().st_size == expected:
        print(f"Already complete: {destination.name}")
        return
    print(f"Downloading {url}")
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            _fetch_once(url, destination, expected)  # 单次尝试（内部支持续传）
            print(f"  completed ({destination.stat().st_size} bytes)")
            return
        except Exception as error:
            last_error = error
            current = destination.stat().st_size if destination.exists() else 0
            print(f"  attempt {attempt}/{MAX_ATTEMPTS} failed at {current} bytes: {type(error).__name__}: {error}")
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)  # 线性退避后重试（会从断点续传）
    raise RuntimeError(f"Download failed after {MAX_ATTEMPTS} attempts: {last_error}")


def safe_extract(archive_path: Path) -> None:
    """解压 tar.bz2 到 models/，先校验成员路径防止目录穿越（zip-slip 攻击）。"""
    models_root = MODELS_DIR.resolve()
    with tarfile.open(archive_path, "r:bz2") as archive:
        for member in archive.getmembers():
            # 每个成员的最终落点必须仍在 models/ 内，否则拒绝解压
            destination = (MODELS_DIR / member.name).resolve()
            if models_root not in destination.parents and destination != models_root:
                raise RuntimeError(f"Unsafe model archive path: {member.name}")
        archive.extractall(MODELS_DIR, filter="data")  # filter="data"：进一步剥掉特殊文件/权限


def fetch(model_id: str) -> None:
    """确保一个模型就绪：marker 文件在则跳过；否则下载（tar.bz2 需解压）。"""
    model = MODELS[model_id]
    directory = MODELS_DIR / model["directory"]
    marker = directory / model["marker"]
    if marker.exists():
        print(f"[{model_id}] ready: {marker.relative_to(ROOT)}")  # 已安装，秒过
        return
    url = model["url"]
    if url.endswith(".tar.bz2"):
        # 压缩包模型：下载 → 校验解压 → 删除压缩包
        archive_path = MODELS_DIR / Path(url).name
        download(url, archive_path)
        try:
            safe_extract(archive_path)
        except Exception as error:
            archive_path.unlink(missing_ok=True)  # never leave a corrupt archive that traps later runs
            raise RuntimeError(f"Extraction failed for {archive_path.name}: {error}") from error
        archive_path.unlink()  # 解压成功后删掉压缩包，省磁盘
    else:
        download(url, marker)  # 单文件模型：直接下到最终位置
    if not marker.exists():
        raise RuntimeError(f"Model setup failed: {marker}")  # 双保险：解压后 marker 仍不在
    print(f"[{model_id}] installed: {marker.relative_to(ROOT)}")


def main() -> None:
    # 命令行：不带参数 = 全部模型；指定若干 id = 只装这些
    parser = argparse.ArgumentParser(description="Download AudioTSE runtime models")
    parser.add_argument("models", nargs="*", metavar="MODEL")
    args = parser.parse_args()
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    selected = args.models or list(MODELS)  # 默认全选
    unknown = set(selected) - set(MODELS)
    if unknown:
        parser.error(f"unknown models: {', '.join(sorted(unknown))}")  # 拼错的 id 直接报错
    for model_id in selected:
        fetch(model_id)  # 逐个确保就绪


if __name__ == "__main__":
    main()
