#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec env OPENKAKAO_VOICE_ENV=1 uv run \
  --project "$repo_root/voice" \
  --python 3.11 \
  python "$repo_root/scripts/jarvis_voice.py" "$@"
