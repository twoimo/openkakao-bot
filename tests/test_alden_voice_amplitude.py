import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from alden_voice import VoiceState, VoiceStatusStore


class VoiceAmplitudeStatusTests(unittest.TestCase):
    def test_native_cursor_is_read_only_when_speaking_publication_is_due(self):
        with TemporaryDirectory() as folder, patch("alden_voice.time.monotonic") as clock:
            store = VoiceStatusStore(Path(folder));read = Mock(return_value=.25)
            clock.return_value=100
            store.write(state=VoiceState.SPEAKING, rms=.9, output_rms_source=read)
            clock.return_value=100.02
            store.write(state=VoiceState.SPEAKING, rms=.8, output_rms_source=read)
            self.assertEqual(read.call_count, 1)
            self.assertEqual(json.loads(store.path.read_text())["output_rms"], .25)
            clock.return_value=100.11
            store.write(state=VoiceState.SPEAKING, rms=.8, output_rms_source=read)
            self.assertEqual(read.call_count, 2)
            store.write(state=VoiceState.ABORTED, rms=.8, output_rms_source=read)
            self.assertEqual(read.call_count, 2)
            self.assertEqual(json.loads(store.path.read_text())["output_rms"], 0)

    def test_missing_invalid_or_failed_output_measurements_never_use_microphone_rms(self):
        with TemporaryDirectory() as folder:
            store = VoiceStatusStore(Path(folder))
            for i, source in enumerate([None, Mock(return_value=float("nan")), Mock(side_effect=RuntimeError("lost device"))]):
                store.write(state=VoiceState.SPEAKING, rms=.9, output_rms_source=source, turn_id=i+1)
                self.assertEqual(json.loads(store.path.read_text())["output_rms"], 0)
            store.write(state=VoiceState.ENDED, rms=.9, output_rms=1)
            self.assertEqual(json.loads(store.path.read_text())["output_rms"], 0)
