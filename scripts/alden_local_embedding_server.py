#!/usr/bin/env python3
"""Loopback-only OpenAI-compatible embeddings for Alden GraphRAG.

This server intentionally supports one pinned local model.  It never downloads
weights or calls a remote inference service.  The E5 retrieval role is an
explicit request field so query and passage prefixes cannot be mixed silently.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import threading
from typing import Any, Sequence


MODEL_REPO_ID = "mlx-community/multilingual-e5-small-mlx"
MODEL_REVISION = "5030c7625865046d350eeea28f427d80353d0ac0"
MODEL_ID = f"{MODEL_REPO_ID}@{MODEL_REVISION}"
MODEL_LICENSE = "MIT"
MODEL_DIMENSIONS = 384
MODEL_MAX_TOKENS = 512
EXPECTED_WEIGHT_BYTES = 235_330_776
TOKENIZER_JSON = "tokenizer.json"
TOKENIZER_SPECIAL_TOKEN_IDS = {
    "<s>": 0,
    "<pad>": 1,
    "</s>": 2,
    "<unk>": 3,
    "<mask>": 250001,
}
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 11236
MAX_BATCH_SIZE = 8
MAX_INPUT_CHARS = 8_192
MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 512 * 1024
MAX_ALLOCATOR_CACHE_BYTES = 512 * 1024 * 1024
INPUT_TYPES = frozenset({"query", "passage"})


def default_model_path() -> Path:
    return (
        Path.home()
        / ".cache"
        / "huggingface"
        / "hub"
        / "models--mlx-community--multilingual-e5-small-mlx"
        / "snapshots"
        / MODEL_REVISION
    )


class EmbeddingRequestError(ValueError):
    def __init__(self, code: str, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class EmbeddingBatch:
    vectors: list[list[float]]
    prompt_tokens: int


def _validate_model_files(model_path: Path) -> dict[str, Any]:
    resolved = model_path.expanduser().resolve()
    if not resolved.is_dir():
        raise RuntimeError(f"pinned embedding model directory is missing: {resolved}")
    if resolved.name != MODEL_REVISION:
        raise RuntimeError(
            "embedding model path must point at the pinned snapshot revision "
            f"{MODEL_REVISION}"
        )

    config_path = resolved / "config.json"
    readme_path = resolved / "README.md"
    weights_path = resolved / "weights.00.safetensors"
    tokenizer_path = resolved / TOKENIZER_JSON
    for required in (config_path, readme_path, weights_path, tokenizer_path):
        if not required.is_file():
            raise RuntimeError(f"pinned embedding model file is missing: {required.name}")

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("pinned embedding config is unreadable") from exc

    expected = {
        "model_type": "bert",
        "hidden_size": MODEL_DIMENSIONS,
        "num_hidden_layers": 12,
        "num_attention_heads": 12,
        "intermediate_size": 1536,
        "max_position_embeddings": MODEL_MAX_TOKENS,
        "vocab_size": 250037,
    }
    for key, value in expected.items():
        if config.get(key) != value:
            raise RuntimeError(
                f"pinned embedding config mismatch: {key}={config.get(key)!r}"
            )
    if config.get("architectures") != ["BertModel"]:
        raise RuntimeError("pinned embedding architecture mismatch")
    if weights_path.stat().st_size != EXPECTED_WEIGHT_BYTES:
        raise RuntimeError("pinned embedding weight size mismatch")
    try:
        readme_head = readme_path.read_text(encoding="utf-8")[:16_384].casefold()
    except (OSError, UnicodeError) as exc:
        raise RuntimeError("pinned embedding model card is unreadable") from exc
    if "license: mit" not in readme_head:
        raise RuntimeError("pinned embedding license metadata mismatch")
    return config


def _build_mlx_bert(config: dict[str, Any], mx: Any, nn: Any) -> Any:
    """Build the small BERT encoder shape used by the pinned MLX checkpoint."""

    hidden_size = int(config["hidden_size"])
    num_heads = int(config["num_attention_heads"])
    head_size = hidden_size // num_heads

    class BertEmbeddings(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.word_embeddings = nn.Embedding(int(config["vocab_size"]), hidden_size)
            self.position_embeddings = nn.Embedding(
                int(config["max_position_embeddings"]), hidden_size
            )
            self.token_type_embeddings = nn.Embedding(
                int(config.get("type_vocab_size", 2)), hidden_size
            )
            self.LayerNorm = nn.LayerNorm(
                hidden_size, eps=float(config.get("layer_norm_eps", 1e-12))
            )

        def __call__(self, input_ids: Any, token_type_ids: Any | None = None) -> Any:
            seq_length = input_ids.shape[1]
            position_ids = mx.arange(seq_length, dtype=mx.int32)[None, :]
            if token_type_ids is None:
                token_type_ids = mx.zeros_like(input_ids)
            hidden = self.word_embeddings(input_ids)
            hidden = hidden + self.position_embeddings(position_ids)
            hidden = hidden + self.token_type_embeddings(token_type_ids)
            return self.LayerNorm(hidden)

    class BertSelfAttention(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.query = nn.Linear(hidden_size, hidden_size)
            self.key = nn.Linear(hidden_size, hidden_size)
            self.value = nn.Linear(hidden_size, hidden_size)

        @staticmethod
        def _heads(value: Any) -> Any:
            shape = value.shape[:-1] + (num_heads, head_size)
            return value.reshape(shape).transpose(0, 2, 1, 3)

        def __call__(self, hidden: Any, attention_mask: Any) -> Any:
            query = self._heads(self.query(hidden))
            key = self._heads(self.key(hidden))
            value = self._heads(self.value(hidden))
            scores = mx.matmul(query, key.transpose(0, 1, 3, 2))
            scores = scores / math.sqrt(head_size)
            scores = scores + attention_mask
            probs = mx.softmax(scores, axis=-1)
            context = mx.matmul(probs, value).transpose(0, 2, 1, 3)
            return context.reshape(context.shape[:-2] + (hidden_size,))

    class BertSelfOutput(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.dense = nn.Linear(hidden_size, hidden_size)
            self.LayerNorm = nn.LayerNorm(
                hidden_size, eps=float(config.get("layer_norm_eps", 1e-12))
            )

        def __call__(self, hidden: Any, residual: Any) -> Any:
            return self.LayerNorm(self.dense(hidden) + residual)

    class BertAttention(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.self = BertSelfAttention()
            self.output = BertSelfOutput()

        def __call__(self, hidden: Any, attention_mask: Any) -> Any:
            return self.output(self.self(hidden, attention_mask), hidden)

    class BertIntermediate(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.dense = nn.Linear(hidden_size, int(config["intermediate_size"]))
            self.activation = nn.GELU()

        def __call__(self, hidden: Any) -> Any:
            return self.activation(self.dense(hidden))

    class BertOutput(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.dense = nn.Linear(int(config["intermediate_size"]), hidden_size)
            self.LayerNorm = nn.LayerNorm(
                hidden_size, eps=float(config.get("layer_norm_eps", 1e-12))
            )

        def __call__(self, hidden: Any, residual: Any) -> Any:
            return self.LayerNorm(self.dense(hidden) + residual)

    class BertLayer(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.attention = BertAttention()
            self.intermediate = BertIntermediate()
            self.output = BertOutput()

        def __call__(self, hidden: Any, attention_mask: Any) -> Any:
            attended = self.attention(hidden, attention_mask)
            return self.output(self.intermediate(attended), attended)

    class BertEncoder(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.layer = [BertLayer() for _ in range(int(config["num_hidden_layers"]))]

        def __call__(self, hidden: Any, attention_mask: Any) -> Any:
            for layer in self.layer:
                hidden = layer(hidden, attention_mask)
            return hidden

    class BertPooler(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.dense = nn.Linear(hidden_size, hidden_size)

        def __call__(self, hidden: Any) -> Any:
            return mx.tanh(self.dense(hidden[:, 0]))

    class BertModel(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.embeddings = BertEmbeddings()
            self.encoder = BertEncoder()
            # Retained for strict checkpoint loading; E5 retrieval uses mean pooling.
            self.pooler = BertPooler()

        def __call__(self, input_ids: Any, attention_mask: Any) -> Any:
            hidden = self.embeddings(input_ids)
            extended = attention_mask[:, None, None, :].astype(mx.float32)
            extended = (1.0 - extended) * -10000.0
            return self.encoder(hidden, extended)

    return BertModel()


class LocalE5Engine:
    """Pinned multilingual-e5-small encoder loaded only from local files."""

    model_id = MODEL_ID
    revision = MODEL_REVISION
    license = MODEL_LICENSE
    dimensions = MODEL_DIMENSIONS

    def __init__(self, model_path: Path) -> None:
        # Keep every optional Hugging Face helper offline even though tokenization
        # uses only the pinned tokenizer.json through the lightweight tokenizers
        # package and performs no hub lookup.
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        self.model_path = model_path.expanduser().resolve()
        self.config = _validate_model_files(self.model_path)
        self._lock = threading.Lock()
        self._load_runtime()

    def _load_runtime(self) -> None:
        try:
            import mlx.core as mx
            import mlx.nn as nn
            import numpy as np
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise RuntimeError(
                "local embedding runtime is incomplete; use the project voice venv "
                "with mlx, numpy, and tokenizers installed"
            ) from exc

        self.mx = mx
        self.np = np
        # This process serves only the pinned E5 model. The default cache can
        # retain many gigabytes across batch/sequence shapes, competing with
        # voice and the generation service without representing live tensors.
        mx.set_cache_limit(MAX_ALLOCATOR_CACHE_BYTES)
        try:
            self.tokenizer = Tokenizer.from_file(str(self.model_path / TOKENIZER_JSON))
        except Exception as exc:
            raise RuntimeError("pinned embedding tokenizer is unreadable") from exc
        for token, expected_id in TOKENIZER_SPECIAL_TOKEN_IDS.items():
            if self.tokenizer.token_to_id(token) != expected_id:
                raise RuntimeError(
                    f"pinned embedding tokenizer special token mismatch: {token}"
                )
        self.tokenizer.no_truncation()
        self.tokenizer.enable_padding(
            direction="right",
            pad_id=TOKENIZER_SPECIAL_TOKEN_IDS["<pad>"],
            pad_type_id=0,
            pad_token="<pad>",
        )
        self.model = _build_mlx_bert(self.config, mx, nn)
        weights = mx.load(str(self.model_path / "weights.00.safetensors"))
        weights = {key: value for key, value in weights.items() if "position_ids" not in key}
        self.model.load_weights(list(weights.items()), strict=True)
        self.model.eval()
        mx.eval(self.model.parameters())

    def embed(self, texts: Sequence[str], input_type: str) -> EmbeddingBatch:
        role = str(input_type).strip().casefold()
        if role not in INPUT_TYPES:
            raise EmbeddingRequestError(
                "embedding_input_type_required",
                "input_type must be exactly 'query' or 'passage'",
            )
        if not texts or len(texts) > MAX_BATCH_SIZE:
            raise EmbeddingRequestError(
                "embedding_batch_out_of_bounds",
                f"input batch must contain 1..{MAX_BATCH_SIZE} texts",
            )

        normalized: list[str] = []
        for index, value in enumerate(texts):
            if not isinstance(value, str):
                raise EmbeddingRequestError(
                    "embedding_input_invalid", f"input {index} must be a string"
                )
            text = value.strip()
            if not text:
                raise EmbeddingRequestError(
                    "embedding_input_empty", f"input {index} must be non-empty"
                )
            if len(text) > MAX_INPUT_CHARS:
                raise EmbeddingRequestError(
                    "embedding_input_too_large",
                    f"input {index} exceeds {MAX_INPUT_CHARS} characters",
                    status=413,
                )
            normalized.append(f"{role}: {text}")

        with self._lock:
            try:
                return self._embed_normalized(normalized)
            finally:
                # The helper's tensor locals are released before trimming.
                # MLX's soft limit evicts on allocation; the final release can
                # overshoot it until the next request, so trim that idle excess.
                if self.mx.get_cache_memory() > MAX_ALLOCATOR_CACHE_BYTES:
                    self.mx.clear_cache()

    def _embed_normalized(self, normalized: list[str]) -> EmbeddingBatch:
        encoded = self.tokenizer.encode_batch(normalized, add_special_tokens=True)
        input_ids_np = self.np.asarray([row.ids for row in encoded], dtype=self.np.int32)
        attention_np = self.np.asarray([row.attention_mask for row in encoded], dtype=self.np.int32)
        token_counts = attention_np.sum(axis=1)
        for index, count in enumerate(token_counts.tolist()):
            if int(count) > MODEL_MAX_TOKENS:
                raise EmbeddingRequestError(
                    "embedding_input_too_long",
                    f"input {index} has {int(count)} tokens; maximum is {MODEL_MAX_TOKENS}",
                )

        mx = self.mx
        input_ids = mx.array(input_ids_np, dtype=mx.int32)
        attention = mx.array(attention_np, dtype=mx.float32)
        hidden = self.model(input_ids, attention)
        mask = attention[:, :, None]
        pooled = mx.sum(hidden * mask, axis=1) / mx.maximum(mx.sum(mask, axis=1), 1e-9)
        norms = mx.sqrt(mx.sum(pooled * pooled, axis=1, keepdims=True))
        vectors = pooled / mx.maximum(norms, 1e-12)
        mx.eval(vectors)
        rows = self.np.asarray(vectors).astype(self.np.float32, copy=False).tolist()

        if len(rows) != len(normalized):
            raise RuntimeError("local embedding output row count mismatch")
        for row in rows:
            if len(row) != MODEL_DIMENSIONS:
                raise RuntimeError("local embedding output dimension mismatch")
            norm = math.sqrt(sum(float(value) * float(value) for value in row))
            if not math.isfinite(norm) or norm <= 0.0:
                raise RuntimeError("local embedding output is non-finite or zero")
        return EmbeddingBatch(rows, int(sum(int(v) for v in token_counts.tolist())))


class LocalEmbeddingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], engine: Any) -> None:
        self.engine = engine
        self._request_lock = threading.Lock()
        self._in_flight = 0
        super().__init__(address, LocalEmbeddingHandler)

    def runtime_status(self) -> dict[str, Any]:
        with self._request_lock:
            in_flight = self._in_flight
        mx = getattr(self.engine, "mx", None)
        status: dict[str, Any] = {"in_flight": in_flight}
        if mx is not None:
            status.update({"cache_limit_bytes": MAX_ALLOCATOR_CACHE_BYTES,
                           "active_bytes": int(mx.get_active_memory()),
                           "cache_bytes": int(mx.get_cache_memory()),
                           "peak_active_bytes": int(mx.get_peak_memory())})
        return status


class LocalEmbeddingHandler(BaseHTTPRequestHandler):
    server_version = "AldenLocalEmbedding/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(
            payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
        if len(body) > MAX_RESPONSE_BYTES:
            status = 500
            body = b'{"error":{"code":"embedding_response_too_large","message":"response exceeds local safety bound"}}'
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _error(self, error: EmbeddingRequestError) -> None:
        self._send_json(
            error.status,
            {
                "error": {
                    "type": "invalid_request_error",
                    "code": error.code,
                    "message": str(error),
                }
            },
        )

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        if self.path != "/v1/models":
            self._send_json(404, {"error": {"code": "not_found", "message": "not found"}})
            return
        engine = self.server.engine
        self._send_json(
            200,
            {
                "object": "list",
                "runtime": self.server.runtime_status(),
                "data": [
                    {
                        "id": engine.model_id,
                        "object": "model",
                        "created": 0,
                        "owned_by": "openkakao-local",
                        "loaded": True,
                        "state": "ready",
                        "capabilities": ["embedding", "embeddings"],
                        "revision": engine.revision,
                        "license": engine.license,
                        "dimensions": engine.dimensions,
                    }
                ],
            },
        )

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        with self.server._request_lock:
            self.server._in_flight += 1
        try:
            self._do_embeddings()
        finally:
            with self.server._request_lock:
                self.server._in_flight -= 1

    def _do_embeddings(self) -> None:
        if self.path != "/v1/embeddings":
            self._send_json(404, {"error": {"code": "not_found", "message": "not found"}})
            return
        try:
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
            if content_type != "application/json":
                raise EmbeddingRequestError(
                    "embedding_content_type_invalid", "Content-Type must be application/json"
                )
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                raise EmbeddingRequestError(
                    "embedding_content_length_required", "Content-Length is required", status=411
                )
            try:
                length = int(raw_length)
            except ValueError as exc:
                raise EmbeddingRequestError(
                    "embedding_content_length_invalid", "Content-Length is invalid"
                ) from exc
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise EmbeddingRequestError(
                    "embedding_request_too_large",
                    f"request body must be 1..{MAX_REQUEST_BYTES} bytes",
                    status=413,
                )
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise EmbeddingRequestError(
                    "embedding_request_incomplete", "request body is incomplete"
                )
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise EmbeddingRequestError(
                    "embedding_json_invalid", "request body must be valid UTF-8 JSON"
                ) from exc
            if not isinstance(payload, dict):
                raise EmbeddingRequestError(
                    "embedding_payload_invalid", "request body must be a JSON object"
                )
            engine = self.server.engine
            if payload.get("model") != engine.model_id:
                raise EmbeddingRequestError(
                    "embedding_model_mismatch", "requested model is not the pinned local model"
                )
            input_type = payload.get("input_type")
            if input_type not in INPUT_TYPES:
                raise EmbeddingRequestError(
                    "embedding_input_type_required",
                    "input_type must be exactly 'query' or 'passage'",
                )
            values = payload.get("input")
            if isinstance(values, str):
                texts = [values]
            elif isinstance(values, list):
                texts = values
            else:
                raise EmbeddingRequestError(
                    "embedding_input_invalid", "input must be a string or list of strings"
                )
            batch = engine.embed(texts, input_type)
            data = [
                {"object": "embedding", "index": index, "embedding": vector}
                for index, vector in enumerate(batch.vectors)
            ]
            self._send_json(
                200,
                {
                    "object": "list",
                    "data": data,
                    "model": engine.model_id,
                    "usage": {
                        "prompt_tokens": batch.prompt_tokens,
                        "total_tokens": batch.prompt_tokens,
                    },
                },
            )
        except EmbeddingRequestError as exc:
            self._error(exc)
        except Exception:
            # Do not expose local paths, dependency details, or model internals over HTTP.
            self._send_json(
                500,
                {
                    "error": {
                        "type": "server_error",
                        "code": "embedding_inference_failed",
                        "message": "local embedding inference failed",
                    }
                },
            )


def create_server(host: str, port: int, engine: Any) -> LocalEmbeddingHTTPServer:
    if host != DEFAULT_HOST:
        raise ValueError("embedding server host must be exactly 127.0.0.1")
    if not 1 <= int(port) <= 65535:
        raise ValueError("embedding server port must be between 1 and 65535")
    return LocalEmbeddingHTTPServer((host, int(port)), engine)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Serve pinned multilingual E5 embeddings on loopback only."
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--model-path", type=Path, default=default_model_path())
    return parser


def main() -> int:
    args = build_parser().parse_args()
    engine = LocalE5Engine(args.model_path)
    server = create_server(args.host, args.port, engine)
    print(
        json.dumps(
            {
                "status": "ready",
                "host": args.host,
                "port": args.port,
                "model": engine.model_id,
                "revision": engine.revision,
                "dimensions": engine.dimensions,
            },
            separators=(",", ":"),
        ),
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
