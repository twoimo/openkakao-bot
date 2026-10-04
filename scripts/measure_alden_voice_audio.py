"""Bounded native microphone/playback evidence; no model load, transcript or raw-audio persistence."""
from __future__ import annotations

import argparse
import hashlib
import json
import threading
import time
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

from alden_abort import AbortController, AldenCancelled
from alden_voice import AldenVoicePipeline, MacVoiceAudio, OpenWakeVadFrontend, VoiceState, _MicrophoneFramePoller


def measure(wav: Path, *, cancel_after: float | None = None, barge_in: bool = False) -> dict:
    import numpy as np
    import webrtcvad

    with wave.open(str(wav), "rb") as source:
        if source.getframerate() != 24_000 or source.getnchannels() != 1 or source.getsampwidth() != 2:
            raise ValueError("expected bounded 24kHz mono PCM16 reference")
        if not 0 < source.getnframes() <= 24_000 * 10:
            raise ValueError("reference must be at most ten seconds")
        samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(np.float32) / 32768
    samples = np.clip(samples, -.25, .25)
    if barge_in:
        # Eight seconds give the participating speaker time to interrupt.
        samples = np.tile(samples, int(np.ceil(8 * 24_000 / len(samples))))[:8 * 24_000]
    vad = webrtcvad.Vad(2)
    observations, errors = [], []
    stop_at = completed_at = None
    old_turn = None
    pipeline = None
    with TemporaryDirectory(prefix="alden-audio-owned-") as temp:
        token = AbortController(Path(temp)).token()
        started = time.monotonic()
        with MacVoiceAudio() as audio:
            ready = time.monotonic()
            poller = _MicrophoneFramePoller(audio, stale_seconds=3)
            worker = None

            def play(playback_token=token):
                nonlocal completed_at
                try: audio.play(samples, 24_000, playback_token)
                except AldenCancelled: pass
                except Exception as error: errors.append(type(error).__name__ + ":" + str(error))
                finally: completed_at = time.monotonic()

            try:
                if barge_in:
                    class RecordedReference:
                        def speak(self, text, playback_token):
                            play(playback_token)

                    class NoInference:
                        def generate(self, *_args, **_kwargs):
                            return "오디오 연결 시험"

                    pipeline = AldenVoicePipeline(stt=object(), llm=NoInference(), tts=RecordedReference(),
                        token=token, echo_processed_microphone=audio.echo_processed)
                    pipeline.submit_text("오디오 연결 시험")
                    old_turn = pipeline._active_turn
                    worker = pipeline._worker
                else:
                    worker = threading.Thread(target=play, name="alden-audio-probe", daemon=True)
                    worker.start()
                deadline = ready + len(samples) / 24_000 + 1
                while time.monotonic() < deadline:
                    now = time.monotonic()
                    if cancel_after is not None and stop_at is None and now - ready >= cancel_after:
                        stop_at = now
                        token.cancel()
                    frame = poller.poll()
                    if frame:
                        pcm, overflow = frame
                        rms, speech = OpenWakeVadFrontend._rms(pcm), vad.is_speech(pcm, 16_000)
                        observations.append((rms, speech, overflow))
                        if overflow:
                            raise RuntimeError("microphone audio gap")
                        if pipeline is not None and pipeline.state == VoiceState.SPEAKING and completed_at is None:
                            detecting_at = time.monotonic()
                            pipeline.feed_audio(pcm, rms=rms, speech=speech, asynchronous=True)
                            if old_turn.token.is_cancelled() and stop_at is None:
                                stop_at = detecting_at
                    if completed_at is not None and now - completed_at > .3:
                        break
                    time.sleep(.01)
                processed = audio.echo_processed
            finally:
                # Unwind the owned worker before destroying its native audio handle,
                # including microphone-gap and setup failures.
                token.cancel()
                if pipeline is not None:
                    pipeline.close()
                if worker is not None:
                    worker.join(.5)
                    if worker.is_alive():
                        raise RuntimeError("owned playback did not unwind")
    return {
        "schema": "alden-native-audio-playback-v1", "ok": not errors,
        "startup_seconds": ready - started, "duration_seconds": time.monotonic() - ready,
        "reference_sha256": hashlib.sha256(wav.read_bytes()).hexdigest(),
        "reference_seconds": len(samples) / 24_000, "frames": len(observations),
        "rms_max": max((row[0] for row in observations), default=None),
        "vad_speech_frames": sum(int(row[1]) for row in observations),
        "overflow_frames": sum(int(row[2]) for row in observations), "echo_processed": processed,
        "cancel_after_seconds": cancel_after,
        "hardware_barge_in_requested": barge_in,
        "hardware_barge_in_detected": barge_in and stop_at is not None,
        "cancel_waiter_ms": None if stop_at is None or completed_at is None else (completed_at - stop_at) * 1000,
        "errors": errors, "raw_audio_saved": False, "model_calls": 0,
        "limits": ["Synthetic recorded reference, not current STT/LLM/TTS generation",
                   "Near-end speech and intelligibility remain unverified by VAD alone or zero input",
                   "Waiter cancellation is not an acoustic speaker-tail latency measurement"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wav", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cancel-after", type=float)
    parser.add_argument("--barge-in", action="store_true")
    args = parser.parse_args()
    if args.cancel_after is not None and not .05 <= args.cancel_after <= 5:
        parser.error("cancel-after must be between 0.05 and 5 seconds")
    if args.barge_in and args.cancel_after is not None:
        parser.error("barge-in and timed cancellation are separate trials")
    report = measure(args.wav, cancel_after=args.cancel_after, barge_in=args.barge_in)
    report["source_sha256"] = {
        str(relative): hashlib.sha256((Path(__file__).resolve().parents[1] / relative).read_bytes()).hexdigest()
        for relative in ["scripts/alden_voice.py", "scripts/libalden_audio.dylib", "voice/native/alden_audio.swift"]
    }
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__": main()
