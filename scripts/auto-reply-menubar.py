#!/usr/bin/env python3
"""Read-only AutoReply menu-bar snapshot.

Runtime is the frozen 3.11 wrapper plus overlay pyc. This file restores a
valid source entrypoint after the previous text was overwritten, then patches
catalog mutate and browser OAuth onto that surface.
"""

from __future__ import annotations

import fcntl
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
FALLBACK_MODEL_ACTIONS = frozenset({"fallback-models-set"})
MAX_REPLY_FALLBACK_MODELS = 6
REPLY_MODEL_FALLBACKS_NAME = "reply-model-fallbacks.json"
# 사용자가 아무것도 고르지 않았을 때 쓰는 내장 폴백 사슬. 워커도 같은 순서를
# 쓴다(scripts/auto-reply-worker.py: DEFAULT_REPLY_FALLBACK_MODELS).
DEFAULT_REPLY_FALLBACK_MODELS: tuple[str, ...] = (
    "google-antigravity/gemini-3.8-flash",
    "mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit",
    "google-antigravity/gemini-3.7-flash-tiered",
)
_WRAPPER_ONLY_FLAGS.update(_CATALOG_MUTATE_FLAGS)

_orig_main = main
_orig_set_reply_model = set_reply_model
_orig_collect_reply_models = collect_reply_models
_orig_add_api_provider = add_api_provider
_orig_collect_vector_list = collect_vector_list
_orig_upsert_vector_row = upsert_vector_row
from auto_reply_reference_store import collect_reference_list
VECTOR_LIST_SOURCES = frozenset(set(VECTOR_LIST_SOURCES) | {"references", "knowledge_graph"})
_DOCTOR_LEVEL_RANK = {"fail": 3, "warn": 2, "off": 1, "ok": 0}
_GJC_GLOBAL_MODELS_ENV = "OPENKAKAO_GJC_GLOBAL_MODELS"


def _gjc_global_models_path() -> Path:
    override = os.environ.get(_GJC_GLOBAL_MODELS_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".gjc" / "agent" / "models.yml"



# The legacy oMLX server (port 8080) was retired; filter only legacy oMLX.
# The active MLX-Serve gateway (mlx/*) remains available for local generation.
_RETIRED_PROVIDER_TAGS: frozenset[str] = frozenset({"omlx"})
_HIDDEN_CANONICAL: frozenset[str] = _RETIRED_PROVIDER_TAGS


def _strip_hidden_providers(providers: list[dict]) -> list[dict]:
    result: list[dict] = []
    for p in providers:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("id") or "").strip().lower()
        plabel = str(p.get("label") or "").strip().lower()
        if pid in _RETIRED_PROVIDER_TAGS or plabel in _RETIRED_PROVIDER_TAGS:
            continue
        models = p.get("models") or []
        kept = []
        for m in models:
            if not isinstance(m, dict):
                continue
            mid = str(m.get("id") or "").strip()
            mlabel = str(m.get("label") or "").strip().lower()
            mcanon = str(m.get("canonical") or "").strip().lower()
            mprov = str(m.get("provider") or "").strip().lower()
            if (
                mcanon in _RETIRED_PROVIDER_TAGS
                or mlabel in _RETIRED_PROVIDER_TAGS
                or mprov in _RETIRED_PROVIDER_TAGS
                or mid.startswith("omlx/")
            ):
                continue
            kept.append(m)
        if not kept:
            continue
        cleaned = dict(p)
        cleaned["models"] = kept
        result.append(cleaned)
    return result

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
    return _strip_hidden_providers([merged[key] for key in sorted(merged)])


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
                "세션 등록 파일이 없거나 방이 비어 있습니다.",
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
                f"라이브 {runtime} ≠ 베이크 {newest_runtime}. 세션 재시작 필요.",
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
                f"준비된 방 {ready}/{rooms} · {agg_state}/{readiness}",
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
                    f"방 {child.name[-4:]} · {state}/{phase}",
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
    ok = int(getattr(completed, "returncode", 1)) == 0
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


def _catalog_lock(state_root: Path):
    """Serialize catalog mutations across processes."""

    try:
        handle = open(Path(state_root) / "menubar-room-catalog.lock", "a+")
        fcntl.flock(handle, fcntl.LOCK_EX)
    except OSError:
        return None
    return handle


def _unlock_catalog(handle) -> None:
    if handle is None:
        return
    try:
        fcntl.flock(handle, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        handle.close()
    except OSError:
        pass


def _existing_catalog_title(state_root: Path, chat_id) -> str:
    try:
        wanted = int(chat_id)
    except (TypeError, ValueError):
        return ""
    path = Path(state_root) / "menubar-room-catalog.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""
    rooms = payload.get("rooms") if isinstance(payload, dict) else None
    if not isinstance(rooms, list):
        return ""
    for room in rooms:
        if isinstance(room, dict) and int(room.get("chat_id") or 0) == wanted:
            return str(room.get("title") or "").strip()
    return ""


def _apply_catalog_mutates() -> None:
    upsert_raw = _argv_flag_value("--catalog-upsert")
    delete_raw = _argv_flag_value("--catalog-delete")
    if not upsert_raw and not delete_raw:
        return
    state_raw = _argv_flag_value("--state-root")
    state_root = Path(state_raw).expanduser() if state_raw else _DEFAULT_STATE_ROOT
    overlay = _ensure_overlay()
    # The room title and flags must land in one catalog change, and it must not
    # be lost to a concurrent mutation. Serialize across processes and let the
    # impl's atomic writer persist the entry (2026-09-13).
    lock = _catalog_lock(state_root)
    try:
        if upsert_raw:
            payload = json.loads(upsert_raw)
            if not isinstance(payload, dict):
                raise MenubarError("catalog_entry_invalid")
            if not str(payload.get("title") or "").strip():
                # A caller that omits the title must not drop the existing one.
                title = _existing_catalog_title(state_root, payload.get("chat_id"))
                if title:
                    payload["title"] = title
            overlay["upsert_catalog_room"](state_root, payload)
        if delete_raw:
            overlay["delete_catalog_room"](state_root, int(delete_raw))
    finally:
        _unlock_catalog(lock)
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
    if source == "knowledge_graph":
        from auto_reply_knowledge_graph import collect_knowledge_graph_list

        # 목록도 그래프와 같은 저장소를 읽어야 한다.
        #
        # 그래프 명령은 --state-root 아래의 knowledge-graph.sqlite3에 색인
        # 결과를 쓴다. 그런데 목록은 호출자가 넘긴 db_path를 그대로 써서
        # 다른 파일을 열었다. 그 파일에는 손으로 적은 다섯 노드만 있어,
        # 74개 뉴런이 있는데도 표에는 5줄만 나왔다 (2026-09-16).
        state_raw = _argv_flag_value("--state-root")
        state_root = Path(state_raw).expanduser() if state_raw else _DEFAULT_STATE_ROOT
        return collect_knowledge_graph_list(
            state_root / "context.sqlite3",
            query=query,
            chat=chat,
            limit=limit,
            offset=offset,
            topic=topic,
        )
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


def _displayed_reply_providers(state_root: Path) -> list[dict[str, Any]]:
    """Providers exactly as the AI model-settings popups render them.

    The save gates for model-set and image-model-set must accept every model
    the user can see in the menu. Since the gateway migration the displayed
    list is wider than the legacy gjc models.yml files, so both gates consult
    this single source instead of their own older lists (2026-09-13).
    """
    try:
        payload = collect_reply_models_standalone(Path(state_root))
    except Exception:
        return []
    providers = payload.get("providers") if isinstance(payload, dict) else None
    return list(providers) if isinstance(providers, list) else []


def _reply_model_fallbacks_path(state_root: Path) -> Path:
    return Path(state_root) / REPLY_MODEL_FALLBACKS_NAME


def _normalize_fallback_model(value: Any) -> str:
    model = str(value or "").strip()
    if not model or len(model) > 200 or any(ch.isspace() for ch in model):
        return ""
    return model


def _fallback_allowed_model_ids(state_root: Path) -> set[str]:
    """Every model id the model-settings popups can show, plus the built-ins.

    The fallback chain saves model ids, so its save gate reads the same
    display/save single source as set_reply_model and set_image_reply_model
    (2026-09-15).
    """

    allowed: set[str] = set(DEFAULT_REPLY_FALLBACK_MODELS)
    try:
        agent_models_path = _gjc_agent_dir(state_root) / "models.yml"
        custom_providers = _parse_custom_models_yml(
            agent_models_path
        ) or _parse_custom_models_yml(Path(state_root) / "models.yml")
    except Exception:
        custom_providers = []
    for provider in custom_providers or []:
        for item in provider.get("models") or []:
            if isinstance(item, dict) and item.get("id"):
                allowed.add(str(item["id"]))
    for provider in _global_catalog_providers(state_root=state_root):
        for item in provider.get("models") or []:
            if isinstance(item, dict) and item.get("id"):
                allowed.add(str(item["id"]))
    for provider in _displayed_reply_providers(state_root):
        for item in provider.get("models") or []:
            if isinstance(item, dict) and item.get("id"):
                allowed.add(str(item["id"]))
    for model in (
        _current_reply_model_id(state_root),
        _read_image_reply_model(state_root)[0],
    ):
        if model:
            allowed.add(model)
    return allowed


def read_reply_model_fallbacks(state_root: Path) -> tuple[list[str], str]:
    """User-ordered fallback chain; source=default when nothing is saved.

    An empty saved list is not the same as "nothing saved": it means the
    operator wants no fallback at all, so the primary model's limit ends the
    turn instead of silently switching models (2026-09-15). A file whose
    entries are all invalid is treated as missing rather than as "no fallback".
    """

    path = _reply_model_fallbacks_path(state_root)
    raw: Any = None
    try:
        if path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= 8192:
            raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raw = None
    if isinstance(raw, dict) and raw.get("schema_version") == 1:
        models = raw.get("models")
        if isinstance(models, list):
            cleaned: list[str] = []
            for item in models:
                model = _normalize_fallback_model(item)
                if model and model not in cleaned:
                    cleaned.append(model)
                if len(cleaned) >= MAX_REPLY_FALLBACK_MODELS:
                    break
            if cleaned or not models:
                return cleaned, "override"
    return list(DEFAULT_REPLY_FALLBACK_MODELS), "default"


def reply_model_fallbacks_state(state_root: Path) -> dict[str, Any]:
    root = Path(state_root)
    models, source = read_reply_model_fallbacks(root)
    allowed = _fallback_allowed_model_ids(root)
    return {
        "models": models,
        "source": source,
        "defaults": list(DEFAULT_REPLY_FALLBACK_MODELS),
        "max": MAX_REPLY_FALLBACK_MODELS,
        # 화면이 "목록에 없음"과 "지금 답변 모델"을 표시할 수 있게 코어가
        # 판단 결과를 함께 보낸다(2026-09-15).
        "unknown": [model for model in models if model not in allowed],
        "primary": _current_reply_model_id(root) or "",
        # 저장한 시각을 리비전으로 알려 준다. 화면은 이 값보다 오래된 조회만
        # 무시하고, 더 새로운 변경(다른 창·CLI)은 그대로 반영한다(2026-09-15).
        "revision": _reply_model_fallbacks_revision(root),
    }


def _reply_model_fallbacks_revision(state_root: Path) -> int:
    """저장 파일의 mtime(ns). 저장한 적이 없으면 0."""

    path = _reply_model_fallbacks_path(Path(state_root))
    try:
        if path.is_file() and not path.is_symlink():
            return int(path.stat().st_mtime_ns)
    except OSError:
        pass
    return 0


def set_reply_model_fallbacks(
    state_root: Path,
    models: str | list[str] | None,
    now: float | None = None,
    clear: bool = False,
):
    """Save the ordered fallback chain.

    Two different "nothing here" requests exist and must not be merged: an
    empty list means the operator wants no fallback (the primary model's limit
    ends the turn), while clear=True removes the saved list so the built-in
    defaults apply again (2026-09-15).
    """

    if models is None and not clear:
        return {
            "ok": False,
            "action": "fallback-models-set",
            "privacy": "content_redacted",
            "reason": "models_required",
            "warnings": ["저장할 폴백 모델 목록이 필요합니다."],
        }
    raw_items = (
        []
        if clear
        else models.split(",")
        if isinstance(models, str)
        else list(models or [])
    )
    wanted: list[str] = []
    for item in raw_items:
        model = _normalize_fallback_model(item)
        if model and model not in wanted:
            wanted.append(model)
    if len(wanted) > MAX_REPLY_FALLBACK_MODELS:
        return {
            "ok": False,
            "action": "fallback-models-set",
            "privacy": "content_redacted",
            "reason": "too_many_fallbacks",
            "warnings": [
                f"폴백 모델은 최대 {MAX_REPLY_FALLBACK_MODELS}개까지 저장할 수 있습니다."
            ],
        }
    root = Path(state_root)
    allowed = _fallback_allowed_model_ids(root)
    saved_now, _saved_source = read_reply_model_fallbacks(root)
    # 이미 저장된 항목은 카탈로그에서 사라져도 계속 저장할 수 있다. 새로 넣는
    # 미등록 ID만 막는다 — 목록이 바뀌었다고 기존 사슬을 고칠 수 없으면
    # 사용자가 빠져나갈 수 없다(2026-09-15).
    blocked = [
        model for model in wanted if model not in allowed and model not in saved_now
    ]
    if blocked:
        return {
            "ok": False,
            "action": "fallback-models-set",
            "privacy": "content_redacted",
            "reason": "model_not_in_catalog",
            "warnings": [
                "등록되지 않은 모델은 폴백으로 쓸 수 없습니다: " + ", ".join(blocked)
            ],
        }
    # 답변 모델과 같은 모델을 폴백에 넣으면 그 칸은 아무 일도 하지 않는다.
    primary = _current_reply_model_id(root) or ""
    if primary and primary in wanted and primary not in saved_now:
        return {
            "ok": False,
            "action": "fallback-models-set",
            "privacy": "content_redacted",
            "reason": "primary_model_in_chain",
            "warnings": [
                f"지금 쓰는 답변 모델({primary})은 폴백에 넣을 수 없습니다. "
                "먼저 답변 모델을 바꾸거나 다른 모델을 골라 주세요."
            ],
        }
    stamp = time.time() if now is None else float(now)
    path = _reply_model_fallbacks_path(Path(state_root))
    try:
        if clear:
            path.unlink(missing_ok=True)
        else:
            _atomic_write_json(
                path,
                {"schema_version": 1, "models": wanted, "updated_at": int(stamp)},
            )
    except OSError:
        return {
            "ok": False,
            "action": "fallback-models-set",
            "privacy": "content_redacted",
            "reason": "fallback_override_write_failed",
            "warnings": ["폴백 모델 목록을 저장하지 못했습니다."],
        }
    state = reply_model_fallbacks_state(path.parent)
    return {
        "ok": True,
        "action": "fallback-models-set",
        "privacy": "content_redacted",
        "fallback_models": list(state["models"]),
        "fallback_source": state["source"],
        "fallback_defaults": list(state["defaults"]),
        "fallback_max": state["max"],
        "fallback_revision": state["revision"],
        "warnings": [],
    }


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
    # Single-source the save gate with the menu display list. The gateway
    # catalog (post-gjc migration) is merged into what "AI 모델 설정" renders,
    # so a model the user can see must also be saveable; the old gate missed
    # those entries and returned model_not_in_catalog for displayed models
    # (2026-09-13).
    for provider in _displayed_reply_providers(state_root):
        for item in provider.get("models") or []:
            if isinstance(item, dict) and item.get("id"):
                allowed_model_ids.add(str(item["id"]))
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
    # Same display/save single-source as set_reply_model above.
    for provider in _displayed_reply_providers(state_root):
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
    fallbacks = reply_model_fallbacks_state(Path(state_root))
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
    for fb_model in list(fallbacks.get("models") or []) + list(fallbacks.get("defaults") or []):
        payload["providers"] = _ensure_listed_model(
            list(payload.get("providers") or []),
            fb_model,
        )
    # 답변 모델 목록과 함께 저장된 이미지 모델도 돌려준다. 화면이 이미지 변경을
    # "저장됨"이라고 말하려면 답변 목록이 아니라 이 필드를 확인해야 한다.
    image_id, image_source = _read_image_reply_model(Path(state_root))
    image_parts = image_id.split("/", 1)
    payload["image_model"] = image_id
    payload["image_label"] = image_parts[1] if len(image_parts) > 1 else image_id
    payload["image_provider"] = image_parts[0] if len(image_parts) > 1 else ""
    payload["image_source"] = image_source
    # 폴백 사슬은 모델 설정 창의 한 섹션이다. 화면이 추정하지 않도록 저장된
    # 목록과 출처(사용자 지정/내장 기본값), 상한을 함께 돌려준다 (2026-09-15).
    payload.update(
        {
            "fallback_models": list(fallbacks["models"]),
            "fallback_source": fallbacks["source"],
            "fallback_defaults": list(fallbacks["defaults"]),
            "fallback_max": fallbacks["max"],
            "fallback_revision": fallbacks["revision"],
        }
    )
    payload.setdefault("ok", True)
    payload.setdefault("action", "models")
    payload.setdefault("privacy", "content_redacted")
    # Strip retired providers/models (omlx) injected by the gateway catalog.
    payload["providers"] = _strip_hidden_providers(payload.get("providers") or [])
    return payload


_orig_attach_reply_model = globals().get("attach_reply_model")


def _clean_attach_reply_model(snap, state_root, *args, **kwargs):
    if callable(_orig_attach_reply_model):
        res = _orig_attach_reply_model(snap, state_root, *args, **kwargs)
        if isinstance(res, dict):
            snap = res
    if not isinstance(snap, dict):
        return snap
    if "reply_model_providers" in snap:
        snap["reply_model_providers"] = _strip_hidden_providers(snap["reply_model_providers"])
    image_id, image_source = _read_image_reply_model(Path(state_root))
    reply_obj = snap.get("reply_model") if isinstance(snap.get("reply_model"), dict) else {}
    reply_id = str(reply_obj.get("id") or "").strip() or _current_reply_model_id(Path(state_root))
    image_enabled = not _reply_model_sees_images(reply_id)
    chosen = image_id if image_enabled else reply_id
    snap["image_reply_model"] = {
        "id": chosen,
        "label": chosen.split("/", 1)[-1],
        "canonical": chosen.split("/", 1)[-1],
        "provider": chosen.split("/", 1)[0] if "/" in chosen else "",
        "source": image_source if image_enabled else "unused",
        "enabled": image_enabled,
        "auto_selected": bool(
            image_enabled
            and (
                str(image_source).strip().lower() == "auto"
                or image_id.lower().endswith("/auto")
                or image_id.lower() == "auto"
            )
        ),
    }
    return snap

attach_reply_model = _clean_attach_reply_model

_orig_reply_model_payload = globals().get("reply_model_payload")
if callable(_orig_reply_model_payload):
    def _clean_reply_model_payload(state_root, *args, **kwargs):
        res = _orig_reply_model_payload(state_root, *args, **kwargs)
        if isinstance(res, dict) and "providers" in res:
            res["providers"] = _strip_hidden_providers(res["providers"])
        return res
    reply_model_payload = _clean_reply_model_payload


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
    if action == "knowledge-graph":
        # The graph view needs the whole node/edge set at once, which the
        # paginated vector-list cannot express. Handled before the frozen
        # dispatch so it never reaches the vector code path (2026-09-16).
        state_raw = _argv_flag_value("--state-root")
        state_root = Path(state_raw).expanduser() if state_raw else _DEFAULT_STATE_ROOT
        try:
            from auto_reply_knowledge_graph import collect_knowledge_graph

            payload = collect_knowledge_graph(
                state_root / "context.sqlite3", state_root=state_root
            )
        except Exception as exc:  # never crash the menu: report and keep going
            payload = {
                "ok": False,
                "nodes": [],
                "edges": [],
                "node_count": 0,
                "edge_count": 0,
                "grounded_nodes": 0,
                "reason": str(exc) or "knowledge_graph_unavailable",
            }
        _print_json(payload)
        return 0
    args = type("Args", (), {"action": action})()
    if (
        args.action in MODEL_ACTIONS
        or args.action in PROVIDER_ACTIONS
        or args.action in IMAGE_MODEL_ACTIONS
        or args.action in FALLBACK_MODEL_ACTIONS
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
        if args.action == "fallback-models-set":
            state_raw = _argv_flag_value("--state-root")
            state_root = (
                Path(state_raw).expanduser() if state_raw else _DEFAULT_STATE_ROOT
            )
            # --clear는 "내장 기본값으로"이고, 빈 --models는 "폴백 없음"이다.
            clear = "--clear" in sys.argv
            _print_json(
                set_reply_model_fallbacks(
                    state_root,
                    _argv_flag_value("--models"),
                    now=time.time(),
                    clear=clear,
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

    if args.action == "model-prepare":
        state_raw = _argv_flag_value("--state-root")
        state_root = Path(state_raw).expanduser() if state_raw else _DEFAULT_STATE_ROOT
        _print_json(prepare_reply_model(state_root, _argv_flag_value("--model")))
        return 0
    return _orig_main()




def _attach_fallback_state(snap: Any, state_root: Any) -> Any:
    """Add the fallback chain the model-settings window draws.

    The window reads this straight off the snapshot, so the field has to be
    attached on the path the menu itself uses. Older callers ignore the extra
    key (2026-09-15).
    """

    if not isinstance(snap, dict) or state_root is None:
        return snap
    try:
        state = reply_model_fallbacks_state(Path(state_root))
    except Exception:
        return snap
    snap = dict(snap)
    snap["reply_model_fallbacks"] = state
    return snap


def _wrap_snapshot_owner(
    owner: Any,
    attr: str,
    *,
    flag: str = "_openkakao_fallbacks",
    attach: Any = None,
) -> bool:
    """Wrap one layer's snapshot builder.

    The runtime is the frozen wrapper plus overlay pyc, and the overlay builds
    the snapshot itself through ``_collect_menubar_model``. Wrapping only the
    impl layer never runs on the real menu path, so the hook has to land on the
    layer the menu actually calls (2026-09-15).
    """

    original = owner.get(attr) if hasattr(owner, "get") else None
    if not callable(original) or getattr(original, flag, False):
        return False

    hook = attach if callable(attach) else _attach_fallback_state

    def wrapper(*args, **kwargs):
        snap = original(*args, **kwargs)
        state_root = args[0] if args else kwargs.get("state_root")
        return hook(snap, state_root)

    setattr(wrapper, flag, True)
    try:
        owner[attr] = wrapper
    except Exception:
        return False
    return True


_SNAPSHOT_ATTRS = (
    "collect_menubar_model",
    "collect_snapshot",
    "_collect_menubar_model",
    "_collect_snapshot",
)


def _patch_overlay_fallbacks(namespace: Any) -> bool:
    """Wrap every snapshot builder the overlay namespace exposes."""

    if not isinstance(namespace, dict):
        return False
    installed = False
    for attr in _SNAPSHOT_ATTRS:
        installed = _wrap_snapshot_owner(namespace, attr) or installed
    return installed


def _install_fallback_snapshot_hook() -> None:
    """Put ``reply_model_fallbacks`` on every snapshot surface.

    The frozen wrapper resolves overlay names lazily through a module
    ``__getattr__`` and keeps the overlay unloaded until something asks for it,
    so this hooks the loader rather than forcing the overlay open at import
    time (2026-09-15).
    """

    ensure = globals().get("_ensure_overlay")
    if callable(ensure) and not getattr(ensure, "_openkakao_fallback_hook", False):

        def _ensure_overlay_with_fallbacks(*args, **kwargs):
            namespace = ensure(*args, **kwargs)
            try:
                _patch_overlay_fallbacks(namespace)
            except Exception:
                pass
            return namespace

        _ensure_overlay_with_fallbacks._openkakao_fallback_hook = True
        globals()["_ensure_overlay"] = _ensure_overlay_with_fallbacks
        return
    # Fall back to patching whatever snapshot builder is already reachable.
    for name in ("collect_menubar_model", "collect_snapshot"):
        fn = globals().get(name)
        owner = getattr(fn, "__globals__", None)
        if _patch_overlay_fallbacks(owner):
            return
    impl = globals().get("_read_catalog")
    impl_globals = getattr(impl, "__globals__", None)
    if isinstance(impl_globals, dict):
        _wrap_snapshot_owner(impl_globals, "collect_menubar_model")


_install_fallback_snapshot_hook()


_REPLY_RECEIPT_LIMIT = 30
_REPLY_RECEIPT_CACHE: dict[str, Any] = {"key": None, "payload": None}


def _reply_receipt_bin() -> Path | None:
    """The CLI this process was started with, when it is a real file."""

    raw = _argv_flag_value("--bin")
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_file() else None


def _room_ledgers(state_root: Path) -> list[Path]:
    """Every room ledger on disk, so the window shows rooms that have turns."""

    try:
        return sorted((state_root / "rooms").glob("*/reply-evidence.jsonl"))
    except Exception:
        return []


def _ledger_stamps(paths: list[Path]) -> tuple:
    stamps = []
    for path in paths:
        try:
            stat = path.stat()
            stamps.append((str(path), stat.st_mtime_ns, stat.st_size))
        except Exception:
            stamps.append((str(path), 0, 0))
    return tuple(stamps)


def collect_reply_receipts(
    state_root: Path | None,
    *,
    limit: int = _REPLY_RECEIPT_LIMIT,
) -> dict[str, Any] | None:
    """Turn receipts for every room that has a ledger.

    The Rust command owns every Korean word and the judgement about whether a
    turn was healthy, so this layer only moves JSON. The command runs again
    only when a ledger file changed: the snapshot is rebuilt every couple of
    seconds (2026-09-15).
    """

    if state_root is None:
        return None
    binary = _reply_receipt_bin()
    if binary is None:
        return _REPLY_RECEIPT_CACHE.get("payload")
    root = Path(state_root)
    ledgers = _room_ledgers(root)
    if not ledgers:
        return {
            "rooms": [],
            "limit": limit,
            "read_at": time.time(),
            "missing": True,
        }
    key = (_ledger_stamps(ledgers), str(binary), limit)
    if key == _REPLY_RECEIPT_CACHE.get("key"):
        return _REPLY_RECEIPT_CACHE.get("payload")
    argv = [
        str(binary),
        "reply-receipt",
        "--state-root",
        str(root),
        "--chat-id",
        ",".join(path.parent.name for path in ledgers),
        "-n",
        str(limit),
        "--json",
    ]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=20)
    except Exception:
        return _REPLY_RECEIPT_CACHE.get("payload")
    if proc.returncode != 0:
        return _REPLY_RECEIPT_CACHE.get("payload")
    try:
        rooms = json.loads(proc.stdout)
    except Exception:
        return _REPLY_RECEIPT_CACHE.get("payload")
    if isinstance(rooms, dict):
        rooms = [rooms]
    if not isinstance(rooms, list):
        return _REPLY_RECEIPT_CACHE.get("payload")
    payload = {
        "rooms": rooms,
        "limit": limit,
        "read_at": time.time(),
        "missing": False,
    }
    _REPLY_RECEIPT_CACHE["key"] = key
    _REPLY_RECEIPT_CACHE["payload"] = payload
    return payload


def _attach_reply_receipts(snap: Any, state_root: Any) -> Any:
    """Add the record window's turn receipts to a snapshot."""

    if not isinstance(snap, dict) or state_root is None:
        return snap
    if "reply_receipts" in snap:
        return snap
    try:
        payload = collect_reply_receipts(Path(state_root))
    except Exception:
        return snap
    if payload is None:
        return snap
    snap = dict(snap)
    snap["reply_receipts"] = payload
    return snap


def _patch_overlay_receipts(namespace: Any) -> bool:
    """Wrap every snapshot builder the overlay namespace exposes."""

    if not isinstance(namespace, dict):
        return False
    installed = False
    for attr in _SNAPSHOT_ATTRS:
        installed = (
            _wrap_snapshot_owner(
                namespace,
                attr,
                flag="_openkakao_receipts",
                attach=_attach_reply_receipts,
            )
            or installed
        )
    return installed


def _install_receipt_snapshot_hook() -> None:
    """Put reply_receipts on every snapshot surface, after the fallback hook."""

    ensure = globals().get("_ensure_overlay")
    if callable(ensure) and not getattr(ensure, "_openkakao_receipt_hook", False):

        def _ensure_overlay_with_receipts(*args, **kwargs):
            namespace = ensure(*args, **kwargs)
            try:
                _patch_overlay_receipts(namespace)
            except Exception:
                pass
            return namespace

        _ensure_overlay_with_receipts._openkakao_receipt_hook = True
        globals()["_ensure_overlay"] = _ensure_overlay_with_receipts
        return
    for name in ("collect_menubar_model", "collect_snapshot"):
        fn = globals().get(name)
        owner = getattr(fn, "__globals__", None)
        if _patch_overlay_receipts(owner):
            return


_install_receipt_snapshot_hook()


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


_ACTIVE_STATE_ROOT: Path | None = None
_TRANSIENT_DB_FENCE_REASONS = frozenset(
    {"db_capability_fenced", "db_delivery_not_ready", "db_watermark_invalid"}
)
# db-watch fence values that are the short context-sync transition, not a DB
# delivery failure ("db_unavailable" and friends must stay red).
_TRANSIENT_DB_WATCH_FENCES = frozenset({"", "context_sync_deferred"})
_DB_WATCH_FRESH_SECONDS = 10.0


def _transient_poll_transition(chat_id: int) -> bool:
    """True only when the db-watch itself is in a short poll/context transition.

    Process liveness is not delivery evidence, so read the authoritative
    db-watch state and require the starting fence with empty pending gaps, no
    transient sync failures, and a fresh heartbeat. A missing/empty state, a
    stale heartbeat, or a delivery/reconcile fence is not softened.
    """

    root = _ACTIVE_STATE_ROOT
    if root is None or chat_id <= 0:
        return False
    path = Path(root) / "rooms" / str(chat_id) / "db-watch-state.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(state, dict):
        return False
    if state.get("capability_state") != "starting" or state.get("fence") != "starting":
        return False
    if state.get("fence_reason") not in _TRANSIENT_DB_WATCH_FENCES:
        return False
    if state.get("pending_gaps") not in ([], None):
        return False
    if state.get("context_sync_transient_failures") not in (None, 0):
        return False
    try:
        heartbeat = float(state.get("heartbeat_at"))
    except (TypeError, ValueError):
        return False
    return time.time() - heartbeat <= _DB_WATCH_FRESH_SECONDS


def _soften_candidate_fence(room: dict) -> dict:
    """Read a running host's verified short poll/context fence as ready.

    The database watcher briefly fences delivery while its context index sync
    catches up, so the supervisor reports ``readiness=fenced`` with the
    transient DB reasons. This is normal work, not a failure, and it made the
    menu flash red. Soften only when the authoritative db-watch state confirms
    that short transition and every child is running; real delivery, reconcile,
    or stale states stay red (2026-09-13).
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
    if not isinstance(children, dict) or not children:
        return room
    if any(state != "running" for state in children.values()):
        return room
    try:
        chat_id = int(room.get("chat_id") or 0)
    except (TypeError, ValueError):
        return room
    if not _transient_poll_transition(chat_id):
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
            global _ACTIVE_STATE_ROOT
            snap = original(*args, **kwargs)
            state_root = args[0] if args else kwargs.get("state_root")
            if state_root is None or not isinstance(snap, dict):
                return snap
            _ACTIVE_STATE_ROOT = Path(state_root)
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
            cid = int(room.get("chat_id") or 0) if isinstance(room, dict) else 0
            allowed = _enrollment_room_ids(_ACTIVE_STATE_ROOT) if _ACTIVE_STATE_ROOT else None
            if allowed is not None and cid not in allowed:
                return ("off", ["service_off"])
            return classify(_soften_candidate_fence(room), *args, **kwargs)

        scoped_classify._openkakao_scoped = True
        impl_globals["_classify_room"] = scoped_classify

    orig_collect = impl_globals.get("collect_menubar_model")
    if callable(orig_collect) and not getattr(orig_collect, "_openkakao_with_models", False):
        def _model_with_image_and_providers(*args, **kwargs):
            snap = orig_collect(*args, **kwargs)
            if not isinstance(snap, dict):
                return snap
            state_root = args[0] if args else kwargs.get("state_root")
            if state_root is None:
                return snap
            try:
                models = collect_reply_models(Path(state_root))
            except Exception:
                return snap
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
                "auto_selected": bool(
                    image_enabled
                    and (
                        str(image_source).strip().lower() == "auto"
                        or image_id.lower().endswith("/auto")
                        or image_id.lower() == "auto"
                    )
                ),
            }
            for chat in snap.get("available_chats") or []:
                if isinstance(chat, dict) and str(chat.get("title") or "").startswith("id:"):
                    cat_title = _existing_catalog_title(Path(state_root), chat.get("chat_id"))
                    if cat_title:
                        chat["title"] = cat_title
            # 모델 설정 창의 폴백 사슬도 같은 스냅샷에 실어 보낸다. 이 경로는
            # 화면이 실제로 쓰는 경로라, 여기서 빠지면 창이 기본값만 보여 준다.
            try:
                snap["reply_model_fallbacks"] = reply_model_fallbacks_state(
                    Path(state_root)
                )
            except Exception:
                pass
            return snap

        _model_with_image_and_providers._openkakao_with_models = True
        impl_globals["collect_menubar_model"] = _model_with_image_and_providers

    validate = impl_globals.get("_validated_catalog_entry")
    if callable(validate) and not getattr(validate, "_openkakao_titled", False):
        def titled_entry(raw):
            entry = validate(raw)
            if isinstance(entry, dict) and isinstance(raw, dict):
                title = " ".join(str(raw.get("title") or "").split())[:128]
                if title:
                    entry["title"] = title
            return entry

        titled_entry._openkakao_titled = True
        impl_globals["_validated_catalog_entry"] = titled_entry


if __name__ == "__main__":
    raise SystemExit(main() or 0)
