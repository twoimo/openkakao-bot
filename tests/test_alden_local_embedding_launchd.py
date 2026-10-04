"""Focused safety tests for Alden's dedicated local E5 LaunchAgent manager."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import pwd
import sys
import tempfile
import threading
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "manage-alden-local-embedding-launchd.py"
SPEC = importlib.util.spec_from_file_location("alden_local_embedding_launchd", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MANAGER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MANAGER
SPEC.loader.exec_module(MANAGER)


class FakeRunner:
    def __init__(self, config) -> None:
        self.config = config
        self.loaded = False
        self.pid = 4242
        self.listener = False
        self.commands: list[tuple[str, ...]] = []
        self.fail: dict[str, int] = {}
        self.command_override: str | None = None
        self.comm_override: str | None = None
        self.auto_listener_on_bootstrap = True
        self.bootout_clears_loaded = True
        self.bootout_clears_listener = True
        self.listener_pid_override: int | None = None
        self.bootout_listener_pid_override: int | None = None

    def run(self, argv):
        args = tuple(str(value) for value in argv)
        self.commands.append(args)
        action = args[1] if args and args[0] == str(self.config.launchctl) and len(args) > 1 else None
        if action in self.fail:
            return MANAGER.CommandResult(self.fail[action], "", f"forced {action} failure")
        if args[0] == str(self.config.plutil):
            return MANAGER.CommandResult(0, f"{args[-1]}: OK\n", "")
        if args[0] == str(self.config.launchctl):
            if action == "print":
                if not self.loaded:
                    return MANAGER.CommandResult(113, "", "Could not find service\n")
                return MANAGER.CommandResult(
                    0,
                    f"path = {self.config.plist_path}\n"
                    f"program = {self.config.python_path}\n"
                    f"pid = {self.pid}\n",
                    "",
                )
            if action == "bootstrap":
                self.loaded = True
                if self.auto_listener_on_bootstrap:
                    self.listener = True
                return MANAGER.CommandResult(0, "", "")
            if action == "kickstart":
                self.listener = True
                return MANAGER.CommandResult(0, "", "")
            if action == "bootout":
                if self.bootout_clears_loaded:
                    self.loaded = False
                if self.bootout_clears_listener:
                    self.listener = False
                if self.bootout_listener_pid_override is not None:
                    self.listener_pid_override = self.bootout_listener_pid_override
                return MANAGER.CommandResult(0, "", "")
        if args[0] == str(self.config.lsof):
            if not self.listener:
                return MANAGER.CommandResult(1, "", "")
            listener_pid = self.listener_pid_override or self.pid
            return MANAGER.CommandResult(
                0,
                f"p{listener_pid}\nn{self.config.host}:{self.config.port}\n",
                "",
            )
        if args[0] == str(self.config.ps):
            if "comm=" in args:
                executable = self.comm_override or str(self.config.python_path)
                return MANAGER.CommandResult(0, f"{os.geteuid()} {executable}\n", "")
            command = self.command_override or " ".join(self.config.program_arguments)
            return MANAGER.CommandResult(0, f"{command}\n", "")
        raise AssertionError(f"unexpected command: {args}")


class FakeAdapterHandler(BaseHTTPRequestHandler):
    model_id = ""
    revision = ""
    dimensions = 384
    wrong_model = False
    redirect_models = False

    def log_message(self, _format, *_args):
        return

    def _send(self, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path == "/redirected-models":
            self._send(
                {
                    "object": "list",
                    "data": [
                        {
                            "id": self.model_id,
                            "loaded": True,
                            "state": "ready",
                            "capabilities": ["embedding"],
                            "revision": self.revision,
                            "license": "MIT",
                            "dimensions": self.dimensions,
                        }
                    ],
                }
            )
            return
        if self.path != "/v1/models":
            self.send_error(404)
            return
        if self.redirect_models:
            self.send_response(302)
            self.send_header("Location", "/redirected-models")
            self.end_headers()
            return
        model_id = "wrong/model" if self.wrong_model else self.model_id
        self._send(
            {
                "object": "list",
                "data": [
                    {
                        "id": model_id,
                        "loaded": True,
                        "state": "ready",
                        "capabilities": ["embedding", "embeddings"],
                        "revision": self.revision,
                        "license": "MIT",
                        "dimensions": self.dimensions,
                    }
                ],
            }
        )

    def do_POST(self):  # noqa: N802
        if self.path != "/v1/embeddings":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        model_id = "wrong/model" if self.wrong_model else self.model_id
        if payload.get("input_type") != "query":
            self.send_error(400)
            return
        self._send(
            {
                "object": "list",
                "model": model_id,
                "data": [{"index": 0, "embedding": [1.0] + [0.0] * (self.dimensions - 1)}],
            }
        )


@contextmanager
def fake_adapter(model_id: str, revision: str, *, wrong_model: bool = False, redirect_models: bool = False):
    class Handler(FakeAdapterHandler):
        pass

    Handler.model_id = model_id
    Handler.revision = revision
    Handler.wrong_model = wrong_model
    Handler.redirect_models = redirect_models
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


class AldenLocalEmbeddingLaunchdTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        home = root / "home"
        app_script = root / "Alden.app" / "Contents" / "Resources" / "scripts" / "alden_local_embedding_server.py"
        python_path = home / MANAGER.RUNTIME_PYTHON_RELATIVE
        python_target = home / ".local" / "share" / "uv" / "python" / "python3"
        model_path = home / MANAGER.MODEL_SNAPSHOT_RELATIVE
        model_root = model_path.parents[1]
        blobs = model_root / "blobs"
        for directory in (
            home / "Library" / "LaunchAgents",
            app_script.parent,
            python_path.parent,
            python_target.parent,
            model_path,
            blobs,
        ):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        app_bytes = b"#!/usr/bin/env python3\nprint('fake adapter')\n"
        app_script.write_bytes(app_bytes)
        app_script.chmod(0o644)
        python_target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        python_target.chmod(0o700)
        python_path.symlink_to(python_target)
        (home / MANAGER.RUNTIME_ROOT_RELATIVE / "pyvenv.cfg").write_text(
            "home = /fake/base/python\n",
            encoding="utf-8",
        )

        files = {
            "config.json": json.dumps(
                {
                    "model_type": "bert",
                    "hidden_size": 384,
                    "max_position_embeddings": 512,
                    "architectures": ["BertModel"],
                }
            ).encode(),
            "README.md": b"---\nlicense: mit\n---\n",
            "weights.00.safetensors": b"weights",
            "tokenizer.json": b"{}",
        }
        for index, (name, data) in enumerate(files.items()):
            blob = blobs / f"blob-{index}"
            blob.write_bytes(data)
            blob.chmod(0o600)
            (model_path / name).symlink_to(blob)

        self.config = MANAGER.Config(
            home=home,
            python_path=python_path,
            app_script=app_script,
            model_path=model_path,
            plist_path=home / "Library" / "LaunchAgents" / f"{MANAGER.LABEL}.plist",
            state_root=home / "Library" / "Application Support" / "openkakao" / "alden-local-embedding",
            expected_weight_bytes=len(files["weights.00.safetensors"]),
            expected_app_script_sha256=hashlib.sha256(app_bytes).hexdigest(),
            launchctl=root / "bin" / "launchctl",
            plutil=root / "bin" / "plutil",
            lsof=root / "bin" / "lsof",
            ps=root / "bin" / "ps",
            readiness_timeout_secs=0.25,
            readiness_poll_secs=0.01,
            stop_timeout_secs=0.05,
            stop_poll_secs=0.01,
        )
        for tool in (self.config.launchctl, self.config.plutil, self.config.lsof, self.config.ps):
            tool.parent.mkdir(parents=True, exist_ok=True)
            tool.write_text("fake\n", encoding="utf-8")
            tool.chmod(0o700)
        self.runner = FakeRunner(self.config)
        self.manager = MANAGER.AldenEmbeddingLaunchdManager(self.config, self.runner)

    def _compatible_prior_plist(self):
        payload = self.manager._expected_plist()
        payload["ThrottleInterval"] = 30
        return payload

    def test_production_contract_is_fixed_to_11236_and_installed_bundle(self):
        cfg = MANAGER.production_config()
        account_home = Path(pwd.getpwuid(os.geteuid()).pw_dir).resolve()
        self.assertEqual(cfg.home, account_home)
        self.assertEqual(cfg.host, "127.0.0.1")
        self.assertEqual(cfg.port, 11236)
        self.assertTrue(cfg.enforce_production_contract)
        self.assertEqual(
            cfg.app_script,
            Path("/Applications/Alden.app/Contents/Resources/scripts/alden_local_embedding_server.py"),
        )
        self.assertEqual(cfg.python_path, account_home / MANAGER.RUNTIME_PYTHON_RELATIVE)
        self.assertEqual(cfg.model_path, account_home / MANAGER.MODEL_SNAPSHOT_RELATIVE)
        self.assertEqual(cfg.plist_path, account_home / MANAGER.LAUNCH_AGENT_RELATIVE)
        self.assertEqual(cfg.state_root, account_home / MANAGER.STATE_ROOT_RELATIVE)
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("/Users/twoimo", source)
        self.assertNotIn("11234", source)
        self.assertNotIn("11235", source)

    def test_manager_pin_matches_current_adapter_source(self):
        source_sha = hashlib.sha256((ROOT / "scripts" / "alden_local_embedding_server.py").read_bytes()).hexdigest()
        self.assertEqual(MANAGER.DEPLOYMENT_APP_SCRIPT_SHA256, source_sha)

    def test_production_port_is_value_checked(self):
        cfg = replace(MANAGER.production_config(), port=11237)
        manager = MANAGER.AldenEmbeddingLaunchdManager(cfg, self.runner)
        with self.assertRaisesRegex(MANAGER.SafetyError, "production port mismatch"):
            manager._validate_production_contract()

    def test_user_managed_python_path_cannot_escape_account_home(self):
        outside = Path(self.tmp.name) / "outside-python"
        outside.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        outside.chmod(0o700)
        cfg = replace(self.config, python_path=outside)
        manager = MANAGER.AldenEmbeddingLaunchdManager(cfg, self.runner)
        with self.assertRaisesRegex(MANAGER.SafetyError, "embedding runtime Python escapes the effective account home"):
            manager._validate_static_assets()

    def test_plist_starts_at_login_and_has_no_keepalive(self):
        plist = self.manager._expected_plist()
        self.assertIs(plist["RunAtLoad"], True)
        self.assertNotIn("KeepAlive", plist)
        self.assertEqual(plist["ProgramArguments"][0], str(self.config.python_path))

    def test_process_identity_handles_runtime_path_with_spaces_and_rejects_wrong_args(self):
        self.manager._verify_process(self.runner.pid)
        self.runner.command_override = " ".join(self.config.program_arguments[:-1] + ("/wrong/model",))
        with self.assertRaisesRegex(MANAGER.SafetyError, "command arguments mismatch"):
            self.manager._verify_process(self.runner.pid)

    def test_http_readiness_ignores_environment_proxy(self):
        with fake_adapter(self.config.model_id, self.config.model_revision) as port:
            cfg = replace(self.config, port=port)
            manager = MANAGER.AldenEmbeddingLaunchdManager(cfg, self.runner)
            with mock.patch.dict(
                os.environ,
                {
                    "HTTP_PROXY": "http://127.0.0.1:9",
                    "HTTPS_PROXY": "http://127.0.0.1:9",
                    "ALL_PROXY": "http://127.0.0.1:9",
                    "NO_PROXY": "",
                },
                clear=False,
            ):
                payload = manager._http_json("GET", "/v1/models")
        self.assertEqual(payload["data"][0]["id"], self.config.model_id)

    def test_http_readiness_rejects_redirect(self):
        with fake_adapter(self.config.model_id, self.config.model_revision, redirect_models=True) as port:
            cfg = replace(self.config, port=port)
            manager = MANAGER.AldenEmbeddingLaunchdManager(cfg, self.runner)
            with self.assertRaisesRegex(MANAGER.SafetyError, "redirect rejected"):
                manager._http_json("GET", "/v1/models")

    def test_install_rolls_back_prior_compatible_plist_if_state_write_fails(self):
        prior = self._compatible_prior_plist()
        self.config.plist_path.write_bytes(plistlib.dumps(prior, sort_keys=False))
        self.config.plist_path.chmod(0o600)
        original = self.config.plist_path.read_bytes()
        with mock.patch.object(self.manager, "_atomic_write_json", side_effect=OSError("state write failed")):
            with self.assertRaisesRegex(OSError, "state write failed"):
                self.manager.install()
        self.assertEqual(self.config.plist_path.read_bytes(), original)
        self.assertFalse(self.config.install_state_path.exists())
        backups = list(self.config.backup_root.glob("prior-*.plist"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)

    def test_install_migrates_prior_manual_start_plist_and_preserves_restore_backup(self):
        original_prior = self._compatible_prior_plist()
        original_prior["ThrottleInterval"] = 45
        original_prior_bytes = plistlib.dumps(original_prior, sort_keys=False)
        original_backup = self.config.backup_root / "original-prior.plist"
        original_backup.parent.mkdir(parents=True, exist_ok=True)
        original_backup.write_bytes(original_prior_bytes)

        legacy = self.manager._legacy_manual_start_plist()
        legacy_bytes = plistlib.dumps(legacy, fmt=plistlib.FMT_XML, sort_keys=False)
        self.config.plist_path.write_bytes(legacy_bytes)
        self.config.plist_path.chmod(0o600)
        self.manager._atomic_write_json(
            self.config.install_state_path,
            {
                "version": 1,
                "label": self.config.label,
                "installed_sha256": self.manager._sha256_bytes(legacy_bytes),
                "prior_present": True,
                "backup_path": str(original_backup),
                "installed_at": "2026-09-27T00:00:00+00:00",
            },
        )

        result = self.manager.install()

        self.assertTrue(result["changed"])
        self.assertTrue(result["migrated"])
        self.assertEqual(result["backup"], str(original_backup))
        installed = plistlib.loads(self.config.plist_path.read_bytes())
        self.assertEqual(installed, self.manager._expected_plist())
        state = json.loads(self.config.install_state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["backup_path"], str(original_backup))
        self.assertTrue(state["prior_present"])
        self.assertEqual(state["installed_at"], "2026-09-27T00:00:00+00:00")
        self.assertIn("updated_at", state)

    def test_start_verifies_exact_model_and_embedding_then_stop_is_owned(self):
        install = self.manager.install()
        self.assertTrue(install["changed"])
        with fake_adapter(self.config.model_id, self.config.model_revision) as port:
            object.__setattr__(self.config, "port", port)
            # install used the original port in its plist, so rewrite through a fresh
            # manager/config before exercising start on the fake ephemeral endpoint.
            self.config.plist_path.unlink()
            self.config.install_state_path.unlink()
            self.runner.loaded = False
            self.runner.listener = False
            self.manager.install()
            started = self.manager.start()
            self.assertTrue(started["changed"])
            self.assertEqual(started["readiness"]["model"], self.config.model_id)
            self.assertEqual(started["readiness"]["dimensions"], 384)
            actions = [cmd[1] for cmd in self.runner.commands if len(cmd) > 1 and cmd[0] == str(self.config.launchctl)]
            self.assertIn("bootstrap", actions)
            self.assertNotIn("kickstart", actions)
            stopped = self.manager.stop()
            self.assertTrue(stopped["changed"])
            self.assertFalse(self.runner.loaded)

    def test_stop_waits_for_async_bootout_until_service_and_listener_are_absent(self):
        self.manager.install()
        self.runner.loaded = True
        self.runner.listener = True
        self.runner.bootout_clears_loaded = False
        self.runner.bootout_clears_listener = False
        sleeps = 0

        def finish_bootout(_seconds):
            nonlocal sleeps
            sleeps += 1
            self.runner.loaded = False
            self.runner.listener = False

        self.manager.sleep = finish_bootout
        stopped = self.manager.stop()

        self.assertTrue(stopped["changed"])
        self.assertGreaterEqual(sleeps, 1)
        bootouts = [cmd for cmd in self.runner.commands if len(cmd) > 1 and cmd[1] == "bootout"]
        self.assertEqual(bootouts, [(str(self.config.launchctl), "bootout", self.config.service)])

    def test_stop_waits_for_owned_listener_to_drain_after_launchd_is_absent(self):
        self.manager.install()
        self.runner.loaded = True
        self.runner.listener = True
        self.runner.bootout_clears_listener = False
        sleeps = 0

        def drain_listener(_seconds):
            nonlocal sleeps
            sleeps += 1
            self.runner.listener = False

        self.manager.sleep = drain_listener
        stopped = self.manager.stop()

        self.assertTrue(stopped["changed"])
        self.assertGreaterEqual(sleeps, 1)

    def test_stop_fails_closed_if_listener_owner_changes_during_bootout(self):
        self.manager.install()
        self.runner.loaded = True
        self.runner.listener = True
        self.runner.bootout_clears_listener = False
        self.runner.bootout_listener_pid_override = 9999

        with self.assertRaisesRegex(MANAGER.SafetyError, "listener is not owned"):
            self.manager.stop()

        bootouts = [cmd for cmd in self.runner.commands if len(cmd) > 1 and cmd[1] == "bootout"]
        self.assertEqual(len(bootouts), 1)

    def test_stop_times_out_if_exact_owned_job_never_finishes_bootout(self):
        self.manager.install()
        self.runner.loaded = True
        self.runner.listener = True
        self.runner.bootout_clears_loaded = False
        self.runner.bootout_clears_listener = False
        clock = [0.0]

        def monotonic():
            return clock[0]

        def sleep(seconds):
            clock[0] += seconds

        self.manager.monotonic = monotonic
        self.manager.sleep = sleep
        with self.assertRaisesRegex(MANAGER.SafetyError, "timed out waiting"):
            self.manager.stop()

        bootouts = [cmd for cmd in self.runner.commands if len(cmd) > 1 and cmd[1] == "bootout"]
        self.assertEqual(len(bootouts), 1)

    def test_start_waits_for_preloaded_runatload_process_without_kickstart(self):
        with fake_adapter(self.config.model_id, self.config.model_revision) as port:
            object.__setattr__(self.config, "port", port)
            self.manager.install()
            self.runner.loaded = True
            self.runner.listener = False
            original_sleep = self.manager.sleep
            wake_count = 0

            def reveal_listener(_seconds):
                nonlocal wake_count
                wake_count += 1
                self.runner.listener = True

            self.manager.sleep = reveal_listener
            try:
                started = self.manager.start()
            finally:
                self.manager.sleep = original_sleep

        self.assertFalse(started["changed"])
        self.assertGreaterEqual(wake_count, 1)
        actions = [cmd[1] for cmd in self.runner.commands if len(cmd) > 1 and cmd[0] == str(self.config.launchctl)]
        self.assertNotIn("bootstrap", actions)
        self.assertNotIn("kickstart", actions)
        self.assertNotIn("bootout", actions)

    def test_start_timeout_after_new_bootstrap_rolls_back_only_new_job(self):
        self.manager.install()
        self.runner.auto_listener_on_bootstrap = False
        with self.assertRaisesRegex(MANAGER.SafetyError, "did not become ready"):
            self.manager.start()
        self.assertFalse(self.runner.loaded)
        actions = [cmd[1] for cmd in self.runner.commands if len(cmd) > 1 and cmd[0] == str(self.config.launchctl)]
        self.assertEqual(actions.count("bootstrap"), 1)
        self.assertEqual(actions.count("bootout"), 1)
        self.assertNotIn("kickstart", actions)

    def test_start_timeout_for_preexisting_loaded_job_does_not_bootout(self):
        self.manager.install()
        self.runner.loaded = True
        self.runner.listener = False
        with self.assertRaisesRegex(MANAGER.SafetyError, "did not become ready"):
            self.manager.start()
        self.assertTrue(self.runner.loaded)
        actions = [cmd[1] for cmd in self.runner.commands if len(cmd) > 1 and cmd[0] == str(self.config.launchctl)]
        self.assertNotIn("bootstrap", actions)
        self.assertNotIn("kickstart", actions)
        self.assertNotIn("bootout", actions)

    def test_start_wrong_model_fails_closed_and_boots_out_only_new_job(self):
        with fake_adapter(self.config.model_id, self.config.model_revision, wrong_model=True) as port:
            object.__setattr__(self.config, "port", port)
            self.manager.install()
            with self.assertRaisesRegex(MANAGER.SafetyError, "model identity mismatch"):
                self.manager.start()
        self.assertFalse(self.runner.loaded)
        bootouts = [cmd for cmd in self.runner.commands if len(cmd) > 1 and cmd[1] == "bootout"]
        self.assertEqual(len(bootouts), 1)
        self.assertEqual(bootouts[0][-1], self.config.service)

    def test_preflight_rejects_unmanaged_listener(self):
        self.runner.listener = True
        with self.assertRaisesRegex(MANAGER.SafetyError, "unmanaged listener"):
            self.manager.preflight()

    def test_uninstall_restores_prior_compatible_plist(self):
        prior = self._compatible_prior_plist()
        prior_bytes = plistlib.dumps(prior, sort_keys=False)
        self.config.plist_path.write_bytes(prior_bytes)
        self.config.plist_path.chmod(0o600)
        installed = self.manager.install()
        self.assertIsNotNone(installed["backup"])
        result = self.manager.uninstall()
        self.assertTrue(result["restored_prior_plist"])
        self.assertEqual(self.config.plist_path.read_bytes(), prior_bytes)
        self.assertFalse(self.config.install_state_path.exists())


if __name__ == "__main__":
    unittest.main()
