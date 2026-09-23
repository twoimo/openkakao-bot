from __future__ import annotations

import asyncio
import os
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
from jarvis_voice import VOICE_CONTEXT_TTL_SECONDS
from jarvis_voice import VOICE_PERSONA_PROMPT
from jarvis_voice import QWEN3_TTS_MODEL_ID, Qwen3TtsAdapter
from jarvis_voice import OpenWakeVadFrontend
from jarvis_voice import BUNDLED_CUSTOM_WAKE_MODEL, resolve_custom_wake_model
from jarvis_voice import LocalMlxLlm
from jarvis_voice import _resolve_qwen3_tts_model_path


class FakeStt:
    def transcribe(self, pcm16: bytes, sample_rate: int, token) -> str:
        token.raise_if_cancelled()
        return "테스트 요청"


class FakeLlm:
    def __init__(self, controller: AbortController | None = None):
        self.controller = controller
        self.calls: list[tuple[str, list[dict[str, str]]]] = []

    def generate(self, text: str, token, *, history=()) -> str:
        if self.controller is not None:
            self.controller.abort()
        self.calls.append((text, [dict(message) for message in history]))
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
    def test_voice_turns_keep_four_bounded_context_turns_and_rearm_wake(self):
        with TemporaryDirectory() as temp_dir:
            controller = AbortController(Path(temp_dir))
            llm = FakeLlm()
            pipeline = JarvisVoicePipeline(
                stt=FakeStt(), llm=llm, tts=FakeTts(), token=controller.token()
            )
            results = [
                pipeline.process_utterance(b"\x01\x00" * 320)
                for _ in range(6)
            ]

        self.assertTrue(all(result.state == VoiceState.ENDED for result in results))
        self.assertEqual(pipeline.state, VoiceState.WAKE_LISTEN)
        self.assertEqual(llm.calls[0][1], [])
        self.assertEqual(
            llm.calls[5][1],
            [
                {"role": "user", "content": "테스트 요청"},
                {"role": "assistant", "content": "알겠습니다."},
            ] * 4,
        )

    def test_voice_context_expires_after_idle_ttl(self):
        from unittest import mock

        with TemporaryDirectory() as temp_dir:
            pipeline = JarvisVoicePipeline(
                stt=FakeStt(),
                llm=FakeLlm(),
                tts=FakeTts(),
                token=AbortController(Path(temp_dir)).token(),
            )
            pipeline._conversation.extend(
                [
                    {"role": "user", "content": "이전 요청"},
                    {"role": "assistant", "content": "이전 응답"},
                ]
            )
            pipeline._last_conversation_turn = 10.0

            with mock.patch(
                "jarvis_voice.time.monotonic",
                return_value=10.0 + VOICE_CONTEXT_TTL_SECONDS + 1,
            ):
                self.assertEqual(pipeline._recent_conversation(), [])

        self.assertEqual(list(pipeline._conversation), [])

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

    def test_openwake_stock_model_loads_only_hey_jarvis(self):
        import types
        from unittest import mock

        calls: list[dict[str, object]] = []

        class FakeModel:
            def __init__(self, **kwargs: object) -> None:
                calls.append(kwargs)

        package = types.ModuleType("openwakeword")
        model_module = types.ModuleType("openwakeword.model")
        model_module.Model = FakeModel
        package.model = model_module

        with mock.patch.dict(
            sys.modules,
            {"openwakeword": package, "openwakeword.model": model_module},
        ):
            OpenWakeVadFrontend._make_wake_model(None)

        self.assertEqual(
            calls,
            [{"wakeword_models": ["hey_jarvis"], "inference_framework": "onnx"}],
        )

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

    def test_missing_bundled_head_falls_back_to_stock_without_lowering_threshold(self):
        """번들 헤드가 없거나 무효하면 stock으로 내려가고 임계값은 그대로다."""
        from unittest import mock

        from jarvis_voice import WAKE_THRESHOLD

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            empty = root / "empty.onnx"
            empty.write_bytes(b"")
            wrong_suffix = root / "head.txt"
            wrong_suffix.write_bytes(b"onnx")
            symlinked = root / "linked.onnx"
            symlinked.symlink_to(wrong_suffix)
            for candidate in (root / "absent.onnx", empty, wrong_suffix, symlinked):
                with mock.patch("jarvis_voice.BUNDLED_CUSTOM_WAKE_MODEL", candidate):
                    self.assertIsNone(resolve_custom_wake_model(None))
        self.assertEqual(WAKE_THRESHOLD, 0.65)
        gate = WakePhraseGate()
        self.assertFalse(gate.accepts(0.64))
        self.assertTrue(gate.accepts(0.65))


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


class Qwen3TtsModelPathTests(unittest.TestCase):
    def test_resolves_existing_huggingface_snapshot(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache = root / "hub"
            revision = "a" * 40
            model_cache = cache / "models--Qwen--Qwen3-TTS-12Hz-1.7B-CustomVoice"
            (model_cache / "refs").mkdir(parents=True)
            (model_cache / "refs" / "main").write_text(revision, encoding="ascii")
            snapshot = model_cache / "snapshots" / revision
            snapshot.mkdir(parents=True)

            resolved = _resolve_qwen3_tts_model_path(
                QWEN3_TTS_MODEL_ID,
                environment={"HF_HUB_CACHE": str(cache)},
                home=root / "home",
            )

            self.assertEqual(resolved, str(snapshot.resolve()))

    def test_missing_snapshot_preserves_original_model_id(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache = root / "hub"
            cache.mkdir()

            resolved = _resolve_qwen3_tts_model_path(
                QWEN3_TTS_MODEL_ID,
                environment={"HF_HUB_CACHE": str(cache)},
                home=root / "home",
            )

            self.assertEqual(resolved, QWEN3_TTS_MODEL_ID)

    def test_explicit_environment_path_takes_priority(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache = root / "hub"
            explicit = cache / "manual" / "qwen3-tts"
            explicit.mkdir(parents=True)

            resolved = _resolve_qwen3_tts_model_path(
                QWEN3_TTS_MODEL_ID,
                environment={
                    "HF_HUB_CACHE": str(cache),
                    "OPENKAKAO_QWEN3_TTS_MODEL_PATH": str(explicit),
                },
                home=root / "home",
            )

            self.assertEqual(resolved, str(explicit.resolve()))

    def test_explicit_path_outside_cache_is_accepted(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache = root / "hub"
            cache.mkdir()
            outside = root / "outside"
            outside.mkdir()

            resolved = _resolve_qwen3_tts_model_path(
                QWEN3_TTS_MODEL_ID,
                environment={
                    "HF_HUB_CACHE": str(cache),
                    "OPENKAKAO_QWEN3_TTS_MODEL_PATH": str(outside),
                },
                home=root / "home",
            )

            self.assertEqual(resolved, str(outside.resolve()))

    def test_explicit_path_rejects_empty_missing_file_symlink_and_relative(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache = root / "hub"
            cache.mkdir()
            regular_file = root / "model.bin"
            regular_file.write_bytes(b"model")
            directory = root / "model"
            directory.mkdir()
            symlink = root / "model-link"
            symlink.symlink_to(directory, target_is_directory=True)

            invalid_paths = (
                "",
                str(root / "missing"),
                str(regular_file),
                str(symlink),
                "relative/model",
            )
            for explicit in invalid_paths:
                with self.subTest(explicit=explicit):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "^qwen3_tts_model_path_invalid$",
                    ):
                        _resolve_qwen3_tts_model_path(
                            QWEN3_TTS_MODEL_ID,
                            environment={
                                "HF_HUB_CACHE": str(cache),
                                "OPENKAKAO_QWEN3_TTS_MODEL_PATH": explicit,
                            },
                            home=root / "home",
                        )


class Qwen3TtsAdapterApiTests(unittest.TestCase):
    def test_speak_writes_env_wav_without_playback(self):
        import types
        from unittest import mock

        fake_mod = types.ModuleType("qwen_tts")

        class FakeModel:
            @classmethod
            def from_pretrained(cls, _model, **_kwargs):
                return cls()

            def get_supported_speakers(self):
                return ["ryan"]

            def generate_custom_voice(self, **_kwargs):
                return [[0.0, 0.25, -0.25, 0.0]], 24000

        fake_mod.Qwen3TTSModel = FakeModel
        fake_play = mock.Mock(side_effect=AssertionError("speaker playback must not run"))
        fake_sounddevice = types.SimpleNamespace(
            play=fake_play,
            get_stream=lambda: types.SimpleNamespace(active=False),
            stop=lambda: None,
        )

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "jarvis-voice-out.wav"
            token = AbortController(root).token()
            adapter = Qwen3TtsAdapter()
            with (
                mock.patch.dict(os.environ, {"OPENKAKAO_VOICE_TTS_OUT": str(output)}),
                mock.patch.dict(
                    sys.modules,
                    {
                        "qwen_tts": fake_mod,
                        "torch": types.SimpleNamespace(bfloat16="bf16", float16="fp16"),
                        "sounddevice": fake_sounddevice,
                    },
                ),
            ):
                adapter.speak("안녕하세요", token)

            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size, 44)
            self.assertEqual(output.read_bytes()[:4], b"RIFF")
            fake_play.assert_not_called()

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
        with (
            mock.patch(
                "jarvis_voice._resolve_qwen3_tts_model_path",
                return_value=QWEN3_TTS_MODEL_ID,
            ),
            mock.patch.dict(sys.modules, {"qwen_tts": fake_mod, "torch": types.SimpleNamespace(bfloat16="bf16", float16="fp16"), "sounddevice": types.SimpleNamespace(play=lambda *a, **k: None, get_stream=lambda: types.SimpleNamespace(active=False), stop=lambda: None)}),
        ):
            adapter.speak("안녕하세요", token)  # type: ignore[arg-type]
        self.assertEqual(adapter._engine.loaded_model, QWEN3_TTS_MODEL_ID)
        self.assertTrue(adapter._engine.kwargs["local_files_only"])
        self.assertEqual(adapter._engine.generated["language"], "Korean")
        self.assertEqual(adapter._engine.generated["speaker"], "ryan")


class LocalMlxLlmRequestTests(unittest.TestCase):
    def test_generate_sends_max_tokens_and_strips_content(self):
        import json
        from unittest import mock

        captured: dict[str, object] = {}

        class FakeResp:
            def read(self, _limit: int = -1) -> bytes:
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
        with mock.patch("jarvis_voice._local_urlopen", fake_urlopen):
            reply = LocalMlxLlm().generate(
                "안녕",
                token,
                history=[
                    {"role": "user", "content": "첫 질문"},
                    {"role": "assistant", "content": "첫 답변"},
                ],
            )
        self.assertEqual(reply, "알겠습니다.")
        self.assertEqual(captured["timeout"], 90.0)
        self.assertEqual(captured["body"]["max_tokens"], 128)
        self.assertEqual(captured["body"]["model"], "ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit")
        self.assertEqual(
            captured["body"]["messages"][1:3],
            [
                {"role": "user", "content": "첫 질문"},
                {"role": "assistant", "content": "첫 답변"},
            ],
        )
        self.assertIn("스스로 질문을 만든 뒤 답하지 않는다", captured["body"]["messages"][0]["content"])

    def test_voice_persona_ends_turn_without_engagement_question(self):
        self.assertIn("스스로 질문을 만든 뒤 답하지 않는다", VOICE_PERSONA_PROMPT)
        self.assertIn("턴을 끝낸다", VOICE_PERSONA_PROMPT)

    def test_generate_rejects_non_loopback_endpoint(self):
        with self.assertRaisesRegex(ValueError, "local_llm_endpoint_invalid"):
            LocalMlxLlm("https://example.invalid/v1")


if __name__ == "__main__":
    unittest.main()
