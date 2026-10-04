#!/usr/bin/env python3
"""Safe LaunchAgent manager for Alden's dedicated local E5 adapter.

Production defaults are intentionally fixed to the installed Alden bundle,
the dedicated account-home embedding runtime, the pinned multilingual E5
snapshot, and loopback port 11236.  The manager never discovers or mutates the
generation services or Kakao workers.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import plistlib
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Sequence
import urllib.error
import urllib.parse
import urllib.request


LABEL = "com.openkakao.alden.local-embedding"
HOST = "127.0.0.1"
PORT = 11236
MODEL_REPO_ID = "mlx-community/multilingual-e5-small-mlx"
MODEL_REVISION = "5030c7625865046d350eeea28f427d80353d0ac0"
MODEL_ID = f"{MODEL_REPO_ID}@{MODEL_REVISION}"
MODEL_LICENSE = "MIT"
MODEL_DIMENSIONS = 384
EXPECTED_WEIGHT_BYTES = 235_330_776
# Deployment pin for the currently installed Alden bundle. After a future
# reviewed rebuild, verify that the repository adapter and installed app
# resource have the same SHA-256, then update this one pin deliberately.
DEPLOYMENT_APP_SCRIPT_SHA256 = "7d94fccfb9a62c006182a5234d854a623fdc5cf699b7771c13036696c3b97112"
MODEL_SNAPSHOT_RELATIVE = (
    Path(".cache")
    / "huggingface"
    / "hub"
    / "models--mlx-community--multilingual-e5-small-mlx"
    / "snapshots"
    / MODEL_REVISION
)
LAUNCH_AGENT_RELATIVE = Path("Library") / "LaunchAgents" / f"{LABEL}.plist"
STATE_ROOT_RELATIVE = Path("Library") / "Application Support" / "openkakao" / "alden-local-embedding"
RUNTIME_ROOT_RELATIVE = STATE_ROOT_RELATIVE / "runtime"
RUNTIME_PYTHON_RELATIVE = RUNTIME_ROOT_RELATIVE / "bin" / "python"
APP_SCRIPT_PATH = Path(
    "/Applications/Alden.app/Contents/Resources/scripts/alden_local_embedding_server.py"
)


class SafetyError(RuntimeError):
    """A fail-closed ownership, identity, or readiness error."""


class NotReady(RuntimeError):
    """The expected loopback endpoint is not accepting requests yet."""


class RejectRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Turn every HTTP redirect into an HTTPError instead of following it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, D401
        return None


def effective_account_home() -> Path:
    """Return the effective uid's account-db home, independent of HOME env."""

    try:
        raw = pwd.getpwuid(os.geteuid()).pw_dir
    except KeyError as exc:  # pragma: no cover - pathological local account state
        raise SafetyError("effective uid has no passwd database entry") from exc
    if not raw:
        raise SafetyError("effective uid has an empty passwd database home")
    path = Path(raw)
    if not path.is_absolute():
        raise SafetyError(f"effective uid home must be absolute: {path}")
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise SafetyError(f"effective uid home cannot be resolved: {path}") from exc


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class SubprocessRunner:
    def run(self, argv: Sequence[str]) -> CommandResult:
        completed = subprocess.run(
            list(argv),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)


@dataclass(frozen=True)
class Config:
    home: Path
    python_path: Path
    app_script: Path
    model_path: Path
    plist_path: Path
    state_root: Path
    label: str = LABEL
    host: str = HOST
    port: int = PORT
    model_id: str = MODEL_ID
    model_revision: str = MODEL_REVISION
    model_license: str = MODEL_LICENSE
    model_dimensions: int = MODEL_DIMENSIONS
    expected_weight_bytes: int = EXPECTED_WEIGHT_BYTES
    expected_app_script_sha256: str = DEPLOYMENT_APP_SCRIPT_SHA256
    enforce_production_contract: bool = False
    launchctl: Path = Path("/bin/launchctl")
    plutil: Path = Path("/usr/bin/plutil")
    lsof: Path = Path("/usr/sbin/lsof")
    ps: Path = Path("/bin/ps")
    readiness_timeout_secs: float = 30.0
    readiness_poll_secs: float = 0.25
    stop_timeout_secs: float = 2.0
    stop_poll_secs: float = 0.05

    @property
    def service(self) -> str:
        return f"gui/{os.geteuid()}/{self.label}"

    @property
    def stdout_path(self) -> Path:
        return self.state_root / "adapter.stdout.log"

    @property
    def stderr_path(self) -> Path:
        return self.state_root / "adapter.stderr.log"

    @property
    def install_state_path(self) -> Path:
        return self.state_root / "install-state.json"

    @property
    def backup_root(self) -> Path:
        return self.state_root / "backups"

    @property
    def program_arguments(self) -> tuple[str, ...]:
        return (
            str(self.python_path),
            str(self.app_script),
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--model-path",
            str(self.model_path),
        )


def production_config() -> Config:
    home = effective_account_home()
    return Config(
        home=home,
        python_path=home / RUNTIME_PYTHON_RELATIVE,
        app_script=APP_SCRIPT_PATH,
        model_path=home / MODEL_SNAPSHOT_RELATIVE,
        plist_path=home / LAUNCH_AGENT_RELATIVE,
        state_root=home / STATE_ROOT_RELATIVE,
        enforce_production_contract=True,
    )


class AldenEmbeddingLaunchdManager:
    def __init__(
        self,
        config: Config,
        runner: Any | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.runner = runner or SubprocessRunner()
        self.sleep = sleep
        self.monotonic = monotonic

    def _run(self, argv: Sequence[str]) -> CommandResult:
        return self.runner.run(tuple(str(value) for value in argv))

    @staticmethod
    def _sha256_bytes(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def _sha256_path(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _validate_absolute(path: Path, label: str) -> None:
        if not path.is_absolute():
            raise SafetyError(f"{label} must be absolute: {path}")
        if any(part in {".", ".."} for part in path.parts):
            raise SafetyError(f"{label} must not contain . or ..: {path}")

    @staticmethod
    def _trusted_uid(uid: int) -> bool:
        return uid in {0, os.geteuid()}

    def _validated_home(self) -> Path:
        cfg = self.config
        self._validate_absolute(cfg.home, "account home")
        try:
            home = cfg.home.resolve(strict=True)
        except OSError as exc:
            raise SafetyError(f"account home cannot be resolved: {cfg.home}") from exc
        st = os.stat(home)
        if not stat.S_ISDIR(st.st_mode):
            raise SafetyError(f"account home must be a directory: {home}")
        if st.st_uid != os.geteuid():
            raise SafetyError(f"account home must be owned by the effective uid: {home}")
        return home

    def _validate_home_contained(self, path: Path, label: str) -> None:
        """Require a user-managed path and its existing ancestry to stay in home."""

        self._validate_absolute(path, label)
        lexical_home = self.config.home
        home = self._validated_home()
        try:
            path.relative_to(lexical_home)
        except ValueError as exc:
            raise SafetyError(f"{label} escapes the effective account home: {path}") from exc

        probe = path
        while not os.path.lexists(probe):
            if probe == lexical_home:
                break
            parent = probe.parent
            if parent == probe:
                raise SafetyError(f"{label} has no existing ancestor inside account home: {path}")
            probe = parent
        try:
            resolved_probe = probe.resolve(strict=True)
            resolved_probe.relative_to(home)
        except (OSError, ValueError) as exc:
            raise SafetyError(f"{label} resolves outside the effective account home: {path}") from exc

    def _validate_production_contract(self) -> None:
        cfg = self.config
        if not cfg.enforce_production_contract:
            return
        home = effective_account_home()
        expected_values = {
            "home": (cfg.home, home),
            "label": (cfg.label, LABEL),
            "host": (cfg.host, HOST),
            "port": (cfg.port, PORT),
            "model id": (cfg.model_id, MODEL_ID),
            "model revision": (cfg.model_revision, MODEL_REVISION),
            "model license": (cfg.model_license, MODEL_LICENSE),
            "model dimensions": (cfg.model_dimensions, MODEL_DIMENSIONS),
            "model weight bytes": (cfg.expected_weight_bytes, EXPECTED_WEIGHT_BYTES),
            "adapter SHA-256 pin": (cfg.expected_app_script_sha256, DEPLOYMENT_APP_SCRIPT_SHA256),
            "embedding runtime Python path": (cfg.python_path, home / RUNTIME_PYTHON_RELATIVE),
            "installed adapter path": (cfg.app_script, APP_SCRIPT_PATH),
            "model snapshot path": (cfg.model_path, home / MODEL_SNAPSHOT_RELATIVE),
            "LaunchAgent plist path": (cfg.plist_path, home / LAUNCH_AGENT_RELATIVE),
            "state root": (cfg.state_root, home / STATE_ROOT_RELATIVE),
        }
        for label, (actual, expected) in expected_values.items():
            if actual != expected:
                raise SafetyError(f"production {label} mismatch: expected {expected}, got {actual}")

    def _validate_regular_file(
        self,
        path: Path,
        label: str,
        *,
        executable: bool = False,
        allow_symlink: bool = False,
        user_owned_only: bool = False,
    ) -> Path:
        self._validate_absolute(path, label)
        try:
            lst = os.lstat(path)
        except FileNotFoundError as exc:
            raise SafetyError(f"{label} is missing: {path}") from exc
        if stat.S_ISLNK(lst.st_mode):
            if not allow_symlink:
                raise SafetyError(f"{label} must not be a symlink: {path}")
            if lst.st_uid != os.geteuid():
                raise SafetyError(f"{label} symlink is not owned by the effective uid: {path}")
        elif not stat.S_ISREG(lst.st_mode):
            raise SafetyError(f"{label} must be a regular file: {path}")

        resolved = path.resolve(strict=True)
        target = os.stat(resolved)
        if not stat.S_ISREG(target.st_mode):
            raise SafetyError(f"{label} target must be a regular file: {resolved}")
        if user_owned_only:
            if target.st_uid != os.geteuid():
                raise SafetyError(f"{label} must be owned by the effective uid: {resolved}")
        elif not self._trusted_uid(target.st_uid):
            raise SafetyError(f"{label} has an untrusted owner: {resolved}")
        if target.st_mode & 0o022:
            raise SafetyError(f"{label} must not be group- or world-writable: {resolved}")
        if executable and not target.st_mode & stat.S_IXUSR:
            raise SafetyError(f"{label} must be owner-executable: {resolved}")
        return resolved

    def _validate_private_directory(self, path: Path, label: str, *, allow_missing: bool) -> None:
        self._validate_absolute(path, label)
        if not os.path.lexists(path):
            if allow_missing:
                return
            raise SafetyError(f"{label} is missing: {path}")
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode):
            raise SafetyError(f"{label} must not be a symlink: {path}")
        if not stat.S_ISDIR(st.st_mode):
            raise SafetyError(f"{label} must be a directory: {path}")
        if st.st_uid != os.geteuid():
            raise SafetyError(f"{label} must be owned by the effective uid: {path}")
        if st.st_mode & 0o022:
            raise SafetyError(f"{label} must not be group- or world-writable: {path}")

    def _ensure_private_directory(self, path: Path) -> None:
        if os.path.lexists(path):
            self._validate_private_directory(path, "managed directory", allow_missing=False)
            return
        parent = path.parent
        if not parent.exists():
            self._ensure_private_directory(parent)
        else:
            self._validate_private_directory(parent, "managed directory parent", allow_missing=False)
        os.mkdir(path, 0o700)
        self._validate_private_directory(path, "managed directory", allow_missing=False)

    def _validate_static_assets(self) -> dict[str, Any]:
        cfg = self.config
        self._validate_production_contract()
        if cfg.host != HOST or cfg.port <= 0 or cfg.port > 65535:
            raise SafetyError("embedding manager requires a concrete loopback host and valid port")

        self._validate_home_contained(cfg.python_path, "embedding runtime Python")
        self._validate_home_contained(cfg.model_path, "pinned model snapshot")
        self._validate_home_contained(cfg.plist_path, "LaunchAgent plist")
        self._validate_home_contained(cfg.state_root, "embedding state root")

        resolved_python = self._validate_regular_file(
            cfg.python_path,
            "embedding runtime Python",
            executable=True,
            allow_symlink=True,
            user_owned_only=True,
        )
        try:
            resolved_python.relative_to(self._validated_home())
        except ValueError as exc:
            raise SafetyError("embedding runtime Python target escapes the effective account home") from exc
        runtime_root = cfg.home / RUNTIME_ROOT_RELATIVE
        self._validate_private_directory(runtime_root, "embedding runtime", allow_missing=False)
        self._validate_regular_file(
            runtime_root / "pyvenv.cfg",
            "embedding runtime pyvenv.cfg",
            user_owned_only=True,
        )
        self._validate_regular_file(cfg.app_script, "installed embedding adapter")
        script_sha = self._sha256_path(cfg.app_script)
        if script_sha != cfg.expected_app_script_sha256:
            raise SafetyError(
                "installed embedding adapter SHA-256 does not match the pinned manager expectation"
            )

        self._validate_private_directory(cfg.model_path, "pinned model snapshot", allow_missing=False)
        if cfg.model_path.name != cfg.model_revision:
            raise SafetyError("pinned model snapshot revision path mismatch")
        repo_root = cfg.model_path.parents[1]
        blobs_root = repo_root / "blobs"
        for name in ("config.json", "README.md", "weights.00.safetensors", "tokenizer.json"):
            member = cfg.model_path / name
            self._validate_absolute(member, f"model {name}")
            try:
                lst = os.lstat(member)
            except FileNotFoundError as exc:
                raise SafetyError(f"pinned model file is missing: {name}") from exc
            if stat.S_ISLNK(lst.st_mode) and lst.st_uid != os.geteuid():
                raise SafetyError(f"pinned model symlink is not user-owned: {name}")
            resolved = member.resolve(strict=True)
            try:
                resolved.relative_to(blobs_root.resolve(strict=True))
            except ValueError as exc:
                raise SafetyError(f"pinned model file escapes local HF blob cache: {name}") from exc
            self._validate_regular_file(
                resolved,
                f"resolved model {name}",
                user_owned_only=True,
            )

        config = json.loads((cfg.model_path / "config.json").read_text(encoding="utf-8"))
        expected_config = {
            "model_type": "bert",
            "hidden_size": cfg.model_dimensions,
            "max_position_embeddings": 512,
            "architectures": ["BertModel"],
        }
        for key, expected in expected_config.items():
            if config.get(key) != expected:
                raise SafetyError(f"pinned model config mismatch: {key}")
        if (cfg.model_path / "weights.00.safetensors").stat().st_size != cfg.expected_weight_bytes:
            raise SafetyError("pinned model weight size mismatch")
        readme_head = (cfg.model_path / "README.md").read_text(encoding="utf-8")[:16_384].casefold()
        if "license: mit" not in readme_head:
            raise SafetyError("pinned model license metadata mismatch")

        self._validate_private_directory(cfg.plist_path.parent, "LaunchAgents directory", allow_missing=True)
        self._validate_private_directory(cfg.state_root, "embedding state root", allow_missing=True)
        return {
            "adapter_sha256": script_sha,
            "model": cfg.model_id,
            "python": str(cfg.python_path),
            "adapter": str(cfg.app_script),
            "model_path": str(cfg.model_path),
        }

    def _expected_plist(self) -> dict[str, Any]:
        cfg = self.config
        return {
            "Label": cfg.label,
            "ProgramArguments": list(cfg.program_arguments),
            "EnvironmentVariables": {
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "PYTHONUNBUFFERED": "1",
            },
            "RunAtLoad": True,
            "ThrottleInterval": 10,
            "ProcessType": "Background",
            "StandardOutPath": str(cfg.stdout_path),
            "StandardErrorPath": str(cfg.stderr_path),
        }

    def _legacy_manual_start_plist(self) -> dict[str, Any]:
        """Return the immediately prior manager plist for a safe resident upgrade."""

        payload = self._expected_plist()
        payload["RunAtLoad"] = False
        return payload

    def _plist_bytes(self) -> bytes:
        return plistlib.dumps(self._expected_plist(), fmt=plistlib.FMT_XML, sort_keys=False)

    def _read_existing_plist(self, *, require_compatible: bool) -> dict[str, Any] | None:
        path = self.config.plist_path
        if not os.path.lexists(path):
            return None
        self._validate_regular_file(path, "LaunchAgent plist", user_owned_only=True)
        try:
            with path.open("rb") as fh:
                payload = plistlib.load(fh)
        except Exception as exc:
            raise SafetyError("LaunchAgent plist is unreadable") from exc
        if not isinstance(payload, dict):
            raise SafetyError("LaunchAgent plist must contain a dictionary")
        if require_compatible:
            expected = self._expected_plist()
            for key in (
                "Label",
                "ProgramArguments",
                "EnvironmentVariables",
                "StandardOutPath",
                "StandardErrorPath",
            ):
                if payload.get(key) != expected.get(key):
                    raise SafetyError(f"existing LaunchAgent plist is not manager-owned: {key}")
        return payload

    def _plist_is_exact(self, payload: dict[str, Any]) -> bool:
        return payload == self._expected_plist()

    def _run_plutil_lint(self, path: Path) -> None:
        result = self._run((self.config.plutil, "-lint", path))
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise SafetyError(f"plutil rejected LaunchAgent plist: {detail or 'unknown error'}")

    def _atomic_write_bytes(self, path: Path, data: bytes, mode: int = 0o600) -> None:
        self._ensure_private_directory(path.parent)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as fh:
                os.fchmod(fh.fileno(), mode)
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            if tmp.exists():
                tmp.unlink()

    def _atomic_write_json(self, path: Path, payload: dict[str, Any]) -> None:
        data = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        self._atomic_write_bytes(path, data, 0o600)

    def _read_install_state(self) -> dict[str, Any] | None:
        path = self.config.install_state_path
        if not os.path.lexists(path):
            return None
        self._validate_regular_file(path, "embedding install state", user_owned_only=True)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SafetyError("embedding install state is unreadable") from exc
        if not isinstance(payload, dict) or payload.get("version") != 1 or payload.get("label") != self.config.label:
            raise SafetyError("embedding install state identity mismatch")
        return payload

    def _backup_current_plist(self, data: bytes) -> Path:
        self._ensure_private_directory(self.config.backup_root)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        path = self.config.backup_root / f"prior-{stamp}-{os.getpid()}.plist"
        self._atomic_write_bytes(path, data, 0o600)
        return path

    def _launchctl_print(self) -> str | None:
        result = self._run((self.config.launchctl, "print", self.config.service))
        if result.returncode == 0:
            return result.stdout
        combined = f"{result.stdout}\n{result.stderr}".casefold()
        benign = (
            "could not find service",
            "service not found",
            "no such process",
            "not loaded",
        )
        if any(token in combined for token in benign):
            return None
        raise SafetyError(f"launchctl print failed for {self.config.service}")

    @staticmethod
    def _parse_launchctl_print(text: str) -> dict[str, Any]:
        path_match = re.search(r"(?m)^\s*path = (.+?)\s*$", text)
        program_match = re.search(r"(?m)^\s*program = (.+?)\s*$", text)
        pid_match = re.search(r"(?m)^\s*pid = (\d+)\s*$", text)
        return {
            "path": path_match.group(1) if path_match else None,
            "program": program_match.group(1) if program_match else None,
            "pid": int(pid_match.group(1)) if pid_match else None,
        }

    def _listener_records(self) -> dict[int, set[str]]:
        result = self._run(
            (
                self.config.lsof,
                "-nP",
                f"-iTCP:{self.config.port}",
                "-sTCP:LISTEN",
                "-Fpn",
            )
        )
        if result.returncode not in {0, 1}:
            raise SafetyError(f"lsof failed while checking port {self.config.port}")
        records: dict[int, set[str]] = {}
        current: int | None = None
        for raw in result.stdout.splitlines():
            if raw.startswith("p") and raw[1:].isdigit():
                current = int(raw[1:])
                records.setdefault(current, set())
            elif raw.startswith("n") and current is not None:
                records[current].add(raw[1:])
        return records

    def _verify_process(self, pid: int) -> None:
        result = self._run((self.config.ps, "-ww", "-p", str(pid), "-o", "uid=", "-o", "comm="))
        if result.returncode != 0 or not result.stdout.strip():
            raise SafetyError("managed LaunchAgent pid cannot be inspected")
        line = result.stdout.strip().splitlines()[0].strip()
        parts = line.split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit() or int(parts[0]) != os.geteuid():
            raise SafetyError("managed LaunchAgent process uid mismatch")
        executable = parts[1].strip()
        try:
            same_executable = Path(executable).resolve(strict=True) == self.config.python_path.resolve(strict=True)
        except (OSError, RuntimeError):
            same_executable = False
        if not same_executable:
            raise SafetyError("managed LaunchAgent Python executable mismatch")

        result = self._run((self.config.ps, "-ww", "-p", str(pid), "-o", "command="))
        if result.returncode != 0 or not result.stdout.strip():
            raise SafetyError("managed LaunchAgent command line cannot be inspected")
        command = result.stdout.strip().splitlines()[0].strip()
        expected_command = " ".join(self.config.program_arguments)
        if command != expected_command:
            raise SafetyError("managed LaunchAgent command arguments mismatch")

    def _verify_loaded_static_identity(self, text: str) -> dict[str, Any]:
        info = self._parse_launchctl_print(text)
        if info["path"] != str(self.config.plist_path):
            raise SafetyError("loaded LaunchAgent plist path mismatch")
        if info["program"] not in {None, str(self.config.python_path)}:
            try:
                same_program = Path(info["program"]).resolve(strict=True) == self.config.python_path.resolve(strict=True)
            except (OSError, RuntimeError):
                same_program = False
            if not same_program:
                raise SafetyError("loaded LaunchAgent program mismatch")
        return info

    def _verify_listener_identity(self, listeners: dict[int, set[str]], pid: int | None) -> None:
        if not listeners:
            return
        if pid is None or set(listeners) != {pid}:
            raise SafetyError(f"port {self.config.port} listener is not owned by the managed LaunchAgent")
        expected_name = f"{self.config.host}:{self.config.port}"
        names = listeners[pid]
        if not names or any(name != expected_name for name in names):
            raise SafetyError("embedding listener is not bound exactly to the fixed loopback address")

    def _verify_loaded_identity(self, text: str, listeners: dict[int, set[str]]) -> dict[str, Any]:
        info = self._verify_loaded_static_identity(text)
        pid = info["pid"]
        if pid is not None:
            self._verify_process(pid)
        self._verify_listener_identity(listeners, pid)
        return info

    def _http_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        if self.config.host != HOST:
            raise SafetyError("embedding readiness HTTP target must use 127.0.0.1")
        if self.config.enforce_production_contract and self.config.port != PORT:
            raise SafetyError(f"production embedding readiness HTTP port must be {PORT}")
        parsed_path = urllib.parse.urlsplit(path)
        if not path.startswith("/") or parsed_path.scheme or parsed_path.netloc or parsed_path.fragment:
            raise SafetyError(f"embedding readiness path must be local and relative: {path}")
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        url = f"http://{self.config.host}:{self.config.port}{path}"
        request = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method=method,
        )
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            RejectRedirectHandler(),
        )
        try:
            with opener.open(request, timeout=1.5) as response:
                if response.status != 200:
                    raise SafetyError(f"embedding readiness returned HTTP {response.status}")
                if response.geturl() != url:
                    raise SafetyError("embedding readiness redirected away from the fixed local endpoint")
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            if 300 <= code < 400:
                raise SafetyError("embedding readiness redirect rejected") from exc
            raise SafetyError(f"embedding readiness returned HTTP {code}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise NotReady("embedding endpoint is not accepting local requests") from exc
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise SafetyError("embedding readiness returned invalid JSON") from exc

    def _readiness(self) -> dict[str, Any]:
        models = self._http_json("GET", "/v1/models")
        if not isinstance(models, dict) or not isinstance(models.get("data"), list) or len(models["data"]) != 1:
            raise SafetyError("embedding readiness must advertise exactly one model")
        row = models["data"][0]
        if not isinstance(row, dict):
            raise SafetyError("embedding readiness model row is invalid")
        caps = row.get("capabilities")
        if (
            row.get("id") != self.config.model_id
            or row.get("loaded") is not True
            or row.get("state") != "ready"
            or not isinstance(caps, list)
            or not {"embedding", "embeddings"}.intersection(caps)
            or row.get("revision") != self.config.model_revision
            or row.get("license") != self.config.model_license
            or row.get("dimensions") != self.config.model_dimensions
        ):
            raise SafetyError("embedding readiness model identity mismatch")

        embeddings = self._http_json(
            "POST",
            "/v1/embeddings",
            {
                "model": self.config.model_id,
                "input": "상태 점검",
                "input_type": "query",
            },
        )
        if not isinstance(embeddings, dict) or embeddings.get("model") != self.config.model_id:
            raise SafetyError("embedding readiness response model mismatch")
        data = embeddings.get("data")
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
            raise SafetyError("embedding readiness response row mismatch")
        vector = data[0].get("embedding")
        if data[0].get("index") != 0 or not isinstance(vector, list) or len(vector) != self.config.model_dimensions:
            raise SafetyError("embedding readiness vector shape mismatch")
        try:
            norm_sq = sum(float(value) * float(value) for value in vector)
        except (TypeError, ValueError) as exc:
            raise SafetyError("embedding readiness vector contains non-numeric values") from exc
        if not math.isfinite(norm_sq) or norm_sq <= 0.0:
            raise SafetyError("embedding readiness vector is non-finite or zero")
        return {
            "model": row["id"],
            "revision": row["revision"],
            "dimensions": len(vector),
            "state": row["state"],
        }

    def _wait_for_readiness(self) -> dict[str, Any]:
        deadline = self.monotonic() + self.config.readiness_timeout_secs
        last_error: Exception | None = None
        while self.monotonic() < deadline:
            try:
                text = self._launchctl_print()
                if text is None:
                    raise NotReady("LaunchAgent is not loaded")
                listeners = self._listener_records()
                self._verify_loaded_identity(text, listeners)
                if not listeners:
                    raise NotReady("managed LaunchAgent has no listener yet")
                return self._readiness()
            except NotReady as exc:
                last_error = exc
                self.sleep(self.config.readiness_poll_secs)
        raise SafetyError(f"embedding LaunchAgent did not become ready: {last_error or 'timeout'}")

    def _wait_for_stop_absence(self, expected_pid: int | None) -> None:
        """Wait for asynchronous launchd bootout without relaxing ownership checks."""

        deadline = self.monotonic() + self.config.stop_timeout_secs
        while True:
            text = self._launchctl_print()
            listeners = self._listener_records()

            if text is None:
                if not listeners:
                    return
                if expected_pid is None:
                    raise SafetyError(f"port {self.config.port} gained an unmanaged listener during bootout")
                self._verify_listener_identity(listeners, expected_pid)
                self._verify_process(expected_pid)
            else:
                info = self._verify_loaded_static_identity(text)
                current_pid = info["pid"]
                if expected_pid is not None and current_pid not in {None, expected_pid}:
                    raise SafetyError("managed LaunchAgent pid changed during bootout")
                if expected_pid is None and current_pid is not None:
                    self._verify_process(current_pid)
                    expected_pid = current_pid

                listener_pid = current_pid if current_pid is not None else expected_pid
                self._verify_listener_identity(listeners, listener_pid)
                if listeners and listener_pid is not None:
                    self._verify_process(listener_pid)

            now = self.monotonic()
            if now >= deadline:
                raise SafetyError(
                    f"timed out waiting for dedicated embedding LaunchAgent and port {self.config.port} to stop"
                )
            self.sleep(min(self.config.stop_poll_secs, max(0.0, deadline - now)))

    def status(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "ok": True,
            "action": "status",
            "service": self.config.service,
            "endpoint": f"http://{self.config.host}:{self.config.port}/v1/embeddings",
            "issues": [],
        }
        try:
            result["assets"] = self._validate_static_assets()
        except Exception as exc:
            result["ok"] = False
            result["issues"].append(str(exc))
        try:
            plist = self._read_existing_plist(require_compatible=True)
            result["plist"] = "absent" if plist is None else ("exact" if self._plist_is_exact(plist) else "compatible")
        except Exception as exc:
            result["ok"] = False
            result["plist"] = "conflict"
            result["issues"].append(str(exc))
        try:
            text = self._launchctl_print()
            listeners = self._listener_records()
            result["listener_pids"] = sorted(listeners)
            if text is None:
                result["launchd"] = "absent"
                if listeners:
                    result["ok"] = False
                    result["issues"].append(f"port {self.config.port} has an unmanaged listener")
            else:
                info = self._verify_loaded_identity(text, listeners)
                result["launchd"] = "loaded"
                result["pid"] = info["pid"]
                if not listeners:
                    result["ok"] = False
                    result["issues"].append("managed LaunchAgent is loaded without its listener")
                else:
                    try:
                        result["readiness"] = self._readiness()
                    except Exception as exc:
                        result["ok"] = False
                        result["issues"].append(str(exc))
        except Exception as exc:
            result["ok"] = False
            result["issues"].append(str(exc))
        return result

    def preflight(self) -> dict[str, Any]:
        assets = self._validate_static_assets()
        plist = self._read_existing_plist(require_compatible=True)
        text = self._launchctl_print()
        listeners = self._listener_records()
        readiness = None
        state = "absent"
        if text is None:
            if listeners:
                raise SafetyError(f"port {self.config.port} already has an unmanaged listener")
        else:
            if plist is None:
                raise SafetyError("LaunchAgent is loaded but the manager plist is absent")
            self._verify_loaded_identity(text, listeners)
            if not listeners:
                raise SafetyError("managed LaunchAgent is loaded without its fixed loopback listener")
            readiness = self._readiness()
            state = "ready"
        return {
            "ok": True,
            "action": "preflight",
            "service": self.config.service,
            "port": self.config.port,
            "launchd": state,
            "plist": "absent" if plist is None else ("exact" if self._plist_is_exact(plist) else "compatible"),
            "assets": assets,
            "readiness": readiness,
        }

    def install(self) -> dict[str, Any]:
        self._validate_static_assets()
        text = self._launchctl_print()
        listeners = self._listener_records()
        if text is not None or listeners:
            raise SafetyError("install requires the dedicated LaunchAgent and port to be inactive")

        existing = self._read_existing_plist(require_compatible=True)
        state = self._read_install_state()
        expected_bytes = self._plist_bytes()
        expected_sha = self._sha256_bytes(expected_bytes)
        migration_state: dict[str, Any] | None = None
        if state is not None:
            if existing is not None and self._plist_is_exact(existing) and state.get("installed_sha256") == expected_sha:
                return {
                    "ok": True,
                    "action": "install",
                    "changed": False,
                    "plist": str(self.config.plist_path),
                    "backup": state.get("backup_path"),
                }
            legacy = self._legacy_manual_start_plist()
            legacy_bytes = plistlib.dumps(legacy, fmt=plistlib.FMT_XML, sort_keys=False)
            legacy_sha = self._sha256_bytes(legacy_bytes)
            if existing != legacy or state.get("installed_sha256") != legacy_sha:
                raise SafetyError("existing embedding install state does not match the current or prior manager plist")
            migration_state = state

        previous_bytes = self.config.plist_path.read_bytes() if existing is not None else None
        rollback_backup_path: Path | None = None
        if previous_bytes is not None:
            rollback_backup_path = self._backup_current_plist(previous_bytes)
        backup_path = migration_state.get("backup_path") if migration_state is not None else (
            str(rollback_backup_path) if rollback_backup_path is not None else None
        )
        replaced = False
        try:
            self._ensure_private_directory(self.config.state_root)
            fd, tmp_name = tempfile.mkstemp(prefix=".launchagent.", dir=str(self.config.state_root))
            tmp = Path(tmp_name)
            try:
                with os.fdopen(fd, "wb") as fh:
                    os.fchmod(fh.fileno(), 0o600)
                    fh.write(expected_bytes)
                    fh.flush()
                    os.fsync(fh.fileno())
                self._run_plutil_lint(tmp)
                self._atomic_write_bytes(self.config.plist_path, expected_bytes, 0o600)
                replaced = True
            finally:
                if tmp.exists():
                    tmp.unlink()
            installed = self._read_existing_plist(require_compatible=True)
            if installed is None or not self._plist_is_exact(installed):
                raise SafetyError("installed LaunchAgent plist readback mismatch")
            next_state = {
                "version": 1,
                "label": self.config.label,
                "installed_sha256": expected_sha,
                "prior_present": (
                    bool(migration_state.get("prior_present"))
                    if migration_state is not None
                    else previous_bytes is not None
                ),
                "backup_path": backup_path,
                "installed_at": (
                    migration_state.get("installed_at")
                    if migration_state is not None
                    else datetime.now(timezone.utc).isoformat()
                ),
            }
            if migration_state is not None:
                next_state["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._atomic_write_json(self.config.install_state_path, next_state)
        except Exception:
            if replaced:
                if previous_bytes is None:
                    try:
                        self.config.plist_path.unlink()
                    except FileNotFoundError:
                        pass
                else:
                    self._atomic_write_bytes(self.config.plist_path, previous_bytes, 0o600)
            raise
        return {
            "ok": True,
            "action": "install",
            "changed": True,
            "plist": str(self.config.plist_path),
            "backup": backup_path,
            "migrated": migration_state is not None,
        }

    def start(self) -> dict[str, Any]:
        self._validate_static_assets()
        plist = self._read_existing_plist(require_compatible=True)
        state = self._read_install_state()
        if plist is None or not self._plist_is_exact(plist) or state is None:
            raise SafetyError("start requires an exact manager-installed LaunchAgent plist")
        if state.get("installed_sha256") != self._sha256_bytes(self._plist_bytes()):
            raise SafetyError("start install-state hash mismatch")

        text = self._launchctl_print()
        listeners = self._listener_records()
        if text is not None:
            self._verify_loaded_identity(text, listeners)
            return {
                "ok": True,
                "action": "start",
                "changed": False,
                "service": self.config.service,
                "readiness": self._wait_for_readiness(),
            }
        if listeners:
            raise SafetyError(f"port {self.config.port} already has an unmanaged listener")

        result = self._run((self.config.launchctl, "bootstrap", f"gui/{os.geteuid()}", self.config.plist_path))
        if result.returncode != 0:
            raise SafetyError(f"failed to bootstrap dedicated embedding LaunchAgent: {(result.stderr or result.stdout).strip()}")
        try:
            readiness = self._wait_for_readiness()
        except Exception:
            rollback = self._run((self.config.launchctl, "bootout", self.config.service))
            if rollback.returncode != 0:
                raise SafetyError("embedding start failed and launchd rollback also failed")
            raise
        return {
            "ok": True,
            "action": "start",
            "changed": True,
            "service": self.config.service,
            "readiness": readiness,
        }

    def stop(self) -> dict[str, Any]:
        plist = self._read_existing_plist(require_compatible=True)
        text = self._launchctl_print()
        listeners = self._listener_records()
        if text is None:
            if listeners:
                raise SafetyError(f"port {self.config.port} has an unmanaged listener; refusing stop")
            return {"ok": True, "action": "stop", "changed": False, "service": self.config.service}
        if plist is None:
            raise SafetyError("loaded LaunchAgent has no manager-owned plist; refusing stop")
        info = self._verify_loaded_identity(text, listeners)
        result = self._run((self.config.launchctl, "bootout", self.config.service))
        if result.returncode != 0:
            raise SafetyError(f"failed to bootout dedicated embedding LaunchAgent: {(result.stderr or result.stdout).strip()}")
        self._wait_for_stop_absence(info["pid"])
        return {"ok": True, "action": "stop", "changed": True, "service": self.config.service}

    def uninstall(self) -> dict[str, Any]:
        state = self._read_install_state()
        if state is None:
            raise SafetyError("uninstall requires manager install-state ownership")
        expected_sha = self._sha256_bytes(self._plist_bytes())
        if state.get("installed_sha256") != expected_sha:
            raise SafetyError("uninstall install-state hash mismatch")
        current = self._read_existing_plist(require_compatible=True)
        if current is None or not self._plist_is_exact(current):
            raise SafetyError("uninstall refuses a missing or modified managed plist")

        stop_result = self.stop()
        backup_path_text = state.get("backup_path")
        restored = False
        if state.get("prior_present"):
            if not isinstance(backup_path_text, str):
                raise SafetyError("uninstall backup metadata is missing")
            backup_path = Path(backup_path_text)
            try:
                backup_path.relative_to(self.config.backup_root)
            except ValueError as exc:
                raise SafetyError("uninstall backup escapes the managed backup directory") from exc
            self._validate_regular_file(backup_path, "LaunchAgent backup", user_owned_only=True)
            backup_bytes = backup_path.read_bytes()
            try:
                backup_plist = plistlib.loads(backup_bytes)
            except Exception as exc:
                raise SafetyError("LaunchAgent backup is unreadable") from exc
            expected = self._expected_plist()
            for key in (
                "Label",
                "ProgramArguments",
                "EnvironmentVariables",
                "StandardOutPath",
                "StandardErrorPath",
            ):
                if backup_plist.get(key) != expected.get(key):
                    raise SafetyError(f"LaunchAgent backup ownership mismatch: {key}")
            self._atomic_write_bytes(self.config.plist_path, backup_bytes, 0o600)
            restored = True
        else:
            self.config.plist_path.unlink()
        self.config.install_state_path.unlink()
        return {
            "ok": True,
            "action": "uninstall",
            "changed": True,
            "restored_prior_plist": restored,
            "stop_changed": stop_result["changed"],
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage only Alden's pinned local E5 LaunchAgent on 127.0.0.1:11236."
    )
    parser.add_argument("action", choices=("status", "preflight", "install", "start", "stop", "uninstall"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = production_config()
    manager = AldenEmbeddingLaunchdManager(config)
    try:
        result = getattr(manager, args.action)()
    except (SafetyError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {"ok": False, "action": args.action, "error": str(exc)},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
