"""Latched cross-process emergency abort for Alden local jobs.

The Tauri global shortcut writes the same small state file. Python workers poll
it between bounded operations, so an abort remains active until an explicit
human resume. No job created while latched is allowed to start.
"""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import stat
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from contextlib import contextmanager
from pathlib import Path
from typing import Callable


ABORT_SCHEMA_VERSION = 1
ABORT_STATE_NAME = "alden-abort.json"
ABORT_LOCK_NAME = "alden-abort.lock"
ABORT_MAX_EPOCH = 9_007_199_254_740_991
ABORT_MAX_STATE_BYTES = 4096
ABORT_MAX_REASON_CHARS = 96
ABORT_LOCK_TIMEOUT_SECONDS = 0.25

_STATE_FIELDS = {"schema_version", "epoch", "latched", "reason"}
_OPEN_COMMON = os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)


@dataclass(frozen=True)
class AbortState:
    epoch: int = 0
    latched: bool = False
    reason: str = ""
    error: str | None = field(default=None, compare=False, repr=False)

    @property
    def is_error(self) -> bool:
        return self.error is not None


class AbortStateError(RuntimeError):
    pass


def _error_state(code: str) -> AbortState:
    return AbortState(epoch=0, latched=True, reason="state_error", error=code)


def _has_control(value: str) -> bool:
    return any(unicodedata.category(char) in {"Cc", "Cs"} for char in value)


def _safe_state(payload: object) -> AbortState:
    if not isinstance(payload, dict) or set(payload) != _STATE_FIELDS:
        return _error_state("state_schema_fields")
    schema_version = payload.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        return _error_state("state_schema_version")
    if schema_version != ABORT_SCHEMA_VERSION:
        return _error_state("state_schema_version")
    epoch = payload.get("epoch")
    if (
        isinstance(epoch, bool)
        or not isinstance(epoch, int)
        or epoch < 0
        or epoch > ABORT_MAX_EPOCH
    ):
        return _error_state("state_epoch")
    latched = payload.get("latched")
    if not isinstance(latched, bool):
        return _error_state("state_latched")
    reason = payload.get("reason")
    if (
        not isinstance(reason, str)
        or len(reason) > ABORT_MAX_REASON_CHARS
        or _has_control(reason)
    ):
        return _error_state("state_reason")
    if latched and not reason:
        return _error_state("state_reason")
    if not latched and not (reason == "human_resume" or (epoch == 0 and reason == "")):
        return _error_state("state_resume_reason")
    return AbortState(epoch=epoch, latched=latched, reason=reason)


def _strict_json_loads(raw: bytes) -> object:
    def no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = value
        return result

    return json.loads(raw.decode("utf-8"), object_pairs_hook=no_duplicate_keys)


def _validate_root_fd(fd: int) -> str | None:
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode):
        return "state_root_type"
    if info.st_uid != os.geteuid():
        return "state_root_owner"
    if stat.S_IMODE(info.st_mode) != 0o700:
        return "state_root_mode"
    return None


def _open_root(root: Path, *, create: bool) -> tuple[int | None, str | None]:
    if create:
        try:
            root.mkdir(parents=True, mode=0o700, exist_ok=False)
        except FileExistsError:
            pass
        except OSError as exc:
            return None, f"state_root_create:{exc.errno}"
    flags = os.O_RDONLY | os.O_DIRECTORY | _OPEN_COMMON
    try:
        fd = os.open(root, flags)
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, f"state_root_open:{exc.errno}"
    error = _validate_root_fd(fd)
    if error is not None:
        os.close(fd)
        return None, error
    return fd, None


def _validate_private_file(fd: int, prefix: str) -> str | None:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        return f"{prefix}_type"
    if info.st_uid != os.geteuid():
        return f"{prefix}_owner"
    if stat.S_IMODE(info.st_mode) != 0o600:
        return f"{prefix}_mode"
    if info.st_nlink != 1:
        return f"{prefix}_link_count"
    return None


def _read_bounded(fd: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total <= ABORT_MAX_STATE_BYTES:
        chunk = os.read(fd, ABORT_MAX_STATE_BYTES + 1 - total)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > ABORT_MAX_STATE_BYTES:
            raise ValueError("state_too_large")
    raise ValueError("state_too_large")


def _read_abort_state_at(root_fd: int, name: str) -> AbortState:
    try:
        fd = os.open(name, os.O_RDONLY | _OPEN_COMMON, dir_fd=root_fd)
    except FileNotFoundError:
        return AbortState()
    except OSError as exc:
        return _error_state(f"state_open:{exc.errno}")
    try:
        error = _validate_private_file(fd, "state")
        if error is not None:
            return _error_state(error)
        if os.fstat(fd).st_size > ABORT_MAX_STATE_BYTES:
            return _error_state("state_too_large")
        try:
            raw = _read_bounded(fd)
            return _safe_state(_strict_json_loads(raw))
        except UnicodeError:
            return _error_state("state_utf8")
        except json.JSONDecodeError:
            return _error_state("state_malformed_json")
        except (TypeError, ValueError) as exc:
            if str(exc) == "state_too_large":
                return _error_state("state_too_large")
            return _error_state("state_malformed_json")
        except OSError as exc:
            return _error_state(f"state_read:{exc.errno}")
    finally:
        os.close(fd)


def read_abort_state(path: Path) -> AbortState:
    root_fd, root_error = _open_root(path.parent, create=False)
    if root_error is not None:
        return _error_state(root_error)
    if root_fd is None:
        return AbortState()
    try:
        return _read_abort_state_at(root_fd, path.name)
    finally:
        os.close(root_fd)


def _encode_state(state: AbortState) -> bytes:
    if state.error is not None:
        raise AbortStateError("alden_abort_refuse_error_state")
    payload = {
        "schema_version": ABORT_SCHEMA_VERSION,
        "epoch": state.epoch,
        "latched": state.latched,
        "reason": state.reason,
    }
    checked = _safe_state(payload)
    if checked.error is not None:
        raise AbortStateError(f"alden_abort_state_invalid:{checked.error}")
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > ABORT_MAX_STATE_BYTES:
        raise AbortStateError("alden_abort_state_too_large")
    return raw


def _atomic_write_state(root_fd: int, name: str, state: AbortState) -> None:
    raw = _encode_state(state)
    temp_name = ""
    temp_fd: int | None = None
    try:
        for _ in range(16):
            candidate = f".{name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
            try:
                temp_fd = os.open(
                    candidate,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | _OPEN_COMMON,
                    0o600,
                    dir_fd=root_fd,
                )
                temp_name = candidate
                break
            except FileExistsError:
                continue
        if temp_fd is None:
            raise AbortStateError("alden_abort_temp_create_exhausted")
        os.fchmod(temp_fd, 0o600)
        error = _validate_private_file(temp_fd, "temp")
        if error is not None:
            raise AbortStateError(f"alden_abort_temp_unsafe:{error}")
        offset = 0
        while offset < len(raw):
            written = os.write(temp_fd, raw[offset:])
            if written <= 0:
                raise AbortStateError("alden_abort_temp_short_write")
            offset += written
        os.fsync(temp_fd)
        os.close(temp_fd)
        temp_fd = None
        os.replace(temp_name, name, src_dir_fd=root_fd, dst_dir_fd=root_fd)
        temp_name = ""
    finally:
        if temp_fd is not None:
            os.close(temp_fd)
        if temp_name:
            try:
                os.unlink(temp_name, dir_fd=root_fd)
            except FileNotFoundError:
                pass


def _open_lock(root_fd: int) -> int:
    try:
        fd = os.open(
            ABORT_LOCK_NAME,
            os.O_RDWR | os.O_CREAT | _OPEN_COMMON,
            0o600,
            dir_fd=root_fd,
        )
    except OSError as exc:
        raise AbortStateError(f"alden_abort_lock_open:{exc.errno}") from exc
    error = _validate_private_file(fd, "lock")
    if error is not None:
        os.close(fd)
        raise AbortStateError(f"alden_abort_lock_unsafe:{error}")
    return fd


def _normalize_abort_reason(reason: object) -> str:
    value = str(reason or "global_abort")[:ABORT_MAX_REASON_CHARS]
    if not value:
        value = "global_abort"
    if _has_control(value):
        raise AbortStateError("alden_abort_reason_control")
    return value


def _transition(path: Path, *, latched: bool, reason: str) -> AbortState:
    root_fd, root_error = _open_root(path.parent, create=True)
    if root_error is not None or root_fd is None:
        raise AbortStateError(f"alden_abort_root_unsafe:{root_error or 'state_root_missing'}")
    lock_fd: int | None = None
    try:
        lock_fd = _open_lock(root_fd)
        deadline = time.monotonic() + ABORT_LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, InterruptedError) as exc:
                if time.monotonic() >= deadline:
                    raise AbortStateError("alden_abort_lock_timeout") from exc
                time.sleep(0.005)
        current = _read_abort_state_at(root_fd, path.name)
        if current.error is not None:
            raise AbortStateError(f"alden_abort_state_unsafe:{current.error}")
        if not latched and not current.latched:
            raise AbortStateError("alden_abort_resume_requires_latched_state")
        if current.epoch >= ABORT_MAX_EPOCH:
            raise AbortStateError("alden_abort_epoch_overflow")
        state = AbortState(current.epoch + 1, latched, reason)
        _atomic_write_state(root_fd, path.name, state)
        return state
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
        os.close(root_fd)


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
        normalized_reason = _normalize_abort_reason(reason)
        with self._lock:
            state = _transition(self.path, latched=True, reason=normalized_reason)
            callbacks = tuple(self._callbacks)
        for callback in callbacks:
            try:
                callback()
            except Exception:
                pass
        return state

    def resume_after_human_action(self) -> AbortState:
        with self._lock:
            return _transition(self.path, latched=False, reason="human_resume")


class AbortToken:
    def __init__(self, path: Path):
        self.path = path
        initial = read_abort_state(path)
        self._epoch = initial.epoch
        self._local = threading.Event()
        self._commit_lock = threading.RLock()
        if initial.latched:
            self._local.set()

    @property
    def captured_epoch(self) -> int:
        return self._epoch

    def cancel(self) -> None:
        # A prepared commit admitted first may finish; when cancellation
        # completes first, no later commit on this token can publish output.
        with self._commit_lock:
            self._local.set()

    def is_cancelled(self) -> bool:
        if self._local.is_set():
            return True
        current = read_abort_state(self.path)
        if current.latched or current.epoch != self._epoch:
            self._local.set()
        return self._local.is_set()

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise AldenCancelled("alden_global_abort")

    @contextmanager
    def commit_guard(self):
        """Linearize a small commit against cross-process abort.

        Only in-memory updates or a prepared atomic file rename belong here.
        Do not perform model, playback, bulk file, or status I/O inside this boundary.
        Abort and resume use the same lock; an older epoch never becomes valid.
        """
        with self._commit_lock:
            with self._shared_commit_guard():
                yield

    @contextmanager
    def _shared_commit_guard(self):
        root_fd, error = _open_root(self.path.parent, create=True)
        if error is not None or root_fd is None:
            raise AldenCancelled("alden_abort_commit_root_unsafe")
        lock_fd = None
        try:
            lock_fd = _open_lock(root_fd)
            deadline = time.monotonic() + ABORT_LOCK_TIMEOUT_SECONDS
            while True:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except (BlockingIOError, InterruptedError) as exc:
                    if time.monotonic() >= deadline:
                        raise AldenCancelled("alden_abort_commit_lock_timeout") from exc
                    time.sleep(.005)
            self.raise_if_cancelled()
            yield
        except AbortStateError as exc:
            raise AldenCancelled("alden_abort_commit_lock_unsafe") from exc
        finally:
            if lock_fd is not None:
                os.close(lock_fd)
            os.close(root_fd)


class AldenCancelled(RuntimeError):
    pass


class AbortableJobQueue:
    """Small in-process queue that stays stopped while the global latch is set."""

    def __init__(self, controller: AbortController):
        self.controller = controller
        self._jobs: list[tuple[Callable[[AbortToken], object], AbortToken]] = []
        self._lock = threading.Lock()
        controller.register_cancel_callback(self.cancel_stale)

    def enqueue(self, job: Callable[[AbortToken], object]) -> bool:
        token = self.controller.token()
        if token.is_cancelled():
            return False
        with self._lock:
            if token.is_cancelled():
                return False
            self._jobs.append((job, token))
        return True

    def cancel_queued(self) -> None:
        with self._lock:
            self._jobs.clear()

    def cancel_stale(self) -> None:
        # A callback can arrive after human resume and new enqueue. The saved
        # epoch distinguishes cancelled work from those newly authorized jobs.
        with self._lock:
            self._jobs = [(job, token) for job, token in self._jobs if not token.is_cancelled()]

    def run_next(self) -> object | None:
        if read_abort_state(self.controller.path).latched:
            self.cancel_stale()
            return None
        with self._lock:
            if not self._jobs:
                return None
            job, token = self._jobs.pop(0)
        if token.is_cancelled():
            return None
        return job(token)
