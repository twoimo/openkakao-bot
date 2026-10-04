import contextlib
import importlib.util
import io
import os
import re
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def liveness_window_seconds() -> float:
    """Read the real db-heartbeat freshness window from the shipped fences.

    The supervisor fences a room whose db heartbeat is older than its own
    ``HEARTBEAT_MAX_AGE_SECONDS`` and the reply worker's pre-send fence uses
    ``SEND_FENCE_MAX_AGE_SECONDS``. Both must agree, and the watcher must stamp
    inside that window, so the test asserts against the shipped numbers instead
    of a copy that could silently drift.
    """

    windows = {}
    for name, pattern in (
        ("auto-reply-supervisor.py", r"^HEARTBEAT_MAX_AGE_SECONDS = ([\d.]+)$"),
        ("auto-reply-worker.py", r"^SEND_FENCE_MAX_AGE_SECONDS = ([\d.]+)$"),
    ):
        source = (SCRIPTS / name).read_text(encoding="utf-8")
        match = re.search(pattern, source, re.MULTILINE)
        if match is None:
            raise AssertionError(f"liveness window not found in {name}")
        windows[name] = float(match.group(1))
    if len(set(windows.values())) != 1:
        raise AssertionError(f"liveness windows disagree: {windows}")
    return next(iter(windows.values()))


def load_db_watch(name: str):
    spec = importlib.util.spec_from_file_location(
        name,
        SCRIPTS / "auto-reply-db-watch.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class AutoReplyDbWatchRetryTests(unittest.TestCase):
    @staticmethod
    def _clean_state(module):
        return {
            "schema_version": module.state_schema_version(),
            "target_chat_id": 42,
            "target_chat_name": module.CHAT,
            "cursor_floor": 100,
            "last_observed_log_id": 123,
            "acked_watermark": 123,
            "pending_log_ids": [],
            "pending_gaps": [],
            "observed_log_ids": [123],
            "acked_log_ids": [123],
            "source_epoch": 7,
            "capability_state": "ready",
            "delivery_enabled": True,
            "fence_reason": "",
            "owner_id": "owner",
            "heartbeat_at": 1.0,
            "fence": "ready",
            "candidate_phase": "idle",
            "in_flight_candidate": None,
            "recent_message_tail": [],
        }

    @staticmethod
    def _envelope(module):
        return {
            "schema_version": 3,
            "chat": {
                "chat_id": 42,
                "chat_name": module.CHAT,
                "last_log_id": 124,
            },
            "messages": [
                {
                    "log_id": 124,
                    "chat_id": 42,
                    "author_id": 700,
                    "is_self": False,
                    "sender_name": "member",
                    "message": "question",
                    "attachment": "",
                    "message_type": 1,
                    "sent_at": 1000,
                    "reply_authorized": True,
                }
            ],
            "completeness": {
                "status": "complete",
                "after_log_id": 123,
                "first_log_id": 124,
                "last_log_id": 124,
                "row_count": 1,
                "returned_count": 1,
                "available_max_log_id": 124,
                "chat_last_log_id": 124,
                "id_domain": "global_sparse",
                "has_gap": False,
                "has_more": False,
                "proof": "sqlite_snapshot_rowset",
            },
        }

    def test_emit_sqlite_busy_becomes_retryable_fence_with_cause(self):
        module = load_db_watch("auto_reply_db_watch_emit_busy_test")
        clean = self._clean_state(module)
        enrollment = {
            "chat_id": 42,
            "chat_name": module.CHAT,
            "identity": {
                "kind": "local_name",
                "local_name": module.CHAT,
                "ax_name": module.CHAT,
            },
            "reply_author_bindings": [
                {"nickname": "member", "author_id": 700}
            ],
        }
        environment = {
            "OPENKAKAO_AUTO_REPLY_CLI": "1",
            "OPENKAKAO_DB_MODE": "database_authoritative",
            "OPENKAKAO_AUTO_REPLY_ENABLED": "1",
            "OPENKAKAO_SUPERVISOR_OWNER": "owner",
            "OPENKAKAO_DB_SOURCE_EPOCH": "7",
        }
        output = io.StringIO()
        with (
            mock.patch.dict(os.environ, environment, clear=False),
            mock.patch.object(module, "_state", side_effect=lambda value: dict(value)),
            mock.patch.object(module, "_reconcile_ingress_journal"),
            mock.patch.object(module, "_owner_epoch_current", return_value=True),
            mock.patch.object(module, "cleanup_orphan_media"),
            mock.patch.object(module, "_start_poll_stream"),
            mock.patch.object(
                module, "_read_poll_envelope", return_value=self._envelope(module)
            ),
            mock.patch.object(module, "_cli_enrollment_target", return_value=enrollment),
            mock.patch.object(
                module, "_generation_lock", side_effect=lambda: contextlib.nullcontext()
            ),
            mock.patch.object(module, "save_state", return_value=True),
            mock.patch.object(module, "_journal_candidate"),
            mock.patch.object(
                module,
                "emit",
                side_effect=sqlite3.OperationalError("database is locked"),
            ),
            contextlib.redirect_stdout(output),
        ):
            fenced, emitted = module.poll_once(clean, 1.0)

        self.assertEqual(emitted, 0)
        self.assertEqual(fenced["capability_state"], "fenced")
        self.assertFalse(fenced["delivery_enabled"])
        self.assertEqual(fenced["fence_reason"], "database_timeout")
        self.assertEqual(
            fenced["poll_retry_kind"], module.TRANSIENT_POLL_RETRY_KIND
        )
        self.assertEqual(fenced["pending_log_ids"], [])
        self.assertIsNone(fenced["in_flight_candidate"])
        self.assertEqual(fenced["candidate_phase"], "idle")
        self.assertEqual(fenced["last_observed_log_id"], 123)
        self.assertEqual(fenced["observed_log_ids"], [123])
        self.assertNotEqual(fenced["fence_reason"], "reconcile_required")
        self.assertIn(
            "database_timeout:SqliteBusyTransient:"
            "emit_sqlite_busy:OperationalError:database is locked",
            output.getvalue(),
        )

        recovered = dict(clean)
        with (
            mock.patch.object(
                module, "poll_once", side_effect=[(fenced, 0), (recovered, 1)]
            ) as poll,
            mock.patch.object(module, "_save_polled_state", return_value=True),
            mock.patch.object(module, "_owner_epoch_current", return_value=True),
            mock.patch.object(module, "_wait_clean_poll_retry") as wait_retry,
        ):
            state, total = module._poll_with_bounded_clean_retry(clean, 1.0)
        self.assertEqual(poll.call_count, 2)
        self.assertEqual(total, 1)
        self.assertTrue(state["delivery_enabled"])
        wait_retry.assert_called_once_with(
            mock.ANY, module.TRANSIENT_POLL_RETRY_DELAYS_SECONDS[0]
        )

    def test_ambiguous_or_noncontention_hook_failure_stays_reconcile_required(self):
        module = load_db_watch("auto_reply_db_watch_emit_ambiguous_test")
        message = {"chat_id": 42, "log_id": 124}
        with mock.patch.object(module, "emit", return_value=None):
            with self.assertRaises(module.DbFence) as caught:
                module._emit_with_ack_fence(message, None)
        self.assertNotIsInstance(caught.exception, module.SqliteBusyTransient)
        self.assertEqual(module._fixed_fence_reason(caught.exception), "reconcile_required")

        with mock.patch.object(module, "emit", side_effect=RuntimeError("hook failed")):
            with self.assertRaises(module.DbFence) as caught:
                module._emit_with_ack_fence(message, None)
        self.assertNotIsInstance(caught.exception, module.SqliteBusyTransient)
        self.assertEqual(module._fixed_fence_reason(caught.exception), "reconcile_required")

    def test_exact_context_sync_replica_exhaustion_marker_retries_fenced_without_state_reset(self):
        module = load_db_watch("auto_reply_db_watch_replica_exhaustion_test")
        with mock.patch.object(
            module,
            "_run_bounded_cli",
            return_value=(
                1,
                b"",
                b"Error: context_sync_snapshot_retry_exhausted\n",
            ),
        ):
            with self.assertRaises(module.ContextSyncTransient) as caught:
                module.run_json(["context-sync-local", "42"])

        self.assertEqual(
            str(caught.exception),
            "context_sync_snapshot_retry_exhausted",
        )
        state = self._clean_state(module)
        state.update(
            last_observed_log_id=124,
            pending_log_ids=[124],
            observed_log_ids=[123, 124],
        )
        fenced, retry_delay = module._context_sync_transient_state(
            state,
            target_chat_id=42,
            consecutive_failures=1,
            now=100.0,
        )
        self.assertEqual(fenced["acked_watermark"], 123)
        self.assertEqual(fenced["acked_log_ids"], [123])
        self.assertEqual(fenced["pending_log_ids"], [124])
        self.assertEqual(fenced["pending_gaps"], [])
        self.assertEqual(fenced["observed_log_ids"], [123, 124])
        self.assertEqual(fenced["fence_reason"], "context_sync_transient")
        self.assertFalse(fenced["delivery_enabled"])
        self.assertEqual(
            retry_delay,
            module.CONTEXT_SYNC_TRANSIENT_RETRY_DELAYS_SECONDS[0],
        )
        self.assertEqual(fenced["context_sync_retry_at"], 100.0 + retry_delay)

    def test_context_sync_replica_exhaustion_marker_match_is_exact(self):
        module = load_db_watch("auto_reply_db_watch_replica_marker_exact_test")
        terminal_cases = [
            (
                ["context-sync-local", "42"],
                b"Error: context_sync_snapshot_retry_exhausted: permission denied\n",
            ),
            (
                ["context-sync-local", "42"],
                b"warning\nError: context_sync_snapshot_retry_exhausted\n",
            ),
            (
                ["local-read", "42"],
                b"Error: context_sync_snapshot_retry_exhausted\n",
            ),
        ]
        for index, (args, stderr) in enumerate(terminal_cases):
            with self.subTest(index=index):
                with mock.patch.object(
                    module,
                    "_run_bounded_cli",
                    return_value=(1, b"", stderr),
                ):
                    with self.assertRaises(module.DbFence) as caught:
                        module.run_json(args)
                self.assertNotIsInstance(caught.exception, module.ContextSyncTransient)

        with mock.patch.object(
            module,
            "_run_bounded_cli",
            return_value=(
                0,
                b"{not-json",
                b"Error: context_sync_snapshot_retry_exhausted\n",
            ),
        ):
            with self.assertRaises(module.DbFence) as caught:
                module.run_json(["context-sync-local", "42"])
        self.assertNotIsInstance(caught.exception, module.ContextSyncTransient)
        self.assertEqual(str(caught.exception), "malformed database response")

    def test_periodic_context_sync_preserves_unconsumed_result(self):
        module = load_db_watch("auto_reply_db_watch_sync_result_test")
        worker = module._PeriodicContextSync()
        result = {"authoritative": True, "checkpoint_log_id": 123}

        with mock.patch.object(
            module,
            "sync_context_index",
            return_value=result,
        ) as sync_context:
            worker._run(42, True, None, None)

        self.assertTrue(worker.in_flight())
        with mock.patch.object(module.threading, "Thread") as thread:
            self.assertFalse(worker.start(42, initial=False))
        thread.assert_not_called()
        self.assertEqual(worker.take_result(), ("ok", result))
        self.assertFalse(worker.in_flight())
        sync_context.assert_called_once_with(
            42,
            initial=True,
            on_wait=None,
            on_tick=None,
        )

    def test_main_keeps_delivery_fenced_until_transient_sync_recovers(self):
        module = load_db_watch("auto_reply_db_watch_main_recovery_test")
        module.SELF = "self"
        clean = self._clean_state(module)
        authoritative_sync = {
            "schema_version": 1,
            "action": "context_sync_local",
            "chat_id": 42,
            "chat": module.CHAT,
            "checkpoint_log_id": 123,
            "pages": 1,
            "authoritative": True,
            "deferred": None,
            "totals": {key: 0 for key in module.CONTEXT_SYNC_TOTAL_KEYS},
            "network": False,
        }
        sync_results = iter(
            [
                None,
                (
                    "transient",
                    module.ContextSyncTransient("context_sync_snapshot_gap"),
                ),
                None,
                ("ok", authoritative_sync),
            ]
        )
        starts = []

        class FakePeriodicSync:
            def take_proof(self):
                return None

            def take_result(self):
                return next(sync_results)

            def in_flight(self):
                return False

            def start(self, chat_id, *, initial, on_wait, on_tick):
                starts.append((chat_id, initial, on_wait, on_tick))
                return True

            def stop(self):
                return None

        poll_calls = []
        promoted_states = []

        def poll(state, interval, **kwargs):
            snapshot = dict(state)
            poll_calls.append((snapshot, interval, dict(kwargs)))
            if len(poll_calls) < 4:
                return snapshot, 0
            promoted = {
                **snapshot,
                "capability_state": "ready",
                "delivery_enabled": True,
                "fence_reason": "",
                "fence": "ready",
            }
            promoted_states.append(promoted)
            return promoted, 0

        saved_states = []

        def save(state, **_kwargs):
            saved_states.append(dict(state))
            return True

        def sleep(_seconds):
            if len(poll_calls) >= 4:
                raise StopIteration

        with (
            mock.patch.dict(
                os.environ,
                {module.TARGET_CHAT_ID_ENV: "42"},
                clear=False,
            ),
            mock.patch.object(sys, "argv", ["auto-reply-db-watch.py"]),
            mock.patch.object(module.signal, "signal"),
            mock.patch.object(module, "load_state", return_value=clean),
            mock.patch.object(module, "_state", side_effect=lambda value: dict(value)),
            mock.patch.object(module, "save_state", side_effect=save),
            mock.patch.object(
                module, "_poll_with_bounded_clean_retry", side_effect=poll
            ),
            mock.patch.object(module, "_PERIODIC_CONTEXT_SYNC", FakePeriodicSync()),
            mock.patch.object(
                module.time,
                "monotonic",
                side_effect=[0.0, 0.0, 1.0, 1.0, 6.0, 7.0, 7.0],
            ),
            mock.patch.object(module.time, "sleep", side_effect=sleep),
            mock.patch.object(module, "_stop_poll_stream"),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(StopIteration),
        ):
            module.main()

        self.assertEqual([initial for _, initial, _, _ in starts], [True, False])
        self.assertEqual(
            [kwargs for _, _, kwargs in poll_calls],
            [
                {"delivery_ready": False, "not_ready_reason": ""},
                {
                    "delivery_ready": False,
                    "not_ready_reason": "context_sync_transient",
                },
                {
                    "delivery_ready": False,
                    "not_ready_reason": "context_sync_transient",
                },
                {},
            ],
        )
        self.assertTrue(all(not state["delivery_enabled"] for state in saved_states))
        self.assertFalse(poll_calls[-1][0]["delivery_enabled"])
        self.assertTrue(promoted_states[-1]["delivery_enabled"])

    def test_database_timeout_fence_keeps_main_loop_running(self):
        module = load_db_watch("auto_reply_db_watch_main_busy_test")
        module.SELF = "self"
        clean = self._clean_state(module)
        calls = []

        def poll(state, interval, **kwargs):
            calls.append((dict(state), interval, dict(kwargs)))
            if len(calls) == 1:
                return {
                    **state,
                    "capability_state": "fenced",
                    "delivery_enabled": False,
                    "fence_reason": "database_timeout",
                    "fence": "db_unavailable",
                    "poll_retry_kind": module.TRANSIENT_POLL_RETRY_KIND,
                }, 0
            raise StopIteration

        with (
            mock.patch.dict(
                os.environ,
                {module.TARGET_CHAT_ID_ENV: "42"},
                clear=False,
            ),
            mock.patch.object(sys, "argv", ["auto-reply-db-watch.py"]),
            mock.patch.object(module.signal, "signal"),
            mock.patch.object(module, "load_state", return_value=clean),
            mock.patch.object(module, "_state", side_effect=lambda value: dict(value)),
            mock.patch.object(module, "save_state", return_value=True),
            mock.patch.object(
                module, "_poll_with_bounded_clean_retry", side_effect=poll
            ),
            mock.patch.object(
                module._PERIODIC_CONTEXT_SYNC,
                "in_flight",
                side_effect=[False, True],
            ),
            mock.patch.object(module._PERIODIC_CONTEXT_SYNC, "start", return_value=True) as start_sync,
            mock.patch.object(module._PERIODIC_CONTEXT_SYNC, "stop"),
            mock.patch.object(module.time, "sleep"),
            mock.patch.object(module, "_stop_poll_stream"),
            self.assertRaises(StopIteration),
        ):
            module.main()
        self.assertEqual(len(calls), 2)
        self.assertFalse(calls[1][0]["delivery_enabled"])
        self.assertEqual(calls[1][0]["fence_reason"], "database_timeout")
        self.assertEqual(
            calls[0][2],
            {"delivery_ready": False, "not_ready_reason": ""},
        )
        self.assertEqual(
            calls[1][2],
            {"delivery_ready": False, "not_ready_reason": ""},
        )
        self.assertTrue(start_sync.call_args.kwargs["initial"])

    def test_main_keeps_polling_when_initial_context_sync_is_deferred(self):
        module = load_db_watch("auto_reply_db_watch_main_deferred_test")
        module.SELF = "self"
        deferred_sync = {
            "schema_version": 1,
            "action": "context_sync_local",
            "chat_id": 42,
            "chat": module.CHAT,
            "checkpoint_log_id": 123,
            "pages": 1,
            "authoritative": False,
            "deferred": {
                "reason": "fresh_unmatched_self",
                "log_id": 124,
                "retry_after_seconds": 1,
            },
            "totals": {key: 0 for key in module.CONTEXT_SYNC_TOTAL_KEYS},
            "network": False,
        }
        calls = []

        def poll(state, interval, **kwargs):
            calls.append((dict(state), interval, dict(kwargs)))
            if len(calls) == 1:
                return dict(state), 0
            raise StopIteration

        with (
            mock.patch.dict(
                os.environ,
                {module.TARGET_CHAT_ID_ENV: "42"},
                clear=False,
            ),
            mock.patch.object(sys, "argv", ["auto-reply-db-watch.py"]),
            mock.patch.object(module.signal, "signal"),
            mock.patch.object(module, "load_state", return_value={}),
            mock.patch.object(module, "_state", side_effect=lambda value: dict(value)),
            mock.patch.object(module, "save_state", return_value=True),
            mock.patch.object(
                module, "_poll_with_bounded_clean_retry", side_effect=poll
            ),
            mock.patch.object(module._PERIODIC_CONTEXT_SYNC, "in_flight", side_effect=[False, False]),
            mock.patch.object(module._PERIODIC_CONTEXT_SYNC, "start", return_value=True) as start_sync,
            mock.patch.object(
                module._PERIODIC_CONTEXT_SYNC,
                "take_result",
                side_effect=[None, ("ok", deferred_sync)],
            ),
            mock.patch.object(module._PERIODIC_CONTEXT_SYNC, "take_proof", return_value=None),
            mock.patch.object(module._PERIODIC_CONTEXT_SYNC, "stop"),
            mock.patch.object(module.time, "monotonic", side_effect=[10.0, 10.0, 10.1, 10.2]),
            mock.patch.object(module.time, "sleep"),
            mock.patch.object(module, "_stop_poll_stream"),
            self.assertRaises(StopIteration),
        ):
            module.main()
        self.assertEqual(len(calls), 2)
        self.assertFalse(calls[0][0]["delivery_enabled"])
        self.assertEqual(
            calls[0][2],
            {"delivery_ready": False, "not_ready_reason": ""},
        )
        self.assertFalse(calls[1][0]["delivery_enabled"])
        self.assertEqual(calls[1][0]["fence_reason"], "context_sync_deferred")
        self.assertEqual(
            calls[1][2],
            {"delivery_ready": False, "not_ready_reason": "context_sync_deferred"},
        )
        self.assertTrue(start_sync.call_args.kwargs["initial"])

    def test_capture_only_poll_queues_candidate_but_keeps_delivery_fenced(self):
        module = load_db_watch("auto_reply_db_watch_capture_only_test")
        state = self._clean_state(module)
        state.update(
            capability_state="starting",
            delivery_enabled=False,
            fence_reason="context_sync_transient",
            fence="starting",
        )
        enrollment = {
            "chat_id": 42,
            "chat_name": module.CHAT,
            "identity": {
                "kind": "local_name",
                "local_name": module.CHAT,
                "ax_name": module.CHAT,
            },
            "reply_author_bindings": [
                {"nickname": "member", "author_id": 700}
            ],
        }
        environment = {
            "OPENKAKAO_AUTO_REPLY_CLI": "1",
            "OPENKAKAO_DB_MODE": "database_authoritative",
            "OPENKAKAO_AUTO_REPLY_ENABLED": "1",
            "OPENKAKAO_SUPERVISOR_OWNER": "owner",
            "OPENKAKAO_DB_SOURCE_EPOCH": "7",
        }
        persisted = {}

        def save(value, **_kwargs):
            persisted.update(value)
            return True

        with (
            mock.patch.dict(os.environ, environment, clear=False),
            mock.patch.object(module, "_state", side_effect=lambda value: dict(value)),
            mock.patch.object(module, "_reconcile_ingress_journal"),
            mock.patch.object(module, "_owner_epoch_current", return_value=True),
            mock.patch.object(module, "cleanup_orphan_media"),
            mock.patch.object(module, "_start_poll_stream"),
            mock.patch.object(module, "_stop_poll_stream"),
            mock.patch.object(
                module, "_read_poll_envelope", return_value=self._envelope(module)
            ),
            mock.patch.object(module, "_cli_enrollment_target", return_value=enrollment),
            mock.patch.object(
                module, "_generation_lock", side_effect=contextlib.nullcontext
            ),
            mock.patch.object(module, "load_state", side_effect=lambda: dict(persisted)),
            mock.patch.object(module, "save_state", side_effect=save),
            mock.patch.object(module, "_journal_candidate"),
            mock.patch.object(module, "emit", return_value="accepted") as emit,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result, emitted = module.poll_once(
                state,
                1.0,
                delivery_ready=False,
                not_ready_reason="context_sync_transient",
            )
        self.assertEqual(emitted, 1)
        self.assertEqual(result["acked_watermark"], 124)
        self.assertEqual(result["pending_log_ids"], [])
        self.assertEqual(result["candidate_phase"], "idle")
        self.assertEqual(result["capability_state"], "starting")
        self.assertFalse(result["delivery_enabled"])
        self.assertEqual(result["fence"], "starting")
        self.assertEqual(result["fence_reason"], "context_sync_transient")
        emit.assert_called_once()

    def test_capture_only_poll_rejects_unrecognized_not_ready_reason(self):
        module = load_db_watch("auto_reply_db_watch_capture_only_reason_test")
        state, emitted = module.poll_once(
            self._clean_state(module),
            1.0,
            delivery_ready=False,
            not_ready_reason="aggregate_readiness_missing",
        )
        self.assertEqual(emitted, 0)
        self.assertEqual(state["capability_state"], "fenced")
        self.assertFalse(state["delivery_enabled"])
        self.assertEqual(state["fence_reason"], "context_sync_unavailable")

    def test_clean_cursor_snapshot_allows_only_context_sync_capture_fences(self):
        module = load_db_watch("auto_reply_db_watch_capture_snapshot_test")
        state = self._clean_state(module)
        state.update(
            capability_state="starting",
            delivery_enabled=False,
            fence="starting",
        )
        for reason in ("context_sync_transient", "context_sync_deferred"):
            state["fence_reason"] = reason
            self.assertIsNotNone(
                module._clean_poll_retry_snapshot(
                    state,
                    retry_fence=False,
                    capture_only=True,
                )
            )
        state["fence_reason"] = "aggregate_readiness_missing"
        self.assertIsNone(
            module._clean_poll_retry_snapshot(
                state,
                retry_fence=False,
                capture_only=True,
            )
        )
        state["fence_reason"] = "context_sync_transient"
        self.assertIsNone(
            module._clean_poll_retry_snapshot(state, retry_fence=False)
        )

    def test_capture_only_retry_wrapper_never_promotes_delivery_readiness(self):
        module = load_db_watch("auto_reply_db_watch_capture_wrapper_test")
        state = self._clean_state(module)
        state.update(
            capability_state="starting",
            delivery_enabled=False,
            fence_reason="context_sync_deferred",
            fence="starting",
        )
        with (
            mock.patch.object(module, "poll_once", return_value=(state, 0)) as poll,
            mock.patch.object(module, "_save_polled_state", return_value=True),
        ):
            result, emitted = module._poll_with_bounded_clean_retry(
                state,
                1.0,
                delivery_ready=False,
                not_ready_reason="context_sync_deferred",
            )
        self.assertEqual(emitted, 0)
        self.assertFalse(result["delivery_enabled"])
        poll.assert_called_once_with(
            state,
            1.0,
            delivery_ready=False,
            not_ready_reason="context_sync_deferred",
        )

    @staticmethod
    def _page_envelope(module, log_ids, *, sent_at_by_log_id=None):
        sent_at_by_log_id = sent_at_by_log_id or {}
        messages = [
            {
                "log_id": log_id,
                "chat_id": 42,
                "author_id": 700,
                "is_self": False,
                "sender_name": "member",
                "message": f"question {log_id}",
                "attachment": "",
                "message_type": 1,
                "sent_at": sent_at_by_log_id.get(log_id, 1000 + log_id),
                "reply_authorized": True,
            }
            for log_id in log_ids
        ]
        return {
            "schema_version": 3,
            "chat": {
                "chat_id": 42,
                "chat_name": module.CHAT,
                "last_log_id": log_ids[-1],
            },
            "messages": messages,
            "completeness": {
                "status": "complete",
                "after_log_id": 123,
                "first_log_id": log_ids[0],
                "last_log_id": log_ids[-1],
                "row_count": len(log_ids),
                "returned_count": len(log_ids),
                "available_max_log_id": log_ids[-1],
                "chat_last_log_id": log_ids[-1],
                "id_domain": "global_sparse",
                "has_gap": False,
                "has_more": False,
                "proof": "sqlite_snapshot_rowset",
            },
        }

    def _drive_one_page(
        self,
        module,
        log_ids,
        *,
        hook_seconds,
        clock_start=1000.0,
        sent_at_by_log_id=None,
    ):
        """Drive one bounded page with a fake clock and record every save.

        ``hook_seconds`` is how long each hook round trip is made to take.
        Every persisted room state is captured together with the clock reading
        at the moment it was written, which is what the supervisor's freshness
        check and the reply worker's pre-send fence actually observe.
        """

        enrollment = {
            "chat_id": 42,
            "chat_name": module.CHAT,
            "identity": {
                "kind": "local_name",
                "local_name": module.CHAT,
                "ax_name": module.CHAT,
            },
            "reply_author_bindings": [{"nickname": "member", "author_id": 700}],
        }
        environment = {
            "OPENKAKAO_AUTO_REPLY_CLI": "1",
            "OPENKAKAO_DB_MODE": "database_authoritative",
            "OPENKAKAO_AUTO_REPLY_ENABLED": "1",
            "OPENKAKAO_SUPERVISOR_OWNER": "owner",
            "OPENKAKAO_DB_SOURCE_EPOCH": "7",
        }
        clock = {"now": float(clock_start)}
        saves = []

        def hook(*_args, **_kwargs):
            clock["now"] += hook_seconds
            return "skipped"

        def save(state, **_kwargs):
            saves.append((clock["now"], dict(state)))
            return True

        def load():
            return dict(saves[-1][1]) if saves else {}

        with (
            mock.patch.dict(os.environ, environment, clear=False),
            mock.patch.object(module.time, "time", side_effect=lambda: clock["now"]),
            mock.patch.object(module, "_state", side_effect=lambda value: dict(value)),
            mock.patch.object(module, "_reconcile_ingress_journal"),
            mock.patch.object(module, "_owner_epoch_current", return_value=True),
            mock.patch.object(module, "cleanup_orphan_media"),
            mock.patch.object(module, "_start_poll_stream"),
            mock.patch.object(module, "_stop_poll_stream"),
            mock.patch.object(
                module,
                "_read_poll_envelope",
                return_value=self._page_envelope(
                    module,
                    list(log_ids),
                    sent_at_by_log_id=sent_at_by_log_id,
                ),
            ),
            mock.patch.object(module, "_cli_enrollment_target", return_value=enrollment),
            mock.patch.object(
                module, "_generation_lock", side_effect=contextlib.nullcontext
            ),
            mock.patch.object(module, "load_state", side_effect=load),
            mock.patch.object(module, "save_state", side_effect=save),
            mock.patch.object(module, "_journal_candidate"),
            mock.patch.object(module, "emit", side_effect=hook),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            state, emitted = module.poll_once(self._clean_state(module), 1.0)
        return state, emitted, saves

    def test_timing_diagnostics_track_recent_max_without_widening_clean_fence(self):
        module = load_db_watch("auto_reply_db_watch_timing_math_test")
        state = self._clean_state(module)
        clean_before = module._clean_poll_retry_snapshot(state, retry_fence=False)

        module._record_successful_poll_envelope(state, 1000.0)
        module._record_successful_poll_envelope(state, 1002.25)
        module._record_successful_poll_envelope(state, 1008.75)
        module._record_successful_poll_envelope(state, 1010.0)

        module._record_candidate_ingress_delay(
            state,
            {
                "sent_at": 840,
                "message": "private body",
                "log_id": 987654321,
                "chat_name": "private room",
            },
            1000.0,
        )
        module._record_candidate_ingress_delay(
            state,
            {"sent_at": 900},
            1010.0,
        )

        diagnostics = state[module.TIMING_DIAGNOSTICS_KEY]
        self.assertEqual(diagnostics["poll_envelope_interval_recent_seconds"], 1.25)
        self.assertEqual(diagnostics["poll_envelope_interval_recent_observed_at"], 1010.0)
        self.assertEqual(diagnostics["poll_envelope_interval_max_seconds"], 6.5)
        self.assertEqual(diagnostics["poll_envelope_interval_max_observed_at"], 1008.75)
        self.assertEqual(diagnostics["candidate_ingress_delay_recent_seconds"], 110.0)
        self.assertEqual(diagnostics["candidate_ingress_delay_recent_observed_at"], 1010.0)
        self.assertEqual(diagnostics["candidate_ingress_delay_max_seconds"], 160.0)
        self.assertEqual(diagnostics["candidate_ingress_delay_max_observed_at"], 1000.0)
        self.assertLessEqual(
            set(diagnostics),
            set(module._TIMING_DIAGNOSTIC_KEYS),
        )
        self.assertNotIn("private body", repr(diagnostics))
        self.assertNotIn("private room", repr(diagnostics))
        self.assertNotIn("987654321", repr(diagnostics))
        self.assertEqual(module.STATE_VERSION, 3)
        self.assertEqual(module.LEGACY_STATE_VERSION, 2)
        self.assertEqual(
            module._clean_poll_retry_snapshot(state, retry_fence=False),
            clean_before,
        )

    def test_fake_candidate_records_160_second_ingress_delay_on_existing_save(self):
        module = load_db_watch("auto_reply_db_watch_ingress_delay_test")
        recorded_at = 2_000_000.0
        state, emitted, saves = self._drive_one_page(
            module,
            (124,),
            hook_seconds=0.0,
            clock_start=recorded_at,
            sent_at_by_log_id={124: int(recorded_at - 160)},
        )

        self.assertEqual(emitted, 1)
        self.assertEqual(len(saves), 3, "diagnostics must not add a state write")
        first_candidate_save = saves[0][1]
        diagnostics = first_candidate_save[module.TIMING_DIAGNOSTICS_KEY]
        self.assertEqual(diagnostics["candidate_ingress_delay_recent_seconds"], 160.0)
        self.assertEqual(diagnostics["candidate_ingress_delay_recent_observed_at"], recorded_at)
        self.assertEqual(diagnostics["candidate_ingress_delay_max_seconds"], 160.0)
        self.assertEqual(diagnostics["candidate_ingress_delay_max_observed_at"], recorded_at)
        self.assertEqual(diagnostics["poll_envelope_last_success_at"], recorded_at)
        self.assertNotIn("question 124", repr(diagnostics))
        self.assertNotIn(module.CHAT, repr(diagnostics))
        self.assertNotIn("log_id", diagnostics)
        self.assertEqual(state["candidate_phase"], "idle")
        self.assertEqual(state["pending_log_ids"], [])
        self.assertIsNone(state["in_flight_candidate"])

    def test_candidate_ingress_delay_ignores_missing_invalid_or_future_sent_at(self):
        module = load_db_watch("auto_reply_db_watch_ingress_delay_invalid_test")
        recorded_at = 1000.0
        invalid_messages = (
            {},
            {"sent_at": None},
            {"sent_at": True},
            {"sent_at": -1},
            {"sent_at": 1.5},
            {"sent_at": 1001},
        )
        for message in invalid_messages:
            state = self._clean_state(module)
            module._record_candidate_ingress_delay(state, message, recorded_at)
            self.assertNotIn(module.TIMING_DIAGNOSTICS_KEY, state)

        # sent_at is the message timestamp, not Kakao's SQLite insertion time;
        # the actual first insert instant remains unavailable to this watcher.
        state = self._clean_state(module)
        module._record_candidate_ingress_delay(state, {"sent_at": 900}, recorded_at)
        self.assertEqual(
            state[module.TIMING_DIAGNOSTICS_KEY]["candidate_ingress_delay_recent_seconds"],
            100.0,
        )

    def test_long_candidate_page_keeps_db_heartbeat_inside_the_liveness_window(self):
        """A catch-up page must not look dead while it is answering.

        One page can carry LOCAL_POLL_MAX_ROWS candidates and each hook is a
        subprocess round trip, so a busy room's page runs for minutes. The
        liveness stamp used to be written once at the top of the poll, so the
        supervisor fenced the room as db_heartbeat_stale and the reply worker's
        pre-send fence read the same stale stamp: the room could not deliver the
        very turn it was working on.
        """

        window = liveness_window_seconds()
        module = load_db_watch("auto_reply_db_watch_page_liveness_test")
        hook_seconds = window + 5.0
        log_ids = (124, 125, 126)
        state, emitted, saves = self._drive_one_page(
            module,
            log_ids,
            hook_seconds=hook_seconds,
        )

        self.assertEqual(emitted, len(log_ids))
        self.assertEqual(state["candidate_phase"], "idle")
        self.assertEqual(state["pending_log_ids"], [])
        self.assertIsNone(state["in_flight_candidate"])
        self.assertEqual(state["acked_watermark"], log_ids[-1])
        self.assertGreaterEqual(len(saves), 2 * len(log_ids))

        page_span = saves[-1][0] - saves[0][0]
        self.assertGreater(page_span, window, "the page must outlast the window")
        ages = [now - float(payload["heartbeat_at"]) for now, payload in saves]
        self.assertLessEqual(
            max(ages),
            window,
            "a page that is answering candidates must keep the db heartbeat fresh",
        )
        stamps = [float(payload["heartbeat_at"]) for _, payload in saves]
        self.assertGreater(
            len(set(stamps)),
            1,
            "the liveness stamp must advance with the page, not freeze at poll start",
        )
        self.assertGreaterEqual(max(stamps), saves[-1][0] - window)

    def test_a_wedged_hook_still_outruns_the_liveness_window(self):
        """Re-stamping must prove progress, not manufacture it.

        A hook that never returns writes no transition, so the room must still
        read as stale and stay fenced instead of inheriting liveness from the
        candidate that preceded it.
        """

        window = liveness_window_seconds()
        module = load_db_watch("auto_reply_db_watch_page_wedged_test")
        state, emitted, saves = self._drive_one_page(
            module,
            (124,),
            hook_seconds=window * 2.0,
        )

        self.assertEqual(emitted, 1)
        self.assertEqual(state["candidate_phase"], "idle")
        gaps = [later[0] - earlier[0] for earlier, later in zip(saves, saves[1:])]
        self.assertTrue(gaps)
        self.assertGreater(
            max(gaps),
            window,
            "an unreturned hook must leave a real staleness gap",
        )


if __name__ == "__main__":
    unittest.main()
