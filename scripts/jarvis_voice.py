"""Local-only Jarvis voice pipeline.

Runtime order: openWakeWord -> VAD -> mlx-whisper -> Flash-Next -> Qwen3-TTS.
The module stores only a small status envelope; raw microphone audio remains in
a bounded in-memory ring and is never written to disk by default.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import threading
import time
import urllib.request
from array import array
from collections import deque
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from auto_reply_ondevice import FLASH_NEXT_MODEL_ID
from jarvis_abort import AbortToken, JarvisCancelled


WAKE_PHRASE = "헤이 자비스"
WAKE_THRESHOLD = 0.65
CUSTOM_WAKE_MODEL_MAX_BYTES = 64 * 1024 * 1024
BUNDLED_CUSTOM_WAKE_MODEL = Path(__file__).resolve().parents[1] / "voice" / "models" / "hey_jarvis_ko_ridge.onnx"
VOICE_STATUS_NAME = "jarvis-voice-status.json"
VOICE_STATUS_SCHEMA_VERSION = 1
WHISPER_MODEL_ID = "mlx-community/whisper-large-v3-turbo"
QWEN3_TTS_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
QWEN3_TTS_PRECISION = "bf16"
VOICE_PERSONA_PROMPT = (
    "당신은 건조하고 절제된 영국식 집사 말투의 Jarvis다. "
    "항상 한국어로 짧고 정확하게 답한다. 과장된 감탄이나 아첨은 하지 않는다. "
    "이 말투는 음성 대화에만 적용된다."
)


class VoiceState(str, Enum):
    IDLE = "idle"
    WAKE_LISTEN = "wake_listen"
    USER_LISTEN = "user_listen"
    TRANSCRIBING = "transcribing"
    GENERATING = "generating"
    SPEAKING = "speaking"
    ENDED = "ended"
    ERROR = "error"
    ABORTED = "aborted"


class SttAdapter(Protocol):
    def transcribe(self, pcm16: bytes, sample_rate: int, token: AbortToken) -> str: ...


class LlmAdapter(Protocol):
    def generate(self, text: str, token: AbortToken) -> str: ...


class TtsAdapter(Protocol):
    def speak(self, text: str, token: AbortToken) -> None: ...


@dataclass(frozen=True)
class AudioFrameAnalysis:
    rms: float
    speech: bool
    stock_wake_score: float
    custom_wake_score: float | None = None


@dataclass(frozen=True)
class VoiceResult:
    state: VoiceState
    error_code: str = ""
    transcript: str = ""
    reply: str = ""


class BoundedAudioRing:
    def __init__(self, *, sample_rate: int = 16_000, seconds: float = 12.0, sample_width: int = 2):
        self.sample_rate = sample_rate
        self.sample_width = sample_width
        self.max_bytes = max(1, int(sample_rate * seconds * sample_width))
        self._chunks: deque[bytes] = deque()
        self._size = 0

    def append(self, chunk: bytes) -> None:
        if not chunk:
            return
        if len(chunk) >= self.max_bytes:
            self._chunks.clear()
            clipped = bytes(chunk[-self.max_bytes :])
            self._chunks.append(clipped)
            self._size = len(clipped)
            return
        self._chunks.append(bytes(chunk))
        self._size += len(chunk)
        while self._size > self.max_bytes and self._chunks:
            removed = self._chunks.popleft()
            self._size -= len(removed)

    def clear(self) -> None:
        self._chunks.clear()
        self._size = 0

    def bytes(self) -> bytes:
        return b"".join(self._chunks)

    @property
    def size(self) -> int:
        return self._size


class WakePhraseGate:
    """Fixed-threshold stock/custom wake gate.

    If the stock model misses Korean pronunciation, callers may supply a
    bounded custom-model score. The threshold is never lowered to compensate.
    """

    def __init__(self, threshold: float = WAKE_THRESHOLD):
        self.threshold = max(WAKE_THRESHOLD, min(float(threshold), 0.95))

    def accepts(self, stock_score: float, custom_score: float | None = None) -> bool:
        stock = max(0.0, min(float(stock_score), 1.0))
        custom = 0.0 if custom_score is None else max(0.0, min(float(custom_score), 1.0))
        return max(stock, custom) >= self.threshold


class OpenWakeVadFrontend:
    """openWakeWord + WebRTC VAD frontend with a fixed wake threshold.

    A custom Korean-pronunciation model is opt-in and size bounded. Runtime
    verification decides whether it is needed; the stock threshold is never
    lowered to force acceptance.
    """

    def __init__(
        self,
        *,
        sample_rate: int = 16_000,
        custom_wake_model: Path | None = None,
        stock_model: Any | None = None,
        custom_model: Any | None = None,
        vad: Any | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        if sample_rate != 16_000:
            raise ValueError("voice_sample_rate_unsupported")
        self.stock_model = stock_model or self._make_wake_model(None)
        self.custom_model = custom_model
        if custom_wake_model is not None:
            self.custom_model = custom_model or self._make_wake_model(custom_wake_model)
        if vad is None:
            import webrtcvad

            vad = webrtcvad.Vad(2)
        self.vad = vad

    @staticmethod
    def _validate_custom_wake_model_path(custom_path: Path) -> tuple[Path, str]:
        path = Path(custom_path).expanduser()
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise RuntimeError("custom_wake_model_invalid") from exc
        if (
            not path.is_file()
            or path.is_symlink()
            or path.suffix.casefold() not in {".onnx", ".tflite"}
            or size <= 0
            or size > CUSTOM_WAKE_MODEL_MAX_BYTES
        ):
            raise RuntimeError("custom_wake_model_invalid")
        framework = "onnx" if path.suffix.casefold() == ".onnx" else "tflite"
        return path, framework

    @staticmethod
    def _make_wake_model(custom_path: Path | None) -> Any:
        from openwakeword.model import Model

        if custom_path is None:
            return Model(inference_framework="onnx")
        path, framework = OpenWakeVadFrontend._validate_custom_wake_model_path(custom_path)
        return Model(wakeword_models=[str(path)], inference_framework=framework)

    @staticmethod
    def _score(prediction: object) -> float:
        if not isinstance(prediction, dict):
            return 0.0
        scores = []
        for key, value in prediction.items():
            if "jarvis" not in str(key).casefold():
                continue
            try:
                scores.append(float(value))
            except (TypeError, ValueError):
                continue
        return max(scores, default=0.0)

    @staticmethod
    def _rms(pcm16: bytes) -> float:
        if not pcm16:
            return 0.0
        samples = array("h")
        samples.frombytes(pcm16[: len(pcm16) - (len(pcm16) % 2)])
        if not samples:
            return 0.0
        mean_square = sum(float(sample) * float(sample) for sample in samples) / len(samples)
        return min(1.0, math.sqrt(mean_square) / 32768.0)

    @staticmethod
    def _wake_input(pcm16: bytes) -> Any:
        # openWakeWord requires a numpy int16 array; WebRTC VAD still wants bytes.
        try:
            import numpy as np

            return np.frombuffer(pcm16, dtype=np.int16, count=320)
        except Exception:
            samples = array("h")
            samples.frombytes(pcm16)
            return samples

    def analyze(self, pcm16: bytes) -> AudioFrameAnalysis:
        # WebRTC VAD accepts 10/20/30 ms frames. A 20 ms frame at 16 kHz is
        # 320 int16 samples / 640 bytes; callers must keep this boundary.
        if len(pcm16) != 640:
            raise ValueError("voice_frame_size_invalid")
        wake_input = self._wake_input(pcm16)
        stock = self._score(self.stock_model.predict(wake_input))
        custom = (
            self._score(self.custom_model.predict(wake_input))
            if self.custom_model is not None
            else None
        )
        return AudioFrameAnalysis(
            rms=self._rms(pcm16),
            speech=bool(self.vad.is_speech(pcm16, self.sample_rate)),
            stock_wake_score=stock,
            custom_wake_score=custom,
        )


def resolve_custom_wake_model(explicit: Path | None = None) -> Path | None:
    """Return a validated custom-model path, or None when only stock is available.

    An explicit path fails closed. The bundled Korean head is selected when it
    validates; a missing/invalid bundle does not lower the wake threshold.
    """

    if explicit is not None:
        path, _framework = OpenWakeVadFrontend._validate_custom_wake_model_path(explicit)
        return path
    try:
        path, _framework = OpenWakeVadFrontend._validate_custom_wake_model_path(BUNDLED_CUSTOM_WAKE_MODEL)
        return path
    except RuntimeError:
        return None


class VoiceStatusStore:
    def __init__(self, state_root: Path):
        self.path = state_root / VOICE_STATUS_NAME
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    def write(
        self,
        *,
        state: VoiceState,
        rms: float,
        error_code: str = "",
        wake_source: str = "stock",
        custom_model_selected: bool = False,
    ) -> None:
        payload = {
            "schema_version": VOICE_STATUS_SCHEMA_VERSION,
            "state": state.value,
            "rms": round(max(0.0, min(float(rms), 1.0)), 4),
            "error_code": str(error_code or "")[:96],
            "wake_phrase": WAKE_PHRASE,
            "wake_source": wake_source if wake_source in {"stock", "custom", "none"} else "none",
            "threshold": WAKE_THRESHOLD,
            "custom_model_selected": bool(custom_model_selected),
            "updated_at": int(time.time()),
        }
        temp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            os.replace(temp, self.path)
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass


class JarvisVoicePipeline:
    def __init__(
        self,
        *,
        stt: SttAdapter,
        llm: LlmAdapter,
        tts: TtsAdapter,
        token: AbortToken,
        status: VoiceStatusStore | None = None,
        sample_rate: int = 16_000,
        ring_seconds: float = 12.0,
    ) -> None:
        self.stt = stt
        self.llm = llm
        self.tts = tts
        self.token = token
        self.status = status
        self.sample_rate = sample_rate
        self.ring = BoundedAudioRing(sample_rate=sample_rate, seconds=ring_seconds)
        self.wake_gate = WakePhraseGate()
        self.state = VoiceState.WAKE_LISTEN
        self.last_rms = 0.0
        self._wake_source = "none"
        self._custom_model_selected = False
        self._transcript = ""
        self._reply = ""
        self._speech_frames = 0
        self._silence_frames = 0
        self._noise_frames = 0
        self._lock = threading.Lock()
        self._publish()

    def _publish(self, error_code: str = "") -> None:
        if self.status is not None:
            self.status.write(
                state=self.state,
                rms=self.last_rms,
                error_code=error_code,
                wake_source=self._wake_source,
                custom_model_selected=self._custom_model_selected,
            )

    def _end(self, state: VoiceState, error_code: str) -> VoiceResult:
        self.state = state
        self.ring.clear()
        self._publish(error_code)
        return VoiceResult(state=state, error_code=error_code, transcript=getattr(self, "_transcript", ""), reply=getattr(self, "_reply", ""))

    def mic_disconnected(self) -> VoiceResult:
        return self._end(VoiceState.ERROR, "mic_disconnected")

    def model_swap_failed(self) -> VoiceResult:
        return self._end(VoiceState.ERROR, "model_swap_failed")

    def feed_audio(
        self,
        pcm16: bytes,
        *,
        rms: float,
        speech: bool,
        stock_wake_score: float = 0.0,
        custom_wake_score: float | None = None,
    ) -> VoiceResult | None:
        with self._lock:
            self.last_rms = max(0.0, min(float(rms), 1.0))
            if self.token.is_cancelled():
                return self._end(VoiceState.ABORTED, "global_abort")
            if self.state == VoiceState.SPEAKING:
                # Playback mute/ignore-while-speaking blocks TTS from feeding
                # the wake detector or user-speech ring.
                self._publish()
                return None
            if self.state == VoiceState.WAKE_LISTEN:
                accepted = self.wake_gate.accepts(stock_wake_score, custom_wake_score)
                if not accepted:
                    self._publish()
                    return None
                self._wake_source = (
                    "stock" if stock_wake_score >= self.wake_gate.threshold else "custom"
                )
                self.state = VoiceState.USER_LISTEN
                self.ring.clear()
                self._speech_frames = self._silence_frames = self._noise_frames = 0
                self._publish()
                return None
            if self.state != VoiceState.USER_LISTEN:
                return None

            if speech:
                self.ring.append(pcm16)
                self._speech_frames += 1
                self._silence_frames = 0
                self._noise_frames = 0
            else:
                self._silence_frames += 1
                if self.last_rms > 0.35:
                    self._noise_frames += 1

            if self._noise_frames >= 25 and self._speech_frames == 0:
                return self._end(VoiceState.ERROR, "noise_rejected")
            if self._silence_frames >= 50 and self._speech_frames == 0:
                return self._end(VoiceState.ENDED, "silence_timeout")
            if self._speech_frames > 0 and self._silence_frames >= 12:
                audio = self.ring.bytes()
                return self.process_utterance(audio)
            self._publish()
            return None

    def feed_frontend_frame(
        self,
        frontend: OpenWakeVadFrontend,
        pcm16: bytes,
    ) -> VoiceResult | None:
        analysis = frontend.analyze(pcm16)
        return self.feed_audio(
            pcm16,
            rms=analysis.rms,
            speech=analysis.speech,
            stock_wake_score=analysis.stock_wake_score,
            custom_wake_score=analysis.custom_wake_score,
        )

    def process_utterance(self, pcm16: bytes) -> VoiceResult:
        try:
            self.token.raise_if_cancelled()
            if not pcm16:
                return self._end(VoiceState.ENDED, "silence_timeout")
            self.state = VoiceState.TRANSCRIBING
            self._publish()
            transcript = self.stt.transcribe(pcm16, self.sample_rate, self.token).strip()
            self.token.raise_if_cancelled()
            self._transcript = transcript
            if not transcript:
                return self._end(VoiceState.ERROR, "stt_empty")
        except JarvisCancelled:
            return self._end(VoiceState.ABORTED, "global_abort")
        except Exception:
            return self._end(VoiceState.ERROR, "stt_error")

        try:
            self.state = VoiceState.GENERATING
            self._publish()
            reply = self.llm.generate(transcript, self.token).strip()
            self.token.raise_if_cancelled()
            if not reply:
                return self._end(VoiceState.ERROR, "generation_error")
        except JarvisCancelled:
            return self._end(VoiceState.ABORTED, "global_abort")
        except Exception as exc:
            code = "model_swap_failed" if "model_swap" in str(exc).casefold() else "generation_error"
            return self._end(VoiceState.ERROR, code)

        try:
            self.state = VoiceState.SPEAKING
            self._publish()
            self.tts.speak(reply, self.token)
            self.token.raise_if_cancelled()
        except JarvisCancelled:
            return self._end(VoiceState.ABORTED, "global_abort")
        except Exception:
            return self._end(VoiceState.ERROR, "tts_error")

        self.state = VoiceState.ENDED
        self.ring.clear()
        self._publish()
        return VoiceResult(VoiceState.ENDED, transcript=transcript, reply=reply)


class LocalMlxLlm:
    def __init__(self, base_url: str = "http://127.0.0.1:11234/v1", model: str = FLASH_NEXT_MODEL_ID):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def generate(self, text: str, token: AbortToken) -> str:
        token.raise_if_cancelled()
        if self.model != FLASH_NEXT_MODEL_ID:
            raise RuntimeError("model_swap_required")
        payload = json.dumps(
            {
                "model": self.model.removeprefix("mlx/"),
                "messages": [
                    {"role": "system", "content": VOICE_PERSONA_PROMPT},
                    {"role": "user", "content": text[:4000]},
                ],
                "temperature": 0.3,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=90.0) as response:
            body = json.loads(response.read().decode("utf-8", "replace"))
        token.raise_if_cancelled()
        return str(body["choices"][0]["message"]["content"])


class MlxWhisperAdapter:
    def __init__(self, model: str = WHISPER_MODEL_ID):
        self.model = model

    def transcribe(self, pcm16: bytes, sample_rate: int, token: AbortToken) -> str:
        token.raise_if_cancelled()
        import numpy as np
        import mlx_whisper

        samples = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        if sample_rate != 16_000:
            raise RuntimeError("voice_sample_rate_unsupported")
        result: dict[str, Any] = mlx_whisper.transcribe(
            samples,
            path_or_hf_repo=self.model,
            language="ko",
        )
        token.raise_if_cancelled()
        return str(result.get("text") or "")


class Qwen3TtsAdapter:
    def __init__(self, model: str = QWEN3_TTS_MODEL_ID):
        self.model = model
        self._engine: Any | None = None

    def _load(self) -> Any:
        if self._engine is None:
            from qwen_tts import Qwen3TTSModel
            import torch

            dtype = torch.bfloat16 if QWEN3_TTS_PRECISION == "bf16" else torch.float16
            self._engine = Qwen3TTSModel.from_pretrained(self.model, dtype=dtype)
        return self._engine

    def synthesize(self, text: str, token: AbortToken) -> tuple[Any, int]:
        token.raise_if_cancelled()
        engine = self._load()
        speakers = engine.get_supported_speakers() or []
        speaker = speakers[0] if speakers else "ryan"
        wavs, sample_rate = engine.generate_custom_voice(
            text=text,
            speaker=speaker,
            language="Korean",
        )
        audio = wavs[0] if isinstance(wavs, list) else wavs
        token.raise_if_cancelled()
        return audio, int(sample_rate)

    def write_wav(self, text: str, path: Path, token: AbortToken) -> dict[str, Any]:
        import numpy as np
        import wave

        audio, sample_rate = self.synthesize(text, token)
        if hasattr(audio, "detach"):
            audio = audio.detach().cpu().numpy()
        samples = np.asarray(audio).squeeze().astype(np.float32)
        peak = float(np.max(np.abs(samples))) if samples.size else 0.0
        if peak > 1.0:
            samples = samples / peak
        pcm = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16)
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            handle.writeframes(pcm.tobytes())
        return {"path": str(path), "bytes": path.stat().st_size, "sample_rate": sample_rate, "nframes": int(pcm.size)}

    def speak(self, text: str, token: AbortToken) -> None:
        token.raise_if_cancelled()
        import sounddevice as sd

        audio, sample_rate = self.synthesize(text, token)
        token.raise_if_cancelled()
        sd.play(audio, int(sample_rate), blocking=False)
        try:
            while sd.get_stream().active:
                if token.is_cancelled():
                    sd.stop()
                    raise JarvisCancelled("jarvis_global_abort")
                time.sleep(0.02)
        finally:
            if token.is_cancelled():
                sd.stop()


def assert_isolated_voice_environment() -> None:
    """Fail closed unless launched from the dedicated voice environment."""

    if os.environ.get("OPENKAKAO_VOICE_ENV") != "1":
        raise RuntimeError("voice_environment_not_isolated")


def run_microphone_session(
    *,
    state_root: Path,
    custom_wake_model: Path | None = None,
) -> VoiceResult:
    """Run one wake -> reply session from a dedicated 16 kHz microphone stream."""

    assert_isolated_voice_environment()
    from jarvis_abort import AbortController
    import sounddevice as sd

    controller = AbortController(state_root)
    token = controller.token()
    status = VoiceStatusStore(state_root)
    pipeline = JarvisVoicePipeline(
        stt=MlxWhisperAdapter(),
        llm=LocalMlxLlm(),
        tts=Qwen3TtsAdapter(),
        token=token,
        status=status,
    )
    if token.is_cancelled():
        return pipeline._end(VoiceState.ABORTED, "global_abort")

    try:
        selected = resolve_custom_wake_model(custom_wake_model)
        pipeline._custom_model_selected = selected is not None
        pipeline._publish()
        frontend = OpenWakeVadFrontend(custom_wake_model=selected)
        # Blocking reads keep capture bounded to one 20 ms frame. When STT/LLM/TTS
        # runs, the stream is not read; the session returns immediately after TTS,
        # so playback cannot become a fresh wake/user utterance.
        with sd.RawInputStream(
            samplerate=16_000,
            channels=1,
            dtype="int16",
            blocksize=320,
        ) as stream:
            while True:
                if token.is_cancelled():
                    return pipeline._end(VoiceState.ABORTED, "global_abort")
                raw, overflowed = stream.read(320)
                if overflowed:
                    pipeline._noise_frames += 1
                result = pipeline.feed_frontend_frame(frontend, bytes(raw))
                if result is not None:
                    return result
    except JarvisCancelled:
        return pipeline._end(VoiceState.ABORTED, "global_abort")
    except Exception:
        return pipeline.mic_disconnected()


def _default_state_root() -> Path:
    override = os.environ.get("OPENKAKAO_STATE_ROOT")
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library/Application Support/openkakao/auto-reply"


def _load_wav_pcm16(path: Path, sample_rate: int = 16_000) -> bytes:
    import numpy as np
    import wave

    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    if width != 2:
        raise ValueError("voice_wav_sample_width_invalid")
    samples = np.frombuffer(raw, dtype=np.int16)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1).astype(np.int16)
    if rate != sample_rate:
        n_out = int(round(len(samples) * sample_rate / rate))
        if n_out <= 1 or len(samples) <= 1:
            samples = np.zeros(max(n_out, 0), dtype=np.int16)
        else:
            x_src = np.linspace(0.0, 1.0, num=len(samples), endpoint=False)
            x_dst = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
            samples = np.clip(np.interp(x_dst, x_src, samples.astype(np.float32)), -32768, 32767).astype(np.int16)
    return samples.tobytes()


def run_file_pipeline(
    *,
    state_root: Path,
    wake_wav: Path,
    utterance_wav: Path,
    tts_out: Path,
    custom_wake_model: Path | None = None,
) -> dict[str, Any]:
    """Wake from a file, then STT -> local LLM -> TTS file. Never opens the mic."""

    assert_isolated_voice_environment()
    from jarvis_abort import AbortController

    selected = resolve_custom_wake_model(custom_wake_model)
    controller = AbortController(state_root)
    token = controller.token()
    status = VoiceStatusStore(state_root)
    engine = Qwen3TtsAdapter()

    class _FileTts:
        def __init__(self) -> None:
            self.meta: dict[str, Any] = {}

        def speak(self, text: str, speak_token: AbortToken) -> None:
            self.meta = engine.write_wav(text, tts_out, speak_token)

    tts = _FileTts()
    pipeline = JarvisVoicePipeline(
        stt=MlxWhisperAdapter(model=os.environ.get("OPENKAKAO_WHISPER_MODEL", WHISPER_MODEL_ID)),
        llm=LocalMlxLlm(),
        tts=tts,
        token=token,
        status=status,
    )
    pipeline._custom_model_selected = selected is not None
    pipeline._publish()
    frontend = OpenWakeVadFrontend(custom_wake_model=selected)
    wake_pcm = _load_wav_pcm16(wake_wav)
    accepted = False
    stock_max = 0.0
    custom_max = 0.0
    for offset in range(0, len(wake_pcm) - 639, 640):
        analysis = frontend.analyze(wake_pcm[offset : offset + 640])
        stock_max = max(stock_max, analysis.stock_wake_score)
        if analysis.custom_wake_score is not None:
            custom_max = max(custom_max, analysis.custom_wake_score)
        result = pipeline.feed_audio(
            wake_pcm[offset : offset + 640],
            rms=analysis.rms,
            speech=analysis.speech,
            stock_wake_score=analysis.stock_wake_score,
            custom_wake_score=analysis.custom_wake_score,
        )
        if pipeline.state == VoiceState.USER_LISTEN:
            accepted = True
            break
        if result is not None:
            return {
                "accepted": False,
                "state": result.state.value,
                "error_code": result.error_code,
                "stock_max": stock_max,
                "custom_max": custom_max,
                "custom_model_selected": selected is not None,
            }
    if not accepted:
        ended = pipeline._end(VoiceState.ENDED, "wake_miss")
        return {
            "accepted": False,
            "state": ended.state.value,
            "error_code": ended.error_code,
            "stock_max": stock_max,
            "custom_max": custom_max,
            "custom_model_selected": selected is not None,
        }

    utterance = _load_wav_pcm16(utterance_wav)
    voice = pipeline.process_utterance(utterance)
    return {
        "accepted": True,
        "state": voice.state.value,
        "error_code": voice.error_code,
        "transcript": voice.transcript,
        "reply": voice.reply,
        "stock_max": stock_max,
        "custom_max": custom_max,
        "custom_model_selected": selected is not None,
        "threshold": WAKE_THRESHOLD,
        "tts": tts.meta,
        "wake_source": pipeline._wake_source,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one local Jarvis voice session.")
    parser.add_argument("--state-root", type=Path, default=None)
    parser.add_argument("--custom-wake-model", type=Path, default=None)
    parser.add_argument("--file-wake", type=Path, default=None)
    parser.add_argument("--file-utterance", type=Path, default=None)
    parser.add_argument("--tts-out", type=Path, default=None)
    args = parser.parse_args(argv)
    state_root = (args.state_root or _default_state_root()).expanduser()
    custom = args.custom_wake_model.expanduser() if args.custom_wake_model else None
    if args.file_wake is not None or args.file_utterance is not None or args.tts_out is not None:
        if args.file_wake is None or args.file_utterance is None or args.tts_out is None:
            raise SystemExit("file_pipeline_args_incomplete")
        report = run_file_pipeline(
            state_root=state_root,
            wake_wav=args.file_wake.expanduser(),
            utterance_wav=args.file_utterance.expanduser(),
            tts_out=args.tts_out.expanduser(),
            custom_wake_model=custom,
        )
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report.get("accepted") and report.get("state") == "ended" else 1
    result = run_microphone_session(state_root=state_root, custom_wake_model=custom)
    print(json.dumps({"state": result.state.value, "error_code": result.error_code}, ensure_ascii=False))
    return 0 if result.state in {VoiceState.ENDED, VoiceState.ABORTED} else 1


if __name__ == "__main__":
    sys.exit(main())
