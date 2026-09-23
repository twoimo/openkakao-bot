#!/bin/sh
# Build the release CLI and the Tauri Jarvis application bundle.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
DESKTOP="$ROOT/desktop"
APP_NAME="OpenKakao Jarvis.app"
APP="$DESKTOP/src-tauri/target/release/bundle/macos/$APP_NAME"
APP_BIN="$APP/Contents/MacOS/openkakao-jarvis-desktop"

if [ ! -f "$DESKTOP/package.json" ] || [ ! -f "$DESKTOP/src-tauri/tauri.conf.json" ]; then
  echo "build-jarvis-desktop: desktop project is incomplete: $DESKTOP" >&2
  exit 2
fi

(
  cd "$ROOT"
  cargo build --release --bin openkakao-cli
)

if [ ! -x "$ROOT/target/release/openkakao-cli" ]; then
  echo "build-jarvis-desktop: release CLI was not produced" >&2
  exit 2
fi

(
  cd "$DESKTOP"
  npm run build

  if [ -n "${OPENKAKAO_SIGN_IDENTITY:-}" ]; then
    APPLE_SIGNING_IDENTITY=$OPENKAKAO_SIGN_IDENTITY
    export APPLE_SIGNING_IDENTITY
    npm run tauri -- build --bundles app
  else
    # Do not inherit an unrelated signing identity. Local builds are unsigned;
    # callers may explicitly request an ad-hoc identity with
    # OPENKAKAO_SIGN_IDENTITY=-.
    unset APPLE_SIGNING_IDENTITY
    npm run tauri -- build --bundles app --no-sign
  fi
)

if [ ! -x "$APP_BIN" ]; then
  echo "build-jarvis-desktop: Tauri app was not produced at $APP" >&2
  exit 2
fi

printf '%s\n' "$APP"
