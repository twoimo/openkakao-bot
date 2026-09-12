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
