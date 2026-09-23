import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
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


class AutoReplyDbWatchStartingTakeoverTests(unittest.TestCase):
    @contextmanager
    def _cli_scenario(
        self,
        *,
        enrollment_floor: int = 123,
        cursor_kind: str = "fenced_leftover_ack_resume",
    ):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            room_root = root / "rooms" / "42"
            room_root.mkdir(parents=True)
            state_path = room_root / "db-watch-state.json"
            enrollment_path = root / "enrollment.json"
            enrollment = {
                "schema_version": 4,
                "targets": [
                    {
                        "chat_id": 42,
                        "chat_name": "enrolled-room",
                        "last_log_id": enrollment_floor,
                        "room_state_root": str(room_root),
                        "cursor_authority": {
                            "schema_version": 1,
                            "kind": cursor_kind,
                            "cursor_floor": enrollment_floor,
                            "attested_db_last_log_id": 250,
                            "prior_owner_id": "old-owner",
                            "prior_source_epoch": 8,
                        },
                        "identity": {
                            "schema_version": 1,
                            "kind": "ax_transcript",
                            "local_name": "",
                            "ax_name": "enrolled-room",
                            "matched_log_ids": [248, 249, 250],
                            "matched_count": 3,
                            "matched_utf8_bytes": 40,
                            "transcript_sha256": "a" * 64,
                            "attested_db_last_log_id": 250,
                        },
                        "reply_author_bindings": [
                            {"nickname": "member", "author_id": 700}
                        ],
                    }
                ],
            }
            raw = json.dumps(enrollment, sort_keys=True).encode("utf-8")
            enrollment_path.write_bytes(raw)
            environment = {
                "OPENKAKAO_AUTO_REPLY_CLI": "1",
                "OPENKAKAO_TARGET_CHAT_ID": "42",
                "OPENKAKAO_TARGET_CHAT_NAME": "enrolled-room",
                "OPENKAKAO_SUPERVISOR_OWNER": "new-owner",
                "OPENKAKAO_DB_SOURCE_EPOCH": "9",
                "OPENKAKAO_DB_WATCH_STATE": str(state_path),
                "OPENKAKAO_ENROLLMENT_PATH": str(enrollment_path),
                "OPENKAKAO_ENROLLMENT_SHA256": hashlib.sha256(raw).hexdigest(),
            }
            with mock.patch.dict(os.environ, environment, clear=False):
                module = load_db_watch(
                    f"auto_reply_dw_starting_takeover_{self._testMethodName}"
                )
                yield module

    @staticmethod
    def _state(
        module,
        *,
        owner_id: str = "old-owner",
        source_epoch: int = 8,
        cursor_floor: int = 123,
        acked_watermark: int = 123,
        acked_log_ids=None,
        observed_log_ids=None,
        pending_log_ids=None,
        candidate_phase: str = "idle",
        capability_state: str = "starting",
        delivery_enabled: bool = False,
        fence: str = "starting",
    ):
        acked = [123] if acked_log_ids is None else list(acked_log_ids)
        observed = [123] if observed_log_ids is None else list(observed_log_ids)
        pending = [] if pending_log_ids is None else list(pending_log_ids)
        return {
            "schema_version": module.state_schema_version(),
            "target_chat_id": 42,
            "target_chat_name": module.CHAT,
            "cursor_floor": cursor_floor,
            "last_observed_log_id": max(observed, default=0),
            "acked_watermark": acked_watermark,
            "pending_log_ids": pending,
            "pending_gaps": [],
            "observed_log_ids": observed,
            "acked_log_ids": acked,
            "source_epoch": source_epoch,
            "capability_state": capability_state,
            "delivery_enabled": delivery_enabled,
            "fence_reason": "",
            "owner_id": owner_id,
            "heartbeat_at": "persisted-heartbeat",
            "fence": fence,
            "candidate_phase": candidate_phase,
            "in_flight_candidate": None,
            "recent_message_tail": [],
        }

    def assertStartingTakeover(self, adopted):
        self.assertEqual(adopted["owner_id"], "new-owner")
        self.assertEqual(adopted["source_epoch"], 9)
        self.assertEqual(adopted["capability_state"], "starting")
        self.assertFalse(adopted["delivery_enabled"])
        self.assertEqual(adopted["fence"], "starting")
        self.assertEqual(adopted["fence_reason"], "")
        self.assertNotEqual(adopted["fence"], "reconcile_required")

    def assertReconcileRequired(self, state):
        self.assertEqual(state["capability_state"], "fenced")
        self.assertFalse(state["delivery_enabled"])
        self.assertEqual(state["fence"], "reconcile_required")
        self.assertEqual(state["fence_reason"], "reconcile_required")

    def test_starting_idle_adopts_new_owner_with_consistent_cursors(self):
        with self._cli_scenario() as module:
            adopted = module._state(self._state(module))

        self.assertStartingTakeover(adopted)
        self.assertEqual(adopted["cursor_floor"], 123)
        self.assertEqual(adopted["acked_watermark"], 123)
        self.assertEqual(adopted["last_observed_log_id"], 123)
        self.assertEqual(adopted["observed_log_ids"], [123])
        self.assertEqual(adopted["acked_log_ids"], [123])
        self.assertEqual(adopted["pending_log_ids"], [])

    def test_starting_unacked_observed_tail_adopts_and_compacts_to_prior_ack(self):
        with self._cli_scenario() as module:
            adopted = module._state(
                self._state(
                    module,
                    observed_log_ids=[123, 200],
                    pending_log_ids=[],
                )
            )

        self.assertStartingTakeover(adopted)
        self.assertEqual(adopted["cursor_floor"], 123)
        self.assertEqual(adopted["acked_watermark"], 123)
        self.assertEqual(adopted["last_observed_log_id"], 123)
        self.assertEqual(adopted["observed_log_ids"], [123])
        self.assertEqual(adopted["acked_log_ids"], [123])
        self.assertEqual(adopted["pending_log_ids"], [])
        self.assertEqual(adopted["pending_gaps"], [])

    def test_starting_tail_requires_matching_leftover_enrollment_authority(self):
        cases = (
            (124, "fenced_leftover_ack_resume"),
            (123, "stopped_clean_ack_replay"),
        )
        for enrollment_floor, cursor_kind in cases:
            with self.subTest(
                enrollment_floor=enrollment_floor,
                cursor_kind=cursor_kind,
            ):
                with self._cli_scenario(
                    enrollment_floor=enrollment_floor,
                    cursor_kind=cursor_kind,
                ) as module:
                    rejected = module._state(
                        self._state(
                            module,
                            observed_log_ids=[123, 200],
                            pending_log_ids=[],
                        )
                    )
                self.assertReconcileRequired(rejected)

    def test_ready_tail_does_not_gain_starting_takeover_tolerance(self):
        with self._cli_scenario() as module:
            rejected = module._state(
                self._state(
                    module,
                    observed_log_ids=[123, 200],
                    pending_log_ids=[],
                    capability_state="ready",
                    delivery_enabled=True,
                    fence="ready",
                )
            )

        self.assertReconcileRequired(rejected)

    def test_sending_phase_stays_fenced(self):
        with self._cli_scenario() as module:
            rejected = module._state(
                self._state(module, candidate_phase="sending")
            )

        self.assertReconcileRequired(rejected)

    def test_pre_delivery_pending_and_acknowledging_phases_can_take_over(self):
        for candidate_phase in ("pending", "acknowledging"):
            with self.subTest(candidate_phase=candidate_phase):
                with self._cli_scenario() as module:
                    adopted = module._state(
                        self._state(module, candidate_phase=candidate_phase)
                    )
                self.assertStartingTakeover(adopted)
                self.assertEqual(adopted["candidate_phase"], "idle")

    def test_sentinel_ids_or_watermark_stay_fenced(self):
        with self._cli_scenario() as module:
            max_int64 = module.MAX_INT64
            cases = {
                "acked": self._state(
                    module,
                    acked_log_ids=[123, max_int64],
                    observed_log_ids=[123, max_int64],
                    acked_watermark=max_int64,
                ),
                "observed": self._state(
                    module,
                    observed_log_ids=[123, max_int64],
                ),
                "pending": self._state(
                    module,
                    pending_log_ids=[max_int64],
                ),
                "watermark": self._state(
                    module,
                    acked_watermark=max_int64,
                ),
            }
            for location, state in cases.items():
                with self.subTest(location=location):
                    self.assertReconcileRequired(module._state(state))

    def test_matching_owner_epoch_keeps_starting_state_without_compaction(self):
        with self._cli_scenario() as module:
            persisted = self._state(
                module,
                owner_id="new-owner",
                source_epoch=9,
                observed_log_ids=[123, 200],
                pending_log_ids=[200],
            )
            normalized = module._state(persisted)

        self.assertEqual(normalized["owner_id"], "new-owner")
        self.assertEqual(normalized["source_epoch"], 9)
        self.assertEqual(normalized["capability_state"], "starting")
        self.assertFalse(normalized["delivery_enabled"])
        self.assertEqual(normalized["fence"], "starting")
        self.assertEqual(normalized["acked_watermark"], 123)
        self.assertEqual(normalized["last_observed_log_id"], 200)
        self.assertEqual(normalized["observed_log_ids"], [123, 200])
        self.assertEqual(normalized["acked_log_ids"], [123])
        self.assertEqual(normalized["pending_log_ids"], [200])

    def test_pending_ids_outside_unacked_observed_set_stay_fenced(self):
        with self._cli_scenario() as module:
            rejected = module._state(
                self._state(
                    module,
                    observed_log_ids=[123, 200],
                    pending_log_ids=[201],
                )
            )

        self.assertReconcileRequired(rejected)


if __name__ == "__main__":
    unittest.main()
