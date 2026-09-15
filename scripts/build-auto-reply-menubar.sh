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
#
# The code identifier has to equal CFBundleIdentifier. tccd looks the record up
# by bundle id and then validates the caller against a requirement built from
# that same bundle id, while the record itself was written from the signing
# identifier. While the app was signed as com.openkakao.menubar those two never
# agreed: the check failed with -67050 on every poll, so macOS re-asked for
# other-app data access about every 20 seconds even after 허용, and the full
# disk access record (com.openkakao.auto-reply.menu, allowed) was denied for
# the same reason (2026-09-15).
SIGN_IDENTITY=${OPENKAKAO_SIGN_IDENTITY:-"Apple Development: twoimo@dgu.ac.kr (2AAG4522X6)"}
APP_IDENTIFIER=com.openkakao.auto-reply.menu
# A CI runner has no developer certificate. OPENKAKAO_SIGN_IDENTITY=- asks
# codesign for an ad-hoc signature instead, which is enough to run the layout
# audit there (2026-09-16).
SIGN_ARGS="--sign ${SIGN_IDENTITY}"
if [ "${SIGN_IDENTITY}" = "-" ]; then
  SIGN_ARGS="--sign -"
fi
if [ -x /usr/bin/codesign ]; then
  # The bundled CLI runs from Resources/bin and reads inside KakaoTalk's
  # container, so it needs its own stable identity as well; cargo's linker
  # signature is ad-hoc and derives an identifier from the code hash, which
  # changed on every rebuild (2026-09-15).
  if [ -f "$RES/bin/openkakao-cli" ]; then
    /usr/bin/codesign --force $SIGN_ARGS --identifier com.openkakao.cli "$RES/bin/openkakao-cli"
  fi
  /usr/bin/codesign --force $SIGN_ARGS --identifier "$APP_IDENTIFIER" "$BIN"
  /usr/bin/codesign --force $SIGN_ARGS --identifier "$APP_IDENTIFIER" "$APP"
fi
printf '%s
' "$APP"
