"""Bounded local tool boundary for Alden browser and background AX jobs.

The public runtime intentionally has no browser-profile, endpoint, model, or
Kakao-send options. Browser jobs always use BrowserUseRunner with its owned
Playwright context and loopback MLX binding. AX jobs delegate to the virtual
cursor primitive, which refuses requests that need focus or the real pointer.
"""

from __future__ import annotations

import argparse
import asyncio
import json
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
    AX_ERROR_ACTION_UNSUPPORTED,
    AX_ERROR_EFFECT_UNKNOWN,
    AX_ERROR_TARGET_AMBIGUOUS,
    AX_ERROR_TARGET_INVALID,
    AX_ERROR_TARGET_MISSING,
    AX_ERROR_TIMEOUT,
    AX_ERROR_UNAVAILABLE,
    MAX_BACKGROUND_AX_TIMEOUT_SECONDS,
    BackgroundAxResult,
    BackgroundAxActionError,
    ExactAxResolvedElement,
    ExactAxTarget,
    FocusStealRequired,
    SystemEventsBackgroundAxAdapter,
    VirtualCursor,
    background_virtual_cursor_action,
)
from alden_abort import AbortController, AbortToken
from alden_browser_use import (
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
ERROR_AX_OPT_IN_REQUIRED = "ax_opt_in_required"

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
class ExactAxToolJob:
    """Production-safe exact-target background AX request."""

    job_id: str
    target: ExactAxTarget
    timeout_seconds: float = 2.0
    opt_in: bool = False


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


class _ExactAxAdapter(Protocol):
    def resolve_exact(
        self, target: ExactAxTarget, *, timeout_seconds: float
    ) -> ExactAxResolvedElement: ...

    def perform_exact(self, target: ExactAxTarget, *, timeout_seconds: float) -> bool: ...


ExactAxAdapterFactory = Callable[[], _ExactAxAdapter]


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


class AldenToolRuntime:
    """Run one bounded local tool job without changing the global abort latch."""

    def __init__(
        self,
        state_root: Path,
        *,
        event_sink: EventSink | None = None,
        clock: Callable[[], float] = time_module.time,
        _browser_runner_factory: BrowserRunnerFactory | None = None,
        _ax_adapter_factory: ExactAxAdapterFactory | None = None,
    ) -> None:
        self._abort = AbortController(Path(state_root))
        self._event_sink = event_sink
        self._clock = clock
        self._browser_runner_factory = _browser_runner_factory or (
            lambda token: self._owned_browser_runner(token, state_root=Path(state_root))
        )
        self._ax_adapter_factory = _ax_adapter_factory or SystemEventsBackgroundAxAdapter

    @staticmethod
    def _owned_browser_runner(
        token: AbortToken,
        *,
        state_root: Path | None = None,
    ) -> BrowserUseRunner:
        return BrowserUseRunner(
            token,
            context_factory=DedicatedPlaywrightContext,
            state_root=state_root,
        )

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

    def _run_ax_with_token(
        self, job: AxToolJob, token: AbortToken, *, post_action_unknown: bool = False
    ) -> ToolJobResult:
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
                post_action_unknown=post_action_unknown,
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
        if outcome.error_code == AX_ERROR_EFFECT_UNKNOWN:
            return self._finish(
                job_id=job_id,
                kind=kind,
                status=ToolStatus.FAILED,
                error_code=AX_ERROR_EFFECT_UNKNOWN,
                cursor=cursor,
            )
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
        if outcome.error_code in {
            AX_ERROR_TARGET_INVALID,
            AX_ERROR_TARGET_MISSING,
            AX_ERROR_TARGET_AMBIGUOUS,
            AX_ERROR_ACTION_UNSUPPORTED,
        }:
            return self._finish(
                job_id=job_id,
                kind=kind,
                status=ToolStatus.REJECTED,
                error_code=outcome.error_code,
                cursor=cursor,
            )
        if outcome.error_code in {AX_ERROR_TIMEOUT, AX_ERROR_UNAVAILABLE, AX_ERROR_EFFECT_UNKNOWN}:
            return self._finish(
                job_id=job_id,
                kind=kind,
                status=ToolStatus.FAILED,
                error_code=outcome.error_code,
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

    def run_ax(self, job: AxToolJob) -> ToolJobResult:
        return self._run_ax_with_token(job, self._abort.token())

    def run_exact_ax(self, job: ExactAxToolJob) -> ToolJobResult:
        """Resolve and execute one exact background AXPress through the safe runtime."""

        kind = ToolKind.AX
        if not isinstance(job, ExactAxToolJob):
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
        if job.opt_in is not True:
            return self._finish(
                job_id=job_id,
                kind=kind,
                status=ToolStatus.REFUSED,
                error_code=ERROR_AX_OPT_IN_REQUIRED,
            )
        if (
            isinstance(job.timeout_seconds, bool)
            or not isinstance(job.timeout_seconds, (int, float))
            or not math.isfinite(float(job.timeout_seconds))
            or not 0.0 < float(job.timeout_seconds) <= MAX_BACKGROUND_AX_TIMEOUT_SECONDS
        ):
            return self._finish(
                job_id=job_id,
                kind=kind,
                status=ToolStatus.REJECTED,
                error_code=AX_ERROR_TARGET_INVALID,
            )

        token = self._abort.token()
        if token.is_cancelled():
            return self._finish(
                job_id=job_id,
                kind=kind,
                status=ToolStatus.ABORTED,
                error_code=AX_ABORT_GLOBAL,
            )

        timeout_seconds = float(job.timeout_seconds)
        deadline = time_module.monotonic() + timeout_seconds
        try:
            adapter = self._ax_adapter_factory()
            resolved = adapter.resolve_exact(job.target, timeout_seconds=timeout_seconds)
        except FocusStealRequired:
            return self._finish(
                job_id=job_id,
                kind=kind,
                status=ToolStatus.REFUSED,
                error_code=AX_ABORT_FOCUS_REQUIRED,
            )
        except BackgroundAxActionError as exc:
            status = (
                ToolStatus.REJECTED
                if exc.error_code
                in {
                    AX_ERROR_TARGET_INVALID,
                    AX_ERROR_TARGET_MISSING,
                    AX_ERROR_TARGET_AMBIGUOUS,
                    AX_ERROR_ACTION_UNSUPPORTED,
                }
                else ToolStatus.FAILED
            )
            return self._finish(
                job_id=job_id,
                kind=kind,
                status=status,
                error_code=exc.error_code,
            )
        except Exception:
            return self._finish(
                job_id=job_id,
                kind=kind,
                status=ToolStatus.FAILED,
                error_code=AX_ERROR_UNAVAILABLE,
            )

        if not isinstance(resolved, ExactAxResolvedElement):
            return self._finish(
                job_id=job_id,
                kind=kind,
                status=ToolStatus.FAILED,
                error_code=AX_ERROR_UNAVAILABLE,
            )
        if token.is_cancelled():
            return self._finish(
                job_id=job_id,
                kind=kind,
                status=ToolStatus.ABORTED,
                error_code=AX_ABORT_GLOBAL,
            )

        rect = _bounded_rect(resolved.rect)
        if rect is None:
            return self._finish(
                job_id=job_id,
                kind=kind,
                status=ToolStatus.REJECTED,
                error_code=ERROR_AX_RECT_INVALID,
            )

        def perform_exact() -> bool:
            remaining = deadline - time_module.monotonic()
            if remaining <= 0.0:
                raise BackgroundAxActionError(AX_ERROR_TIMEOUT)
            return adapter.perform_exact(job.target, timeout_seconds=remaining)

        return self._run_ax_with_token(
            AxToolJob(job_id, rect, perform_exact),
            token,
            post_action_unknown=True,
        )


__all__ = [
    "AxToolJob",
    "ExactAxToolJob",
    "BrowserToolJob",
    "AldenToolRuntime",
    "ToolJobResult",
    "ToolKind",
    "ToolRuntimeEvent",
    "ToolStatus",
]


def _default_state_root() -> Path:
    support = Path.home() / "Library/Application Support/openkakao"
    modern = support / "auto-reply"
    legacy = support / "bujamentor"
    if (modern / "enrollment.json").is_file() or not (legacy / "enrollment.json").is_file():
        return modern
    return legacy


def _exact_ax_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one opt-in exact-target background macOS AXPress."
    )
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--bundle-id", required=True)
    parser.add_argument("--window-title", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--identifier", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--description", default="")
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--job-id", default="background-ax")
    parser.add_argument("--state-root", type=Path, default=_default_state_root())
    parser.add_argument("--allow-background-ax", action="store_true")
    return parser


def exact_ax_cli(
    argv: list[str] | None = None,
    *,
    _ax_adapter: _ExactAxAdapter | None = None,
) -> int:
    """Concrete CLI entrypoint; exposes only exact background AXPress."""

    args = _exact_ax_parser().parse_args(argv)
    target = ExactAxTarget(
        pid=args.pid,
        bundle_id=args.bundle_id,
        window_title=args.window_title,
        element_role=args.role,
        element_identifier=args.identifier,
        element_title=args.title,
        element_description=args.description,
    )
    adapter_factory = None if _ax_adapter is None else (lambda: _ax_adapter)
    runtime = AldenToolRuntime(args.state_root, _ax_adapter_factory=adapter_factory)
    result = runtime.run_exact_ax(
        ExactAxToolJob(
            job_id=args.job_id,
            target=target,
            timeout_seconds=args.timeout,
            opt_in=args.allow_background_ax,
        )
    )
    payload: dict[str, object] = {
        "jobId": result.job_id,
        "kind": result.kind.value,
        "status": result.status.value,
        "ok": result.ok,
        "errorCode": result.error_code,
    }
    if result.cursor is not None:
        payload["virtualCursor"] = {"x": result.cursor.x, "y": result.cursor.y}
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(exact_ax_cli())
