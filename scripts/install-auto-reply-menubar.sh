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
open -a "$TARGET"
echo "installed: $TARGET"
