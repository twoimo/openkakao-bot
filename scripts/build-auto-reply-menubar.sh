#!/bin/sh
# Compatibility entrypoint. Tauri is primary; Swift remains opt-in legacy.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BACKEND=${OPENKAKAO_MENUBAR_BACKEND:-tauri}

case "$BACKEND" in
  tauri)
    exec "$ROOT/scripts/build-jarvis-desktop.sh" "$@"
    ;;
  swift)
    exec "$ROOT/scripts/build-swift-auto-reply-menubar.sh" "$@"
    ;;
  *)
    echo "build-auto-reply-menubar: OPENKAKAO_MENUBAR_BACKEND must be tauri or swift" >&2
    exit 2
    ;;
esac
