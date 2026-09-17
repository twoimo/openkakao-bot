"""On-device LLM hardware detection and engine/model recommendation pipeline.

Detects Apple Silicon chips, unified memory size, and available engines (MLX,
llama.cpp, Ollama) to recommend optimal quantized models for local KakaoTalk
auto-replies (2026-09-17).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


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


def detect_hardware() -> HardwareSpec:
    """Read CPU model, core count, and memory from macOS sysctl."""
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

    is_apple = "Apple" in chip or "M" in chip

    return HardwareSpec(
        chip=chip,
        cores=cores,
        memory_bytes=mem_bytes,
        memory_gb=round(mem_bytes / (1024**3), 1),
        is_apple_silicon=is_apple,
    )


def detect_available_engines() -> list[str]:
    """Find installed local LLM runtimes."""
    engines = []
    if shutil.which("mlx_lm") or Path.home().joinpath(".local/bin/mlx_lm").exists():
        engines.append("mlx")
    if shutil.which("llama-cli") or shutil.which("llama-server"):
        engines.append("llama.cpp")
    if shutil.which("ollama"):
        engines.append("ollama")
    return engines


def recommend_ondevice_setup(hw: HardwareSpec | None = None) -> EngineRecommendation:
    """Recommend the optimal local engine and model based on hardware spec."""
    spec = hw or detect_hardware()
    available = detect_available_engines()

    # Engine selection: MLX is preferred on Apple Silicon for unified memory bandwidth
    if spec.is_apple_silicon:
        primary = "mlx"
    elif "ollama" in available:
        primary = "ollama"
    else:
        primary = "llama.cpp"

    # Model tier selection by memory capacity
    if spec.memory_gb >= 64:
        model = "mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit"
        quant = "mixed 4-8bit (128GB 고성능 최적화)"
        reason = f"{spec.chip} ({spec.memory_gb}GB RAM) 고사양 통합 메모리에 최적화된 Qwen 3.8 초고속 서빙 모델"
    elif spec.memory_gb >= 32:
        model = "mlx-community/Qwen2.5-14B-Instruct-4bit"
        quant = "4bit"
        reason = f"{spec.chip} ({spec.memory_gb}GB RAM)에 최적화된 14B 고정밀 추론 모델"
    else:
        model = "mlx-community/Qwen2.5-7B-Instruct-4bit"
        quant = "4bit"
        reason = f"{spec.chip} ({spec.memory_gb}GB RAM)에 최적화된 7B 저지연 경량 모델"

    return EngineRecommendation(
        primary_engine=primary,
        available_engines=available,
        recommended_model=model,
        recommended_quant=quant,
        reason=reason,
    )


def ondevice_summary_dict() -> dict[str, Any]:
    hw = detect_hardware()
    rec = recommend_ondevice_setup(hw)
    return {
        "hardware": asdict(hw),
        "recommendation": asdict(rec),
    }


if __name__ == "__main__":
    print(json.dumps(ondevice_summary_dict(), ensure_ascii=False, indent=2))

