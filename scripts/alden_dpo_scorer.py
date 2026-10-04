"""Offline teacher-forced DPO scorer for fixed local MLX checkpoints."""

from __future__ import annotations

import contextlib
import gc
import hashlib
import json
import math
import os
import sys
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable, Sequence

SCORING_METHOD = "mlx_direct_teacher_forcing"
PEAK_BYTES_SCOPE = "scoring_after_model_load"
CHECKPOINT_FORMAT_AUTO = "auto"
CHECKPOINT_FORMAT_MLX_LM = "mlx-lm"
CHECKPOINT_FORMAT_MLX_SERVE = "mlx-serve-qwen3_5-converted"
CHECKPOINT_FORMAT_CHOICES = (
    CHECKPOINT_FORMAT_AUTO,
    CHECKPOINT_FORMAT_MLX_LM,
    CHECKPOINT_FORMAT_MLX_SERVE,
)
MAX_PAIRS = 32
MAX_TOTAL_TOKENS = 2048
MAX_RESPONSE_TOKENS = 256
DEFAULT_CHUNK_SIZE = 64
MAX_CHUNK_SIZE = 128
TOKENIZER_NAMES = {
    "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
    "added_tokens.json", "tokenizer.model", "tokenizer.tiktoken",
    "tiktoken.model", "chat_template.jinja",
}


class ScorerError(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _checkpoint_format(value: str | None) -> str:
    normalized = str(value or CHECKPOINT_FORMAT_AUTO).strip().lower()
    if normalized not in CHECKPOINT_FORMAT_CHOICES:
        raise ScorerError("unknown_checkpoint_format")
    return normalized


def _qwen35_sanitize_input(
    weights: dict[str, Any], checkpoint_format: str
) -> tuple[dict[str, Any], str]:
    """Prepare qwen3_5 weights for the upstream MLX-LM sanitizer.

    MLX-LM 0.31.3 uses either MTP weights or an unconverted Conv1d layout as
    evidence that layer/final/qk norms still need the Hugging Face +1 fold.
    ddalcu MLX-Serve checkpoints preserve inference-only MTP weights after that
    fold and Conv1d conversion, so MTP must be removed before the upstream
    sanitizer makes that decision.
    """
    checkpoint_format = _checkpoint_format(checkpoint_format)
    mtp_keys = tuple(key for key in weights if "mtp." in key)
    conv_weights = [value for key, value in weights.items() if "conv1d.weight" in key]
    conv_last_dims: list[int] = []
    for value in conv_weights:
        shape = getattr(value, "shape", None)
        if not shape or len(shape) != 3:
            raise ScorerError("checkpoint_layout_ambiguous")
        try:
            if any(int(dim) <= 0 for dim in shape):
                raise ScorerError("checkpoint_layout_ambiguous")
            conv_last_dims.append(int(shape[-1]))
        except (TypeError, ValueError, IndexError) as exc:
            raise ScorerError("checkpoint_layout_ambiguous") from exc
    has_unconverted_conv = any(last_dim != 1 for last_dim in conv_last_dims)
    if has_unconverted_conv and any(last_dim == 1 for last_dim in conv_last_dims):
        raise ScorerError("checkpoint_conv_layout_mixed")

    if checkpoint_format == CHECKPOINT_FORMAT_AUTO:
        if mtp_keys and not has_unconverted_conv:
            raise ScorerError("checkpoint_layout_ambiguous")
        return weights, CHECKPOINT_FORMAT_MLX_LM

    if checkpoint_format == CHECKPOINT_FORMAT_MLX_LM:
        return weights, CHECKPOINT_FORMAT_MLX_LM

    if not conv_weights:
        raise ScorerError("mlx_serve_conv_layout_missing")
    if has_unconverted_conv:
        raise ScorerError("mlx_serve_conv_layout_unconverted")
    prepared = {key: value for key, value in weights.items() if "mtp." not in key}
    return prepared, CHECKPOINT_FORMAT_MLX_SERVE


def _scoped_qwen35_model_class(base_model_class, checkpoint_format: str, selected: dict[str, str]):
    """Return a scorer-only Model subclass without mutating mlx_lm globals."""

    class ScorerQwen35Model(base_model_class):
        def sanitize(self, weights):
            prepared, actual_format = _qwen35_sanitize_input(weights, checkpoint_format)
            selected["format"] = actual_format
            self._alden_checkpoint_format = actual_format
            return super().sanitize(prepared)

    return ScorerQwen35Model


def _versions() -> dict[str, str]:
    out = {"python": sys.version.split()[0]}
    for name in ("mlx", "mlx-lm", "transformers"):
        try:
            out[name] = version(name)
        except PackageNotFoundError:
            out[name] = "unavailable"
    return out


def _check_abort(abort_check, deadline) -> None:
    if abort_check is not None and abort_check():
        raise ScorerError("aborted")
    if deadline is not None and time.monotonic() >= deadline:
        raise ScorerError("deadline_exceeded")


def _checkpoint(path: Path | str | None, missing_reason: str) -> Path:
    if path is None:
        raise ScorerError(missing_reason)
    raw = Path(path).expanduser()
    if not raw.is_absolute():
        raise ScorerError("checkpoint_not_absolute")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise ScorerError("checkpoint_missing") from exc
    if not resolved.is_dir():
        raise ScorerError("checkpoint_not_directory")
    try:
        config = json.loads((resolved / "config.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ScorerError("checkpoint_config_invalid") from exc
    if not isinstance(config, dict) or config.get("model_file") is not None:
        raise ScorerError("checkpoint_custom_model_code_forbidden")
    if not list(resolved.glob("model*.safetensors")):
        raise ScorerError("checkpoint_weights_missing")
    return resolved


def _tokenizer_files(checkpoint: Path) -> list[Path]:
    files = [checkpoint / name for name in TOKENIZER_NAMES if (checkpoint / name).is_file()]
    files.extend(path for path in checkpoint.glob("*.jinja") if path.is_file())
    return sorted(set(files), key=lambda path: path.name)


def _digest(checkpoint: Path, files: Sequence[Path], abort_check=None, deadline=None) -> str:
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.relative_to(checkpoint).as_posix()):
        _check_abort(abort_check, deadline)
        name = path.relative_to(checkpoint).as_posix().encode()
        digest.update(len(name).to_bytes(4, "big") + name)
        try:
            with path.open("rb") as handle:
                while chunk := handle.read(8 * 1024 * 1024):
                    digest.update(chunk)
                    _check_abort(abort_check, deadline)
        except OSError as exc:
            raise ScorerError("checkpoint_hash_failed") from exc
    return digest.hexdigest()


def tokenizer_fingerprint(checkpoint: Path, abort_check=None, deadline=None) -> str:
    files = _tokenizer_files(checkpoint)
    if not files:
        raise ScorerError("tokenizer_files_missing")
    return _digest(checkpoint, files, abort_check, deadline)


def _default_dpo_loss_fn():
    try:
        from scripts.auto_reply_finetune import dpo_loss_from_logprobs
    except ImportError:  # Direct script/sibling import from scripts/.
        from auto_reply_finetune import dpo_loss_from_logprobs
    return dpo_loss_from_logprobs


def checkpoint_fingerprint(checkpoint: Path, abort_check=None, deadline=None) -> str:
    files = [checkpoint / "config.json", *_tokenizer_files(checkpoint)]
    generation = checkpoint / "generation_config.json"
    if generation.is_file():
        files.append(generation)
    files.extend(sorted(checkpoint.glob("model*.safetensors")))
    return _digest(checkpoint, files, abort_check, deadline)


def _encode(tokenizer: Any, text: str) -> tuple[int, ...]:
    try:
        return tuple(int(value) for value in tokenizer.encode(text, add_special_tokens=False))
    except Exception as exc:
        raise ScorerError("tokenization_failed") from exc


def _prepare_response(tokenizer: Any, pair_id: str, rendered: str, response: str) -> dict[str, Any]:
    prompt_ids = _encode(tokenizer, rendered)
    full_ids = _encode(tokenizer, rendered + response)
    if not prompt_ids or len(full_ids) <= len(prompt_ids):
        raise ScorerError("response_tokens_missing")
    if full_ids[: len(prompt_ids)] != prompt_ids:
        raise ScorerError("prompt_boundary_tokenization_mismatch")
    response_ids = full_ids[len(prompt_ids):]
    if len(response_ids) > MAX_RESPONSE_TOKENS:
        raise ScorerError("response_too_long")
    if len(full_ids) > MAX_TOTAL_TOKENS:
        raise ScorerError("sequence_too_long")
    mask = tuple(index >= len(prompt_ids) - 1 for index in range(len(full_ids) - 1))
    if sum(mask) != len(response_ids):
        raise ScorerError("response_mask_mismatch")
    return {
        "pair_id": pair_id, "prompt_ids": prompt_ids, "full_ids": full_ids,
        "response_ids": response_ids, "response_mask": mask,
    }


def prepare_pair_tokens(tokenizer: Any, pair: dict[str, Any], index: int = 0):
    if not isinstance(pair, dict):
        raise ScorerError("invalid_pair")
    pair_id = str(pair.get("pair_id") or f"pair-{index}")
    prompt = str(pair.get("prompt") or "")
    chosen = pair.get("preferred") if pair.get("preferred") is not None else pair.get("chosen")
    rejected = pair.get("dispreferred") if pair.get("dispreferred") is not None else pair.get("rejected")
    if not prompt.strip():
        raise ScorerError("empty_prompt")
    if not isinstance(chosen, str) or not chosen:
        raise ScorerError("missing_chosen_response")
    if not isinstance(rejected, str) or not rejected:
        raise ScorerError("missing_rejected_response")
    try:
        rendered = tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}], tokenize=False,
            add_generation_prompt=True, enable_thinking=False,
        )
    except Exception as exc:
        raise ScorerError("chat_template_failed") from exc
    if not isinstance(rendered, str) or not rendered:
        raise ScorerError("chat_template_invalid")
    chosen_tokens = _prepare_response(tokenizer, pair_id, rendered, chosen)
    rejected_tokens = _prepare_response(tokenizer, pair_id, rendered, rejected)
    if chosen_tokens["prompt_ids"] != rejected_tokens["prompt_ids"]:
        raise ScorerError("pair_prompt_mismatch")
    return chosen_tokens, rejected_tokens


class MlxBackend:
    def __init__(self, model, tokenizer, mx, nn, make_cache,
                 checkpoint_format=CHECKPOINT_FORMAT_MLX_LM):
        self.model, self.tokenizer, self.mx, self.nn = model, tokenizer, mx, nn
        self._make_cache = make_cache
        self.checkpoint_format = checkpoint_format

    @classmethod
    def from_checkpoint(cls, checkpoint: Path, *, checkpoint_format=CHECKPOINT_FORMAT_AUTO,
                        adapter=None):
        checkpoint_format = _checkpoint_format(checkpoint_format)
        checkpoint = _checkpoint(checkpoint, "missing_policy_dir")
        config = json.loads((checkpoint / "config.json").read_text(encoding="utf-8"))
        qwen35 = config.get("model_type") in {"qwen3_5", "qwen3_5_text"}
        if checkpoint_format == CHECKPOINT_FORMAT_MLX_SERVE and not qwen35:
            raise ScorerError("checkpoint_format_model_mismatch")
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        import mlx.core as mx
        import mlx.nn as nn
        from mlx_lm.models.cache import make_prompt_cache
        from mlx_lm.utils import load, load_model, load_tokenizer
        selected = {"format": CHECKPOINT_FORMAT_MLX_LM}
        with contextlib.redirect_stdout(sys.stderr):
            tokenizer_config = {"trust_remote_code": False, "local_files_only": True}
            if qwen35:
                from mlx_lm.models import qwen3_5
                base_model, args_class = (
                    (qwen3_5.TextModel, qwen3_5.TextModelArgs)
                    if config["model_type"] == "qwen3_5_text"
                    else (qwen3_5.Model, qwen3_5.ModelArgs)
                )
                scoped_model = _scoped_qwen35_model_class(base_model, checkpoint_format, selected)
                # load_model calls its factory with the named `config` argument.
                def model_classes(config):
                    return scoped_model, args_class
                model, loaded_config = load_model(checkpoint, get_model_classes=model_classes)
                tokenizer = load_tokenizer(
                    checkpoint, tokenizer_config, eos_token_ids=loaded_config.get("eos_token_id")
                )
            else:
                model, tokenizer = load(str(checkpoint), tokenizer_config=tokenizer_config)
            if adapter is not None:
                try:
                    from scripts.alden_dpo_adapter import apply
                except ImportError:
                    from alden_dpo_adapter import apply
                apply(model, adapter, mx)
        return cls(model, tokenizer, mx, nn, make_prompt_cache, selected["format"])

    def reset_peak_memory(self):
        self.mx.reset_peak_memory()

    def peak_bytes(self):
        return int(self.mx.get_peak_memory())

    def make_cache(self):
        return self._make_cache(self.model)

    def forward_chunk(self, input_ids, target_ids, cache, score_from):
        logits = self.model(self.mx.array([list(input_ids)]), cache=cache)
        states = [item.state for item in cache if hasattr(item, "state")]
        if score_from >= len(input_ids):
            self.mx.eval(states if states else logits[:, -1, 0])
            self.mx.clear_cache()
            return []
        logits = logits[:, score_from:, :].astype(self.mx.float32)
        log_probs = self.nn.log_softmax(logits)
        targets = self.mx.array([list(target_ids[score_from:])])[:, :, self.mx.newaxis]
        gathered = self.mx.take_along_axis(log_probs, targets, axis=-1)[..., 0]
        self.mx.eval(gathered, states)
        values = [float(value) for value in gathered[0].tolist()]
        self.mx.clear_cache()
        return values

    def close(self):
        self.model = self.tokenizer = None
        gc.collect()
        self.mx.clear_cache()


def score_fixed_response(backend: Any, prepared: dict[str, Any], *, chunk_size=DEFAULT_CHUNK_SIZE,
                         abort_check=None, deadline=None) -> dict[str, Any]:
    if not isinstance(chunk_size, int) or not 1 <= chunk_size <= MAX_CHUNK_SIZE:
        raise ScorerError("invalid_chunk_size")
    full_ids, prompt_ids = prepared["full_ids"], prepared["prompt_ids"]
    input_ids, targets, score_start = full_ids[:-1], full_ids[1:], len(prompt_ids) - 1
    cache, logprobs = backend.make_cache(), []
    for start in range(0, len(input_ids), chunk_size):
        _check_abort(abort_check, deadline)
        end = min(start + chunk_size, len(input_ids))
        local_start = min(max(score_start - start, 0), end - start)
        logprobs.extend(float(value) for value in backend.forward_chunk(
            input_ids[start:end], targets[start:end], cache, local_start
        ))
    if len(logprobs) != len(prepared["response_ids"]):
        raise ScorerError("invalid_logprob_length")
    if any(not math.isfinite(value) for value in logprobs):
        raise ScorerError("non_finite_logprob")
    if any(value > 0.0 for value in logprobs):
        raise ScorerError("positive_logprob")
    return {
        "pair_id": prepared["pair_id"], "prompt_ids": prompt_ids,
        "response_token_ids": prepared["response_ids"],
        "response_mask": prepared["response_mask"], "logprobs": tuple(logprobs),
    }


def _score_checkpoint(checkpoint, pairs, chunk_size, backend_factory, abort_check, deadline,
                      checkpoint_format=CHECKPOINT_FORMAT_AUTO, adapter=None):
    _check_abort(abort_check, deadline)
    if backend_factory is not None:
        backend = backend_factory(checkpoint) if adapter is None else backend_factory(checkpoint, adapter=adapter)
    else:
        options = {} if adapter is None else {"adapter":adapter}
        backend = MlxBackend.from_checkpoint(checkpoint, checkpoint_format=checkpoint_format, **options)
    try:
        if hasattr(backend, "reset_peak_memory"):
            backend.reset_peak_memory()
        scored = []
        for index, pair in enumerate(pairs):
            chosen, rejected = prepare_pair_tokens(backend.tokenizer, pair, index)
            scored.append({
                "pair_id": chosen["pair_id"],
                "chosen": score_fixed_response(backend, chosen, chunk_size=chunk_size,
                    abort_check=abort_check, deadline=deadline),
                "rejected": score_fixed_response(backend, rejected, chunk_size=chunk_size,
                    abort_check=abort_check, deadline=deadline),
            })
        peak = int(backend.peak_bytes()) if hasattr(backend, "peak_bytes") else 0
        return scored, peak, getattr(backend, "checkpoint_format", "injected_backend")
    finally:
        try:
            backend.close()
        finally:
            del backend
            gc.collect()


def validate_pair_scores(policy: dict[str, Any], reference: dict[str, Any]) -> None:
    if policy["pair_id"] != reference["pair_id"]:
        raise ScorerError("reference_pair_id_mismatch")
    for score in (policy["chosen"], policy["rejected"], reference["chosen"], reference["rejected"]):
        if len(score["logprobs"]) != len(score["response_token_ids"]):
            raise ScorerError("invalid_logprob_length")
        if sum(score["response_mask"]) != len(score["response_token_ids"]):
            raise ScorerError("response_mask_mismatch")
        if any(not math.isfinite(value) for value in score["logprobs"]):
            raise ScorerError("non_finite_logprob")
        if any(value > 0.0 for value in score["logprobs"]):
            raise ScorerError("positive_logprob")
    if policy["chosen"]["prompt_ids"] != policy["rejected"]["prompt_ids"]:
        raise ScorerError("pair_prompt_mismatch")
    if reference["chosen"]["prompt_ids"] != reference["rejected"]["prompt_ids"]:
        raise ScorerError("reference_pair_prompt_mismatch")
    if policy["chosen"]["prompt_ids"] != reference["chosen"]["prompt_ids"]:
        raise ScorerError("reference_prompt_token_mismatch")
    if policy["chosen"]["response_token_ids"] != reference["chosen"]["response_token_ids"]:
        raise ScorerError("reference_chosen_token_mismatch")
    if policy["rejected"]["response_token_ids"] != reference["rejected"]["response_token_ids"]:
        raise ScorerError("reference_rejected_token_mismatch")


def _unavailable(reason: str, started: float, pair_count: int):
    return {
        "status": "eval_unavailable", "reason": reason, "scoring_method": SCORING_METHOD,
        "string_similarity_used": False, "pair_count": pair_count, "evaluated": 0,
        "mean_loss": None, "runtime_seconds": round(time.monotonic() - started, 6),
        "peak_bytes": 0, "peak_bytes_scope": PEAK_BYTES_SCOPE,
        "versions": _versions(), "pairs": [],
    }


def score_local_dpo_pairs(
    pairs: Sequence[dict[str, Any]], *, policy_dir: Path | str | None,
    reference_dir: Path | str | None, beta: float = 0.1,
    chunk_size: int = DEFAULT_CHUNK_SIZE, backend_factory: Callable[[Path], Any] | None = None,
    dpo_loss_fn: Callable[..., dict[str, Any]] | None = None,
    abort_check: Callable[[], bool] | None = None, deadline_seconds: float | None = None,
    checkpoint_format: str = CHECKPOINT_FORMAT_AUTO,
    policy_adapter_dir: Path | str | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    pair_count = len(pairs) if isinstance(pairs, (list, tuple)) else 0
    deadline = started + float(deadline_seconds) if deadline_seconds is not None else None
    try:
        checkpoint_format = _checkpoint_format(checkpoint_format)
        if not isinstance(pairs, (list, tuple)) or not pairs:
            raise ScorerError("no_pairs")
        if pair_count > MAX_PAIRS:
            raise ScorerError("too_many_pairs")
        if reference_dir is None:
            raise ScorerError("missing_reference_dir")
        policy = _checkpoint(policy_dir, "missing_policy_dir")
        reference = _checkpoint(reference_dir, "missing_reference_dir")
        policy_tok = tokenizer_fingerprint(policy, abort_check, deadline)
        reference_tok = tokenizer_fingerprint(reference, abort_check, deadline)
        if policy_tok != reference_tok:
            raise ScorerError("tokenizer_fingerprint_mismatch")
        policy_sha = checkpoint_fingerprint(policy, abort_check, deadline)
        reference_sha = policy_sha if policy == reference else checkpoint_fingerprint(reference, abort_check, deadline)
        adapter_context = contextlib.nullcontext(None)
        if policy_adapter_dir is not None:
            try:
                from scripts.alden_dpo_adapter import snapshot
            except ImportError:
                from alden_dpo_adapter import snapshot
            adapter_context = snapshot(policy_adapter_dir, base_sha256=policy_sha,
                                       checkpoint_format=checkpoint_format,
                                       abort_check=abort_check, deadline=deadline)
        with adapter_context as adapter:
            adapter_sha = adapter["sha256"] if adapter is not None else None
            policy_scores, policy_peak, policy_format = _score_checkpoint(
                policy, pairs, chunk_size, backend_factory, abort_check, deadline, checkpoint_format, adapter
            )
        same_checkpoint = policy_sha == reference_sha and adapter_sha is None
        if same_checkpoint:
            reference_scores, reference_peak, reference_format = policy_scores, policy_peak, policy_format
        else:
            reference_scores, reference_peak, reference_format = _score_checkpoint(
                reference, pairs, chunk_size, backend_factory, abort_check, deadline, checkpoint_format
            )
        if len(policy_scores) != len(reference_scores):
            raise ScorerError("reference_pair_count_mismatch")
        if dpo_loss_fn is None:
            dpo_loss_fn = _default_dpo_loss_fn()
        public_pairs, losses = [], []
        for policy_pair, reference_pair in zip(policy_scores, reference_scores):
            validate_pair_scores(policy_pair, reference_pair)
            chosen, rejected = policy_pair["chosen"], policy_pair["rejected"]
            ref_chosen, ref_rejected = reference_pair["chosen"], reference_pair["rejected"]
            result = dpo_loss_fn(
                chosen_logprobs=chosen["logprobs"], rejected_logprobs=rejected["logprobs"],
                ref_chosen_logprobs=ref_chosen["logprobs"], ref_rejected_logprobs=ref_rejected["logprobs"],
                beta=beta, require_reference=True, tokenizer_id=policy_tok, base_model=policy_sha,
            )
            if result.get("status") != "ok" or result.get("loss") is None:
                raise ScorerError(str(result.get("reason") or "dpo_loss_unavailable"))
            loss, delta = float(result["loss"]), float(result.get("delta", 0.0))
            if not math.isfinite(loss) or not math.isfinite(delta):
                raise ScorerError("non_finite_dpo_result")
            if same_checkpoint and (delta != 0.0 or loss != math.log(2.0)):
                raise ScorerError("identical_reference_invariant_failed")
            losses.append(loss)
            public_pairs.append({
                "pair_id": policy_pair["pair_id"],
                "chosen_tokens": len(chosen["response_token_ids"]),
                "rejected_tokens": len(rejected["response_token_ids"]),
                "policy_chosen_logprob": sum(chosen["logprobs"]),
                "policy_rejected_logprob": sum(rejected["logprobs"]),
                "reference_chosen_logprob": sum(ref_chosen["logprobs"]),
                "reference_rejected_logprob": sum(ref_rejected["logprobs"]),
                "delta": delta, "loss": loss,
            })
        return {
            "status": "ok", "reason": "dpo_teacher_forced", "scoring_method": SCORING_METHOD,
            "string_similarity_used": False, "pair_count": pair_count, "evaluated": len(public_pairs),
            "mean_loss": sum(losses) / len(losses), "runtime_seconds": round(time.monotonic() - started, 6),
            "peak_bytes": max(policy_peak, reference_peak), "peak_bytes_scope": PEAK_BYTES_SCOPE,
            "policy_sha256": policy_sha,
            "policy_adapter_sha256": adapter_sha,
            "reference_sha256": reference_sha, "tokenizer_sha256": policy_tok,
            "reference_frozen": True, "reference_reused": same_checkpoint,
            "requested_checkpoint_format": checkpoint_format,
            "policy_checkpoint_format": policy_format,
            "reference_checkpoint_format": reference_format,
            "identical_checkpoint_delta_zero": same_checkpoint, "versions": _versions(), "pairs": public_pairs,
        }
    except ScorerError as exc:
        return _unavailable(exc.reason, started, pair_count)
    except Exception as exc:
        print(f"alden_dpo_scorer: {type(exc).__name__}", file=sys.stderr)
        return _unavailable("scorer_runtime_error", started, pair_count)
