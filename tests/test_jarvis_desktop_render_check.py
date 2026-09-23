"""Contract tests for the rendered Jarvis desktop cross-check.

scripts/jarvis_desktop_render_check.py loads the built frontend in Chromium and
reports what the live DOM and the WebGL context actually are. It reads its
expectations from the TypeScript contract test so the banned tokens and the
settings section list have one source, which means this module has to keep those
two files honest about each other.

Nothing here needs a browser, a GPU, or the built bundle: every test covers a
pure helper, the shared expectations, or the Tauri-bridge stub the script
installs into the page.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import jarvis_desktop_render_check as check  # noqa: E402


UI_SOURCE = ROOT / "desktop" / "src" / "ui.ts"
STYLES_SOURCE = ROOT / "desktop" / "src" / "styles.css"
RUNTIME_SOURCE = ROOT / "desktop" / "src" / "runtime.ts"
CONTRACT_SOURCE = ROOT / "desktop" / "src" / "__tests__" / "ui-removal-contract.test.ts"
TAURI_MAIN_SOURCE = ROOT / "desktop" / "src-tauri" / "src" / "main.rs"
BACKTICK = chr(96)


def template_body(source: str, function_name: str) -> str:
    """Return the template literal a markup function returns.

    The markup helpers are plain template literals with no nested delimiters, so
    the first delimiter after the declaration and the next one bound the body.
    """
    marker = "function " + function_name
    start = source.index(marker)
    opening = source.index(BACKTICK, start)
    closing = source.index(BACKTICK, opening + 1)
    return source[opening + 1 : closing]


class SharedExpectations(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = check.parse_contract(CONTRACT_SOURCE)

    def test_contract_exposes_banned_tokens_and_sections(self) -> None:
        self.assertIn("\uac80\uc99d", self.contract["banned_tokens"])
        self.assertIn("permission", self.contract["banned_tokens"])
        self.assertEqual(
            self.contract["settings_sections"],
            [
                "\ub300\uc0c1 \ucc44\ud305\ubc29",
                "AI \ub2f5\ubcc0",
                "\uc74c\uc131",
                "\uce74\uce74\uc624\ud1a1 \ub300\ud654",
                "\ub300\ud654\uc5d0\uc11c \ucc3e\uae30",
                "\ucd5c\uadfc \ub2f5\ubcc0",
            ],
        )

    def test_contract_hash_is_recorded_from_the_file_it_read(self) -> None:
        expected = hashlib.sha256(CONTRACT_SOURCE.read_bytes()).hexdigest()
        self.assertEqual(self.contract["sha256"], expected)

    def test_contract_does_not_invent_a_panel_control_label(self) -> None:
        self.assertNotIn("panel_label", self.contract)

    def test_contract_parse_fails_closed_on_a_missing_array(self) -> None:
        with self.assertRaises(check.ContractParseError):
            check._array_literals("const OTHER = [];", "REMOVED_TOKENS")

    def test_contract_sections_match_the_settings_source(self) -> None:
        headings = check.headings_from_markup(template_body(UI_SOURCE.read_text(encoding="utf-8"), "settingsMarkup"))
        self.assertEqual(headings, self.contract["settings_sections"])

    def test_ui_sources_carry_no_banned_token(self) -> None:
        tokens = self.contract["banned_tokens"]
        for path in (UI_SOURCE, STYLES_SOURCE):
            with self.subTest(path=path.name):
                self.assertEqual(check.scan_banned_tokens([path.read_text(encoding="utf-8")], tokens), [])

    def test_panel_source_has_zero_interactive_elements(self) -> None:
        panel = template_body(UI_SOURCE.read_text(encoding="utf-8"), "mainPanelMarkup")
        self.assertEqual(check.count_contract_interactive(panel), 0)
        self.assertNotIn('id="gear"', panel)


class TraySourceContract(unittest.TestCase):
    def test_tray_source_keeps_right_click_settings_and_left_up_toggle(self) -> None:
        inspected = check.inspect_tray_source(TAURI_MAIN_SOURCE)
        self.assertTrue(inspected["right_up_opens_settings"])
        self.assertTrue(inspected["left_up_toggles_panel"])
        self.assertFalse(inspected["browser_exercised"])

    def test_tray_right_click_detector_fails_when_branch_is_missing(self) -> None:
        source = TAURI_MAIN_SOURCE.read_text(encoding="utf-8")
        without_right_click = source.replace(
            "button: MouseButton::Right,",
            "button: MouseButton::Middle,",
            1,
        )
        routes = check.tray_click_routes(without_right_click)
        self.assertFalse(routes["right_up_opens_settings"])
        self.assertTrue(routes["left_up_toggles_panel"])


class Helpers(unittest.TestCase):
    def test_banned_token_scan_detects_and_ignores(self) -> None:
        tokens = ["\uac80\uc99d", "bulk"]
        self.assertEqual(check.scan_banned_tokens(["<section>\uc77c\uad04 \uac80\uc99d</section>"], tokens), ["\uac80\uc99d"])
        self.assertEqual(check.scan_banned_tokens(["<canvas id='knowledge-graph-canvas'></canvas>"], tokens), [])

    def test_banned_token_scan_is_case_insensitive(self) -> None:
        self.assertEqual(check.scan_banned_tokens(["<div>BULK approve</div>"], ["bulk", "approve"]), ["bulk", "approve"])

    def test_banned_token_scan_reads_every_haystack(self) -> None:
        self.assertEqual(check.scan_banned_tokens(["clean", "second \uc810\uac80"], ["\uc810\uac80"]), ["\uc810\uac80"])

    def test_interactive_counter_matches_the_typescript_tag_set(self) -> None:
        markup = "<main><canvas></canvas><button id='gear'></button><div tabindex='0'></div></main>"
        self.assertEqual(check.count_contract_interactive(markup), 1)
        self.assertEqual(check.count_contract_interactive("<a href='#'></a><details></details><summary></summary>"), 3)

    def test_headings_from_markup_ignores_attributes_and_non_h2(self) -> None:
        markup = "<h1>Jarvis</h1><h2 id='a'>\uc54c\ud30c</h2><h2>\ubca0\ud0c0</h2><h3>\uac10\ub9c8</h3>"
        self.assertEqual(check.headings_from_markup(markup), ["\uc54c\ud30c", "\ubca0\ud0c0"])

    def test_verdict_requires_every_check(self) -> None:
        self.assertEqual(check.verdict([check.check("a", True, 1), check.check("b", True, 2)]), "pass")
        self.assertEqual(check.verdict([check.check("a", True, 1), check.check("b", False, 2)]), "fail")
        self.assertEqual(check.verdict([]), "pass")

    def test_check_coerces_truthiness_to_a_bool(self) -> None:
        self.assertIs(check.check("a", [], "detail")["ok"], False)
        self.assertIs(check.check("a", 1, "detail")["ok"], True)


class BridgeStub(unittest.TestCase):
    def test_stub_job_events_use_only_the_contract_keys(self) -> None:
        allowed = {"jobId", "kind", "stage", "load", "time", "errorCode"}
        for name, snapshot in (("idle", check.IDLE_SNAPSHOT), ("busy", check.BUSY_SNAPSHOT)):
            with self.subTest(snapshot=name):
                for event in snapshot["jobs"]:
                    self.assertEqual(set(event), allowed)

    def test_stub_snapshots_survive_the_json_round_trip_the_stub_performs(self) -> None:
        for name, snapshot in (("idle", check.IDLE_SNAPSHOT), ("busy", check.BUSY_SNAPSHOT)):
            with self.subTest(snapshot=name):
                self.assertEqual(snapshot["context_sync"]["mode"], "async")
                self.assertFalse(snapshot["context_sync"]["waited"])
                self.assertEqual(json.loads(json.dumps(snapshot)), snapshot)

    def test_busy_snapshot_really_carries_load(self) -> None:
        self.assertGreater(check.BUSY_SNAPSHOT["job_load"], 0.08)
        self.assertTrue(check.BUSY_SNAPSHOT["pipeline"]["active"])
        self.assertGreater(check.BUSY_SNAPSHOT["background"]["geeknews"]["activity"], 0.0)
        self.assertEqual(check.IDLE_SNAPSHOT["job_load"], 0.0)

    def test_stub_answers_every_settings_action_the_client_can_send(self) -> None:
        runtime_source = RUNTIME_SOURCE.read_text(encoding="utf-8")
        union = re.search(r"type SettingsAction =(.*?);", runtime_source, re.S)
        self.assertIsNotNone(union)
        declared = set(re.findall(r'"([a-z][a-z0-9-]*)"', union.group(1)))
        self.assertEqual(
            declared,
            {
                "knowledge-graph-status",
                "knowledge-graph",
                "knowledge-graph-focus",
                "room-upsert",
            },
        )
        extra = {"model-set", "model-prepare", "model-swap"}
        missing = (declared | extra) - set(check.SETTINGS_ACTIONS)
        self.assertEqual(missing, set(), "stub is missing settings actions: " + ", ".join(sorted(missing)))

    def test_stub_graph_payload_matches_the_parser_shape(self) -> None:
        graph = check.SETTINGS_ACTIONS["knowledge-graph"]
        node_ids = {node["id"] for node in graph["nodes"]}
        self.assertTrue(node_ids)
        self.assertTrue(all(node["id"] and node["label"] for node in graph["nodes"]))
        self.assertTrue(all(not node["id"].startswith("message:") for node in graph["nodes"]))
        for edge in graph["edges"]:
            self.assertIn(edge["source"], node_ids)
            self.assertIn(edge["target"], node_ids)

    def test_generated_capture_names_are_the_documented_ones(self) -> None:
        source = Path(check.__file__).read_text(encoding="utf-8")
        generated = set(re.findall(r"jarvis-render-[a-z.-]+\.png", source))
        self.assertIn("jarvis-render-panel.light.png", generated)
        self.assertIn("jarvis-render-settings.focus.png", generated)


class PauseOnHideContract(unittest.TestCase):
    """The panel must make zero render calls while it is hidden.

    Acceptance is the app's own render-call count, not whole-machine GPU use, so
    these assertions cover the same `pause` receipt the browser run records. The
    event name is pinned to the TypeScript bridge, because a renamed event would
    otherwise leave the hide proof asserting on a dispatch nobody subscribed to.
    """

    EVENT_SOURCE = ROOT / "desktop" / "src" / "core" / "lifecycle-wiring.ts"
    BRIDGE_TOKENS = ("plugin:event|listen", "plugin:event|unlisten", "window_is_visible")

    def passing(self) -> dict:
        return {
            "listeners": [check.VISIBILITY_EVENT],
            "hidden_emitted": True,
            "visible_emitted": True,
            "hidden": {"frames": 0, "fps": 0.0},
            "visible": {"frames": 33, "fps": 22.0},
            "blur": {"frames": 0, "fps": 0.0},
            "focus": {"frames": 34, "fps": 22.6},
            "hidden_poll_delta": 0,
        }

    def results(self, pause: dict) -> dict:
        return {entry["name"]: entry["ok"] for entry in check.pause_checks(pause)}

    def test_event_name_matches_the_typescript_bridge(self) -> None:
        source = self.EVENT_SOURCE.read_text(encoding="utf-8")
        declared = re.search(r'VISIBILITY_EVENT = "([^"]+)"', source)
        self.assertIsNotNone(declared, "lifecycle-wiring.ts must declare VISIBILITY_EVENT")
        self.assertEqual(check.VISIBILITY_EVENT, declared.group(1))

    def test_stub_models_the_visibility_bridge(self) -> None:
        for token in self.BRIDGE_TOKENS + ("emitEvent",):
            with self.subTest(token=token):
                self.assertIn(token, check.STUB_SOURCE)

    def test_full_receipt_passes(self) -> None:
        pause = self.passing()
        self.assertEqual(self.results(pause), {entry["name"]: True for entry in check.pause_checks(pause)})
        self.assertEqual(check.verdict(check.pause_checks(pause)), "pass")

    def test_a_frame_while_hidden_fails(self) -> None:
        pause = self.passing()
        pause["hidden"] = {"frames": 1, "fps": 0.4}
        self.assertFalse(self.results(pause)["panel.render_stops_when_hidden"])
        self.assertEqual(check.verdict(check.pause_checks(pause)), "fail")

    def test_a_snapshot_poll_while_hidden_fails(self) -> None:
        pause = self.passing()
        pause["hidden_poll_delta"] = 2
        self.assertFalse(self.results(pause)["panel.poller_stops_when_hidden"])

    def test_no_resume_after_visible_fails(self) -> None:
        pause = self.passing()
        pause["visible"] = {"frames": 0, "fps": 0.0}
        self.assertFalse(self.results(pause)["panel.render_resumes_when_visible"])

    def test_missing_bridge_subscription_fails(self) -> None:
        pause = self.passing()
        pause["listeners"] = []
        pause["hidden_emitted"] = False
        results = self.results(pause)
        self.assertFalse(results["panel.visibility_bridge_subscribed"])
        self.assertFalse(results["panel.visibility_bridge_emit_lands"])

    def test_blur_and_focus_are_checked_independently(self) -> None:
        pause = self.passing()
        pause["blur"] = {"frames": 2, "fps": 1.0}
        pause["focus"] = {"frames": 0, "fps": 0.0}
        results = self.results(pause)
        self.assertFalse(results["panel.render_stops_on_blur"])
        self.assertFalse(results["panel.render_resumes_on_focus"])

    def test_an_empty_pause_receipt_fails_closed(self) -> None:
        self.assertEqual(check.verdict(check.pause_checks({})), "fail")


class ProductSurfaceContract(unittest.TestCase):
    """The renderer stub follows the simplified settings surface."""

    def test_snapshots_include_voice_activity_without_hardware_diagnostics(self) -> None:
        self.assertEqual(check.IDLE_SNAPSHOT["voice"]["rms"], 0.0)
        self.assertEqual(check.BUSY_SNAPSHOT["voice"]["rms"], 0.42)
        self.assertNotIn("onDevice", check.IDLE_SNAPSHOT)
        self.assertNotIn("onDevice", check.BUSY_SNAPSHOT)

    def test_stub_has_no_removed_diagnostic_status_actions(self) -> None:
        removed = {"model-owner-status", "mlx-server-status", "dream-rsi-status"}
        self.assertTrue(removed.isdisjoint(check.SETTINGS_ACTIONS))


if __name__ == "__main__":
    unittest.main()
