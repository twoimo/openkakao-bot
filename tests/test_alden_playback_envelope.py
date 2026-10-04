"""Actual macOS SDK regression for causal PCM envelopes and player reset."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("xcrun"), "macOS SDK required")
class NativePlaybackEnvelopeTests(unittest.TestCase):
    def test_completed_pcm_matches_real_offline_player_clock_without_future_samples(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="alden-playback-sdk-") as folder:
            program = Path(folder) / "probe"
            built = subprocess.run(["xcrun", "swiftc", "-swift-version", "5", "-O", "-framework", "AVFoundation",
                                    str(root / "voice/native/playback_envelope.swift"),
                                    str(root / "voice/native/tests/main.swift"), "-o", str(program)],
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(program)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            case = json.loads(result.stdout)["cases"][0]
            self.assertEqual(case["mismatches"], 0)
            self.assertEqual([row["measuredRms"] for row in case["rows"]], [0, .25, .25, .5, .5, 0, 0])
            self.assertEqual(case["afterStopRms"], 0)
            self.assertEqual(case["beforeReplacementRenderRms"], 0)
