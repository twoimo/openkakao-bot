"""Fine-tuning pipeline for the golden reply dataset.

The golden extractor produces supervised (prompt, completion) pairs. This
module turns them into the train/valid/test JSONL layout mlx_lm.lora reads,
runs the LoRA/DoRA training, and evaluates the result against a held-out split.

The evaluation is the part that matters for the improvement loop: a trained
adapter is only worth deploying if it moves the held-out loss toward the gold
answers, so both numbers are measured and reported together.

Nothing here downloads weights or starts training on its own; every entry point
takes explicit paths and the caller decides when to spend the compute
(2026-09-17).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import time
import math
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

try:
    # The golden extractor owns the transcript clock format, so its parser is
    # reused instead of re-implemented: a session bucket that disagrees with
    # the row's own timestamp would put one burst on both sides of the split
    # (2026-09-19).
    from scripts.auto_reply_golden_dataset import parse_timestamp
except ImportError:  # `scripts` is not importable from inside its own directory
    from auto_reply_golden_dataset import parse_timestamp

SCHEMA_VERSION = 1
DEFAULT_VALID_RATIO = 0.1
DEFAULT_TEST_RATIO = 0.05
DEFAULT_SEED = 42
# Golden rows are cut from the same room bursts, so neighbours share most of
# their window text. Two rows further apart than this gap come from separate
# conversations and may land on opposite sides of the split (2026-09-19).
DEFAULT_SESSION_GAP = 1800.0
# mlx_lm.lora masks the prompt when told to, so the model is scored on the
# answer only. The instruction line frames the task the same way every time.
DEFAULT_INSTRUCTION = "다음 카카오톡 대화에 자연스럽게 답장하세요."
FLASH_NEXT_MODEL_ID = "mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit"


@dataclass
class SplitStats:
    train: int = 0
    valid: int = 0
    test: int = 0
    dropped: int = 0
    duplicate_prompts: int = 0
    groups: int = 0
    train_groups: int = 0
    valid_groups: int = 0
    test_groups: int = 0


@dataclass
class TrainingPlan:
    """Everything the trainer needs, without running anything."""

    model: str
    data_dir: str
    adapter_path: str
    iters: int
    batch_size: int
    learning_rate: float
    num_layers: int
    max_seq_length: int
    fine_tune_type: str
    train_examples: int
    command: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_pairs(path: Path) -> list[dict[str, Any]]:
    """Read the golden JSONL, skipping rows that cannot be trained on."""
    if not path.is_file():
        return []
    pairs: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            prompt = str(record.get("prompt") or "").strip()
            completion = str(record.get("completion") or "").strip()
            if not prompt or not completion:
                continue
            pairs.append(record)
    return pairs


def _to_mlx_row(record: dict[str, Any], instruction: str) -> dict[str, str]:
    """Shape one golden pair as the mlx_lm chat row.

    mlx_lm reads a prompt/completion pair for completions-style data. The room
    window is folded into the prompt so the model sees the same context the
    live worker would give it (2026-09-17).
    """
    prompt = str(record.get("prompt") or "").strip()
    window = record.get("window")
    lines: list[str] = []
    if isinstance(window, list):
        for entry in window:
            if not isinstance(entry, dict):
                continue
            author = str(entry.get("author") or "").strip()
            message = str(entry.get("message") or "").strip()
            if not message:
                continue
            lines.append(f"{author}: {message}" if author else message)
    if not lines:
        lines.append(prompt)
    body = chr(10).join(lines)
    return {
        "prompt": f"{instruction}{chr(10)}{chr(10)}{body}{chr(10)}",
        "completion": str(record.get("completion") or "").strip(),
    }


def _normalized_prompt(record: dict[str, Any]) -> str:
    """Fold whitespace so two spellings of one window count as one prompt."""
    return " ".join(str(record.get("prompt") or "").split())


def _session_key(record: dict[str, Any], session_gap: float) -> tuple[str, str] | None:
    """Name the conversation a row belongs to, or None when it stands alone.

    A row without a room cannot be held together with anything else, so it is
    its own group. A room row whose clock cannot be read joins that room's
    no_clock group: an unreadable timestamp must not become a licence to split
    a burst apart (2026-09-19).
    """
    room = str(record.get("room") or "").strip()
    if not room:
        return None
    epoch = parse_timestamp(record.get("recorded_at") or record.get("recordedAt"))
    if epoch <= 0:
        return (room, "no_clock")
    return (room, str(int(epoch // session_gap)))


def _group_rows(
    pairs: Sequence[dict[str, Any]], session_gap: float
) -> list[list[dict[str, Any]]]:
    """Bucket the pairs by (room, session), keeping the input order inside."""
    groups: list[list[dict[str, Any]]] = []
    index: dict[tuple[str, str], int] = {}
    for record in pairs:
        key = _session_key(record, session_gap)
        if key is None:
            groups.append([record])
            continue
        position = index.get(key)
        if position is None:
            index[key] = len(groups)
            groups.append([record])
        else:
            groups[position].append(record)
    return groups


def build_splits(
    pairs: Sequence[dict[str, Any]],
    *,
    instruction: str = DEFAULT_INSTRUCTION,
    valid_ratio: float = DEFAULT_VALID_RATIO,
    test_ratio: float = DEFAULT_TEST_RATIO,
    seed: int = DEFAULT_SEED,
    session_gap: float = DEFAULT_SESSION_GAP,
) -> tuple[dict[str, list[dict[str, str]]], SplitStats]:
    """Split the pairs into train/valid/test without leaking between them.

    Golden rows are cut from the same room bursts, so their windows overlap: a
    row-by-row shuffle puts one conversation on both sides of the split and the
    held-out loss stops measuring anything. The rows are therefore
    de-duplicated on the whitespace-folded prompt and then grouped by room and
    session, and a whole group is dealt to exactly one split (2026-09-19).
    """
    stats = SplitStats()
    if not pairs:
        return {"train": [], "valid": [], "test": []}, stats

    kept: list[dict[str, Any]] = []
    seen_prompts: set[str] = set()
    for record in pairs:
        prompt = _normalized_prompt(record)
        if prompt and prompt in seen_prompts:
            # The same window with a second answer teaches nothing, and kept
            # apart it would score the model on text it trained on.
            stats.duplicate_prompts += 1
            stats.dropped += 1
            continue
        if prompt:
            seen_prompts.add(prompt)
        kept.append(record)

    # A zero or negative gap would divide the clock by zero.
    session_seconds = max(1.0, float(session_gap))
    groups = _group_rows(kept, session_seconds)
    rng = random.Random(seed)
    rng.shuffle(groups)

    total = len(kept)
    test_count = int(total * max(0.0, test_ratio))
    valid_count = int(total * max(0.0, valid_ratio))
    # Keep at least one training row; a split that trains on nothing is worse
    # than a slightly larger validation set.
    while test_count + valid_count >= total and (test_count or valid_count):
        if test_count >= valid_count and test_count:
            test_count -= 1
        elif valid_count:
            valid_count -= 1

    # Deal whole groups in the shuffled order until each target is reached, so
    # a target is crossed by a conversation instead of inside one.
    entries: list[tuple[list[dict[str, Any]], str]] = []
    dealt_test = 0
    dealt_valid = 0
    for group in groups:
        size = len(group)
        if dealt_test < test_count:
            entries.append((group, "test"))
            dealt_test += size
        elif dealt_valid < valid_count:
            entries.append((group, "valid"))
            dealt_valid += size
        else:
            entries.append((group, "train"))

    if not any(name == "train" for _, name in entries):
        # One burst can swallow the whole test and valid budget and leave
        # nothing to train on. Demote the smallest group that is not already
        # training, which is how a single-group dataset ends up in train
        # (2026-09-19).
        smallest = min(range(len(entries)), key=lambda i: (len(entries[i][0]), i))
        entries[smallest] = (entries[smallest][0], "train")

    splits: dict[str, list[dict[str, str]]] = {"train": [], "valid": [], "test": []}
    group_counts = {"train": 0, "valid": 0, "test": 0}
    for group, name in entries:
        group_counts[name] += 1
        splits[name].extend(_to_mlx_row(record, instruction) for record in group)

    stats.train = len(splits["train"])
    stats.valid = len(splits["valid"])
    stats.test = len(splits["test"])
    stats.groups = len(entries)
    stats.train_groups = group_counts["train"]
    stats.valid_groups = group_counts["valid"]
    stats.test_groups = group_counts["test"]
    return splits, stats


def write_splits(splits: dict[str, list[dict[str, str]]], data_dir: Path) -> list[Path]:
    """Write the three JSONL files mlx_lm.lora expects."""
    data_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name in ("train", "valid", "test"):
        target = data_dir / f"{name}.jsonl"
        tmp = target.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            for row in splits.get(name, []):
                handle.write(json.dumps(row, ensure_ascii=False) + chr(10))
        tmp.replace(target)
        written.append(target)
    return written


def plan_training(
    *,
    model: str,
    data_dir: Path,
    adapter_path: Path,
    train_examples: int,
    iters: int = 0,
    batch_size: int = 4,
    learning_rate: float = 1e-5,
    num_layers: int = 16,
    max_seq_length: int = 1024,
    fine_tune_type: str = "lora",
) -> TrainingPlan:
    """Decide the hyper-parameters and the exact command.

    The iteration count is derived from the dataset when the caller does not
    pin it: two epochs over the training split is enough to move a small set
    without overfitting it into memorised replies (2026-09-17).
    """
    effective_iters = iters
    if effective_iters <= 0:
        epochs = 2.0
        effective_iters = max(20, int(train_examples / max(1, batch_size) * epochs))

    command = [
        "mlx_lm.lora",
        "--model",
        model,
        "--train",
        "--data",
        str(data_dir),
        "--adapter-path",
        str(adapter_path),
        "--fine-tune-type",
        fine_tune_type,
        "--batch-size",
        str(batch_size),
        "--iters",
        str(effective_iters),
        "--learning-rate",
        str(learning_rate),
        "--num-layers",
        str(num_layers),
        "--max-seq-length",
        str(max_seq_length),
        "--mask-prompt",
    ]
    return TrainingPlan(
        model=model,
        data_dir=str(data_dir),
        adapter_path=str(adapter_path),
        iters=effective_iters,
        batch_size=batch_size,
        learning_rate=learning_rate,
        num_layers=num_layers,
        max_seq_length=max_seq_length,
        fine_tune_type=fine_tune_type,
        train_examples=train_examples,
        command=command,
    )


def prepare_dataset(
    *,
    golden_path: Path,
    data_dir: Path,
    instruction: str = DEFAULT_INSTRUCTION,
    valid_ratio: float = DEFAULT_VALID_RATIO,
    test_ratio: float = DEFAULT_TEST_RATIO,
    seed: int = DEFAULT_SEED,
    session_gap: float = DEFAULT_SESSION_GAP,
) -> tuple[dict[str, Any], dict[str, list[dict[str, str]]]]:
    """Read the golden set and write the three split files."""
    pairs = load_pairs(golden_path)
    splits, stats = build_splits(
        pairs,
        instruction=instruction,
        valid_ratio=valid_ratio,
        test_ratio=test_ratio,
        seed=seed,
        session_gap=session_gap,
    )
    files = write_splits(splits, data_dir)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "golden_path": str(golden_path),
        "data_dir": str(data_dir),
        "files": [str(f) for f in files],
        "train": stats.train,
        "valid": stats.valid,
        "test": stats.test,
        "dropped": stats.dropped,
        "duplicate_prompts": stats.duplicate_prompts,
        "groups": stats.groups,
        "train_groups": stats.train_groups,
        "valid_groups": stats.valid_groups,
        "test_groups": stats.test_groups,
        "valid_ratio": valid_ratio,
        "test_ratio": test_ratio,
        "seed": seed,
        "session_gap": session_gap,
        "prepared_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    summary_path = data_dir / "split-summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary, splits


def _parse_test_loss(output: str) -> float | None:
    """Pull the reported loss out of mlx_lm.lora test output."""
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        if not stripped.lower().startswith("test loss"):
            continue
        # The line reads "Test loss 3.412, Test ppl 30.3". Take the
        # number directly after the word loss, not the last number on
        # the line, which is the perplexity (2026-09-17).
        parts = stripped.replace(",", " ").split()
        for index, token in enumerate(parts):
            if token.lower() == "loss" and index + 1 < len(parts):
                try:
                    return float(parts[index + 1])
                except ValueError:
                    break
    return None


def _resolve_lora_executable() -> str:
    found = shutil.which("mlx_lm.lora")
    if found:
        return found
    local = Path.home() / ".local/bin/mlx_lm.lora"
    if local.exists():
        return str(local)
    # mlx_lm also dispatches subcommands, which is how it is usually installed.
    mlx = shutil.which("mlx_lm") or str(Path.home() / ".local/bin/mlx_lm")
    if Path(mlx).exists():
        return mlx
    return ""


def evaluate_adapter(
    *,
    model: str,
    adapter_path: Path | None,
    data_dir: Path,
    test_batches: int = 4,
    batch_size: int = 4,
    timeout: float = 1800.0,
) -> dict[str, Any]:
    """Run the mlx_lm test pass and return the reported loss.

    The same call is used before and after training, so the two numbers are
    comparable. A missing mlx_lm is reported rather than raised, because the
    evaluation is diagnostic and must not kill a scheduled run (2026-09-17).
    """
    executable = _resolve_lora_executable()
    if not executable:
        return {"ok": False, "error": "mlx_lm_not_installed"}

    command = [executable, "lora"] if executable.endswith("mlx_lm") else [executable]
    command += [
        "--model",
        model,
        "--data",
        str(data_dir),
        "--test",
        "--test-batches",
        str(test_batches),
        "--batch-size",
        str(batch_size),
    ]
    if adapter_path is not None:
        command.extend(["--adapter-path", str(adapter_path)])

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "command": command}

    output = (completed.stdout or "") + (completed.stderr or "")
    loss = _parse_test_loss(output)
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "loss": loss,
        "command": command,
        "tail": output[-1500:],
    }


def compare_runs(
    baseline: dict[str, Any], tuned: dict[str, Any], *, min_delta: float = 0.02
) -> dict[str, Any]:
    """Decide whether the adapter actually improved the held-out loss."""
    before = baseline.get("loss")
    after = tuned.get("loss")
    if before is None or after is None:
        return {
            "improved": False,
            "reason": "loss_unavailable",
            "baseline_loss": before,
            "tuned_loss": after,
        }
    delta = float(before) - float(after)
    return {
        "improved": delta >= min_delta,
        "reason": "loss_improved" if delta >= min_delta else "no_meaningful_change",
        "baseline_loss": before,
        "tuned_loss": after,
        "delta": round(delta, 4),
        "min_delta": min_delta,
    }


DPO_EVAL_UNAVAILABLE = "eval_unavailable"


def _sum_response_logprobs(values: Any) -> float | None:
    """Sum token logprobs from one response. Missing or non-finite -> None."""
    if not isinstance(values, (list, tuple)) or not values:
        return None
    total = 0.0
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        total += number
    if not math.isfinite(total):
        return None
    return total


def dpo_loss_from_logprobs(
    *,
    chosen_logprobs: Any,
    rejected_logprobs: Any,
    beta: float = 0.1,
    tokenizer_id: str = "",
    base_model: str = "",
    ref_chosen_logprobs: Any = None,
    ref_rejected_logprobs: Any = None,
    require_reference: bool = False,
) -> dict[str, Any]:
    """Standard DPO loss from response-token logprobs. Never string similarity.

    기준 모델 로그확률이 있으면 표준 DPO 목적값(objective="dpo")을 계산한다.
    기준 값이 없으면 0으로 대체한 reference-free 값으로 계산하되 objective와
    reference_free로 그 사실을 표시한다. require_reference=True이면 기준 값이
    없을 때 수치를 만들지 않고 missing_reference_logprobs로 닫는다.
    """
    chosen = _sum_response_logprobs(chosen_logprobs)
    rejected = _sum_response_logprobs(rejected_logprobs)
    if chosen is None or rejected is None:
        return {
            "status": DPO_EVAL_UNAVAILABLE,
            "reason": "missing_logprobs",
            "loss": None,
            "tokenizer_id": tokenizer_id,
            "base_model": base_model,
        }
    if require_reference and (
        ref_chosen_logprobs is None or ref_rejected_logprobs is None
    ):
        # 표준 DPO는 같은 토크나이저·기준 모델의 응답 토큰 로그확률을 요구한다.
        # 기준 모델 값이 없으면 0으로 대체하지 않고 평가 불가로 닫는다.
        return {
            "status": DPO_EVAL_UNAVAILABLE,
            "reason": "missing_reference_logprobs",
            "loss": None,
            "tokenizer_id": tokenizer_id,
            "base_model": base_model,
            "require_reference": True,
            "reference_model": "none",
            "reference_free": False,
            "objective": "dpo",
        }
    chosen_ref = _sum_response_logprobs(ref_chosen_logprobs) if ref_chosen_logprobs is not None else 0.0
    rejected_ref = _sum_response_logprobs(ref_rejected_logprobs) if ref_rejected_logprobs is not None else 0.0
    if ref_chosen_logprobs is not None and chosen_ref is None:
        return {
            "status": DPO_EVAL_UNAVAILABLE,
            "reason": "missing_logprobs",
            "loss": None,
            "tokenizer_id": tokenizer_id,
            "base_model": base_model,
        }
    if ref_rejected_logprobs is not None and rejected_ref is None:
        return {
            "status": DPO_EVAL_UNAVAILABLE,
            "reason": "missing_logprobs",
            "loss": None,
            "tokenizer_id": tokenizer_id,
            "base_model": base_model,
        }
    if chosen_ref is None:
        chosen_ref = 0.0
    if rejected_ref is None:
        rejected_ref = 0.0
    # L = -log σ(β [(log πθ(yw)-log πref(yw)) - (log πθ(yl)-log πref(yl))])
    delta = float(beta) * ((chosen - chosen_ref) - (rejected - rejected_ref))
    if delta >= 0:
        loss = math.log1p(math.exp(-delta))
    else:
        loss = -delta + math.log1p(math.exp(delta))
    return {
        "status": "ok",
        "reason": "dpo_logprob",
        "loss": loss,
        "delta": (chosen - chosen_ref) - (rejected - rejected_ref),
        "beta": float(beta),
        "tokenizer_id": tokenizer_id,
        "base_model": base_model,
        "require_reference": bool(require_reference),
        "reference_model": (
            "given"
            if ref_chosen_logprobs is not None and ref_rejected_logprobs is not None
            else "none"
        ),
        "reference_free": not (
            ref_chosen_logprobs is not None and ref_rejected_logprobs is not None
        ),
        "objective": (
            "dpo"
            if ref_chosen_logprobs is not None and ref_rejected_logprobs is not None
            else "reference_free_preference"
        ),
    }


def evaluate_preference_pairs(
    pairs: Sequence[dict[str, Any]],
    *,
    tokenizer_id: str,
    base_model: str,
    beta: float = 0.1,
    require_reference: bool = False,
) -> dict[str, Any]:
    """Evaluate preferred/dispreferred pairs. Missing logprobs are unavailable."""
    evaluated = 0
    unavailable = 0
    losses: list[float] = []
    reports: list[dict[str, Any]] = []
    for pair in pairs:
        report = dpo_loss_from_logprobs(
            chosen_logprobs=pair.get("chosen_logprobs") or pair.get("preferred_logprobs"),
            rejected_logprobs=pair.get("rejected_logprobs") or pair.get("dispreferred_logprobs"),
            beta=beta,
            tokenizer_id=tokenizer_id,
            base_model=base_model,
            ref_chosen_logprobs=pair.get("ref_chosen_logprobs"),
            ref_rejected_logprobs=pair.get("ref_rejected_logprobs"),
            require_reference=require_reference,
        )
        provenance = {
            "pair_id": str(pair.get("pair_id") or ""),
            "source": str(pair.get("source") or ""),
            "preferred": str(pair.get("preferred") or pair.get("chosen") or ""),
            "dispreferred": str(pair.get("dispreferred") or pair.get("rejected") or ""),
        }
        report = {**report, **provenance}
        reports.append(report)
        if report["status"] == DPO_EVAL_UNAVAILABLE:
            unavailable += 1
            continue
        evaluated += 1
        if report.get("loss") is not None:
            losses.append(float(report["loss"]))
    mean_loss = sum(losses) / len(losses) if losses else None
    objectives = {str(report.get("objective") or "") for report in reports}
    return {
        "status": "ok" if evaluated else DPO_EVAL_UNAVAILABLE,
        "evaluated": evaluated,
        "unavailable": unavailable,
        "mean_loss": mean_loss,
        "tokenizer_id": tokenizer_id,
        "base_model": base_model,
        "require_reference": bool(require_reference),
        "reference_free": "reference_free_preference" in objectives,
        "string_similarity_used": False,
        "pairs": reports,
    }


# ---------------------------------------------------------------------------
# DPO 응답 토큰 로그확률 수집 (2026-09-22)
#
# 로컬 MLX 게이트웨이는 요청에 logprobs=true를 주면 모델이 직접 생성한 토큰의
# 로그확률을 돌려준다. 다만 echo 모드를 구현하지 않고 assistant prefill도
# 이어쓰기로 처리하지 않으므로, 이미 저장된 임의의 응답 문자열은 채점할 수 없다.
# 그래서 아래 수집 경로는 실제로 생성된 토큰의 로그확률만 기록하고, 값이 없으면
# 문자열 유사도로 대체하지 않고 평가 불가로 닫는다.
# ---------------------------------------------------------------------------

CAPTURE_METHOD = "generation_response_token_logprob"
CAPTURE_MAX_RESPONSE_BYTES = 256 * 1024
CAPTURE_TIMEOUT_SECONDS = 45.0
CAPTURE_HARD_TIMEOUT_SECONDS = 90.0
CAPTURE_MAX_MESSAGES = 16
CAPTURE_MAX_MESSAGE_CHARS = 4_000
CAPTURE_MAX_MODEL_CHARS = 512
CAPTURE_DEFAULT_MAX_TOKENS = 96
CAPTURE_MAX_TOKENS_CEILING = 512
CAPTURE_PREVIEW_CHARS = 240
CAPTURE_MAX_SAMPLES = 8
CAPTURE_MAX_TEMPERATURE = 2.0
SCORING_PROBE_MARKER = "OPENKAKAO_ECHO_PROBE"
SCORING_PROBE_SUFFIX = " alpha beta gamma delta"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost"})


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """로컬 게이트웨이 밖으로 요청이 튀지 않게 리다이렉트를 거부한다."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


def _open_local_only(request: urllib.request.Request, *, timeout: float):
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _RejectRedirects(),
    )
    return opener.open(request, timeout=timeout)


def _normalize_gateway_base_url(value: Any) -> str | None:
    """loopback http 게이트웨이만 허용하고 /v1 형태로 정규화한다."""
    try:
        parsed = urllib.parse.urlsplit(str(value or "").strip())
    except ValueError:
        return None
    if parsed.scheme != "http" or parsed.hostname not in _LOOPBACK_HOSTS:
        return None
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return None
    if parsed.path.rstrip("/") not in ("", "/v1"):
        return None
    port = f":{parsed.port}" if parsed.port else ""
    return f"http://{parsed.hostname}{port}/v1"


def _sanitize_capture_messages(messages: Any) -> list[dict[str, str]] | None:
    """역할·길이를 제한한 chat 메시지 목록. 형식이 어긋나면 None."""
    if not isinstance(messages, (list, tuple)) or not messages:
        return None
    if len(messages) > CAPTURE_MAX_MESSAGES:
        return None
    cleaned: list[dict[str, str]] = []
    for message in messages:
        if not isinstance(message, dict):
            return None
        role = str(message.get("role") or "").strip().lower()
        if role not in ("system", "user", "assistant"):
            return None
        content = message.get("content")
        if not isinstance(content, str):
            return None
        trimmed = content.strip()
        if not trimmed:
            return None
        cleaned.append({"role": role, "content": trimmed[:CAPTURE_MAX_MESSAGE_CHARS]})
    return cleaned


def _capture_failure(reason: str, *, model: str = "") -> dict[str, Any]:
    return {
        "status": DPO_EVAL_UNAVAILABLE,
        "reason": reason,
        "logprobs": None,
        "sum": None,
        "token_count": 0,
        "model": model,
        "response_text": "",
        "capture_method": CAPTURE_METHOD,
        "string_similarity_used": False,
    }


def _post_local_json(
    url: str, payload: dict[str, Any], *, timeout: float
) -> tuple[dict[str, Any] | None, str]:
    """로컬 게이트웨이에 한 번 POST하고 (본문, 오류코드)를 돌려준다."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer local",
        },
    )
    try:
        with _open_local_only(request, timeout=timeout) as response:
            raw = response.read(CAPTURE_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        return None, f"http_{getattr(exc, 'code', 0)}"
    except Exception as exc:
        return None, f"gateway_{type(exc).__name__}"
    if len(raw) > CAPTURE_MAX_RESPONSE_BYTES:
        return None, "response_too_large"
    try:
        parsed = json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError):
        return None, "malformed_response"
    if not isinstance(parsed, dict):
        return None, "malformed_response"
    return parsed, ""


def _bounded_timeout(timeout: Any) -> float | None:
    try:
        value = float(timeout)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return min(value, CAPTURE_HARD_TIMEOUT_SECONDS)


def capture_response_logprobs(
    *,
    base_url: str,
    model: str,
    messages: Any,
    max_tokens: int = CAPTURE_DEFAULT_MAX_TOKENS,
    timeout: float = CAPTURE_TIMEOUT_SECONDS,
    temperature: float = 0.0,
) -> dict[str, Any]:
    """로컬 게이트웨이에서 응답을 생성하고 실제 토큰 로그확률을 기록한다."""
    gateway = _normalize_gateway_base_url(base_url)
    if gateway is None:
        return _capture_failure("non_loopback_gateway")
    safe_model = str(model or "").strip()
    if not safe_model or len(safe_model) > CAPTURE_MAX_MODEL_CHARS:
        return _capture_failure("invalid_model")
    cleaned = _sanitize_capture_messages(messages)
    if cleaned is None:
        return _capture_failure("invalid_messages", model=safe_model)
    try:
        token_budget = int(max_tokens)
    except (TypeError, ValueError):
        return _capture_failure("invalid_max_tokens", model=safe_model)
    if token_budget < 1 or token_budget > CAPTURE_MAX_TOKENS_CEILING:
        return _capture_failure("invalid_max_tokens", model=safe_model)
    effective_timeout = _bounded_timeout(timeout)
    if effective_timeout is None:
        return _capture_failure("invalid_timeout", model=safe_model)
    try:
        effective_temperature = float(temperature)
    except (TypeError, ValueError):
        return _capture_failure("invalid_temperature", model=safe_model)
    if (
        not math.isfinite(effective_temperature)
        or effective_temperature < 0.0
        or effective_temperature > CAPTURE_MAX_TEMPERATURE
    ):
        return _capture_failure("invalid_temperature", model=safe_model)

    payload = {
        "model": safe_model,
        "messages": cleaned,
        "max_tokens": token_budget,
        "temperature": effective_temperature,
        "logprobs": True,
        "top_logprobs": 1,
    }
    body, error = _post_local_json(
        f"{gateway}/chat/completions", payload, timeout=effective_timeout
    )
    if body is None:
        return _capture_failure(error or "malformed_response", model=safe_model)
    try:
        choice = body["choices"][0]
        content = str(choice["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError, AttributeError):
        return _capture_failure("malformed_response", model=safe_model)
    if not content:
        return _capture_failure("empty_completion", model=safe_model)
    logprob_block = choice.get("logprobs")
    entries = (
        logprob_block.get("content") if isinstance(logprob_block, dict) else None
    )
    if not isinstance(entries, list) or not entries:
        return _capture_failure("logprobs_absent", model=safe_model)
    values: list[Any] = []
    for entry in entries:
        if not isinstance(entry, dict):
            return _capture_failure("malformed_logprobs", model=safe_model)
        values.append(entry.get("logprob"))
    total = _sum_response_logprobs(values)
    if total is None:
        return _capture_failure("non_finite_logprobs", model=safe_model)
    return {
        "status": "ok",
        "reason": "dpo_logprob_capture",
        "logprobs": [float(value) for value in values],
        "sum": total,
        "token_count": len(values),
        "model": safe_model,
        "response_text": content[:CAPTURE_PREVIEW_CHARS],
        "capture_method": CAPTURE_METHOD,
        "string_similarity_used": False,
    }


def probe_response_scoring(
    *,
    base_url: str,
    model: str,
    timeout: float = CAPTURE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """게이트웨이가 이미 저장된 응답 문자열을 채점할 수 있는지 실측한다.

    표준 DPO는 같은 응답 문자열에 대한 정책·기준 모델 로그확률을 모두 요구한다.
    생성 전용 엔드포인트는 그중 한쪽만 줄 수 있으므로, echo 지원 여부를 실제
    요청으로 확인해 보고서에 근거로 남긴다.
    """
    base = {
        "mode": "completions_echo",
        "string_similarity_used": False,
        "probe_executed": False,
    }
    gateway = _normalize_gateway_base_url(base_url)
    if gateway is None:
        return {**base, "status": DPO_EVAL_UNAVAILABLE, "reason": "non_loopback_gateway", "supports_response_scoring": False}
    safe_model = str(model or "").strip()
    if not safe_model or len(safe_model) > CAPTURE_MAX_MODEL_CHARS:
        return {**base, "status": DPO_EVAL_UNAVAILABLE, "reason": "invalid_model", "supports_response_scoring": False}
    effective_timeout = _bounded_timeout(timeout)
    if effective_timeout is None:
        return {**base, "status": DPO_EVAL_UNAVAILABLE, "reason": "invalid_timeout", "supports_response_scoring": False}
    marker = f"{SCORING_PROBE_MARKER}{SCORING_PROBE_SUFFIX}"
    body, error = _post_local_json(
        f"{gateway}/completions",
        {
            "model": safe_model,
            "prompt": marker,
            "max_tokens": 1,
            "temperature": 0.0,
            "echo": True,
            "logprobs": 1,
        },
        timeout=effective_timeout,
    )
    if body is None:
        return {**base, "status": DPO_EVAL_UNAVAILABLE, "reason": error or "malformed_response", "supports_response_scoring": False}
    try:
        echoed = str(body["choices"][0]["text"] or "")
    except (KeyError, IndexError, TypeError, AttributeError):
        return {**base, "status": DPO_EVAL_UNAVAILABLE, "reason": "malformed_response", "supports_response_scoring": False}
    supported = SCORING_PROBE_MARKER.lower() in echoed.lower()
    return {
        **base,
        "status": "ok",
        "reason": "echo_supported" if supported else "echo_unsupported",
        "supports_response_scoring": supported,
        "probe_executed": True,
        "echo_preview": echoed[:CAPTURE_PREVIEW_CHARS],
    }


def _attribute_pair_logprobs(
    pair: dict[str, Any], samples: list[dict[str, Any]]
) -> tuple[Any, Any, str]:
    """샘플과 저장된 선호 라벨로 chosen/rejected 로그확률을 정한다."""
    chosen = pair.get("chosen_logprobs") or pair.get("preferred_logprobs")
    rejected = pair.get("rejected_logprobs") or pair.get("dispreferred_logprobs")
    if chosen is not None and rejected is not None:
        return chosen, rejected, "stored_logprobs"
    by_text = {str(sample.get("response_text") or ""): sample for sample in samples}
    preferred = str(pair.get("preferred") or pair.get("chosen") or "").strip()
    dispreferred = str(pair.get("dispreferred") or pair.get("rejected") or "").strip()
    if preferred and dispreferred and preferred in by_text and dispreferred in by_text:
        return (
            by_text[preferred]["logprobs"],
            by_text[dispreferred]["logprobs"],
            "sample_text_match",
        )
    return None, None, "unattributed"


def capture_preference_pair_logprobs(
    pairs: Sequence[dict[str, Any]],
    *,
    base_url: str,
    model: str,
    max_tokens: int = CAPTURE_DEFAULT_MAX_TOKENS,
    timeout: float = CAPTURE_TIMEOUT_SECONDS,
    samples: int = 2,
    temperature: float = 0.7,
    tokenizer_id: str = "",
    reference: dict[str, dict[str, Any]] | None = None,
    require_reference: bool = True,
) -> dict[str, Any]:
    """프롬프트를 실제로 샘플링해 로그확률을 모으고 표준 DPO로 평가한다.

    기준 모델 로그확률(reference)이 없으면 표준 DPO 수치는 만들지 않고
    평가 불가로 닫는다. 값이 없는 쌍은 문자열 유사도로 채우지 않는다.
    """
    return _run_pair_capture(
        pairs,
        base_url=base_url,
        model=model,
        max_tokens=max_tokens,
        timeout=timeout,
        samples=samples,
        temperature=temperature,
        tokenizer_id=tokenizer_id,
        reference=reference,
        require_reference=require_reference,
    )


def sample_prompt_candidates(
    prompt: str,
    *,
    base_url: str,
    model: str,
    samples: int = 2,
    max_tokens: int = CAPTURE_DEFAULT_MAX_TOKENS,
    timeout: float = CAPTURE_TIMEOUT_SECONDS,
    temperature: float = 0.7,
) -> dict[str, Any]:
    """한 프롬프트의 온폴리시 후보와 실제 로그확률을 저장 가능한 형태로 돌려준다."""
    base = {
        "capture_method": CAPTURE_METHOD,
        "string_similarity_used": False,
        "samples_requested": 0,
    }
    try:
        sample_budget = int(samples)
    except (TypeError, ValueError):
        return {**base, "status": DPO_EVAL_UNAVAILABLE, "reason": "invalid_samples", "candidates": []}
    if sample_budget < 1 or sample_budget > CAPTURE_MAX_SAMPLES:
        return {**base, "status": DPO_EVAL_UNAVAILABLE, "reason": "invalid_samples", "candidates": []}
    safe_prompt = str(prompt or "").strip()
    if not safe_prompt:
        return {**base, "status": DPO_EVAL_UNAVAILABLE, "reason": "empty_prompt", "candidates": []}
    candidates: list[dict[str, Any]] = []
    reasons: list[str] = []
    for _ in range(sample_budget):
        result = capture_response_logprobs(
            base_url=base_url,
            model=model,
            messages=[{"role": "user", "content": safe_prompt[:CAPTURE_MAX_MESSAGE_CHARS]}],
            max_tokens=max_tokens,
            timeout=timeout,
            temperature=temperature,
        )
        if result.get("status") != "ok":
            reasons.append(str(result.get("reason") or ""))
            continue
        candidates.append(
            {
                "response_text": result.get("response_text"),
                "logprobs": result.get("logprobs"),
                "sum": result.get("sum"),
                "token_count": result.get("token_count"),
                "capture_method": CAPTURE_METHOD,
            }
        )
    return {
        **base,
        "status": "ok" if candidates else DPO_EVAL_UNAVAILABLE,
        "reason": "" if candidates else (reasons[0] if reasons else "insufficient_samples"),
        "samples_requested": sample_budget,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


def _run_pair_capture(
    pairs: Sequence[dict[str, Any]],
    *,
    base_url: str,
    model: str,
    max_tokens: int,
    timeout: float,
    samples: int,
    temperature: float,
    tokenizer_id: str,
    reference: dict[str, dict[str, Any]] | None,
    require_reference: bool,
) -> dict[str, Any]:
    base = {
        "capture_method": CAPTURE_METHOD,
        "string_similarity_used": False,
        "base_url": str(base_url or ""),
        "model": str(model or ""),
        "sample_temperature": temperature,
        "samples_per_prompt": 0,
    }
    try:
        sample_budget = int(samples)
    except (TypeError, ValueError):
        return {**base, "status": DPO_EVAL_UNAVAILABLE, "reason": "invalid_samples", "pairs": []}
    if sample_budget < 1 or sample_budget > CAPTURE_MAX_SAMPLES:
        return {**base, "status": DPO_EVAL_UNAVAILABLE, "reason": "invalid_samples", "pairs": []}
    if not isinstance(pairs, (list, tuple)) or not pairs:
        return {**base, "status": DPO_EVAL_UNAVAILABLE, "reason": "no_pairs", "pairs": []}
    if any(not isinstance(pair, dict) for pair in pairs):
        return {**base, "status": DPO_EVAL_UNAVAILABLE, "reason": "invalid_pair", "pairs": []}
    if reference is not None and not isinstance(reference, dict):
        return {**base, "status": DPO_EVAL_UNAVAILABLE, "reason": "invalid_reference", "pairs": []}

    prepared: list[dict[str, Any]] = []
    captured = 0
    unavailable = 0
    for index, pair in enumerate(pairs):
        prompt = str(pair.get("prompt") or "").strip()
        pair_id = str(pair.get("pair_id") or f"pair-{index}")
        if not prompt:
            unavailable += 1
            prepared.append({**pair, "pair_id": pair_id, "capture_status": DPO_EVAL_UNAVAILABLE, "capture_reason": "empty_prompt"})
            continue
        usable: list[dict[str, Any]] = []
        last_reason = ""
        for _ in range(sample_budget):
            captured_result = capture_response_logprobs(
                base_url=base_url,
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                timeout=timeout,
                temperature=temperature,
            )
            if captured_result.get("status") == "ok":
                usable.append(captured_result)
            else:
                last_reason = str(captured_result.get("reason") or "")
        if len(usable) < 2:
            unavailable += 1
            prepared.append(
                {
                    **pair,
                    "pair_id": pair_id,
                    "capture_status": DPO_EVAL_UNAVAILABLE,
                    "capture_reason": last_reason or "insufficient_samples",
                }
            )
            continue
        chosen, rejected, attributed_by = _attribute_pair_logprobs(pair, usable)
        ref_pair = (reference or {}).get(pair_id) or {}
        if chosen is None or rejected is None:
            unavailable += 1
            prepared.append(
                {
                    **pair,
                    "pair_id": pair_id,
                    "capture_status": DPO_EVAL_UNAVAILABLE,
                    "capture_reason": "unattributed_samples",
                    "attributed_by": attributed_by,
                }
            )
            continue
        captured += 1
        prepared.append(
            {
                **pair,
                "pair_id": pair_id,
                "capture_status": "ok",
                "attributed_by": attributed_by,
                "chosen_logprobs": chosen,
                "rejected_logprobs": rejected,
                "ref_chosen_logprobs": ref_pair.get("ref_chosen_logprobs"),
                "ref_rejected_logprobs": ref_pair.get("ref_rejected_logprobs"),
            }
        )

    evaluation = evaluate_preference_pairs(
        [pair for pair in prepared if pair.get("capture_status") == "ok"],
        tokenizer_id=tokenizer_id,
        base_model=model,
        require_reference=require_reference,
    )
    return {
        **base,
        "status": "ok" if captured else DPO_EVAL_UNAVAILABLE,
        "captured": captured,
        "unavailable": unavailable,
        "samples_per_prompt": sample_budget,
        "require_reference": bool(require_reference),
        "reference_supplied": bool(reference),
        "pairs": prepared,
        "evaluation": evaluation,
    }


def load_preference_pairs(path: Path, *, max_bytes: int = 8 * 1024 * 1024) -> tuple[list[dict[str, Any]], str]:
    """선호 쌍 JSONL을 읽는다. 없거나 읽을 수 없으면 (빈 목록, 오류코드)."""
    try:
        if not path.is_file():
            return [], "pairs_missing"
        if path.stat().st_size > max_bytes:
            return [], "pairs_too_large"
        text = path.read_text(encoding="utf-8")
    except OSError:
        return [], "pairs_unreadable"
    except UnicodeDecodeError:
        return [], "pairs_not_utf8"
    pairs: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            item = json.loads(stripped)
        except ValueError:
            continue
        if isinstance(item, dict):
            pairs.append(item)
    if not pairs:
        return [], "pairs_empty"
    return pairs, ""


def build_dpo_report(
    *,
    pairs_path: Path,
    base_url: str,
    model: str,
    ref_pairs_path: Path | None = None,
    max_tokens: int = CAPTURE_DEFAULT_MAX_TOKENS,
    samples: int = 2,
    timeout: float = CAPTURE_TIMEOUT_SECONDS,
    tokenizer_id: str = "",
) -> dict[str, Any]:
    """JSONL 선호 쌍을 읽어 로그확률 수집·기준 채점 가능성·DPO 평가를 묶는다."""
    pairs, error = load_preference_pairs(pairs_path)
    if error:
        return {
            "status": DPO_EVAL_UNAVAILABLE,
            "reason": error,
            "capture_method": CAPTURE_METHOD,
            "string_similarity_used": False,
            "pairs": [],
        }
    reference: dict[str, dict[str, Any]] | None = None
    if ref_pairs_path is not None:
        ref_pairs, ref_error = load_preference_pairs(ref_pairs_path)
        if ref_error:
            return {
                "status": DPO_EVAL_UNAVAILABLE,
                "reason": ref_error,
                "capture_method": CAPTURE_METHOD,
                "string_similarity_used": False,
                "pairs": [],
            }
        reference = {}
        for ref_pair in ref_pairs:
            ref_id = str(ref_pair.get("pair_id") or "")
            if not ref_id:
                continue
            reference[ref_id] = {
                "ref_chosen_logprobs": ref_pair.get("chosen_logprobs"),
                "ref_rejected_logprobs": ref_pair.get("rejected_logprobs"),
            }
    probe = probe_response_scoring(base_url=base_url, model=model, timeout=timeout)
    capture = capture_preference_pair_logprobs(
        pairs,
        base_url=base_url,
        model=model,
        max_tokens=max_tokens,
        timeout=timeout,
        samples=samples,
        tokenizer_id=tokenizer_id,
        reference=reference,
    )
    return {
        "status": capture.get("status"),
        "capture_method": CAPTURE_METHOD,
        "string_similarity_used": False,
        "base_url": base_url,
        "model": model,
        "scoring_probe": probe,
        "capture": {
            "status": capture.get("status"),
            "reason": capture.get("reason", ""),
            "captured": capture.get("captured", 0),
            "unavailable": capture.get("unavailable", 0),
            "samples_per_prompt": capture.get("samples_per_prompt", 0),
            "sample_temperature": capture.get("sample_temperature"),
        },
        "evaluation": capture.get("evaluation", {}),
        "pairs": capture.get("pairs", []),
    }


def run_training(plan: TrainingPlan, *, timeout: float = 7200.0) -> dict[str, Any]:
    """Execute the training command and report how it went."""
    executable = _resolve_lora_executable()
    if not executable:
        return {"ok": False, "error": "mlx_lm_not_installed", "command": plan.command}
    command = list(plan.command)
    if executable.endswith("mlx_lm"):
        command = [executable, "lora"] + command[1:]
    else:
        command[0] = executable

    started = time.time()
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "command": command,
            "elapsed_seconds": round(time.time() - started, 2),
        }
    output = (completed.stdout or "") + (completed.stderr or "")
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "command": command,
        "elapsed_seconds": round(time.time() - started, 2),
        "tail": output[-2000:],
    }


def _default_golden(state_root: Path) -> Path:
    return state_root / "golden" / "reply-golden.jsonl"


def _default_state_root() -> Path:
    override = os.environ.get("OPENKAKAO_STATE_ROOT")
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library/Application Support/openkakao/bujamentor"


def _resolve_model(explicit: str | None) -> str:
    if explicit:
        return explicit
    try:
        from scripts.auto_reply_ondevice import detect_hardware, recommend_ondevice_setup

        return recommend_ondevice_setup(detect_hardware()).recommended_model
    except Exception:
        return FLASH_NEXT_MODEL_ID


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="골든 데이터셋으로 LoRA/DoRA 파인튜닝을 준비하고 실행합니다.",
    )
    parser.add_argument("--state-root", type=Path, default=None, help="자동답변 상태 루트")
    parser.add_argument("--golden", type=Path, default=None, help="골든 JSONL 경로")
    parser.add_argument("--data-dir", type=Path, default=None, help="학습 데이터 출력 디렉터리")
    parser.add_argument("--adapter-path", type=Path, default=None, help="어댑터 출력 경로")
    parser.add_argument("--model", default=None, help="학습 기반 모델 (기본: 온디바이스 권장)")
    parser.add_argument("--iters", type=int, default=0, help="학습 반복 수 (0=자동)")
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--learning-rate", type=float, default=1e-5)
    parser.add_argument("--num-layers", type=int, default=16)
    parser.add_argument("--max-seq-length", type=int, default=1024)
    parser.add_argument(
        "--fine-tune-type", choices=("lora", "dora"), default="lora", help="경량 학습 방식"
    )
    parser.add_argument("--valid-ratio", type=float, default=DEFAULT_VALID_RATIO)
    parser.add_argument("--test-ratio", type=float, default=DEFAULT_TEST_RATIO)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--session-gap",
        type=float,
        default=DEFAULT_SESSION_GAP,
        help="한 세션으로 묶을 최대 시간 간격(초). 같은 그룹은 한 split에만 들어감",
    )
    parser.add_argument("--prepare-only", action="store_true", help="데이터만 만들고 학습은 안 함")
    parser.add_argument("--train", action="store_true", help="실제 학습을 실행")
    parser.add_argument("--evaluate", action="store_true", help="학습 전후 손실을 비교")
    parser.add_argument("--test-batches", type=int, default=4)
    parser.add_argument("--json", action="store_true", help="결과를 JSON으로 출력")
    parser.add_argument("--dpo-pairs", type=Path, default=None, help="선호 쌍 JSONL 경로")
    parser.add_argument(
        "--dpo-ref-pairs",
        type=Path,
        default=None,
        help="기준 모델 로그확률을 담은 JSONL 경로(선택)",
    )
    parser.add_argument(
        "--dpo-capture",
        action="store_true",
        help="로컬 게이트웨이에서 응답 토큰 로그확률을 실제로 수집",
    )
    parser.add_argument(
        "--dpo-base-url",
        default="http://127.0.0.1:11234/v1",
        help="로컬 MLX 게이트웨이 base URL",
    )
    parser.add_argument("--dpo-model", default=None, help="수집에 쓸 모델 ID (기본: --model)")
    parser.add_argument("--dpo-max-tokens", type=int, default=CAPTURE_DEFAULT_MAX_TOKENS)
    parser.add_argument("--dpo-samples", type=int, default=2)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    state_root = (args.state_root or _default_state_root()).expanduser()
    golden = (args.golden or _default_golden(state_root)).expanduser()
    data_dir = (args.data_dir or state_root / "finetune" / "data").expanduser()
    adapter_path = (args.adapter_path or state_root / "finetune" / "adapter").expanduser()
    model = _resolve_model(args.model)

    summary, _ = prepare_dataset(
        golden_path=golden,
        data_dir=data_dir,
        valid_ratio=args.valid_ratio,
        test_ratio=args.test_ratio,
        seed=args.seed,
        session_gap=args.session_gap,
    )
    plan = plan_training(
        model=model,
        data_dir=data_dir,
        adapter_path=adapter_path,
        train_examples=summary["train"],
        iters=args.iters,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        num_layers=args.num_layers,
        max_seq_length=args.max_seq_length,
        fine_tune_type=args.fine_tune_type,
    )
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "dataset": summary,
        "plan": plan.to_dict(),
    }

    if args.evaluate and not args.prepare_only:
        report["baseline"] = evaluate_adapter(
            model=model,
            adapter_path=None,
            data_dir=data_dir,
            test_batches=args.test_batches,
            batch_size=args.batch_size,
        )

    if args.train and not args.prepare_only:
        report["training"] = run_training(plan)
        if args.evaluate:
            report["tuned"] = evaluate_adapter(
                model=model,
                adapter_path=adapter_path,
                data_dir=data_dir,
                test_batches=args.test_batches,
                batch_size=args.batch_size,
            )
            if "baseline" in report:
                report["comparison"] = compare_runs(report["baseline"], report["tuned"])

    if args.dpo_pairs is not None:
        # 로컬 게이트웨이가 생성한 토큰의 실제 로그확률만 사용한다. 값이 없으면
        # build_dpo_report가 평가 불가로 닫고 문자열 유사도로 대체하지 않는다.
        report["dpo"] = build_dpo_report(
            pairs_path=args.dpo_pairs.expanduser(),
            base_url=args.dpo_base_url,
            model=args.dpo_model or model,
            ref_pairs_path=(
                args.dpo_ref_pairs.expanduser() if args.dpo_ref_pairs is not None else None
            ),
            max_tokens=args.dpo_max_tokens,
            samples=args.dpo_samples,
            tokenizer_id=args.dpo_model or model,
        )

    report_path = data_dir / "finetune-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            f"학습 데이터: {summary['train']} train · {summary['valid']} valid · "
            f"{summary['test']} test"
        )
        print(
            f"  세션 그룹 {summary['groups']}개 (train {summary['train_groups']} · "
            f"valid {summary['valid_groups']} · test {summary['test_groups']}) · "
            f"중복 prompt {summary['duplicate_prompts']}건 제외"
        )
        print(f"  → {data_dir}")
        print(f"기반 모델: {plan.model}")
        print(f"계획: {plan.fine_tune_type} · {plan.iters} iters · batch {plan.batch_size}")
        if "training" in report:
            status = "완료" if report["training"].get("ok") else "실패"
            print(f"학습 {status} ({report['training'].get('elapsed_seconds', 0)}초)")
        if "comparison" in report:
            cmp_ = report["comparison"]
            print(f"손실: {cmp_['baseline_loss']} → {cmp_['tuned_loss']} (개선 {cmp_['improved']})")
        if "dpo" in report:
            dpo = report["dpo"]
            evaluation = dpo.get("evaluation") or {}
            probe = dpo.get("scoring_probe") or {}
            captured = dpo.get("capture") or {}
            print(
                f"DPO: {dpo.get('status')} · 수집 {captured.get('captured', 0)}쌍 · "
                f"평가 {evaluation.get('evaluated', 0)} · 평가 불가 {evaluation.get('unavailable', 0)}"
            )
            print(
                "  응답 문자열 채점 지원: "
                f"{probe.get('supports_response_scoring')} ({probe.get('reason', '')})"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
