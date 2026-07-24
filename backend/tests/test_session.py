import unittest

from audio_tse.session import AudioSession, SessionError, SessionState


class AudioSessionTest(unittest.TestCase):
    def test_enrollment_then_extraction(self) -> None:
        session = AudioSession()
        session.start_enrollment()
        session.accept_pcm16(bytes(16_000 * 2 * 3))
        session.finish_enrollment()
        self.assertEqual(session.state, SessionState.READY)
        session.start_extraction()
        self.assertEqual(session.state, SessionState.EXTRACTING)
        session.stop_extraction()
        self.assertEqual(session.state, SessionState.READY)

    def test_rejects_short_enrollment(self) -> None:
        session = AudioSession()
        session.start_enrollment()
        session.accept_pcm16(bytes(16_000 * 2))
        with self.assertRaisesRegex(SessionError, "至少需要 3 秒"):
            session.finish_enrollment()

    def test_rejects_audio_while_idle(self) -> None:
        with self.assertRaisesRegex(SessionError, "不接收音频"):
            AudioSession().accept_pcm16(b"\x00\x00")


if __name__ == "__main__":
    unittest.main()