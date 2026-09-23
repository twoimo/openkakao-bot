"""Fine-tuning pipeline tests.

The pipeline feeds mlx_lm.lora, so the split layout and the argument list are
contracts, not internal details. These tests pin the failure modes that would
silently produce a useless run: a split that leaves no training rows, a window
that leaks because one conversation was cut across train and test, a mis-parsed
loss line, and a comparison that calls noise an improvement (2026-09-19).
"""

from __future__ import annotations

import contextlib
import io
import json
import math
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from scripts import auto_reply_finetune as F
from scripts.auto_reply_finetune import (
    CAPTURE_MAX_RESPONSE_BYTES,
    DEFAULT_INSTRUCTION,
    DEFAULT_SESSION_GAP,
    DPO_EVAL_UNAVAILABLE,
    _parse_test_loss,
    _to_mlx_row,
    build_parser,
    capture_preference_pair_logprobs,
    capture_response_logprobs,
    build_splits,
    compare_runs,
    dpo_loss_from_logprobs,
    evaluate_preference_pairs,
    load_pairs,
    load_preference_pairs,
    main,
    plan_training,
    prepare_dataset,
    probe_response_scoring,
    sample_prompt_candidates,
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
            model="mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit",
            data_dir=Path("/d"),
            adapter_path=Path("/a"),
            train_examples=10,
            iters=5,
            fine_tune_type="dora",
        )
        self.assertIn("--mask-prompt", plan.command)
        self.assertIn("dora", plan.command)
        self.assertIn("mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit", plan.command)
        self.assertNotIn("gemma", " ".join(plan.command).casefold())
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


class _FakeResponse:
    """urlopen 대역. 게이트웨이 응답 본문만 돌려준다."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self, _size: int = -1) -> bytes:
        return self._payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


def _logprob_body(entries, content: str = "응답") -> bytes:
    return json.dumps(
        {
            "choices": [
                {
                    "message": {"role": "assistant", "content": content},
                    "logprobs": {
                        "content": [
                            {"token": "t", "logprob": value} for value in entries
                        ]
                    },
                }
            ]
        },
        ensure_ascii=False,
    ).encode("utf-8")


def _capture_ok(text: str, logprobs) -> dict:
    return {
        "status": "ok",
        "logprobs": list(logprobs),
        "sum": float(sum(logprobs)),
        "token_count": len(logprobs),
        "response_text": text,
    }


class _IsolatedMlxLeaseTestCase(unittest.TestCase):
    def setUp(self):
        self._lease_state = tempfile.TemporaryDirectory()
        self.addCleanup(self._lease_state.cleanup)
        patcher = mock.patch(
            "scripts.local_mlx_gateway.resolve_mlx_state_root",
            return_value=Path(self._lease_state.name),
        )
        patcher.start()
        self.addCleanup(patcher.stop)


class DpoCaptureTests(_IsolatedMlxLeaseTestCase):
    def test_real_logprobs_are_summed(self):
        with mock.patch.object(
            F, "_open_local_only", return_value=_FakeResponse(_logprob_body([-0.5, -0.25]))
        ):
            result = capture_response_logprobs(
                base_url="http://127.0.0.1:11234/v1",
                model="flash-next",
                messages=[{"role": "user", "content": "안녕"}],
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["logprobs"], [-0.5, -0.25])
        self.assertAlmostEqual(result["sum"], -0.75)
        self.assertEqual(result["token_count"], 2)
        self.assertFalse(result["string_similarity_used"])

    def test_missing_logprobs_are_unavailable(self):
        body = json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": "응답"}}]},
            ensure_ascii=False,
        ).encode("utf-8")
        with mock.patch.object(F, "_open_local_only", return_value=_FakeResponse(body)):
            result = capture_response_logprobs(
                base_url="http://127.0.0.1:11234/v1",
                model="flash-next",
                messages=[{"role": "user", "content": "안녕"}],
            )
        self.assertEqual(result["status"], DPO_EVAL_UNAVAILABLE)
        self.assertEqual(result["reason"], "logprobs_absent")
        self.assertIsNone(result["logprobs"])
        self.assertIsNone(result["sum"])

    def test_non_loopback_gateway_is_refused(self):
        result = capture_response_logprobs(
            base_url="http://example.com/v1",
            model="flash-next",
            messages=[{"role": "user", "content": "안녕"}],
        )
        self.assertEqual(result["status"], DPO_EVAL_UNAVAILABLE)
        self.assertEqual(result["reason"], "non_loopback_gateway")

    def test_oversized_body_is_refused(self):
        payload = b"x" * (CAPTURE_MAX_RESPONSE_BYTES + 8)
        with mock.patch.object(F, "_open_local_only", return_value=_FakeResponse(payload)):
            result = capture_response_logprobs(
                base_url="http://127.0.0.1:11234/v1",
                model="flash-next",
                messages=[{"role": "user", "content": "안녕"}],
            )
        self.assertEqual(result["reason"], "response_too_large")

    def test_malformed_json_is_refused(self):
        with mock.patch.object(
            F, "_open_local_only", return_value=_FakeResponse(b"{not json")
        ):
            result = capture_response_logprobs(
                base_url="http://127.0.0.1:11234/v1",
                model="flash-next",
                messages=[{"role": "user", "content": "안녕"}],
            )
        self.assertEqual(result["reason"], "malformed_response")

    def test_http_error_is_reported(self):
        error = urllib.error.HTTPError("http://127.0.0.1:11234/v1", 503, "busy", None, None)
        with mock.patch.object(F, "_open_local_only", side_effect=error):
            result = capture_response_logprobs(
                base_url="http://127.0.0.1:11234/v1",
                model="flash-next",
                messages=[{"role": "user", "content": "안녕"}],
            )
        self.assertEqual(result["status"], DPO_EVAL_UNAVAILABLE)
        self.assertEqual(result["reason"], "http_503")

    def test_invalid_messages_and_token_budget_are_refused(self):
        bad_role = capture_response_logprobs(
            base_url="http://127.0.0.1:11234/v1",
            model="flash-next",
            messages=[{"role": "tool", "content": "안녕"}],
        )
        self.assertEqual(bad_role["reason"], "invalid_messages")
        empty_prompt = capture_response_logprobs(
            base_url="http://127.0.0.1:11234/v1",
            model="flash-next",
            messages=[{"role": "user", "content": "   "}],
        )
        self.assertEqual(empty_prompt["reason"], "invalid_messages")
        budget = capture_response_logprobs(
            base_url="http://127.0.0.1:11234/v1",
            model="flash-next",
            messages=[{"role": "user", "content": "안녕"}],
            max_tokens=0,
        )
        self.assertEqual(budget["reason"], "invalid_max_tokens")
        temperature = capture_response_logprobs(
            base_url="http://127.0.0.1:11234/v1",
            model="flash-next",
            messages=[{"role": "user", "content": "안녕"}],
            temperature=9.0,
        )
        self.assertEqual(temperature["reason"], "invalid_temperature")


class DpoReferenceContractTests(unittest.TestCase):
    def test_require_reference_without_reference_is_unavailable(self):
        report = dpo_loss_from_logprobs(
            chosen_logprobs=[-0.1, -0.1],
            rejected_logprobs=[-1.0, -1.0],
            require_reference=True,
        )
        self.assertEqual(report["status"], DPO_EVAL_UNAVAILABLE)
        self.assertEqual(report["reason"], "missing_reference_logprobs")
        self.assertIsNone(report["loss"])

    def test_reference_free_result_is_labelled_not_called_dpo(self):
        report = dpo_loss_from_logprobs(
            chosen_logprobs=[-0.1, -0.1],
            rejected_logprobs=[-1.0, -1.0],
        )
        self.assertEqual(report["status"], "ok")
        self.assertTrue(report["reference_free"])
        self.assertEqual(report["reference_model"], "none")
        self.assertEqual(report["objective"], "reference_free_preference")

    def test_reference_given_yields_standard_dpo_loss(self):
        report = dpo_loss_from_logprobs(
            chosen_logprobs=[-0.1, -0.1],
            rejected_logprobs=[-1.0, -1.0],
            ref_chosen_logprobs=[-0.2, -0.2],
            ref_rejected_logprobs=[-0.5, -0.5],
            beta=0.1,
            require_reference=True,
        )
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["objective"], "dpo")
        self.assertFalse(report["reference_free"])
        self.assertEqual(report["reference_model"], "given")
        self.assertAlmostEqual(report["delta"], 1.2)
        self.assertAlmostEqual(report["loss"], math.log1p(math.exp(-0.12)))

    def test_text_only_pairs_never_yield_a_number(self):
        result = evaluate_preference_pairs(
            [
                {
                    "pair_id": "p1",
                    "preferred": "좋은 답",
                    "dispreferred": "나쁜 답",
                    "source": "human_authored",
                }
            ],
            tokenizer_id="tok",
            base_model="flash-next",
            require_reference=True,
        )
        self.assertEqual(result["status"], DPO_EVAL_UNAVAILABLE)
        self.assertIsNone(result["mean_loss"])
        self.assertFalse(result["string_similarity_used"])
        for report in result["pairs"]:
            self.assertIsNone(report["loss"])

    def test_evaluate_marks_reference_free(self):
        result = evaluate_preference_pairs(
            [{"pair_id": "p1", "chosen_logprobs": [-0.1], "rejected_logprobs": [-1.0]}],
            tokenizer_id="tok",
            base_model="flash-next",
        )
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["reference_free"])
        self.assertEqual(result["pairs"][0]["objective"], "reference_free_preference")


class DpoNumericStabilityTests(unittest.TestCase):
    """A crafted preference-pair file must never yield a status ok non-finite loss."""

    def _report(self, **kwargs):
        return dpo_loss_from_logprobs(
            tokenizer_id="tok", base_model="flash-next", **kwargs
        )

    def test_non_finite_beta_is_unavailable_not_a_nan_loss(self):
        for beta in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(beta=beta):
                report = self._report(
                    chosen_logprobs=[-1.0], rejected_logprobs=[-2.0], beta=beta
                )
                self.assertEqual(report["status"], DPO_EVAL_UNAVAILABLE)
                self.assertEqual(report["reason"], "invalid_beta")
                self.assertIsNone(report["loss"])

    def test_non_numeric_beta_is_a_report_not_an_exception(self):
        for beta in ("abc", None, {}, []):
            with self.subTest(beta=beta):
                report = self._report(
                    chosen_logprobs=[-1.0], rejected_logprobs=[-2.0], beta=beta
                )
                self.assertEqual(report["status"], DPO_EVAL_UNAVAILABLE)
                self.assertEqual(report["reason"], "invalid_beta")
                self.assertIsNone(report["loss"])

    def test_an_overflowing_logprob_gap_is_unavailable_not_infinity(self):
        report = self._report(chosen_logprobs=[-1e308], rejected_logprobs=[1e308])
        self.assertEqual(report["status"], DPO_EVAL_UNAVAILABLE)
        self.assertEqual(report["reason"], "non_finite_delta")
        self.assertIsNone(report["loss"])

    def test_a_zero_beta_still_gives_the_ln_two_boundary(self):
        report = self._report(
            chosen_logprobs=[-1.0], rejected_logprobs=[-2.0], beta=0.0
        )
        self.assertEqual(report["status"], "ok")
        self.assertAlmostEqual(report["loss"], math.log(2.0))

    def test_ordinary_inputs_keep_the_stable_softplus_value(self):
        report = self._report(
            chosen_logprobs=[-0.1, -0.1], rejected_logprobs=[-1.0, -1.0]
        )
        self.assertEqual(report["status"], "ok")
        self.assertAlmostEqual(report["delta"], 1.8)
        self.assertAlmostEqual(report["loss"], math.log1p(math.exp(-0.18)))

    def test_one_non_finite_pair_cannot_poison_the_mean_loss(self):
        result = evaluate_preference_pairs(
            [
                {
                    "pair_id": "usable",
                    "preferred": "가",
                    "dispreferred": "나",
                    "chosen_logprobs": [-0.1, -0.1],
                    "rejected_logprobs": [-1.0, -1.0],
                },
                {
                    "pair_id": "overflow",
                    "preferred": "다",
                    "dispreferred": "라",
                    "chosen_logprobs": [1e308],
                    "rejected_logprobs": [-1e308],
                },
            ],
            tokenizer_id="tok",
            base_model="flash-next",
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["evaluated"], 1)
        self.assertEqual(result["unavailable"], 1)
        self.assertTrue(math.isfinite(result["mean_loss"]))
        self.assertAlmostEqual(result["mean_loss"], math.log1p(math.exp(-0.18)))
        self.assertFalse(result["string_similarity_used"])
        self.assertEqual(
            [report["reason"] for report in result["pairs"]],
            ["dpo_logprob", "non_finite_delta"],
        )

class DpoPairCaptureTests(unittest.TestCase):
    def test_single_usable_sample_is_insufficient(self):
        unavailable = {"status": DPO_EVAL_UNAVAILABLE, "reason": "gateway_TimeoutError"}
        with mock.patch.object(F, "capture_response_logprobs", return_value=unavailable):
            result = capture_preference_pair_logprobs(
                [{"pair_id": "p1", "prompt": "질문"}],
                base_url="http://127.0.0.1:11234/v1",
                model="flash-next",
                samples=2,
            )
        self.assertEqual(result["status"], DPO_EVAL_UNAVAILABLE)
        self.assertEqual(result["captured"], 0)
        self.assertEqual(result["pairs"][0]["capture_reason"], "gateway_TimeoutError")
        self.assertFalse(result["string_similarity_used"])

    def test_sample_text_match_attributes_the_pair(self):
        samples = [_capture_ok("A", [-0.1]), _capture_ok("B", [-2.0])]
        with mock.patch.object(F, "capture_response_logprobs", side_effect=samples):
            result = capture_preference_pair_logprobs(
                [
                    {
                        "pair_id": "p1",
                        "prompt": "질문",
                        "preferred": "A",
                        "dispreferred": "B",
                        "source": "unit",
                    }
                ],
                base_url="http://127.0.0.1:11234/v1",
                model="flash-next",
                samples=2,
                require_reference=False,
            )
        self.assertEqual(result["captured"], 1)
        self.assertEqual(result["pairs"][0]["attributed_by"], "sample_text_match")
        self.assertEqual(result["evaluation"]["status"], "ok")
        self.assertIsInstance(result["evaluation"]["mean_loss"], float)

    def test_unmatched_labels_are_not_scored(self):
        samples = [_capture_ok("A", [-0.1]), _capture_ok("B", [-2.0])]
        with mock.patch.object(F, "capture_response_logprobs", side_effect=samples):
            result = capture_preference_pair_logprobs(
                [{"pair_id": "p1", "prompt": "질문", "preferred": "X", "dispreferred": "Y"}],
                base_url="http://127.0.0.1:11234/v1",
                model="flash-next",
                samples=2,
            )
        self.assertEqual(result["captured"], 0)
        self.assertEqual(result["pairs"][0]["capture_reason"], "unattributed_samples")

    def test_stored_logprobs_skip_attribution_by_text(self):
        samples = [_capture_ok("A", [-0.1]), _capture_ok("B", [-2.0])]
        with mock.patch.object(F, "capture_response_logprobs", side_effect=samples):
            result = capture_preference_pair_logprobs(
                [
                    {
                        "pair_id": "p1",
                        "prompt": "질문",
                        "chosen_logprobs": [-0.3],
                        "rejected_logprobs": [-1.5],
                    }
                ],
                base_url="http://127.0.0.1:11234/v1",
                model="flash-next",
                samples=2,
                require_reference=False,
            )
        self.assertEqual(result["captured"], 1)
        self.assertEqual(result["pairs"][0]["attributed_by"], "stored_logprobs")
        self.assertEqual(result["pairs"][0]["chosen_logprobs"], [-0.3])

    def test_reference_pairs_switch_to_standard_objective(self):
        samples = [_capture_ok("A", [-0.1]), _capture_ok("B", [-2.0])]
        with mock.patch.object(F, "capture_response_logprobs", side_effect=samples):
            result = capture_preference_pair_logprobs(
                [{"pair_id": "p1", "prompt": "질문", "preferred": "A", "dispreferred": "B"}],
                base_url="http://127.0.0.1:11234/v1",
                model="flash-next",
                samples=2,
                reference={"p1": {"ref_chosen_logprobs": [-0.2], "ref_rejected_logprobs": [-1.0]}},
                require_reference=True,
            )
        self.assertEqual(result["evaluation"]["status"], "ok")
        self.assertEqual(result["evaluation"]["pairs"][0]["objective"], "dpo")
        self.assertFalse(result["evaluation"]["reference_free"])

    def test_missing_reference_keeps_standard_dpo_unavailable(self):
        samples = [_capture_ok("A", [-0.1]), _capture_ok("B", [-2.0])]
        with mock.patch.object(F, "capture_response_logprobs", side_effect=samples):
            result = capture_preference_pair_logprobs(
                [{"pair_id": "p1", "prompt": "질문", "preferred": "A", "dispreferred": "B"}],
                base_url="http://127.0.0.1:11234/v1",
                model="flash-next",
                samples=2,
            )
        self.assertEqual(result["captured"], 1)
        self.assertEqual(result["evaluation"]["status"], DPO_EVAL_UNAVAILABLE)
        self.assertEqual(
            result["evaluation"]["pairs"][0]["reason"], "missing_reference_logprobs"
        )
        self.assertIsNone(result["evaluation"]["mean_loss"])

    def test_sample_prompt_candidates_records_capture_failures(self):
        unavailable = {"status": DPO_EVAL_UNAVAILABLE, "reason": "logprobs_absent"}
        with mock.patch.object(F, "capture_response_logprobs", return_value=unavailable):
            result = sample_prompt_candidates(
                "질문",
                base_url="http://127.0.0.1:11234/v1",
                model="flash-next",
                samples=2,
            )
        self.assertEqual(result["status"], DPO_EVAL_UNAVAILABLE)
        self.assertEqual(result["candidate_count"], 0)
        self.assertEqual(result["reason"], "logprobs_absent")
        self.assertEqual(result["candidates"], [])

    def test_sample_prompt_candidates_returns_real_logprobs(self):
        samples = [_capture_ok("A", [-0.1, -0.2]), _capture_ok("B", [-1.0])]
        with mock.patch.object(F, "capture_response_logprobs", side_effect=samples):
            result = sample_prompt_candidates(
                "질문",
                base_url="http://127.0.0.1:11234/v1",
                model="flash-next",
                samples=2,
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["candidate_count"], 2)
        self.assertAlmostEqual(result["candidates"][0]["sum"], -0.3)


class DpoScoringProbeTests(_IsolatedMlxLeaseTestCase):
    def test_echo_unsupported_is_measured(self):
        body = json.dumps({"choices": [{"text": " epsilon"}]}).encode("utf-8")
        with mock.patch.object(F, "_open_local_only", return_value=_FakeResponse(body)):
            result = probe_response_scoring(
                base_url="http://127.0.0.1:11234/v1", model="flash-next"
            )
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["supports_response_scoring"])
        self.assertEqual(result["reason"], "echo_unsupported")
        self.assertTrue(result["probe_executed"])

    def test_echo_supported_is_measured(self):
        body = json.dumps(
            {"choices": [{"text": "OPENKAKAO_ECHO_PROBE alpha beta gamma delta"}]}
        ).encode("utf-8")
        with mock.patch.object(F, "_open_local_only", return_value=_FakeResponse(body)):
            result = probe_response_scoring(
                base_url="http://127.0.0.1:11234/v1", model="flash-next"
            )
        self.assertTrue(result["supports_response_scoring"])
        self.assertEqual(result["reason"], "echo_supported")

    def test_probe_refuses_non_loopback(self):
        result = probe_response_scoring(base_url="https://example.com/v1", model="flash-next")
        self.assertEqual(result["status"], DPO_EVAL_UNAVAILABLE)
        self.assertEqual(result["reason"], "non_loopback_gateway")
        self.assertFalse(result["supports_response_scoring"])


class PreferencePairFileTests(unittest.TestCase):
    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            pairs, error = load_preference_pairs(Path(tmp) / "absent.jsonl")
        self.assertEqual(pairs, [])
        self.assertEqual(error, "pairs_missing")

    def test_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.jsonl"
            path.write_text("", encoding="utf-8")
            pairs, error = load_preference_pairs(path)
        self.assertEqual(pairs, [])
        self.assertEqual(error, "pairs_empty")

    def test_malformed_lines_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pairs.jsonl"
            path.write_text(
                NL.join(["{\"pair_id\": \"p1\"}", "not json", "", "[1, 2]"]) + NL,
                encoding="utf-8",
            )
            pairs, error = load_preference_pairs(path)
        self.assertEqual(error, "")
        self.assertEqual([pair["pair_id"] for pair in pairs], ["p1"])

    def test_oversized_file_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "big.jsonl"
            path.write_text("{\"pair_id\": \"p1\"}" + NL, encoding="utf-8")
            pairs, error = load_preference_pairs(path, max_bytes=4)
        self.assertEqual(pairs, [])
        self.assertEqual(error, "pairs_too_large")


class DpoCliTests(unittest.TestCase):
    def test_dpo_flags_default_off(self):
        args = build_parser().parse_args([])
        self.assertIsNone(args.dpo_pairs)
        self.assertIsNone(args.dpo_ref_pairs)
        self.assertFalse(args.dpo_capture)
        self.assertEqual(args.dpo_base_url, "http://127.0.0.1:11234/v1")

    def test_report_has_no_dpo_key_without_the_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = main(["--state-root", tmp, "--prepare-only", "--json"])
            report = json.loads(buffer.getvalue())
        self.assertEqual(code, 0)
        self.assertNotIn("dpo", report)

    def test_dpo_key_fails_closed_without_a_local_gateway(self):
        with tempfile.TemporaryDirectory() as tmp:
            pairs_path = Path(tmp) / "pairs.jsonl"
            pairs_path.write_text(
                json.dumps({"pair_id": "p1", "prompt": "질문"}, ensure_ascii=False) + NL,
                encoding="utf-8",
            )
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = main(
                    [
                        "--state-root",
                        tmp,
                        "--prepare-only",
                        "--json",
                        "--dpo-pairs",
                        str(pairs_path),
                        "--dpo-base-url",
                        "http://example.com/v1",
                    ]
                )
            report = json.loads(buffer.getvalue())
        self.assertEqual(code, 0)
        self.assertIn("dpo", report)
        self.assertEqual(report["dpo"]["status"], DPO_EVAL_UNAVAILABLE)
        self.assertFalse(report["dpo"]["string_similarity_used"])
        self.assertFalse(report["dpo"]["scoring_probe"]["supports_response_scoring"])


if __name__ == "__main__":
    unittest.main()
