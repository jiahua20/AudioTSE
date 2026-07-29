from __future__ import annotations

import importlib.util
import os
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np


SAMPLE_RATE = 16_000
# 分块推理。本 BSRNN 是在线/因果变体（配置：bsrnn_online.yaml），
# 因此对短窗口有较好的容忍度。WINDOW_SECONDS=1 以约 5 dB 的 SI-SDR
# 损失（测试样本 12.6→7.3 dB）换取首字延迟减半（约 3.4 s → 1.7 s），
# 且 RTF 基本不变（约 0.8）。窗口越小首字越快，但低于约 0.75 s 时
# 分离质量会急剧下降。
WINDOW_SECONDS = 1
# 相邻窗口重叠 CROSSFADE_SECONDS 并互相交叉淡入淡出，使两段独立估计的
# BSRNN 掩码之间的拼接缝被平滑过渡，而不是硬拼接（硬拼接会丢失/失真
# 落在边界上的那个词）。hop = 窗口 - 交叉淡入淡出，因此 RTF 仍保持
# 约 window/hop，而不会像 50% 重叠的 OLA 那样翻倍。
CROSSFADE_SECONDS = 0.1
# 实时麦克风流永远不会暂停。当输入缓冲超过一个窗口加此容忍量时，
# 说明 CPU 已跟不上实时——因此丢弃最旧的溢出部分，只分离最新窗口。
# 这样可以把端到端延迟控制在有界范围内，而不是在实时流上无限增长；
# 被跳过的中间音频只是不被转写。在正常负载下为无操作，此时 FIFO 的
# 抖动仍落在容忍量内（缓冲通常在 [hop 残余, 窗口] 之间波动）。
BACKLOG_TOLERANCE_SECONDS = 0.5
# VAD 门控。语音帧占比低于此阈值的窗口被视为静音窗口：直接放行原始音频
# （乘以合成窗，使其仍能通过 OLA 交叉淡入淡出），并完全跳过 BSRNN 前向
# 推理。这样在实时麦克风中，长时间静音段的开销约为 0 CPU，而不是每个
# hop 都做一次前向推理。
VOICE_RATIO_THRESHOLD = 0.05


@dataclass(frozen=True)
class TseModel:
    id: str
    name: str
    directory: Path

    @property
    def model_files_ready(self) -> bool:
        return all((self.directory / filename).exists() for filename in ("config.yaml", "avg_model.pt"))

    @property
    def dependency_ready(self) -> bool:
        return importlib.util.find_spec("torch") is not None and importlib.util.find_spec("wesep") is not None

    @property
    def available(self) -> bool:
        return self.model_files_ready and self.dependency_ready

    @property
    def unavailable_reason(self) -> str | None:
        if not self.model_files_ready:
            return "缺少 WeSep BSRNN 权重；运行 scripts/install-wesep-tse.ps1 安装"
        if not self.dependency_ready:
            return "缺少 PyTorch/WeSep；运行 scripts/install-wesep-tse.ps1 安装"
        return None


class WeSepTseEngine:
    """会话级共享引擎：BSRNN 权重只加载一次，多次提取/换人复用。

    约 262 MB 的 BSRNN 权重与「提取哪个注册人」完全无关，因此提到连接/
    会话级加载一次。换人、停止后重新提取时复用本引擎，只重算注册嵌入
    （见 BufferedWeSepTse），省掉每次重新 load_model_local 的开销——
    这是「每次点击开始提取都很久」的主要可优化项。

    注意：VAD 不放在这里。Silero VAD 的检测器内部有缓冲状态，跨注册人
    复用会让上一个人的尾音泄漏到下一个人的语音判定，所以 VAD 由
    BufferedWeSepTse 每次提取新建。
    """

    def __init__(self, model: TseModel) -> None:
        if not model.available:
            raise RuntimeError(model.unavailable_reason or "WeSep TSE 不可用")

        from .wesep_loader import ensure_wesep_runtime

        ensure_wesep_runtime()

        import torch
        from wesep import load_model_local

        self._torch = torch
        self._torch.set_num_threads(os.cpu_count() or 4)
        self._extractor = load_model_local(str(model.directory))
        self._extractor.set_device("cpu")
        self._extractor.set_resample_rate(SAMPLE_RATE)
        self._extractor.set_vad(False)
        # OLA 要求各窗口振幅保持一致；逐窗口归一化会使每个窗口的增益在
        # 拼接缝处跳变。限幅仅作为安全兜底。
        self._extractor.set_output_norm(False)
        self._device = self._extractor.device

        self._window_samples = int(SAMPLE_RATE * WINDOW_SECONDS)
        self._cf_samples = max(1, int(SAMPLE_RATE * CROSSFADE_SECONDS))
        self._hop_samples = self._window_samples - self._cf_samples
        # 线性斜坡分析/合成窗：在重叠区 ramp_up + ramp_down == 1，
        # 从而重叠相加可重构为单位增益。
        ramp = np.linspace(0.0, 1.0, self._cf_samples, endpoint=False, dtype=np.float32)
        win = np.ones(self._window_samples, dtype=np.float32)
        win[: self._cf_samples] = ramp
        win[-self._cf_samples:] = 1.0 - ramp
        self._synth_win = win

        self._max_buf_samples = self._window_samples + int(SAMPLE_RATE * BACKLOG_TOLERANCE_SECONDS)

    def _to_tensor(self, pcm16: bytes):
        samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0
        return self._torch.from_numpy(samples).unsqueeze(0)

    def compute_enroll_embedding(self, enrollment_pcm16: bytes):
        """用注册语音算一次 ECAPA 说话人嵌入。换人时重算；因权重已在引擎里
        加载好，这里只剩一次 ECAPA 前向（很快）。"""
        torch = self._torch
        model = self._extractor.model
        enroll = self._to_tensor(enrollment_pcm16).to(self._device)
        feats = self._extractor.compute_fbank(enroll, sample_rate=SAMPLE_RATE, cmn=True)
        feats = feats.unsqueeze(0).to(self._device)
        with torch.no_grad():
            tmp = model.spk_model(feats)
            emb = tmp[-1] if isinstance(tmp, tuple) else tmp
            spk_embedding = model.spk_transform(emb)
            spk_embedding = spk_embedding.unsqueeze(1).unsqueeze(3)
        return spk_embedding

    def _forward_cached(self, wav_input, spk_embedding):
        """直接注入注册嵌入执行 BSRNN 前向推理，跳过逐窗口的 ECAPA 调用。
        与 wesep.models.bsrnn.BSRNN.forward 的逻辑保持一致。"""
        torch = self._torch
        model = self._extractor.model
        batch_size, nsample = wav_input.shape
        nch = 1
        win, stride = model.win, model.stride
        window = torch.hann_window(win).to(wav_input.device).type(wav_input.type())
        spec = torch.stft(wav_input, n_fft=win, hop_length=stride,
                          window=window, return_complex=True)
        spec_RI = torch.stack([spec.real, spec.imag], 1)
        subband_spec, subband_mix_spec = [], []
        band_idx = 0
        for i in range(len(model.band_width)):
            bw = model.band_width[i]
            subband_spec.append(spec_RI[:, :, band_idx:band_idx + bw].contiguous())
            subband_mix_spec.append(spec[:, band_idx:band_idx + bw])
            band_idx += bw
        subband_feature = []
        for i, bn_func in enumerate(model.BN):
            bw = model.band_width[i]
            subband_feature.append(bn_func(subband_spec[i].view(batch_size * nch, bw * 2, -1)))
        subband_feature = torch.stack(subband_feature, 1)
        sep_output = model.separator(subband_feature, spk_embedding,
                                     torch.tensor(nch))
        sep_subband_spec = []
        for i, mask_func in enumerate(model.mask):
            bw = model.band_width[i]
            this_output = mask_func(sep_output[:, i]).view(batch_size * nch, 2, 2, bw, -1)
            this_mask = this_output[:, 0] * torch.sigmoid(this_output[:, 1])
            est_real = (subband_mix_spec[i].real * this_mask[:, 0]
                        - subband_mix_spec[i].imag * this_mask[:, 1])
            est_imag = (subband_mix_spec[i].real * this_mask[:, 1]
                        + subband_mix_spec[i].imag * this_mask[:, 0])
            sep_subband_spec.append(torch.complex(est_real, est_imag))
        est_spec = torch.cat(sep_subband_spec, 1)
        output = torch.istft(est_spec.view(batch_size * nch, model.enc_dim, -1),
                             n_fft=win, hop_length=stride, window=window, length=nsample)
        s = output.view(batch_size, nch, -1).squeeze(dim=1)
        if self._extractor.output_norm:
            s = s / s.abs().max(dim=1, keepdim=True).values * 0.9
        return s

    def extract_float(self, samples: np.ndarray, spk_embedding) -> np.ndarray:
        wav = self._torch.from_numpy(
            np.ascontiguousarray(samples, dtype=np.float32)
        ).unsqueeze(0).to(self._device)
        with self._torch.no_grad():
            s = self._forward_cached(wav, spk_embedding)
        return s.detach().cpu().numpy().reshape(-1)

    def warmup(self) -> None:
        """跑一次端到端空转（ECAPA 注册嵌入 + 单窗 BSRNN 前向），触发 PyTorch 的
        惰性内存分配与内核选择，使首次真实提取不再额外卡那约 0.5–1 秒的首次推理。
        在连接级预加载阶段调用；输入输出均为静音，结果丢弃。"""
        silence_pcm16 = np.zeros(self._window_samples * 3, dtype="<i2").tobytes()
        embedding = self.compute_enroll_embedding(silence_pcm16)
        self.extract_float(np.zeros(self._window_samples, dtype=np.float32), embedding)


class BufferedWeSepTse:
    """基于固定窗口与重叠相加（overlap-add）的流式音频参考 TSE。

    每个窗口独立分离（使用缓存的注册嵌入）；相邻窗口重叠
    CROSSFADE_SECONDS 并进行交叉淡入淡出，使两段独立估计的掩码之间的
    拼接缝被平滑过渡，而不是硬拼接（硬拼接会明显丢失/失真落在边界上的
    那个词）。

    本对象只持有「与本次注册人有关」的状态（注册嵌入 + OLA 缓冲 + 本次
    的 VAD）；权重等与具体人无关的重资源由 WeSepTseEngine 在会话级加载
    一次后传入复用。
    """

    def __init__(self, engine: WeSepTseEngine, enrollment_pcm16: bytes,
                 vad_model: Path | None = None) -> None:
        self._engine = engine
        self._cached_spk_embedding = engine.compute_enroll_embedding(enrollment_pcm16)
        self._in_buf = np.array([], dtype=np.float32)
        self._out_overlap = np.array([], dtype=np.float32)
        # VAD 门控：逐帧语音标志，与 _in_buf 一一对应，使窗口可被判定为
        # 静音（无语音）从而旁路 BSRNN 前向推理。每次提取新建（检测器有
        # 内部缓冲状态，不能跨注册人复用）；模型缺失时为 None——此时每个
        # 窗口都走前向（安全默认）。
        self._vad = self._create_vad(vad_model)
        self._buf_voice = np.array([], dtype=np.bool_)
        self.last_extract_ms = 0.0
        self.dropped_seconds = 0.0
        self.silent_windows = 0

    # 引擎上与注册人无关的常量的代理，使下面的流式逻辑保持简洁。
    @property
    def _window_samples(self) -> int:
        return self._engine._window_samples

    @property
    def _cf_samples(self) -> int:
        return self._engine._cf_samples

    @property
    def _hop_samples(self) -> int:
        return self._engine._hop_samples

    @property
    def _synth_win(self) -> np.ndarray:
        return self._engine._synth_win

    @property
    def _max_buf_samples(self) -> int:
        return self._engine._max_buf_samples

    @staticmethod
    def _create_vad(vad_model: Path | None):
        """用于静音窗口门控的 Silero VAD。若模型缺失或 sherpa_onnx 不可导入，
        则返回 None（此时每个窗口都走前向推理）。"""
        if vad_model is None or not Path(vad_model).exists():
            return None
        try:
            import sherpa_onnx
            config = sherpa_onnx.VadModelConfig()
            config.silero_vad.model = str(vad_model)
            config.silero_vad.threshold = 0.5
            config.silero_vad.min_silence_duration = 0.25
            config.silero_vad.min_speech_duration = 0.1
            config.silero_vad.max_speech_duration = 20.0
            config.sample_rate = SAMPLE_RATE
            config.provider = "cpu"
            config.num_threads = 1
            return sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=30)
        except Exception:
            return None

    @property
    def buffered_seconds(self) -> float:
        return (len(self._in_buf) + len(self._out_overlap)) / SAMPLE_RATE

    @staticmethod
    def _to_pcm16(samples: np.ndarray) -> bytes:
        samples = np.nan_to_num(samples, nan=0.0, posinf=1.0, neginf=-1.0)
        return (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()

    def _ola_add(self, seg: np.ndarray) -> None:
        n = len(seg)
        if len(self._out_overlap) < n:
            self._out_overlap = np.concatenate(
                [self._out_overlap, np.zeros(n - len(self._out_overlap), dtype=np.float32)]
            )
        self._out_overlap[:n] += seg

    def _ola_take(self, hop: int) -> np.ndarray:
        ready = self._out_overlap[:hop].copy()
        self._out_overlap = self._out_overlap[hop:]
        return ready

    def _shed_backlog(self) -> None:
        """当输入缓冲超过一个窗口加容忍量时，丢弃最旧的溢出部分——即在实时流上
        CPU 已跟不上实时。只保留最新窗口以使延迟有界；被跳过的中间音频不会被
        转写。在正常负载下为无操作。"""
        if len(self._in_buf) <= self._max_buf_samples:
            return
        drop = len(self._in_buf) - self._window_samples
        self._in_buf = self._in_buf[drop:]
        self._buf_voice = self._buf_voice[drop:]
        self.dropped_seconds += drop / SAMPLE_RATE

    def accept_pcm16(self, chunk: bytes) -> list[bytes]:
        samples = np.frombuffer(chunk, dtype="<i2").astype(np.float32) / 32768.0
        self._in_buf = np.concatenate([self._in_buf, samples])
        # 本数据块的语音活动标志，与 _in_buf 一一对应，使每个窗口可按其
        # 窗内语音占比进行分类。
        if self._vad is not None:
            self._vad.accept_waveform(np.ascontiguousarray(samples, dtype=np.float32))
            voice = bool(self._vad.is_speech_detected())
        else:
            voice = True
        self._buf_voice = np.concatenate(
            [self._buf_voice, np.full(len(samples), voice, dtype=np.bool_)]
        )
        self._shed_backlog()
        outputs: list[bytes] = []
        while len(self._in_buf) >= self._window_samples:
            window = self._in_buf[: self._window_samples]
            voice_ratio = (
                float(np.mean(self._buf_voice[: self._window_samples]))
                if self._vad is not None else 1.0
            )
            if voice_ratio < VOICE_RATIO_THRESHOLD:
                # 静音窗口：放行原始音频，仍乘以合成窗以使 OLA 在拼接缝处
                # 继续交叉淡入淡出，并完全跳过 BSRNN 前向推理——开销约 0 CPU
                # 而非一次前向。
                self.silent_windows += 1
                seg = window * self._synth_win
            else:
                start = time.perf_counter()
                seg = self._engine.extract_float(window, self._cached_spk_embedding) * self._synth_win
                self.last_extract_ms = (time.perf_counter() - start) * 1000.0
            self._ola_add(seg)
            outputs.append(self._to_pcm16(self._ola_take(self._hop_samples)))
            self._in_buf = self._in_buf[self._hop_samples:]
            self._buf_voice = self._buf_voice[self._hop_samples:]
        return outputs

    def flush(self) -> list[bytes]:
        """排空尾部：分离缓冲区中的剩余部分（补零到完整窗口），只输出重叠中
        真实音频对应的部分，使尾部词语既不被丢弃，也不会被静音填充伪影污染。"""
        outputs: list[bytes] = []
        tail_len = len(self._in_buf)
        take = len(self._out_overlap)  # 无新数据 -> 输出已暂存的重叠（淡出）
        if tail_len >= int(SAMPLE_RATE * 2 * 0.3):
            n = min(tail_len, self._window_samples)
            window = np.zeros(self._window_samples, dtype=np.float32)
            window[:n] = self._in_buf[:n]
            start = time.perf_counter()
            seg = self._engine.extract_float(window, self._cached_spk_embedding) * self._synth_win
            self._ola_add(seg)
            self.last_extract_ms = (time.perf_counter() - start) * 1000.0
            # 真实音频占据 out_overlap[0:max(cf, n)]；其后为补零部分
            take = min(len(self._out_overlap), max(self._cf_samples, n))
        self._in_buf = np.array([], dtype=np.float32)
        self._buf_voice = np.array([], dtype=np.bool_)
        if take > 0:
            outputs.append(self._to_pcm16(self._out_overlap[:take].copy()))
        self._out_overlap = np.array([], dtype=np.float32)
        return outputs
