"""Focused contract tests for Alden's local multilingual E5 adapter."""

from __future__ import annotations

from contextlib import contextmanager
import json
import socket
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from scripts import alden_local_embedding_server as SERVER  # noqa: E402
from scripts import auto_reply_knowledge_graph as KG  # noqa: E402


class FakeEngine:
    model_id = SERVER.MODEL_ID
    revision = SERVER.MODEL_REVISION
    license = SERVER.MODEL_LICENSE
    dimensions = SERVER.MODEL_DIMENSIONS

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str]] = []

    def embed(self, texts, input_type):
        values = list(texts)
        if not values or len(values) > SERVER.MAX_BATCH_SIZE:
            raise SERVER.EmbeddingRequestError(
                "embedding_batch_out_of_bounds",
                f"input batch must contain 1..{SERVER.MAX_BATCH_SIZE} texts",
            )
        for index, value in enumerate(values):
            if not isinstance(value, str) or not value.strip():
                raise SERVER.EmbeddingRequestError(
                    "embedding_input_invalid", f"input {index} must be a non-empty string"
                )
            if len(value.strip()) > SERVER.MAX_INPUT_CHARS:
                raise SERVER.EmbeddingRequestError(
                    "embedding_input_too_large",
                    f"input {index} exceeds {SERVER.MAX_INPUT_CHARS} characters",
                    status=413,
                )
        self.calls.append((values, input_type))
        vectors = [[1.0, float(index + 1)] for index in range(len(values))]
        return SERVER.EmbeddingBatch(vectors=vectors, prompt_tokens=len(values) * 3)


@contextmanager
def running_server(engine: FakeEngine):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    server = SERVER.create_server("127.0.0.1", port, engine)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}", engine
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def request_json(url: str, *, method: str = "GET", payload=None):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=2.0) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


class LocalEmbeddingServerTests(unittest.TestCase):
    def test_idle_cache_cleanup_preserves_result_and_releases_serialized_work(self):
        engine = SERVER.LocalE5Engine.__new__(SERVER.LocalE5Engine)
        engine._lock = threading.Lock()
        engine.mx = mock.Mock()
        engine.mx.get_cache_memory.side_effect = [SERVER.MAX_ALLOCATOR_CACHE_BYTES + 1, 0]
        result = SERVER.EmbeddingBatch([[1.0]], 1)
        engine._embed_normalized = mock.Mock(side_effect=[result, RuntimeError("failure")])
        self.assertIs(engine.embed(["first"], "query"), result)
        engine.mx.clear_cache.assert_called_once()
        with self.assertRaisesRegex(RuntimeError,"failure"):
            engine.embed(["second"],"passage")
        self.assertFalse(engine._lock.locked())
        self.assertEqual(engine._embed_normalized.call_args_list,
                         [mock.call(["query: first"]),mock.call(["passage: second"])])

    def test_discovery_observes_an_actual_in_flight_request_and_clears_failure(self):
        entered, release = threading.Event(), threading.Event()
        engine = FakeEngine()
        def blocked(*_args):
            entered.set()
            release.wait(2)
            raise RuntimeError("probe failure")
        engine.embed = blocked
        with running_server(engine) as (base_url, _engine):
            def invoke():
                try:request_json(base_url+"/v1/embeddings",method="POST",payload={"model":SERVER.MODEL_ID,"input":["probe"],"input_type":"query"})
                except urllib.error.HTTPError as error:error.close()
            worker=threading.Thread(target=invoke);worker.start()
            self.assertTrue(entered.wait(1))
            _,body=request_json(base_url+"/v1/models")
            self.assertEqual(body["runtime"]["in_flight"],1)
            release.set();worker.join(2)
            _,body=request_json(base_url+"/v1/models")
            self.assertEqual(body["runtime"]["in_flight"],0)

    def test_runtime_uses_lightweight_tokenizers_backend_without_transformers(self):
        source = (ROOT / "scripts/alden_local_embedding_server.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("from tokenizers import Tokenizer", source)
        self.assertNotIn("from transformers import", source)

    def test_default_port_matches_graphrag_dedicated_adapter_endpoint(self):
        self.assertEqual(SERVER.DEFAULT_HOST, "127.0.0.1")
        self.assertEqual(SERVER.DEFAULT_PORT, 11236)
        self.assertEqual(
            KG.DEFAULT_DENSE_EMBEDDING_URL,
            f"http://{SERVER.DEFAULT_HOST}:{SERVER.DEFAULT_PORT}/v1/embeddings",
        )

    def test_models_advertises_exact_pinned_ready_embedding_model(self):
        with running_server(FakeEngine()) as (base_url, _engine):
            status, body = request_json(f"{base_url}/v1/models")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["data"]), 1)
        row = body["data"][0]
        self.assertEqual(row["id"], SERVER.MODEL_ID)
        self.assertIs(row["loaded"], True)
        self.assertEqual(row["state"], "ready")
        self.assertIn("embedding", row["capabilities"])
        self.assertEqual(row["revision"], SERVER.MODEL_REVISION)
        self.assertEqual(row["license"], "MIT")
        self.assertEqual(row["dimensions"], 384)

    def test_embeddings_requires_explicit_role_and_preserves_order(self):
        engine = FakeEngine()
        with running_server(engine) as (base_url, _engine):
            with self.assertRaises(urllib.error.HTTPError) as raised:
                request_json(
                    f"{base_url}/v1/embeddings",
                    method="POST",
                    payload={"model": SERVER.MODEL_ID, "input": ["하나"]},
                )
            self.assertEqual(raised.exception.code, 400)
            raised.exception.close()

            status, body = request_json(
                f"{base_url}/v1/embeddings",
                method="POST",
                payload={
                    "model": SERVER.MODEL_ID,
                    "input": ["첫째", "둘째"],
                    "input_type": "query",
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(body["model"], SERVER.MODEL_ID)
            self.assertEqual([row["index"] for row in body["data"]], [0, 1])
            self.assertEqual(engine.calls[-1], (["첫째", "둘째"], "query"))

            request_json(
                f"{base_url}/v1/embeddings",
                method="POST",
                payload={
                    "model": SERVER.MODEL_ID,
                    "input": "문서",
                    "input_type": "passage",
                },
            )
            self.assertEqual(engine.calls[-1], (["문서"], "passage"))

    def test_fail_closed_bounds_and_loopback_binding(self):
        with self.assertRaisesRegex(ValueError, "exactly 127.0.0.1"):
            SERVER.create_server("0.0.0.0", 11236, FakeEngine())

        with running_server(FakeEngine()) as (base_url, _engine):
            with self.assertRaises(urllib.error.HTTPError) as batch_error:
                request_json(
                    f"{base_url}/v1/embeddings",
                    method="POST",
                    payload={
                        "model": SERVER.MODEL_ID,
                        "input": ["x"] * (SERVER.MAX_BATCH_SIZE + 1),
                        "input_type": "query",
                    },
                )
            self.assertEqual(batch_error.exception.code, 400)
            batch_error.exception.close()

            with self.assertRaises(urllib.error.HTTPError) as size_error:
                request_json(
                    f"{base_url}/v1/embeddings",
                    method="POST",
                    payload={
                        "model": SERVER.MODEL_ID,
                        "input": "x" * (SERVER.MAX_INPUT_CHARS + 1),
                        "input_type": "query",
                    },
                )
            self.assertEqual(size_error.exception.code, 413)
            size_error.exception.close()

    def test_graph_client_sends_query_and_passage_roles_to_adapter(self):
        engine = FakeEngine()
        with running_server(engine) as (base_url, _engine), mock.patch.object(
            KG, "DENSE_EMBEDDING_URL", f"{base_url}/v1/embeddings"
        ), mock.patch.object(KG, "DENSE_EMBEDDING_MODEL", SERVER.MODEL_ID), mock.patch.object(
            KG, "_DENSE_MODEL_CACHE", None
        ):
            selected = KG._active_dense_embedding_model()
            self.assertEqual(selected, SERVER.MODEL_ID)
            query_rows = KG._local_dense_embeddings_unleased(
                ["검색 질의"],
                input_type=KG.DENSE_INPUT_TYPE_QUERY,
                model_id=selected,
            )
            passage_rows = KG._local_dense_embeddings_unleased(
                ["색인 문서"],
                input_type=KG.DENSE_INPUT_TYPE_PASSAGE,
                model_id=selected,
            )

        self.assertEqual(len(query_rows), 1)
        self.assertEqual(len(passage_rows), 1)
        self.assertEqual(
            engine.calls,
            [(["검색 질의"], "query"), (["색인 문서"], "passage")],
        )


if __name__ == "__main__":
    unittest.main()
