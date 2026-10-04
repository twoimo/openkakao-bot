"""Duplex interruption races with fake microphone/native playback adapters only."""
from __future__ import annotations

import ctypes
import importlib.util
import sys
import threading
import time
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from alden_abort import AbortController, AldenCancelled, read_abort_state
from alden_voice import AldenVoicePipeline, MacVoiceAudio, VoiceState

PCM = b"\x00\x04" * 320


class BargeInTests(unittest.TestCase):
    def test_processed_speech_interrupts_playback_and_keeps_leading_audio_and_latest_turn(self):
        entered = threading.Event()
        calls, transcripts, history = [], [], []

        class Tts:
            def speak(self, text, token):
                if text == "첫 답변":
                    entered.set()
                    deadline = time.monotonic() + 2
                    while not token.is_cancelled() and time.monotonic() < deadline:
                        time.sleep(.005)
                    token.raise_if_cancelled()
                calls.append(text)

        class Stt:
            def transcribe(self, pcm, rate, token):
                token.raise_if_cancelled()
                transcripts.append((pcm, rate))
                return "새 질문"

        class Llm:
            def generate(self, text, token, **kwargs):
                history.append(kwargs.get("history"))
                return "첫 답변" if text == "첫 질문" else "새 답변"

        class Frontend:
            speech_calls = 0
            wake_calls = 0
            resets = 0

            def analyze_speech(self, frame):
                self.speech_calls += 1
                return SimpleNamespace(rms=.03, speech=True, stock_wake_score=0, custom_wake_score=None)

            def analyze(self, frame):
                self.wake_calls += 1
                return SimpleNamespace(rms=0, speech=False, stock_wake_score=0, custom_wake_score=None)

            def reset_for_independent_clip(self):
                self.resets += 1

        with TemporaryDirectory() as temp:
            controller = AbortController(Path(temp))
            pipeline = AldenVoicePipeline(stt=Stt(), llm=Llm(), tts=Tts(),
                token=controller.token(), echo_processed_microphone=True)
            try:
                pipeline.submit_text("첫 질문")
                self.assertTrue(entered.wait(1))
                old = pipeline._active_turn
                frontend = Frontend()
                for _ in range(2):
                    pipeline.feed_frontend_frame(frontend, PCM, asynchronous=True)
                self.assertFalse(old.token.is_cancelled())
                pipeline.feed_frontend_frame(frontend, PCM, asynchronous=True)
                self.assertTrue(old.token.is_cancelled())
                self.assertEqual(pipeline.state, VoiceState.USER_LISTEN)
                self.assertEqual(pipeline.ring.bytes(), PCM * 3)
                self.assertEqual((frontend.speech_calls, frontend.wake_calls), (3, 0))
                for _ in range(12):
                    pipeline.feed_frontend_frame(frontend, b"\0" * 640, asynchronous=True)
                deadline = time.monotonic() + 1
                result = None
                while result is None and time.monotonic() < deadline:
                    result = pipeline.poll_result()
                    time.sleep(.005)
                self.assertIsNotNone(result)
                self.assertEqual((result.state, result.turn_id, result.transcript), (VoiceState.ENDED, 2, "새 질문"))
                self.assertEqual(calls, ["새 답변"])
                self.assertEqual(transcripts, [(PCM * 3, 16_000)])
                self.assertEqual(history[1], [{"role": "user", "content": "첫 질문"}])
                self.assertEqual(frontend.resets, 1)
                self.assertEqual(read_abort_state(controller.path).epoch, 0)
            finally:
                pipeline.close()

    def test_raw_playback_and_nonconsecutive_speech_cannot_interrupt(self):
        with TemporaryDirectory() as temp:
            pipeline = AldenVoicePipeline(stt=object(), llm=object(), tts=object(),
                token=AbortController(Path(temp)).token(), echo_processed_microphone=True)
            turn = pipeline._begin_turn("text", None)
            pipeline.state = VoiceState.SPEAKING
            for _ in range(5):
                pipeline.feed_audio(PCM, rms=.1, speech=True, source="playback")
            self.assertFalse(turn.token.is_cancelled())
            for _ in range(2): pipeline.feed_audio(PCM, rms=.1, speech=True)
            pipeline.feed_audio(b"\0" * 640, rms=0, speech=False)
            for _ in range(2): pipeline.feed_audio(PCM, rms=.1, speech=True)
            self.assertFalse(turn.token.is_cancelled())
            pipeline._echo_processed_microphone = False
            for _ in range(5): pipeline.feed_audio(PCM, rms=.1, speech=True)
            self.assertFalse(turn.token.is_cancelled())
            self.assertEqual(pipeline.ring.size, 0)
            pipeline.close()

    def test_global_stop_wins_over_processed_speech(self):
        with TemporaryDirectory() as temp:
            controller = AbortController(Path(temp))
            pipeline = AldenVoicePipeline(stt=object(), llm=object(), tts=object(),
                token=controller.token(), echo_processed_microphone=True)
            pipeline._begin_turn("text", None)
            pipeline.state = VoiceState.SPEAKING
            for _ in range(2): pipeline.feed_audio(PCM, rms=.1, speech=True)
            controller.abort()
            result = pipeline.feed_audio(PCM, rms=.1, speech=True)
            self.assertEqual(result.state, VoiceState.ABORTED)
            self.assertEqual(pipeline.ring.size, 0)
            self.assertTrue(read_abort_state(controller.path).latched)
            pipeline.close()


def native_library():
    values = {"abi": 2, "permission": 3, "create": 7, "start": 0, "processed": 1,
              "available": 640, "dropped": 0, "read": 640, "play": 17, "playing": 0,
              "cancel": None, "destroy": None, "request_permission": None, "output_rms": 0.0}
    return SimpleNamespace(**{"alden_audio_" + name: Mock(return_value=value) for name, value in values.items()})


class NativeAudioOwnershipTests(unittest.TestCase):
    def test_old_abi_is_rejected_before_any_audio_or_permission_action(self):
        lib = native_library();lib.alden_audio_abi.return_value = 1
        del lib.alden_audio_output_rms
        with patch("alden_voice._load_voice_audio_library", return_value=lib):
            with self.assertRaisesRegex(RuntimeError, "voice_audio_abi_unsupported"):
                MacVoiceAudio()
        lib.alden_audio_create.assert_not_called();lib.alden_audio_request_permission.assert_not_called()

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "numpy unavailable")
    def test_output_level_is_owned_by_the_current_playback_ticket_and_clears_on_close(self):
        import numpy as np
        lib = native_library();entered = threading.Event()
        lib.alden_audio_playing.side_effect = lambda *_args: (entered.set(), 1)[1]
        lib.alden_audio_output_rms.return_value = .25
        errors = []
        with TemporaryDirectory() as temp, patch("alden_voice._load_voice_audio_library", return_value=lib):
            token = AbortController(Path(temp)).token();audio = MacVoiceAudio().__enter__()
            self.assertEqual(audio.output_rms, 0);lib.alden_audio_output_rms.assert_not_called()
            def play():
                try: audio.play(np.zeros(960), 24_000, token)
                except Exception as error: errors.append(error)
            worker = threading.Thread(target=play);worker.start();self.assertTrue(entered.wait(1))
            self.assertEqual(audio.output_rms, .25);lib.alden_audio_output_rms.assert_called_with(7, 17)
            lib.alden_audio_output_rms.return_value = float("nan");self.assertEqual(audio.output_rms, 0)
            token.cancel();worker.join(1);self.assertFalse(worker.is_alive())
            self.assertEqual(audio.output_rms, 0);self.assertEqual(len(errors), 1)
            lib.alden_audio_cancel.assert_called_once_with(7, 17)
            audio.close();self.assertEqual(audio.output_rms, 0)

    def test_foreground_permission_grant_and_denial_do_not_start_audio(self):
        for permissions, allowed in [([0, 3], True), ([2], False)]:
            with self.subTest(permissions=permissions), TemporaryDirectory() as temp:
                lib = native_library()
                lib.alden_audio_permission.side_effect = permissions
                with patch("alden_voice._load_voice_audio_library", return_value=lib):
                    audio = MacVoiceAudio()
                    if allowed:
                        audio.ensure_permission(AbortController(Path(temp)).token())
                        lib.alden_audio_request_permission.assert_called_once()
                    else:
                        with self.assertRaisesRegex(RuntimeError, "mic_access_required"):
                            audio.ensure_permission(AbortController(Path(temp)).token())
                        lib.alden_audio_request_permission.assert_not_called()
                lib.alden_audio_create.assert_not_called()

    def test_permission_wait_cancels_without_creating_or_resuming_an_engine(self):
        lib = native_library()
        lib.alden_audio_permission.return_value = 0
        with TemporaryDirectory() as temp, patch("alden_voice._load_voice_audio_library", return_value=lib):
            token = AbortController(Path(temp)).token()
            lib.alden_audio_request_permission.side_effect = token.cancel
            audio = MacVoiceAudio()
            with self.assertRaises(AldenCancelled):
                audio.ensure_permission(token)
            lib.alden_audio_request_permission.assert_called_once()
            lib.alden_audio_create.assert_not_called()

    def test_destroyed_audio_owner_cannot_be_reopened_with_reused_native_tickets(self):
        lib = native_library()
        with patch("alden_voice._load_voice_audio_library", return_value=lib):
            audio = MacVoiceAudio().__enter__()
            audio.close()
            with self.assertRaisesRegex(RuntimeError, "voice_audio_closed"):
                audio.__enter__()
        lib.alden_audio_create.assert_called_once()
        lib.alden_audio_destroy.assert_called_once_with(7)

    def test_denied_permission_never_creates_an_engine(self):
        lib = native_library()
        lib.alden_audio_permission.return_value = 0
        with patch("alden_voice._load_voice_audio_library", return_value=lib):
            audio = MacVoiceAudio()
            with self.assertRaisesRegex(RuntimeError, "mic_access_required"):
                audio.__enter__()
            audio.close()
        lib.alden_audio_create.assert_not_called()
        lib.alden_audio_destroy.assert_not_called()

    def test_failed_start_and_repeated_close_release_exactly_one_handle(self):
        lib = native_library()
        lib.alden_audio_start.return_value = -10875
        with patch("alden_voice._load_voice_audio_library", return_value=lib):
            audio = MacVoiceAudio()
            with self.assertRaisesRegex(RuntimeError, "processing_unavailable:-10875"):
                audio.__enter__()
            audio.close()
        lib.alden_audio_destroy.assert_called_once_with(7)

    def test_partial_read_and_lost_processing_proof_fail_closed(self):
        lib = native_library()
        with patch("alden_voice._load_voice_audio_library", return_value=lib):
            with MacVoiceAudio() as audio:
                lib.alden_audio_read.return_value = 320
                with self.assertRaisesRegex(RuntimeError, "mic_disconnected"):
                    audio.read(320)
                lib.alden_audio_processed.return_value = 0
                self.assertFalse(audio.active)
        lib.alden_audio_destroy.assert_called_once_with(7)

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "numpy unavailable")
    def test_cancel_after_native_submission_stops_only_its_ticket(self):
        import numpy as np
        lib = native_library()
        with TemporaryDirectory() as temp, patch("alden_voice._load_voice_audio_library", return_value=lib):
            token = AbortController(Path(temp)).token()
            lib.alden_audio_play.side_effect = lambda *_args: (token.cancel(), 17)[1]
            with MacVoiceAudio() as audio:
                with self.assertRaises(AldenCancelled):
                    audio.play(np.zeros(480), 24_000, token)
            lib.alden_audio_cancel.assert_called_once_with(7, 17)
            self.assertFalse(read_abort_state(token.path).latched)

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "numpy unavailable")
    def test_closing_during_playback_never_calls_a_destroyed_handle(self):
        import numpy as np
        lib = native_library()
        entered = threading.Event()
        lib.alden_audio_playing.side_effect = lambda *_args: (entered.set(), 1)[1]
        errors = []
        with TemporaryDirectory() as temp, patch("alden_voice._load_voice_audio_library", return_value=lib):
            token = AbortController(Path(temp)).token()
            audio = MacVoiceAudio().__enter__()
            def play():
                try: audio.play(np.zeros(480), 24_000, token)
                except Exception as error: errors.append(error)
            worker = threading.Thread(target=play)
            worker.start()
            self.assertTrue(entered.wait(1))
            audio.close()
            worker.join(1)
            self.assertFalse(worker.is_alive())
            self.assertEqual(len(errors), 1)
            lib.alden_audio_destroy.assert_called_once_with(7)
            lib.alden_audio_cancel.assert_not_called()


@unittest.skipUnless(importlib.util.find_spec("numpy"), "numpy unavailable")
class AudioMeasurementCleanupTests(unittest.TestCase):
    def test_input_gap_unwinds_playback_before_destroying_audio_in_both_modes(self):
        import numpy  # Load its extension before patch.dict restores the module inventory.
        import measure_alden_voice_audio as measurement
        for barge_in in [False, True]:
            with self.subTest(barge_in=barge_in), TemporaryDirectory() as temp:
                started, stopped = threading.Event(), threading.Event()
                pipelines = []

                class FakeAudio:
                    echo_processed = True
                    def __enter__(self): return self
                    def __exit__(self, *_args):
                        if not stopped.is_set():
                            raise AssertionError("destroyed audio before owned playback stopped")
                    def play(self, _samples, _rate, token):
                        started.set()
                        try:
                            while True:
                                token.raise_if_cancelled()
                                time.sleep(.005)
                        finally:
                            stopped.set()

                def poll():
                    self.assertTrue(started.wait(1))
                    return PCM, True

                def make_pipeline(**kwargs):
                    pipeline = AldenVoicePipeline(**kwargs)
                    pipelines.append(pipeline)
                    return pipeline

                wav = Path(temp) / "synthetic.wav"
                with wave.open(str(wav), "wb") as output:
                    output.setparams((1, 2, 24_000, 480, "NONE", "not compressed"))
                    output.writeframes(b"\0" * 960)
                fake_vad = SimpleNamespace(Vad=lambda _mode: SimpleNamespace(is_speech=lambda *_args: True))
                with patch.dict(sys.modules, {"webrtcvad": fake_vad}), \
                     patch.object(measurement, "MacVoiceAudio", FakeAudio), \
                     patch.object(measurement, "AldenVoicePipeline", side_effect=make_pipeline), \
                     patch.object(measurement._MicrophoneFramePoller, "poll", side_effect=poll):
                    with self.assertRaisesRegex(RuntimeError, "microphone audio gap"):
                        measurement.measure(wav, barge_in=barge_in)
                self.assertTrue(stopped.is_set())
                for pipeline in pipelines:
                    self.assertTrue(pipeline._closed)
                    self.assertFalse(pipeline._worker.is_alive())


if __name__ == "__main__": unittest.main()
