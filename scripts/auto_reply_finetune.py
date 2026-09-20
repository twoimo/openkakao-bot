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
    return 0


if __name__ == "__main__":
    sys.exit(main())
