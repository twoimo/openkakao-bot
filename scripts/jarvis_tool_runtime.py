"""Bounded local tool boundary for Jarvis browser and background AX jobs.

The public runtime intentionally has no browser-profile, endpoint, model, or
Kakao-send options. Browser jobs always use BrowserUseRunner with its owned
Playwright context and loopback MLX binding. AX jobs delegate to the virtual
cursor primitive, which refuses requests that need focus or the real pointer.
"""

from __future__ import annotations

import asyncio
import math
import re
import time as time_module
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol

from auto_reply_ax_ui import (
    AX_ABORT_FOCUS_REQUIRED,
    AX_ABORT_GLOBAL,
    BackgroundAxResult,
    VirtualCursor,
    background_virtual_cursor_action,
)
from jarvis_abort import AbortController, AbortToken
from jarvis_browser_use import (
    BrowserJobResult,
    BrowserUseRunner,
    DedicatedPlaywrightContext,
)


MAX_JOB_ID_CHARS = 64
MAX_BROWSER_TASK_BYTES = 16 * 1024
MAX_TOOL_RESULT_BYTES = 64 * 1024
MAX_AX_COORDINATE_ABS = 1_000_000.0
MAX_AX_EXTENT = 100_000.0

ERROR_JOB_ID_INVALID = "job_id_invalid"
ERROR_BROWSER_TASK_INVALID = "browser_task_invalid"
ERROR_BROWSER_TASK_TOO_LARGE = "browser_task_too_large"
ERROR_BROWSER_JOB_FAILED = "browser_job_failed"
ERROR_BROWSER_RESULT_INVALID = "browser_result_invalid"
ERROR_BROWSER_RESULT_TOO_LARGE = "browser_result_too_large"
ERROR_AX_RECT_INVALID = "ax_rect_invalid"
ERROR_AX_ACTION_INVALID = "ax_action_invalid"
ERROR_AX_ACTION_FAILED = "ax_action_failed"

_SAFE_JOB_ID = re.compile(rf"[A-Za-z0-9][A-Za-z0-9._-]{{0,{MAX_JOB_ID_CHARS - 1}}}")


class ToolKind(str, Enum):
    BROWSER = "browser"
    AX = "ax"


class ToolStatus(str, Enum):
    COMPLETED = "completed"
    ABORTED = "aborted"
    REFUSED = "refused"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass(frozen=True)
class BrowserToolJob:
    job_id: str
    task: str = field(repr=False)


@dataclass(frozen=True)
class AxToolJob:
    job_id: str
    element_rect: tuple[float, float, float, float]
    perform_ax_action: Callable[[], bool] = field(repr=False, compare=False)
    requires_frontmost_activation: bool = False
    requires_real_pointer: bool = False


@dataclass(frozen=True)
class ToolRuntimeEvent:
    """Redacted event shape safe for a worker or bridge status stream."""

    job_id: str
    kind: ToolKind
    stage: str
    load: float
    time: float
    error_code: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "jobId": self.job_id,
            "kind": self.kind.value,
            "stage": self.stage,
            "load": self.load,
            "time": self.time,
            "errorCode": self.error_code,
        }


@dataclass(frozen=True)
class ToolJobResult:
    job_id: str
    kind: ToolKind
    status: ToolStatus
    ok: bool
    error_code: str = ""
    result: str = field(default="", repr=False)
    cursor: VirtualCursor | None = None


class _BrowserRunner(Protocol):
    async def run(self, task: str) -> BrowserJobResult: ...


BrowserRunnerFactory = Callable[[AbortToken], _BrowserRunner]
EventSink = Callable[[ToolRuntimeEvent], None]


def _valid_job_id(value: object) -> str | None:
    if not isinstance(value, str) or _SAFE_JOB_ID.fullmatch(value) is None:
        return None
    return value


def _task_error(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return ERROR_BROWSER_TASK_INVALID
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        return ERROR_BROWSER_TASK_INVALID
    if size > MAX_BROWSER_TASK_BYTES:
        return ERROR_BROWSER_TASK_TOO_LARGE
    return None


def _bounded_rect(value: object) -> tuple[float, float, float, float] | None:
    if not isinstance(value, tuple) or len(value) != 4:
        return None
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        return None
    x, y, width, height = (float(item) for item in value)
    if not all(math.isfinite(item) for item in (x, y, width, height)):
        return None
    if width < 0.0 or height < 0.0 or width > MAX_AX_EXTENT or height > MAX_AX_EXTENT:
        return None
    if max(abs(x), abs(y), abs(x + width), abs(y + height)) > MAX_AX_COORDINATE_ABS:
        return None
    return x, y, width, height


def _virtual_cursor(rect: tuple[float, float, float, float]) -> VirtualCursor:
    x, y, width, height = rect
    return VirtualCursor(x + width / 2.0, y + height / 2.0)


class JarvisToolRuntime:
    """Run one bounded local tool job without changing the global abort latch."""

    def __init__(
        self,
        state_root: Path,
        *,
        event_sink: EventSink | None = None,
        clock: Callable[[], float] = time_module.time,
        _browser_runner_factory: BrowserRunnerFactory | None = None,
    ) -> None:
        self._abort = AbortController(Path(state_root))
        self._event_sink = event_sink
        self._clock = clock
        self._browser_runner_factory = _browser_runner_factory or self._owned_browser_runner

    @staticmethod
    def _owned_browser_runner(token: AbortToken) -> BrowserUseRunner:
        return BrowserUseRunner(token, context_factory=DedicatedPlaywrightContext)

    def _time(self) -> float:
        try:
            value = float(self._clock())
        except Exception:
            return 0.0
        return value if math.isfinite(value) and value >= 0.0 else 0.0

    def _emit(
        self,
        job_id: str,
        kind: ToolKind,
        stage: str,
        load: float,
        error_code: str | None = None,
    ) -> None:
        if self._event_sink is None:
            return
        event = ToolRuntimeEvent(
            job_id=job_id,
            kind=kind,
            stage=stage,
            load=max(0.0, min(1.0, float(load))),
            time=self._time(),
            error_code=error_code,
        )
        try:
            self._event_sink(event)
        except Exception:
            # Status reporting must never change the tool outcome.
            pass

    def _finish(
        self,
        *,
        job_id: str,
        kind: ToolKind,
        status: ToolStatus,
        error_code: str = "",
        result: str = "",
        cursor: VirtualCursor | None = None,
    ) -> ToolJobResult:
        self._emit(job_id, kind, status.value, 0.0, error_code or None)
        return ToolJobResult(
            job_id=job_id,
            kind=kind,
            status=status,
            ok=status is ToolStatus.COMPLETED,
            error_code=error_code,
            result=result,
            cursor=cursor,
        )

    async def run_browser(self, job: BrowserToolJob) -> ToolJobResult:
        kind = ToolKind.BROWSER
        if not isinstance(job, BrowserToolJob):
            return self._finish(
                job_id="invalid",
                kind=kind,
                status=ToolStatus.REJECTED,
                error_code=ERROR_JOB_ID_INVALID,
            )
        job_id = _valid_job_id(job.job_id)
        if job_id is None:
            return self._finish(
                job_id="invalid",
                kind=kind,
                status=ToolStatus.REJECTED,
                error_code=ERROR_JOB_ID_INVALID,
            )
        task_error = _task_error(job.task)
        if task_error:
            return self._finish(
                job_id=job_id,
                kind=kind,
                status=ToolStatus.REJECTED,
                error_code=task_error,
            )

        token = self._abort.token()
        if token.is_cancelled():
            return self._finish(
                job_id=job_id,
                kind=kind,
                status=ToolStatus.ABORTED,
                error_code=AX_ABORT_GLOBAL,
            )

        self._emit(job_id, kind, "running", 1.0)
        try:
            runner = self._browser_runner_factory(token)
            outcome = await runner.run(job.task)
        except asyncio.CancelledError:
            if token.is_cancelled():
                return self._finish(
                    job_id=job_id,
                    kind=kind,
                    status=ToolStatus.ABORTED,
                    error_code=AX_ABORT_GLOBAL,
                )
            raise
        except Exception:
            return self._finish(
                job_id=job_id,
                kind=kind,
                status=ToolStatus.FAILED,
                error_code=ERROR_BROWSER_JOB_FAILED,
            )

        if token.is_cancelled() or (
            isinstance(outcome, BrowserJobResult)
            and not outcome.ok
            and outcome.error_code == AX_ABORT_GLOBAL
        ):
            return self._finish(
                job_id=job_id,
                kind=kind,
                status=ToolStatus.ABORTED,
                error_code=AX_ABORT_GLOBAL,
            )
        if not isinstance(outcome, BrowserJobResult) or not outcome.ok or outcome.error_code:
            return self._finish(
                job_id=job_id,
                kind=kind,
                status=ToolStatus.FAILED,
                error_code=ERROR_BROWSER_JOB_FAILED,
            )
        if not isinstance(outcome.result, str):
            return self._finish(
                job_id=job_id,
                kind=kind,
                status=ToolStatus.FAILED,
                error_code=ERROR_BROWSER_RESULT_INVALID,
            )
        try:
            result_size = len(outcome.result.encode("utf-8"))
        except UnicodeEncodeError:
            return self._finish(
                job_id=job_id,
                kind=kind,
                status=ToolStatus.FAILED,
                error_code=ERROR_BROWSER_RESULT_INVALID,
            )
        if result_size > MAX_TOOL_RESULT_BYTES:
            return self._finish(
                job_id=job_id,
                kind=kind,
                status=ToolStatus.FAILED,
                error_code=ERROR_BROWSER_RESULT_TOO_LARGE,
            )
        return self._finish(
            job_id=job_id,
            kind=kind,
            status=ToolStatus.COMPLETED,
            result=outcome.result,
        )

    def run_ax(self, job: AxToolJob) -> ToolJobResult:
        kind = ToolKind.AX
        if not isinstance(job, AxToolJob):
            return self._finish(
                job_id="invalid",
                kind=kind,
                status=ToolStatus.REJECTED,
                error_code=ERROR_JOB_ID_INVALID,
            )
        job_id = _valid_job_id(job.job_id)
        if job_id is None:
            return self._finish(
                job_id="invalid",
                kind=kind,
                status=ToolStatus.REJECTED,
                error_code=ERROR_JOB_ID_INVALID,
            )
        rect = _bounded_rect(job.element_rect)
        if rect is None:
            return self._finish(
                job_id=job_id,
                kind=kind,
                status=ToolStatus.REJECTED,
                error_code=ERROR_AX_RECT_INVALID,
            )
        cursor = _virtual_cursor(rect)
        if not callable(job.perform_ax_action) or not isinstance(
            job.requires_frontmost_activation, bool
        ) or not isinstance(job.requires_real_pointer, bool):
            return self._finish(
                job_id=job_id,
                kind=kind,
                status=ToolStatus.REJECTED,
                error_code=ERROR_AX_ACTION_INVALID,
                cursor=cursor,
            )

        token = self._abort.token()
        if token.is_cancelled():
            return self._finish(
                job_id=job_id,
                kind=kind,
                status=ToolStatus.ABORTED,
                error_code=AX_ABORT_GLOBAL,
                cursor=cursor,
            )

        self._emit(job_id, kind, "running", 1.0)
        try:
            outcome = background_virtual_cursor_action(
                element_rect=rect,
                perform_ax_action=job.perform_ax_action,
                token=token,
                requires_frontmost_activation=job.requires_frontmost_activation,
                requires_real_pointer=job.requires_real_pointer,
            )
        except Exception:
            return self._finish(
                job_id=job_id,
                kind=kind,
                status=ToolStatus.FAILED,
                error_code=ERROR_AX_ACTION_FAILED,
                cursor=cursor,
            )

        if not isinstance(outcome, BackgroundAxResult):
            return self._finish(
                job_id=job_id,
                kind=kind,
                status=ToolStatus.FAILED,
                error_code=ERROR_AX_ACTION_FAILED,
                cursor=cursor,
            )
        cursor = outcome.cursor or cursor
        if token.is_cancelled() or outcome.error_code == AX_ABORT_GLOBAL:
            return self._finish(
                job_id=job_id,
                kind=kind,
                status=ToolStatus.ABORTED,
                error_code=AX_ABORT_GLOBAL,
                cursor=cursor,
            )
        if outcome.error_code == AX_ABORT_FOCUS_REQUIRED:
            return self._finish(
                job_id=job_id,
                kind=kind,
                status=ToolStatus.REFUSED,
                error_code=AX_ABORT_FOCUS_REQUIRED,
                cursor=cursor,
            )
        if not outcome.ok or outcome.error_code:
            return self._finish(
                job_id=job_id,
                kind=kind,
                status=ToolStatus.FAILED,
                error_code=ERROR_AX_ACTION_FAILED,
                cursor=cursor,
            )
        return self._finish(
            job_id=job_id,
            kind=kind,
            status=ToolStatus.COMPLETED,
            cursor=cursor,
        )


__all__ = [
    "AxToolJob",
    "BrowserToolJob",
    "JarvisToolRuntime",
    "ToolJobResult",
    "ToolKind",
    "ToolRuntimeEvent",
    "ToolStatus",
]
