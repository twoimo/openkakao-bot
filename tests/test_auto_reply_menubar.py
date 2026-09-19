import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
MENUBAR = SCRIPTS / "auto-reply-menubar.py"
SWIFT = ROOT / "macos" / "AutoReplyMenu" / "main.swift"
for path in (str(TESTS), str(SCRIPTS)):
    if path not in sys.path:
        sys.path.insert(0, path)

import test_auto_reply_tui as _tui_tests


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, MENUBAR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


FORBIDDEN = (
    "queue-body-must-never-escape",
    "reply-body-must-never-escape",
    "candidate-body-must-never-escape",
    "status-body-must-never-escape",
    "Private Author",
    "monitor-secret",
    "watchdog-secret",
    "supervisor-secret",
    "worker-secret",
    "ax-secret",
    "https://news.hada.io",
    "secret-headline",
)


class AutoReplyMenubarTests(unittest.TestCase):
    maxDiff = None

    def _load_fixture(self, root: Path):
        helper = _tui_tests.AutoReplyTuiTests()
        tui, state, room, queue, now = helper.fixture(root)
        return helper, tui, state, room, queue, now

    def _model(self, root: Path, **kwargs):
        module = load(f"auto_reply_menubar_{id(root)}")
        helper, _tui, state, room, queue, now = self._load_fixture(root)
        kwargs.setdefault("now", now)
        model = module.collect_menubar_model(state.resolve(), **kwargs)
        return helper, module, state, room, queue, now, model
    def _doctor(self, module, state, **kwargs):
        impl = getattr(module, "_FROZEN_RUN_DOCTOR", None)
        if callable(impl):
            report = impl(state, **kwargs)
        else:
            report = module.run_doctor(state, **kwargs)
        if isinstance(report, dict) and hasattr(module, "_enrich_doctor_report"):
            try:
                extra = module._enrich_doctor_report({"checks": []}, Path(state)).get("checks") or []
                seen = {item.get("code") for item in report.get("checks") or []}
                report = dict(report)
                report["checks"] = list(report.get("checks") or []) + [
                    item for item in extra if item.get("code") not in seen
                ]
                report["health"] = module._doctor_component_health(Path(state))
            except Exception:
                return report
        return report

    def _encoded(self, model: dict) -> str:
        return json.dumps(model, ensure_ascii=False, sort_keys=True)

    def test_healthy_fixture_is_green_and_redacted(self):
        with tempfile.TemporaryDirectory() as temporary:
            _helper, _module, state, _room, _queue, _now, model = self._model(
                Path(temporary)
            )
            encoded = self._encoded(model)
            self.assertEqual(model["privacy"], "content_redacted")
            self.assertEqual(model["level"], "yellow")
            self.assertEqual(model["primary_code"], "processing")
            self.assertIn("processing", model["codes"])
            self.assertEqual(model["watermark"], "99")
            self.assertEqual(model["open_jobs"], 1)
            self.assertIsInstance(model["log_lines"], list)
            self.assertEqual(
                set(model["health"]),
                {"watchdog", "supervisor", "ax", "worker", "model"},
            )
            self.assertIn(model["health"]["watchdog"], {"ok", "warn", "err", "off"})
            self.assertNotIn("Room", encoded)
            self.assertNotIn(str(state), encoded)
            for secret in FORBIDDEN:
                self.assertNotIn(secret, encoded)

    def test_ax_window_missing_is_yellow(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, room, _queue, now, _model = self._model(
                Path(temporary)
            )
            ax = json.loads((room / "apple-watch-status.json").read_text())
            ax["rows"] = 0
            helper._private_json(room / "apple-watch-status.json", ax)
            model = module.collect_menubar_model(state.resolve(), now=now)
            self.assertEqual(model["level"], "yellow")
            self.assertIn("ax_window_missing", model["codes"])
            self.assertEqual(
                model["notifications"][0]["code"], "ax_window_missing"
            )
            self.assertEqual(model["notifications"][0]["body"], "code=ax_window_missing")

    def test_ax_watcher_unhealthy_fence_is_yellow_not_red(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, room, _queue, now, _model = self._model(
                Path(temporary)
            )
            supervisor = json.loads((room / "supervisor-status.json").read_text())
            supervisor["fence_reason"] = "ax_watcher_unhealthy"
            helper._private_json(room / "supervisor-status.json", supervisor)
            ax = json.loads((room / "apple-watch-status.json").read_text())
            ax["state"] = "degraded"
            ax["rows"] = 0
            helper._private_json(room / "apple-watch-status.json", ax)
            model = module.collect_menubar_model(state.resolve(), now=now)
            self.assertEqual(model["level"], "yellow")
            self.assertIn("ax_window_missing", model["codes"])
            self.assertNotIn("fenced", model["codes"])

    def test_owner_fence_is_red(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, room, _queue, now, _model = self._model(
                Path(temporary)
            )
            supervisor = json.loads((room / "supervisor-status.json").read_text())
            supervisor["fence_reason"] = "owner_fence"
            helper._private_json(room / "supervisor-status.json", supervisor)
            model = module.collect_menubar_model(state.resolve(), now=now)
            self.assertEqual(model["level"], "red")
            self.assertIn("fenced", model["codes"])

    def test_worker_death_is_red_and_notifies(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, room, _queue, now, _model = self._model(
                Path(temporary)
            )
            worker = json.loads((room / "reply-worker-status.json").read_text())
            worker["state"] = "unavailable"
            helper._private_json(room / "reply-worker-status.json", worker)
            model = module.collect_menubar_model(state.resolve(), now=now)
            self.assertEqual(model["level"], "red")
            self.assertIn("worker_unhealthy", model["codes"])
            self.assertEqual(
                [item["code"] for item in model["notifications"]],
                ["worker_unhealthy"],
            )


    def test_operator_request_only_room_does_not_paint_menubar_red(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, room, _queue, now, before = self._model(
                Path(temporary)
            )
            leftover = state / "rooms" / "260330955968694"
            leftover.mkdir(mode=0o700)
            helper._private_json(
                leftover / "operator-request.json",
                {
                    "action": "geeknews-now",
                    "requested_at": int(now),
                    "schema_version": 1,
                    "targets": [int(room.name), 260330955968694],
                },
            )
            after = module.collect_menubar_model(state.resolve(), now=now)
            self.assertEqual(after["level"], before["level"])
            self.assertNotIn("worker_unhealthy", after["codes"])
            self.assertNotIn("supervisor_unhealthy", after["codes"])
            chat_ids = [item["chat_id"] for item in after["rooms"]]
            self.assertNotIn(260330955968694, chat_ids)
            self.assertIn(int(room.name), chat_ids)


    def test_leftover_non_enrolled_room_does_not_paint_menubar_red(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, room, _queue, now, before = self._model(
                Path(temporary)
            )
            helper._private_json(
                state / "enrollment.json",
                {
                    "schema_version": 4,
                    "selectors": [f"bind:{room.name}:fixture"],
                    "targets": [{"chat_id": int(room.name)}],
                },
            )
            leftover = state / "rooms" / "325472527151234"
            leftover.mkdir(mode=0o700)
            helper._private_json(
                leftover / "supervisor-status.json",
                {
                    "schema_version": 1,
                    "state": "stopped",
                    "readiness": "fenced",
                    "fence_reason": "shutdown",
                    "target_chat_id": 325472527151234,
                    "target_chat_name": "leftover",
                },
            )
            helper._private_json(
                leftover / "reply-worker-status.json",
                {
                    "schema_version": 1,
                    "state": "exited",
                    "readiness": "ready",
                    "phase": "idle",
                    "target_chat_id": 325472527151234,
                },
            )
            module._scope_menubar_rooms_to_enrollment()
            after = module.collect_menubar_model(state.resolve(), now=now)
            self.assertEqual(after["level"], before["level"])
            self.assertNotIn("supervisor_unhealthy", after["codes"])
            self.assertNotIn("fenced", after["codes"])
            self.assertNotIn("identity_mismatch", after["codes"])
            chat_ids = [item["chat_id"] for item in after["rooms"]]
            self.assertNotIn(325472527151234, chat_ids)
            self.assertIn(int(room.name), chat_ids)

    def _fenced_room(self, **value_overrides):
        value = {
            "state": "running",
            "readiness": "fenced",
            "fence_reason": "db_capability_fenced",
            "shutdown_state": "not_stopped",
            "readiness_reasons": [
                "db_capability_fenced",
                "db_delivery_not_ready",
                "db_watermark_invalid",
            ],
            "child_states": {
                "ax_watch": "running",
                "db_watch": "running",
                "reply_worker": "running",
            },
        }
        value.update(value_overrides)
        return {
            "chat_id": 1,
            "statuses": {"supervisor": {"value": value}},
        }

    def _transient_room_root(self, module, **state_overrides):
        temporary = tempfile.TemporaryDirectory()
        room_dir = Path(temporary.name) / "rooms" / "1"
        room_dir.mkdir(parents=True)
        state = {
            "capability_state": "starting",
            "fence": "starting",
            "fence_reason": "",
            "pending_gaps": [],
            "heartbeat_at": module.time.time(),
        }
        state.update(state_overrides)
        (room_dir / "db-watch-state.json").write_text(
            json.dumps(state), encoding="utf-8"
        )
        module._ACTIVE_STATE_ROOT = Path(temporary.name)
        return temporary

    def test_transient_poll_fence_is_softened_to_ready(self):
        module = load(f"auto_reply_menubar_soften_{id(self)}")
        temporary = self._transient_room_root(module)
        try:
            softened = module._soften_candidate_fence(self._fenced_room())
            value = softened["statuses"]["supervisor"]["value"]
            self.assertEqual(value["readiness"], "ready")
            self.assertEqual(value["fence_reason"], "")
            self.assertEqual(value["readiness_reasons"], [])
        finally:
            temporary.cleanup()
            module._ACTIVE_STATE_ROOT = None

    def test_real_fence_is_not_softened(self):
        module = load(f"auto_reply_menubar_soften_neg_{id(self)}")
        temporary = self._transient_room_root(module)
        try:
            owner = module._soften_candidate_fence(
                self._fenced_room(
                    fence_reason="owner_fence", readiness_reasons=["owner_fence"]
                )
            )
            self.assertEqual(
                owner["statuses"]["supervisor"]["value"]["readiness"], "fenced"
            )
            stopped = module._soften_candidate_fence(
                self._fenced_room(state="stopped")
            )
            self.assertEqual(
                stopped["statuses"]["supervisor"]["value"]["readiness"], "fenced"
            )
            child = module._soften_candidate_fence(
                self._fenced_room(
                    child_states={
                        "ax_watch": "running",
                        "db_watch": "exited",
                        "reply_worker": "running",
                    }
                )
            )
            self.assertEqual(
                child["statuses"]["supervisor"]["value"]["readiness"], "fenced"
            )
            empty = module._soften_candidate_fence(
                self._fenced_room(child_states={})
            )
            self.assertEqual(
                empty["statuses"]["supervisor"]["value"]["readiness"], "fenced"
            )
        finally:
            temporary.cleanup()
            module._ACTIVE_STATE_ROOT = None

    def test_delivery_fence_is_not_softened(self):
        module = load(f"auto_reply_menubar_soften_db_{id(self)}")
        temporary = self._transient_room_root(
            module,
            capability_state="fenced",
            fence="db_unavailable",
            fence_reason="poll_fence",
        )
        try:
            out = module._soften_candidate_fence(self._fenced_room())
            self.assertEqual(
                out["statuses"]["supervisor"]["value"]["readiness"], "fenced"
            )
        finally:
            temporary.cleanup()
            module._ACTIVE_STATE_ROOT = None

    def test_stale_db_watch_is_not_softened(self):
        module = load(f"auto_reply_menubar_soften_stale_{id(self)}")
        temporary = self._transient_room_root(
            module, heartbeat_at=module.time.time() - 60
        )
        try:
            out = module._soften_candidate_fence(self._fenced_room())
            self.assertEqual(
                out["statuses"]["supervisor"]["value"]["readiness"], "fenced"
            )
        finally:
            temporary.cleanup()
            module._ACTIVE_STATE_ROOT = None

    def test_missing_db_watch_state_is_not_softened(self):
        module = load(f"auto_reply_menubar_soften_missing_{id(self)}")
        temporary = tempfile.TemporaryDirectory()
        try:
            module._ACTIVE_STATE_ROOT = Path(temporary.name)
            out = module._soften_candidate_fence(self._fenced_room())
            self.assertEqual(
                out["statuses"]["supervisor"]["value"]["readiness"], "fenced"
            )
        finally:
            temporary.cleanup()
            module._ACTIVE_STATE_ROOT = None

    def test_catalog_upsert_writes_room_title(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            catalog = state / "menubar-room-catalog.json"
            catalog.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "rooms": [
                            {"auto_reply": True, "chat_id": 111, "geeknews": True}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            module = load(f"auto_reply_menubar_catalog_title_{id(self)}")
            module._scope_menubar_rooms_to_enrollment()
            argv = sys.argv
            try:
                sys.argv = [
                    "auto-reply-menubar.py",
                    "--state-root",
                    str(state),
                    "--catalog-upsert",
                    json.dumps(
                        {
                            "chat_id": 111,
                            "auto_reply": True,
                            "geeknews": True,
                            "title": "Room A",
                        }
                    ),
                ]
                module._apply_catalog_mutates()
            finally:
                sys.argv = argv
            written = json.loads(catalog.read_text(encoding="utf-8"))
            self.assertEqual(written["rooms"][0]["title"], "Room A")
            # A caller that omits the title must not drop the existing one.
            try:
                sys.argv = [
                    "auto-reply-menubar.py",
                    "--state-root",
                    str(state),
                    "--catalog-upsert",
                    json.dumps({"chat_id": 111, "auto_reply": True, "geeknews": True}),
                ]
                module._apply_catalog_mutates()
            finally:
                sys.argv = argv
            written = json.loads(catalog.read_text(encoding="utf-8"))
            self.assertEqual(written["rooms"][0]["title"], "Room A")

    def test_delivery_unknown_is_red(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, room, queue, now, _model = self._model(
                Path(temporary)
            )
            connection = sqlite3.connect(queue)
            try:
                connection.execute(
                    "UPDATE reply_jobs SET status='delivery_unknown'"
                )
                connection.commit()
            finally:
                connection.close()
            model = module.collect_menubar_model(state.resolve(), now=now)
            self.assertEqual(model["level"], "red")
            self.assertIn("delivery_unknown", model["codes"])
            self.assertEqual(model["delivery_unknown"], 1)

    def test_stale_reply_state_is_yellow_leftover(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, room, queue, now, _model = self._model(
                Path(temporary)
            )
            connection = sqlite3.connect(queue)
            try:
                connection.execute("UPDATE reply_jobs SET status='skipped'")
                connection.commit()
            finally:
                connection.close()
            helper._private_json(
                room / "reply-state.json",
                {
                    "last_event": "db:42:99",
                    "delivery_state": "delivery_unknown",
                },
            )
            model = module.collect_menubar_model(state.resolve(), now=now)
            self.assertEqual(model["level"], "yellow")
            self.assertIn("leftover_occupancy", model["codes"])
            self.assertEqual(model["delivery_unknown"], 0)

    def test_model_temporarily_unavailable_is_yellow(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, room, _queue, now, _model = self._model(
                Path(temporary)
            )
            worker = json.loads((room / "reply-worker-status.json").read_text())
            worker["model_state"] = "cooldown"
            worker["model_failure_class"] = "rate_limit"
            helper._private_json(room / "reply-worker-status.json", worker)
            model = module.collect_menubar_model(state.resolve(), now=now)
            self.assertEqual(model["level"], "yellow")
            self.assertIn("model_temporarily_unavailable", model["codes"])

    def test_bake_digest_mismatch_is_red(self):
        with tempfile.TemporaryDirectory() as temporary:
            _helper, module, state, _room, _queue, now, _model = self._model(
                Path(temporary)
            )
            model = module.collect_menubar_model(
                state.resolve(),
                now=now,
                expected_command_sha256="b" * 64,
            )
            self.assertEqual(model["level"], "red")
            self.assertIn("bake_digest_mismatch", model["codes"])

    def test_geeknews_cursor_drops_feed_and_keeps_slots(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, room, _queue, now, _model = self._model(
                Path(temporary)
            )
            helper._private_json(
                room / "geeknews-rss-cursor.json",
                {
                    "feed": "https://news.hada.io/rss/news",
                    "newest_id": 32653,
                    "seen_ids": [1, 2, 3],
                    "posted_slots": [
                        "2026-08-17:evening",
                        "not-a-slot",
                        "2026-08-19:lunch",
                    ],
                    "headline": "secret-headline",
                },
            )
            model = module.collect_menubar_model(state.resolve(), now=now)
            encoded = self._encoded(model)
            self.assertEqual(model["geeknews_newest_id"], 32653)
            self.assertEqual(
                model["geeknews_slots"],
                ["2026-08-17:evening", "2026-08-19:lunch"],
            )
            self.assertNotIn("https://news.hada.io", encoded)
            self.assertNotIn("secret-headline", encoded)
            self.assertNotIn("seen_ids", encoded)

    def test_log_lines_include_journal_and_redacted_transitions(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, room, _queue, now, _model = self._model(
                Path(temporary)
            )
            logs = Path(temporary) / "logs"
            logs.mkdir()
            (logs / "transitions.jsonl").write_text(
                json.dumps(
                    {
                        "ts": 1_776_000_000,
                        "level": "yellow",
                        "codes": ["leftover_occupancy", "not-a-code"],
                        "open_jobs": 0,
                        "delivery_unknown": 0,
                        "watermark": "3911023721210660865",
                        "geeknews_slots": ["2026-08-19:lunch", "bad"],
                        "body": "queue-body-must-never-escape",
                        "url": "https://news.hada.io",
                        "headline": "secret-headline",
                    },
                    ensure_ascii=False,
                )
                + "\nnot-json\n"
                + json.dumps(["array-not-object"])
                + "\n",
                encoding="utf-8",
            )
            model = module.collect_menubar_model(
                state.resolve(), now=now, logs_dir=logs
            )
            encoded = self._encoded(model)
            self.assertTrue(model["log_lines"])
            self.assertIn(
                "ts=1776000000 level=yellow codes=leftover_occupancy "
                "open=0 unknown=0 wm=3911023721210660865 "
                "geeknews=2026-08-19:lunch",
                model["log_lines"],
            )
            self.assertIn("log=unreadable", model["log_lines"])
            self.assertTrue(
                any(line.startswith("journal ") for line in model["log_lines"])
            )
            display = model["log_display"]
            self.assertTrue(display)
            joined = chr(10).join(display)
            self.assertIn("점유 표시", joined)
            self.assertIn("읽지 못했", joined)
            self.assertTrue(any(line.startswith("기록 ·") for line in display))
            self.assertNotIn("ts=", joined)
            self.assertNotIn("wm=", joined)
            self.assertNotIn("codes=", joined)
            self.assertNotIn("event=", joined)
            self.assertNotIn("not-a-code", joined)
            self.assertIn("주의", model["log_summary"])
            self.assertIn("점유 표시", model["log_summary"] + joined)
            for secret in FORBIDDEN:
                self.assertNotIn(secret, encoded)
            self.assertNotIn("not-a-code", encoded)
            del helper, room

    def test_cli_json_matches_model_and_never_shows_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, room, queue, now, model = self._model(
                Path(temporary)
            )
            del helper, room, queue, now
            import subprocess

            completed = subprocess.run(
                [
                    sys.executable,
                    str(MENUBAR),
                    "--state-root",
                    str(state.resolve()),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            parsed = json.loads(completed.stdout)
            self.assertEqual(parsed["privacy"], "content_redacted")
            self.assertEqual(parsed["level"], model["level"])
            self.assertIn("log_lines", parsed)
            encoded = completed.stdout
            for secret in FORBIDDEN:
                self.assertNotIn(secret, encoded)

    def test_idle_skipped_job_is_green_complete(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, room, queue, now, _model = self._model(
                Path(temporary)
            )
            connection = sqlite3.connect(queue)
            try:
                connection.execute("UPDATE reply_jobs SET status='skipped'")
                connection.commit()
            finally:
                connection.close()
            model = module.collect_menubar_model(state.resolve(), now=now)
            self.assertEqual(model["level"], "green")
            self.assertEqual(model["primary_code"], "ready")
            self.assertEqual(model["open_jobs"], 0)
            del helper, room

    def test_auto_reply_off_is_off(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, _room, _queue, now, _model = self._model(
                Path(temporary)
            )
            module.upsert_catalog_room(
                state.resolve(),
                {"chat_id": 42, "auto_reply": False, "geeknews": False},
            )
            model = module.collect_menubar_model(state.resolve(), now=now)
            self.assertEqual(model["rooms"][0]["level"], "off")
            self.assertIn("auto_reply_off", model["rooms"][0]["codes"])
            self.assertEqual(model["level"], "off")
            self.assertEqual(model["health"]["worker"], "off")
            del helper

    def test_watchdog_stopped_is_off(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, room, _queue, now, _model = self._model(
                Path(temporary)
            )
            watchdog = json.loads((state / "session-watchdog-status.json").read_text())
            watchdog["state"] = "stopped"
            helper._private_json(state / "session-watchdog-status.json", watchdog)
            model = module.collect_menubar_model(state.resolve(), now=now)
            self.assertEqual(model["level"], "off")
            self.assertIn("service_off", model["codes"])
            self.assertNotIn("watchdog_unhealthy", model["codes"])
            del helper, room

    def test_swift_menu_shows_logs_instead_of_finder(self):

        source = SWIFT.read_text(encoding="utf-8")
        self.assertIn("showLogWindow", source)
        self.assertIn("답변 기록", source)
        self.assertIn("log_display", source)
        self.assertNotIn("Reveal Logs", source)
        self.assertNotIn("revealLogs", source)
        self.assertNotIn("NSWorkspace.shared.open(URL(fileURLWithPath: config.logsDir))", source)
        self.assertIn("orderFrontRegardless", source)

    def test_pipeline_marks_delay_active_for_scheduled_job(self):
        with tempfile.TemporaryDirectory() as temporary:
            _helper, _module, _state, _room, _queue, _now, model = self._model(
                Path(temporary)
            )
            pipeline = model["pipeline"]
            states = {item["id"]: item["state"] for item in pipeline["stages"]}
            self.assertEqual(states["delay"], "active")
            self.assertEqual(states["detect"], "done")
            self.assertEqual(model["schema_version"], 3)
            self.assertEqual(model["rooms"][0]["chat_id"], 42)
            self.assertTrue(model["rooms"][0]["live"])
            self.assertIn("pipeline=", chr(10).join(model["menu_lines"]))
            encoded = self._encoded(model)
            for secret in FORBIDDEN:
                self.assertNotIn(secret, encoded)

    def test_catalog_upsert_and_delete_is_id_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, _room, _queue, now, model = self._model(
                Path(temporary)
            )
            rooms = module.upsert_catalog_room(
                state.resolve(),
                {"chat_id": 99, "auto_reply": True, "geeknews": False},
            )
            self.assertEqual(rooms[-1]["chat_id"], 99)
            self.assertFalse(rooms[-1]["geeknews"])
            updated = module.collect_menubar_model(state.resolve(), now=now)
            ids = [item["chat_id"] for item in updated["rooms"]]
            self.assertIn(42, ids)
            self.assertIn(99, ids)
            catalog_only = next(item for item in updated["rooms"] if item["chat_id"] == 99)
            self.assertFalse(catalog_only["live"])
            self.assertFalse(catalog_only["geeknews"])
            self.assertEqual(catalog_only["level"], "off")
            self.assertEqual(updated["level"], model["level"])
            encoded = self._encoded(updated)
            self.assertNotIn("Room", encoded)
            for secret in FORBIDDEN:
                self.assertNotIn(secret, encoded)
            remaining = module.delete_catalog_room(state.resolve(), 99)
            self.assertNotIn(99, [item["chat_id"] for item in remaining])
            del helper

    def test_auto_reply_now_releases_scheduled_job(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, room, queue, now, model = self._model(
                Path(temporary)
            )
            self.assertGreater(model["open_jobs"], 0)
            result = module.run_operator_action(
                state.resolve(), "auto-reply-now", now=now
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["action"], "auto-reply-now")
            self.assertEqual(result["released"], 1)
            self.assertEqual(result["rooms"], [42])
            connection = sqlite3.connect(queue)
            try:
                due_at = connection.execute(
                    "SELECT due_at FROM reply_jobs WHERE event_id = ?",
                    ("db:42:99",),
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertLessEqual(due_at, now)
            request = json.loads(
                (room / module.OPERATOR_REQUEST_NAME).read_text(encoding="utf-8")
            )
            self.assertEqual(request["action"], "auto-reply-now")
            encoded = json.dumps(
                {"result": result, "request": request}, ensure_ascii=False
            )
            for secret in FORBIDDEN:
                self.assertNotIn(secret, encoded)
            del helper

    def test_geeknews_now_writes_operator_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, room, _queue, now, _model = self._model(
                Path(temporary)
            )
            result = module.run_operator_action(
                state.resolve(), "geeknews-now", now=now
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["action"], "geeknews-now")
            self.assertEqual(result["released"], 0)
            request = json.loads(
                (room / module.OPERATOR_REQUEST_NAME).read_text(encoding="utf-8")
            )
            self.assertEqual(request["action"], "geeknews-now")
            self.assertEqual(request["schema_version"], 1)
            encoded = json.dumps(
                {"result": result, "request": request}, ensure_ascii=False
            )
            for secret in FORBIDDEN:
                self.assertNotIn(secret, encoded)
            del helper


    def test_available_chats_lists_group_titles_from_cli(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, _room, _queue, now, model = self._model(
                Path(temporary)
            )
            self.assertEqual(model["available_chats"][0]["chat_id"], 42)
            self.assertEqual(model["available_chats"][0]["title"], "id:42")
            fake = Path(temporary) / "fake-openkakao-cli"
            fake.write_text(
                "#!/bin/sh\n"
                "cat <<'JSON'\n"
                '[{"chat_id": 99, "chat_type": 1, "title": "Study Club", "members": 5}]'
                "\nJSON\n",
                encoding="utf-8",
            )
            fake.chmod(0o700)
            updated = module.collect_menubar_model(
                state.resolve(), now=now, bin_path=fake
            )
            titles = {item["chat_id"]: item for item in updated["available_chats"]}
            self.assertEqual(titles[99]["title"], "Study Club")
            self.assertFalse(titles[99]["catalog"])
            self.assertEqual(titles[42]["title"], "Room")
            encoded = self._encoded(updated)
            for secret in FORBIDDEN:
                self.assertNotIn(secret, encoded)
            del helper

    def test_available_chats_marks_catalog_membership(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, _room, _queue, now, _model = self._model(
                Path(temporary)
            )
            fake = Path(temporary) / "fake-openkakao-cli"
            fake.write_text(
                "#!/bin/sh\n"
                "cat <<'JSON'\n"
                '[{"chat_id": 42, "chat_type": 1, "title": "Study Club", "members": 5}]'
                "\nJSON\n",
                encoding="utf-8",
            )
            fake.chmod(0o700)
            updated = module.collect_menubar_model(
                state.resolve(), now=now, bin_path=fake
            )
            self.assertTrue(updated["available_chats"][0]["catalog"])
            self.assertTrue(updated["available_chats"][0]["live"])
            remaining = module.delete_catalog_room(state.resolve(), 42)
            self.assertNotIn(42, [item["chat_id"] for item in remaining])
            del helper

    def test_swift_draws_pipeline_and_rooms(self):
        source = SWIFT.read_text(encoding="utf-8")
        panel = source[
            source.index("final class MenuPanelView"):
            source.index("final class CenteredLabelCell")
        ]
        self.assertIn("class JarvisCoreView", source)
        self.assertIn("let coreView = JarvisCoreView(frame: .zero)", panel)
        self.assertIn('NSUserInterfaceItemIdentifier("gear")', panel)
        self.assertIn("x: width - Self.panelInset - Self.gearSize", panel)
        self.assertIn("y: Self.panelInset", panel)
        self.assertIn("static let panelWidth: CGFloat = 276", panel)
        self.assertIn("static let panelBaseHeight: CGFloat = 260", panel)
        self.assertIn("static let coreSize: CGFloat = 236", panel)
        self.assertIn("static let gearSize: CGFloat = 28", panel)
        self.assertIn("coreView.activity = JarvisCoreView.activity(", panel)
        self.assertIn("JarvisCoreView.background(model, chatId:", panel)

        # The menu extra itself is only the Jarvis core plus the top-right gear.
        self.assertNotIn("tileButtons", panel)
        self.assertNotIn("tileClicked", panel)
        self.assertNotIn('"room-popup"', panel)
        self.assertNotIn("roomGridExtra", panel)
        self.assertNotIn("layoutRoomGrid", panel)
        self.assertNotIn("roomsExpanded", panel)
        self.assertNotIn("drawStatusRow", panel)
        self.assertNotIn("statusRowTop", panel)
        self.assertNotIn("LampCell", panel)
        self.assertNotIn("health-row", panel)
        self.assertNotIn("즉시 답장 보내기", panel)
        self.assertNotIn("긱뉴스 바로 전송", panel)
        self.assertNotIn("대량 검증", source)
        self.assertNotIn("기능 점검", source)
        self.assertNotIn("showImproveWindow", source)
        self.assertNotIn("showOnboarding", source)

        self.assertIn("self?.showUnifiedSettingsWindow()", source)
        self.assertIn("func ensureUnifiedSettingsWindow()", source)
        self.assertNotIn("func presentGearMenu()", source)
        self.assertIn("settingsRoomPopup", source)
        self.assertIn("settingsHealthLamps", source)
        self.assertIn("settingsSlotFields", source)
        self.assertIn("settingsAutoButton", source)
        self.assertIn("settingsGeekButton", source)

    def _check_codes(self, report):
        return [item["code"] for item in report["checks"]]

    def test_doctor_reports_processing_and_ax_without_secrets(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, room, _queue, now, _model = self._model(
                Path(temporary)
            )
            report = self._doctor(module, state.resolve(), now=now)
            encoded = json.dumps(report, ensure_ascii=False)
            self.assertEqual(report["privacy"], "content_redacted")
            self.assertEqual(report["action"], "doctor")
            self.assertIn("scheduled_waiting", self._check_codes(report))
            self.assertIn("release_scheduled", report["healable"])
            self.assertEqual(report["healed"], [])
            for item in report["checks"]:
                self.assertEqual(item["detail"], f"code={item['code']}")
            self.assertNotIn("Room", encoded)
            self.assertNotIn(str(state), encoded)
            for secret in FORBIDDEN:
                self.assertNotIn(secret, encoded)
            ax = json.loads((room / "apple-watch-status.json").read_text())
            ax["rows"] = 0
            helper._private_json(room / "apple-watch-status.json", ax)
            missing = self._doctor(module, state.resolve(), now=now)
            self.assertIn("ax_window_missing", self._check_codes(missing))
            self.assertEqual(
                next(
                    item
                    for item in missing["checks"]
                    if item["code"] == "ax_window_missing"
                )["level"],
                "warn",
            )
            del helper

    def test_doctor_heal_clears_stale_leftover_sidecar(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, room, queue, now, _model = self._model(
                Path(temporary)
            )
            connection = sqlite3.connect(queue)
            try:
                connection.execute("UPDATE reply_jobs SET status='skipped'")
                connection.commit()
            finally:
                connection.close()
            helper._private_json(
                room / "reply-state.json",
                {
                    "last_event": "db:42:99",
                    "delivery_state": "delivery_unknown",
                },
            )
            report = self._doctor(module, state.resolve(), now=now)
            self.assertIn("leftover_occupancy", self._check_codes(report))
            leftover = next(item for item in report["checks"] if item["code"] == "leftover_occupancy")
            self.assertEqual(leftover["level"], "warn")
            self.assertEqual(report["healable"], ["stale_leftover_sidecar"])
            healed = self._doctor(module, state.resolve(), now=now, heal=True)
            self.assertEqual(healed["action"], "doctor-heal")
            self.assertIn("stale_leftover_sidecar", healed["healed"])
            self.assertNotIn("leftover_occupancy", self._check_codes(healed))
            payload = json.loads(
                (room / "reply-state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(payload["last_event"], "db:42:99")
            self.assertEqual(payload["delivery_state"], "delivery_enabled")
            encoded = json.dumps(healed, ensure_ascii=False)
            self.assertNotIn("db:42:99", encoded)
            for secret in FORBIDDEN:
                self.assertNotIn(secret, encoded)
            del helper

    def test_doctor_does_not_heal_delivery_unknown_or_circuit(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, room, queue, now, _model = self._model(
                Path(temporary)
            )
            connection = sqlite3.connect(queue)
            try:
                connection.execute("UPDATE reply_jobs SET status='delivery_unknown'")
                connection.commit()
            finally:
                connection.close()
            unknown = self._doctor(module, state.resolve(), now=now, heal=True)
            self.assertIn("delivery_unknown", self._check_codes(unknown))
            self.assertNotIn("stale_leftover_sidecar", unknown["healed"])
            self.assertFalse(unknown["ok"])
            watchdog = json.loads(
                (state / "session-watchdog-status.json").read_text(encoding="utf-8")
            )
            watchdog["state"] = "circuit_open"
            watchdog["reason"] = "preflight_failed"
            helper._private_json(state / "session-watchdog-status.json", watchdog)
            supervisor = json.loads((room / "supervisor-status.json").read_text())
            supervisor["shutdown_state"] = "stopped_unclean"
            supervisor["fence_reason"] = "db_watch_exited"
            helper._private_json(room / "supervisor-status.json", supervisor)
            fenced = self._doctor(module, state.resolve(), now=now, heal=True)
            codes = self._check_codes(fenced)
            self.assertIn("circuit_open", codes)
            self.assertIn("preflight_failed", codes)
            self.assertIn("stopped_unclean", codes)
            self.assertIn("db_watch_exited", codes)
            self.assertEqual(fenced["level"], "red")
            self.assertFalse(fenced["ok"])
            self.assertEqual(fenced["healed"], [])
            encoded = json.dumps(fenced, ensure_ascii=False)
            for secret in FORBIDDEN:
                self.assertNotIn(secret, encoded)
            del helper

    def test_doctor_cli_always_exits_zero(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, _room, _queue, now, _model = self._model(
                Path(temporary)
            )
            import subprocess

            completed = subprocess.run(
                [
                    sys.executable,
                    str(MENUBAR),
                    "--state-root",
                    str(state.resolve()),
                    "--action",
                    "doctor",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            parsed = json.loads(completed.stdout)
            self.assertEqual(parsed["action"], "doctor")
            self.assertEqual(parsed["privacy"], "content_redacted")
            for secret in FORBIDDEN:
                self.assertNotIn(secret, completed.stdout)
            del helper, module, now

    def test_improve_actions_are_gone_from_the_core(self):
        """자가 개선 파이프라인은 사용자 요청으로 통째로 없앴다.

        예전에는 improve-prep이 남은 배달 불확실 작업을 정리하고
        improve-launch가 최신 베이크 세션을 띄웠다. 그 두 동작은 자가 점검
        창에서만 쓰였고, 그 창이 사라지면서 부를 곳이 없어졌다. 지금은
        같은 일을 하는 명령이 남아 있으면 안 된다 (2026-09-17).
        """
        source = MENUBAR.read_text(encoding="utf-8")
        self.assertNotIn("improve-prep", source)
        self.assertNotIn("improve-launch", source)
        self.assertNotIn("_improve_launch", source)
        self.assertNotIn("doctor-heal", source)
        with tempfile.TemporaryDirectory() as temporary:
            helper, _module, state, _room, _queue, _now, _model = self._model(
                Path(temporary)
            )
            # 없는 동작을 부르면 조용히 성공한 척하지 않는다. argparse가
            # 알 수 없는 선택지를 거절하고, 출력에 ok=true가 남지 않는다.
            output = os.popen(
                f"{sys.executable} {MENUBAR} "
                f"--state-root '{state.resolve()}' --action improve-prep 2>&1"
            ).read()
            self.assertNotIn('"ok": true', output)
            self.assertIn("improve-prep", output)
            del helper

    def test_doctor_health_map_is_closed_vocab_for_five_components(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, room, _queue, now, _model = self._model(
                Path(temporary)
            )
            report = self._doctor(module, state.resolve(), now=now)
            report["health"] = module._doctor_component_health(Path(state))
            health = report.get("health")
            self.assertIsInstance(health, dict)
            self.assertEqual(
                set(health),
                {"watchdog", "supervisor", "ax", "worker", "model"},
            )
            for value in health.values():
                self.assertIn(value, {"ok", "warn", "err", "off"})
            encoded = json.dumps(report, ensure_ascii=False)
            for secret in FORBIDDEN:
                self.assertNotIn(secret, encoded)
            del helper

    def test_models_catalog_is_standalone_and_lists_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, room, _queue, now, _model = self._model(
                Path(temporary)
            )
            global_yml = Path(temporary) / "global-models.yml"
            global_yml.write_text(
                "providers:\n"
                "  omlx:\n"
                "    models:\n"
                "      - id: Qwen3.6-35B-A3B-4bit\n"
                "        name: Qwen 4bit\n",
                encoding="utf-8",
            )
            app_dir = state / "gjc-agent"
            # The snapshot already creates the agent dir, so a plain mkdir
            # raced it and failed with FileExistsError (2026-09-16).
            app_dir.mkdir(mode=0o700, exist_ok=True)
            (app_dir / "models.yml").write_text(
                "providers:\n"
                "  omlx:\n"
                "    models:\n"
                "      - id: Qwen3.6-35B-A3B-8bit\n"
                "        name: Qwen 8bit\n",
                encoding="utf-8",
            )
            helper._private_json(
                state / "reply-model.json",
                {"schema_version": 1, "model": "omlx/Qwen3.6-35B-A3B-8bit"},
            )
            previous = os.environ.get("OPENKAKAO_GJC_GLOBAL_MODELS")
            os.environ["OPENKAKAO_GJC_GLOBAL_MODELS"] = str(global_yml)
            try:
                payload = module.collect_reply_models(state.resolve(), now=now)
            finally:
                if previous is None:
                    os.environ.pop("OPENKAKAO_GJC_GLOBAL_MODELS", None)
                else:
                    os.environ["OPENKAKAO_GJC_GLOBAL_MODELS"] = previous
            providers = {p["id"]: p for p in payload["providers"]}
            # oMLX provider and its models must be filtered out (retired).
            self.assertNotIn("omlx", providers)
            all_ids = [
                m["id"]
                for p in payload["providers"]
                for m in p.get("models", [])
            ]
            for mid in all_ids:
                self.assertFalse(
                    mid.startswith("omlx/"),
                    f"omlx model {mid} should have been filtered",
                )
            self.assertEqual(payload.get("privacy"), "content_redacted")
            encoded = json.dumps(payload, ensure_ascii=False)
            for secret in FORBIDDEN:
                self.assertNotIn(secret, encoded)
            del helper

    def test_doctor_enrichment_adds_session_and_model_without_secrets(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, room, _queue, now, _model = self._model(
                Path(temporary)
            )
            report = self._doctor(module, state.resolve(), now=now)
            codes = self._check_codes(report)
            self.assertTrue(
                "session_enrolled" in codes or "session_unenrolled" in codes
            )
            self.assertTrue(
                "reply_model_override" in codes or "reply_model_default" in codes
            )
            encoded = json.dumps(report, ensure_ascii=False)
            self.assertNotIn(str(state), encoded)
            for secret in FORBIDDEN:
                self.assertNotIn(secret, encoded)
            for item in report["checks"]:
                self.assertEqual(item["detail"], f"code={item['code']}")
                self.assertTrue(item["advice"])
                self.assertNotEqual(item["advice"], item["detail"])
            del helper


    def test_doctor_warns_when_live_runtime_is_older_than_newest_bake(self):
        module = load("auto_reply_menubar_stale_bake_doctor")
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state"
            state.mkdir(mode=0o700)
            (state / "enrollment.json").write_text(
                json.dumps(
                    {
                        "schema_version": 4,
                        "selectors": ["bind:1:kakao-test"],
                        "runtime_root": str(state / "runtime" / "old-bake"),
                        "created_at": "2026-08-25T00:00:00Z",
                        "targets": [],
                    }
                ),
                encoding="utf-8",
            )
            old = state / "runtime" / "old-bake"
            new = state / "runtime" / "new-bake"
            old.mkdir(parents=True)
            new.mkdir(parents=True)
            (old / "start-auto-reply-session.command").write_text(
                "#!/bin/sh\n", encoding="utf-8"
            )
            newer = new / "start-auto-reply-session.command"
            newer.write_text("#!/bin/sh\n", encoding="utf-8")
            os.utime(old / "start-auto-reply-session.command", (1, 1))
            os.utime(newer, (100, 100))
            report = module._enrich_doctor_report({"checks": []}, state)
            codes = [item["code"] for item in report["checks"]]
            self.assertIn("session_bake_stale", codes)
            encoded = json.dumps(report, ensure_ascii=False)
            self.assertNotIn(str(state), encoded)
            for secret in FORBIDDEN:
                self.assertNotIn(secret, encoded)

    def test_jobs_list_is_chronological_and_redacted(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, _room, queue, now, _model = self._model(
                Path(temporary)
            )
            connection = sqlite3.connect(queue)
            try:
                connection.execute(
                    """
                    INSERT INTO reply_jobs(
                        event_id, event_json, status, due_at, decision, reason,
                        category, reply, scheduled_delay_seconds, error_class,
                        created_at, updated_at, attempt_no
                    ) VALUES
                    (?, ?, 'sent', ?, 'reply', 'social_reply', 'social', ?, 0, '', ?, ?, 1),
                    (?, ?, 'skipped', ?, 'skip', 'direct_question', 'question', ?, 0, '', ?, ?, 1)
                    """,
                    (
                        "db:42:10", "{}", now, "reply-body-must-never-escape", now - 20, now - 10,
                        "db:42:11", "{}", now, "queue-body-must-never-escape", now - 5, now - 1,
                    ),
                )
                connection.commit()
            finally:
                connection.close()
            sent = module.collect_job_list(state.resolve(), None, status="sent", now=now)
            skipped = module.collect_job_list(state.resolve(), None, status="skipped", now=now)
            self.assertEqual(sent["privacy"], "content_redacted")
            self.assertEqual(sent["title"], "전송")
            self.assertGreaterEqual(sent["count"], 1)
            self.assertEqual(sent["jobs"][0]["status"], "sent")
            self.assertEqual(skipped["jobs"][-1]["status"], "skipped")
            encoded = json.dumps({"sent": sent, "skipped": skipped}, ensure_ascii=False)
            for secret in FORBIDDEN:
                self.assertNotIn(secret, encoded)
            self.assertNotIn("reply-body-must-never-escape", encoded)
            self.assertNotIn("queue-body-must-never-escape", encoded)
            self.assertRegex(sent["jobs"][0]["when"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
            self.assertIn("일상 답변", sent["jobs"][0]["detail"])
            connection = sqlite3.connect(queue)
            try:
                connection.execute(
                    """
                    INSERT INTO reply_jobs(
                        event_id, event_json, status, due_at, decision, reason,
                        category, reply, scheduled_delay_seconds, error_class,
                        created_at, updated_at, attempt_no
                    ) VALUES (?, ?, 'pending', ?, NULL, NULL, NULL, ?, 0, '', ?, ?, 0)
                    """,
                    (
                        "db:42:12",
                        json.dumps(
                            {
                                "author_nickname": "문승현",
                                "event_type": "local_db_message",
                                "message_type": 1,
                                "reply_authorized": True,
                                "message": "queue-body-must-never-escape",
                            },
                            ensure_ascii=False,
                        ),
                        now,
                        "reply-body-must-never-escape",
                        now - 2,
                        now - 2,
                    ),
                )
                connection.commit()
            finally:
                connection.close()
            opened = module.collect_job_list(state.resolve(), None, status="open", now=now)
            self.assertEqual(opened["privacy"], "content_redacted")
            pending = [job for job in opened["jobs"] if job["event_id"] == "db:42:12"]
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["detail"], "답장 · 문승현")
            self.assertNotIn("대기", pending[0]["detail"])
            encoded_pending = json.dumps(pending[0], ensure_ascii=False)
            self.assertNotIn("queue-body-must-never-escape", encoded_pending)
            self.assertNotIn("reply-body-must-never-escape", encoded_pending)
            model = module.collect_menubar_model(state.resolve(), None, now=now)
            encoded_model = json.dumps(model, ensure_ascii=False)
            for secret in FORBIDDEN:
                self.assertNotIn(secret, encoded_model)
            del helper

    def test_unknown_job_can_be_skipped_without_send(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, _room, queue, now, _model = self._model(
                Path(temporary)
            )
            connection = sqlite3.connect(queue)
            try:
                connection.execute("UPDATE reply_jobs SET status='delivery_unknown'")
                connection.commit()
                event_id = connection.execute("SELECT event_id FROM reply_jobs").fetchone()[0]
            finally:
                connection.close()
            skipped = module.apply_unknown_job_action(
                state.resolve(),
                None,
                action="jobs-skip",
                event_id=event_id,
                now=now,
            )
            self.assertTrue(skipped["ok"])
            self.assertEqual(skipped["reason"], "skipped")
            connection = sqlite3.connect(queue)
            try:
                status, reason = connection.execute(
                    "SELECT status, reason FROM reply_jobs WHERE event_id = ?",
                    (event_id,),
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(status, "skipped")
            self.assertEqual(reason, "operator_dismissed")
            encoded = json.dumps(skipped, ensure_ascii=False)
            for secret in FORBIDDEN:
                self.assertNotIn(secret, encoded)
            del helper

    def test_unknown_job_ack_without_confirm_does_not_mark_sent(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, _room, queue, now, _model = self._model(
                Path(temporary)
            )
            connection = sqlite3.connect(queue)
            try:
                connection.execute("UPDATE reply_jobs SET status='delivery_unknown'")
                connection.commit()
                event_id = connection.execute("SELECT event_id FROM reply_jobs").fetchone()[0]
            finally:
                connection.close()
            acked = module.apply_unknown_job_action(
                state.resolve(),
                None,
                action="jobs-ack",
                event_id=event_id,
                now=now,
            )
            self.assertFalse(acked["ok"])
            self.assertEqual(acked["reason"], "unconfirmed")
            connection = sqlite3.connect(queue)
            try:
                status = connection.execute(
                    "SELECT status FROM reply_jobs WHERE event_id = ?",
                    (event_id,),
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(status, "delivery_unknown")
            del helper

    def test_geeknews_now_includes_catalog_rooms(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, room, _queue, now, _model = self._model(
                Path(temporary)
            )
            module.upsert_catalog_room(
                state.resolve(),
                {"chat_id": 99, "auto_reply": False, "geeknews": True},
            )
            result = module.run_operator_action(
                state.resolve(), "geeknews-now", now=now
            )
            self.assertIn(99, result.get("targets") or result.get("rooms") or [])
            request = json.loads(
                (room / module.OPERATOR_REQUEST_NAME).read_text(encoding="utf-8")
            )
            self.assertEqual(request["action"], "geeknews-now")
            self.assertIn(99, request.get("targets") or [])
            extra = state / "rooms" / "99" / module.OPERATOR_REQUEST_NAME
            self.assertTrue(extra.is_file())
            encoded = json.dumps(result, ensure_ascii=False)
            for secret in FORBIDDEN:
                self.assertNotIn(secret, encoded)
            del helper

    def test_swift_tiles_open_job_window(self):
        source = SWIFT.read_text(encoding="utf-8")
        settings = source[
            source.index("func ensureUnifiedSettingsWindow()"):
            source.index("@objc func showRoomsWindow()")
        ]
        self.assertIn('"AI 모델 설정"', settings)
        self.assertIn("#selector(showModelSettingsWindow)", settings)
        self.assertIn('"채팅방 관리"', settings)
        self.assertIn("#selector(showRoomsWindow)", settings)
        self.assertIn('"답변 기록"', settings)
        self.assertIn("#selector(showLogWindow)", settings)
        self.assertIn('"지식 그래프"', settings)
        self.assertIn("#selector(showVectorWindow)", settings)
        self.assertIn('"작업 목록"', settings)
        self.assertIn("#selector(settingsJobClicked(_:))", settings)
        self.assertIn("showJobsWindow(status: Self.jobKinds[tag])", settings)
        self.assertIn('"즉시 답장 보내기"', settings)
        self.assertIn("#selector(instantAutoReplyClicked)", settings)
        self.assertIn('"긱뉴스 바로 전송"', settings)
        self.assertIn("#selector(instantGeekNewsClicked)", settings)
        self.assertIn('NSUserInterfaceItemIdentifier("settings-instant-auto")', settings)
        self.assertIn('NSUserInterfaceItemIdentifier("settings-instant-geek")', settings)
        self.assertIn(
            '[("morning", "아침"), ("lunch", "점심"), ("evening", "저녁")]',
            settings,
        )
        self.assertIn("--jobs-status", source)
        self.assertNotIn("tileClicked", source)
        self.assertNotIn("func presentGearMenu()", source)

        build = source[
            source.index("func buildMenu(_ model: MenubarModel)"):
            source.index("func applyImageReplyModelSelection")
        ]
        self.assertIn("menu.addItem(graphic)", build)
        self.assertNotIn("NSMenuItem(title:", build)

    def test_swift_unified_settings_empty_room_disables_send(self):
        source = SWIFT.read_text(encoding="utf-8")
        settings = source[
            source.index("func updateUnifiedSettingsWindow"):
            source.index("func traceOperatorSurface")
        ]
        self.assertIn("if rooms.isEmpty", settings)
        self.assertIn('popup.addItem(withTitle: "고를 방이 없습니다")', settings)
        self.assertIn("popup.isEnabled = false", settings)
        self.assertIn(
            "let canAuto = selected.map { $0.live && $0.auto_reply } ?? false",
            settings,
        )
        self.assertIn(
            "let canGeek = selected.map { $0.live && $0.geeknews } ?? false",
            settings,
        )
        self.assertIn("settingsAutoButton?.isEnabled = canAuto", settings)
        self.assertIn("settingsGeekButton?.isEnabled = canGeek", settings)
        room_choice = source[
            source.index("struct RoomChoice"):
            source.index("struct AvailableChat")
        ]
        self.assertIn("let auto_reply: Bool", room_choice)
        self.assertIn("let geeknews: Bool", room_choice)
        self.assertIn("auto_reply: room.auto_reply", source)
        self.assertIn("geeknews: room.geeknews", source)
        self.assertIn("auto_reply: chat.auto_reply", source)
        self.assertIn("geeknews: chat.geeknews", source)
        self.assertIn("등록된 채팅방이 없어 바로 실행을 사용할 수 없습니다.", settings)

    def test_swift_unified_settings_rejects_bad_job_tag(self):
        source = SWIFT.read_text(encoding="utf-8")
        handler = source[
            source.index("@objc func settingsJobClicked"):
            source.index("func updateUnifiedSettingsWindow")
        ]
        self.assertIn("guard tag >= 0, tag < Self.jobKinds.count else", handler)
        self.assertIn('traceOperatorSurface("settings-job invalid tag=\\(tag)")', handler)
        self.assertIn("showJobsWindow(status: Self.jobKinds[tag])", handler)

    def test_swift_operator_surface_failure_logs_are_payload_free(self):
        source = SWIFT.read_text(encoding="utf-8")
        result = source[
            source.index("func presentOperatorResult"):
            source.index("func alertOperator")
        ]
        self.assertIn("guard let data else", result)
        self.assertIn("operator action failed without response", result)
        self.assertIn("operator action returned invalid response", result)

        trace = source[
            source.index("func traceOperatorSurface"):
            source.index("@objc func showRoomsWindow")
        ]
        self.assertIn(".prefix(240)", trace)
        self.assertIn('replacingOccurrences(of: "\\n", with: " ")', trace)
        self.assertNotIn("JSONSerialization", trace)
        self.assertNotIn("api_key", trace.lower())
        self.assertNotIn("token", trace.lower())

        action = source[
            source.index("func runOperatorAction"):
            source.index("func presentOperatorResult")
        ]
        self.assertIn('var extra = ["--action", action]', action)
        self.assertIn('["--chat-id", String(chatId)]', action)
        self.assertNotIn("--message", action)
        self.assertNotIn("--api-key", action)

    def test_vector_crud_roundtrip_on_temp_db(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, _state, _room, _queue, _now, _model = self._model(
                Path(temporary)
            )
            db = Path(temporary) / "context.sqlite3"
            created = module.upsert_vector_row(
                db,
                {
                    "chat": "부자멘토멘티",
                    "user_name": "최연우",
                    "message": "기억 테스트 문장",
                    "date": "2026-08-20 20:30:00",
                },
            )
            self.assertTrue(created["ok"])
            self.assertEqual(created["origin_label"], "직접 추가")
            self.assertEqual(created["count"], 1)
            listed = module.collect_vector_list(db, query="기억", chat="부자멘토멘티")
            self.assertEqual(listed["count"], 1)
            self.assertEqual(listed["rows"][0]["user_name"], "최연우")
            self.assertNotIn("queue-body-must-never-escape", json.dumps(listed, ensure_ascii=False))
            updated = module.upsert_vector_row(
                db,
                {
                    "id": created["id"],
                    "chat": "부자멘토멘티",
                    "user_name": "문승현",
                    "message": "수정한 기억",
                    "date": "2026-08-20 20:31:00",
                },
            )
            self.assertEqual(updated["rows"][0]["user_name"], "문승현")
            deleted = module.delete_vector_row(db, created["id"], chat="부자멘토멘티")
            self.assertEqual(deleted["count"], 0)
            del helper

    def test_vector_cli_uses_explicit_db(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, _room, _queue, _now, _model = self._model(
                Path(temporary)
            )
            db = Path(temporary) / "context.sqlite3"
            module.upsert_vector_row(
                db,
                {
                    "chat": "부자멘토멘티",
                    "user_name": "현준",
                    "message": "cli 기억",
                    "date": "2026-08-20 20:40:00",
                },
            )
            import subprocess

            completed = subprocess.run(
                [
                    sys.executable,
                    str(MENUBAR),
                    "--state-root",
                    str(state.resolve()),
                    "--vector-db",
                    str(db),
                    "--action",
                    "vector-list",
                    "--vector-chat",
                    "부자멘토멘티",
                    "--vector-query",
                    "cli",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            parsed = json.loads(completed.stdout)
            self.assertEqual(parsed["action"], "vector-list")
            self.assertEqual(parsed["count"], 1)
            self.assertEqual(parsed["rows"][0]["user_name"], "현준")
            del helper

    def test_swift_has_korean_vector_memory_window(self):
        source = SWIFT.read_text(encoding="utf-8")
        # 창 이름은 지식 그래프로 바뀌었고, 제목은 코어가 아니라 창이 정한다.
        # 메뉴 패널의 항목은 톱니바퀴 안으로 들어갔다 (2026-09-17).
        self.assertIn('("지식 그래프…", #selector(showVectorWindow))', source)
        self.assertIn('window.title = "지식 그래프 (대화 기억)"', source)
        self.assertIn("showVectorWindow", source)
        self.assertIn("--vector-upsert", source)
        self.assertIn("--vector-delete", source)
        self.assertIn("presentOperatorWindow", source)
        self.assertIn("makeKeyAndOrderFront", source)
        self.assertIn("cancelTracking", source)
        self.assertIn("DispatchQueue.global", source)
        self.assertIn("vector_memory", source)
        self.assertIn("방금 갱신", source)
        self.assertIn("최연우 기억", source)
        self.assertIn("menuWillOpen", source)
        self.assertIn("menuTracking", source)
        self.assertIn("reusedLabel", source)
        self.assertIn("--vector-source", source)
        self.assertIn('"최연우 기억", "모든 대화"', source)
        self.assertIn('"최연우 기억", "모든 대화", "주제별 지식", "설명 자료", "답장 기록", "말투·반응 통계", "탐색 프롬프트"', source)
        self.assertIn("--vector-topic", source)
        self.assertIn("vectorTopicsField", source)
        self.assertIn("vectorTopicChanged", source)
        self.assertIn("주제별 지식", source)
        self.assertIn("답장 기록", source)
        self.assertIn("말투·반응 통계", source)
        self.assertIn("탐색 프롬프트", source)
        self.assertIn("설명 자료", source)
        self.assertIn("references", source)
        self.assertIn("vectorRestoreClicked", source)
        self.assertIn("restore_prompts", source)
        self.assertIn("검색된 대화 기억과 함께", source)
        self.assertIn('("topics", "주제"', source)
        self.assertNotIn("Open TUI", source)
        self.assertNotIn("VectorDB", source)
        run_python = source[source.find("func runPython") :]
        wait_at = run_python.find("waitUntilExit")
        read_at = run_python.find("readDataToEndOfFile")
        self.assertGreater(wait_at, 0)
        self.assertGreater(read_at, 0)
        self.assertLess(read_at, wait_at)
        self.assertIn("windowShouldClose", source)
        self.assertIn("orderOut(nil)", source)
        self.assertIn("vectorEmbeddingField", source)
        self.assertIn("selectedVectorChat", source)
        self.assertIn('extra.contains("vector-list") ? (extra.contains("references") ? 45 : 20) : 8', source)
        self.assertIn("원문에서 만든 128차원 해시 벡터", source)
        self.assertIn("원문에서 128차원 해시 임베딩을 다시 만듭니다", source)
        self.assertNotIn(
            "vectorChatField?.stringValue = item.chat", source
        )
        date_at = source.find('("date", "시각"')
        self.assertGreater(date_at, 0)
        date_cols = source[date_at : date_at + 700]
        # 시각은 숫자라 가운데, 글자 열은 왼쪽이다. 두 정렬이 같은 호출에서
        # 정해지는지 본다 (2026-09-16).
        self.assertIn("alignment: textColumn ? .left : .center", date_cols)

    def test_vector_list_all_chats_and_offset(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, _state, _room, _queue, _now, _model = self._model(
                Path(temporary)
            )
            db = Path(temporary) / "context.sqlite3"
            module.upsert_vector_row(
                db,
                {
                    "chat": "부자멘토멘티",
                    "user_name": "최연우",
                    "message": "첫번째 방 기억",
                    "date": "2026-08-20 10:00:00",
                },
            )
            module.upsert_vector_row(
                db,
                {
                    "chat": "다른방",
                    "user_name": "현준",
                    "message": "두번째 방 기억",
                    "date": "2026-08-20 10:01:00",
                },
            )
            listed = module.collect_vector_list(db)
            self.assertEqual(listed["total"], 2)
            self.assertEqual(listed["chat"], "")
            self.assertEqual(listed["offset"], 0)
            self.assertEqual({row["chat"] for row in listed["rows"]}, {"부자멘토멘티", "다른방"})
            page = module.collect_vector_list(db, limit=1, offset=0)
            self.assertEqual(page["count"], 1)
            self.assertTrue(page["truncated"])
            page2 = module.collect_vector_list(db, limit=1, offset=1)
            self.assertEqual(page2["count"], 1)
            self.assertFalse(page2["truncated"])
            self.assertEqual(
                {page["rows"][0]["id"], page2["rows"][0]["id"]},
                {row["id"] for row in listed["rows"]},
            )
            filtered = module.collect_vector_list(db, chat="다른방")
            self.assertEqual(filtered["total"], 1)
            self.assertEqual(filtered["rows"][0]["user_name"], "현준")
            self.assertEqual(listed["memory"]["ok"], True)
            self.assertEqual(listed["memory"]["total"], 2)
            self.assertGreaterEqual(listed["memory"]["style_total"], 1)
            del helper

    def test_vector_status_tracks_choi_memory_without_bodies(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, _room, _queue, now, _model = self._model(
                Path(temporary)
            )
            missing = module.collect_vector_status(Path(temporary) / "missing.sqlite3")
            self.assertFalse(missing["ok"])
            self.assertEqual(missing["state"], "unavailable")
            db = Path(temporary) / "context.sqlite3"
            created = module.upsert_vector_row(
                db,
                {
                    "chat": "부자멘토멘티",
                    "user_name": "최연우",
                    "message": "상태 확인용 기억",
                    "date": "2026-08-20 15:43:30",
                },
            )
            self.assertGreater(created["id"], 0)
            status = module.collect_vector_status(db, now=now)
            self.assertTrue(status["ok"])
            self.assertIn(status["state"], {"live", "idle", "stale"})
            self.assertEqual(status["total"], 1)
            self.assertEqual(status["style_total"], 1)
            self.assertEqual(status["last_user"], "최연우")
            self.assertEqual(status["last_date"], "2026-08-20 15:43:30")
            encoded = json.dumps(status, ensure_ascii=False)
            self.assertNotIn("상태 확인용 기억", encoded)
            import subprocess

            completed = subprocess.run(
                [
                    sys.executable,
                    str(MENUBAR),
                    "--state-root",
                    str(state.resolve()),
                    "--vector-db",
                    str(db),
                    "--action",
                    "vector-status",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            parsed = json.loads(completed.stdout)
            self.assertEqual(parsed["action"], "vector-status")
            self.assertEqual(parsed["total"], 1)
            self.assertNotIn("상태 확인용 기억", completed.stdout)
            snapshot = subprocess.run(
                [
                    sys.executable,
                    str(MENUBAR),
                    "--state-root",
                    str(state.resolve()),
                    "--vector-db",
                    str(db),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(snapshot.returncode, 0, snapshot.stderr)
            snap = json.loads(snapshot.stdout)
            self.assertIn("vector_memory", snap)
            self.assertEqual(snap["vector_memory"]["total"], 1)
            self.assertEqual(snap["privacy"], "content_redacted")
            self.assertNotIn("상태 확인용 기억", snapshot.stdout)
            for secret in FORBIDDEN:
                self.assertNotIn(secret, snapshot.stdout)
            del helper


    def test_vector_style_source_lists_choi_memory(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, _state, _room, _queue, _now, _model = self._model(
                Path(temporary)
            )
            db = Path(temporary) / "context.sqlite3"
            module.upsert_vector_row(
                db,
                {
                    "chat": "부자멘토멘티",
                    "user_name": "최연우",
                    "message": "스타일 기억",
                    "date": "2026-08-20 15:43:30",
                },
            )
            module.upsert_vector_row(
                db,
                {
                    "chat": "부자멘토멘티",
                    "user_name": "현준",
                    "message": "다른 사람 기억",
                    "date": "2026-08-20 15:44:00",
                },
            )
            listed = module.collect_vector_list(db, source="style")
            self.assertEqual(listed["source"], "style")
            self.assertEqual(listed["count"], 1)
            self.assertEqual(listed["rows"][0]["user_name"], "최연우")
            self.assertEqual(listed["rows"][0]["message"], "스타일 기억")
            self.assertEqual(listed["rows"][0]["vector_dim"], module.VECTOR_DIM)
            self.assertTrue(
                listed["rows"][0]["vector_preview"].startswith("128차원")
            )
            everyone = module.collect_vector_list(db, source="messages")
            self.assertEqual(everyone["source"], "messages")
            self.assertEqual(everyone["total"], 2)
            del helper

    def test_vector_list_exposes_hashed_embedding_preview(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, _state, _room, _queue, _now, _model = self._model(
                Path(temporary)
            )
            db = Path(temporary) / "context.sqlite3"
            module.upsert_vector_row(
                db,
                {
                    "chat": "부자멘토멘티",
                    "user_name": "최연우",
                    "message": "임베딩 미리보기",
                    "date": "2026-08-20 17:25:22",
                },
            )
            listed = module.collect_vector_list(db, source="messages")
            row = listed["rows"][0]
            self.assertEqual(row["message"], "임베딩 미리보기")
            self.assertEqual(row["vector_dim"], module.VECTOR_DIM)
            self.assertIn("차원 [", row["vector_preview"])
            encoded = module._encode_vector("임베딩 미리보기")
            decoded = module._decode_vector(encoded)
            self.assertEqual(len(decoded), module.VECTOR_DIM)
            self.assertAlmostEqual(sum(value * value for value in decoded), 1.0, places=5)
            del helper

    def test_vector_list_skips_blob_table_count(self):
        source = MENUBAR.read_text(encoding="utf-8")
        fn = source[
            source.find("def collect_vector_list") : source.find("def upsert_vector_row")
        ]
        self.assertNotIn("count_sql", fn)
        self.assertNotIn("SELECT COUNT", fn)
        self.assertIn("m.vector", fn)
        self.assertIn(", vector", fn)

    def test_vector_topics_and_other_stores_are_handleable(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, _state, _room, _queue, _now, _model = self._model(
                Path(temporary)
            )
            db = Path(temporary) / "context.sqlite3"
            created = module.upsert_vector_row(
                db,
                {
                    "chat": "부자멘토멘티",
                    "user_name": "최연우",
                    "message": "이더리움 50개. 진입 평단 260만",
                    "date": "2026-08-20 17:01:52",
                },
            )
            self.assertIn("coins", created["topics"])
            self.assertEqual(created["topics_label"], "코인")
            listed = module.collect_vector_list(db, source="messages")
            self.assertEqual(listed["rows"][0]["topics"], ["coins"])
            summaries = module.collect_vector_list(db, source="topics")
            self.assertEqual(summaries["source"], "topics")
            self.assertEqual(summaries["count"], 1)
            self.assertEqual(summaries["rows"][0]["kind"], "topic")
            self.assertEqual(summaries["rows"][0]["row_key"], "coins")
            self.assertFalse(summaries["rows"][0]["editable"])
            self.assertEqual(summaries["topics"][0]["label"], "코인")
            tagged = module.collect_vector_list(db, source="topics", topic="코인")
            self.assertEqual(tagged["topic"], "coins")
            self.assertEqual(tagged["count"], 1)
            self.assertEqual(tagged["rows"][0]["message"], "이더리움 50개. 진입 평단 260만")
            retagged = module.upsert_vector_row(
                db,
                {
                    "id": created["id"],
                    "chat": "부자멘토멘티",
                    "user_name": "최연우",
                    "message": "사업자등록증 발급",
                    "date": "2026-08-20 18:14:37",
                    "topics": "사업, 주식",
                },
            )
            self.assertEqual(retagged["topics"], ["business", "stocks"])
            cleared = module.upsert_vector_row(
                db,
                {
                    "id": created["id"],
                    "chat": "부자멘토멘티",
                    "user_name": "최연우",
                    "message": "사업자등록증 발급",
                    "date": "2026-08-20 18:14:37",
                    "topics": "없음",
                },
            )
            self.assertEqual(cleared["topics"], [])
            connection = module._open_context_db(db, create=False, writable=True)
            connection.execute(
                """
                CREATE TABLE reply_decisions(
                    event_id TEXT PRIMARY KEY,
                    chat TEXT NOT NULL,
                    author TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    message TEXT NOT NULL,
                    vector BLOB NOT NULL,
                    decision TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    category TEXT NOT NULL,
                    context_match_count INTEGER NOT NULL DEFAULT 0,
                    style_match_count INTEGER NOT NULL DEFAULT 0,
                    best_context_score REAL NOT NULL DEFAULT 0,
                    best_style_score REAL NOT NULL DEFAULT 0,
                    prior_similarity REAL NOT NULL DEFAULT 0,
                    scheduled_delay_seconds REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    reply TEXT,
                    sent_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    evidence_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            connection.execute(
                """
                INSERT INTO reply_decisions(
                    event_id, chat, author, received_at, message, vector,
                    decision, reason, category, status, reply, created_at, updated_at,
                    evidence_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "db:1:2",
                    "부자멘토멘티",
                    "현준",
                    "2026-08-20 19:00:00",
                    "코인 어떻게 봐요",
                    b"",
                    "skip",
                    "self_author",
                    "policy",
                    "skipped",
                    "",
                    "2026-08-20 19:00:01",
                    "2026-08-20 19:00:01",
                    "{\"path\": \"/Users/twoimo/secret.sqlite3\"}",
                ),
            )
            connection.execute(
                """
                CREATE TABLE choi_yeonwoo_style_profile(
                    chat TEXT NOT NULL,
                    source TEXT NOT NULL,
                    user_name TEXT NOT NULL,
                    sample_count INTEGER NOT NULL,
                    average_character_length REAL NOT NULL,
                    median_character_length REAL NOT NULL,
                    p90_character_length REAL NOT NULL,
                    casual_ending_count INTEGER NOT NULL,
                    question_count INTEGER NOT NULL,
                    emoji_count INTEGER NOT NULL,
                    punctuation_count INTEGER NOT NULL,
                    policy_version TEXT NOT NULL DEFAULT ''
                )
                """
            )
            connection.execute(
                """
                INSERT INTO choi_yeonwoo_style_profile(
                    chat, source, user_name, sample_count,
                    average_character_length, median_character_length,
                    p90_character_length, casual_ending_count,
                    question_count, emoji_count, punctuation_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "부자멘토멘티",
                    "/Users/twoimo/Documents/secret.csv",
                    "최연우",
                    12,
                    11.0,
                    10.0,
                    20.0,
                    1,
                    2,
                    0,
                    3,
                ),
            )
            connection.commit()
            connection.close()
            replies = module.collect_vector_list(db, source="replies")
            self.assertEqual(replies["count"], 1)
            self.assertEqual(replies["rows"][0]["kind"], "reply")
            self.assertEqual(replies["rows"][0]["row_key"], "db:1:2")
            self.assertEqual(replies["rows"][0]["decision_label"], "건너뜀")
            self.assertEqual(replies["rows"][0]["reason_label"], "내가 보낸 말")
            encoded = json.dumps(replies, ensure_ascii=False)
            self.assertNotIn("/Users/twoimo/secret.sqlite3", encoded)
            self.assertNotIn("evidence_json", encoded)
            deleted = module.delete_vector_row(
                db, 0, source="replies", key="db:1:2"
            )
            self.assertEqual(deleted["count"], 0)
            profiles = module.collect_vector_list(db, source="profiles")
            self.assertGreaterEqual(profiles["count"], 1)
            self.assertEqual(profiles["rows"][0]["origin_label"], "말투 통계")
            self.assertNotIn("/Users/twoimo/Documents/secret.csv", json.dumps(profiles))
            with self.assertRaises(module.MenubarError):
                module.delete_vector_row(db, 1, source="profiles")
            del helper


    def test_vector_prompts_are_crudable(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, _state, _room, _queue, _now, _model = self._model(
                Path(temporary)
            )
            db = Path(temporary) / "context.sqlite3"
            listed = module.collect_vector_list(db, source="prompts")
            self.assertEqual(listed["source"], "prompts")
            self.assertGreaterEqual(listed["count"], 2)
            keys = {row["row_key"] for row in listed["rows"]}
            self.assertIn("system.reply", keys)
            system = next(row for row in listed["rows"] if row["row_key"] == "system.reply")
            self.assertEqual(system["kind"], "prompt")
            self.assertEqual(system["topics"], ["system"])
            self.assertTrue(system["editable"])
            self.assertFalse(system["deletable"])
            self.assertIn("untrusted data", system["message"])
            created = module.upsert_vector_row(
                db,
                {
                    "source": "prompts",
                    "user_name": "검색 강조",
                    "message": "Use context_evidence before guessing.",
                    "topics": "지시",
                    "chat": "사용",
                },
            )
            self.assertEqual(created["source"], "prompts")
            self.assertGreater(created["id"], 0)
            listed = module.collect_vector_list(db, source="prompts", query="검색 강조")
            self.assertEqual(listed["count"], 1)
            self.assertEqual(listed["rows"][0]["user_name"], "검색 강조")
            self.assertTrue(listed["rows"][0]["deletable"])
            updated = module.upsert_vector_row(
                db,
                {
                    "source": "prompts",
                    "id": created["id"],
                    "user_name": "검색 강조",
                    "message": "Prefer retrieved facts over invention.",
                    "topics": "instruction",
                    "chat": "끄기",
                },
            )
            disabled = next(row for row in updated["rows"] if row["id"] == created["id"])
            self.assertEqual(disabled["chat"], "끄기")
            self.assertEqual(disabled["message"], "Prefer retrieved facts over invention.")
            deleted = module.delete_vector_row(
                db, created["id"], source="prompts"
            )
            remaining = [row["id"] for row in deleted["rows"]]
            self.assertNotIn(created["id"], remaining)
            with self.assertRaises(module.MenubarError):
                module.delete_vector_row(db, system["id"], source="prompts")
            restored = module.upsert_vector_row(
                db, {"source": "prompts", "restore_prompts": True}
            )
            restored_keys = {row["row_key"] for row in restored["rows"]}
            self.assertIn("system.reply", restored_keys)
            del helper

    def test_swift_pipeline_idle_is_colorless(self):
        source = SWIFT.read_text(encoding="utf-8")
        self.assertIn('case "idle": return NSColor.tertiaryLabelColor', source)
        self.assertIn("let fill = Palette.stage(state)", source)
        self.assertNotIn('state == "idle" ? overall', source)
        self.assertIn('("chat", "채팅방"', source)
        self.assertIn("비우면 모든 채팅방", source)
        self.assertIn("vectorPrevClicked", source)
        self.assertIn("vectorNextClicked", source)
        self.assertIn("--vector-offset", source)
        self.assertIn("lastApplySignature", source)
        self.assertIn("applicationWillTerminate", source)
        self.assertIn("FileHandle.nullDevice", source)
        self.assertIn("menuTracking", source)
        self.assertNotIn("let pythonLock = NSLock()", source)
        self.assertIn("applyJobs", source)
        # 자가 점검 창은 사용자 요청으로 완전히 없앴다. 그 창이 쓰던
        # applyDoctor도 함께 사라졌다 (2026-09-17).
        self.assertNotIn("applyDoctor", source)
        self.assertNotIn("ensureDoctorWindow", source)
        self.assertNotIn("showDoctorWindow", source)

    def test_vector_paths_skip_overlay_and_cache_status(self):
        module = load(f"auto_reply_menubar_lazy_{id(self)}")
        self.assertIsNone(module._OVERLAY_NS)
        missing = module.collect_vector_status(Path("/tmp/auto-reply-missing-status.sqlite3"))
        self.assertFalse(missing["ok"])
        self.assertIsNone(module._OVERLAY_NS)
        with tempfile.TemporaryDirectory() as temporary:
            helper, loaded, _state, _room, _queue, now, _model = self._model(
                Path(temporary)
            )
            db = Path(temporary) / "context.sqlite3"
            loaded.upsert_vector_row(
                db,
                {
                    "chat": "부자멘토멘티",
                    "user_name": "최연우",
                    "message": "캐시 확인용 기억",
                    "date": "2026-08-20 15:43:30",
                },
            )
            status = loaded.collect_vector_status(db, now=now)
            self.assertEqual(status["total"], 1)
            cache = db.with_name(db.name + ".menubar-status.json")
            self.assertTrue(cache.is_file())
            payload = json.loads(cache.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"]["total"], 1)
            self.assertNotIn("캐시 확인용 기억", cache.read_text(encoding="utf-8"))
            again = loaded.collect_vector_status(db, now=now)
            self.assertEqual(again["total"], 1)
            del helper

    def test_gjc_list_models_parser_groups_providers(self):
        module = load("auto_reply_menubar_models_parse")
        text = (
            "Canonical models\n"
            "canonical                             selected                                        variants  context  max-out\n"
            "gemini-3.7-flash-tiered               google-antigravity/gemini-3.7-flash-tiered      1         1M       66K\n"
            "claude-4-sonnet                       cursor/claude-4-sonnet                          2         200K     64K\n"
            "\n"
            "google-antigravity  gemini-3.6-flash-tiered             1M       66K      minimal,low,medium,high        yes\n"
        )
        parsed = module.parse_gjc_list_models(text)
        ids = {item["id"] for item in parsed}
        self.assertIn("google-antigravity/gemini-3.7-flash-tiered", ids)
        self.assertIn("cursor/claude-4-sonnet", ids)
        self.assertIn("google-antigravity/gemini-3.6-flash-tiered", ids)

    def test_attach_reply_model_uses_stale_catalog_without_fetch(self):
        module = load("auto_reply_menubar_stale_catalog")
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            now = 1_000.0
            module._atomic_write_json(
                state / "gjc-model-catalog.json",
                {
                    "schema_version": 1,
                    "updated_at": int(now - module.GJC_MODEL_CACHE_TTL_SECONDS - 10),
                    "models": [
                        {
                            "id": "google-antigravity/gemini-3.7-flash-tiered",
                            "canonical": "gemini-3.7-flash-tiered",
                            "provider": "google-antigravity",
                            "label": "gemini-3.7-flash-tiered",
                        }
                    ],
                },
            )
            called = {"n": 0}

            def boom():
                called["n"] += 1
                raise module.MenubarError("model_catalog_unavailable")

            models = module._load_gjc_model_catalog(
                state, now=now, refresh=False, fetcher=boom, allow_fetch=False
            )
            self.assertEqual(called["n"], 0)
            self.assertEqual(models[0]["id"], "google-antigravity/gemini-3.7-flash-tiered")
            payload = module.attach_reply_model({"privacy": "content_redacted"}, state, now=now)
            self.assertEqual(
                payload["reply_model"]["id"],
                "google-antigravity/gemini-3.7-flash-tiered",
            )
            self.assertEqual(payload["reply_model_providers"][0]["id"], "google-antigravity")

    def test_menubar_model_set_persists_override_without_bodies(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            helper, module, state, room, queue, now, model = self._model(root)
            module._atomic_write_json(
                state / "gjc-model-catalog.json",
                {
                    "schema_version": 1,
                    "updated_at": int(now),
                    "models": [
                        {
                            "id": "google-antigravity/gemini-3.7-flash-tiered",
                            "canonical": "gemini-3.7-flash-tiered",
                            "provider": "google-antigravity",
                            "label": "gemini-3.7-flash-tiered",
                        },
                        {
                            "id": "google-antigravity/gemini-3.6-flash-tiered",
                            "canonical": "gemini-3.6-flash-tiered",
                            "provider": "google-antigravity",
                            "label": "gemini-3.6-flash-tiered",
                        },
                    ],
                },
            )
            result = module.set_reply_model(
                state.resolve(),
                "google-antigravity/gemini-3.6-flash-tiered",
                now=now,
            )
            self.assertTrue(result["ok"])
            self.assertEqual(
                result["model"], "google-antigravity/gemini-3.6-flash-tiered"
            )
            payload = module.attach_reply_model({"privacy": "content_redacted"}, state.resolve(), now=now)
            self.assertEqual(
                payload["reply_model"]["id"],
                "google-antigravity/gemini-3.6-flash-tiered",
            )
            self.assertEqual(payload["reply_model"]["source"], "override")
            self.assertEqual(payload["reply_model_providers"][0]["id"], "google-antigravity")
            encoded = json.dumps(payload, ensure_ascii=False)
            for secret in FORBIDDEN:
                self.assertNotIn(secret, encoded)
            denied = module.set_reply_model(
                state.resolve(),
                "not-a-real/model",
                now=now,
            )
            self.assertFalse(denied["ok"])
            del helper

    def test_worker_uses_reply_model_override(self):
        worker_path = SCRIPTS / "auto-reply-worker.py"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            room = root / "rooms" / "1"
            room.mkdir(parents=True)
            state = room / "reply-state.json"
            state.write_text("{}", encoding="utf-8")
            (root / "reply-model.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "model": "google-antigravity/gemini-3.6-flash-tiered",
                        "updated_at": 1,
                    }
                ),
                encoding="utf-8",
            )
            os.environ["OPENKAKAO_REPLY_STATE"] = str(state)
            os.environ["OPENKAKAO_REPLY_MODEL"] = (
                "google-antigravity/gemini-3.7-flash-tiered"
            )
            spec = importlib.util.spec_from_file_location(
                "auto_reply_model_override", worker_path
            )
            worker = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(worker)
            self.assertEqual(
                worker._active_reply_model(),
                "google-antigravity/gemini-3.6-flash-tiered",
            )

    def test_menubar_model_set_uses_stale_catalog_without_fetch(self):
        module = load("auto_reply_menubar_model_set_stale")
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            now = 1_000.0
            catalog = [
                {
                    "id": "google-antigravity/gemini-3.7-flash-tiered",
                    "canonical": "gemini-3.7-flash-tiered",
                    "provider": "google-antigravity",
                    "label": "gemini-3.7-flash-tiered",
                },
                {
                    "id": "google-antigravity/gemini-3.6-flash-tiered",
                    "canonical": "gemini-3.6-flash-tiered",
                    "provider": "google-antigravity",
                    "label": "gemini-3.6-flash-tiered",
                },
            ]
            module._atomic_write_json(
                state / "gjc-model-catalog.json",
                {
                    "schema_version": 1,
                    "updated_at": int(now - module.GJC_MODEL_CACHE_TTL_SECONDS - 10),
                    "models": catalog,
                },
            )
            called = {"n": 0}

            def boom():
                called["n"] += 1
                raise module.MenubarError("model_catalog_unavailable")

            result = module.set_reply_model(
                state.resolve(),
                "google-antigravity/gemini-3.6-flash-tiered",
                now=now,
                fetcher=boom,
            )
            self.assertTrue(result["ok"])
            self.assertEqual(called["n"], 0)
            self.assertEqual(
                result["model"], "google-antigravity/gemini-3.6-flash-tiered"
            )
            denied = module.set_reply_model(
                state.resolve(),
                "not-a-real/model",
                now=now,
                fetcher=boom,
            )
            self.assertFalse(denied["ok"])
            self.assertEqual(denied["reason"], "model_not_in_catalog")
            self.assertEqual(called["n"], 0)
            encoded = json.dumps(result, ensure_ascii=False)
            for secret in FORBIDDEN:
                self.assertNotIn(secret, encoded)

    def test_swift_menu_applies_model_before_python_roundtrip(self):
        source = SWIFT.read_text(encoding="utf-8")
        clicked = source.split("@objc func modelClicked", 1)[1].split("@objc func reloadModelsClicked", 1)[0]
        self.assertLess(
            clicked.find("applyReplyModelSelection"),
            clicked.find("runPython"),
        )
        self.assertIn('timeout: 8', clicked)
        self.assertNotIn("self?.refresh()", clicked)
        self.assertIn("snapshotLag", source)
        wrapper = MENUBAR.read_text(encoding="utf-8")
        # The fast-path dispatch must cover all three model action families
        # before the frozen main runs; the source wraps the condition across
        # lines, so assert on the fragments instead of one exact line.
        self.assertIn("args.action in MODEL_ACTIONS", wrapper)
        self.assertIn("or args.action in PROVIDER_ACTIONS", wrapper)
        self.assertIn("or args.action in IMAGE_MODEL_ACTIONS", wrapper)
        self.assertIn("allow_fetch=False", wrapper.split("def set_reply_model", 1)[1].split("def collect_reply_models", 1)[0])

    def test_swift_menu_decodes_reply_model_fields(self):
        source = SWIFT.read_text(encoding="utf-8")
        self.assertIn("struct ReplyModelSelection", source)
        self.assertIn("struct ModelsReport", source)
        self.assertIn("func loadModelCatalog", source)
        self.assertIn("모델 목록 불러오는 중", source)
        self.assertIn("--action\", \"model-set\"", source)
        self.assertIn("statusItem.menu = buildMenu(model)", source)
        self.assertIn("guard menu === statusItem.menu", source)

    def test_image_reply_model_defaults_to_gemini_flash_and_persists(self):
        module = load("auto_reply_menubar_image_model")
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            now = 1_000.0
            agent_dir = state / "gjc-agent"
            agent_dir.mkdir(mode=0o700, exist_ok=True)
            (agent_dir / "models.yml").write_text(
                "providers:\n"
                "  google-antigravity:\n"
                "    models:\n"
                "      - id: gemini-3.7-flash-tiered\n"
                "      - id: gemini-3.6-flash-tiered\n",
                encoding="utf-8",
            )
            image_id, source = module._read_image_reply_model(state)
            self.assertEqual(image_id, "google-antigravity/gemini-3.7-flash-tiered")
            self.assertEqual(source, "default")
            result = module.set_image_reply_model(
                state.resolve(),
                "google-antigravity/gemini-3.6-flash-tiered",
                now=now,
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["action"], "image-model-set")
            self.assertEqual(
                result["model"], "google-antigravity/gemini-3.6-flash-tiered"
            )
            saved = json.loads((state / "reply-image-model.json").read_text())
            self.assertEqual(saved["model"], "google-antigravity/gemini-3.6-flash-tiered")
            denied = module.set_image_reply_model(
                state.resolve(), "not-a-real/model", now=now
            )
            self.assertFalse(denied["ok"])

    def test_worker_uses_image_model_override_only_for_images(self):
        worker_path = SCRIPTS / "auto-reply-worker.py"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            room = root / "rooms" / "1"
            room.mkdir(parents=True)
            state = room / "reply-state.json"
            state.write_text("{}", encoding="utf-8")
            (root / "reply-model.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "model": "omlx/Qwen3.6-35B-A3B-8bit",
                        "updated_at": 1,
                    }
                ),
                encoding="utf-8",
            )
            (root / "reply-image-model.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "model": "google-antigravity/gemini-3.7-flash-tiered",
                        "updated_at": 1,
                    }
                ),
                encoding="utf-8",
            )
            os.environ["OPENKAKAO_REPLY_STATE"] = str(state)
            os.environ["OPENKAKAO_REPLY_MODEL"] = "omlx/Qwen3.6-35B-A3B-8bit"
            spec = importlib.util.spec_from_file_location(
                "auto_reply_image_model_override", worker_path
            )
            worker = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(worker)
            self.assertEqual(worker._active_reply_model(), "omlx/Qwen3.6-35B-A3B-8bit")
            self.assertEqual(
                worker._generation_reply_model(False), "omlx/Qwen3.6-35B-A3B-8bit"
            )
            self.assertEqual(
                worker._generation_reply_model(True),
                "google-antigravity/gemini-3.7-flash-tiered",
            )
            source = worker_path.read_text(encoding="utf-8")
            isolated = source.split("if REPLY_RUNNER_KIND == \"codex\":", 1)[1].split(
                "codex_stdin_prefix", 1
            )[0]
            self.assertIn('env.pop("GJC_CODING_AGENT_DIR", None)', isolated)
            self.assertIn('env.pop("PI_CODING_AGENT_DIR", None)', isolated)
            self.assertNotIn("GJC_CODING_AGENT_DIR\"] = str(gjc_agent_dir)", isolated)

    def test_worker_skips_image_model_when_reply_model_sees_images(self):
        worker_path = SCRIPTS / "auto-reply-worker.py"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            room = root / "rooms" / "1"
            room.mkdir(parents=True)
            (room / "reply-state.json").write_text("{}", encoding="utf-8")
            (root / "reply-model.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "model": "google-antigravity/gemini-3.7-flash-tiered",
                        "updated_at": 1,
                    }
                ),
                encoding="utf-8",
            )
            (root / "reply-image-model.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "model": "google-antigravity/gemini-3.6-flash-tiered",
                        "updated_at": 1,
                    }
                ),
                encoding="utf-8",
            )
            os.environ["OPENKAKAO_REPLY_STATE"] = str(room / "reply-state.json")
            spec = importlib.util.spec_from_file_location(
                "auto_reply_image_model_unused", worker_path
            )
            worker = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(worker)
            self.assertEqual(
                worker._generation_reply_model(True),
                "google-antigravity/gemini-3.7-flash-tiered",
            )

    def test_omlx_reply_model_keeps_image_model_enabled(self):
        module = load("auto_reply_menubar_image_gate")
        self.assertFalse(module._reply_model_sees_images("omlx/Qwen3.6-35B-A3B-8bit"))
        self.assertTrue(
            module._reply_model_sees_images("google-antigravity/gemini-3.7-flash-tiered")
        )
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            (state / "reply-model.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "model": "omlx/Qwen3.6-35B-A3B-8bit",
                        "updated_at": 1,
                    }
                ),
                encoding="utf-8",
            )
            self.assertTrue(module._image_model_enabled(state))
            (state / "reply-model.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "model": "google-antigravity/gemini-3.7-flash-tiered",
                        "updated_at": 1,
                    }
                ),
                encoding="utf-8",
            )
            self.assertFalse(module._image_model_enabled(state))
            denied = module.set_image_reply_model(
                state, "google-antigravity/gemini-3.6-flash-tiered", now=1.0
            )
            self.assertFalse(denied["ok"])
            self.assertEqual(denied["reason"], "image_model_unused")

    def test_swift_menu_decodes_image_reply_model_fields(self):
        source = SWIFT.read_text(encoding="utf-8")
        self.assertIn("let image_reply_model", source)
        self.assertIn("--action\", \"image-model-set\"", source)
        self.assertIn("이미지 모델", source)

    def test_reply_model_fallbacks_default_when_nothing_saved(self):
        module = load("auto_reply_menubar_fallbacks_default")
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            models, source = module.read_reply_model_fallbacks(state)
            self.assertEqual(source, "default")
            self.assertEqual(models, list(module.DEFAULT_REPLY_FALLBACK_MODELS))

    def test_reply_model_fallbacks_save_reads_back_ordered_chain(self):
        module = load("auto_reply_menubar_fallbacks_save")
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            module._fallback_allowed_model_ids = lambda _root: {
                "kiro/claude-opus-5",
                "kiro/claude-sonnet-5",
                "kiro/claude-opus-4.8",
            }
            saved = module.set_reply_model_fallbacks(
                state,
                "kiro/claude-opus-5,kiro/claude-sonnet-5,kiro/claude-opus-4.8",
                now=1.0,
            )
            self.assertTrue(saved["ok"])
            self.assertEqual(
                saved["fallback_models"],
                ["kiro/claude-opus-5", "kiro/claude-sonnet-5", "kiro/claude-opus-4.8"],
            )
            self.assertEqual(saved["fallback_source"], "override")
            # The window and the worker read this file, so check the on-disk shape.
            payload = json.loads(
                (state / module.REPLY_MODEL_FALLBACKS_NAME).read_text(encoding="utf-8")
            )
            self.assertEqual(payload["schema_version"], 1)
            self.assertEqual(payload["models"], saved["fallback_models"])
            self.assertEqual(
                module.read_reply_model_fallbacks(state),
                (saved["fallback_models"], "override"),
            )

    def test_reply_model_fallbacks_empty_list_means_no_fallback(self):
        module = load("auto_reply_menubar_fallbacks_empty")
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            module._fallback_allowed_model_ids = lambda _root: set()
            saved = module.set_reply_model_fallbacks(state, "", now=1.0)
            self.assertTrue(saved["ok"])
            self.assertEqual(saved["fallback_models"], [])
            # An empty list is a real choice, so it must not read as the defaults.
            self.assertEqual(saved["fallback_source"], "override")
            self.assertEqual(module.read_reply_model_fallbacks(state), ([], "override"))

    def test_reply_model_fallbacks_clear_restores_builtin_defaults(self):
        module = load("auto_reply_menubar_fallbacks_clear")
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            module._fallback_allowed_model_ids = lambda _root: {"kiro/claude-opus-5"}
            module.set_reply_model_fallbacks(state, "kiro/claude-opus-5", now=1.0)
            self.assertTrue((state / module.REPLY_MODEL_FALLBACKS_NAME).is_file())
            cleared = module.set_reply_model_fallbacks(state, None, now=2.0, clear=True)
            self.assertTrue(cleared["ok"])
            self.assertEqual(cleared["fallback_source"], "default")
            self.assertFalse((state / module.REPLY_MODEL_FALLBACKS_NAME).is_file())
            self.assertEqual(
                cleared["fallback_models"], list(module.DEFAULT_REPLY_FALLBACK_MODELS)
            )

    def test_reply_model_fallbacks_rejects_overflow_and_unknown_models(self):
        module = load("auto_reply_menubar_fallbacks_reject")
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            module._fallback_allowed_model_ids = lambda _root: {"kiro/claude-opus-5"}
            too_many = module.set_reply_model_fallbacks(
                state, ",".join(f"kiro/m{index}" for index in range(7)), now=1.0
            )
            self.assertFalse(too_many["ok"])
            self.assertEqual(too_many["reason"], "too_many_fallbacks")
            unknown = module.set_reply_model_fallbacks(state, "nope/nope", now=1.0)
            self.assertFalse(unknown["ok"])
            self.assertEqual(unknown["reason"], "model_not_in_catalog")
            self.assertFalse((state / module.REPLY_MODEL_FALLBACKS_NAME).exists())

    def test_reply_model_fallbacks_snapshot_reaches_the_settings_window(self):
        # The settings window draws straight from the snapshot, and the frozen
        # overlay builds that snapshot through its own reference. A hook that
        # only patches the impl layer leaves the window showing defaults.
        module = load("auto_reply_menubar_fallbacks_snapshot")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            root.joinpath(module.REPLY_MODEL_FALLBACKS_NAME).write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "models": ["kiro/claude-opus-5"],
                        "updated_at": 1,
                    }
                ),
                encoding="utf-8",
            )
            snapshot = module.collect_menubar_model(root)
            self.assertEqual(
                snapshot["reply_model_fallbacks"]["models"], ["kiro/claude-opus-5"]
            )
            self.assertEqual(
                snapshot["reply_model_fallbacks"]["source"], "override"
            )
            self.assertEqual(
                snapshot["reply_model_fallbacks"]["max"],
                module.MAX_REPLY_FALLBACK_MODELS,
            )

    def test_models_action_exposes_fallback_chain(self):
        module = load("auto_reply_menubar_fallbacks_models_action")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            root.joinpath(module.REPLY_MODEL_FALLBACKS_NAME).write_text(
                json.dumps({"schema_version": 1, "models": [], "updated_at": 1}),
                encoding="utf-8",
            )
            payload = module.collect_reply_models(root)
            self.assertEqual(payload["fallback_models"], [])
            self.assertEqual(payload["fallback_source"], "override")
            self.assertEqual(
                payload["fallback_defaults"], list(module.DEFAULT_REPLY_FALLBACK_MODELS)
            )
            self.assertEqual(payload["fallback_max"], module.MAX_REPLY_FALLBACK_MODELS)

    def test_worker_uses_saved_fallback_chain_and_respects_empty(self):
        worker_path = SCRIPTS / "auto-reply-worker.py"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            state = root / "rooms" / "1" / "reply-state.json"
            state.parent.mkdir(parents=True)
            state.write_text("{}", encoding="utf-8")
            os.environ["OPENKAKAO_REPLY_STATE"] = str(state)
            spec = importlib.util.spec_from_file_location(
                "auto_reply_fallback_chain", worker_path
            )
            worker = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(worker)
            path = root / worker.REPLY_MODEL_FALLBACKS_NAME

            self.assertEqual(
                worker._reply_fallback_candidates(),
                list(worker.DEFAULT_REPLY_FALLBACK_MODELS),
            )
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "models": ["kiro/claude-opus-5", "kiro/claude-sonnet-5"],
                        "updated_at": 1,
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                worker._reply_fallback_candidates(),
                ["kiro/claude-opus-5", "kiro/claude-sonnet-5"],
            )
            # "No fallback" must survive as an empty chain, not become defaults.
            path.write_text(
                json.dumps({"schema_version": 1, "models": [], "updated_at": 1}),
                encoding="utf-8",
            )
            self.assertEqual(worker._reply_fallback_candidates(), [])

    def test_swift_menu_renders_fallback_model_chain(self):
        source = SWIFT.read_text(encoding="utf-8")
        self.assertIn("let fallback_models", source)
        self.assertIn("struct ReplyModelFallbacks", source)
        self.assertIn("reply_model_fallbacks", source)
        self.assertIn("--action\", \"fallback-models-set\"", source)
        self.assertIn("\"--clear\"", source)
        self.assertIn("폴백 모델", source)


    def test_provider_preset_parser_reads_gjc_setup_list(self):
        module = load("auto_reply_menubar_provider_parse")
        blob = chr(10).join(
            [
                "Missing required provider setup option(s): --compat. Or use --preset <preset>.",
                "Available presets:",
                "glm (aliases: zai, z-ai, bigmodel): OpenAI-compatible GLM endpoint from zAI/BigModel",
                "litellm (aliases: litellm-proxy): OpenAI-compatible LiteLLM proxy endpoint (user-supplied base URL) with live model discovery",
                "minimax (aliases: minimax-code): OpenAI-compatible MiniMax Coding Plan endpoint",
            ]
        )
        parsed = module.parse_provider_preset_list(blob)
        ids = {item["id"] for item in parsed}
        self.assertEqual(ids, {"glm", "litellm", "minimax"})
        litellm = next(item for item in parsed if item["id"] == "litellm")
        self.assertTrue(litellm["needs_base_url"])
        glm = next(item for item in parsed if item["id"] == "glm")
        self.assertFalse(glm["needs_base_url"])
        self.assertEqual(glm["api_key_env"], "ZAI_API_KEY")

    def test_list_provider_presets_uses_gjc_without_secrets(self):
        module = load("auto_reply_menubar_provider_list")
        captured = {"argv": None}

        class Result:
            returncode = 1
            stdout = json.dumps(
                {
                    "ok": False,
                    "error": chr(10).join(
                        [
                            "Missing required provider setup option(s). Or use --preset <preset>.",
                            "Available presets:",
                            "glm (aliases: zai): OpenAI-compatible GLM endpoint from zAI/BigModel",
                        ]
                    ),
                }
            )
            stderr = ""

        def executor(argv):
            captured["argv"] = argv
            return Result()

        with tempfile.TemporaryDirectory() as raw:
            payload = module.list_provider_presets(
                Path(raw),
                runner=Path("/tmp/fake-gjc"),
                executor=executor,
            )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["action"], "provider-presets")
        self.assertEqual(payload["source"], "gjc")
        self.assertEqual(payload["presets"][0]["id"], "glm")
        self.assertEqual(captured["argv"][:4], ["/tmp/fake-gjc", "setup", "provider", "--json"])
        encoded = json.dumps(payload, ensure_ascii=False)
        for secret in FORBIDDEN + ("sk-secret-must-never-escape",):
            self.assertNotIn(secret, encoded)

    def test_add_api_provider_builds_gjc_setup_without_raw_key(self):
        module = load("auto_reply_menubar_provider_add")
        captured = {"argv": None}

        class Result:
            returncode = 0
            stdout = json.dumps(
                {
                    "providerId": "glm-proxy",
                    "compatibility": "openai",
                    "api": "openai-completions",
                    "modelIds": ["glm-4.6"],
                    "preset": "glm",
                    "credentialSource": "env",
                    "redactedApiKey": "sk-secret-must-never-escape",
                }
            )
            stderr = ""

        def executor(argv):
            captured["argv"] = argv
            return Result()

        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            denied = module.add_api_provider(
                state,
                preset="glm",
                api_key_env="sk-secret-must-never-escape",
                runner=Path("/tmp/fake-gjc"),
                executor=executor,
            )
            self.assertFalse(denied["ok"])
            self.assertEqual(denied["reason"], "api_key_env_invalid")
            self.assertIsNone(captured["argv"])
            result = module.add_api_provider(
                state,
                preset="glm",
                api_key_env="ZAI_API_KEY",
                runner=Path("/tmp/fake-gjc"),
                executor=executor,
                now=1_000.0,
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["provider"], "glm-proxy")
        self.assertEqual(result["credential_source"], "env")
        self.assertNotIn("sk-secret-must-never-escape", json.dumps(result))
        argv = captured["argv"]
        self.assertEqual(argv[:4], ["/tmp/fake-gjc", "setup", "provider", "--json"])
        self.assertIn("--preset", argv)
        self.assertIn("glm", argv)
        self.assertIn("--api-key-env", argv)
        self.assertIn("ZAI_API_KEY", argv)
        self.assertNotIn("--api-key", argv)
        self.assertNotIn("sk-secret-must-never-escape", argv)

    def test_add_api_provider_custom_openai_requires_env_and_model(self):
        module = load("auto_reply_menubar_provider_custom")
        captured = {"argv": None}

        class Result:
            returncode = 0
            stdout = json.dumps({"providerId": "my-proxy", "modelIds": ["demo-model"]})
            stderr = ""

        def executor(argv):
            captured["argv"] = argv
            return Result()

        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            missing = module.add_api_provider(
                state,
                compat="openai",
                provider_id="my-proxy",
                base_url="https://example.invalid/v1",
                runner=Path("/tmp/fake-gjc"),
                executor=executor,
            )
            self.assertFalse(missing["ok"])
            self.assertEqual(missing["reason"], "api_key_env_required")
            result = module.add_api_provider(
                state,
                compat="openai",
                provider_id="my-proxy",
                base_url="https://example.invalid/v1",
                api_key_env="MY_PROXY_API_KEY",
                models="demo-model",
                runner=Path("/tmp/fake-gjc"),
                executor=executor,
                now=1_000.0,
            )
        self.assertTrue(result["ok"])
        argv = captured["argv"]
        self.assertIn("--compat", argv)
        self.assertIn("openai", argv)
        self.assertIn("--provider", argv)
        self.assertIn("my-proxy", argv)
        self.assertIn("--base-url", argv)
        self.assertIn("https://example.invalid/v1", argv)
        self.assertIn("--api-key-env", argv)
        self.assertNotIn("--api-key", argv)

    def test_add_api_provider_parameterized_preset_needs_url(self):
        module = load("auto_reply_menubar_provider_proxy")
        with tempfile.TemporaryDirectory() as raw:
            denied = module.add_api_provider(
                Path(raw),
                preset="litellm",
                api_key_env="LITELLM_API_KEY",
                runner=Path("/tmp/fake-gjc"),
                executor=lambda argv: None,
            )
        self.assertFalse(denied["ok"])
        self.assertEqual(denied["reason"], "base_url_required")

    def test_swift_menu_registers_providers_without_raw_keys(self):
        source = SWIFT.read_text(encoding="utf-8")
        self.assertIn("프로바이더 등록", source)
        self.assertIn("func buildProviderRegisterMenu", source)
        self.assertIn("providerPresetClicked", source)
        self.assertIn("customProviderClicked", source)
        self.assertIn('--action", "provider-add"', source)
        self.assertIn("--provider-api-key-env", source)
        stripped = source.replace("--provider-api-key-env", "")
        self.assertNotIn('--api-key"', stripped)
        self.assertIn("provider-oauth-login", source)
        self.assertIn("func providerOAuthClicked", source)
        self.assertIn("func loadOAuthProviders", source)
        self.assertIn("timeout: 180", source)
        self.assertNotIn("메뉴바는 브라우저 로그인을 직접 열지 않습니다", source)
        wrapper = MENUBAR.read_text(encoding="utf-8")
        self.assertIn("PROVIDER_ACTIONS", wrapper)
        self.assertIn("def add_api_provider", wrapper)
        self.assertIn("api_key_rejected", wrapper)
        self.assertIn("provider-oauth-login", wrapper)
        self.assertIn("auth-broker", wrapper)

    def test_catalog_cli_returns_full_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper, module, state, _room, _queue, now, model = self._model(
                Path(temporary)
            )
            logs = Path(temporary) / "logs"
            logs.mkdir()
            payload = json.dumps({"chat_id": 99, "auto_reply": True, "geeknews": False})
            import subprocess
            completed = subprocess.run(
                [
                    sys.executable,
                    str(MENUBAR),
                    "--state-root",
                    str(state.resolve()),
                    "--logs-dir",
                    str(logs),
                    "--catalog-upsert",
                    payload,
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            parsed = json.loads(completed.stdout)
            self.assertIn("privacy", parsed)
            self.assertIn("available_chats", parsed)
            self.assertIn("log_lines", parsed)
            self.assertNotEqual(list(parsed.keys()), ["rooms"])
            ids = [item["chat_id"] for item in parsed.get("rooms") or []]
            self.assertIn(99, ids)
            deleted = subprocess.run(
                [
                    sys.executable,
                    str(MENUBAR),
                    "--state-root",
                    str(state.resolve()),
                    "--logs-dir",
                    str(logs),
                    "--catalog-delete",
                    "99",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(deleted.returncode, 0, deleted.stderr)
            gone = json.loads(deleted.stdout)
            self.assertIn("privacy", gone)
            remaining = [item["chat_id"] for item in gone.get("rooms") or []]
            self.assertNotIn(99, remaining)
            del helper, now, model

    def test_oauth_login_uses_auth_broker_without_secrets(self):
        module = load("auto_reply_menubar_oauth")
        captured = {"argv": None}

        class Result:
            returncode = 1
            stdout = ""
            stderr = "Unknown provider. Known: anthropic, openai-codex, google-antigravity"

        def executor(argv):
            captured["argv"] = argv
            return Result()

        with tempfile.TemporaryDirectory() as raw:
            listed = module.list_oauth_providers(
                Path(raw),
                runner=Path("/tmp/fake-gjc"),
                executor=executor,
            )
            denied = module.login_oauth_provider(
                Path(raw),
                "sk-secret-must-never-escape",
                runner=Path("/tmp/fake-gjc"),
                executor=executor,
            )
            okish = module.login_oauth_provider(
                Path(raw),
                "anthropic",
                runner=Path("/tmp/fake-gjc"),
                executor=executor,
            )
        self.assertTrue(listed["ok"])
        self.assertEqual(listed["action"], "provider-oauth-list")
        ids = [item["id"] for item in listed["providers"]]
        self.assertIn("anthropic", ids)
        self.assertEqual(captured["argv"][:4], ["/tmp/fake-gjc", "auth-broker", "login", "anthropic"])
        self.assertFalse(denied["ok"])
        self.assertEqual(denied["reason"], "provider_id_invalid")
        self.assertFalse(okish["ok"])
        encoded = json.dumps(listed) + json.dumps(denied) + json.dumps(okish)
        self.assertNotIn("sk-secret-must-never-escape", encoded)
        self.assertNotIn("--api-key", json.dumps(captured["argv"]))

    def test_swift_rooms_catalog_applies_mutate_snapshot(self):
        source = SWIFT.read_text(encoding="utf-8")
        self.assertIn("func applyCatalogSnapshot", source)
        self.assertIn("func applyOptimisticChat", source)
        self.assertIn("override func hitTest", source)
        clicked = source.split("@objc func roomsTableClicked", 1)[1].split("func applyCatalogSnapshot", 1)[0]
        self.assertIn("toggleRoomCatalog", clicked)
        self.assertIn("toggleRoomLive", clicked)
        self.assertIn('case "live":', clicked)
        self.assertNotIn("toggleRoomCatalog(chat)\n            toggleRoomCatalog", clicked)
    def test_provider_add_uses_isolated_agent_dir_and_models_yml(self):
        module = load("auto_reply_menubar_isolation_test")
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            agent_dir = state / "gjc-agent"
            agent_dir.mkdir(parents=True, exist_ok=True)
            models_yml = agent_dir / "models.yml"
            models_yml.write_text("providers:\n  isolated-proxy:\n    baseUrl: https://api.proxy.invalid/v1\n    apiKeyEnv: PROXY_KEY\n    models:\n      - id: proxy-model-1\n")

            # Verify custom parser
            parsed = module._parse_custom_models_yml(models_yml)
            self.assertEqual(len(parsed), 1)
            self.assertEqual(parsed[0]["id"], "isolated-proxy")
            self.assertEqual(parsed[0]["models"][0]["id"], "isolated-proxy/proxy-model-1")

            # Verify collect_reply_models includes custom provider
            catalog_file = state / "gjc-model-catalog.json"
            module._atomic_write_json(
                catalog_file,
                {
                    "schema_version": 1,
                    "updated_at": 1000,
                    "models": [
                        {"id": "base-prov/base-model", "label": "base-model", "provider": "base-prov", "canonical": "base-model"}
                    ]
                }
            )
            report = module.collect_reply_models(state, now=1000.0)
            self.assertTrue(report["ok"])
            prov_ids = [p["id"] for p in report["providers"]]
            self.assertIn("isolated-proxy", prov_ids)

            # Verify set_reply_model allows selecting isolated custom model
            set_res = module.set_reply_model(state, "isolated-proxy/proxy-model-1", now=1000.0)
            self.assertTrue(set_res["ok"])
            self.assertEqual(set_res["model"], "isolated-proxy/proxy-model-1")
            self.assertEqual(set_res["source"], "override")
            override = json.loads(module._reply_model_override_path(state).read_text())
            self.assertEqual(override["model"], "isolated-proxy/proxy-model-1")

    def test_gjc_process_env_exports_isolated_coding_agent_dir(self):
        module = load("auto_reply_menubar_env_isolation")
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            env = module._gjc_process_env(state)
            self.assertIn("GJC_CODING_AGENT_DIR", env)
            self.assertIn("PI_CODING_AGENT_DIR", env)
            self.assertEqual(env["GJC_CODING_AGENT_DIR"], str(state / "gjc-agent"))
            self.assertEqual(env["PI_CODING_AGENT_DIR"], str(state / "gjc-agent"))
            self.assertTrue((state / "gjc-agent").is_dir())

    def test_global_catalog_providers_parse_table_and_cache(self):
        module = load("auto_reply_menubar_global_catalog")
        table = (
            "Canonical models\n"
            "canonical  selected  variants  context  max-out\n"
            "xai/grok-4.6  xai/grok-4.6  1  256K  64K\n"
        )
        calls = {"n": 0}

        def executor(argv):
            calls["n"] += 1
            self.assertEqual(argv[-1], "--list-models")

            class Result:
                stdout = table
                returncode = 0

            return Result()

        providers = module._global_catalog_providers(executor=executor)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(providers[0]["id"], "xai")
        model_ids = [m["id"] for m in providers[0]["models"]]
        self.assertIn("xai/grok-4.6", model_ids)
        # Executor-provided runs never touch the TTL cache.
        providers2 = module._global_catalog_providers(executor=executor)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(providers2, providers)
        self.assertNotIn("GJC_CODING_AGENT_DIR", module._global_models_env())
        self.assertNotIn("PI_CODING_AGENT_DIR", module._global_models_env())


    def test_custom_models_yml_skips_thinking_and_input_role_keys(self):
        module = load("auto_reply_menubar_yml_roles")
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "models.yml"
            path.write_text(
                "providers:\n"
                "  omlx:\n"
                "    models:\n"
                "      - id: Qwen3.6-35B-A3B-8bit\n"
                "        thinking:\n"
                "          levels:\n"
                "            - low\n"
                "            - medium\n"
                "            - high\n"
                "        input:\n"
                "          - text\n"
                "  openrouter:\n"
                "    models:\n"
                "      - id: stealth/ox-alpha\n"
                "        thinking:\n"
                "          levels:\n"
                "            - low\n"
                "            - high\n"
                "            - max\n"
                "        input:\n"
                "          - text\n"
                "          - image\n",
                encoding="utf-8",
            )
            parsed = module._parse_custom_models_yml(path)
            ids = {m["id"] for p in parsed for m in p["models"]}
            self.assertEqual(
                ids,
                {"omlx/Qwen3.6-35B-A3B-8bit", "openrouter/stealth/ox-alpha"},
            )
            self.assertNotIn("omlx/low", ids)
            self.assertNotIn("openrouter/text", ids)
            self.assertNotIn("openrouter/image", ids)
    def test_collect_reply_models_merges_global_providers_readonly(self):
        module = load("auto_reply_menubar_global_merge")
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            module._atomic_write_json(
                state / "gjc-model-catalog.json",
                {
                    "schema_version": 1,
                    "updated_at": 1000,
                    "models": [
                        {
                            "id": "base-prov/base-model",
                            "label": "base-model",
                            "provider": "base-prov",
                            "canonical": "base-model",
                        }
                    ],
                },
            )
            original_global = module._global_catalog_providers

            def fake_global(*, executor=None, state_root=None):
                return [
                    {
                        "id": "google-antigravity",
                        "label": "google-antigravity",
                        "models": [
                            {
                                "id": "google-antigravity/gemini-3.7-flash-tiered",
                                "label": "gemini-3.7-flash-tiered",
                            }
                        ],
                    },
                    {
                        "id": "base-prov",
                        "label": "base-prov",
                        "models": [
                            {"id": "base-prov/extra-model", "label": "extra-model"}
                        ],
                    },
                ]

            module._global_catalog_providers = fake_global
            try:
                report = module.collect_reply_models(state, now=1000.0)
            finally:
                module._global_catalog_providers = original_global
            self.assertTrue(report["ok"])
            prov_map = {p["id"]: p for p in report["providers"]}
            self.assertIn("google-antigravity", prov_map)
            base_model_ids = {
                m["id"] for m in prov_map["base-prov"]["models"]
            }
            self.assertIn("base-prov/base-model", base_model_ids)
            self.assertIn("base-prov/extra-model", base_model_ids)

    def test_set_reply_model_accepts_global_catalog_model(self):
        module = load("auto_reply_menubar_global_set")
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            module._atomic_write_json(
                state / "gjc-model-catalog.json",
                {"schema_version": 1, "updated_at": 1000, "models": []},
            )
            original_global = module._global_catalog_providers

            def fake_global(*, executor=None, state_root=None):
                return [
                    {
                        "id": "openai-codex",
                        "label": "openai-codex",
                        "models": [{"id": "openai-codex/gpt-5.2", "label": "gpt-5.2"}],
                    }
                ]

            module._global_catalog_providers = fake_global
            try:
                result = module.set_reply_model(
                    state, "openai-codex/gpt-5.2", now=1000.0
                )
            finally:
                module._global_catalog_providers = original_global
            self.assertTrue(result["ok"])
            override = json.loads(
                module._reply_model_override_path(state).read_text()
            )
            self.assertEqual(override["model"], "openai-codex/gpt-5.2")
    def test_progress_counter_sums_toggled_catalog_rooms(self):
        import g_progress_counter as progress

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            catalog = {
                "schema_version": 1,
                "rooms": [
                    {"chat_id": 11, "auto_reply": True, "geeknews": True},
                    {"chat_id": 22, "auto_reply": True, "geeknews": False},
                    {"chat_id": 33, "auto_reply": False, "geeknews": True},
                    {"chat_id": 44, "auto_reply": False, "geeknews": False},
                ],
            }
            (root / "menubar-room-catalog.json").write_text(
                json.dumps(catalog), encoding="utf-8"
            )
            for chat_id, rows in (
                (
                    11,
                    [
                        ("sent", "social_reply", 0),
                        ("sent", "geeknews_rss", 1),
                    ],
                ),
                (
                    22,
                    [
                        ("sent", "direct_question", 0),
                        ("skipped", "stale_backlog", 0),
                    ],
                ),
                (
                    33,
                    [
                        ("sent", "geeknews_rss", 1),
                        ("sent", "social_reply", 0),
                    ],
                ),
                (
                    44,
                    [("sent", "social_reply", 0)],
                ),
            ):
                room = root / "rooms" / str(chat_id)
                room.mkdir(parents=True)
                conn = sqlite3.connect(room / "reply-queue.sqlite3")
                try:
                    conn.execute(
                        "CREATE TABLE reply_jobs ("
                        "event_id TEXT PRIMARY KEY, status TEXT, reason TEXT, event_json TEXT)"
                    )
                    for index, (status, reason, proactive) in enumerate(rows):
                        conn.execute(
                            "INSERT INTO reply_jobs VALUES (?,?,?,?)",
                            (
                                f"e{index}",
                                status,
                                reason,
                                json.dumps({"proactive": proactive}),
                            ),
                        )
                    conn.commit()
                finally:
                    conn.close()
            (root / "aggregate-status.json").write_text(
                json.dumps(
                    {
                        "targets": [
                            {"chat_id": 11},
                            {"chat_id": 55},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            live = root / "rooms" / "55"
            live.mkdir(parents=True)
            conn = sqlite3.connect(live / "reply-queue.sqlite3")
            try:
                conn.execute(
                    "CREATE TABLE reply_jobs ("
                    "event_id TEXT PRIMARY KEY, status TEXT, reason TEXT, event_json TEXT)"
                )
                conn.execute(
                    "INSERT INTO reply_jobs VALUES (?,?,?,?)",
                    ("live", "sent", "social_reply", json.dumps({"proactive": 0})),
                )
                conn.commit()
            finally:
                conn.close()
            result = progress.collect_progress([root])
            self.assertEqual(result["ordinary"], 3)
            self.assertEqual(result["geeknews"], 2)
            self.assertEqual(result["reply_rooms"], [11, 22, 55])
            self.assertEqual(result["geeknews_rooms"], [11, 33, 55])

    def test_swift_draws_the_knowledge_graph_as_neurons_and_synapses(self):
        """The graph window draws a force layout, not a table of constants."""
        source = SWIFT.read_text(encoding="utf-8")
        self.assertIn("final class KnowledgeGraphView", source)
        self.assertIn("struct KnowledgeNode", source)
        self.assertIn("struct KnowledgeEdge", source)
        self.assertIn("struct KnowledgeEvidence", source)
        # A node is a cell body whose size follows importance, and a synapse
        # is an edge whose thickness follows weight.
        self.assertIn("private func radius(_ node: KnowledgeNode) -> CGFloat", source)
        # 선 굵기는 관계 강도를 따르되, 고른 뉴런에 붙은 시냅스만 더 굵다.
        # 예전에는 모든 시냅스에 중간 점을 찍어 341개 관계가 점밭이 되었다
        # (2026-09-17, 6 Pro 지적).
        self.assertIn("path.lineWidth = (touchesHighlight ? 1.6 : 0.8) + 2.0 * strength", source)
        self.assertIn("guard touchesHighlight else { continue }", source)
        # Grounded neurons glow and unverified seeds stay dim, so a reader can
        # tell a checked concept from a placeholder. The colours come from the
        # Jarvis gold family so the graph reads as part of the same core
        # (2026-09-17, 사용자 지시).
        self.assertIn("node.evidence.grounded ? Self.neuronGold : Self.neuronBrass", source)
        self.assertIn("static let neuronGold", source)
        self.assertIn("static let synapseGold", source)
        # A retracted node is visibly different instead of silently stale.
        self.assertIn("if node.evidence.retracted {", source)
        # The layout has to be deterministic: the window redraws on a timer and
        # a random layout would look like a different graph every poll.
        self.assertNotIn("Int.random", source[source.find("final class KnowledgeGraphView"):])
        self.assertNotIn("arc4random", source[source.find("final class KnowledgeGraphView"):])

    def test_swift_applies_a_graph_snapshot_in_one_layout_pass(self):
        """A snapshot must not settle the force layout twice.

        nodes and edges each rebuild the layout from their didSet. Assigning
        them one after the other ran the whole force simulation twice per
        refresh, and the doc comment claimed otherwise (2026-09-17, 6 Pro 지적).
        """
        source = SWIFT.read_text(encoding="utf-8")
        self.assertIn("private var applyingSnapshot = false", source)
        self.assertIn("private func rebuildLayoutUnlessApplyingSnapshot()", source)
        self.assertIn("didSet { rebuildLayoutUnlessApplyingSnapshot() }", source)
        start = source.find("func applySnapshot(nodes newNodes: [KnowledgeNode]")
        end = source.find("func focusGroup(around nodeId: String)")
        self.assertGreater(start, 0)
        self.assertGreater(end, start)
        body = source[start:end]
        # 두 값을 다 넣은 뒤 한 번만 배치한다.
        self.assertIn("applyingSnapshot = true", body)
        self.assertIn("applyingSnapshot = false", body)
        self.assertLess(
            body.find("applyingSnapshot = false"),
            body.find("rebuildLayout()"),
            "the single rebuild has to come after both assignments",
        )

    def test_swift_shows_the_retry_affordance_on_the_first_graph_failure(self):
        """The first failed read has no picture, so the canvas stays empty.

        The retry button lives inside the graph stack, and the stack used to be
        hidden whenever there were no nodes. A user whose very first read timed
        out therefore saw no way to try again (2026-09-17, 6 Pro 지적).
        """
        source = SWIFT.read_text(encoding="utf-8")
        self.assertIn("let showsGraphPanel = showsGraph || graphFailed", source)
        self.assertIn("vectorGraphStack?.isHidden = !showsGraphPanel", source)
        # 캔버스는 접되 안내 줄은 남긴다.
        self.assertIn("vectorGraphView?.isHidden = !showsGraph", source)
        self.assertIn("vectorGraphHeightConstraint?.isActive = showsGraph", source)
        # 실패를 알릴 때 화면 구성도 다시 계산해야 스택이 펼쳐진다.
        start = source.find("func presentVectorGraphError(hasPreviousPicture: Bool)")
        end = source.find("func refreshKnowledgeGraph()")
        self.assertGreater(start, 0)
        self.assertGreater(end, start)
        self.assertIn("applyVectorLayout()", source[start:end])

    def test_swift_graph_refresh_is_throttled(self):
        """Re-reading the graph on every 2-second redraw would spawn a process
        per tick, so the view only re-reads on a source change or after 30s."""
        source = SWIFT.read_text(encoding="utf-8")
        self.assertIn("lastVectorGraphReadAt", source)
        self.assertIn("timeIntervalSince(lastVectorGraphReadAt) >= 30", source)
        self.assertIn("lastVectorGraphSource != vectorSourceKind", source)

    def test_swift_graph_action_is_exposed(self):
        source = SWIFT.read_text(encoding="utf-8")
        self.assertIn('"--action", "knowledge-graph"', source)
        self.assertIn("refreshKnowledgeGraph", source)
        self.assertIn("selectVectorRow(forKnowledgeNode:", source)

    def test_swift_cards_grow_with_their_content(self):
        """A card must take its height from its content.

        The cards used to be NSBox(.custom) inside a vertical stack. NSBox
        reports its own (zero) intrinsic height there instead of the content's,
        so every card collapsed to 0pt: the border and background vanished and
        the content spilled out of the card, which is what made the windows
        look broken. The layout audit measured the collapsed height, so this
        guards the fix (2026-09-16).
        """
        source = SWIFT.read_text(encoding="utf-8")
        self.assertIn("final class CardView: NSView", source)
        self.assertIn("static func card(_ child: NSView, padding: CGFloat = 12) -> CardView", source)
        card_start = source.find("final class CardView: NSView")
        card_body = source[card_start : card_start + 2600]
        # The content is pinned on all four sides, so the card height follows it.
        self.assertIn("content.topAnchor.constraint(equalTo: topAnchor, constant: padding)", card_body)
        self.assertIn("content.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -padding)", card_body)
        # NSBox would collapse again if it came back.
        self.assertNotIn("box.contentView = container", source)

    def test_swift_status_labels_do_not_hold_empty_space(self):
        """An empty status line must not reserve a row.

        The model window kept a fixed empty row for its notification field, so
        the window ended with 142pt of dead space below the last card. The
        labels now hide themselves while empty (2026-09-16).
        """
        source = SWIFT.read_text(encoding="utf-8")
        self.assertIn("final class AutoHidingLabel: NSTextField", source)
        self.assertIn("static func statusLabel(size: CGFloat = 12, lines: Int = 2) -> AutoHidingLabel", source)
        self.assertIn("let status = Chrome.statusLabel()", source)

    def test_swift_model_window_shrinks_to_its_content(self):
        """The model window must not keep a dead band under its last card."""
        source = SWIFT.read_text(encoding="utf-8")
        self.assertIn("func shrinkModelSettingsWindow(attempt: Int = 0)", source)
        # A window the operator resized by hand is left alone.
        self.assertIn("modelWindowUserResized", source)
        self.assertIn("func windowDidEndLiveResize(_ notification: Notification)", source)

    def test_swift_pipeline_dots_are_vertically_centered(self):
        """The pipeline drew its dots at 40% height and left a top band."""
        source = SWIFT.read_text(encoding="utf-8")
        pipeline_start = source.find("final class PipelineView: NSView")
        pipeline_body = source[pipeline_start : pipeline_start + 2000]
        self.assertNotIn("bounds.height * 0.40", pipeline_body)
        # 점·이름이 차지하는 높이를 상수로 두고, 그리는 쪽이 그 값으로
        # 세로 가운데를 잡는다. 예전에는 56pt 고정 띠라 위아래로 12pt씩
        # 빈 띠가 남았다 (2026-09-16).
        self.assertIn("static let contentHeight: CGFloat = nodeRadius * 2 + 5 + nodeLabelHeight", pipeline_body)
        self.assertIn("bounds.height - contentHeight", pipeline_body)
        self.assertNotIn("heightAnchor.constraint(equalToConstant: 56)", source)
        # 메뉴 패널은 더 이상 파이프라인 띠를 쓰지 않는다. 자비스 코어가 그
        # 자리를 대신한다. 그래서 띠 높이 상수도 더 이상 참조되지 않아 함께
        # 없앴다 (2026-09-17).
        self.assertNotIn("PipelineView.stripHeight", source)
        self.assertNotIn("static let stripHeight", pipeline_body)
        # 단계 표는 남는다. 코어 아래 한 줄과 메뉴바 아이콘이 이 표를 쓴다.
        self.assertIn("PipelineView.labels", source)

    def test_swift_has_a_layout_audit_mode(self):
        """The windows cannot be eyeballed from CI, so the app can dump the
        geometry and a PNG of every window instead."""
        source = SWIFT.read_text(encoding="utf-8")
        self.assertIn("case \"--layout-audit\": config.layoutAudit = take()", source)
        self.assertIn("func runLayoutAudit(_ outputDir: String) -> String", source)
        self.assertIn("enum LayoutAudit", source)
        # The audit has to run after the run loop laid the views out.
        self.assertIn("DispatchQueue.main.async { [weak self] in", source)

    def test_layout_audit_detects_collapsed_cards_and_empty_bands(self):
        """The audit is the only way to check a window nobody can see, so it has
        to actually report the defects it claims to find."""
        import struct
        import zlib

        spec = importlib.util.spec_from_file_location(
            "audit_menubar_layout", SCRIPTS / "audit-menubar-layout.py"
        )
        audit = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(audit)

        def png(path: Path, rows: list[list[tuple[int, int, int, int]]]) -> None:
            height = len(rows)
            width = len(rows[0])
            raw = b"".join(
                b"\x00" + b"".join(struct.pack("BBBB", *pixel) for pixel in row)
                for row in rows
            )
            def chunk(kind: bytes, body: bytes) -> bytes:
                return (
                    struct.pack(">I", len(body))
                    + kind
                    + body
                    + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
                )
            path.write_bytes(
                b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw))
                + chunk(b"IEND", b"")
            )

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            background = (40, 40, 40, 255)
            ink = (240, 240, 240, 255)
            # 위 40줄은 비어 있고, 아래에 글자가 있는 창.
            rows = [[background] * 200 for _ in range(120)]
            for y in range(60, 100):
                for x in range(20, 120):
                    rows[y][x] = ink
            png(directory / "sample.png", rows)
            (directory / "layout.json").write_text(
                json.dumps(
                    {
                        "windows": [
                            {
                                "window": "sample",
                                "path": "sample/AutoReplyMenu.CardView#0",
                                "kind": "AutoReplyMenu.CardView",
                                "w": 180,
                                "h": 4,
                                "hidden": False,
                                "winLeft": 10,
                                "winTop": 10,
                            },
                            {
                                "window": "sample",
                                "path": "sample/NSTextField#1",
                                "kind": "NSTextField",
                                "text": "긴 안내 문구",
                                "w": 40,
                                "h": 14,
                                "needW": 160,
                                "clipped": 120,
                                "hidden": False,
                                "winLeft": 10,
                                "winTop": 10,
                            },
                            {
                                "window": "sample",
                                "path": "sample/NSView#2",
                                "kind": "NSView",
                                "w": 50,
                                "h": 50,
                                "hidden": False,
                                "winLeft": 0,
                                "winTop": 0,
                                "overBottom": 30,
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = audit.analyze(directory)
            self.assertEqual([row["h"] for row in result["collapsed_cards"]], [4])
            self.assertEqual(len(result["overflow"]), 1)
            self.assertEqual(result["overflow"][0]["overBottom"], 30)
            self.assertEqual(len(result["clipped"]), 1)
            # 글자가 창의 절반 아래에만 있으므로 위쪽 빈 띠를 잡아내야 한다.
            bands = result["images"]["sample"]["empty_row_bands"]
            self.assertTrue(bands, "빈 띠를 못 찾았습니다")
            self.assertLess(bands[0][0], 60)

            # 감사 결과가 없으면 조용히 통과하지 않고 실패해야 한다.
            empty = directory / "empty"
            empty.mkdir()
            with self.assertRaises(ValueError):
                audit.analyze(empty)
            with self.assertRaises(ValueError):
                audit.analyze(directory / "missing")

    def test_menubar_exposes_the_knowledge_graph_action(self):
        """The action has to be answered before the frozen vector dispatch."""
        source = MENUBAR.read_text(encoding="utf-8")
        self.assertIn('if action == "knowledge-graph":', source)
        self.assertIn("collect_knowledge_graph", source)
        graph_at = source.find('if action == "knowledge-graph":')
        dispatch_at = source.find('args = type("Args", (), {"action": action})()')
        self.assertGreater(graph_at, 0)
        self.assertGreater(dispatch_at, 0)
        self.assertLess(graph_at, dispatch_at, "the graph action must come first")

    def test_knowledge_graph_action_survives_a_broken_state_root(self):
        """A missing state root must return an empty graph, not crash the menu."""
        spec = importlib.util.spec_from_file_location(
            "kg_for_menubar_test", SCRIPTS / "auto_reply_knowledge_graph.py"
        )
        graph_module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[spec.name] = graph_module
        spec.loader.exec_module(graph_module)
        payload = graph_module.collect_knowledge_graph(
            Path("/nonexistent/state/root/context.sqlite3"),
            state_root=Path("/nonexistent/state/root"),
        )
        # An unreachable store reports the failure instead of raising, and the
        # window then shows its own "읽지 못했습니다" line.
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["nodes"], [])
        self.assertEqual(payload["node_count"], 0)
        self.assertEqual(payload["grounded_nodes"], 0)


    def _background_room(self, root: Path, chat_id: int = 42) -> Path:
        """A state root with one room directory the hooks can read."""

        room = root / "rooms" / str(chat_id)
        room.mkdir(parents=True, exist_ok=True)
        return room

    def _background_snap(self, root: Path, chat_id: int = 42) -> dict:
        return {"rooms": [{"chat_id": chat_id, "selector": f"id:{chat_id}"}]}

    def _write_background_json(self, path: Path, payload) -> None:
        path.write_text(json.dumps(payload), encoding="utf-8")

    def _background_queue(self, room: Path, statuses: tuple[str, ...]) -> None:
        connection = sqlite3.connect(room / "reply-queue.sqlite3")
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS reply_jobs(event_id TEXT PRIMARY KEY,"
                " reason TEXT, status TEXT)"
            )
            connection.execute("DELETE FROM reply_jobs")
            for index, status in enumerate(statuses):
                connection.execute(
                    "INSERT INTO reply_jobs(event_id, reason, status)"
                    " VALUES(?,?,?)",
                    (f"event_{index}", "geeknews_rss", status),
                )
            connection.commit()
        finally:
            connection.close()

    def test_background_state_reports_a_fresh_db_sync(self):
        """A sync that just finished has to move the core, and say so."""

        module = load(f"auto_reply_menubar_bg_sync_{id(self)}")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "state"
            room = self._background_room(root)
            now = 2_000_000_000.0
            self._write_background_json(
                room / "db-watch-state.json",
                {
                    "capability_state": "ready",
                    "fence_reason": "",
                    "context_sync_at": now - 3.0,
                    "context_sync_retry_at": now + 57.0,
                    "heartbeat_at": now - 1.0,
                },
            )
            self._write_background_json(
                room / "geeknews-rss-cursor.json",
                {"posted_slots": ["2026-05-18:morning"], "updated_at": now - 3600},
            )
            snap = module._attach_background_state(
                self._background_snap(root), root, now=now
            )
            background = snap["background"]
            self.assertEqual(background["db_sync"]["state"], "syncing")
            self.assertEqual(background["activity"], 0.6)
            self.assertEqual(background["caption"], "DB 동기화 중")
            self.assertEqual(background["geeknews"]["state"], "idle")
            self.assertEqual(background["rooms"][0]["chat_id"], 42)
            self.assertEqual(background["schema_version"], 1)

    def test_background_state_separates_a_retrying_room_from_a_dead_one(self):
        """A fenced room is only "working" while its heartbeat is alive."""

        module = load(f"auto_reply_menubar_bg_fence_{id(self)}")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "state"
            room = self._background_room(root)
            now = 2_000_000_000.0
            self._write_background_json(
                room / "db-watch-state.json",
                {
                    "capability_state": "starting",
                    "fence_reason": "context_sync_transient",
                    "heartbeat_at": now - 5.0,
                },
            )
            snap = module._attach_background_state(
                self._background_snap(root), root, now=now
            )
            self.assertEqual(snap["background"]["db_sync"]["state"], "retrying")
            self.assertEqual(snap["background"]["activity"], 0.3)

            self._write_background_json(
                room / "db-watch-state.json",
                {
                    "capability_state": "starting",
                    "fence_reason": "context_sync_transient",
                    "heartbeat_at": now - 3600.0,
                },
            )
            snap = module._attach_background_state(
                self._background_snap(root), root, now=now
            )
            # 심장이 멈춘 방을 "도는 중"으로 세면 코어가 계속 빨라진다.
            self.assertEqual(snap["background"]["db_sync"]["state"], "stalled")
            self.assertEqual(snap["background"]["activity"], 0.0)
            self.assertEqual(snap["background"]["caption"], "")

    def test_background_state_confirms_a_recent_geeknews_send(self):
        """The cursor only counts as a send when a slot was actually posted."""

        module = load(f"auto_reply_menubar_bg_geek_{id(self)}")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "state"
            room = self._background_room(root)
            now = 2_000_000_000.0
            cursor = room / "geeknews-rss-cursor.json"
            self._write_background_json(
                cursor,
                {"posted_slots": ["2026-05-18:morning"], "updated_at": now - 5.0},
            )
            snap = module._attach_background_state(
                self._background_snap(root), root, now=now
            )
            self.assertEqual(snap["background"]["geeknews"]["state"], "confirmed")
            self.assertEqual(snap["background"]["activity"], 0.8)
            self.assertEqual(snap["background"]["caption"], "긱뉴스 전송 확인")

            # 새 항목만 훑고 아직 보낸 슬롯이 없으면 전송이 아니다.
            self._write_background_json(
                cursor, {"posted_slots": [], "updated_at": now - 5.0}
            )
            snap = module._attach_background_state(
                self._background_snap(root), root, now=now
            )
            self.assertEqual(snap["background"]["geeknews"]["state"], "idle")
            self.assertEqual(snap["background"]["activity"], 0.0)

    def test_background_state_sees_a_geeknews_job_in_the_queue(self):
        """A job that is queued or going out is the strongest signal."""

        module = load(f"auto_reply_menubar_bg_queue_{id(self)}")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "state"
            room = self._background_room(root)
            now = 2_000_000_000.0
            self._background_queue(room, ("sent", "sending"))
            snap = module._attach_background_state(
                self._background_snap(root), root, now=now
            )
            self.assertEqual(snap["background"]["geeknews"]["state"], "sending")
            self.assertEqual(snap["background"]["activity"], 0.9)
            self.assertEqual(snap["background"]["caption"], "긱뉴스 전송 중")

            self._background_queue(room, ("sent", "skipped"))
            snap = module._attach_background_state(
                self._background_snap(root), root, now=now
            )
            self.assertEqual(snap["background"]["geeknews"]["state"], "idle")

    def test_background_state_survives_broken_and_missing_inputs(self):
        """Every failure has to read as "모름", never as activity or an error."""

        module = load(f"auto_reply_menubar_bg_broken_{id(self)}")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "state"
            room = self._background_room(root)
            now = 2_000_000_000.0
            (room / "db-watch-state.json").write_text("{", encoding="utf-8")
            self._write_background_json(
                room / "geeknews-rss-cursor.json", ["not", "a", "mapping"]
            )
            snap = module._attach_background_state(
                self._background_snap(root), root, now=now
            )
            self.assertEqual(snap["background"]["db_sync"]["state"], "ready")
            self.assertEqual(snap["background"]["geeknews"]["state"], "idle")
            self.assertEqual(snap["background"]["activity"], 0.0)

            # 방 디렉터리가 없어도, 스냅샷이 dict가 아니어도 같은 조회가
            # 죽으면 안 된다.
            missing = module._attach_background_state(
                self._background_snap(root, chat_id=7), root, now=now
            )
            self.assertEqual(missing["background"]["db_sync"]["state"], "unknown")
            self.assertEqual(missing["background"]["geeknews"]["state"], "unknown")
            self.assertIs(module._attach_background_state(None, root), None)

            # 두 번 붙여도 값이 겹쳐 덮이지 않는다.
            again = module._attach_background_state(snap, root, now=now)
            self.assertIs(again, snap)

    def test_background_state_ignores_a_nonsense_clock(self):
        """A NaN or an infinity must not turn into activity."""

        module = load(f"auto_reply_menubar_bg_clock_{id(self)}")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "state"
            room = self._background_room(root)
            self._write_background_json(
                room / "db-watch-state.json",
                {
                    "capability_state": "ready",
                    "context_sync_at": float("nan"),
                    "context_sync_retry_at": float("inf"),
                    "heartbeat_at": -1,
                },
            )
            snap = module._attach_background_state(
                self._background_snap(root), root, now=2_000_000_000.0
            )
            self.assertEqual(snap["background"]["db_sync"]["state"], "ready")
            self.assertEqual(snap["background"]["activity"], 0.0)

    def test_the_cached_snapshot_path_still_carries_the_background_state(self):
        """A cache hit prints the snapshot itself, so the hook has to wrap it."""

        module = load(f"auto_reply_menubar_bg_cache_{id(self)}")
        self.assertTrue(
            getattr(module._snapshot_with_vector_status, "_openkakao_background", False)
        )
        source = MENUBAR.read_text(encoding="utf-8")
        self.assertIn("_install_background_cache_hook", source)
        # 백그라운드 나이는 매번 달라진다. 캐시 안에 넣으면 화면이 멈춘다.
        self.assertIn("redirect_stdout", source)
        capture_at = source.find("_orig_snapshot_with_vector_status = globals().get(")
        hook_at = source.find("_install_background_cache_hook()")
        self.assertGreater(capture_at, 0)
        self.assertGreater(hook_at, capture_at)

    def test_swift_panel_feeds_background_work_into_the_core(self):
        """The panel has to pass the core's background value into the core."""

        source = SWIFT.read_text(encoding="utf-8")
        self.assertIn("background: Double = 0", source)
        self.assertIn("background: String? = nil", source)
        self.assertIn("JarvisCoreView.background(model, chatId:", source)
        self.assertIn("background: background.activity", source)
        self.assertIn("background: background.caption", source)
        self.assertIn("let background: BackgroundActivity?", source)
        # 파이프라인 단계가 살아 있으면 그 설명이 먼저고, 백그라운드 한 줄은
        # 그다음이다. 순서가 뒤집히면 답변 생성 중에 화면이 동기화만 말한다.
        active_at = source.find('stages.first(where: { $0.state == "active" })')
        background_at = source.find("if let text = background, !text.isEmpty {")
        open_jobs_at = source.find("if openJobs > 0 {", background_at)
        self.assertGreater(active_at, 0)
        self.assertGreater(background_at, active_at)
        self.assertGreater(open_jobs_at, background_at)


def load_layout_check():
    spec = importlib.util.spec_from_file_location(
        "check_menubar_layout", SCRIPTS / "check-menubar-layout.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class LayoutGateTests(unittest.TestCase):
    """The gate has to fail on the defects it was written for.

    The menu extra cannot be looked at from CI, so this script is the only
    thing standing between a broken window and a green build. A check that
    silently matches nothing is worse than no check, which is exactly what
    happened to the first version of the column check: it looked for the clip
    view among the table's children, but the clip view is the table's parent,
    so it never found one and always passed (2026-09-16).
    """

    def setUp(self):
        self.check = load_layout_check()

    def scroll_stack(
        self, table_width, clip_width, h_scroller=False, window="log-min", columns_right=None
    ):
        """A table inside a clip view inside a scroll view, as AppKit builds it."""
        scroll = {
            "window": window,
            "path": f"{window}/NSStackView#0/AutoReplyMenu.TableScrollView#4",
            "kind": "AutoReplyMenu.TableScrollView",
            "hidden": False,
            "w": clip_width,
            "h": 260.0,
            "winLeft": 16.0,
            "hScroller": h_scroller,
        }
        clip = {
            "window": window,
            "path": scroll["path"] + "/NSClipView#2",
            "kind": "NSClipView",
            "hidden": False,
            "w": clip_width,
            "h": 260.0,
            "winLeft": 16.0,
        }
        table = {
            "window": window,
            "path": clip["path"] + "/NSTableView#0",
            "kind": "NSTableView",
            "hidden": False,
            "w": table_width,
            "h": 260.0,
            "winLeft": 16.0,
            "intercellSpacing": 8.0,
            # 열이 끝나는 자리. 표 프레임과 다르다: 표는 좌우에 자기 여백을
            # 두고, 그 여백은 클립 뷰가 잘라 낸다 (2026-09-16).
            "columnsRight": columns_right if columns_right is not None else table_width,
            "columns": [
                {"id": name, "width": 92.0, "minWidth": minimum}
                for name, minimum in (
                    ("time", 76.0),
                    ("room", 96.0),
                    ("outcome", 76.0),
                    ("reason", 96.0),
                    ("retrieval", 120.0),
                    ("reply", 120.0),
                )
            ],
        }
        return scroll, clip, table

    def test_table_wider_than_its_clip_view_is_reported(self):
        """The receipts window shipped a 976pt table in a 951pt clip view."""
        scroll, clip, table = self.scroll_stack(table_width=976.0, clip_width=951.0)
        found = self.check.tables_past_their_clip([scroll, clip, table])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["window"], "log-min")
        self.assertAlmostEqual(found[0]["over"], 25.0, places=1)

    def test_table_matched_to_its_clip_view_is_clean(self):
        scroll, clip, table = self.scroll_stack(table_width=951.0, clip_width=951.0)
        self.assertEqual(self.check.tables_past_their_clip([scroll, clip, table]), [])

    def test_a_hidden_table_is_not_reported(self):
        scroll, clip, table = self.scroll_stack(table_width=976.0, clip_width=951.0)
        table["hidden"] = True
        self.assertEqual(self.check.tables_past_their_clip([scroll, clip, table]), [])

    def test_a_rounding_difference_is_not_reported(self):
        scroll, clip, table = self.scroll_stack(table_width=951.4, clip_width=951.0)
        self.assertEqual(self.check.tables_past_their_clip([scroll, clip, table]), [])

    def test_a_table_wider_than_its_clip_view_is_fine_when_the_columns_fit(self):
        """The table keeps a margin the clip view trims; that is not a defect.

        AppKit's inset table style gives the table 20pt at each side. The frame
        is therefore wider than the clip view even when every column is fully
        visible, and the frame alone cannot tell the two apart (2026-09-16).
        """
        scroll, clip, table = self.scroll_stack(
            table_width=976.0, clip_width=951.0, columns_right=940.0
        )
        self.assertEqual(self.check.tables_past_their_clip([scroll, clip, table]), [])

    def test_columns_ending_past_the_clip_view_are_reported(self):
        """This is the shipped receipts-window bug: 답변 ran off the edge."""
        scroll, clip, table = self.scroll_stack(
            table_width=976.0, clip_width=951.0, columns_right=976.0
        )
        found = self.check.tables_past_their_clip([scroll, clip, table])
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0]["over"], 25.0, places=1)

    def test_columns_that_do_not_fit_are_reported(self):
        """624pt of column minimums in a 600pt clip view is unreachable."""
        scroll, clip, table = self.scroll_stack(table_width=600.0, clip_width=600.0)
        found = self.check.unreachable_columns([scroll, clip, table])
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0]["needed"], 624.0, places=1)
        self.assertAlmostEqual(found[0]["available"], 600.0, places=1)

    def test_columns_that_fit_are_clean(self):
        scroll, clip, table = self.scroll_stack(table_width=655.0, clip_width=655.0)
        self.assertEqual(self.check.unreachable_columns([scroll, clip, table]), [])

    def test_horizontal_scrolling_makes_narrow_columns_reachable(self):
        """With a horizontal scroller the operator can scroll to the last one."""
        scroll, clip, table = self.scroll_stack(
            table_width=600.0, clip_width=600.0, h_scroller=True
        )
        self.assertEqual(self.check.unreachable_columns([scroll, clip, table]), [])

    def test_a_table_without_a_clip_view_is_skipped(self):
        """Nothing to compare against, so the check must not guess."""
        _, _, table = self.scroll_stack(table_width=600.0, clip_width=600.0)
        self.assertEqual(self.check.unreachable_columns([table]), [])

    def test_content_inside_a_scroll_view_is_never_clipped(self):
        """A scroll view exists precisely to hold more than fits."""
        for path in (
            "log-min/NSStackView#0/NSScrollView#4/NSTextField#0",
            "log-min/NSStackView#0/AutoReplyMenu.TableScrollView#4/NSView#0",
        ):
            row = {
                "window": "log-min",
                "path": path,
                "kind": "NSView",
                "hidden": False,
                "w": 100.0,
                "h": 24.0,
                "overRight": 300.0,
            }
            self.assertTrue(
                self.check.inside_scroll_view(row),
                f"{path} should read as inside a scroll view",
            )
            self.assertEqual(self.check.clipped_at_minimum([row]), [])

    def test_vertical_dead_band_uses_the_last_painted_table_row(self):
        stack = {
            "window": "log",
            "path": "log/NSStackView#0",
            "kind": "NSStackView",
            "hidden": False,
            "orientation": "v",
            "spacing": 10.0,
            "winTop": 16.0,
            "h": 600.0,
        }
        scroll = {
            "window": "log",
            "path": stack["path"] + "/AutoReplyMenu.TableScrollView#3",
            "kind": "AutoReplyMenu.TableScrollView",
            "hidden": False,
            "winTop": 100.0,
            "h": 300.0,
        }
        row = {
            "window": "log",
            "path": scroll["path"] + "/NSClipView#0/NSTableView#0/AutoReplyMenu.StripedRowView#1",
            "kind": "AutoReplyMenu.StripedRowView",
            "hidden": False,
            "winTop": 134.0,
            "h": 34.0,
        }
        detail = {
            "window": "log",
            "path": stack["path"] + "/AutoReplyMenu.CardView#4",
            "kind": "AutoReplyMenu.CardView",
            "hidden": False,
            "winTop": 410.0,
            "h": 208.0,
        }
        found = self.check.vertical_dead_bands([stack, scroll, row, detail])
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0]["gap"], 242.0, places=1)

    def test_vertical_dead_band_inside_a_scroll_view_is_ignored(self):
        stack = {
            "window": "model",
            "path": "model/NSScrollView#0/NSClipView#0/NSStackView#0",
            "kind": "NSStackView",
            "hidden": False,
            "orientation": "v",
            "spacing": 10.0,
            "winTop": 0.0,
            "h": 500.0,
        }
        first = {
            "window": "model",
            "path": stack["path"] + "/NSView#0",
            "kind": "NSView",
            "hidden": False,
            "winTop": 10.0,
            "h": 20.0,
        }
        second = {
            "window": "model",
            "path": stack["path"] + "/NSView#1",
            "kind": "NSView",
            "hidden": False,
            "winTop": 200.0,
            "h": 20.0,
        }
        self.assertEqual(self.check.vertical_dead_bands([stack, first, second]), [])

    def test_overflow_at_the_minimum_size_is_reported(self):
        row = {
            "window": "log-min",
            "path": "log-min/NSStackView#0/NSButton#1",
            "kind": "NSButton",
            "hidden": False,
            "w": 100.0,
            "h": 24.0,
            "overRight": 40.0,
        }
        found = self.check.clipped_at_minimum([row])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["why"], "오른쪽으로")

    def test_the_build_size_is_not_judged_for_overflow(self):
        """Only the minimum-size pass is judged; the build size has room."""
        row = {
            "window": "log",
            "path": "log/NSStackView#0/NSButton#1",
            "kind": "NSButton",
            "hidden": False,
            "w": 100.0,
            "h": 24.0,
            "overRight": 40.0,
        }
        self.assertEqual(self.check.clipped_at_minimum([row]), [])

    def test_the_audit_expects_the_resized_pass(self):
        """Every window has to be measured at its minimum size too."""
        source = SWIFT.read_text(encoding="utf-8")
        self.assertIn("LayoutAudit.settle()", source)
        self.assertIn('entry.0 + "-min"', source)
        # minSize is a frame size, so it must be applied through setFrame.
        # Passing it to setContentSize measured a window smaller than the
        # operator can ever reach (2026-09-17, 6 Pro 지적).
        self.assertIn("window.minSize", source)
        self.assertIn("window.setFrame(minimumFrame, display: false)", source)
        self.assertNotIn("window.setContentSize(window.minSize)", source)
        # 되돌리지 않으면 아래 캡처가 줄어든 창을 찍는다.
        self.assertIn("window.setContentSize(originalSize)", source)

    def test_every_table_follows_its_clip_view(self):
        """The table has to resize with its clip view, or columns get cut."""
        source = SWIFT.read_text(encoding="utf-8")
        self.assertIn("final class TableScrollView: NSScrollView", source)
        self.assertIn("override func tile()", source)
        self.assertIn("let scroll = TableScrollView()", source)


if __name__ == "__main__":
    unittest.main()
