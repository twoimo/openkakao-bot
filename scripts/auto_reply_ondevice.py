"""On-device MLX hardware detection, recommendation, and local probe support.

Apple Silicon uses MLX Core/Serve only. The recommendation keeps MLX serving
ids separate from local filesystem paths and never downloads model weights.
The explicit probe prefers the already served Qwen3.8 Flash-Next model, then
falls back to an already present local MLX model without touching KakaoTalk.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


MLX_GATEWAY_BASE_URL = "http://127.0.0.1:10100/v1"
FLASH_NEXT_MODEL_ID = "mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit"
QWEN38_27B_MODEL_ID = "mlx/ddalcu/Qwen3.8-27B-MLX-Serve-4bit"
QWEN38_27B_MIN_MEMORY_GB = 32.0
PROBE_TIMEOUT_SECONDS = 45.0
PROBE_HARD_TIMEOUT_SECONDS = 90.0
PROBE_PROMPT_MAX_CHARS = 160
PROBE_PREVIEW_MAX_CHARS = 240
LAST_PROBE_NAME = "ondevice-last-probe.json"


@dataclass
class HardwareSpec:
    chip: str
    cores: int
    memory_bytes: int
    memory_gb: float
    is_apple_silicon: bool


@dataclass
class EngineRecommendation:
    primary_engine: str
    available_engines: list[str]
    recommended_model: str
    recommended_quant: str
    reason: str
    engine_paths: dict[str, str] = field(default_factory=dict)
    fallback_models: list[str] = field(default_factory=list)
    worker_model_id: str = ""


def _is_apple_silicon_chip(chip: str) -> bool:
    return chip.strip().startswith("Apple ")


def detect_hardware() -> HardwareSpec:
    """Read CPU model, core count and memory from macOS sysctl."""
    chip = "Unknown CPU"
    cores = os.cpu_count() or 4
    mem_bytes = 16 * 1024 * 1024 * 1024

    if sys.platform == "darwin":
        try:
            chip = subprocess.check_output(
                ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except Exception:
            pass
        try:
            cores = int(
                subprocess.check_output(
                    ["/usr/sbin/sysctl", "-n", "hw.ncpu"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                ).strip()
            )
        except Exception:
            pass
        try:
            mem_bytes = int(
                subprocess.check_output(
                    ["/usr/sbin/sysctl", "-n", "hw.memsize"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                ).strip()
            )
        except Exception:
            pass

    return HardwareSpec(
        chip=chip,
        cores=cores,
        memory_bytes=mem_bytes,
        memory_gb=round(mem_bytes / (1024**3), 1),
        is_apple_silicon=_is_apple_silicon_chip(chip),
    )


def _find_executable(names: Sequence[str], extra_paths: Sequence[Path]) -> str:
    for name in names:
        found = shutil.which(name)
        if found and os.access(found, os.X_OK):
            return found
    for candidate in extra_paths:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return ""


def detect_mlx_gateway_models(
    *,
    base_url: str = MLX_GATEWAY_BASE_URL,
    timeout: float = 1.5,
) -> list[dict[str, str]]:
    """Return only MLX models advertised by the already running local gateway."""
    try:
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/models",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=max(0.1, min(timeout, 3.0))) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return []

    models: list[dict[str, str]] = []
    for item in payload.get("data") or []:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        owner = str(item.get("owned_by") or "").strip()
        if model_id and (model_id.startswith("mlx/") or owner == "mlx-serve"):
            models.append({"id": model_id, "owned_by": owner})
    return models


def detect_engine_paths(
    *,
    gateway_models: Sequence[dict[str, str]] | None = None,
) -> dict[str, str]:
    """Locate MLX Core/Serve executables and the existing local MLX gateway."""
    home = Path.home()
    paths: dict[str, str] = {}
    mlx_lm = _find_executable(["mlx_lm"], [home / ".local/bin/mlx_lm"])
    if mlx_lm:
        paths["mlx_lm"] = mlx_lm
    mlx_serve = _find_executable(["mlx-serve"], [home / ".local/bin/mlx-serve"])
    if mlx_serve:
        paths["mlx-serve"] = mlx_serve
    advertised = list(gateway_models) if gateway_models is not None else detect_mlx_gateway_models()
    if advertised:
        paths["mlx-gateway"] = MLX_GATEWAY_BASE_URL
    return paths


def detect_available_engines() -> list[str]:
    return list(detect_engine_paths().keys())


def detect_local_qwen_models(home: Path | None = None) -> dict[str, str]:
    """Find already present Qwen3.8 directories without touching model hubs."""
    root = (home or Path.home()) / "Models"
    if not root.is_dir():
        return {}
    found: dict[str, str] = {}
    try:
        children = list(root.iterdir())
    except OSError:
        return {}
    for child in children:
        if child.is_dir() and "qwen3.8" in child.name.casefold():
            found[child.name] = str(child)
    return found


def _model_id(models: Sequence[dict[str, str]], marker: str) -> str:
    wanted = marker.casefold()
    for item in models:
        model_id = str(item.get("id") or "").strip()
        if model_id.startswith("mlx/") and wanted in model_id.casefold():
            return model_id
    return ""


def _local_model_path(models: dict[str, str], marker: str) -> str:
    wanted = marker.casefold()
    for name, path in models.items():
        if wanted in f"{name} {path}".casefold() and Path(path).is_dir():
            return path
    return ""


def recommend_ondevice_setup(
    hw: HardwareSpec | None = None,
    engines: dict[str, str] | None = None,
    *,
    gateway_models: Sequence[dict[str, str]] | None = None,
    local_models: dict[str, str] | None = None,
) -> EngineRecommendation:
    """Recommend a Qwen3.8 model for the MLX Core/Serve path."""
    spec = hw or detect_hardware()
    advertised = (
        list(gateway_models)
        if gateway_models is not None
        else (detect_mlx_gateway_models() if engines is None else [])
    )
    raw_paths = (
        detect_engine_paths(gateway_models=advertised) if engines is None else dict(engines)
    )
    engine_paths = {
        key: value
        for key, value in raw_paths.items()
        if key in {"mlx_lm", "mlx-serve", "mlx-gateway"}
    }
    available = list(engine_paths.keys())
    local = (
        detect_local_qwen_models()
        if local_models is None and engines is None
        else dict(local_models or {})
    )

    served_flash = _model_id(advertised, "Qwen3.8-Flash-Next")
    served_27b = _model_id(advertised, "Qwen3.8-27B")
    local_flash = _local_model_path(local, "Qwen3.8-Flash-Next")
    local_27b = _local_model_path(local, "Qwen3.8-27B")
    can_host_27b = spec.memory_gb >= QWEN38_27B_MIN_MEMORY_GB

    if not spec.is_apple_silicon:
        return EngineRecommendation(
            primary_engine="mlx-unavailable",
            available_engines=available,
            recommended_model="",
            recommended_quant="",
            reason=f"{spec.chip} · MLX Core/Serve는 Apple Silicon에서만 사용합니다",
            engine_paths=engine_paths,
            fallback_models=[FLASH_NEXT_MODEL_ID],
            worker_model_id=FLASH_NEXT_MODEL_ID,
        )

    primary = "mlx-serve"
    model = ""
    quant = ""
    model_note = ""

    if "mlx-gateway" in engine_paths:
        primary = "mlx-serve"
        if can_host_27b and served_27b:
            model = served_27b
            quant = "4bit"
            model_note = "게이트웨이가 실제 제공 중인 Qwen3.8 27B"
        elif served_flash:
            model = served_flash
            quant = "mixed 4/8bit"
            model_note = "게이트웨이가 실제 제공 중인 Qwen3.8 Flash-Next"
    if not model and "mlx_lm" in engine_paths:
        if can_host_27b and local_27b:
            primary = "mlx_lm"
            model = local_27b
            quant = "local"
            model_note = "로컬 경로에서 확인한 Qwen3.8 27B"
        elif local_flash:
            primary = "mlx_lm"
            model = local_flash
            quant = "local"
            model_note = "로컬 경로에서 확인한 Qwen3.8 Flash-Next"
    if not model and "mlx-serve" in engine_paths:
        primary = "mlx-serve"
        model = QWEN38_27B_MODEL_ID if can_host_27b else FLASH_NEXT_MODEL_ID
        quant = "4bit" if can_host_27b else "mixed 4/8bit"
        model_note = "MLX Serve는 설치됐지만 해당 가중치 제공 여부는 아직 확인되지 않음"
    if not model and not available:
        primary = "mlx-serve"
        model = QWEN38_27B_MODEL_ID if can_host_27b else FLASH_NEXT_MODEL_ID
        quant = "4bit" if can_host_27b else "mixed 4/8bit"
        model_note = "설치된 MLX Core/Serve 엔진을 찾지 못함"

    reason = (
        f"{spec.chip} ({spec.memory_gb}GB 통합 메모리) · MLX Core/Serve · {model_note}"
    )
    fallbacks = [FLASH_NEXT_MODEL_ID]
    if served_27b and served_27b not in fallbacks:
        fallbacks.append(served_27b)

    return EngineRecommendation(
        primary_engine=primary,
        available_engines=available,
        recommended_model=model,
        recommended_quant=quant,
        reason=reason,
        engine_paths=engine_paths,
        fallback_models=fallbacks,
        worker_model_id=FLASH_NEXT_MODEL_ID,
    )


def verify_ondevice_setup(rec: EngineRecommendation | None = None) -> dict[str, Any]:
    """Read-only verification of the selected MLX runtime and local model."""
    engine = ""
    model = ""
    checks: list[dict[str, Any]] = []
    errors: list[str] = []

    def record(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})
        if not ok:
            errors.append(detail)

    try:
        recommendation = rec or recommend_ondevice_setup()
        engine = recommendation.primary_engine
        model = recommendation.recommended_model
        if engine == "mlx-serve":
            gateway = recommendation.engine_paths.get("mlx-gateway", "")
            serve_exe = recommendation.engine_paths.get("mlx-serve", "")
            runtime_ok = bool(gateway) or bool(
                serve_exe and Path(serve_exe).is_file() and os.access(serve_exe, os.X_OK)
            )
            record(
                "mlx_runtime",
                runtime_ok,
                "MLX Serve/gateway 확인" if runtime_ok else "MLX Serve/gateway를 찾지 못했습니다",
            )
            if gateway and model:
                models = detect_mlx_gateway_models(base_url=gateway)
                ids = {str(item.get("id") or "") for item in models}
                record(
                    "served_model",
                    model in ids,
                    f"게이트웨이 모델 확인: {model}" if model in ids else f"게이트웨이에 모델이 없습니다: {model}",
                )
            else:
                record("served_model", False, "게이트웨이에서 제공 중인 모델을 확인하지 못했습니다")
        elif engine == "mlx_lm":
            mlx_lm = recommendation.engine_paths.get("mlx_lm", "")
            executable_ok = bool(
                mlx_lm and Path(mlx_lm).is_file() and os.access(mlx_lm, os.X_OK)
            )
            record(
                "mlx_lm_executable",
                executable_ok,
                f"mlx_lm: {mlx_lm}" if executable_ok else "실행 가능한 mlx_lm을 찾지 못했습니다",
            )
            model_ok = bool(model and Path(model).is_dir())
            record(
                "model_directory",
                model_ok,
                f"로컬 모델 확인: {model}" if model_ok else f"로컬 모델 디렉터리가 없습니다: {model or '-'}",
            )
        else:
            record("mlx_runtime", False, "Apple Silicon MLX Core/Serve 구성이 아닙니다")
    except Exception as exc:
        errors.append(f"온디바이스 검증 실패: {type(exc).__name__}")

    return {
        "ok": bool(checks) and all(check["ok"] for check in checks) and not errors,
        "engine": engine,
        "model": model,
        "checks": checks,
        "errors": errors,
    }


def _default_state_root() -> Path:
    parent = Path.home() / "Library" / "Application Support" / "openkakao"
    modern = parent / "auto-reply"
    legacy = parent / "bujamentor"
    if (modern / "enrollment.json").is_file() or not (legacy / "enrollment.json").is_file():
        return modern
    return legacy


def _sanitize_probe_text(value: Any, limit: int) -> str:
    """Keep diagnostic prompt/preview printable, single-line, and shell inert."""
    allowed_punct = set(" .?!,:-_/()[]")
    cleaned = []
    for char in str(value or ""):
        if char.isspace():
            cleaned.append(" ")
        elif char.isalnum() or char in allowed_punct:
            cleaned.append(char)
    return " ".join("".join(cleaned).split())[: max(0, limit)]


def _probe_record(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": str(result.get("timestamp") or "")[:64],
        "engine": _sanitize_probe_text(result.get("engine"), 64),
        "model": _sanitize_probe_text(result.get("model"), 180),
        "latency_ms": max(0, int(result.get("latency_ms") or 0)),
        "ok": bool(result.get("ok")),
        "preview": _sanitize_probe_text(result.get("preview"), PROBE_PREVIEW_MAX_CHARS),
    }


def _persist_last_probe(result: dict[str, Any], state_root: Path | None = None) -> None:
    root = Path(state_root) if state_root is not None else _default_state_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        path = root / LAST_PROBE_NAME
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(_probe_record(result), ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except Exception:
        pass


def read_last_probe(state_root: Path | None = None) -> dict[str, Any] | None:
    root = Path(state_root) if state_root is not None else _default_state_root()
    try:
        raw = json.loads((root / LAST_PROBE_NAME).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        return _probe_record(raw)
    except Exception:
        return None


def _finish_probe(
    *,
    started: float,
    engine: str,
    model: str,
    ok: bool,
    preview: str = "",
    errors: Sequence[str] = (),
    state_root: Path | None = None,
) -> dict[str, Any]:
    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "engine": engine,
        "model": model,
        "latency_ms": max(0, int((time.monotonic() - started) * 1000)),
        "ok": bool(ok),
        "preview": _sanitize_probe_text(preview, PROBE_PREVIEW_MAX_CHARS),
        "errors": [str(error)[:160] for error in errors if str(error).strip()][:4],
    }
    _persist_last_probe(result, state_root)
    return result


def probe_ondevice_generation(
    rec: EngineRecommendation | None = None,
    *,
    state_root: Path | None = None,
    prompt: str = "LOCAL_OK 한 단어로 답하세요",
    timeout: float = PROBE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Run one bounded local generation against an already present MLX runtime."""
    started = time.monotonic()
    safe_prompt = _sanitize_probe_text(prompt, PROBE_PROMPT_MAX_CHARS) or "LOCAL_OK"
    effective_timeout = max(
        0.1,
        min(float(timeout), PROBE_TIMEOUT_SECONDS, PROBE_HARD_TIMEOUT_SECONDS),
    )

    try:
        recommendation = rec or recommend_ondevice_setup()
        gateway = recommendation.engine_paths.get("mlx-gateway", "")
        if gateway:
            model = FLASH_NEXT_MODEL_ID
            payload = json.dumps(
                {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "Return a very short diagnostic reply."},
                        {"role": "user", "content": safe_prompt},
                    ],
                    "max_tokens": 16,
                    "temperature": 0.0,
                },
                ensure_ascii=False,
            ).encode("utf-8")
            session_digest = hashlib.sha256(b"openkakao/ondevice/probe/v1").hexdigest()[:32]
            req = urllib.request.Request(
                f"{gateway.rstrip('/')}/chat/completions",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer not-needed",
                    "x-opencode-session": f"ocx_{session_digest}",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=effective_timeout) as resp:
                    body = json.loads(resp.read().decode("utf-8", "replace"))
                content = str(body["choices"][0]["message"]["content"] or "").strip()
                if not content:
                    return _finish_probe(
                        started=started,
                        engine="mlx-serve-gateway",
                        model=model,
                        ok=False,
                        errors=["empty_output"],
                        state_root=state_root,
                    )
                return _finish_probe(
                    started=started,
                    engine="mlx-serve-gateway",
                    model=model,
                    ok=True,
                    preview=content,
                    state_root=state_root,
                )
            except urllib.error.HTTPError as exc:
                return _finish_probe(
                    started=started,
                    engine="mlx-serve-gateway",
                    model=model,
                    ok=False,
                    errors=[f"http_{exc.code}"],
                    state_root=state_root,
                )
            except Exception as exc:
                return _finish_probe(
                    started=started,
                    engine="mlx-serve-gateway",
                    model=model,
                    ok=False,
                    errors=[f"gateway_{type(exc).__name__}"],
                    state_root=state_root,
                )

        mlx_lm = recommendation.engine_paths.get("mlx_lm", "")
        local_models = detect_local_qwen_models()
        local_flash = _local_model_path(local_models, "Qwen3.8-Flash-Next")
        local_27b = _local_model_path(local_models, "Qwen3.8-27B")
        local_model = local_flash
        if local_model == "" and local_27b != "":
            if detect_hardware().memory_gb >= QWEN38_27B_MIN_MEMORY_GB:
                local_model = local_27b
        if not mlx_lm or not Path(mlx_lm).is_file() or not local_model:
            return _finish_probe(
                started=started,
                engine="mlx_lm" if mlx_lm else "mlx-unavailable",
                model=local_model,
                ok=False,
                errors=["missing_engine_or_local_weights"],
                state_root=state_root,
            )

        env = os.environ.copy()
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
        command = [
            mlx_lm,
            "generate",
            "--model",
            local_model,
            "--prompt",
            safe_prompt,
            "--max-tokens",
            "16",
            "--temp",
            "0.0",
        ]
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        try:
            stdout, _stderr = proc.communicate(timeout=effective_timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return _finish_probe(
                started=started,
                engine="mlx_lm",
                model=local_model,
                ok=False,
                errors=["probe_timeout"],
                state_root=state_root,
            )
        if proc.returncode != 0:
            return _finish_probe(
                started=started,
                engine="mlx_lm",
                model=local_model,
                ok=False,
                errors=[f"mlx_lm_exit_{proc.returncode}"],
                state_root=state_root,
            )
        if not str(stdout or "").strip():
            return _finish_probe(
                started=started,
                engine="mlx_lm",
                model=local_model,
                ok=False,
                errors=["empty_output"],
                state_root=state_root,
            )
        return _finish_probe(
            started=started,
            engine="mlx_lm",
            model=local_model,
            ok=True,
            preview=stdout,
            state_root=state_root,
        )
    except Exception as exc:
        return _finish_probe(
            started=started,
            engine="mlx",
            model="",
            ok=False,
            errors=[f"probe_{type(exc).__name__}"],
            state_root=state_root,
        )


def _display_model(model: str) -> str:
    folded = model.casefold()
    if "qwen3.8-flash-next" in folded:
        return "Qwen3.8 Flash-Next"
    if "qwen3.8-27b" in folded:
        return "Qwen3.8 27B"
    return Path(model).name if model else "모델 미확인"


def ondevice_summary_dict(state_root: Path | None = None) -> dict[str, Any]:
    hw = detect_hardware()
    rec = recommend_ondevice_setup(hw)
    verification = verify_ondevice_setup(rec)
    last_probe = read_last_probe(state_root)
    verify_status = "설정 검증 통과" if verification.get("ok") else "설정 미확인"
    status_bits = [
        f"온디바이스 감지: {hw.chip} ({int(hw.memory_gb)}GB RAM)",
        "MLX Core/Serve",
        _display_model(rec.recommended_model),
        verify_status,
    ]
    detail_bits = [rec.reason, rec.recommended_model]
    if last_probe:
        probe_status = "실추론 통과" if last_probe.get("ok") else "실추론 실패"
        status_bits.append(f"{probe_status} ({_display_model(str(last_probe.get('model') or ''))})")
        detail_bits.append(
            f"최근 실추론: {last_probe.get('engine') or '-'} · {last_probe.get('model') or '-'} · "
            f"{last_probe.get('latency_ms') or 0}ms · {probe_status}"
        )
    return {
        "hardware": asdict(hw),
        "recommendation": asdict(rec),
        "verification": verification,
        "last_probe": last_probe,
        "status_label": " · ".join(status_bits),
        "status_detail": " · ".join(bit for bit in detail_bits if bit),
    }


def download_command(_rec: EngineRecommendation) -> str:
    """Downloads are intentionally unsupported by the on-device setup helper."""
    return ""


def generate_command(rec: EngineRecommendation, prompt: str) -> str:
    """Return a diagnostic description without interpolating text into a shell."""
    safe_prompt = _sanitize_probe_text(prompt, PROBE_PROMPT_MAX_CHARS)
    if rec.primary_engine == "mlx-serve" and rec.recommended_model:
        return f"MLX Serve POST {MLX_GATEWAY_BASE_URL}/chat/completions model={rec.recommended_model} prompt={safe_prompt}"
    if rec.primary_engine == "mlx_lm" and rec.recommended_model:
        return f"mlx_lm generate --model {rec.recommended_model} --prompt {safe_prompt} --max-tokens 16"
    return ""


if __name__ == "__main__":
    print(json.dumps(ondevice_summary_dict(), ensure_ascii=False, indent=2))
