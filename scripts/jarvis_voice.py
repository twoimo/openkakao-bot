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
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from array import array
from collections.abc import Mapping, Sequence
from collections import deque
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from auto_reply_ondevice import FLASH_NEXT_MODEL_ID
from jarvis_abort import AbortToken, JarvisCancelled
from local_mlx_gateway import (
    MlxRequestAdmissionClosed,
    mlx_model_request_lease,
    mlx_response_model_conflicts,
)


WAKE_PHRASE = "헤이 자비스"
WAKE_THRESHOLD = 0.65
CUSTOM_WAKE_MODEL_MAX_BYTES = 64 * 1024 * 1024
BUNDLED_CUSTOM_WAKE_MODEL = Path(__file__).resolve().parents[1] / "voice" / "models" / "hey_jarvis_ko_ridge.onnx"
VOICE_STATUS_NAME = "jarvis-voice-status.json"
VOICE_STATUS_SCHEMA_VERSION = 1
WHISPER_MODEL_ID = "mlx-community/whisper-large-v3-turbo"
QWEN3_TTS_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
QWEN3_TTS_PRECISION = "bf16"
LOCAL_LLM_BASE_URL = "http://127.0.0.1:11234/v1"
LOCAL_LLM_MAX_RESPONSE_BYTES = 64 * 1024
VOICE_CONTEXT_TURNS = 4
VOICE_CONTEXT_ITEM_MAX_CHARS = 600
VOICE_CONTEXT_TTL_SECONDS = 10 * 60
VOICE_WAKE_RESUME_DELAY_SECONDS = 0.5
VOICE_STATUS_HEARTBEAT_SECONDS = 5.0
VOICE_MIC_POLL_SECONDS = 0.02
VOICE_MIC_STALE_SECONDS = 15.0
VOICE_MIC_FRAME_SAMPLES = 320
# The last bounded host run began with 1.65 GiB of swap headroom and crossed
# the 512 MiB emergency stop while a voice model stage was still running.
# Keep 2 GiB free before starting either large local voice model, plus enough
# reclaimable RAM for the model and its temporary inference buffers.
VOICE_MIN_SWAP_FREE_BYTES = 2 * 1024**3
VOICE_STT_MIN_RECLAIMABLE_BYTES = 8 * 1024**3
VOICE_TTS_MIN_RECLAIMABLE_BYTES = 10 * 1024**3
VOICE_PERSONA_PROMPT = (
    "당신은 건조하고 절제된 영국식 집사 말투의 Jarvis다. "
    "항상 한국어로 짧고 정확하게 답한다. 과장된 감탄이나 아첨은 하지 않는다. "
    "스스로 질문을 만든 뒤 답하지 않는다. 필요한 정보가 빠져 행동할 수 없을 때만 짧게 되묻고, "
    "그 외에는 답을 마친 뒤 대화를 억지로 이어가는 질문 없이 턴을 끝낸다. "
    "이 말투는 음성 대화에만 적용된다."
)


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


@dataclass(frozen=True)
class VoiceMemoryBudget:
    reclaimable_bytes: int
    swap_free_bytes: int


class VoiceMemoryBudgetError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _read_voice_memory_budget() -> VoiceMemoryBudget:
    """Read reclaimable macOS pages and swap headroom without network access."""

    if sys.platform != "darwin":
        raise VoiceMemoryBudgetError("voice_memory_budget_unavailable")
    try:
        vm = subprocess.run(
            ["/usr/bin/vm_stat"], capture_output=True, text=True, timeout=2.0, check=False
        )
        swap = subprocess.run(
            ["/usr/sbin/sysctl", "vm.swapusage"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise VoiceMemoryBudgetError("voice_memory_budget_unavailable") from exc
    if vm.returncode != 0 or swap.returncode != 0:
        raise VoiceMemoryBudgetError("voice_memory_budget_unavailable")

    page_match = re.search(r"page size of\s+(\d+) bytes", vm.stdout)
    swap_match = re.search(r"free\s*=\s*([0-9]+(?:\.[0-9]+)?)M", swap.stdout)
    if page_match is None or swap_match is None:
        raise VoiceMemoryBudgetError("voice_memory_budget_unavailable")
    page_size = int(page_match.group(1))
    pages = 0
    for label in ("Pages free", "Pages inactive", "Pages speculative"):
        match = re.search(rf"^{re.escape(label)}:\s+(\d+)\.", vm.stdout, re.MULTILINE)
        if match is None:
            raise VoiceMemoryBudgetError("voice_memory_budget_unavailable")
        pages += int(match.group(1))
    reclaimable = pages * page_size
    swap_free = int(float(swap_match.group(1)) * 1024**2)
    if reclaimable <= 0 or swap_free < 0:
        raise VoiceMemoryBudgetError("voice_memory_budget_unavailable")
    return VoiceMemoryBudget(reclaimable, swap_free)


def _require_voice_memory_budget(stage: str) -> VoiceMemoryBudget:
    """Fail closed before STT/TTS loads when the host lacks measured headroom."""

    minimum_reclaimable = {
        "stt": VOICE_STT_MIN_RECLAIMABLE_BYTES,
        "tts": VOICE_TTS_MIN_RECLAIMABLE_BYTES,
    }.get(stage)
    if minimum_reclaimable is None:
        raise ValueError("voice_memory_stage_invalid")
    budget = _read_voice_memory_budget()
    if (
        budget.reclaimable_bytes < minimum_reclaimable
        or budget.swap_free_bytes < VOICE_MIN_SWAP_FREE_BYTES
    ):
        raise VoiceMemoryBudgetError("voice_memory_budget_low")
    return budget


def _force_local_model_cache() -> None:
    """Make model libraries use already cached weights and never fetch them."""

    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
        os.environ[name] = "1"


def _resolve_qwen3_tts_model_path(
    model: str,
    *,
    environment: Mapping[str, str],
    home: Path,
) -> str:
    """Resolve a trusted local model directory without contacting Hugging Face."""

    raw_model = str(model or "").strip()
    repo_match = re.fullmatch(
        r"([A-Za-z0-9][A-Za-z0-9._-]*)/([A-Za-z0-9][A-Za-z0-9._-]*)",
        raw_model,
    )

    def absolute_path(value: str | Path) -> Path | None:
        raw = str(value).strip()
        if raw == "~":
            return home
        if raw.startswith("~/"):
            return home / raw[2:]
        path = Path(raw)
        return path if path.is_absolute() else None

    root_inputs: list[Path] = []
    for name in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        configured = absolute_path(environment.get(name, ""))
        if configured is not None:
            root_inputs.append(configured)
    hf_home = absolute_path(environment.get("HF_HOME", ""))
    if hf_home is not None:
        root_inputs.append(hf_home / "hub")
    root_inputs.append(home / ".cache" / "huggingface" / "hub")

    roots: list[Path] = []
    for root in root_inputs:
        try:
            resolved = root.resolve(strict=False)
        except OSError:
            continue
        if resolved not in roots:
            roots.append(resolved)

    def resolved_absolute_directory(value: str | Path) -> Path | None:
        path = Path(str(value).strip())
        if not path.is_absolute() or path.is_symlink() or not path.is_dir():
            return None
        try:
            return path.resolve(strict=True)
        except OSError:
            return None

    def trusted_directory(value: str | Path) -> Path | None:
        path = absolute_path(value)
        if path is None:
            return None
        resolved = resolved_absolute_directory(path)
        if resolved is None:
            return None
        if any(resolved == root or resolved.is_relative_to(root) for root in roots):
            return resolved
        return None

    explicit_name = "OPENKAKAO_QWEN3_TTS_MODEL_PATH"
    if explicit_name in environment:
        local = resolved_absolute_directory(environment.get(explicit_name, ""))
        if local is None:
            raise RuntimeError("qwen3_tts_model_path_invalid")
        return str(local)

    if repo_match is None:
        local = trusted_directory(raw_model)
        if local is not None:
            return str(local)
        raise RuntimeError("qwen3_tts_model_path_invalid")

    organization, name = repo_match.groups()
    cache_name = f"models--{organization}--{name}"
    for root in roots:
        ref = root / cache_name / "refs" / "main"
        if ref.is_symlink() or not ref.is_file():
            continue
        try:
            revision = ref.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            continue
        if re.fullmatch(r"[0-9a-fA-F]{40}", revision) is None:
            continue
        snapshot = root / cache_name / "snapshots" / revision
        local = trusted_directory(snapshot)
        if local is not None:
            return str(local)
    return raw_model


def _local_urlopen(request: urllib.request.Request, *, timeout: float):
    """Open the fixed local endpoint without proxies or redirects."""

    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _RejectRedirects(),
    )
    return opener.open(request, timeout=timeout)


def _validate_local_llm_base_url(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    try:
        parsed = urllib.parse.urlsplit(raw)
        valid = (
            parsed.scheme == "http"
            and parsed.hostname == "127.0.0.1"
            and parsed.port == 11234
            and parsed.path == "/v1"
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("local_llm_endpoint_invalid")
    return raw


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
    def generate(
        self,
        text: str,
        token: AbortToken,
        *,
        history: Sequence[Mapping[str, str]] = (),
    ) -> str: ...


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
            return Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
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
        self._last_signature: tuple[str, str, str, bool] | None = None
        self._last_write_monotonic = float("-inf")

    def write(
        self,
        *,
        state: VoiceState,
        rms: float,
        error_code: str = "",
        wake_source: str = "stock",
        custom_model_selected: bool = False,
    ) -> None:
        normalized_error = str(error_code or "")[:96]
        normalized_wake_source = (
            wake_source if wake_source in {"stock", "custom", "none"} else "none"
        )
        signature = (
            state.value,
            normalized_error,
            normalized_wake_source,
            bool(custom_model_selected),
        )
        now = time.monotonic()
        if (
            state == VoiceState.WAKE_LISTEN
            and signature == self._last_signature
            and now - self._last_write_monotonic < VOICE_STATUS_HEARTBEAT_SECONDS
        ):
            return
        payload = {
            "schema_version": VOICE_STATUS_SCHEMA_VERSION,
            "state": state.value,
            "rms": round(max(0.0, min(float(rms), 1.0)), 4),
            "error_code": normalized_error,
            "wake_phrase": WAKE_PHRASE,
            "wake_source": normalized_wake_source,
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
        self._last_signature = signature
        self._last_write_monotonic = now


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
        self._conversation: deque[dict[str, str]] = deque(maxlen=VOICE_CONTEXT_TURNS * 2)
        self._last_conversation_turn = 0.0
        self._lock = threading.Lock()
        self._publish()

    def _recent_conversation(self) -> list[dict[str, str]]:
        if (
            self._last_conversation_turn
            and time.monotonic() - self._last_conversation_turn > VOICE_CONTEXT_TTL_SECONDS
        ):
            self._conversation.clear()
        return [dict(message) for message in self._conversation]

    def _remember_conversation_turn(self, transcript: str, reply: str) -> None:
        self._conversation.append(
            {"role": "user", "content": transcript[:VOICE_CONTEXT_ITEM_MAX_CHARS]}
        )
        self._conversation.append(
            {"role": "assistant", "content": reply[:VOICE_CONTEXT_ITEM_MAX_CHARS]}
        )
        self._last_conversation_turn = time.monotonic()

    def _rearm_after_reply(self) -> None:
        self.state = VoiceState.WAKE_LISTEN
        self.ring.clear()
        self._speech_frames = self._silence_frames = self._noise_frames = 0
        self._wake_source = "none"
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

    def mic_unavailable(self) -> VoiceResult:
        return self._end(VoiceState.ERROR, "mic_unavailable")

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
        except VoiceMemoryBudgetError as exc:
            return self._end(VoiceState.ERROR, exc.code)
        except Exception:
            return self._end(VoiceState.ERROR, "stt_error")

        try:
            self.state = VoiceState.GENERATING
            self._publish()
            reply = self.llm.generate(
                transcript,
                self.token,
                history=self._recent_conversation(),
            ).strip()
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
        except VoiceMemoryBudgetError as exc:
            return self._end(VoiceState.ERROR, exc.code)
        except Exception:
            return self._end(VoiceState.ERROR, "tts_error")

        self._remember_conversation_turn(transcript, reply)
        self._rearm_after_reply()
        return VoiceResult(VoiceState.ENDED, transcript=transcript, reply=reply)


class LocalMlxLlm:
    def __init__(
        self,
        base_url: str = LOCAL_LLM_BASE_URL,
        model: str = FLASH_NEXT_MODEL_ID,
        *,
        state_root: Path | None = None,
    ):
        self.base_url = _validate_local_llm_base_url(base_url)
        self.model = model
        self.state_root = state_root

    def generate(
        self,
        text: str,
        token: AbortToken,
        *,
        history: Sequence[Mapping[str, str]] = (),
    ) -> str:
        token.raise_if_cancelled()
        if self.model != FLASH_NEXT_MODEL_ID:
            raise RuntimeError("model_swap_required")
        payload = json.dumps(
            {
                "model": self.model.removeprefix("mlx/"),
                "messages": [
                    {"role": "system", "content": VOICE_PERSONA_PROMPT},
                    *[
                        {
                            "role": message["role"],
                            "content": message["content"][:VOICE_CONTEXT_ITEM_MAX_CHARS],
                        }
                        for message in history[-VOICE_CONTEXT_TURNS * 2 :]
                        if message.get("role") in {"user", "assistant"}
                        and isinstance(message.get("content"), str)
                    ],
                    {"role": "user", "content": text[:4000]},
                ],
                "temperature": 0.3,
                "max_tokens": 128,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with mlx_model_request_lease(self.state_root):
                with _local_urlopen(request, timeout=90.0) as response:
                    raw = response.read(LOCAL_LLM_MAX_RESPONSE_BYTES + 1)
        except MlxRequestAdmissionClosed as exc:
            raise RuntimeError(exc.code) from exc
        except (OSError, urllib.error.URLError, ValueError) as exc:
            raise RuntimeError("local_llm_request_failed") from exc
        if len(raw) > LOCAL_LLM_MAX_RESPONSE_BYTES:
            raise RuntimeError("local_llm_response_too_large")
        try:
            body = json.loads(raw.decode("utf-8", "replace"))
            message = body["choices"][0]["message"]
            content = message.get("content")
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("local_llm_response_invalid") from exc
        if mlx_response_model_conflicts(self.model, body.get("model")):
            raise RuntimeError("local_llm_model_mismatch")
        if not isinstance(content, str) or not content.strip():
            # Never speak a model's private reasoning channel as if it were a
            # user-facing answer. A reasoning-only response ends this turn.
            raise RuntimeError("local_llm_reply_empty")
        token.raise_if_cancelled()
        return content.strip()


class MlxWhisperAdapter:
    def __init__(self, model: str = WHISPER_MODEL_ID):
        self.model = model

    def transcribe(self, pcm16: bytes, sample_rate: int, token: AbortToken) -> str:
        token.raise_if_cancelled()
        _require_voice_memory_budget("stt")
        _force_local_model_cache()
        import numpy as np
        import mlx_whisper

        samples = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        if sample_rate != 16_000:
            raise RuntimeError("voice_sample_rate_unsupported")
        model = os.environ.get("OPENKAKAO_WHISPER_MODEL_PATH", self.model).strip() or self.model
        result: dict[str, Any] = mlx_whisper.transcribe(
            samples,
            path_or_hf_repo=model,
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
            _require_voice_memory_budget("tts")
            _force_local_model_cache()
            from qwen_tts import Qwen3TTSModel
            import torch

            dtype = torch.bfloat16 if QWEN3_TTS_PRECISION == "bf16" else torch.float16
            model = _resolve_qwen3_tts_model_path(
                self.model,
                environment=os.environ,
                home=Path.home(),
            )
            self._engine = Qwen3TTSModel.from_pretrained(
                model,
                dtype=dtype,
                local_files_only=True,
            )
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
        import wave

        audio, sample_rate = self.synthesize(text, token)
        try:
            import numpy as np

            if hasattr(audio, "detach"):
                audio = audio.detach().cpu().numpy()
            samples = np.asarray(audio).squeeze().astype(np.float32)
            peak = float(np.max(np.abs(samples))) if samples.size else 0.0
            if peak > 1.0:
                samples = samples / peak
            pcm = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16)
            pcm_bytes = pcm.tobytes()
            nframes = int(pcm.size)
        except ImportError:
            def _flatten(a: Any) -> list[float]:
                out: list[float] = []
                if hasattr(a, "tolist"):
                    a = a.tolist()
                if isinstance(a, (list, tuple)):
                    for item in a:
                        out.extend(_flatten(item))
                else:
                    out.append(float(a))
                return out

            samples_list = _flatten(audio)
            peak = max((abs(x) for x in samples_list), default=0.0)
            if peak > 1.0:
                samples_list = [x / peak for x in samples_list]
            from array import array

            pcm_arr = array("h", [int(max(-32768, min(32767, round(x * 32767.0)))) for x in samples_list])
            pcm_bytes = pcm_arr.tobytes()
            nframes = len(pcm_arr)

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            handle.writeframes(pcm_bytes)
        return {"path": str(path), "bytes": path.stat().st_size, "sample_rate": sample_rate, "nframes": nframes}

    def speak(self, text: str, token: AbortToken) -> None:
        token.raise_if_cancelled()
        tts_out = _voice_tts_output_path()
        if tts_out is not None:
            self.write_wav(text, tts_out, token)
            return

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


def _voice_tts_output_path() -> Path | None:
    raw = os.environ.get("OPENKAKAO_VOICE_TTS_OUT")
    if raw is None:
        return None

    path = Path(raw)
    try:
        if not raw or path.suffix.lower() != ".wav" or path.is_symlink():
            raise RuntimeError("voice_tts_output_invalid")
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.parent.is_dir() or not os.access(path.parent, os.W_OK):
            raise RuntimeError("voice_tts_output_invalid")
        if path.exists() and (not path.is_file() or not os.access(path, os.W_OK)):
            raise RuntimeError("voice_tts_output_invalid")
    except OSError as exc:
        raise RuntimeError("voice_tts_output_invalid") from exc
    return path


def assert_isolated_voice_environment() -> None:
    """Fail closed unless launched from the dedicated voice environment."""

    if os.environ.get("OPENKAKAO_VOICE_ENV") != "1":
        raise RuntimeError("voice_environment_not_isolated")


class _MicrophoneDisconnected(RuntimeError):
    pass


class _MicrophoneFramePoller:
    """Read one complete frame only when PortAudio reports it is available."""

    def __init__(
        self,
        stream: Any,
        *,
        frame_samples: int = VOICE_MIC_FRAME_SAMPLES,
        stale_seconds: float = VOICE_MIC_STALE_SECONDS,
    ) -> None:
        self.stream = stream
        self.frame_samples = max(1, int(frame_samples))
        self.frame_bytes = self.frame_samples * 2
        self.stale_seconds = max(VOICE_MIC_POLL_SECONDS, float(stale_seconds))
        self._last_frame_at = time.monotonic()

    def poll(self) -> tuple[bytes, bool] | None:
        try:
            if not bool(self.stream.active):
                raise _MicrophoneDisconnected("microphone stream is inactive")
            available = int(self.stream.read_available)
        except _MicrophoneDisconnected:
            raise
        except Exception as exc:
            raise _MicrophoneDisconnected("microphone availability check failed") from exc

        now = time.monotonic()
        if available < 0:
            raise _MicrophoneDisconnected("microphone availability is invalid")
        if available < self.frame_samples:
            if now - self._last_frame_at >= self.stale_seconds:
                raise _MicrophoneDisconnected("microphone input stalled")
            return None

        try:
            raw, overflowed = self.stream.read(self.frame_samples)
            frame = bytes(raw)
        except Exception as exc:
            raise _MicrophoneDisconnected("microphone read failed") from exc
        if len(frame) != self.frame_bytes:
            raise _MicrophoneDisconnected("microphone returned a partial frame")
        self._last_frame_at = time.monotonic()
        return frame, bool(overflowed)


def run_microphone_session(
    *,
    state_root: Path,
    custom_wake_model: Path | None = None,
) -> VoiceResult:
    """Run a continuous wake -> reply loop from a dedicated 16 kHz microphone stream."""

    assert_isolated_voice_environment()
    from jarvis_abort import AbortController
    import sounddevice as sd

    controller = AbortController(state_root)
    token = controller.token()
    status = VoiceStatusStore(state_root)
    pipeline = JarvisVoicePipeline(
        stt=MlxWhisperAdapter(),
        llm=LocalMlxLlm(state_root=state_root),
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
        # Poll frame availability so an idle device cannot block status heartbeats
        # or global abort checks. STT/LLM/TTS ordering remains synchronous; after
        # TTS, a short suppression interval prevents its tail becoming a new wake.
        wake_suppressed_until = 0.0
        stream_entered = False
        try:
            with sd.RawInputStream(
                samplerate=16_000,
                channels=1,
                dtype="int16",
                blocksize=VOICE_MIC_FRAME_SAMPLES,
            ) as stream:
                stream_entered = True
                poller = _MicrophoneFramePoller(stream)
                while True:
                    if token.is_cancelled():
                        return pipeline._end(VoiceState.ABORTED, "global_abort")
                    try:
                        polled = poller.poll()
                    except _MicrophoneDisconnected:
                        return pipeline.mic_disconnected()
                    if polled is None:
                        if pipeline.state == VoiceState.WAKE_LISTEN:
                            pipeline._publish()
                        time.sleep(VOICE_MIC_POLL_SECONDS)
                        continue
                    raw, overflowed = polled
                    if overflowed:
                        pipeline._noise_frames += 1
                    if time.monotonic() < wake_suppressed_until:
                        pipeline._publish()
                        continue
                    result = pipeline.feed_frontend_frame(frontend, raw)
                    if result is None:
                        continue
                    if result.state == VoiceState.ENDED and pipeline.state == VoiceState.WAKE_LISTEN:
                        wake_suppressed_until = time.monotonic() + VOICE_WAKE_RESUME_DELAY_SECONDS
                        continue
                    return result
        except JarvisCancelled:
            raise
        except Exception:
            if not stream_entered:
                return pipeline.mic_unavailable()
            return pipeline.mic_disconnected()
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
        llm=LocalMlxLlm(state_root=state_root),
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
