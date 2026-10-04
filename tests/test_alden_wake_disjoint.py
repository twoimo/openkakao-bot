"""Focused leakage and clip-peak tests for the experimental Alden wake head."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import evaluate_alden_korean_wake as evaluations  # noqa: E402

try:
    import numpy as np  # noqa: E402

    import train_alden_korean_wake as training  # noqa: E402

    HAS_NUMPY = True
except Exception:
    np = None  # type: ignore[assignment]
    training = None  # type: ignore[assignment]
    HAS_NUMPY = False


def _training_report() -> dict[str, object]:
    return {
        "training": {
            "provenance": {
                "voices": ["aiden", "eric"],
                "negative_phrases": ["오늘 회의 있어", "음악 재생"],
                "wake_phrase": "올든",
            },
            "input_manifest": {
                "wake": [{"name": "wake-aiden.wav", "sha256": "a" * 64}],
                "control": [{"name": "neg-eric.wav", "sha256": "b" * 64}],
            },
        }
    }


class DisjointSplitTests(unittest.TestCase):
    def test_rejects_missing_training_provenance(self) -> None:
        with self.assertRaises(evaluations.WakeEvaluationError) as caught:
            evaluations.validate_disjoint_split(
                {"training": {"input_manifest": _training_report()["training"]["input_manifest"]}},
                eval_voices=["serena"],
                eval_negative_phrases=["내일 비 와"],
            )
        self.assertIn("wake_eval_training_provenance_incomplete", str(caught.exception))

    def test_rejects_missing_training_input_hash(self) -> None:
        report = _training_report()
        report["training"]["input_manifest"]["control"] = [{"name": "neg-eric.wav"}]
        with self.assertRaises(evaluations.WakeEvaluationError) as caught:
            evaluations.validate_disjoint_split(
                report,
                eval_voices=["serena"],
                eval_negative_phrases=["내일 비 와"],
            )
        self.assertIn("wake_eval_training_hash_invalid:control", str(caught.exception))

    def test_rejects_training_voice_in_evaluation(self) -> None:
        with self.assertRaises(evaluations.WakeEvaluationError) as caught:
            evaluations.validate_disjoint_split(
                _training_report(),
                eval_voices=["serena", "aiden"],
                eval_negative_phrases=["내일 비 와"],
            )
        self.assertIn("wake_eval_voice_overlap:aiden", str(caught.exception))

    def test_rejects_training_voice_case_insensitively(self) -> None:
        report = _training_report()
        report["training"]["provenance"]["voices"] = ["AIDEN", "Eric"]
        with self.assertRaises(evaluations.WakeEvaluationError) as caught:
            evaluations.validate_disjoint_split(
                report,
                eval_voices=["  aiden  ", "serena"],
                eval_negative_phrases=["내일 비 와"],
            )
        self.assertIn("wake_eval_voice_overlap:aiden", str(caught.exception))

    def test_rejects_training_negative_text_in_evaluation(self) -> None:
        with self.assertRaises(evaluations.WakeEvaluationError) as caught:
            evaluations.validate_disjoint_split(
                _training_report(),
                eval_voices=["serena"],
                eval_negative_phrases=["내일 비 와", "음악 재생"],
            )
        self.assertIn("wake_eval_negative_phrase_overlap:음악 재생", str(caught.exception))

    def test_returns_training_clip_hashes_for_post_render_overlap_check(self) -> None:
        hashes = evaluations.validate_disjoint_split(
            _training_report(),
            eval_voices=["serena", "vivian"],
            eval_negative_phrases=["내일 비 와", "문 열어 줘"],
        )
        self.assertEqual(hashes, {"a" * 64, "b" * 64})


@unittest.skipUnless(HAS_NUMPY, "numpy is required for clip-peak fitting")
class ClipPeakTrainingTests(unittest.TestCase):
    def test_calibration_bounds_each_training_clip_peak(self) -> None:
        assert training is not None and np is not None
        positives = [
            np.asarray([[3.2, 0.0], [2.8, 0.1], [0.2, 0.2]], dtype=np.float32),
            np.asarray([[2.9, -0.1], [2.5, 0.2], [0.1, 0.1]], dtype=np.float32),
        ]
        negatives = [
            np.asarray([[0.0, 3.0], [0.2, 2.4], [1.0, 0.0]], dtype=np.float32),
            np.asarray([[-0.2, 2.8], [0.1, 2.1], [1.1, 0.1]], dtype=np.float32),
        ]
        weight, bias, fit = training._fit_clip_peak_head(positives, negatives)

        def sigmoid(value: float) -> float:
            return 1.0 / (1.0 + math.exp(-value))

        positive_peaks = [
            max(sigmoid(float(row @ weight + bias)) for row in clip) for clip in positives
        ]
        negative_peaks = [
            max(sigmoid(float(row @ weight + bias)) for row in clip) for clip in negatives
        ]
        self.assertGreaterEqual(min(positive_peaks), training.TARGET_POSITIVE_SCORE - 1e-5)
        self.assertLessEqual(max(negative_peaks), training.TARGET_NEGATIVE_SCORE + 1e-5)
        self.assertEqual(fit["objective"], "clip_peak_multiple_instance_hard_negative")
        self.assertGreater(fit["raw_clip_peak_margin"], 0.0)


if __name__ == "__main__":
    unittest.main()
