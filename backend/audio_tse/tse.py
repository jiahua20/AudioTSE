from __future__ import annotations

import importlib.util
import os
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np


SAMPLE_RATE = 16_000
WINDOW_SECONDS = 2
# Adjacent windows overlap by CROSSFADE_SECONDS and crossfade into each other,
# so the seam between two independently-estimated BSRNN masks is smoothed
# instead of butting together (which drops/distorts the word on the boundary).
# Hop = window - crossfade, so RTF stays ~window/hop (1.8s here) rather than
# doubling like a 50%-overlap OLA would.
CROSSFADE_SECONDS = 0.1


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


class BufferedWeSepTse:
    """Streaming audio-reference TSE over fixed windows with overlap-add.

    Each window is separated independently (cached enroll embedding); adjacent
    windows overlap by CROSSFADE_SECONDS and are crossfaded so the seam between
    two independently-estimated masks is smoothed, instead of butting them
    together (which audibly drops/distorts the word sitting on the boundary).
    """

    def __init__(self, model: TseModel, enrollment_pcm16: bytes) -> None:
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
        # OLA needs consistent amplitude across windows; per-window normalization
        # would make each window's gain jump at the seam. Clip is the safety net.
        self._extractor.set_output_norm(False)
        self._enrollment = self._to_tensor(enrollment_pcm16)
        self._device = self._extractor.device
        self._cached_spk_embedding = self._compute_enroll_embedding()

        self._window_samples = int(SAMPLE_RATE * WINDOW_SECONDS)
        self._cf_samples = max(1, int(SAMPLE_RATE * CROSSFADE_SECONDS))
        self._hop_samples = self._window_samples - self._cf_samples
        # Linear-ramp analysis/synthesis window: ramp_up + ramp_down == 1 on the
        # overlap, so overlap-add reconstructs unity (verified by _verify_ola.py).
        ramp = np.linspace(0.0, 1.0, self._cf_samples, endpoint=False, dtype=np.float32)
        win = np.ones(self._window_samples, dtype=np.float32)
        win[: self._cf_samples] = ramp
        win[-self._cf_samples:] = 1.0 - ramp
        self._synth_win = win

        self._in_buf = np.array([], dtype=np.float32)
        self._out_overlap = np.array([], dtype=np.float32)
        self.last_extract_ms = 0.0

    def _compute_enroll_embedding(self):
        torch = self._torch
        model = self._extractor.model
        enroll = self._enrollment.to(self._device)
        feats = self._extractor.compute_fbank(enroll, sample_rate=SAMPLE_RATE, cmn=True)
        feats = feats.unsqueeze(0).to(self._device)
        with torch.no_grad():
            tmp = model.spk_model(feats)
            emb = tmp[-1] if isinstance(tmp, tuple) else tmp
            spk_embedding = model.spk_transform(emb)
            spk_embedding = spk_embedding.unsqueeze(1).unsqueeze(3)
        return spk_embedding

    def _forward_cached(self, wav_input):
        """BSRNN forward with the enrollment embedding supplied directly, skipping
        the per-window ECAPA pass. Mirrors wesep.models.bsrnn.BSRNN.forward."""
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
        sep_output = model.separator(subband_feature, self._cached_spk_embedding,
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

    @property
    def buffered_seconds(self) -> float:
        return (len(self._in_buf) + len(self._out_overlap)) / SAMPLE_RATE

    def _to_tensor(self, pcm16: bytes):
        samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0
        return self._torch.from_numpy(samples).unsqueeze(0)

    def _extract_float(self, samples: np.ndarray) -> np.ndarray:
        wav = self._torch.from_numpy(
            np.ascontiguousarray(samples, dtype=np.float32)
        ).unsqueeze(0).to(self._device)
        with self._torch.no_grad():
            s = self._forward_cached(wav)
        return s.detach().cpu().numpy().reshape(-1)

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

    def accept_pcm16(self, chunk: bytes) -> list[bytes]:
        samples = np.frombuffer(chunk, dtype="<i2").astype(np.float32) / 32768.0
        self._in_buf = np.concatenate([self._in_buf, samples])
        outputs: list[bytes] = []
        while len(self._in_buf) >= self._window_samples:
            start = time.perf_counter()
            window = self._in_buf[: self._window_samples]
            seg = self._extract_float(window) * self._synth_win
            self._ola_add(seg)
            outputs.append(self._to_pcm16(self._ola_take(self._hop_samples)))
            self.last_extract_ms = (time.perf_counter() - start) * 1000.0
            self._in_buf = self._in_buf[self._hop_samples:]
        return outputs

    def flush(self) -> list[bytes]:
        """Drain the tail: separate the buffered remainder (zero-padded to a full
        window) and emit only the real-audio part of the overlap, so trailing
        words are neither dropped nor padded with silence artifacts."""
        outputs: list[bytes] = []
        tail_len = len(self._in_buf)
        take = len(self._out_overlap)  # nothing new -> emit held overlap (fades out)
        if tail_len >= int(SAMPLE_RATE * 2 * 0.3):
            n = min(tail_len, self._window_samples)
            window = np.zeros(self._window_samples, dtype=np.float32)
            window[:n] = self._in_buf[:n]
            start = time.perf_counter()
            seg = self._extract_float(window) * self._synth_win
            self._ola_add(seg)
            self.last_extract_ms = (time.perf_counter() - start) * 1000.0
            # real audio occupies out_overlap[0:max(cf, n)]; beyond is zero-pad
            take = min(len(self._out_overlap), max(self._cf_samples, n))
        self._in_buf = np.array([], dtype=np.float32)
        if take > 0:
            outputs.append(self._to_pcm16(self._out_overlap[:take].copy()))
        self._out_overlap = np.array([], dtype=np.float32)
        return outputs
