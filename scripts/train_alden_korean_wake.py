#!/usr/bin/env python3
"""Export a tiny Korean Alden wake classifier for openWakeWord.

This is intentionally a bounded calibration/distillation path. It reuses the
local openWakeWord audio embedding frontend, fits a regularized linear head to
one positive wake clip plus explicit non-wake and silence negatives, exports a
real ONNX model, then scores all three clips through OpenWakeVadFrontend.

Running this exporter is an explicit operator step. Output is written under
voice/models/experimental and is never bundled or auto-selected. Synthetic
results are calibration evidence only; release requires a separate
false-accept gate on representative human speech.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import wave
from pathlib import Path
from typing import Any, Sequence

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from alden_voice import CUSTOM_WAKE_MODEL_MAX_BYTES, OpenWakeVadFrontend, WAKE_THRESHOLD


SAMPLE_RATE = 16_000
FEATURE_FRAMES = 16
FEATURE_DIM = 96
NATIVE_CHUNK_SAMPLES = 1_280
FRONTEND_FRAME_SAMPLES = 320
WARMUP_WINDOWS = FEATURE_FRAMES
RIDGE_LAMBDA = 1.0
TARGET_POSITIVE_SCORE = 0.90
TARGET_NEGATIVE_SCORE = 0.20
POSITIVE_WINDOWS_PER_CLIP = 2
HARD_NEGATIVE_WINDOWS_PER_CLIP = 6
HARD_NEGATIVE_WEIGHT = 2
HARD_NEGATIVE_ROUNDS = 4


def _load_wav_mono_pcm16(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sample_width = handle.getsampwidth()
        source_rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    if channels != 1 or sample_width != 2:
        raise ValueError(f"wake_training_wav_format_invalid:{path}")
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if source_rate != SAMPLE_RATE:
        output_size = round(samples.size * SAMPLE_RATE / source_rate)
        source_positions = np.arange(samples.size, dtype=np.float64)
        target_positions = np.arange(output_size, dtype=np.float64) * source_rate / SAMPLE_RATE
        samples = np.interp(target_positions, source_positions, samples)
    return np.clip(np.rint(samples), -32768, 32767).astype(np.int16)


def _feature_windows(samples: np.ndarray) -> np.ndarray:
    from openwakeword.utils import AudioFeatures

    # Use only openWakeWord's pretrained acoustic embedding pipeline. Loading
    # an unrelated stock phrase would make training depend on that detector
    # and could accidentally keep its branded wake path enabled at runtime.
    # Synthetic wake clips can be shorter than the 16 embedding windows needed
    # to warm the frontend. Runtime audio always has prior ambient context, so
    # prepend deterministic silence and then discard those cold-start windows
    # from the training set instead of rejecting short but valid wake renders.
    preroll_samples = WARMUP_WINDOWS * NATIVE_CHUNK_SAMPLES
    samples = np.pad(samples, (preroll_samples, NATIVE_CHUNK_SAMPLES), mode="constant")
    preprocessor = AudioFeatures(inference_framework="onnx", ncpu=1)
    windows: list[np.ndarray] = []
    for start in range(0, samples.size - NATIVE_CHUNK_SAMPLES + 1, NATIVE_CHUNK_SAMPLES):
        preprocessor(samples[start : start + NATIVE_CHUNK_SAMPLES])
        features = np.asarray(preprocessor.get_features(FEATURE_FRAMES), dtype=np.float32)
        if features.shape != (1, FEATURE_FRAMES, FEATURE_DIM):
            raise RuntimeError(f"wake_training_feature_shape_invalid:{features.shape}")
        windows.append(features.reshape(-1))
    if len(windows) <= WARMUP_WINDOWS:
        raise RuntimeError("wake_training_clip_too_short")
    return np.stack(windows[WARMUP_WINDOWS:])


def _fit_ridge_raw(positive: np.ndarray, negative: np.ndarray) -> tuple[np.ndarray, float]:
    samples = np.concatenate([positive, negative], axis=0).astype(np.float32)
    mean = samples.mean(axis=0)
    scale = samples.std(axis=0) + np.float32(1e-3)
    standardized = (samples - mean) / scale
    labels = np.concatenate(
        [np.ones(positive.shape[0], dtype=np.float32), -np.ones(negative.shape[0], dtype=np.float32)]
    )

    kernel = standardized @ standardized.T
    kernel += np.eye(kernel.shape[0], dtype=np.float32) * np.float32(RIDGE_LAMBDA)
    dual = np.linalg.solve(kernel, labels)
    standardized_weight = standardized.T @ dual
    raw_weight = standardized_weight / scale
    raw_bias = float(-np.dot(raw_weight, mean))
    return raw_weight.astype(np.float32), raw_bias


def _linear_scores(features: np.ndarray, weight: np.ndarray, bias: float) -> np.ndarray:
    return features.astype(np.float32) @ weight + np.float32(bias)


def _top_rows(features: np.ndarray, scores: np.ndarray, count: int) -> np.ndarray:
    take = min(max(int(count), 1), features.shape[0])
    indices = np.argpartition(scores, -take)[-take:]
    return features[indices]


def _fit_clip_peak_head(
    positive_clips: Sequence[np.ndarray],
    negative_clips: Sequence[np.ndarray],
) -> tuple[np.ndarray, float, dict[str, Any]]:
    """Fit a linear head against the same clip-peak statistic used at runtime.

    A wake clip is a multiple-instance positive: only one short window needs to
    cross the runtime threshold. A negative clip is the opposite: its highest
    scoring window determines a false accept. The old exporter labelled every
    window in a positive clip as positive, even silence/context, and optimized
    average window separation. This bounded hard-negative loop repeatedly
    refits to each positive clip's strongest windows and each negative clip's
    strongest (therefore most dangerous) windows before calibrating from clip
    peaks only.
    """

    if not positive_clips or not negative_clips:
        raise ValueError("wake_training_clip_sets_missing")
    positive_all = np.concatenate(tuple(positive_clips), axis=0)
    negative_all = np.concatenate(tuple(negative_clips), axis=0)
    weight, bias = _fit_ridge_raw(positive_all, negative_all)

    selected_positive = positive_all
    selected_negative = negative_all
    for _ in range(HARD_NEGATIVE_ROUNDS):
        selected_positive = np.concatenate(
            [
                _top_rows(clip, _linear_scores(clip, weight, bias), POSITIVE_WINDOWS_PER_CLIP)
                for clip in positive_clips
            ],
            axis=0,
        )
        selected_negative = np.concatenate(
            [
                _top_rows(clip, _linear_scores(clip, weight, bias), HARD_NEGATIVE_WINDOWS_PER_CLIP)
                for clip in negative_clips
            ],
            axis=0,
        )
        weighted_negative = np.repeat(selected_negative, HARD_NEGATIVE_WEIGHT, axis=0)
        weight, bias = _fit_ridge_raw(selected_positive, weighted_negative)

    positive_peaks = [float(_linear_scores(clip, weight, bias).max()) for clip in positive_clips]
    negative_peaks = [float(_linear_scores(clip, weight, bias).max()) for clip in negative_clips]
    positive_floor = min(positive_peaks)
    negative_ceiling = max(negative_peaks)
    if not positive_floor > negative_ceiling:
        raise RuntimeError("wake_training_clip_peaks_not_separable")

    positive_logit = math.log(TARGET_POSITIVE_SCORE / (1.0 - TARGET_POSITIVE_SCORE))
    negative_logit = math.log(TARGET_NEGATIVE_SCORE / (1.0 - TARGET_NEGATIVE_SCORE))
    gain = (positive_logit - negative_logit) / max(positive_floor - negative_ceiling, 1e-6)
    offset = negative_logit - gain * negative_ceiling
    calibrated_weight = weight * np.float32(gain)
    calibrated_bias = float(bias * gain + offset)
    return calibrated_weight.astype(np.float32), calibrated_bias, {
        "objective": "clip_peak_multiple_instance_hard_negative",
        "hard_negative_rounds": HARD_NEGATIVE_ROUNDS,
        "positive_windows_per_clip": POSITIVE_WINDOWS_PER_CLIP,
        "hard_negative_windows_per_clip": HARD_NEGATIVE_WINDOWS_PER_CLIP,
        "hard_negative_weight": HARD_NEGATIVE_WEIGHT,
        "selected_positive_windows": int(selected_positive.shape[0]),
        "selected_negative_windows": int(selected_negative.shape[0]),
        "raw_positive_clip_peak_floor": positive_floor,
        "raw_negative_clip_peak_ceiling": negative_ceiling,
        "raw_clip_peak_margin": positive_floor - negative_ceiling,
        "calibration_gain": float(gain),
        "target_positive_score": TARGET_POSITIVE_SCORE,
        "target_negative_score": TARGET_NEGATIVE_SCORE,
    }


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_model_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(ROOT.resolve()))
    except ValueError:
        return path.name


def _export_onnx(output: Path, weight: np.ndarray, bias: float) -> None:
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and output.is_symlink():
        raise RuntimeError("wake_training_output_symlink_rejected")

    flattened_size = FEATURE_FRAMES * FEATURE_DIM
    weight_matrix = weight.reshape(flattened_size, 1)
    graph = helper.make_graph(
        [
            helper.make_node("Flatten", ["features"], ["flat"], axis=1),
            helper.make_node("Gemm", ["flat", "weight", "bias"], ["logit"]),
            helper.make_node("Sigmoid", ["logit"], ["score"]),
        ],
        "alden_korean_wake_ridge",
        [helper.make_tensor_value_info("features", TensorProto.FLOAT, [1, FEATURE_FRAMES, FEATURE_DIM])],
        [helper.make_tensor_value_info("score", TensorProto.FLOAT, [1, 1])],
        [
            numpy_helper.from_array(weight_matrix, name="weight"),
            numpy_helper.from_array(np.asarray([bias], dtype=np.float32), name="bias"),
        ],
    )
    model = helper.make_model(
        graph,
        producer_name="openkakao-alden-local-wake",
        opset_imports=[helper.make_opsetid("", 13)],
    )
    model.ir_version = min(model.ir_version, 10)
    onnx.checker.check_model(model)
    onnx.save(model, str(output))
    size = output.stat().st_size
    if size <= 0 or size > CUSTOM_WAKE_MODEL_MAX_BYTES:
        output.unlink(missing_ok=True)
        raise RuntimeError("wake_training_model_size_invalid")


class _ZeroStockModel:
    def predict(self, _samples: np.ndarray) -> dict[str, float]:
        return {"alden_v0.1": 0.0}


class _NoopVad:
    def is_speech(self, _frame: bytes, _sample_rate: int) -> bool:
        return False


def _score_custom_model(model_path: Path, samples: np.ndarray) -> dict[str, Any]:
    frontend = OpenWakeVadFrontend(
        custom_wake_model=model_path,
        stock_model=_ZeroStockModel(),
        vad=_NoopVad(),
    )
    scores: list[float] = []
    usable = samples.size - (samples.size % FRONTEND_FRAME_SAMPLES)
    for start in range(0, usable, FRONTEND_FRAME_SAMPLES):
        frame = samples[start : start + FRONTEND_FRAME_SAMPLES].tobytes()
        analysis = frontend.analyze(frame)
        scores.append(float(analysis.custom_wake_score or 0.0))
    maximum = max(scores, default=0.0)
    return {
        "frames": len(scores),
        "custom_max": maximum,
        "accepted": maximum >= WAKE_THRESHOLD,
    }


def _as_paths(value: Path | Sequence[Path]) -> tuple[Path, ...]:
    if isinstance(value, Path):
        return (value,)
    paths = tuple(Path(item) for item in value)
    if not paths:
        raise ValueError("wake_training_clips_missing")
    return paths


def train_and_score(
    wake_path: Path | Sequence[Path],
    control_path: Path | Sequence[Path],
    output: Path,
    *,
    training_voices: Sequence[str] = (),
    training_negative_phrases: Sequence[str] = (),
) -> dict[str, Any]:
    wake_paths = _as_paths(wake_path)
    control_paths = _as_paths(control_path)
    wakes = [_load_wav_mono_pcm16(path) for path in wake_paths]
    controls = [_load_wav_mono_pcm16(path) for path in control_paths]
    silence = np.zeros(SAMPLE_RATE * 3, dtype=np.int16)

    positive_clips = [_feature_windows(wake) for wake in wakes]
    control_windows = [_feature_windows(control) for control in controls]
    silence_windows = _feature_windows(silence)
    negative_clips = [*control_windows, silence_windows]
    positive = np.concatenate(positive_clips, axis=0)
    negative = np.concatenate(negative_clips, axis=0)
    weight, bias, fit = _fit_clip_peak_head(positive_clips, negative_clips)
    _export_onnx(output, weight, bias)
    wake_scores = [_score_custom_model(output, wake) for wake in wakes]
    control_scores = [_score_custom_model(output, control) for control in controls]
    silence_score = _score_custom_model(output, silence)

    return {
        "model_path": _stable_model_path(output),
        "model_bytes": output.stat().st_size,
        "model_sha256": _sha256_path(output),
        "threshold": WAKE_THRESHOLD,
        "training": {
            "method": "openwakeword_embedding_clip_peak_ridge_head",
            "positive_windows": int(positive.shape[0]),
            "negative_windows": int(negative.shape[0]),
            "positive_clips": len(wakes),
            "negative_clips": len(negative_clips),
            "proof_scope": "bounded_synthetic_training_only",
            "provenance": {
                "voices": sorted(set(training_voices)),
                "negative_phrases": sorted(set(training_negative_phrases)),
                "wake_phrase": "올든",
            },
            "input_manifest": {
                "wake": [
                    {"name": path.name, "sha256": _sha256_path(path)} for path in wake_paths
                ],
                "control": [
                    {"name": path.name, "sha256": _sha256_path(path)} for path in control_paths
                ],
            },
            **fit,
        },
        "scores": {
            "wake": max(wake_scores, key=lambda score: score["custom_max"]),
            "wake_clips": wake_scores,
            "control": max(control_scores, key=lambda score: score["custom_max"]),
            "control_clips": control_scores,
            "silence": silence_score,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train/export a bounded Korean Alden wake ONNX head.")
    parser.add_argument("--wake", type=Path, nargs="+", default=None)
    parser.add_argument("--control", type=Path, nargs="+", default=None)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "voice/models/experimental/alden_ko_ridge_candidate.onnx",
    )
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--training-voices", default="", help="Comma-separated synthetic training voice IDs.")
    parser.add_argument(
        "--training-negative-phrases",
        default="",
        help="Pipe-separated negative phrases used to render --control clips.",
    )
    args = parser.parse_args(argv)

    args.output.expanduser().parent.mkdir(parents=True, exist_ok=True)

    wake_paths = args.wake or [ROOT / ".venv-voice/smoke/alden-ko.wav"]
    control_paths = args.control or [ROOT / ".venv-voice/smoke/alden-control-ko.wav"]
    report = train_and_score(
        tuple(path.expanduser() for path in wake_paths),
        tuple(path.expanduser() for path in control_paths),
        args.output.expanduser(),
        training_voices=tuple(part.strip() for part in args.training_voices.split(",") if part.strip()),
        training_negative_phrases=tuple(
            part.strip() for part in args.training_negative_phrases.split("|") if part.strip()
        ),
    )
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.report is not None:
        report_path = args.report.expanduser()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
