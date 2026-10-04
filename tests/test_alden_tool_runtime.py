from __future__ import annotations

import json
import io
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from auto_reply_ax_ui import (
    AX_ABORT_FOCUS_REQUIRED,
    AX_ABORT_GLOBAL,
    AX_ERROR_TARGET_AMBIGUOUS,
    AX_ERROR_EFFECT_UNKNOWN,
    BackgroundAxActionError,
    ExactAxResolvedElement,
    ExactAxTarget,
    FocusStealRequired,
    SystemEventsBackgroundAxAdapter,
)
from auto_reply_ondevice import QWEN38_27B_MODEL_ID
from alden_abort import AbortController, read_abort_state
from alden_browser_use import (
    BrowserJobResult,
    BrowserUseRunner,
    DedicatedPlaywrightContext,
    LOCAL_MLX_BASE_URL,
)
from alden_tool_runtime import (
    ERROR_AX_OPT_IN_REQUIRED,
    MAX_AX_EXTENT,
    MAX_BROWSER_TASK_BYTES,
    MAX_TOOL_RESULT_BYTES,
    AxToolJob,
    BrowserToolJob,
    AldenToolRuntime,
    ExactAxToolJob,
    ToolStatus,
    _default_state_root,
    exact_ax_cli,
)


def ready_browser_catalog() -> bytes:
    return json.dumps({
        "data": [{
            "id": QWEN38_27B_MODEL_ID.removeprefix("mlx/"),
            "loaded": True,
            "state": "ready",
        }],
    }).encode()


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


class FakeExactAxAdapter:
    def __init__(self, *, resolve_error=None, perform_error=None, on_perform=None) -> None:
        self.resolve_error = resolve_error
        self.perform_error = perform_error
        self.on_perform = on_perform
        self.resolve_calls = []
        self.perform_calls = []

    def resolve_exact(self, target, *, timeout_seconds):
        self.resolve_calls.append((target, timeout_seconds))
        if self.resolve_error is not None:
            raise self.resolve_error
        return ExactAxResolvedElement((10.0, 20.0, 100.0, 40.0))

    def perform_exact(self, target, *, timeout_seconds):
        self.perform_calls.append((target, timeout_seconds))
        if self.on_perform is not None:
            self.on_perform()
        if self.perform_error is not None:
            raise self.perform_error
        return True


def exact_target() -> ExactAxTarget:
    return ExactAxTarget(
        pid=4242,
        bundle_id="com.example.BackgroundTarget",
        window_title="Exact Window",
        element_role="AXButton",
        element_identifier="commit-button",
    )


class AldenToolRuntimeBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_browser_factory_is_fixed_to_owned_context(self):
        with TemporaryDirectory() as temp_dir:
            runner = StaticBrowserRunner(BrowserJobResult(True, result="ok"))
            with mock.patch("alden_tool_runtime.BrowserUseRunner", return_value=runner) as factory:
                runtime = AldenToolRuntime(Path(temp_dir))
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
                    catalog_reader=ready_browser_catalog,
                    state_root=Path(temp_dir),
                )

            runtime = AldenToolRuntime(
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
            self.assertEqual(seen["model"], QWEN38_27B_MODEL_ID)
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

            runtime = AldenToolRuntime(
                Path(temp_dir),
                _browser_runner_factory=lambda token: BrowserUseRunner(
                    token,
                    context_factory=lambda: owned,
                    agent_factory=agent,
                    catalog_reader=ready_browser_catalog,
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

            runtime = AldenToolRuntime(root, _browser_runner_factory=runner_factory)
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

            runtime = AldenToolRuntime(
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

            runtime = AldenToolRuntime(root)
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
            runtime = AldenToolRuntime(root, _browser_runner_factory=lambda _token: runner)

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

            runtime = AldenToolRuntime(
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


class AldenToolRuntimeAxTests(unittest.TestCase):
    def test_default_ax_state_root_uses_enrolled_legacy_root(self):
        with TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            support = home / "Library/Application Support/openkakao"
            legacy = support / "bujamentor"
            legacy.mkdir(parents=True)
            (legacy / "enrollment.json").write_text("{}")
            with mock.patch("alden_tool_runtime.Path.home", return_value=home):
                self.assertEqual(_default_state_root(), legacy)
                modern = support / "auto-reply"
                modern.mkdir()
                (modern / "enrollment.json").write_text("{}")
                self.assertEqual(_default_state_root(), modern)

    def test_focus_or_real_pointer_requests_are_refused_without_action(self):
        with TemporaryDirectory() as temp_dir:
            calls = 0

            def action() -> bool:
                nonlocal calls
                calls += 1
                return True

            runtime = AldenToolRuntime(Path(temp_dir))
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

    def test_exact_ax_requires_opt_in_and_rejects_ambiguous_target(self):
        with TemporaryDirectory() as temp_dir:
            adapter = FakeExactAxAdapter()
            runtime = AldenToolRuntime(
                Path(temp_dir), _ax_adapter_factory=lambda: adapter
            )
            refused = runtime.run_exact_ax(
                ExactAxToolJob("exact-no-opt-in", exact_target())
            )
            self.assertEqual(refused.status, ToolStatus.REFUSED)
            self.assertEqual(refused.error_code, ERROR_AX_OPT_IN_REQUIRED)
            self.assertEqual((adapter.resolve_calls, adapter.perform_calls), ([], []))

        with TemporaryDirectory() as temp_dir:
            adapter = FakeExactAxAdapter(
                resolve_error=BackgroundAxActionError(AX_ERROR_TARGET_AMBIGUOUS)
            )
            runtime = AldenToolRuntime(
                Path(temp_dir), _ax_adapter_factory=lambda: adapter
            )
            rejected = runtime.run_exact_ax(
                ExactAxToolJob("exact-ambiguous", exact_target(), opt_in=True)
            )
            self.assertEqual(rejected.status, ToolStatus.REJECTED)
            self.assertEqual(rejected.error_code, AX_ERROR_TARGET_AMBIGUOUS)
            self.assertEqual(len(adapter.resolve_calls), 1)
            self.assertEqual(adapter.perform_calls, [])

    def test_exact_ax_global_abort_after_action_reports_uncertain_effect(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller = AbortController(root)
            adapter = FakeExactAxAdapter(on_perform=lambda: controller.abort("stop"))
            runtime = AldenToolRuntime(root, _ax_adapter_factory=lambda: adapter)
            result = runtime.run_exact_ax(
                ExactAxToolJob("exact-abort", exact_target(), opt_in=True)
            )
            self.assertEqual(result.status, ToolStatus.FAILED)
            self.assertEqual(result.error_code, AX_ERROR_EFFECT_UNKNOWN)
            self.assertEqual(len(adapter.resolve_calls), 1)
            self.assertEqual(len(adapter.perform_calls), 1)

    def test_exact_ax_latched_abort_prevents_target_resolution(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            AbortController(root).abort("stop before resolve")
            adapter = FakeExactAxAdapter()
            runtime = AldenToolRuntime(root, _ax_adapter_factory=lambda: adapter)
            result = runtime.run_exact_ax(
                ExactAxToolJob("exact-pre-abort", exact_target(), opt_in=True)
            )
            self.assertEqual(result.status, ToolStatus.ABORTED)
            self.assertEqual(result.error_code, AX_ABORT_GLOBAL)
            self.assertEqual((adapter.resolve_calls, adapter.perform_calls), ([], []))

    def test_system_events_adapter_refuses_focus_or_pointer_style_roles_before_script(self):
        calls = []

        def runner(command, script, timeout):
            calls.append((command, script, timeout))
            return 0, b"", b""

        adapter = SystemEventsBackgroundAxAdapter(script_runner=runner)
        unsafe_target = ExactAxTarget(
            pid=4242,
            bundle_id="com.example.BackgroundTarget",
            window_title="Exact Window",
            element_role="AXTextField",
            element_identifier="composer",
        )
        with self.assertRaises(FocusStealRequired):
            adapter.resolve_exact(unsafe_target, timeout_seconds=1.0)
        self.assertEqual(calls, [])

    def test_system_events_adapter_uses_axpress_without_pointer_or_activation(self):
        calls = []

        def runner(command, script, timeout):
            calls.append((command, script, timeout))
            if 'perform action "AXPress"' in script:
                return 0, b"ok\n", b""
            return 0, b"10\x1f20\x1f100\x1f40\n", b""

        adapter = SystemEventsBackgroundAxAdapter(script_runner=runner)
        resolved = adapter.resolve_exact(exact_target(), timeout_seconds=1.5)
        self.assertEqual(resolved.rect, (10.0, 20.0, 100.0, 40.0))
        self.assertTrue(adapter.perform_exact(exact_target(), timeout_seconds=1.0))
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call[0] == ["/usr/bin/osascript", "-"] for call in calls))
        combined = "\n".join(call[1] for call in calls)
        self.assertIn('perform action "AXPress" of targetElement', combined)
        self.assertIn("if frontmost of targetProcess then error", combined)
        for forbidden in ("click ", "keystroke", "CGEvent", "activate"):
            self.assertNotIn(forbidden, combined)

    def test_system_events_adapter_maps_exact_target_ambiguity(self):
        def runner(_command, _script, _timeout):
            return 1, b"", b"execution error: ALDEN_AX_TARGET_AMBIGUOUS (-2700)"

        adapter = SystemEventsBackgroundAxAdapter(script_runner=runner)
        with self.assertRaises(BackgroundAxActionError) as raised:
            adapter.resolve_exact(exact_target(), timeout_seconds=1.0)
        self.assertEqual(raised.exception.error_code, AX_ERROR_TARGET_AMBIGUOUS)

    def test_system_events_adapter_marks_post_press_timeout_uncertain(self):
        def runner(_command, _script, timeout):
            raise subprocess.TimeoutExpired("osascript", timeout)

        adapter = SystemEventsBackgroundAxAdapter(script_runner=runner)
        with self.assertRaises(BackgroundAxActionError) as raised:
            adapter.perform_exact(exact_target(), timeout_seconds=1.0)
        self.assertEqual(raised.exception.error_code, AX_ERROR_EFFECT_UNKNOWN)

    def test_cli_is_a_gated_production_caller_with_fake_adapter(self):
        with TemporaryDirectory() as temp_dir:
            adapter = FakeExactAxAdapter()
            args = [
                "--pid", "4242",
                "--bundle-id", "com.example.BackgroundTarget",
                "--window-title", "Exact Window",
                "--role", "AXButton",
                "--identifier", "commit-button",
                "--state-root", temp_dir,
                "--allow-background-ax",
            ]
            output = io.StringIO()
            with redirect_stdout(output):
                code = exact_ax_cli(args, _ax_adapter=adapter)
            payload = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["virtualCursor"], {"x": 60.0, "y": 40.0})
            self.assertEqual((len(adapter.resolve_calls), len(adapter.perform_calls)), (1, 1))


if __name__ == "__main__":
    unittest.main()
