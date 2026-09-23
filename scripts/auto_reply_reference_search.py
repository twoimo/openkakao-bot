"""Local-only BM25 + Dense retrieval for harvested reference packs.

BM25 candidates come from SQLite FTS5. Dense candidates come from a separately
versioned SQLite LSH index populated with vectors from an explicitly local
embedding engine. The two candidate lists are generated independently and are
combined only with Reciprocal Rank Fusion (RRF).

The Dense path is fail-closed: if the embedding engine is absent, not loopback,
fails, or the Dense index is absent/stale/incompatible, retrieval reports
``bm25_only`` and does not substitute the legacy lexical hash vectors.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
import re
import sqlite3
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

PACK_TABLE = "context_reference_packs"
FTS_TABLE = "context_reference_search_fts"
DENSE_TABLE = "context_reference_dense_lsh"
DENSE_META_TABLE = "context_reference_dense_meta"
RETRIEVAL_META_TABLE = "context_retrieval_meta"

SEARCH_INDEX_VERSION = "reference-hybrid-rrf-v1"
FTS_INDEX_VERSION = "reference-fts5-v1"
DENSE_INDEX_VERSION = "reference-dense-lsh-v1"
RRF_K = 60
DEFAULT_LIMIT = 20
DEFAULT_CANDIDATE_LIMIT = 80
LSH_BANDS = 4
LSH_BITS_PER_BAND = 16


class ReferenceSearchError(RuntimeError):
    pass


class DenseUnavailable(ReferenceSearchError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


class _RejectEmbeddingRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> urllib.request.Request:
        raise urllib.error.HTTPError(
            new_url,
            code,
            "loopback embedding redirects are disabled",
            headers,
            file_pointer,
        )


class EmbeddingEngine(Protocol):
    model: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class DenseIndex(Protocol):
    name: str

    def search(
        self,
        connection: sqlite3.Connection,
        query_vector: Sequence[float],
        *,
        model: str,
        source_watermark: str,
        filters: "SearchFilters",
        limit: int,
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class SearchFilters:
    chat: str = ""
    chat_id: int = 0
    participant: str = ""
    start_time: str = ""
    end_time: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "chat": self.chat,
            "chat_id": self.chat_id,
            "participant": self.participant,
            "start_time": self.start_time,
            "end_time": self.end_time,
        }


def _normalise_vector(values: Sequence[float]) -> list[float]:
    vector: list[float] = []
    for value in values:
        number = float(value)
        if not math.isfinite(number):
            raise DenseUnavailable("embedding_invalid", "non_finite_embedding")
        vector.append(number)
    if not vector:
        raise DenseUnavailable("embedding_invalid", "empty_embedding")
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0.0:
        raise DenseUnavailable("embedding_invalid", "zero_embedding")
    return [value / norm for value in vector]


def _pack_vector(vector: Sequence[float]) -> bytes:
    values = list(vector)
    return struct.pack("<" + "f" * len(values), *values)


def _unpack_vector(blob: object, dim: int) -> list[float]:
    if not isinstance(blob, (bytes, bytearray)) or len(blob) != dim * 4:
        raise DenseUnavailable("dense_index_corrupt", "embedding_blob_size")
    return list(struct.unpack("<" + "f" * dim, bytes(blob)))


def _cosine_normalized(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise DenseUnavailable("dense_index_incompatible", "dimension_mismatch")
    return sum(a * b for a, b in zip(left, right))


def _is_loopback_endpoint(endpoint: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        if parsed.hostname.lower() == "localhost":
            return True
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


class LoopbackOpenAIEmbeddingEngine:
    """OpenAI-compatible embeddings restricted to a loopback HTTP endpoint."""

    def __init__(self, endpoint: str, model: str, *, timeout: float = 15.0) -> None:
        endpoint = str(endpoint or "").strip()
        model = str(model or "").strip()
        if not endpoint or not _is_loopback_endpoint(endpoint):
            raise DenseUnavailable("embedding_endpoint_not_loopback")
        if not model:
            raise DenseUnavailable("embedding_model_missing")
        parsed = urllib.parse.urlparse(endpoint)
        if parsed.path in {"", "/"}:
            endpoint = endpoint.rstrip("/") + "/v1/embeddings"
        self.endpoint = endpoint
        self.model = model
        self.timeout = max(0.5, min(float(timeout), 60.0))

    @classmethod
    def from_environment(cls) -> "LoopbackOpenAIEmbeddingEngine | None":
        endpoint = (
            os.environ.get("OPENKAKAO_EMBEDDING_ENDPOINT", "").strip()
            or os.environ.get("OPENKAKAO_EMBEDDING_URL", "").strip()
        )
        model = os.environ.get("OPENKAKAO_EMBEDDING_MODEL", "").strip()
        if not endpoint or not model:
            return None
        return cls(endpoint, model)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = json.dumps(
            {"model": self.model, "input": [str(text) for text in texts]},
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        # Ignore configured proxies. A loopback-only engine should never leave
        # the host through a proxy even when the shell has HTTP(S)_PROXY set.
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _RejectEmbeddingRedirects(),
        )
        try:
            with opener.open(request, timeout=self.timeout) as response:
                raw = response.read(8 * 1024 * 1024)
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            if isinstance(exc, urllib.error.HTTPError):
                exc.close()
            raise DenseUnavailable("embedding_engine_failed", type(exc).__name__) from exc
        try:
            body = json.loads(raw)
            data = body["data"]
            if not isinstance(data, list):
                raise TypeError("data")
            ordered = sorted(
                data,
                key=lambda item: int(item.get("index", 0)) if isinstance(item, dict) else 0,
            )
            vectors = [
                _normalise_vector(item["embedding"])
                for item in ordered
                if isinstance(item, dict)
            ]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise DenseUnavailable("embedding_response_invalid", type(exc).__name__) from exc
        if len(vectors) != len(texts):
            raise DenseUnavailable("embedding_response_invalid", "count_mismatch")
        dimensions = {len(vector) for vector in vectors}
        if len(dimensions) != 1:
            raise DenseUnavailable("embedding_response_invalid", "dimension_mismatch")
        return vectors


def _projection_coefficient(bit: int, dimension_index: int) -> float:
    digest = hashlib.blake2b(
        f"{DENSE_INDEX_VERSION}:{bit}:{dimension_index}".encode("ascii"),
        digest_size=8,
    ).digest()
    integer = int.from_bytes(digest, "little", signed=False)
    return (integer / float((1 << 64) - 1)) * 2.0 - 1.0


def _lsh_buckets(vector: Sequence[float]) -> tuple[int, ...]:
    buckets: list[int] = []
    for band in range(LSH_BANDS):
        bucket = 0
        for offset in range(LSH_BITS_PER_BAND):
            bit = band * LSH_BITS_PER_BAND + offset
            projection = sum(
                value * _projection_coefficient(bit, index)
                for index, value in enumerate(vector)
            )
            if projection >= 0.0:
                bucket |= 1 << offset
        buckets.append(bucket)
    return tuple(buckets)


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _source_watermark(connection: sqlite3.Connection) -> str:
    if not _table_exists(connection, PACK_TABLE):
        return "sha256:" + hashlib.sha256(b"").hexdigest()
    digest = hashlib.sha256()
    rows = connection.execute(
        f"""
        SELECT id, pack_key, source, chat, chat_id, user_name, started_at, ended_at,
               start_log_id, end_log_id, topics, what_text, how_text, why_text,
               body, policy_version, COALESCE(context_message_id, 0)
        FROM {PACK_TABLE}
        ORDER BY id ASC
        """
    )
    for row in rows:
        encoded = json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "little"))
        digest.update(encoded)
    return "sha256:" + digest.hexdigest()


def _ensure_meta_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {RETRIEVAL_META_TABLE}(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )


def _ensure_fts(connection: sqlite3.Connection, watermark: str) -> None:
    _ensure_meta_table(connection)
    try:
        connection.execute(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5(
                pack_id UNINDEXED,
                user_name,
                what_text,
                how_text,
                why_text,
                body,
                tokenize='unicode61'
            )
            """
        )
    except sqlite3.Error as exc:
        raise ReferenceSearchError("fts5_unavailable") from exc
    saved = dict(
        connection.execute(
            f"SELECT key, value FROM {RETRIEVAL_META_TABLE} WHERE key IN (?, ?)",
            ("reference_fts_version", "reference_fts_watermark"),
        ).fetchall()
    )
    if (
        saved.get("reference_fts_version") == FTS_INDEX_VERSION
        and saved.get("reference_fts_watermark") == watermark
    ):
        return
    connection.execute(f"DELETE FROM {FTS_TABLE}")
    if _table_exists(connection, PACK_TABLE):
        connection.execute(
            f"""
            INSERT INTO {FTS_TABLE}(pack_id, user_name, what_text, how_text, why_text, body)
            SELECT id, user_name, what_text, how_text, why_text, body
            FROM {PACK_TABLE}
            """
        )
    connection.executemany(
        f"""
        INSERT INTO {RETRIEVAL_META_TABLE}(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        [
            ("reference_fts_version", FTS_INDEX_VERSION),
            ("reference_fts_watermark", watermark),
        ],
    )
    connection.commit()


def _filter_sql(filters: SearchFilters, *, alias: str = "p") -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if filters.chat and filters.chat not in {"전체", "*"}:
        clauses.append(f"{alias}.chat = ?")
        params.append(filters.chat)
    if int(filters.chat_id or 0) > 0:
        clauses.append(f"{alias}.chat_id = ?")
        params.append(int(filters.chat_id))
    if filters.participant and filters.participant not in {"전체", "*"}:
        clauses.append(f"{alias}.user_name = ?")
        params.append(filters.participant)
    # Overlap semantics: a pack is in range when any part of its interval is.
    if filters.start_time:
        clauses.append(f"{alias}.ended_at >= ?")
        params.append(filters.start_time)
    if filters.end_time:
        clauses.append(f"{alias}.started_at <= ?")
        params.append(filters.end_time)
    return clauses, params


def _fts_query(text: str) -> str:
    tokens = [
        token.lower()
        for token in re.split(r"[^0-9A-Za-z가-힣]+", str(text or ""))
        if token
    ]
    # User text never becomes FTS syntax. OR keeps lexical candidates broad;
    # BM25 ranking handles relative strength and Dense remains independent.
    return " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens[:24])


def _bm25_candidates(
    connection: sqlite3.Connection,
    query: str,
    filters: SearchFilters,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    expression = _fts_query(query)
    if not expression:
        return []
    filter_clauses, filter_params = _filter_sql(filters, alias="p")
    where = [f"{FTS_TABLE} MATCH ?", *filter_clauses]
    params: list[Any] = [expression, *filter_params, max(1, int(limit))]
    # SQLite FTS5 requires the real virtual-table name in MATCH/bm25(); using
    # a table alias here produces "no such column" on supported SQLite builds.
    rows = connection.execute(
        f"""
        SELECT p.id, bm25({FTS_TABLE}, 0.0, 1.2, 2.5, 1.0, 1.0, 2.0) AS score
        FROM {FTS_TABLE}
        JOIN {PACK_TABLE} AS p ON p.id = {FTS_TABLE}.pack_id
        WHERE {' AND '.join(where)}
        ORDER BY score ASC, p.quality_score DESC, p.end_log_id DESC, p.id DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [
        {"id": int(row[0]), "score": float(row[1]), "rank": rank}
        for rank, row in enumerate(rows, start=1)
    ]


def _dense_text(row: Sequence[Any]) -> str:
    return "\n".join(str(value or "").strip() for value in row if str(value or "").strip())


def _ensure_dense_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {DENSE_TABLE}(
            pack_id INTEGER PRIMARY KEY,
            model TEXT NOT NULL,
            index_version TEXT NOT NULL,
            source_watermark TEXT NOT NULL,
            dim INTEGER NOT NULL,
            embedding BLOB NOT NULL,
            band0 INTEGER NOT NULL,
            band1 INTEGER NOT NULL,
            band2 INTEGER NOT NULL,
            band3 INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {DENSE_META_TABLE}(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    for band in range(LSH_BANDS):
        connection.execute(
            f"CREATE INDEX IF NOT EXISTS idx_reference_dense_band{band} "
            f"ON {DENSE_TABLE}(band{band})"
        )


def rebuild_dense_index(
    db_path: Path,
    *,
    embedding_engine: EmbeddingEngine,
    batch_size: int = 32,
) -> dict[str, Any]:
    """Build the local Dense LSH index without touching legacy hash vectors."""
    connection = sqlite3.connect(str(db_path), timeout=30.0)
    connection.execute("PRAGMA busy_timeout = 5000")
    try:
        if not _table_exists(connection, PACK_TABLE):
            raise DenseUnavailable("dense_source_unavailable", PACK_TABLE)
        watermark = _source_watermark(connection)
        rows = connection.execute(
            f"""
            SELECT id, user_name, what_text, how_text, why_text, body
            FROM {PACK_TABLE}
            ORDER BY id ASC
            """
        ).fetchall()
        indexed: list[tuple[int, list[float]]] = []
        bounded_batch = max(1, min(int(batch_size or 32), 128))
        for start in range(0, len(rows), bounded_batch):
            batch = rows[start : start + bounded_batch]
            vectors = embedding_engine.embed([_dense_text(row[1:]) for row in batch])
            if len(vectors) != len(batch):
                raise DenseUnavailable("embedding_response_invalid", "count_mismatch")
            for row, vector in zip(batch, vectors):
                indexed.append((int(row[0]), _normalise_vector(vector)))
        dimensions = {len(vector) for _, vector in indexed}
        if len(dimensions) > 1:
            raise DenseUnavailable("embedding_response_invalid", "dimension_mismatch")
        dim = next(iter(dimensions), 0)
        # Do not publish an index built from a moving source snapshot.
        if _source_watermark(connection) != watermark:
            raise DenseUnavailable("dense_source_changed")
        connection.execute("BEGIN IMMEDIATE")
        if _source_watermark(connection) != watermark:
            connection.rollback()
            raise DenseUnavailable("dense_source_changed")
        _ensure_dense_schema(connection)
        connection.execute(f"DELETE FROM {DENSE_TABLE}")
        now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        for pack_id, vector in indexed:
            buckets = _lsh_buckets(vector)
            connection.execute(
                f"""
                INSERT INTO {DENSE_TABLE}(
                    pack_id, model, index_version, source_watermark, dim,
                    embedding, band0, band1, band2, band3, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pack_id,
                    embedding_engine.model,
                    DENSE_INDEX_VERSION,
                    watermark,
                    len(vector),
                    _pack_vector(vector),
                    *buckets,
                    now,
                ),
            )
        metadata = {
            "model": embedding_engine.model,
            "index_version": DENSE_INDEX_VERSION,
            "source_watermark": watermark,
            "dim": str(dim),
            "count": str(len(indexed)),
        }
        connection.execute(f"DELETE FROM {DENSE_META_TABLE}")
        connection.executemany(
            f"INSERT INTO {DENSE_META_TABLE}(key, value) VALUES (?, ?)",
            metadata.items(),
        )
        connection.commit()
        return {
            "ok": True,
            "action": "reference-dense-rebuild",
            "index_version": DENSE_INDEX_VERSION,
            "watermark": watermark,
            "model": embedding_engine.model,
            "dimension": dim,
            "count": len(indexed),
        }
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


class SQLiteLSHDenseIndex:
    name = "sqlite_lsh"

    def _metadata(self, connection: sqlite3.Connection) -> dict[str, str]:
        if not _table_exists(connection, DENSE_TABLE) or not _table_exists(
            connection, DENSE_META_TABLE
        ):
            raise DenseUnavailable("dense_index_unavailable")
        return {
            str(key): str(value)
            for key, value in connection.execute(
                f"SELECT key, value FROM {DENSE_META_TABLE}"
            ).fetchall()
        }

    def search(
        self,
        connection: sqlite3.Connection,
        query_vector: Sequence[float],
        *,
        model: str,
        source_watermark: str,
        filters: SearchFilters,
        limit: int,
    ) -> list[dict[str, Any]]:
        metadata = self._metadata(connection)
        if metadata.get("index_version") != DENSE_INDEX_VERSION:
            raise DenseUnavailable("dense_index_version_mismatch")
        if metadata.get("model") != model:
            raise DenseUnavailable("dense_model_mismatch")
        if metadata.get("source_watermark") != source_watermark:
            raise DenseUnavailable("dense_index_stale")
        try:
            dim = int(metadata.get("dim", "0"))
        except ValueError as exc:
            raise DenseUnavailable("dense_index_corrupt", "invalid_dimension") from exc
        vector = _normalise_vector(query_vector)
        if dim <= 0 or len(vector) != dim:
            raise DenseUnavailable("dense_index_incompatible", "dimension_mismatch")
        buckets = _lsh_buckets(vector)
        filter_clauses, filter_params = _filter_sql(filters, alias="p")
        bucket_clause = "(" + " OR ".join(
            f"d.band{index} = ?" for index in range(LSH_BANDS)
        ) + ")"
        where = [bucket_clause, *filter_clauses]
        candidate_limit = max(max(1, int(limit)) * 8, 64)
        rows = connection.execute(
            f"""
            SELECT p.id, d.embedding, d.dim
            FROM {DENSE_TABLE} AS d
            JOIN {PACK_TABLE} AS p ON p.id = d.pack_id
            WHERE {' AND '.join(where)}
              AND d.model = ?
              AND d.index_version = ?
              AND d.source_watermark = ?
            ORDER BY p.quality_score DESC, p.end_log_id DESC, p.id DESC
            LIMIT ?
            """,
            [
                *buckets,
                *filter_params,
                model,
                DENSE_INDEX_VERSION,
                source_watermark,
                candidate_limit,
            ],
        ).fetchall()
        scored = [
            {
                "id": int(pack_id),
                "score": _cosine_normalized(vector, _unpack_vector(blob, int(row_dim))),
            }
            for pack_id, blob, row_dim in rows
        ]
        scored.sort(key=lambda item: (-float(item["score"]), int(item["id"])))
        return [
            {**item, "rank": rank}
            for rank, item in enumerate(scored[: max(1, int(limit))], start=1)
        ]


def _rrf(
    bm25: Sequence[dict[str, Any]],
    dense: Sequence[dict[str, Any]],
    *,
    k: int,
) -> list[dict[str, Any]]:
    fused: dict[int, dict[str, Any]] = {}
    for source, candidates in (("bm25", bm25), ("dense", dense)):
        for rank, candidate in enumerate(candidates, start=1):
            ident = int(candidate["id"])
            item = fused.setdefault(
                ident,
                {
                    "id": ident,
                    "rrf_score": 0.0,
                    "bm25_rank": None,
                    "bm25_score": None,
                    "dense_rank": None,
                    "dense_score": None,
                },
            )
            item["rrf_score"] += 1.0 / (max(1, int(k)) + rank)
            item[f"{source}_rank"] = rank
            item[f"{source}_score"] = float(candidate.get("score", 0.0))
    return sorted(
        fused.values(),
        key=lambda item: (-float(item["rrf_score"]), int(item["id"])),
    )


def _load_rows(
    connection: sqlite3.Connection,
    fused: Sequence[dict[str, Any]],
    *,
    index_version: str,
    watermark: str,
    limit: int,
) -> list[dict[str, Any]]:
    selected = list(fused[: max(1, int(limit))])
    if not selected:
        return []
    ids = [int(item["id"]) for item in selected]
    placeholders = ",".join("?" for _ in ids)
    rows = connection.execute(
        f"""
        SELECT id, pack_key, source, chat, chat_id, user_name, started_at, ended_at,
               start_log_id, end_log_id, topics, what_text, how_text, why_text,
               body, quality_score, image_count, message_count,
               COALESCE(context_message_id, 0)
        FROM {PACK_TABLE}
        WHERE id IN ({placeholders})
        """,
        ids,
    ).fetchall()
    by_id = {int(row[0]): row for row in rows}
    output: list[dict[str, Any]] = []
    for item in selected:
        row = by_id.get(int(item["id"]))
        if row is None:
            continue
        evidence_ids = [f"pack:{row[0]}"]
        if int(row[18] or 0) > 0:
            evidence_ids.append(f"context_message:{int(row[18])}")
        if int(row[4] or 0) > 0:
            evidence_ids.extend(
                [
                    f"log:{int(row[4])}:{int(row[8])}",
                    f"log:{int(row[4])}:{int(row[9])}",
                ]
            )
        message_parts = [
            "[설명자료]",
            f"누가:{row[5]}",
            f"무엇을:{row[11]}",
            f"어떻게:{row[12]}",
            f"왜:{row[13]}",
        ]
        if str(row[14] or "").strip():
            message_parts.append(f"핵심:{row[14]}")
        output.append(
            {
                "id": int(row[0]),
                "pack_key": str(row[1]),
                "origin_label": str(row[2]),
                "chat": str(row[3]),
                "chat_id": int(row[4] or 0),
                "participant": str(row[5]),
                "user_name": str(row[5]),
                "started_at": str(row[6]),
                "ended_at": str(row[7]),
                "evidence_ids": evidence_ids,
                "topics": [part for part in str(row[10] or "").split(",") if part],
                "message": "\n".join(message_parts),
                "quality_score": int(row[15] or 0),
                "image_count": int(row[16] or 0),
                "message_count": int(row[17] or 0),
                "index_version": index_version,
                "watermark": watermark,
                "rrf_score": float(item["rrf_score"]),
                "bm25_rank": item["bm25_rank"],
                "bm25_score": item["bm25_score"],
                "dense_rank": item["dense_rank"],
                "dense_score": item["dense_score"],
            }
        )
    return output


def search_reference_packs(
    db_path: Path,
    *,
    query: str,
    chat: str = "",
    chat_id: int = 0,
    participant: str = "",
    start_time: str = "",
    end_time: str = "",
    limit: int = DEFAULT_LIMIT,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
    rrf_k: int = RRF_K,
    embedding_engine: EmbeddingEngine | None = None,
    dense_index: DenseIndex | None = None,
    use_environment: bool = True,
) -> dict[str, Any]:
    """Search reference packs and expose degradation instead of hiding it."""
    connection = sqlite3.connect(str(db_path), timeout=30.0)
    connection.execute("PRAGMA busy_timeout = 5000")
    filters = SearchFilters(
        chat=str(chat or "").strip(),
        chat_id=max(0, int(chat_id or 0)),
        participant=str(participant or "").strip(),
        start_time=str(start_time or "").strip(),
        end_time=str(end_time or "").strip(),
    )
    bounded_limit = max(1, min(int(limit or DEFAULT_LIMIT), 200))
    bounded_candidates = max(
        bounded_limit,
        min(int(candidate_limit or DEFAULT_CANDIDATE_LIMIT), 1000),
    )
    try:
        if not _table_exists(connection, PACK_TABLE):
            return {
                "ok": True,
                "action": "reference-search",
                "mode": "bm25_only",
                "degraded": True,
                "query": query,
                "filters": filters.as_dict(),
                "index_version": SEARCH_INDEX_VERSION,
                "watermark": _source_watermark(connection),
                "candidate_counts": {"bm25": 0, "dense": 0},
                "dense": {"available": False, "status": "dense_source_unavailable"},
                "count": 0,
                "rows": [],
            }
        watermark = _source_watermark(connection)
        _ensure_fts(connection, watermark)
        bm25 = _bm25_candidates(
            connection, query, filters, limit=bounded_candidates
        )

        engine = embedding_engine
        dense_state: dict[str, Any] = {
            "available": False,
            "status": "embedding_engine_unavailable",
        }
        dense: list[dict[str, Any]] = []
        if engine is None and use_environment:
            try:
                engine = LoopbackOpenAIEmbeddingEngine.from_environment()
            except DenseUnavailable as exc:
                dense_state = {
                    "available": False,
                    "status": exc.code,
                    "detail": exc.detail,
                }
        if engine is not None:
            index = dense_index or SQLiteLSHDenseIndex()
            dense_state = {
                "available": False,
                "status": "dense_index_unavailable",
                "engine": type(engine).__name__,
                "model": str(getattr(engine, "model", "")),
                "index": str(getattr(index, "name", type(index).__name__)),
            }
            try:
                vectors = engine.embed([query])
                if len(vectors) != 1:
                    raise DenseUnavailable("embedding_response_invalid", "count_mismatch")
            except DenseUnavailable as exc:
                dense = []
                dense_state.update(
                    {"available": False, "status": exc.code, "detail": exc.detail}
                )
                vectors = []
            except Exception as exc:
                dense = []
                dense_state.update(
                    {
                        "available": False,
                        "status": "embedding_engine_failed",
                        "detail": type(exc).__name__,
                    }
                )
                vectors = []
            if vectors:
                try:
                    dense = index.search(
                        connection,
                        _normalise_vector(vectors[0]),
                        model=str(getattr(engine, "model", "")),
                        source_watermark=watermark,
                        filters=filters,
                        limit=bounded_candidates,
                    )
                    dense_state.update(
                        {
                            "available": True,
                            "status": "active",
                            "index_version": DENSE_INDEX_VERSION,
                        }
                    )
                except DenseUnavailable as exc:
                    dense = []
                    dense_state.update(
                        {"available": False, "status": exc.code, "detail": exc.detail}
                    )
                except Exception as exc:
                    dense = []
                    dense_state.update(
                        {
                            "available": False,
                            "status": "dense_index_failed",
                            "detail": type(exc).__name__,
                        }
                    )
        mode = "hybrid_rrf" if dense_state.get("available") else "bm25_only"
        fused = _rrf(bm25, dense if mode == "hybrid_rrf" else [], k=rrf_k)
        rows = _load_rows(
            connection,
            fused,
            index_version=SEARCH_INDEX_VERSION,
            watermark=watermark,
            limit=bounded_limit,
        )
        return {
            "ok": True,
            "action": "reference-search",
            "mode": mode,
            "degraded": mode != "hybrid_rrf",
            "query": query,
            "filters": filters.as_dict(),
            "index_version": SEARCH_INDEX_VERSION,
            "watermark": watermark,
            "rrf_k": max(1, int(rrf_k)),
            "candidate_counts": {"bm25": len(bm25), "dense": len(dense)},
            "dense": dense_state,
            "count": len(rows),
            "rows": rows,
        }
    except ReferenceSearchError as exc:
        return {
            "ok": False,
            "action": "reference-search",
            "mode": "unavailable",
            "query": query,
            "filters": filters.as_dict(),
            "error": str(exc),
            "rows": [],
        }
    finally:
        connection.close()
