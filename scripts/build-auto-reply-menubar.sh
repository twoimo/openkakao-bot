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

# Assemble Resources from the repository so the installer never has to carry
# over an older app's Resources. The build used to leave Resources empty, and
# install-auto-reply-menubar.sh then preserved whatever the previous install
# had — the running app kept stale scripts and a stale CLI for a day
# (2026-09-13).
RES="$APP/Contents/Resources"
rm -rf "$RES/scripts" "$RES/bin"
mkdir -p "$RES/scripts" "$RES/bin"
for pattern in '*.py' '*.pyc' '*.sh' '*.command' '*.json'; do
  for f in "$ROOT"/scripts/$pattern; do
    [ -e "$f" ] && cp "$f" "$RES/scripts/"
  done
done
if [ -x "$ROOT/target/release/openkakao-cli" ]; then
  cp "$ROOT/target/release/openkakao-cli" "$RES/bin/openkakao-cli"
fi
# Toolchain: prefer the selected Xcode, but an unagreed Xcode license makes
# `xcrun` fail and the menu extra then cannot be rebuilt at all. Fall back to
# CommandLineTools, which ships the same AppKit frameworks (2026-09-15).
SWIFTC=/usr/bin/swiftc
SDK=$(/usr/bin/xcrun --show-sdk-path 2>/dev/null || true)
if [ -z "$SDK" ] || [ ! -d "$SDK" ]; then
  if [ -x /Library/Developer/CommandLineTools/usr/bin/swiftc ]; then
    SWIFTC=/Library/Developer/CommandLineTools/usr/bin/swiftc
    SDK=/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk
    export DEVELOPER_DIR=/Library/Developer/CommandLineTools
  fi
fi
if [ -z "$SDK" ] || [ ! -d "$SDK" ]; then
  echo "build-auto-reply-menubar: no usable macOS SDK (run 'sudo xcodebuild -license')" >&2
  exit 3
fi
"$SWIFTC" -O \
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
