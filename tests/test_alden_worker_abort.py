import importlib.util
from contextlib import contextmanager
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
WORKER = SCRIPTS / "auto-reply-worker.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_worker(name: str):
    spec = importlib.util.spec_from_file_location(name, WORKER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class AldenWorkerAbortTests(unittest.TestCase):
    def setUp(self):
        self.worker = load_worker(f"alden_worker_abort_{id(self)}")

    def _abort_root(self, base: Path) -> Path:
        root = base / "operator-state"
        root.mkdir(mode=0o700)
        root.chmod(0o700)
        return root

    def _queue_connection(self, event: dict, *, status: str, reply: str | None = None):
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE reply_jobs(
                event_id TEXT PRIMARY KEY,
                event_json TEXT NOT NULL,
                status TEXT NOT NULL,
                due_at REAL,
                decision TEXT,
                reason TEXT,
                category TEXT,
                reply TEXT,
                scheduled_delay_seconds REAL,
                error_class TEXT,
                updated_at REAL NOT NULL
            );
            CREATE TABLE reply_job_supersessions(event_id TEXT PRIMARY KEY);
            """
        )
        connection.execute(
            """
            INSERT INTO reply_jobs(
                event_id, event_json, status, reply, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("event-1", json.dumps(event, ensure_ascii=False), status, reply, time.time()),
        )
        connection.commit()
        return connection

    def test_latch_during_claimed_job_defers_row_and_preserves_watermark(self):
        module = self.worker
        event = {
            "message": "hello",
            "analysis_watermark_log_id": 77,
        }
        connection = self._queue_connection(event, status="processing")
        job = {
            "event_id": "event-1",
            "event_json": json.dumps(event, ensure_ascii=False),
            "reply": None,
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = self._abort_root(Path(temporary))
            controller = module.AbortController(root)
            token = controller.token()

            def cancel_inside_job(*_args):
                controller.abort("worker_test")
                module._raise_if_job_aborted()

            with (
                mock.patch.object(module, "_capture_job_abort_token", return_value=token),
                mock.patch.object(module, "_process_job_impl", side_effect=cancel_inside_job),
                mock.patch.object(module, "_append_semantic_job_transitions"),
            ):
                module.process_job(job, "pending", connection)

        row = connection.execute(
            "SELECT * FROM reply_jobs WHERE event_id = 'event-1'"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["error_class"], module.ALDEN_ABORT_DEFER_REASON)
        self.assertGreater(float(row["due_at"]), time.time())
        self.assertEqual(json.loads(row["event_json"])["analysis_watermark_log_id"], 77)
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM reply_jobs").fetchone()[0], 1
        )

    def test_generation_latch_cancels_waiter_and_discards_late_result(self):
        module = self.worker
        with tempfile.TemporaryDirectory() as temporary:
            root = self._abort_root(Path(temporary))
            controller = module.AbortController(root)
            token = controller.token()
            started = threading.Event()
            release = threading.Event()
            finished = threading.Event()

            def fake_generation(*_args, **_kwargs):
                started.set()
                release.wait(2.0)
                finished.set()
                return 0, b"late", b""

            def abort_when_started():
                self.assertTrue(started.wait(1.0))
                controller.abort("generation_test")

            aborter = threading.Thread(target=abort_when_started, daemon=True)
            aborter.start()
            try:
                with (
                    mock.patch.object(module, "_is_mlx_serve_text_model", return_value=False),
                    mock.patch.object(
                        module,
                        "_run_opencodex_generation_unleased",
                        side_effect=fake_generation,
                    ),
                    module._active_job_abort_token(token),
                ):
                    with self.assertRaises(module.AldenCancelled):
                        module._run_opencodex_generation(
                            "fake-model", "system", b"prompt", timeout=2.0
                        )
            finally:
                release.set()
                aborter.join(1.0)
            self.assertTrue(finished.wait(1.0))

    def test_stop_then_immediate_resume_does_not_revive_captured_epoch(self):
        module = self.worker
        with tempfile.TemporaryDirectory() as temporary:
            root = self._abort_root(Path(temporary))
            with mock.patch.object(module, "_operator_state_root", return_value=root):
                token = module._capture_job_abort_token()
            controller = module.AbortController(root)
            controller.abort("race_test")
            resumed = controller.resume_after_human_action()
            self.assertFalse(resumed.latched)
            self.assertGreaterEqual(resumed.epoch, 2)
            with self.assertRaises(module.AldenCancelled):
                with module._active_job_abort_token(token):
                    module._raise_if_job_aborted()
            with self.assertRaises(module.AldenCancelled):
                token.raise_if_cancelled()

    def test_malformed_abort_state_fails_closed_before_job_start(self):
        module = self.worker
        with tempfile.TemporaryDirectory() as temporary:
            root = self._abort_root(Path(temporary))
            state = root / "alden-abort.json"
            state.write_bytes(b"{malformed")
            state.chmod(0o600)
            with mock.patch.object(module, "_operator_state_root", return_value=root):
                with self.assertRaises(module.AldenCancelled):
                    module._capture_job_abort_token()

    def test_managed_send_relays_original_abort_epoch_and_root_to_both_children(self):
        module = self.worker
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = self._abort_root(base)
            token = module.AbortController(root).token()
            binary = base / "openkakao-cli"
            binary.write_text("fake", encoding="utf-8")
            queue = base / "state" / "queue.json"
            queue.parent.mkdir(mode=0o700)
            calls: list[tuple[list[str], dict[str, str]]] = []

            def fake_process(command, **kwargs):
                calls.append((list(command), dict(kwargs["env"])))
                if "--preflight" in command:
                    payload = {
                        "status": "preflight_ready",
                        "preflight_ready": True,
                        "will_send": False,
                        "network": False,
                    }
                    return 0, json.dumps(payload).encode("utf-8"), b""
                return 1, b"{}", b""

            event = {
                "message": "hello",
                "author_id": 42,
                "author_nickname": "friend",
                "log_id": 10,
            }
            with (
                mock.patch.object(module, "BIN", binary),
                mock.patch.object(module, "QUEUE", queue),
                mock.patch.object(module, "_WORKER_HEALTH", None),
                mock.patch.object(module, "_outbound_reaction_allows", return_value=True),
                mock.patch.object(module, "_outbound_question_allows", return_value=True),
                mock.patch.object(module, "_outbound_register_allows", return_value=True),
                mock.patch.object(module, "numeric_author_identity_status", return_value="allowed"),
                mock.patch.object(module, "send_readiness_fence", return_value=(True, "fence")),
                mock.patch.object(module, "conversation_advanced_past_event", return_value=False),
                mock.patch.object(module, "_pre_mutation_send_hold", return_value=None),
                mock.patch.object(module, "_run_bounded_process", side_effect=fake_process),
                mock.patch.dict(
                    os.environ,
                    {"OPENKAKAO_HOOK_DRY_RUN": "0", "OPENKAKAO_DB_MODE": ""},
                    clear=False,
                ),
                module._active_job_abort_token(token),
            ):
                self.assertFalse(
                    module.send_reply(
                        "reply",
                        event=event,
                        expected_target_chat_id=7,
                        expected_owner="worker",
                        expected_epoch=1,
                    )
                )

            self.assertEqual(len(calls), 2)
            self.assertIn("--preflight", calls[0][0])
            self.assertNotIn("--preflight", calls[1][0])
            for _, environment in calls:
                self.assertEqual(
                    environment["OPENKAKAO_ALDEN_ABORT_STATE_ROOT"], str(root)
                )
                self.assertEqual(
                    environment["OPENKAKAO_ALDEN_ABORT_EPOCH"],
                    str(token.captured_epoch),
                )
            self.assertEqual(
                calls[0][1]["OPENKAKAO_ALDEN_ABORT_EPOCH"],
                calls[1][1]["OPENKAKAO_ALDEN_ABORT_EPOCH"],
            )

    def test_stop_resume_old_token_refuses_send_without_recapture_or_child(self):
        module = self.worker
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = self._abort_root(base)
            controller = module.AbortController(root)
            token = controller.token()
            controller.abort("race_test")
            controller.resume_after_human_action()
            binary = base / "openkakao-cli"
            binary.write_text("fake", encoding="utf-8")
            queue = base / "state" / "queue.json"
            queue.parent.mkdir(mode=0o700)
            event = {
                "message": "hello",
                "author_id": 42,
                "author_nickname": "friend",
                "log_id": 10,
            }
            previous = getattr(module._ACTIVE_ABORT_TOKEN, "value", None)
            module._ACTIVE_ABORT_TOKEN.value = token
            try:
                with (
                    mock.patch.object(module, "BIN", binary),
                    mock.patch.object(module, "QUEUE", queue),
                    mock.patch.object(module, "_WORKER_HEALTH", None),
                    mock.patch.object(module, "_outbound_reaction_allows", return_value=True),
                    mock.patch.object(module, "_outbound_question_allows", return_value=True),
                    mock.patch.object(module, "_outbound_register_allows", return_value=True),
                    mock.patch.object(module, "numeric_author_identity_status", return_value="allowed"),
                    mock.patch.object(module, "send_readiness_fence", return_value=(True, "fence")),
                    mock.patch.object(module, "conversation_advanced_past_event", return_value=False),
                    mock.patch.object(module, "_capture_job_abort_token") as capture,
                    mock.patch.object(module, "_run_bounded_process") as process,
                    mock.patch.dict(
                        os.environ,
                        {"OPENKAKAO_HOOK_DRY_RUN": "0", "OPENKAKAO_DB_MODE": ""},
                        clear=False,
                    ),
                ):
                    with self.assertRaises(module.AldenCancelled):
                        module.send_reply(
                            "reply",
                            event=event,
                            expected_target_chat_id=7,
                            expected_owner="worker",
                            expected_epoch=1,
                        )
                capture.assert_not_called()
                process.assert_not_called()
            finally:
                module._ACTIVE_ABORT_TOKEN.value = previous

    def test_no_active_abort_token_preserves_local_send_legacy_environment(self):
        module = self.worker
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            binary = base / "openkakao-cli"
            binary.write_text("fake", encoding="utf-8")
            queue = base / "state" / "queue.json"
            queue.parent.mkdir(mode=0o700)
            environments: list[dict[str, str]] = []

            def fake_process(command, **kwargs):
                environments.append(dict(kwargs["env"]))
                if "--preflight" in command:
                    payload = {
                        "status": "preflight_ready",
                        "preflight_ready": True,
                        "will_send": False,
                        "network": False,
                    }
                    return 0, json.dumps(payload).encode("utf-8"), b""
                return 1, b"{}", b""

            event = {
                "message": "hello",
                "author_id": 42,
                "author_nickname": "friend",
                "log_id": 10,
            }
            with (
                mock.patch.object(module, "BIN", binary),
                mock.patch.object(module, "QUEUE", queue),
                mock.patch.object(module, "_WORKER_HEALTH", None),
                mock.patch.object(module, "_outbound_reaction_allows", return_value=True),
                mock.patch.object(module, "_outbound_question_allows", return_value=True),
                mock.patch.object(module, "_outbound_register_allows", return_value=True),
                mock.patch.object(module, "numeric_author_identity_status", return_value="allowed"),
                mock.patch.object(module, "send_readiness_fence", return_value=(True, "fence")),
                mock.patch.object(module, "conversation_advanced_past_event", return_value=False),
                mock.patch.object(module, "_pre_mutation_send_hold", return_value=None),
                mock.patch.object(module, "_run_bounded_process", side_effect=fake_process),
                mock.patch.dict(
                    os.environ,
                    {"OPENKAKAO_HOOK_DRY_RUN": "0", "OPENKAKAO_DB_MODE": ""},
                    clear=False,
                ),
            ):
                self.assertFalse(
                    module.send_reply(
                        "reply",
                        event=event,
                        expected_target_chat_id=7,
                        expected_owner="worker",
                        expected_epoch=1,
                    )
                )

            self.assertEqual(len(environments), 2)
            for environment in environments:
                self.assertNotIn("OPENKAKAO_ALDEN_ABORT_STATE_ROOT", environment)
                self.assertNotIn("OPENKAKAO_ALDEN_ABORT_EPOCH", environment)

    def test_invalid_active_abort_token_path_or_epoch_fails_closed(self):
        module = self.worker
        previous = getattr(module._ACTIVE_ABORT_TOKEN, "value", None)
        try:
            module._ACTIVE_ABORT_TOKEN.value = object()
            with self.assertRaises(module.AldenCancelled):
                module._local_send_abort_relay_environment()

            with tempfile.TemporaryDirectory() as temporary:
                root = self._abort_root(Path(temporary))
                token = module.AbortController(root).token()
                token.path = Path("relative") / "alden-abort.json"
                module._ACTIVE_ABORT_TOKEN.value = token
                with self.assertRaises(module.AldenCancelled):
                    module._local_send_abort_relay_environment()

                token = module.AbortController(root).token()
                token._epoch = 0.5
                module._ACTIVE_ABORT_TOKEN.value = token
                with self.assertRaises(module.AldenCancelled):
                    module._local_send_abort_relay_environment()
        finally:
            module._ACTIVE_ABORT_TOKEN.value = previous

    def test_operator_state_root_uses_legacy_default_when_only_legacy_enrolled(self):
        module = self.worker
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            legacy = home / "Library" / "Application Support" / "openkakao" / "bujamentor"
            legacy.mkdir(parents=True)
            (legacy / "enrollment.json").write_text("{}", encoding="utf-8")
            with (
                mock.patch.object(module.Path, "home", return_value=home),
                mock.patch.dict(os.environ, {}, clear=True),
            ):
                self.assertEqual(module._operator_state_root(), legacy)

    def test_native_pre_send_unavailable_returns_sending_row_to_processing(self):
        module = self.worker
        event = {
            "message": "hello",
            "author_id": 42,
            "author_nickname": "friend",
            "log_id": 10,
        }
        connection = self._queue_connection(event, status="processing", reply="reply")
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = self._abort_root(base)
            token = module.AbortController(root).token()
            binary = base / "openkakao-cli"
            binary.write_text("fake", encoding="utf-8")
            queue = base / "state" / "queue.json"
            queue.parent.mkdir(mode=0o700)
            calls: list[list[str]] = []

            def fake_process(command, **_kwargs):
                calls.append(list(command))
                if "--preflight" in command:
                    payload = {
                        "status": "preflight_ready",
                        "preflight_ready": True,
                        "will_send": False,
                        "network": False,
                    }
                    return 0, json.dumps(payload).encode("utf-8"), b""
                payload = {
                    "chat_name": module.CHAT,
                    "status": "pre_send_unavailable",
                    "mutation_started": False,
                    "confirmed": False,
                    "network": False,
                }
                return 0, json.dumps(payload).encode("utf-8"), b""

            with (
                mock.patch.object(module, "BIN", binary),
                mock.patch.object(module, "QUEUE", queue),
                mock.patch.object(module, "_WORKER_HEALTH", None),
                mock.patch.object(module, "_outbound_reaction_allows", return_value=True),
                mock.patch.object(module, "_outbound_question_allows", return_value=True),
                mock.patch.object(module, "_outbound_register_allows", return_value=True),
                mock.patch.object(module, "numeric_author_identity_status", return_value="allowed"),
                mock.patch.object(module, "send_readiness_fence", return_value=(True, "fence")),
                mock.patch.object(module, "conversation_advanced_past_event", return_value=False),
                mock.patch.object(module, "_pre_mutation_send_hold", return_value=None),
                mock.patch.object(module, "_journal_checkpoint"),
                mock.patch.object(module, "_append_semantic_job_transitions"),
                mock.patch.object(module, "_append_job_transition"),
                mock.patch.object(module, "_run_bounded_process", side_effect=fake_process),
                mock.patch.dict(
                    os.environ,
                    {"OPENKAKAO_HOOK_DRY_RUN": "0", "OPENKAKAO_DB_MODE": ""},
                    clear=False,
                ),
                module._active_job_abort_token(token),
            ):
                self.assertFalse(
                    module.send_reply(
                        "reply",
                        event=event,
                        event_id="event-1",
                        connection=connection,
                        expected_target_chat_id=7,
                        expected_owner="worker",
                        expected_epoch=1,
                    )
                )

            self.assertEqual(len(calls), 2)
            row = connection.execute(
                "SELECT status FROM reply_jobs WHERE event_id = 'event-1'"
            ).fetchone()
            self.assertEqual(row["status"], "processing")

    def test_abort_in_preflight_send_gap_never_launches_local_send(self):
        module = self.worker
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = self._abort_root(base)
            controller = module.AbortController(root)
            token = controller.token()
            binary = base / "openkakao-cli"
            binary.write_text("fake", encoding="utf-8")
            queue = base / "state" / "queue.json"
            queue.parent.mkdir(mode=0o700)
            calls: list[list[str]] = []

            def fake_process(command, **_kwargs):
                calls.append(list(command))
                self.assertIn("--preflight", command)
                controller.abort("preflight_gap")
                payload = {
                    "status": "preflight_ready",
                    "preflight_ready": True,
                    "will_send": False,
                    "network": False,
                }
                return 0, json.dumps(payload).encode("utf-8"), b""

            event = {
                "message": "hello",
                "author_id": 42,
                "author_nickname": "friend",
            }
            with (
                mock.patch.object(module, "BIN", binary),
                mock.patch.object(module, "QUEUE", queue),
                mock.patch.object(module, "_WORKER_HEALTH", None),
                mock.patch.object(module, "_outbound_reaction_allows", return_value=True),
                mock.patch.object(module, "_outbound_question_allows", return_value=True),
                mock.patch.object(module, "_outbound_register_allows", return_value=True),
                mock.patch.object(module, "numeric_author_identity_status", return_value="allowed"),
                mock.patch.object(module, "send_readiness_fence", return_value=(True, "fence")),
                mock.patch.object(module, "conversation_advanced_past_event", return_value=False),
                mock.patch.object(module, "_run_bounded_process", side_effect=fake_process),
                mock.patch.dict(os.environ, {"OPENKAKAO_HOOK_DRY_RUN": "0"}, clear=False),
                module._active_job_abort_token(token),
            ):
                with self.assertRaises(module.AldenCancelled):
                    module.send_reply(
                        "reply",
                        event=event,
                        expected_target_chat_id=7,
                        expected_owner="worker",
                        expected_epoch=1,
                    )
            self.assertEqual(len(calls), 1)
            self.assertIn("--preflight", calls[0])

    def test_abort_after_sending_phase_becomes_delivery_unknown_without_retry(self):
        module = self.worker
        event = {
            "message": "hello",
            "analysis_watermark_log_id": 91,
        }
        connection = self._queue_connection(event, status="sending", reply="reply")
        job = {
            "event_id": "event-1",
            "event_json": json.dumps(event, ensure_ascii=False),
            "reply": "reply",
        }
        with (
            mock.patch.object(module, "_append_semantic_job_transitions"),
            mock.patch.object(module, "update_context_decision"),
            mock.patch.object(module, "record_delivery_unknown"),
            mock.patch.object(module, "release_media_event", side_effect=lambda value: value),
            mock.patch.object(module, "settle_processing_transition") as retry,
        ):
            module._defer_alden_cancelled_job(job, "pending", connection)
        row = connection.execute(
            "SELECT * FROM reply_jobs WHERE event_id = 'event-1'"
        ).fetchone()
        self.assertEqual(row["status"], module.DELIVERY_UNKNOWN)
        self.assertIsNone(row["due_at"])
        self.assertEqual(row["error_class"], module.ALDEN_ABORT_DEFER_REASON)
        self.assertEqual(row["reply"], "reply")
        self.assertEqual(json.loads(row["event_json"])["analysis_watermark_log_id"], 91)
        retry.assert_not_called()

    def test_abort_terminates_and_reaps_owned_bounded_child(self):
        module = self.worker
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = self._abort_root(base)
            controller = module.AbortController(root)
            token = controller.token()
            pid_path = base / "child.pid"
            child_code = (
                "import os, pathlib, sys, time; "
                "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); "
                "time.sleep(30)"
            )

            def abort_after_child_starts():
                deadline = time.monotonic() + 2.0
                while not pid_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                controller.abort("owned_child_test")

            aborter = threading.Thread(target=abort_after_child_starts, daemon=True)
            aborter.start()
            with module._active_job_abort_token(token):
                with self.assertRaises(module.AldenCancelled):
                    module._run_bounded_process(
                        [sys.executable, "-c", child_code, str(pid_path)],
                        cwd=base,
                        env=dict(os.environ),
                        timeout=5.0,
                        stdout_cap=1024,
                        stderr_cap=1024,
                        isolate_group=True,
                    )
            aborter.join(1.0)
            self.assertTrue(pid_path.exists())
            pid = int(pid_path.read_text(encoding="utf-8"))
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
            with self.assertRaises(ChildProcessError):
                os.waitpid(pid, os.WNOHANG)

    def test_cancelled_call_lease_waits_for_transport_and_then_releases(self):
        module = self.worker
        with tempfile.TemporaryDirectory() as temporary:
            queue = Path(temporary) / "model-circuit.sqlite3"
            with mock.patch.dict(os.environ, {"OPENKAKAO_MODEL_CIRCUIT_DB": str(queue)}):
                module.QUEUE = queue.with_name("room-queue.sqlite3")
                slot = module._acquire_model_call_slot(model="fake")
                self.assertTrue(slot["allowed"])
                completed = threading.Event()
                error = module.AldenCancelled("cancelled")
                error.transport_finished = completed
                cleaner = module._cancel_model_call_after_abort(slot["lease_token"], "fake", error)
                self.assertIsNotNone(cleaner)
                self.assertFalse(module._acquire_model_call_slot(model="fake")["allowed"])
                completed.set()
                cleaner.join(1.0)
                self.assertFalse(cleaner.is_alive())
                replacement = module._acquire_model_call_slot(model="fake")
                self.assertTrue(replacement["allowed"])

    def test_late_cancelled_lease_cleanup_cannot_remove_new_owner(self):
        module = self.worker
        with tempfile.TemporaryDirectory() as temporary:
            queue = Path(temporary) / "model-circuit.sqlite3"
            with mock.patch.dict(os.environ, {"OPENKAKAO_MODEL_CIRCUIT_DB": str(queue)}):
                module.QUEUE = queue.with_name("room-queue.sqlite3")
                first = module._acquire_model_call_slot(model="fake")
                self.assertTrue(module._release_cancelled_model_call(first["lease_token"], "fake"))
                second = module._acquire_model_call_slot(model="fake")
                self.assertFalse(module._release_cancelled_model_call(first["lease_token"], "fake"))
                connection = module._model_circuit_connection()
                row = connection.execute("SELECT lease_token FROM model_circuit_breaker").fetchone()
                connection.close()
                self.assertEqual(row[0], second["lease_token"])

    def test_cancellation_preserves_existing_failure_count_without_increment(self):
        module = self.worker
        with tempfile.TemporaryDirectory() as temporary:
            queue = Path(temporary) / "model-circuit.sqlite3"
            with mock.patch.dict(os.environ, {"OPENKAKAO_MODEL_CIRCUIT_DB": str(queue)}):
                module.QUEUE = queue.with_name("room-queue.sqlite3")
                slot = module._acquire_model_call_slot(model="fake")
                connection = module._model_circuit_connection()
                connection.execute("UPDATE model_circuit_breaker SET failure_class='rate_limit', consecutive_failures=2")
                connection.commit()
                connection.close()
                self.assertTrue(module._release_cancelled_model_call(slot["lease_token"], "fake"))
                connection = module._model_circuit_connection()
                row = connection.execute("SELECT state, failure_class, consecutive_failures, lease_token FROM model_circuit_breaker").fetchone()
                connection.close()
                self.assertEqual(tuple(row), ("open", "rate_limit", 2, None))
                self.assertTrue(module._acquire_model_call_slot(model="fake")["allowed"])

    def test_operator_cancellation_propagates_without_another_fallback(self):
        module = self.worker
        slot = {"lease_token": "a" * 32, "retry_at": time.time() + 180}
        with (
            mock.patch.object(module, "_reply_fallback_candidates", return_value=["one", "two"]),
            mock.patch.object(module, "_open_fallback_lease", return_value=slot) as opened,
            mock.patch.object(module, "_cancel_model_call_after_abort") as cancel,
            mock.patch.object(module, "_close_model_lease") as failure,
        ):
            operation = mock.Mock(side_effect=module.AldenCancelled("operator stop"))
            with self.assertRaises(module.AldenCancelled):
                module._model_fallback_chain("primary", operation)
        self.assertEqual(operation.call_count, 1)
        self.assertEqual(opened.call_count, 1)
        cancel.assert_called_once()
        failure.assert_not_called()

    def test_turn_guard_cancellation_is_not_a_context_failure(self):
        module = self.worker
        with self.assertRaises(module.AldenCancelled):
            module.generate_reply("synthetic", [], [], [], [],
                                  turn_guard=mock.Mock(side_effect=module.AldenCancelled("stop")))

    def test_primary_generation_cancellation_releases_its_owned_call_lease(self):
        module = self.worker
        model = "ddalcu/Qwen3.8-27B-MLX-Serve-4bit"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            queue = root / "model-circuit.sqlite3"
            with (
                mock.patch.dict(os.environ, {"OPENKAKAO_MODEL_CIRCUIT_DB": str(queue)}),
                mock.patch.object(module, "QUEUE", queue.with_name("room-queue.sqlite3")),
                mock.patch.object(module, "_operator_state_root", return_value=root),
                mock.patch.object(module, "_generation_reply_model", return_value=model),
                mock.patch.object(module, "REPLY_RUNNER_KIND", "opencodex"),
                mock.patch.object(module, "_publish_model_status"),
                mock.patch.object(module, "_active_journal_checkpoint"),
                mock.patch.object(module, "_run_generation_candidate", side_effect=module.AldenCancelled("stop")),
            ):
                slot = module._acquire_model_call_slot(model=model)
                self.assertTrue(slot["allowed"])
                with self.assertRaises(module.AldenCancelled):
                    module.generate_reply("synthetic", [], [], [], [],
                                          _capacity_probe=True, _preacquired_model_slot=slot)
                self.assertTrue(module._acquire_model_call_slot(model=model)["allowed"])

    def test_local_transport_lease_outlives_cancelled_waiter(self):
        module = self.worker
        with tempfile.TemporaryDirectory() as temporary:
            root = self._abort_root(Path(temporary))
            controller = module.AbortController(root)
            token = controller.token()
            started, release, finished = threading.Event(), threading.Event(), threading.Event()
            state = {"leased": False}

            @contextmanager
            def lease(_root):
                state["leased"] = True
                try:
                    yield
                finally:
                    state["leased"] = False
                    finished.set()

            def operation(*_args, **_kwargs):
                started.set()
                release.wait(2.0)
                return 0, b"late result", b""

            def stop():
                started.wait(1.0)
                controller.abort("test")

            aborter = threading.Thread(target=stop, daemon=True)
            aborter.start()
            try:
                with (
                    mock.patch.object(module, "_operator_state_root", return_value=root),
                    mock.patch.object(module, "_is_mlx_serve_text_model", return_value=True),
                    mock.patch.object(module.auto_reply_ondevice, "mlx_model_request_lease", side_effect=lease),
                    mock.patch.object(module, "_run_opencodex_generation_unleased", side_effect=operation),
                    module._active_job_abort_token(token),
                ):
                    with self.assertRaises(module.AldenCancelled) as raised:
                        module._run_opencodex_generation("fake", "system", b"synthetic")
                    self.assertTrue(state["leased"])
                    self.assertFalse(raised.exception.transport_finished.is_set())
            finally:
                release.set()
                aborter.join(1.0)
                self.assertTrue(finished.wait(1.0))
            self.assertFalse(state["leased"])


if __name__ == "__main__":
    unittest.main()
