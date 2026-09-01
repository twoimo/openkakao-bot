#!/usr/bin/env python3
"""Offline RAG quality evaluator for AutoReply decisions.

Reads each room's ``reply-evidence.jsonl`` ledger (one record per scheduled
decision) and optionally joins the room queue for terminal status.  Computes
citation/recall style metrics per model cohort so retrieval behaviour can be
compared across models without re-running any model.

Stdlib only.  Read-only: never opens a database for writing.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

DEFAULT_STATE_ROOT = (
    Path.home() / "Library" / "Application Support" / "openkakao" / "bujamentor"
)


def load_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.is_file():
        return records
    for line in path.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _ids(values: Any) -> set[str]:
    if not isinstance(values, list):
        return set()
    return {str(item) for item in values}


def citation_metrics(record: dict[str, Any]) -> tuple[float, float]:
    """Return (citation_recall, pool_utilization) for one record.

    citation_recall   = used_evidence ∩ supplied_recent / used_evidence
    pool_utilization  = used_evidence ∩ supplied_recent / supplied_recent
    Non-recent citations (media:, timing:, reaction:) legitimately lower the
    first number; they are reported separately via composition counters.
    """
    used = _ids(record.get("evidence_ids"))
    supplied = {
        str(item.get("evidence_id"))
        for item in record.get("recent_conversation") or []
        if isinstance(item, dict) and item.get("evidence_id")
    }
    if not used:
        return (0.0, 0.0)
    hit = len(used & supplied)
    recall = hit / len(used)
    utilization = (hit / len(supplied)) if supplied else 0.0
    return (recall, utilization)


def evidence_composition(records: list[dict[str, Any]]) -> Counter:
    kinds: Counter = Counter()
    for record in records:
        for value in record.get("evidence_ids") or []:
            kind = str(value).split(":", 1)[0]
            kinds[kind] += 1
    return kinds


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 4) if values else None


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 4) if values else None


def cohort_metrics(records: list[dict[str, Any]], status_by_event: dict[str, str]) -> dict[str, Any]:
    recalls: list[float] = []
    utils: list[float] = []
    ctx_matches: list[float] = []
    style_matches: list[float] = []
    best_ctx: list[float] = []
    best_style: list[float] = []
    prior_sim: list[float] = []
    reply_lens: list[int] = []
    draft_counts: list[int] = []
    models: Counter = Counter()
    terminals: Counter = Counter()

    for record in records:
        model = str(record.get("model") or "none")
        models[model.split("/", 1)[-1]] += 1
        recall, utilization = citation_metrics(record)
        recalls.append(recall)
        utils.append(utilization)
        if isinstance(record.get("context_match_count"), (int, float)):
            ctx_matches.append(float(record["context_match_count"]))
        if isinstance(record.get("style_match_count"), (int, float)):
            style_matches.append(float(record["style_match_count"]))
        for key, bucket in (
            ("best_context_score", best_ctx),
            ("best_style_score", best_style),
            ("prior_similarity", prior_sim),
        ):
            if isinstance(record.get(key), (int, float)):
                bucket.append(float(record[key]))
        reply = str(record.get("reply") or "")
        if reply:
            reply_lens.append(len(reply))
        if isinstance(record.get("drafts"), list):
            draft_counts.append(len(record["drafts"]))
        event_id = str(record.get("event_id") or "")
        terminal = status_by_event.get(event_id)
        if terminal:
            terminals[terminal] += 1

    return {
        "n": len(records),
        "models": dict(models.most_common()),
        "citation_recall_mean": _mean(recalls),
        "citation_recall_median": _median(recalls),
        "pool_utilization_mean": _mean(utils),
        "context_match_mean": _mean(ctx_matches),
        "style_match_mean": _mean(style_matches),
        "best_context_score_mean": _mean(best_ctx),
        "best_style_score_mean": _mean(best_style),
        "prior_similarity_mean": _mean(prior_sim),
        "reply_len_p50": _median([float(v) for v in reply_lens]),
        "drafts_mean": _mean([float(v) for v in draft_counts]),
        "terminal_status": dict(terminals.most_common()),
    }


def per_model(records: list[dict[str, Any]], status_by_event: dict[str, str]) -> dict[str, Any]:
    groups: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[str(record.get("model") or "none").split("/", 1)[-1]].append(record)
    return {
        name: cohort_metrics(items, status_by_event)
        for name, items in sorted(groups.items())
    }


def load_terminal_status(queue_path: Path) -> dict[str, str]:
    import sqlite3

    if not queue_path.is_file():
        return {}
    connection = sqlite3.connect(f"file:{queue_path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "select event_id, status from reply_jobs where status in ('sent','skipped')"
        ).fetchall()
    finally:
        connection.close()
    return {str(row[0]): str(row[1]) for row in rows}


def evaluate_room(room_dir: Path, *, join_queue: bool) -> dict[str, Any]:
    ledger = room_dir / "reply-evidence.jsonl"
    records = load_records(ledger)
    status_by_event: dict[str, str] = {}
    if join_queue:
        status_by_event = load_terminal_status(room_dir / "reply-queue.sqlite3")
    composition = evidence_composition(records)
    return {
        "room": room_dir.name,
        "records": len(records),
        "overall": cohort_metrics(records, status_by_event),
        "per_model": per_model(records, status_by_event),
        "evidence_kinds": dict(composition.most_common()),
        "citation_positions": citation_position_analysis(records),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state-root",
        type=Path,
        default=DEFAULT_STATE_ROOT,
        help="AutoReply state root (default: bujamentor)",
    )
    parser.add_argument(
        "--room",
        action="append",
        dest="rooms",
        help="Room directory name under rooms/. Repeatable. Default: all.",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    arguments = parser.parse_args()

    rooms_root = arguments.state_root / "rooms"
    room_names = arguments.rooms or sorted(
        p.name for p in rooms_root.iterdir() if p.is_dir() and p.name.isdigit()
    ) if rooms_root.is_dir() else []

    reports = [
        evaluate_room(rooms_root / name, join_queue=True)
        for name in room_names
        if (rooms_root / name / "reply-evidence.jsonl").is_file()
    ]

    if arguments.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
        return 0

    for report in reports:
        print(f"== room {report['room']} ({report['records']} records) ==")
        overall = report["overall"]
        print(f"  models: {overall['models']}")
        print(
            f"  citation_recall mean={overall['citation_recall_mean']} "
            f"median={overall['citation_recall_median']}"
        )
        print(f"  pool_utilization mean={overall['pool_utilization_mean']}")
        print(
            f"  context_match={overall['context_match_mean']} "
            f"style_match={overall['style_match_mean']} "
            f"best_ctx={overall['best_context_score_mean']} "
            f"best_style={overall['best_style_score_mean']} "
            f"prior_sim={overall['prior_similarity_mean']}"
        )
        print(
            f"  reply_len_p50={overall['reply_len_p50']} "
            f"drafts={overall['drafts_mean']}"
        )
        print(f"  terminal: {overall['terminal_status']}")
        print(f"  evidence_kinds: {report['evidence_kinds']}")
        print("  -- per model --")
        for name, metrics in report["per_model"].items():
            print(
                f"  {name:<28} n={metrics['n']:<4} "
                f"recall={metrics['citation_recall_mean']} "
                f"ctx={metrics['context_match_mean']} "
                f"len_p50={metrics['reply_len_p50']} "
                f"terminal={metrics['terminal_status']}"
            )
        positions = report.get("citation_positions") or {}
        print(
            f"  citations={positions.get('citations', 0)} "
            f"window_len_mean={positions.get('window_len_mean')}"
        )
        if positions.get("recall_at_k"):
            recall = " ".join(
                f"@{k}={v}" for k, v in positions["recall_at_k"].items()
            )
            precision = " ".join(
                f"@{k}={v}" for k, v in (positions.get("precision_at_k") or {}).items()
            )
            print(f"  recall@k {recall}")
            print(f"  precision@k {precision}")
        print(
            f"  rerank empty={positions.get('rerank_empty')} "
            f"scored={positions.get('rerank_scored')} "
            f"fallback={positions.get('rerank_fallbacks')}"
        )
    return 0



def citation_position_analysis(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute recall@k and precision@k from cited recent:* evidence.

    recent_conversation is chronological (oldest first), so position from
    the end is age in messages (0 = newest).

    recall@k  = cited recent:* items whose age < k / all cited recent:* items
    precision@k = mean over records of (cited items among the last k window
                  messages) / min(k, window_len)
    """
    ks = (3, 5, 7, 10, 13)
    positions: list[int] = []
    window_lens: list[int] = []
    precision_hits: dict[int, list[float]] = {k: [] for k in ks}
    rerank_empty = 0
    rerank_scored = 0
    rerank_fallbacks: Counter = Counter()
    for record in records:
        scores = record.get("rerank_scores")
        if isinstance(scores, list) and scores:
            rerank_scored += 1
        else:
            rerank_empty += 1
        fallback = str(record.get("fallback") or "none")
        rerank_fallbacks[fallback] += 1
        rc = record.get("recent_conversation") or []
        if not isinstance(rc, list) or not rc:
            continue
        window_lens.append(len(rc))
        newest_ids = [
            str(item.get("evidence_id"))
            for item in reversed(rc)
            if isinstance(item, dict) and item.get("evidence_id")
        ]
        id_pos = {eid: idx for idx, eid in enumerate(newest_ids)}
        used_recent = {eid for eid in _ids(record.get("evidence_ids")) if eid.startswith("recent:")}
        for eid in used_recent:
            if eid in id_pos:
                positions.append(id_pos[eid])
        for k in ks:
            window_k = newest_ids[:k]
            if not window_k:
                continue
            hit = sum(1 for eid in window_k if eid in used_recent)
            precision_hits[k].append(hit / len(window_k))
    total = len(positions)
    if not total:
        return {
            "citations": 0,
            "window_len_mean": _mean([float(v) for v in window_lens]),
            "rerank_empty": rerank_empty,
            "rerank_scored": rerank_scored,
            "rerank_fallbacks": dict(rerank_fallbacks.most_common()),
        }
    dist: Counter = Counter(positions)
    recall_at_k = {}
    precision_at_k = {}
    for k in ks:
        kept = sum(1 for p in positions if p < k)
        recall_at_k[k] = round(kept / total, 4)
        precision_at_k[k] = _mean(precision_hits[k])
    return {
        "citations": total,
        "window_len_mean": _mean([float(v) for v in window_lens]),
        "age_distribution": {str(k): v for k, v in sorted(dist.items())},
        "recall_at_k": recall_at_k,
        "precision_at_k": precision_at_k,
        "rerank_empty": rerank_empty,
        "rerank_scored": rerank_scored,
        "rerank_fallbacks": dict(rerank_fallbacks.most_common()),
    }


if __name__ == "__main__":
    raise SystemExit(main())
