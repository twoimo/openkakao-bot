#!/bin/sh
# Build a fresh reply-host runtime and restart it without opening any Terminal
# window. Run this from your own Terminal, not from a sandboxed shell:
#
#   sh "$HOME/Library/Application Support/openkakao/rebake-and-restart.sh"
#
# Why from Terminal: the packager stamps com.apple.quarantine onto every file it
# writes when it runs inside a sandboxed app, and macOS refuses to remove that
# attribute afterwards (EPERM for the user and for launchd alike), so the first
# run of each new launcher or binary waits for an approval dialog. A Terminal
# shell is not sandboxed, so the files it creates carry no flag and nothing asks.
set -eu

REPO="$HOME/Documents/projects/openkakao-bot"
STATE_ROOT="$HOME/Library/Application Support/openkakao/bujamentor"
LABEL="com.openkakao.auto-reply.session-monitor"
DOMAIN="gui/$(id -u)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PY=/usr/bin/python3

cd "$REPO"
echo "building a new runtime..."
./target/release/openkakao-cli auto-reply-host --bake --json >/tmp/ok-bake.json 2>/tmp/ok-bake.err || {
  echo "bake failed:"; tail -3 /tmp/ok-bake.err; exit 1
}
RT=$("$PY" -c 'import json;print(json.load(open("/tmp/ok-bake.json"))["runtime"]["runtime_root"])')
echo "runtime: $(basename "$RT")"

if ! "$RT/openkakao-cli" --version >/dev/null 2>&1; then
  echo "the new runtime binary did not start; leaving the running host alone"; exit 1
fi
echo "binary check: ok"

cp "$RT/$LABEL.plist" "$PLIST"
pkill -f "auto-reply-service.py --mode session" 2>/dev/null || true
sleep 2
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"
launchctl kickstart -k "$DOMAIN/$LABEL"

ok=unknown
for _ in 1 2 3 4 5 6 7 8 9 10; do
  sleep 10
  ok=$("$REPO/target/release/openkakao-cli" auto-reply-host --status --json 2>/dev/null | "$PY" -c 'import json,sys
try: print("true" if json.load(sys.stdin).get("healthy") else "false")
except Exception: print("unknown")' || echo unknown)
  [ "$ok" = "true" ] && break
done

echo
echo "healthy=$ok"
echo "runtime=$(basename "$RT")"
echo "watchdog log: $STATE_ROOT/session-service/watchdog.out.log"
echo "no Terminal window is opened by this path (the LaunchAgent starts the service directly)."
