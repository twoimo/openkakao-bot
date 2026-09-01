#!/bin/sh
# Package the AutoReply agent into a signed, notarized, drag-to-install .app + DMG.
#
# This script extends scripts/build-auto-reply-menubar.sh with the full
# deployment path required by R9:
#
#   1. cargo build --release          → build the Rust core binary
#   2. assemble AutoReplyMenu.app      → Swift shell + embedded core binary
#   3. package (sign → notarize → staple → DMG)
#
# Step 3 mirrors, one-for-one, the Rust `packaging::package()` function
# (src/packaging/mod.rs). That function is the single source of truth for the
# rule "only a bundle whose three stages (codesign → notarize → staple) ALL
# succeed is turned into a DMG; if any stage fails, zero artifacts are produced
# plus the failed stage, its cause, and the next action" (R9.3, R9.5). The
# `SigningTools` trait models exactly these four tools:
#
#   SigningTools::codesign   → /usr/bin/codesign
#   SigningTools::notarize   → xcrun notarytool submit --wait
#   SigningTools::staple     → xcrun stapler staple
#   SigningTools::make_dmg   → hdiutil create
#
# In production the wiring is: the maintainer runs this script; the ordered
# codesign/notarytool/stapler/hdiutil invocations below are the concrete
# `SigningTools` implementation that `package()` drives. Unit and property
# tests inject a fake `SigningTools` so the "all three succeed ⟺ artifact
# produced" equivalence is verified without a real certificate.
#
# Required environment for a real signed build (absent → the signing path is
# skipped and NO DMG is produced, matching "zero artifacts without signing"):
#   SIGNING_IDENTITY   Developer ID Application identity (codesign -s)
#   NOTARY_PROFILE     notarytool --keychain-profile name (stored credentials)
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
SRC="$ROOT/macos/AutoReplyMenu"
OUT="$SRC/build"
APP="$OUT/AutoReplyMenu.app"
MACOS_DIR="$APP/Contents/MacOS"
SHELL_BIN="$MACOS_DIR/AutoReplyMenu"
CORE_BIN="$MACOS_DIR/openkakao-cli"
DMG="$OUT/AutoReplyMenu.dmg"
PLIST="$SRC/Info.plist"
SWIFT="$SRC/main.swift"

if [ ! -f "$SWIFT" ] || [ ! -f "$PLIST" ]; then
  echo "package-app: missing Swift shell source" >&2
  exit 2
fi

# --- Step 1: build the Rust core binary (R9.1) ------------------------------
echo "package-app: building Rust core (cargo build --release)…" >&2
( cd "$ROOT" && cargo build --release --bin openkakao-cli )
CORE_SRC="$ROOT/target/release/openkakao-cli"
if [ ! -x "$CORE_SRC" ]; then
  echo "package-app: core binary not found at $CORE_SRC" >&2
  exit 2
fi

# --- Step 2: assemble the .app bundle (Swift shell + embedded core) ---------
echo "package-app: assembling $APP…" >&2
mkdir -p "$MACOS_DIR" "$APP/Contents/Resources"
cp "$PLIST" "$APP/Contents/Info.plist"

SDK=$(/usr/bin/xcrun --show-sdk-path)
/usr/bin/swiftc -O \
  -target arm64-apple-macosx13.0 \
  -sdk "$SDK" \
  -framework AppKit \
  -framework UserNotifications \
  -o "$SHELL_BIN" \
  "$SWIFT"
/bin/chmod 755 "$SHELL_BIN"

# Embed the Rust core so there is nothing else to install (extra_installers == 0,
# setup_commands == 0; R9.1).
cp "$CORE_SRC" "$CORE_BIN"
/bin/chmod 755 "$CORE_BIN"

# --- Step 3: package = sign → notarize → staple → DMG (R9.3, R9.5) ----------
# This is the concrete SigningTools implementation package() drives, in the
# exact order the Rust function enforces. Any stage failing aborts before the
# DMG is created, so zero artifacts are produced (R9.5).
if [ "${SIGNING_IDENTITY:-}" = "" ] || [ "${NOTARY_PROFILE:-}" = "" ]; then
  echo "package-app: SIGNING_IDENTITY / NOTARY_PROFILE not set." >&2
  echo "package-app: skipping sign+notarize+staple+DMG — no distribution artifact produced." >&2
  echo "package-app: set both variables to produce a signed, notarized DMG." >&2
  printf '%s\n' "$APP"
  exit 0
fi

# Stage 1: codesign (SigningTools::codesign).
echo "package-app: [1/4] codesign…" >&2
/usr/bin/codesign --force --options runtime --timestamp \
  --sign "$SIGNING_IDENTITY" "$CORE_BIN"
/usr/bin/codesign --force --options runtime --timestamp \
  --sign "$SIGNING_IDENTITY" "$APP"

# Stage 2: notarize (SigningTools::notarize). Notarytool needs a zip.
echo "package-app: [2/4] notarize…" >&2
ZIP="$OUT/AutoReplyMenu.zip"
/usr/bin/ditto -c -k --keepParent "$APP" "$ZIP"
/usr/bin/xcrun notarytool submit "$ZIP" \
  --keychain-profile "$NOTARY_PROFILE" --wait
/bin/rm -f "$ZIP"

# Stage 3: staple (SigningTools::staple).
echo "package-app: [3/4] staple…" >&2
/usr/bin/xcrun stapler staple "$APP"

# Stage 4: make_dmg (SigningTools::make_dmg). Reached only after all three
# stages succeed. The image holds the signed .app plus one drag-to-install
# location (R9.4).
echo "package-app: [4/4] make DMG…" >&2
STAGE=$(/usr/bin/mktemp -d)
cp -R "$APP" "$STAGE/"
/bin/ln -s /Applications "$STAGE/Applications"
/bin/rm -f "$DMG"
/usr/bin/hdiutil create -volname "자동 답변" \
  -srcfolder "$STAGE" -ov -format UDZO "$DMG"
/bin/rm -rf "$STAGE"

printf '%s\n' "$DMG"
