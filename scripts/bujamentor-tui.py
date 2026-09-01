#!/usr/bin/env python3
"""Compatibility shim: frozen menubar overlay still imports the legacy
`bujamentor-tui.py` path. Delegate to the renamed auto-reply-tui module so
doctor/snapshot keep working without duplicating code."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_TARGET = _SCRIPTS_DIR / "auto-reply-tui.py"
_NAME = "_auto_reply_tui_shim_target"

if _SCRIPTS_DIR.as_posix() not in sys.path[:3]:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_spec = importlib.util.spec_from_file_location(_NAME, _TARGET)
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_NAME] = _mod
_spec.loader.exec_module(_mod)

globals().update({k: v for k, v in vars(_mod).items() if not k.startswith("__")})
collect_snapshot = _mod.collect_snapshot  # noqa: F841
