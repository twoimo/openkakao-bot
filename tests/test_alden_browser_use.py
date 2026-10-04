"""Contract tests for the dedicated Browser-Use/Playwright adapter.

The adapter must bind Browser-Use to the browser this module owns and must not
send screenshots to the text-only local gateway. These tests pin the failure
modes that are invisible at runtime: a release that silently swallows an
argument through `**kwargs`, and a release whose `Agent` declares none of the
known arguments. Both must fail closed.
"""

from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
import urllib.request
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from alden_abort import AbortToken  # noqa: E402
from auto_reply_ondevice import FLASH_NEXT_MODEL_ID, QWEN38_27B_MODEL_ID  # noqa: E402
from alden_browser_use import (  # noqa: E402
    AGENT_BROWSER_KWARG,
    AGENT_CONTEXT_KWARG,
    CDP_URL_ATTR,
    DEFAULT_BROWSER_MODEL_ID,
    LOCAL_MLX_BASE_URL,
    LOCAL_MLX_MODELS_MAX_BYTES,
    LOCAL_MLX_MODELS_TIMEOUT_SECS,
    LOCAL_MLX_MODELS_URL,
    TELEMETRY_ENV_VAR,
    TEXT_ONLY_AGENT_KWARGS,
    DOCUMENT_TITLE_EVIDENCE_KIND,
    FIRST_SEARCH_RESULT_EVIDENCE_KIND,
    WIKIPEDIA_FIRST_RESULT_SELECTOR,
    BrowserUseApiUnsupported,
    BrowserUseRunner,
    DedicatedPlaywrightContext,
    LocalModelCatalogRejected,
    _RejectRedirects,
    _collect_dom_evidence,
    _local_only_urlopen,
    browser_agent_kwargs,
    declared_agent_kwargs,
    disable_browser_use_telemetry,
    read_local_model_catalog,
    require_selected_model_ready,
)
from local_mlx_gateway import MlxRequestAdmissionClosed  # noqa: E402


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
        register_new_step_callback=None,
        **kwargs,
    ):
        self.task = task
        self.llm = llm
        self.use_vision = use_vision
        self.generate_gif = generate_gif
        self.use_judge = use_judge
        self.browser = browser
        self.register_new_step_callback = register_new_step_callback
        self.extra = kwargs
        _AgentDeclaringVisionSwitches.instances.append(self)

    async def run(self, *args, **kwargs):
        if self.register_new_step_callback is not None:
            await self.register_new_step_callback(None, None, 1)
        return types.SimpleNamespace(final_result="done")


class _FakeProfile:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeSession:
    instances: list = []
    page = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        _FakeSession.instances.append(self)

    async def get_current_page(self):
        return self.page


class _FakeActorPage:
    def __init__(self, *, url: str, title: str = "", first_result: str = "") -> None:
        self.url = url
        self.title = title
        self.first_result = first_result
        self.evaluations: list[str] = []

    async def evaluate(self, expression: str) -> str:
        self.evaluations.append(expression)
        if expression == "() => window.location.href":
            return self.url
        if expression == "() => document.title":
            return self.title
        if WIKIPEDIA_FIRST_RESULT_SELECTOR in expression:
            return self.first_result
        raise AssertionError(f"unexpected DOM expression: {expression}")


class _SlowActorPage(_FakeActorPage):
    async def evaluate(self, expression: str) -> str:
        import asyncio

        await asyncio.sleep(0.02)
        return await super().evaluate(expression)


def _fake_browser_use_module() -> types.ModuleType:
    module = types.ModuleType("browser_use")
    module.BrowserProfile = _FakeProfile
    module.BrowserSession = _FakeSession
    return module


def _ready_catalog(model: str = QWEN38_27B_MODEL_ID) -> bytes:
    return json.dumps(
        {
            "data": [
                {
                    "id": model.removeprefix("mlx/"),
                    "loaded": True,
                    "state": "ready",
                }
            ]
        }
    ).encode("utf-8")


class _CatalogResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.read_limits: list[int] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit: int) -> bytes:
        self.read_limits.append(limit)
        return self.payload[:limit]


class LocalModelCatalogTests(unittest.TestCase):
    def test_default_browser_model_is_qwen38_27b(self):
        self.assertEqual(DEFAULT_BROWSER_MODEL_ID, QWEN38_27B_MODEL_ID)

    def test_reader_uses_only_fixed_local_get_with_bounded_read(self):
        response = _CatalogResponse(_ready_catalog())
        calls = []

        def opener(request, *, timeout):
            calls.append((request.full_url, request.get_method(), timeout))
            return response

        raw = read_local_model_catalog(opener=opener)

        self.assertEqual(raw, _ready_catalog())
        self.assertEqual(
            calls,
            [(LOCAL_MLX_MODELS_URL, "GET", LOCAL_MLX_MODELS_TIMEOUT_SECS)],
        )
        self.assertEqual(response.read_limits, [LOCAL_MLX_MODELS_MAX_BYTES + 1])

    def test_default_local_opener_disables_proxies_and_redirects(self):
        request = urllib.request.Request(LOCAL_MLX_MODELS_URL, method="GET")
        director = mock.Mock()
        response = _CatalogResponse(_ready_catalog())
        director.open.return_value = response
        with mock.patch(
            "alden_browser_use.urllib.request.build_opener",
            return_value=director,
        ) as build_opener:
            self.assertIs(
                _local_only_urlopen(request, timeout=LOCAL_MLX_MODELS_TIMEOUT_SECS),
                response,
            )

        handlers = build_opener.call_args.args
        self.assertEqual(len(handlers), 2)
        self.assertIsInstance(handlers[0], urllib.request.ProxyHandler)
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsInstance(handlers[1], _RejectRedirects)
        self.assertIsNone(
            handlers[1].redirect_request(
                request, None, 302, "redirect", {}, "https://example.invalid/"
            )
        )

    def test_oversized_catalog_is_rejected_after_one_bounded_read(self):
        response = _CatalogResponse(b"x" * (LOCAL_MLX_MODELS_MAX_BYTES + 1))
        with self.assertRaises(LocalModelCatalogRejected) as raised:
            read_local_model_catalog(opener=lambda _request, *, timeout: response)
        self.assertEqual(raised.exception.code, "mlx_model_catalog_malformed")
        self.assertEqual(response.read_limits, [LOCAL_MLX_MODELS_MAX_BYTES + 1])

    def test_selected_model_requires_one_exact_loaded_ready_row(self):
        require_selected_model_ready(QWEN38_27B_MODEL_ID, _ready_catalog())
        require_selected_model_ready(FLASH_NEXT_MODEL_ID, _ready_catalog(FLASH_NEXT_MODEL_ID))

        cases = [
            (
                json.dumps({"data": []}).encode(),
                "mlx_model_catalog_model_absent",
            ),
            (
                json.dumps(
                    {
                        "data": [
                            {
                                "id": QWEN38_27B_MODEL_ID.removeprefix("mlx/"),
                                "loaded": True,
                                "state": "ready",
                            },
                            {
                                "id": QWEN38_27B_MODEL_ID.removeprefix("mlx/"),
                                "loaded": True,
                                "state": "ready",
                            },
                        ]
                    }
                ).encode(),
                "mlx_model_catalog_duplicate_model",
            ),
            (b"{not-json", "mlx_model_catalog_malformed"),
            (
                json.dumps(
                    {
                        "data": [
                            {
                                "id": QWEN38_27B_MODEL_ID.removeprefix("mlx/"),
                                "loaded": "true",
                                "state": "ready",
                            }
                        ]
                    }
                ).encode(),
                "mlx_model_catalog_malformed",
            ),
            (
                json.dumps(
                    {
                        "data": [
                            {
                                "id": QWEN38_27B_MODEL_ID.removeprefix("mlx/"),
                                "loaded": False,
                                "state": "unloaded",
                            }
                        ]
                    }
                ).encode(),
                "mlx_model_catalog_model_unready",
            ),
            (
                json.dumps(
                    {
                        "data": [
                            {
                                "id": QWEN38_27B_MODEL_ID,
                                "loaded": True,
                                "state": "ready",
                            }
                        ]
                    }
                ).encode(),
                "mlx_model_catalog_model_absent",
            ),
        ]
        for raw, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                with self.assertRaises(LocalModelCatalogRejected) as raised:
                    require_selected_model_ready(QWEN38_27B_MODEL_ID, raw)
                self.assertEqual(raised.exception.code, expected_code)

    def test_only_the_two_fixed_local_models_are_selectable(self):
        with self.assertRaises(LocalModelCatalogRejected) as raised:
            require_selected_model_ready("gpt-5.6-sol", _ready_catalog())
        self.assertEqual(raised.exception.code, "browser_model_not_allowed")


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


class _RunnerOwnedContext:
    def __init__(self, events: list) -> None:
        self.events = events
        self.context = object()
        self.closed = False

    async def start(self):
        self.events.append("context_start")
        return self.context

    async def close(self) -> None:
        self.events.append("context_close")
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
    def setUp(self) -> None:
        _FakeSession.page = None

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
            token = AbortToken(Path(temp_dir) / "alden-abort.json")
            owned = DedicatedPlaywrightContext()
            modules = _fake_modules_with_agent(_AgentDeclaringOnlyCatchAll, recorder)

            runner = BrowserUseRunner(
                token,
                context_factory=lambda: owned,
                catalog_reader=_ready_catalog,
                state_root=Path(temp_dir),
            )
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
            token = AbortToken(Path(temp_dir) / "alden-abort.json")
            owned = DedicatedPlaywrightContext()
            modules = _fake_modules_with_agent(_AgentDeclaringVisionSwitches, recorder)

            runner = BrowserUseRunner(
                token,
                context_factory=lambda: owned,
                catalog_reader=_ready_catalog,
                state_root=Path(temp_dir),
            )
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

    async def test_document_title_is_grounded_from_live_cdp_dom(self):
        _AgentDeclaringVisionSwitches.instances = []
        _FakeSession.instances = []
        _FakeSession.page = _FakeActorPage(
            url="https://example.com/",
            title="Example Domain",
        )
        recorder = {}
        with tempfile.TemporaryDirectory() as temp_dir:
            token = AbortToken(Path(temp_dir) / "alden-abort.json")
            owned = DedicatedPlaywrightContext()
            modules = _fake_modules_with_agent(_AgentDeclaringVisionSwitches, recorder)
            runner = BrowserUseRunner(
                token,
                context_factory=lambda: owned,
                catalog_reader=_ready_catalog,
                state_root=Path(temp_dir),
            )
            with mock.patch.dict(sys.modules, modules):
                result = await runner.run(
                    "Navigate to https://example.com and return only the document title"
                )

        self.assertTrue(result.ok, result.error_code)
        self.assertEqual(result.result, "Example Domain")
        self.assertEqual(len(result.evidence), 1)
        self.assertEqual(result.evidence[0].kind, DOCUMENT_TITLE_EVIDENCE_KIND)
        self.assertEqual(result.evidence[0].value, "Example Domain")
        self.assertEqual(result.evidence[0].url, "https://example.com/")
        self.assertEqual(result.evidence[0].source, "document.title")
        self.assertIn("() => document.title", _FakeSession.page.evaluations)

    async def test_wikipedia_first_result_is_grounded_from_fixed_dom_selector(self):
        _AgentDeclaringVisionSwitches.instances = []
        _FakeSession.instances = []
        _FakeSession.page = _FakeActorPage(
            url="https://en.wikipedia.org/wiki/Special:Search?search=Tauri+software",
            title="Search results - Wikipedia",
            first_result="Tauri (software framework)",
        )
        recorder = {}
        with tempfile.TemporaryDirectory() as temp_dir:
            token = AbortToken(Path(temp_dir) / "alden-abort.json")
            owned = DedicatedPlaywrightContext()
            modules = _fake_modules_with_agent(_AgentDeclaringVisionSwitches, recorder)
            runner = BrowserUseRunner(
                token,
                context_factory=lambda: owned,
                catalog_reader=_ready_catalog,
                state_root=Path(temp_dir),
            )
            with mock.patch.dict(sys.modules, modules):
                result = await runner.run(
                    "Open Wikipedia Special:Search for Tauri software and return only the first search result"
                )

        self.assertTrue(result.ok, result.error_code)
        self.assertEqual(result.result, "Tauri (software framework)")
        self.assertEqual(len(result.evidence), 1)
        self.assertEqual(result.evidence[0].kind, FIRST_SEARCH_RESULT_EVIDENCE_KIND)
        self.assertEqual(result.evidence[0].value, "Tauri (software framework)")
        self.assertEqual(result.evidence[0].source, WIKIPEDIA_FIRST_RESULT_SELECTOR)
        self.assertTrue(
            any(
                WIKIPEDIA_FIRST_RESULT_SELECTOR in expression
                for expression in _FakeSession.page.evaluations
            )
        )

    async def test_dom_evidence_collection_has_a_hard_timeout(self):
        session = _FakeSession()
        session.page = _SlowActorPage(
            url="https://example.com/",
            title="Example Domain",
        )
        with mock.patch("alden_browser_use.DOM_EVIDENCE_TIMEOUT_SECS", 0.001):
            evidence = await _collect_dom_evidence(
                session,
                frozenset({DOCUMENT_TITLE_EVIDENCE_KIND}),
            )
        self.assertEqual(evidence, ())

    async def test_document_title_without_dom_evidence_is_not_reported_as_success(self):
        _FakeSession.page = None
        recorder = {}
        with tempfile.TemporaryDirectory() as temp_dir:
            token = AbortToken(Path(temp_dir) / "alden-abort.json")
            owned = DedicatedPlaywrightContext()
            modules = _fake_modules_with_agent(_AgentDeclaringVisionSwitches, recorder)
            runner = BrowserUseRunner(
                token,
                context_factory=lambda: owned,
                catalog_reader=_ready_catalog,
                state_root=Path(temp_dir),
            )
            with mock.patch.dict(sys.modules, modules):
                result = await runner.run("Return only the document title")
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "browser_job_failed")
        self.assertEqual(result.result, "")


class RunnerCatalogGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_rejection_happens_before_context_or_agent(self):
        cases = [
            (json.dumps({"data": []}).encode(), "mlx_model_catalog_model_absent"),
            (
                json.dumps(
                    {
                        "data": [
                            {
                                "id": QWEN38_27B_MODEL_ID.removeprefix("mlx/"),
                                "loaded": True,
                                "state": "ready",
                            },
                            {
                                "id": QWEN38_27B_MODEL_ID.removeprefix("mlx/"),
                                "loaded": True,
                                "state": "ready",
                            },
                        ]
                    }
                ).encode(),
                "mlx_model_catalog_duplicate_model",
            ),
            (b"{not-json", "mlx_model_catalog_malformed"),
            (
                json.dumps(
                    {
                        "data": [
                            {
                                "id": QWEN38_27B_MODEL_ID.removeprefix("mlx/"),
                                "loaded": False,
                                "state": "unloaded",
                            }
                        ]
                    }
                ).encode(),
                "mlx_model_catalog_model_unready",
            ),
        ]
        for raw_catalog, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                events = []

                def context_factory():
                    events.append("context_factory")
                    return _RunnerOwnedContext(events)

                async def agent_factory(*_args):
                    events.append("agent")
                    return "unexpected"

                with tempfile.TemporaryDirectory() as temp_dir:
                    runner = BrowserUseRunner(
                        AbortToken(Path(temp_dir) / "alden-abort.json"),
                        context_factory=context_factory,
                        agent_factory=agent_factory,
                        catalog_reader=lambda: raw_catalog,
                        state_root=Path(temp_dir),
                    )
                    result = await runner.run("local-only task")

                self.assertFalse(result.ok)
                self.assertEqual(result.error_code, expected_code)
                self.assertEqual(events, [])
                self.assertIsNone(runner._owned_context)

    async def test_ready_catalog_precedes_context_and_default_qwen_agent_request(self):
        events = []
        owned = _RunnerOwnedContext(events)

        def catalog_reader():
            events.append("catalog")
            return _ready_catalog()

        def context_factory():
            events.append("context_factory")
            return owned

        async def agent_factory(task, context, model, base_url):
            events.append(("agent", task, context, model, base_url))
            return "done"

        with tempfile.TemporaryDirectory() as temp_dir:
            runner = BrowserUseRunner(
                AbortToken(Path(temp_dir) / "alden-abort.json"),
                context_factory=context_factory,
                agent_factory=agent_factory,
                catalog_reader=catalog_reader,
                state_root=Path(temp_dir),
            )
            result = await runner.run("local-only task")

        self.assertTrue(result.ok, result.error_code)
        self.assertEqual(result.result, "done")
        self.assertEqual(events[:3], ["catalog", "context_factory", "context_start"])
        self.assertEqual(events[3][0], "agent")
        self.assertEqual(events[3][1], "local-only task")
        self.assertIs(events[3][2], owned.context)
        self.assertEqual(events[3][3], QWEN38_27B_MODEL_ID)
        self.assertEqual(events[3][4], LOCAL_MLX_BASE_URL)
        self.assertEqual(events[4], "context_close")
        self.assertTrue(owned.closed)

    async def test_explicit_flash_next_requires_its_ready_row_and_reaches_agent(self):
        events = []
        owned = _RunnerOwnedContext(events)

        async def agent_factory(_task, _context, model, _base_url):
            events.append(("agent_model", model))
            return "done"

        with tempfile.TemporaryDirectory() as temp_dir:
            runner = BrowserUseRunner(
                AbortToken(Path(temp_dir) / "alden-abort.json"),
                context_factory=lambda: owned,
                agent_factory=agent_factory,
                catalog_reader=lambda: _ready_catalog(FLASH_NEXT_MODEL_ID),
                model=FLASH_NEXT_MODEL_ID,
                state_root=Path(temp_dir),
            )
            result = await runner.run("local-only task")

        self.assertTrue(result.ok, result.error_code)
        self.assertIn(("agent_model", FLASH_NEXT_MODEL_ID), events)

    async def test_non_allowlisted_model_fails_before_context_or_agent(self):
        events = []

        def context_factory():
            events.append("context_factory")
            return _RunnerOwnedContext(events)

        async def agent_factory(*_args):
            events.append("agent")
            return "unexpected"

        with tempfile.TemporaryDirectory() as temp_dir:
            runner = BrowserUseRunner(
                AbortToken(Path(temp_dir) / "alden-abort.json"),
                context_factory=context_factory,
                agent_factory=agent_factory,
                catalog_reader=_ready_catalog,
                model="gpt-5.6-sol",
                state_root=Path(temp_dir),
            )
            result = await runner.run("local-only task")

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "browser_model_not_allowed")
        self.assertEqual(events, [])

    async def test_closed_mlx_lease_prevents_catalog_context_and_agent(self):
        events = []

        def catalog_reader():
            events.append("catalog")
            return _ready_catalog()

        def context_factory():
            events.append("context_factory")
            return _RunnerOwnedContext(events)

        async def agent_factory(*_args):
            events.append("agent")
            return "unexpected"

        with tempfile.TemporaryDirectory() as temp_dir:
            runner = BrowserUseRunner(
                AbortToken(Path(temp_dir) / "alden-abort.json"),
                context_factory=context_factory,
                agent_factory=agent_factory,
                catalog_reader=catalog_reader,
                state_root=Path(temp_dir),
            )
            with mock.patch(
                "alden_browser_use.mlx_model_request_lease",
                side_effect=MlxRequestAdmissionClosed("model_swap_in_progress"),
            ):
                result = await runner.run("local-only task")

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "model_swap_in_progress")
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
