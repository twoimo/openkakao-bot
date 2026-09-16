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
import sqlite3
import statistics
import sys
from pathlib import Path
from typing import Any

SCORER_REVISION = "live-ledger-3"
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

# The app's own reply pacing budget: a reply that reaches the room later than
# this stops being a reply to the burst that prompted it. The worker records
# response_window_upper_seconds with the same value, so the scorer and the
# pipeline agree on what "on time" means (2026-09-16).
RESPONSE_WINDOW_SECONDS = 600

# Two replies this close together read as one machine replying twice.
BURST_WINDOW_SECONDS = 120


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


def read_reply_jobs(state_root: Path, chat_id: str) -> list[dict[str, Any]] | None:
    """Read the worker's job table without writing to it.

    This is the only place that records what happened to a turn after the
    reply was written: the ledger says a turn was *planned*, the job table
    says whether it was finally sent, dropped, or is still waiting for its
    retry window. A missing table is not an error (rooms that never ran the
    worker have none), so the caller gets None and falls back to the ledger
    (2026-09-16).
    """
    path = state_root / "rooms" / chat_id / "reply-queue.sqlite3"
    if not path.is_file():
        return None
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    rows: list[dict[str, Any]] = []
    try:
        connection.row_factory = sqlite3.Row
        for record in connection.execute(
            "select event_id, status, decision, reason, error_class,"
            " created_at, updated_at, attempt_no from reply_jobs"
        ):
            rows.append(dict(record))
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    return rows


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
    """The replies that did go out were timely, and a burst got one reply.

    This used to count every ledger row and fail the ones the policy dropped
    for staleness or duplication. That inverted the criterion: declining to
    answer a two-hour-old message is the timing behaviour being asked for, and
    it was being scored as the defect. Half of the considered rows in this
    room's records were those deliberate drops, so the score measured how
    often the policy was quiet (2026-09-16).

    What the operator actually notices is an intervention that arrives too
    late or twice. Both are now read off the sends: the ledger records how
    long each reply took to reach the room (send_delay_seconds, with
    detect_delay_seconds as the fallback), and a second reply inside
    BURST_WINDOW_SECONDS of the previous one is the double reply the room
    complained about. A sent row without timing data cannot be judged, so it
    is left out rather than counted either way.
    """
    sent: list[tuple[dt.datetime, float]] = []
    undated = 0
    for row in rows:
        if str(row.get("status") or "") != "sent":
            continue
        delay = _seconds(row.get("send_delay_seconds"))
        if delay is None:
            delay = _seconds(row.get("detect_delay_seconds"))
        when = parse_stamp(row.get("recorded_at"))
        if delay is None or when is None:
            undated += 1
            continue
        sent.append((when, delay))
    if not sent:
        return criterion(
            "intervention_timing", None, 0, 0, 0, period, event_ids,
            "시각이 기록된 전송이 없어 개입 시점을 잴 수 없음",
        )
    sent.sort(key=lambda item: item[0])
    clean = 0
    late = 0
    doubled = 0
    previous: dt.datetime | None = None
    for when, delay in sent:
        if previous is not None and (when - previous).total_seconds() <= BURST_WINDOW_SECONDS:
            doubled += 1
        elif delay > RESPONSE_WINDOW_SECONDS:
            late += 1
        else:
            clean += 1
        previous = when
    note = (
        f"시각이 있는 전송 {len(sent)}건: 제때 {clean} · "
        f"{int(RESPONSE_WINDOW_SECONDS)}초 초과 {late} · 같은 버스트 중복 {doubled}"
    )
    if undated:
        note += f" · 시각 없는 전송 {undated}건 제외"
    return criterion(
        "intervention_timing",
        ratio(clean, len(sent)),
        clean,
        len(sent),
        len(sent),
        period,
        event_ids,
        note,
    )


def _seconds(value: Any) -> float | None:
    """Read a recorded duration without trusting its type."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number < 0:  # NaN or negative
        return None
    return number


def measure_content_fit(
    rows: list[dict[str, Any]], period: str, event_ids: list[str]
) -> dict[str, Any]:
    """Sent replies were written with retrieved context available.

    The retrieval counts live on the scheduled row and the send confirmation
    lives on the sent row, so one turn spans two ledger lines with the same
    event_id. Judging the sent row alone would report 0 for every turn that did
    retrieve context, which is what the first version of this scorer did
    (2026-09-16).
    """
    families: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        event_id = str(row.get("event_id") or "").strip()
        if event_id:
            families.setdefault(event_id, []).append(row)
    considered = 0
    grounded = 0
    for family in families.values():
        if not any(str(row.get("status") or "") == "sent" for row in family):
            continue
        considered += 1
        for row in family:
            matches = row.get("context_match_count")
            if isinstance(matches, int) and matches > 0:
                grounded += 1
                break
            retrieval = row.get("retrieval")
            if isinstance(retrieval, dict):
                count = retrieval.get("context_matches")
                if isinstance(count, int) and count > 0:
                    grounded += 1
                    break
    score = ratio(grounded, considered)
    return criterion(
        "content_fit",
        score,
        grounded,
        considered,
        considered,
        period,
        event_ids,
        "검색된 대화 맥락이 붙은 채로 보낸 답변 비율(같은 이벤트의 예약·전송 행을 합쳐서 판단)",
    )


def measure_reliability(
    rows: list[dict[str, Any]],
    period: str,
    event_ids: list[str],
    worker_status: dict[str, Any],
    jobs: list[dict[str, Any]] | None = None,
    start: dt.datetime | None = None,
) -> dict[str, Any]:
    """Every turn the policy chose to answer reached the room.

    The first version of this measured something else. It walked ledger rows
    one at a time and failed any row whose reason was
    model_temporarily_unavailable, so a turn that was deferred while the model
    warmed and then sent on the retry counted against reliability, and a turn
    that the policy deliberately declined to answer (an old message, an
    already-answered one) counted as a generation attempt. On this room's own
    records that read 0.432 while the job table showed every reply decision of
    the same week delivered (2026-09-16).

    A turn is now judged as the thing the operator experiences: the policy
    decided to answer it (decision=reply) and the job either reached the room
    (delivered) or ended without sending (lost). Turns still inside their
    retry window are pending and are reported, not counted, because a warm-up
    deferral is not yet an outcome. Turns the operator dismissed by hand are
    neither: that was the operator's own decision, not a failure.

    worker_status still supplies the live queue depth for the note.
    """
    pending_note = ""
    if jobs is not None:
        considered = 0
        delivered = 0
        lost = 0
        pending = 0
        dismissed = 0
        for job in jobs:
            if str(job.get("decision") or "") != "reply":
                continue
            if start is not None and not _job_in_window(job, start):
                continue
            status = str(job.get("status") or "")
            if status == "sent":
                delivered += 1
            elif status in {"deferred", "scheduled", "pending", "running"}:
                pending += 1
            elif str(job.get("reason") or "") == "operator_dismissed":
                dismissed += 1
            else:
                lost += 1
            considered += 1
        score = ratio(delivered, delivered + lost)
        pending_note = (
            f" · 답변 결정 {considered}건: 전송 {delivered} · 유실 {lost}"
            f" · 대기 {pending} · 운영자 취소 {dismissed}"
        )
        if score is None:
            return criterion(
                "reliability", None, 0, 0, 0, period, event_ids,
                "이 기간에 답변하기로 한 턴이 없어 전송 성공률을 잴 수 없음"
                + pending_note,
            )
        return criterion(
            "reliability",
            score,
            delivered,
            delivered + lost,
            considered,
            period,
            event_ids,
            "답변하기로 한 턴 중 실제로 전송된 비율(대기 중인 턴은 아직 결과가 아님)"
            + pending_note,
        )

    # No job table: fall back to the ledger, but still judge a turn instead of
    # a row. Any row of the same event reaching sent means it was delivered.
    delivered = 0
    lost = 0
    pending = 0
    families: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        event_id = str(row.get("event_id") or "").strip()
        if event_id:
            families.setdefault(event_id, []).append(row)
    for family in families.values():
        statuses = {str(row.get("status") or "") for row in family}
        if "sent" in statuses:
            delivered += 1
        elif statuses <= {"skipped"}:
            continue
        elif statuses & {"deferred", "scheduled"}:
            pending += 1
        else:
            lost += 1
    score = ratio(delivered, delivered + lost)
    if score is None:
        return criterion(
            "reliability", None, 0, 0, 0, period, event_ids,
            "답변 시도 기록이 없어 전송 성공률을 잴 수 없음",
        )
    note = (
        f"턴 {delivered + lost}건 중 전송 {delivered}건(작업 표 없이 원장만으로 판단)"
        f" · 대기 {pending}"
    )
    live_pending = worker_status.get("pending")
    if isinstance(live_pending, int):
        note += f" · 지금 대기 {live_pending}"
    return criterion(
        "reliability", score, delivered, delivered + lost, delivered + lost,
        period, event_ids, note,
    )


def _job_in_window(job: dict[str, Any], start: dt.datetime) -> bool:
    """Jobs carry epoch seconds, not the ledger's ISO stamps."""
    for key in ("updated_at", "created_at"):
        raw = job.get(key)
        if raw is None:
            continue
        try:
            when = dt.datetime.fromtimestamp(float(raw), dt.timezone.utc)
        except (TypeError, ValueError, OSError, OverflowError):
            continue
        return when >= start
    return True


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
    jobs = read_reply_jobs(state_root, chat_id)
    criteria = {
        "evidence_delivery": measure_evidence_delivery(rows, period, event_ids),
        "register_style": measure_register_style(rows, period, event_ids),
        "intervention_timing": measure_intervention_timing(rows, period, event_ids),
        "content_fit": measure_content_fit(rows, period, event_ids),
        "reliability": measure_reliability(
            rows, period, event_ids, worker_status, jobs, start
        ),
        "observability": measure_observability(rows, period, event_ids),
    }
    return {
        "scorer_revision": SCORER_REVISION,
        "chat_id": chat_id,
        "days": days,
        "period": period,
        "job_table_rows": len(jobs) if jobs is not None else None,
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
