"""On-device LLM hardware detection and engine/model recommendation.

Detects the Apple Silicon chip, unified memory size and the local inference
engines (MLX, llama.cpp, Ollama) that are actually installed, then recommends a
Gemma-family open-weights model for local KakaoTalk auto-replies.

Two things this module is careful about (both raised in review):

- A model id is only meaningful for the engine that will load it. MLX wants an
  mlx-community repo, llama.cpp wants a GGUF repo, and Ollama wants a local
  tag. Handing an MLX repo id to Ollama cannot work, so the engine and its model
  are chosen together.
- The worker routes some models through an internal provider id (for example
  mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit). Those ids are not
  Hugging Face repos. Both are reported, separately and labelled (2026-09-17).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

# Gemma is the preferred family: open weights, strong Korean coverage, and a
# first-party MLX conversion for every size. Gemini Nano informed the on-device
# design idea but is not distributable, so it cannot be used here.
GEMMA_TIERS: tuple[tuple[float, str, str, str], ...] = (
    (
        96.0,
        "mlx-community/gemma-4-31B-it-qat-4bit",
        "QAT 4bit",
        "31B급 추론 품질을 4bit 대역폭으로 유지하는 QAT 양자화",
    ),
    (
        48.0,
        "mlx-community/gemma-4-31b-it-8bit",
        "8bit",
        "31B급 추론 품질을 8bit 정밀도로 쓰는 고품질 구성",
    ),
    (
        24.0,
        "mlx-community/gemma-4-e4b-it-4bit",
        "4bit",
        "Gemma 4 E4B 경량 구성. 중형 메모리에서 한국어 대화용",
    ),
    (
        8.0,
        "mlx-community/gemma-4-e2b-it-4bit",
        "4bit",
        "Gemma 4 E2B 초경량 구성. 작은 기기에서도 상주 가능",
    ),
    (
        0.0,
        "mlx-community/gemma-3-1b-it-4bit",
        "4bit",
        "1B급 최후 보조. Gemma 4 경량조차 부담일 때",
    ),
)

# llama.cpp and Ollama cannot load an MLX repo, so they get their own ids.
LLAMACPP_FALLBACK = "ggml-org/gemma-3-12b-it-GGUF"
OLLAMA_FALLBACK = "gemma3:12b"


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
    # The internal provider id the worker can route to, when one exists. Kept
    # apart from recommended_model because it is not a Hugging Face repo.
    worker_model_id: str = ""


def _is_apple_silicon_chip(chip: str) -> bool:
    """True only for an Apple-branded chip.

    A substring test for "M" also matches "AMD Ryzen" and "MediaTek", which
    would then be handed MLX models they cannot run (2026-09-17).
    """
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
            cores_out = subprocess.check_output(
                ["/usr/sbin/sysctl", "-n", "hw.ncpu"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            cores = int(cores_out)
        except Exception:
            pass

        try:
            mem_out = subprocess.check_output(
                ["/usr/sbin/sysctl", "-n", "hw.memsize"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            mem_bytes = int(mem_out)
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
    """Return the first usable executable path, or an empty string.

    A path that exists but cannot be executed is not an installed engine, and
    reporting it as one sends the operator to a command that fails
    (2026-09-17).
    """
    for name in names:
        found = shutil.which(name)
        if found and os.access(found, os.X_OK):
            return found
    for candidate in extra_paths:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return ""


def detect_engine_paths() -> dict[str, str]:
    """Locate each installed engine and remember where it was found."""
    home = Path.home()
    paths: dict[str, str] = {}
    mlx = _find_executable(["mlx_lm"], [home / ".local/bin/mlx_lm"])
    if mlx:
        paths["mlx"] = mlx
    llama = _find_executable(
        ["llama-cli", "llama-server"],
        [Path("/opt/homebrew/bin/llama-cli"), Path("/usr/local/bin/llama-cli")],
    )
    if llama:
        paths["llama.cpp"] = llama
    ollama = _find_executable(
        ["ollama"], [Path("/opt/homebrew/bin/ollama"), Path("/usr/local/bin/ollama")]
    )
    if ollama:
        paths["ollama"] = ollama
    return paths


def detect_available_engines() -> list[str]:
    """Find installed local LLM runtimes."""
    return list(detect_engine_paths().keys())


def _gemma_tier(memory_gb: float) -> tuple[str, str, str]:
    for threshold, model, quant, note in GEMMA_TIERS:
        if memory_gb >= threshold:
            return model, quant, note
    return GEMMA_TIERS[-1][1], GEMMA_TIERS[-1][2], GEMMA_TIERS[-1][3]


def recommend_ondevice_setup(
    hw: HardwareSpec | None = None,
    engines: dict[str, str] | None = None,
) -> EngineRecommendation:
    """Recommend the engine and the Gemma model that engine can load."""
    spec = hw or detect_hardware()
    engine_paths = engines if engines is not None else detect_engine_paths()
    available = list(engine_paths.keys())

    # MLX is preferred on Apple Silicon because the weights stay in unified
    # memory. Elsewhere the engine that is actually installed decides.
    if spec.is_apple_silicon and "mlx" in available:
        primary = "mlx"
    elif "ollama" in available:
        primary = "ollama"
    elif "llama.cpp" in available:
        primary = "llama.cpp"
    elif spec.is_apple_silicon:
        primary = "mlx"
    else:
        primary = "llama.cpp"

    gemma_model, gemma_quant, gemma_note = _gemma_tier(spec.memory_gb)

    if primary == "mlx":
        model, quant = gemma_model, gemma_quant
    elif primary == "llama.cpp":
        model, quant = LLAMACPP_FALLBACK, "GGUF Q4_K_M"
    else:
        model, quant = OLLAMA_FALLBACK, "ollama 기본 양자화"

    reason = (
        f"{spec.chip} ({spec.memory_gb}GB 통합 메모리) · {primary} 엔진 기준 "
        f"Gemma 계열 권장 — {gemma_note}"
    )
    if not available:
        reason += " · 설치된 로컬 엔진을 찾지 못해 기본 엔진으로 안내합니다"

    # The room already routes a fast local model through an internal id. It is
    # listed as a fallback, not as the primary recommendation, because it is a
    # serving endpoint rather than a downloadable weight set.
    worker_fallbacks = [
        "mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit",
    ]

    return EngineRecommendation(
        primary_engine=primary,
        available_engines=available,
        recommended_model=model,
        recommended_quant=quant,
        reason=reason,
        engine_paths=engine_paths,
        fallback_models=worker_fallbacks,
        worker_model_id=worker_fallbacks[0],
    )


def ondevice_summary_dict() -> dict[str, Any]:
    hw = detect_hardware()
    rec = recommend_ondevice_setup(hw)
    verification = verify_ondevice_setup(rec)
    if verification.get("ok"):
        status = "검증 통과"
    elif verification.get("checks") or verification.get("errors"):
        status = "가중치 미확인"
    else:
        status = "검증 정보 없음"
    model_name = Path(rec.recommended_model).name
    return {
        "hardware": asdict(hw),
        "recommendation": asdict(rec),
        "verification": verification,
        "status_label": (
            f"온디바이스 감지: {hw.chip} ({int(hw.memory_gb)}GB RAM)"
            f" · {rec.primary_engine} · {model_name} · {status}"
        ),
        "status_detail": f"{rec.reason} · {rec.recommended_model}",
    }


def verify_ondevice_setup(rec: EngineRecommendation | None = None) -> dict[str, Any]:
    """Verify that the recommended local runtime and weights are already usable.

    Verification is deliberately read-only: it never downloads weights or runs
    inference. Any failure is reported in the result envelope instead of being
    allowed to terminate the caller.
    """
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
        engine_path = recommendation.engine_paths.get(engine, "")
        executable_ok = bool(
            engine_path
            and Path(engine_path).is_file()
            and os.access(engine_path, os.X_OK)
        )
        record(
            "engine_executable",
            executable_ok,
            (
                f"{engine}: {engine_path}"
                if executable_ok
                else f"{engine}: 실행 가능한 엔진 경로를 찾지 못했습니다"
            ),
        )

        if engine in {"mlx", "llama.cpp"}:
            model_dir = Path.home() / "Models" / Path(model).name
            model_ok = model_dir.is_dir()
            record(
                "model_directory",
                model_ok,
                (
                    f"모델 디렉터리 확인: {model_dir}"
                    if model_ok
                    else f"모델 디렉터리가 없습니다: {model_dir}"
                ),
            )
        elif engine == "ollama":
            if executable_ok:
                try:
                    proc = subprocess.run(
                        [engine_path, "list"],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=5,
                    )
                    listed_models = {
                        line.split()[0]
                        for line in proc.stdout.splitlines()
                        if line.split()
                    }
                    listed = proc.returncode == 0 and model in listed_models
                    detail = (
                        f"ollama 로컬 모델 확인: {model}"
                        if listed
                        else f"ollama 로컬 모델 목록에 없습니다: {model}"
                    )
                    if proc.returncode != 0:
                        detail = f"ollama list 실패 (exit {proc.returncode})"
                    record("ollama_model", listed, detail)
                except Exception as exc:
                    record("ollama_model", False, f"ollama list 확인 실패: {exc}")
    except Exception as exc:
        errors.append(f"온디바이스 검증 실패: {exc}")

    return {
        "ok": bool(checks) and all(check["ok"] for check in checks) and not errors,
        "engine": engine,
        "model": model,
        "checks": checks,
        "errors": errors,
    }


def download_command(rec: EngineRecommendation) -> str:
    """The exact command that fetches the recommended weights."""
    name = rec.recommended_model.split("/")[-1]
    if rec.primary_engine == "ollama":
        return f"ollama pull {rec.recommended_model}"
    return 'hf download ' + rec.recommended_model + ' --local-dir "$HOME/Models/' + name + '"'


def generate_command(rec: EngineRecommendation, prompt: str) -> str:
    """The exact command that runs one generation on the recommended model."""
    name = rec.recommended_model.split("/")[-1]
    if rec.primary_engine == "ollama":
        return 'ollama run ' + rec.recommended_model + ' "' + prompt + '"'
    if rec.primary_engine == "llama.cpp":
        return 'llama-cli -m "$HOME/Models/' + name + "/" + name + '.gguf" -p "' + prompt + '" -n 128'
    return (
        "HF_HUB_OFFLINE=1 mlx_lm.generate --model " + chr(34) + "$HOME/Models/" + name + chr(34)
        + ' --prompt "' + prompt + '" --max-tokens 128 --temp 0.2 --seed 42'
    )


if __name__ == "__main__":
    print(json.dumps(ondevice_summary_dict(), ensure_ascii=False, indent=2))
