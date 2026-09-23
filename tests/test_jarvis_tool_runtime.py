from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from auto_reply_ax_ui import AX_ABORT_FOCUS_REQUIRED, AX_ABORT_GLOBAL
from auto_reply_ondevice import FLASH_NEXT_MODEL_ID
from jarvis_abort import AbortController, read_abort_state
from jarvis_browser_use import (
    BrowserJobResult,
    BrowserUseRunner,
    DedicatedPlaywrightContext,
    LOCAL_MLX_BASE_URL,
)
from jarvis_tool_runtime import (
    MAX_AX_EXTENT,
    MAX_BROWSER_TASK_BYTES,
    MAX_TOOL_RESULT_BYTES,
    AxToolJob,
    BrowserToolJob,
    JarvisToolRuntime,
    ToolStatus,
)


class FakeOwnedContext:
    def __init__(self) -> None:
        self.context = object()
        self.started = False
        self.closed = False

    async def start(self):
        self.started = True
        return self.context

    async def close(self) -> None:
        self.closed = True


class StaticBrowserRunner:
    def __init__(self, outcome: BrowserJobResult) -> None:
        self.outcome = outcome
        self.calls = 0

    async def run(self, _task: str) -> BrowserJobResult:
        self.calls += 1
        return self.outcome


class ExplodingBrowserRunner:
    async def run(self, _task: str) -> BrowserJobResult:
        raise RuntimeError("Bearer secret at https://example.test/?token=do-not-log")


class JarvisToolRuntimeBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_browser_factory_is_fixed_to_owned_context(self):
        with TemporaryDirectory() as temp_dir:
            runner = StaticBrowserRunner(BrowserJobResult(True, result="ok"))
            with mock.patch("jarvis_tool_runtime.BrowserUseRunner", return_value=runner) as factory:
                runtime = JarvisToolRuntime(Path(temp_dir))
                result = await runtime.run_browser(BrowserToolJob("browser-default", "task"))

            self.assertTrue(result.ok)
            factory.assert_called_once()
            self.assertIs(factory.call_args.kwargs["context_factory"], DedicatedPlaywrightContext)
            self.assertNotIn("profile", factory.call_args.kwargs)
            self.assertEqual(LOCAL_MLX_BASE_URL, "http://127.0.0.1:11234/v1")

    async def test_owned_context_loopback_binding_cleanup_and_redacted_events(self):
        with TemporaryDirectory() as temp_dir:
            owned = FakeOwnedContext()
            seen: dict[str, object] = {}
            events = []
            secret_task = "visit https://example.test/?token=secret and inspect private chat body"

            async def agent(task, context, model, base_url):
                seen.update(task=task, context=context, model=model, base_url=base_url)
                return "bounded local result"

            def runner_factory(token):
                return BrowserUseRunner(
                    token,
                    context_factory=lambda: owned,
                    agent_factory=agent,
                    state_root=Path(temp_dir),
                )

            runtime = JarvisToolRuntime(
                Path(temp_dir),
                event_sink=events.append,
                clock=lambda: 123.5,
                _browser_runner_factory=runner_factory,
            )
            result = await runtime.run_browser(BrowserToolJob("browser-1", secret_task))

            self.assertTrue(result.ok)
            self.assertEqual(result.status, ToolStatus.COMPLETED)
            self.assertEqual(result.result, "bounded local result")
            self.assertTrue(owned.started)
            self.assertTrue(owned.closed)
            self.assertIs(seen["context"], owned.context)
            self.assertEqual(seen["task"], secret_task)
            self.assertEqual(seen["model"], FLASH_NEXT_MODEL_ID)
            self.assertEqual(seen["base_url"], LOCAL_MLX_BASE_URL)
            self.assertEqual(LOCAL_MLX_BASE_URL, "http://127.0.0.1:11234/v1")

            safe_events = [event.as_dict() for event in events]
            self.assertEqual([event["stage"] for event in safe_events], ["running", "completed"])
            self.assertEqual(
                set(safe_events[0]),
                {"jobId", "kind", "stage", "load", "time", "errorCode"},
            )
            serialized = repr(safe_events)
            self.assertNotIn(secret_task, serialized)
            self.assertNotIn("token=secret", serialized)
            self.assertNotIn("private chat body", serialized)

    async def test_agent_exception_still_closes_owned_context(self):
        with TemporaryDirectory() as temp_dir:
            owned = FakeOwnedContext()

            async def agent(_task, _context, _model, _base_url):
                raise RuntimeError("credential=do-not-log")

            runtime = JarvisToolRuntime(
                Path(temp_dir),
                _browser_runner_factory=lambda token: BrowserUseRunner(
                    token,
                    context_factory=lambda: owned,
                    agent_factory=agent,
                    state_root=Path(temp_dir),
                ),
            )
            result = await runtime.run_browser(BrowserToolJob("browser-failure", "safe task"))

            self.assertFalse(result.ok)
            self.assertEqual(result.error_code, "browser_job_failed")
            self.assertTrue(owned.closed)

    async def test_global_abort_is_latched_and_prevents_browser_and_ax_start(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller = AbortController(root)
            controller.abort("operator requested stop")
            runner_calls = 0
            ax_calls = 0

            def runner_factory(_token):
                nonlocal runner_calls
                runner_calls += 1
                return StaticBrowserRunner(BrowserJobResult(True, result="unreachable"))

            def ax_action() -> bool:
                nonlocal ax_calls
                ax_calls += 1
                return True

            runtime = JarvisToolRuntime(root, _browser_runner_factory=runner_factory)
            first = await runtime.run_browser(BrowserToolJob("browser-abort-1", "task"))
            second = await runtime.run_browser(BrowserToolJob("browser-abort-2", "task"))
            ax_result = runtime.run_ax(AxToolJob("ax-abort", (0, 0, 20, 20), ax_action))

            self.assertEqual(first.error_code, AX_ABORT_GLOBAL)
            self.assertEqual(second.error_code, AX_ABORT_GLOBAL)
            self.assertEqual(ax_result.error_code, AX_ABORT_GLOBAL)
            self.assertEqual((runner_calls, ax_calls), (0, 0))
            self.assertTrue(read_abort_state(controller.path).latched)

    async def test_global_abort_during_each_job_overrides_success(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller = AbortController(root)

            class AbortingBrowserRunner:
                async def run(self, _task: str) -> BrowserJobResult:
                    controller.abort("browser stop")
                    return BrowserJobResult(True, result="must be discarded")

            runtime = JarvisToolRuntime(
                root,
                _browser_runner_factory=lambda _token: AbortingBrowserRunner(),
            )
            browser = await runtime.run_browser(BrowserToolJob("browser-mid-abort", "task"))
            self.assertEqual(browser.status, ToolStatus.ABORTED)
            self.assertEqual(browser.error_code, AX_ABORT_GLOBAL)
            self.assertEqual(browser.result, "")

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller = AbortController(root)

            def aborting_ax_action() -> bool:
                controller.abort("ax stop")
                return True

            runtime = JarvisToolRuntime(root)
            ax = runtime.run_ax(
                AxToolJob("ax-mid-abort", (10, 20, 30, 40), aborting_ax_action)
            )
            self.assertEqual(ax.status, ToolStatus.ABORTED)
            self.assertEqual(ax.error_code, AX_ABORT_GLOBAL)

    async def test_task_rect_and_result_bounds_fail_closed(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runner = StaticBrowserRunner(
                BrowserJobResult(True, result="x" * (MAX_TOOL_RESULT_BYTES + 1))
            )
            runtime = JarvisToolRuntime(root, _browser_runner_factory=lambda _token: runner)

            oversized_task = await runtime.run_browser(
                BrowserToolJob("task-bound", "가" * (MAX_BROWSER_TASK_BYTES // 3 + 1))
            )
            self.assertEqual(oversized_task.error_code, "browser_task_too_large")
            self.assertEqual(runner.calls, 0)

            oversized_result = await runtime.run_browser(BrowserToolJob("result-bound", "task"))
            self.assertEqual(oversized_result.error_code, "browser_result_too_large")
            self.assertEqual(oversized_result.result, "")
            self.assertEqual(runner.calls, 1)

            rect_result = runtime.run_ax(
                AxToolJob("rect-bound", (0, 0, MAX_AX_EXTENT + 1, 10), lambda: True)
            )
            self.assertEqual(rect_result.error_code, "ax_rect_invalid")

    async def test_exceptions_and_event_sink_failures_return_fixed_codes(self):
        with TemporaryDirectory() as temp_dir:
            events = []

            def sink(event) -> None:
                events.append(event.as_dict())
                raise RuntimeError("status sink secret")

            runtime = JarvisToolRuntime(
                Path(temp_dir),
                event_sink=sink,
                _browser_runner_factory=lambda _token: ExplodingBrowserRunner(),
            )
            browser = await runtime.run_browser(BrowserToolJob("browser-exception", "secret task"))
            ax = runtime.run_ax(
                AxToolJob(
                    "ax-exception",
                    (10, 20, 30, 40),
                    lambda: (_ for _ in ()).throw(RuntimeError("credential=secret")),
                )
            )

            self.assertEqual(browser.error_code, "browser_job_failed")
            self.assertEqual(ax.error_code, "ax_action_failed")
            serialized = repr(events)
            self.assertNotIn("secret task", serialized)
            self.assertNotIn("token=do-not-log", serialized)
            self.assertNotIn("credential=secret", serialized)


class JarvisToolRuntimeAxTests(unittest.TestCase):
    def test_focus_or_real_pointer_requests_are_refused_without_action(self):
        with TemporaryDirectory() as temp_dir:
            calls = 0

            def action() -> bool:
                nonlocal calls
                calls += 1
                return True

            runtime = JarvisToolRuntime(Path(temp_dir))
            for job in (
                AxToolJob(
                    "ax-focus",
                    (10, 20, 100, 40),
                    action,
                    requires_frontmost_activation=True,
                ),
                AxToolJob(
                    "ax-pointer",
                    (10, 20, 100, 40),
                    action,
                    requires_real_pointer=True,
                ),
            ):
                result = runtime.run_ax(job)
                self.assertFalse(result.ok)
                self.assertEqual(result.status, ToolStatus.REFUSED)
                self.assertEqual(result.error_code, AX_ABORT_FOCUS_REQUIRED)
                self.assertEqual((result.cursor.x, result.cursor.y), (60, 40))
            self.assertEqual(calls, 0)


if __name__ == "__main__":
    unittest.main()
