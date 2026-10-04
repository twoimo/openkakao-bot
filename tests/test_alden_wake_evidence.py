"""Evidence contract for the experimental Korean wake-word candidate."""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from alden_voice import RELEASED_WAKE_MODEL, WAKE_THRESHOLD, resolve_custom_wake_model, resolve_live_wake_model  # noqa: E402


EVIDENCE = ROOT / "docs/architecture/alden-wake-heldout-eval.json"
V5_EVIDENCE = ROOT / "docs/architecture/alden-wake-v5-heldout-eval.json"
V5_TRAINING_REPORT = ROOT / "voice/models/experimental/alden_ko_ridge_candidate_v5.train.json"
V5_FROZEN_FIT_REFERENCE = {
    "positive_clips": 4,
    "negative_clips": 17,
    "method": "openwakeword_embedding_clip_peak_ridge_head",
    "proof_scope": "bounded_synthetic_training_only",
}


class WakeEvidenceBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = json.loads(EVIDENCE.read_text(encoding="utf-8"))

    def test_schema_and_scope_are_explicitly_experimental(self) -> None:
        bundle = self.bundle
        self.assertEqual(bundle["schema"], "alden.wake.experimental-eval/1")
        self.assertEqual(bundle["threshold"], WAKE_THRESHOLD)
        self.assertIs(bundle["threshold_changed"], False)
        self.assertIs(bundle["scope"]["synthetic_speech_only"], True)
        self.assertIs(bundle["scope"]["human_speakers"], False)
        self.assertIs(bundle["scope"]["microphones_or_rooms"], False)
        self.assertEqual(bundle["scope"]["voices"], ["Yuna"])

    def test_v4_evaluator_hash_matches_current_source(self) -> None:
        evaluator = ROOT / self.bundle["evaluator"]
        self.assertEqual(self.bundle["evaluator"], "scripts/evaluate_alden_korean_wake.py")
        self.assertEqual(self.bundle["evaluator_sha256"], hashlib.sha256(evaluator.read_bytes()).hexdigest())

    def test_v5_evaluator_hash_matches_current_source(self) -> None:
        bundle = json.loads(V5_EVIDENCE.read_text(encoding="utf-8"))
        evaluator = ROOT / bundle["evaluator"]
        self.assertEqual(bundle["evaluator"], "scripts/evaluate_alden_korean_wake.py")
        self.assertEqual(bundle["evaluator_sha256"], hashlib.sha256(evaluator.read_bytes()).hexdigest())

    def test_v5_fit_reference_is_self_contained(self) -> None:
        bundle = json.loads(V5_EVIDENCE.read_text(encoding="utf-8"))
        fit_reference = bundle["scope"]["fit_reference"]
        self.assertEqual(fit_reference, V5_FROZEN_FIT_REFERENCE)

        if V5_TRAINING_REPORT.is_file():
            training = json.loads(V5_TRAINING_REPORT.read_text(encoding="utf-8"))["training"]
            self.assertEqual(
                fit_reference,
                {
                    "positive_clips": training["positive_clips"],
                    "negative_clips": training["negative_clips"],
                    "method": training["method"],
                    "proof_scope": training["proof_scope"],
                },
            )

    def test_v5_isolated_evidence_still_fails_release_requirements(self) -> None:
        bundle = json.loads(V5_EVIDENCE.read_text(encoding="utf-8"))
        summary = bundle["summary"]
        baseline = bundle["baseline"]["summary"]

        self.assertEqual(bundle["threshold"], WAKE_THRESHOLD)
        self.assertIs(bundle["scope"]["threshold_changed"], False)
        self.assertIs(bundle["scope"]["independent_clip_state_reset"], True)
        self.assertIs(bundle["scope"]["human_speakers"], False)
        self.assertIs(bundle["scope"]["microphones_or_rooms"], False)
        self.assertIs(bundle["split"]["disjoint_verified"], True)
        self.assertEqual(bundle["split"]["voice_overlap"], [])
        self.assertEqual(bundle["split"]["negative_phrase_overlap"], [])
        self.assertEqual(bundle["split"]["clip_hash_overlap"], [])

        self.assertEqual(summary["positive_accepts"], 1)
        self.assertEqual(summary["positive_total"], 2)
        self.assertAlmostEqual(summary["positive_accept_rate"], 0.5)
        self.assertEqual(summary["negative_false_accepts"], 0)
        self.assertEqual(summary["negative_total"], 10)
        self.assertAlmostEqual(summary["negative_false_accept_rate"], 0.0)

        self.assertEqual(baseline["positive_accepts"], 0)
        self.assertEqual(baseline["positive_total"], 2)
        self.assertAlmostEqual(baseline["positive_accept_rate"], 0.0)
        self.assertEqual(baseline["negative_false_accepts"], 1)
        self.assertEqual(baseline["negative_total"], 10)
        self.assertAlmostEqual(baseline["negative_false_accept_rate"], 0.1)

        self.assertIsNone(RELEASED_WAKE_MODEL)
        self.assertIsNone(resolve_live_wake_model())

    def test_experimental_candidate_hash_when_available(self) -> None:
        candidate = self.bundle["candidate"]
        self.assertEqual(
            candidate["path"],
            "voice/models/experimental/alden_ko_ridge_candidate_v4.onnx",
        )
        self.assertGreater(candidate["bytes"], 0)
        self.assertRegex(candidate["sha256"], r"^[0-9a-f]{64}$")
        model = ROOT / candidate["path"]
        if not model.is_file():
            self.skipTest("unreleased experimental candidate is absent from this checkout")
        raw_model = model.read_bytes()
        self.assertEqual(candidate["bytes"], len(raw_model))
        self.assertEqual(candidate["sha256"], hashlib.sha256(raw_model).hexdigest())

    def test_measured_rows_reproduce_summary_and_threshold_decisions(self) -> None:
        rows = self.bundle["rows"]
        positives = [row for row in rows if row["kind"] == "positive"]
        negatives = [row for row in rows if row["kind"] == "negative"]
        summary = self.bundle["summary"]

        self.assertEqual(len(positives), 3)
        self.assertEqual(len(negatives), 3)
        for row in rows:
            self.assertEqual(row["accepted"], row["custom_max"] >= WAKE_THRESHOLD, row["name"])
            self.assertEqual(row["stock_accepted"], row["stock_max"] >= WAKE_THRESHOLD, row["name"])
        self.assertEqual(summary["positive_accepts"], sum(row["accepted"] for row in positives))
        self.assertEqual(summary["positive_total"], len(positives))
        self.assertEqual(summary["negative_false_accepts"], sum(row["accepted"] for row in negatives))
        self.assertEqual(summary["negative_total"], len(negatives))
        self.assertAlmostEqual(summary["positive_accept_rate"], 2 / 3, places=6)
        self.assertAlmostEqual(summary["negative_false_accept_rate"], 0.0)
        self.assertEqual(summary["stock_positive_accepts"], 0)
        self.assertEqual(summary["stock_negative_false_accepts"], 0)
        self.assertIs(self.bundle["scope"]["independent_clip_state_reset"], True)

    def test_failed_gate_cannot_enable_a_live_microphone(self) -> None:
        gate = self.bundle["release_gate"]
        self.assertIs(gate["passed"], False)
        self.assertIs(gate["runtime_enabled"], False)
        self.assertIn("synthetic_positive_accept_rate_below_one", gate["reasons"])
        self.assertIn("no_human_speaker_trials", gate["reasons"])
        self.assertIsNone(RELEASED_WAKE_MODEL)
        self.assertIsNone(resolve_live_wake_model())
        self.assertIsNone(resolve_custom_wake_model(None))

    def test_experimental_candidate_is_not_in_the_desktop_bundle(self) -> None:
        config = json.loads((ROOT / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
        resource_map = config["bundle"]["resources"]
        self.assertFalse(any("alden_ko_ridge" in str(path) for path in resource_map.values()))

    def test_evidence_contains_no_ephemeral_host_paths(self) -> None:
        text = EVIDENCE.read_text(encoding="utf-8")
        for marker in ("/var/folders/", "/tmp/", "/Users/", "/private/"):
            self.assertNotIn(marker, text)


if __name__ == "__main__":
    unittest.main()
