"""Focused alphaXiv/DREAM-RSI/DPO provenance regression tests."""

from __future__ import annotations

import json
import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from scripts.auto_reply_dream_rsi import load_replay_rows
from scripts.dream_rsi_alphaxiv import (
    DPO_EVAL_UNAVAILABLE,
    DREAM_RSI_PAPER_ID,
    MAX_CLI_JSON_BYTES,
    build_dpo_provenance,
    build_exploration_provenance,
    build_provenance_report,
    build_runtime_model_probe_provenance,
    build_parser,
    collect_orx_paper_report,
    collect_paper_report,
    main,
)


def _completed(stdout: object = b"{}", *, returncode: int = 0, stderr: object = b""):
    return subprocess.CompletedProcess(
        args=["alphaxiv"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class AlphaXivFailClosedTests(unittest.TestCase):
    @mock.patch("scripts.dream_rsi_alphaxiv.shutil.which", return_value=None)
    def test_missing_cli_fails_closed(self, _which):
        report = collect_paper_report("DREAM-RSI", alphaxiv_cli="alphaxiv")
        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(report["reason"], "cli_missing")
        self.assertFalse(report["analysis_available"])
        self.assertIsNone(report["evidence"])

    @mock.patch("scripts.dream_rsi_alphaxiv.subprocess.run")
    @mock.patch("scripts.dream_rsi_alphaxiv.shutil.which", return_value="/mock/alphaxiv")
    def test_timeout_fails_closed(self, _which, run):
        run.side_effect = subprocess.TimeoutExpired(["alphaxiv"], 1)
        report = collect_paper_report("DREAM-RSI", timeout=1)
        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(report["reason"], "cli_timeout")
        self.assertIsNone(report["evidence"])

    @mock.patch("scripts.dream_rsi_alphaxiv.subprocess.run")
    @mock.patch("scripts.dream_rsi_alphaxiv.shutil.which", return_value="/mock/alphaxiv")
    def test_nonzero_fails_closed(self, _which, run):
        run.return_value = _completed(returncode=7, stderr=b"network failed")
        report = collect_paper_report("DREAM-RSI")
        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(report["reason"], "cli_nonzero")
        self.assertIsNone(report["evidence"])

    @mock.patch("scripts.dream_rsi_alphaxiv.subprocess.run")
    @mock.patch("scripts.dream_rsi_alphaxiv.shutil.which", return_value="/mock/alphaxiv")
    def test_invalid_json_fails_closed(self, _which, run):
        run.return_value = _completed(stdout=b"not-json")
        report = collect_paper_report("DREAM-RSI")
        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(report["reason"], "invalid_json")
        self.assertIsNone(report["evidence"])

    @mock.patch("scripts.dream_rsi_alphaxiv.subprocess.run")
    def test_relative_cli_path_traversal_is_rejected_before_exec(self, run):
        report = collect_paper_report("DREAM-RSI", alphaxiv_cli="../alphaxiv")
        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(report["reason"], "invalid_cli_path")
        run.assert_not_called()


class AlphaXivProvenanceTests(unittest.TestCase):
    def test_cli_defaults_to_exact_dream_rsi_paper_id(self):
        args = build_parser().parse_args([])
        self.assertEqual(args.paper_id, DREAM_RSI_PAPER_ID)

    @mock.patch("scripts.dream_rsi_alphaxiv.subprocess.run")
    @mock.patch("scripts.dream_rsi_alphaxiv.shutil.which", return_value="/mock/alphaxiv")
    def test_cli_default_path_uses_exact_paper_without_search(self, _which, run):
        run.side_effect = [
            _completed(b"context selected"),
            _completed(
                json.dumps(
                    {"paper_id": DREAM_RSI_PAPER_ID, "title": "DREAM-RSI Research"}
                ).encode()
            ),
            _completed(json.dumps({"summary": "alphaXiv verified summary"}).encode()),
        ]

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = main([])

        self.assertEqual(exit_code, 0)
        report = json.loads(stdout.getvalue())
        self.assertEqual(report["paper_analysis"]["selection_method"], "direct_paper_id")
        self.assertEqual(report["paper_analysis"]["selected_paper"]["paper_id"], DREAM_RSI_PAPER_ID)
        self.assertEqual(
            [item["operation"] for item in report["paper_analysis"]["cli"]["commands"]],
            ["context_use_paper", "context_show", "paper_summary"],
        )
        self.assertEqual(
            run.call_args_list[0].args[0][1:],
            ["context", "use", "paper", DREAM_RSI_PAPER_ID],
        )

    @mock.patch("scripts.dream_rsi_alphaxiv.subprocess.run")
    @mock.patch("scripts.dream_rsi_alphaxiv.shutil.which", return_value="/mock/alphaxiv")
    def test_exact_paper_identity_mismatch_fails_closed(self, _which, run):
        run.side_effect = [
            _completed(b"context selected"),
            _completed(json.dumps({"paper_id": "2609.00001", "title": "Other"}).encode()),
        ]

        report = collect_paper_report("DREAM-RSI", paper_id=DREAM_RSI_PAPER_ID)

        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(report["reason"], "paper_identity_mismatch")
        self.assertFalse(report["analysis_available"])
        self.assertIsNone(report["evidence"])
        self.assertTrue(report["cli"]["isolated_context"])

    def test_alphaxiv_extreme_timeout_is_invalid_without_raise(self):
        report = collect_paper_report(timeout=10**10000)
        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(report["reason"], "invalid_timeout")
        self.assertTrue(report["cli"]["isolated_context"])

    @mock.patch("scripts.dream_rsi_alphaxiv.subprocess.run")
    @mock.patch("scripts.dream_rsi_alphaxiv.shutil.which", return_value="/mock/alphaxiv")
    def test_verified_paper_report_keeps_alphaXiv_provenance(self, _which, run):
        run.side_effect = [
            _completed(
                json.dumps(
                    {
                        "papers": [
                            {
                                "paper_id": "2601.12345",
                                "title": "DREAM-RSI Research",
                                "url": "https://arxiv.org/abs/2601.12345",
                            }
                        ]
                    }
                ).encode()
            ),
            _completed(b"context selected"),
            _completed(
                json.dumps(
                    {"paper_id": "2601.12345", "title": "DREAM-RSI Research"}
                ).encode()
            ),
            _completed(json.dumps({"summary": "alphaXiv verified summary"}).encode()),
        ]

        report = collect_paper_report("DREAM-RSI; literal search text")

        self.assertEqual(report["status"], "ok")
        self.assertTrue(report["analysis_available"])
        self.assertEqual(report["provider"], "alphaxiv_cli")
        self.assertEqual(report["selection_method"], "alphaxiv_search_rank_1")
        self.assertEqual(report["selected_paper"]["paper_id"], "2601.12345")
        self.assertEqual(report["evidence"]["summary"], "alphaXiv verified summary")
        self.assertFalse(report["string_similarity_used"])
        self.assertEqual(
            [item["operation"] for item in report["cli"]["commands"]],
            ["search_papers", "context_use_paper", "context_show", "paper_summary"],
        )
        first_call = run.call_args_list[0]
        argv = first_call.args[0]
        self.assertEqual(argv[1:3], ["search", "papers"])
        self.assertIn("DREAM-RSI; literal search text", argv)
        self.assertFalse(first_call.kwargs["shell"])
        self.assertEqual(first_call.kwargs["stdin"], subprocess.DEVNULL)
        self.assertIn("ALPHAXIV_HOME", first_call.kwargs["env"])

    @mock.patch("scripts.dream_rsi_alphaxiv.subprocess.run")
    @mock.patch("scripts.dream_rsi_alphaxiv.shutil.which", return_value="/mock/alphaxiv")
    def test_incomplete_summary_does_not_become_paper_evidence(self, _which, run):
        run.side_effect = [
            _completed(json.dumps({"papers": [{"paper_id": "2601.12345", "title": "DREAM"}]}).encode()),
            _completed(b"ok"),
            _completed(json.dumps({"paper_id": "2601.12345", "title": "DREAM"}).encode()),
            _completed(json.dumps({"summary": ""}).encode()),
        ]
        report = collect_paper_report("DREAM-RSI")
        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(report["reason"], "incomplete_summary_response")
        self.assertIsNone(report["evidence"])


class OpenResearchProviderTests(unittest.TestCase):
    @staticmethod
    def _report(paper_id: str = DREAM_RSI_PAPER_ID, body: str = "# Report\nverified") -> bytes:
        return f"alphaXiv: https://www.alphaxiv.org/abs/{paper_id}\n{body}\n".encode()

    @mock.patch("scripts.dream_rsi_alphaxiv.subprocess.run")
    @mock.patch("scripts.dream_rsi_alphaxiv.shutil.which", return_value="/mock/orx")
    def test_orx_success_requires_matching_identity_and_exact_argv(self, _which, run):
        run.return_value = _completed(self._report(), stderr=b"e" * 3000)

        report = collect_orx_paper_report("DREAM-RSI")

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["provider"], "orx_cli")
        self.assertEqual(report["evidence"]["kind"], "orx_alphaxiv_paper_report")
        self.assertEqual(report["evidence"]["source_operation"], "orx_paper")
        self.assertFalse(report["string_similarity_used"])
        self.assertEqual(report["cli"]["commands"][0]["stderr"], "<redacted>")
        call = run.call_args
        self.assertEqual(
            call.args[0],
            [
                "/mock/orx",
                "paper",
                DREAM_RSI_PAPER_ID,
                "--source",
                "alphaxiv",
                "--no-telemetry",
            ],
        )
        self.assertFalse(call.kwargs["shell"])
        self.assertEqual(call.kwargs["stdin"], subprocess.DEVNULL)

    def test_orx_extreme_timeout_is_invalid_without_raise(self):
        report = collect_orx_paper_report(timeout=10**10000)
        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(report["reason"], "invalid_timeout")
        self.assertFalse(report["cli"]["isolated_context"])

    @mock.patch("scripts.dream_rsi_alphaxiv.subprocess.run")
    @mock.patch("scripts.dream_rsi_alphaxiv.shutil.which", return_value="/mock/orx")
    def test_stderr_secret_is_redacted_and_bounded(self, _which, run):
        run.return_value = _completed(
            returncode=4,
            stderr=b"token=" + b"A" * 64 + b" " + (b"word " * 80),
        )
        report = collect_orx_paper_report("DREAM-RSI")
        stderr = report["cli"]["commands"][0]["stderr"]
        self.assertIn("<redacted>", stderr)
        self.assertNotIn("A" * 64, stderr)
        self.assertEqual(len(stderr), 200)

    @mock.patch("scripts.dream_rsi_alphaxiv.subprocess.run")
    @mock.patch("scripts.dream_rsi_alphaxiv.shutil.which", return_value="/mock/orx")
    def test_orx_identity_mismatch_fails_closed(self, _which, run):
        run.return_value = _completed(self._report("2609.99999"))
        report = collect_orx_paper_report("DREAM-RSI")
        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(report["reason"], "identity_mismatch")
        self.assertEqual(report["provider"], "orx_cli")
        self.assertFalse(report["cli"]["isolated_context"])

    @mock.patch("scripts.dream_rsi_alphaxiv.shutil.which", return_value=None)
    def test_orx_missing_cli_fails_closed(self, _which):
        report = collect_orx_paper_report("DREAM-RSI")
        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(report["reason"], "cli_missing")
        self.assertEqual(report["provider"], "orx_cli")

    @mock.patch("scripts.dream_rsi_alphaxiv.subprocess.run")
    @mock.patch("scripts.dream_rsi_alphaxiv.shutil.which", return_value="/mock/orx")
    def test_orx_nonzero_fails_closed(self, _which, run):
        run.return_value = _completed(returncode=4, stderr=b"failed")
        report = collect_orx_paper_report("DREAM-RSI")
        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(report["reason"], "cli_nonzero")
        self.assertFalse(report["cli"]["isolated_context"])

    @mock.patch("scripts.dream_rsi_alphaxiv.subprocess.run")
    @mock.patch("scripts.dream_rsi_alphaxiv.shutil.which", return_value="/mock/orx")
    def test_orx_timeout_fails_closed(self, _which, run):
        run.side_effect = subprocess.TimeoutExpired(["orx"], 1)
        report = collect_orx_paper_report("DREAM-RSI", timeout=1)
        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(report["reason"], "cli_timeout")

    @mock.patch("scripts.dream_rsi_alphaxiv.subprocess.run")
    @mock.patch("scripts.dream_rsi_alphaxiv.shutil.which", return_value="/mock/orx")
    def test_orx_oversized_stdout_fails_closed(self, _which, run):
        run.return_value = _completed(b"x" * (MAX_CLI_JSON_BYTES + 1))
        report = collect_orx_paper_report("DREAM-RSI")
        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(report["reason"], "output_too_large")

    @mock.patch("scripts.dream_rsi_alphaxiv.subprocess.run")
    @mock.patch("scripts.dream_rsi_alphaxiv.shutil.which", return_value="/mock/orx")
    def test_orx_empty_body_fails_closed(self, _which, run):
        run.return_value = _completed(
            f"alphaXiv: https://www.alphaxiv.org/abs/{DREAM_RSI_PAPER_ID}\n\n".encode()
        )
        report = collect_orx_paper_report("DREAM-RSI")
        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(report["reason"], "incomplete_summary_response")

    @mock.patch("scripts.dream_rsi_alphaxiv.subprocess.run")
    def test_orx_invalid_paper_id_fails_before_subprocess(self, run):
        report = collect_orx_paper_report("DREAM-RSI", paper_id="../2609.14858")
        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(report["reason"], "invalid_paper_id")
        run.assert_not_called()

    @mock.patch("scripts.dream_rsi_alphaxiv.subprocess.run")
    @mock.patch("scripts.dream_rsi_alphaxiv.shutil.which")
    def test_auto_prefers_alphaxiv_when_both_resolve(self, which, run):
        which.side_effect = lambda name: f"/mock/{name}" if name in {"alphaxiv", "orx"} else None
        run.side_effect = [
            _completed(b"context selected"),
            _completed(
                json.dumps(
                    {"paper_id": DREAM_RSI_PAPER_ID, "title": "DREAM-RSI Research"}
                ).encode()
            ),
            _completed(json.dumps({"summary": "alphaXiv verified summary"}).encode()),
        ]

        report = collect_paper_report(
            "DREAM-RSI",
            paper_source="auto",
            paper_id=DREAM_RSI_PAPER_ID,
        )

        self.assertEqual(report["provider"], "alphaxiv_cli")
        self.assertTrue(all(call.args[0][0] != "/mock/orx" for call in run.call_args_list))

    @mock.patch("scripts.dream_rsi_alphaxiv.subprocess.run")
    @mock.patch("scripts.dream_rsi_alphaxiv.shutil.which")
    def test_auto_falls_back_to_orx_when_alphaxiv_is_absent(self, which, run):
        which.side_effect = lambda name: "/mock/orx" if name == "orx" else None
        run.return_value = _completed(self._report())

        report = collect_paper_report(
            "DREAM-RSI",
            paper_source="auto",
            paper_id=DREAM_RSI_PAPER_ID,
        )

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["provider"], "orx_cli")
        self.assertEqual(run.call_args.args[0][0], "/mock/orx")

    @mock.patch("scripts.dream_rsi_alphaxiv.subprocess.run")
    @mock.patch("scripts.dream_rsi_alphaxiv.shutil.which", return_value="/mock/orx")
    def test_main_forced_orx_returns_ok(self, _which, run):
        run.return_value = _completed(self._report())
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = main(["--paper-source", "orx"])
        report = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["paper_analysis"]["provider"], "orx_cli")


class ExplorationAndDpoTests(unittest.TestCase):
    def test_fixed_budget_records_branch_and_never_promotes(self):
        report = build_exploration_provenance(
            {
                "budget": 3,
                "candidates": [
                    {"name": "a", "evaluation": {"status": "ok", "factuality": False}},
                    {"name": "b", "evaluation": {"status": "ok", "factuality": False}},
                    {"name": "c", "evaluation": {"status": "ok"}},
                ],
            }
        )
        evaluated = [event for event in report["events"] if event["event"] == "candidate_evaluated"]
        self.assertEqual(report["fixed_budget"], {"initial": 3, "consumed": 3, "remaining": 0})
        self.assertEqual(evaluated[2]["selection"]["action"], "branch")
        self.assertEqual(report["stop"]["reason"], "budget_exhausted")
        self.assertEqual(report["promotion"], {"allowed": False, "observed": False})

    def test_dpo_without_response_logprobs_is_eval_unavailable(self):
        report = build_dpo_provenance(
            {
                "tokenizer_id": "tok",
                "base_model": "base",
                "pairs": [
                    {
                        "pair_id": "p1",
                        "source": "human_authored",
                        "preferred": "좋은 답",
                        "dispreferred": "나쁜 답",
                    }
                ],
            }
        )
        self.assertEqual(report["status"], DPO_EVAL_UNAVAILABLE)
        self.assertEqual(report["reason"], "missing_logprobs")
        self.assertEqual(report["evaluated"], 0)
        self.assertEqual(report["unavailable"], 1)
        self.assertIsNone(report["mean_loss"])
        self.assertFalse(report["string_similarity_used"])

    def test_sections_are_separate_and_model_change_is_forbidden(self):
        paper = {
            "status": "ok",
            "analysis_available": True,
            "evidence": {"summary": "verified"},
        }
        report = build_provenance_report(
            paper_analysis=paper,
            exploration={"budget": 0, "candidates": []},
            dpo={"pairs": [], "tokenizer_id": "", "base_model": ""},
        )
        self.assertIn("dream_rsi_exploration", report)
        self.assertIn("dpo_preference_evaluation", report)
        self.assertIsNot(report["dream_rsi_exploration"], report["dpo_preference_evaluation"])
        self.assertEqual(
            report["model_policy"],
            {"automatic_promotion": False, "automatic_replacement": False},
        )

    def test_current_runtime_probe_is_distinct_from_stale_success(self):
        probe = build_runtime_model_probe_provenance(
            {
                "observed_date": "2026-09-21",
                "gateway": "http://127.0.0.1:11234/v1",
                "model": "mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit",
                "result": "OK",
                "latency_ms": 8211,
                "timeout_ms": 90000,
                "advertised_models": [
                    {
                        "model": "mlx/ddalcu/Qwen3.8-27B-MLX-Serve-4bit",
                        "loaded": False,
                        "state": "unloaded",
                    }
                ],
            }
        )
        self.assertEqual(probe["status"], "observed")
        self.assertEqual(probe["source"], "caller_supplied_current_observation")
        self.assertEqual(probe["freshness"], "current_observation")
        self.assertEqual(probe["latency_ms"], 8211)
        self.assertEqual(probe["timeout_ms"], 90000)
        self.assertFalse(probe["stale_success_reused"])
        self.assertFalse(probe["probe_executed_by_report"])
        self.assertFalse(probe["model_switch_performed"])
        self.assertEqual(probe["advertised_models"][0]["state"], "unloaded")

    def test_absent_runtime_probe_never_implies_previous_success(self):
        probe = build_runtime_model_probe_provenance(None)
        self.assertEqual(probe["status"], "not_supplied")
        self.assertEqual(probe["freshness"], "unknown")
        self.assertFalse(probe["stale_success_reused"])


class GoldenReplayRegressionTests(unittest.TestCase):
    def test_default_replay_cap_is_400_human_rows_without_model_gold(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            golden = root / "golden" / "reply-golden.jsonl"
            golden.parent.mkdir(parents=True, exist_ok=True)
            rows = [
                {
                    "prompt": f"model-q-{i}",
                    "completion": f"MODEL-GOLD-{i}",
                    "source": "auto_reply_sent",
                }
                for i in range(3)
            ] + [
                {
                    "prompt": f"human-q-{i}",
                    "completion": f"HUMAN-GOLD-{i}",
                    "source": "self_history",
                }
                for i in range(405)
            ]
            golden.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                encoding="utf-8",
            )
            counters: dict[str, int] = {}
            replay = load_replay_rows(root, counters=counters)

        self.assertEqual(len(replay), 400)
        self.assertEqual(counters["gold_rows"], 400)
        self.assertEqual(counters["excluded_model_gold"], 3)
        self.assertTrue(all(not row["gold"].startswith("MODEL-GOLD") for row in replay))


if __name__ == "__main__":
    unittest.main()
