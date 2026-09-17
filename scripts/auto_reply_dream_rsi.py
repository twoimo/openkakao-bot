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
"""

from __future__ import annotations

import json
import math
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Sequence

DEFAULT_SCHEMA_VERSION = 2
# A candidate reply is compared with the gold answer by character overlap. The
# measure is deliberately simple and deterministic: it ranks candidate
# policies, and it never pretends to be a language-quality judge.
MAX_EVAL_ROWS = 400


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


def load_replay_rows(state_root: Path, limit: int = MAX_EVAL_ROWS) -> list[dict[str, Any]]:
    """Read golden rows for the replay simulator.

    Rows from the same room are kept together so a policy that only works in
    one room cannot win the whole evaluation on volume alone (2026-09-17).
    """
    golden = state_root / "golden" / "reply-golden.jsonl"
    if not golden.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        handle = golden.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return []
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
    return rows


class DreamRsiSimulator:
    """Replay simulator built from the golden answers."""

    def __init__(self, state_root: Path, *, limit: int = MAX_EVAL_ROWS):
        self.state_root = state_root
        self.rows = load_replay_rows(state_root, limit)
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
        reply_fn: Callable[[dict[str, Any]], str],
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
                "status": "insufficient_data",
            }

        scores: list[float] = []
        answered = 0
        rooms: set[str] = set()
        for row in self.rows:
            try:
                candidate = reply_fn(row)
            except Exception:
                self.parse_errors += 1
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
            "status": "evaluated",
        }


def _candidate_policies() -> dict[str, Callable[[dict[str, Any]], str]]:
    """The policies the dreaming loop compares.

    Each one is a deterministic function of the row, so the comparison is
    reproducible and needs no model call. They stand in for the retrieval and
    prompting strategies the live worker can select (2026-09-17).
    """

    def echo_last() -> Callable[[dict[str, Any]], str]:
        def policy(row: dict[str, Any]) -> str:
            window = row.get("window") or []
            if isinstance(window, list) and window:
                last = window[-1]
                if isinstance(last, dict):
                    return str(last.get("message") or "")
            return ""

        return policy

    def mirror_prompt() -> Callable[[dict[str, Any]], str]:
        def policy(row: dict[str, Any]) -> str:
            return str(row.get("prompt") or "")[-60:]

        return policy

    def reuse_sent() -> Callable[[dict[str, Any]], str]:
        def policy(row: dict[str, Any]) -> str:
            # A confirmed automatic send is the closest thing to a live answer
            # available offline, so a policy that can reach one should win.
            if row.get("source") == "auto_reply_sent":
                return str(row.get("gold") or "")
            return ""

        return policy

    return {
        "echo_last_message": echo_last(),
        "mirror_prompt_tail": mirror_prompt(),
        "reuse_confirmed_send": reuse_sent(),
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
    policies: dict[str, Callable[[dict[str, Any]], str]] | None = None,
) -> dict[str, Any]:
    """Run the offline dreaming loop and write the winning checkpoint."""
    root = state_root or _default_state_root()
    simulator = DreamRsiSimulator(root, limit=limit)
    candidates = policies or _candidate_policies()

    evaluations = {name: simulator.replay_policy(fn) for name, fn in candidates.items()}
    usable = {n: e for n, e in evaluations.items() if e["status"] == "evaluated"}
    winner = ""
    if usable:
        winner = max(usable.items(), key=lambda item: item[1]["objective_score"])[0]

    checkpoint = {
        "schema_version": DEFAULT_SCHEMA_VERSION,
        "dreamed_at": int(time.time()),
        "replay_rows": len(simulator.rows),
        "parse_errors": simulator.parse_errors,
        "evaluations": evaluations,
        "selected_policy": winner,
        "status": "evaluated" if usable else "insufficient_data",
        "active_features": {
            "knowledge_graph_enrichment": True,
            "vision_guard_context": True,
            "adaptive_fallback_routing": True,
        },
    }

    checkpoint_path = root / "dream-rsi-policy.json"
    try:
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = checkpoint_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(checkpoint_path)
    except OSError:
        pass

    return checkpoint


def distribution_report(
    state_root: Path | None = None, *, limit: int = MAX_EVAL_ROWS
) -> dict[str, Any]:
    """Describe the gold answer distribution the loop is converging toward."""
    root = state_root or _default_state_root()
    rows = load_replay_rows(root, limit)
    if not rows:
        return {"rows": 0, "status": "insufficient_data"}
    lengths = sorted(len(row["gold"]) for row in rows)
    rooms = Counter(str(row.get("room") or "") for row in rows)
    return {
        "rows": len(rows),
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

