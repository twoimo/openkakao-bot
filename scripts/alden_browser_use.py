"""Dedicated Browser-Use/Playwright context for Alden web jobs.

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
import json
import os
import socket
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from auto_reply_ondevice import FLASH_NEXT_MODEL_ID, QWEN38_27B_MODEL_ID
from alden_abort import AbortToken, AldenCancelled
from local_mlx_gateway import MlxRequestAdmissionClosed, mlx_model_request_lease


LOCAL_MLX_BASE_URL = "http://127.0.0.1:11234/v1"
LOCAL_MLX_MODELS_URL = "http://127.0.0.1:11234/v1/models"
LOCAL_MLX_MODELS_TIMEOUT_SECS = 2.0
LOCAL_MLX_MODELS_MAX_BYTES = 256 * 1024
LOCAL_MLX_MODELS_MAX_ROWS = 256
DEFAULT_BROWSER_MODEL_ID = QWEN38_27B_MODEL_ID
SELECTABLE_BROWSER_MODEL_IDS = frozenset(
    {QWEN38_27B_MODEL_ID, FLASH_NEXT_MODEL_ID}
)

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
CDP_URL_ATTR = "alden_cdp_url"

# Browser-Use stays text-only for both supported local MLX models. Judge/GIF
# extras remain disabled because the adapter owns one local request path and the
# product writes no unrequested artifacts.
LOCAL_MODEL_USE_VISION = False
TEXT_ONLY_AGENT_KWARGS: dict[str, Any] = {
    "use_vision": LOCAL_MODEL_USE_VISION,
    "generate_gif": False,
    "use_judge": False,
}

# Exact single-field facts are read from the live DOM through Browser-Use's
# bound CDP page. Keep this collector deliberately small: it exists to ground
# fields that are otherwise easy for a text-only model to guess from a URL or
# page summary, and every collection attempt has one short wall-clock budget.
DOM_EVIDENCE_TIMEOUT_SECS = 2.0
DOM_EVIDENCE_MAX_CHARS = 4096
DOCUMENT_TITLE_EVIDENCE_KIND = "document_title"
FIRST_SEARCH_RESULT_EVIDENCE_KIND = "first_search_result"
WIKIPEDIA_FIRST_RESULT_SELECTOR = ".mw-search-result-heading a"


class BrowserUseApiUnsupported(RuntimeError):
    """The installed browser_use cannot be bound to the dedicated browser."""


class LocalModelCatalogRejected(RuntimeError):
    """The fixed local MLX catalog did not prove the selected model is ready."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


def _local_only_urlopen(request: urllib.request.Request, *, timeout: float):
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _RejectRedirects(),
    )
    return opener.open(request, timeout=timeout)


def _catalog_model_id(model: str) -> str:
    if model not in SELECTABLE_BROWSER_MODEL_IDS:
        raise LocalModelCatalogRejected("browser_model_not_allowed")
    return model.removeprefix("mlx/")


def read_local_model_catalog(
    *,
    opener: Callable[..., Any] = _local_only_urlopen,
    timeout: float = LOCAL_MLX_MODELS_TIMEOUT_SECS,
) -> bytes:
    """Read only the fixed localhost MLX model catalog with hard bounds."""

    request = urllib.request.Request(
        LOCAL_MLX_MODELS_URL,
        method="GET",
        headers={"Accept": "application/json"},
    )
    try:
        with opener(request, timeout=float(timeout)) as response:
            raw = response.read(LOCAL_MLX_MODELS_MAX_BYTES + 1)
    except Exception as exc:
        raise LocalModelCatalogRejected("mlx_model_catalog_unavailable") from exc
    if not isinstance(raw, bytes) or len(raw) > LOCAL_MLX_MODELS_MAX_BYTES:
        raise LocalModelCatalogRejected("mlx_model_catalog_malformed")
    return raw


def require_selected_model_ready(model: str, raw_catalog: bytes) -> None:
    """Require one exact ready row for the selected fixed local model."""

    wanted = _catalog_model_id(model)
    try:
        payload = json.loads(raw_catalog)
    except (ValueError, TypeError, RecursionError) as exc:
        raise LocalModelCatalogRejected("mlx_model_catalog_malformed") from exc
    if not isinstance(payload, dict):
        raise LocalModelCatalogRejected("mlx_model_catalog_malformed")
    rows = payload.get("data")
    if not isinstance(rows, list) or len(rows) > LOCAL_MLX_MODELS_MAX_ROWS:
        raise LocalModelCatalogRejected("mlx_model_catalog_malformed")

    matches: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise LocalModelCatalogRejected("mlx_model_catalog_malformed")
        row_id = row.get("id")
        if not isinstance(row_id, str) or not row_id:
            raise LocalModelCatalogRejected("mlx_model_catalog_malformed")
        if row_id == wanted:
            matches.append(row)

    if not matches:
        raise LocalModelCatalogRejected("mlx_model_catalog_model_absent")
    if len(matches) != 1:
        raise LocalModelCatalogRejected("mlx_model_catalog_duplicate_model")

    target = matches[0]
    loaded = target.get("loaded")
    state = target.get("state")
    if type(loaded) is not bool or not isinstance(state, str):
        raise LocalModelCatalogRejected("mlx_model_catalog_malformed")
    if loaded is not True or state != "ready":
        raise LocalModelCatalogRejected("mlx_model_catalog_model_unready")


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
class DomEvidence:
    kind: str
    value: str
    url: str
    source: str


@dataclass(frozen=True)
class _BrowserAgentOutcome:
    result: str
    evidence: tuple[DomEvidence, ...] = ()


@dataclass(frozen=True)
class BrowserJobResult:
    ok: bool
    error_code: str = ""
    result: str = ""
    evidence: tuple[DomEvidence, ...] = ()


def _requested_dom_evidence_kinds(task: str) -> frozenset[str]:
    """Recognize the two bounded exact-field intents this adapter can ground."""

    normalized = " ".join(str(task).casefold().split())
    kinds: set[str] = set()
    if any(
        phrase in normalized
        for phrase in (
            "document title",
            "page title",
            "title of the page",
            "html title",
            "문서 제목",
            "페이지 제목",
        )
    ):
        kinds.add(DOCUMENT_TITLE_EVIDENCE_KIND)

    asks_for_first = "first" in normalized or "1st" in normalized or "첫" in normalized
    asks_for_search_result = (
        "search result" in normalized
        or ("search" in normalized and "result" in normalized)
        or "검색 결과" in normalized
    )
    if asks_for_first and asks_for_search_result:
        kinds.add(FIRST_SEARCH_RESULT_EVIDENCE_KIND)
    return frozenset(kinds)


def _bounded_dom_value(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.strip()
    if not normalized or len(normalized) > DOM_EVIDENCE_MAX_CHARS:
        return ""
    return normalized


def _is_wikipedia_search_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    hostname = (parsed.hostname or "").casefold()
    path = urllib.parse.unquote(parsed.path).casefold()
    query = urllib.parse.parse_qs(parsed.query)
    query_title = " ".join(query.get("title", ())).casefold()
    return (
        parsed.scheme in {"http", "https"}
        and (hostname == "wikipedia.org" or hostname.endswith(".wikipedia.org"))
        and ("special:search" in path or query_title == "special:search")
    )


async def _collect_dom_evidence(
    browser_session: Any,
    requested_kinds: frozenset[str],
) -> tuple[DomEvidence, ...]:
    """Read supported facts from the actual focused CDP page within one timeout."""

    if not requested_kinds:
        return ()

    async def collect() -> tuple[DomEvidence, ...]:
        page = await browser_session.get_current_page()
        if page is None:
            return ()

        url = _bounded_dom_value(await page.evaluate("() => window.location.href"))
        if not url.startswith(("http://", "https://")):
            return ()

        evidence: list[DomEvidence] = []
        if DOCUMENT_TITLE_EVIDENCE_KIND in requested_kinds:
            title = _bounded_dom_value(await page.evaluate("() => document.title"))
            if title:
                evidence.append(
                    DomEvidence(
                        kind=DOCUMENT_TITLE_EVIDENCE_KIND,
                        value=title,
                        url=url,
                        source="document.title",
                    )
                )

        if (
            FIRST_SEARCH_RESULT_EVIDENCE_KIND in requested_kinds
            and _is_wikipedia_search_url(url)
        ):
            first_result = _bounded_dom_value(
                await page.evaluate(
                    "() => { const node = document.querySelector("
                    f"{WIKIPEDIA_FIRST_RESULT_SELECTOR!r}); "
                    "return node ? (node.textContent || '').trim() : ''; }"
                )
            )
            if first_result:
                evidence.append(
                    DomEvidence(
                        kind=FIRST_SEARCH_RESULT_EVIDENCE_KIND,
                        value=first_result,
                        url=url,
                        source=WIKIPEDIA_FIRST_RESULT_SELECTOR,
                    )
                )
        return tuple(evidence)

    try:
        return await asyncio.wait_for(collect(), timeout=DOM_EVIDENCE_TIMEOUT_SECS)
    except Exception:
        return ()


def _ground_exact_requested_field(
    task: str,
    fallback: str,
    evidence: tuple[DomEvidence, ...],
) -> str:
    """Use live DOM evidence only when the task asks for one supported field."""

    requested = _requested_dom_evidence_kinds(task)
    if len(requested) != 1:
        return fallback
    wanted = next(iter(requested))
    for item in reversed(evidence):
        if item.kind == wanted and item.value:
            return item.value
    return fallback


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
            raise AldenCancelled("alden_global_abort") from exc
        raise
    finally:
        watcher.cancel()


class BrowserUseRunner:
    def __init__(
        self,
        token: AbortToken,
        *,
        context_factory: Callable[[], DedicatedPlaywrightContext] = DedicatedPlaywrightContext,
        agent_factory: Callable[
            [str, Any, str, str], Awaitable[str | _BrowserAgentOutcome]
        ]
        | None = None,
        catalog_reader: Callable[[], bytes] = read_local_model_catalog,
        model: str = DEFAULT_BROWSER_MODEL_ID,
        state_root: Path | None = None,
    ) -> None:
        self.token = token
        self.context_factory = context_factory
        self.agent_factory = agent_factory or self._run_browser_use_agent
        self.catalog_reader = catalog_reader
        self.model = model
        self.state_root = state_root
        self._owned_context: DedicatedPlaywrightContext | None = None

    async def _run_browser_use_agent(
        self,
        task: str,
        context: Any,
        model: str,
        base_url: str,
    ) -> _BrowserAgentOutcome:
        """Bind Browser-Use to the dedicated Chromium and the local MLX endpoint."""

        disable_browser_use_telemetry()
        from browser_use import Agent
        from browser_use.llm import ChatOpenAI

        llm = ChatOpenAI(model=model.removeprefix("mlx/"), base_url=base_url, api_key="local")
        # Both the text-only switches and the browser argument are chosen from
        # the installed signature, so a release that ignores them cannot
        # silently send screenshots or drop the dedicated browser.
        browser_kwargs = browser_agent_kwargs(Agent, context)
        evidence_by_kind: dict[str, DomEvidence] = {}
        requested_kinds = _requested_dom_evidence_kinds(task)
        browser_session = browser_kwargs.get(AGENT_BROWSER_KWARG)

        async def capture_dom_evidence(*_args: Any) -> None:
            if browser_session is None or not requested_kinds:
                return
            for item in await _collect_dom_evidence(browser_session, requested_kinds):
                evidence_by_kind[item.kind] = item

        callback_kwargs = declared_agent_kwargs(
            Agent,
            {"register_new_step_callback": capture_dom_evidence},
        )
        kwargs = {
            **declared_agent_kwargs(Agent, TEXT_ONLY_AGENT_KWARGS),
            **browser_kwargs,
            **callback_kwargs,
        }
        agent = Agent(task=task, llm=llm, **kwargs)
        history = await agent.run()
        await capture_dom_evidence()
        final = getattr(history, "final_result", None)
        return _BrowserAgentOutcome(
            result=str(final() if callable(final) else final or ""),
            evidence=tuple(evidence_by_kind.values()),
        )

    async def run(self, task: str) -> BrowserJobResult:
        if self.token.is_cancelled():
            return BrowserJobResult(False, "global_abort")
        owned: DedicatedPlaywrightContext | None = None
        try:
            with mlx_model_request_lease(self.state_root):
                raw_catalog = await _cancelable(
                    asyncio.to_thread(self.catalog_reader),
                    self.token,
                )
                require_selected_model_ready(self.model, raw_catalog)
                self.token.raise_if_cancelled()

                owned = self.context_factory()
                self._owned_context = owned
                context = await _cancelable(owned.start(), self.token)
                self.token.raise_if_cancelled()
                agent_outcome = await _cancelable(
                    self.agent_factory(task, context, self.model, LOCAL_MLX_BASE_URL),
                    self.token,
                )
            self.token.raise_if_cancelled()
            if isinstance(agent_outcome, _BrowserAgentOutcome):
                evidence = agent_outcome.evidence
                requested = _requested_dom_evidence_kinds(task)
                if len(requested) == 1 and not any(
                    item.kind in requested and item.value for item in evidence
                ):
                    return BrowserJobResult(False, "browser_job_failed")
                result = _ground_exact_requested_field(task, agent_outcome.result, evidence)
            else:
                evidence = ()
                result = agent_outcome
            return BrowserJobResult(True, result=result, evidence=evidence)
        except AldenCancelled:
            return BrowserJobResult(False, "global_abort")
        except MlxRequestAdmissionClosed as exc:
            return BrowserJobResult(False, exc.code)
        except LocalModelCatalogRejected as exc:
            return BrowserJobResult(False, exc.code)
        except Exception:
            return BrowserJobResult(False, "browser_job_failed")
        finally:
            try:
                if owned is not None:
                    await owned.close()
            finally:
                self._owned_context = None
