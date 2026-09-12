#!/bin/sh
# Window monitor for the openkakao-bot AHP acceptance window.
#
# Self-contained on purpose: launchd cannot read paths under ~/Documents on this
# Mac (TCC), so this script is installed into the state root and keeps all of its
# logic inline. It records the window counters, checks the reply host's health
# from the state files, and raises a macOS notification when the window completes
# or when the host stops delivering. It never sends anything to KakaoTalk, never
# opens a browser, and never spends a Chat 6 Pro turn: the final Astra review is a
# deliberate agent action once the window is full.
#
# The canonical definition of the counters lives in
# scripts/ahp_window_progress.py in the repository; keep the two in step.
set -u

STATE_ROOT="$HOME/Library/Application Support/openkakao/bujamentor"
CHAT_ID=417780809780519
START_FILE="$STATE_ROOT/ahp-window-start.json"
STATUS_LOG="$STATE_ROOT/ahp-window-monitor.jsonl"
COMPLETE_MARKER="$STATE_ROOT/ahp-window-complete.json"
ALERT_MARKER="$STATE_ROOT/ahp-window-alert.json"
PYTHON="/opt/homebrew/opt/python@3.13/bin/python3.13"

[ -f "$START_FILE" ] || exit 0

notify() {
  /usr/bin/osascript -e "display notification \"$1\" with title \"openkakao-bot AHP\" subtitle \"$2\"" >/dev/null 2>&1 || true
}

REPORT="$("$PYTHON" - "$STATE_ROOT" "$CHAT_ID" "$START_FILE" <<'PY'
import datetime, json, os, sqlite3, sys

state_root, chat_id, start_file = sys.argv[1], int(sys.argv[2]), sys.argv[3]
UNIT_GAP = 15.0
SESSION_GAP = 30 * 60.0
TARGETS = {"sends": 30, "units": 100, "sessions": 3}

try:
    start = float(json.load(open(start_file))["start_unix"])
except Exception:
    print(json.dumps({"error": "start_unavailable"}))
    raise SystemExit(0)

def read_json(path):
    try:
        return json.load(open(path))
    except Exception:
        return {}

sends = []
queue = os.path.join(state_root, "rooms", str(chat_id), "reply-queue.sqlite3")
if os.path.isfile(queue):
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % queue, uri=True)
        sends = [
            float(row[0])
            for row in conn.execute(
                "SELECT updated_at FROM reply_jobs WHERE status='sent' AND updated_at >= ?",
                (start,),
            )
        ]
        conn.close()
    except Exception:
        sends = []

inbounds = []
context_db = os.path.expanduser("~/Library/Application Support/openkakao/context.sqlite3")
if os.path.isfile(context_db):
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % context_db, uri=True)
        rows = list(
            conn.execute(
                "SELECT sender_name, sent_at FROM context_live_events"
                " WHERE chat_id=? AND sent_at>=? AND auto_generated=0"
                " ORDER BY sent_at, log_id",
                (chat_id, int(start)),
            )
        )
        conn.close()
        inbounds = [(str(row[0] or ""), float(row[1])) for row in rows]
    except Exception:
        inbounds = []

units = 0
previous = {}
for author, sent_at in inbounds:
    last = previous.get(author)
    if last is None or sent_at - last > UNIT_GAP:
        units += 1
    previous[author] = sent_at

stamps = sorted([*sends, *(value for _, value in inbounds)])
sessions = 0
last_stamp = None
for stamp in stamps:
    if last_stamp is None or stamp - last_stamp >= SESSION_GAP:
        sessions += 1
    last_stamp = stamp

counts = {"sends": len(sends), "units": units, "sessions": sessions}
missing = {k: max(0, TARGETS[k] - counts[k]) for k in TARGETS if counts[k] < TARGETS[k]}

monitor = read_json(os.path.join(state_root, "session-monitor-status.json"))
watchdog = read_json(os.path.join(state_root, "session-watchdog-status.json"))
room = read_json(os.path.join(state_root, "rooms", str(chat_id), "supervisor-status.json"))
db_watch = read_json(os.path.join(state_root, "rooms", str(chat_id), "db-watch-state.json"))
healthy = (
    monitor.get("state") == "watchdog_running"
    and watchdog.get("state") == "running"
    and room.get("state") == "running"
    and room.get("readiness") == "ready"
    and room.get("reply_worker_state") == "running"
    and db_watch.get("delivery_enabled") is True
)

print(
    json.dumps(
        {
            "checked_at_utc": datetime.datetime.now(datetime.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "host_healthy": healthy,
            "monitor_state": monitor.get("state"),
            "room": "%s/%s" % (room.get("state"), room.get("readiness")),
            "window": counts,
            "missing": missing,
            "window_complete": not missing,
            "start_local": json.load(open(start_file)).get("start_local"),
        },
        ensure_ascii=False,
    )
)
PY
)"
[ -n "$REPORT" ] || REPORT='{"error":"report_unavailable"}'

printf '%s\n' "$REPORT" >> "$STATUS_LOG"

COMPLETE="$(printf '%s' "$REPORT" | "$PYTHON" -c 'import json,sys
try: print("true" if json.load(sys.stdin).get("window_complete") else "false")
except Exception: print("unknown")')"
HEALTHY="$(printf '%s' "$REPORT" | "$PYTHON" -c 'import json,sys
try: print("true" if json.load(sys.stdin).get("host_healthy") else "false")
except Exception: print("unknown")')"

if [ "$COMPLETE" = "true" ] && [ ! -f "$COMPLETE_MARKER" ]; then
  printf '%s\n' "$REPORT" > "$COMPLETE_MARKER"
  notify "평가 창이 찼습니다. 최종 Chat 6 Pro 판정을 요청하세요." "3세션/100단위/30전송 도달"
fi

if [ "$HEALTHY" = "false" ]; then
  if [ ! -f "$ALERT_MARKER" ]; then
    printf '%s\n' "$REPORT" > "$ALERT_MARKER"
    notify "답장 호스트가 멈춰 있습니다. 상태를 확인하세요." "$(printf '%s' "$REPORT" | "$PYTHON" -c 'import json,sys
try:
    d=json.load(sys.stdin); print("monitor=%s room=%s" % (d.get("monitor_state"), d.get("room")))
except Exception: print("state unreadable")')"
  fi
else
  rm -f "$ALERT_MARKER"
fi
exit 0
