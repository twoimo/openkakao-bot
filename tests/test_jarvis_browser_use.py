"""Contract tests for the dedicated Browser-Use/Playwright adapter.

The adapter must bind Browser-Use to the browser this module owns and must not
send screenshots to the text-only local gateway. These tests pin the failure
modes that are invisible at runtime: a release that silently swallows an
argument through \`**kwargs\`, and a release whose \`Agent\` declares none of the
known arguments. Both must fail closed.
"""

from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from jarvis_abort import AbortToken  # noqa: E402
from jarvis_browser_use import (  # noqa: E402
    AGENT_BROWSER_KWARG,
    AGENT_CONTEXT_KWARG,
    CDP_URL_ATTR,
    TELEMETRY_ENV_VAR,
    TEXT_ONLY_AGENT_KWARGS,
    BrowserUseApiUnsupported,
    BrowserUseRunner,
    DedicatedPlaywrightContext,
    browser_agent_kwargs,
    declared_agent_kwargs,
    disable_browser_use_telemetry,
)


class _AgentDeclaringContextKwarg:
    def __init__(self, *, task, llm, browser_context=None, **kwargs):
        self.task = task
        self.llm = llm
        self.browser_context = browser_context
        self.extra = kwargs


class _AgentDeclaringBrowserKwarg:
    def __init__(self, *, task, llm, browser=None, **kwargs):
        self.task = task
        self.llm = llm
        self.browser = browser
        self.extra = kwargs


class _AgentDeclaringOnlyCatchAll:
    def __init__(self, **kwargs):
        self.extra = kwargs


class _AgentDeclaringVisionSwitches:
    instances: list = []

    def __init__(
        self,
        *,
        task,
        llm,
        use_vision=None,
        generate_gif=None,
        use_judge=None,
        browser=None,
        **kwargs,
    ):
        self.task = task
        self.llm = llm
        self.use_vision = use_vision
        self.generate_gif = generate_gif
        self.use_judge = use_judge
        self.browser = browser
        self.extra = kwargs
        _AgentDeclaringVisionSwitches.instances.append(self)

    async def run(self, *args, **kwargs):
        return types.SimpleNamespace(final_result="done")


class _FakeProfile:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeSession:
    instances: list = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        _FakeSession.instances.append(self)


def _fake_browser_use_module() -> types.ModuleType:
    module = types.ModuleType("browser_use")
    module.BrowserProfile = _FakeProfile
    module.BrowserSession = _FakeSession
    return module


class BrowserArgumentSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeSession.instances = []

    def test_context_kwarg_is_used_when_the_release_declares_it(self):
        context = object()
        kwargs = browser_agent_kwargs(_AgentDeclaringContextKwarg, context)
        self.assertEqual(kwargs, {AGENT_CONTEXT_KWARG: context})

    def test_modern_browser_kwarg_binds_a_session_to_the_cdp_endpoint(self):
        context = types.SimpleNamespace()
        setattr(context, CDP_URL_ATTR, "http://127.0.0.1:45678")
        with mock.patch.dict(sys.modules, {"browser_use": _fake_browser_use_module()}):
            kwargs = browser_agent_kwargs(_AgentDeclaringBrowserKwarg, context)

        self.assertEqual(list(kwargs), [AGENT_BROWSER_KWARG])
        self.assertEqual(len(_FakeSession.instances), 1)
        session = _FakeSession.instances[0]
        self.assertEqual(session.kwargs["cdp_url"], "http://127.0.0.1:45678")
        profile = session.kwargs["browser_profile"]
        self.assertIsInstance(profile, _FakeProfile)
        self.assertTrue(profile.kwargs["headless"])
        self.assertFalse(profile.kwargs["enable_default_extensions"])
        self.assertFalse(profile.kwargs["accept_downloads"])

    def test_a_catch_all_alone_fails_closed(self):
        # browser_use 0.13 accepts browser_context= into **kwargs and then
        # ignores it, so a catch-all must not count as support.
        with self.assertRaises(BrowserUseApiUnsupported):
            browser_agent_kwargs(_AgentDeclaringOnlyCatchAll, object())

    def test_modern_kwarg_without_a_cdp_endpoint_fails_closed(self):
        with self.assertRaises(BrowserUseApiUnsupported):
            browser_agent_kwargs(_AgentDeclaringBrowserKwarg, object())


class TextOnlySwitchTests(unittest.TestCase):
    def test_only_declared_switches_are_kept(self):
        # A catch-all must not be treated as support, or the switch is dropped
        # and every step fails against the text-only gateway.
        self.assertEqual(
            declared_agent_kwargs(_AgentDeclaringOnlyCatchAll, TEXT_ONLY_AGENT_KWARGS), {}
        )
        kept = declared_agent_kwargs(_AgentDeclaringVisionSwitches, TEXT_ONLY_AGENT_KWARGS)
        self.assertEqual(kept, {"use_vision": False, "generate_gif": False, "use_judge": False})


class TelemetryTests(unittest.TestCase):
    def test_environment_variable_is_forced_off(self):
        environ = {TELEMETRY_ENV_VAR: "True"}
        with mock.patch.dict(sys.modules, {"browser_use": None}):
            disable_browser_use_telemetry(environ)
        self.assertEqual(environ[TELEMETRY_ENV_VAR], "False")

    def test_config_attribute_is_forced_off_for_an_already_imported_package(self):
        config = types.SimpleNamespace(ANONYMIZED_TELEMETRY=True)
        config_module = types.ModuleType("browser_use.config")
        config_module.CONFIG = config
        package = types.ModuleType("browser_use")
        package.config = config_module
        with mock.patch.dict(
            sys.modules,
            {"browser_use": package, "browser_use.config": config_module},
        ):
            disable_browser_use_telemetry({})
        self.assertFalse(config.ANONYMIZED_TELEMETRY)

    def test_a_missing_package_is_not_an_error(self):
        with mock.patch.dict(sys.modules, {"browser_use": None}):
            environ = {}
            disable_browser_use_telemetry(environ)
        self.assertEqual(environ[TELEMETRY_ENV_VAR], "False")


class _FakePlaywrightContext:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakeBrowser:
    def __init__(self, recorder: dict) -> None:
        self.recorder = recorder
        self.closed = False

    async def new_context(self) -> _FakePlaywrightContext:
        self.recorder["context"] = _FakePlaywrightContext()
        return self.recorder["context"]

    async def close(self) -> None:
        self.closed = True


class _FakeChromium:
    def __init__(self, recorder: dict) -> None:
        self.recorder = recorder

    async def launch(self, **kwargs) -> _FakeBrowser:
        self.recorder["launch"] = kwargs
        self.recorder["browser"] = _FakeBrowser(self.recorder)
        return self.recorder["browser"]


class _FakePlaywright:
    def __init__(self, recorder: dict) -> None:
        self.recorder = recorder
        self.chromium = _FakeChromium(recorder)

    async def stop(self) -> None:
        self.recorder["stopped"] = True


class _FakePlaywrightStarter:
    """Playwright's async_playwright() is sync and its .start() is awaitable."""

    def __init__(self, recorder: dict) -> None:
        self.recorder = recorder

    async def start(self) -> _FakePlaywright:
        return _FakePlaywright(self.recorder)


def _fake_playwright_modules(recorder: dict) -> dict:
    async_api = types.ModuleType("playwright.async_api")
    async_api.async_playwright = lambda: _FakePlaywrightStarter(recorder)
    package = types.ModuleType("playwright")
    package.async_api = async_api
    return {"playwright": package, "playwright.async_api": async_api}


def _fake_modules_with_agent(agent_class, recorder: dict) -> dict:
    package = _fake_browser_use_module()
    package.Agent = agent_class
    llm_module = types.ModuleType("browser_use.llm")
    llm_module.ChatOpenAI = lambda **kwargs: types.SimpleNamespace(**kwargs)
    package.llm = llm_module
    modules = _fake_playwright_modules(recorder)
    modules["browser_use"] = package
    modules["browser_use.llm"] = llm_module
    return modules


class DedicatedContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_exposes_a_loopback_devtools_endpoint(self):
        recorder = {}
        context_holder = DedicatedPlaywrightContext()
        with mock.patch.dict(sys.modules, _fake_playwright_modules(recorder)):
            context = await context_holder.start()

        launch = recorder["launch"]
        self.assertTrue(launch["headless"])
        args = list(launch["args"])
        self.assertTrue(any(arg.startswith("--remote-debugging-port=") for arg in args))
        self.assertIn("--remote-debugging-address=127.0.0.1", args)
        self.assertFalse(any(arg.startswith("--user-data-dir") for arg in args))

        port = next(
            arg.split("=", 1)[1] for arg in args if arg.startswith("--remote-debugging-port=")
        )
        self.assertTrue(port.isdigit() and int(port) > 0)
        self.assertEqual(context_holder.cdp_url, "http://127.0.0.1:" + port)
        self.assertEqual(getattr(context, CDP_URL_ATTR, ""), context_holder.cdp_url)

        await context_holder.close()
        self.assertTrue(recorder["context"].closed)
        self.assertTrue(recorder["browser"].closed)
        self.assertTrue(recorder["stopped"])
        self.assertEqual(context_holder.cdp_url, "")

    async def test_runner_fails_closed_when_the_agent_api_is_unsupported(self):
        recorder = {}
        with tempfile.TemporaryDirectory() as temp_dir:
            token = AbortToken(Path(temp_dir) / "jarvis-abort.json")
            owned = DedicatedPlaywrightContext()
            modules = _fake_modules_with_agent(_AgentDeclaringOnlyCatchAll, recorder)

            runner = BrowserUseRunner(token, context_factory=lambda: owned)
            with mock.patch.dict(sys.modules, modules):
                result = await runner.run("local-only task")

            self.assertFalse(result.ok)
            self.assertEqual(result.error_code, "browser_job_failed")
            self.assertIsNone(runner._owned_context)
            self.assertTrue(recorder["browser"].closed)

    async def test_default_adapter_runs_text_only_on_the_dedicated_browser(self):
        _AgentDeclaringVisionSwitches.instances = []
        _FakeSession.instances = []
        recorder = {}
        with tempfile.TemporaryDirectory() as temp_dir:
            token = AbortToken(Path(temp_dir) / "jarvis-abort.json")
            owned = DedicatedPlaywrightContext()
            modules = _fake_modules_with_agent(_AgentDeclaringVisionSwitches, recorder)

            runner = BrowserUseRunner(token, context_factory=lambda: owned)
            with mock.patch.dict(sys.modules, modules):
                result = await runner.run("local-only task")

        self.assertTrue(result.ok, result.error_code)
        self.assertEqual(result.result, "done")
        self.assertEqual(len(_AgentDeclaringVisionSwitches.instances), 1)
        agent = _AgentDeclaringVisionSwitches.instances[0]
        self.assertFalse(agent.use_vision)
        self.assertFalse(agent.generate_gif)
        self.assertFalse(agent.use_judge)
        self.assertEqual(list(agent.extra), [])
        self.assertEqual(len(_FakeSession.instances), 1)
        self.assertIs(agent.browser, _FakeSession.instances[0])
        cdp_url = _FakeSession.instances[0].kwargs["cdp_url"]
        self.assertTrue(cdp_url.startswith("http://127.0.0.1:"), cdp_url)


if __name__ == "__main__":
    unittest.main()

