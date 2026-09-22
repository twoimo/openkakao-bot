import contextlib
import importlib.util
import io
import os
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


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

    def test_database_timeout_fence_keeps_main_loop_running(self):
        module = load_db_watch("auto_reply_db_watch_main_busy_test")
        module.SELF = "self"
        valid_sync = {
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
        calls = []

        def poll(state, interval):
            calls.append((dict(state), interval))
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
            mock.patch.object(module, "load_state", return_value={}),
            mock.patch.object(module, "_state", side_effect=lambda value: dict(value)),
            mock.patch.object(module, "sync_context_index", return_value=valid_sync),
            mock.patch.object(module, "save_state", return_value=True),
            mock.patch.object(
                module, "_poll_with_bounded_clean_retry", side_effect=poll
            ),
            mock.patch.object(module.time, "sleep"),
            mock.patch.object(module, "_stop_poll_stream"),
            self.assertRaises(StopIteration),
        ):
            module.main()
        self.assertEqual(len(calls), 2)
        self.assertFalse(calls[1][0]["delivery_enabled"])
        self.assertEqual(calls[1][0]["fence_reason"], "database_timeout")


if __name__ == "__main__":
    unittest.main()
