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
                "AI \ubaa8\ub378",
                "Voice",
                "\uce74\uce74\uc624 DB \ub3d9\uae30\ud654 \u00b7 \uc0c9\uc778",
                "DREAM-RSI",
                "Knowledge",
                "History",
            ],
        )

    def test_contract_hash_is_recorded_from_the_file_it_read(self) -> None:
        expected = hashlib.sha256(CONTRACT_SOURCE.read_bytes()).hexdigest()
        self.assertEqual(self.contract["sha256"], expected)

    def test_panel_label_comes_from_the_contract_file(self) -> None:
        self.assertEqual(self.contract["panel_label"], "\uc124\uc815 \uc5f4\uae30")

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

    def test_panel_source_keeps_the_single_gear(self) -> None:
        panel = template_body(UI_SOURCE.read_text(encoding="utf-8"), "mainPanelMarkup")
        self.assertEqual(check.count_contract_interactive(panel), 1)
        self.assertIn('id="gear"', panel)
        self.assertIn('aria-label="' + self.contract["panel_label"] + '"', panel)


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
                "models",
                "model-owner-status",
                "mlx-server-status",
                "dream-rsi-status",
                "knowledge-graph-status",
                "knowledge-graph",
                "knowledge-graph-focus",
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


if __name__ == "__main__":
    unittest.main()

