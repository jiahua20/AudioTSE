# 流式 ASR 封装：把 sherpa-onnx 的在线识别器（Zipformer transducer /
# Paraformer）包成「喂 PCM16 字节 → 返回 (增量文本, 是否句尾)」的简单接口。
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np


SAMPLE_RATE = 16_000
# paraformer/transducer 的流式解码在很大程度上是自回归的；基准测试表明
# 线程数越多反而越慢，因此保持较小的取值。
_ASR_THREADS = 2


@dataclass(frozen=True)
class AsrModel:
    """一个 ASR 模型的静态描述（路径 + 类型），以及「文件齐了没」的探测。"""

    id: str            # 模型标识（前端用来切换）
    name: str          # 展示名
    model_dir: Path    # 模型文件所在目录（models/ 下）
    kind: str          # "transducer"（zipformer）或 "paraformer"

    @property
    def available(self) -> bool:
        # 按模型类型检查各自的必需文件是否都已下载到位
        if self.kind == "paraformer":
            required = ["tokens.txt", "encoder.int8.onnx", "decoder.int8.onnx"]
        else:
            required = [
                "tokens.txt",
                "encoder-epoch-99-avg-1.int8.onnx",
                "decoder-epoch-99-avg-1.onnx",
                "joiner-epoch-99-avg-1.int8.onnx",
            ]
        return all((self.model_dir / filename).exists() for filename in required)


class StreamingAsr:
    """流式识别器：内部持有一个持续的识别流（stream），逐帧喂入音频。

    sherpa-onnx 的在线模型逐「chunk」出增量结果；endpoint 检测到停顿时
    报告句尾并重置流，避免上下文无限增长。"""

    def __init__(self, model: AsrModel) -> None:
        # 延迟导入：sherpa_onnx 只在真正用到 ASR 时才加载（加快启动）
        import sherpa_onnx

        self.model = model
        if model.kind == "paraformer":
            # Paraformer 中英双语：encoder + decoder 两段式，无需 joiner
            self._recognizer = sherpa_onnx.OnlineRecognizer.from_paraformer(
                tokens=str(model.model_dir / "tokens.txt"),
                encoder=str(model.model_dir / "encoder.int8.onnx"),
                decoder=str(model.model_dir / "decoder.int8.onnx"),
                num_threads=_ASR_THREADS,
                sample_rate=SAMPLE_RATE,
                feature_dim=80,              # 80 维 fbank 特征
                decoding_method="greedy_search",  # 贪心解码即可（流式不做 beam）
                provider="cpu",
            )
            # Paraformer 流式版有「右窗」上下文：结束时需要补约 0.66 s 静音，
            # 解码器才能把最后悬着的字吐出来
            self._tail_padding = 0.66
        else:
            # Zipformer transducer：encoder + decoder + joiner 三件套
            self._recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                tokens=str(model.model_dir / "tokens.txt"),
                encoder=str(model.model_dir / "encoder-epoch-99-avg-1.int8.onnx"),
                decoder=str(model.model_dir / "decoder-epoch-99-avg-1.onnx"),
                joiner=str(model.model_dir / "joiner-epoch-99-avg-1.int8.onnx"),
                num_threads=_ASR_THREADS,
                sample_rate=SAMPLE_RATE,
                feature_dim=80,
                decoding_method="greedy_search",
                enable_endpoint_detection=True,  # 启用句尾（停顿）检测
                provider="cpu",
            )
            self._tail_padding = 0.0  # transducer 无右窗，不需要补静音
        self._stream = self.create_stream()  # 本次识别的持续流
        self.last_feed_ms = 0.0              # 最近一次喂入的耗时（供 RTF 统计）

    def create_stream(self):
        # 每个识别会话（声纹门控的一段话）各开一个流，互不干扰
        return self._recognizer.create_stream()

    def feed(self, stream, samples: np.ndarray) -> str:
        """把一段 float32 采样喂进流并尽量解码，返回当前累计的识别文本。"""
        samples = np.ascontiguousarray(samples, dtype=np.float32)  # C 连续内存，C++ 侧直读
        stream.accept_waveform(SAMPLE_RATE, samples)
        # 流式解码是「有多少算多少」：只要解码器还能吐 token 就继续解
        while self._recognizer.is_ready(stream):
            self._recognizer.decode_stream(stream)
        return self.result(stream)

    def result(self, stream) -> str:
        # 当前累计的识别结果（从上次重置起算）
        return self._recognizer.get_result(stream).strip()

    def finish(self, stream) -> str:
        """流结束：可选地补尾部静音、冲刷解码器，返回最终文本。"""
        if self._tail_padding:
            # Paraformer 的右窗补偿：喂 0.66 s 静音把最后几个字顶出来
            stream.accept_waveform(
                SAMPLE_RATE,
                np.zeros(int(SAMPLE_RATE * self._tail_padding), dtype=np.float32),
            )
        stream.input_finished()  # 通知底层不会再有音频进来
        while self._recognizer.is_ready(stream):
            self._recognizer.decode_stream(stream)  # 把剩余的可解码部分全部解完
        return self.result(stream)

    def accept_pcm16(self, chunk: bytes) -> tuple[str, bool]:
        """入口：喂一帧 PCM16 字节，返回 (增量文本, 是否句尾)。

        PCM16 → float32（÷32768 归一到 [-1,1]）后进流；句尾时重置流，
        下一帧从新句开始。"""
        samples = np.frombuffer(chunk, dtype="<i2").astype(np.float32) / 32768.0
        start = time.perf_counter()          # 记录耗时供 RTF/性能面板
        text = self.feed(self._stream, samples)
        self.last_feed_ms = (time.perf_counter() - start) * 1000.0
        # endpoint 检测：当前停顿达到阈值 → 视为一句话结束
        final = self._recognizer.is_endpoint(self._stream)
        if final:
            self._recognizer.reset(self._stream)  # 重置流：下句从空上下文开始
        return text, final
