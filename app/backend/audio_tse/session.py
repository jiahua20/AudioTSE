# 会话状态机：跟踪「注册 → 就绪 → 提取」的生命周期，以及注册音频的暂存。
# 这是纯内存对象，不涉及 IO；server.py 在每个 WebSocket 连接上各建一个。
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


SAMPLE_RATE = 16_000        # 全工程统一的采样率：16 kHz
MIN_ENROLLMENT_SECONDS = 3.0  # 注册语音的最短时长；低于此值声纹/TSE 嵌入不可靠


class SessionState(str, Enum):
    """会话的四态状态机：

    IDLE（空闲）→ ENROLLING（注册中，收集目标人语音）
              → READY（注册完成，可以开始提取）
              → EXTRACTING（实时提取中）→ 回到 READY
    """

    IDLE = "idle"
    ENROLLING = "enrolling"
    READY = "ready"
    EXTRACTING = "extracting"


class SessionError(RuntimeError):
    """业务规则违规（非法状态迁移、音频太短等）。

    server.py 捕获后转成 error 事件发给前端，而不是断开连接。"""
    pass


@dataclass
class AudioSession:
    """一次 WebSocket 连接对应的会话状态 + 注册音频缓冲。"""

    state: SessionState = SessionState.IDLE  # 当前状态机状态
    # 注册期间累积的目标人 PCM16 原始字节（16 kHz 单声道，每采样 2 字节）
    enrollment: bytearray = field(default_factory=bytearray)

    @property
    def enrollment_seconds(self) -> float:
        # 已注册音频的时长（秒）= 字节数 / (采样率 × 每采样 2 字节)
        return len(self.enrollment) / (SAMPLE_RATE * 2)

    def start_enrollment(self) -> None:
        # 提取进行中不允许重新注册：状态机必须先回到 READY
        if self.state == SessionState.EXTRACTING:
            raise SessionError("请先停止实时提取")
        self.enrollment.clear()  # 丢弃上次注册的残留，重新开始收集
        self.state = SessionState.ENROLLING

    def accept_pcm16(self, chunk: bytes) -> None:
        # PCM16 每采样 2 字节，奇数字节说明帧被截断/损坏
        if len(chunk) % 2:
            raise SessionError("PCM16 数据长度必须是偶数")
        if self.state == SessionState.ENROLLING:
            self.enrollment.extend(chunk)  # 注册中：累积到注册缓冲
        elif self.state != SessionState.EXTRACTING:
            # IDLE / READY 时前端若还在发音频帧则拒绝（server 层会静默丢弃）
            raise SessionError("当前状态不接收音频")

    def finish_enrollment(self) -> None:
        # 只有注册中才能结束注册，防止误触发
        if self.state != SessionState.ENROLLING:
            raise SessionError("当前没有正在进行的声纹注册")
        # 注册太短则判失败：回 IDLE，需要重新注册
        if self.enrollment_seconds < MIN_ENROLLMENT_SECONDS:
            self.state = SessionState.IDLE
            raise SessionError("注册语音至少需要 3 秒")
        self.state = SessionState.READY  # 成功：等待开始提取

    def start_extraction(self) -> None:
        # 必须先有注册声纹，才能启动实时提取
        if self.state != SessionState.READY:
            raise SessionError("请先完成目标说话人注册")
        self.state = SessionState.EXTRACTING

    def stop_extraction(self) -> None:
        # 只有提取中才能停止（幂等保护）
        if self.state != SessionState.EXTRACTING:
            raise SessionError("实时提取尚未启动")
        # 回到 READY 而非 IDLE：注册声纹仍保留，可以直接再开一轮提取
        self.state = SessionState.READY
