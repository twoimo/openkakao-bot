#!/bin/sh
# Compile the read-only AutoReply menu extra into a LSUIElement .app.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
SRC="$ROOT/macos/AutoReplyMenu"
APP="$SRC/build/AutoReplyMenu.app"
BIN="$APP/Contents/MacOS/AutoReplyMenu"
PLIST="$SRC/Info.plist"
SWIFT="$SRC/main.swift"

if [ ! -f "$SWIFT" ] || [ ! -f "$PLIST" ]; then
  echo "build-auto-reply-menubar: missing source" >&2
  exit 2
fi

mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$PLIST" "$APP/Contents/Info.plist"
SDK=$(/usr/bin/xcrun --show-sdk-path)
/usr/bin/swiftc -O \
  -target arm64-apple-macosx13.0 \
  -sdk "$SDK" \
  -framework AppKit \
  -framework UserNotifications \
  -o "$BIN" \
  "$SWIFT"
/bin/chmod 755 "$BIN"
# Stable code identity: an ad-hoc signature changes with every build, so macOS
# treated each build as a new app and re-asked for folder permissions.
SIGN_IDENTITY=${OPENKAKAO_SIGN_IDENTITY:-"Apple Development: twoimo@dgu.ac.kr (2AAG4522X6)"}
if [ -x /usr/bin/codesign ]; then
  /usr/bin/codesign --force --sign "$SIGN_IDENTITY" --identifier com.openkakao.menubar "$BIN" >/dev/null 2>&1 || true
  /usr/bin/codesign --force --sign "$SIGN_IDENTITY" --identifier com.openkakao.menubar "$APP" >/dev/null 2>&1 || true
fi
printf '%s
' "$APP"
