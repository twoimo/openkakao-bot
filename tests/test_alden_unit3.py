from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from auto_reply_ax_ui import AX_ABORT_FOCUS_REQUIRED, AX_ABORT_GLOBAL, background_virtual_cursor_action
from alden_abort import AbortController, AbortableJobQueue, AldenCancelled
from alden_browser_use import BrowserUseRunner
from local_mlx_gateway import mlx_model_swap_lease
from alden_voice import CUSTOM_WAKE_MODEL_MAX_BYTES, AldenVoicePipeline, VoiceState, WakePhraseGate
from alden_voice import VOICE_CONTEXT_TTL_SECONDS
from alden_voice import VOICE_PERSONA_PROMPT
from alden_voice import QWEN3_TTS_MODEL_ID, Qwen3TtsAdapter
from alden_voice import MlxWhisperAdapter, VoiceMemoryBudget, VoiceMemoryBudgetError
from alden_voice import _require_voice_memory_budget
from alden_voice import OpenWakeVadFrontend
from alden_voice import resolve_custom_wake_model
from alden_voice import LocalMlxLlm
from alden_voice import FLASH_NEXT_MODEL_ID, QWEN38_27B_MODEL_ID
from alden_voice import _resolve_qwen3_tts_model_path


class FakeJsonResponse:
    def __init__(self, payload: object):
        self.payload = payload

    def read(self, _limit: int = -1) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False


def ready_model_catalog(model_id: str) -> dict[str, object]:
    return {"data": [{"id": model_id, "loaded": True, "state": "ready"}]}


def ready_browser_catalog() -> bytes:
    return json.dumps(ready_model_catalog(QWEN38_27B_MODEL_ID.removeprefix("mlx/"))).encode()


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


class AldenAbortAndVoiceTests(unittest.TestCase):
    def test_voice_turns_keep_four_bounded_context_turns_and_rearm_wake(self):
        with TemporaryDirectory() as temp_dir:
            controller = AbortController(Path(temp_dir))
            llm = FakeLlm()
            pipeline = AldenVoicePipeline(
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
            pipeline = AldenVoicePipeline(
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
                "alden_voice.time.monotonic",
                return_value=10.0 + VOICE_CONTEXT_TTL_SECONDS + 1,
            ):
                self.assertEqual(pipeline._recent_conversation(), [])

        self.assertEqual(list(pipeline._conversation), [])

    def test_abort_during_generation_stops_before_tts(self):
        with TemporaryDirectory() as temp_dir:
            controller = AbortController(Path(temp_dir))
            tts = FakeTts()
            pipeline = AldenVoicePipeline(
                stt=FakeStt(),
                llm=FakeLlm(controller),
                tts=tts,
                token=controller.token(),
            )
            result = pipeline.process_utterance(b"\x01\x00" * 320)
            self.assertEqual(result.state, VoiceState.ABORTED)
            self.assertEqual(result.error_code, "global_abort")
            self.assertEqual(tts.calls, 0)

    def test_reasoning_only_model_response_is_never_spoken(self):
        from unittest import mock

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tts = FakeTts()
            pipeline = AldenVoicePipeline(
                stt=FakeStt(),
                llm=LocalMlxLlm(state_root=root),
                tts=tts,
                token=AbortController(root).token(),
            )
            responses = iter(
                [
                    FakeJsonResponse(ready_model_catalog(QWEN38_27B_MODEL_ID)),
                    FakeJsonResponse(
                        {
                            "choices": [
                                {
                                    "message": {
                                        "content": "",
                                        "reasoning_content": "이 내용은 사용자에게 읽으면 안 됩니다.",
                                    }
                                }
                            ]
                        }
                    ),
                ]
            )
            with mock.patch(
                "alden_voice._local_urlopen", side_effect=lambda *_args, **_kwargs: next(responses)
            ):
                result = pipeline.process_utterance(b"\x01\x00" * 320)

        self.assertEqual(result.state, VoiceState.ERROR)
        self.assertEqual(result.error_code, "generation_error")
        self.assertEqual(tts.calls, 0)
        self.assertEqual(pipeline._recent_conversation(), [{"role": "user", "content": "테스트 요청"}])

    def test_abort_during_tts_stops_session(self):
        with TemporaryDirectory() as temp_dir:
            controller = AbortController(Path(temp_dir))
            tts = FakeTts(controller)
            pipeline = AldenVoicePipeline(
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
            pipeline = AldenVoicePipeline(
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
                return {"alden_v0.1": 0.21}

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

    def test_openwake_default_does_not_load_an_unrelated_stock_phrase(self):
        frontend = OpenWakeVadFrontend(vad=object())
        self.assertIsNone(frontend.stock_model)
        self.assertIsNone(frontend.custom_model)

    def test_custom_model_warmup_flushes_the_full_embedding_window(self):
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest("NumPy is required for the real wake feature warmup")
        class FakeModel:
            def __init__(self) -> None:
                self.inputs = []

            def predict(self, samples):
                self.inputs.append(samples)
                return {}

        model = FakeModel()
        OpenWakeVadFrontend._warm_custom_model(model)
        self.assertEqual(len(model.inputs), 16)
        self.assertTrue(all(len(samples) == 1_280 for samples in model.inputs))
        self.assertTrue(all(not samples.any() for samples in model.inputs))

    def test_openwake_custom_model_loads_only_the_explicit_experimental_head(self):
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

        with TemporaryDirectory() as temp_dir:
            model_path = Path(temp_dir) / "alden_ko.onnx"
            model_path.write_bytes(b"onnx")
            with mock.patch.dict(
                sys.modules,
                {"openwakeword": package, "openwakeword.model": model_module},
            ):
                OpenWakeVadFrontend._make_wake_model(model_path)

        self.assertEqual(
            calls,
            [{"wakeword_models": [str(model_path)], "inference_framework": "onnx"}],
        )

    def test_custom_wake_model_path_validation_is_bounded(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            model = root / "alden_ko.onnx"
            model.write_bytes(b"onnx")
            path, framework = OpenWakeVadFrontend._validate_custom_wake_model_path(model)
            self.assertEqual(path, model)
            self.assertEqual(framework, "onnx")

            wrong_type = root / "alden_ko.bin"
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
            symlink = root / "alden_ko.onnx"
            symlink.symlink_to(target)
            with self.assertRaisesRegex(RuntimeError, "custom_wake_model_invalid"):
                OpenWakeVadFrontend._validate_custom_wake_model_path(symlink)

    def test_resolve_custom_wake_model_requires_explicit_experimental_path(self):
        self.assertIsNone(resolve_custom_wake_model(None))
        with TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing.onnx"
            with self.assertRaisesRegex(RuntimeError, "custom_wake_model_invalid"):
                resolve_custom_wake_model(missing)
            valid = Path(temp_dir) / "ok.onnx"
            valid.write_bytes(b"onnx")
            self.assertEqual(resolve_custom_wake_model(valid), valid)

    def test_unreleased_wake_head_stays_disabled_without_lowering_threshold(self):
        from alden_voice import WAKE_THRESHOLD

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


class AldenBrowserAbortTests(unittest.IsolatedAsyncioTestCase):
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
                catalog_reader=ready_browser_catalog,
                state_root=Path(temp_dir),
            )
            result = await runner.run("local-only test")
            self.assertFalse(result.ok)
            self.assertEqual(result.error_code, "global_abort")
            self.assertTrue(owned.closed)

    async def test_browser_use_does_not_start_an_agent_during_model_swap(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller = AbortController(root)
            owned = FakeOwnedContext()
            calls = []

            async def agent(*_args):
                calls.append(True)
                return "unexpected"

            runner = BrowserUseRunner(
                controller.token(),
                context_factory=lambda: owned,
                agent_factory=agent,
                catalog_reader=ready_browser_catalog,
                state_root=root,
            )
            with mlx_model_swap_lease(root):
                result = await runner.run("local-only task")

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "model_swap_in_progress")
        self.assertEqual(calls, [])
        self.assertFalse(owned.closed)


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
    def test_close_releases_engine_before_interpreter_teardown(self):
        import types
        adapter = Qwen3TtsAdapter(); adapter._engine = object(); adapter._device = "mps"
        events = []
        mps = types.SimpleNamespace(synchronize=lambda: events.append("sync"), empty_cache=lambda: events.append("cache"))
        with mock.patch.dict(sys.modules, {"torch":types.SimpleNamespace(mps=mps)}), mock.patch("gc.collect",side_effect=lambda: events.append("released" if adapter._engine is None else "held")):
            adapter.close(); adapter.close()
        self.assertEqual(events,["sync","released","cache","sync"])
        self.assertIsNone(adapter._engine)

    def test_korean_integer_pronunciation_preserves_other_meanings(self):
        from alden_voice import _tts_spoken_text
        for original, spoken in (("0입니다.","영입니다."),("4입니다.","사입니다."),("12입니다.","십이입니다."),("100입니다!","백입니다!"),("1234입니다.","천이백삼십사입니다.")):
            self.assertEqual(_tts_spoken_text(original),"숫자는 " + spoken)
        for original in ("0012입니다.","-12입니다.","1.2입니다.","2026-10-01입니다.","답은 12입니다.","https://example.com/12", "10000입니다."):
            self.assertEqual(_tts_spoken_text(original),original)

    def test_local_backend_keeps_bf16_and_korean_speaker_without_loading_twice(self):
        import types
        for platform, available, expected in (("darwin", True, "mps"), ("darwin", False, "cpu"), ("linux", True, "cpu")):
            with self.subTest(platform=platform, available=available), TemporaryDirectory() as temporary:
                engine = mock.Mock()
                engine.get_supported_speakers.return_value = ["aiden", "Ryan", "Sohee", "vivian"]
                class Talker:
                    def generate(self, **_kwargs): return None
                engine.model = types.SimpleNamespace(talker=Talker())
                engine.generate_custom_voice.return_value = ([[0.0, 0.25]], 24000)
                factory = mock.Mock(return_value=engine)
                mps_available = mock.Mock(return_value=available)
                clear_cache = mock.Mock()
                set_fraction = mock.Mock()
                torch = types.SimpleNamespace(bfloat16="bf16", float16="fp16", backends=types.SimpleNamespace(mps=types.SimpleNamespace(is_available=mps_available)), mps=types.SimpleNamespace(empty_cache=clear_cache, recommended_max_memory=lambda: 128 * 1024**3, set_per_process_memory_fraction=set_fraction))
                token = AbortController(Path(temporary)).token()
                with (
                    mock.patch("alden_voice.sys.platform", platform),
                    mock.patch("alden_voice._require_voice_memory_budget") as admission,
                    mock.patch("alden_voice._resolve_qwen3_tts_model_path", return_value="/owned/model"),
                    mock.patch.dict(sys.modules, {"torch": torch, "qwen_tts": types.SimpleNamespace(Qwen3TTSModel=types.SimpleNamespace(from_pretrained=factory))}),
                ):
                    adapter = Qwen3TtsAdapter()
                    adapter.synthesize("첫 답변", token)
                    adapter.synthesize("다음 답변", token)
                    factory.assert_called_once_with("/owned/model", dtype="bf16", device_map=expected, local_files_only=True)
                    self.assertEqual(admission.call_count, 2)
                    self.assertEqual(engine.generate_custom_voice.call_count, 2)
                    args = engine.generate_custom_voice.call_args.kwargs
                    self.assertEqual((args["speaker"], args["language"]), ("sohee", "Korean"))
                    self.assertIn("절제", args["instruct"])
                    self.assertEqual(mps_available.call_count, int(platform == "darwin"))
                    self.assertEqual(clear_cache.call_count, 2 if expected == "mps" else 0)
                    if expected == "mps":
                        set_fraction.assert_called_once_with(10 / 128)
                    else:
                        set_fraction.assert_not_called()

    def test_missing_korean_speaker_does_not_silently_change_product_voice(self):
        engine = mock.Mock()
        engine.get_supported_speakers.return_value = ["vivian"]
        adapter = Qwen3TtsAdapter()
        adapter._engine = engine
        with TemporaryDirectory() as temporary, mock.patch("alden_voice._require_voice_memory_budget"):
            with self.assertRaisesRegex(RuntimeError, "qwen3_tts_speaker_unavailable"):
                adapter.synthesize("안녕하세요", AbortController(Path(temporary)).token())
        engine.generate_custom_voice.assert_not_called()

    def test_speak_writes_env_wav_without_playback(self):
        import types
        from unittest import mock

        fake_mod = types.ModuleType("qwen_tts")

        class FakeModel:
            def __init__(self):
                class Talker:
                    def generate(self, **kwargs):
                        for criterion in kwargs.get("stopping_criteria", []): criterion(None, None)
                self.model = types.SimpleNamespace(talker=Talker())

            @classmethod
            def from_pretrained(cls, _model, **_kwargs):
                return cls()

            def get_supported_speakers(self):
                return ["sohee"]

            def generate_custom_voice(self, **_kwargs):
                self.model.talker.generate()
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
            output = root / "alden-voice-out.wav"
            token = AbortController(root).token()
            adapter = Qwen3TtsAdapter()
            with (
                mock.patch.dict(os.environ, {"OPENKAKAO_VOICE_TTS_OUT": str(output)}),
                mock.patch("alden_voice._require_voice_memory_budget"),
                mock.patch.dict(
                    sys.modules,
                    {
                        "qwen_tts": fake_mod,
                        "torch": types.SimpleNamespace(bfloat16="bf16", float16="fp16", backends=types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: False))),
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
            def __init__(self):
                class Talker:
                    def generate(self, **_kwargs): return None
                self.model = types.SimpleNamespace(talker=Talker())

            @classmethod
            def from_pretrained(cls, model, **kwargs):
                inst = cls()
                inst.loaded_model = model
                inst.kwargs = kwargs
                return inst

            def get_supported_speakers(self):
                return ["sohee"]

            def generate_custom_voice(self, **kwargs):
                self.generated = kwargs
                return [b"wav"], 24000

        fake_mod.Qwen3TTSModel = FakeModel
        adapter = Qwen3TtsAdapter()
        with TemporaryDirectory() as temp_dir:
            token = AbortController(Path(temp_dir)).token()
        with (
            mock.patch("alden_voice._require_voice_memory_budget"),
            mock.patch(
                "alden_voice._resolve_qwen3_tts_model_path",
                return_value=QWEN3_TTS_MODEL_ID,
            ),
            mock.patch.dict(sys.modules, {"qwen_tts": fake_mod, "torch": types.SimpleNamespace(bfloat16="bf16", float16="fp16", backends=types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: False))), "sounddevice": types.SimpleNamespace(play=lambda *a, **k: None, get_stream=lambda: types.SimpleNamespace(active=False), stop=lambda: None)}),
        ):
            adapter.speak("안녕하세요", token)  # type: ignore[arg-type]
        self.assertEqual(adapter._engine.loaded_model, QWEN3_TTS_MODEL_ID)
        self.assertTrue(adapter._engine.kwargs["local_files_only"])
        self.assertEqual(adapter._engine.generated["language"], "Korean")
        self.assertEqual(adapter._engine.generated["speaker"], "sohee")

    def test_load_refuses_before_importing_tts_when_memory_is_low(self):
        from unittest import mock

        adapter = Qwen3TtsAdapter()
        with mock.patch(
            "alden_voice._require_voice_memory_budget",
            side_effect=VoiceMemoryBudgetError("voice_memory_budget_low"),
        ):
            with self.assertRaisesRegex(VoiceMemoryBudgetError, "voice_memory_budget_low"):
                adapter._load()
        self.assertIsNone(adapter._engine)


class VoiceMemoryBudgetTests(unittest.TestCase):
    def test_pipeline_close_waits_for_owned_turn_before_disposal(self):
        import threading
        entered = threading.Event(); release = threading.Event(); closed = []
        class Stt:
            def close(self): closed.append("stt")
        class Llm:
            def generate(self, *args, **kwargs):
                entered.set(); release.wait(2); return "확인"
        class Tts:
            def close(self): closed.append("tts")
            def speak(self, *args): raise AssertionError("cancelled reply must not be spoken")
        with TemporaryDirectory() as temporary:
            pipeline = AldenVoicePipeline(stt=Stt(),llm=Llm(),tts=Tts(),token=AbortController(Path(temporary)).token())
            pipeline.submit_text("질문")
            self.assertTrue(entered.wait(1));pipeline.close();self.assertEqual(closed,[])
            release.set();pipeline._worker.join(2)
            self.assertFalse(pipeline._worker.is_alive());pipeline.close()
            self.assertEqual(closed,["tts","stt"])

    def test_resident_models_require_workspace_and_keep_pressure_reserve(self):
        for stage, minimum in (("stt", 10), ("tts", 14)):
            for resident in (False, True):
                budget = VoiceMemoryBudget(minimum * 1024**3, 0, 2 * 1024**3, 1)
                with self.subTest(stage=stage, resident=resident), mock.patch("alden_voice._read_voice_memory_budget", return_value=budget):
                    if resident:
                        self.assertEqual(_require_voice_memory_budget(stage, model_resident=True), budget)
                    else:
                        with self.assertRaisesRegex(VoiceMemoryBudgetError, "voice_memory_budget_low"):
                            _require_voice_memory_budget(stage)
            for pressure, delta in ((2, 0), (1, -1)):
                budget = VoiceMemoryBudget(minimum * 1024**3 + delta, 0, 2 * 1024**3, pressure)
                with self.subTest(stage=stage, pressure=pressure, delta=delta), mock.patch("alden_voice._read_voice_memory_budget", return_value=budget):
                    with self.assertRaisesRegex(VoiceMemoryBudgetError, "voice_memory_budget_low"):
                        _require_voice_memory_budget(stage, model_resident=True)

    def test_whisper_residency_is_the_exact_current_sdk_cache(self):
        import types
        class Samples:
            def astype(self, _dtype): return self
            def __truediv__(self, _scale): return self
        numpy = types.SimpleNamespace(frombuffer=lambda *a, **k: Samples(), int16="i16", float32="f32")
        core = types.SimpleNamespace(clear_cache=mock.Mock())
        mlx = types.ModuleType("mlx"); mlx.core = core
        whisper = types.SimpleNamespace(transcribe=mock.Mock(return_value={"text":"확인"}))
        for holder, expected in ((None, False), (types.SimpleNamespace(model=None, model_path="same"), False), (types.SimpleNamespace(model=object(), model_path="other"), False), (types.SimpleNamespace(model=object(), model_path="same"), True)):
            with self.subTest(holder=holder), TemporaryDirectory() as temporary:
                with (
                    mock.patch.dict(sys.modules, {"numpy":numpy,"mlx":mlx,"mlx.core":core,"mlx_whisper":whisper,"mlx_whisper.transcribe":types.SimpleNamespace(ModelHolder=holder)}),
                    mock.patch("alden_voice._require_voice_memory_budget") as admission,
                ):
                    self.assertEqual(MlxWhisperAdapter("same").transcribe(b"\0\0"*320,16000,AbortController(Path(temporary)).token()), "확인")
                    admission.assert_called_once_with("stt", model_resident=expected)

    def test_tts_requires_ten_gib_reclaimable_and_two_gib_swap(self):
        from unittest import mock

        enough = VoiceMemoryBudget(10 * 1024**3, 2 * 1024**3, 2 * 1024**3, 1)
        with mock.patch("alden_voice._read_voice_memory_budget", return_value=enough):
            self.assertEqual(_require_voice_memory_budget("tts"), enough)

        low_swap = VoiceMemoryBudget(22 * 1024**3 - 1, 2 * 1024**3 - 1, 4 * 1024**3, 1)
        with mock.patch("alden_voice._read_voice_memory_budget", return_value=low_swap):
            with self.assertRaisesRegex(VoiceMemoryBudgetError, "voice_memory_budget_low"):
                _require_voice_memory_budget("tts")

    def test_whisper_refuses_when_reclaimable_ram_is_below_eight_gib(self):
        from unittest import mock

        low_ram = VoiceMemoryBudget(8 * 1024**3 - 1, 4 * 1024**3, 2 * 1024**3, 1)
        with mock.patch("alden_voice._read_voice_memory_budget", return_value=low_ram):
            with self.assertRaisesRegex(VoiceMemoryBudgetError, "voice_memory_budget_low"):
                _require_voice_memory_budget("stt")

    def test_unavailable_memory_probe_fails_closed_before_whisper_import(self):
        from unittest import mock

        adapter = MlxWhisperAdapter()
        with TemporaryDirectory() as temp_dir:
            token = AbortController(Path(temp_dir)).token()
            with mock.patch(
                "alden_voice._require_voice_memory_budget",
                side_effect=VoiceMemoryBudgetError("voice_memory_budget_unavailable"),
            ):
                with self.assertRaisesRegex(
                    VoiceMemoryBudgetError, "voice_memory_budget_unavailable"
                ):
                    adapter.transcribe(b"\0\0" * 320, 16_000, token)

    def test_normal_pressure_reclaimable_reserve_accepts_unallocated_swap_at_boundary(self):
        for stage, reclaimable_gib in (("stt", 18), ("tts", 22)):
            for delta, allowed in ((0, True), (-1, False)):
                with self.subTest(stage=stage, delta=delta):
                    budget = VoiceMemoryBudget(reclaimable_gib * 1024**3 + delta, 0, 2 * 1024**3, 1)
                    with mock.patch("alden_voice._read_voice_memory_budget", return_value=budget):
                        if allowed:
                            self.assertEqual(_require_voice_memory_budget(stage), budget)
                        else:
                            with self.assertRaisesRegex(VoiceMemoryBudgetError, "voice_memory_budget_low"):
                                _require_voice_memory_budget(stage)

    def test_high_pressure_never_admits_even_with_plentiful_ram_and_swap(self):
        for pressure in (2, 4):
            budget = VoiceMemoryBudget(64 * 1024**3, 8 * 1024**3, 40 * 1024**3, pressure)
            with mock.patch("alden_voice._read_voice_memory_budget", return_value=budget):
                with self.assertRaisesRegex(VoiceMemoryBudgetError, "voice_memory_budget_low"):
                    _require_voice_memory_budget("tts")

    def test_incomplete_or_invalid_sensor_metrics_fail_closed(self):
        for budget in (
            VoiceMemoryBudget(64 * 1024**3, 8 * 1024**3),
            VoiceMemoryBudget(64 * 1024**3, 8 * 1024**3, None, 1),
            VoiceMemoryBudget(64 * 1024**3, 8 * 1024**3, 40 * 1024**3, 3),
            VoiceMemoryBudget(64 * 1024**3, 8 * 1024**3, 40 * 1024**3, True),
            VoiceMemoryBudget(64 * 1024**3, -1, 40 * 1024**3, 1),
            VoiceMemoryBudget(64 * 1024**3, 8 * 1024**3, 65 * 1024**3, 1),
        ):
            with self.subTest(budget=budget), mock.patch("alden_voice._read_voice_memory_budget", return_value=budget):
                with self.assertRaisesRegex(VoiceMemoryBudgetError, "voice_memory_budget_unavailable"):
                    _require_voice_memory_budget("stt")

    def test_cli_parser_keeps_inactive_pages_out_of_physical_reserve(self):
        from alden_voice import _parse_voice_memory_budget

        vm = "page size of 16384 bytes\nPages free: 100.\nPages inactive: 500.\nPages speculative: 20.\n"
        budget = _parse_voice_memory_budget(vm, "free = 0.00M (encrypted)", "1\n")
        self.assertEqual(budget.free_physical_bytes, 120 * 16384)
        self.assertEqual(budget.reclaimable_bytes, 620 * 16384)
        self.assertEqual(budget.swap_free_bytes, 0)
        self.assertEqual(budget.pressure_level, 1)

    def test_cli_parser_rejects_incomplete_or_malformed_reports(self):
        from alden_voice import _parse_voice_memory_budget

        vm = "page size of 16384 bytes\nPages free: 100.\nPages inactive: 500.\nPages speculative: 20.\n"
        for values in (
            (vm, "free = NaNM", "1"), (vm, "free = -1M", "1"),
            (vm, "free = 20M", ""), (vm, "free = 20M", "0"),
            (vm, "free = 20M", "3"), (vm, "free = 20M", "1 2"),
            (vm.replace("Pages speculative: 20.", ""), "free = 20M", "1"),
            (vm.replace("16384", "0"), "free = 20M", "1"),
            (vm.replace("16384", "16385"), "free = 20M", "1"),
        ):
            with self.subTest(values=values), self.assertRaisesRegex(VoiceMemoryBudgetError, "voice_memory_budget_unavailable"):
                _parse_voice_memory_budget(*values)

    def test_sensor_failure_or_timeout_does_not_admit(self):
        import subprocess
        from alden_voice import _read_voice_memory_budget

        vm = "page size of 16384 bytes\nPages free: 100.\nPages inactive: 500.\nPages speculative: 20.\n"
        success = lambda text: subprocess.CompletedProcess([], 0, stdout=text)
        for failure in (subprocess.CompletedProcess([], 1, stdout="1"), subprocess.TimeoutExpired("sysctl", 2)):
            with mock.patch("alden_voice.sys.platform", "darwin"), mock.patch("alden_voice.subprocess.run", side_effect=[success(vm), success("free = 20M"), failure]):
                with self.assertRaisesRegex(VoiceMemoryBudgetError, "voice_memory_budget_unavailable"):
                    _read_voice_memory_budget()

    def test_cached_tts_rechecks_pressure_before_next_generation(self):
        engine = mock.Mock()
        engine.get_supported_speakers.return_value = ["sohee"]
        import types
        class Talker:
            def generate(self, **_kwargs): return None
        engine.model = types.SimpleNamespace(talker=Talker())
        engine.generate_custom_voice.return_value = ([[0.0, 0.25]], 24000)
        adapter = Qwen3TtsAdapter()
        adapter._engine = engine
        good = VoiceMemoryBudget(64 * 1024**3, 0, 40 * 1024**3, 1)
        warning = VoiceMemoryBudget(64 * 1024**3, 8 * 1024**3, 40 * 1024**3, 2)
        with TemporaryDirectory() as temporary:
            token = AbortController(Path(temporary)).token()
            with mock.patch("alden_voice._read_voice_memory_budget", side_effect=[good, warning]):
                self.assertEqual(adapter.synthesize("첫 답변", token), ([0.0, 0.25], 24000))
                with self.assertRaisesRegex(VoiceMemoryBudgetError, "voice_memory_budget_low"):
                    adapter.synthesize("다음 답변", token)
        self.assertEqual(engine.generate_custom_voice.call_count, 1)


class VoiceMemoryBudgetPipelineTests(unittest.TestCase):
    def test_stt_budget_error_stops_before_llm_or_tts(self):
        class DeniedStt:
            def transcribe(self, _pcm16, _sample_rate, _token):
                raise VoiceMemoryBudgetError("voice_memory_budget_low")

        llm = FakeLlm()
        tts = FakeTts()
        with TemporaryDirectory() as temp_dir:
            pipeline = AldenVoicePipeline(
                stt=DeniedStt(),
                llm=llm,
                tts=tts,
                token=AbortController(Path(temp_dir)).token(),
            )
            result = pipeline.process_utterance(b"\0\0")

        self.assertEqual(result.state, VoiceState.ERROR)
        self.assertEqual(result.error_code, "voice_memory_budget_low")
        self.assertEqual(llm.calls, [])
        self.assertEqual(tts.calls, 0)

    def test_tts_budget_error_keeps_user_input_without_committing_unheard_reply(self):
        class DeniedTts:
            def speak(self, _text, _token):
                raise VoiceMemoryBudgetError("voice_memory_budget_low")

        llm = FakeLlm()
        with TemporaryDirectory() as temp_dir:
            pipeline = AldenVoicePipeline(
                stt=FakeStt(),
                llm=llm,
                tts=DeniedTts(),
                token=AbortController(Path(temp_dir)).token(),
            )
            result = pipeline.process_utterance(b"\0\0")

        self.assertEqual(result.state, VoiceState.ERROR)
        self.assertEqual(result.error_code, "voice_memory_budget_low")
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(pipeline._recent_conversation(), [{"role": "user", "content": "테스트 요청"}])


class LocalMlxLlmRequestTests(unittest.TestCase):
    def test_generate_ready_27b_catalog_get_then_post(self):
        captured: dict[str, object] = {}
        calls: list[tuple[str, str, float]] = []

        def fake_urlopen(request, timeout=0):
            calls.append((request.get_method(), request.full_url, timeout))
            if request.get_method() == "GET":
                return FakeJsonResponse(ready_model_catalog(QWEN38_27B_MODEL_ID))
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeJsonResponse({"choices": [{"message": {"content": " 알겠습니다. "}}]})

        with TemporaryDirectory() as temp_dir:
            token = AbortController(Path(temp_dir)).token()
            with mock.patch("alden_voice._local_urlopen", fake_urlopen):
                reply = LocalMlxLlm(state_root=Path(temp_dir)).generate(
                    "안녕",
                    token,
                    history=[
                        {"role": "user", "content": "첫 질문"},
                        {"role": "assistant", "content": "첫 답변"},
                    ],
                )
        self.assertEqual(reply, "알겠습니다.")
        self.assertEqual([call[0] for call in calls], ["GET", "POST"])
        self.assertTrue(calls[0][1].endswith("/models"))
        self.assertTrue(calls[1][1].endswith("/chat/completions"))
        self.assertEqual(calls[0][2], 3.0)
        self.assertEqual(calls[1][2], 90.0)
        self.assertEqual(captured["body"]["max_tokens"], 128)
        self.assertEqual(captured["body"]["model"], QWEN38_27B_MODEL_ID.removeprefix("mlx/"))
        self.assertEqual(
            captured["body"]["messages"][1:3],
            [
                {"role": "user", "content": "첫 질문"},
                {"role": "assistant", "content": "첫 답변"},
            ],
        )
        self.assertIn("스스로 질문을 만든 뒤 답하지 않는다", captured["body"]["messages"][0]["content"])

    def test_generate_unready_flash_fails_without_post(self):
        calls: list[str] = []

        def fake_urlopen(request, timeout=0):
            calls.append(request.get_method())
            return FakeJsonResponse(
                {
                    "data": [
                        {
                            "id": FLASH_NEXT_MODEL_ID,
                            "loaded": False,
                            "state": "unloaded",
                        }
                    ]
                }
            )

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            token = AbortController(root).token()
            with mock.patch("alden_voice._local_urlopen", fake_urlopen):
                with self.assertRaisesRegex(RuntimeError, "local_llm_model_not_ready"):
                    LocalMlxLlm(model=FLASH_NEXT_MODEL_ID, state_root=root).generate(
                        "안녕", token
                    )

        self.assertEqual(calls, ["GET"])

    def test_generate_ready_flash_catalog_get_then_post(self):
        captured: dict[str, object] = {}
        calls: list[tuple[str, str, float]] = []

        def fake_urlopen(request, timeout=0):
            calls.append((request.get_method(), request.full_url, timeout))
            if request.get_method() == "GET":
                return FakeJsonResponse(ready_model_catalog(FLASH_NEXT_MODEL_ID))
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeJsonResponse(
                {
                    "model": FLASH_NEXT_MODEL_ID.removeprefix("mlx/"),
                    "choices": [{"message": {"content": "flash reply"}}],
                }
            )

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            token = AbortController(root).token()
            with mock.patch("alden_voice._local_urlopen", fake_urlopen):
                reply = LocalMlxLlm(
                    model=FLASH_NEXT_MODEL_ID,
                    state_root=root,
                ).generate("안녕", token)

        self.assertEqual(reply, "flash reply")
        self.assertEqual([call[0] for call in calls], ["GET", "POST"])
        self.assertEqual(calls[0][2], 3.0)
        self.assertEqual(calls[1][2], 90.0)
        self.assertEqual(captured["body"]["model"], FLASH_NEXT_MODEL_ID.removeprefix("mlx/"))

    def test_generate_malformed_catalog_fails_without_post(self):
        calls: list[str] = []

        def fake_urlopen(request, timeout=0):
            calls.append(request.get_method())
            return FakeJsonResponse({"data": "not-a-list"})

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            token = AbortController(root).token()
            with mock.patch("alden_voice._local_urlopen", fake_urlopen):
                with self.assertRaisesRegex(RuntimeError, "local_llm_model_not_ready"):
                    LocalMlxLlm(state_root=root).generate("안녕", token)

        self.assertEqual(calls, ["GET"])

    def test_generate_absent_or_ambiguous_model_fails_without_post(self):
        catalogs = (
            {"data": [{"id": FLASH_NEXT_MODEL_ID, "loaded": True, "state": "ready"}]},
            {
                "data": [
                    {"id": QWEN38_27B_MODEL_ID, "loaded": True, "state": "ready"},
                    {
                        "id": QWEN38_27B_MODEL_ID.removeprefix("mlx/"),
                        "loaded": True,
                        "state": "ready",
                    },
                ]
            },
        )

        for catalog in catalogs:
            with self.subTest(catalog=catalog):
                calls: list[str] = []

                def fake_urlopen(request, timeout=0):
                    calls.append(request.get_method())
                    return FakeJsonResponse(catalog)

                with TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    token = AbortController(root).token()
                    with mock.patch("alden_voice._local_urlopen", fake_urlopen):
                        with self.assertRaisesRegex(
                            RuntimeError, "local_llm_model_not_ready"
                        ):
                            LocalMlxLlm(state_root=root).generate("안녕", token)

                self.assertEqual(calls, ["GET"])

    def test_generate_rejects_unsupported_model_before_http(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            token = AbortController(root).token()
            with mock.patch(
                "alden_voice._local_urlopen",
                side_effect=AssertionError("unsupported model must not reach gateway"),
            ) as opener:
                with self.assertRaisesRegex(RuntimeError, "model_swap_required"):
                    LocalMlxLlm(model="mlx/example/unsupported", state_root=root).generate(
                        "안녕", token
                    )
            opener.assert_not_called()

    def test_voice_generation_does_not_contact_mlx_during_model_swap(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            token = AbortController(root).token()
            with mlx_model_swap_lease(root), mock.patch(
                "alden_voice._local_urlopen",
                side_effect=AssertionError("gateway must stay closed"),
            ) as opener:
                with self.assertRaisesRegex(RuntimeError, "model_swap_in_progress"):
                    LocalMlxLlm(state_root=root).generate("안녕", token)
            opener.assert_not_called()

    def test_generate_rejects_explicit_response_from_another_model(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            token = AbortController(root).token()
            responses = iter(
                [
                    FakeJsonResponse(ready_model_catalog(QWEN38_27B_MODEL_ID)),
                    FakeJsonResponse(
                        {
                            "model": FLASH_NEXT_MODEL_ID.removeprefix("mlx/"),
                            "choices": [{"message": {"content": "wrong model"}}],
                        }
                    ),
                ]
            )
            with mock.patch(
                "alden_voice._local_urlopen", side_effect=lambda *_args, **_kwargs: next(responses)
            ):
                with self.assertRaisesRegex(RuntimeError, "local_llm_model_mismatch"):
                    LocalMlxLlm(state_root=root).generate("안녕", token)

    def test_generate_never_uses_reasoning_as_a_spoken_reply(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            token = AbortController(root).token()
            responses = iter(
                [
                    FakeJsonResponse(ready_model_catalog(QWEN38_27B_MODEL_ID)),
                    FakeJsonResponse(
                        {
                            "choices": [
                                {
                                    "message": {
                                        "content": "",
                                        "reasoning_content": "내부 추론을 음성으로 읽으면 안 됩니다.",
                                    }
                                }
                            ]
                        }
                    ),
                ]
            )
            with mock.patch(
                "alden_voice._local_urlopen", side_effect=lambda *_args, **_kwargs: next(responses)
            ):
                with self.assertRaisesRegex(RuntimeError, "local_llm_reply_empty"):
                    LocalMlxLlm(state_root=root).generate("안녕", token)

    def test_voice_persona_ends_turn_without_engagement_question(self):
        self.assertIn("스스로 질문을 만든 뒤 답하지 않는다", VOICE_PERSONA_PROMPT)
        self.assertIn("턴을 끝낸다", VOICE_PERSONA_PROMPT)

    def test_generate_rejects_non_loopback_endpoint(self):
        with self.assertRaisesRegex(ValueError, "local_llm_endpoint_invalid"):
            LocalMlxLlm("https://example.invalid/v1")


if __name__ == "__main__":
    unittest.main()
