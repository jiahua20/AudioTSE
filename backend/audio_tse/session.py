from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


SAMPLE_RATE = 16_000
MIN_ENROLLMENT_SECONDS = 3.0


class SessionState(str, Enum):
    IDLE = "idle"
    ENROLLING = "enrolling"
    READY = "ready"
    EXTRACTING = "extracting"


class SessionError(RuntimeError):
    pass


@dataclass
class AudioSession:
    state: SessionState = SessionState.IDLE
    enrollment: bytearray = field(default_factory=bytearray)

    @property
    def enrollment_seconds(self) -> float:
        return len(self.enrollment) / (SAMPLE_RATE * 2)

    def start_enrollment(self) -> None:
        if self.state == SessionState.EXTRACTING:
            raise SessionError("请先停止实时提取")
        self.enrollment.clear()
        self.state = SessionState.ENROLLING

    def accept_pcm16(self, chunk: bytes) -> None:
        if len(chunk) % 2:
            raise SessionError("PCM16 数据长度必须是偶数")
        if self.state == SessionState.ENROLLING:
            self.enrollment.extend(chunk)
        elif self.state != SessionState.EXTRACTING:
            raise SessionError("当前状态不接收音频")

    def finish_enrollment(self) -> None:
        if self.state != SessionState.ENROLLING:
            raise SessionError("当前没有正在进行的声纹注册")
        if self.enrollment_seconds < MIN_ENROLLMENT_SECONDS:
            self.state = SessionState.IDLE
            raise SessionError("注册语音至少需要 3 秒")
        self.state = SessionState.READY

    def start_extraction(self) -> None:
        if self.state != SessionState.READY:
            raise SessionError("请先完成目标说话人注册")
        self.state = SessionState.EXTRACTING

    def stop_extraction(self) -> None:
        if self.state != SessionState.EXTRACTING:
            raise SessionError("实时提取尚未启动")
        self.state = SessionState.READY
