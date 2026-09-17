"""Fine-tuning pipeline tests.

The pipeline feeds mlx_lm.lora, so the split layout and the argument list are
contracts, not internal details. These tests pin the failure modes that would
silently produce a useless run: a split that leaves no training rows, a leaked
window, a mis-parsed loss line, and a comparison that calls noise an
improvement (2026-09-17).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.auto_reply_finetune import (
    DEFAULT_INSTRUCTION,
    _parse_test_loss,
    _to_mlx_row,
    build_splits,
    compare_runs,
    load_pairs,
    plan_training,
    prepare_dataset,
    write_splits,
)

NL = chr(10)


def _pair(prompt: str, completion: str, window=None) -> dict:
    return {
        "prompt": prompt,
        "completion": completion,
        "window": window or [],
        "room": "room-a",
    }


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

    def test_split_is_deterministic_for_a_seed(self):
        pairs = [_pair("질문 " + str(i), "답변 " + str(i)) for i in range(50)]
        first, _ = build_splits(pairs, seed=7)
        second, _ = build_splits(pairs, seed=7)
        self.assertEqual(first["train"], second["train"])
        self.assertEqual(first["test"], second["test"])

    def test_ratios_are_respected(self):
        pairs = [_pair("질문 " + str(i), "답변 " + str(i)) for i in range(200)]
        splits, stats = build_splits(pairs, valid_ratio=0.1, test_ratio=0.05, seed=1)
        self.assertEqual(stats.test, 10)
        self.assertEqual(stats.valid, 20)
        self.assertEqual(stats.train, 170)
        self.assertEqual(stats.train + stats.valid + stats.test, len(pairs))

    def test_a_tiny_set_still_keeps_training_rows(self):
        pairs = [_pair("질문", "답변")]
        splits, stats = build_splits(pairs, valid_ratio=0.1, test_ratio=0.05, seed=1)
        self.assertEqual(stats.train, 1)
        self.assertEqual(stats.valid, 0)
        self.assertEqual(stats.test, 0)

    def test_no_row_appears_in_two_splits(self):
        pairs = [_pair("질문 " + str(i), "답변 " + str(i)) for i in range(60)]
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


class TestPrepareDataset(unittest.TestCase):
    def test_prepare_writes_splits_and_a_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            golden = root / "golden.jsonl"
            rows = [
                json.dumps(_pair("질문 " + str(i), "답변 " + str(i)), ensure_ascii=False)
                for i in range(40)
            ]
            golden.write_text(NL.join(rows) + NL, encoding="utf-8")
            summary, splits = prepare_dataset(
                golden_path=golden, data_dir=root / "data", seed=5
            )
            self.assertEqual(summary["train"] + summary["valid"] + summary["test"], 40)
            self.assertTrue((root / "data" / "split-summary.json").exists())
            self.assertTrue(splits["train"])

    def test_prepare_on_a_missing_golden_set_is_empty_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary, splits = prepare_dataset(
                golden_path=root / "absent.jsonl", data_dir=root / "data"
            )
            self.assertEqual(summary["train"], 0)
            self.assertEqual(splits["train"], [])
            self.assertTrue((root / "data" / "train.jsonl").exists())


if __name__ == "__main__":
    unittest.main()

