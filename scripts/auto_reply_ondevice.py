"""On-device MLX hardware detection, recommendation, and local probe support.

Apple Silicon uses MLX Core/Serve only. The recommendation keeps MLX serving
ids separate from local filesystem paths and never downloads model weights.
The explicit probe prefers the already served Qwen3.8 Flash-Next model, then
falls back to an already present local MLX model without touching KakaoTalk.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol, Sequence

from local_mlx_gateway import MLX_GATEWAY_BASE_URL


MLX_GATEWAY_CANDIDATES = (MLX_GATEWAY_BASE_URL,)
FLASH_NEXT_MODEL_ID = "mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit"
QWEN38_27B_MODEL_ID = "mlx/ddalcu/Qwen3.8-27B-MLX-Serve-4bit"
QWEN38_27B_MIN_MEMORY_GB = 32.0
QWEN38_27B_DEFAULT_LOADED = False
PROBE_TIMEOUT_SECONDS = 45.0
PROBE_HARD_TIMEOUT_SECONDS = 90.0
PROBE_PROMPT_MAX_CHARS = 160
PROBE_PREVIEW_MAX_CHARS = 240
LAST_PROBE_NAME = "ondevice-last-probe.json"
MLX_GATEWAY_MAX_RESPONSE_BYTES = 256 * 1024
MODEL_RESIDENCY_STATE_NAME = "mlx-model-residency.json"
MODEL_RESIDENCY_STATE_MAX_BYTES = 4096
MODEL_RESIDENCY_STATE_MAX_AGE_SECONDS = 30.0
QWEN38_27B_REQUIRED_BYTES = 40 * 1024**3
# Admission includes runtime/KV headroom, not just weight bytes on disk.
FLASH_NEXT_REQUIRED_BYTES = 88 * 1024**3


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


def _local_only_urlopen(request: urllib.request.Request, *, timeout: float):
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _RejectRedirects(),
    )
    return opener.open(request, timeout=timeout)


def _valid_mlx_gateway_url(value: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(str(value or "").strip().rstrip("/"))
        return (
            parsed.scheme == "http"
            and parsed.hostname == "127.0.0.1"
            and parsed.port == 11234
            and parsed.path == "/v1"
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


@dataclass
class HardwareSpec:
    chip: str
    cores: int
    memory_bytes: int
    memory_gb: float
    is_apple_silicon: bool


@dataclass
class EngineRecommendation:
    primary_engine: str
    available_engines: list[str]
    recommended_model: str
    recommended_quant: str
    reason: str
    engine_paths: dict[str, str] = field(default_factory=dict)
    fallback_models: list[str] = field(default_factory=list)
    worker_model_id: str = ""


class SwapStage(str, Enum):
    IDLE = "idle"
    DRAIN = "drain"
    UNLOAD = "unload"
    MEMORY_CHECK = "memory_check"
    LOAD = "load"
    PROBE = "probe"
    ROLLBACK = "rollback"
    READY = "ready"
    ABORTED = "aborted"
    FAILED = "failed"


@dataclass(frozen=True)
class MemoryBudget:
    """Conservative memory view used before a text-model swap."""

    free_bytes: int
    kv_cache_bytes: int = 0
    voice_models_bytes: int = 0
    other_resident_bytes: int = 0

    def __post_init__(self) -> None:
        if any(type(value) is not int or value < 0 for value in (
            self.free_bytes, self.kv_cache_bytes,
            self.voice_models_bytes, self.other_resident_bytes,
        )):
            raise ValueError("memory_budget_unavailable")

    @property
    def usable_bytes(self) -> int:
        reserved = self.kv_cache_bytes + self.voice_models_bytes + self.other_resident_bytes
        return max(0, self.free_bytes - reserved)


@dataclass(frozen=True)
class ModelSwapResult:
    ok: bool
    model: str
    stage: SwapStage
    reason: str
    stages: tuple[str, ...]


@dataclass(frozen=True)
class ManagedModelResidency:
    """Secret-free proof that this product owns the mutable MLX residency."""

    current_model: str | None
    owned_models: tuple[str, ...]
    owner_pid: int
    owner_verified: bool
    drain_verified: bool
    reason: str
    kv_cache_bytes: int = 0
    voice_models_bytes: int = 0
    other_resident_bytes: int = 0


class MlxModelGateway(Protocol):
    def unload(self, model_id: str) -> None: ...

    def load(self, model_id: str) -> None: ...

    def probe(self, model_id: str) -> bool: ...


class ModelResidencyUncertain(RuntimeError):
    """A control request may have committed; no subsequent mutation is safe."""


class ModelSwapCommitError(RuntimeError):
    """Persistence failed before the model selection was committed."""


class HttpMlxModelGateway:
    """Control client for an already-running MLX Serve instance."""

    def __init__(self, base_url: str = "http://127.0.0.1:11234/v1", timeout: float = 30.0):
        if not _valid_mlx_gateway_url(base_url):
            raise ValueError("mlx_gateway_endpoint_invalid")
        self.base_url = base_url.rstrip("/")
        self.timeout = max(0.5, min(float(timeout), 180.0))

    def _model_action(self, model_id: str, action: str) -> None:
        if not _canonical_managed_model_id(model_id):
            raise ValueError("model_not_allowed")
        encoded = urllib.parse.quote(model_id.removeprefix("mlx/"), safe="")
        request = urllib.request.Request(
            f"{self.base_url}/models/{encoded}/{action}",
            data=b"",
            method="POST",
        )
        try:
            with _local_only_urlopen(request, timeout=self.timeout) as response:
                raw = response.read(MLX_GATEWAY_MAX_RESPONSE_BYTES + 1)
            if len(raw) > MLX_GATEWAY_MAX_RESPONSE_BYTES:
                raise ValueError("mlx_gateway_response_too_large")
            expected = (True, "ready") if action == "load" else (False, "unloaded")
            if self._resident_state(model_id) != expected:
                raise ValueError("mlx_gateway_state_unverified")
        except Exception as exc:
            # Even a timeout/HTTP error may follow a committed control operation.
            # In particular, an unloaded catalog row after a timed-out load does
            # not prove the server has stopped loading. Never retry or roll back.
            raise ModelResidencyUncertain("model_residency_uncertain") from exc

    def _resident_state(self, model_id: str) -> tuple[bool, str] | None:
        answered, rows = _read_mlx_gateway_models(base_url=self.base_url, timeout=self.timeout)
        matches = [row for row in rows if _canonical_managed_model_id(row.get("id")) == model_id]
        if not answered or len(matches) != 1 or type(matches[0].get("loaded")) is not bool:
            return None
        return matches[0]["loaded"], matches[0].get("state", "")

    def unload(self, model_id: str) -> None:
        self._model_action(model_id, "unload")

    def load(self, model_id: str) -> None:
        self._model_action(model_id, "load")

    def probe(self, model_id: str) -> bool:
        try:
            return self._probe(model_id)
        except Exception as exc:
            raise ModelResidencyUncertain("model_residency_uncertain") from exc

    def _probe(self, model_id: str) -> bool:
        if not _canonical_managed_model_id(model_id) or self._resident_state(model_id) != (True, "ready"):
            return False
        payload = json.dumps(
            {
                "model": model_id.removeprefix("mlx/"),
                "messages": [{"role": "user", "content": "LOCAL_OK"}],
                "max_tokens": 4,
                "temperature": 0,
                "stream": False,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with _local_only_urlopen(request, timeout=min(self.timeout, PROBE_TIMEOUT_SECONDS)) as response:
            raw = response.read(MLX_GATEWAY_MAX_RESPONSE_BYTES + 1)
        if len(raw) > MLX_GATEWAY_MAX_RESPONSE_BYTES:
            raise ValueError("mlx_gateway_response_too_large")
        body = json.loads(raw.decode("utf-8", "replace"))
        if not isinstance(body, dict) or _canonical_managed_model_id(body.get("model")) != model_id:
            return False
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            return False
        message = choices[0].get("message")
        content = message.get("content") if isinstance(message, dict) else None
        return isinstance(content, str) and bool(content.strip()) and self._resident_state(model_id) == (True, "ready")


class ModelResidencyManager:
    """Drain -> owned unload -> memory check -> load -> probe state machine.

    27B is disabled by default. A later human-controlled call must explicitly
    opt in; constructing this manager or reading status can never load it.
    """

    _ALLOWED_TEXT_MODELS = frozenset({FLASH_NEXT_MODEL_ID, QWEN38_27B_MODEL_ID})

    def __init__(
        self,
        gateway: MlxModelGateway,
        *,
        current_model: str = FLASH_NEXT_MODEL_ID,
        owned_models: Sequence[str] = (),
        memory_budget: Callable[[], MemoryBudget] | None = None,
        required_bytes: dict[str, int] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> None:
        self.gateway = gateway
        self.current_model: str | None = current_model
        self.owned_models = set(owned_models)
        self.memory_budget = memory_budget or (lambda: MemoryBudget(0))
        self.required_bytes = {
            FLASH_NEXT_MODEL_ID: FLASH_NEXT_REQUIRED_BYTES,
            QWEN38_27B_MODEL_ID: QWEN38_27B_REQUIRED_BYTES,
        }
        for model, amount in (required_bytes or {}).items():
            if type(amount) is not int or amount <= 0:
                raise ValueError("memory_budget_unavailable")
            self.required_bytes[model] = max(self.required_bytes.get(model, 0), amount)
        self.cancel_check = cancel_check
        self._condition = threading.Condition()
        self._in_flight = 0
        self._cancelled = False
        self._swap_in_progress = False

    def _is_cancelled(self) -> bool:
        if self._cancelled:
            return True
        if self.cancel_check is None:
            return False
        try:
            return bool(self.cancel_check())
        except Exception:
            # A broken cancellation channel cannot authorize a destructive step.
            return True

    @contextmanager
    def request_lease(self) -> Iterator[None]:
        with self._condition:
            while self._swap_in_progress and not self._is_cancelled():
                self._condition.wait(0.1)
            if self._is_cancelled():
                raise RuntimeError("model_swap_cancelled")
            self._in_flight += 1
        try:
            yield
        finally:
            with self._condition:
                self._in_flight = max(0, self._in_flight - 1)
                self._condition.notify_all()

    def cancel(self) -> None:
        with self._condition:
            self._cancelled = True
            self._condition.notify_all()

    def reset_after_human_resume(self) -> None:
        with self._condition:
            self._cancelled = False

    def _drain(self, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._condition:
            while self._in_flight > 0 and not self._is_cancelled():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(min(0.1, remaining))
            return self._in_flight == 0 and not self._is_cancelled()

    def _begin_swap(self, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._condition:
            while self._swap_in_progress and not self._is_cancelled():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(min(0.1, remaining))
            if self._is_cancelled():
                return False
            self._swap_in_progress = True
            return True

    def _end_swap(self) -> None:
        with self._condition:
            self._swap_in_progress = False
            self._condition.notify_all()

    def swap(
        self,
        target_model: str,
        *,
        allow_27b: bool = QWEN38_27B_DEFAULT_LOADED,
        drain_timeout: float = 30.0,
        commit: Callable[[ModelResidencyManager], None] | None = None,
    ) -> ModelSwapResult:
        stages: list[str] = [SwapStage.DRAIN.value]
        if target_model not in self._ALLOWED_TEXT_MODELS:
            return ModelSwapResult(False, target_model, SwapStage.ABORTED, "model_not_allowed", tuple(stages))
        if target_model == QWEN38_27B_MODEL_ID and not allow_27b:
            return ModelSwapResult(False, target_model, SwapStage.ABORTED, "27b_human_opt_in_required", tuple(stages))
        if not self._begin_swap(drain_timeout):
            reason = "cancelled" if self._is_cancelled() else "drain_timeout"
            return ModelSwapResult(False, target_model, SwapStage.ABORTED, reason, tuple(stages))
        try:
            return self._swap_gated(
                target_model,
                allow_27b=allow_27b,
                drain_timeout=drain_timeout,
                commit=commit,
            )
        finally:
            self._end_swap()

    def _swap_gated(
        self,
        target_model: str,
        *,
        allow_27b: bool,
        drain_timeout: float,
        commit: Callable[[ModelResidencyManager], None] | None,
    ) -> ModelSwapResult:
        stages: list[str] = [SwapStage.DRAIN.value]
        if not self._drain(drain_timeout):
            reason = "cancelled" if self._is_cancelled() else "drain_timeout"
            return ModelSwapResult(False, target_model, SwapStage.ABORTED, reason, tuple(stages))
        if self.current_model is not None and self.current_model not in self.owned_models:
            return ModelSwapResult(False, target_model, SwapStage.ABORTED, "model_owner_unknown", tuple(stages))
        if target_model == self.current_model:
            stages.append(SwapStage.PROBE.value)
            try:
                ok = bool(self.gateway.probe(target_model))
            except ModelResidencyUncertain:
                self.current_model = None
                return ModelSwapResult(False, target_model, SwapStage.FAILED, "model_residency_uncertain", tuple(stages))
            except Exception:
                ok = False
            if self._is_cancelled():
                return ModelSwapResult(False, target_model, SwapStage.ABORTED, "cancelled", tuple(stages))
            if ok and commit is not None:
                try:
                    commit(self)
                except ModelSwapCommitError as exc:
                    return ModelSwapResult(False, target_model, SwapStage.FAILED, str(exc), tuple(stages))
            if ok:
                stages.append(SwapStage.READY.value)
            return ModelSwapResult(
                ok,
                target_model,
                SwapStage.READY if ok else SwapStage.FAILED,
                "already_resident" if ok else "probe_failed",
                tuple(stages),
            )

        previous = self.current_model
        previous_was_owned = previous is not None and previous in self.owned_models
        previous_was_unloaded = False

        def failure_after_unload(stage: SwapStage, reason: str) -> ModelSwapResult:
            if not previous_was_unloaded or previous is None:
                return ModelSwapResult(False, target_model, stage, reason, tuple(stages))

            stages.append(SwapStage.ROLLBACK.value)
            try:
                if self.memory_budget().usable_bytes < self.required_bytes[previous]:
                    raise RuntimeError("insufficient_free_memory")
                self.gateway.load(previous)
            except Exception:
                self.current_model = None
                return ModelSwapResult(
                    False,
                    target_model,
                    SwapStage.FAILED,
                    f"{reason}_rollback_failed",
                    tuple(stages),
                )

            self.owned_models.add(previous)
            try:
                restored = bool(self.gateway.probe(previous))
            except ModelResidencyUncertain:
                self.current_model = None
                return ModelSwapResult(False, target_model, SwapStage.FAILED, f"{reason}_rollback_failed", tuple(stages))
            except Exception:
                restored = False
            if restored:
                self.current_model = previous
                return ModelSwapResult(False, target_model, stage, reason, tuple(stages))

            self.current_model = None
            try:
                self.gateway.unload(previous)
            except Exception:
                pass
            else:
                self.owned_models.discard(previous)
            return ModelSwapResult(
                False,
                target_model,
                SwapStage.FAILED,
                f"{reason}_rollback_failed",
                tuple(stages),
            )

        if previous_was_owned:
            if self._is_cancelled():
                return ModelSwapResult(
                    False, target_model, SwapStage.ABORTED, "cancelled", tuple(stages)
                )
            stages.append(SwapStage.UNLOAD.value)
            try:
                self.gateway.unload(previous)
            except ModelResidencyUncertain:
                self.current_model = None
                return ModelSwapResult(False, target_model, SwapStage.FAILED, "model_residency_uncertain", tuple(stages))
            except Exception:
                return ModelSwapResult(False, target_model, SwapStage.FAILED, "unload_failed", tuple(stages))
            self.owned_models.discard(previous)
            self.current_model = None
            previous_was_unloaded = True

        stages.append(SwapStage.MEMORY_CHECK.value)
        try:
            required = max(0, int(self.required_bytes.get(target_model, 0)))
            budget = self.memory_budget()
            usable_bytes = budget.usable_bytes
        except Exception:
            return failure_after_unload(SwapStage.FAILED, "memory_budget_unavailable")
        if required and usable_bytes < required:
            return failure_after_unload(SwapStage.ABORTED, "insufficient_free_memory")
        if self._is_cancelled():
            return failure_after_unload(SwapStage.ABORTED, "cancelled")

        stages.append(SwapStage.LOAD.value)
        try:
            self.gateway.load(target_model)
        except ModelResidencyUncertain:
            self.current_model = None
            return ModelSwapResult(False, target_model, SwapStage.FAILED, "model_residency_uncertain", tuple(stages))
        except Exception:
            return failure_after_unload(SwapStage.FAILED, "load_failed")
        self.owned_models.add(target_model)

        if self._is_cancelled():
            try:
                self.gateway.unload(target_model)
            except Exception:
                self.current_model = None
                return ModelSwapResult(
                    False,
                    target_model,
                    SwapStage.FAILED,
                    "cancelled_rollback_failed",
                    tuple(stages),
                )
            self.owned_models.discard(target_model)
            return failure_after_unload(SwapStage.ABORTED, "cancelled")

        stages.append(SwapStage.PROBE.value)
        try:
            probe_ok = bool(self.gateway.probe(target_model))
        except ModelResidencyUncertain:
            self.current_model = None
            return ModelSwapResult(False, target_model, SwapStage.FAILED, "model_residency_uncertain", tuple(stages))
        except Exception:
            probe_ok = False
        probe_cancelled = self._is_cancelled()
        failure_reason = "cancelled" if probe_cancelled else "probe_failed" if not probe_ok else ""
        if not failure_reason and commit is not None:
            self.current_model = target_model
            try:
                commit(self)
            except ModelSwapCommitError as exc:
                failure_reason = str(exc)
        if failure_reason:
            self.current_model = None
            try:
                self.gateway.unload(target_model)
            except Exception:
                return ModelSwapResult(
                    False,
                    target_model,
                    SwapStage.FAILED,
                    f"{failure_reason}_rollback_failed",
                    tuple(stages),
                )
            else:
                self.owned_models.discard(target_model)
            return failure_after_unload(
                SwapStage.ABORTED if failure_reason == "cancelled" else SwapStage.FAILED,
                failure_reason,
            )
        self.current_model = target_model
        stages.append(SwapStage.READY.value)
        return ModelSwapResult(True, target_model, SwapStage.READY, "ready", tuple(stages))


def _canonical_managed_model_id(value: Any) -> str:
    candidate = str(value or "").strip()
    if candidate and not candidate.startswith("mlx/"):
        candidate = f"mlx/{candidate}"
    if candidate in {FLASH_NEXT_MODEL_ID, QWEN38_27B_MODEL_ID}:
        return candidate
    return ""


def _pid_owns_mlx_gateway(pid: int) -> bool:
    """Verify the recorded process is an MLX server listening on port 11234."""

    if pid <= 1 or sys.platform != "darwin":
        return False
    try:
        ps = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        command = ps.stdout.strip().casefold()
        if ps.returncode != 0 or "mlx" not in command or "serve" not in command:
            return False
        lsof = subprocess.run(
            [
                "/usr/sbin/lsof",
                "-nP",
                "-a",
                "-p",
                str(pid),
                "-iTCP:11234",
                "-sTCP:LISTEN",
                "-t",
            ],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        return lsof.returncode == 0 and str(pid) in lsof.stdout.split()
    except (OSError, subprocess.SubprocessError):
        return False


def _has_listening_mlx_gateway(
    *,
    pid_reader: Callable[[], Sequence[int]] | None = None,
    pid_probe: Callable[[int], bool] | None = None,
) -> bool:
    """Detect an MLX gateway that exists without an OpenKakao owner record.

    This is deliberately an observation-only probe.  A listening MLX process is
    evidence that another runtime may own the model lifecycle; it is never
    treated as permission to unload, load, or adopt that process.  The seams
    keep the diagnostic deterministic in tests and avoid shell interpolation.
    """

    if sys.platform != "darwin":
        return False
    read_pids = pid_reader
    if read_pids is None:
        def read_pids() -> Sequence[int]:
            result = subprocess.run(
                [
                    "/usr/sbin/lsof",
                    "-nP",
                    "-iTCP:11234",
                    "-sTCP:LISTEN",
                    "-t",
                ],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
            if result.returncode != 0:
                return ()
            pids: list[int] = []
            for token in result.stdout.split():
                try:
                    pid = int(token)
                except (TypeError, ValueError):
                    continue
                if pid > 1:
                    pids.append(pid)
            return tuple(dict.fromkeys(pids))

    probe = pid_probe or _pid_owns_mlx_gateway
    try:
        return any(bool(probe(pid)) for pid in read_pids())
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return False


def _private_json(path: Path) -> dict[str, Any] | None:
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_size > MODEL_RESIDENCY_STATE_MAX_BYTES
            or metadata.st_mode & 0o077
        ):
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def write_managed_model_residency(
    state_root: Path,
    residency: ManagedModelResidency,
    *,
    accepting_requests: bool,
    in_flight: int,
    updated_at: float | None = None,
) -> None:
    """Atomically persist only the bounded ownership facts used by the swap gate."""

    root = Path(state_root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    path = root / MODEL_RESIDENCY_STATE_NAME
    if path.is_symlink():
        raise OSError("model_residency_state_unsafe")
    payload = {
        "schema_version": 1,
        "gateway": MLX_GATEWAY_BASE_URL,
        "owner_pid": int(residency.owner_pid),
        "current_model": residency.current_model,
        "owned_models": list(residency.owned_models),
        "accepting_requests": bool(accepting_requests),
        "in_flight": max(0, int(in_flight)),
        "kv_cache_bytes": max(0, int(residency.kv_cache_bytes)),
        "voice_models_bytes": max(0, int(residency.voice_models_bytes)),
        "other_resident_bytes": max(0, int(residency.other_resident_bytes)),
        "updated_at": float(time.time() if updated_at is None else updated_at),
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MODEL_RESIDENCY_STATE_MAX_BYTES:
        raise OSError("model_residency_state_too_large")
    temp = root / f".{MODEL_RESIDENCY_STATE_NAME}.{os.getpid()}.{threading.get_ident()}.tmp"
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        try:
            directory = os.open(root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            pass
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def read_managed_model_residency(
    state_root: Path,
    *,
    gateway_reader: Callable[[], tuple[bool, list[dict[str, Any]]]] | None = None,
    process_probe: Callable[[int], bool] | None = None,
    now: float | None = None,
) -> ManagedModelResidency:
    """Read and independently attest the model owner; uncertainty stays closed."""

    unknown = lambda reason: ManagedModelResidency(None, (), 0, False, False, reason)
    raw = _private_json(Path(state_root) / MODEL_RESIDENCY_STATE_NAME)
    if raw is None:
        # A live server without this app's private, fresh ownership record is
        # an unmanaged runtime.  Surface that distinction to the operator, but
        # keep the swap gate closed: presence is not ownership.
        if _has_listening_mlx_gateway():
            return unknown("model_owner_unmanaged")
        return unknown("model_owner_unknown")
    if raw.get("schema_version") != 1 or raw.get("gateway") != MLX_GATEWAY_BASE_URL:
        return unknown("model_owner_state_invalid")
    try:
        owner_pid = int(raw.get("owner_pid"))
        updated_at = float(raw.get("updated_at"))
        in_flight = int(raw.get("in_flight"))
        reserves = tuple(
            max(0, int(raw.get(key, 0)))
            for key in ("kv_cache_bytes", "voice_models_bytes", "other_resident_bytes")
        )
    except (TypeError, ValueError, OverflowError):
        return unknown("model_owner_state_invalid")
    current = _canonical_managed_model_id(raw.get("current_model"))
    owned_raw = raw.get("owned_models")
    if not current or not isinstance(owned_raw, list):
        return unknown("model_owner_state_invalid")
    owned = tuple(dict.fromkeys(_canonical_managed_model_id(item) for item in owned_raw))
    if "" in owned or current not in owned:
        return unknown("model_owner_state_invalid")
    stamp = time.time() if now is None else float(now)
    if not math.isfinite(updated_at) or not math.isfinite(stamp) or in_flight < 0 or owner_pid <= 1:
        return unknown("model_owner_state_invalid")
    if updated_at > stamp + 5.0 or stamp - updated_at > MODEL_RESIDENCY_STATE_MAX_AGE_SECONDS:
        return unknown("model_owner_state_stale")
    probe = process_probe or _pid_owns_mlx_gateway
    try:
        process_owned = bool(probe(owner_pid))
    except Exception:
        process_owned = False
    if not process_owned:
        return unknown("model_owner_unknown")
    reader = gateway_reader or (
        lambda: _read_mlx_gateway_models(base_url=MLX_GATEWAY_BASE_URL, timeout=1.5)
    )
    try:
        answered, models = reader()
    except Exception:
        answered, models = False, []
    if not answered:
        return unknown("model_gateway_unavailable")
    loaded: list[str] = []
    other_resident_bytes = 0
    seen: set[str] = set()
    for item in models:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            return unknown("model_residency_mismatch")
        identity = item["id"].removeprefix("mlx/")
        if identity in seen:
            return unknown("model_residency_mismatch")
        seen.add(identity)
        model_id = _canonical_managed_model_id(item.get("id"))
        if item.get("loaded") is True and item.get("state") == "ready":
            if model_id:
                loaded.append(model_id)
            else:
                amount = item.get("bytes_resident")
                if type(amount) is not int or amount <= 0:
                    return unknown("model_residency_mismatch")
                other_resident_bytes += amount
        elif item.get("loaded") is not False or item.get("state") != "unloaded":
            return unknown("model_residency_mismatch")
    if loaded != [current]:
        return unknown("model_residency_mismatch")
    drained = raw.get("accepting_requests") is False and in_flight == 0
    return ManagedModelResidency(
        current,
        owned,
        owner_pid,
        True,
        drained,
        "ready" if drained else "model_drain_unverified",
        reserves[0], reserves[1], max(reserves[2], other_resident_bytes),
    )


MANAGED_MODEL_OWNER_STATES = frozenset(
    {
        "app_owned",
        "model_owner_unknown",
        "model_owner_unmanaged",
        "model_owner_state_invalid",
        "model_owner_state_stale",
        "model_gateway_unavailable",
        "model_residency_mismatch",
        "model_drain_unverified",
    }
)


def managed_residency_status(state_root: Path) -> dict[str, Any]:
    """Bounded, secret-free MLX owner snapshot for the settings surface.

    Only fixed reason codes, booleans, and the allowlisted model id leave this
    function.  Process arguments, filesystem paths, and chat content never do.
    A failing probe is reported as unverified rather than raised, because the
    menu must keep rendering when the local gateway is down.
    """

    unknown = ManagedModelResidency(None, (), 0, False, False, "model_owner_unknown")
    try:
        residency = read_managed_model_residency(Path(state_root))
    except Exception:
        residency = unknown
    if not isinstance(residency, ManagedModelResidency):
        residency = unknown
    if residency.owner_verified:
        owner_state = "app_owned" if residency.drain_verified else "model_drain_unverified"
    else:
        reason = "" if type(residency.reason) is not str else residency.reason
        owner_state = reason if reason in MANAGED_MODEL_OWNER_STATES else "model_owner_unknown"
    current = residency.current_model
    return {
        "ok": True,
        "action": "model-owner-status",
        "owner_state": owner_state,
        "owner_verified": bool(residency.owner_verified),
        "drain_verified": bool(residency.drain_verified),
        "current_model": current if type(current) is str and current else None,
    }


def detect_memory_budget() -> MemoryBudget:
    """Return a conservative reclaimable-memory estimate without network access."""

    if sys.platform != "darwin":
        raise RuntimeError("memory_budget_unavailable")
    try:
        result = subprocess.run(
            ["/usr/bin/vm_stat"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("memory_budget_unavailable") from exc
    if result.returncode != 0:
        raise RuntimeError("memory_budget_unavailable")
    page_match = re.search(r"page size of\s+(\d+) bytes", result.stdout)
    if page_match is None:
        raise RuntimeError("memory_budget_unavailable")
    page_size = int(page_match.group(1))
    pages = 0
    for label in ("Pages free", "Pages inactive", "Pages speculative"):
        match = re.search(rf"^{re.escape(label)}:\s+(\d+)\.", result.stdout, re.MULTILINE)
        if match is not None:
            pages += int(match.group(1))
    free_bytes = pages * page_size
    if free_bytes <= 0:
        raise RuntimeError("memory_budget_unavailable")
    return MemoryBudget(free_bytes=free_bytes)


def _is_apple_silicon_chip(chip: str) -> bool:
    return chip.strip().startswith("Apple ")


def detect_hardware() -> HardwareSpec:
    """Read CPU model, core count and memory from macOS sysctl."""
    chip = "Unknown CPU"
    cores = os.cpu_count() or 4
    mem_bytes = 16 * 1024 * 1024 * 1024

    if sys.platform == "darwin":
        try:
            chip = subprocess.check_output(
                ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except Exception:
            pass
        try:
            cores = int(
                subprocess.check_output(
                    ["/usr/sbin/sysctl", "-n", "hw.ncpu"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                ).strip()
            )
        except Exception:
            pass
        try:
            mem_bytes = int(
                subprocess.check_output(
                    ["/usr/sbin/sysctl", "-n", "hw.memsize"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                ).strip()
            )
        except Exception:
            pass

    return HardwareSpec(
        chip=chip,
        cores=cores,
        memory_bytes=mem_bytes,
        memory_gb=round(mem_bytes / (1024**3), 1),
        is_apple_silicon=_is_apple_silicon_chip(chip),
    )


def _find_executable(names: Sequence[str], extra_paths: Sequence[Path]) -> str:
    for name in names:
        found = shutil.which(name)
        if found and os.access(found, os.X_OK):
            return found
    for candidate in extra_paths:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return ""


def _read_mlx_gateway_models(
    *,
    base_url: str,
    timeout: float = 1.5,
) -> tuple[bool, list[dict[str, Any]]]:
    if not _valid_mlx_gateway_url(base_url):
        return False, []
    try:
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/models",
            headers={"Accept": "application/json"},
        )
        with _local_only_urlopen(req, timeout=max(0.1, min(timeout, 3.0))) as resp:
            raw = resp.read(MLX_GATEWAY_MAX_RESPONSE_BYTES + 1)
        if len(raw) > MLX_GATEWAY_MAX_RESPONSE_BYTES:
            return False, []
        payload = json.loads(raw.decode("utf-8", "replace"))
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            return False, []
    except Exception:
        return False, []

    models: list[dict[str, Any]] = []
    for item in payload["data"]:
        if not isinstance(item, dict):
            return False, []
        model_id = str(item.get("id") or "").strip()
        owner = str(item.get("owned_by") or "").strip()
        if model_id and (model_id.startswith("mlx/") or owner.casefold() == "mlx-serve"):
            model: dict[str, Any] = {"id": model_id, "owned_by": owner}
            if "loaded" in item:
                model["loaded"] = item.get("loaded")
            if "state" in item:
                model["state"] = str(item.get("state") or "")
            if "bytes_resident" in item:
                model["bytes_resident"] = item["bytes_resident"]
            models.append(model)
    return True, models


def detect_mlx_gateway_models(
    *,
    base_url: str = MLX_GATEWAY_BASE_URL,
    timeout: float = 1.5,
) -> list[dict[str, Any]]:
    """Return only MLX models advertised by one already running local gateway."""
    _answered, models = _read_mlx_gateway_models(base_url=base_url, timeout=timeout)
    return models


def _gateway_has_qwen38(models: Sequence[dict[str, Any]]) -> bool:
    for item in models:
        model_id = str(item.get("id") or "").casefold()
        if "qwen3.8-flash-next" in model_id or "qwen3.8-27b" in model_id:
            return True
    return False


def discover_mlx_gateway(
    *,
    candidates: Sequence[str] = MLX_GATEWAY_CANDIDATES,
    timeout: float = 1.5,
) -> tuple[str, list[dict[str, Any]]]:
    """Find an existing loopback MLX gateway without starting any service."""
    first_answered: tuple[str, list[dict[str, Any]]] = ("", [])
    for base_url in candidates:
        answered, models = _read_mlx_gateway_models(base_url=base_url, timeout=timeout)
        if not answered:
            continue
        if not first_answered[0]:
            first_answered = (base_url, models)
        if _gateway_has_qwen38(models):
            return base_url, models
    return first_answered


def detect_engine_paths(
    *,
    gateway_models: Sequence[dict[str, Any]] | None = None,
    gateway_base_url: str = "",
) -> dict[str, str]:
    """Locate MLX Core/Serve executables and the existing local MLX gateway."""
    home = Path.home()
    paths: dict[str, str] = {}
    mlx_lm = _find_executable(["mlx_lm"], [home / ".local/bin/mlx_lm"])
    if mlx_lm:
        paths["mlx_lm"] = mlx_lm
    mlx_serve = _find_executable(["mlx-serve"], [home / ".local/bin/mlx-serve"])
    if mlx_serve:
        paths["mlx-serve"] = mlx_serve
    if gateway_models is None:
        discovered_base, advertised = discover_mlx_gateway()
    else:
        advertised = list(gateway_models)
        discovered_base = gateway_base_url.strip() or (MLX_GATEWAY_BASE_URL if advertised else "")
    if discovered_base and advertised:
        paths["mlx-gateway"] = discovered_base
    return paths


def detect_available_engines() -> list[str]:
    return list(detect_engine_paths().keys())


def detect_local_qwen_models(home: Path | None = None) -> dict[str, str]:
    """Find already present Qwen3.8 directories without touching model hubs."""
    root = (home or Path.home()) / "Models"
    if not root.is_dir():
        return {}
    found: dict[str, str] = {}
    try:
        children = list(root.iterdir())
    except OSError:
        return {}
    for child in children:
        if child.is_dir() and "qwen3.8" in child.name.casefold():
            found[child.name] = str(child)
    return found


def _model_id(models: Sequence[dict[str, Any]], marker: str) -> str:
    wanted = marker.casefold()
    for item in models:
        model_id = str(item.get("id") or "").strip()
        owner = str(item.get("owned_by") or "").strip().casefold()
        if not (model_id.startswith("mlx/") or owner == "mlx-serve"):
            continue
        if wanted not in model_id.casefold():
            continue
        state = str(item.get("state") or "").strip().casefold()
        if item.get("loaded") is False or state in {"unloaded", "error", "failed"}:
            continue
        if model_id:
            return model_id
    return ""


def _local_model_path(models: dict[str, str], marker: str) -> str:
    wanted = marker.casefold()
    for name, path in models.items():
        if wanted in f"{name} {path}".casefold() and Path(path).is_dir():
            return path
    return ""


def recommend_ondevice_setup(
    hw: HardwareSpec | None = None,
    engines: dict[str, str] | None = None,
    *,
    gateway_models: Sequence[dict[str, Any]] | None = None,
    local_models: dict[str, str] | None = None,
) -> EngineRecommendation:
    """Recommend a Qwen3.8 model for the MLX Core/Serve path."""
    spec = hw or detect_hardware()
    gateway_base_url = ""
    if engines is None:
        if gateway_models is None:
            gateway_base_url, advertised = discover_mlx_gateway()
        else:
            advertised = list(gateway_models)
            gateway_base_url = MLX_GATEWAY_BASE_URL if advertised else ""
        raw_paths = detect_engine_paths(
            gateway_models=advertised,
            gateway_base_url=gateway_base_url,
        )
    else:
        advertised = list(gateway_models or [])
        raw_paths = dict(engines)
    engine_paths = {
        key: value
        for key, value in raw_paths.items()
        if key in {"mlx_lm", "mlx-serve", "mlx-gateway"}
    }
    available = list(engine_paths.keys())
    local = (
        detect_local_qwen_models()
        if local_models is None and engines is None
        else dict(local_models or {})
    )

    served_flash = _model_id(advertised, "Qwen3.8-Flash-Next")
    local_flash = _local_model_path(local, "Qwen3.8-Flash-Next")

    if not spec.is_apple_silicon:
        return EngineRecommendation(
            primary_engine="mlx-unavailable",
            available_engines=available,
            recommended_model="",
            recommended_quant="",
            reason=f"{spec.chip} · MLX Core/Serve는 Apple Silicon에서만 사용합니다",
            engine_paths=engine_paths,
            fallback_models=[FLASH_NEXT_MODEL_ID],
            worker_model_id=served_flash or FLASH_NEXT_MODEL_ID,
        )

    primary = "mlx-serve"
    model = ""
    quant = ""
    model_note = ""

    if "mlx-gateway" in engine_paths:
        primary = "mlx-serve"
        if served_flash:
            model = served_flash
            quant = "mixed 4/8bit"
            model_note = "게이트웨이가 실제 제공 중인 Qwen3.8 Flash-Next"
    if not model and "mlx_lm" in engine_paths:
        if local_flash:
            primary = "mlx_lm"
            model = local_flash
            quant = "local"
            model_note = "로컬 경로에서 확인한 Qwen3.8 Flash-Next"
    if not model and "mlx-serve" in engine_paths:
        primary = "mlx-serve"
        model = FLASH_NEXT_MODEL_ID
        quant = "mixed 4/8bit"
        model_note = "MLX Serve는 설치됐지만 해당 가중치 제공 여부는 아직 확인되지 않음"
    if not model and not available:
        primary = "mlx-serve"
        model = FLASH_NEXT_MODEL_ID
        quant = "mixed 4/8bit"
        model_note = "설치된 MLX Core/Serve 엔진을 찾지 못함"

    reason = (
        f"{spec.chip} ({spec.memory_gb}GB 통합 메모리) · MLX Core/Serve · {model_note}"
    )
    fallbacks = [FLASH_NEXT_MODEL_ID]
    if served_flash and served_flash not in fallbacks:
        fallbacks.insert(0, served_flash)

    return EngineRecommendation(
        primary_engine=primary,
        available_engines=available,
        recommended_model=model,
        recommended_quant=quant,
        reason=reason,
        engine_paths=engine_paths,
        fallback_models=fallbacks,
        worker_model_id=served_flash or FLASH_NEXT_MODEL_ID,
    )


def verify_ondevice_setup(rec: EngineRecommendation | None = None) -> dict[str, Any]:
    """Read-only verification of the selected MLX runtime and local model."""
    engine = ""
    model = ""
    checks: list[dict[str, Any]] = []
    errors: list[str] = []

    def record(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})
        if not ok:
            errors.append(detail)

    try:
        recommendation = rec or recommend_ondevice_setup()
        engine = recommendation.primary_engine
        model = recommendation.recommended_model
        if engine == "mlx-serve":
            gateway = recommendation.engine_paths.get("mlx-gateway", "")
            serve_exe = recommendation.engine_paths.get("mlx-serve", "")
            runtime_ok = bool(gateway) or bool(
                serve_exe and Path(serve_exe).is_file() and os.access(serve_exe, os.X_OK)
            )
            record(
                "mlx_runtime",
                runtime_ok,
                "MLX Serve/gateway 확인" if runtime_ok else "MLX Serve/gateway를 찾지 못했습니다",
            )
            if gateway and model:
                models = detect_mlx_gateway_models(base_url=gateway)
                ids = {str(item.get("id") or "") for item in models}
                record(
                    "served_model",
                    model in ids,
                    f"게이트웨이 모델 확인: {model}" if model in ids else f"게이트웨이에 모델이 없습니다: {model}",
                )
            else:
                record("served_model", False, "게이트웨이에서 제공 중인 모델을 확인하지 못했습니다")
        elif engine == "mlx_lm":
            mlx_lm = recommendation.engine_paths.get("mlx_lm", "")
            executable_ok = bool(
                mlx_lm and Path(mlx_lm).is_file() and os.access(mlx_lm, os.X_OK)
            )
            record(
                "mlx_lm_executable",
                executable_ok,
                f"mlx_lm: {mlx_lm}" if executable_ok else "실행 가능한 mlx_lm을 찾지 못했습니다",
            )
            model_ok = bool(model and Path(model).is_dir())
            record(
                "model_directory",
                model_ok,
                f"로컬 모델 확인: {model}" if model_ok else f"로컬 모델 디렉터리가 없습니다: {model or '-'}",
            )
        else:
            record("mlx_runtime", False, "Apple Silicon MLX Core/Serve 구성이 아닙니다")
    except Exception as exc:
        errors.append(f"온디바이스 구성 확인 실패: {type(exc).__name__}")

    return {
        "ok": bool(checks) and all(check["ok"] for check in checks) and not errors,
        "engine": engine,
        "model": model,
        "checks": checks,
        "errors": errors,
    }


def _default_state_root() -> Path:
    parent = Path.home() / "Library" / "Application Support" / "openkakao"
    modern = parent / "auto-reply"
    legacy = parent / "bujamentor"
    if (modern / "enrollment.json").is_file() or not (legacy / "enrollment.json").is_file():
        return modern
    return legacy


def _sanitize_probe_text(value: Any, limit: int) -> str:
    """Keep diagnostic prompt/preview printable, single-line, and shell inert."""
    allowed_punct = set(" .?!,:-_/()[]")
    cleaned = []
    for char in str(value or ""):
        if char.isspace():
            cleaned.append(" ")
        elif char.isalnum() or char in allowed_punct:
            cleaned.append(char)
    return " ".join("".join(cleaned).split())[: max(0, limit)]


def _probe_record(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": str(result.get("timestamp") or "")[:64],
        "engine": _sanitize_probe_text(result.get("engine"), 64),
        "model": _sanitize_probe_text(result.get("model"), 180),
        "latency_ms": max(0, int(result.get("latency_ms") or 0)),
        "ok": bool(result.get("ok")),
        "preview": _sanitize_probe_text(result.get("preview"), PROBE_PREVIEW_MAX_CHARS),
    }


def _persist_last_probe(result: dict[str, Any], state_root: Path | None = None) -> None:
    root = Path(state_root) if state_root is not None else _default_state_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        path = root / LAST_PROBE_NAME
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(_probe_record(result), ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except Exception:
        pass


def read_last_probe(state_root: Path | None = None) -> dict[str, Any] | None:
    root = Path(state_root) if state_root is not None else _default_state_root()
    try:
        raw = json.loads((root / LAST_PROBE_NAME).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        return _probe_record(raw)
    except Exception:
        return None


def _finish_probe(
    *,
    started: float,
    engine: str,
    model: str,
    ok: bool,
    preview: str = "",
    errors: Sequence[str] = (),
    state_root: Path | None = None,
) -> dict[str, Any]:
    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "engine": engine,
        "model": model,
        "latency_ms": max(0, int((time.monotonic() - started) * 1000)),
        "ok": bool(ok),
        "preview": _sanitize_probe_text(preview, PROBE_PREVIEW_MAX_CHARS),
        "errors": [str(error)[:160] for error in errors if str(error).strip()][:4],
    }
    _persist_last_probe(result, state_root)
    return result


def probe_ondevice_generation(
    rec: EngineRecommendation | None = None,
    *,
    state_root: Path | None = None,
    prompt: str = "LOCAL_OK 한 단어로 답하세요",
    timeout: float = PROBE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Run one bounded local generation against an already present MLX runtime."""
    started = time.monotonic()
    safe_prompt = _sanitize_probe_text(prompt, PROBE_PROMPT_MAX_CHARS) or "LOCAL_OK"
    effective_timeout = max(
        0.1,
        min(float(timeout), PROBE_HARD_TIMEOUT_SECONDS),
    )

    try:
        recommendation = rec or recommend_ondevice_setup()
        gateway = recommendation.engine_paths.get("mlx-gateway", "")
        if gateway:
            advertised = detect_mlx_gateway_models(
                base_url=gateway,
                timeout=min(effective_timeout, 3.0),
            )
            model = _model_id(advertised, "Qwen3.8-Flash-Next")
            if not model:
                return _finish_probe(
                    started=started,
                    engine="mlx-serve-gateway",
                    model="",
                    ok=False,
                    errors=["flash_next_not_advertised"],
                    state_root=state_root,
                )
            payload = json.dumps(
                {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "Return a very short diagnostic reply."},
                        {"role": "user", "content": safe_prompt},
                    ],
                    "max_tokens": 16,
                    "temperature": 0.0,
                },
                ensure_ascii=False,
            ).encode("utf-8")
            session_digest = hashlib.sha256(b"openkakao/ondevice/probe/v1").hexdigest()[:32]
            req = urllib.request.Request(
                f"{gateway.rstrip('/')}/chat/completions",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer not-needed",
                    "x-opencode-session": f"ocx_{session_digest}",
                },
            )
            try:
                with _local_only_urlopen(req, timeout=effective_timeout) as resp:
                    raw = resp.read(MLX_GATEWAY_MAX_RESPONSE_BYTES + 1)
                if len(raw) > MLX_GATEWAY_MAX_RESPONSE_BYTES:
                    raise ValueError("mlx_gateway_response_too_large")
                body = json.loads(raw.decode("utf-8", "replace"))
                content = str(body["choices"][0]["message"]["content"] or "").strip()
                if not content:
                    return _finish_probe(
                        started=started,
                        engine="mlx-serve-gateway",
                        model=model,
                        ok=False,
                        errors=["empty_output"],
                        state_root=state_root,
                    )
                return _finish_probe(
                    started=started,
                    engine="mlx-serve-gateway",
                    model=model,
                    ok=True,
                    preview=content,
                    state_root=state_root,
                )
            except urllib.error.HTTPError as exc:
                return _finish_probe(
                    started=started,
                    engine="mlx-serve-gateway",
                    model=model,
                    ok=False,
                    errors=[f"http_{exc.code}"],
                    state_root=state_root,
                )
            except Exception as exc:
                return _finish_probe(
                    started=started,
                    engine="mlx-serve-gateway",
                    model=model,
                    ok=False,
                    errors=[f"gateway_{type(exc).__name__}"],
                    state_root=state_root,
                )

        mlx_lm = recommendation.engine_paths.get("mlx_lm", "")
        local_models = detect_local_qwen_models()
        local_flash = _local_model_path(local_models, "Qwen3.8-Flash-Next")
        local_model = local_flash
        if not mlx_lm or not Path(mlx_lm).is_file() or not local_model:
            return _finish_probe(
                started=started,
                engine="mlx_lm" if mlx_lm else "mlx-unavailable",
                model=local_model,
                ok=False,
                errors=["missing_engine_or_local_weights"],
                state_root=state_root,
            )

        env = os.environ.copy()
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
        command = [
            mlx_lm,
            "generate",
            "--model",
            local_model,
            "--prompt",
            safe_prompt,
            "--max-tokens",
            "16",
            "--temp",
            "0.0",
        ]
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        try:
            stdout, _stderr = proc.communicate(timeout=effective_timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return _finish_probe(
                started=started,
                engine="mlx_lm",
                model=local_model,
                ok=False,
                errors=["probe_timeout"],
                state_root=state_root,
            )
        if proc.returncode != 0:
            return _finish_probe(
                started=started,
                engine="mlx_lm",
                model=local_model,
                ok=False,
                errors=[f"mlx_lm_exit_{proc.returncode}"],
                state_root=state_root,
            )
        if not str(stdout or "").strip():
            return _finish_probe(
                started=started,
                engine="mlx_lm",
                model=local_model,
                ok=False,
                errors=["empty_output"],
                state_root=state_root,
            )
        return _finish_probe(
            started=started,
            engine="mlx_lm",
            model=local_model,
            ok=True,
            preview=stdout,
            state_root=state_root,
        )
    except Exception as exc:
        return _finish_probe(
            started=started,
            engine="mlx",
            model="",
            ok=False,
            errors=[f"probe_{type(exc).__name__}"],
            state_root=state_root,
        )


def _display_model(model: str) -> str:
    folded = model.casefold()
    if "qwen3.8-flash-next" in folded:
        return "Qwen3.8 Flash-Next"
    if "qwen3.8-27b" in folded:
        return "Qwen3.8 27B"
    return Path(model).name if model else "모델 미확인"


ONDEVICE_RUNTIME_VERIFIED_LABEL = "런타임·가중치 확인됨"
ONDEVICE_RUNTIME_UNVERIFIED_LABEL = "런타임 미확인"
ONDEVICE_PROBE_OK_LABEL = "실추론 통과"
ONDEVICE_PROBE_FAIL_LABEL = "실추론 실패"


def ondevice_status_strings(
    *,
    chip: str,
    memory_gb: float,
    reason: str,
    recommended_model: str,
    verified: bool,
    last_probe: dict[str, Any] | None,
) -> tuple[list[str], list[str]]:
    """Build the settings window's on-device status and detail text.

    Pure: no filesystem, network, or clock access, so the rendered-UI token
    guard in tests can exercise the exact strings the product ships. The
    labels deliberately avoid the tokens the desktop UI contract bans.
    """
    verify_label = (
        ONDEVICE_RUNTIME_VERIFIED_LABEL if verified else ONDEVICE_RUNTIME_UNVERIFIED_LABEL
    )
    status_bits = [
        f"온디바이스 감지: {chip} ({int(memory_gb)}GB RAM)",
        "MLX Core/Serve",
        _display_model(recommended_model),
        verify_label,
    ]
    detail_bits = [reason, recommended_model]
    if last_probe:
        probe_label = ONDEVICE_PROBE_OK_LABEL if last_probe.get("ok") else ONDEVICE_PROBE_FAIL_LABEL
        status_bits.append(f"{probe_label} ({_display_model(str(last_probe.get('model') or ''))})")
        detail_bits.append(
            f"최근 실추론: {last_probe.get('engine') or '-'} · {last_probe.get('model') or '-'} · "
            f"{last_probe.get('latency_ms') or 0}ms · {probe_label}"
        )
    return status_bits, detail_bits


def ondevice_summary_dict(state_root: Path | None = None) -> dict[str, Any]:
    hw = detect_hardware()
    rec = recommend_ondevice_setup(hw)
    verification = verify_ondevice_setup(rec)
    last_probe = read_last_probe(state_root)
    status_bits, detail_bits = ondevice_status_strings(
        chip=hw.chip,
        memory_gb=hw.memory_gb,
        reason=rec.reason,
        recommended_model=rec.recommended_model,
        verified=bool(verification.get("ok")),
        last_probe=last_probe,
    )
    return {
        "hardware": asdict(hw),
        "recommendation": asdict(rec),
        "verification": verification,
        "last_probe": last_probe,
        "status_label": " · ".join(status_bits),
        "status_detail": " · ".join(bit for bit in detail_bits if bit),
    }


def download_command(_rec: EngineRecommendation) -> str:
    """Downloads are intentionally unsupported by the on-device setup helper."""
    return ""


def generate_command(rec: EngineRecommendation, prompt: str) -> str:
    """Return a diagnostic description without interpolating text into a shell."""
    safe_prompt = _sanitize_probe_text(prompt, PROBE_PROMPT_MAX_CHARS)
    gateway = rec.engine_paths.get("mlx-gateway", "")
    if rec.primary_engine == "mlx-serve" and rec.recommended_model and gateway:
        return f"MLX Serve POST {gateway.rstrip('/')}/chat/completions model={rec.recommended_model} prompt={safe_prompt}"
    if rec.primary_engine == "mlx_lm" and rec.recommended_model:
        return f"mlx_lm generate --model {rec.recommended_model} --prompt {safe_prompt} --max-tokens 16"
    return ""


if __name__ == "__main__":
    print(json.dumps(ondevice_summary_dict(), ensure_ascii=False, indent=2))
