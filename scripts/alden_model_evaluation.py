"""Versioned offline preference evaluation; never trains or promotes weights."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import math
import os
import re
import secrets
import stat
import time
import unicodedata
from pathlib import Path

try:
    from scripts import alden_dpo_scorer as scorer
except ImportError:
    import alden_dpo_scorer as scorer

MAX_DATASET_BYTES = 256 * 1024
MAX_RECEIPT_BYTES = 256 * 1024
SPLITS = ("train", "validation", "test")
LABEL_SOURCES = ("verified_context", "verified_reference", "human_feedback")


class EvaluationError(ValueError):
    pass


def _json(raw):
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise EvaluationError("duplicate_json_key")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(EvaluationError("non_finite_json")))


def _text(value, limit=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise EvaluationError("invalid_dataset_text")
    return value


def _normalized(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def load_dataset(path: Path):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise EvaluationError("dataset_not_regular_file")
        raw = handle.read(MAX_DATASET_BYTES + 1)
    if len(raw) > MAX_DATASET_BYTES:
        raise EvaluationError("dataset_too_large")
    dataset = _json(raw)
    if (not isinstance(dataset, dict) or type(dataset.get("schema_version")) is not int
            or dataset["schema_version"] != 1):
        raise EvaluationError("invalid_dataset_schema")
    rows = dataset.get("pairs")
    if not isinstance(rows, list) or not 3 <= len(rows) <= scorer.MAX_PAIRS:
        raise EvaluationError("invalid_pair_count")
    splits = {name: [] for name in SPLITS}
    ids, prompts, groups = set(), set(), {}
    for row in rows:
        if not isinstance(row, dict) or row.get("split") not in splits:
            raise EvaluationError("invalid_split")
        pair_id = _text(row.get("pair_id"), 128)
        prompt = _text(row.get("prompt"))
        chosen = _text(row.get("chosen"))
        rejected = _text(row.get("rejected"))
        group = _normalized(_text(row.get("source_group"), 128))
        label_source = row.get("label_source")
        _text(row.get("label_reference"), 1000)
        if label_source not in LABEL_SOURCES or chosen == rejected:
            raise EvaluationError("unverified_preference_label")
        normalized = _normalized(prompt)
        if pair_id in ids or normalized in prompts:
            raise EvaluationError("duplicate_pair_or_prompt")
        if group in groups and groups[group] != row["split"]:
            raise EvaluationError("source_group_split_leak")
        ids.add(pair_id); prompts.add(normalized); groups[group] = row["split"]
        splits[row["split"]].append({"pair_id": pair_id, "prompt": prompt,
                                      "chosen": chosen, "rejected": rejected})
    if any(not rows for rows in splits.values()):
        raise EvaluationError("empty_split")
    return splits, hashlib.sha256(raw).hexdigest()


def source_fingerprint():
    digest = hashlib.sha256()
    root = Path(__file__).parent
    for name in ("alden_model_evaluation.py", "alden_dpo_scorer.py",
                 "auto_reply_finetune.py", "auto-reply-worker.py",
                 "alden_dpo_adapter.py", "alden_dpo_training.py"):
        digest.update(name.encode() + b"\0" + (root / name).read_bytes())
    return digest.hexdigest()


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode()


def _private_file(fd):
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
        raise EvaluationError("unsafe_evaluation_file")


@contextlib.contextmanager
def _locked_root(path):
    if not path.is_absolute():
        raise EvaluationError("evaluation_root_not_absolute")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_NOFOLLOW | os.O_CLOEXEC
    root_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | flags)
    lock_fd = None
    try:
        info = os.fstat(root_fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise EvaluationError("unsafe_evaluation_root")
        lock_fd = os.open("evaluation.lock", os.O_RDWR | os.O_CREAT | flags, 0o600, dir_fd=root_fd)
        _private_file(lock_fd)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise EvaluationError("evaluation_in_progress") from exc
        yield root_fd
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
        os.close(root_fd)


def _scoring_identity(scoring, key, pairs):
    identity = (isinstance(scoring, dict) and scoring.get("status") == "ok"
            and scoring.get("policy_sha256") == key["policy_sha256"]
            and scoring.get("reference_sha256") == key["reference_sha256"]
            and scoring.get("policy_adapter_sha256") == key.get("policy_adapter_sha256")
            and type(scoring.get("evaluated")) is int and scoring["evaluated"] == len(pairs)
            and scoring.get("requested_checkpoint_format") == key["checkpoint_format"]
            and scoring.get("scoring_method") == scorer.SCORING_METHOD
            and scoring.get("reference_frozen") is True
            and scoring.get("string_similarity_used") is False
            and isinstance(scoring.get("pairs"), list)
            and [row.get("pair_id") if isinstance(row, dict) else None for row in scoring["pairs"]]
            == [row["pair_id"] for row in pairs]
            and type(scoring.get("mean_loss")) in (int, float)
            and math.isfinite(scoring["mean_loss"]) and scoring["mean_loss"] >= 0)
    if not identity:
        return False
    losses = []
    for row in scoring["pairs"]:
        names = ("policy_chosen_logprob", "policy_rejected_logprob",
                 "reference_chosen_logprob", "reference_rejected_logprob", "delta", "loss")
        if any(type(row.get(name)) not in (int, float) or not math.isfinite(row[name]) for name in names):
            return False
        if any(row[name] > 0 for name in names[:4]):
            return False
        if any(type(row.get(name)) is not int or not 1 <= row[name] <= scorer.MAX_RESPONSE_TOKENS
               for name in ("chosen_tokens", "rejected_tokens")):
            return False
        delta = ((row["policy_chosen_logprob"] - row["reference_chosen_logprob"])
                 - (row["policy_rejected_logprob"] - row["reference_rejected_logprob"]))
        margin = -key["beta"] * delta
        loss = max(margin, 0.0) + math.log1p(math.exp(-abs(margin)))
        if not math.isclose(row["delta"], delta, rel_tol=1e-12, abs_tol=1e-12):
            return False
        if not math.isclose(row["loss"], loss, rel_tol=1e-12, abs_tol=1e-12):
            return False
        if not key.get("policy_adapter_sha256") and key["policy_sha256"] == key["reference_sha256"] and (
                row["policy_chosen_logprob"] != row["reference_chosen_logprob"]
                or row["policy_rejected_logprob"] != row["reference_rejected_logprob"]
                or row["delta"] != 0.0 or row["loss"] != math.log(2.0)):
            return False
        losses.append(row["loss"])
    return math.isclose(scoring["mean_loss"], sum(losses)/len(losses), rel_tol=1e-12, abs_tol=1e-12)


def _cached(root_fd, name, key, splits):
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                     dir_fd=root_fd)
    except FileNotFoundError:
        return None
    try:
        _private_file(fd)
        raw = os.read(fd, MAX_RECEIPT_BYTES + 1)
        if len(raw) > MAX_RECEIPT_BYTES:
            raise EvaluationError("cached_receipt_too_large")
        envelope = _json(raw)
        receipt = envelope["receipt"]
        if (envelope["sha256"] != hashlib.sha256(_encode(receipt)).hexdigest()
                or receipt["key"] != key):
            raise EvaluationError("cached_receipt_invalid")
        if receipt["phase"] == "unavailable":
            return None
        if (receipt["phase"] != "evaluated"
                or receipt.get("weights_trained") is not False or receipt.get("model_promoted") is not False
                or receipt.get("split_counts") != {k: len(v) for k, v in splits.items()}
                or not _scoring_identity(receipt.get("scoring"), key, splits[key["split"]])):
            raise EvaluationError("cached_receipt_invalid")
        return receipt
    finally:
        os.close(fd)


def _publish(root_fd, path, name, receipt, token, deadline):
    envelope = {"receipt": receipt, "sha256": hashlib.sha256(_encode(receipt)).hexdigest()}
    raw = _encode(envelope)
    if len(raw) > MAX_RECEIPT_BYTES:
        raise EvaluationError("receipt_too_large")
    temporary = ".evaluation-" + secrets.token_hex(16)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o600, dir_fd=root_fd)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw); handle.flush(); os.fsync(handle.fileno())
        guard = token.commit_guard() if token is not None else contextlib.nullcontext()
        with guard:
            actual, held = path.lstat(), os.fstat(root_fd)
            if (actual.st_dev, actual.st_ino) != (held.st_dev, held.st_ino):
                raise EvaluationError("evaluation_root_changed")
            scorer._check_abort(token.is_cancelled if token is not None else None, deadline)
            os.replace(temporary, name, src_dir_fd=root_fd, dst_dir_fd=root_fd)
        os.fsync(root_fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=root_fd)
        except FileNotFoundError:
            pass


def evaluate_version(*, dataset_path, policy_dir, reference_dir, version,
                     evaluation_root, checkpoint_format, beta=0.1, final_test=False,
                     token=None, deadline_seconds=120, score_fn=None, admission=None,
                     policy_adapter_dir=None):
    """Evaluate validation per version; final held-out test is explicit opt-in."""
    if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", version):
        raise EvaluationError("invalid_version")
    if type(beta) not in (int, float) or not math.isfinite(beta) or not 0 < beta <= 1:
        raise EvaluationError("invalid_beta")
    if (type(deadline_seconds) not in (int, float) or not math.isfinite(deadline_seconds)
            or not 1 <= deadline_seconds <= 300):
        raise EvaluationError("invalid_deadline")
    if type(final_test) is not bool:
        raise EvaluationError("invalid_final_test")
    started = time.monotonic(); deadline = started + deadline_seconds
    abort = token.is_cancelled if token is not None else None
    scorer._check_abort(abort, deadline)
    splits, dataset_sha = load_dataset(Path(dataset_path))
    policy = scorer._checkpoint(policy_dir, "missing_policy_dir")
    reference = scorer._checkpoint(reference_dir, "missing_reference_dir")
    checkpoint_format = scorer._checkpoint_format(checkpoint_format)
    split = "test" if final_test else "validation"
    with _locked_root(Path(evaluation_root)) as root_fd:
        policy_sha = scorer.checkpoint_fingerprint(policy, abort, deadline)
        reference_sha = policy_sha if policy == reference else scorer.checkpoint_fingerprint(reference, abort, deadline)
        adapter_sha = None
        if policy_adapter_dir is not None:
            try:
                from scripts.alden_dpo_adapter import fingerprint
            except ImportError:
                from alden_dpo_adapter import fingerprint
            adapter_sha = fingerprint(policy_adapter_dir, base_sha256=policy_sha,
                                      checkpoint_format=checkpoint_format, abort_check=abort, deadline=deadline)
        key = {"schema_version": 1, "version": version, "source_sha256": source_fingerprint(),
               "dataset_sha256": dataset_sha, "policy_sha256": policy_sha,
               "reference_sha256": reference_sha, "checkpoint_format": checkpoint_format,
               "policy_adapter_sha256": adapter_sha,
               "split": split, "beta": beta, "runtime_versions": scorer._versions()}
        identifier = hashlib.sha256(_encode(key)).hexdigest()
        name = identifier + ".json"
        cached = _cached(root_fd, name, key, splits)
        scorer._check_abort(abort, deadline)
        if cached is not None:
            return {**cached, "cached": True, "evaluation_id": identifier,
                    "original_evaluation_runtime_seconds": cached["runtime_seconds"],
                    "runtime_seconds": round(time.monotonic() - started, 6)}
        if admission is not None:
            admission(policy, reference)
        fn = score_fn or scorer.score_local_dpo_pairs
        options = {} if policy_adapter_dir is None else {"policy_adapter_dir":policy_adapter_dir}
        scoring = fn(splits[split], policy_dir=policy, reference_dir=reference,
                     beta=beta, checkpoint_format=checkpoint_format,
                     abort_check=abort, deadline_seconds=max(.001, deadline - time.monotonic()), **options)
        scorer._check_abort(abort, deadline)
        if scoring.get("status") == "ok" and not _scoring_identity(scoring, key, splits[split]):
            raise EvaluationError("scoring_identity_mismatch")
        if source_fingerprint() != key["source_sha256"]:
            raise EvaluationError("evaluation_source_changed")
        receipt = {"key": key, "phase": "evaluated" if scoring.get("status") == "ok" else "unavailable",
                   "scoring": scoring, "split_counts": {k: len(v) for k, v in splits.items()},
                   "weights_trained": False, "model_promoted": False,
                   "runtime_seconds": round(time.monotonic() - started, 6)}
        _publish(root_fd, Path(evaluation_root), name, receipt, token, deadline)
        return {**receipt, "cached": False, "evaluation_id": identifier}


def memory_admission(policy, reference):
    try:
        from scripts.auto_reply_ondevice import detect_memory_budget
    except ImportError:
        from auto_reply_ondevice import detect_memory_budget
    largest = max(sum(path.stat().st_size for path in checkpoint.glob("model*.safetensors"))
                  for checkpoint in (policy, reference))
    required = max(40 * 1024**3, int(largest * 1.5) + 8 * 1024**3)
    if detect_memory_budget().free_bytes < required:
        raise EvaluationError("evaluation_memory_budget_low")


def run_cli(args):
    try:
        from scripts.alden_abort import AbortController, AldenCancelled
    except ImportError:
        from alden_abort import AbortController, AldenCancelled
    support = Path.home() / "Library/Application Support/openkakao"
    legacy, modern = support / "bujamentor", support / "auto-reply"
    state_root = args.state_root or (modern if (modern / "enrollment.json").is_file()
                                    or not (legacy / "enrollment.json").is_file() else legacy)
    token = AbortController(state_root).token()
    try:
        result = evaluate_version(
            dataset_path=args.model_evaluation_dataset, policy_dir=args.dpo_policy_dir,
            reference_dir=args.dpo_reference_dir, version=args.evaluation_version,
            evaluation_root=args.evaluation_root, checkpoint_format=args.dpo_checkpoint_format,
            final_test=args.evaluation_final_test, deadline_seconds=args.evaluation_deadline,
            beta=args.dpo_beta, token=token, admission=memory_admission,
            policy_adapter_dir=args.dpo_adapter_dir)
    except (EvaluationError, scorer.ScorerError, AldenCancelled, OSError, ValueError, KeyError, TypeError) as exc:
        reason = str(exc) if isinstance(exc, (EvaluationError, scorer.ScorerError, AldenCancelled)) else type(exc).__name__
        result = {"phase": "unavailable", "reason": reason, "cached": False,
                  "weights_trained": False, "model_promoted": False}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result["phase"] == "evaluated" else 2
