"""Dedicated Browser-Use/Playwright context for Jarvis web jobs.

The implementation launches its own ephemeral Chromium, creates a fresh context
and exposes that browser's loopback DevTools endpoint so Browser-Use can drive
the very browser this module owns and closes. It never attaches to the user's
Chrome or Safari, never opens a persistent profile, and never connects to an
outside CDP endpoint, so existing user tabs and sessions stay outside its reach.

Browser-Use releases disagree about how a browser is handed to `Agent`. The
argument is chosen from the installed signature and an unrecognised signature
fails closed, because a release that quietly swallows the argument would leave
the dedicated browser unused (2026-09-22).
"""

from __future__ import annotations

import asyncio
import inspect
import os
import socket
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from auto_reply_ondevice import FLASH_NEXT_MODEL_ID
from jarvis_abort import AbortToken, JarvisCancelled


LOCAL_MLX_BASE_URL = "http://127.0.0.1:11234/v1"

# Browser-Use reads this at import time and uploads anonymized telemetry by
# default. The product keeps every LLM, STT, TTS and embedding call on the
# machine, so the package's own upload is switched off before it is imported
# (2026-09-22).
TELEMETRY_ENV_VAR = "ANONYMIZED_TELEMETRY"

# Older releases take our Playwright context; newer ones take their own browser
# session bound to a DevTools endpoint. Only a parameter the release actually
# declares is used, so a `**kwargs` catch-all can never absorb the argument.
AGENT_CONTEXT_KWARG = "browser_context"
AGENT_BROWSER_KWARG = "browser"
# Stamped on the Playwright context so the agent factory keeps its
# (task, context, model, base_url) contract while the CDP endpoint travels
# alongside the context it belongs to.
CDP_URL_ATTR = "jarvis_cdp_url"

# The resident Flash-Next gateway is served without a vision tower, so a request
# carrying a screenshot is rejected with HTTP 400 ("This model is serving
# without its vision tower"). Browser-Use must therefore run text-only against
# it, and its judge/GIF extras are switched off because this host has no second
# model and the product writes no unrequested artifacts (2026-09-22).
LOCAL_MODEL_USE_VISION = False
TEXT_ONLY_AGENT_KWARGS: dict[str, Any] = {
    "use_vision": LOCAL_MODEL_USE_VISION,
    "generate_gif": False,
    "use_judge": False,
}


class BrowserUseApiUnsupported(RuntimeError):
    """The installed browser_use cannot be bound to the dedicated browser."""


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def disable_browser_use_telemetry(environ: dict[str, str] | None = None) -> None:
    """Force Browser-Use telemetry off for this process.

    The environment variable covers a package that has not been imported yet;
    the config attribute covers one that was imported by an earlier job.
    """
    target = os.environ if environ is None else environ
    target[TELEMETRY_ENV_VAR] = "False"
    try:
        from browser_use.config import CONFIG
    except Exception:
        return
    try:
        CONFIG.ANONYMIZED_TELEMETRY = False
    except Exception:
        pass


def _declares_keyword(callable_object: Any, name: str) -> bool:
    """Report whether the callable declares `name` as a real parameter.

    A `**kwargs` catch-all deliberately does not count: browser_use 0.13
    accepts `browser_context=` into `**kwargs` and then ignores it, which is
    exactly the silent drop this module must not rely on.
    """
    try:
        parameters = inspect.signature(callable_object.__init__).parameters
    except (TypeError, ValueError):
        return False
    return name in parameters


def _browser_session_for_cdp(cdp_url: str) -> Any:
    from browser_use import BrowserProfile, BrowserSession

    profile = BrowserProfile(
        headless=True,
        enable_default_extensions=False,
        accept_downloads=False,
    )
    return BrowserSession(cdp_url=cdp_url, browser_profile=profile)


def declared_agent_kwargs(agent_class: Any, candidates: dict[str, Any]) -> dict[str, Any]:
    """Keep only the keyword arguments the installed release actually declares.

    The same reasoning as `browser_agent_kwargs` applies: a `**kwargs`
    catch-all would accept `use_vision=False` and then ignore it, which would
    leave every Browser-Use step failing against a text-only gateway.
    """
    return {
        name: value
        for name, value in candidates.items()
        if _declares_keyword(agent_class, name)
    }


def browser_agent_kwargs(agent_class: Any, context: Any) -> dict[str, Any]:
    """Choose the browser argument the installed Browser-Use honours.

    Raises `BrowserUseApiUnsupported` when the release declares neither
    argument, or when the modern `browser` argument is declared but the
    dedicated context carries no DevTools endpoint. Both cases fail closed
    instead of letting the agent silently launch a browser of its own.
    """
    if _declares_keyword(agent_class, AGENT_CONTEXT_KWARG):
        return {AGENT_CONTEXT_KWARG: context}
    if _declares_keyword(agent_class, AGENT_BROWSER_KWARG):
        cdp_url = str(getattr(context, CDP_URL_ATTR, "") or "")
        if not cdp_url:
            raise BrowserUseApiUnsupported("dedicated_context_has_no_cdp_endpoint")
        return {AGENT_BROWSER_KWARG: _browser_session_for_cdp(cdp_url)}
    raise BrowserUseApiUnsupported("browser_use_agent_has_no_browser_argument")


@dataclass(frozen=True)
class BrowserJobResult:
    ok: bool
    error_code: str = ""
    result: str = ""


class DedicatedPlaywrightContext:
    def __init__(self) -> None:
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self.context: Any | None = None
        self.cdp_url: str = ""

    async def start(self) -> Any:
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        port = _free_loopback_port()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=[
                f"--remote-debugging-port={port}",
                "--remote-debugging-address=127.0.0.1",
            ],
        )
        self.cdp_url = f"http://127.0.0.1:{port}"
        self.context = await self._browser.new_context()
        setattr(self.context, CDP_URL_ATTR, self.cdp_url)
        return self.context

    async def close(self) -> None:
        if self.context is not None:
            await self.context.close()
            self.context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
        self.cdp_url = ""


async def _cancelable(awaitable: Awaitable[Any], token: AbortToken) -> Any:
    operation = asyncio.ensure_future(awaitable)

    async def watch_abort() -> None:
        while not operation.done():
            if token.is_cancelled():
                operation.cancel()
                return
            await asyncio.sleep(0.02)

    watcher = asyncio.create_task(watch_abort())
    try:
        return await operation
    except asyncio.CancelledError as exc:
        if token.is_cancelled():
            raise JarvisCancelled("jarvis_global_abort") from exc
        raise
    finally:
        watcher.cancel()


class BrowserUseRunner:
    def __init__(
        self,
        token: AbortToken,
        *,
        context_factory: Callable[[], DedicatedPlaywrightContext] = DedicatedPlaywrightContext,
        agent_factory: Callable[[str, Any, str, str], Awaitable[str]] | None = None,
    ) -> None:
        self.token = token
        self.context_factory = context_factory
        self.agent_factory = agent_factory or self._run_browser_use_agent
        self._owned_context: DedicatedPlaywrightContext | None = None

    async def _run_browser_use_agent(
        self,
        task: str,
        context: Any,
        model: str,
        base_url: str,
    ) -> str:
        """Bind Browser-Use to the dedicated Chromium and the local MLX endpoint."""

        disable_browser_use_telemetry()
        from browser_use import Agent
        from browser_use.llm import ChatOpenAI

        llm = ChatOpenAI(model=model.removeprefix("mlx/"), base_url=base_url, api_key="local")
        # Both the text-only switches and the browser argument are chosen from
        # the installed signature, so a release that ignores them cannot
        # silently send screenshots or drop the dedicated browser.
        kwargs = {
            **declared_agent_kwargs(Agent, TEXT_ONLY_AGENT_KWARGS),
            **browser_agent_kwargs(Agent, context),
        }
        agent = Agent(task=task, llm=llm, **kwargs)
        history = await agent.run()
        final = getattr(history, "final_result", None)
        return str(final() if callable(final) else final or "")

    async def run(self, task: str) -> BrowserJobResult:
        if self.token.is_cancelled():
            return BrowserJobResult(False, "global_abort")
        owned = self.context_factory()
        self._owned_context = owned
        try:
            context = await _cancelable(owned.start(), self.token)
            self.token.raise_if_cancelled()
            result = await _cancelable(
                self.agent_factory(task, context, FLASH_NEXT_MODEL_ID, LOCAL_MLX_BASE_URL),
                self.token,
            )
            self.token.raise_if_cancelled()
            return BrowserJobResult(True, result=result)
        except JarvisCancelled:
            return BrowserJobResult(False, "global_abort")
        except Exception:
            return BrowserJobResult(False, "browser_job_failed")
        finally:
            try:
                await owned.close()
            finally:
                self._owned_context = None

