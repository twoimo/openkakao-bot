"""Dream-RSI replay loop tests.

The loop used to score hardcoded constants, which meant it always picked the
same policy no matter what the room said. It now scores candidate replies
against the golden answers, so these tests pin the similarity measure, the
room-spread term, and the insufficient-data path (2026-09-17).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.auto_reply_dream_rsi import (
    DreamRsiSimulator,
    answer_similarity,
    distribution_report,
    dream_policy_evaluation,
    load_replay_rows,
)

NL = chr(10)


def _write_golden(root: Path, rows) -> Path:
    golden = root / "golden" / "reply-golden.jsonl"
    golden.parent.mkdir(parents=True, exist_ok=True)
    golden.write_text(
        NL.join(json.dumps(r, ensure_ascii=False) for r in rows) + NL,
        encoding="utf-8",
    )
    return golden


class TestSimilarity(unittest.TestCase):
    def test_identical_answers_score_one(self):
        self.assertAlmostEqual(answer_similarity("좋은데?", "좋은데?"), 1.0, places=6)

    def test_unrelated_answers_score_low(self):
        self.assertLess(answer_similarity("좋은데?", "완전 다른 이야기입니다"), 0.2)

    def test_spacing_does_not_change_the_score(self):
        self.assertAlmostEqual(
            answer_similarity("일단 해보고 판단하자", "일단해보고판단하자"), 1.0, places=6
        )

    def test_empty_side_scores_zero(self):
        self.assertEqual(answer_similarity("", "답변"), 0.0)
        self.assertEqual(answer_similarity("답변", ""), 0.0)

    def test_partial_overlap_sits_between(self):
        score = answer_similarity("일단 해보고 판단하자", "일단 해보고 나중에 결정하자")
        self.assertGreater(score, 0.2)
        self.assertLess(score, 1.0)


class TestReplayRows(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_missing_golden_set_yields_nothing(self):
        self.assertEqual(load_replay_rows(self.root), [])

    def test_rows_without_prompt_or_gold_are_skipped(self):
        _write_golden(
            self.root,
            [
                {"prompt": "질문", "completion": "답변", "room": "a"},
                {"prompt": "", "completion": "답변", "room": "a"},
                {"prompt": "질문", "completion": "", "room": "a"},
                "not json",
            ],
        )
        rows = load_replay_rows(self.root)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["gold"], "답변")

    def test_limit_is_respected(self):
        _write_golden(
            self.root,
            [{"prompt": "질문" + str(i), "completion": "답변" + str(i)} for i in range(30)],
        )
        self.assertEqual(len(load_replay_rows(self.root, limit=5)), 5)


class TestReplayPolicy(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_no_rows_reports_insufficient_data(self):
        simulator = DreamRsiSimulator(self.root)
        result = simulator.replay_policy(lambda row: "답변")
        self.assertEqual(result["status"], "insufficient_data")
        self.assertEqual(result["objective_score"], 0.0)

    def test_a_perfect_policy_beats_a_blank_one(self):
        _write_golden(
            self.root,
            [{"prompt": "질문", "completion": "정확한 답변", "room": "a"}],
        )
        simulator = DreamRsiSimulator(self.root)
        perfect = simulator.replay_policy(lambda row: row["gold"])
        blank = simulator.replay_policy(lambda row: "")
        self.assertGreater(perfect["objective_score"], blank["objective_score"])
        self.assertEqual(perfect["avg_similarity"], 1.0)
        self.assertEqual(blank["answered_rows"], 0)

    def test_a_policy_that_raises_is_counted_not_fatal(self):
        _write_golden(
            self.root,
            [{"prompt": "질문", "completion": "답변", "room": "a"}],
        )
        simulator = DreamRsiSimulator(self.root)

        def broken(row):
            raise RuntimeError("boom")

        result = simulator.replay_policy(broken)
        self.assertEqual(result["status"], "evaluated")
        self.assertEqual(result["answered_rows"], 0)
        self.assertEqual(simulator.parse_errors, 1)

    def test_room_spread_counts_distinct_rooms(self):
        _write_golden(
            self.root,
            [
                {"prompt": "q1", "completion": "a1", "room": "room-a"},
                {"prompt": "q2", "completion": "a2", "room": "room-b"},
            ],
        )
        simulator = DreamRsiSimulator(self.root)
        result = simulator.replay_policy(lambda row: row["gold"])
        self.assertEqual(result["room_spread"], 2)


class TestDreamLoop(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_loop_picks_the_best_scoring_policy(self):
        _write_golden(
            self.root,
            [{"prompt": "질문", "completion": "정확한 답변", "room": "a"}],
        )
        result = dream_policy_evaluation(
            self.root,
            policies={
                "perfect": lambda row: row["gold"],
                "blank": lambda row: "",
            },
        )
        self.assertEqual(result["selected_policy"], "perfect")
        self.assertEqual(result["status"], "evaluated")
        self.assertTrue((self.root / "dream-rsi-policy.json").exists())

    def test_loop_without_data_selects_nothing(self):
        result = dream_policy_evaluation(self.root, policies={"blank": lambda row: ""})
        self.assertEqual(result["selected_policy"], "")
        self.assertEqual(result["status"], "insufficient_data")


class TestDistribution(unittest.TestCase):
    def test_distribution_describes_the_gold_answers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_golden(
                root,
                [
                    {"prompt": "q" + str(i), "completion": "가" * (i + 1), "room": "a"}
                    for i in range(10)
                ],
            )
            report = distribution_report(root)
            self.assertEqual(report["rows"], 10)
            self.assertEqual(report["status"], "measured")
            self.assertGreater(report["mean_length"], 0)

    def test_distribution_without_data_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = distribution_report(Path(tmp))
            self.assertEqual(report["status"], "insufficient_data")


if __name__ == "__main__":
    unittest.main()

