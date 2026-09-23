"""Contract tests for the held-out Korean wake evaluation helper.

The helper decides whether the bundled Korean wake head generalizes, so the
failure modes that matter are the quiet ones: a synthesis that produced silence
scored as a wake miss, a threshold moved to force acceptance, and a missing
custom head silently replaced by the stock model. These tests pin the guard
paths, the frame contract, and the reported rates with scripted frontends so no
model, microphone, or network is needed.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import evaluate_jarvis_korean_wake as evaluations  # noqa: E402
from jarvis_voice import WAKE_THRESHOLD  # noqa: E402


try:  # The focused CI interpreter ships without numpy.
    import numpy as np  # noqa: F401

    HAS_NUMPY = True
except Exception:  # pragma: no cover - exercised only where numpy is absent.
    HAS_NUMPY = False


class FakeFrames:
    """A numpy-free stand-in with the size/slice/tobytes surface score_clip uses."""

    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    @property
    def size(self) -> int:
        return len(self._raw) // 2

    def __getitem__(self, item: object) -> "FakeFrames":
        if not isinstance(item, slice):
            raise TypeError("FakeFrames only supports slicing")
        start = 0 if item.start is None else item.start
        stop = self.size if item.stop is None else item.stop
        return FakeFrames(self._raw[start * 2 : stop * 2])

    def tobytes(self) -> bytes:
        return self._raw

    @staticmethod
    def of(frames: int) -> "FakeFrames":
        return FakeFrames(b"\x01\x00" * (320 * frames))


class FakeAnalysis:
    def __init__(self, stock: float, custom: float) -> None:
        self.stock_wake_score = stock
        self.custom_wake_score = custom
        self.rms = 0.0
        self.speech = True


class ScriptedFrontend:
    """Replay scripted scores and assert the production 20 ms frame contract."""

    def __init__(self, custom: list[float], stock: list[float] | None = None) -> None:
        self.custom = list(custom)
        self.stock = list(stock) if stock is not None else [0.0] * len(custom)
        self.frames: list[int] = []

    def analyze(self, frame: bytes) -> FakeAnalysis:
        self.frames.append(len(frame))
        index = min(len(self.frames) - 1, len(self.custom) - 1)
        return FakeAnalysis(self.stock[index], self.custom[index])


class WakeThresholdBoundaryTests(unittest.TestCase):
    def test_accepts_exactly_at_the_pinned_threshold(self) -> None:
        frontend = ScriptedFrontend([WAKE_THRESHOLD])
        result = evaluations.score_clip(frontend, FakeFrames.of(3))
        self.assertTrue(result["accepted"])
        self.assertEqual(result["custom_max"], round(WAKE_THRESHOLD, 6))

    def test_rejects_just_below_the_pinned_threshold(self) -> None:
        frontend = ScriptedFrontend([WAKE_THRESHOLD - 0.0001])
        result = evaluations.score_clip(frontend, FakeFrames.of(3))
        self.assertFalse(result["accepted"])

    def test_keeps_the_peak_over_every_frame(self) -> None:
        frontend = ScriptedFrontend([0.1, 0.9, 0.2])
        result = evaluations.score_clip(frontend, FakeFrames.of(3))
        self.assertEqual(result["frames"], 3)
        self.assertEqual(result["custom_max"], 0.9)

    def test_uses_only_whole_production_frames(self) -> None:
        frontend = ScriptedFrontend([0.5])
        result = evaluations.score_clip(frontend, FakeFrames.of(2))
        self.assertEqual(result["frames"], 2)
        self.assertEqual(frontend.frames, [640, 640])

    def test_reports_zero_frames_without_crashing(self) -> None:
        result = evaluations.score_clip(ScriptedFrontend([1.0]), FakeFrames(b""))
        self.assertEqual(result["frames"], 0)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "clip_shorter_than_one_frame")

    def test_threshold_constant_is_never_mutated(self) -> None:
        before = WAKE_THRESHOLD
        evaluations.score_clip(ScriptedFrontend([0.99]), FakeFrames.of(2))
        self.assertEqual(evaluations.WAKE_THRESHOLD, before)


class EvaluationGuardTests(unittest.TestCase):
    def test_missing_custom_head_fails_closed(self) -> None:
        with mock.patch.object(evaluations, "resolve_custom_wake_model", return_value=None):
            with self.assertRaises(evaluations.WakeEvaluationError) as caught:
                evaluations.run_evaluation(corpus_dir=Path(tempfile.gettempdir()), synth=lambda *a, **k: None)
        self.assertIn("custom_wake_model_missing", str(caught.exception))

    def test_unknown_backend_is_refused(self) -> None:
        with self.assertRaises(evaluations.WakeEvaluationError):
            evaluations.make_synthesizer("espeak")

    def test_missing_numpy_fails_closed(self) -> None:
        with mock.patch.object(evaluations, "np", None):
            with self.assertRaises(evaluations.WakeEvaluationError) as caught:
                evaluations.load_wav_mono_pcm16(Path("/nonexistent.wav"))
        self.assertIn("wake_eval_numpy_unavailable", str(caught.exception))

    def test_probe_reports_a_failed_render_instead_of_scoring_it(self) -> None:
        def refusing(text: str, voice: str, output: Path, *, rate_mpr: int | None = None) -> Path:
            raise evaluations.WakeEvaluationError(f"wake_eval_synthesis_failed:{voice}")

        with tempfile.TemporaryDirectory() as scratch:
            rows = evaluations.probe_renders(Path(scratch), ["Yuna", "Eddy"], refusing)
        self.assertEqual([row["voice"] for row in rows], ["Yuna", "Eddy"])
        self.assertTrue(all(row["usable"] is False for row in rows))

    def test_empty_voice_selection_is_refused(self) -> None:
        with mock.patch.object(evaluations, "resolve_custom_wake_model", return_value=Path(__file__)):
            with self.assertRaises(evaluations.WakeEvaluationError) as caught:
                evaluations.run_evaluation(corpus_dir=Path(tempfile.gettempdir()), backend="say", voices=(), synth=lambda *a, **k: None)
        self.assertIn("wake_eval_voices_empty", str(caught.exception))


class EvaluationReportTests(unittest.TestCase):
    def _entries(self, spec: list[tuple[str, str, str]]) -> list[dict[str, object]]:
        return [
            {"kind": kind, "voice": voice, "rate_mpr": None, "text": text, "wav": f"/tmp/{voice}-{index}.wav"}
            for index, (kind, voice, text) in enumerate(spec)
        ]

    def test_summary_rates_separate_positives_and_negatives(self) -> None:
        entries = self._entries(
            [
                ("positive", "aiden", "헤이 자비스"),
                ("positive", "dylan", "헤이 자비스"),
                ("negative", "aiden", "안녕하세요"),
                ("negative", "dylan", "자비스 봇이야"),
            ]
        )
        frontend = ScriptedFrontend([0.9, 0.4, 0.1, 0.67, 0.2])
        report = evaluations.evaluate(entries, frontend, loader=lambda path: FakeFrames.of(1))
        summary = report["summary"]
        self.assertEqual(summary["positive_accepts"], 1)
        self.assertEqual(summary["positive_total"], 2)
        self.assertEqual(summary["positive_accept_rate"], 0.5)
        self.assertEqual(summary["missed_voices"], ["dylan"])
        self.assertEqual(summary["accepted_voices"], ["aiden"])
        self.assertEqual(summary["negative_false_accepts"], 1)
        self.assertEqual(summary["false_accept_voices"], ["dylan"])
        self.assertFalse(report["scope"]["threshold_changed"])
        self.assertFalse(report["scope"]["human_speakers"])

    def test_borderline_negatives_are_reported_below_threshold(self) -> None:
        entries = self._entries([("negative", "aiden", "자비스 봇이야")])
        frontend = ScriptedFrontend([WAKE_THRESHOLD - 0.046])
        report = evaluations.evaluate(entries, frontend, loader=lambda path: FakeFrames.of(1))
        borderline = report["summary"]["borderline_negatives"]
        self.assertEqual(len(borderline), 1)
        self.assertEqual(borderline[0]["text"], "자비스 봇이야")
        self.assertEqual(report["summary"]["negative_false_accepts"], 0)

    def test_loader_failure_is_not_swallowed(self) -> None:
        entries = self._entries([("positive", "aiden", "헤이 자비스")])

        def broken(path: Path) -> FakeFrames:
            raise evaluations.WakeEvaluationError("wake_eval_wav_format_invalid")

        with self.assertRaises(evaluations.WakeEvaluationError):
            evaluations.evaluate(entries, ScriptedFrontend([1.0]), loader=broken)


@unittest.skipUnless(HAS_NUMPY, "numpy is required for the audio-quality guards")
class AudioQualityGuardTests(unittest.TestCase):
    def _write(self, path: Path, samples, rate: int = 16000, channels: int = 1, width: int = 2) -> Path:
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(channels)
            handle.setsampwidth(width)
            handle.setframerate(rate)
            handle.writeframes(samples)
        return path

    def test_silence_is_refused_as_a_synthesis_failure(self) -> None:
        quiet = evaluations.clip_quality(np.zeros(16000, dtype=np.int16))
        self.assertFalse(quiet["usable"])
        self.assertEqual(quiet["reason"], "clip_too_quiet")

    def test_short_clip_is_refused(self) -> None:
        short = evaluations.clip_quality((np.ones(1600, dtype=np.int16) * 4000))
        self.assertFalse(short["usable"])
        self.assertEqual(short["reason"], "clip_too_short")

    def test_empty_clip_is_refused_before_the_frame_loop(self) -> None:
        empty = evaluations.clip_quality(np.zeros(0, dtype=np.int16))
        self.assertFalse(empty["usable"])
        self.assertEqual(empty["reason"], "clip_shorter_than_one_frame")

    def test_render_inside_the_speech_band_is_accepted(self) -> None:
        good = evaluations.clip_quality(np.ones(16000, dtype=np.int16) * 3000)
        self.assertTrue(good["usable"])
        self.assertEqual(good["reason"], "")
        self.assertEqual(good["frames"], 50)

    def test_8khz_wav_is_resampled_to_the_16khz_frame_contract(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            source = Path(scratch) / "eight.wav"
            self._write(source, (np.ones(8000, dtype=np.int16) * 4000).tobytes(), rate=8000)
            samples = evaluations.load_wav_mono_pcm16(source)
        self.assertEqual(samples.size, 16000)
        self.assertEqual(samples.dtype, np.int16)

    def test_stereo_wav_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            source = Path(scratch) / "stereo.wav"
            self._write(source, (np.ones(2000, dtype=np.int16) * 4000).tobytes(), channels=2)
            with self.assertRaises(evaluations.WakeEvaluationError):
                evaluations.load_wav_mono_pcm16(source)

    def test_build_corpus_refuses_a_silent_render(self) -> None:
        def silent(text: str, voice: str, output: Path, *, rate_mpr: int | None = None) -> Path:
            evaluations.write_wav_mono_pcm16(output, np.zeros(16000, dtype=np.int16))
            return output

        with tempfile.TemporaryDirectory() as scratch:
            with self.assertRaises(evaluations.WakeEvaluationError) as caught:
                evaluations.build_corpus(Path(scratch), ["aiden"], (), synth=silent)
        self.assertIn("wake_eval_clip_unusable", str(caught.exception))

    def test_build_corpus_records_unusable_renders_when_explicitly_allowed(self) -> None:
        def silent(text: str, voice: str, output: Path, *, rate_mpr: int | None = None) -> Path:
            evaluations.write_wav_mono_pcm16(output, np.zeros(16000, dtype=np.int16))
            return output

        with tempfile.TemporaryDirectory() as scratch:
            entries, unusable = evaluations.build_corpus(
                Path(scratch), ["aiden"], (), synth=silent, allow_unusable=True
            )
        negatives = [row for row in entries if row["kind"] == "negative"]
        self.assertEqual(negatives, [])
        positives = [row for row in entries if row["kind"] == "positive"]
        self.assertEqual(positives, [])
        self.assertEqual(len(unusable), 1 + len(evaluations.NEGATIVE_PHRASES))
        self.assertTrue(all(row["quality"]["reason"] == "clip_too_quiet" for row in unusable))

    def test_say_backend_render_is_byte_exact_16k_mono(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            target = Path(scratch) / "clip.wav"
            evaluations.write_wav_mono_pcm16(target, (np.ones(16000, dtype=np.int16) * 3000))
            with wave.open(str(target), "rb") as handle:
                self.assertEqual((handle.getnchannels(), handle.getsampwidth(), handle.getframerate()), (1, 2, 16000))
            samples = evaluations.load_wav_mono_pcm16(target)
        self.assertEqual(samples.size, 16000)


if __name__ == "__main__":
    unittest.main()

