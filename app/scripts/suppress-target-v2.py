#!/usr/bin/env python3
"""反向 TSE v2：从混音中消除注册人、保留其他人（重叠语音场景）。

用法（app/ 目录，AudioTSE 环境的 python）：
  python scripts/suppress-target-v2.py                      # 默认文件，CPU
  python scripts/suppress-target-v2.py 注册音 混音 输出.wav  # 任意指定
  python scripts/suppress-target-v2.py … cpu                # 末尾设备词：cpu / gpu / auto
  python scripts/suppress-target-v2.py … gpu                # BSRNN 前向与维纳滤波改跑 GPU
  python scripts/suppress-target-v2.py --device gpu --threads 8   # 显式等价写法

默认输入/输出：
  注册音  audios/output1.mp3
  混音    audios/output.mp3
  输出    audios/output-suppressed.wav    （--also-target 再输出目标估计便于对照试听）

性能统计（每次运行结束打印「──── 性能统计」一节）：
  · 阶段耗时：ffmpeg 解码 / 引擎加载 / TSE 流式提取 / 增益对齐相减 / 维纳后滤波…
  · 总耗时与 RTF（含单独的 TSE 提取 RTF）
  · CPU：本进程平均/峰值占用核数、累计 CPU·s、系统整体占用、torch 线程数
  · 内存：进程工作集与提交内存的精确峰值（Windows API），起始基线单列，
    可看出模型加载的增量
  · GPU（gpu 运行时）：利用率平均/峰值（nvidia-smi 采样）、显存占用峰值、
    torch 张量分配峰值
  除 WeSep 外唯一的模型是流式链路原有的 Silero VAD（静音窗旁路，CPU，很轻）；
  GPU 加速的是 WeSep BSRNN 前向与维纳后滤波这两个 torch 大头。

GPU 前置条件：CUDA 版 torch（install-wesep-tse.ps1 装的是 CPU 版；gpu 模式
下若检测不到 CUDA 会打印安装命令并退出）。装好后同一命令末尾加 gpu 即可
与 cpu 模式跑出同款统计，直接比对快慢。

原理（在 eval-suppress.py / eval-scenarios.py 上用带真值的重叠测试集验证过）：
  1. WeSep BSRNN 以 2 s 窗提取注册人估计 ŝ（2 s 窗提取质量约 15 dB SI-SDR，
     明显好于实时链路的 1 s 窗的 9 dB）。
  2. 关键一步——带符号增益对齐后相减：r = y − γ(t)·ŝ。
     WeSep 以 SI-SDR 损失训练，输出相对真实目标可以有任意整体极性与幅度
     （实测 ŝ ≈ 反相、幅度 31% 的目标副本；且同一文件内不同段还会翻转），
     直接相减会把目标加回去（实测比不处理还差 1.5 dB）。逐 0.5 s 子窗用
     最小二乘估计带符号增益 γ = ⟨ŝ,y⟩/⟨ŝ,ŝ⟩ 即可自动校正。
     实测（两人全程重叠测试集）：SI-SDR −1.2 → +12.8 dB，目标抑制 39 dB，
     另一人损伤 0.5 dB。
  3. 门控防误减（模型偶发退化——输出近静音垃圾或与混音不相关的片段时，
     贸然减掉会伤到另一人）。只做纯信号门控，不加载声纹模型（本脚本面向
     两人全程重叠的场景，目标人持续在场，无需声纹校验）：
       · 能量门：ŝ 子窗 RMS < 0.15×p95(各子窗 RMS) 视为垃圾，不减；
       · 相关门：|corr(ŝ,y)| < 0.40 视为估计不可信，不减；
       · 增益帽：|γ| > 15 视为数值发散，不减。
  4. 维纳后滤波（默认开，--wiener-beta 0 关闭）：残差再按
     g = |R|²/(|R|²+β|S|²) 收一刀，抑制 39 → 42 dB。

已知局限：窄带（8 kHz 电话音质）音频会让宽带 VoxCeleb 训练的 BSRNN 整体
退化，脚本会在检测到「输出能量异常塌缩」时告警，此时请改用
suppress-target.py 的 gate 或 aligned 方法。
"""
from __future__ import annotations

import argparse
import ctypes
import subprocess
import sys
import threading
import time
import wave
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import psutil

ROOT = Path(__file__).resolve().parents[1]    # app/
sys.path.insert(0, str(ROOT / "backend"))     # 复用后端 audio_tse 包

SAMPLE_RATE = 16_000
TSE_DIR = ROOT / "models" / "wesep-bsrnn-ecapa-vox1"
VAD_MODEL = ROOT / "models" / "silero_vad" / "silero_vad.onnx"
# 与实时链路一致的小块喂入（大块会触发 BufferedWeSepTse 的积压丢帧保护）
CHUNK_SAMPLES = 2048
MIN_ENROLLMENT_SECONDS = 3.0
PROGRESS_SECONDS = 30
MONITOR_INTERVAL = 0.2        # 资源采样周期（秒）

# ── 算法参数（eval-scenarios.py 网格验证过，详见模块 docstring）──────────
WINDOW_SECONDS = 2.0        # 提取窗长：2 s 提取质量 ~15 dB（1 s 只有 ~9 dB）
CROSSFADE_SECONDS = 0.25
SUBWIN_SECONDS = 0.5        # 增益对齐子窗
NC_GATE = 0.40              # 相关门：|corr(ŝ,y)| 下限
GAMMA_CAP = 15.0            # 增益帽：正常 γ 实测 1-10，发散时 30-150
RMS_REL_GATE = 0.15         # 能量门：子窗 RMS 下限 = 此值 × p95(各子窗 RMS)
SMOOTH_SECONDS = 0.10       # γ 曲线平滑窗（块边界防咔嗒）
WIENER_BETA = 1.0           # 维纳后滤波强度，0 = 关闭
COLLAPSE_RATIO = 0.05       # 输出/输入 RMS 低于此值 → 疑似模型整体退化


# ── 计时与资源统计 ────────────────────────────────────────────────────────
STAGE_TIMES: dict[str, float] = {}   # 阶段名 → 累计墙钟秒（结束报告用）


@contextmanager
def stage(name: str):
    """计时一个处理阶段，累加进 STAGE_TIMES。"""
    t0 = time.perf_counter()
    yield
    STAGE_TIMES[name] = STAGE_TIMES.get(name, 0.0) + (time.perf_counter() - t0)


def peak_memory_bytes() -> tuple[int, int] | None:
    """Windows 精确峰值：(物理工作集峰值, 提交内存峰值)，单位字节。

    比 0.2 s 采样更可靠（不会漏掉两次采样之间的瞬时峰值）；非 Windows 或
    调用失败返回 None，调用方回退到采样最大值。"""
    try:
        class _PMC(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]
        pmc = _PMC()
        pmc.cb = ctypes.sizeof(_PMC)
        if not ctypes.windll.psapi.GetProcessMemoryInfo(
                ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return None
        return int(pmc.PeakWorkingSetSize), int(pmc.PeakPagefileUsage)
    except Exception:
        return None


class ResourceMonitor:
    """后台线程采样资源：进程/系统 CPU 占用与 RSS；GPU 模式下再采
    nvidia-smi 的利用率与显存占用。start() 记起始基线，stop() 后取聚合值。"""

    def __init__(self, gpu: bool, interval: float = MONITOR_INTERVAL) -> None:
        self._proc = psutil.Process()
        self._interval = interval
        self._gpu = gpu          # 是否请求 GPU 采样
        self._gpu_ok = gpu       # nvidia-smi 是否仍可用（失败后置 False 停采）
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.proc_cpu: list[float] = []   # 进程 CPU（100 = 1 核，可超 100）
        self.sys_cpu: list[float] = []    # 系统整体 CPU（0-100）
        self.rss: list[int] = []          # 进程驻留内存（字节）
        self.gpu_util: list[float] = []   # GPU 利用率（%）
        self.gpu_mem: list[float] = []    # GPU 已用显存（MiB，整机所有进程）
        self.baseline_rss = 0

    def _sample_gpu(self) -> tuple[float, float] | None:
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used",
                 "--format=csv,noheader,nounits"], capture_output=True, timeout=5
            ).stdout.decode()
            util_s, mem_s = out.strip().splitlines()[0].split(",")
            return float(util_s), float(mem_s)
        except Exception:
            self._gpu_ok = False   # 没装驱动/命令缺失：停采，报告里注明
            return None

    def _loop(self) -> None:
        self._proc.cpu_percent(None)   # 首次调用只播种基线，返回值丢弃
        psutil.cpu_percent(None)
        while not self._stop.wait(self._interval):
            try:
                self.proc_cpu.append(self._proc.cpu_percent(None))
                self.sys_cpu.append(psutil.cpu_percent(None))
                self.rss.append(self._proc.memory_info().rss)
            except psutil.Error:
                break                # 进程对象失效（理论上不会）：停止采样
            if self._gpu and self._gpu_ok:
                g = self._sample_gpu()
                if g is not None:
                    self.gpu_util.append(g[0])
                    self.gpu_mem.append(g[1])

    def start(self) -> None:
        self.baseline_rss = self._proc.memory_info().rss
        self._thread = threading.Thread(target=self._loop, name="resource-monitor",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)


def resolve_device(spec: str) -> tuple[str, str]:
    """设备词 → (torch 设备串, 展示名)。gpu 且无 CUDA 时退出并给安装指引。"""
    if spec == "cpu":
        return "cpu", "cpu"
    import torch
    if not torch.cuda.is_available():
        if spec == "gpu":
            base = torch.__version__.split("+")[0]   # 2.14.1+cpu → 2.14.1
            raise SystemExit(
                f"✗ 请求 GPU，但当前 torch 无 CUDA 支持"
                f"（torch {torch.__version__}，torch.cuda.is_available() = False）。\n"
                "  先装同版本号的 CUDA 构建（会替换现有 CPU 版，下载约 3 GB；"
                "实时链路不受影响，仍默认走 CPU）：\n"
                f"    {sys.executable} -m pip install torch=={base}+cu130"
                " --index-url https://download.pytorch.org/whl/cu130\n"
                "  （若该版本没有 cu130 构建，去掉 == 版本号装最新版即可）")
        return "cpu", "cpu（未检测到 CUDA，auto 回退）"
    return "cuda", f"cuda（{torch.cuda.get_device_name(0)}）"


# ── 音频编解码 ────────────────────────────────────────────────────────────
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
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)


# ── 处理流水线 ────────────────────────────────────────────────────────────
def extract_target_stream(mix_pcm: bytes, enroll_pcm: bytes,
                          device: str = "cpu", threads: int | None = None
                          ) -> np.ndarray:
    """混音过一遍流式 WeSep（extract 模式，2 s 窗），返回与输入等长的 ŝ。"""
    from audio_tse.tse import BufferedWeSepTse, TseModel, WeSepTseEngine

    model = TseModel("wesep_bsrnn", "WeSep BSRNN", TSE_DIR)
    if not model.available:
        raise SystemExit(f"WeSep TSE 不可用：{model.unavailable_reason}")
    print("加载 WeSep BSRNN 引擎（约 5 秒）…")
    with stage("WeSep 引擎加载"):
        engine = WeSepTseEngine(model, window_seconds=WINDOW_SECONDS,
                                crossfade_seconds=CROSSFADE_SECONDS,
                                device=device, threads=threads)
    if device == "cuda":
        import torch
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()  # 此后峰值 = 模型常驻 + 推理激活
    with stage("注册嵌入 + VAD 初始化"):
        tse = BufferedWeSepTse(engine, enroll_pcm,
                               VAD_MODEL if VAD_MODEL.exists() else None, "extract")

    chunk_bytes = CHUNK_SAMPLES * 2
    outputs: list[bytes] = []
    start = time.perf_counter()
    next_report = PROGRESS_SECONDS
    total = len(mix_pcm)
    with stage("TSE 流式提取"):
        for offset in range(0, total, chunk_bytes):
            outputs.extend(tse.accept_pcm16(mix_pcm[offset:offset + chunk_bytes]))
            done = min(offset + chunk_bytes, total) / (SAMPLE_RATE * 2)
            if done >= next_report or done >= total / (SAMPLE_RATE * 2):
                next_report += PROGRESS_SECONDS
                print(f"  提取 {done:6.1f}s（已处理 {time.perf_counter() - start:.0f}s）")
        outputs.extend(tse.flush())
    s = to_float(b"".join(outputs))
    print(f"  提取完成，耗时 {time.perf_counter() - start:.1f}s")
    return s


def gain_aligned_subtract(mix: np.ndarray, s: np.ndarray
                          ) -> tuple[np.ndarray, np.ndarray, dict]:
    """带符号增益对齐相减：r = y − γ(t)·ŝ。返回 (残差, 逐采样 γ, 门控统计)。

    只做纯信号门控（能量/相关/增益帽），不加载声纹模型。"""
    n = len(mix)
    w = max(1, int(SAMPLE_RATE * SUBWIN_SECONDS))
    block_rms: list[float] = []
    for i in range(0, n, w):
        ss = s[i:i + w]
        block_rms.append(float(np.sqrt(np.mean(ss * ss) + 1e-15)))
    rms_floor = RMS_REL_GATE * float(np.percentile(block_rms, 95))

    centers: list[float] = []
    gammas: list[float] = []
    stats = {"blocks": len(block_rms), "rms_failed": 0, "nc_failed": 0,
             "gamma_failed": 0, "kept": 0}
    for i in range(0, n, w):
        ys, ss = mix[i:i + w], s[i:i + w]
        center = min(i + len(ss) / 2, n - 0.5)
        centers.append(center)
        if float(np.sqrt(np.mean(ss * ss) + 1e-15)) < rms_floor:
            gammas.append(0.0)                    # ŝ 近静音垃圾：目标人没在说
            stats["rms_failed"] += 1
            continue
        ss_norm = float(np.linalg.norm(ss))
        nc = float(np.dot(ys, ss) / (np.linalg.norm(ys) * ss_norm + 1e-12))
        if abs(nc) < NC_GATE:
            gammas.append(0.0)                    # 估计与混音不相关：不可信
            stats["nc_failed"] += 1
            continue
        gamma = float(np.dot(ys, ss) / (np.dot(ss, ss) + 1e-12))
        if abs(gamma) > GAMMA_CAP:
            gammas.append(0.0)                    # 除以小范数导致发散
            stats["gamma_failed"] += 1
            continue
        gammas.append(gamma)
        stats["kept"] += 1

    gamma_t = np.interp(np.arange(n, dtype=np.float64), centers, gammas).astype(np.float32)
    k = max(1, int(SAMPLE_RATE * SMOOTH_SECONDS)) | 1
    kernel = np.ones(k, dtype=np.float32) / k
    gamma_t = np.convolve(np.pad(gamma_t, (k // 2, k // 2), mode="edge"),
                          kernel, mode="valid").astype(np.float32)
    return (mix - gamma_t * s).astype(np.float32), gamma_t, stats


def wiener_postfilter(y: np.ndarray, s: np.ndarray, beta: float,
                      device: str = "cpu") -> np.ndarray:
    """维纳式后滤波：g = |R|²/(|R|² + β|S|²)（R = Y−S），压掉目标残留时频点。"""
    import torch
    n_fft, hop = 512, 128
    dev = torch.device(device)
    window = torch.hann_window(n_fft).to(dev)

    def stft_(x: np.ndarray):
        return torch.stft(torch.from_numpy(
            np.ascontiguousarray(x, dtype=np.float32)).to(dev),
            n_fft, hop, window=window, return_complex=True)

    r = stft_(y) - stft_(s)
    g = torch.abs(r) ** 2 / (torch.abs(r) ** 2 + beta * torch.abs(stft_(s)) ** 2 + 1e-12)
    out = torch.istft(g * r, n_fft, hop, window=window, length=len(y))
    return out.cpu().numpy()


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x * x) + 1e-15))


# ── 性能报告 ──────────────────────────────────────────────────────────────
def print_performance_report(device: str, mix_sec: float, monitor: ResourceMonitor,
                             wall: float, proc_cpu_s: float) -> None:
    import torch

    def gb(x: float) -> str:
        return f"{x / 2**30:.2f} GB"

    def mean(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    print(f"\n──── 性能统计（设备 {device}）────")
    print("阶段耗时：" + " │ ".join(f"{name} {secs:.1f}s"
                                  for name, secs in STAGE_TIMES.items()))
    rtf = f"，RTF {wall / mix_sec:.2f}" if mix_sec > 0 else ""
    extract = STAGE_TIMES.get("TSE 流式提取", 0.0)
    extract_rtf = f"；其中 TSE 提取 RTF {extract / mix_sec:.2f}" if mix_sec > 0 else ""
    print(f"总耗时 {wall:.1f}s（音频 {mix_sec:.1f}s{rtf}{extract_rtf}）")

    cores = psutil.cpu_count(logical=True) or 1
    if monitor.proc_cpu:
        print(f"CPU：本进程平均 {mean(monitor.proc_cpu) / 100:.1f}/{cores} 核、"
              f"峰值 {max(monitor.proc_cpu) / 100:.1f} 核，"
              f"累计 {proc_cpu_s:.0f} CPU·s（折合全程 {proc_cpu_s / wall:.1f} 核）；"
              f"系统整体平均 {mean(monitor.sys_cpu):.0f}%、峰值 {max(monitor.sys_cpu):.0f}%；"
              f"torch {torch.get_num_threads()} 线程")

    peaks = peak_memory_bytes()
    ws_peak = peaks[0] if peaks else max(monitor.rss, default=monitor.baseline_rss)
    line = (f"内存：工作集峰值 {gb(ws_peak)}"
            f"（起始基线 {gb(monitor.baseline_rss)}，"
            f"增量 {gb(max(0, ws_peak - monitor.baseline_rss))}）")
    if peaks:
        line += f"；提交内存峰值 {gb(peaks[1])}"
    print(line)

    if device == "cuda":
        props = torch.cuda.get_device_properties(0)
        if monitor.gpu_util:
            print(f"GPU：{props.name}，利用率平均 {mean(monitor.gpu_util):.0f}%、"
                  f"峰值 {max(monitor.gpu_util):.0f}%；显存占用峰值 "
                  f"{max(monitor.gpu_mem) / 1024:.2f} / {props.total_memory / 2**30:.1f} GB"
                  f"（整机），torch 张量分配峰值 {gb(torch.cuda.max_memory_allocated())}")
            if not monitor._gpu_ok:
                print("  ⚠ nvidia-smi 中途不可用，GPU 利用率/显存曲线不完整")
        else:
            print(f"GPU：{props.name}（nvidia-smi 采样不可用），"
                  f"torch 张量分配峰值 {gb(torch.cuda.max_memory_allocated())}")
        print("  Silero VAD（静音窗旁路）跑在 CPU（很轻，非瓶颈）")
    else:
        print("GPU：未启用（本次 cpu 运行；命令末尾加 gpu 可出同款 GPU 统计比对）")


# ── 主流程 ────────────────────────────────────────────────────────────────
def main() -> None:
    # 末尾设备简写：「… 输出.wav gpu」等价于 --device gpu（未用 --device 时生效）
    argv = sys.argv[1:]
    trailing_device = None
    if argv and "--device" not in argv and argv[-1].lower() in ("cpu", "gpu", "auto"):
        trailing_device = argv.pop().lower()

    parser = argparse.ArgumentParser(
        description="反向 TSE v2：消除混音中的注册人（重叠语音场景），"
                    "并报告各阶段耗时与 CPU/内存/GPU 消耗",
        epilog="设备可简写为最后一个参数：… 输出.wav gpu（cpu/gpu/auto），与 --device 等价")
    parser.add_argument("enrollment", type=Path, nargs="?", default=ROOT / "audios" / "output1.mp3",
                        help="注册音频（默认 audios/output1.mp3）")
    parser.add_argument("mixture", type=Path, nargs="?", default=ROOT / "audios" / "output.mp3",
                        help="混音音频（默认 audios/output.mp3）")
    parser.add_argument("output", type=Path, nargs="?", default=ROOT / "audios" / "output-suppressed.wav",
                        help="输出 wav（默认 audios/output-suppressed.wav）")
    parser.add_argument("--device", choices=("cpu", "gpu", "auto"), default=None,
                        help="计算设备（默认 cpu；gpu 需 CUDA 版 torch；auto = 有 CUDA 用 GPU）")
    parser.add_argument("--threads", type=int, default=None,
                        help="torch CPU 线程数（默认吃满所有核心；GPU 模式下影响不大）")
    parser.add_argument("--also-target", action="store_true",
                        help="另存目标估计（audios/output-target.wav，对照试听用）")
    parser.add_argument("--wiener-beta", type=float, default=WIENER_BETA,
                        help=f"维纳后滤波强度（默认 {WIENER_BETA}，0 = 关闭）")
    args = parser.parse_args(argv)

    device_spec = args.device or trailing_device or "cpu"
    device, device_desc = resolve_device(device_spec)
    print(f"计算设备：{device_spec} → {device_desc}")

    for path in (args.enrollment, args.mixture):
        if not path.exists():
            raise SystemExit(f"输入文件不存在：{path}")

    monitor = ResourceMonitor(gpu=device == "cuda")
    wall_t0 = time.perf_counter()
    cpu_t0 = time.process_time()
    monitor.start()
    try:
        with stage("ffmpeg 解码"):
            enroll_pcm = decode_pcm16(args.enrollment)
            mix_pcm = decode_pcm16(args.mixture)
        mix = to_float(mix_pcm)
        enroll_sec = len(enroll_pcm) / (SAMPLE_RATE * 2)
        mix_sec = len(mix_pcm) / (SAMPLE_RATE * 2)
        print(f"注册音频 {args.enrollment.name}: {enroll_sec:.1f}s")
        print(f"混音音频 {args.mixture.name}: {mix_sec:.1f}s")
        if enroll_sec < MIN_ENROLLMENT_SECONDS:
            raise SystemExit(f"注册语音至少需要 {MIN_ENROLLMENT_SECONDS:.0f} 秒（当前 {enroll_sec:.1f}s）")

        s = extract_target_stream(mix_pcm, enroll_pcm, device, args.threads)
        if len(s) < len(mix):
            s = np.pad(s, (0, len(mix) - len(s)))
        s = s[:len(mix)]

        print("带符号增益对齐相减…")
        with stage("增益对齐相减"):
            residual, gamma_t, stats = gain_aligned_subtract(mix, s)
        s_aligned = gamma_t * s

        out = residual
        if args.wiener_beta > 0:
            print("维纳后滤波…")
            with stage("维纳后滤波"):
                out = wiener_postfilter(mix, s_aligned, args.wiener_beta, device)

        with stage("写出 wav"):
            write_wav(args.output, out)
            if args.also_target:
                write_wav(ROOT / "audios" / "output-target.wav", s_aligned)
    finally:
        monitor.stop()
        wall = time.perf_counter() - wall_t0
        proc_cpu_s = time.process_time() - cpu_t0

    # 结果统计与失效告警
    ratio = rms(out) / (rms(mix) + 1e-15)
    nz = float(np.mean((gamma_t != 0).astype(np.float64)))
    print(f"\n完成：{args.output}")
    print(f"  增益对齐子窗：保留 {stats['kept']}/{stats['blocks']}"
          f"（能量门拦 {stats['rms_failed']}，相关门拦 {stats['nc_failed']}，"
          f"增益帽拦 {stats['gamma_failed']}）")
    kept_g = np.abs(gamma_t[gamma_t != 0])
    if kept_g.size:
        print(f"  γ 非零占比 {nz * 100:.0f}%，|γ| 中位 {float(np.median(kept_g)):.2f}")
    print(f"  RMS {rms(mix):.4f} → {rms(out):.4f}（{ratio * 100:.0f}%）")
    if ratio < COLLAPSE_RATIO:
        print("  ⚠ 警告：输出能量异常塌缩——典型的窄带音频使 BSRNN 整体退化的特征，"
              "结果可能不可用。请改用 suppress-target.py 的 gate 或 aligned 方法。")
    elif nz < 0.10:
        print("  ⚠ 提示：注册人在混音中几乎未被检出（保留子窗 <10%）。"
              "若注册人确实在说话，可能是音频带宽/信道不匹配导致 BSRNN 失效。")

    print_performance_report(device, mix_sec, monitor, wall, proc_cpu_s)


if __name__ == "__main__":
    main()
