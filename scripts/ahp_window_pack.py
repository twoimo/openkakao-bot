#!/usr/bin/env python3
"""Build the acceptance-window evidence pack the 6 Pro reviewer asked for.

Outputs five bounded files into one directory (no prompt text, no chat dumps):

  window-manifest.json     start/end, timezone, runtime + commit identity, models,
                           operator policy/profile versions, restarts and manual notes
  window-events.csv        every inbound row for the room with its job outcome
  reply-receipts.jsonl     the ledger lines for the window, linked by event_id
  learning-audit.csv       confirmed sends vs owner_style / recipient / pacing rows
  conversation-review.csv  every auto send with the conversation around it

Usage:
  python3 scripts/ahp_window_pack.py --out <dir> [--end <iso>]
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

STATE_ROOT = Path(
    os.environ.get(
        "OPENKAKAO_AUTO_REPLY_STATE_ROOT",
        Path.home() / "Library" / "Application Support" / "openkakao" / "bujamentor",
    )
)
CONTEXT_DB = Path(
    os.environ.get(
        "OPENKAKAO_CONTEXT_DB",
        Path.home() / "Library" / "Application Support" / "openkakao" / "context.sqlite3",
    )
)
REPO = Path(__file__).resolve().parents[1]
CHAT_ID = int(os.environ.get("OPENKAKAO_AHP_CHAT_ID", "417780809780519"))
UNIT_GAP = 15.0
SESSION_GAP = 30 * 60.0
CONTEXT_WINDOW = 6


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def git_rev() -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except Exception:
        return ""


def connect(path: Path) -> sqlite3.Connection | None:
    if not path.is_file():
        return None
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except Exception:
        return None


def load_inbound(start: float) -> list[dict]:
    conn = connect(CONTEXT_DB)
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT log_id, sent_at, sender_name, disposition FROM context_live_events"
            " WHERE chat_id=? AND sent_at>=? ORDER BY sent_at, log_id",
            (CHAT_ID, int(start)),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "log_id": row[0],
            "sent_at_unix": float(row[1]),
            "author": str(row[2] or ""),
            "disposition": str(row[3] or ""),
        }
        for row in rows
    ]


def label_units(rows: list[dict]) -> None:
    previous: dict[str, float] = {}
    unit = 0
    session = 0
    last_session_stamp: float | None = None
    for row in rows:
        if row["disposition"] != "context":
            row["unit_id"] = ""
            row["session_id"] = ""
            continue
        author = row["author"]
        last = previous.get(author)
        if last is None or row["sent_at_unix"] - last > UNIT_GAP:
            unit += 1
        previous[author] = row["sent_at_unix"]
        if last_session_stamp is None or row["sent_at_unix"] - last_session_stamp >= SESSION_GAP:
            session += 1
        last_session_stamp = row["sent_at_unix"]
        row["unit_id"] = f"u{unit}"
        row["session_id"] = f"s{session}"


def job_outcomes(start: float) -> dict[str, dict]:
    conn = connect(STATE_ROOT / "rooms" / str(CHAT_ID) / "reply-queue.sqlite3")
    if conn is None:
        return {}
    try:
        rows = conn.execute(
            "SELECT event_id, status, decision, reason, category, reply, created_at, updated_at"
            " FROM reply_jobs WHERE updated_at >= ? OR created_at >= ?",
            (start, start),
        ).fetchall()
    finally:
        conn.close()
    return {str(row[0]): dict(zip(("status", "decision", "reason", "category", "reply", "created_at", "updated_at"), row)) for row in rows}


def send_records(start: float) -> list[dict]:
    conn = connect(STATE_ROOT / "rooms" / str(CHAT_ID) / "reply-queue.sqlite3")
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT event_id, reply, updated_at FROM reply_jobs"
            " WHERE status='sent' AND updated_at>=? ORDER BY updated_at",
            (start,),
        ).fetchall()
    finally:
        conn.close()
    return [{"event_id": str(r[0]), "reply": str(r[1] or ""), "sent_at_unix": float(r[2])} for r in rows]


def ledger_rows(start: float) -> list[dict]:
    path = STATE_ROOT / "rooms" / str(CHAT_ID) / "reply-evidence.jsonl"
    out: list[dict] = []
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            record = json.loads(line)
        except Exception:
            continue
        stamp = str(record.get("recorded_at") or "")
        try:
            when = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
        if when >= start:
            out.append(record)
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ahp_window_pack.py")
    parser.add_argument("--out", required=True)
    parser.add_argument("--end")
    args = parser.parse_args(argv[1:])

    start_file = STATE_ROOT / "ahp-window-start.json"
    window = read_json(start_file)
    start = float(window.get("start_unix") or 0)
    if start <= 0:
        raise SystemExit("window start is missing; run the monitor first")
    end = (
        dt.datetime.fromisoformat(args.end).timestamp()
        if args.end
        else dt.datetime.now(dt.timezone.utc).timestamp()
    )

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    runtimes = sorted(p.name for p in (STATE_ROOT / "runtime").glob("*") if p.is_dir())[-3:]
    manifest = {
        "window": {
            "start_unix": start,
            "start_local": window.get("start_local"),
            "end_unix": end,
            "end_local": dt.datetime.fromtimestamp(end, dt.timezone(dt.timedelta(hours=9))).isoformat(),
            "runtime": window.get("runtime"),
            "note": window.get("note"),
        },
        "chat_id": CHAT_ID,
        "repo_head": git_rev(),
        "recent_runtimes": runtimes,
        "models": read_json(STATE_ROOT / "runtime" / str(window.get("runtime") or "") / "config.toml")
        if False
        else "see runtime config.toml (text model opencode-go-session/deepseek-v4.1-flash)",
        "monitor_history": [
            json.loads(line)
            for line in (STATE_ROOT / "ahp-window-monitor.jsonl").read_text(encoding="utf-8").splitlines()[-20:]
            if line.strip()
        ]
        if (STATE_ROOT / "ahp-window-monitor.jsonl").is_file()
        else [],
    }
    (out / "window-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    inbound = load_inbound(start)
    label_units(inbound)
    jobs = job_outcomes(start)
    with (out / "window-events.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "log_id",
                "sent_at_utc",
                "author",
                "disposition",
                "session_id",
                "unit_id",
                "event_id",
                "job_status",
                "decision",
                "reason",
                "category",
            ]
        )
        for row in inbound:
            event_id = f"db:{CHAT_ID}:{row['log_id']}"
            job = jobs.get(event_id, {})
            writer.writerow(
                [
                    row["log_id"],
                    dt.datetime.fromtimestamp(row["sent_at_unix"], dt.timezone.utc).isoformat(),
                    row["author"],
                    row["disposition"],
                    row.get("session_id", ""),
                    row.get("unit_id", ""),
                    event_id,
                    job.get("status", ""),
                    job.get("decision", ""),
                    job.get("reason", ""),
                    job.get("category", ""),
                ]
            )

    receipts = ledger_rows(start)
    with (out / "reply-receipts.jsonl").open("w", encoding="utf-8") as handle:
        for record in receipts:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    sends = send_records(start)
    receipts_by_event: dict[str, list[dict]] = {}
    for record in receipts:
        receipts_by_event.setdefault(str(record.get("event_id")), []).append(record)
    with (out / "conversation-review.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["event_id", "sent_at_utc", "reply", "send_delay_seconds", "generation_seconds", "prompt_sha256"]
        )
        for send in sends:
            linked = receipts_by_event.get(send["event_id"], [])
            delivery = next((r for r in linked if r.get("outgoing_log_id")), {})
            writer.writerow(
                [
                    send["event_id"],
                    dt.datetime.fromtimestamp(send["sent_at_unix"], dt.timezone.utc).isoformat(),
                    send["reply"],
                    delivery.get("send_delay_seconds"),
                    delivery.get("generation_seconds"),
                    (delivery.get("prompt_sha256") or ""),
                ]
            )

    conn = connect(CONTEXT_DB)
    audit_rows: list[list[object]] = []
    if conn is not None:
        try:
            for row in conn.execute(
                "SELECT source, content_kind, style_eligible, COUNT(*) FROM owner_style"
                " WHERE chat=(SELECT chat FROM context_sources WHERE chat_id=? LIMIT 1)"
                " GROUP BY source, content_kind, style_eligible",
                (CHAT_ID,),
            ):
                audit_rows.append(list(row))
        finally:
            conn.close()
    with (out / "learning-audit.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["source", "content_kind", "style_eligible", "rows"])
        writer.writerows(audit_rows)

    counts = {
        "sends": len(sends),
        "units": len({row["unit_id"] for row in inbound if row.get("unit_id")}),
        "sessions": len({row["session_id"] for row in inbound if row.get("session_id")}),
        "events": len(inbound),
        "receipts": len(receipts),
    }
    print(json.dumps({"out": str(out), "counts": counts, "files": sorted(p.name for p in out.iterdir())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
