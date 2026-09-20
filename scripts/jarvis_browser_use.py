"""Dedicated Browser-Use/Playwright context for Jarvis web jobs.

The implementation launches its own ephemeral Chromium and creates a fresh
context. It never connects over CDP and never opens a persistent Chrome/Safari
profile, so existing user tabs and sessions are outside its reach.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from auto_reply_ondevice import FLASH_NEXT_MODEL_ID
from jarvis_abort import AbortToken, JarvisCancelled


LOCAL_MLX_BASE_URL = "http://127.0.0.1:11234/v1"


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

    async def start(self) -> Any:
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        self.context = await self._browser.new_context()
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
        """Bind Browser-Use to the local OpenAI-compatible MLX endpoint."""

        from browser_use import Agent
        from browser_use.llm import ChatOpenAI

        llm = ChatOpenAI(model=model.removeprefix("mlx/"), base_url=base_url, api_key="local")
        # Browser-Use releases differ in their browser argument name. Keeping
        # construction in one method makes the adapter easy to update without
        # ever falling back to a user browser profile.
        agent = Agent(task=task, llm=llm, browser_context=context)
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
