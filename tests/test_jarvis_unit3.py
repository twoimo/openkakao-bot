from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from auto_reply_ax_ui import AX_ABORT_FOCUS_REQUIRED, AX_ABORT_GLOBAL, background_virtual_cursor_action
from jarvis_abort import AbortController, AbortableJobQueue, JarvisCancelled
from jarvis_browser_use import BrowserUseRunner
from jarvis_voice import CUSTOM_WAKE_MODEL_MAX_BYTES, JarvisVoicePipeline, VoiceState, WakePhraseGate
from jarvis_voice import Qwen3TtsAdapter
from jarvis_voice import OpenWakeVadFrontend
from jarvis_voice import BUNDLED_CUSTOM_WAKE_MODEL, resolve_custom_wake_model
from jarvis_voice import LocalMlxLlm


class FakeStt:
    def transcribe(self, pcm16: bytes, sample_rate: int, token) -> str:
        token.raise_if_cancelled()
        return "테스트 요청"


class FakeLlm:
    def __init__(self, controller: AbortController | None = None):
        self.controller = controller

    def generate(self, text: str, token) -> str:
        if self.controller is not None:
            self.controller.abort()
        return "알겠습니다."


class FakeTts:
    def __init__(self, controller: AbortController | None = None):
        self.controller = controller
        self.calls = 0

    def speak(self, text: str, token) -> None:
        self.calls += 1
        if self.controller is not None:
            self.controller.abort()
            token.raise_if_cancelled()


class JarvisAbortAndVoiceTests(unittest.TestCase):
    def test_abort_during_generation_stops_before_tts(self):
        with TemporaryDirectory() as temp_dir:
            controller = AbortController(Path(temp_dir))
            tts = FakeTts()
            pipeline = JarvisVoicePipeline(
                stt=FakeStt(),
                llm=FakeLlm(controller),
                tts=tts,
                token=controller.token(),
            )
            result = pipeline.process_utterance(b"\x01\x00" * 320)
            self.assertEqual(result.state, VoiceState.ABORTED)
            self.assertEqual(result.error_code, "global_abort")
            self.assertEqual(tts.calls, 0)

    def test_abort_during_tts_stops_session(self):
        with TemporaryDirectory() as temp_dir:
            controller = AbortController(Path(temp_dir))
            tts = FakeTts(controller)
            pipeline = JarvisVoicePipeline(
                stt=FakeStt(),
                llm=FakeLlm(),
                tts=tts,
                token=controller.token(),
            )
            result = pipeline.process_utterance(b"\x01\x00" * 320)
            self.assertEqual(result.state, VoiceState.ABORTED)
            self.assertEqual(tts.calls, 1)

    def test_wake_threshold_rejects_false_accept_and_tts_echo(self):
        gate = WakePhraseGate()
        self.assertFalse(gate.accepts(0.64))
        self.assertFalse(gate.accepts(0.10, 0.64))
        self.assertTrue(gate.accepts(0.10, 0.80))

        with TemporaryDirectory() as temp_dir:
            controller = AbortController(Path(temp_dir))
            pipeline = JarvisVoicePipeline(
                stt=FakeStt(), llm=FakeLlm(), tts=FakeTts(), token=controller.token()
            )
            pipeline.state = VoiceState.SPEAKING
            pipeline.feed_audio(
                b"\x01\x00" * 320,
                rms=0.8,
                speech=True,
                stock_wake_score=1.0,
            )
            self.assertEqual(pipeline.state, VoiceState.SPEAKING)
            self.assertEqual(pipeline.ring.size, 0)

    def test_openwake_frontend_requires_20ms_int16_frames(self):
        class FakeModel:
            def __init__(self) -> None:
                self.seen: list[object] = []

            def predict(self, x):
                self.seen.append(x)
                return {"hey_jarvis_v0.1": 0.21}

        class FakeVad:
            def is_speech(self, frame: bytes, sample_rate: int) -> bool:
                self.frame = frame
                self.sample_rate = sample_rate
                return True

        model = FakeModel()
        vad = FakeVad()
        frontend = OpenWakeVadFrontend(stock_model=model, vad=vad)
        with self.assertRaisesRegex(ValueError, "voice_frame_size_invalid"):
            frontend.analyze(b"\x00\x00" * 1280)
        frame = b"\x01\x00" * 320
        analysis = frontend.analyze(frame)
        self.assertEqual(len(model.seen), 1)
        wake_input = model.seen[0]
        self.assertEqual(len(wake_input), 320)
        self.assertEqual(vad.frame, frame)
        self.assertEqual(vad.sample_rate, 16_000)
        self.assertAlmostEqual(analysis.stock_wake_score, 0.21)
        self.assertIsNone(analysis.custom_wake_score)

    def test_custom_wake_model_path_validation_is_bounded(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            model = root / "hey_jarvis_ko.onnx"
            model.write_bytes(b"onnx")
            path, framework = OpenWakeVadFrontend._validate_custom_wake_model_path(model)
            self.assertEqual(path, model)
            self.assertEqual(framework, "onnx")

            wrong_type = root / "hey_jarvis_ko.bin"
            wrong_type.write_bytes(b"x")
            with self.assertRaisesRegex(RuntimeError, "custom_wake_model_invalid"):
                OpenWakeVadFrontend._validate_custom_wake_model_path(wrong_type)

            oversized = root / "oversized.onnx"
            with oversized.open("wb") as handle:
                handle.truncate(CUSTOM_WAKE_MODEL_MAX_BYTES + 1)
            with self.assertRaisesRegex(RuntimeError, "custom_wake_model_invalid"):
                OpenWakeVadFrontend._validate_custom_wake_model_path(oversized)

    def test_custom_wake_model_path_rejects_symlink(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "target.onnx"
            target.write_bytes(b"onnx")
            symlink = root / "hey_jarvis_ko.onnx"
            symlink.symlink_to(target)
            with self.assertRaisesRegex(RuntimeError, "custom_wake_model_invalid"):
                OpenWakeVadFrontend._validate_custom_wake_model_path(symlink)

    def test_resolve_custom_wake_model_uses_bundled_or_fails_closed(self):
        if BUNDLED_CUSTOM_WAKE_MODEL.is_file():
            self.assertEqual(resolve_custom_wake_model(None), BUNDLED_CUSTOM_WAKE_MODEL)
        with TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing.onnx"
            with self.assertRaisesRegex(RuntimeError, "custom_wake_model_invalid"):
                resolve_custom_wake_model(missing)
            valid = Path(temp_dir) / "ok.onnx"
            valid.write_bytes(b"onnx")
            self.assertEqual(resolve_custom_wake_model(valid), valid)

    def test_global_abort_clears_queue_and_never_auto_resumes(self):
        with TemporaryDirectory() as temp_dir:
            controller = AbortController(Path(temp_dir))
            queue = AbortableJobQueue(controller)
            ran: list[str] = []
            self.assertTrue(queue.enqueue(lambda _token: ran.append("old")))
            controller.abort()
            self.assertIsNone(queue.run_next())
            self.assertFalse(queue.enqueue(lambda _token: ran.append("new")))
            self.assertEqual(ran, [])
            controller.resume_after_human_action()
            self.assertIsNone(queue.run_next())
            self.assertEqual(ran, [])

    def test_background_ax_aborts_focus_stealing_and_global_cancel(self):
        with TemporaryDirectory() as temp_dir:
            controller = AbortController(Path(temp_dir))
            token = controller.token()
            result = background_virtual_cursor_action(
                element_rect=(10, 20, 100, 40),
                perform_ax_action=lambda: True,
                token=token,
                requires_frontmost_activation=True,
            )
            self.assertFalse(result.ok)
            self.assertEqual(result.error_code, AX_ABORT_FOCUS_REQUIRED)
            self.assertEqual((result.cursor.x, result.cursor.y), (60, 40))

            token2 = controller.token()
            def cancel_mid_action() -> bool:
                controller.abort()
                return True
            cancelled = background_virtual_cursor_action(
                element_rect=(0, 0, 20, 20),
                perform_ax_action=cancel_mid_action,
                token=token2,
            )
            self.assertFalse(cancelled.ok)
            self.assertEqual(cancelled.error_code, AX_ABORT_GLOBAL)


class FakeOwnedContext:
    def __init__(self):
        self.context = object()
        self.closed = False

    async def start(self):
        return self.context

    async def close(self):
        self.closed = True


class JarvisBrowserAbortTests(unittest.IsolatedAsyncioTestCase):
    async def test_playwright_job_cancels_and_owned_context_closes(self):
        with TemporaryDirectory() as temp_dir:
            controller = AbortController(Path(temp_dir))
            owned = FakeOwnedContext()

            async def agent(_task, _context, _model, _base_url):
                controller.abort()
                await asyncio.sleep(0.2)
                return "should-not-complete"

            runner = BrowserUseRunner(
                controller.token(),
                context_factory=lambda: owned,
                agent_factory=agent,
            )
            result = await runner.run("local-only test")
            self.assertFalse(result.ok)
            self.assertEqual(result.error_code, "global_abort")
            self.assertTrue(owned.closed)


class Qwen3TtsAdapterApiTests(unittest.TestCase):
    def test_load_uses_qwen3ttsmodel_and_custom_voice(self):
        import types
        from unittest import mock

        fake_mod = types.ModuleType("qwen_tts")

        class FakeModel:
            @classmethod
            def from_pretrained(cls, model, **kwargs):
                inst = cls()
                inst.loaded_model = model
                inst.kwargs = kwargs
                return inst

            def get_supported_speakers(self):
                return ["ryan"]

            def generate_custom_voice(self, **kwargs):
                self.generated = kwargs
                return [b"wav"], 24000

        fake_mod.Qwen3TTSModel = FakeModel
        adapter = Qwen3TtsAdapter()
        with TemporaryDirectory() as temp_dir:
            token = AbortController(Path(temp_dir)).token()
        with mock.patch.dict(sys.modules, {"qwen_tts": fake_mod, "torch": types.SimpleNamespace(bfloat16="bf16", float16="fp16"), "sounddevice": types.SimpleNamespace(play=lambda *a, **k: None, get_stream=lambda: types.SimpleNamespace(active=False), stop=lambda: None)}):
            adapter.speak("안녕하세요", token)  # type: ignore[arg-type]
        self.assertEqual(adapter._engine.loaded_model, "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice")
        self.assertEqual(adapter._engine.generated["language"], "Korean")
        self.assertEqual(adapter._engine.generated["speaker"], "ryan")


class LocalMlxLlmRequestTests(unittest.TestCase):
    def test_generate_sends_max_tokens_and_strips_content(self):
        import json
        from unittest import mock

        captured: dict[str, object] = {}

        class FakeResp:
            def read(self) -> bytes:
                return json.dumps({"choices": [{"message": {"content": " 알겠습니다. "}}]}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *args) -> bool:
                return False

        def fake_urlopen(request, timeout=0):
            captured["timeout"] = timeout
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResp()

        with TemporaryDirectory() as temp_dir:
            token = AbortController(Path(temp_dir)).token()
        with mock.patch("urllib.request.urlopen", fake_urlopen):
            reply = LocalMlxLlm().generate("안녕", token)
        self.assertEqual(reply, "알겠습니다.")
        self.assertEqual(captured["timeout"], 90.0)
        self.assertEqual(captured["body"]["max_tokens"], 128)
        self.assertEqual(captured["body"]["model"], "ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit")


if __name__ == "__main__":
    unittest.main()
