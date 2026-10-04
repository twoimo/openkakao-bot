from __future__ import annotations

import contextlib
import io
import json
import math
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from scripts import alden_dpo_scorer as S
from scripts import auto_reply_finetune as F


PAIR = {
    "pair_id": "p1",
    "prompt": "Q",
    "preferred": "AB",
    "dispreferred": "CD",
}


class FakeTokenizer:
    def __init__(self):
        self.template_calls = []

    def apply_chat_template(self, messages, **kwargs):
        self.template_calls.append(kwargs)
        return f"<U>{messages[0]['content']}<A>"

    def encode(self, text, add_special_tokens=False):
        del add_special_tokens
        return [ord(char) + 1 for char in text]


class FakeBackend:
    active = 0
    max_active = 0
    loads = 0

    def __init__(self, checkpoint=None):
        self.checkpoint = checkpoint
        self.model = object()
        self.tokenizer = FakeTokenizer()
        type(self).loads += 1
        type(self).active += 1
        type(self).max_active = max(type(self).max_active, type(self).active)

    @classmethod
    def reset(cls):
        cls.active = cls.max_active = cls.loads = 0

    def reset_peak_memory(self):
        return None

    def peak_bytes(self):
        return 1234

    def make_cache(self):
        return {"offset": 0}

    def forward_chunk(self, input_ids, target_ids, cache, score_from):
        offset = cache["offset"]
        values = []
        for local in range(score_from, len(input_ids)):
            global_pos = offset + local
            values.append(-(global_pos + 1) - int(target_ids[local]) / 1_000_000.0)
        cache["offset"] += len(input_ids)
        return values

    def close(self):
        if self.model is not None:
            self.model = None
            type(self).active -= 1


def _checkpoint(root: Path, name: str, *, tokenizer="same", weights="w") -> Path:
    path = root / name
    path.mkdir()
    (path / "config.json").write_text('{"model_type":"fake"}', encoding="utf-8")
    (path / "tokenizer.json").write_text(tokenizer, encoding="utf-8")
    (path / "model-00001-of-00001.safetensors").write_bytes(weights.encode())
    return path


def _score_dict(*, prompt=(1, 2), response=(3, 4), logprobs=(-0.2, -0.3)):
    return {
        "pair_id": "p1",
        "prompt_ids": prompt,
        "response_token_ids": response,
        "response_mask": (False, True, True),
        "logprobs": logprobs,
    }


def _pair_score(**overrides):
    chosen = overrides.get("chosen", _score_dict())
    rejected = overrides.get(
        "rejected", _score_dict(response=(5, 6), logprobs=(-0.4, -0.5))
    )
    return {"pair_id": overrides.get("pair_id", "p1"), "chosen": chosen, "rejected": rejected}


class TokenBoundaryTests(unittest.TestCase):
    def setUp(self):
        FakeBackend.reset()

    def test_first_and_last_response_tokens_use_correct_logits(self):
        tokenizer = FakeTokenizer()
        chosen, rejected = S.prepare_pair_tokens(tokenizer, PAIR)
        self.assertEqual(chosen["prompt_ids"], rejected["prompt_ids"])
        self.assertEqual(len(tokenizer.template_calls), 1)
        self.assertFalse(tokenizer.template_calls[0]["enable_thinking"])
        self.assertTrue(tokenizer.template_calls[0]["add_generation_prompt"])

        backend = FakeBackend()
        try:
            score = S.score_fixed_response(backend, chosen, chunk_size=2)
        finally:
            backend.close()
        prompt_len = len(chosen["prompt_ids"])
        first_target = chosen["response_ids"][0]
        last_target = chosen["response_ids"][-1]
        self.assertAlmostEqual(score["logprobs"][0], -prompt_len - first_target / 1_000_000.0)
        self.assertAlmostEqual(
            score["logprobs"][-1],
            -(len(chosen["full_ids"]) - 1) - last_target / 1_000_000.0,
        )

    def test_prompt_boundary_mask_marks_only_response_targets(self):
        chosen, _ = S.prepare_pair_tokens(FakeTokenizer(), PAIR)
        prompt_targets = len(chosen["prompt_ids"]) - 1
        self.assertFalse(any(chosen["response_mask"][:prompt_targets]))
        self.assertTrue(all(chosen["response_mask"][prompt_targets:]))
        self.assertEqual(sum(chosen["response_mask"]), len(chosen["response_ids"]))

    def test_chunking_is_equivalent(self):
        chosen, _ = S.prepare_pair_tokens(FakeTokenizer(), PAIR)
        one, many = FakeBackend(), FakeBackend()
        try:
            score_one = S.score_fixed_response(one, chosen, chunk_size=1)
            score_many = S.score_fixed_response(many, chosen, chunk_size=64)
        finally:
            one.close()
            many.close()
        self.assertEqual(score_one["logprobs"], score_many["logprobs"])

    def test_chunk_size_above_128_fails(self):
        chosen, _ = S.prepare_pair_tokens(FakeTokenizer(), PAIR)
        backend = FakeBackend()
        try:
            with self.assertRaisesRegex(S.ScorerError, "invalid_chunk_size"):
                S.score_fixed_response(backend, chosen, chunk_size=129)
        finally:
            backend.close()

    def test_response_over_256_tokens_fails(self):
        pair = dict(PAIR, preferred="A" * 257)
        with self.assertRaisesRegex(S.ScorerError, "response_too_long"):
            S.prepare_pair_tokens(FakeTokenizer(), pair)

    def test_total_sequence_over_2048_tokens_fails(self):
        pair = dict(PAIR, prompt="Q" * 2048, preferred="A")
        with self.assertRaisesRegex(S.ScorerError, "sequence_too_long"):
            S.prepare_pair_tokens(FakeTokenizer(), pair)


class ScoreValidationTests(unittest.TestCase):
    def test_nan_fails(self):
        bad = _pair_score(chosen=_score_dict(logprobs=(float("nan"), -0.2)))
        with self.assertRaisesRegex(S.ScorerError, "non_finite_logprob"):
            S.validate_pair_scores(bad, _pair_score())

    def test_positive_logprob_fails(self):
        bad = _pair_score(chosen=_score_dict(logprobs=(0.01, -0.2)))
        with self.assertRaisesRegex(S.ScorerError, "positive_logprob"):
            S.validate_pair_scores(bad, _pair_score())

    def test_invalid_logprob_length_fails(self):
        bad = _pair_score(chosen=_score_dict(logprobs=(-0.2,)))
        with self.assertRaisesRegex(S.ScorerError, "invalid_logprob_length"):
            S.validate_pair_scores(bad, _pair_score())

    def test_reference_token_mismatch_fails(self):
        reference = _pair_score(chosen=_score_dict(response=(3, 9)))
        with self.assertRaisesRegex(S.ScorerError, "reference_chosen_token_mismatch"):
            S.validate_pair_scores(_pair_score(), reference)

    def test_reference_prompt_mismatch_fails(self):
        reference = _pair_score(
            chosen=_score_dict(prompt=(1, 9)),
            rejected=_score_dict(prompt=(1, 9), response=(5, 6), logprobs=(-0.4, -0.5)),
        )
        with self.assertRaisesRegex(S.ScorerError, "reference_prompt_token_mismatch"):
            S.validate_pair_scores(_pair_score(), reference)


class CheckpointFormatTests(unittest.TestCase):
    def weights(self, *, shape=(12, 4, 1), mtp=True):
        weights = {
            "model.layers.0.linear_attn.conv1d.weight": types.SimpleNamespace(shape=shape),
            "model.layers.0.input_layernorm.weight": object(),
        }
        if mtp:
            weights["mtp.layers.0.weight"] = object()
        return weights

    def test_auto_rejects_converted_conv_with_mtp(self):
        with self.assertRaisesRegex(S.ScorerError, "checkpoint_layout_ambiguous"):
            S._qwen35_sanitize_input(self.weights(), S.CHECKPOINT_FORMAT_AUTO)

    def test_auto_keeps_unconverted_norm_contract(self):
        weights = self.weights(shape=(12, 1, 4))
        prepared, selected = S._qwen35_sanitize_input(weights, S.CHECKPOINT_FORMAT_AUTO)
        self.assertIs(prepared, weights)
        self.assertEqual(selected, S.CHECKPOINT_FORMAT_MLX_LM)

    def test_converted_format_removes_mtp_without_mutating_norms(self):
        weights = self.weights()
        prepared, selected = S._qwen35_sanitize_input(weights, S.CHECKPOINT_FORMAT_MLX_SERVE)
        self.assertEqual(selected, S.CHECKPOINT_FORMAT_MLX_SERVE)
        self.assertNotIn("mtp.layers.0.weight", prepared)
        self.assertIn("mtp.layers.0.weight", weights)
        self.assertIs(prepared["model.layers.0.input_layernorm.weight"],
                      weights["model.layers.0.input_layernorm.weight"])

    def test_converted_format_allows_already_removed_mtp(self):
        weights = self.weights(mtp=False)
        prepared, _ = S._qwen35_sanitize_input(weights, S.CHECKPOINT_FORMAT_MLX_SERVE)
        self.assertEqual(prepared, weights)

    def test_converted_format_rejects_unconverted_or_missing_conv(self):
        for weights, reason in (
            (self.weights(shape=(12, 1, 4)), "mlx_serve_conv_layout_unconverted"),
            ({"mtp.layers.0.weight": object()}, "mlx_serve_conv_layout_missing"),
        ):
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(S.ScorerError, reason):
                    S._qwen35_sanitize_input(weights, S.CHECKPOINT_FORMAT_MLX_SERVE)

    def test_malformed_conv_shapes_fail(self):
        for shape in ((12, 4), (12, 0, 1), (12, 4, "bad")):
            with self.subTest(shape=shape):
                with self.assertRaisesRegex(S.ScorerError, "checkpoint_layout_ambiguous"):
                    S._qwen35_sanitize_input(self.weights(shape=shape), S.CHECKPOINT_FORMAT_AUTO)

    def test_mixed_conv_layouts_fail(self):
        weights = self.weights()
        weights["model.layers.1.linear_attn.conv1d.weight"] = types.SimpleNamespace(shape=(12, 1, 4))
        with self.assertRaisesRegex(S.ScorerError, "checkpoint_conv_layout_mixed"):
            S._qwen35_sanitize_input(weights, S.CHECKPOINT_FORMAT_AUTO)

    def test_unknown_format_fails_before_loading(self):
        FakeBackend.reset()
        result = S.score_local_dpo_pairs(
            [PAIR], policy_dir="/tmp/unused", reference_dir="/tmp/unused",
            checkpoint_format="guess", backend_factory=FakeBackend,
        )
        self.assertEqual(result["reason"], "unknown_checkpoint_format")
        self.assertIsNone(result["mean_loss"])
        self.assertEqual(FakeBackend.loads, 0)

    def test_scoped_subclass_leaves_upstream_class_unchanged(self):
        class Upstream:
            def sanitize(self, weights):
                return dict(weights)
        original = Upstream.sanitize
        selected = {}
        scoped = S._scoped_qwen35_model_class(Upstream, S.CHECKPOINT_FORMAT_MLX_SERVE, selected)
        weights = self.weights()
        self.assertNotIn("mtp.layers.0.weight", scoped().sanitize(weights))
        self.assertIn("mtp.layers.0.weight", Upstream().sanitize(weights))
        self.assertIs(Upstream.sanitize, original)
        self.assertEqual(selected["format"], S.CHECKPOINT_FORMAT_MLX_SERVE)

    def test_converted_format_rejects_other_architecture_before_importing_mlx(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = _checkpoint(Path(tmp), "other")
            with self.assertRaisesRegex(S.ScorerError, "checkpoint_format_model_mismatch"):
                S.MlxBackend.from_checkpoint(checkpoint, checkpoint_format=S.CHECKPOINT_FORMAT_MLX_SERVE)

    def test_custom_model_file_falsey_values_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = _checkpoint(Path(tmp), "custom")
            for value in ("custom.py", "", False, 0):
                with self.subTest(value=value):
                    (checkpoint / "config.json").write_text(json.dumps({"model_file": value}))
                    with self.assertRaisesRegex(S.ScorerError, "checkpoint_custom_model_code_forbidden"):
                        S._checkpoint(checkpoint, "missing")


class UpstreamQwen35NumericsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import mlx.core as mx
            from mlx_lm.models import qwen3_5
        except ImportError as exc:
            raise unittest.SkipTest("MLX numerical test requires the pinned evaluation runtime") from exc
        cls.mx, cls.qwen = mx, qwen3_5

    def test_upstream_sanitizer_preserves_folded_norms_and_shifts_raw_norms_once(self):
        mx, qwen = self.mx, self.qwen
        original = qwen.Model.sanitize
        args = qwen.ModelArgs(model_type="qwen3_5", text_config={
            "hidden_size": 4, "intermediate_size": 8, "num_hidden_layers": 1,
            "num_attention_heads": 1, "num_key_value_heads": 1, "head_dim": 4,
            "vocab_size": 8, "linear_num_value_heads": 1, "linear_num_key_heads": 1,
            "linear_key_head_dim": 4, "linear_value_head_dim": 4,
            "rope_parameters": {"type": "default", "partial_rotary_factor": 1.0, "rope_theta": 10000},
        })
        keys = ["model.layers.0.input_layernorm.weight", "model.layers.0.post_attention_layernorm.weight",
                "model.norm.weight", "model.layers.0.self_attn.q_norm.weight",
                "model.layers.0.self_attn.k_norm.weight"]
        raw = mx.array([-0.5, 0.0, 0.5, 1.0])
        folded = raw + 1.0
        gdn_key = "model.layers.0.linear_attn.norm.weight"
        conv_key = "model.layers.0.linear_attn.conv1d.weight"
        converted = {key: folded for key in keys}
        converted.update({gdn_key: raw, conv_key: mx.zeros((12, 4, 1)), "mtp.weight": mx.zeros((1,))})
        # Exercise the real upstream heuristic: retaining MTP would shift a second time.
        wrong = qwen.Model(args).sanitize(dict(converted))
        self.assertEqual(wrong["language_model.model.norm.weight"].tolist(), (folded + 1.0).tolist())
        selected = {}
        scoped = S._scoped_qwen35_model_class(qwen.Model, S.CHECKPOINT_FORMAT_MLX_SERVE, selected)
        correct = scoped(args).sanitize(converted)
        for key in keys:
            self.assertEqual(correct["language_model." + key].tolist(), folded.tolist())
        self.assertEqual(correct["language_model." + gdn_key].tolist(), raw.tolist())
        self.assertFalse(any("mtp." in key for key in correct))
        raw_weights = {key: raw for key in keys}
        raw_weights.update({gdn_key: raw, conv_key: mx.zeros((12, 1, 4)), "mtp.weight": mx.zeros((1,))})
        raw_scoped = S._scoped_qwen35_model_class(qwen.Model, S.CHECKPOINT_FORMAT_AUTO, {})
        raw_correct = raw_scoped(args).sanitize(raw_weights)
        for key in keys:
            self.assertEqual(raw_correct["language_model." + key].tolist(), folded.tolist())
        self.assertEqual(raw_correct["language_model." + conv_key].shape, (12, 4, 1))
        self.assertEqual(raw_correct["language_model." + gdn_key].tolist(), raw.tolist())
        self.assertIs(qwen.Model.sanitize, original)


class LocalReportTests(unittest.TestCase):
    def setUp(self):
        FakeBackend.reset()

    def test_missing_reference_fails_without_loading(self):
        result = S.score_local_dpo_pairs(
            [PAIR], policy_dir="/tmp/unused", reference_dir=None,
            backend_factory=FakeBackend,
        )
        self.assertEqual(result["status"], "eval_unavailable")
        self.assertEqual(result["reason"], "missing_reference_dir")
        self.assertIsNone(result["mean_loss"])
        self.assertEqual(FakeBackend.loads, 0)

    def test_tokenizer_fingerprint_mismatch_fails_before_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = _checkpoint(root, "policy", tokenizer="a", weights="p")
            reference = _checkpoint(root, "reference", tokenizer="b", weights="r")
            result = S.score_local_dpo_pairs(
                [PAIR], policy_dir=policy, reference_dir=reference,
                backend_factory=FakeBackend,
            )
        self.assertEqual(result["reason"], "tokenizer_fingerprint_mismatch")
        self.assertEqual(FakeBackend.loads, 0)

    def test_tokenizer_fingerprint_honors_abort_before_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = _checkpoint(root, "policy")
            reference = _checkpoint(root, "reference")
            result = S.score_local_dpo_pairs(
                [PAIR], policy_dir=policy, reference_dir=reference,
                backend_factory=FakeBackend, abort_check=lambda: True,
            )
        self.assertEqual(result["reason"], "aborted")
        self.assertEqual(FakeBackend.loads, 0)

    def test_tokenizer_fingerprint_honors_deadline_before_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = _checkpoint(root, "policy")
            reference = _checkpoint(root, "reference")
            result = S.score_local_dpo_pairs(
                [PAIR], policy_dir=policy, reference_dir=reference,
                backend_factory=FakeBackend, deadline_seconds=0.0,
            )
        self.assertEqual(result["reason"], "deadline_exceeded")
        self.assertEqual(FakeBackend.loads, 0)

    def test_identical_checkpoint_scores_once_and_is_ln2(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = _checkpoint(Path(tmp), "same")
            result = S.score_local_dpo_pairs(
                [PAIR], policy_dir=checkpoint, reference_dir=checkpoint,
                backend_factory=FakeBackend, dpo_loss_fn=F.dpo_loss_from_logprobs,
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(FakeBackend.loads, 1)
        self.assertTrue(result["reference_reused"])
        self.assertEqual(result["peak_bytes_scope"], S.PEAK_BYTES_SCOPE)
        self.assertEqual(result["pairs"][0]["delta"], 0.0)
        self.assertEqual(result["pairs"][0]["loss"], math.log(2.0))
        encoded = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("preferred", encoded)
        self.assertNotIn("prompt_ids", encoded)
        self.assertNotIn("response_token_ids", encoded)

    def test_different_checkpoints_load_sequentially(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = _checkpoint(root, "policy", weights="policy")
            reference = _checkpoint(root, "reference", weights="reference")
            result = S.score_local_dpo_pairs(
                [PAIR], policy_dir=policy, reference_dir=reference,
                backend_factory=FakeBackend, dpo_loss_fn=F.dpo_loss_from_logprobs,
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(FakeBackend.loads, 2)
        self.assertEqual(FakeBackend.max_active, 1)
        self.assertFalse(result["reference_reused"])


class CliIntegrationTests(unittest.TestCase):
    def test_new_flags_default_off_and_dirs_explicit(self):
        args = F.build_parser().parse_args([])
        self.assertFalse(args.dpo_score_local)
        self.assertIsNone(args.dpo_policy_dir)
        self.assertIsNone(args.dpo_reference_dir)
        self.assertEqual(args.dpo_checkpoint_format, S.CHECKPOINT_FORMAT_AUTO)

    def test_local_cli_report_uses_scorer_without_gateway(self):
        fake_report = {
            "status": "ok", "reason": "dpo_teacher_forced",
            "scoring_method": S.SCORING_METHOD, "string_similarity_used": False,
            "pair_count": 1, "evaluated": 1, "mean_loss": math.log(2.0), "pairs": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pairs = root / "pairs.jsonl"
            pairs.write_text(json.dumps(PAIR, ensure_ascii=False) + "\n", encoding="utf-8")
            policy, reference = root / "policy", root / "reference"
            buffer = io.StringIO()
            with mock.patch.object(S, "score_local_dpo_pairs", return_value=fake_report) as scorer:
                with contextlib.redirect_stdout(buffer):
                    code = F.main([
                        "--state-root", tmp, "--prepare-only", "--json",
                        "--dpo-pairs", str(pairs), "--dpo-score-local",
                        "--dpo-policy-dir", str(policy), "--dpo-reference-dir", str(reference),
                        "--dpo-checkpoint-format", S.CHECKPOINT_FORMAT_MLX_SERVE,
                    ])
            report = json.loads(buffer.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(report["dpo"], fake_report)
        scorer.assert_called_once()
        self.assertEqual(scorer.call_args.kwargs["checkpoint_format"], S.CHECKPOINT_FORMAT_MLX_SERVE)

    def test_direct_script_cli_imports_sibling_scorer(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            pairs = Path(tmp) / "pairs.jsonl"
            pairs.write_text(json.dumps(PAIR, ensure_ascii=False) + "\n", encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable, str(repo / "scripts" / "auto_reply_finetune.py"),
                    "--state-root", tmp, "--prepare-only", "--json",
                    "--dpo-pairs", str(pairs), "--dpo-score-local",
                ],
                cwd=repo, text=True, capture_output=True, check=False,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report["dpo"]["status"], "eval_unavailable")
        self.assertEqual(report["dpo"]["reason"], "missing_reference_dir")

    def test_sibling_scorer_imports_default_dpo_loss(self):
        repo = Path(__file__).resolve().parents[1]
        code = """
import json, tempfile
from pathlib import Path
import alden_dpo_scorer as S

class T:
    def apply_chat_template(self, messages, **kwargs):
        return '<U>' + messages[0]['content'] + '<A>'
    def encode(self, text, add_special_tokens=False):
        return [ord(c) + 1 for c in text]

class B:
    def __init__(self, checkpoint): self.tokenizer = T()
    def reset_peak_memory(self): pass
    def peak_bytes(self): return 7
    def make_cache(self): return {'offset': 0}
    def forward_chunk(self, input_ids, target_ids, cache, score_from):
        vals = [-0.25 for _ in range(score_from, len(input_ids))]
        cache['offset'] += len(input_ids)
        return vals
    def close(self): pass

with tempfile.TemporaryDirectory() as tmp:
    p = Path(tmp) / 'cp'; p.mkdir()
    (p/'config.json').write_text('{"model_type":"fake"}')
    (p/'tokenizer.json').write_text('same')
    (p/'model-00001-of-00001.safetensors').write_bytes(b'w')
    pair = {'pair_id':'p','prompt':'Q','preferred':'AB','dispreferred':'CD'}
    out = S.score_local_dpo_pairs([pair], policy_dir=p, reference_dir=p, backend_factory=B)
    print(json.dumps({'status': out['status'], 'loss': out['mean_loss']}))
"""
        proc = subprocess.run(
            [sys.executable, "-c", code], cwd=repo / "scripts",
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["loss"], math.log(2.0))


if __name__ == "__main__":
    unittest.main()
