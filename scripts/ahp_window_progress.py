#!/usr/bin/env python3
"""Measure the AutoReply acceptance window Astra defined.

Window rule (agreed with the Chat 6 Pro reviewer, 2026-09-12):
  - the code, model and operator policy are frozen at a start instant
  - at least 3 conversation sessions
  - at least 100 independent inbound units
  - at least 30 confirmed automatic sends

Definitions used here, so the numbers cannot be argued afterwards:
  inbound unit   incoming message from someone else, where a new unit starts
                 when the same author's previous message is more than 15s older
  session        contiguous activity; a gap of 30 minutes or more starts a new one
  send           reply_jobs row with status='sent' (the delivery authority),
                 written at or after the start instant

Usage:
  python3 scripts/ahp_window_progress.py --start 2026-09-12T02:57:00+09:00 --json
  python3 scripts/ahp_window_progress.py --start-file <state>/ahp-window-start.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

UNIT_GAP_SECONDS = 15
SESSION_GAP_SECONDS = 30 * 60
STATE_ROOT = Path(
    os.environ.get(
        "OPENKAKAO_AUTO_REPLY_STATE_ROOT",
        Path.home() / "Library" / "Application Support" / "openkakao" / "bujamentor",
    )
)
CHAT_ID = int(os.environ.get("OPENKAKAO_AHP_CHAT_ID", "417780809780519"))
CONTEXT_DB = Path(
    os.environ.get(
        "OPENKAKAO_CONTEXT_DB",
        Path.home() / "Library" / "Application Support" / "openkakao" / "context.sqlite3",
    )
)
TARGETS = {"sends": 30, "units": 100, "sessions": 3}


def parse_start(value: str) -> float:
    text = value.strip()
    if text.isdigit():
        return float(text)
    stamp = dt.datetime.fromisoformat(text)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.timezone.utc)
    return stamp.timestamp()


def read_start(args: argparse.Namespace) -> tuple[float, str]:
    if args.start_file:
        payload = json.loads(Path(args.start_file).read_text(encoding="utf-8"))
        return float(payload["start_unix"]), str(payload.get("start_local", ""))
    start = parse_start(args.start)
    return start, ""


def confirmed_sends(start: float) -> list[dict[str, Any]]:
    queue = STATE_ROOT / "rooms" / str(CHAT_ID) / "reply-queue.sqlite3"
    if not queue.is_file():
        return []
    connection = sqlite3.connect(f"file:{queue}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT event_id, reply, updated_at FROM reply_jobs "
            "WHERE status = 'sent' AND updated_at >= ? ORDER BY updated_at",
            (start,),
        ).fetchall()
    finally:
        connection.close()
    return [
        {
            "event_id": row["event_id"],
            "reply": str(row["reply"] or "")[:120],
            "sent_at_unix": float(row["updated_at"]),
        }
        for row in rows
    ]


def inbound_units(start: float) -> list[dict[str, Any]]:
    if not CONTEXT_DB.is_file():
        return []
    connection = sqlite3.connect(f"file:{CONTEXT_DB}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            # Only the other people's incoming speech counts as an inbound
            # unit: disposition 'context' is a participant message that was
            # indexed, while 'style' is the operator's own row and the
            # auto_generated ones are the assistant's replies.
            "SELECT chat_id, log_id, sent_at, sender_name FROM context_live_events "
            "WHERE chat_id = ? AND sent_at >= ? AND disposition = 'context' "
            "ORDER BY sent_at, log_id",
            (CHAT_ID, int(start)),
        ).fetchall()
    finally:
        connection.close()
    units: list[dict[str, Any]] = []
    for row in rows:
        units.append(
            {
                "log_id": row["log_id"],
                "sent_at_unix": float(row["sent_at"]),
                "author": str(row["sender_name"] or ""),
            }
        )
    return units


def count_units(rows: list[dict[str, Any]]) -> int:
    units = 0
    previous: dict[str, float] = {}
    for row in rows:
        author = row["author"]
        last = previous.get(author)
        if last is None or row["sent_at_unix"] - last > UNIT_GAP_SECONDS:
            units += 1
        previous[author] = row["sent_at_unix"]
    return units


def count_sessions(*streams: list[dict[str, Any]]) -> int:
    stamps: list[float] = []
    for stream in streams:
        stamps.extend(item["sent_at_unix"] for item in stream)
    stamps.sort()
    sessions = 0
    last: float | None = None
    for stamp in stamps:
        if last is None or stamp - last >= SESSION_GAP_SECONDS:
            sessions += 1
        last = stamp
    return sessions


def evaluate(start: float, start_label: str) -> dict[str, Any]:
    sends = confirmed_sends(start)
    inbounds = inbound_units(start)
    units = count_units(inbounds)
    sessions = count_sessions(sends, inbounds)
    counts = {"sends": len(sends), "units": units, "sessions": sessions}
    missing = {
        key: max(0, TARGETS[key] - counts[key])
        for key in TARGETS
        if counts[key] < TARGETS[key]
    }
    return {
        "start_unix": round(start, 3),
        "start_local": start_label,
        "chat_id": CHAT_ID,
        "window_target": TARGETS,
        "counts": counts,
        "missing": missing,
        "window_complete": not missing,
        "unit_gap_seconds": UNIT_GAP_SECONDS,
        "session_gap_seconds": SESSION_GAP_SECONDS,
        "first_send_at_unix": sends[0]["sent_at_unix"] if sends else None,
        "last_send_at_unix": sends[-1]["sent_at_unix"] if sends else None,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ahp_window_progress.py")
    parser.add_argument("--start")
    parser.add_argument("--start-file")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv[1:])
    if not args.start and not args.start_file:
        parser.error("provide --start or --start-file")
    start, label = read_start(args)
    report = evaluate(start, label)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    counts = report["counts"]
    print(
        "window: sends {sends}/{ts} | units {units}/{tu} | sessions {sessions}/{tsess} "
        "| complete={done}".format(
            sends=counts["sends"],
            units=counts["units"],
            sessions=counts["sessions"],
            ts=TARGETS["sends"],
            tu=TARGETS["units"],
            tsess=TARGETS["sessions"],
            done=report["window_complete"],
        )
    )
    if report["missing"]:
        print("missing:", json.dumps(report["missing"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
