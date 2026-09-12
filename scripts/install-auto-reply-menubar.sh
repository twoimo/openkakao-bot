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
rm -rf "$TARGET"
cp -R "$APP" "$TARGET"
xattr -dr com.apple.quarantine "$TARGET" 2>/dev/null || true
open -a "$TARGET"
echo "installed: $TARGET"
