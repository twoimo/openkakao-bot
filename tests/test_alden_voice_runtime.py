from __future__ import annotations

import json
import os
import sys
import threading
import types
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from alden_voice import (  # noqa: E402
    VOICE_MIC_FRAME_SAMPLES,
    VoiceState,
    VoiceStatusStore,
    Qwen3TtsAdapter,
    VoiceTurnToken,
    _MicrophoneDisconnected,
    _MicrophoneFramePoller,
    run_microphone_session,
)
from alden_abort import AbortController, AldenCancelled  # noqa: E402


class VoiceTtsDecoderCancellationTests(unittest.TestCase):
    def engine(self, decode):
        class Talker:
            def generate(self, **kwargs):
                return decode(kwargs)
        talker = Talker()
        # Reproduce qwen-tts 0.1.1: the public wrapper doesn't forward the
        # caller's arbitrary kwargs; only the real talker sees its criteria.
        class Engine:
            model = types.SimpleNamespace(talker=talker)
            def get_supported_speakers(self):
                return ["ryan", "sohee"]
            def generate_custom_voice(self, **_kwargs):
                existing = lambda *_a, **_k: False
                self.model.talker.generate(stopping_criteria=[existing])
                return [[0.1, -0.1]], 24000
        return Engine(), talker

    def test_cancellation_during_decode_preserves_previous_wav_and_method(self):
        with TemporaryDirectory() as td:
            root = Path(td); output = root / "speech.wav"; output.write_bytes(b"previous")
            token = AbortController(root).token(); visited = []
            def decode(kwargs):
                self.assertEqual(len(kwargs["stopping_criteria"]), 2)
                for step in range(3):
                    for criterion in kwargs["stopping_criteria"]:
                        criterion(None, None)
                    visited.append(step)
                    token.cancel()
            engine, talker = self.engine(decode); adapter = Qwen3TtsAdapter()
            with mock.patch.object(adapter, "_load", return_value=engine):
                with self.assertRaises(AldenCancelled):
                    adapter.write_wav("4입니다.", output, token)
            self.assertEqual(visited, [0]); self.assertEqual(output.read_bytes(), b"previous")
            self.assertNotIn("generate", vars(talker))
            self.assertEqual(list(root.glob(".alden-tts-*")), [])

    def test_decoder_failure_restores_method_and_next_turn_uses_new_token(self):
        with TemporaryDirectory() as td:
            controller = AbortController(Path(td)); first = controller.token()
            fail = True
            def decode(kwargs):
                for criterion in kwargs["stopping_criteria"]:
                    criterion(None, None)
                if fail:
                    raise RuntimeError("codec_failure")
            engine, talker = self.engine(decode); adapter = Qwen3TtsAdapter()
            with mock.patch.object(adapter, "_load", return_value=engine):
                with self.assertRaisesRegex(RuntimeError, "codec_failure"):
                    adapter.synthesize("4입니다.", first)
                self.assertNotIn("generate", vars(talker))
                first.cancel(); fail = False
                audio, rate = adapter.synthesize("12입니다.", controller.token())
            self.assertEqual(rate, 24000); self.assertEqual(audio, [0.1, -0.1])
            self.assertNotIn("generate", vars(talker))

    def test_unsupported_decoder_does_not_generate_uncancellable_audio(self):
        with TemporaryDirectory() as td:
            engine = types.SimpleNamespace(get_supported_speakers=lambda: ["ryan", "sohee"], generate_custom_voice=mock.Mock())
            adapter = Qwen3TtsAdapter()
            with mock.patch.object(adapter, "_load", return_value=engine):
                with self.assertRaisesRegex(RuntimeError, "qwen3_tts_decoder_cancellation_unavailable"):
                    adapter.synthesize("4입니다.", AbortController(Path(td)).token())
            engine.generate_custom_voice.assert_not_called()

    def test_global_stop_latches_during_decode_and_requires_fresh_resumed_token(self):
        with TemporaryDirectory() as td:
            controller = AbortController(Path(td)); old = controller.token(); stop = True
            def decode(kwargs):
                if stop: controller.abort("test_global_stop")
                for criterion in kwargs["stopping_criteria"]:
                    criterion(None, None)
            engine, talker = self.engine(decode); adapter = Qwen3TtsAdapter()
            with mock.patch.object(adapter, "_load", return_value=engine):
                with self.assertRaises(AldenCancelled):
                    adapter.synthesize("4입니다.", old)
                self.assertTrue(controller.token().is_cancelled())
                controller.resume_after_human_action(); self.assertTrue(old.is_cancelled())
                stop = False
                self.assertEqual(adapter.synthesize("12입니다.", controller.token())[1], 24000)
            self.assertNotIn("generate", vars(talker))

    def test_overlapping_turns_do_not_share_a_cancelled_decoder_hook(self):
        with TemporaryDirectory() as td:
            controller = AbortController(Path(td)); old = controller.token(); fresh = controller.token()
            entered = threading.Event(); release = threading.Event(); second_started = threading.Event()
            results = []; calls = []
            def decode(kwargs):
                calls.append(threading.current_thread().name)
                if len(calls) == 1:
                    entered.set(); self.assertTrue(release.wait(1))
                for criterion in kwargs["stopping_criteria"]:
                    criterion(None, None)
            engine, talker = self.engine(decode); adapter = Qwen3TtsAdapter()
            def run(token, started=None):
                if started is not None: started.set()
                try: results.append(adapter.synthesize("4입니다.", token))
                except AldenCancelled: results.append("cancelled")
            with mock.patch.object(adapter, "_load", return_value=engine):
                one = threading.Thread(target=run, args=(old,), name="old")
                two = threading.Thread(target=run, args=(fresh,second_started), name="new")
                one.start(); self.assertTrue(entered.wait(1)); two.start(); self.assertTrue(second_started.wait(1))
                old.cancel(); release.set(); one.join(1); two.join(1)
                self.assertFalse(one.is_alive()); self.assertFalse(two.is_alive())
            self.assertEqual(calls, ["old", "new"])
            self.assertEqual(results, ["cancelled", ([0.1,-0.1],24000)])
            self.assertNotIn("generate", vars(talker))


class VoiceWavCommitTests(unittest.TestCase):
    def test_conversion_cancellation_preserves_existing_wav_without_numpy(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            token = AbortController(root).token()
            destination = root / "reply.wav"
            destination.write_bytes(b"previous owned artifact")

            class Audio:
                def tolist(self):
                    token.cancel()
                    return [0.0, 0.25]

            adapter = Qwen3TtsAdapter()
            with mock.patch.object(adapter, "synthesize", return_value=(Audio(), 24000)), mock.patch.dict(sys.modules, {"numpy": None}):
                with self.assertRaises(AldenCancelled):
                    adapter.write_wav("public synthetic probe", destination, token)
            self.assertEqual(destination.read_bytes(), b"previous owned artifact")
            self.assertEqual(list(root.glob(".alden-tts-*")), [])

    def test_numpy_conversion_cancellation_preserves_existing_wav(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy is absent from the focused CI interpreter")
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            token = AbortController(root).token()
            destination = root / "reply.wav"
            destination.write_bytes(b"previous owned artifact")
            adapter = Qwen3TtsAdapter()
            asarray = np.asarray

            def cancel_during_conversion(*args, **kwargs):
                token.cancel()
                return asarray(*args, **kwargs)

            with mock.patch.object(adapter, "synthesize", return_value=([0.0, 0.25], 24000)), mock.patch.object(np, "asarray", side_effect=cancel_during_conversion):
                with self.assertRaises(AldenCancelled):
                    adapter.write_wav("public synthetic probe", destination, token)
            self.assertEqual(destination.read_bytes(), b"previous owned artifact")
            self.assertEqual(list(root.glob(".alden-tts-*")), [])

    def test_cancel_resume_or_partial_disk_failure_never_publishes_wav(self):
        for failure in ("cancel", "abort_resume", "disk_failure"):
            with self.subTest(failure=failure), TemporaryDirectory() as temporary:
                root = Path(temporary)
                controller = AbortController(root)
                token = controller.token()
                destination = root / "reply.wav"
                destination.write_bytes(b"previous owned artifact")
                adapter = Qwen3TtsAdapter()
                original_write = wave.Wave_write.writeframes

                def write_then_fail(handle, data):
                    original_write(handle, data)
                    if failure == "disk_failure":
                        raise OSError("owned simulated partial write")
                    if failure == "cancel":
                        token.cancel()
                    else:
                        controller.abort("test_abort")
                        controller.resume_after_human_action()

                with mock.patch.object(adapter, "synthesize", return_value=([0.0, 0.25], 24000)), mock.patch.object(wave.Wave_write, "writeframes", write_then_fail):
                    with self.assertRaises(OSError if failure == "disk_failure" else AldenCancelled):
                        adapter.write_wav("public synthetic probe", destination, token)
                self.assertEqual(destination.read_bytes(), b"previous owned artifact")
                self.assertEqual(list(root.glob(".alden-tts-*")), [])

    def test_pre_cancelled_job_does_not_synthesize_or_create_output(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            token = AbortController(root).token()
            token.cancel()
            adapter = Qwen3TtsAdapter()
            with mock.patch.object(adapter, "synthesize") as synthesize:
                with self.assertRaises(AldenCancelled):
                    adapter.write_wav("public synthetic probe", root / "reply.wav", token)
            synthesize.assert_not_called()
            self.assertEqual(list(root.glob("*.wav")), [])

    def test_direct_file_mode_rejects_symlink_before_synthesis(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "reply.wav"
            original = root / "original.wav"
            original.write_bytes(b"previous owned artifact")
            destination.symlink_to(original)
            adapter = Qwen3TtsAdapter()
            with mock.patch.object(adapter, "synthesize") as synthesize:
                with self.assertRaisesRegex(RuntimeError, "voice_tts_output_invalid"):
                    adapter.write_wav("public synthetic probe", destination, AbortController(root).token())
            synthesize.assert_not_called()
            self.assertTrue(destination.is_symlink())
            self.assertEqual(original.read_bytes(), b"previous owned artifact")

    def test_success_replaces_complete_wav_with_private_file_and_cleans_staging(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "reply.wav"
            destination.write_bytes(b"previous owned artifact")
            adapter = Qwen3TtsAdapter()
            with mock.patch.object(adapter, "synthesize", return_value=([0.0, 0.25, -0.25], 24000)), mock.patch.dict(sys.modules, {"numpy": None}):
                result = adapter.write_wav("public synthetic probe", destination, AbortController(root).token())
            with wave.open(str(destination), "rb") as saved:
                self.assertEqual((saved.getframerate(), saved.getnchannels(), saved.getsampwidth(), saved.getnframes()), (24000, 1, 2, 3))
            self.assertEqual(result["bytes"], destination.stat().st_size)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(root.glob(".alden-tts-*")), [])

    def test_new_abort_root_and_nested_env_output_keep_private_permissions(self):
        for nested in (False, True):
            with self.subTest(nested=nested), TemporaryDirectory() as temporary:
                root = Path(temporary) / "state"
                output = root / "nested" / "reply.wav" if nested else root / "reply.wav"
                adapter = Qwen3TtsAdapter()
                token = AbortController(root).token()
                with mock.patch.object(adapter, "synthesize", return_value=([0.0, 0.25], 24000)), mock.patch.dict(os.environ, {"OPENKAKAO_VOICE_TTS_OUT": str(output)}):
                    adapter.speak("public synthetic probe", token)
                self.assertFalse(token.is_cancelled())
                self.assertEqual(root.stat().st_mode & 0o777, 0o700)
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_local_turn_and_session_cancel_are_ordered_against_atomic_publish(self):
        for kind in ("local", "turn", "session"):
            with self.subTest(kind=kind), TemporaryDirectory() as temporary:
                root = Path(temporary)
                session = AbortController(root).token()
                token = session if kind == "local" else VoiceTurnToken(session)
                cancel_token = session if kind == "session" else token
                destination = root / "reply.wav"
                destination.write_bytes(b"previous owned artifact")
                adapter = Qwen3TtsAdapter()
                entered, release, attempted, cancelled = (threading.Event() for _ in range(4))
                errors, order = [], []
                original_replace = os.replace

                def delayed_replace(source, target):
                    entered.set()
                    if not release.wait(2):
                        raise TimeoutError("owned test commit was not released")
                    original_replace(source, target)
                    order.append("published")

                def write():
                    try:
                        adapter.write_wav("public synthetic probe", destination, token)
                    except BaseException as error:
                        errors.append(error)

                def cancel():
                    attempted.set()
                    cancel_token.cancel()
                    order.append("cancelled")
                    cancelled.set()

                with mock.patch.object(adapter, "synthesize", return_value=([0.0, 0.25], 24000)), mock.patch("alden_voice.os.replace", delayed_replace):
                    writer = threading.Thread(target=write)
                    canceller = threading.Thread(target=cancel)
                    writer.start()
                    try:
                        self.assertTrue(entered.wait(1))
                        canceller.start()
                        self.assertTrue(attempted.wait(1))
                        # Commit was admitted first. A concurrent local cancel
                        # must not complete before this atomic publication.
                        self.assertFalse(cancelled.wait(.05))
                    finally:
                        release.set()
                        writer.join(2)
                        if canceller.ident is not None:
                            canceller.join(2)
                self.assertFalse(writer.is_alive())
                self.assertFalse(canceller.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(order, ["published", "cancelled"])
                self.assertTrue(token.is_cancelled())
                self.assertEqual(destination.read_bytes()[:4], b"RIFF")
                self.assertEqual(list(root.glob(".alden-tts-*")), [])


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeStream:
    def __init__(
        self,
        *,
        active: bool = True,
        available: int = 0,
        frame: bytes | None = None,
        overflowed: bool = False,
        **_kwargs: object,
    ) -> None:
        self.active = active
        self.echo_processed = True
        self.available = available
        self.frame = frame if frame is not None else b"\0\0" * VOICE_MIC_FRAME_SAMPLES
        self.overflowed = overflowed
        self.read_calls = 0
        self.availability_checks = 0

    @property
    def read_available(self) -> int:
        self.availability_checks += 1
        return self.available

    def read(self, frames: int) -> tuple[bytes, bool]:
        self.read_calls += 1
        if frames != VOICE_MIC_FRAME_SAMPLES:
            raise AssertionError(f"unexpected frame request: {frames}")
        return self.frame, self.overflowed

    def __enter__(self) -> "FakeStream":
        return self

    def ensure_permission(self, token):
        token.raise_if_cancelled()

    def __exit__(self, *_args: object) -> bool:
        return False


class MicrophoneFramePollerTests(unittest.TestCase):
    def test_idle_poll_checks_availability_without_blocking_read(self) -> None:
        clock = FakeClock(10.0)
        stream = FakeStream(available=0)
        with mock.patch("alden_voice.time.monotonic", side_effect=clock.monotonic):
            poller = _MicrophoneFramePoller(stream)
            self.assertIsNone(poller.poll())
            self.assertEqual(stream.read_calls, 0)

            clock.now += 0.02
            stream.available = VOICE_MIC_FRAME_SAMPLES
            frame, overflowed = poller.poll() or (b"", True)

        self.assertEqual(frame, b"\0\0" * VOICE_MIC_FRAME_SAMPLES)
        self.assertFalse(overflowed)
        self.assertEqual(stream.read_calls, 1)
        self.assertEqual(stream.availability_checks, 2)

    def test_stale_and_disconnected_streams_fail_clearly(self) -> None:
        clock = FakeClock()
        stale = FakeStream(available=0)
        with mock.patch("alden_voice.time.monotonic", side_effect=clock.monotonic):
            poller = _MicrophoneFramePoller(stale, stale_seconds=0.1)
            clock.now = 0.1
            with self.assertRaisesRegex(_MicrophoneDisconnected, "stalled"):
                poller.poll()
        self.assertEqual(stale.read_calls, 0)

        inactive = FakeStream(active=False)
        with mock.patch("alden_voice.time.monotonic", return_value=0.0):
            poller = _MicrophoneFramePoller(inactive)
            with self.assertRaisesRegex(_MicrophoneDisconnected, "inactive"):
                poller.poll()
        self.assertEqual(inactive.read_calls, 0)


class VoiceStatusStoreTests(unittest.TestCase):
    def test_state_changes_write_immediately_inside_heartbeat_window(self) -> None:
        clock = FakeClock()
        real_replace = os.replace
        with TemporaryDirectory() as temp_dir, mock.patch(
            "alden_voice.time.monotonic", side_effect=clock.monotonic
        ), mock.patch("alden_voice.time.time", return_value=1000.0), mock.patch(
            "alden_voice.os.replace", wraps=real_replace
        ) as replace:
            store = VoiceStatusStore(Path(temp_dir))
            store.write(state=VoiceState.WAKE_LISTEN, rms=0.0)
            clock.now = 0.1
            store.write(state=VoiceState.USER_LISTEN, rms=0.2)
            payload = json.loads(store.path.read_text(encoding="utf-8"))

        self.assertEqual(replace.call_count, 2)
        self.assertEqual(payload["state"], "user_listen")
        self.assertEqual(payload["rms"], 0.2)

    def test_unchanged_wake_listen_writes_at_most_once_per_five_seconds(self) -> None:
        clock = FakeClock()
        wall_times = iter((1000.0, 1005.0))
        real_replace = os.replace
        with TemporaryDirectory() as temp_dir, mock.patch(
            "alden_voice.time.monotonic", side_effect=clock.monotonic
        ), mock.patch(
            "alden_voice.time.time", side_effect=lambda: next(wall_times)
        ), mock.patch("alden_voice.os.replace", wraps=real_replace) as replace:
            store = VoiceStatusStore(Path(temp_dir))
            store.write(state=VoiceState.WAKE_LISTEN, rms=0.0)
            clock.now = 1.0
            store.write(state=VoiceState.WAKE_LISTEN, rms=0.1)
            clock.now = 4.999
            store.write(state=VoiceState.WAKE_LISTEN, rms=0.2)
            clock.now = 5.0
            store.write(state=VoiceState.WAKE_LISTEN, rms=0.3)
            payload = json.loads(store.path.read_text(encoding="utf-8"))

        self.assertEqual(replace.call_count, 2)
        self.assertEqual(payload["rms"], 0.3)
        self.assertEqual(payload["updated_at"], 1005)


class MicrophoneSessionErrorTests(unittest.TestCase):
    @staticmethod
    def _run_with_stream_factory(factory: object):
        with TemporaryDirectory() as temp_dir, mock.patch.dict(
            os.environ, {"OPENKAKAO_VOICE_ENV": "1"}
        ), mock.patch("alden_voice.MacVoiceAudio", side_effect=factory), mock.patch(
            "alden_voice.resolve_live_wake_model", return_value=Path(__file__)
        ), mock.patch("alden_voice.OpenWakeVadFrontend", return_value=object()):
            return run_microphone_session(state_root=Path(temp_dir))

    def test_session_distinguishes_unavailable_from_disconnected_input(self) -> None:
        def unavailable(**_kwargs: object):
            raise OSError("no input device")

        unavailable_result = self._run_with_stream_factory(unavailable)
        disconnected_result = self._run_with_stream_factory(
            lambda **kwargs: FakeStream(active=False, **kwargs)
        )

        self.assertEqual(unavailable_result.state, VoiceState.ERROR)
        self.assertEqual(unavailable_result.error_code, "mic_unavailable")
        self.assertEqual(disconnected_result.state, VoiceState.ERROR)
        self.assertEqual(disconnected_result.error_code, "mic_disconnected")

    def test_missing_alden_wake_model_stops_before_opening_microphone(self) -> None:
        def must_not_open(**_kwargs: object):
            self.fail("microphone opened without a validated Alden wake model")

        with TemporaryDirectory() as temp_dir, mock.patch.dict(
            os.environ, {"OPENKAKAO_VOICE_ENV": "1"}
        ), mock.patch("alden_voice.MacVoiceAudio", side_effect=must_not_open), mock.patch(
            "alden_voice.resolve_live_wake_model", return_value=None
        ):
            result = run_microphone_session(state_root=Path(temp_dir))

        self.assertEqual(result.state, VoiceState.ERROR)
        self.assertEqual(result.error_code, "alden_wake_model_unavailable")


if __name__ == "__main__":
    unittest.main()
