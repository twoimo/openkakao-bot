import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from unittest import mock

from tests import test_auto_reply_cli_runtime as runtime_helpers


class AutoReplyTurnHoldTests(unittest.TestCase):
    _load_auto_reply_module = staticmethod(
        runtime_helpers.AutoReplyCliRuntimeTests._load_auto_reply_module
    )
    _burst_event = staticmethod(runtime_helpers.AutoReplyCliRuntimeTests._burst_event)
    _recent_row = staticmethod(runtime_helpers.AutoReplyCliRuntimeTests._recent_row)
    _write_numeric_enrollment = staticmethod(
        runtime_helpers.AutoReplyCliRuntimeTests._write_numeric_enrollment
    )
    _worker_queue_connection = staticmethod(
        runtime_helpers.AutoReplyCliRuntimeTests._worker_queue_connection
    )
    _queued_turn_with_authoritative_watermark = (
        runtime_helpers.AutoReplyCliRuntimeTests._queued_turn_with_authoritative_watermark
    )

    def test_self_inbound_holds_before_analyze_model(self):
        module = self._load_auto_reply_module("auto_reply_turn_hold_self_test")
        event = self._burst_event(module, 501, "비밀 본문", int(time.time()))
        event["is_self"] = True

        self.assertEqual(module._reply_turn_hold_reason(event), "self_author")
        with mock.patch.object(module, "generate_reply") as generate:
            analysis = module.analyze_event(event)

        self.assertEqual(analysis["decision"], "skip")
        self.assertEqual(analysis["reason"], "self_author")
        self.assertEqual(analysis["category"], "policy")
        generate.assert_not_called()

    def test_malformed_is_self_fails_closed_before_model(self):
        module = self._load_auto_reply_module("auto_reply_turn_hold_bad_self_test")
        missing = object()
        for value in (missing, None, "false", 0):
            with self.subTest(is_self=value):
                event = self._burst_event(module, 502, "질문", int(time.time()))
                if value is missing:
                    event.pop("is_self")
                else:
                    event["is_self"] = value
                self.assertEqual(
                    module._reply_turn_hold_reason(event),
                    "author_identity_drift",
                )
                with mock.patch.object(module, "generate_reply") as generate:
                    analysis = module.analyze_event(event)
                self.assertEqual(analysis["reason"], "author_identity_drift")
                self.assertEqual(analysis["category"], "policy")
                generate.assert_not_called()

    def test_enrolled_nickname_wrong_author_id_is_identity_drift(self):
        module = self._load_auto_reply_module("auto_reply_turn_hold_wrong_id_test")
        with tempfile.TemporaryDirectory() as temporary:
            environment = self._write_numeric_enrollment(
                module,
                temporary,
                [{"nickname": "member", "author_id": 700}],
            )
            event = self._burst_event(
                module,
                503,
                "hello",
                int(time.time()),
                author="member",
                author_id=701,
            )
            with mock.patch.dict(os.environ, environment, clear=False):
                self.assertEqual(
                    module._inbound_identity_hold_reason(event),
                    "author_identity_drift",
                )
                self.assertEqual(
                    module._reply_turn_hold_reason(event),
                    "author_identity_drift",
                )

    def test_unenrolled_nonhuman_and_bot_names_are_not_allowlisted(self):
        module = self._load_auto_reply_module("auto_reply_turn_hold_allowlist_test")
        with tempfile.TemporaryDirectory() as temporary:
            environment = self._write_numeric_enrollment(
                module,
                temporary,
                [{"nickname": "member", "author_id": 700}],
            )
            cases = (
                ("stranger", 701),
                ("뉴스봇", 702),
                ("테스트봇", 703),
            )
            with mock.patch.dict(os.environ, environment, clear=False):
                for nickname, author_id in cases:
                    with self.subTest(nickname=nickname):
                        event = self._burst_event(
                            module,
                            504,
                            "hello",
                            int(time.time()),
                            author=nickname,
                            author_id=author_id,
                        )
                        self.assertEqual(
                            module._inbound_identity_hold_reason(event),
                            "author_not_allowlisted",
                        )

    def test_identity_hold_precedes_conversation_advance(self):
        module = self._load_auto_reply_module("auto_reply_turn_hold_precedence_test")
        event = self._burst_event(module, 505, "self", int(time.time()))
        event["is_self"] = True
        with mock.patch.object(
            module,
            "conversation_advanced_past_event",
            return_value=True,
        ) as advanced:
            self.assertEqual(module._reply_turn_hold_reason(event), "self_author")
        advanced.assert_not_called()

    def test_proactive_short_circuits_reply_clock(self):
        module = self._load_auto_reply_module("auto_reply_turn_hold_proactive_test")
        event = self._burst_event(module, 506, "digest", int(time.time()))
        event.update(proactive=True, is_self=True)
        with (
            mock.patch.object(module, "numeric_author_identity_status") as identity,
            mock.patch.object(module, "conversation_advanced_past_event") as advanced,
        ):
            self.assertIsNone(module._reply_turn_hold_reason(event))
        identity.assert_not_called()
        advanced.assert_not_called()

    def test_watermark_holds_analyze_and_process_without_model(self):
        module = self._load_auto_reply_module("auto_reply_turn_hold_watermark_test")
        cases = (
            (lambda state: {**state, "last_observed_log_id": 421}, "conversation_advanced"),
            (lambda _state: {}, "context_freshness_unavailable"),
        )
        for state_builder, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                with self._queued_turn_with_authoritative_watermark(module) as fixture:
                    event, state_path, state = fixture
                    state_path.write_text(
                        json.dumps(state_builder(state)),
                        encoding="utf-8",
                    )
                    with mock.patch.object(module, "generate_reply") as generate:
                        analysis = module.analyze_event(event)
                    self.assertEqual(analysis["reason"], expected_reason)
                    self.assertEqual(analysis["category"], "policy")
                    generate.assert_not_called()

                    job = {
                        "event_id": event["event_id"],
                        "event_json": json.dumps(event, ensure_ascii=False),
                    }
                    with (
                        mock.patch.object(
                            module,
                            "db_authoritative_event_allowed",
                            return_value=True,
                        ),
                        mock.patch.object(
                            module,
                            "privacy_attestation_current",
                            return_value=True,
                        ),
                        mock.patch.object(module, "finish_turn_policy_skip") as finish,
                        mock.patch.object(module, "analyze_event") as analyze,
                        mock.patch.object(module, "generate_reply") as process_generate,
                    ):
                        module.process_job(job, "pending", None)
                    finish.assert_called_once_with(
                        event,
                        event["event_id"],
                        None,
                        expected_reason,
                    )
                    analyze.assert_not_called()
                    process_generate.assert_not_called()

    def test_process_job_burst_superseded_uses_special_finisher(self):
        module = self._load_auto_reply_module("auto_reply_turn_hold_burst_test")
        event = self._burst_event(module, 507, "later part", int(time.time()))
        job = {
            "event_id": event["event_id"],
            "event_json": json.dumps(event, ensure_ascii=False),
        }
        with (
            mock.patch.object(
                module,
                "db_authoritative_event_allowed",
                return_value=True,
            ),
            mock.patch.object(module, "privacy_attestation_current", return_value=True),
            mock.patch.object(
                module,
                "_reply_turn_hold_reason",
                return_value="burst_superseded",
            ),
            mock.patch.object(module, "finish_burst_superseded") as finish_burst,
            mock.patch.object(module, "durable_policy_skip") as generic_skip,
            mock.patch.object(module, "analyze_event") as analyze,
        ):
            module.process_job(job, "pending", None)

        finish_burst.assert_called_once_with(event, event["event_id"], None)
        generic_skip.assert_not_called()
        analyze.assert_not_called()

    def test_process_job_rechecks_turn_after_formed_reply_before_send(self):
        module = self._load_auto_reply_module("auto_reply_turn_hold_post_analysis_test")
        event = self._burst_event(module, 511, "question", int(time.time()))
        job = {
            "event_id": event["event_id"],
            "event_json": json.dumps(event, ensure_ascii=False),
        }
        analysis = {
            "decision": "reply",
            "reason": "direct_question",
            "category": "question",
            "reply": "formed reply",
            "provenance": {},
        }
        with (
            mock.patch.object(module, "db_authoritative_event_allowed", return_value=True),
            mock.patch.object(module, "privacy_attestation_current", return_value=True),
            mock.patch.object(
                module,
                "_reply_turn_hold_reason",
                side_effect=(None, "conversation_advanced"),
            ) as turn_hold,
            mock.patch.object(module, "analyze_event", return_value=analysis) as analyze,
            mock.patch.object(module, "update_job") as update_job,
            mock.patch.object(module, "finish_turn_policy_skip") as finish,
            mock.patch.object(module, "send_reply") as send,
        ):
            module.process_job(job, "pending", None)

        self.assertEqual(turn_hold.call_count, 2)
        analyze.assert_called_once()
        update_job.assert_called_once_with(
            event["event_id"], connection=None, reply="formed reply"
        )
        finish.assert_called_once_with(
            event,
            event["event_id"],
            None,
            "conversation_advanced",
        )
        send.assert_not_called()

    def test_partner_streak_holds_and_exceptions(self):
        module = self._load_auto_reply_module("auto_reply_turn_hold_partner_streak_test")
        recent = [
            {
                "author_nickname": "member",
                "is_self": False,
                "message": "첫 메시지",
                "sent_at": 100,
            },
            {
                "author_nickname": "최연우",
                "is_self": True,
                "message": "이미 답함",
                "sent_at": 101,
            },
        ]
        event = {"author_nickname": "member", "sent_at": 102}
        self.assertEqual(
            module._partner_streak_hold_reason(event, "아하", recent),
            "already_commented",
        )
        self.assertIsNone(
            module._partner_streak_hold_reason(event, "이거 뭐야?", recent)
        )
        self.assertIsNone(
            module._partner_streak_hold_reason(
                event,
                "아하",
                recent,
                {"directed_at_self": True},
            )
        )
        self.assertIsNone(
            module._partner_streak_hold_reason(
                event,
                "아하",
                recent,
                attachment="image",
                media_bundle_digest="a" * 64,
            )
        )
        self.assertIsNone(
            module._partner_streak_hold_reason(event, "너 AI냐", recent)
        )
        self.assertIsNone(
            module._partner_streak_hold_reason(
                {"author_nickname": "최연우", "sent_at": 102},
                "아하",
                recent,
            )
        )

    def test_trace_unknown_hold_reason_redacts_content_and_identity(self):
        module = self._load_auto_reply_module("auto_reply_turn_hold_trace_test")
        event = {
            "chat_id": 42,
            "log_id": 508,
            "message": "TOP SECRET BODY",
            "author_nickname": "SECRET DISPLAY NAME",
        }
        captured = io.StringIO()
        with redirect_stderr(captured):
            module._trace_turn_hold(event, "unknown_reason_with_secret")
        output = captured.getvalue()
        self.assertIn("skip=context_freshness_unavailable", output)
        self.assertIn("chat_id=42", output)
        self.assertIn("log_id=508", output)
        self.assertNotIn("TOP SECRET BODY", output)
        self.assertNotIn("SECRET DISPLAY NAME", output)
        self.assertNotIn("unknown_reason_with_secret", output)

    def test_invalid_event_dicts_fail_closed_without_exception(self):
        module = self._load_auto_reply_module("auto_reply_turn_hold_invalid_event_test")
        invalid_events = (
            {},
            {"is_self": None},
            {"is_self": []},
            {"is_self": False},
            {
                "is_self": False,
                "author_id": [],
                "author_nickname": [],
                "chat_id": [],
            },
            {"is_self": True, "author_id": "bad"},
        )
        for event in invalid_events:
            with self.subTest(event=event):
                reason = module._reply_turn_hold_reason(event)
                self.assertIn(reason, module.TURN_HOLD_REASONS)
        self.assertIsNone(
            module._reply_turn_hold_reason({"proactive": True, "is_self": "bad"})
        )

    def test_media_unavailable_clarification_holds_before_retrieval(self):
        module = self._load_auto_reply_module("auto_reply_turn_hold_media_test")
        event = self._burst_event(module, 509, "[사진]", int(time.time()), attachment=True)
        event["is_self"] = True
        with (
            mock.patch.object(module, "run_context_reply_bundle") as retrieve,
            mock.patch.object(module, "generate_reply") as generate,
        ):
            analysis = module.analyze_media_unavailable_clarification(event)
        self.assertEqual(analysis["reason"], "self_author")
        self.assertEqual(analysis["category"], "policy")
        retrieve.assert_not_called()
        generate.assert_not_called()

    def test_generate_reply_turn_guard_holds_before_runner(self):
        module = self._load_auto_reply_module("auto_reply_turn_hold_generation_test")

        def broken_guard():
            raise RuntimeError("watermark unavailable")

        with (
            mock.patch.object(module, "runner_is_trusted") as trusted,
            mock.patch.object(module, "_run_bounded_process") as bounded_runner,
            mock.patch.object(module, "_run_opencodex_generation") as opencodex_runner,
        ):
            for guard, expected_reason in (
                (lambda: "conversation_advanced", "conversation_advanced"),
                (lambda: "unknown_hold", "context_freshness_unavailable"),
                (broken_guard, "context_freshness_unavailable"),
            ):
                with self.subTest(reason=expected_reason):
                    result = module.generate_reply(
                        "hello",
                        [],
                        [],
                        [],
                        [],
                        turn_guard=guard,
                    )
                    self.assertFalse(result["should_reply"])
                    self.assertEqual(result["reason"], expected_reason)
                    self.assertEqual(result["category"], "policy")
        trusted.assert_not_called()
        bounded_runner.assert_not_called()
        opencodex_runner.assert_not_called()

    def test_pre_mutation_send_hold_uses_turn_guard_before_repeat_check(self):
        module = self._load_auto_reply_module("auto_reply_turn_hold_pre_send_test")
        event = {
            "event_id": "db:42:510",
            "chat_id": 42,
            "log_id": 510,
            "sent_at": int(time.time()),
            "response_window_upper_seconds": 600.0,
        }
        with (
            mock.patch.object(module, "_past_send_grace", return_value=False),
            mock.patch.object(
                module,
                "_reply_turn_hold_reason",
                return_value="conversation_advanced",
            ) as turn_hold,
            mock.patch.object(module, "_send_time_repeat_hold") as repeat_hold,
        ):
            reason = module._pre_mutation_send_hold(
                "formed reply",
                event=event,
                connection=None,
            )
        self.assertEqual(reason, "conversation_advanced")
        turn_hold.assert_called_once_with(event, None)
        repeat_hold.assert_not_called()


if __name__ == "__main__":
    unittest.main()
