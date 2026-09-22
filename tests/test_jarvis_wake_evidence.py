"""Contract tests for the committed held-out Korean wake evidence bundle.

``docs/architecture/jarvis-wake-heldout-eval.json`` is the record that the bundled
Korean wake head was measured against voices it never saw. Evidence that drifts
from the head it measured, from the pinned threshold, or from its own per-clip
rows is worse than no evidence, so every rate here is recomputed from the raw
rows, the measured head is re-hashed, and the acceptance flags are checked
against the production decision rule. No model, microphone, or network is used.
"""

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

import evaluate_jarvis_korean_wake as evaluations  # noqa: E402
from jarvis_voice import WAKE_THRESHOLD  # noqa: E402


EVIDENCE = ROOT / "docs/architecture/jarvis-wake-heldout-eval.json"
EXPECTED_LABELS = {
    "qwen3tts-nine-voice",
    "qwen3tts-two-voice-rerender",
    "macos-say",
}


class WakeEvidenceBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = json.loads(EVIDENCE.read_text(encoding="utf-8"))

    def test_schema_and_scope_pin_the_measurement(self) -> None:
        bundle = self.bundle
        self.assertEqual(bundle["schema"], "jarvis.wake.heldout-eval/1")
        self.assertEqual(bundle["threshold"], 0.65)
        self.assertEqual(bundle["threshold"], WAKE_THRESHOLD)
        self.assertIs(bundle["threshold_changed"], False)
        self.assertIs(bundle["human_speakers"], False)
        self.assertIs(bundle["microphones_or_rooms"], False)
        self.assertTrue(bundle["scope_note"].strip())
        for run in bundle["runs"]:
            self.assertEqual(run["threshold"], WAKE_THRESHOLD)
            self.assertIs(run["threshold_changed"], False)
            self.assertIs(run["scope"]["human_speakers"], False)
            self.assertIs(run["scope"]["microphones_or_rooms"], False)

    def test_measured_head_hash_matches_the_committed_model(self) -> None:
        head = self.bundle["head"]
        path = ROOT / head["path"]
        self.assertTrue(path.is_file(), str(path))
        raw = path.read_bytes()
        self.assertEqual(head["bytes"], len(raw))
        self.assertEqual(head["sha256"], hashlib.sha256(raw).hexdigest())

    def test_evaluator_revision_is_recorded(self) -> None:
        self.assertEqual(self.bundle["evaluator"], "scripts/evaluate_jarvis_korean_wake.py")
        self.assertRegex(self.bundle["evaluator_sha256"], r"^[0-9a-f]{64}$")

    def test_every_row_uses_the_product_phrase_contract(self) -> None:
        negatives = set(evaluations.NEGATIVE_PHRASES)
        for run in self.bundle["runs"]:
            for row in run["rows"]:
                if row["kind"] == "positive":
                    self.assertEqual(row["text"], evaluations.WAKE_PHRASE)
                else:
                    self.assertEqual(row["kind"], "negative")
                    self.assertIn(row["text"], negatives)

    def test_acceptance_follows_the_pinned_threshold(self) -> None:
        for run in self.bundle["runs"]:
            for row in run["rows"]:
                expected = row["custom_max"] >= WAKE_THRESHOLD
                self.assertEqual(row["accepted"], expected, row["name"])
                self.assertEqual(row["stock_accepted"], row["stock_max"] >= WAKE_THRESHOLD, row["name"])

    def test_rates_are_recomputed_from_the_raw_rows(self) -> None:
        for run in self.bundle["runs"]:
            rows = run["rows"]
            summary = run["summary"]
            positives = [r for r in rows if r["kind"] == "positive"]
            negatives = [r for r in rows if r["kind"] == "negative"]
            with self.subTest(run=run["report_label"]):
                self.assertTrue(positives)
                self.assertTrue(negatives)
                self.assertEqual(summary["positive_total"], len(positives))
                self.assertEqual(summary["negative_total"], len(negatives))
                self.assertEqual(summary["positive_accepts"], sum(1 for r in positives if r["accepted"]))
                self.assertEqual(summary["negative_false_accepts"], sum(1 for r in negatives if r["accepted"]))
                self.assertEqual(summary["stock_positive_accepts"], sum(1 for r in positives if r["stock_accepted"]))
                self.assertEqual(
                    summary["stock_negative_false_accepts"],
                    sum(1 for r in negatives if r["stock_accepted"]),
                )
                self.assertEqual(
                    summary["accepted_voices"], sorted({r["voice"] for r in positives if r["accepted"]})
                )
                self.assertEqual(
                    summary["missed_voices"], sorted({r["voice"] for r in positives if not r["accepted"]})
                )
                self.assertEqual(
                    summary["false_accept_voices"], sorted({r["voice"] for r in negatives if r["accepted"]})
                )
                self.assertAlmostEqual(
                    summary["positive_accept_rate"],
                    round(summary["positive_accepts"] / len(positives), 6),
                    places=6,
                )
                self.assertAlmostEqual(
                    summary["negative_false_accept_rate"],
                    round(summary["negative_false_accepts"] / len(negatives), 6),
                    places=6,
                )
                self.assertAlmostEqual(
                    summary["stock_positive_accept_rate"],
                    round(summary["stock_positive_accepts"] / len(positives), 6),
                    places=6,
                )
                self.assertAlmostEqual(
                    summary["stock_negative_false_accept_rate"],
                    round(summary["stock_negative_false_accepts"] / len(negatives), 6),
                    places=6,
                )

    def test_borderline_negatives_come_from_scored_negative_rows(self) -> None:
        for run in self.bundle["runs"]:
            scored = {(r["voice"], r["text"], r["custom_max"]) for r in run["rows"] if r["kind"] == "negative"}
            for item in run["summary"]["borderline_negatives"]:
                self.assertIn((item["voice"], item["text"], item["custom_max"]), scored)

    def test_unusable_renders_are_refused_and_never_scored(self) -> None:
        for run in self.bundle["runs"]:
            self.assertEqual(run["unusable_render_count"], len(run["unusable_renders"]))
            scored_voices = {r["voice"] for r in run["rows"]}
            for item in run["unusable_renders"]:
                self.assertTrue(item["reason"].strip(), str(item))
                self.assertLess(item["seconds"], 0.25)
                self.assertNotIn(item["voice"], scored_voices)

    def test_bundle_has_the_three_expected_runs(self) -> None:
        runs = {run["report_label"]: run for run in self.bundle["runs"]}
        self.assertEqual(set(runs), EXPECTED_LABELS)
        nine = runs["qwen3tts-nine-voice"]
        self.assertEqual(len(nine["rows"]), 36)
        self.assertEqual(len({r["voice"] for r in nine["rows"]}), 9)
        self.assertEqual(
            sorted(nine["summary"]["accepted_voices"] + nine["summary"]["missed_voices"]),
            sorted({r["voice"] for r in nine["rows"]}),
        )
        self.assertEqual(nine["summary"]["stock_positive_accepts"], 0)
        self.assertEqual(nine["summary"]["stock_negative_false_accepts"], 0)
        self.assertEqual(sorted(nine["declared_voices"]), sorted({r["voice"] for r in nine["rows"]}))

    def test_bundle_carries_no_ephemeral_paths(self) -> None:
        text = EVIDENCE.read_text(encoding="utf-8")
        for marker in ("/var/folders/", "/tmp/", "/Users/", "/private/"):
            self.assertNotIn(marker, text)


if __name__ == "__main__":
    unittest.main()
