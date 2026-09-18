"""Dream-RSI replay loop tests.

The loop used to score hardcoded constants, which meant it always picked the
same policy no matter what the room said. It now scores candidate replies
against the golden answers, so these tests pin the similarity measure, the
room-spread term, and the insufficient-data path (2026-09-17).

The only rows that may score a candidate are the ones a person wrote or
approved. A row holding the worker's own sent reply is the target the loop
would otherwise converge toward, so the model-gold exclusion, its counters,
and the policy_row allowlist are pinned here as well (2026-09-19).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scripts.auto_reply_dream_rsi import (
    DreamRsiSimulator,
    _candidate_policies,
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
                {"prompt": "질문", "completion": "답변", "room": "a", "source": "self_history"},
                {"prompt": "", "completion": "답변", "room": "a", "source": "self_history"},
                {"prompt": "질문", "completion": "", "room": "a", "source": "self_history"},
                "not json",
            ],
        )
        rows = load_replay_rows(self.root)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["gold"], "답변")

    def test_limit_is_respected(self):
        _write_golden(
            self.root,
            [
                {"prompt": "질문" + str(i), "completion": "답변" + str(i), "source": "self_history"}
                for i in range(30)
            ],
        )
        self.assertEqual(len(load_replay_rows(self.root, limit=5)), 5)

    def test_model_generated_gold_is_excluded_and_counted(self):
        _write_golden(
            self.root,
            [
                {
                    "prompt": "사람 질문",
                    "completion": "사람 답변",
                    "room": "a",
                    "source": "self_history",
                },
                {
                    "prompt": "봇 질문",
                    "completion": "모델이 보낸 답변",
                    "room": "a",
                    "source": "auto_reply_sent",
                },
            ],
        )
        counters: dict[str, int] = {}

        rows = load_replay_rows(self.root, counters=counters)

        self.assertEqual([row["gold"] for row in rows], ["사람 답변"])
        self.assertEqual(counters["gold_rows"], 1)
        self.assertEqual(counters["excluded_model_gold"], 1)

    def test_allow_model_gold_keeps_the_sent_reply_explicitly(self):
        _write_golden(
            self.root,
            [
                {
                    "prompt": "사람 질문",
                    "completion": "사람 답변",
                    "room": "a",
                    "source": "self_history",
                },
                {
                    "prompt": "봇 질문",
                    "completion": "모델이 보낸 답변",
                    "room": "a",
                    "source": "auto_reply_sent",
                },
            ],
        )
        counters: dict[str, int] = {}

        rows = load_replay_rows(self.root, allow_model_gold=True, counters=counters)

        self.assertEqual([row["gold"] for row in rows], ["사람 답변", "모델이 보낸 답변"])
        self.assertEqual(counters["gold_rows"], 2)
        self.assertEqual(counters["excluded_model_gold"], 0)

    def test_a_quality_field_decides_when_there_is_no_source(self):
        _write_golden(
            self.root,
            [
                {"prompt": "q1", "completion": "승인된 답변", "quality": "approved"},
                {"prompt": "q2", "completion": "사람이 쓴 답변", "quality": "human_authored"},
                {"prompt": "q3", "completion": "검토하지 않은 답변", "quality": "unreviewed"},
                {"prompt": "q4", "completion": "모델이 쓴 답변", "quality": "model_generated"},
            ],
        )
        counters: dict[str, int] = {}

        rows = load_replay_rows(self.root, counters=counters)

        self.assertEqual([row["gold"] for row in rows], ["승인된 답변", "사람이 쓴 답변"])
        self.assertEqual(counters["gold_rows"], 2)
        self.assertEqual(counters["excluded_model_gold"], 1)

    def test_a_row_without_provenance_is_not_evaluation_gold(self):
        _write_golden(
            self.root, [{"prompt": "질문", "completion": "출처 없는 답변", "room": "a"}]
        )
        counters: dict[str, int] = {}

        rows = load_replay_rows(self.root, counters=counters)

        self.assertEqual(rows, [])
        self.assertEqual(counters["gold_rows"], 0)
        self.assertEqual(counters["excluded_model_gold"], 0)


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
            [
                {
                    "prompt": "질문",
                    "completion": "정확한 답변",
                    "room": "a",
                    "source": "self_history",
                }
            ],
        )
        simulator = DreamRsiSimulator(self.root)
        perfect = simulator.replay_policy(lambda row: "정확한 답변")
        blank = simulator.replay_policy(lambda row: "")
        self.assertGreater(perfect["objective_score"], blank["objective_score"])
        self.assertEqual(perfect["avg_similarity"], 1.0)
        self.assertEqual(blank["answered_rows"], 0)

    def test_candidate_policies_cannot_observe_or_copy_gold(self):
        sentinel = "__UNIQUE_GOLD_SENTINEL_7f12c9__"
        _write_golden(
            self.root,
            [
                {
                    "prompt": "generation-time prompt",
                    "completion": sentinel,
                    "room": "a",
                    "source": "self_history",
                    "window": [{"message": "generation-time context"}],
                }
            ],
        )
        simulator = DreamRsiSimulator(self.root)

        for name, policy in _candidate_policies().items():
            seen_rows = []
            returned = []

            def observed(row, *, _policy=policy):
                seen_rows.append(dict(row))
                candidate = _policy(row)
                returned.append(candidate)
                return candidate

            with self.subTest(policy=name):
                simulator.replay_policy(observed)
                self.assertNotIn("gold", seen_rows[0])
                self.assertNotIn("source", seen_rows[0])
                self.assertNotEqual(returned[0], sentinel)

    def test_policy_row_carries_only_generation_time_inputs(self):
        _write_golden(
            self.root,
            [
                {
                    "prompt": "질문",
                    "completion": "답변",
                    "room": "a",
                    "source": "self_history",
                }
            ],
        )
        simulator = DreamRsiSimulator(self.root)
        seen: list[dict[str, Any]] = []

        simulator.replay_policy(lambda row: seen.append(dict(row)) or "")

        self.assertEqual(list(seen[0]), ["prompt", "room", "window"])

    def test_policy_row_hides_source_even_for_a_kept_model_gold_row(self):
        _write_golden(
            self.root,
            [
                {
                    "prompt": "질문",
                    "completion": "모델이 보낸 답변",
                    "room": "a",
                    "source": "auto_reply_sent",
                }
            ],
        )
        simulator = DreamRsiSimulator(self.root, allow_model_gold=True)
        seen: list[dict[str, Any]] = []

        result = simulator.replay_policy(lambda row: seen.append(dict(row)) or "")

        self.assertEqual(result["evaluated_rows"], 1)
        self.assertNotIn("source", seen[0])
        self.assertNotIn("gold", seen[0])

    def test_a_policy_that_raises_is_counted_not_fatal(self):
        _write_golden(
            self.root,
            [
                {
                    "prompt": "질문",
                    "completion": "답변",
                    "room": "a",
                    "source": "self_history",
                }
            ],
        )
        simulator = DreamRsiSimulator(self.root)

        def broken(row):
            raise RuntimeError("boom")

        result = simulator.replay_policy(broken)
        self.assertEqual(result["status"], "evaluated")
        self.assertEqual(result["answered_rows"], 0)
        self.assertEqual(simulator.parse_errors, 1)
        self.assertEqual(result["candidate_errors"], {"exception:RuntimeError": 1})

    def test_non_string_candidate_is_skipped_and_recorded(self):
        _write_golden(
            self.root,
            [
                {
                    "prompt": "질문",
                    "completion": "답변",
                    "room": "a",
                    "source": "self_history",
                }
            ],
        )
        simulator = DreamRsiSimulator(self.root)
        result = simulator.replay_policy(lambda row: 123)

        self.assertEqual(result["status"], "evaluated")
        self.assertEqual(result["answered_rows"], 0)
        self.assertEqual(result["avg_similarity"], 0.0)
        self.assertEqual(simulator.parse_errors, 1)
        self.assertEqual(result["candidate_errors"], {"non_string:int": 1})

    def test_room_spread_counts_distinct_rooms(self):
        _write_golden(
            self.root,
            [
                {
                    "prompt": "q1",
                    "completion": "a1",
                    "room": "room-a",
                    "source": "self_history",
                },
                {
                    "prompt": "q2",
                    "completion": "a2",
                    "room": "room-b",
                    "source": "self_history",
                },
            ],
        )
        simulator = DreamRsiSimulator(self.root)
        result = simulator.replay_policy(
            lambda row: {"q1": "a1", "q2": "a2"}[row["prompt"]]
        )
        self.assertEqual(result["room_spread"], 2)


class TestCandidatePolicies(unittest.TestCase):
    def test_longest_window_message_picks_the_longest_message(self):
        row = {
            "window": [
                {"message": "짧은 말"},
                {"message": "가장 긴 메시지입니다"},
                {"message": "중간"},
            ]
        }

        policy = _candidate_policies()["longest_window_message"]

        self.assertEqual(policy(row), "가장 긴 메시지입니다")

    def test_longest_window_message_prefers_the_earlier_message_on_a_tie(self):
        row = {"window": [{"message": "가나다라"}, {"message": "마바사아"}]}

        policy = _candidate_policies()["longest_window_message"]

        self.assertEqual(policy(row), "가나다라")

    def test_window_policies_tolerate_a_missing_or_odd_window(self):
        policies = _candidate_policies()

        for name in ("echo_last_message", "longest_window_message"):
            with self.subTest(policy=name):
                self.assertEqual(policies[name]({}), "")
                self.assertEqual(policies[name]({"window": "not a list"}), "")
                self.assertEqual(policies[name]({"window": ["not a dict"]}), "")


class TestDreamLoop(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _write_human_and_model_gold(self) -> None:
        """One row the operator wrote, one row the worker sent itself."""
        _write_golden(
            self.root,
            [
                {
                    "prompt": "사람 질문",
                    "completion": "사람이 쓴 답변",
                    "room": "a",
                    "source": "self_history",
                },
                {
                    "prompt": "봇 질문",
                    "completion": "모델이 보낸 답변",
                    "room": "a",
                    "source": "auto_reply_sent",
                },
            ],
        )

    def test_loop_picks_the_best_scoring_policy(self):
        _write_golden(
            self.root,
            [
                {
                    "prompt": "질문",
                    "completion": "정확한 답변",
                    "room": "a",
                    "source": "self_history",
                }
            ],
        )
        result = dream_policy_evaluation(
            self.root,
            policies={
                "perfect": lambda row: "정확한 답변",
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

    def test_active_features_follow_actual_policy_definitions(self):
        result = dream_policy_evaluation(
            self.root,
            policies={"first": lambda row: "", "second": lambda row: ""},
        )
        self.assertEqual(result["active_features"], {"first": True, "second": True})

    def test_checkpoint_write_errors_are_surfaced(self):
        blocked_root = self.root / "not-a-directory"
        blocked_root.write_text("block mkdir", encoding="utf-8")
        with self.assertRaises(OSError):
            dream_policy_evaluation(blocked_root, policies={"blank": lambda row: ""})

    def test_checkpoint_records_the_gold_source_filter(self):
        self._write_human_and_model_gold()

        result = dream_policy_evaluation(self.root, policies={"blank": lambda row: ""})

        self.assertEqual(result["replay_rows"], 1)
        self.assertEqual(result["gold_rows"], 1)
        self.assertEqual(result["excluded_model_gold"], 1)
        self.assertEqual(result["gold_source_policy"], "human_only")
        stored = json.loads(
            (self.root / "dream-rsi-policy.json").read_text(encoding="utf-8")
        )
        self.assertEqual(stored["gold_rows"], 1)
        self.assertEqual(stored["excluded_model_gold"], 1)
        self.assertEqual(stored["gold_source_policy"], "human_only")

    def test_checkpoint_records_an_opted_in_model_gold_run(self):
        self._write_human_and_model_gold()

        result = dream_policy_evaluation(
            self.root,
            policies={"blank": lambda row: ""},
            allow_model_gold=True,
        )

        self.assertEqual(result["replay_rows"], 2)
        self.assertEqual(result["excluded_model_gold"], 0)
        self.assertEqual(result["gold_source_policy"], "human_and_model")

    def test_the_loops_own_reply_is_not_the_target(self):
        self._write_human_and_model_gold()

        result = dream_policy_evaluation(
            self.root, policies={"copy_sent_reply": lambda row: "모델이 보낸 답변"}
        )

        # The candidate reproduces the reply the worker already sent, which is
        # only scored against the human answer instead of against itself.
        scored = result["evaluations"]["copy_sent_reply"]
        self.assertEqual(scored["evaluated_rows"], result["gold_rows"])
        self.assertEqual(scored["evaluated_rows"], 1)


class TestDistribution(unittest.TestCase):
    def test_distribution_describes_the_gold_answers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_golden(
                root,
                [
                    {
                        "prompt": "q" + str(i),
                        "completion": "가" * (i + 1),
                        "room": "a",
                        "source": "self_history",
                    }
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

    def test_distribution_uses_the_same_human_only_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_golden(
                root,
                [
                    {
                        "prompt": "q1",
                        "completion": "사람 답변",
                        "room": "a",
                        "source": "self_history",
                    },
                    {
                        "prompt": "q2",
                        "completion": "모델이 보낸 아주 긴 답변입니다",
                        "room": "a",
                        "source": "auto_reply_sent",
                    },
                ],
            )
            report = distribution_report(root)

            self.assertEqual(report["rows"], 1)
            self.assertEqual(report["excluded_model_gold"], 1)
            self.assertAlmostEqual(report["mean_length"], float(len("사람 답변")))


if __name__ == "__main__":
    unittest.main()
