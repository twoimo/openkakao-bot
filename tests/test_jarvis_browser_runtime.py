"""Contract tests for the provisioned Browser-Use runtime probe.

``browser/pyproject.toml`` pins the Browser-Use pair the product was measured
against, and ``scripts/jarvis_browser_runtime.py`` is what a launcher asks before
a web job starts. Both have to fail closed, so these tests pin the pins against
the project file, cover every reason code the probe can emit, and keep the
Chromium answer a tri-state. No browser, network, or installed Browser-Use is
required.
"""

from __future__ import annotations

import io
import json
import sys
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import jarvis_browser_runtime as runtime  # noqa: E402
import jarvis_browser_use as adapter  # noqa: E402


class LegacyAgent:
    """An Agent whose constructor declares the Playwright-context argument."""

    def __init__(self, browser_context=None) -> None:
        self.browser_context = browser_context


class ModernAgent:
    """An Agent that declares the modern browser argument."""

    def __init__(self, browser=None) -> None:
        self.browser = browser


class CatchAllAgent:
    """An Agent that only swallows unknown keywords, which must never pass."""

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


def fake_adapter(decision):
    """An adapter stub with the two attributes the probe touches."""

    class _Adapter:
        BrowserUseApiUnsupported = adapter.BrowserUseApiUnsupported
        CDP_URL_ATTR = adapter.CDP_URL_ATTR

        @staticmethod
        def browser_agent_kwargs(agent_class, context):
            if isinstance(decision, Exception):
                raise decision
            return decision

    return _Adapter


def version_lookup(source):
    """A metadata lookup over a mapping, or one that always raises."""

    def _lookup(name):
        if isinstance(source, Exception):
            raise source
        return source[name]

    return _lookup


def raiser(error):
    def _raise():
        raise error

    return _raise


class BrowserRuntimeProbeTests(unittest.TestCase):
    PINS = {"browser-use": "0.13.10", "playwright": "1.63.0"}

    def _probe(self, **overrides):
        params = {
            "version_lookup": version_lookup(dict(self.PINS)),
            "adapter_loader": lambda: fake_adapter({"browser_context": object()}),
            "agent_loader": lambda: LegacyAgent,
            "chromium_probe": lambda: True,
        }
        params.update(overrides)
        return runtime.probe_browser_runtime(**params)

    def test_declared_pins_match_the_runtime_project(self):
        project = tomllib.loads((ROOT / runtime.RUNTIME_PROJECT).read_text(encoding="utf-8"))
        declared = {}
        for entry in project["project"]["dependencies"]:
            name, separator, version = entry.partition("==")
            self.assertEqual(separator, "==", entry)
            declared[name] = version
        self.assertEqual(declared, runtime.PINNED_DISTRIBUTIONS)
        self.assertEqual(project["project"]["requires-python"], ">=3.11,<4")

    def test_pins_are_exact_three_part_versions(self):
        for name, version in runtime.PINNED_DISTRIBUTIONS.items():
            self.assertRegex(version, r"^[0-9]+\.[0-9]+\.[0-9]+$", name)

    def test_ready_report_when_every_check_passes(self):
        report = self._probe()
        self.assertIs(report["ready"], True)
        self.assertEqual(report["reasons"], [])
        self.assertEqual(report["binding"], runtime.BINDING_OK)
        self.assertEqual(report["binding_arguments"], ["browser_context"])
        self.assertEqual(report["installed"], self.PINS)
        self.assertEqual(report["project"], "browser/pyproject.toml")
        self.assertIs(report["chromium_installed"], True)

    def test_missing_and_mismatched_distributions_fail_closed(self):
        report = self._probe(version_lookup=version_lookup({"browser-use": "0.13.9"}))
        self.assertIs(report["ready"], False)
        self.assertIn("missing_distribution:playwright", report["reasons"])
        self.assertIn("version_mismatch:browser-use:0.13.9", report["reasons"])
        self.assertIsNone(report["installed"]["playwright"])

    def test_lookup_failure_is_a_missing_distribution_not_a_crash(self):
        report = self._probe(version_lookup=version_lookup(RuntimeError("metadata unavailable")))
        self.assertIs(report["ready"], False)
        self.assertEqual(
            report["reasons"],
            ["missing_distribution:browser-use", "missing_distribution:playwright"],
        )
        self.assertEqual(report["installed"], {"browser-use": None, "playwright": None})

    def test_real_adapter_accepts_the_legacy_context_argument(self):
        self.assertEqual(
            runtime.probe_binding(adapter, LegacyAgent),
            (runtime.BINDING_OK, ("browser_context",)),
        )

    def test_real_adapter_refuses_a_keyword_catch_all(self):
        self.assertEqual(
            runtime.probe_binding(adapter, CatchAllAgent),
            ("unsupported:browser_use_agent_has_no_browser_argument", ()),
        )

    def test_real_adapter_modern_path_fails_closed_without_the_package(self):
        with mock.patch.dict(sys.modules, {"browser_use": None}):
            self.assertEqual(
                runtime.probe_binding(adapter, ModernAgent),
                ("session_construction_failed", ()),
            )

    def test_unsupported_binding_is_reported_with_the_adapter_reason(self):
        report = self._probe(
            adapter_loader=lambda: fake_adapter(
                adapter.BrowserUseApiUnsupported("browser_use_agent_has_no_browser_argument")
            )
        )
        self.assertEqual(
            report["binding"],
            "unsupported:browser_use_agent_has_no_browser_argument",
        )
        self.assertIn(
            "binding:unsupported:browser_use_agent_has_no_browser_argument",
            report["reasons"],
        )

    def test_empty_binding_result_is_not_ready(self):
        report = self._probe(adapter_loader=lambda: fake_adapter({}))
        self.assertEqual(report["binding"], "no_browser_argument")
        self.assertEqual(report["binding_arguments"], [])
        self.assertIn("binding:no_browser_argument", report["reasons"])

    def test_adapter_and_agent_import_failures_are_distinct(self):
        adapter_report = self._probe(adapter_loader=raiser(ImportError("no adapter")))
        self.assertEqual(adapter_report["binding"], "adapter_import_failed")
        agent_report = self._probe(agent_loader=raiser(ImportError("no browser_use")))
        self.assertEqual(agent_report["binding"], "browser_use_import_failed")

    def test_chromium_tristate_codes(self):
        cases = [
            (lambda: True, True, []),
            (lambda: False, False, ["chromium_not_installed"]),
            (lambda: None, None, ["chromium_unknown"]),
        ]
        for probe, value, expected in cases:
            with self.subTest(value=value):
                report = self._probe(chromium_probe=probe)
                self.assertIs(report["chromium_installed"], value)
                self.assertEqual(report["reasons"], expected)
                self.assertIs(report["ready"], not expected)

    def test_chromium_probe_failure_is_uncertainty(self):
        report = self._probe(chromium_probe=raiser(RuntimeError("driver missing")))
        self.assertIsNone(report["chromium_installed"])
        self.assertEqual(report["reasons"], ["chromium_unknown"])


class BrowserRuntimeCliTests(unittest.TestCase):
    REPORT = {
        "ready": True,
        "project": "browser/pyproject.toml",
        "pinned": {"browser-use": "0.13.10", "playwright": "1.63.0"},
        "installed": {"browser-use": "0.13.10", "playwright": "1.63.0"},
        "binding": "ok",
        "chromium_installed": True,
        "reasons": [],
    }

    def _run(self, argv, report=None, error=None):
        out, err = io.StringIO(), io.StringIO()
        target = mock.patch.object(
            runtime,
            "probe_browser_runtime",
            **({"side_effect": error} if error is not None else {"return_value": report}),
        )
        with target, redirect_stdout(out), redirect_stderr(err):
            code = runtime.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_json_ready_report_exits_zero(self):
        code, out, _ = self._run(["--json"], report=dict(self.REPORT))
        self.assertEqual(code, 0)
        self.assertEqual(out.strip().count("\n"), 0)
        self.assertIs(json.loads(out)["ready"], True)

    def test_json_not_ready_report_exits_one(self):
        report = dict(self.REPORT, ready=False, reasons=["chromium_unknown"])
        code, out, _ = self._run(["--json"], report=report)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["reasons"], ["chromium_unknown"])

    def test_probe_crash_is_reported_and_never_propagates(self):
        code, out, err = self._run(["--json"], error=RuntimeError("boom"))
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertIs(payload["ready"], False)
        self.assertEqual(payload["reasons"], ["probe_crashed"])
        self.assertIn("RuntimeError", err)

    def test_human_output_lists_reasons(self):
        report = dict(self.REPORT, ready=False, chromium_installed=None, reasons=["chromium_unknown"])
        code, out, _ = self._run([], report=report)
        self.assertEqual(code, 1)
        self.assertIn("ready=False", out)
        self.assertIn("- chromium_unknown", out)


if __name__ == "__main__":
    unittest.main()
