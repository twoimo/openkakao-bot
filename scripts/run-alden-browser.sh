#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec uv run \
  --project "$repo_root/browser" \
  --python 3.11 \
  --frozen \
  python "$repo_root/scripts/alden_browser_runtime.py" "$@"
