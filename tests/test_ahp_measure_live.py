"""Tests for the live AHP scorer.

The scorer used to return a hard-coded dict, so nothing here could fail. These
tests pin the behaviour that made it a measurement: a criterion with no
observations reports insufficient_data instead of a high default, and the
numbers move when the ledger moves (2026-09-16).
"""

import datetime as dt
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_scorer():
    path = SCRIPTS / "ahp_measure_live.py"
    spec = importlib.util.spec_from_file_location("ahp_measure_live_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SCORER = load_scorer()


def write_ledger(state_root: Path, chat_id: str, rows: list) -> None:
    room = state_root / "rooms" / chat_id
    room.mkdir(parents=True, exist_ok=True)
    body = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    (room / "reply-evidence.jsonl").write_text(body + "\n", encoding="utf-8")


def stamp(days_ago: float = 0.0) -> str:
    when = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)
    return when.isoformat()


class MeasureEmptyStateTests(unittest.TestCase):
    """No ledger at all: every criterion must say it has no data."""

    def test_missing_ledger_reports_insufficient_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = SCORER.measure(Path(tmp), "417780809780519", 7)
        for name, item in report["criteria"].items():
            with self.subTest(criterion=name):
                self.assertIsNone(item["score"])
                self.assertEqual(item["state"], "insufficient_data")

    def test_empty_ledger_file_reports_insufficient_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_ledger(root, "1", [])
            report = SCORER.measure(root, "1", 7)
        self.assertEqual(report["ledger_rows_in_window"], 0)
        self.assertIsNone(report["criteria"]["observability"]["score"])

    def test_malformed_lines_are_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            room = root / "rooms" / "1"
            room.mkdir(parents=True, exist_ok=True)
            (room / "reply-evidence.jsonl").write_text(
                'not json\n{"recorded_at": "' + stamp() + '", "event_id": "e1", "status": "sent"}\n',
                encoding="utf-8",
            )
            report = SCORER.measure(root, "1", 7)
        self.assertEqual(report["ledger_rows_in_window"], 1)

    def test_rows_outside_the_window_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_ledger(
                root,
                "1",
                [
                    {
                        "recorded_at": stamp(days_ago=30),
                        "event_id": "old",
                        "status": "sent",
                        "reply": "old",
                    }
                ],
            )
            report = SCORER.measure(root, "1", 7)
        self.assertEqual(report["ledger_rows_in_window"], 0)
        self.assertEqual(report["ledger_rows_total"], 1)


class CriterionTests(unittest.TestCase):
    def test_evidence_delivery_counts_only_turns_that_retrieved(self):
        rows = [
            # Retrieved 3, delivered 3 -> counts as delivered.
            {
                "recorded_at": stamp(),
                "event_id": "a",
                "status": "scheduled",
                "retrieved_evidence_ids": 3,
                "prompt_evidence_ids": 3,
            },
            # Retrieved 4, delivered 1 -> counts against.
            {
                "recorded_at": stamp(),
                "event_id": "b",
                "status": "scheduled",
                "retrieved_evidence_ids": 4,
                "prompt_evidence_ids": 1,
            },
            # Never retrieved -> excluded, cannot fail this criterion.
            {
                "recorded_at": stamp(),
                "event_id": "c",
                "status": "skipped",
                "retrieved_evidence_ids": 0,
                "prompt_evidence_ids": 0,
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_ledger(root, "1", rows)
            item = SCORER.measure(root, "1", 7)["criteria"]["evidence_delivery"]
        self.assertEqual(item["denominator"], 2)
        self.assertEqual(item["numerator"], 1)
        self.assertAlmostEqual(item["score"], 0.5, places=4)

    def test_register_style_flags_overlong_replies(self):
        rows = [
            {"recorded_at": stamp(), "event_id": "a", "status": "sent", "reply": "짧은 답"},
            {"recorded_at": stamp(), "event_id": "b", "status": "sent", "reply": "가" * 200},
            {"recorded_at": stamp(), "event_id": "c", "status": "deferred", "reply": "안 보낸 답"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_ledger(root, "1", rows)
            item = SCORER.measure(root, "1", 7)["criteria"]["register_style"]
        # Only the two sent rows count; the deferred draft is not a send.
        self.assertEqual(item["denominator"], 2)
        self.assertEqual(item["numerator"], 1)

    def test_intervention_timing_penalises_stale_and_duplicate(self):
        rows = [
            {"recorded_at": stamp(), "event_id": "a", "status": "sent", "reason": None},
            {"recorded_at": stamp(), "event_id": "b", "status": "skipped", "reason": "stale_backlog"},
            {"recorded_at": stamp(), "event_id": "c", "status": "skipped", "reason": "already_commented"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_ledger(root, "1", rows)
            item = SCORER.measure(root, "1", 7)["criteria"]["intervention_timing"]
        self.assertEqual(item["denominator"], 3)
        self.assertEqual(item["numerator"], 1)

    def test_content_fit_joins_the_scheduled_and_sent_rows(self):
        """The retrieval counts sit on one row and the send on another.

        Scoring the sent row alone reported 0 for a turn that did retrieve
        context, which is the bug this pins.
        """
        rows = [
            {
                "recorded_at": stamp(),
                "event_id": "turn-1",
                "status": "scheduled",
                "context_match_count": 4,
            },
            {
                "recorded_at": stamp(),
                "event_id": "turn-1",
                "status": "sent",
                "reply": "답",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_ledger(root, "1", rows)
            item = SCORER.measure(root, "1", 7)["criteria"]["content_fit"]
        self.assertEqual(item["denominator"], 1)
        self.assertEqual(item["numerator"], 1)
        self.assertAlmostEqual(item["score"], 1.0, places=4)

    def test_content_fit_reads_retrieval_block_too(self):
        rows = [
            {
                "recorded_at": stamp(),
                "event_id": "turn-2",
                "status": "scheduled",
                "retrieval": {"context_matches": 2},
            },
            {"recorded_at": stamp(), "event_id": "turn-2", "status": "sent", "reply": "답"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_ledger(root, "1", rows)
            item = SCORER.measure(root, "1", 7)["criteria"]["content_fit"]
        self.assertEqual(item["numerator"], 1)

    def test_content_fit_ignores_turns_that_never_sent(self):
        rows = [
            {
                "recorded_at": stamp(),
                "event_id": "turn-3",
                "status": "scheduled",
                "context_match_count": 0,
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_ledger(root, "1", rows)
            item = SCORER.measure(root, "1", 7)["criteria"]["content_fit"]
        self.assertEqual(item["state"], "insufficient_data")

    def test_reliability_counts_model_outages(self):
        rows = [
            {"recorded_at": stamp(), "event_id": "a", "status": "sent", "reason": None},
            {
                "recorded_at": stamp(),
                "event_id": "b",
                "status": "deferred",
                "reason": "model_temporarily_unavailable",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_ledger(root, "1", rows)
            item = SCORER.measure(root, "1", 7)["criteria"]["reliability"]
        self.assertEqual(item["denominator"], 2)
        self.assertEqual(item["numerator"], 1)

    def test_observability_needs_event_id_status_and_time(self):
        rows = [
            {"recorded_at": stamp(), "event_id": "a", "status": "sent"},
            {"recorded_at": stamp(), "event_id": "", "status": "sent"},
            {"recorded_at": stamp(), "event_id": "c", "status": ""},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_ledger(root, "1", rows)
            item = SCORER.measure(root, "1", 7)["criteria"]["observability"]
        self.assertEqual(item["denominator"], 3)
        self.assertEqual(item["numerator"], 1)

    def test_an_unstampable_row_is_outside_every_window(self):
        """A row with no readable time cannot be placed in the period.

        It is dropped from the window rather than counted as a traced turn, so
        the score never credits a row a reviewer could not date.
        """
        rows = [
            {"recorded_at": "not-a-time", "event_id": "c", "status": "sent"},
            {"recorded_at": stamp(), "event_id": "a", "status": "sent"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_ledger(root, "1", rows)
            report = SCORER.measure(root, "1", 7)
        self.assertEqual(report["ledger_rows_in_window"], 1)
        self.assertEqual(report["criteria"]["observability"]["denominator"], 1)


class ShapeTests(unittest.TestCase):
    def test_every_criterion_carries_its_own_evidence(self):
        rows = [
            {"recorded_at": stamp(), "event_id": "a", "status": "sent", "reply": "답"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_ledger(root, "1", rows)
            report = SCORER.measure(root, "1", 7)
        for name, item in report["criteria"].items():
            with self.subTest(criterion=name):
                self.assertEqual(item["criterion"], name)
                self.assertEqual(item["scorer_revision"], SCORER.SCORER_REVISION)
                self.assertIn("sample_count", item)
                self.assertIn("period", item)
                self.assertIn("source_event_ids", item)

    def test_scores_file_keeps_the_insufficient_data_state(self):
        """ahp_naturalness.py reads this file, so the shape has to survive."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = SCORER.measure(root, "1", 7)
            out = root / "ahp-scores.json"
            SCORER.write_scores(report, out)
            payload = json.loads(out.read_text(encoding="utf-8"))
        for name in SCORER_CRITERIA:
            with self.subTest(criterion=name):
                self.assertIn(name, payload)
                self.assertEqual(payload[name]["state"], "insufficient_data")
                # A missing observation must never become a high default.
                self.assertEqual(payload[name]["score"], 0.0)

    def test_measure_never_raises_on_a_broken_state_root(self):
        """A scorer that dies scores nothing; it has to report instead."""
        report = SCORER.measure(Path("/nonexistent/state/root"), "1", 7)
        self.assertEqual(report["ledger_rows_total"], 0)


SCORER_CRITERIA = (
    "evidence_delivery",
    "register_style",
    "intervention_timing",
    "content_fit",
    "reliability",
    "observability",
)


if __name__ == "__main__":
    unittest.main()
