"""Latched cross-process emergency abort for Jarvis local jobs.

The Tauri global shortcut writes the same small state file. Python workers poll
it between bounded operations, so an abort remains active until an explicit
human resume. No job created while latched is allowed to start.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


ABORT_SCHEMA_VERSION = 1
ABORT_STATE_NAME = "jarvis-abort.json"


@dataclass(frozen=True)
class AbortState:
    epoch: int = 0
    latched: bool = False
    reason: str = ""


def _safe_state(payload: object) -> AbortState:
    if not isinstance(payload, dict) or payload.get("schema_version") != ABORT_SCHEMA_VERSION:
        return AbortState()
    epoch = payload.get("epoch")
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
        return AbortState()
    latched = payload.get("latched") is True
    reason = str(payload.get("reason") or "")[:96]
    return AbortState(epoch=epoch, latched=latched, reason=reason)


def read_abort_state(path: Path) -> AbortState:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
            return AbortState()
        return _safe_state(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return AbortState()


def _write_abort_state(path: Path, state: AbortState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = {
        "schema_version": ABORT_SCHEMA_VERSION,
        "epoch": state.epoch,
        "latched": state.latched,
        "reason": state.reason,
    }
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


class AbortController:
    def __init__(self, state_root: Path):
        self.path = state_root / ABORT_STATE_NAME
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[], None]] = []

    def token(self) -> "AbortToken":
        return AbortToken(self.path)

    def register_cancel_callback(self, callback: Callable[[], None]) -> None:
        with self._lock:
            self._callbacks.append(callback)

    def abort(self, reason: str = "global_abort") -> AbortState:
        with self._lock:
            current = read_abort_state(self.path)
            state = AbortState(current.epoch + 1, True, str(reason or "global_abort")[:96])
            _write_abort_state(self.path, state)
            callbacks = tuple(self._callbacks)
        for callback in callbacks:
            try:
                callback()
            except Exception:
                pass
        return state

    def resume_after_human_action(self) -> AbortState:
        with self._lock:
            current = read_abort_state(self.path)
            state = AbortState(current.epoch + 1, False, "human_resume")
            _write_abort_state(self.path, state)
            return state


class AbortToken:
    def __init__(self, path: Path):
        self.path = path
        initial = read_abort_state(path)
        self._epoch = initial.epoch
        self._local = threading.Event()
        self._born_latched = initial.latched

    def cancel(self) -> None:
        self._local.set()

    def is_cancelled(self) -> bool:
        if self._local.is_set() or self._born_latched:
            return True
        current = read_abort_state(self.path)
        return current.latched or current.epoch != self._epoch

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise JarvisCancelled("jarvis_global_abort")


class JarvisCancelled(RuntimeError):
    pass


class AbortableJobQueue:
    """Small in-process queue that stays stopped while the global latch is set."""

    def __init__(self, controller: AbortController):
        self.controller = controller
        self._jobs: list[Callable[[AbortToken], object]] = []
        self._lock = threading.Lock()
        controller.register_cancel_callback(self.cancel_queued)

    def enqueue(self, job: Callable[[AbortToken], object]) -> bool:
        if read_abort_state(self.controller.path).latched:
            return False
        with self._lock:
            self._jobs.append(job)
        return True

    def cancel_queued(self) -> None:
        with self._lock:
            self._jobs.clear()

    def run_next(self) -> object | None:
        if read_abort_state(self.controller.path).latched:
            self.cancel_queued()
            return None
        with self._lock:
            if not self._jobs:
                return None
            job = self._jobs.pop(0)
        token = self.controller.token()
        token.raise_if_cancelled()
        return job(token)
