from __future__ import annotations

import json
import os
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from jarvis_voice import (  # noqa: E402
    VOICE_MIC_FRAME_SAMPLES,
    VoiceState,
    VoiceStatusStore,
    _MicrophoneDisconnected,
    _MicrophoneFramePoller,
    run_microphone_session,
)


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

    def __exit__(self, *_args: object) -> bool:
        return False


class MicrophoneFramePollerTests(unittest.TestCase):
    def test_idle_poll_checks_availability_without_blocking_read(self) -> None:
        clock = FakeClock(10.0)
        stream = FakeStream(available=0)
        with mock.patch("jarvis_voice.time.monotonic", side_effect=clock.monotonic):
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
        with mock.patch("jarvis_voice.time.monotonic", side_effect=clock.monotonic):
            poller = _MicrophoneFramePoller(stale, stale_seconds=0.1)
            clock.now = 0.1
            with self.assertRaisesRegex(_MicrophoneDisconnected, "stalled"):
                poller.poll()
        self.assertEqual(stale.read_calls, 0)

        inactive = FakeStream(active=False)
        with mock.patch("jarvis_voice.time.monotonic", return_value=0.0):
            poller = _MicrophoneFramePoller(inactive)
            with self.assertRaisesRegex(_MicrophoneDisconnected, "inactive"):
                poller.poll()
        self.assertEqual(inactive.read_calls, 0)


class VoiceStatusStoreTests(unittest.TestCase):
    def test_state_changes_write_immediately_inside_heartbeat_window(self) -> None:
        clock = FakeClock()
        real_replace = os.replace
        with TemporaryDirectory() as temp_dir, mock.patch(
            "jarvis_voice.time.monotonic", side_effect=clock.monotonic
        ), mock.patch("jarvis_voice.time.time", return_value=1000.0), mock.patch(
            "jarvis_voice.os.replace", wraps=real_replace
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
            "jarvis_voice.time.monotonic", side_effect=clock.monotonic
        ), mock.patch(
            "jarvis_voice.time.time", side_effect=lambda: next(wall_times)
        ), mock.patch("jarvis_voice.os.replace", wraps=real_replace) as replace:
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
        fake_sounddevice = types.ModuleType("sounddevice")
        fake_sounddevice.RawInputStream = factory  # type: ignore[attr-defined]
        with TemporaryDirectory() as temp_dir, mock.patch.dict(
            os.environ, {"OPENKAKAO_VOICE_ENV": "1"}
        ), mock.patch.dict(sys.modules, {"sounddevice": fake_sounddevice}), mock.patch(
            "jarvis_voice.resolve_custom_wake_model", return_value=None
        ), mock.patch("jarvis_voice.OpenWakeVadFrontend", return_value=object()):
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


if __name__ == "__main__":
    unittest.main()
