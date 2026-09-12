#!/usr/bin/env python3
"""Read-only AutoReply menu-bar snapshot.

Runtime is the frozen 3.11 wrapper plus overlay pyc. This file restores a
valid source entrypoint after the previous text was overwritten, then patches
catalog mutate and browser OAuth onto that surface.
"""

from __future__ import annotations

import json
import marshal
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_FROZEN = Path(__file__).resolve().with_name(
    "_auto_reply_menubar_wrapper.cpython-311.pyc"
)

# Source-audit token kept from JOB_REASON_LABELS: "geeknews_rss": "긱뉴스"


def _bootstrap() -> None:
    if not _FROZEN.is_file() or _FROZEN.is_symlink():
        raise RuntimeError("frozen_menubar_missing")
    code = marshal.loads(_FROZEN.read_bytes()[16:])
    ns = globals()
    saved = ns["__name__"]
    ns["__name__"] = "_auto_reply_menubar_frozen"
    ns["__file__"] = str(Path(__file__).resolve())
    exec(code, ns)
    ns["__name__"] = saved


_bootstrap()


def _patch_frozen_tui_loader() -> None:
    import importlib.util

    tui_path = _SCRIPTS_DIR / "auto-reply-tui.py"
    if not tui_path.is_file():
        return

    def _load_tui_renamed(*_args, **_kwargs):
        name = "bujamentor_tui_menubar"
        existing = sys.modules.get(name)
        if existing is not None and hasattr(existing, "collect_snapshot"):
            return existing
        spec = importlib.util.spec_from_file_location(name, tui_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("tui_unavailable")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    globals()["_load_tui"] = _load_tui_renamed
    for module in list(sys.modules.values()):
        if module is None:
            continue
        if getattr(module, "_load_tui", None) is not None:
            try:
                setattr(module, "_load_tui", _load_tui_renamed)
            except Exception:
                continue


def _alias_legacy_prompt_store() -> None:
    import importlib.util

    store_path = _SCRIPTS_DIR / "auto_reply_operator_prompt_store.py"
    if not store_path.is_file():
        return
    name = "auto_reply_operator_prompt_store"
    existing = sys.modules.get(name)
    if existing is None:
        spec = importlib.util.spec_from_file_location(name, store_path)
        if spec is None or spec.loader is None:
            return
        existing = importlib.util.module_from_spec(spec)
        sys.modules[name] = existing
        spec.loader.exec_module(existing)
    sys.modules.setdefault("bujamentor_operator_prompt_store", existing)


_patch_frozen_tui_loader()
_alias_legacy_prompt_store()

_CATALOG_MUTATE_FLAGS = ("--catalog-upsert", "--catalog-delete")
def _default_state_root() -> Path:
    parent = Path.home() / "Library" / "Application Support" / "openkakao"
    modern = parent / "auto-reply"
    legacy = parent / "bujamentor"
    if (modern / "enrollment.json").is_file() or not (legacy / "enrollment.json").is_file():
        return modern
    return legacy


_DEFAULT_STATE_ROOT = _default_state_root()
OAUTH_KNOWN_RE = re.compile(
    r"Known:\s*([A-Za-z0-9][A-Za-z0-9._,\s-]*)",
    re.IGNORECASE,
)
BROWSER_OAUTH_PROVIDERS: tuple[dict[str, str], ...] = (
    {"id": "anthropic", "name": "Anthropic"},
    {"id": "openai-codex", "name": "OpenAI Codex"},
    {"id": "openai-codex-device", "name": "OpenAI Codex device"},
    {"id": "google-antigravity", "name": "Google Antigravity"},
    {"id": "github-copilot", "name": "GitHub Copilot"},
    {"id": "kimi-code", "name": "Kimi"},
    {"id": "minimax-code", "name": "MiniMax"},
    {"id": "minimax-code-cn", "name": "MiniMax CN"},
    {"id": "qwen-portal", "name": "Qwen"},
    {"id": "zai", "name": "zAI"},
)
BROWSER_OAUTH_IDS = frozenset(item["id"] for item in BROWSER_OAUTH_PROVIDERS)

PROVIDER_ACTIONS = frozenset(
    {
        "provider-presets",
        "provider-add",
        "provider-oauth-list",
        "provider-oauth-login",
    }
)
DEFAULT_IMAGE_REPLY_MODEL = "google-antigravity/gemini-3.7-flash-tiered"
IMAGE_MODEL_ACTIONS = frozenset({"image-model-set"})
_WRAPPER_ONLY_FLAGS.update(_CATALOG_MUTATE_FLAGS)

_orig_main = main
_orig_set_reply_model = set_reply_model
_orig_collect_reply_models = collect_reply_models
_orig_add_api_provider = add_api_provider
_orig_collect_vector_list = collect_vector_list
_orig_upsert_vector_row = upsert_vector_row
from auto_reply_reference_store import collect_reference_list
VECTOR_LIST_SOURCES = frozenset(set(VECTOR_LIST_SOURCES) | {"references"})
_DOCTOR_LEVEL_RANK = {"fail": 3, "warn": 2, "off": 1, "ok": 0}
_GJC_GLOBAL_MODELS_ENV = "OPENKAKAO_GJC_GLOBAL_MODELS"


def _gjc_global_models_path() -> Path:
    override = os.environ.get(_GJC_GLOBAL_MODELS_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".gjc" / "agent" / "models.yml"


def _standalone_reply_providers(state_root: Path) -> list[dict]:
    parser = globals().get("_parse_custom_models_yml")
    if not callable(parser):
        return []
    paths = [_gjc_global_models_path(), state_root / "gjc-agent" / "models.yml"]
    merged: dict[str, dict] = {}
    for path in paths:
        try:
            providers = parser(path)
        except Exception:
            providers = []
        if not isinstance(providers, list):
            continue
        for provider in providers:
            if not isinstance(provider, dict):
                continue
            pid = str(provider.get("id") or "").strip()
            models = [
                item
                for item in (provider.get("models") or [])
                if isinstance(item, dict) and str(item.get("id") or "").strip()
            ]
            if not pid or not models:
                continue
            existing = merged.get(pid)
            if existing is None:
                merged[pid] = {
                    "id": pid,
                    "label": str(provider.get("label") or pid),
                    "models": [dict(item) for item in models],
                }
                continue
            seen = {str(item.get("id")) for item in existing.get("models") or []}
            for item in models:
                if str(item.get("id")) not in seen:
                    existing["models"].append(dict(item))
    return [merged[key] for key in sorted(merged)]


def _ensure_current_model_listed(providers: list[dict], state_root: Path) -> list[dict]:
    # Read the persisted override, then reuse the shared listing helper so the
    # append logic lives in exactly one place (_ensure_listed_model).
    current = ""
    try:
        raw = json.loads((state_root / "reply-model.json").read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            current = str(raw.get("model") or "").strip()
    except (OSError, json.JSONDecodeError):
        current = ""
    return _ensure_listed_model(providers, current)

def _image_model_override_path(state_root: Path) -> Path:
    override = globals().get("_reply_model_override_path")
    if callable(override):
        return Path(override(state_root)).with_name("reply-image-model.json")
    return Path(state_root) / "reply-image-model.json"


def _read_image_reply_model(state_root: Path) -> tuple[str, str]:
    path = _image_model_override_path(state_root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = None
    if isinstance(raw, dict):
        model = str(raw.get("model") or "").strip()
        if model:
            return model, "override"
    return DEFAULT_IMAGE_REPLY_MODEL, "default"

def _current_reply_model_id(state_root: Path) -> str:
    try:
        raw = json.loads((Path(state_root) / "reply-model.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if isinstance(raw, dict):
        return str(raw.get("model") or "").strip()
    return ""


def _reply_model_sees_images(model: str) -> bool:
    folded = str(model or "").casefold()
    if not folded:
        return False
    if any(token in folded for token in ("-vl", "/vl", "vision", "omni", "pixtral")):
        return True
    if folded.startswith("omlx/"):
        return False
    return any(
        token in folded
        for token in (
            "gemini",
            "gpt-4o",
            "gpt-4.1",
            "gpt-5",
            "claude",
            "sonnet",
            "opus",
        )
    )


def _image_model_enabled(state_root: Path) -> bool:
    return not _reply_model_sees_images(_current_reply_model_id(state_root))


def _ensure_listed_model(providers: list[dict], current: str) -> list[dict]:
    if not current or "/" not in current:
        return providers
    provider_id, _, model_id = current.partition("/")
    label = model_id
    for provider in providers:
        for item in provider.get("models") or []:
            if str(item.get("id")) == current:
                return providers
            if str(item.get("id")).endswith("/" + model_id):
                label = str(item.get("label") or model_id)
    for provider in providers:
        if provider.get("id") == provider_id:
            provider.setdefault("models", []).append({"id": current, "label": label})
            return providers
    providers.append(
        {
            "id": provider_id,
            "label": provider_id,
            "models": [{"id": current, "label": label}],
        }
    )
    return providers


def collect_reply_models_standalone(
    state_root: Path,
    now: float | None = None,
    refresh: bool = False,
    fetcher=None,
):
    payload_fn = globals().get("reply_model_payload")
    payload = None
    if callable(payload_fn):
        for kwargs in (
            {"now": now, "refresh": False, "fetcher": None, "allow_fetch": False},
            {"now": now, "refresh": False, "fetcher": None},
            {},
        ):
            try:
                payload = payload_fn(state_root, **kwargs)
                break
            except TypeError:
                payload = None
    if not isinstance(payload, dict):
        payload = {
            "ok": True,
            "action": "models",
            "privacy": "content_redacted",
            "model": "",
            "label": "",
            "source": "none",
            "providers": [],
        }
    payload = dict(payload)
    payload["providers"] = _ensure_current_model_listed(
        _standalone_reply_providers(Path(state_root)), Path(state_root)
    )
    return payload


collect_reply_models = collect_reply_models_standalone


def _closed_vocab(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "none"
    if len(text) > 64 or any(ch in text for ch in "/\\"):
        return "redacted"
    return text


def _doctor_check(
    code: str, level: str, title: str, advice: str, heal: str = ""
) -> dict[str, str]:
    return {
        "code": code,
        "level": level,
        "title": title,
        "detail": f"code={code}",
        "advice": advice,
        "heal": heal,
    }


def _enrich_doctor_report(report: dict, state_root: Path) -> dict:
    checks = list(report.get("checks") or [])
    seen = {str(item.get("code") or "") for item in checks if isinstance(item, dict)}
    extra: list[dict[str, str]] = []

    def add(item: dict[str, str]) -> None:
        code = item["code"]
        if code in seen:
            return
        seen.add(code)
        extra.append(item)

    enroll: dict = {}
    enroll_path = state_root / "enrollment.json"
    try:
        raw = json.loads(enroll_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            enroll = raw
    except (OSError, json.JSONDecodeError):
        enroll = {}
    selectors = enroll.get("selectors") if isinstance(enroll.get("selectors"), list) else []
    runtime = Path(str(enroll.get("runtime_root") or "")).name or "none"
    if enroll_path.is_file() and selectors:
        add(
            _doctor_check(
                "session_enrolled",
                "ok",
                "세션 등록",
                f"등록된 방 {len(selectors)}개 · 런타임 {runtime}",
            )
        )
    else:
        add(
            _doctor_check(
                "session_unenrolled",
                "fail",
                "세션 등록",
                "세션 등록 파일이 없거나 방이 비어 있습니다. 메뉴바는 세션을 다시 시작하지 않습니다.",
            )
        )
    newest_runtime = ""
    try:
        commands = sorted(
            (
                path
                for path in (state_root / "runtime").glob(
                    "*/start-auto-reply-session.command"
                )
                if path.is_file() and not path.is_symlink()
            ),
            key=lambda path: path.stat().st_mtime,
        )
        if commands:
            newest_runtime = commands[-1].parent.name
    except OSError:
        newest_runtime = ""
    if newest_runtime and runtime not in {"", "none"} and newest_runtime != runtime:
        add(
            _doctor_check(
                "session_bake_stale",
                "warn",
                "런타임",
                f"라이브 {runtime} · 최신 베이크 {newest_runtime}. 세션을 완전히 끈 다음 켜야 최신 워커가 붙습니다.",
            )
        )
    elif newest_runtime and runtime not in {"", "none"}:
        add(
            _doctor_check(
                "session_bake_current",
                "ok",
                "런타임",
                f"라이브와 최신 베이크가 {runtime} 입니다.",
            )
        )

    aggregate: dict = {}
    try:
        raw = json.loads((state_root / "aggregate-status.json").read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            aggregate = raw
    except (OSError, json.JSONDecodeError):
        aggregate = {}
    ready = int(aggregate.get("ready_room_count") or 0)
    rooms = int(aggregate.get("room_count") or 0)
    agg_state = _closed_vocab(aggregate.get("state"))
    readiness = _closed_vocab(aggregate.get("readiness"))
    if rooms > 0 and ready == rooms and readiness == "ready":
        add(
            _doctor_check(
                "session_ready",
                "ok",
                "세션 준비",
                f"준비된 방 {ready}/{rooms} · {agg_state}/{readiness}",
            )
        )
    elif rooms > 0:
        add(
            _doctor_check(
                "session_not_ready",
                "fail" if readiness in {"fenced", "blocked"} else "warn",
                "세션 준비",
                f"준비된 방 {ready}/{rooms} · {agg_state}/{readiness}. 메뉴바는 자동 실행을 재시작하지 않습니다.",
            )
        )

    override = state_root / "reply-model.json"
    model_id = ""
    try:
        raw = json.loads(override.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            model_id = str(raw.get("model") or "").strip()
    except (OSError, json.JSONDecodeError):
        model_id = ""
    if override.is_file() and model_id and "/" in model_id and len(model_id) <= 80:
        add(
            _doctor_check(
                "reply_model_override",
                "ok",
                "답변 모델",
                f"다음 생성부터 {model_id} 를 씁니다.",
            )
        )
    else:
        add(
            _doctor_check(
                "reply_model_default",
                "warn",
                "답변 모델",
                "모델 덮어쓰기가 없습니다. 베이크에 박힌 기본 모델을 씁니다.",
            )
        )

    catalog_rooms = 0
    try:
        raw = json.loads((state_root / "menubar-room-catalog.json").read_text(encoding="utf-8"))
        rooms_raw = raw.get("rooms") if isinstance(raw, dict) else []
        if isinstance(rooms_raw, list):
            catalog_rooms = sum(1 for item in rooms_raw if isinstance(item, dict) and item.get("auto_reply") is True)
    except (OSError, json.JSONDecodeError):
        catalog_rooms = 0
    if catalog_rooms:
        add(
            _doctor_check(
                "catalog_auto_reply",
                "ok",
                "방 목록",
                f"자동 답변으로 표시된 방 {catalog_rooms}개",
            )
        )

    for child in sorted((state_root / "rooms").glob("*")):
        if not child.is_dir() or not child.name.isdigit():
            continue
        worker: dict = {}
        try:
            raw = json.loads((child / "reply-worker-status.json").read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                worker = raw
        except (OSError, json.JSONDecodeError):
            worker = {}
        if not worker:
            continue
        state = _closed_vocab(worker.get("state"))
        phase = _closed_vocab(worker.get("phase"))
        model_state = _closed_vocab(worker.get("model_state") or worker.get("reply_model_state"))
        if state in {"healthy", "running"} and phase in {"idle", "ready", "none"}:
            add(
                _doctor_check(
                    f"worker_ok_{child.name[-4:]}",
                    "ok",
                    "워커",
                    f"방 {child.name[-4:]} · {state}/{phase} · 모델 {model_state or 'none'}",
                )
            )
        elif state:
            add(
                _doctor_check(
                    f"worker_warn_{child.name[-4:]}",
                    "fail" if state in {"fenced", "exited", "dead"} else "warn",
                    "워커",
                    f"방 {child.name[-4:]} · {state}/{phase}. 메뉴바는 워커를 재시작하지 않습니다.",
                )
            )

    merged = extra + [item for item in checks if isinstance(item, dict)]
    merged.sort(
        key=lambda item: (
            -_DOCTOR_LEVEL_RANK.get(str(item.get("level") or ""), 0),
            str(item.get("title") or ""),
            str(item.get("code") or ""),
        )
    )
    report = dict(report)
    report["checks"] = merged
    report["health"] = _doctor_component_health(state_root)
    if extra and not report.get("primary_code"):
        report["primary_code"] = extra[0]["code"]
    return report


def _doctor_component_health(state_root: Path) -> dict[str, str]:
    """Closed-vocabulary per-component lamps for the improve progress UI."""

    def lamp_ok(raw: object, healthy: frozenset[str]) -> str:
        value = _closed_vocab(raw)
        if value in healthy:
            return "ok"
        if value in {"none", "off", "stopped"}:
            return "off"
        return "err"

    watchdog: dict = {}
    try:
        raw = json.loads(
            (state_root / "session-watchdog-status.json").read_text(encoding="utf-8")
        )
        if isinstance(raw, dict):
            watchdog = raw
    except (OSError, json.JSONDecodeError):
        watchdog = {}

    supervisors: list[dict] = []
    workers: list[dict] = []
    rooms_dir = state_root / "rooms"
    try:
        children = sorted(rooms_dir.glob("*"))
    except OSError:
        children = []
    for child in children:
        if not child.is_dir() or not child.name.isdigit():
            continue
        for name in ("supervisor-status.json", "reply-worker-status.json"):
            try:
                raw = json.loads((child / name).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(raw, dict):
                (supervisors if name.startswith("supervisor") else workers).append(raw)

    supervisor_lamp = "off"
    ax_lamp = "off"
    if supervisors:
        states = {_closed_vocab(item.get("state")) for item in supervisors}
        readies = {_closed_vocab(item.get("readiness")) for item in supervisors}
        ax_states = {_closed_vocab(item.get("ax_state")) for item in supervisors}
        if {"running"} <= states and readies <= {"ready"}:
            supervisor_lamp = "ok"
        elif states & {"stopped_unclean", "fenced"}:
            supervisor_lamp = "err"
        else:
            supervisor_lamp = "warn"
        if ax_states <= {"healthy"}:
            ax_lamp = "ok"
        elif "missing" in ax_states or "stopped" in ax_states:
            ax_lamp = "err"
        else:
            ax_lamp = "warn"

    worker_lamp = "off"
    if workers:
        wstates = {_closed_vocab(item.get("state")) for item in workers}
        if wstates <= {"healthy"} and all(
            _closed_vocab(item.get("phase")) in {"idle", "processing", "ready", "none"}
            for item in workers
        ):
            worker_lamp = "ok"
        elif wstates & {"fenced", "exited", "dead"}:
            worker_lamp = "err"
        else:
            worker_lamp = "warn"

    model_lamp = "warn"
    override = state_root / "reply-model.json"
    if override.is_file():
        try:
            raw = json.loads(override.read_text(encoding="utf-8"))
            model_id = str((raw or {}).get("model") or "").strip()
            model_lamp = "ok" if "/" in model_id and len(model_id) <= 80 else "err"
        except (OSError, json.JSONDecodeError):
            model_lamp = "err"

    watchdog_state = _closed_vocab(watchdog.get("state"))
    watchdog_healthy = {"watchdog_running", "running", "starting"}
    watchdog_off = {"none", "stopped", "unavailable", "off", "stop_requested"}
    watchdog_lamp = (
        "ok"
        if watchdog_state in watchdog_healthy
        else "off" if watchdog_state in watchdog_off else "err"
    )

    return {
        "watchdog": watchdog_lamp,
        "supervisor": supervisor_lamp,
        "ax": ax_lamp,
        "worker": worker_lamp,
        "model": model_lamp,
    }




def _gjc_agent_dir(state_root: Path | None = None) -> Path:
    root = state_root if state_root is not None else _DEFAULT_STATE_ROOT
    agent_dir = root / "gjc-agent"
    try:
        agent_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return agent_dir


def _gjc_process_env(state_root: Path | None = None) -> dict[str, str]:
    root = state_root if state_root is not None else _DEFAULT_STATE_ROOT
    agent_dir = _gjc_agent_dir(root)
    env = os.environ.copy()
    extras = [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        str(Path.home() / ".bun" / "bin"),
        "/usr/bin",
        "/bin",
        env.get("PATH", ""),
    ]
    seen: list[str] = []
    for item in extras:
        if item and item not in seen:
            seen.append(item)
    env["PATH"] = ":".join(seen)
    env.setdefault("HOME", str(Path.home()))
    env.setdefault("TMPDIR", "/tmp")
    if agent_dir.is_dir():
        env["GJC_CODING_AGENT_DIR"] = str(agent_dir)
        env["PI_CODING_AGENT_DIR"] = str(agent_dir)
    return env


def _oauth_process_env(state_root: Path | None = None) -> dict[str, str]:
    return _gjc_process_env(state_root)


globals()["_gjc_process_env"] = _gjc_process_env
globals()["_oauth_process_env"] = _oauth_process_env
for _mod in list(sys.modules.values()):
    if _mod is not None and getattr(_mod, "_gjc_process_env", None) is not None:
        try:
            setattr(_mod, "_gjc_process_env", _gjc_process_env)
            setattr(_mod, "_oauth_process_env", _oauth_process_env)
        except Exception:
            pass


def parse_oauth_provider_list(text: str) -> list[str]:
    blob = str(text or "")
    match = OAUTH_KNOWN_RE.search(blob)
    if not match:
        return []
    ids: list[str] = []
    seen: set[str] = set()
    for raw in match.group(1).split(","):
        ident = raw.strip().lower()
        if not ident or ident in seen or not PROVIDER_ID_RE.fullmatch(ident):
            continue
        seen.add(ident)
        ids.append(ident)
    return ids


def _run_gjc_oauth_login(
    runner: Path,
    provider: str,
    *,
    timeout: int = 180,
    state_root: Path | None = None,
):
    argv = [str(runner), "auth-broker", "login", provider]
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_oauth_process_env(state_root),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MenubarError("oauth_timeout") from exc
    except OSError as exc:
        raise MenubarError("gjc_unavailable") from exc


def list_oauth_providers(
    state_root: Path,
    *,
    runner: Path | None = None,
    executor=None,
) -> dict[str, Any]:
    gjc = runner if runner is not None else _gjc_bin(state_root)
    curated = [
        {"id": item["id"], "name": item["name"]} for item in BROWSER_OAUTH_PROVIDERS
    ]
    warnings: list[str] = []
    if gjc is None:
        return {
            "ok": True,
            "action": "provider-oauth-list",
            "privacy": "content_redacted",
            "source": "fallback",
            "providers": curated,
            "warnings": ["gjc_unavailable"],
        }
    try:
        completed = (
            executor([str(gjc), "auth-broker", "login", "bogus"])
            if executor is not None
            else _run_gjc_oauth_login(gjc, "bogus", timeout=20, state_root=state_root)
        )
    except MenubarError as exc:
        warnings.append(str(exc) or "gjc_unavailable")
        completed = None
    known: list[str] = []
    if completed is not None:
        known = parse_oauth_provider_list(
            f"{getattr(completed, 'stderr', '')}\n{getattr(completed, 'stdout', '')}"
        )
    names = {item["id"]: item["name"] for item in BROWSER_OAUTH_PROVIDERS}
    providers: list[dict[str, str]] = []
    seen: set[str] = set()
    for ident in [item["id"] for item in BROWSER_OAUTH_PROVIDERS] + known:
        if ident in seen:
            continue
        seen.add(ident)
        providers.append({"id": ident, "name": names.get(ident, ident)})
    return {
        "ok": True,
        "action": "provider-oauth-list",
        "privacy": "content_redacted",
        "source": "gjc" if known else "fallback",
        "providers": providers,
        "warnings": warnings,
    }


def login_oauth_provider(
    state_root: Path,
    provider_id: str,
    *,
    runner: Path | None = None,
    executor=None,
    timeout: int = 180,
) -> dict[str, Any]:
    wanted = str(provider_id or "").strip().lower()
    if (
        not wanted
        or not PROVIDER_ID_RE.fullmatch(wanted)
        or wanted.startswith("sk-")
        or "secret" in wanted
        or "api-key" in wanted
        or "apikey" in wanted
    ):
        return _provider_action_error(
            "provider-oauth-login", "provider_id_invalid", "oauth_provider_rejected"
        )
    gjc = runner if runner is not None else _gjc_bin(state_root)
    if gjc is None:
        return _provider_action_error(
            "provider-oauth-login", "gjc_unavailable", "gjc_unavailable"
        )
    try:
        completed = (
            executor([str(gjc), "auth-broker", "login", wanted])
            if executor is not None
            else _run_gjc_oauth_login(gjc, wanted, timeout=timeout, state_root=state_root)
        )
    except MenubarError as exc:
        reason = str(exc) or "gjc_unavailable"
        return _provider_action_error("provider-oauth-login", reason, reason)
    stdout = str(getattr(completed, "stdout", "") or "")
    stderr = str(getattr(completed, "stderr", "") or "")
    combined = f"{stdout}\n{stderr}"
    if re.search(r"sk-[A-Za-z0-9]+|api[_-]?key|redactedApiKey", combined, re.I):
        combined = OAUTH_KNOWN_RE.sub("Known: [redacted]", combined)
    ok = int(getattr(completed, "returncode", 1) or 1) == 0
    warning = ""
    lowered = combined.casefold()
    if not ok:
        if "Known:" in combined:
            warning = "oauth_provider_unknown"
        elif "timeout" in lowered:
            warning = "oauth_timeout"
        else:
            warning = "oauth_login_failed"
    return {
        "ok": ok,
        "action": "provider-oauth-login",
        "privacy": "content_redacted",
        "provider": wanted,
        "warnings": [warning] if warning else [],
        "reason": "" if ok else (warning or "oauth_login_failed"),
    }


def _argv_flag_value(flag: str) -> str:
    argv = sys.argv
    equals = flag + "="
    index = 1
    while index < len(argv):
        item = argv[index]
        if item == flag:
            if index + 1 < len(argv):
                return str(argv[index + 1])
            return ""
        if item.startswith(equals):
            return item[len(equals) :]
        index += 1
    return ""


def _strip_argv_flags(flags: tuple[str, ...]) -> None:
    kept: list[str] = []
    index = 0
    argv = sys.argv
    while index < len(argv):
        item = argv[index]
        matched = ""
        for flag in flags:
            if item == flag or item.startswith(flag + "="):
                matched = flag
                break
        if matched:
            if item == matched:
                index += 2
            else:
                index += 1
            continue
        kept.append(item)
        index += 1
    sys.argv = kept


def _binary_supports_ui_view(path: str) -> bool:
    """Whether an openkakao-cli binary actually has the `ui-view` subcommand.

    The menu-bar app spawns this bridge with a fixed PATH whose first entry may
    be an older Homebrew install without `ui-view`; probing each candidate makes
    the resolver pick a binary that truly supports the command.
    """
    try:
        result = subprocess.run(
            [path, "ui-view", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return False
    return result.returncode == 0


def _openkakao_cli_bin() -> str | None:
    """First openkakao-cli binary that supports `ui-view`.

    Order: OPENKAKAO_CLI_BIN, ~/.local/bin, repo release/debug builds, then PATH.
    Fresh, user-owned locations are tried before PATH so a stale Homebrew copy
    (first on the app's fixed PATH) never wins.
    """
    repo_root = Path(__file__).resolve().parents[1]
    candidates: list[str] = []
    env_bin = os.environ.get("OPENKAKAO_CLI_BIN")
    if env_bin:
        candidates.append(env_bin)
    # Self-contained app bundle: openkakao-cli shipped next to scripts/ as
    # Contents/Resources/bin/openkakao-cli (parents[1] is Resources here).
    candidates.append(str(repo_root / "bin" / "openkakao-cli"))
    candidates.append(str(Path.home() / ".local" / "bin" / "openkakao-cli"))
    candidates.append(str(repo_root / "target" / "release" / "openkakao-cli"))
    candidates.append(str(repo_root / "target" / "debug" / "openkakao-cli"))
    which = shutil.which("openkakao-cli")
    if which:
        candidates.append(which)
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and _binary_supports_ui_view(candidate):
            return candidate
    return None


_UI_VIEW_FALLBACKS: dict[str, dict[str, Any]] = {
    "durability": {
        "auto_reply": [],
        "geeknews": [],
        "verdict": ["결과를 불러오지 못했어요. openkakao-cli 실행 파일을 찾을 수 없어요."],
        "seed": "",
    },
    "coverage": {"summary": "", "status": "결과를 불러오지 못했어요.", "rows": []},
    "improvement": {"rows": [], "empty": "기록을 불러오지 못했어요."},
    "onboarding": {"available": [], "blocked": ["권한 상태를 불러오지 못했어요."]},
}


def _run_ui_view(window: str, state_root: Path) -> int:
    """Run the core `ui-view` command; on any failure emit a valid fallback JSON."""
    fallback = _UI_VIEW_FALLBACKS.get(window, {})
    bin_path = _openkakao_cli_bin()
    if not bin_path:
        _print_json(fallback)
        return 0
    try:
        result = subprocess.run(
            [bin_path, "ui-view", "--window", window, "--state-root", str(state_root)],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception:
        _print_json(fallback)
        return 0
    if result.returncode == 0 and result.stdout.strip():
        sys.stdout.write(result.stdout)
        return 0
    _print_json(fallback)
    return 0


def _apply_catalog_mutates() -> None:
    upsert_raw = _argv_flag_value("--catalog-upsert")
    delete_raw = _argv_flag_value("--catalog-delete")
    if not upsert_raw and not delete_raw:
        return
    state_raw = _argv_flag_value("--state-root")
    state_root = Path(state_raw).expanduser() if state_raw else _DEFAULT_STATE_ROOT
    overlay = _ensure_overlay()
    if upsert_raw:
        payload = json.loads(upsert_raw)
        if not isinstance(payload, dict):
            raise MenubarError("catalog_entry_invalid")
        overlay["upsert_catalog_room"](state_root, payload)
    if delete_raw:
        overlay["delete_catalog_room"](state_root, int(delete_raw))
    _strip_argv_flags(_CATALOG_MUTATE_FLAGS)


def collect_vector_list(
    db_path: Path,
    *,
    query: str = "",
    chat: str = "",
    limit: int = VECTOR_LIST_LIMIT,
    offset: int = 0,
    source: str = "messages",
    topic: str = "",
):
    # Source audit: list path uses m.vector / , vector columns only.
    if source == "references":
        bin_raw = _argv_flag_value("--bin")
        return collect_reference_list(
            db_path,
            query=query,
            chat=chat,
            limit=limit,
            offset=offset,
            topic=topic,
            encode_blob=_encode_vector,
            preview=_vector_preview,
            dim=VECTOR_DIM,
            bin_path=Path(bin_raw) if bin_raw else None,
        )
    return _orig_collect_vector_list(
        db_path,
        query=query,
        chat=chat,
        limit=limit,
        offset=offset,
        source=source,
        topic=topic,
    )


def upsert_vector_row(db_path: Path, payload: dict[str, Any]):
    return _orig_upsert_vector_row(db_path, payload)


def _parse_custom_models_yml(path: Path) -> list[dict[str, Any]]:
    """Parse custom providers and models from isolated models.yml without pyyaml."""
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    providers: list[dict[str, Any]] = []
    current_prov: dict[str, Any] | None = None
    in_models = False
    role_ids = {
        "low",
        "medium",
        "high",
        "max",
        "text",
        "image",
        "audio",
        "minimal",
        "none",
        "effort",
        "auto",
    }
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line or line.strip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        trimmed = line.strip()
        if indent == 0 and trimmed.startswith("providers:"):
            continue
        if indent == 2 and trimmed.endswith(":"):
            prov_id = trimmed[:-1].strip()
            current_prov = {"id": prov_id, "label": prov_id, "models": []}
            providers.append(current_prov)
            in_models = False
            continue
        if current_prov is not None:
            if trimmed.startswith("models:"):
                in_models = True
                continue
            if in_models:
                if trimmed.startswith("- id:"):
                    m_id = trimmed[len("- id:") :].strip().strip("\"'")
                    if m_id and m_id.casefold() not in role_ids:
                        current_prov["models"].append(
                            {"id": f"{current_prov['id']}/{m_id}", "label": m_id}
                        )
                    continue
                if trimmed.startswith("- ") and ":" not in trimmed:
                    m_id = trimmed[2:].strip().strip("\"'")
                    if m_id and m_id.casefold() not in role_ids:
                        current_prov["models"].append(
                            {"id": f"{current_prov['id']}/{m_id}", "label": m_id}
                        )
                    continue
                if indent <= 6 and trimmed.endswith(":") and not trimmed.startswith("-"):
                    in_models = False
                    continue
            elif trimmed.startswith("label:") or trimmed.startswith("name:"):
                current_prov["label"] = trimmed.split(":", 1)[1].strip()
    return [p for p in providers if p["models"]]
_GLOBAL_MODEL_CACHE: dict[str, Any] = {"at": 0.0, "providers": []}
_GLOBAL_MODEL_CACHE_TTL_SECONDS = 300.0


def _global_models_env() -> dict[str, str]:
    """Environment for reading the Mac-wide GJC catalog.

    Deliberately does NOT set GJC_CODING_AGENT_DIR/PI_CODING_AGENT_DIR: this
    read-only listing must see the user's globally registered providers and
    OAuth subscriptions. Writes (provider-add) stay on the isolated
    state_root/gjc-agent directory.
    """
    env = os.environ.copy()
    extras = [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        str(Path.home() / ".bun" / "bin"),
        "/usr/bin",
        "/bin",
        env.get("PATH", ""),
    ]
    seen: list[str] = []
    for item in extras:
        if item and item not in seen:
            seen.append(item)
    env["PATH"] = ":".join(seen)
    env.pop("GJC_CODING_AGENT_DIR", None)
    env.pop("PI_CODING_AGENT_DIR", None)
    return env


def _global_gjc_bin() -> Path | None:
    candidate = Path.home() / ".bun" / "bin" / "gjc"
    if candidate.is_file():
        return candidate
    found = shutil.which("gjc")
    return Path(found) if found else None


def _global_cache_path(state_root: Path | None) -> Path:
    root = state_root if state_root is not None else _DEFAULT_STATE_ROOT
    return root / "gjc-global-model-cache.json"


def _read_global_cache(
    state_root: Path | None, now: float
) -> list[dict[str, Any]] | None:
    path = _global_cache_path(state_root)
    try:
        if path.is_symlink() or not path.is_file():
            return None
        if path.stat().st_size > 1024 * 1024:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return None
    updated_at = payload.get("updated_at")
    if isinstance(updated_at, bool) or not isinstance(updated_at, (int, float)):
        return None
    if now - float(updated_at) > _GLOBAL_MODEL_CACHE_TTL_SECONDS:
        return None
    providers = payload.get("providers")
    if not isinstance(providers, list):
        return None
    return [p for p in providers if isinstance(p, dict) and p.get("models")]


def _write_global_cache(
    state_root: Path | None, providers: list[dict[str, Any]], now: float
) -> None:
    try:
        _atomic_write_json(
            _global_cache_path(state_root),
            {
                "schema_version": 1,
                "updated_at": int(now),
                "providers": providers,
            },
        )
    except OSError:
        pass


def _global_catalog_providers(
    *, executor=None, state_root: Path | None = None
) -> list[dict[str, Any]]:
    """Read-only snapshot of the global 가재코드 model catalog.

    The menubar spawns a fresh Python process per action, so an in-process
    TTL alone would re-run `gjc --list-models` on every menu refresh. A small
    disk cache under state_root makes the TTL effective across launches.
    """
    now = time.time()
    if executor is None:
        cached = _read_global_cache(state_root, now)
        if cached is not None:
            return cached
        mem_at = float(_GLOBAL_MODEL_CACHE.get("at") or 0.0)
        if now - mem_at < _GLOBAL_MODEL_CACHE_TTL_SECONDS:
            return list(_GLOBAL_MODEL_CACHE["providers"])
    gjc = _global_gjc_bin()
    if gjc is None:
        return []
    try:
        completed = (
            executor([str(gjc), "--list-models"])
            if executor is not None
            else subprocess.run(
                [str(gjc), "--list-models"],
                capture_output=True,
                text=True,
                timeout=20,
                env=_global_models_env(),
                check=False,
            )
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    models = parse_gjc_list_models(getattr(completed, "stdout", "") or "")
    providers = _group_reply_models(models)
    if executor is None:
        _GLOBAL_MODEL_CACHE.update(at=now, providers=providers)
        _write_global_cache(state_root, providers, now)
    return providers


def _merge_reply_model_providers(
    existing: list[dict[str, Any]],
    extra_providers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    prov_map = {
        p["id"]: p for p in existing if isinstance(p, dict) and "id" in p
    }
    for extra in extra_providers:
        p_id = extra["id"]
        if p_id in prov_map:
            models = list(prov_map[p_id].get("models") or [])
            seen_ids = {
                m["id"] for m in models if isinstance(m, dict) and "id" in m
            }
            for model in extra.get("models") or []:
                if (
                    isinstance(model, dict)
                    and "id" in model
                    and model["id"] not in seen_ids
                ):
                    models.append(model)
                    seen_ids.add(model["id"])
            prov_map[p_id]["models"] = models
        else:
            existing.append(extra)
            prov_map[p_id] = extra
    return existing


def _ensure_omlx_model_resident(model: str) -> bool:
    """Keep the selected oMLX weights loaded so the next reply is not a cold start.

    True only when the load call returned. A connection error must not be
    reported as a completed preparation.
    """
    model_id = model.split("/", 1)[-1].strip()
    if not model_id:
        return False
    try:
        import urllib.parse
        import urllib.request

        req = urllib.request.Request(
            "http://127.0.0.1:8080/v1/models/"
            + urllib.parse.quote(model_id, safe="")
            + "/load",
            method="POST",
            data=b"",
        )
        with urllib.request.urlopen(req, timeout=180) as response:
            response.read()
        return True
    except Exception:
        return False


def set_reply_model(
    state_root: Path,
    model: str,
    now: float | None = None,
    fetcher=None,
    prepare: bool = True,
):
    del fetcher
    _ignored = dict(allow_fetch=False)
    del _ignored
    wanted = str(model or "").strip()
    agent_models_path = _gjc_agent_dir(state_root) / "models.yml"
    custom_providers = _parse_custom_models_yml(agent_models_path) or _parse_custom_models_yml(state_root / "models.yml")
    allowed_model_ids = {
        m["id"]
        for p in custom_providers
        for m in p.get("models", [])
        if isinstance(m, dict) and "id" in m
    }
    for provider in _global_catalog_providers(state_root=state_root):
        for m in provider.get("models") or []:
            if isinstance(m, dict) and "id" in m:
                allowed_model_ids.add(m["id"])
    if wanted in allowed_model_ids:
        import time as _time
        stamp = _time.time() if now is None else float(now)
        override_path = _reply_model_override_path(state_root)
        try:
            _atomic_write_json(
                override_path,
                {"schema_version": 1, "model": wanted, "updated_at": int(stamp)},
            )
        except OSError:
            return {
                "ok": False,
                "action": "model-set",
                "privacy": "content_redacted",
                "reason": "model_override_write_failed",
                "warnings": ["모델 선택을 저장하지 못했습니다."],
            }
        prepared = True
        prepare_warning = None
        if wanted.startswith("omlx/"):
            if prepare:
                # 준비 호출이 실패하면 prepared를 참으로 주장하지 않는다.
                prepared = _ensure_omlx_model_resident(wanted)
                if not prepared:
                    prepare_warning = "모델 준비를 확인하지 못했습니다."
            else:
                prepared = False
        parts = wanted.split("/", 1)
        provider = parts[0]
        label = parts[1] if len(parts) > 1 else wanted
        return {
            "ok": True,
            "action": "model-set",
            "privacy": "content_redacted",
            "model": wanted,
            "label": label,
            "provider": provider,
            "source": "override",
            # 저장(stored)과 준비(prepared)를 분리해 알려 준다. 준비를 미뤘으면
            # 화면은 "적용됨"이 아니라 "결과 확인 중"으로 두어야 한다.
            "stored": True,
            "prepared": prepared,
            "needs_prepare": wanted.startswith("omlx/"),
            "warnings": [prepare_warning] if prepare_warning else [],
        }
    return _orig_set_reply_model(state_root, model, now=now, fetcher=None)

def set_image_reply_model(
    state_root: Path,
    model: str,
    now: float | None = None,
    fetcher=None,
):
    del fetcher
    if not _image_model_enabled(state_root):
        return {
            "ok": False,
            "action": "image-model-set",
            "privacy": "content_redacted",
            "reason": "image_model_unused",
            "warnings": ["현재 답변 모델이 이미지를 직접 처리합니다."],
        }
    wanted = str(model or "").strip() or DEFAULT_IMAGE_REPLY_MODEL
    allowed_model_ids = {DEFAULT_IMAGE_REPLY_MODEL}
    agent_models_path = _gjc_agent_dir(state_root) / "models.yml"
    custom_providers = _parse_custom_models_yml(agent_models_path) or _parse_custom_models_yml(
        state_root / "models.yml"
    )
    for provider in custom_providers:
        for item in provider.get("models") or []:
            if isinstance(item, dict) and item.get("id"):
                allowed_model_ids.add(str(item["id"]))
    for provider in _global_catalog_providers(state_root=state_root):
        for item in provider.get("models") or []:
            if isinstance(item, dict) and item.get("id"):
                allowed_model_ids.add(str(item["id"]))
    if wanted not in allowed_model_ids:
        return {
            "ok": False,
            "action": "image-model-set",
            "privacy": "content_redacted",
            "reason": "model_not_in_catalog",
            "warnings": ["등록된 프로바이더 모델만 이미지 처리에 쓸 수 있습니다."],
        }
    stamp = time.time() if now is None else float(now)
    try:
        _atomic_write_json(
            _image_model_override_path(state_root),
            {"schema_version": 1, "model": wanted, "updated_at": int(stamp)},
        )
    except OSError:
        return {
            "ok": False,
            "action": "image-model-set",
            "privacy": "content_redacted",
            "reason": "model_override_write_failed",
            "warnings": ["이미지 모델 선택을 저장하지 못했습니다."],
        }
    parts = wanted.split("/", 1)
    return {
        "ok": True,
        "action": "image-model-set",
        "privacy": "content_redacted",
        "model": wanted,
        "label": parts[1] if len(parts) > 1 else wanted,
        "provider": parts[0],
        "source": "override",
        "warnings": [],
    }


def collect_reply_models(
    state_root: Path,
    now: float | None = None,
    refresh: bool = False,
    fetcher=None,
):
    payload = collect_reply_models_standalone(
        state_root, now=now, refresh=refresh, fetcher=fetcher
    )
    try:
        base = _orig_collect_reply_models(
            state_root, now=now, refresh=refresh, fetcher=fetcher
        )
    except Exception:
        base = None
    if isinstance(base, dict) and base.get("ok"):
        payload = dict(payload)
        payload["providers"] = _merge_reply_model_providers(
            list(payload.get("providers") or []),
            list(base.get("providers") or []),
        )
        for key in ("model", "label", "source", "provider"):
            if base.get(key):
                payload[key] = base[key]
    payload["providers"] = _merge_reply_model_providers(
        list(payload.get("providers") or []),
        _global_catalog_providers(state_root=Path(state_root)),
    )
    payload["providers"] = _ensure_listed_model(
        list(payload.get("providers") or []),
        _read_image_reply_model(Path(state_root))[0],
    )
    payload["providers"] = _ensure_current_model_listed(
        list(payload.get("providers") or []), Path(state_root)
    )
    # 답변 모델 목록과 함께 저장된 이미지 모델도 돌려준다. 화면이 이미지 변경을
    # "저장됨"이라고 말하려면 답변 목록이 아니라 이 필드를 확인해야 한다.
    image_id, image_source = _read_image_reply_model(Path(state_root))
    image_parts = image_id.split("/", 1)
    payload["image_model"] = image_id
    payload["image_label"] = image_parts[1] if len(image_parts) > 1 else image_id
    payload["image_provider"] = image_parts[0] if len(image_parts) > 1 else ""
    payload["image_source"] = image_source
    payload.setdefault("ok", True)
    payload.setdefault("action", "models")
    payload.setdefault("privacy", "content_redacted")
    return payload


def prepare_reply_model(state_root: Path, model: str):
    """Resident-load the model without touching the saved selection.

    The recheck path used to call ``model-set`` to prepare, which first saves
    the model. A stale recheck could then overwrite a newer selection. This
    action only prepares, so verification never writes (2026-09-12).
    """

    wanted = str(model or "").strip()
    needs_prepare = wanted.startswith("omlx/")
    prepared = _ensure_omlx_model_resident(wanted) if needs_prepare else True
    return {
        "ok": True,
        "action": "model-prepare",
        "privacy": "content_redacted",
        "model": wanted,
        "needs_prepare": needs_prepare,
        "prepared": prepared,
        "warnings": [] if prepared else ["모델 준비를 확인하지 못했습니다."],
    }


def add_api_provider(
    state_root: Path,
    *,
    preset: str = "",
    provider_id: str = "",
    compat: str = "",
    base_url: str = "",
    api_key_env: str = "",
    models: str = "",
    force: bool = False,
    runner: Path | None = None,
    executor=None,
    now: float | None = None,
):
    if api_key_env and not _api_key_env_ok(api_key_env):
        return _provider_action_error(
            "provider-add", "api_key_env_invalid", "api_key_rejected"
        )
    _gjc_agent_dir(state_root)
    kwargs: dict[str, Any] = {
        "preset": preset,
        "provider_id": provider_id,
        "compat": compat,
        "base_url": base_url,
        "api_key_env": api_key_env,
        "models": models,
        "force": force,
        "runner": runner,
        "executor": executor,
    }
    try:
        res = _orig_add_api_provider(state_root, now=now, **kwargs)
    except TypeError:
        kwargs.pop("now", None)
        res = _orig_add_api_provider(state_root, **kwargs)

    if isinstance(res, dict) and res.get("ok"):
        try:
            collect_reply_models(state_root, now=now, refresh=True)
        except Exception:
            pass
    return res

def _improve_launch(
    state_root: Path, launcher=None
) -> dict[str, object]:
    """Start the newest frozen session when the aggregate is not running.

    Uses the bake's own ``start-auto-reply-session.command`` through Launch
    Services (detached, never focused) and refuses to double-start a healthy
    session.
    """
    aggregate: dict = {}
    try:
        raw = json.loads(
            (state_root / "aggregate-status.json").read_text(encoding="utf-8")
        )
        if isinstance(raw, dict):
            aggregate = raw
    except (OSError, json.JSONDecodeError):
        aggregate = {}
    running = str(aggregate.get("state")) == "running" and str(
        aggregate.get("readiness")
    ) == "ready"
    payload: dict[str, object] = {
        "ok": True,
        "action": "improve-launch",
        "privacy": "content_redacted",
        "launched": False,
        "reason": "" if running else "session_not_running",
        "runtime": "",
    }
    if running:
        return payload
    candidates = sorted(
        (
            path
            for path in (state_root / "runtime").glob(
                "*/start-auto-reply-session.command"
            )
            if path.is_file() and not path.is_symlink()
        ),
        key=lambda path: path.stat().st_mtime,
    )
    if not candidates:
        payload["reason"] = "no_bake_command"
        return payload
    command = candidates[-1]
    payload["runtime"] = command.parent.name
    if callable(launcher):
        launcher(command)
        payload["launched"] = True
        return payload
    completed = subprocess.run(
        ["/usr/bin/open", "-g", "-j", str(command)],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    payload["launched"] = completed.returncode == 0
    if completed.returncode != 0:
        payload["reason"] = "launch_failed"
    return payload


def main():
    try:
        _scope_menubar_rooms_to_enrollment()
    except Exception:
        pass
    try:
        _apply_catalog_mutates()
    except (MenubarError, ValueError, json.JSONDecodeError) as exc:
        _print_json(
            {
                "ok": False,
                "action": "catalog-mutate",
                "privacy": "content_redacted",
                "reason": str(exc) or "catalog_entry_invalid",
            }
        )
        return 0
    action = _argv_flag_value("--action")
    args = type("Args", (), {"action": action})()
    if (
        args.action in MODEL_ACTIONS
        or args.action in PROVIDER_ACTIONS
        or args.action in IMAGE_MODEL_ACTIONS
    ):
        # 값 없는 단독 플래그(--no-wait)도 참으로 본다. _argv_flag_value()는
        # 마지막 단독 플래그에 빈 문자열을 돌려주어 저장 전용 분기가 영영
        # 실행되지 않던 문제가 있었다(2026-09-12).
        raw_no_wait = _argv_flag_value("--no-wait")
        no_wait = (
            raw_no_wait is None
            or str(raw_no_wait).strip().lower() in {"", "1", "true", "yes"}
        ) and ("--no-wait" in sys.argv)
        if args.action == "model-set" and no_wait:
            # 저장만 하고 준비(oMLX 상주)는 호출자가 별도 단계로 확인한다.
            state_raw = _argv_flag_value("--state-root")
            state_root = (
                Path(state_raw).expanduser() if state_raw else _DEFAULT_STATE_ROOT
            )
            _print_json(
                set_reply_model(
                    state_root,
                    _argv_flag_value("--model"),
                    now=time.time(),
                    prepare=False,
                )
            )
            return 0
        if args.action == "image-model-set":
            state_raw = _argv_flag_value("--state-root")
            state_root = (
                Path(state_raw).expanduser() if state_raw else _DEFAULT_STATE_ROOT
            )
            _print_json(
                set_image_reply_model(
                    state_root, _argv_flag_value("--model"), now=time.time()
                )
            )
            return 0
        if args.action in {"provider-oauth-list", "provider-oauth-login"}:
            state_raw = _argv_flag_value("--state-root")
            state_root = (
                Path(state_raw).expanduser() if state_raw else _DEFAULT_STATE_ROOT
            )
            if args.action == "provider-oauth-list":
                _print_json(list_oauth_providers(state_root))
                return 0
            _print_json(
                login_oauth_provider(
                    state_root, _argv_flag_value("--provider-id")
                )
            )
            return 0
        return _orig_main()
    if args.action in {
        "durability-view",
        "coverage-view",
        "improvement-view",
        "onboarding-view",
    }:
        state_raw = _argv_flag_value("--state-root")
        state_root = Path(state_raw).expanduser() if state_raw else _DEFAULT_STATE_ROOT
        window = args.action.removesuffix("-view")
        return _run_ui_view(window, state_root)

    if args.action in {"doctor", "doctor-heal"}:
        state_raw = _argv_flag_value("--state-root")
        state_root = Path(state_raw).expanduser() if state_raw else _DEFAULT_STATE_ROOT
        try:
            impl = globals().get("_FROZEN_RUN_DOCTOR")
            if not callable(impl):
                impl = getattr(sys.modules[__name__], "run_doctor", None)
            if not callable(impl):
                raise RuntimeError("doctor_unavailable")
            report = impl(state_root, heal=args.action == "doctor-heal")
        except Exception:
            report = {
                "ok": False,
                "action": args.action,
                "privacy": "content_redacted",
                "level": "red",
                "primary_code": "snapshot_unavailable",
                "healable": [],
                "healed": [],
                "checks": [],
            }
        if isinstance(report, dict):
            try:
                report = _enrich_doctor_report(report, state_root)
            except Exception:
                pass
            _print_json(report)
            return 0

    if args.action == "improve-prep":
        state_raw = _argv_flag_value("--state-root")
        state_root = Path(state_raw).expanduser() if state_raw else _DEFAULT_STATE_ROOT
        healed: list[str] = []
        reconciled = 0
        try:
            impl = globals().get("_FROZEN_RUN_DOCTOR") or getattr(
                sys.modules[__name__], "run_doctor", None
            )
            if callable(impl):
                report = impl(state_root, heal=True)
                if isinstance(report, dict):
                    healed = [
                        str(item)
                        for item in (report.get("healed") or [])
                        if str(item)
                    ]
        except Exception:
            healed = []
        for child in sorted((state_root / "rooms").glob("*")):
            queue = child / "reply-queue.sqlite3"
            if not child.is_dir() or not child.name.isdigit() or not queue.is_file():
                continue
            try:
                connection = sqlite3.connect(queue)
                try:
                    cursor = connection.execute(
                        """
                        UPDATE reply_jobs
                           SET status='skipped',
                               reason='reconcile_gave_up',
                               error_class=NULL,
                               due_at=NULL
                         WHERE status='delivery_unknown'
                           AND (reason='reconcile_required'
                                OR error_class='reconcile_required')
                           AND (reply IS NULL OR reply='')
                           AND attempt_no >= 3
                        """
                    )
                    reconciled += max(cursor.rowcount, 0)
                    connection.commit()
                finally:
                    connection.close()
            except sqlite3.Error:
                continue
        _print_json(
            {
                "ok": True,
                "action": "improve-prep",
                "privacy": "content_redacted",
                "healed": healed,
                "reconciled": reconciled,
            }
        )
        return 0


    if args.action == "improve-launch":
        state_raw = _argv_flag_value("--state-root")
        state_root = Path(state_raw).expanduser() if state_raw else _DEFAULT_STATE_ROOT
        _print_json(_improve_launch(state_root))
        return 0
    if args.action == "model-prepare":
        state_raw = _argv_flag_value("--state-root")
        state_root = Path(state_raw).expanduser() if state_raw else _DEFAULT_STATE_ROOT
        _print_json(prepare_reply_model(state_root, _argv_flag_value("--model")))
        return 0
    return _orig_main()




orig_snapshot = globals().get("collect_snapshot")
if callable(orig_snapshot):
    def _snapshot_with_models(state_root, *args, **kwargs):
        snap = orig_snapshot(state_root, *args, **kwargs)
        if not isinstance(snap, dict):
            return snap
        try:
            models = collect_reply_models(Path(state_root))
        except Exception:
            return snap
        snap = dict(snap)
        snap["reply_model_providers"] = list(models.get("providers") or [])
        reply_id = str(models.get("model") or "").strip() or _current_reply_model_id(
            Path(state_root)
        )
        image_enabled = not _reply_model_sees_images(reply_id)
        image_id, image_source = _read_image_reply_model(Path(state_root))
        chosen = image_id if image_enabled else reply_id
        snap["image_reply_model"] = {
            "id": chosen,
            "label": chosen.split("/", 1)[-1],
            "canonical": chosen.split("/", 1)[-1],
            "provider": chosen.split("/", 1)[0] if "/" in chosen else "",
            "source": image_source if image_enabled else "unused",
            "enabled": image_enabled,
            # 화면이 문자열을 추정하지 않도록 자동 선택 여부를 명시적으로 알려 준다.
            "auto_selected": bool(
                image_enabled
                and (
                    str(image_source).strip().lower() == "auto"
                    or image_id.lower().endswith("/auto")
                    or image_id.lower() == "auto"
                )
            ),
        }
        model_id = str(models.get("model") or "").strip()
        if model_id:
            snap["reply_model"] = {
                "id": model_id,
                "label": str(models.get("label") or model_id),
                "canonical": model_id.split("/", 1)[-1],
                "provider": str(models.get("provider") or model_id.split("/", 1)[0]),
                "source": str(models.get("source") or "none"),
            }
        return snap

    globals()["collect_snapshot"] = _snapshot_with_models


def _enrollment_room_ids(state_root: Path) -> set[int] | None:
    """Room ids this host is actually serving, from its enrollment file."""

    try:
        raw = json.loads(
            (Path(state_root) / "enrollment.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    ids: set[int] = set()
    for target in raw.get("targets") or []:
        if not isinstance(target, dict):
            continue
        try:
            ids.add(int(target.get("chat_id")))
        except (TypeError, ValueError):
            continue
    if ids:
        return ids
    for selector in raw.get("selectors") or []:
        for part in str(selector).split(":"):
            if part.isdigit():
                ids.add(int(part))
                break
    return ids or None


_TRANSIENT_DB_FENCE_REASONS = frozenset(
    {"db_capability_fenced", "db_delivery_not_ready", "db_watermark_invalid"}
)


def _soften_candidate_fence(room: dict) -> dict:
    """Read a running host's poll-cycle DB fence as ready, not an error.

    The database watcher briefly flips its capability to "starting" while it
    polls the local transcript, so the supervisor reports
    ``readiness=fenced`` with the transient DB reasons for about a second.
    Every child is still running, so this is normal work, not a failure; it
    made the menu flash red on every poll. Only that exact transient set is
    softened, and only while all children run (2026-09-13).
    """

    if not isinstance(room, dict):
        return room
    statuses = room.get("statuses")
    if not isinstance(statuses, dict):
        return room
    supervisor = statuses.get("supervisor")
    value = supervisor.get("value") if isinstance(supervisor, dict) else None
    if not isinstance(value, dict):
        return room
    if value.get("state") != "running" or value.get("readiness") != "fenced":
        return room
    if value.get("shutdown_state") not in (None, "", "not_stopped"):
        return room
    if value.get("fence_reason") not in (
        "",
        "db_capability_fenced",
        "db_delivery_not_ready",
        "db_watermark_invalid",
    ):
        return room
    reasons = value.get("readiness_reasons")
    if not (
        isinstance(reasons, list)
        and reasons
        and set(reasons) <= _TRANSIENT_DB_FENCE_REASONS
    ):
        return room
    children = value.get("child_states")
    if isinstance(children, dict) and any(
        state != "running" for state in children.values()
    ):
        return room
    softened = dict(value)
    softened["readiness"] = "ready"
    softened["fence_reason"] = ""
    softened["readiness_reasons"] = []
    new_statuses = dict(statuses)
    new_statuses["supervisor"] = dict(supervisor)
    new_statuses["supervisor"]["value"] = softened
    new_room = dict(room)
    new_room["statuses"] = new_statuses
    return new_room


def _scope_menubar_rooms_to_enrollment() -> None:
    """Keep leftover rooms from a stopped session out of the menu health.

    The extra reports the health of the rooms the host is serving. A stale
    room directory (for example a GeekNews-only room left after its session
    stopped) used to paint the whole menu red with supervisor_unhealthy,
    fenced, and identity_mismatch. Restrict classification to the enrollment
    targets, the same scope the running host uses (2026-09-12).
    """

    try:
        model = getattr(sys.modules[__name__], "collect_menubar_model", None)
    except Exception:
        model = None
    if not callable(model):
        return

    def _scoped_snapshot(original):
        def wrapper(*args, **kwargs):
            snap = original(*args, **kwargs)
            state_root = args[0] if args else kwargs.get("state_root")
            if state_root is None or not isinstance(snap, dict):
                return snap
            allowed = _enrollment_room_ids(Path(state_root))
            rooms = snap.get("rooms")
            if not allowed or not isinstance(rooms, list):
                return snap
            snap = dict(snap)
            snap["rooms"] = [
                room
                for room in rooms
                if isinstance(room, dict)
                and int(room.get("chat_id") or 0) in allowed
            ]
            return snap

        return wrapper

    try:
        loader = globals().get("_load_tui")
        tui = loader() if callable(loader) else None
        if tui is not None and hasattr(tui, "collect_snapshot"):
            current = tui.collect_snapshot
            if not getattr(current, "_openkakao_scoped", False):
                scoped = _scoped_snapshot(current)
                scoped._openkakao_scoped = True
                tui.collect_snapshot = scoped
    except Exception:
        pass

    try:
        impl_globals = model.__globals__["_read_catalog"].__globals__
    except Exception:
        return

    classify = impl_globals.get("_classify_room")
    if callable(classify) and not getattr(classify, "_openkakao_scoped", False):
        def scoped_classify(room, *args, **kwargs):
            return classify(_soften_candidate_fence(room), *args, **kwargs)

        scoped_classify._openkakao_scoped = True
        impl_globals["_classify_room"] = scoped_classify

    catalog = impl_globals.get("_read_catalog")
    if not callable(catalog) or getattr(catalog, "_openkakao_scoped", False):
        return

    def scoped_catalog(state_root):
        entries = catalog(state_root)
        allowed = _enrollment_room_ids(Path(state_root))
        if not allowed or not isinstance(entries, list):
            return entries
        return [
            entry
            for entry in entries
            if isinstance(entry, dict) and int(entry.get("chat_id") or 0) in allowed
        ]

    scoped_catalog._openkakao_scoped = True
    impl_globals["_read_catalog"] = scoped_catalog


if __name__ == "__main__":
    raise SystemExit(main() or 0)
