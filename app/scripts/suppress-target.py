#!/usr/bin/env python3
"""离线反向 TSE：用注册语音从混音中「消除」目标人，保留其他人（或正向提取作对照）。

三种算法，按目标人特性选择（--method）：

  aligned（默认） 对齐相减 + 声纹兜底：目标人是「固定录音回放」（数字人播报、
               IVR 提示音等）时的最优解——互相关在混音里定位回放波形，
               逐块估计增益后逐采样相减。线性叠加原理保证重叠语音也被
               精确去除（混音=回放+人声，减去回放即剩人声），无模型误差。
               回放库没覆盖到的目标人语音（同声线但不同内容）由声纹反门控
               兜底静音——兜底部分在重叠时无法区分（见 gate 的局限）。
               因此请尽量提供完整的回放音频（拼接成一条注册文件即可）。

  gate          声纹反门控：滑动窗口算 ER2Net 声纹嵌入，与注册嵌入比相似度，
               判定「当前是谁在说话」，对目标人片段做平滑衰减。适合轮流
               发言（如双人通话/面审）和窄带（电话 8 kHz 内容）音频——这类
               音频会让宽带 VoxCeleb 训练的 WeSep 退化。局限：两人同时
               说话的窗口无法区分，会被整体衰减或整体保留。

  wesep        波形减法：WeSep BSRNN 输出目标人在混音中那份贡献的相位对齐
               估计，逐采样计算 混音−目标估计 即得其余声源。适合宽带音频、
               真重叠语音，复用后端 BufferedWeSepTse 的全部流式机器
               （OLA 交叉淡入淡出、VAD 门控）。

用法（在 app/ 目录下，用 AudioTSE 环境的 python）：
  python scripts/suppress-target.py <注册音频> <混音音频> <输出.wav> \
      [--method aligned|gate|wesep] [--mode suppress|extract] [--cos-threshold 0.4] [--atten 0]

示例：
  python scripts/suppress-target.py audio/output-20.mp3 audio/202611090822510000000000000004.mp3 audio/mix-suppressed.wav
  python scripts/suppress-target.py audio/output-20.mp3 audio/202611090822510000000000000004.mp3 audio/mix-target.wav --mode extract

输入音频可以是任意 ffmpeg 支持的格式/采样率/声道（统一解码为 16 kHz
单声道 PCM16）；输出为 16 kHz 单声道 PCM16 wav。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]    # app/
sys.path.insert(0, str(ROOT / "backend"))     # 复用后端 audio_tse 包

SAMPLE_RATE = 16_000
# 与 server.py 一致的模型位置
TSE_DIR = ROOT / "models" / "wesep-bsrnn-ecapa-vox1"
VAD_MODEL = ROOT / "models" / "silero_vad" / "silero_vad.onnx"
SVM_MODEL = (ROOT / "models" / "sherpa-onnx-3dspeaker-speech-eres2net-base-sv-zh-cn-3dspeaker-16k"
             / "model.onnx")
# 每次喂入的音频块：2048 采样（128 ms），与实时链路的前端采集帧完全一致。
# 不能用大块喂入：BufferedWeSepTse._shed_backlog 假设生产者按小帧到达
# （输入缓冲上限 = 1 窗 + 0.5 s 容忍 = 21000 采样），大块喂入会让
# 「残余 + 新块」超过上限而被误判为 CPU 跟不上实时，触发丢帧。
CHUNK_SAMPLES = 2048
# 进度打印间隔（秒）
PROGRESS_SECONDS = 30
MIN_ENROLLMENT_SECONDS = 3.0
# gate 方法的参数。窗口取 3 s：实测电话窄带音频上 1.5 s 窗的余弦相似度
# 抖动大（同人窗会跌到 0.3x，阈值判定破碎、衰减不干净），3 s 窗下同人
# 稳定在 0.6-0.95、他人 ≈0，边界清晰。代价是说话人切换处有最长约
# 一个窗的过渡带（增益线性渐变，无可听硬切）。
GATE_WINDOW_SECONDS = 3.0    # 声纹判定窗口
GATE_HOP_SECONDS = 1.0       # 判定步长：决定边界过渡的粒度
GATE_RMS_FLOOR = 0.005       # 低于此能量的窗口视为静音：不判声纹、原样保留
# 迟滞双阈值：cos ≥ enter 判为目标人；已判定为目标人后，cos 要跌破 exit
# 才判为其他人。实测同一人说话中途个别窗的 cos 会从 0.7+ 短暂跌到
# 0.2-0.35（内容/信道起伏），单阈值会把这几窗误放行，留下泄漏段；
# 迟滞让「同人中途掉分」不掉线，而真正的说话人切换（cos 骤降到 ≈0）
# 能立即退出。
GATE_COS_ENTER = 0.40
GATE_COS_EXIT_DROP = 0.20    # exit = enter − 此值
# aligned 方法的参数。实测数字人回放在混音中与注册波形逐采样一致
# （相关系数 1.000），但不同批次录音可能经过不同的转码链路，因此逐块
# （而非整段）估计增益、并在小范围内重新对齐，容忍缓慢的电平变化与
# 时钟漂移；单块相关过低的直接跳过（宁可不减也不错减，错减会把人声
# 减出伪影）。
MATCH_BLOCK_SECONDS = 5      # 逐块对齐/增益估计的块长
MATCH_LOCAL_SEARCH = 1600    # 块内重新对齐的局部搜索半径（采样，±0.1 s）
MATCH_CORR_FLOOR = 0.80      # 全局命中的归一化相关阈值
MATCH_BLOCK_FLOOR = 0.50     # 单块相关低于此值则跳过该块
MATCH_MAX_OCCURRENCES = 6    # 回放可能出现多次：最多搜索/相减的次数
MATCH_SILENCE_RMS = 0.001    # 局部 RMS 低于此值的起点视为静音：相关置 0（防数值噪声假峰）


def decode_pcm16(path: Path) -> bytes:
    """任意音频 → 16 kHz 单声道 s16le 原始字节（ffmpeg 子进程解码）。"""
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path),
         "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"],
        capture_output=True, check=True)
    raw = result.stdout
    if len(raw) % 2:      # 奇数字节说明被截断：丢弃最后一个不完整采样
        raw = raw[:-1]
    return raw


def rms(pcm16: bytes) -> float:
    samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0
    return float(np.sqrt(np.mean(samples * samples) + 1e-12))


def write_wav(path: Path, pcm16: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm16)


def _median3(x: np.ndarray) -> np.ndarray:
    """三点中值滤波：抑制孤立的误判窗口（前后邻居都不同意时听邻居的）。"""
    if len(x) < 3:
        return x.copy()
    stacked = np.stack([np.r_[x[:1], x[:-1]], x, np.r_[x[1:], x[-1:]]])
    return np.median(stacked, axis=0)


def suppress_by_gate(enroll_pcm: bytes, mix_pcm: bytes, mode: str,
                     cos_threshold: float, atten: float) -> tuple[bytes, dict]:
    """声纹反门控：逐窗判定说话人，对目标人片段乘平滑增益。

    返回 (输出 PCM16, 统计信息)。增益曲线在判定点之间线性插值，
    使说话人切换处是渐变过渡而不是硬切（硬切会有可听的「咔」）。
    """
    import sherpa_onnx

    config = sherpa_onnx.SpeakerEmbeddingExtractorConfig()
    config.model = str(SVM_MODEL)
    config.num_threads = 4
    extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)

    def embed(samples: np.ndarray) -> np.ndarray | None:
        stream = extractor.create_stream()
        stream.accept_waveform(SAMPLE_RATE, np.ascontiguousarray(samples, dtype=np.float32))
        if extractor.is_ready(stream):
            return extractor.compute(stream)
        return None  # 音频太短算不出嵌入

    def to_float(pcm: bytes) -> np.ndarray:
        return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0

    enroll = to_float(enroll_pcm)
    reference = embed(enroll)
    if reference is None:
        raise SystemExit("注册音频过短，无法计算声纹嵌入")

    mix = to_float(mix_pcm)
    win = int(SAMPLE_RATE * GATE_WINDOW_SECONDS)
    hop = int(SAMPLE_RATE * GATE_HOP_SECONDS)
    exit_threshold = cos_threshold - GATE_COS_EXIT_DROP

    centers: list[int] = []       # 各判定窗口的中心采样位置
    target_flags: list[float] = []  # 1=目标人在说，0=其他人/静音
    cosines: list[float] = []
    is_target = False             # 迟滞状态机的当前状态
    start = time.perf_counter()
    for left in range(0, len(mix) - win + 1, hop):
        seg = mix[left:left + win]
        centers.append(left + win // 2)
        if float(np.sqrt(np.mean(seg * seg))) < GATE_RMS_FLOOR:
            # 静音窗：保持当前状态（说话人不会因为喘口气就换人），
            # 只是不产生新的相似度观测
            target_flags.append(float(is_target))
            cosines.append(float("nan"))
            continue
        emb = embed(seg)
        if emb is None:
            target_flags.append(float(is_target))
            cosines.append(float("nan"))
            continue
        cos = float(np.dot(reference, emb)
                    / (np.linalg.norm(reference) * np.linalg.norm(emb) + 1e-12))
        cosines.append(cos)
        if not is_target and cos >= cos_threshold:
            is_target = True       # 迟滞：进入目标态
        elif is_target and cos < exit_threshold:
            is_target = False      # 迟滞：跌出下阈值才退出
        target_flags.append(float(is_target))

    # 补一个落在尾部残余音频上的判定点，保证增益曲线覆盖到文件末尾
    if mix.size and (not centers or centers[-1] < len(mix) - hop):
        tail = mix[-win:]
        if float(np.sqrt(np.mean(tail * tail))) >= GATE_RMS_FLOOR:
            emb = embed(tail)
            if emb is not None:
                cos = float(np.dot(reference, emb)
                            / (np.linalg.norm(reference) * np.linalg.norm(emb) + 1e-12))
                cosines.append(cos)
                centers.append(max(len(mix) - win // 2, centers[-1] + hop if centers else 0))
                if not is_target and cos >= cos_threshold:
                    is_target = True
                elif is_target and cos < exit_threshold:
                    is_target = False
                target_flags.append(float(is_target))

    if not centers:  # 整条混音都没能量：原样返回
        return mix_pcm, {"target_ratio": 0.0, "elapsed": 0.0}

    flags = _median3(np.array(target_flags, dtype=np.float32))

    # 判定点 → 逐采样增益：在相邻判定点之间线性插值，边界向外延伸常值
    center_arr = np.array(centers, dtype=np.float32)
    samples_axis = np.arange(len(mix), dtype=np.float32)
    target_prob = np.interp(samples_axis, center_arr, flags)
    gain = 1.0 - target_prob * (1.0 - atten)
    if mode == "extract":
        # 提取=只留目标人：增益反过来（目标人原声、其他人压到底）
        gain = atten + target_prob * (1.0 - atten)

    out = np.clip(mix * gain, -1.0, 1.0)
    elapsed = time.perf_counter() - start
    stats = {
        "windows": len(flags),
        "target_ratio": float(np.mean(target_prob > 0.5)) if len(mix) else 0.0,
        "elapsed": elapsed,
        "cosines": [c for c in cosines if c == c],  # 去掉 nan（静音窗）
    }
    return (out * 32767.0).astype("<i2").tobytes(), stats


def _normalized_xcorr(window: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """归一化互相关曲线：window 每个可能起点对 ref 的相关系数。

    FFT 卷积一次算出所有起点的点积与局部能量，避免逐起点滑动窗口。
    近静音区域（局部能量过低）的分母会被数值噪声主导，相关系数爆炸出
    假峰值（实测能到 4.8，盖过真匹配）——这些位置直接置 0，并裁剪到
    [-1, 1] 兜底。
    """
    from scipy.signal import fftconvolve

    n = len(ref)
    corr = fftconvolve(window, ref[::-1], mode="valid")
    energy = fftconvolve(window * window, np.ones(n), mode="valid")
    nc = corr / (np.sqrt(np.maximum(energy, 1e-12)) * (np.linalg.norm(ref) + 1e-12))
    silence = energy < n * (MATCH_SILENCE_RMS ** 2)
    nc[silence] = 0.0
    return np.clip(nc, -1.0, 1.0)


def _subtract_occurrence(mix: np.ndarray, enroll: np.ndarray, start: int) -> tuple[int, int]:
    """把 mix 中 start 处的 enroll 波形逐块减掉（原地修改 mix）。

    逐块（而非整段）做局部重对齐 + 最小二乘增益，容忍回放电平缓慢变化
    与轻微时钟漂移；单块相关低于 MATCH_BLOCK_FLOOR 的跳过（宁可不减
    也不错减）。返回 (命中块数, 总块数)。
    """
    block = int(SAMPLE_RATE * MATCH_BLOCK_SECONDS)
    blocks_hit = blocks_total = 0
    for off in range(0, len(enroll), block):
        n = min(block, len(enroll) - off)
        ref = enroll[off:off + n]
        lo = max(0, start + off - MATCH_LOCAL_SEARCH)
        hi = min(len(mix), start + off + n + MATCH_LOCAL_SEARCH)
        window = mix[lo:hi]
        blocks_total += 1
        if len(window) < n:
            continue
        nc = _normalized_xcorr(window, ref)
        j = int(np.argmax(nc))
        if float(nc[j]) < MATCH_BLOCK_FLOOR:
            continue
        seg = window[j:j + n]
        gain = float(np.dot(seg, ref) / (np.dot(ref, ref) + 1e-12))
        window[j:j + n] -= gain * ref   # window 是 mix 的视图：原地相减
        blocks_hit += 1
    return blocks_hit, blocks_total


def suppress_by_aligned(enroll_pcm: bytes, mix_pcm: bytes, mode: str,
                        cos_threshold: float, atten: float) -> tuple[bytes, dict]:
    """对齐相减 + 声纹兜底：数字人/固定回放场景的反向 TSE。

    1) 全局互相关在混音中定位注册波形（可多次出现），逐块增益估计后
       逐采样相减。线性叠加原理使重叠语音同样被精确去除。
    2) 相减后的残差再过声纹反门控：回放库没覆盖的目标人语音（同声线、
       不同内容）静音兜底。
    """
    def to_float(pcm: bytes) -> np.ndarray:
        return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0

    enroll = to_float(enroll_pcm)
    mix = to_float(mix_pcm).copy()          # 可写副本：原地相减
    occurrences: list[dict] = []
    for _ in range(MATCH_MAX_OCCURRENCES):
        if len(mix) < len(enroll):
            break
        nc = _normalized_xcorr(mix, enroll)
        start = int(np.argmax(nc))
        if float(nc[start]) < MATCH_CORR_FLOOR:
            break                           # 找不到下一处回放：结束
        hit, total = _subtract_occurrence(mix, enroll, start)
        occurrences.append({"start": start, "corr": float(nc[start]),
                            "blocks": f"{hit}/{total}"})

    # 残差 → 声纹兜底（沿用 gate 的全部判定/平滑逻辑）
    residual_pcm = (np.clip(mix, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    out_pcm, gate_stats = suppress_by_gate(enroll_pcm, residual_pcm, "suppress",
                                           cos_threshold, atten)
    if mode == "extract":
        # 提取 = 原混音 − 消除结果（被去掉的正是目标人：对齐减掉的回放 + 兜底静音段）
        removed = to_float(mix_pcm) - to_float(out_pcm)
        out_pcm = (np.clip(removed, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()

    elapsed = gate_stats.get("elapsed", 0.0)
    stats = {
        "elapsed": elapsed,
        "occurrences": occurrences,
        "cosines": gate_stats.get("cosines") or [],
        "target_ratio": gate_stats.get("target_ratio", 0.0),
        "windows": gate_stats.get("windows", 0),
    }
    return out_pcm, stats


def run_wesep(enroll_pcm: bytes, mix_pcm: bytes, mode: str) -> tuple[bytes, dict]:
    """WeSep 波形减法/提取：复用后端 BufferedWeSepTse 的流式机器。"""
    from audio_tse.tse import BufferedWeSepTse, TseModel, WeSepTseEngine

    model = TseModel("wesep_bsrnn", "WeSep BSRNN", TSE_DIR)
    if not model.available:
        raise SystemExit(f"WeSep TSE 不可用：{model.unavailable_reason}")
    print("加载 WeSep BSRNN 引擎（约 5 秒）…")
    engine = WeSepTseEngine(model)
    tse = BufferedWeSepTse(engine, enroll_pcm,
                           VAD_MODEL if VAD_MODEL.exists() else None, mode)

    chunk_bytes = CHUNK_SAMPLES * 2
    outputs: list[bytes] = []
    start = time.perf_counter()
    next_report = PROGRESS_SECONDS
    mix_sec = len(mix_pcm) / (SAMPLE_RATE * 2)
    for offset in range(0, len(mix_pcm), chunk_bytes):
        outputs.extend(tse.accept_pcm16(mix_pcm[offset:offset + chunk_bytes]))
        done_sec = min(offset + chunk_bytes, len(mix_pcm)) / (SAMPLE_RATE * 2)
        if done_sec >= next_report or done_sec >= mix_sec:
            next_report += PROGRESS_SECONDS
            elapsed = time.perf_counter() - start
            print(f"  {done_sec:6.1f}s / {mix_sec:.1f}s（已处理 {elapsed:.0f}s，RTF {elapsed / done_sec:.2f}）")
    outputs.extend(tse.flush())
    elapsed = time.perf_counter() - start
    # 窄带音频会让宽带训练的 BSRNN 输出「反相缩小的混音副本」（实测相关
    # 度 −0.97，减法反而放大混音）。检测到这种失效时明确告警，而不是让
    # 用户拿到一个被放大的「消除结果」。判据：消除模式下输出能量不降反升
    # ——正确的消除只能减少或保持总能量（整体相关会被静音直通段稀释，
    # 不如能量判据稳健）。
    out_pcm = b"".join(outputs)
    o = np.frombuffer(out_pcm, dtype="<i2").astype(np.float32)
    m = np.frombuffer(mix_pcm[:len(out_pcm)], dtype="<i2").astype(np.float32)
    if len(o) > SAMPLE_RATE:
        ratio = float(np.sqrt(np.mean(o * o)) / (np.sqrt(np.mean(m * m)) + 1e-12))
        if mode == "suppress" and ratio > 1.05:
            print(f"  ⚠ 警告：消除模式输出能量比输入高 {(ratio - 1) * 100:.0f}%——"
                  "典型的窄带失效特征（BSRNN 输出反相副本，减法变放大），结果不可用。"
                  "请改用 --method aligned 或 gate。")
    stats = {"elapsed": elapsed, "silent_windows": tse.silent_windows,
             "dropped_seconds": tse.dropped_seconds}
    return out_pcm, stats


def main() -> None:
    parser = argparse.ArgumentParser(description="离线反向 TSE：消除混音中的注册目标人")
    parser.add_argument("enrollment", type=Path, help="目标人注册音频（纯音）")
    parser.add_argument("mixture", type=Path, help="混音音频")
    parser.add_argument("output", type=Path, help="输出 wav 路径（16 kHz 单声道 PCM16）")
    parser.add_argument("--method", choices=["aligned", "gate", "wesep"], default="aligned",
                        help="aligned=对齐相减+声纹兜底（默认；数字人/固定回放场景，重叠语音也能精确去除）；"
                             "gate=声纹反门控（轮流发言/窄带）；wesep=波形减法（宽带/真重叠）")
    parser.add_argument("--mode", choices=["suppress", "extract"], default="suppress",
                        help="suppress=消除目标人（默认）；extract=正向提取目标人（对照试听用）")
    parser.add_argument("--cos-threshold", type=float, default=GATE_COS_ENTER,
                        help=f"gate 方法判「目标人」的进入阈值（默认 {GATE_COS_ENTER}；"
                             f"退出阈值为进入阈值 - {GATE_COS_EXIT_DROP} 的迟滞判定）")
    parser.add_argument("--atten", type=float, default=0.0,
                        help="gate 方法对目标人片段的衰减系数（默认 0=完全静音。"
                             "注意：非零残留（如 0.03≈-30dB）人耳近无声，"
                             "但流式 ASR 仍可能把它转写出来——需要防转写就保持 0）")
    args = parser.parse_args()

    for path in (args.enrollment, args.mixture):
        if not path.exists():
            raise SystemExit(f"输入文件不存在：{path}")

    enroll_pcm = decode_pcm16(args.enrollment)
    mix_pcm = decode_pcm16(args.mixture)
    enroll_sec = len(enroll_pcm) / (SAMPLE_RATE * 2)
    mix_sec = len(mix_pcm) / (SAMPLE_RATE * 2)
    print(f"注册音频 {args.enrollment.name}: {enroll_sec:.1f}s")
    print(f"混音音频 {args.mixture.name}: {mix_sec:.1f}s")
    if enroll_sec < MIN_ENROLLMENT_SECONDS:
        raise SystemExit(f"注册语音至少需要 {MIN_ENROLLMENT_SECONDS:.0f} 秒（当前 {enroll_sec:.1f}s）")

    print(f"方法 {args.method}，模式 {args.mode}…")
    if args.method == "aligned":
        out_pcm, stats = suppress_by_aligned(enroll_pcm, mix_pcm, args.mode,
                                             args.cos_threshold, args.atten)
        for occ in stats.get("occurrences") or []:
            print(f"  命中回放 @ {occ['start'] / SAMPLE_RATE:.2f}s"
                  f"（全局相关 {occ['corr']:+.3f}，匹配块 {occ['blocks']}）")
        if not (stats.get("occurrences") or []):
            print("  未在混音中找到与注册波形一致的回放（相关 < "
                  f"{MATCH_CORR_FLOOR}），仅执行声纹兜底")
    elif args.method == "gate":
        out_pcm, stats = suppress_by_gate(enroll_pcm, mix_pcm, args.mode,
                                          args.cos_threshold, args.atten)
    else:
        out_pcm, stats = run_wesep(enroll_pcm, mix_pcm, args.mode)
    cosines = stats.get("cosines") or []
    if cosines:
        lo, hi = min(cosines), max(cosines)
        print(f"  声纹相似度范围 [{lo:+.3f}, {hi:+.3f}]（阈值 {args.cos_threshold}）")

    write_wav(args.output, out_pcm)

    print(f"\n完成：{args.output}")
    print(f"  输出时长 {len(out_pcm) / (SAMPLE_RATE * 2):.1f}s（输入 {mix_sec:.1f}s）")
    print(f"  处理耗时 {stats['elapsed']:.1f}s，RTF {stats['elapsed'] / mix_sec:.2f}")
    print(f"  RMS 输入 {rms(mix_pcm):.4f} → 输出 {rms(out_pcm):.4f}")
    if "target_ratio" in stats:
        print(f"  判为目标人的时间占比 {stats['target_ratio'] * 100:.0f}%（共 {stats['windows']} 个判定窗）")
    if "silent_windows" in stats:
        print(f"  静音直通窗口 {stats['silent_windows']} 个，积压丢弃 {stats['dropped_seconds']:.2f}s")


if __name__ == "__main__":
    main()

