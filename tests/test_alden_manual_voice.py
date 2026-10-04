import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from alden_abort import AbortController
from alden_voice import AldenVoicePipeline, VoiceState, VoiceStatusStore, run_microphone_session


class ManualVoiceTests(unittest.TestCase):
    def pipeline(self, root):
        calls = []
        class Stt:
            def transcribe(self, pcm16, sample_rate, token):
                calls.append("stt")
                calls.append(("pcm", pcm16))
                return "금요일 오후 3시에 회의" if calls.count("stt") == 1 else "그 회의 언제지?"
        class Llm:
            def generate(self, text, token, *, history=None):
                calls.append((text, history))
                return "금요일 오후 3시입니다."
        class Tts:
            def speak(self, text, token):
                calls.append("tts")
        pipeline = AldenVoicePipeline(
            stt=Stt(), llm=Llm(), tts=Tts(), token=AbortController(root).token(),
            manual_listen=True,
            status=VoiceStatusStore(root),
        )
        self.addCleanup(pipeline.close)
        return pipeline, calls

    def utterance(self, pipeline):
        for _ in range(10):
            pipeline.feed_audio(bytes(640), rms=.1, speech=True)
        result = None
        for _ in range(30):
            result = pipeline.feed_audio(bytes(640), rms=0, speech=False)
        return result

    def test_manual_two_turns_keep_context_without_synthetic_wake_scores(self):
        with TemporaryDirectory() as tmp:
            pipeline, calls = self.pipeline(Path(tmp))
            self.assertEqual(pipeline.state, VoiceState.IDLE)
            pipeline.begin_manual_listening()
            first = self.utterance(pipeline)
            second = self.utterance(pipeline)
            self.assertEqual((first.state, second.state), (VoiceState.ENDED, VoiceState.ENDED))
            self.assertEqual(first.conversation_id, second.conversation_id)
            self.assertGreater(second.turn_id, first.turn_id)
            history = [item for item in calls if isinstance(item, tuple) and item[0] != "pcm"][1][1]
            self.assertEqual([item["role"] for item in history], ["user", "assistant"])
            self.assertIn("금요일", history[0]["content"])
            self.assertEqual(pipeline.state, VoiceState.USER_LISTEN)

    def test_manual_capture_keeps_onsets_and_pauses_and_rejects_overflow(self):
        with TemporaryDirectory() as tmp:
            pipeline, calls = self.pipeline(Path(tmp))
            pipeline.begin_manual_listening()
            onset = b'o' * 640
            pause = b'p' * 640
            pipeline.feed_audio(onset, rms=.01, speech=False)
            pipeline.feed_audio(b's' * 640, rms=.1, speech=True)
            pipeline.feed_audio(pause, rms=.01, speech=False)
            self.utterance(pipeline)
            pcm = [item[1] for item in calls if isinstance(item, tuple) and item[0] == "pcm"][0]
            self.assertTrue(pcm.startswith(onset + b's' * 640 + pause))
            self.assertEqual(pipeline.state, VoiceState.USER_LISTEN)
            result = None
            for _ in range(601):
                result = pipeline.feed_audio(b's' * 640, rms=.1, speech=True)
                if result is not None: break
            self.assertEqual(result.error_code, "voice_utterance_too_long")
            self.assertEqual(calls.count("stt"), 1)

    def test_silence_and_tts_frames_never_start_inference(self):
        with TemporaryDirectory() as tmp:
            pipeline, calls = self.pipeline(Path(tmp))
            pipeline.begin_manual_listening()
            for _ in range(30):
                pipeline.feed_audio(bytes(640), rms=.3, speech=True, source="tts")
            for _ in range(749):
                self.assertIsNone(pipeline.feed_audio(bytes(640), rms=0, speech=False))
            result = pipeline.feed_audio(bytes(640), rms=0, speech=False)
            self.assertEqual(result.error_code, "silence_timeout")
            self.assertEqual(calls, [])

    def test_selected_context_survives_a_new_mic_session_without_mixing_conversations(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, _ = self.pipeline(root)
            first.begin_manual_listening()
            self.utterance(first)
            other, _ = self.pipeline(root)
            other.process_text("관계없는 이야기")
            resumed, calls = self.pipeline(root)
            resumed.restore_selected_conversation(first.conversation_id)
            resumed.begin_manual_listening()
            result = self.utterance(resumed)
            history = [item for item in calls if isinstance(item,tuple) and item[0] != 'pcm'][0][1]
            self.assertEqual([item['role'] for item in history],['user','assistant'])
            self.assertNotIn('관계없는', str(history))
            self.assertGreater(result.turn_id, 1)
            self.assertEqual(result.conversation_id, first.conversation_id)
            with self.assertRaisesRegex(RuntimeError,'voice_conversation_unavailable'):
                resumed.restore_selected_conversation('f'*32)

    def test_manual_mode_keeps_the_wake_gate_closed_and_stops_after_parent_exit(self):
        class Audio:
            echo_processed = True
            closed = False
            def ensure_permission(self, token): pass
            def __enter__(self): return self
            def __exit__(self, *args): self.closed = True
        with TemporaryDirectory() as tmp:
            audio = Audio()
            with mock.patch.dict("os.environ", {"OPENKAKAO_VOICE_ENV": "1"}), \
                 mock.patch("alden_voice.resolve_live_wake_model", side_effect=AssertionError("no automatic wake")), \
                 mock.patch("alden_voice.OpenWakeVadFrontend"), \
                 mock.patch("alden_voice.MacVoiceAudio", return_value=audio), \
                 mock.patch("alden_voice.MlxWhisperAdapter") as stt, \
                 mock.patch("alden_voice.Qwen3TtsAdapter") as tts, \
                 mock.patch("alden_voice.os.getppid", return_value=1):
                result = run_microphone_session(state_root=Path(tmp), manual_listen=True, parent_pid=9999)
            self.assertEqual(result.state, VoiceState.ABORTED)
            self.assertEqual(result.error_code, "voice_parent_ended")
            self.assertTrue(audio.closed)
            stt.return_value.transcribe.assert_not_called()
            tts.return_value.speak.assert_not_called()


if __name__ == "__main__":
    unittest.main()
