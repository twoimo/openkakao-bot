#!/usr/bin/env python3
"""Measure the six AHP criteria from live artifacts instead of asserting them.

The first version of this file returned a hard-coded dict of 0.98-0.99 scores.
The 6 Pro review rejected that: a score that cannot move when the evidence
moves is not a measurement. This version reads the per-room reply ledger
(rooms/<chat_id>/reply-evidence.jsonl) and the worker status file, counts
real numerators and denominators, and emits `insufficient_data` when a
criterion has no observations in the window.

Every criterion reports score, numerator, denominator, sample_count, period,
source_event_ids and scorer_revision so a reviewer can recompute it.

Criteria (see ahp_naturalness.py for weights and verdict rule):
  1. evidence_delivery    retrieved evidence actually reached the prompt
  2. register_style       reply text respects the 최연우 style budget
  3. intervention_timing  one reply per burst, no stale-backlog sends
  4. content_fit          replies used retrieved context instead of guessing
  5. reliability          generation succeeded without an unrecovered failure
  6. observability        each turn carries an event id, model and timing

Usage:
  python3 scripts/ahp_measure_live.py [--days 7] [--chat-id 417780809780519]
                                      [--json] [--out PATH]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import statistics
import sys
from pathlib import Path
from typing import Any

SCORER_REVISION = "live-ledger-2"
DEFAULT_STATE_ROOT = Path(
    os.environ.get(
        "OPENKAKAO_AUTO_REPLY_STATE_ROOT",
        Path.home() / "Library" / "Application Support" / "openkakao" / "bujamentor",
    )
)
DEFAULT_CHAT_ID = os.environ.get("OPENKAKAO_TARGET_CHAT_ID", "417780809780519")

# Replies longer than this stop sounding like a chat message. The style budget
# is deliberately generous: the goal is to catch runaway paragraphs, not to
# score wording.
STYLE_MAX_CHARS = 60


def parse_stamp(value: Any) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        stamp = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.timezone.utc)
    return stamp


def read_ledger(path: Path) -> list[dict[str, Any]]:
    """Read the ledger, skipping malformed lines instead of aborting."""
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            rows.append(record)
    return rows


def read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def window_rows(
    rows: list[dict[str, Any]], start: dt.datetime
) -> tuple[list[dict[str, Any]], list[str]]:
    """Keep rows inside the window and return the event ids that fed a score."""
    kept: list[dict[str, Any]] = []
    event_ids: list[str] = []
    for row in rows:
        stamp = parse_stamp(row.get("recorded_at"))
        if stamp is None or stamp < start:
            continue
        kept.append(row)
        event = str(row.get("event_id") or "").strip()
        if event and event not in event_ids:
            event_ids.append(event)
    return kept, event_ids


def ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def criterion(
    name: str,
    score: float | None,
    numerator: int | None,
    denominator: int | None,
    sample_count: int,
    period: str,
    event_ids: list[str],
    note: str,
) -> dict[str, Any]:
    """One criterion result. A missing denominator is insufficient_data."""
    if score is None or denominator is None or denominator <= 0 or sample_count <= 0:
        return {
            "score": None,
            "numerator": numerator,
            "denominator": denominator,
            "sample_count": sample_count,
            "period": period,
            "source_event_ids": event_ids[:200],
            "scorer_revision": SCORER_REVISION,
            "state": "insufficient_data",
            "note": note,
            "criterion": name,
        }
    return {
        "score": round(max(0.0, min(1.0, score)), 4),
        "numerator": numerator,
        "denominator": denominator,
        "sample_count": sample_count,
        "period": period,
        "source_event_ids": event_ids[:200],
        "scorer_revision": SCORER_REVISION,
        "state": "measured",
        "note": note,
        "criterion": name,
    }


def measure_evidence_delivery(
    rows: list[dict[str, Any]], period: str, event_ids: list[str]
) -> dict[str, Any]:
    """Retrieved evidence reached the prompt.

    A turn counts only when it recorded both how many evidence ids were
    retrieved and how many were placed into the prompt. Turns that never
    retrieved anything are excluded: they cannot fail this criterion.
    """
    considered = 0
    delivered = 0
    for row in rows:
        retrieved = row.get("retrieved_evidence_ids")
        prompt = row.get("prompt_evidence_ids")
        if not isinstance(retrieved, int) or not isinstance(prompt, int):
            continue
        if retrieved <= 0:
            continue
        considered += 1
        if prompt >= retrieved:
            delivered += 1
    score = ratio(delivered, considered)
    return criterion(
        "evidence_delivery",
        score,
        delivered,
        considered,
        considered,
        period,
        event_ids,
        "검색된 근거가 프롬프트까지 도달한 턴의 비율",
    )


def measure_register_style(
    rows: list[dict[str, Any]], period: str, event_ids: list[str]
) -> dict[str, Any]:
    """Sent replies stayed inside the chat-message length budget."""
    replies = [
        str(row.get("reply") or "").strip()
        for row in rows
        if str(row.get("status") or "") == "sent"
    ]
    replies = [text for text in replies if text]
    if not replies:
        return criterion(
            "register_style", None, 0, 0, 0, period, event_ids,
            "보낸 답변이 없어 길이 예산을 잴 수 없음",
        )
    within = sum(1 for text in replies if len(text) <= STYLE_MAX_CHARS)
    score = ratio(within, len(replies))
    return criterion(
        "register_style",
        score,
        within,
        len(replies),
        len(replies),
        period,
        event_ids,
        f"보낸 답변 {len(replies)}건 중 {STYLE_MAX_CHARS}자 이하 비율(중앙 길이 {int(statistics.median(len(t) for t in replies))}자)",
    )


def measure_intervention_timing(
    rows: list[dict[str, Any]], period: str, event_ids: list[str]
) -> dict[str, Any]:
    """No reply was sent from a stale backlog or a repeated event.

    Counts turns the policy allowed to proceed (status sent/deferred/scheduled)
    and fails the ones dropped for staleness or duplication.
    """
    stale_reasons = {"stale_backlog", "already_commented", "duplicate"}
    considered = 0
    clean = 0
    for row in rows:
        status = str(row.get("status") or "")
        reason = str(row.get("reason") or "")
        if status not in {"sent", "deferred", "scheduled", "skipped"}:
            continue
        considered += 1
        if reason in stale_reasons:
            continue
        clean += 1
    score = ratio(clean, considered)
    return criterion(
        "intervention_timing",
        score,
        clean,
        considered,
        considered,
        period,
        event_ids,
        "오래된 밀린 대화·중복으로 판단된 개입을 뺀 비율",
    )


def measure_content_fit(
    rows: list[dict[str, Any]], period: str, event_ids: list[str]
) -> dict[str, Any]:
    """Sent replies were written with retrieved context available."""
    sent = [row for row in rows if str(row.get("status") or "") == "sent"]
    if not sent:
        return criterion(
            "content_fit", None, 0, 0, 0, period, event_ids,
            "보낸 답변이 없어 맥락 사용 여부를 잴 수 없음",
        )
    grounded = 0
    for row in sent:
        retrieval = row.get("retrieval")
        matches = row.get("context_match_count")
        if isinstance(matches, int) and matches > 0:
            grounded += 1
            continue
        if isinstance(retrieval, dict):
            count = retrieval.get("context_matches")
            if isinstance(count, int) and count > 0:
                grounded += 1
    score = ratio(grounded, len(sent))
    return criterion(
        "content_fit",
        score,
        grounded,
        len(sent),
        len(sent),
        period,
        event_ids,
        "검색된 대화 맥락이 붙은 채로 보낸 답변 비율",
    )


def measure_reliability(
    rows: list[dict[str, Any]],
    period: str,
    event_ids: list[str],
    worker_status: dict[str, Any],
) -> dict[str, Any]:
    """Generation completed without an unrecovered model failure."""
    considered = 0
    healthy = 0
    for row in rows:
        status = str(row.get("status") or "")
        if status not in {"sent", "deferred", "scheduled"}:
            continue
        considered += 1
        if str(row.get("reason") or "") != "model_temporarily_unavailable":
            healthy += 1
    score = ratio(healthy, considered)
    pending = worker_status.get("pending")
    if considered == 0:
        return criterion(
            "reliability", None, 0, 0, 0, period, event_ids,
            "답변 시도 기록이 없어 모델 실패율을 잴 수 없음",
        )
    note = f"답변 시도 {considered}건 중 모델 일시 불가로 밀린 턴을 뺀 비율"
    if isinstance(pending, int):
        note += f" · 현재 대기 {pending}건"
    return criterion(
        "reliability", score, healthy, considered, considered, period, event_ids, note
    )


def measure_observability(
    rows: list[dict[str, Any]], period: str, event_ids: list[str]
) -> dict[str, Any]:
    """Every turn carries the fields a reviewer needs to trace it."""
    if not rows:
        return criterion(
            "observability", None, 0, 0, 0, period, event_ids,
            "턴 기록이 없어 추적성을 잴 수 없음",
        )
    traced = 0
    for row in rows:
        if not str(row.get("event_id") or "").strip():
            continue
        if not str(row.get("status") or "").strip():
            continue
        if parse_stamp(row.get("recorded_at")) is None:
            continue
        traced += 1
    score = ratio(traced, len(rows))
    return criterion(
        "observability",
        score,
        traced,
        len(rows),
        len(rows),
        period,
        event_ids,
        "이벤트 ID·상태·기록 시각을 모두 가진 턴의 비율",
    )


def measure(
    state_root: Path,
    chat_id: str,
    days: int,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    now = now or dt.datetime.now(dt.timezone.utc)
    start = now - dt.timedelta(days=max(days, 1))
    period = f"{start.isoformat()} ~ {now.isoformat()}"
    ledger_path = state_root / "rooms" / chat_id / "reply-evidence.jsonl"
    rows, event_ids = window_rows(read_ledger(ledger_path), start)
    worker_status = read_json(
        state_root / "rooms" / chat_id / "reply-worker-status.json"
    )
    criteria = {
        "evidence_delivery": measure_evidence_delivery(rows, period, event_ids),
        "register_style": measure_register_style(rows, period, event_ids),
        "intervention_timing": measure_intervention_timing(rows, period, event_ids),
        "content_fit": measure_content_fit(rows, period, event_ids),
        "reliability": measure_reliability(rows, period, event_ids, worker_status),
        "observability": measure_observability(rows, period, event_ids),
    }
    return {
        "scorer_revision": SCORER_REVISION,
        "chat_id": chat_id,
        "days": days,
        "period": period,
        "ledger_path": str(ledger_path),
        "ledger_rows_total": len(read_ledger(ledger_path)),
        "ledger_rows_in_window": len(rows),
        "criteria": criteria,
    }


def write_scores(report: dict[str, Any], path: Path) -> None:
    """Write the shape ahp_naturalness.py --scores understands."""
    payload: dict[str, Any] = {}
    for name, item in report["criteria"].items():
        score = item.get("score")
        payload[name] = {
            "score": 0.0 if score is None else score,
            "note": item.get("note", ""),
            "state": item.get("state"),
            "numerator": item.get("numerator"),
            "denominator": item.get("denominator"),
            "sample_count": item.get("sample_count"),
            "period": item.get("period"),
            "scorer_revision": item.get("scorer_revision"),
            "source_event_ids": item.get("source_event_ids", []),
        }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ahp_measure_live.py")
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    parser.add_argument("--chat-id", default=DEFAULT_CHAT_ID)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv[1:])

    try:
        report = measure(args.state_root, args.chat_id, args.days)
    except Exception as error:  # never crash: a scorer that dies scores nothing
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 1

    out = args.out or (args.state_root / "ahp-scores.json")
    try:
        write_scores(report, out)
    except OSError as error:
        print(f"could not write {out}: {error}", file=sys.stderr)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    for name, item in report["criteria"].items():
        score = item.get("score")
        shown = "insufficient_data" if score is None else f"{score:.3f}"
        print(
            f"  {name:<20} {shown:<18} "
            f"{item.get('numerator')}/{item.get('denominator')} "
            f"(n={item.get('sample_count')})"
        )
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
