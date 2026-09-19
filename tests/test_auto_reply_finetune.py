"""Fine-tuning pipeline tests.

The pipeline feeds mlx_lm.lora, so the split layout and the argument list are
contracts, not internal details. These tests pin the failure modes that would
silently produce a useless run: a split that leaves no training rows, a window
that leaks because one conversation was cut across train and test, a mis-parsed
loss line, and a comparison that calls noise an improvement (2026-09-19).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from scripts.auto_reply_finetune import (
    DEFAULT_INSTRUCTION,
    DEFAULT_SESSION_GAP,
    _parse_test_loss,
    _to_mlx_row,
    build_parser,
    build_splits,
    compare_runs,
    load_pairs,
    plan_training,
    prepare_dataset,
    prepare_dpo_preferences,
    write_splits,
)

NL = chr(10)
# A fixed clock inside the extractor's plausible range (2000-01-01 .. 2100-01-01).
_BASE_CLOCK = datetime(2026, 1, 1, 0, 0, 0)


def _stamp(hours: float) -> str:
    """A transcript clock, hours after the fixed base."""
    return (_BASE_CLOCK + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")


def _pair(
    prompt: str,
    completion: str,
    window=None,
    room: str = "room-a",
    recorded_at=None,
) -> dict:
    record = {
        "prompt": prompt,
        "completion": completion,
        "window": window or [],
        "room": room,
    }
    if recorded_at is not None:
        record["recorded_at"] = recorded_at
    return record


def _session_pairs(sessions: int, per_session: int, *, room: str = "room-a"):
    """Bursts one hour apart, so every one of them is its own session.

    Returns the pairs and a completion -> session tag map, which is how the
    leak tests say which conversation a row came from (2026-09-19).
    """
    pairs: list[dict] = []
    tags: dict[str, str] = {}
    for session in range(sessions):
        recorded_at = _stamp(session)
        for index in range(per_session):
            completion = f"답변 {session}-{index}"
            pairs.append(
                _pair(
                    f"질문 {session}-{index}",
                    completion,
                    room=room,
                    recorded_at=recorded_at,
                )
            )
            tags[completion] = str(session)
    return pairs, tags


class TestLoadPairs(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "golden.jsonl"

    def test_missing_file_yields_nothing(self):
        self.assertEqual(load_pairs(Path(self._tmp.name) / "absent.jsonl"), [])

    def test_blank_and_broken_lines_are_skipped(self):
        rows = [
            "",
            "not json",
            json.dumps(_pair("질문", "답변"), ensure_ascii=False),
            json.dumps({"prompt": "", "completion": "답변"}, ensure_ascii=False),
            json.dumps({"prompt": "질문", "completion": ""}, ensure_ascii=False),
        ]
        self.path.write_text(NL.join(rows) + NL, encoding="utf-8")
        pairs = load_pairs(self.path)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["completion"], "답변")


class TestRowShaping(unittest.TestCase):
    def test_window_becomes_the_prompt_body(self):
        row = _to_mlx_row(
            _pair(
                "폴백 질문",
                "답변입니다",
                window=[
                    {"author": "현준", "message": "오늘 늦을 것 같아"},
                    {"author": "문승현", "message": "ㅇㅇ"},
                ],
            ),
            DEFAULT_INSTRUCTION,
        )
        self.assertIn("다음 카카오톡 대화", row["prompt"])
        self.assertIn("현준: 오늘 늦을 것 같아", row["prompt"])
        self.assertIn("문승현: ㅇㅇ", row["prompt"])
        self.assertEqual(row["completion"], "답변입니다")

    def test_prompt_is_used_when_there_is_no_window(self):
        row = _to_mlx_row(_pair("그냥 질문", "답변입니다"), DEFAULT_INSTRUCTION)
        self.assertIn("그냥 질문", row["prompt"])

    def test_window_entries_without_a_message_are_ignored(self):
        row = _to_mlx_row(
            _pair("질문", "답변", window=[{"author": "현준", "message": ""}, {"nope": 1}]),
            DEFAULT_INSTRUCTION,
        )
        self.assertIn("질문", row["prompt"])


class TestSplits(unittest.TestCase):
    def test_empty_input_produces_empty_splits(self):
        splits, stats = build_splits([])
        self.assertEqual(splits["train"], [])
        self.assertEqual(stats.train, 0)
        self.assertEqual(stats.groups, 0)

    def test_split_is_deterministic_for_a_seed(self):
        pairs, _ = _session_pairs(40, 5)
        first, first_stats = build_splits(pairs, seed=7)
        second, second_stats = build_splits(pairs, seed=7)
        self.assertEqual(first["train"], second["train"])
        self.assertEqual(first["valid"], second["valid"])
        self.assertEqual(first["test"], second["test"])
        self.assertEqual(first_stats.train_groups, second_stats.train_groups)
        self.assertEqual(first_stats.test_groups, second_stats.test_groups)

    def test_ratios_are_respected(self):
        # 40 sessions of 5 rows each. The targets stay the same, but they are
        # crossed five rows at a time because a session is never cut in half.
        pairs, _ = _session_pairs(40, 5)
        splits, stats = build_splits(pairs, valid_ratio=0.1, test_ratio=0.05, seed=1)
        self.assertEqual(stats.test, 10)
        self.assertEqual(stats.valid, 20)
        self.assertEqual(stats.train, 170)
        self.assertEqual(stats.train + stats.valid + stats.test, len(pairs))
        self.assertEqual(stats.groups, 40)
        self.assertEqual(stats.test_groups, 2)
        self.assertEqual(stats.valid_groups, 4)
        self.assertEqual(stats.train_groups, 34)

    def test_a_session_never_straddles_two_splits(self):
        pairs, tags = _session_pairs(24, 3)
        splits, stats = build_splits(pairs, seed=11)
        home: dict[str, str] = {}
        for name, rows in splits.items():
            for row in rows:
                session = tags[row["completion"]]
                self.assertEqual(home.setdefault(session, name), name)
        self.assertEqual(len(home), 24)
        self.assertEqual(stats.groups, 24)

    def test_duplicate_prompts_are_dropped_and_counted(self):
        pairs = [
            _pair("오늘  늦어", "첫 번째 답변", recorded_at=_stamp(0)),
            _pair("오늘 늦어", "두 번째 답변", recorded_at=_stamp(0)),
            _pair("다른 질문", "세 번째 답변", recorded_at=_stamp(0)),
        ]
        splits, stats = build_splits(pairs, seed=4)
        completions = [row["completion"] for row in splits["train"]]
        self.assertEqual(stats.duplicate_prompts, 1)
        self.assertEqual(stats.dropped, 1)
        self.assertIn("첫 번째 답변", completions)
        self.assertNotIn("두 번째 답변", completions)
        self.assertEqual(stats.train + stats.valid + stats.test, 2)

    def test_rows_without_a_clock_share_one_group_per_room(self):
        pairs = [_pair("질문 " + str(i), "답변 " + str(i)) for i in range(3)]
        pairs += [
            _pair("질문 b" + str(i), "답변 b" + str(i), room="room-b") for i in range(2)
        ]
        splits, stats = build_splits(pairs, seed=2)
        self.assertEqual(stats.groups, 2)
        self.assertEqual(stats.train, 5)
        self.assertEqual(splits["valid"], [])
        self.assertEqual(splits["test"], [])

    def test_a_camel_case_clock_is_read(self):
        pairs = [_pair("질문 a", "답변 a"), _pair("질문 b", "답변 b")]
        pairs[0]["recordedAt"] = _stamp(0)
        pairs[1]["recordedAt"] = _stamp(2)
        _, stats = build_splits(pairs, seed=1)
        self.assertEqual(stats.groups, 2)

    def test_a_row_without_a_room_is_its_own_group(self):
        pairs = [
            _pair("질문 " + str(i), "답변 " + str(i), room="", recorded_at=_stamp(0))
            for i in range(100)
        ]
        splits, stats = build_splits(pairs, valid_ratio=0.1, test_ratio=0.05, seed=1)
        self.assertEqual(stats.groups, 100)
        self.assertEqual(stats.test, 5)
        self.assertEqual(stats.valid, 10)
        self.assertEqual(stats.train, 85)

    def test_a_single_group_trains_on_everything(self):
        pairs = [_pair("질문 " + str(i), "답변 " + str(i)) for i in range(200)]
        splits, stats = build_splits(pairs, valid_ratio=0.1, test_ratio=0.05, seed=1)
        self.assertEqual(stats.groups, 1)
        self.assertEqual(stats.train_groups, 1)
        self.assertEqual(stats.train, 200)
        self.assertEqual(stats.valid, 0)
        self.assertEqual(stats.test, 0)
        self.assertTrue(splits["train"])

    def test_a_burst_that_swallows_the_targets_still_leaves_a_train_group(self):
        big = [
            _pair("큰 질문 " + str(i), "큰 답변 " + str(i), recorded_at=_stamp(0))
            for i in range(190)
        ]
        small = [
            _pair("작은 질문 " + str(i), "작은 답변 " + str(i), recorded_at=_stamp(5))
            for i in range(10)
        ]
        splits, stats = build_splits(big + small, valid_ratio=0.1, test_ratio=0.05, seed=1)
        self.assertEqual(stats.train_groups, 1)
        self.assertEqual(stats.train, 10)
        self.assertTrue(
            all(row["completion"].startswith("작은") for row in splits["train"])
        )

    def test_the_session_gap_decides_what_counts_as_one_conversation(self):
        pairs = [
            _pair("질문 " + str(i), "답변 " + str(i), recorded_at=_stamp(i * 0.25))
            for i in range(4)
        ]
        _, tight = build_splits(pairs, session_gap=600, seed=1)
        _, loose = build_splits(pairs, session_gap=3600, seed=1)
        self.assertEqual(tight.groups, 4)
        self.assertEqual(loose.groups, 1)

    def test_a_tiny_set_still_keeps_training_rows(self):
        pairs = [_pair("질문", "답변")]
        splits, stats = build_splits(pairs, valid_ratio=0.1, test_ratio=0.05, seed=1)
        self.assertEqual(stats.train, 1)
        self.assertEqual(stats.valid, 0)
        self.assertEqual(stats.test, 0)

    def test_no_row_appears_in_two_splits(self):
        pairs, _ = _session_pairs(30, 2)
        splits, _ = build_splits(pairs, seed=3)
        train = {row["completion"] for row in splits["train"]}
        valid = {row["completion"] for row in splits["valid"]}
        test = {row["completion"] for row in splits["test"]}
        self.assertEqual(train & valid, set())
        self.assertEqual(train & test, set())
        self.assertEqual(valid & test, set())

    def test_write_splits_creates_the_three_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            written = write_splits(
                {"train": [{"prompt": "p", "completion": "c"}], "valid": [], "test": []},
                data_dir,
            )
            names = sorted(p.name for p in written)
            self.assertEqual(names, ["test.jsonl", "train.jsonl", "valid.jsonl"])
            self.assertFalse((data_dir / "train.jsonl.tmp").exists())


class TestTrainingPlan(unittest.TestCase):
    def test_iterations_are_derived_from_the_dataset(self):
        plan = plan_training(
            model="m",
            data_dir=Path("/d"),
            adapter_path=Path("/a"),
            train_examples=400,
            batch_size=4,
        )
        # two epochs over 400 examples at batch size 4
        self.assertEqual(plan.iters, 200)

    def test_explicit_iterations_win(self):
        plan = plan_training(
            model="m", data_dir=Path("/d"), adapter_path=Path("/a"),
            train_examples=400, iters=30,
        )
        self.assertEqual(plan.iters, 30)

    def test_command_masks_the_prompt_and_carries_every_option(self):
        plan = plan_training(
            model="mlx-community/gemma-3-4b-it-4bit",
            data_dir=Path("/d"),
            adapter_path=Path("/a"),
            train_examples=10,
            iters=5,
            fine_tune_type="dora",
        )
        self.assertIn("--mask-prompt", plan.command)
        self.assertIn("dora", plan.command)
        self.assertIn("mlx-community/gemma-3-4b-it-4bit", plan.command)
        self.assertEqual(plan.command[0], "mlx_lm.lora")


class TestEvaluation(unittest.TestCase):
    def test_loss_line_is_parsed(self):
        output = NL.join(["Iter 1: Val loss 10.846", "Test loss 3.412, Test ppl 30.3"])
        self.assertEqual(_parse_test_loss(output), 3.412)

    def test_missing_loss_returns_none(self):
        self.assertIsNone(_parse_test_loss("nothing here"))

    def test_improvement_needs_to_clear_the_threshold(self):
        result = compare_runs({"loss": 3.0}, {"loss": 2.0})
        self.assertTrue(result["improved"])
        self.assertEqual(result["reason"], "loss_improved")

    def test_noise_is_not_an_improvement(self):
        result = compare_runs({"loss": 3.00}, {"loss": 2.995})
        self.assertFalse(result["improved"])
        self.assertEqual(result["reason"], "no_meaningful_change")

    def test_missing_loss_is_reported_not_assumed(self):
        result = compare_runs({"loss": None}, {"loss": 2.0})
        self.assertFalse(result["improved"])
        self.assertEqual(result["reason"], "loss_unavailable")


class TestArguments(unittest.TestCase):
    def test_the_session_gap_defaults_to_thirty_minutes(self):
        self.assertEqual(DEFAULT_SESSION_GAP, 1800)
        self.assertEqual(build_parser().parse_args([]).session_gap, DEFAULT_SESSION_GAP)


class TestDPOPreparation(unittest.TestCase):
    def test_dpo_prepare_missing_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "dpo" / "preferences.jsonl"
            result = prepare_dpo_preferences(
                golden_path=root / "missing-golden.jsonl",
                output_path=output,
            )
            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "golden_missing")
            self.assertEqual(result["pairs"], 0)
            self.assertFalse(output.exists())
            self.assertNotIn("prompt", result)
            self.assertNotIn("completion", result)


class TestPrepareDataset(unittest.TestCase):
    def test_prepare_writes_splits_and_reports_duplicate_prompts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pairs, _ = _session_pairs(10, 4)
            golden = root / "golden.jsonl"
            rows = [json.dumps(pair, ensure_ascii=False) for pair in pairs]
            # The first row comes back a second time, as it does in a golden
            # set built from an overlapping window.
            rows.append(json.dumps(pairs[0], ensure_ascii=False))
            golden.write_text(NL.join(rows) + NL, encoding="utf-8")
            summary, splits = prepare_dataset(
                golden_path=golden, data_dir=root / "data", seed=5, session_gap=1800
            )
            self.assertEqual(summary["train"] + summary["valid"] + summary["test"], 40)
            self.assertEqual(summary["dropped"], 1)
            self.assertEqual(summary["duplicate_prompts"], 1)
            self.assertEqual(summary["groups"], 10)
            self.assertEqual(summary["test_groups"], 1)
            self.assertEqual(summary["valid_groups"], 1)
            self.assertEqual(summary["train_groups"], 8)
            self.assertEqual(summary["session_gap"], 1800)
            self.assertTrue((root / "data" / "split-summary.json").exists())
            self.assertTrue(splits["train"])

    def test_prepare_on_a_missing_golden_set_is_empty_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary, splits = prepare_dataset(
                golden_path=root / "absent.jsonl", data_dir=root / "data"
            )
            self.assertEqual(summary["train"], 0)
            self.assertEqual(summary["groups"], 0)
            self.assertEqual(splits["train"], [])
            self.assertTrue((root / "data" / "train.jsonl").exists())


if __name__ == "__main__":
    unittest.main()

