#!/bin/sh
# Install the menubar app into /Applications.
#
# The build output is copied by a sandboxed process, so it carries
# com.apple.quarantine. macOS then launches it from a translocation path and
# dyld aborts with "unknown imports format" (2026-09-12). Clearing the flag
# right after the copy keeps the app running from /Applications.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/macos/AutoReplyMenu/build/AutoReplyMenu.app"
TARGET="/Applications/AutoReplyMenu.app"
if [ ! -d "$APP" ]; then
  echo "build the app first: scripts/build-auto-reply-menubar.sh" >&2
  exit 1
fi
pkill -f "AutoReplyMenu.app/Contents/MacOS" 2>/dev/null || true
# The build output has an empty Contents/Resources, but the running app also
# needs the helper scripts and bundled CLI. Replacing the installed app
# without them left the helper missing and the menu showed
# snapshot_unavailable (2026-09-12). Carry the installed Resources over when
# the build output has none.
if [ -d "$TARGET/Contents/Resources" ] &&
  [ -z "$(ls -A "$APP/Contents/Resources" 2>/dev/null || true)" ]; then
  rm -rf "$APP/Contents/Resources"
  cp -R "$TARGET/Contents/Resources" "$APP/Contents/Resources" || true
fi
rm -rf "$TARGET"
cp -R "$APP" "$TARGET"
xattr -dr com.apple.quarantine "$TARGET" 2>/dev/null || true
# The menu extra must run as the LaunchAgent job so it gets its
# --python/--script/--state-root/--bin arguments. `open -a` would start a
# second, argument-less LaunchServices instance that shows
# snapshot_unavailable, so restart the job and never use open(1) (2026-09-13).
LABEL="com.openkakao.auto-reply.menu"
UID_NOW="$(id -u)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
if ! launchctl kickstart -k "gui/$UID_NOW/$LABEL" 2>/dev/null; then
  if [ -f "$PLIST" ]; then
    launchctl bootstrap "gui/$UID_NOW" "$PLIST" 2>/dev/null || true
    launchctl kickstart -k "gui/$UID_NOW/$LABEL" 2>/dev/null || \
      echo "could not start $LABEL; check: launchctl print gui/$UID_NOW/$LABEL" >&2
  else
    echo "menu LaunchAgent missing; install it first (e.g. scripts/install-auto-reply-launchd.sh)" >&2
  fi
fi
echo "installed: $TARGET"

# Point the LaunchAgent at the stable CLI, not the copy inside the bundle.
#
# macOS keys an automation (Apple Events) approval on the binary's path and
# code identity. The bundle path changes identity on every build and had no
# approval row at all, so every send asked the operator again; the log holds
# 86 separate approval rows, one per path this app has ever run from. The
# session runtime already stages one signed copy at a fixed path for exactly
# this reason, so the menu uses that one (2026-09-16).
#
# Only the --bin value is rewritten. Everything else in the plist belongs to
# whoever installed it, and the operator may have edited it deliberately.
STABLE_BIN="$HOME/Library/Application Support/openkakao/bin/openkakao-cli"
if [ -f "$PLIST" ] && [ -x "$STABLE_BIN" ]; then
  if /usr/libexec/PlistBuddy -c 'Print :ProgramArguments:8' "$PLIST" 2>/dev/null |
    grep -q "AutoReplyMenu.app/Contents/Resources/bin/openkakao-cli"; then
    cp "$PLIST" "$PLIST.bak-$(date +%Y%m%dT%H%M%S)"
    /usr/libexec/PlistBuddy -c "Set :ProgramArguments:8 $STABLE_BIN" "$PLIST" 2>/dev/null &&
      echo "menu LaunchAgent now runs the approved CLI: $STABLE_BIN" ||
      echo "could not repoint $LABEL at the stable CLI; it will ask for access again" >&2
  fi
fi
