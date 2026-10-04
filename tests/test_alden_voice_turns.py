"""Controlled races and owned loopback transport; no live mic/models/sends."""
from __future__ import annotations

import http.client
import json
import socket
import sys
import threading
import time
import unittest
import urllib.request
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from alden_abort import AbortController, AldenCancelled, AbortableJobQueue
from alden_voice import AldenVoicePipeline, VoiceState, VoiceStatusStore, _CancellableLocalResponse, LocalMlxLlm, QWEN38_27B_MODEL_ID


class Speech:
    def __init__(self):
        self.spoken = []

    def speak(self, text, token):
        token.raise_if_cancelled()
        self.spoken.append(text)


class AldenVoiceTurnTests(unittest.TestCase):
    def test_external_abort_resume_never_revives_a_queued_job(self):
        with TemporaryDirectory() as temp:
            first = AbortController(Path(temp))
            other = AbortController(Path(temp))
            queue = AbortableJobQueue(first)
            ran = []
            self.assertTrue(queue.enqueue(lambda token: ran.append(token.captured_epoch)))
            other.abort()
            other.resume_after_human_action()
            self.assertIsNone(queue.run_next())
            self.assertEqual(ran, [])
            self.assertTrue(queue.enqueue(lambda token: ran.append(token.captured_epoch)))
            queue.run_next()
            self.assertEqual(ran, [2])

    def test_abort_during_final_status_publish_does_not_commit_an_answer(self):
        class Llm:
            def generate(self, *_args, **_kwargs):
                return "답변"

        with TemporaryDirectory() as temp:
            controller = AbortController(Path(temp))
            pipeline = AldenVoicePipeline(stt=object(), llm=Llm(), tts=Speech(), token=controller.token(), status=VoiceStatusStore(Path(temp)))
            original = pipeline._publish
            fired = False

            def publish(error_code=""):
                nonlocal fired
                if pipeline.state == VoiceState.WAKE_LISTEN and pipeline._reply and not fired:
                    fired = True
                    controller.abort()
                    controller.resume_after_human_action()
                original(error_code)

            pipeline._publish = publish
            result = pipeline.process_text("요청")
            self.assertTrue(fired)
            self.assertEqual(result.state, VoiceState.ABORTED)
            self.assertTrue(result.cancelled)
            self.assertEqual(pipeline.state, VoiceState.ABORTED)
            self.assertEqual(pipeline._recent_conversation(), [{"role": "user", "content": "요청"}])
            from alden_history import read
            stored = read(Path(temp), Path('/unused'), 'voice-history-messages', chat_id=pipeline.conversation_id)
            self.assertEqual([(row['role'], row['content']) for row in stored['items']], [('user', '요청')])
            next_frame = pipeline.feed_audio(b"silence", rms=0, speech=False)
            self.assertEqual(next_frame.state, VoiceState.ABORTED)
            self.assertTrue(next_frame.cancelled)
            self.assertEqual(next_frame.reply, "")
            self.assertEqual(next_frame.conversation_id, result.conversation_id)
            pipeline.close()

    def test_confirmed_voice_text_is_persisted_in_session_order(self):
        class Llm:
            def generate(self, text, *_args, **_kwargs):
                return "답변 " + text
        with TemporaryDirectory() as temp:
            root = Path(temp)
            pipeline = AldenVoicePipeline(stt=object(), llm=Llm(), tts=Speech(),
                token=AbortController(root).token(), status=VoiceStatusStore(root))
            try:
                self.assertEqual(pipeline.process_text("첫 말씀").state, VoiceState.ENDED)
                self.assertEqual(pipeline.process_text("다음 말씀").state, VoiceState.ENDED)
                from alden_history import read
                page = read(root, Path('/unused'), 'voice-history-messages', chat_id=pipeline.conversation_id)
                self.assertEqual([(row['role'], row['content']) for row in page['items']], [
                    ('user', '첫 말씀'), ('assistant', '답변 첫 말씀'),
                    ('user', '다음 말씀'), ('assistant', '답변 다음 말씀')])
                self.assertFalse(list(root.glob('*.wav')))
            finally:
                pipeline.close()

    def test_delayed_abort_callback_preserves_jobs_enqueued_after_resume(self):
        with TemporaryDirectory() as temp:
            controller = AbortController(Path(temp))
            entered, release = threading.Event(), threading.Event()
            controller.register_cancel_callback(lambda: (entered.set(), release.wait(1)))
            queue = AbortableJobQueue(controller)
            ran = []
            queue.enqueue(lambda token: ran.append("old"))
            worker = threading.Thread(target=controller.abort)
            worker.start()
            self.assertTrue(entered.wait(1))
            controller.resume_after_human_action()
            queue.enqueue(lambda token: ran.append("new"))
            release.set()
            worker.join(timeout=1)
            self.assertFalse(worker.is_alive())
            queue.run_next()
            self.assertEqual(ran, ["new"])

    def test_synchronous_frontend_does_not_deadlock_a_previous_processing_turn(self):
        entered, release = threading.Event(), threading.Event()

        class Llm:
            def generate(self, text, token, *, history=()):
                if text == "old":
                    entered.set()
                    release.wait(2)
                return "답변"

        class Stt:
            def transcribe(self, *_args):
                return "new"

        class Frontend:
            def analyze(self, _pcm):
                return SimpleNamespace(rms=0, speech=False, stock_wake_score=0, custom_wake_score=0)

        with TemporaryDirectory() as temp:
            pipeline = AldenVoicePipeline(stt=Stt(), llm=Llm(), tts=Speech(), token=AbortController(Path(temp)).token())
            try:
                pipeline.submit_text("old")
                self.assertTrue(entered.wait(1))
                pipeline.state = VoiceState.USER_LISTEN
                pipeline.ring.append(b"\1\0" * 320)
                pipeline._speech_frames = 1
                pipeline._silence_frames = 11
                results = []
                worker = threading.Thread(target=lambda: results.append(pipeline.feed_frontend_frame(Frontend(), b"silence")), daemon=True)
                worker.start()
                deadline = time.monotonic() + 1
                while pipeline.turn_id < 2 and time.monotonic() < deadline:
                    time.sleep(.005)
                self.assertEqual(pipeline.turn_id, 2)
                release.set()
                worker.join(timeout=1)
                self.assertFalse(worker.is_alive(), "state/processing lock inversion")
                self.assertEqual(results[0].transcript, "new")
            finally:
                release.set()
                pipeline.close()

    def test_playback_never_enters_detector_and_next_frame_resets_temporal_state(self):
        class Frontend:
            calls = 0
            resets = 0

            def analyze(self, pcm16):
                self.calls += 1
                return SimpleNamespace(rms=0, speech=False, stock_wake_score=0, custom_wake_score=0)

            def reset_for_independent_clip(self):
                self.resets += 1

        with TemporaryDirectory() as temp:
            pipeline = AldenVoicePipeline(stt=object(), llm=object(), tts=Speech(), token=AbortController(Path(temp)).token())
            frontend = Frontend()
            pipeline.state = VoiceState.SPEAKING
            for _ in range(20):
                pipeline.feed_frontend_frame(frontend, b"playback")
            self.assertEqual(frontend.calls, 0)
            pipeline.state = VoiceState.WAKE_LISTEN
            pipeline.feed_frontend_frame(frontend, b"silence")
            pipeline.feed_frontend_frame(frontend, b"silence")
            self.assertEqual(frontend.resets, 1)
            self.assertEqual(frontend.calls, 2)
            self.assertEqual(pipeline.state, VoiceState.WAKE_LISTEN)
            pipeline.close()

    def test_replacement_input_keeps_only_one_pending_turn_and_close_rejects_input(self):
        started, release = threading.Event(), threading.Event()
        calls = []

        class Llm:
            def generate(self, text, token, *, history=()):
                calls.append(text)
                if text == "first":
                    started.set()
                    release.wait(2)
                return "답변"

        with TemporaryDirectory() as temp:
            speech = Speech()
            pipeline = AldenVoicePipeline(stt=object(), llm=Llm(), tts=speech, token=AbortController(Path(temp)).token())
            try:
                pipeline.submit_text("first")
                self.assertTrue(started.wait(1))
                running = pipeline._latest_future
                replaced = []
                for index in range(100):
                    pipeline.submit_text(str(index))
                    replaced.append(pipeline._latest_future)
                self.assertTrue(all(future.cancelled() for future in replaced[:-1]))
                self.assertEqual(pipeline._pending_turn[2], "99")
                pipeline.close()
                self.assertFalse(pipeline.submit_text("after close"))
                self.assertEqual(pipeline.process_text("after close").error_code, "input_ignored")
                pipeline.submit_utterance(b"audio after close")
                release.set()
                self.assertEqual(running.result(timeout=1).state, VoiceState.ABORTED)
                self.assertEqual(calls, ["first"])
                self.assertEqual(speech.spoken, [])
                self.assertIsNone(pipeline._pending_turn)
                pipeline._worker.join(timeout=1)
                self.assertFalse(pipeline._worker.is_alive())
            finally:
                release.set()
                pipeline.close()

    def test_late_generation_is_discarded_and_new_turn_uses_confirmed_user_input(self):
        started, release = threading.Event(), threading.Event()
        history, tokens = {}, []

        class Llm:
            def generate(self, text, token, *, history=()):
                tokens.append(token)
                if text == "회의는 3층입니다.":
                    started.set()
                    if not release.wait(2):
                        raise AssertionError("fixture release missing")
                    return "취소된 옛 답변"
                # This local adapter deliberately ignores cancellation on return.
                history_by_text[text] = history
                return "3층으로 가십시오."

        history_by_text = history
        with TemporaryDirectory() as temp:
            speech = Speech()
            root = Path(temp)
            pipeline = AldenVoicePipeline(stt=object(), llm=Llm(), tts=speech, token=AbortController(root).token(), status=VoiceStatusStore(root))
            try:
                self.assertTrue(pipeline.submit_text("회의는 3층입니다.", event_id="first"))
                self.assertTrue(started.wait(1))
                old_future = pipeline._latest_future
                self.assertTrue(pipeline.submit_text("그럼 어디로 가죠?", event_id="second"))
                latest_future = pipeline._latest_future
                self.assertTrue(tokens[0].is_cancelled())
                release.set()
                old = old_future.result(timeout=1)
                latest = latest_future.result(timeout=1)
                state = json.loads(pipeline.status.path.read_text())
                self.assertEqual(old.state, VoiceState.ABORTED)
                self.assertEqual(old.error_code, "turn_superseded")
                self.assertTrue(old.cancelled)
                self.assertEqual(latest.turn_id, 2)
                self.assertEqual(latest.conversation_id, old.conversation_id)
                self.assertEqual(speech.spoken, ["3층으로 가십시오."])
                self.assertEqual(history["그럼 어디로 가죠?"], [{"role": "user", "content": "회의는 3층입니다."}])
                self.assertEqual(pipeline._recent_conversation()[-1], {"role": "assistant", "content": "3층으로 가십시오."})
                self.assertEqual(state["turn_id"], 2)
                self.assertEqual(state["context_version"], latest.context_version)
                self.assertFalse(state["cancelled"])
            finally:
                release.set()
                pipeline.close()

    def test_late_stt_cannot_replace_a_newer_confirmed_turn(self):
        started, release = threading.Event(), threading.Event()

        class Stt:
            def transcribe(self, _audio, _rate, _token):
                started.set()
                release.wait(2)
                return "이미 취소된 발화"

        class Llm:
            def generate(self, text, token, *, history=()):
                token.raise_if_cancelled()
                return "현재 답변"

        with TemporaryDirectory() as temp:
            speech = Speech()
            pipeline = AldenVoicePipeline(stt=Stt(), llm=Llm(), tts=speech, token=AbortController(Path(temp)).token(), status=VoiceStatusStore(Path(temp)))
            try:
                pipeline.submit_utterance(b"\1\0" * 320)
                self.assertTrue(started.wait(1))
                old_future = pipeline._latest_future
                pipeline.submit_text("새로운 입력")
                current_future = pipeline._latest_future
                accepted_state = json.loads(pipeline.status.path.read_text())
                self.assertEqual(accepted_state["turn_id"], 2)
                self.assertEqual(accepted_state["state"], VoiceState.GENERATING.value)
                release.set()
                self.assertEqual(old_future.result(timeout=1).state, VoiceState.ABORTED)
                self.assertEqual(current_future.result(timeout=1).transcript, "새로운 입력")
                self.assertEqual(speech.spoken, ["현재 답변"])
                self.assertNotIn("이미 취소된 발화", str(pipeline._recent_conversation()))
            finally:
                release.set()
                pipeline.close()

    def test_duplicate_or_nonuser_event_never_enters_conversation(self):
        class Llm:
            def generate(self, text, token, *, history=()):
                return "확인했습니다."

        with TemporaryDirectory() as temp:
            speech = Speech()
            pipeline = AldenVoicePipeline(stt=object(), llm=Llm(), tts=speech, token=AbortController(Path(temp)).token())
            result = pipeline.process_text("내 요청", event_id="input-1")
            for source in ["playback", "tool", "system", "automation", "assistant"]:
                ignored = pipeline.process_text("가짜 사용자 입력", source=source, event_id="input-1")
                self.assertEqual(ignored.error_code, "input_ignored")
            self.assertEqual(pipeline.process_text("중복 요청", event_id="input-1").error_code, "input_ignored")
            self.assertEqual(pipeline.turn_id, result.turn_id)
            self.assertEqual(speech.spoken, ["확인했습니다."])
            self.assertEqual(len(pipeline._recent_conversation()), 2)
            pipeline.close()

    def test_resume_does_not_restart_an_old_voice_session(self):
        class Llm:
            def generate(self, text, token, *, history=()):
                self.fail("old session must not infer")

        with TemporaryDirectory() as temp:
            controller = AbortController(Path(temp))
            pipeline = AldenVoicePipeline(stt=object(), llm=Llm(), tts=Speech(), token=controller.token())
            controller.abort()
            controller.resume_after_human_action()
            result = pipeline.process_text("재개 전 세션")
            self.assertEqual(result.state, VoiceState.ABORTED)
            self.assertTrue(result.cancelled)
            self.assertEqual(pipeline._recent_conversation(), [])
            pipeline.close()

    def test_microphone_polling_can_interrupt_generation_without_waiting_for_it(self):
        started, release = threading.Event(), threading.Event()

        class Llm:
            def generate(self, text, token, *, history=()):
                started.set()
                release.wait(2)
                return "늦은 답변"

        with TemporaryDirectory() as temp:
            speech = Speech()
            pipeline = AldenVoicePipeline(stt=object(), llm=Llm(), tts=speech, token=AbortController(Path(temp)).token())
            try:
                pipeline.submit_text("먼저 처리")
                self.assertTrue(started.wait(1))
                old = pipeline._latest_future
                pipeline.feed_audio(b"\1\0" * 320, rms=.1, speech=True, stock_wake_score=1)
                self.assertEqual(pipeline.state, VoiceState.USER_LISTEN)
                release.set()
                self.assertEqual(old.result(timeout=1).state, VoiceState.ABORTED)
                self.assertEqual(pipeline.state, VoiceState.USER_LISTEN)
                self.assertEqual(speech.spoken, [])
                self.assertIsNone(pipeline.poll_result())
            finally:
                release.set()
                pipeline.close()


class LocalSocketCancellationTests(unittest.TestCase):
    def test_readiness_get_receives_token_and_preserves_cancellation(self):
        with TemporaryDirectory() as temp:
            token = AbortController(Path(temp)).token()

            def opener(request, **_kwargs):
                self.assertEqual(request.get_method(), "GET")
                self.assertIs(request._alden_abort_token, token)
                token.cancel()
                token.raise_if_cancelled()

            with patch("alden_voice._local_urlopen", opener):
                with self.assertRaises(AldenCancelled):
                    LocalMlxLlm(state_root=Path(temp)).generate("요청", token)

    def test_cancellation_closes_socket_while_waiting_for_headers(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(2)
        seen, disconnected = threading.Event(), threading.Event()

        def server():
            with listener:
                connection, _ = listener.accept()
                with connection:
                    connection.settimeout(2)
                    pending = b""
                    while b"\r\n\r\n" not in pending:
                        pending += connection.recv(4096)
                    head, body = pending.split(b"\r\n\r\n", 1)
                    length = int(next(line.split(b":", 1)[1].strip() for line in head.split(b"\r\n") if line.lower().startswith(b"content-length:")))
                    while len(body) < length:
                        body += connection.recv(4096)
                    seen.set()
                    if connection.recv(1) == b"":
                        disconnected.set()

        server_thread = threading.Thread(target=server, daemon=True)
        server_thread.start()
        real_connection = http.client.HTTPConnection
        observed = []
        with TemporaryDirectory() as temp:
            token = AbortController(Path(temp)).token()
            request = urllib.request.Request("http://127.0.0.1:11234/v1/chat/completions", data=b"{}")

            def read():
                try:
                    with _CancellableLocalResponse(request, 90, token) as response:
                        response.read(1024)
                except Exception as error:
                    observed.append(error)

            with patch("alden_voice.http.client.HTTPConnection", side_effect=lambda *_args, **kwargs: real_connection("127.0.0.1", listener.getsockname()[1], **kwargs)):
                client_thread = threading.Thread(target=read)
                client_thread.start()
                self.assertTrue(seen.wait(1))
                started = time.perf_counter()
                token.cancel()
                client_thread.join(timeout=1)
                elapsed = time.perf_counter() - started
                self.assertFalse(client_thread.is_alive())
                self.assertTrue(disconnected.wait(1))
                self.assertIsInstance(observed[0], AldenCancelled)
                self.assertLess(elapsed, .5)
        server_thread.join(timeout=1)


class LocalStreamTests(unittest.TestCase):
    def generate(self, chunks, *, done=True):
        from tests.test_alden_unit3 import FakeJsonResponse, ready_model_catalog

        class Stream:
            headers = {"Content-Type": "text/event-stream"}

            def __init__(self):
                self.lines = iter([b"data: " + json.dumps(chunk).encode() + b"\n\n" for chunk in chunks] + ([b"data: [DONE]\n\n"] if done else []))

            def __enter__(self):
                return self

            def __exit__(self, *_values):
                return False

            def readline(self, _limit=-1):
                return next(self.lines, b"")

        def opener(request, **_kwargs):
            if request.get_method() == "GET":
                return FakeJsonResponse(ready_model_catalog(QWEN38_27B_MODEL_ID))
            self.assertTrue(json.loads(request.data)["stream"])
            return Stream()

        with TemporaryDirectory() as temp, patch("alden_voice._local_urlopen", opener):
            adapter = LocalMlxLlm(state_root=Path(temp))
            result = adapter.generate("현재 요청", AbortController(Path(temp)).token())
            return result, adapter.last_metrics

    def test_stream_uses_visible_content_and_records_usage_without_reasoning(self):
        result, metrics = self.generate([
            {"model": QWEN38_27B_MODEL_ID, "choices": [{"delta": {"reasoning_content": "내부 추론"}}]},
            {"model": QWEN38_27B_MODEL_ID, "choices": [{"delta": {"content": "확인"}}]},
            {"choices": [{"delta": {"content": "했습니다."}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"completion_tokens": 6, "prompt_tokens": 20}},
        ])
        self.assertEqual(result, "확인했습니다.")
        self.assertEqual(metrics["usage"]["completion_tokens"], 6)
        self.assertLessEqual(metrics["first_model_token_seconds"], metrics["first_visible_token_seconds"])

    def test_truncated_or_reasoning_only_stream_is_not_an_answer(self):
        with self.assertRaisesRegex(RuntimeError, "stream_incomplete"):
            self.generate([{"choices": [{"delta": {"content": "부분 응답"}}]}], done=False)
        with self.assertRaisesRegex(RuntimeError, "reply_empty"):
            self.generate([{"choices": [{"delta": {"reasoning_content": "내부 추론"}}]}])

    def test_stream_model_mismatch_and_nonstring_content_fail_closed(self):
        with self.assertRaisesRegex(RuntimeError, "model_mismatch"):
            self.generate([{"model": "other/model", "choices": [{"delta": {"content": "잘못된 답변"}}]}])
        with self.assertRaisesRegex(RuntimeError, "response_invalid"):
            self.generate([{"choices": [{"delta": {"content": ["잘못된 형식"]}}]}])


if __name__ == "__main__":
    unittest.main()
