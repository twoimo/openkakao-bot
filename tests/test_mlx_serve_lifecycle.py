"""Tests for the app-owned MLX Serve lifecycle contract.

These tests never spawn a real process, open a socket, or load a model: every
side effect is injected, so what is measured is the fail-closed ownership
contract itself.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.mlx_serve_lifecycle import (
    MLX_SERVE_DEFAULT_PORT,
    MLX_SERVE_OWNER_STATES,
    MLX_SERVE_STATE_MAX_BYTES,
    MLX_SERVE_STATE_NAME,
    AppOwnedServerRecord,
    LaunchHooks,
    LaunchStage,
    MlxLaunchSpec,
    _default_free_bytes,
    _model_basename,
    attest_app_owned_server,
    launch_app_owned_server,
    ownership_status,
    read_app_owned_state,
    stop_app_owned_server,
    validate_executable,
    write_app_owned_state,
)


RESIDENT_NAME = "Qwen3.8-27B-MLX-Serve-4bit"


class FakeClock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.value = float(start)

    def now(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += float(seconds)


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid


def make_spec(root: Path, **overrides) -> MlxLaunchSpec:
    resident = root / "models" / RESIDENT_NAME
    resident.mkdir(parents=True, exist_ok=True)
    models_dir = root / "models"
    executable = root / "Apps" / "MLX Core.app" / "Contents" / "MacOS" / "mlx-serve"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    kwargs = {
        "executable": executable,
        "resident_model_dir": resident,
        "models_dir": models_dir,
        "log_path": root / "logs" / "mlx-serve.log",
        "minimum_free_bytes": 1,
    }
    kwargs.update(overrides)
    return MlxLaunchSpec(**kwargs)


class Harness:
    """Injected seams that model a server coming up without a real process."""

    def __init__(self, spec: MlxLaunchSpec, *, pid: int = 4242) -> None:
        self.spec = spec
        self.pid = pid
        self.clock = FakeClock()
        self.listeners: set[int] = set()
        self.signals: list[tuple[int, str]] = []
        self.spawn_calls: list[tuple[str, ...]] = []
        self.spawn_error: Exception | None = None
        self.spawn_alive = True
        self.spawn_listens = True
        self.spawn_command: str | None = None
        self.alive: set[int] = set()
        self.commands: dict[int, str] = {}
        self.catalog_sequence: list[tuple[str, ...]] = []
        self.catalog_calls = 0
        self.free_bytes = 8 * 1024**3
        self.free_error: Exception | None = None
        self.executable_reason = ""

    def hooks(self) -> LaunchHooks:
        return LaunchHooks(
            popen=self._popen,
            listener_pids=lambda port: tuple(self.listeners),
            command_of=lambda pid: self.commands.get(pid, ""),
            alive_probe=lambda pid: pid in self.alive,
            catalog_reader=self._catalog,
            free_reader=self._free,
            executable_probe=lambda path: self.executable_reason,
            signal_process=lambda pid, name: self.signals.append((pid, name)),
            sleeper=self.clock.sleep,
            clock=self.clock.now,
            wait_budget=5.0,
            poll_interval=1.0,
        )

    def _popen(self, command, log_path):
        if self.spawn_error is not None:
            raise self.spawn_error
        self.spawn_calls.append(tuple(command))
        if self.spawn_alive:
            self.alive.add(self.pid)
        if self.spawn_listens:
            self.listeners.add(self.pid)
        self.commands[self.pid] = self.spawn_command or " ".join(command)
        return FakeProcess(self.pid)

    def _catalog(self, host, port):
        index = self.catalog_calls
        self.catalog_calls += 1
        if not self.catalog_sequence:
            return ()
        if index < len(self.catalog_sequence):
            return self.catalog_sequence[index]
        return self.catalog_sequence[-1]

    def _free(self):
        if self.free_error is not None:
            raise self.free_error
        return self.free_bytes


class LaunchHappyPathTests(unittest.TestCase):
    def test_launch_attests_and_persists_ownership(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            spec = make_spec(root)
            harness = Harness(spec)
            harness.catalog_sequence = [(), (RESIDENT_NAME,)]

            result = launch_app_owned_server(spec, root / "state", hooks=harness.hooks())

            self.assertTrue(result.ok)
            self.assertEqual(result.stage, LaunchStage.READY)
            self.assertEqual(result.reason, "launch_ready")
            self.assertEqual(result.pid, harness.pid)
            self.assertEqual(
                list(result.stages),
                [
                    "validate",
                    "executable",
                    "model_dir",
                    "models_dir",
                    "port_check",
                    "memory_check",
                    "state_check",
                    "spawn",
                    "startup",
                    "attest",
                    "ready",
                ],
            )
            record = read_app_owned_state(root / "state")
            self.assertIsNotNone(record)
            assert record is not None
            self.assertEqual(record.pid, harness.pid)
            self.assertEqual(record.model, RESIDENT_NAME)
            self.assertEqual(record.prefix, spec.prefix())
            self.assertEqual(record.command_digest, spec.command_digest())

    def test_command_is_an_argument_tuple_with_attested_prefix(self) -> None:
        with TemporaryDirectory() as raw:
            spec = make_spec(Path(raw))
            command = spec.command()
            self.assertIsInstance(command, tuple)
            self.assertEqual(command[:2], (str(spec.executable), "--serve"))
            self.assertEqual(command[3], str(spec.resident_model_dir))
            self.assertEqual(command[5], str(spec.models_dir))
            self.assertNotIn(";", " ".join(command))
            self.assertEqual(spec.prefix(), command[:6])

    def test_catalog_accepts_prefixed_serving_id(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            spec = make_spec(root)
            harness = Harness(spec)
            harness.catalog_sequence = [("mlx/ddalcu/" + RESIDENT_NAME,)]

            result = launch_app_owned_server(spec, root / "state", hooks=harness.hooks())

            self.assertTrue(result.ok)
            self.assertEqual(result.reason, "launch_ready")

    def test_already_running_requires_matching_attested_record(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            spec = make_spec(root)
            harness = Harness(spec)
            record = AppOwnedServerRecord(
                harness.pid,
                spec.host,
                spec.port,
                RESIDENT_NAME,
                spec.prefix(),
                spec.command_digest(),
                harness.clock.now(),
            )
            write_app_owned_state(root / "state", record)
            harness.alive.add(harness.pid)
            harness.listeners.add(harness.pid)
            harness.commands[harness.pid] = " ".join(spec.command())

            result = launch_app_owned_server(spec, root / "state", hooks=harness.hooks())

            self.assertTrue(result.ok)
            self.assertEqual(result.stage, LaunchStage.ALREADY_RUNNING)
            self.assertEqual(result.reason, "launch_already_running")
            self.assertEqual(harness.spawn_calls, [])


class ForeignOwnershipTests(unittest.TestCase):
    def test_foreign_listener_is_never_adopted(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            spec = make_spec(root)
            harness = Harness(spec)
            foreign_pid = 999_999
            harness.listeners.add(foreign_pid)
            harness.alive.add(foreign_pid)
            harness.commands[foreign_pid] = "/Applications/MLX Core.app/Contents/MacOS/mlx-serve --serve"

            result = launch_app_owned_server(spec, root / "state", hooks=harness.hooks())

            self.assertFalse(result.ok)
            self.assertEqual(result.reason, "launch_port_in_use")
            self.assertEqual(harness.spawn_calls, [])
            self.assertEqual(harness.signals, [])
            self.assertIsNone(read_app_owned_state(root / "state"))
            status = ownership_status(root / "state", hooks=harness.hooks())
            self.assertEqual(status["owner_state"], "foreign_listener")
            self.assertFalse(status["app_owned"])

    def test_state_for_another_pid_does_not_unlock_port(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            spec = make_spec(root)
            harness = Harness(spec)
            foreign_pid = 999_999
            harness.listeners.add(foreign_pid)
            record = AppOwnedServerRecord(
                harness.pid,
                spec.host,
                spec.port,
                RESIDENT_NAME,
                spec.prefix(),
                spec.command_digest(),
                harness.clock.now(),
            )
            write_app_owned_state(root / "state", record)
            harness.alive.add(harness.pid)
            harness.commands[harness.pid] = " ".join(spec.command())

            result = launch_app_owned_server(spec, root / "state", hooks=harness.hooks())

            self.assertFalse(result.ok)
            self.assertEqual(result.reason, "launch_port_in_use")
            self.assertEqual(harness.spawn_calls, [])
            self.assertEqual(harness.signals, [])

    def test_record_with_drifted_command_is_not_adopted(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            spec = make_spec(root)
            harness = Harness(spec)
            record = AppOwnedServerRecord(
                harness.pid,
                spec.host,
                spec.port,
                RESIDENT_NAME,
                spec.prefix(),
                "0" * 16,
                harness.clock.now(),
            )
            write_app_owned_state(root / "state", record)
            harness.alive.add(harness.pid)
            harness.listeners.add(harness.pid)
            harness.commands[harness.pid] = " ".join(spec.command())

            result = launch_app_owned_server(spec, root / "state", hooks=harness.hooks())

            self.assertFalse(result.ok)
            self.assertEqual(result.reason, "launch_port_in_use")
            self.assertNotIn("attest_command_mismatch", list(result.stages))


class RefusalStageTests(unittest.TestCase):
    def test_insufficient_memory_refuses_before_spawn(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            spec = make_spec(root, minimum_free_bytes=64 * 1024**3)
            harness = Harness(spec)

            result = launch_app_owned_server(spec, root / "state", hooks=harness.hooks())

            self.assertFalse(result.ok)
            self.assertEqual(result.reason, "launch_memory_insufficient")
            self.assertEqual(harness.spawn_calls, [])

    def test_unavailable_memory_reading_is_not_treated_as_free(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            spec = make_spec(root)
            harness = Harness(spec)
            harness.free_error = RuntimeError("memory_budget_unavailable")

            result = launch_app_owned_server(spec, root / "state", hooks=harness.hooks())

            self.assertFalse(result.ok)
            self.assertEqual(result.reason, "launch_memory_unavailable")
            self.assertEqual(harness.spawn_calls, [])

    def test_unsafe_executable_is_refused_before_spawn(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            spec = make_spec(root)
            harness = Harness(spec)
            harness.executable_reason = "launch_executable_unsafe"

            result = launch_app_owned_server(spec, root / "state", hooks=harness.hooks())

            self.assertFalse(result.ok)
            self.assertEqual(result.reason, "launch_executable_unsafe")
            self.assertEqual(harness.spawn_calls, [])

    def test_missing_model_directories_are_refused(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            spec = make_spec(root)
            harness = Harness(spec)
            missing = make_spec(root, resident_model_dir=root / "nope" / RESIDENT_NAME)

            result = launch_app_owned_server(missing, root / "state", hooks=harness.hooks())

            self.assertFalse(result.ok)
            self.assertEqual(result.reason, "launch_model_dir_missing")
            self.assertEqual(harness.spawn_calls, [])

    def test_invalid_spec_is_refused_before_any_probe(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            spec = make_spec(root, host="0.0.0.0")
            harness = Harness(spec)

            result = launch_app_owned_server(spec, root / "state", hooks=harness.hooks())

            self.assertFalse(result.ok)
            self.assertEqual(result.reason, "launch_invalid_spec")
            self.assertEqual(harness.spawn_calls, [])
            self.assertEqual(result.stages, ("validate",))

    def test_spawn_failure_is_reported_without_state(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            spec = make_spec(root)
            harness = Harness(spec)
            harness.spawn_error = OSError("no such file")

            result = launch_app_owned_server(spec, root / "state", hooks=harness.hooks())

            self.assertFalse(result.ok)
            self.assertEqual(result.reason, "launch_spawn_failed")
            self.assertIsNone(read_app_owned_state(root / "state"))


class StartupAndAttestationTests(unittest.TestCase):
    def test_child_that_exits_is_reported_and_cleaned_up(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            spec = make_spec(root)
            harness = Harness(spec)
            harness.spawn_alive = False

            result = launch_app_owned_server(spec, root / "state", hooks=harness.hooks())

            self.assertFalse(result.ok)
            self.assertEqual(result.reason, "launch_spawn_exited")
            self.assertEqual(harness.signals, [(harness.pid, "TERM")])
            self.assertIsNone(read_app_owned_state(root / "state"))

    def test_startup_timeout_is_bounded_by_wait_budget(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            spec = make_spec(root)
            harness = Harness(spec)
            start = harness.clock.now()

            result = launch_app_owned_server(spec, root / "state", hooks=harness.hooks())

            self.assertFalse(result.ok)
            self.assertEqual(result.reason, "launch_startup_timeout")
            self.assertLessEqual(harness.clock.now() - start, 5.0)
            self.assertGreater(harness.clock.now() - start, 0.0)
            self.assertEqual(harness.signals, [(harness.pid, "TERM")])
            self.assertIsNone(read_app_owned_state(root / "state"))

    def test_state_is_persisted_only_after_attestation(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            spec = make_spec(root)
            harness = Harness(spec)
            harness.catalog_sequence = [(RESIDENT_NAME,)]
            harness.spawn_command = "/usr/bin/true"

            result = launch_app_owned_server(spec, root / "state", hooks=harness.hooks())

            self.assertFalse(result.ok)
            self.assertEqual(result.reason, "launch_attest_failed")
            self.assertEqual(harness.signals, [(harness.pid, "TERM")])
            self.assertIsNone(read_app_owned_state(root / "state"))

    def test_absent_socket_attestation_is_denied(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            spec = make_spec(root)
            harness = Harness(spec)
            harness.catalog_sequence = [(RESIDENT_NAME,)]
            harness.spawn_listens = False

            result = launch_app_owned_server(spec, root / "state", hooks=harness.hooks())

            self.assertFalse(result.ok)
            self.assertEqual(result.reason, "launch_attest_failed")
            self.assertIsNone(read_app_owned_state(root / "state"))

    def test_symlinked_state_file_blocks_launch(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            spec = make_spec(root)
            harness = Harness(spec)
            harness.catalog_sequence = [(RESIDENT_NAME,)]
            state_root = root / "state"
            state_root.mkdir(parents=True, exist_ok=True)
            target = root / "elsewhere.json"
            target.write_text("{}", encoding="utf-8")
            os.symlink(target, state_root / MLX_SERVE_STATE_NAME)

            result = launch_app_owned_server(spec, state_root, hooks=harness.hooks())

            self.assertFalse(result.ok)
            self.assertEqual(result.reason, "launch_state_unsafe")
            self.assertEqual(harness.spawn_calls, [])


class StopTests(unittest.TestCase):
    def _record(self, spec: MlxLaunchSpec, pid: int) -> AppOwnedServerRecord:
        return AppOwnedServerRecord(
            pid,
            spec.host,
            spec.port,
            RESIDENT_NAME,
            spec.prefix(),
            spec.command_digest(),
            1_000_000.0,
        )

    def test_stop_without_state_is_a_no_op(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            spec = make_spec(root)
            harness = Harness(spec)

            result = stop_app_owned_server(root / "state", hooks=harness.hooks())

            self.assertFalse(result.ok)
            self.assertEqual(result.reason, "stop_no_owned_server")
            self.assertEqual(harness.signals, [])

    def test_stop_refuses_a_dead_pid(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            spec = make_spec(root)
            harness = Harness(spec)
            write_app_owned_state(root / "state", self._record(spec, harness.pid))

            result = stop_app_owned_server(root / "state", hooks=harness.hooks())

            self.assertFalse(result.ok)
            self.assertEqual(result.reason, "stop_owner_mismatch")
            self.assertEqual(harness.signals, [])
            self.assertIsNotNone(read_app_owned_state(root / "state"))

    def test_stop_refuses_a_pid_that_is_not_listening(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            spec = make_spec(root)
            harness = Harness(spec)
            write_app_owned_state(root / "state", self._record(spec, harness.pid))
            harness.alive.add(harness.pid)
            harness.commands[harness.pid] = " ".join(spec.command())

            result = stop_app_owned_server(root / "state", hooks=harness.hooks())

            self.assertFalse(result.ok)
            self.assertEqual(result.reason, "stop_owner_mismatch")
            self.assertEqual(harness.signals, [])

    def test_stop_refuses_a_reused_pid_with_a_different_command(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            spec = make_spec(root)
            harness = Harness(spec)
            write_app_owned_state(root / "state", self._record(spec, harness.pid))
            harness.alive.add(harness.pid)
            harness.listeners.add(harness.pid)
            harness.commands[harness.pid] = "/bin/zsh"

            result = stop_app_owned_server(root / "state", hooks=harness.hooks())

            self.assertFalse(result.ok)
            self.assertEqual(result.reason, "stop_owner_mismatch")
            self.assertEqual(harness.signals, [])

    def test_stop_signals_only_the_attested_pid(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            spec = make_spec(root)
            harness = Harness(spec)
            write_app_owned_state(root / "state", self._record(spec, harness.pid))
            harness.alive.add(harness.pid)
            harness.listeners.add(harness.pid)
            harness.commands[harness.pid] = " ".join(spec.command())

            result = stop_app_owned_server(root / "state", hooks=harness.hooks())

            self.assertTrue(result.ok)
            self.assertEqual(result.reason, "stop_stopped")
            self.assertEqual(harness.signals, [(harness.pid, "TERM")])
            self.assertIsNone(read_app_owned_state(root / "state"))

    def test_signal_failure_is_reported_not_swallowed(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            spec = make_spec(root)
            harness = Harness(spec)
            write_app_owned_state(root / "state", self._record(spec, harness.pid))
            harness.alive.add(harness.pid)
            harness.listeners.add(harness.pid)
            harness.commands[harness.pid] = " ".join(spec.command())
            hooks = harness.hooks()
            hooks.signal_process = lambda pid, name: (_ for _ in ()).throw(ProcessLookupError())

            result = stop_app_owned_server(root / "state", hooks=hooks)

            self.assertFalse(result.ok)
            self.assertEqual(result.reason, "stop_signal_failed")
            self.assertIsNotNone(read_app_owned_state(root / "state"))


class OwnershipStatusTests(unittest.TestCase):
    def _record(self, spec: MlxLaunchSpec, pid: int) -> AppOwnedServerRecord:
        return AppOwnedServerRecord(
            pid,
            spec.host,
            spec.port,
            RESIDENT_NAME,
            spec.prefix(),
            spec.command_digest(),
            1_000_000.0,
        )

    def test_attested_record_reports_app_owned(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            spec = make_spec(root)
            harness = Harness(spec)
            write_app_owned_state(root / "state", self._record(spec, harness.pid))
            harness.alive.add(harness.pid)
            harness.listeners.add(harness.pid)
            harness.commands[harness.pid] = " ".join(spec.command())

            status = ownership_status(root / "state", hooks=harness.hooks())

            self.assertEqual(status["owner_state"], "app_owned")
            self.assertTrue(status["app_owned"])
            self.assertEqual(status["model"], RESIDENT_NAME)

    def test_dead_pid_reports_stopped(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            spec = make_spec(root)
            harness = Harness(spec)
            write_app_owned_state(root / "state", self._record(spec, harness.pid))

            status = ownership_status(root / "state", hooks=harness.hooks())

            self.assertEqual(status["owner_state"], "stopped")
            self.assertFalse(status["app_owned"])

    def test_drifted_command_reports_state_stale(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            spec = make_spec(root)
            harness = Harness(spec)
            write_app_owned_state(root / "state", self._record(spec, harness.pid))
            harness.alive.add(harness.pid)
            harness.listeners.add(harness.pid)
            harness.commands[harness.pid] = "/bin/zsh"

            status = ownership_status(root / "state", hooks=harness.hooks())

            self.assertEqual(status["owner_state"], "state_stale")

    def test_missing_state_with_no_listener_reports_stopped(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            harness = Harness(make_spec(root))

            status = ownership_status(root / "state", hooks=harness.hooks())

            self.assertEqual(status["owner_state"], "stopped")

    def test_world_readable_state_is_rejected(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            spec = make_spec(root)
            harness = Harness(spec)
            state_root = root / "state"
            state_root.mkdir(parents=True, exist_ok=True)
            payload = self._record(spec, harness.pid).payload()
            path = state_root / MLX_SERVE_STATE_NAME
            path.write_text(json.dumps(payload), encoding="utf-8")
            path.chmod(0o644)

            status = ownership_status(state_root, hooks=harness.hooks())

            self.assertEqual(status["owner_state"], "state_invalid")
            self.assertIsNone(read_app_owned_state(state_root))

    def test_corrupt_and_oversized_state_is_rejected(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            harness = Harness(make_spec(root))
            state_root = root / "state"
            state_root.mkdir(parents=True, exist_ok=True)
            path = state_root / MLX_SERVE_STATE_NAME
            path.write_text("not-json", encoding="utf-8")
            path.chmod(0o600)
            self.assertEqual(
                ownership_status(state_root, hooks=harness.hooks())["owner_state"],
                "state_invalid",
            )
            path.write_text("x" * (MLX_SERVE_STATE_MAX_BYTES + 1), encoding="utf-8")
            path.chmod(0o600)
            self.assertEqual(
                ownership_status(state_root, hooks=harness.hooks())["owner_state"],
                "state_invalid",
            )

    def test_owner_state_is_always_an_allowlisted_code(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            harness = Harness(make_spec(root))
            for state_root in (root / "state", root, root / "missing"):
                status = ownership_status(state_root, hooks=harness.hooks())
                self.assertIn(status["owner_state"], MLX_SERVE_OWNER_STATES)


class StateFileContractTests(unittest.TestCase):
    def test_permissions_and_payload_shape(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            spec = make_spec(root)
            state_root = root / "state"
            record = AppOwnedServerRecord(
                4242,
                spec.host,
                spec.port,
                RESIDENT_NAME,
                spec.prefix(),
                spec.command_digest(),
                1_000_000.0,
            )

            write_app_owned_state(state_root, record)

            path = state_root / MLX_SERVE_STATE_NAME
            mode = stat.S_IMODE(path.stat().st_mode)
            self.assertEqual(mode, 0o600)
            self.assertEqual(stat.S_IMODE(state_root.stat().st_mode), 0o700)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                set(payload),
                {
                    "schema_version",
                    "pid",
                    "host",
                    "port",
                    "model",
                    "prefix",
                    "command_digest",
                    "started_at",
                },
            )
            self.assertEqual(len(payload["prefix"]), 6)
            self.assertNotIn("message", payload)
            self.assertNotIn("chat_id", payload)
            self.assertEqual(read_app_owned_state(state_root), record)

    def test_record_parser_rejects_bad_payloads(self) -> None:
        base = {
            "schema_version": 1,
            "pid": 4242,
            "host": "127.0.0.1",
            "port": MLX_SERVE_DEFAULT_PORT,
            "model": RESIDENT_NAME,
            "prefix": ["/x/mlx-serve", "--serve", "--model", "/m", "--model-dir", "/m"],
            "command_digest": "a" * 16,
            "started_at": 1_000_000.0,
        }
        for mutation in (
            {"schema_version": 2},
            {"pid": 0},
            {"port": 80},
            {"host": "0.0.0.0"},
            {"model": "a/b"},
            {"model": ""},
            {"command_digest": "short"},
            {"prefix": ["only", "two"]},
            {"prefix": ["", "x", "y", "z", "q", "w"]},
            {"started_at": 0},
        ):
            with TemporaryDirectory() as raw:
                root = Path(raw)
                state_root = root / "state"
                state_root.mkdir(parents=True, exist_ok=True)
                payload = dict(base)
                payload.update(mutation)
                path = state_root / MLX_SERVE_STATE_NAME
                path.write_text(json.dumps(payload), encoding="utf-8")
                path.chmod(0o600)
                self.assertIsNone(
                    read_app_owned_state(state_root),
                    msg=f"accepted bad payload: {mutation}",
                )

    def test_write_refuses_a_symlinked_state_file(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            state_root = root / "state"
            state_root.mkdir(parents=True, exist_ok=True)
            target = root / "target.json"
            target.write_text("{}", encoding="utf-8")
            os.symlink(target, state_root / MLX_SERVE_STATE_NAME)
            spec = make_spec(root)
            record = AppOwnedServerRecord(
                4242,
                spec.host,
                spec.port,
                RESIDENT_NAME,
                spec.prefix(),
                spec.command_digest(),
                1_000_000.0,
            )

            with self.assertRaises(OSError):
                write_app_owned_state(state_root, record)
            self.assertEqual(target.read_text(encoding="utf-8"), "{}")


class ExecutableValidationTests(unittest.TestCase):
    def _bundle_binary(self, root: Path, name: str = "mlx-serve") -> Path:
        binary = root / "MLX Core.app" / "Contents" / "MacOS" / name
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        binary.chmod(0o755)
        return binary

    def test_rejects_relative_or_misnamed_paths(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            self.assertEqual(
                validate_executable(Path("mlx-serve"), home=root),
                "launch_executable_unsafe",
            )
            self.assertEqual(
                validate_executable(self._bundle_binary(root, "mlx"), home=root),
                "launch_executable_unsafe",
            )

    def test_rejects_missing_non_regular_and_non_executable(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            missing = root / "MLX Core.app" / "Contents" / "MacOS" / "mlx-serve"
            self.assertEqual(
                validate_executable(missing, home=root), "launch_executable_unsafe"
            )
            plain = self._bundle_binary(root)
            plain.chmod(0o644)
            self.assertEqual(
                validate_executable(plain, home=root), "launch_executable_unsafe"
            )

    def test_rejects_symlinked_binary(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            target = self._bundle_binary(root)
            link = root / "Linked.app" / "Contents" / "MacOS" / "mlx-serve"
            link.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(target, link)
            self.assertEqual(
                validate_executable(link, home=root), "launch_executable_unsafe"
            )

    def test_rejects_paths_outside_home_and_applications(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            binary = self._bundle_binary(root)
            other = root / "other-home"
            other.mkdir(parents=True, exist_ok=True)
            self.assertEqual(
                validate_executable(binary, home=other), "launch_executable_unsafe"
            )

    def test_requires_signed_bundle(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            binary = self._bundle_binary(root)
            self.assertEqual(
                validate_executable(
                    binary, home=root, codesign=lambda bundle: "launch_executable_unverified"
                ),
                "launch_executable_unverified",
            )
            self.assertEqual(
                validate_executable(binary, home=root, codesign=lambda bundle: ""), ""
            )

    def test_binary_outside_a_bundle_is_unverified(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            plain = root / "bin" / "mlx-serve"
            plain.parent.mkdir(parents=True, exist_ok=True)
            plain.write_text("#!/bin/sh\n", encoding="utf-8")
            plain.chmod(0o755)
            self.assertEqual(
                validate_executable(plain, home=root), "launch_executable_unverified"
            )

    def test_codesign_failure_is_fail_closed(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            binary = self._bundle_binary(root)

            def boom(bundle):
                raise RuntimeError("codesign exploded")

            self.assertEqual(
                validate_executable(binary, home=root, codesign=boom),
                "launch_executable_unverified",
            )


class ReportRedactionTests(unittest.TestCase):
    def test_launch_and_stop_reports_expose_no_paths_or_pids(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            spec = make_spec(root)
            harness = Harness(spec)
            harness.catalog_sequence = [(RESIDENT_NAME,)]
            launch = launch_app_owned_server(spec, root / "state", hooks=harness.hooks())
            report = launch.report()

            self.assertEqual(
                set(report),
                {"ok", "action", "stage", "reason", "model", "stages"},
            )
            self.assertNotIn("pid", report)
            blob = json.dumps(report)
            self.assertNotIn(str(root), blob)
            self.assertNotIn(str(spec.executable), blob)
            self.assertNotIn(".app", blob)
            self.assertNotIn("--serve", blob)
            self.assertNotIn("--model", blob)

            stop = stop_app_owned_server(root / "state", hooks=harness.hooks())
            stop_report = stop.report()
            self.assertEqual(set(stop_report), {"ok", "action", "reason"})
            stop_blob = json.dumps(stop_report)
            self.assertNotIn(str(root), stop_blob)
            self.assertNotIn(".app", stop_blob)
            self.assertNotIn("--serve", stop_blob)

    def test_status_report_exposes_no_paths_or_pids(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            spec = make_spec(root)
            harness = Harness(spec)
            write_app_owned_state(
                root / "state",
                AppOwnedServerRecord(
                    harness.pid,
                    spec.host,
                    spec.port,
                    RESIDENT_NAME,
                    spec.prefix(),
                    spec.command_digest(),
                    1_000_000.0,
                ),
            )
            harness.alive.add(harness.pid)
            harness.listeners.add(harness.pid)
            harness.commands[harness.pid] = " ".join(spec.command())

            status = ownership_status(root / "state", hooks=harness.hooks())

            self.assertEqual(
                set(status), {"ok", "action", "owner_state", "app_owned", "model"}
            )
            blob = json.dumps(status)
            self.assertNotIn(str(root), blob)
            self.assertNotIn(str(spec.executable), blob)
            self.assertNotIn(".app", blob)
            self.assertNotIn("--serve", blob)

    def test_unknown_reasons_are_mapped_to_safe_codes(self) -> None:
        from scripts.mlx_serve_lifecycle import LaunchResult, StopResult

        launch = LaunchResult(False, LaunchStage.FAILED, "totally_made_up", ("spawn",), 7, None)
        self.assertEqual(launch.report()["reason"], "launch_invalid_spec")
        self.assertNotIn("pid", launch.report())
        stop = StopResult(False, "totally_made_up", 7)
        self.assertEqual(stop.report()["reason"], "stop_no_owned_server")


class DefaultProbeTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin", "vm_stat is macOS only")
    def test_free_bytes_probe_returns_a_positive_integer(self) -> None:
        value = _default_free_bytes()
        self.assertIsInstance(value, int)
        self.assertGreater(value, 0)

    def test_listener_probe_never_invents_a_pid(self) -> None:
        from scripts.mlx_serve_lifecycle import _default_listener_pids

        # Port 1 cannot host a listener for this user; the probe must answer
        # with an empty tuple rather than fabricating ownership.
        self.assertEqual(_default_listener_pids(1), ())

    def test_model_basename_normalises_serving_ids(self) -> None:
        self.assertEqual(_model_basename("mlx/ddalcu/" + RESIDENT_NAME), RESIDENT_NAME)
        self.assertEqual(_model_basename(RESIDENT_NAME + "/"), RESIDENT_NAME)
        self.assertEqual(_model_basename(""), "")
        self.assertEqual(_model_basename(None), "")


if __name__ == "__main__":
    unittest.main()
