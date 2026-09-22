#!/usr/bin/env python3
"""Readiness probe for the provisioned Browser-Use runtime.

`scripts/jarvis_browser_use.py` binds a web job to whatever `browser_use` release
is installed, and that dependency is deliberately kept out of both the app bundle
and the voice environment. `browser/pyproject.toml` pins the pair that was
actually measured, and this probe answers whether the environment it runs in
satisfies that contract without installing anything, launching a browser, or
touching the network:

1. both distributions are installed at exactly the pinned versions,
2. the adapter's own `browser_agent_kwargs` still yields a usable binding, so a
   release that dropped the argument or changed the session constructor is
   caught before a job starts rather than mid-run,
3. the Playwright Chromium build is present, reported as a tri-state because a
   probe that cannot run is uncertainty and must not be reported as a missing
   browser.

The probe never changes application state and exits non-zero when the runtime is
not ready, so a launcher can refuse to start a browser job instead of failing
closed halfway through one.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import sys
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]

# Mirrors the pins in `browser/pyproject.toml`; a test asserts they stay equal.
RUNTIME_PROJECT = "browser/pyproject.toml"
PINNED_DISTRIBUTIONS: dict[str, str] = {
    "browser-use": "0.13.10",
    "playwright": "1.63.0",
}

# The binding check only reads the DevTools endpoint off the context, so a stub
# carrying that one attribute is enough. It must never be reachable.
PROBE_CDP_URL = "http://127.0.0.1:1"
BINDING_OK = "ok"


def _default_distribution_version(name: str) -> str:
    return importlib.metadata.version(name)


def _default_adapter_loader() -> Any:
    import jarvis_browser_use

    return jarvis_browser_use


def _default_agent_loader() -> Any:
    from browser_use import Agent

    return Agent


def _default_chromium_probe() -> bool | None:
    """Report whether Playwright's Chromium build exists, or None if unknown."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    try:
        with sync_playwright() as playwright:
            executable = str(playwright.chromium.executable_path or "")
    except Exception:
        return None
    if not executable:
        return False
    return Path(executable).exists()


def _probe_context(adapter: Any) -> Any:
    """Build the smallest context the adapter's binding check can read."""
    attribute = str(getattr(adapter, "CDP_URL_ATTR", "jarvis_cdp_url"))
    return type("ProbeContext", (), {attribute: PROBE_CDP_URL})()


def probe_binding(
    adapter: Any,
    agent_class: Any,
) -> tuple[str, tuple[str, ...]]:
    """Return the adapter's binding decision and the keywords it chose.

    The keyword names are reported because they say which Browser-Use binding
    style the installed release accepts, which is the fact a reviewer needs to
    read the runtime against the adapter's contract.
    """
    try:
        kwargs = adapter.browser_agent_kwargs(agent_class, _probe_context(adapter))
    except adapter.BrowserUseApiUnsupported as exc:
        return f"unsupported:{exc}", ()
    except Exception:
        return "session_construction_failed", ()
    if not kwargs:
        return "no_browser_argument", ()
    return BINDING_OK, tuple(sorted(str(name) for name in kwargs))


def _resolve_binding(
    adapter_loader: Callable[[], Any],
    agent_loader: Callable[[], Any],
) -> tuple[str, tuple[str, ...]]:
    try:
        adapter = adapter_loader()
    except Exception:
        return "adapter_import_failed", ()
    try:
        agent_class = agent_loader()
    except Exception:
        return "browser_use_import_failed", ()
    return probe_binding(adapter, agent_class)


def probe_browser_runtime(
    *,
    version_lookup: Callable[[str], str] | None = None,
    adapter_loader: Callable[[], Any] | None = None,
    agent_loader: Callable[[], Any] | None = None,
    chromium_probe: Callable[[], bool | None] | None = None,
) -> dict[str, Any]:
    """Return a JSON-ready readiness report; every step fails closed."""
    lookup = version_lookup or _default_distribution_version
    installed: dict[str, str | None] = {}
    reasons: list[str] = []
    for name, expected in PINNED_DISTRIBUTIONS.items():
        try:
            found: str | None = lookup(name)
        except Exception:
            found = None
        installed[name] = found
        if found is None:
            reasons.append(f"missing_distribution:{name}")
        elif found != expected:
            reasons.append(f"version_mismatch:{name}:{found}")

    binding, binding_arguments = _resolve_binding(
        adapter_loader or _default_adapter_loader,
        agent_loader or _default_agent_loader,
    )
    if binding != BINDING_OK:
        reasons.append(f"binding:{binding}")

    try:
        chromium = (chromium_probe or _default_chromium_probe)()
    except Exception:
        chromium = None
    if chromium is False:
        reasons.append("chromium_not_installed")
    elif chromium is None:
        reasons.append("chromium_unknown")

    return {
        "ready": not reasons,
        "project": RUNTIME_PROJECT,
        "pinned": dict(PINNED_DISTRIBUTIONS),
        "installed": installed,
        "binding": binding,
        "binding_arguments": list(binding_arguments),
        "chromium_installed": chromium,
        "reasons": reasons,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Browser-Use runtime readiness probe.")
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON on stdout.")
    args = parser.parse_args(argv)

    try:
        report = probe_browser_runtime()
    except Exception as exc:  # noqa: BLE001 - a probe must never crash its caller.
        print(f"browser runtime probe failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        report = {
            "ready": False,
            "project": RUNTIME_PROJECT,
            "pinned": dict(PINNED_DISTRIBUTIONS),
            "installed": {},
            "binding": "probe_crashed",
            "binding_arguments": [],
            "chromium_installed": None,
            "reasons": ["probe_crashed"],
        }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(
            f"ready={report['ready']} binding={report['binding']} "
            f"chromium={report['chromium_installed']} project={report['project']}"
        )
        for reason in report["reasons"]:
            print(f"- {reason}")
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
