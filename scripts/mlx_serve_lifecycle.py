"""App-owned MLX Serve lifecycle: launch, attest, and stop a local server.

The menu-bar app may only manage a server that it started *and* can prove it
started.  A listening MLX gateway without this app's private ownership record
is a foreign runtime: it is reported, never adopted, and never signalled.

Every stage is fail-closed, and process, network, and filesystem access flows
through injectable seams so the contract is verifiable without spawning a real
server, loading model weights, or touching another process.  Commands are
built as argument tuples; no shell string is ever constructed or interpolated.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import signal
import stat
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Sequence


MLX_SERVE_STATE_NAME = "mlx-app-owned-server.json"
MLX_SERVE_STATE_MAX_BYTES = 4096
MLX_SERVE_SCHEMA_VERSION = 1
MLX_SERVE_DEFAULT_HOST = "127.0.0.1"
MLX_SERVE_DEFAULT_PORT = 11234
MLX_SERVE_CATALOG_MAX_BYTES = 256 * 1024
MLX_SERVE_DEFAULT_WAIT_BUDGET_SECONDS = 120.0
MLX_SERVE_DEFAULT_POLL_INTERVAL_SECONDS = 1.0
# The attested command prefix is: exe, --serve, --model, <dir>, --model-dir, <dir>.
MLX_SERVE_PREFIX_TOKENS = 6
MLX_SERVE_MIN_KV_QUANT = 2
MLX_SERVE_MAX_KV_QUANT = 16


class LaunchStage(str, Enum):
    VALIDATE = "validate"
    EXECUTABLE = "executable"
    MODEL_DIR = "model_dir"
    MODELS_DIR = "models_dir"
    PORT_CHECK = "port_check"
    MEMORY_CHECK = "memory_check"
    STATE_CHECK = "state_check"
    SPAWN = "spawn"
    STARTUP = "startup"
    ATTEST = "attest"
    ALREADY_RUNNING = "already_running"
    READY = "ready"
    FAILED = "failed"


LAUNCH_REASONS = frozenset(
    {
        "launch_ready",
        "launch_already_running",
        "launch_invalid_spec",
        "launch_executable_unsafe",
        "launch_executable_unverified",
        "launch_model_dir_missing",
        "launch_models_dir_missing",
        "launch_port_in_use",
        "launch_memory_insufficient",
        "launch_memory_unavailable",
        "launch_state_unsafe",
        "launch_spawn_failed",
        "launch_spawn_exited",
        "launch_startup_timeout",
        "launch_attest_failed",
        "launch_state_write_failed",
    }
)

STOP_REASONS = frozenset(
    {
        "stop_stopped",
        "stop_no_owned_server",
        "stop_owner_mismatch",
        "stop_signal_failed",
    }
)

ATTEST_REASONS = frozenset(
    {
        "attest_not_alive",
        "attest_command_mismatch",
        "attest_not_listening",
        "attest_unknown",
    }
)

MLX_SERVE_OWNER_STATES = frozenset(
    {
        "app_owned",
        "stopped",
        "foreign_listener",
        "state_invalid",
        "state_stale",
    }
)


def _model_basename(value: Any) -> str:
    token = str(value or "").strip().rstrip("/")
    if token.startswith("mlx/"):
        token = token[4:]
    return token.rsplit("/", 1)[-1]


def _is_directory(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _command_prefix_matches(command: str, prefix: Sequence[str]) -> bool:
    """Compare a ps command line against an argv prefix, tolerating spaces.

    Application bundles on macOS live under names such as "MLX Core.app", so
    argument values legitimately contain spaces.  Splitting the observed
    command on whitespace would therefore shatter a path into several tokens
    and reject a perfectly valid process.  Collapsing runs of whitespace on
    both sides keeps the comparison exact without that failure mode.
    """

    if not command:
        return False
    desired = " ".join(str(token) for token in prefix)
    if not desired:
        return False
    observed = " ".join(str(command).split())
    return observed == desired or observed.startswith(desired + " ")


@dataclass(frozen=True)
class MlxLaunchSpec:
    """Everything needed to launch an app-owned mlx-serve process.

    validate() is purely structural.  It never touches the filesystem or the
    network, so a malformed spec is rejected before any side effect occurs.
    """

    executable: Path
    resident_model_dir: Path
    models_dir: Path
    log_path: Path
    host: str = MLX_SERVE_DEFAULT_HOST
    port: int = MLX_SERVE_DEFAULT_PORT
    ctx_size: int = 32768
    request_timeout: float = 120.0
    max_concurrent: int = 4
    kv_quant: int = 8
    max_resident_models: int = 1
    minimum_free_bytes: int = 24 * 1024**3

    @property
    def resident_model_name(self) -> str:
        return self.resident_model_dir.name

    def validate(self) -> str:
        if self.host != MLX_SERVE_DEFAULT_HOST:
            return "launch_invalid_spec"
        if type(self.port) is not int or not 1024 <= self.port <= 65535:
            return "launch_invalid_spec"
        for name in ("executable", "resident_model_dir", "models_dir", "log_path"):
            value = getattr(self, name)
            if not isinstance(value, Path) or not value.is_absolute():
                return "launch_invalid_spec"
        if type(self.ctx_size) is not int or self.ctx_size < 256:
            return "launch_invalid_spec"
        if type(self.max_concurrent) is not int or self.max_concurrent < 1:
            return "launch_invalid_spec"
        if type(self.max_resident_models) is not int or self.max_resident_models < 1:
            return "launch_invalid_spec"
        if type(self.kv_quant) is not int or not (
            MLX_SERVE_MIN_KV_QUANT <= self.kv_quant <= MLX_SERVE_MAX_KV_QUANT
        ):
            return "launch_invalid_spec"
        if not math.isfinite(self.request_timeout) or self.request_timeout <= 0:
            return "launch_invalid_spec"
        if not math.isfinite(float(self.minimum_free_bytes)) or self.minimum_free_bytes < 0:
            return "launch_invalid_spec"
        if not self.resident_model_name or "/" in self.resident_model_name:
            return "launch_invalid_spec"
        return ""

    def command(self) -> tuple[str, ...]:
        return (
            str(self.executable),
            "--serve",
            "--model",
            str(self.resident_model_dir),
            "--model-dir",
            str(self.models_dir),
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--ctx-size",
            str(self.ctx_size),
            "--request-timeout",
            str(self.request_timeout),
            "--max-concurrent",
            str(self.max_concurrent),
            "--kv-quant",
            str(self.kv_quant),
            "--max-resident-models",
            str(self.max_resident_models),
        )

    def prefix(self) -> tuple[str, ...]:
        return self.command()[:MLX_SERVE_PREFIX_TOKENS]

    def command_digest(self) -> str:
        joined = "\x1f".join(self.command()).encode("utf-8")
        return hashlib.sha256(joined).hexdigest()[:16]


@dataclass(frozen=True)
class AppOwnedServerRecord:
    """Bounded ownership record; never carries chat content or secrets."""

    pid: int
    host: str
    port: int
    model: str
    prefix: tuple[str, ...]
    command_digest: str
    started_at: float

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": MLX_SERVE_SCHEMA_VERSION,
            "pid": int(self.pid),
            "host": self.host,
            "port": int(self.port),
            "model": self.model,
            "prefix": list(self.prefix),
            "command_digest": self.command_digest,
            "started_at": float(self.started_at),
        }


@dataclass(frozen=True)
class LaunchResult:
    ok: bool
    stage: LaunchStage
    reason: str
    stages: tuple[str, ...]
    pid: int | None
    model: str | None

    def report(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "action": "mlx-server-launch",
            "stage": self.stage.value,
            "reason": self.reason if self.reason in LAUNCH_REASONS else "launch_invalid_spec",
            "model": self.model if type(self.model) is str and self.model else None,
            "stages": list(self.stages),
        }


@dataclass(frozen=True)
class StopResult:
    ok: bool
    reason: str
    pid: int | None

    def report(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "action": "mlx-server-stop",
            "reason": self.reason if self.reason in STOP_REASONS else "stop_no_owned_server",
        }


def _app_bundle_root(path: Path) -> Path | None:
    for parent in path.parents:
        if parent.name.endswith(".app"):
            return parent
    return None


def _default_codesign(bundle: Path) -> str:
    try:
        result = subprocess.run(
            ["/usr/bin/codesign", "--verify", "--strict", str(bundle)],
            capture_output=True,
            text=True,
            timeout=10.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "launch_executable_unverified"
    return "" if result.returncode == 0 else "launch_executable_unverified"


def validate_executable(
    executable: Path,
    *,
    home: Path | None = None,
    codesign: Callable[[Path], str] | None = None,
) -> str:
    """Refuse anything that is not a signed, app-local mlx-serve binary."""

    path = Path(executable)
    if not path.is_absolute() or path.name != "mlx-serve":
        return "launch_executable_unsafe"
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        return "launch_executable_unsafe"
    root = Path(home) if home is not None else Path.home()
    try:
        home_root = root.resolve(strict=False)
    except OSError:
        return "launch_executable_unsafe"
    allowed = False
    for base in (home_root, Path("/Applications")):
        try:
            resolved.relative_to(base)
            allowed = True
            break
        except ValueError:
            continue
    if not allowed:
        return "launch_executable_unsafe"
    try:
        metadata = path.lstat()
    except OSError:
        return "launch_executable_unsafe"
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        return "launch_executable_unsafe"
    if not metadata.st_mode & 0o111:
        return "launch_executable_unsafe"
    bundle = _app_bundle_root(resolved)
    if bundle is None:
        # Only a code-signed application bundle may own a resident server.
        return "launch_executable_unverified"
    probe = codesign or _default_codesign
    try:
        reason = probe(bundle)
    except Exception:
        return "launch_executable_unverified"
    if not reason:
        return ""
    return reason if reason in LAUNCH_REASONS else "launch_executable_unverified"


def _default_popen(command: Sequence[str], log_path: Path) -> Any:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(log_path, "ab", buffering=0)
    try:
        process = subprocess.Popen(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    except BaseException:
        handle.close()
        raise
    # Keep the descriptor alive for the child's lifetime.
    process._openkakao_log_handle = handle  # type: ignore[attr-defined]
    return process


def _default_alive(pid: int) -> bool:
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _default_command_of(pid: int) -> str:
    result = subprocess.run(
        ["/bin/ps", "-p", str(pid), "-o", "command="],
        capture_output=True,
        text=True,
        timeout=2.0,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _default_listener_pids(port: int) -> tuple[int, ...]:
    result = subprocess.run(
        ["/usr/sbin/lsof", "-nP", "-iTCP:" + str(int(port)), "-sTCP:LISTEN", "-t"],
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


def _default_free_bytes() -> int:
    """Reclaimable bytes from vm_stat; raises so callers stay fail-closed."""

    result = subprocess.run(
        ["/usr/bin/vm_stat"],
        capture_output=True,
        text=True,
        timeout=2.0,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("memory_budget_unavailable")
    page_match = re.search(r"page size of\s+(\d+) bytes", result.stdout)
    if page_match is None:
        raise RuntimeError("memory_budget_unavailable")
    page_size = int(page_match.group(1))
    total = 0
    for label in ("Pages free", "Pages inactive"):
        match = re.search(re.escape(label) + r":\s+(\d+)\.", result.stdout)
        if match is None:
            raise RuntimeError("memory_budget_unavailable")
        total += int(match.group(1)) * page_size
    return total


def _default_signal(pid: int, name: str) -> None:
    number = signal.SIGTERM if name == "TERM" else signal.SIGKILL
    os.kill(int(pid), number)


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


def _default_catalog_reader(host: str, port: int) -> tuple[str, ...]:
    url = "http://" + str(host) + ":" + str(int(port)) + "/v1/models"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _RejectRedirects(),
    )
    with opener.open(request, timeout=1.5) as response:
        raw = response.read(MLX_SERVE_CATALOG_MAX_BYTES + 1)
    if len(raw) > MLX_SERVE_CATALOG_MAX_BYTES:
        raise ValueError("catalog too large")
    payload = json.loads(raw.decode("utf-8"))
    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise ValueError("catalog malformed")
    loaded: list[str] = []
    for item in entries:
        if not isinstance(item, dict):
            raise ValueError("catalog malformed")
        identity = item.get("id")
        if not isinstance(identity, str) or not identity:
            raise ValueError("catalog malformed")
        if item.get("loaded") is True or item.get("state") == "ready":
            loaded.append(identity)
    return tuple(loaded)


@dataclass
class LaunchHooks:
    """Injectable seams; every default is conservative and never adopting."""

    popen: Callable[[Sequence[str], Path], Any] | None = None
    listener_pids: Callable[[int], Sequence[int]] | None = None
    command_of: Callable[[int], str] | None = None
    alive_probe: Callable[[int], bool] | None = None
    catalog_reader: Callable[[str, int], Sequence[str]] | None = None
    free_reader: Callable[[], int] | None = None
    executable_probe: Callable[[Path], str] | None = None
    codesign: Callable[[Path], str] | None = None
    signal_process: Callable[[int, str], None] | None = None
    sleeper: Callable[[float], None] | None = None
    clock: Callable[[], float] | None = None
    wait_budget: float = MLX_SERVE_DEFAULT_WAIT_BUDGET_SECONDS
    poll_interval: float = MLX_SERVE_DEFAULT_POLL_INTERVAL_SECONDS

    def spawn(self, command: Sequence[str], log_path: Path) -> Any:
        runner = self.popen or _default_popen
        return runner(tuple(command), Path(log_path))

    def listeners(self, port: int) -> tuple[int, ...]:
        reader = self.listener_pids or _default_listener_pids
        try:
            values = tuple(int(item) for item in reader(int(port)))
        except Exception:
            return ()
        return tuple(item for item in values if item > 1)

    def command_for(self, pid: int) -> str:
        reader = self.command_of or _default_command_of
        try:
            value = reader(int(pid))
        except Exception:
            return ""
        return value if isinstance(value, str) else ""

    def alive(self, pid: int) -> bool:
        probe = self.alive_probe or _default_alive
        try:
            return bool(probe(int(pid)))
        except Exception:
            return False

    def catalog(self, host: str, port: int) -> tuple[str, ...]:
        reader = self.catalog_reader or _default_catalog_reader
        return tuple(str(item) for item in reader(host, int(port)))

    def free_bytes(self) -> int:
        reader = self.free_reader or _default_free_bytes
        return int(reader())

    def probe_executable(self, path: Path) -> str:
        if self.executable_probe is not None:
            reason = self.executable_probe(Path(path))
        else:
            reason = validate_executable(Path(path), codesign=self.codesign)
        return reason if reason in LAUNCH_REASONS else ""

    def signal(self, pid: int, name: str) -> None:
        sender = self.signal_process or _default_signal
        sender(int(pid), name)

    def sleep(self, seconds: float) -> None:
        waiter = self.sleeper or time.sleep
        waiter(max(0.0, float(seconds)))

    def now(self) -> float:
        reader = self.clock or time.time
        return float(reader())


def _state_path(state_root: Path) -> Path:
    return Path(state_root) / MLX_SERVE_STATE_NAME


def _state_dir_unsafe(state_root: Path) -> bool:
    try:
        return _state_path(state_root).is_symlink()
    except OSError:
        return True


def _load_state_payload(state_root: Path) -> tuple[str, dict[str, Any] | None]:
    path = _state_path(state_root)
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return "missing", None
    except OSError:
        return "invalid", None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_size > MLX_SERVE_STATE_MAX_BYTES
        or metadata.st_mode & 0o077
    ):
        return "invalid", None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "invalid", None
    return ("ok", raw) if isinstance(raw, dict) else ("invalid", None)


def _record_from_payload(raw: dict[str, Any] | None) -> AppOwnedServerRecord | None:
    if not isinstance(raw, dict) or raw.get("schema_version") != MLX_SERVE_SCHEMA_VERSION:
        return None
    try:
        pid = int(raw.get("pid"))
        port = int(raw.get("port"))
        started_at = float(raw.get("started_at"))
    except (TypeError, ValueError, OverflowError):
        return None
    host = raw.get("host")
    model = raw.get("model")
    digest = raw.get("command_digest")
    prefix = raw.get("prefix")
    if host != MLX_SERVE_DEFAULT_HOST or not 1024 <= port <= 65535 or pid <= 1:
        return None
    if type(model) is not str or not model or "/" in model:
        return None
    if type(digest) is not str or len(digest) != 16:
        return None
    if not isinstance(prefix, list) or len(prefix) != MLX_SERVE_PREFIX_TOKENS:
        return None
    if any(type(token) is not str or not token for token in prefix):
        return None
    if not math.isfinite(started_at) or started_at <= 0:
        return None
    return AppOwnedServerRecord(
        pid,
        host,
        port,
        model,
        tuple(prefix),
        digest,
        started_at,
    )


def read_app_owned_state(state_root: Path) -> AppOwnedServerRecord | None:
    kind, raw = _load_state_payload(Path(state_root))
    if kind != "ok":
        return None
    return _record_from_payload(raw)


def write_app_owned_state(state_root: Path, record: AppOwnedServerRecord) -> None:
    """Atomically persist the ownership record with 0600 permissions."""

    root = Path(state_root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    path = _state_path(root)
    if path.is_symlink():
        raise OSError("mlx_serve_state_unsafe")
    encoded = json.dumps(
        record.payload(), ensure_ascii=True, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded) > MLX_SERVE_STATE_MAX_BYTES:
        raise OSError("mlx_serve_state_too_large")
    temp = root / ("." + MLX_SERVE_STATE_NAME + "." + str(os.getpid()) + "." + str(time.time_ns()) + ".tmp")
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _remove_app_owned_state(state_root: Path) -> None:
    try:
        _state_path(Path(state_root)).unlink(missing_ok=True)
    except OSError:
        pass


def attest_app_owned_server(
    record: AppOwnedServerRecord,
    *,
    hooks: LaunchHooks | None = None,
    spec: MlxLaunchSpec | None = None,
) -> str:
    """Return an empty string when the recorded pid is provably app-owned."""

    runtime = hooks or LaunchHooks()
    if not runtime.alive(record.pid):
        return "attest_not_alive"
    if spec is not None:
        if tuple(record.prefix) != spec.prefix() or record.command_digest != spec.command_digest():
            return "attest_command_mismatch"
        if record.port != spec.port:
            return "attest_command_mismatch"
    command = runtime.command_for(record.pid)
    if not _command_prefix_matches(command, record.prefix):
        return "attest_command_mismatch"
    if record.pid not in runtime.listeners(record.port):
        return "attest_not_listening"
    return ""


def _await_catalog_ready(
    spec: MlxLaunchSpec,
    hooks: LaunchHooks,
    pid: int,
) -> tuple[bool, str]:
    desired = _model_basename(spec.resident_model_name)
    deadline = hooks.now() + max(0.0, float(hooks.wait_budget))
    while True:
        if not hooks.alive(pid):
            return False, "launch_spawn_exited"
        try:
            entries = hooks.catalog(spec.host, spec.port)
        except Exception:
            entries = ()
        if any(_model_basename(item) == desired for item in entries):
            return True, "launch_ready"
        if hooks.now() >= deadline:
            return False, "launch_startup_timeout"
        hooks.sleep(hooks.poll_interval)


def _stop_quietly(pid: int, hooks: LaunchHooks) -> None:
    try:
        hooks.signal(pid, "TERM")
    except Exception:
        pass


def launch_app_owned_server(
    spec: MlxLaunchSpec,
    state_root: Path,
    *,
    hooks: LaunchHooks | None = None,
) -> LaunchResult:
    """Launch and attest an app-owned MLX server; never adopt a foreign one."""

    runtime = hooks or LaunchHooks()
    stages: list[str] = []

    def fail(reason: str) -> LaunchResult:
        return LaunchResult(False, LaunchStage.FAILED, reason, tuple(stages), None, None)

    stages.append(LaunchStage.VALIDATE.value)
    reason = spec.validate()
    if reason:
        return fail(reason)

    stages.append(LaunchStage.EXECUTABLE.value)
    reason = runtime.probe_executable(spec.executable)
    if reason:
        return fail(reason)

    stages.append(LaunchStage.MODEL_DIR.value)
    if not _is_directory(spec.resident_model_dir):
        return fail("launch_model_dir_missing")

    stages.append(LaunchStage.MODELS_DIR.value)
    if not _is_directory(spec.models_dir):
        return fail("launch_models_dir_missing")

    stages.append(LaunchStage.PORT_CHECK.value)
    listeners = runtime.listeners(spec.port)
    if listeners:
        record = read_app_owned_state(state_root)
        attested = record is not None and attest_app_owned_server(
            record, hooks=runtime, spec=spec
        ) == ""
        if attested and record is not None and record.pid in listeners:
            return LaunchResult(
                True,
                LaunchStage.ALREADY_RUNNING,
                "launch_already_running",
                tuple(stages),
                record.pid,
                spec.resident_model_name,
            )
        return fail("launch_port_in_use")

    stages.append(LaunchStage.MEMORY_CHECK.value)
    try:
        free_bytes = runtime.free_bytes()
    except Exception:
        return fail("launch_memory_unavailable")
    if free_bytes < spec.minimum_free_bytes:
        return fail("launch_memory_insufficient")

    stages.append(LaunchStage.STATE_CHECK.value)
    if _state_dir_unsafe(state_root):
        return fail("launch_state_unsafe")

    stages.append(LaunchStage.SPAWN.value)
    try:
        process = runtime.spawn(spec.command(), spec.log_path)
        pid = int(process.pid)
    except Exception:
        return fail("launch_spawn_failed")
    if pid <= 1:
        return fail("launch_spawn_failed")

    stages.append(LaunchStage.STARTUP.value)
    ready, startup_reason = _await_catalog_ready(spec, runtime, pid)
    if not ready:
        _stop_quietly(pid, runtime)
        return fail(startup_reason)

    stages.append(LaunchStage.ATTEST.value)
    record = AppOwnedServerRecord(
        pid,
        spec.host,
        spec.port,
        spec.resident_model_name,
        spec.prefix(),
        spec.command_digest(),
        runtime.now(),
    )
    if attest_app_owned_server(record, hooks=runtime, spec=spec):
        _stop_quietly(pid, runtime)
        return fail("launch_attest_failed")

    stages.append(LaunchStage.READY.value)
    try:
        write_app_owned_state(state_root, record)
    except OSError:
        _stop_quietly(pid, runtime)
        return fail("launch_state_write_failed")
    return LaunchResult(
        True,
        LaunchStage.READY,
        "launch_ready",
        tuple(stages),
        pid,
        spec.resident_model_name,
    )


def stop_app_owned_server(
    state_root: Path,
    *,
    hooks: LaunchHooks | None = None,
) -> StopResult:
    """Signal only a pid that is still attested as this app's server."""

    runtime = hooks or LaunchHooks()
    record = read_app_owned_state(state_root)
    if record is None:
        return StopResult(False, "stop_no_owned_server", None)
    if attest_app_owned_server(record, hooks=runtime):
        return StopResult(False, "stop_owner_mismatch", record.pid)
    try:
        runtime.signal(record.pid, "TERM")
    except Exception:
        return StopResult(False, "stop_signal_failed", record.pid)
    _remove_app_owned_state(state_root)
    return StopResult(True, "stop_stopped", record.pid)


def ownership_status(
    state_root: Path,
    *,
    port: int = MLX_SERVE_DEFAULT_PORT,
    hooks: LaunchHooks | None = None,
) -> dict[str, Any]:
    """Bounded, secret-free view of who owns the MLX gateway port.

    Only fixed state codes, one boolean, and the resident model basename leave
    this function.  Paths, pids, command lines, and chat content never do.
    """

    runtime = hooks or LaunchHooks()

    def report(owner_state: str, app_owned: bool, model: str | None) -> dict[str, Any]:
        state = owner_state if owner_state in MLX_SERVE_OWNER_STATES else "state_invalid"
        return {
            "ok": True,
            "action": "mlx-server-status",
            "owner_state": state,
            "app_owned": bool(app_owned),
            "model": model if type(model) is str and model else None,
        }

    kind, raw = _load_state_payload(Path(state_root))
    if kind == "invalid":
        return report("state_invalid", False, None)
    if kind == "ok":
        record = _record_from_payload(raw)
        if record is None:
            return report("state_invalid", False, None)
        reason = attest_app_owned_server(record, hooks=runtime)
        if not reason:
            return report("app_owned", True, record.model)
        if reason in {"attest_not_alive", "attest_not_listening"}:
            return report("stopped", False, record.model)
        return report("state_stale", False, record.model)
    listeners = runtime.listeners(port)
    return report("foreign_listener" if listeners else "stopped", False, None)
