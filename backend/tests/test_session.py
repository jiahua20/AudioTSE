# AudioSession 状态机的单元测试：合法迁移成功、非法迁移/太短注册被拒。
import unittest

from audio_tse.session import AudioSession, SessionError, SessionState


class AudioSessionTest(unittest.TestCase):
    def test_enrollment_then_extraction(self) -> None:
        # 正向主流程：注册 3 秒 → ready → 提取 → 停止回 ready
        session = AudioSession()
        session.start_enrollment()
        session.accept_pcm16(bytes(16_000 * 2 * 3))  # 3 秒 PCM16（32000 字节/秒）
        session.finish_enrollment()
        self.assertEqual(session.state, SessionState.READY)
        session.start_extraction()
        self.assertEqual(session.state, SessionState.EXTRACTING)
        session.stop_extraction()
        self.assertEqual(session.state, SessionState.READY)

    def test_rejects_short_enrollment(self) -> None:
        # 注册不足 3 秒 → finishEnrollment 应抛错并回到 idle
        session = AudioSession()
        session.start_enrollment()
        session.accept_pcm16(bytes(16_000 * 2))  # 只有 1 秒
        with self.assertRaisesRegex(SessionError, "至少需要 3 秒"):
            session.finish_enrollment()

    def test_rejects_audio_while_idle(self) -> None:
        # idle 状态发音频帧 → 拒绝（server 层会静默丢弃这类帧）
        with self.assertRaisesRegex(SessionError, "不接收音频"):
            AudioSession().accept_pcm16(b"\x00\x00")


if __name__ == "__main__":
    unittest.main()
