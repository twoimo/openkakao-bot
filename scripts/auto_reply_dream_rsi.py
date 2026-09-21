"""Dream-RSI: recursive self-improvement against the golden dataset.

Based on Zheng et al., 2026 (https://dream-rsi.com/):

1. Online Explore: real execution traces recorded in the evidence ledger.
2. Construct Replay Simulator: past turns and decisions become a replay set
   that costs nothing to re-run.
3. Dreaming-based Policy Improvement: candidate policies are scored offline
   against the replay set instead of against the live room.
4. Redeploy: the winning policy is written to a checkpoint for the next turn.

The replay set is the golden dataset, not the evidence ledger alone. The gold
answers are what the operator actually typed, so a candidate reply can be
scored against a real target rather than a hardcoded number. When no golden
rows exist the module reports insufficient_data instead of inventing a score
(2026-09-17).

Only rows a person wrote or explicitly approved reach the scoring step. The
worker's own sent replies also land in the golden file, and scoring candidates
against those would let the loop converge on what the model already produced
instead of on the operator's style, so they are counted and dropped unless the
caller asks for them (2026-09-19).
"""

from __future__ import annotations

import json
import math
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable

try:  # Tests import scripts.*, a flat script run imports the sibling module.
    from scripts.auto_reply_golden_dataset import (
        QUALITY_MODEL_GENERATED,
        gold_quality,
        is_gold_quality,
    )
except ImportError:  # pragma: no cover - flat import path
    from auto_reply_golden_dataset import (
        QUALITY_MODEL_GENERATED,
        gold_quality,
        is_gold_quality,
    )

DEFAULT_SCHEMA_VERSION = 2
# Which gold a run was allowed to learn from, recorded in the checkpoint.
GOLD_SOURCE_HUMAN_ONLY = "human_only"
GOLD_SOURCE_HUMAN_AND_MODEL = "human_and_model"
# A candidate reply is compared with the gold answer by character overlap. The
# measure is deliberately simple and deterministic: it ranks candidate
# policies, and it never pretends to be a language-quality judge.
MAX_EVAL_ROWS = 400
# The paper's replay guarantee compares every candidate with the current policy.
# Keep that incumbent in the candidate set so the fixed-history comparison exists.
INCUMBENT_POLICY = "echo_last_message"

# The replay metric is a character-bigram cosine over gold answers. It selects
# dreaming-loop candidates; it is not a preference loss. The DPO path in
# scripts/auto_reply_finetune.py computes the preference loss from response
# token logprobs and never substitutes this similarity for a missing number
# (2026-09-22).
REPLAY_SIMILARITY_METRIC = "character_bigram_cosine_replay"
STRING_SIMILARITY_SCOPE = "replay_answer_distribution"
PREFERENCE_EVALUATION_PATH = "separate_dpo_logprob_path"


def _char_ngrams(text: str, n: int = 2) -> Counter:
    compact = "".join(text.split())
    if len(compact) < n:
        return Counter({compact: 1}) if compact else Counter()
    return Counter(compact[i : i + n] for i in range(len(compact) - n + 1))


def answer_similarity(candidate: str, gold: str) -> float:
    """Cosine similarity of character bigrams, in 0..1.

    Korean is agglutinative, so a word-level measure misses the endings that
    carry the tone. Character bigrams survive the spacing and particle
    differences that dominate KakaoTalk replies (2026-09-17).
    """
    left = _char_ngrams(candidate)
    right = _char_ngrams(gold)
    if not left or not right:
        return 0.0
    common = set(left) & set(right)
    dot = sum(left[g] * right[g] for g in common)
    norm_left = math.sqrt(sum(v * v for v in left.values()))
    norm_right = math.sqrt(sum(v * v for v in right.values()))
    if norm_left == 0.0 or norm_right == 0.0:
        return 0.0
    return dot / (norm_left * norm_right)


def load_replay_rows(
    state_root: Path,
    limit: int = MAX_EVAL_ROWS,
    allow_model_gold: bool = False,
    counters: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Read golden rows for the replay simulator.

    Rows from the same room are kept together so a policy that only works in
    one room cannot win the whole evaluation on volume alone (2026-09-17).

    A row the worker sent itself carries the model's own output as its gold
    answer, so scoring candidates against it would grade the model on its own
    habits. Those rows are excluded unless allow_model_gold asks for them, and
    counters receives the excluded model-gold count plus the adopted row count
    (2026-09-19).
    """
    golden = state_root / "golden" / "reply-golden.jsonl"
    rows: list[dict[str, Any]] = []
    excluded_model_gold = 0
    try:
        handle = golden.open("r", encoding="utf-8", errors="replace")
    except OSError:
        # A missing or unreadable golden set is an empty replay set, not an
        # error for the caller (2026-09-17).
        handle = None
    if handle is not None:
        with handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                # A JSON line can be a bare string or list. Calling .get on it
                # raises and would abort the whole read (2026-09-17).
                if not isinstance(record, dict):
                    continue
                prompt = str(record.get("prompt") or "").strip()
                completion = str(record.get("completion") or "").strip()
                if not prompt or not completion:
                    continue
                # Golden files written before the quality field existed are
                # classified from their source, which is what the extractor
                # already does when it writes a row (2026-09-19).
                quality = gold_quality(record)
                if not is_gold_quality(quality):
                    model_gold = quality == QUALITY_MODEL_GENERATED
                    if not (allow_model_gold and model_gold):
                        if model_gold:
                            excluded_model_gold += 1
                        continue
                rows.append(
                    {
                        "prompt": prompt,
                        "gold": completion,
                        "room": str(record.get("room") or ""),
                        "source": str(record.get("source") or ""),
                        "window": record.get("window") or [],
                    }
                )
                if limit and len(rows) >= limit:
                    break
    if counters is not None:
        counters["gold_rows"] = counters.get("gold_rows", 0) + len(rows)
        counters["excluded_model_gold"] = (
            counters.get("excluded_model_gold", 0) + excluded_model_gold
        )
    return rows


class DreamRsiSimulator:
    """Replay simulator built from the golden answers."""

    def __init__(
        self,
        state_root: Path,
        *,
        limit: int = MAX_EVAL_ROWS,
        allow_model_gold: bool = False,
    ):
        counters: dict[str, int] = {}
        self.state_root = state_root
        self.rows = load_replay_rows(
            state_root, limit, allow_model_gold=allow_model_gold, counters=counters
        )
        # The two counts the checkpoint reports about the filter it applied.
        self.gold_rows = counters["gold_rows"]
        self.excluded_model_gold = counters["excluded_model_gold"]
        self.parse_errors = 0

    @property
    def traces(self) -> list[dict[str, Any]]:
        """Kept for callers that still read the old attribute name."""
        return [
            {
                "id": str(index),
                "inbound": row["prompt"],
                "historical_reply": row["gold"],
                "sent": row.get("source") == "auto_reply_sent",
                "room": row.get("room", ""),
            }
            for index, row in enumerate(self.rows)
        ]

    def replay_policy(
        self,
        reply_fn: Callable[[dict[str, Any]], Any],
        *,
        beta1: float = 0.05,
        beta2: float = 0.1,
    ) -> dict[str, Any]:
        """Score one candidate policy against the gold answers.

        Objective: V = mean(similarity) - beta1 * coverage_cost + beta2 * spread
        where coverage_cost penalises a policy that answers few rows and spread
        rewards it for working across rooms (2026-09-17).
        """
        if not self.rows:
            return {
                "objective_score": 0.0,
                "avg_similarity": 0.0,
                "max_similarity": 0.0,
                "evaluated_rows": 0,
                "answered_rows": 0,
                "room_spread": 0,
                "candidate_errors": {},
                "status": "insufficient_data",
            }

        scores: list[float] = []
        answered = 0
        rooms: set[str] = set()
        candidate_errors: Counter[str] = Counter()
        for row in self.rows:
            # Candidate policies only receive generation-time inputs. `gold` and
            # `source` are written after the reply was generated, so a policy
            # that read them would be scored on information the live worker
            # never had; the allowlist keeps them out (2026-09-19).
            policy_row = {
                "prompt": row.get("prompt", ""),
                "room": row.get("room", ""),
                "window": row.get("window") or [],
            }
            try:
                raw_candidate = reply_fn(policy_row)
            except Exception as exc:
                self.parse_errors += 1
                candidate_errors[f"exception:{type(exc).__name__}"] += 1
                candidate = ""
            else:
                if isinstance(raw_candidate, str):
                    candidate = raw_candidate
                else:
                    self.parse_errors += 1
                    candidate_errors[f"non_string:{type(raw_candidate).__name__}"] += 1
                    candidate = ""

            if candidate.strip():
                answered += 1
                rooms.add(str(row.get("room") or ""))
            scores.append(answer_similarity(candidate, row["gold"]))

        avg = sum(scores) / len(scores)
        best = max(scores)
        coverage_cost = 1.0 - (answered / len(scores))
        spread = len(rooms) / max(1, len({str(r.get("room") or "") for r in self.rows}))
        objective = avg - beta1 * coverage_cost + beta2 * spread * 0.1

        return {
            "objective_score": round(objective, 6),
            "avg_similarity": round(avg, 6),
            "max_similarity": round(best, 6),
            "evaluated_rows": len(scores),
            "answered_rows": answered,
            "room_spread": len(rooms),
            "candidate_errors": dict(sorted(candidate_errors.items())),
            "metric": REPLAY_SIMILARITY_METRIC,
            "string_similarity_used": True,
            "string_similarity_scope": STRING_SIMILARITY_SCOPE,
            "preference_evaluation": PREFERENCE_EVALUATION_PATH,
            "status": "evaluated",
        }


def _last_window_message(row: dict[str, Any]) -> str:
    """The newest message in the row's window, "" when there is none."""
    window = row.get("window") or []
    if isinstance(window, list) and window:
        last = window[-1]
        if isinstance(last, dict):
            return str(last.get("message") or "")
    return ""


def _longest_window_message(row: dict[str, Any]) -> str:
    """The longest message in the row's window, the earliest one on a tie."""
    window = row.get("window") or []
    if not isinstance(window, list):
        return ""
    longest = ""
    for entry in window:
        if not isinstance(entry, dict):
            continue
        message = str(entry.get("message") or "")
        if len(message) > len(longest):
            longest = message
    return longest


def _candidate_policies() -> dict[str, Callable[[dict[str, Any]], str]]:
    """The policies the dreaming loop compares.

    Each one is a deterministic function of the row, so the comparison is
    reproducible and needs no model call. They stand in for the retrieval and
    prompting strategies the live worker can select, and each one reads only
    what generation-time code had: the inbound prompt and the window of
    messages that preceded it (2026-09-19).
    """
    return {
        INCUMBENT_POLICY: _last_window_message,
        "mirror_prompt_tail": lambda row: str(row.get("prompt") or "")[-60:],
        "longest_window_message": _longest_window_message,
    }


def replay_guarantee(
    evaluations: dict[str, dict[str, Any]],
    selected: str,
    *,
    incumbent: str = INCUMBENT_POLICY,
) -> dict[str, Any]:
    """Report the paper's non-degradation check on the fixed replay set only.

    The guarantee applies when the evaluated candidate set includes the incumbent; caller-supplied sets may yield not_applicable.
    """
    incumbent_name = incumbent if isinstance(incumbent, str) and incumbent else INCUMBENT_POLICY
    selected_name = selected if isinstance(selected, str) else ""
    report: dict[str, Any] = {
        "incumbent_policy": incumbent_name,
        "incumbent_score": None,
        "incumbent_included": False,
        "selected_policy": selected_name,
        "selected_score": None,
        "non_degradation_on_replay": None,
        "status": "not_applicable",
        "scope": "fixed_replay_set_only",
        "scope_note": (
            "Guarantee covers the fixed replay history only and does not extend "
            "to future online performance."
        ),
    }

    if not selected_name:
        report["status"] = "insufficient_data"
        report["non_degradation_on_replay"] = False
        return report
    if not isinstance(evaluations, dict):
        return report

    incumbent_eval = evaluations.get(incumbent_name)
    report["incumbent_included"] = isinstance(incumbent_eval, dict)
    if not report["incumbent_included"]:
        return report
    selected_eval = evaluations.get(selected_name)
    if not isinstance(selected_eval, dict):
        return report

    def finite_score(entry: dict[str, Any]) -> float | None:
        raw = entry.get("objective_score")
        if not isinstance(raw, (int, float)) or isinstance(raw, bool):
            return None
        try:
            value = float(raw)
        except (OverflowError, TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None

    incumbent_value = finite_score(incumbent_eval)
    selected_value = finite_score(selected_eval)
    if incumbent_value is None or selected_value is None:
        return report
    report["incumbent_score"] = incumbent_value
    report["selected_score"] = selected_value
    report["non_degradation_on_replay"] = selected_value >= incumbent_value
    report["status"] = "verified"
    return report


def select_experiment_action(
    *,
    remaining_budget: int,
    last_status: str = "",
    tried: list[str] | tuple[str, ...] = (),
    candidates: list[str] | tuple[str, ...] = (),
    failure_axes: dict[str, int] | None = None,
) -> dict[str, Any]:
    """DREAM-RSI candidate selection / branch / stop. Not a preference scorer."""
    unused = [name for name in candidates if name not in set(tried)]
    axes = failure_axes or {}
    if remaining_budget <= 0:
        return {"action": "stop", "reason": "budget_exhausted", "next": ""}
    if last_status == "eval_unavailable":
        return {"action": "stop", "reason": "eval_unavailable", "next": ""}
    if not unused:
        return {"action": "stop", "reason": "candidate_exhausted", "next": ""}
    if int(axes.get("factuality", 0) or 0) >= 2:
        return {"action": "branch", "reason": "factuality_branch", "next": unused[0]}
    if int(axes.get("self_talk", 0) or 0) >= 2:
        return {"action": "branch", "reason": "self_talk_branch", "next": unused[0]}
    return {"action": "continue", "reason": "next_candidate", "next": unused[0]}


def run_fixed_budget_loop(
    *,
    candidates: list[str],
    evaluate_fn: Callable[[str], dict[str, Any]],
    budget: int,
) -> dict[str, Any]:
    """generate -> evaluate -> failure analysis -> next candidate. No live promote."""
    history: list[dict[str, Any]] = []
    tried: list[str] = []
    remaining = int(budget)
    last_status = ""
    failure_axes: dict[str, int] = {}
    while remaining > 0:
        decision = select_experiment_action(
            remaining_budget=remaining,
            last_status=last_status,
            tried=tried,
            candidates=candidates,
            failure_axes=failure_axes,
        )
        if decision["action"] == "stop":
            return {
                "status": "stopped",
                "reason": decision["reason"],
                "history": history,
                "promoted": False,
            }
        name = str(decision.get("next") or "")
        if not name:
            return {
                "status": "stopped",
                "reason": "empty_candidate",
                "history": history,
                "promoted": False,
            }
        remaining -= 1
        tried.append(name)
        report = evaluate_fn(name)
        last_status = str(report.get("status") or "")
        for axis in ("factuality", "self_talk", "tone", "grounding", "latency"):
            if report.get(axis) is False or report.get(f"{axis}_failed") is True:
                failure_axes[axis] = failure_axes.get(axis, 0) + 1
        history.append({"candidate": name, "decision": decision, "report": report})
        if last_status == "eval_unavailable":
            return {
                "status": "stopped",
                "reason": "eval_unavailable",
                "history": history,
                "promoted": False,
            }
    return {
        "status": "stopped",
        "reason": "budget_exhausted",
        "history": history,
        "promoted": False,
    }


def _default_state_root() -> Path:
    """Resolve the state root the same way the other scripts do."""
    override = os.environ.get("OPENKAKAO_STATE_ROOT")
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library/Application Support/openkakao/bujamentor"


def dream_policy_evaluation(
    state_root: Path | None = None,
    *,
    limit: int = MAX_EVAL_ROWS,
    policies: dict[str, Callable[[dict[str, Any]], Any]] | None = None,
    allow_model_gold: bool = False,
) -> dict[str, Any]:
    """Run the offline dreaming loop and write the winning checkpoint.

    A replay guarantee is verified only when the evaluated policies include the incumbent candidate.
    """
    root = state_root or _default_state_root()
    simulator = DreamRsiSimulator(root, limit=limit, allow_model_gold=allow_model_gold)
    candidates = _candidate_policies() if policies is None else policies

    evaluations = {name: simulator.replay_policy(fn) for name, fn in candidates.items()}
    usable = {n: e for n, e in evaluations.items() if e["status"] == "evaluated"}
    winner = ""
    if usable:
        winner = max(usable.items(), key=lambda item: item[1]["objective_score"])[0]
    guarantee = replay_guarantee(evaluations, winner)

    checkpoint = {
        "schema_version": DEFAULT_SCHEMA_VERSION,
        "dreamed_at": int(time.time()),
        "replay_rows": len(simulator.rows),
        # Which gold the score came from, so a later reader can tell a
        # human-gold result from one that included the worker's own replies
        # (2026-09-19).
        "gold_rows": simulator.gold_rows,
        "excluded_model_gold": simulator.excluded_model_gold,
        "gold_source_policy": (
            GOLD_SOURCE_HUMAN_AND_MODEL if allow_model_gold else GOLD_SOURCE_HUMAN_ONLY
        ),
        "parse_errors": simulator.parse_errors,
        "evaluations": evaluations,
        "selected_policy": winner,
        "replay_guarantee": guarantee,
        "metric": REPLAY_SIMILARITY_METRIC,
        "string_similarity_used": True,
        "string_similarity_scope": STRING_SIMILARITY_SCOPE,
        "preference_evaluation": PREFERENCE_EVALUATION_PATH,
        "status": "evaluated" if usable else "insufficient_data",
        "active_features": {name: True for name in candidates},
    }

    checkpoint_path = root / "dream-rsi-policy.json"
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = checkpoint_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(checkpoint_path)

    return checkpoint


def distribution_report(
    state_root: Path | None = None, *, limit: int = MAX_EVAL_ROWS
) -> dict[str, Any]:
    """Describe the gold answer distribution the loop is converging toward.

    The report reads the replay set through the same human-only filter as the
    evaluation, so a distribution the model wrote itself cannot pass for the
    operator's own answers (2026-09-19).
    """
    root = state_root or _default_state_root()
    counters: dict[str, int] = {}
    rows = load_replay_rows(root, limit, counters=counters)
    gold_filter = {
        "excluded_model_gold": counters["excluded_model_gold"],
        "gold_source_policy": GOLD_SOURCE_HUMAN_ONLY,
    }
    if not rows:
        return {"rows": 0, "status": "insufficient_data", **gold_filter}
    lengths = sorted(len(row["gold"]) for row in rows)
    rooms = Counter(str(row.get("room") or "") for row in rows)
    return {
        "rows": len(rows),
        **gold_filter,
        "length_p10": lengths[len(lengths) // 10],
        "length_median": lengths[len(lengths) // 2],
        "length_p90": lengths[len(lengths) * 9 // 10],
        "mean_length": round(sum(lengths) / len(lengths), 2),
        "rooms": dict(rooms.most_common(10)),
        "status": "measured",
    }


if __name__ == "__main__":
    result = dream_policy_evaluation()
    result["distribution"] = distribution_report()
    print(json.dumps(result, ensure_ascii=False, indent=2))
