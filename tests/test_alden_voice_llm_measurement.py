"""Do not report invalid or prompt-derived throughput as model token speed."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from measure_alden_voice_llm import observed_decode_rate


class VoiceLlmMeasurementTests(unittest.TestCase):
    def test_excludes_prompt_and_first_completion_token(self):
        metrics = {"usage": {"prompt_tokens": 1000, "completion_tokens": 21},
                   "first_model_token_seconds": 1, "elapsed_seconds": 3}
        self.assertEqual(observed_decode_rate(metrics), 10)

    def test_missing_invalid_or_reversed_window_has_no_rate(self):
        base = {"usage": {"completion_tokens": 21}, "first_model_token_seconds": 1, "elapsed_seconds": 3}
        for patch in ({"usage": {}}, {"usage": {"completion_tokens": True}},
                      {"usage": {"completion_tokens": 1}}, {"first_model_token_seconds": -1},
                      {"first_model_token_seconds": float("nan")}, {"elapsed_seconds": float("inf")},
                      {"elapsed_seconds": 1}, {"elapsed_seconds": None}):
            with self.subTest(patch=patch):
                self.assertIsNone(observed_decode_rate({**base, **patch}))
