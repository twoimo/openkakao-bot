"""Shared endpoint and request-admission contract for the local MLX gateway."""

from __future__ import annotations

import fcntl
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

MLX_GATEWAY_BASE_URL = "http://127.0.0.1:11234/v1"
MLX_GATEWAY_EMBEDDINGS_URL = f"{MLX_GATEWAY_BASE_URL}/embeddings"
MLX_MODEL_SWAP_LOCK_NAME = "mlx-model-swap.lock"


def mlx_response_model_conflicts(requested_model: object, response_model: object) -> bool:
    """Return whether an explicit gateway model identity contradicts the request."""

    requested = str(requested_model or "").strip().removeprefix("mlx/")
    reported = str(response_model or "").strip().removeprefix("mlx/")
    return bool(requested and reported and requested != reported)


class MlxRequestAdmissionClosed(RuntimeError):
    """A model mutation owns the exclusive gateway lease, or the gate is unsafe."""

    def __init__(self, code: str = "model_swap_in_progress") -> None:
        super().__init__(code)
        self.code = code


class MlxModelSwapLeaseBusy(RuntimeError):
    """A local inference request still owns a shared gateway lease."""


def resolve_mlx_state_root(state_root: Path | str | None = None) -> Path:
    if state_root is not None:
        return Path(state_root).expanduser()
    for key in (
        "OPENKAKAO_STATE_ROOT",
        "OPENKAKAO_AUTO_REPLY_STATE_ROOT",
        "OPENKAKAO_BUJAMENTOR_STATE_ROOT",
    ):
        override = os.environ.get(key, "").strip()
        if override:
            return Path(override).expanduser()
    parent = Path.home() / "Library" / "Application Support" / "openkakao"
    modern = parent / "auto-reply"
    legacy = parent / "bujamentor"
    if (modern / "enrollment.json").is_file() or not (legacy / "enrollment.json").is_file():
        return modern
    return legacy


def _open_mlx_swap_lock(state_root: Path | str | None) -> int:
    root = resolve_mlx_state_root(state_root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink():
        raise OSError("mlx_request_state_unsafe")
    path = root / MLX_MODEL_SWAP_LOCK_NAME
    flags = os.O_CREAT | os.O_RDWR
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or (hasattr(os, "getuid") and info.st_uid != os.getuid())
        ):
            raise OSError("mlx_request_state_unsafe")
        os.fchmod(descriptor, 0o600)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


@contextmanager
def mlx_model_request_lease(
    state_root: Path | str | None = None,
) -> Iterator[None]:
    """Hold a nonblocking shared lease across one complete MLX request.

    Requests racing with a swap fail before touching the HTTP endpoint; a swap
    racing with an active request fails before its first model mutation.
    """

    try:
        descriptor = _open_mlx_swap_lock(state_root)
    except OSError as exc:
        raise MlxRequestAdmissionClosed("mlx_request_gate_unavailable") from exc
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise MlxRequestAdmissionClosed("model_swap_in_progress") from exc
        yield
    finally:
        os.close(descriptor)


@contextmanager
def mlx_model_swap_lease(
    state_root: Path | str | None = None,
) -> Iterator[None]:
    """Own the exclusive model-mutation lease after all local requests drain."""

    try:
        descriptor = _open_mlx_swap_lock(state_root)
    except OSError as exc:
        raise MlxRequestAdmissionClosed("mlx_request_gate_unavailable") from exc
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise MlxModelSwapLeaseBusy("model_swap_busy") from exc
        yield
    finally:
        os.close(descriptor)
