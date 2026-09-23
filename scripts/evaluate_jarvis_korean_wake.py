#!/usr/bin/env python3
"""Measure Korean wake generalization on held-out synthesized voices.

The bundled head at `voice/models/hey_jarvis_ko_ridge.onnx` was fitted to a single
synthesized positive clip, so the honest question it can answer about
generalization is whether it survives voices it never saw. This script renders a
held-out corpus with a local synthesizer, scores every clip through the
production `OpenWakeVadFrontend` at the pinned `WAKE_THRESHOLD`, and reports per-clip
scores plus accept and false-accept rates.

Two backends are available. `qwen3tts` drives the same cached
`Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice` model the product uses and can select any of
its speakers, which is what makes a multi-voice corpus possible on this host.
`say` uses the macOS synthesizer; only Yuna produces real Korean speech here, so
that backend is mostly useful as a second, independent realization.

A render that is silent or shorter than one usable frame is a synthesis failure,
not a wake miss, so it is refused by default instead of being scored as a
negative result. The script never changes the threshold, never writes
application state, and fails closed when the custom head is missing or invalid.

Synthesized voices are still not human speakers: recordings from real people,
microphones, and rooms remain a separate unverified gap.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

try:  # The focused CI interpreter ships without numpy, so keep this import soft.
    import numpy as np
except Exception:  # pragma: no cover - exercised only where numpy is absent.
    np = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from jarvis_voice import OpenWakeVadFrontend, WAKE_THRESHOLD, resolve_custom_wake_model


SAMPLE_RATE = 16_000
FRAME_SAMPLES = 320  # 20 ms at 16 kHz is the production analyze() contract.
WAKE_PHRASE = "헤이 자비스"
NEGATIVE_PHRASES: tuple[str, ...] = ("안녕하세요", "자비스 봇이야", "오늘 날씨 어때")

# Speakers the cached Qwen3-TTS CustomVoice build reports through
# get_supported_speakers(). The product adapter always speaks with the first
# one, so every other speaker is held out from the shipped voice.
QWEN3_TTS_SPEAKERS: tuple[str, ...] = (
    "aiden",
    "dylan",
    "eric",
    "ono_anna",
    "ryan",
    "serena",
    "sohee",
    "uncle_fu",
    "vivian",
)
SAY_VOICES: tuple[str, ...] = (
    "Yuna",
    "Eddy",
    "Flo",
    "Grandma",
    "Grandpa",
    "Reed",
    "Rocko",
    "Sandy",
    "Shelley",
)
DEFAULT_RATE_VARIANTS_MPR: tuple[tuple[str, int], ...] = (("Yuna", 140), ("Yuna", 240))

MIN_CLIP_SECONDS = 0.25
MIN_CLIP_RMS = 32.0


class WakeEvaluationError(RuntimeError):
    """The evaluation cannot be trusted, so it must not report a rate."""


def _require_numpy() -> Any:
    """Fail loudly when the numpy-backed scoring path is unavailable."""

    if np is None:
        raise WakeEvaluationError("wake_eval_numpy_unavailable")
    return np


def load_wav_mono_pcm16(path: Path) -> np.ndarray:
    """Load a mono 16-bit WAV and resample it to 16 kHz."""

    np = _require_numpy()

    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sample_width = handle.getsampwidth()
        source_rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    if channels != 1 or sample_width != 2:
        raise WakeEvaluationError(f"wake_eval_wav_format_invalid:{path}")
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if source_rate != SAMPLE_RATE:
        if source_rate <= 0:
            raise WakeEvaluationError(f"wake_eval_wav_rate_invalid:{path}")
        output_size = round(samples.size * SAMPLE_RATE / source_rate)
        source_positions = np.arange(samples.size, dtype=np.float64)
        target_positions = np.arange(output_size, dtype=np.float64) * source_rate / SAMPLE_RATE
        samples = np.interp(target_positions, source_positions, samples)
    return np.clip(np.rint(samples), -32768, 32767).astype(np.int16)


def write_wav_mono_pcm16(path: Path, samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> None:
    np = _require_numpy()
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(np.asarray(samples, dtype=np.int16).tobytes())


def clip_quality(samples: np.ndarray, *, sample_rate: int = SAMPLE_RATE) -> dict[str, Any]:
    """Describe a render so a synthesis failure cannot masquerade as a wake miss."""

    np = _require_numpy()

    frames = int(samples.size) // FRAME_SAMPLES
    seconds = float(samples.size) / float(sample_rate)
    rms = float(np.sqrt(np.mean(np.square(samples.astype(np.float64))))) if samples.size else 0.0
    if frames <= 0:
        return {"seconds": seconds, "rms": rms, "frames": frames, "usable": False, "reason": "clip_shorter_than_one_frame"}
    if seconds < MIN_CLIP_SECONDS:
        return {"seconds": seconds, "rms": rms, "frames": frames, "usable": False, "reason": "clip_too_short"}
    if rms < MIN_CLIP_RMS:
        return {"seconds": seconds, "rms": rms, "frames": frames, "usable": False, "reason": "clip_too_quiet"}
    return {"seconds": round(seconds, 4), "rms": round(rms, 4), "frames": frames, "usable": True, "reason": ""}


def make_say_synthesizer(runner: Callable[..., Any] = subprocess.run) -> Callable[..., Path]:
    """Return a renderer backed by the macOS `say` synthesizer."""

    def synthesize(text: str, voice: str, output: Path, *, rate_mpr: int | None = None) -> Path:
        say_binary = shutil.which("say")
        if say_binary is None:
            raise WakeEvaluationError("wake_eval_say_unavailable")
        output.parent.mkdir(parents=True, exist_ok=True)
        command = [
            say_binary,
            "-v",
            voice,
            "-o",
            str(output),
            "--file-format=WAVE",
            "--data-format=LEI16@16000",
        ]
        if rate_mpr is not None:
            command.extend(["-r", str(rate_mpr)])
        command.append(text)
        completed = runner(command, capture_output=True, text=True)
        if getattr(completed, "returncode", 1) != 0 or not output.is_file() or output.stat().st_size == 0:
            raise WakeEvaluationError(f"wake_eval_synthesis_failed:{voice}")
        return output

    return synthesize


def make_qwen3tts_synthesizer(model_id: str | None = None) -> Callable[..., Path]:
    """Return a renderer backed by the cached Qwen3-TTS CustomVoice model."""

    import os

    from jarvis_voice import QWEN3_TTS_MODEL_ID, QWEN3_TTS_PRECISION, _resolve_qwen3_tts_model_path

    state: dict[str, Any] = {}

    def engine() -> Any:
        if "model" not in state:
            _resolve_qwen3_tts_model_path(model_id or QWEN3_TTS_MODEL_ID, environment=os.environ, home=Path.home())
            import torch
            from qwen_tts import Qwen3TTSModel

            dtype = torch.bfloat16 if QWEN3_TTS_PRECISION == "bf16" else torch.float16
            path = _resolve_qwen3_tts_model_path(model_id or QWEN3_TTS_MODEL_ID, environment=os.environ, home=Path.home())
            state["model"] = Qwen3TTSModel.from_pretrained(path, dtype=dtype, local_files_only=True)
        return state["model"]

    def synthesize(text: str, voice: str, output: Path, *, rate_mpr: int | None = None) -> Path:
        del rate_mpr  # Qwen3-TTS CustomVoice has no speaking-rate control.
        np = _require_numpy()
        wavs, sample_rate = engine().generate_custom_voice(text=text, speaker=voice, language="Korean")
        audio = wavs[0] if isinstance(wavs, list) else wavs
        if hasattr(audio, "detach"):
            audio = audio.detach().cpu().numpy()
        samples = np.asarray(audio).squeeze().astype(np.float32)
        peak = float(np.max(np.abs(samples))) if samples.size else 0.0
        if peak > 1.0:
            samples = samples / peak
        pcm = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16)
        target_rate = int(sample_rate)
        if target_rate != SAMPLE_RATE:
            pcm = load_wav_mono_pcm16(_resave(pcm, target_rate, output))
            write_wav_mono_pcm16(output, pcm)
            return output
        write_wav_mono_pcm16(output, pcm, target_rate)
        return output

    return synthesize


def _resave(pcm: np.ndarray, sample_rate: int, output: Path) -> Path:
    scratch = output.with_suffix(".raw-rate.wav")
    write_wav_mono_pcm16(scratch, pcm, sample_rate)
    return scratch


def score_clip(frontend: Any, samples: np.ndarray) -> dict[str, Any]:
    """Score one clip frame by frame and keep the peak stock and custom scores."""

    usable = samples.size - (samples.size % FRAME_SAMPLES)
    frames = usable // FRAME_SAMPLES
    if frames <= 0:
        return {
            "frames": 0,
            "stock_max": 0.0,
            "custom_max": 0.0,
            "accepted": False,
            "stock_accepted": False,
            "reason": "clip_shorter_than_one_frame",
        }
    stock_max = 0.0
    custom_max = 0.0
    for start in range(0, usable, FRAME_SAMPLES):
        analysis = frontend.analyze(samples[start : start + FRAME_SAMPLES].tobytes())
        stock_max = max(stock_max, float(analysis.stock_wake_score or 0.0))
        custom_max = max(custom_max, float(analysis.custom_wake_score or 0.0))
    return {
        "frames": frames,
        "stock_max": round(stock_max, 6),
        "custom_max": round(custom_max, 6),
        "accepted": bool(custom_max >= WAKE_THRESHOLD),
        "stock_accepted": bool(stock_max >= WAKE_THRESHOLD),
        "reason": "",
    }


def build_corpus(
    corpus_dir: Path,
    voices: Sequence[str],
    rate_variants: Iterable[tuple[str, int]],
    *,
    synth: Callable[..., Path],
    allow_unusable: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Render the planned clips, refusing any render that is not real speech."""

    entries: list[dict[str, Any]] = []
    unusable: list[dict[str, Any]] = []
    planned: list[dict[str, Any]] = []
    for voice in voices:
        planned.append({"kind": "positive", "voice": voice, "rate_mpr": None, "text": WAKE_PHRASE, "name": f"pos-{voice}-default"})
    for voice, rate in rate_variants:
        planned.append({"kind": "positive", "voice": voice, "rate_mpr": rate, "text": WAKE_PHRASE, "name": f"pos-{voice}-{rate}"})
    for voice in voices:
        for index, phrase in enumerate(NEGATIVE_PHRASES):
            planned.append({"kind": "negative", "voice": voice, "rate_mpr": None, "text": phrase, "name": f"neg-{voice}-{index}"})

    for item in planned:
        target = corpus_dir / f"{item['name']}.wav"
        synth(item["text"], item["voice"], target, rate_mpr=item["rate_mpr"])
        quality = clip_quality(load_wav_mono_pcm16(target))
        if not quality["usable"]:
            record = {**item, "wav": str(target), "quality": quality}
            if not allow_unusable:
                raise WakeEvaluationError(f"wake_eval_clip_unusable:{item['voice']}:{quality['reason']}")
            unusable.append(record)
            continue
        entries.append({**item, "wav": target, "quality": quality})
    return entries, unusable


def evaluate(
    entries: Sequence[dict[str, Any]],
    frontend: Any,
    *,
    loader: Callable[[Path], np.ndarray] = load_wav_mono_pcm16,
    extra_references: Sequence[Path] = (),
) -> dict[str, Any]:
    """Score every entry and summarise accept and false-accept rates."""

    rows: list[dict[str, Any]] = []
    for entry in entries:
        samples = loader(Path(entry["wav"]))
        result = score_clip(frontend, samples)
        rows.append({**{k: v for k, v in entry.items() if k != "wav"}, "wav": str(entry["wav"]), **result})

    references: list[dict[str, Any]] = []
    for path in extra_references:
        candidate = Path(path)
        if not candidate.is_file():
            continue
        references.append({"wav": str(candidate), "note": "seen_voice_reference", **score_clip(frontend, loader(candidate))})

    positives = [row for row in rows if row["kind"] == "positive"]
    negatives = [row for row in rows if row["kind"] == "negative"]

    def rate(items: Sequence[dict[str, Any]], key: str) -> tuple[int, int, float]:
        hits = sum(1 for row in items if row[key])
        total = len(items)
        return hits, total, (hits / total if total else 0.0)

    accepted, positive_total, positive_rate = rate(positives, "accepted")
    false_accepts, negative_total, false_accept_rate = rate(negatives, "accepted")
    stock_accepted, _, stock_positive_rate = rate(positives, "stock_accepted")
    stock_false, _, stock_false_rate = rate(negatives, "stock_accepted")
    borderline = [row for row in negatives if row["custom_max"] >= WAKE_THRESHOLD - 0.05]

    return {
        "threshold": WAKE_THRESHOLD,
        "scope": {
            "corpus": "held_out_synthetic_voices",
            "human_speakers": False,
            "microphones_or_rooms": False,
            "threshold_changed": False,
            "fit_reference": "single synthesized positive clip",
        },
        "summary": {
            "positive_accepts": accepted,
            "positive_total": positive_total,
            "positive_accept_rate": round(positive_rate, 6),
            "negative_false_accepts": false_accepts,
            "negative_total": negative_total,
            "negative_false_accept_rate": round(false_accept_rate, 6),
            "stock_positive_accepts": stock_accepted,
            "stock_positive_accept_rate": round(stock_positive_rate, 6),
            "stock_negative_false_accepts": stock_false,
            "stock_negative_false_accept_rate": round(stock_false_rate, 6),
            "missed_voices": sorted({row["voice"] for row in positives if not row["accepted"]}),
            "accepted_voices": sorted({row["voice"] for row in positives if row["accepted"]}),
            "false_accept_voices": sorted({row["voice"] for row in negatives if row["accepted"]}),
            "borderline_negatives": [
                {"voice": row["voice"], "text": row["text"], "custom_max": row["custom_max"]} for row in borderline
            ],
        },
        "references": references,
        "rows": rows,
    }


def default_voices(backend: str) -> tuple[str, ...]:
    return QWEN3_TTS_SPEAKERS if backend == "qwen3tts" else SAY_VOICES


def make_synthesizer(backend: str) -> Callable[..., Path]:
    if backend == "qwen3tts":
        return make_qwen3tts_synthesizer()
    if backend == "say":
        return make_say_synthesizer()
    raise WakeEvaluationError(f"wake_eval_backend_unsupported:{backend}")


def probe_renders(
    corpus_dir: Path,
    voices: Sequence[str],
    synth: Callable[..., Path],
) -> list[dict[str, Any]]:
    """Report which requested voices actually render speech on this host."""

    rows: list[dict[str, Any]] = []
    for voice in voices:
        target = corpus_dir / f"probe-{voice}.wav"
        try:
            synth(WAKE_PHRASE, voice, target)
            quality = clip_quality(load_wav_mono_pcm16(target))
        except WakeEvaluationError as exc:
            rows.append({"voice": voice, "usable": False, "reason": str(exc)})
            continue
        rows.append({"voice": voice, **quality})
    return rows


def run_evaluation(
    *,
    corpus_dir: Path,
    backend: str = "qwen3tts",
    voices: Sequence[str] | None = None,
    rate_variants: Iterable[tuple[str, int]] = DEFAULT_RATE_VARIANTS_MPR,
    custom_wake_model: Path | None = None,
    references: Sequence[Path] = (),
    allow_unusable: bool = False,
    synth: Callable[..., Path] | None = None,
) -> dict[str, Any]:
    """Resolve the custom head fail-closed, build the corpus, and score it."""

    model_path = resolve_custom_wake_model(custom_wake_model)
    if model_path is None:
        raise WakeEvaluationError("custom_wake_model_missing")
    renderer = synth or make_synthesizer(backend)
    selected = default_voices(backend) if voices is None else tuple(voices)
    if not selected:
        raise WakeEvaluationError("wake_eval_voices_empty")
    entries, unusable = build_corpus(
        corpus_dir,
        selected,
        rate_variants if backend == "say" else (),
        synth=renderer,
        allow_unusable=allow_unusable,
    )
    frontend = OpenWakeVadFrontend(custom_wake_model=model_path)
    report = evaluate(entries, frontend, extra_references=references)
    report["backend"] = backend
    report["custom_model"] = str(model_path)
    report["custom_model_bytes"] = model_path.stat().st_size
    report["corpus_size"] = len(entries)
    report["unusable_renders"] = unusable
    report["voices"] = list(selected)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Held-out Korean wake evaluation.")
    parser.add_argument("--backend", choices=("qwen3tts", "say"), default="qwen3tts")
    parser.add_argument("--corpus-dir", type=Path, default=None)
    parser.add_argument("--custom-wake-model", type=Path, default=None)
    parser.add_argument("--references", default="", help="Comma-separated WAV paths for seen-voice contrast.")
    parser.add_argument("--voices", default="", help="Comma-separated voices; defaults to every voice of the backend.")
    parser.add_argument("--allow-unusable", action="store_true", help="Record unusable renders instead of refusing.")
    parser.add_argument("--probe-only", action="store_true", help="Only report which voices render speech.")
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args(argv)

    voices = tuple(part.strip() for part in args.voices.split(",") if part.strip())
    references = tuple(Path(part.strip()) for part in args.references.split(",") if part.strip())
    with tempfile.TemporaryDirectory(prefix="jarvis-wake-eval-") as scratch:
        corpus_dir = args.corpus_dir.expanduser() if args.corpus_dir is not None else Path(scratch)
        if args.probe_only:
            report = {
                "backend": args.backend,
                "probe": probe_renders(corpus_dir, voices or default_voices(args.backend), make_synthesizer(args.backend)),
            }
        else:
            report = run_evaluation(
                corpus_dir=corpus_dir,
                backend=args.backend,
                voices=voices or None,
                custom_wake_model=args.custom_wake_model,
                references=references,
                allow_unusable=args.allow_unusable,
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

