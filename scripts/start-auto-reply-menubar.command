#!/bin/sh
# Start the installed Tauri app. Swift remains an explicit legacy backend.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BACKEND=${OPENKAKAO_MENUBAR_BACKEND:-tauri}

case "$BACKEND" in
  swift)
    exec "$ROOT/scripts/start-swift-auto-reply-menubar.command" "$@"
    ;;
  tauri)
    ;;
  *)
    echo "start-auto-reply-menubar: OPENKAKAO_MENUBAR_BACKEND must be tauri or swift" >&2
    exit 2
    ;;
esac

APPLICATIONS_DIR=${OPENKAKAO_APPLICATIONS_DIR:-/Applications}
LAUNCH_AGENTS_DIR=${OPENKAKAO_LAUNCH_AGENTS_DIR:-"$HOME/Library/LaunchAgents"}
LAUNCHCTL=${OPENKAKAO_LAUNCHCTL:-/bin/launchctl}
PLISTBUDDY=${OPENKAKAO_PLISTBUDDY:-/usr/libexec/PlistBuddy}
LABEL="com.openkakao.jarvis.desktop"
UID_NOW=$(id -u)
DOMAIN="gui/$UID_NOW"
SERVICE="$DOMAIN/$LABEL"
APP="$APPLICATIONS_DIR/OpenKakao Jarvis.app"
BIN="$APP/Contents/MacOS/openkakao-jarvis-desktop"
PLIST="$LAUNCH_AGENTS_DIR/$LABEL.plist"

if [ ! -x "$BIN" ]; then
  echo "OpenKakao Jarvis is not installed at $APP" >&2
  echo "build and install it with scripts/build-auto-reply-menubar.sh and scripts/install-auto-reply-menubar.sh" >&2
  exit 1
fi
if [ ! -f "$PLIST" ]; then
  echo "OpenKakao Jarvis LaunchAgent is not installed: $PLIST" >&2
  echo "run scripts/install-auto-reply-menubar.sh first" >&2
  exit 1
fi
if [ ! -x "$LAUNCHCTL" ] || [ ! -x "$PLISTBUDDY" ]; then
  echo "start-auto-reply-menubar: required macOS launch tools are unavailable" >&2
  exit 2
fi

CONFIGURED_BIN=$("$PLISTBUDDY" -c 'Print :ProgramArguments:0' "$PLIST" 2>/dev/null || true)
if [ "$CONFIGURED_BIN" != "$BIN" ]; then
  echo "OpenKakao Jarvis LaunchAgent does not point at the installed app" >&2
  echo "run scripts/install-auto-reply-menubar.sh to refresh it" >&2
  exit 1
fi

if ! "$LAUNCHCTL" print "$SERVICE" >/dev/null 2>&1; then
  "$LAUNCHCTL" bootstrap "$DOMAIN" "$PLIST"
fi
"$LAUNCHCTL" kickstart -k "$SERVICE"
if ! "$LAUNCHCTL" print "$SERVICE" >/dev/null 2>&1; then
  echo "OpenKakao Jarvis did not register in $DOMAIN" >&2
  exit 3
fi

printf 'started: %s\n' "$SERVICE"
