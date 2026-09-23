"""Provider reset hints and model budgets, using isolated circuit storage."""

import importlib.util
import contextlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


class ModelRetryPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "retry_policy_worker", SCRIPTS / "auto-reply-worker.py",
        )
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o700)
        environment = mock.patch.dict(os.environ, {
            "OPENKAKAO_MODEL_CIRCUIT_DB": str(self.root / "circuit.sqlite3"),
        })
        environment.start()
        self.addCleanup(environment.stop)
        for name, value in (("QUEUE", self.root / "queue.sqlite3"),
                            ("WORKER_STATUS", self.root / "worker-status.json"),
                            ("_WORKER_HEALTH", None)):
            patch = mock.patch.object(self.module, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    @contextlib.contextmanager
    def isolated_generation(self, runner_kind, runner):
        """Use the real generation and circuit paths with fake external adapters."""
        m = self.module
        # Product generation is local-only. Keep the synthetic primary local
        # while retaining "opencode" in its id for the quota-reset branch.
        primary = "mlx/opencode-test-primary"
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, {
                "OPENKAKAO_MODEL_CIRCUIT_DB": str(self.root / f"{runner_kind}.sqlite3"),
            }))
            for name, value in {
                "REPLY_RUNNER_KIND": runner_kind,
                "_generation_reply_model": mock.Mock(return_value=primary),
                "_reply_fallback_candidates": mock.Mock(return_value=["mlx/local"]),
                "privacy_attestation_current": mock.Mock(return_value=True),
                "runner_is_trusted": mock.Mock(return_value=True),
                "record_learned_style_tells": mock.Mock(),
                "learned_style_tell_avoids": mock.Mock(return_value=[]),
                "_reply_decision_instructions": mock.Mock(return_value=[]),
                "_reply_decision_system_prompt": mock.Mock(return_value="Test decision"),
                "_queue_expected_chat_id": mock.Mock(return_value=1),
                "_ensure_omlx_model_resident": mock.Mock(),
                "_publish_model_status": mock.Mock(),
                "_active_journal_checkpoint": mock.Mock(),
            }.items():
                stack.enter_context(mock.patch.object(m, name, value))
            stack.enter_context(mock.patch(
                "auto_reply_knowledge_graph.retrieve_knowledge_bundle", return_value={},
            ))

            def subprocess_runner(command, **kwargs):
                return runner(command[command.index("--model") + 1], **kwargs)

            stack.enter_context(mock.patch.object(m, "_run_bounded_process", subprocess_runner))
            stack.enter_context(mock.patch.object(m, "_run_opencodex_generation", runner))
            yield primary

    @staticmethod
    def decision_output():
        return 0, json.dumps({
            "should_reply": False, "reply": "", "reason": "low_information",
            "category": "uncertain", "evidence_ids": [],
        }).encode(), b""

    def test_compound_reset_windows_and_existing_numeric_hints(self):
        cases = {
            "Resets in 2h39m22s.": 9562,
            "Weekly usage limit reached. Resets in 16hr 12min.": 58320,
            "try again in 2 hours 3 minutes 4 seconds": 7384,
            "Retry in 0h5m": 300,
            "Reset in 1.5 minutes": 90,
            "Retry-After: 75": 75,
            "retry_after_seconds 120": 120,
            "Resets in 81h33m30s.": 86400,
            "Resets in 2 days": 86400,
        }
        for text, seconds in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.module._retry_after_seconds(text), seconds)

    def test_malformed_reset_windows_never_accept_a_numeric_prefix(self):
        for text in (
            "Resets in -2h", "Resets in 0s", "Resets in NaNs",
            "Resets in infs", "Resets in 2h -3m", "Resets in 2h 30",
            "Resets in 2h30ms", "Resets in 2h2h", "Resets in 2hoursx",
            "Resets in 1e3s", "Resets in " + "9" * 400 + "h",
        ):
            with self.subTest(text=text):
                self.assertIsNone(self.module._retry_after_seconds(text))

    def test_known_resets_replace_unknown_window_defaults(self):
        m = self.module
        with mock.patch.object(m.random, "random", return_value=0):
            for failure in ("quota_exhausted", "usage_limit"):
                self.assertEqual(m._model_failure_delay(failure, 16, 9562), 9562)
                self.assertEqual(m._model_failure_delay(failure, 1, 1), 5)
                self.assertEqual(m._model_failure_delay(failure, 1, 1e9), 86400)
            self.assertEqual(m._model_failure_delay("rate_limit", 1, 1), 60)
            self.assertEqual(m._model_failure_delay("usage_limit", 1, None), 21600)
            self.assertEqual(m._model_failure_delay("quota_exhausted", 1, None), 86400)

    def test_invalid_hints_keep_defaults_and_jitter_never_shortens_reset(self):
        m = self.module
        with mock.patch.object(m.random, "random", return_value=0):
            for hint in (True, False, "20", -1, 0, float("nan"), float("inf")):
                self.assertEqual(m._model_failure_delay("usage_limit", 1, hint), 21600)
        with mock.patch.object(m.random, "random", return_value=0.9):
            self.assertGreaterEqual(m._model_failure_delay("quota_exhausted", 1, 9562), 9562)

    def test_failed_fallback_persists_reset_and_winner_owns_its_lease(self):
        m = self.module
        failed, good = "cloud/limited", "mlx/local"
        now = 1800000000.0
        calls = []

        def runner(model):
            calls.append(model)
            if model == failed:
                return 429, b"", b"Quota exhausted. Resets in 2h39m22s."
            return 0, b"answer", b""

        with mock.patch.object(m, "_reply_fallback_candidates", return_value=[failed, good]), \
             mock.patch.object(m.time, "time", return_value=now), \
             mock.patch.object(m.random, "random", return_value=0):
            winner = m._model_fallback_chain("primary", runner)
            self.assertEqual(calls, [failed, good])
            self.assertEqual(winner["model"], good)
            connection = m._model_circuit_connection()
            try:
                row = connection.execute(
                    "SELECT state, failure_class, open_until, lease_token "
                    "FROM model_circuit_breaker WHERE model_key = ?",
                    (m._model_circuit_key(failed),),
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(tuple(row), ("open", "quota_exhausted", now + 9562, None))
            self.assertTrue(m._finish_model_call_success(winner["lease_token"], model=good))

    def test_primary_closure_keeps_reset_after_a_successful_fallback(self):
        m = self.module
        now = 1800000000.0
        with mock.patch.object(m.time, "time", return_value=now), \
             mock.patch.object(m.random, "random", return_value=0):
            slot = m._acquire_model_call_slot(model="primary")
            failure, hint = m._classify_model_failure(
                1, json.dumps({"type": "error", "message":
                    "Weekly usage limit reached. Resets in 16hr 12min."}).encode(), b"",
            )
            m._close_model_lease(slot["lease_token"], "primary", failure,
                                 retry_after_seconds=hint)
            refused = m._acquire_model_call_slot(model="primary")
            self.assertFalse(refused["allowed"])
            self.assertEqual(refused["retry_at"], now + 58320)

    def test_model_timeouts_fit_remaining_budget(self):
        m = self.module
        self.assertEqual(m._model_generation_timeout("mlx/local"), 90)
        self.assertEqual(m._model_generation_timeout("oMLX/local"), 90)
        self.assertEqual(m._model_generation_timeout("cloud/model"), 45)
        self.assertLess(m.LOCAL_MODEL_GENERATION_TIMEOUT_SECONDS, m.MODEL_CALL_LEASE_SECONDS)
        with mock.patch.object(m.time, "monotonic", return_value=100):
            self.assertEqual(m._model_generation_timeout("mlx/local", deadline=130), 30)
            self.assertEqual(m._model_generation_timeout("mlx/local", deadline=99), 0)

    def test_chain_stops_before_opening_another_lease_when_budget_is_spent(self):
        m = self.module
        clock = [100.0]
        calls = []

        def runner(model):
            calls.append(model)
            clock[0] = 249.0
            return 1, b"", b"failed"

        with mock.patch.object(m.time, "monotonic", side_effect=lambda: clock[0]), \
             mock.patch.object(m, "_reply_fallback_candidates", return_value=["one", "two"]):
            self.assertIsNone(m._model_fallback_chain("primary", runner, deadline=250))
        self.assertEqual(calls, ["one"])
        connection = m._model_circuit_connection()
        try:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM model_circuit_breaker WHERE state = 'in_flight'",
            ).fetchone()[0], 0)
        finally:
            connection.close()

    def test_generation_budget_accounts_for_elapsed_processing(self):
        m = self.module
        with mock.patch.object(m.time, "monotonic", return_value=100):
            health = m._WorkerHealth()
            health.phase("processing")
        with mock.patch.object(m.time, "monotonic", return_value=170):
            self.assertEqual(health.generation_deadline(), 265)
        with mock.patch.object(m.time, "monotonic", return_value=300):
            self.assertLess(health.generation_deadline(), 300)

    def test_generation_adopts_fallback_and_settles_primary_with_reset(self):
        m = self.module
        now = 1800000000.0
        for kind in ("opencodex", "gjc"):
            calls = []

            def runner(model, *_args, **kwargs):
                calls.append((model, kwargs["timeout"]))
                if model == "mlx/local":
                    return self.decision_output()
                return 429, b"", b"Quota exhausted. Resets in 2h39m22s."

            with self.subTest(kind=kind), self.isolated_generation(kind, runner) as primary, \
                 mock.patch.object(m.time, "time", return_value=now), \
                 mock.patch.object(m.time, "monotonic", return_value=1000), \
                 mock.patch.object(m.random, "random", return_value=0):
                result = m.generate_reply("확인했어요", [], [], [], [])
                self.assertEqual(result["model"], "mlx/local")
                self.assertEqual(result["reason"], "low_information")
                self.assertEqual([model for model, _ in calls], [primary, "mlx/local"])
                self.assertEqual(calls[-1][1], 90)
                refused = m._acquire_model_call_slot(model=primary)
                self.assertFalse(refused["allowed"])
                self.assertEqual(refused["retry_at"], now + 9562)
                self.assertIsNotNone(m._open_fallback_lease("mlx/local"))

    def test_unparsed_success_is_bounded_deferral_without_circuit_failure(self):
        m = self.module
        now = 1800000000.0

        def runner(_model, *_args, **_kwargs):
            return 0, b"not a decision", b""

        with self.isolated_generation("opencodex", runner), \
             mock.patch.object(m.time, "time", return_value=now), \
             mock.patch.object(m.time, "monotonic", return_value=1000.0):
            model_result = m.generate_reply("확인했어요", [], [], [], [])
            self.assertEqual(model_result["reason"], "unparsed_model_decision")
            self.assertEqual(model_result["category"], "uncertain")
            self.assertFalse(model_result["should_reply"])
            self.assertEqual(model_result["model_failure_class"], "unparsed_output")
            self.assertTrue(m.math.isfinite(model_result["model_defer_until"]))
            self.assertGreater(model_result["model_defer_until"], 0.0)
            self.assertNotIn("unparsed_output", m.MODEL_CIRCUIT_FAILURE_CLASSES)
            self.assertIn(
                "unparsed_output",
                m.MODEL_DEFERRABLE_NON_CIRCUIT_FAILURE_CLASSES,
            )
            m._publish_model_status.assert_called_with("available")

            event = {
                "event_id": "retry-policy-unparsed",
                "message": "확인했어요",
                "sent_at": int(now),
                "response_window_upper_seconds": m.MIN_REPLY_DELAY_SECONDS,
            }
            bundle = {
                "context": [{"evidence_id": "context:1", "message": "bounded"}],
                "styles": [],
                "prior_decisions": [],
                "style_profile": {},
                "recipient_style_profile": {},
                "response_time": {},
            }
            with mock.patch.object(m, "_reply_turn_hold_reason", return_value=None), \
                 mock.patch.object(m, "_event_recent_conversation", return_value=[]), \
                 mock.patch.object(m, "capture_visible_image", return_value=None), \
                 mock.patch.object(m, "fetch_link_previews", return_value=[]), \
                 mock.patch.object(m, "run_context_reply_bundle", return_value=bundle), \
                 mock.patch.object(m, "event_exceeds_response_window", return_value=False), \
                 mock.patch.object(m, "_conversation_target", return_value={}), \
                 mock.patch.object(m, "_partner_streak_hold_reason", return_value=None), \
                 mock.patch.object(m, "generate_reply", return_value=model_result):
                analysis = m.analyze_event(event)

        self.assertEqual(analysis["model_failure_class"], "unparsed_output")
        self.assertEqual(analysis["reason"], "unparsed_model_decision")
        due_at = m._model_defer_due_at(event, analysis, now=now)
        self.assertIsInstance(due_at, float)
        self.assertTrue(m.math.isfinite(due_at))
        response_deadline = m.response_due_at(
            event["sent_at"],
            event["response_window_upper_seconds"],
            now=now,
        )
        self.assertLessEqual(due_at, response_deadline)

        expired_now = (
            response_deadline
            + m.PRE_SEND_RETRY_GRACE_SECONDS
            + m.MODEL_MIN_DEFER_SECONDS
        )
        self.assertTrue(
            m._past_send_grace(
                event,
                event["response_window_upper_seconds"],
                now=expired_now,
            )
        )
        self.assertLessEqual(
            m._model_defer_due_at(event, analysis, now=expired_now),
            expired_now,
        )

    def test_legitimate_non_reply_without_failure_class_is_not_deferred(self):
        m = self.module
        now = 1800000000.0
        event = {
            "sent_at": int(now),
            "response_window_upper_seconds": m.MIN_REPLY_DELAY_SECONDS,
        }
        analysis = {
            "should_reply": False,
            "reply": "",
            "reason": "low_information",
            "category": "uncertain",
        }
        self.assertIsNone(m._model_defer_due_at(event, analysis, now=now))

    def test_completed_cooldown_fallback_survives_elapsed_budget(self):
        m = self.module
        for kind in ("opencodex", "gjc"):
            clock = [1000.0]
            calls = []

            def runner(model, *_args, **_kwargs):
                calls.append(model)
                # Answer exists even when post-call accounting crosses the budget.
                clock[0] = 1151.0
                return self.decision_output()

            with self.subTest(kind=kind), self.isolated_generation(kind, runner) as primary, \
                 mock.patch.object(m.time, "monotonic", side_effect=lambda: clock[0]):
                slot = m._acquire_model_call_slot(model=primary)
                m._close_model_lease(slot["lease_token"], primary, "usage_limit")
                result = m.generate_reply("확인했어요", [], [], [], [])
                self.assertEqual(calls, ["mlx/local"])
                self.assertEqual(result["model"], "mlx/local")
                self.assertEqual(result["reason"], "low_information")

    def test_timeout_fallback_uses_only_remaining_generation_budget(self):
        m = self.module
        clock = [1000.0]
        calls = []

        def runner(model, *_args, **kwargs):
            calls.append((model, kwargs["timeout"]))
            if model != "mlx/local":
                clock[0] += 90
                raise m.subprocess.TimeoutExpired("fake runner", 90)
            return self.decision_output()

        with self.isolated_generation("gjc", runner), \
             mock.patch.object(m.time, "monotonic", side_effect=lambda: clock[0]):
            result = m.generate_reply("확인했어요", [], [], [], [])
            self.assertEqual(result["reason"], "low_information")
            self.assertEqual(result["model"], "mlx/local")
            self.assertEqual(calls[-1], ("mlx/local", 60))

    def _delivery_fixture(self, log_id, *, sent_at=None, response_window=300.0):
        m = self.module
        current = time.time()
        event = {
            "event_id": f"db:1:{log_id}",
            "chat_id": 1,
            "log_id": log_id,
            "analysis_watermark_log_id": log_id,
            "source_epoch": 1,
            "owner_id": "owner",
            "author_nickname": "member",
            "message": "질문",
            "sent_at": int(current - 5 if sent_at is None else sent_at),
        }
        if response_window is not ...:
            event["response_window_upper_seconds"] = response_window
        connection = m.transition_journal.open_queue(
            m.QUEUE,
            create=True,
            expected_chat_id=None,
        )
        connection.execute(
            """
            INSERT INTO reply_jobs(
                event_id,event_json,status,due_at,decision,reason,category,
                reply,scheduled_delay_seconds,error_class,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                event["event_id"], json.dumps(event, ensure_ascii=False), "processing",
                None, "reply", "useful", "social", "answer", 9.5, None,
                current - 10.0, current,
            ),
        )
        connection.commit()
        job = {
            "event_id": event["event_id"],
            "event_json": json.dumps(event, ensure_ascii=False),
            "reply": "answer",
        }
        return event, job, connection

    @contextlib.contextmanager
    def _scheduled_delivery_patches(self, watermarks, *, preflight=None, sender=None):
        m = self.module
        sender = sender or mock.Mock(return_value=True)
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(m, "db_authoritative_event_allowed", return_value=True))
            stack.enter_context(mock.patch.object(m, "privacy_attestation_current", return_value=True))
            stack.enter_context(mock.patch.object(m, "_reply_turn_hold_reason", return_value=None))
            stack.enter_context(mock.patch.object(m, "event_exceeds_response_upper", return_value=False))
            stack.enter_context(mock.patch.object(m, "later_self_already_replied", return_value=False))
            stack.enter_context(mock.patch.object(m, "_constructed_geeknews_outbound", return_value=False))
            stack.enter_context(mock.patch.object(m, "_event_recent_conversation", return_value=[]))
            stack.enter_context(mock.patch.object(m, "_policy_valid_draft", return_value=True))
            stack.enter_context(mock.patch.object(m, "_send_time_repeat_hold", return_value=None))
            stack.enter_context(mock.patch.object(
                m,
                "pre_ax_delivery_probe",
                return_value=preflight or {"result": "ready"},
            ))
            stack.enter_context(mock.patch.object(
                m,
                "stable_room_watermark_log_id",
                side_effect=watermarks,
            ))
            stack.enter_context(mock.patch.object(m, "send_reply", sender))
            yield sender

    def test_context_freshness_unknown_defers_at_both_send_boundaries(self):
        m = self.module
        for offset, site in enumerate(("before_preflight", "after_preflight"), start=1):
            with self.subTest(site=site):
                event, job, connection = self._delivery_fixture(900 + offset)
                watermarks = (
                    [None]
                    if site == "before_preflight"
                    else [event["analysis_watermark_log_id"], None]
                )
                try:
                    with self._scheduled_delivery_patches(watermarks) as sender, \
                         mock.patch.object(m, "finish_delivery_unknown") as unknown:
                        m.process_job(job, "scheduled", connection)
                    sender.assert_not_called()
                    unknown.assert_not_called()
                    row = connection.execute(
                        """
                        SELECT status,due_at,reason,reply,error_class
                        FROM reply_jobs WHERE event_id=?
                        """,
                        (event["event_id"],),
                    ).fetchone()
                    self.assertEqual(row["status"], "pending")
                    self.assertIsNotNone(row["due_at"])
                    self.assertEqual(row["reason"], "context_freshness_unavailable")
                    self.assertIsNone(row["reply"])
                    self.assertEqual(row["error_class"], "context_freshness_unavailable")
                finally:
                    connection.close()

    def test_context_freshness_unknown_expires_as_stale_backlog(self):
        m = self.module
        current = time.time()
        sent_at = current - 300.0 - m.PRE_SEND_RETRY_GRACE_SECONDS - 10.0
        event, job, connection = self._delivery_fixture(
            910,
            sent_at=sent_at,
            response_window=300.0,
        )
        try:
            with self._scheduled_delivery_patches([None]) as sender, \
                 mock.patch.object(m, "_coalesced_burst_event", side_effect=lambda value: value), \
                 mock.patch.object(m, "durable_policy_skip", return_value=True), \
                 mock.patch.object(m, "complete_event"), \
                 mock.patch.object(m, "finish_delivery_unknown") as unknown:
                m.process_job(job, "scheduled", connection)
            sender.assert_not_called()
            unknown.assert_not_called()
            row = connection.execute(
                "SELECT status,due_at,reason,reply FROM reply_jobs WHERE event_id=?",
                (event["event_id"],),
            ).fetchone()
            self.assertEqual(tuple(row), ("skipped", None, "stale_backlog", None))
        finally:
            connection.close()

    def test_context_freshness_unknown_malformed_or_absent_window_is_bounded(self):
        m = self.module
        event, job, connection = self._delivery_fixture(
            920,
            response_window="not-a-window",
        )
        try:
            with self._scheduled_delivery_patches([None]) as sender, \
                 mock.patch.object(m, "update_context_decision", return_value=True), \
                 mock.patch.object(m, "record_delivery_unknown"):
                m.process_job(job, "scheduled", connection)
            sender.assert_not_called()
            row = connection.execute(
                "SELECT status,due_at FROM reply_jobs WHERE event_id=?",
                (event["event_id"],),
            ).fetchone()
            self.assertEqual(tuple(row), ("delivery_unknown", None))
        finally:
            connection.close()

        current = time.time()
        sent_at = (
            current
            - m.PRE_SEND_DEFAULT_RESPONSE_WINDOW_SECONDS
            - m.PRE_SEND_RETRY_GRACE_SECONDS
            - 10.0
        )
        event, job, connection = self._delivery_fixture(
            921,
            sent_at=sent_at,
            response_window=...,
        )
        try:
            with self._scheduled_delivery_patches([None]) as sender, \
                 mock.patch.object(m, "_coalesced_burst_event", side_effect=lambda value: value), \
                 mock.patch.object(m, "durable_policy_skip", return_value=True), \
                 mock.patch.object(m, "complete_event"):
                m.process_job(job, "scheduled", connection)
            sender.assert_not_called()
            row = connection.execute(
                "SELECT status,reason FROM reply_jobs WHERE event_id=?",
                (event["event_id"],),
            ).fetchone()
            self.assertEqual(tuple(row), ("skipped", "stale_backlog"))
        finally:
            connection.close()

    def test_newer_context_requeues_and_stable_context_sends(self):
        m = self.module
        event, job, connection = self._delivery_fixture(930)
        try:
            with self._scheduled_delivery_patches([event["log_id"] + 1]) as sender, \
                 mock.patch.object(m, "_coalesced_burst_event", side_effect=lambda value: value), \
                 mock.patch.object(m, "durable_policy_skip", return_value=True), \
                 mock.patch.object(m, "complete_event"):
                m.process_job(job, "scheduled", connection)
            sender.assert_not_called()
            row = connection.execute(
                "SELECT status,due_at,reason,reply,error_class FROM reply_jobs WHERE event_id=?",
                (event["event_id"],),
            ).fetchone()
            self.assertEqual(row["status"], "pending")
            self.assertIsNotNone(row["due_at"])
            self.assertEqual(row["reason"], "conversation_context_changed")
            self.assertIsNone(row["reply"])
            self.assertEqual(row["error_class"], "conversation_context_changed")
        finally:
            connection.close()

        event, job, connection = self._delivery_fixture(931)
        sender = mock.Mock(return_value=True)
        try:
            with self._scheduled_delivery_patches(
                [event["log_id"], event["log_id"]], sender=sender
            ), \
                 mock.patch.object(m, "update_context_decision", return_value=True), \
                 mock.patch.object(m, "record_delivery_ledger"), \
                 mock.patch.object(m, "complete_event"):
                m.process_job(job, "scheduled", connection)
            sender.assert_called_once()
            row = connection.execute(
                "SELECT status FROM reply_jobs WHERE event_id=?",
                (event["event_id"],),
            ).fetchone()
            self.assertEqual(row["status"], "sent")
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
