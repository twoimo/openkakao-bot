"""Deterministic Knowledge Graph for OpenKakao Bujamentor Auto-Reply.

Inspired by Andrej Karpathy's LLM Wiki & Algorithmic Knowledge Graph concepts:
- Entity nodes (People, Technologies, Concepts, Activities, Channels)
- Relation edges (DISCUSSED_IN, SUBSCRIBED_TO, OPINION_ON, ACTIVITY_OF)
- Grounded contextual facts with evidence identifiers and timestamps
- Deterministic, zero-hallucination graph queries for prompt context enrichment.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

KNOWLEDGE_GRAPH_DB_NAME = "knowledge-graph.sqlite3"
GRAPH_SOURCE_KIND = "knowledge_graph"
DEFAULT_K_HOP = 2
MAX_K_HOP = 3
K_HOP_NEIGHBOR_LIMIT = 10
GRAPH_ENTITY_PREFIXES = ("ent:", "chat:", "person:", "topic:", "time:", "author:")
INDEX_STATUS_STALE_SECONDS = 300

SEARCH_INDEX_VERSION = "unit4-rrf-bm25-dense-v1"
DENSE_INDEX_DB_NAME = "knowledge-dense-ann.sqlite3"
DENSE_INDEX_VERSION = "bge-m3-lsh-v1"
DENSE_EMBEDDING_MODEL = os.environ.get(
    "OPENKAKAO_DENSE_EMBEDDING_MODEL", "BAAI/bge-m3"
)
DENSE_EMBEDDING_URL = os.environ.get(
    "OPENKAKAO_LOCAL_EMBEDDING_URL", "http://127.0.0.1:8000/v1/embeddings"
)
DENSE_EMBEDDING_TIMEOUT_SECONDS = 4.0
RRF_K = 60
ANN_BANDS = 8
ANN_BITS_PER_BAND = 8
ALIAS_DICTIONARY_VERSION = "2026-09-20.1"
KST = ZoneInfo("Asia/Seoul")

# Provenance kinds. "seed" is a hand-written starting node that no message
# backs yet; "ledger" means a real room message was found to mention the node.
# The 6 Pro review rejected nodes that carried no source message id, room id,
# confirmation time or retraction state, so every node and edge now reports
# which of the two it is and what backs it (2026-09-16).
PROVENANCE_SEED = "seed"
PROVENANCE_LEDGER = "ledger"
MAX_EVIDENCE_PER_NODE = 8


def _trace_graph(event: str, **fields: Any) -> None:
    safe = {
        str(key): value
        for key, value in fields.items()
        if isinstance(value, (str, int, float, bool)) or value is None
    }
    try:
        payload = json.dumps(safe, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        payload = "{}"
    print(f"knowledge-graph {event} {payload}", file=sys.stderr)


def _is_graph_entity_id(entity_id: Any) -> bool:
    value = str(entity_id or "").strip()
    return bool(value) and value.startswith(GRAPH_ENTITY_PREFIXES)


def _bounded_k(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        _trace_graph("khop_invalid_k", fallback=DEFAULT_K_HOP)
        return DEFAULT_K_HOP
    return min(max(parsed, 0), MAX_K_HOP)


def _bounded_neighbor_limit(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        _trace_graph("khop_invalid_limit", fallback=K_HOP_NEIGHBOR_LIMIT)
        return K_HOP_NEIGHBOR_LIMIT
    return min(max(parsed, 0), K_HOP_NEIGHBOR_LIMIT)


def stable_node_id(entity_id: str) -> int:
    """A row id that is the same in every process.

    The window keys selection on this number, and the list is rebuilt by a
    fresh interpreter on every refresh. Python's ``hash()`` is salted per
    process, so ids changed on every poll and a selected row was lost between
    reads. CRC32 of the entity id is stable across processes and machines
    (2026-09-16).
    """
    return zlib.crc32(entity_id.encode("utf-8")) % 10000000


def _seed_evidence() -> dict[str, Any]:
    return {
        "kind": PROVENANCE_SEED,
        "source_event_ids": [],
        "chat_id": "",
        "confirmed_at": None,
        "retracted": False,
    }


def _normalize_evidence(raw: Any) -> dict[str, Any]:
    """Coerce whatever is on disk into the documented evidence shape."""
    if not isinstance(raw, dict):
        return _seed_evidence()
    ids = raw.get("source_event_ids")
    if not isinstance(ids, list):
        ids = []
    return {
        "kind": PROVENANCE_LEDGER if raw.get("kind") == PROVENANCE_LEDGER else PROVENANCE_SEED,
        "source_event_ids": [str(item) for item in ids if str(item).strip()][:MAX_EVIDENCE_PER_NODE],
        "chat_id": str(raw.get("chat_id") or ""),
        "confirmed_at": raw.get("confirmed_at") or None,
        "retracted": bool(raw.get("retracted")),
    }

DEFAULT_ENTITIES = [
    {
        "entity_id": "ent:tech:alizonku",
        "name": "알쫀쿠 (알리바바 클라우드 구독)",
        "category": "클라우드/인프라",
        "aliases": ["알쫀쿠", "알리바바", "알리바바 클라우드", "alibaba cloud", "알리 클라우드"],
        "description": "알리바바 클라우드(Alibaba Cloud) 1년 구독 및 할인 프로모션. AWS 대비 가성비 인프라로 과거 방에서 '이게 훨씬 낫죠'라며 추천 및 비교했던 핵심 주제.",
        "key_facts": [
            "알쫀쿠는 알리바바 클라우드 국내 프로모션/할인 구독을 지칭함",
            "과거 최연우가 '이게 훨씬 낫죠'라고 평가하며 비용 효율성과 스펙 장점을 강조함",
            "서버 비용 절감 및 스타트업/사이드 프로젝트 인프라로 강력 추천한 맥락 보유",
        ],
        "importance": 95,
    },
    {
        "entity_id": "ent:person:moon_seunghyun",
        "name": "문승현",
        "category": "대화 상대",
        "aliases": ["문승현", "승현", "승현님"],
        "description": "부자멘토멘티 채팅방의 핵심 대화 상대. 러닝/운동을 즐기며 인증 사진을 자주 공유함.",
        "key_facts": [
            "취미로 야외 러닝/마라톤 훈련을 진행함",
            "운동 기록(NRC/가민 등) 및 러닝 후기 사진을 공유함 (음식 사진과 혼동 금지)",
            "최연우의 알쫀쿠 추천 및 테크/스타트업 논의에 호응함",
        ],
        "importance": 90,
    },
    {
        "entity_id": "ent:person:choi_yeonwoo",
        "name": "최연우 (페르소나/본인)",
        "category": "화자",
        "aliases": ["최연우", "연우"],
        "description": "오픈카카오 봇의 사용자 페르소나. 솔직하고 현실적인 말투로 스타트업, 클라우드, 자동화에 대해 실용적 조언을 제공함.",
        "key_facts": [
            "자신감 넘치고 담백한 어투 (과도한 존대나 AI스러운 문체 지양)",
            "클라우드 비용과 인프라 효율성을 매우 중요하게 평가함",
            "친구들과의 일상 공유(운동, 식사)에 자연스럽고 따뜻하게 반응함",
        ],
        "importance": 98,
    },
    {
        "entity_id": "ent:activity:running",
        "name": "러닝 / 마라톤 훈련",
        "category": "활동/취미",
        "aliases": ["러닝", "달리기", "런", "마라톤", "조깅", "운동 인증"],
        "description": "대화방 멤버들이 공유하는 러닝 및 유산소 운동 활동. 거리, 페이스, 주로 사진이 함께 올라옴.",
        "key_facts": [
            "러닝 후 인증 사진(운동화, 트랙, 주로, 거리 측정 앱)이 공유됨",
            "사진에 러닝 주로/신발/트랙이 보이면 식사/음식 질문을 하지 않고 러닝 거리/페이스에 맞게 반응해야 함",
        ],
        "importance": 85,
    },
    {
        "entity_id": "ent:channel:bujamentor",
        "name": "부자멘토멘티",
        "category": "채팅방",
        "aliases": ["부자멘토멘티", "부자방", "멘토멘티"],
        "description": "문승현, 송우섭, 주원, 현준, 최연우가 참여하는 핵심 카카오톡 그룹 채팅방 (ID: 417780809780519).",
        "key_facts": [
            "스타트업 인프라, 클라우드 비용 절감, 일상 라이프스타일 논의",
            "과거 합의된 맥락(알쫀쿠 가성비 추천)을 일관되게 유지해야 함",
        ],
        "importance": 100,
    },
]

DEFAULT_RELATIONS = [
    {
        "source_id": "ent:person:choi_yeonwoo",
        "relation": "RECOMMENDED",
        "target_id": "ent:tech:alizonku",
        "context": "최연우가 '이게 훨씬 낫죠'라며 알리바바 클라우드(알쫀쿠) 비용 효율성을 강조하고 추천함",
        "weight": 95,
    },
    {
        "source_id": "ent:channel:bujamentor",
        "relation": "DISCUSSED",
        "target_id": "ent:tech:alizonku",
        "context": "부자멘토멘티 채팅방에서 클라우드 구독 비용 비교 주제로 깊이 논의됨",
        "weight": 90,
    },
    {
        "source_id": "ent:person:moon_seunghyun",
        "relation": "PARTICIPATES_IN",
        "target_id": "ent:activity:running",
        "context": "문승현이 러닝 사진 및 운동 기록을 채팅방에 공유함",
        "weight": 88,
    },
    {
        "source_id": "ent:person:moon_seunghyun",
        "relation": "ACTIVE_MEMBER_OF",
        "target_id": "ent:channel:bujamentor",
        "context": "부자멘토멘티 채팅방의 활발한 대화 참여자",
        "weight": 90,
    },
]


def _connect_kg(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kg_entities (
            entity_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            aliases_json TEXT NOT NULL,
            description TEXT NOT NULL,
            key_facts_json TEXT NOT NULL,
            importance INTEGER DEFAULT 50,
            evidence_json TEXT NOT NULL DEFAULT '{}',
            updated_at INTEGER NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kg_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kg_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            relation TEXT NOT NULL,
            target_id TEXT NOT NULL,
            subject_id TEXT NOT NULL DEFAULT '',
            relation_type TEXT NOT NULL DEFAULT '',
            object_id TEXT NOT NULL DEFAULT '',
            room_id TEXT NOT NULL DEFAULT '',
            valid_from TEXT NOT NULL DEFAULT '',
            valid_to TEXT NOT NULL DEFAULT '',
            evidence_message_id TEXT NOT NULL DEFAULT '',
            context TEXT NOT NULL,
            weight INTEGER DEFAULT 50,
            evidence_json TEXT NOT NULL DEFAULT '{}',
            updated_at INTEGER NOT NULL,
            UNIQUE(source_id, relation, target_id)
        )
    """)
    # Existing graphs predate the evidence columns; add them in place so an
    # installed graph keeps its rows instead of being rebuilt (2026-09-16).
    for table in ("kg_entities", "kg_relations"):
        try:
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        except sqlite3.Error:
            continue
        if "evidence_json" not in columns:
            try:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN evidence_json TEXT NOT NULL DEFAULT '{{}}'"
                )
            except sqlite3.Error:
                pass
        if table == "kg_relations":
            relation_columns = {
                "subject_id": "TEXT NOT NULL DEFAULT ''",
                "relation_type": "TEXT NOT NULL DEFAULT ''",
                "object_id": "TEXT NOT NULL DEFAULT ''",
                "room_id": "TEXT NOT NULL DEFAULT ''",
                "valid_from": "TEXT NOT NULL DEFAULT ''",
                "valid_to": "TEXT NOT NULL DEFAULT ''",
                "evidence_message_id": "TEXT NOT NULL DEFAULT ''",
            }
            for column, definition in relation_columns.items():
                if column in columns:
                    continue
                try:
                    conn.execute(
                        f"ALTER TABLE kg_relations ADD COLUMN {column} {definition}"
                    )
                except sqlite3.Error:
                    pass
            try:
                conn.execute(
                    "UPDATE kg_relations SET"
                    " subject_id=CASE WHEN subject_id='' THEN source_id ELSE subject_id END,"
                    " relation_type=CASE WHEN relation_type='' THEN relation ELSE relation_type END,"
                    " object_id=CASE WHEN object_id='' THEN target_id ELSE object_id END"
                )
            except sqlite3.Error:
                pass
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS kg_entities_fts USING fts5("
            "entity_id UNINDEXED, name, aliases, description, facts, tokenize='unicode61')"
        )
        conn.executescript(
            """
            CREATE TRIGGER IF NOT EXISTS kg_entities_fts_ai AFTER INSERT ON kg_entities BEGIN
              INSERT INTO kg_entities_fts(rowid, entity_id, name, aliases, description, facts)
              VALUES (new.rowid, new.entity_id, new.name, new.aliases_json, new.description, new.key_facts_json);
            END;
            CREATE TRIGGER IF NOT EXISTS kg_entities_fts_ad AFTER DELETE ON kg_entities BEGIN
              DELETE FROM kg_entities_fts WHERE rowid = old.rowid;
            END;
            CREATE TRIGGER IF NOT EXISTS kg_entities_fts_au AFTER UPDATE ON kg_entities BEGIN
              DELETE FROM kg_entities_fts WHERE rowid = old.rowid;
              INSERT INTO kg_entities_fts(rowid, entity_id, name, aliases, description, facts)
              VALUES (new.rowid, new.entity_id, new.name, new.aliases_json, new.description, new.key_facts_json);
            END;
            """
        )
        conn.execute(
            "INSERT INTO kg_entities_fts(rowid, entity_id, name, aliases, description, facts)"
            " SELECT e.rowid, e.entity_id, e.name, e.aliases_json, e.description, e.key_facts_json"
            " FROM kg_entities e"
            " WHERE NOT EXISTS (SELECT 1 FROM kg_entities_fts f WHERE f.rowid=e.rowid)"
        )
    except sqlite3.Error as error:
        _trace_graph("fts5_unavailable", error=type(error).__name__)
    conn.commit()
    return conn


def read_meta(conn: sqlite3.Connection, key: str) -> str:
    """Read one value from the graph's own bookkeeping table."""
    try:
        row = conn.execute("SELECT value FROM kg_meta WHERE key = ?", (key,)).fetchone()
    except sqlite3.Error:
        return ""
    return str(row[0]) if row else ""


def write_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Record a value in the graph's bookkeeping table."""
    try:
        conn.execute(
            "INSERT INTO kg_meta (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
        conn.commit()
    except sqlite3.Error:
        pass


def collect_knowledge_graph_status(
    db_path: Path,
    *,
    state_root: Path | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    """Return the persisted Kakao snapshot/index state without starting an index.

    The settings window polls this path, so it must never copy the live Kakao DB
    or trigger a reindex. It reads only the existing graph store in mode=ro with
    query_only enabled and reports the same indexed_at/indexed_count/stale fields
    as ``collect_knowledge_graph``.
    """
    root = state_root if state_root is not None else db_path.parent
    kg_path = root / KNOWLEDGE_GRAPH_DB_NAME
    empty = {
        "ok": True,
        "nodes": [],
        "edges": [],
        "node_count": 0,
        "edge_count": 0,
        "grounded_nodes": 0,
        "indexed_at": 0,
        "indexed_count": 0,
        "stale": True,
        "snapshot_status": "unknown",
        "indexing_mode": "wal+isolated-copy+mode=ro+query_only",
    }
    if not kg_path.exists():
        return empty

    try:
        conn = sqlite3.connect(f"file:{kg_path}?mode=ro", uri=True)
        conn.execute("PRAGMA query_only = ON")
    except sqlite3.Error as error:
        return {**empty, "ok": False, "reason": str(error) or "knowledge_graph_status_unavailable"}

    try:
        try:
            indexed_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM kg_entities"
                    " WHERE entity_id LIKE 'chat:%' OR entity_id LIKE 'topic:%'"
                ).fetchone()[0]
                or 0
            )
        except sqlite3.Error:
            indexed_count = 0
        try:
            indexed_at = int(read_meta(conn, "last_indexed_at") or 0)
        except ValueError:
            indexed_at = 0
        last_error = read_meta(conn, "last_index_error")
        snapshot_status = read_meta(conn, "last_snapshot_status")
        if not snapshot_status:
            if "isolated read-only snapshot unavailable" in last_error:
                snapshot_status = "fail_closed"
            elif indexed_at > 0:
                snapshot_status = "copy_ok"
            else:
                snapshot_status = "unknown"
        stamp = int(time.time()) if now is None else int(now)
        stale = bool(last_error) or indexed_at <= 0 or stamp - indexed_at >= INDEX_STATUS_STALE_SECONDS
        return {
            **empty,
            "indexed_at": indexed_at,
            "indexed_count": indexed_count,
            "stale": stale,
            "snapshot_status": snapshot_status,
        }
    finally:
        conn.close()


def ensure_seeded(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM kg_entities")
    if cur.fetchone()[0] > 0:
        return
    now = int(time.time())
    for e in DEFAULT_ENTITIES:
        conn.execute(
            """
            INSERT INTO kg_entities (entity_id, name, category, aliases_json, description, key_facts_json, importance, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entity_id) DO UPDATE SET
                name=excluded.name,
                category=excluded.category,
                aliases_json=excluded.aliases_json,
                description=excluded.description,
                key_facts_json=excluded.key_facts_json,
                importance=excluded.importance,
                updated_at=excluded.updated_at
            """,
            (
                e["entity_id"],
                e["name"],
                e["category"],
                json.dumps(e["aliases"], ensure_ascii=False),
                e["description"],
                json.dumps(e["key_facts"], ensure_ascii=False),
                e["importance"],
                now,
            ),
        )
    for r in DEFAULT_RELATIONS:
        _upsert_relation(
            conn,
            source_id=r["source_id"],
            relation=r["relation"],
            target_id=r["target_id"],
            context=r["context"],
            weight=int(r["weight"]),
            updated_at=now,
        )
    conn.commit()


def _room_ledgers(state_root: Path) -> list[Path]:
    """Every room ledger, newest name last. Read-only, never creates."""
    try:
        rooms = state_root / "rooms"
        if not rooms.is_dir():
            return []
        return sorted(rooms.glob("*/reply-evidence.jsonl"))
    except OSError:
        return []


# The context index already holds the room's history with a topic per message.
# The graph used to be five hand-written nodes, so it could not answer anything
# the author had not thought of in advance: the room discussed Alibaba Cloud
# for an hour and the graph still had nothing to say about it. These rows turn
# the index into neurons and synapses (2026-09-16).
INDEX_TOPIC_LABELS = {
    "coins": "코인",
    "stocks": "주식",
    "ai": "AI",
    "business": "사업/창업",
    "llm_tools": "LLM 도구",
    "investing": "투자",
    "real_estate": "부동산",
    "contact": "연락/인맥",
    "auction": "경매",
    "computer_use": "컴퓨터 유즈",
    "news": "뉴스",
    "infra": "인프라/클라우드",
    "kakao_auto": "카카오 자동화",
    "identity": "정체성",
    "ax_macos": "macOS 자동화",
}

# A topic needs this many messages before it earns a neuron. Below it the
# graph fills with one-off words that tell the reader nothing.
INDEX_TOPIC_MIN_MESSAGES = 20

# 대화방과 사람도 뉴런이 된다.
#
# 예전 그래프는 주제 뉴런만 있었다. 그래서 "코인"은 알았지만 그것이 어느
# 방에서 누구와 나눈 이야기인지는 몰랐다. 답변에 필요한 것은 주제만이
# 아니라 그 주제가 누구의 어떤 맥락이었는가다. 방과 사람을 따로 세우고
# 주제와 이어 두면, 같은 "주식"이라도 성린이형 방의 주식과 부자멘토멘티의
# 주식이 다른 자리로 남는다 (2026-09-16, 사용자 지시).
INDEX_CHAT_MIN_MESSAGES = 40
INDEX_PERSON_MIN_MESSAGES = 25
# 한 방에서 이 사람의 메시지가 이 비율을 넘으면 그 방의 중심 인물로 본다.
INDEX_PERSON_SHARE = 0.06
# 방마다 만들 사람 뉴런의 상한. 잡담만 하는 사람까지 세우면 그림이 사람으로
# 가득 차 정작 주제가 보이지 않는다.
INDEX_PERSONS_PER_CHAT = 6
# 사람 뉴런의 key_facts에 넣을 최근 발언 수.
INDEX_PERSON_SAMPLE_LINES = 4
# How many recent messages to read per topic to write its description and the
# facts the answer will lean on.
INDEX_TOPIC_SAMPLE_MESSAGES = 60

# A neuron's facts should quote the people in the room, not the bots that post
# into it. Without this every 인프라 fact was a GeekNews digest line and the
# node said nothing about what the members actually discussed (2026-09-16).
INDEX_SAMPLE_SKIP_AUTHORS = (
    "드리고",
    "드리고봇",
    "뉴스봇",
    "채팅봇",
    "주식봇",
    "날씨날씨",
    "인아웃",
    "chatgpt",
    "(알 수 없음)",
)
# Long digests and link dumps are not conversation either.
INDEX_SAMPLE_MAX_LENGTH = 200


def _sample_is_conversation(user: str, message: str) -> bool:
    """Whether one indexed line is worth quoting as a neuron's evidence."""
    folded = (user or "").strip().casefold()
    if not folded or folded.endswith("봇"):
        return False
    if any(folded == skip.casefold() for skip in INDEX_SAMPLE_SKIP_AUTHORS):
        return False
    line = " ".join((message or "").split())
    if len(line) < 6 or len(line) > INDEX_SAMPLE_MAX_LENGTH:
        return False
    # GeekNews digests and other auto-posted link lists arrive on the persona's
    # own account. They read as the room talking when they are a broadcast.
    if line.startswith("GeekNews") or line.count("http") >= 2:
        return False
    return True


from contextlib import contextmanager
import shutil

ISOLATED_COPY_MAX_ATTEMPTS = 3


def _snapshot_signature(db_path: Path) -> tuple[tuple[str, bool, int, int, int], ...]:
    """Return a cheap mutation detector for the DB and its WAL sidecars."""
    signature: list[tuple[str, bool, int, int, int]] = []
    for path in (
        db_path,
        db_path.with_name(db_path.name + "-wal"),
        db_path.with_name(db_path.name + "-shm"),
    ):
        try:
            stat = path.stat()
        except FileNotFoundError:
            signature.append((path.name, False, 0, 0, 0))
            continue
        signature.append(
            (path.name, True, int(stat.st_size), int(stat.st_mtime_ns), int(stat.st_ino))
        )
    return tuple(signature)


def _copy_consistent_sqlite_replica(db_path: Path, tmpdir: Path) -> Path:
    """Copy DB+WAL+SHM only when the source stayed unchanged for the copy."""
    tmp_db = tmpdir / db_path.name
    last_error: BaseException | None = None
    for attempt in range(1, ISOLATED_COPY_MAX_ATTEMPTS + 1):
        before = _snapshot_signature(db_path)
        try:
            for target in (
                tmp_db,
                tmpdir / (db_path.name + "-wal"),
                tmpdir / (db_path.name + "-shm"),
            ):
                try:
                    target.unlink()
                except FileNotFoundError:
                    pass
            shutil.copy2(db_path, tmp_db)
            for sidecar in (
                db_path.with_name(db_path.name + "-wal"),
                db_path.with_name(db_path.name + "-shm"),
            ):
                if sidecar.exists():
                    shutil.copy2(sidecar, tmpdir / sidecar.name)
        except OSError as error:
            last_error = error
            continue
        after = _snapshot_signature(db_path)
        if before == after:
            if attempt > 1:
                _trace_graph("isolated_copy_retry_succeeded", attempt=attempt)
            return tmp_db
        last_error = sqlite3.OperationalError("source mutated during isolated copy")
        _trace_graph("isolated_copy_mutated", attempt=attempt)
    raise sqlite3.OperationalError("consistent isolated snapshot unavailable") from last_error


@contextmanager
def _open_isolated_ro_conn(db_path: Path):
    """DB Lock 방지: 카카오톡 DB 동기화 시 실행 중 파일 잠금을 방지하기 위해
    임시 디렉터리로 복제한 뒤 Read Only 모드로 안전하게 연결한다 (2026-09-17).
    """
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    with tempfile.TemporaryDirectory(prefix="kg-ro-copy-") as tmpdir_name:
        tmpdir = Path(tmpdir_name)
        try:
            tmp_db = _copy_consistent_sqlite_replica(db_path, tmpdir)
            conn = sqlite3.connect(f"file:{tmp_db}?mode=ro", uri=True)
        except (OSError, sqlite3.Error) as error:
            _trace_graph("isolated_copy_failed", error=type(error).__name__)
            raise sqlite3.OperationalError("isolated read-only snapshot unavailable") from error

        try:
            conn.execute("PRAGMA query_only = ON")
            yield conn
        finally:
            conn.close()


def _k_hop_neighborhood_conn(
    conn: sqlite3.Connection,
    node_id: str,
    k: int,
    limit: int,
    *,
    state_root: Path | None = None,
    chat_id: "int | str | None" = None,
    participant_id: "int | str | None" = None,
    time_from: Any = None,
    time_to: Any = None,
) -> dict[str, Any]:
    """Return a bounded entity-relation-entity neighborhood from one graph DB."""
    root = str(node_id or "").strip()
    bounded_k = _bounded_k(k)
    bounded_limit = _bounded_neighbor_limit(limit)
    empty = {
        "root_id": root,
        "k": bounded_k,
        "limit": bounded_limit,
        "node_ids": [],
        "depth_by_node": {},
        "edges": [],
    }
    if not _is_graph_entity_id(root):
        if root:
            _trace_graph("khop_invalid_node_id", k=bounded_k, limit=bounded_limit)
        return empty

    valid_ids = {
        str(row[0])
        for row in conn.execute("SELECT entity_id FROM kg_entities")
        if _is_graph_entity_id(row[0])
    }
    if root not in valid_ids:
        _trace_graph("khop_missing_node", k=bounded_k, limit=bounded_limit)
        return empty

    ordered = [root]
    depth_by_node: dict[str, int] = {root: 0}
    visited = {root}
    frontier = {root}
    for depth in range(1, bounded_k + 1):
        if not frontier or bounded_limit == 0:
            break
        marks = ",".join("?" for _ in frontier)
        params = list(frontier) + list(frontier)
        rows = conn.execute(
            f"SELECT source_id, target_id, weight FROM kg_relations"
            f" WHERE source_id IN ({marks}) OR target_id IN ({marks})"
            f" ORDER BY weight DESC, id ASC",
            params,
        ).fetchall()
        strongest: dict[str, int] = {}
        for source, target, weight in rows:
            source = str(source)
            target = str(target)
            other = target if source in frontier else source if target in frontier else ""
            if not other or other in visited or other not in valid_ids:
                continue
            if not _participant_in_scope(other, participant_id):
                continue
            if str(chat_id or "").strip() and (
                other.startswith("chat:") or other.startswith("person:")
            ):
                room = other.split(":")[1] if ":" in other else ""
                wanted = str(chat_id)
                if state_root is not None:
                    wanted_key = _room_key(state_root, wanted) or wanted
                    room_key = _room_key(state_root, room) or room
                else:
                    wanted_key, room_key = wanted, room
                if room_key != wanted_key and room != wanted:
                    continue
            strongest[other] = max(strongest.get(other, 0), int(weight or 0))
        next_ids = [
            item[0]
            for item in sorted(strongest.items(), key=lambda item: (-item[1], item[0]))[
                :bounded_limit
            ]
        ]
        if not next_ids:
            break
        for entity_id in next_ids:
            visited.add(entity_id)
            ordered.append(entity_id)
            depth_by_node[entity_id] = depth
        frontier = set(next_ids)

    marks = ",".join("?" for _ in ordered)
    edge_rows = conn.execute(
        f"SELECT source_id, relation, target_id, weight FROM kg_relations"
        f" WHERE source_id IN ({marks}) AND target_id IN ({marks})"
        f" ORDER BY weight DESC, id ASC",
        ordered + ordered,
    ).fetchall()
    edges = [
        {
            "source": str(source),
            "relation": str(relation),
            "target": str(target),
            "weight": int(weight or 0),
        }
        for source, relation, target, weight in edge_rows
        if source in valid_ids and target in valid_ids
    ]
    return {
        "root_id": root,
        "k": bounded_k,
        "limit": bounded_limit,
        "node_ids": ordered,
        "depth_by_node": depth_by_node,
        "edges": edges,
    }


def k_hop_neighborhood(
    node_id: str,
    k: int,
    limit: int,
    *,
    state_root: Path | None = None,
    chat_id: "int | str | None" = None,
    participant_id: "int | str | None" = None,
    time_from: Any = None,
    time_to: Any = None,
) -> dict[str, Any]:
    """Read a bounded k-hop subgraph; invalid input returns an empty result."""
    root = state_root or Path.home() / "Library/Application Support/openkakao/bujamentor"
    kg_path = root / KNOWLEDGE_GRAPH_DB_NAME
    if not kg_path.exists():
        return {
            "root_id": str(node_id or "").strip(),
            "k": _bounded_k(k),
            "limit": _bounded_neighbor_limit(limit),
            "node_ids": [],
            "depth_by_node": {},
            "edges": [],
        }
    conn = None
    try:
        conn = sqlite3.connect(f"file:{kg_path}?mode=ro", uri=True)
        conn.execute("PRAGMA query_only = ON")
        return _k_hop_neighborhood_conn(
            conn,
            node_id,
            k,
            limit,
            state_root=root,
            chat_id=chat_id,
            participant_id=participant_id,
            time_from=time_from,
            time_to=time_to,
        )
    except (OSError, sqlite3.Error, ValueError) as error:
        _trace_graph("khop_failed", error=type(error).__name__)
        return {
            "root_id": str(node_id or "").strip(),
            "k": _bounded_k(k),
            "limit": _bounded_neighbor_limit(limit),
            "node_ids": [],
            "depth_by_node": {},
            "edges": [],
        }
    finally:
        if conn is not None:
            conn.close()


def _index_db_path(state_root: Path) -> Path:
    """The shared context index. Sibling of the state root's parent."""
    return state_root.parent / "context.sqlite3"


def _index_topic_rows(conn: sqlite3.Connection, *, chat: str = "") -> list[tuple[str, int]]:
    """Topics with enough messages to be worth a neuron."""
    if chat:
        cursor = conn.execute(
            "SELECT topic, SUM(message_count) FROM context_topic_stats"
            " WHERE chat = ? GROUP BY topic HAVING SUM(message_count) >= ?"
            " ORDER BY SUM(message_count) DESC",
            (chat, INDEX_TOPIC_MIN_MESSAGES),
        )
    else:
        cursor = conn.execute(
            "SELECT topic, SUM(message_count) FROM context_topic_stats"
            " GROUP BY topic HAVING SUM(message_count) >= ?"
            " ORDER BY SUM(message_count) DESC",
            (INDEX_TOPIC_MIN_MESSAGES,),
        )
    return [(str(topic), int(count or 0)) for topic, count in cursor.fetchall() if topic]


def _index_topic_samples(
    conn: sqlite3.Connection,
    topic: str,
    *,
    chat: str = "",
    limit: int = INDEX_TOPIC_SAMPLE_MESSAGES,
) -> list[tuple[str, str, str]]:
    """Recent (date, user, message) rows for one topic, newest last."""
    # The obvious join with ORDER BY m.date DESC makes SQLite build a temp
    # B-tree over every message the topic ever had, which cost 6.7s for 13
    # topics. The topic index is keyed (topic, message_id), so walking it
    # backwards yields the newest ids directly and any sorting happens in
    # Python on at most `want` rows (2026-09-16).
    want = max(int(limit), 1)
    try:
        row = conn.execute(
            "SELECT MAX(message_id) FROM context_message_topics WHERE topic = ?",
            (topic,),
        ).fetchone()
    except sqlite3.Error:
        return []
    highest = row[0] if row else None
    if highest is None:
        return []
    # Two probes: most topics fit in the first slice, and a busy room whose
    # recent slice is filtered out by the chat clause needs the older one.
    ceiling = int(highest)
    for _attempt in range(2):
        try:
            ids = [
                int(value)
                for (value,) in conn.execute(
                    "SELECT message_id FROM context_message_topics"
                    " WHERE topic = ? AND message_id <= ?"
                    " ORDER BY message_id DESC LIMIT ?",
                    (topic, ceiling, want),
                ).fetchall()
            ]
        except sqlite3.Error:
            return []
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        sql = (
            "SELECT id, date, user_name, message FROM context_messages"
            f" WHERE id IN ({placeholders})"
        )
        params: list[Any] = list(ids)
        if chat:
            sql += " AND chat = ?"
            params.append(chat)
        try:
            found = {
                int(i): (str(d or ""), str(u or ""), str(m or ""))
                for i, d, u, m in conn.execute(sql, params)
            }
        except sqlite3.Error:
            return []
        if found:
            return [found[i] for i in sorted(found)]
        ceiling = min(ids) - 1
        if ceiling <= 0:
            break
    return []


def _synapse_weight(count: int) -> int:
    """Map a co-occurrence count onto 30..99 without flattening the top.

    ``30 + count`` pushed every busy pair to the ceiling: 코인-주식 and
    연락-주식 both drew at 99, so the picture showed no difference between the
    strongest and the weakest link. A log curve keeps them apart (2026-09-16).
    """
    count = max(int(count), 1)
    return max(30, min(99, 30 + int(round(20 * math.log10(count + 1)))))


def _topic_terms(topic: str, samples: list[tuple[str, str, str]]) -> list[str]:
    """Aliases that let a later turn find this neuron.

    The topic key is an English slug, so matching it against Korean chat would
    find nothing. The label is the reliable alias; the slug is kept for turns
    that do use it.
    """
    label = INDEX_TOPIC_LABELS.get(topic, topic)
    terms = [label, topic]
    for part in label.replace("/", " ").split():
        if len(part) >= 2 and part not in terms:
            terms.append(part)
    return terms


def _chat_label(chat: str) -> str:
    """A room id becomes a readable name, and stays findable by its number."""
    raw = (chat or "").strip()
    if raw.startswith("그룹:"):
        return f"그룹방 {raw.split(':', 1)[1]}"
    return raw


def _room_titles(state_root: Path) -> dict[str, str]:
    """방 번호를 사람이 읽는 이름으로 바꾸는 표.

    색인에는 방이 ``그룹:325472527151234``처럼 번호로만 남는 경우가 많다.
    메뉴바가 이미 방 목록을 카탈로그에 적어 두므로, 그래프도 같은 이름을
    써야 표와 그림이 같은 방을 다른 이름으로 부르지 않는다. 읽지 못하면
    빈 표를 돌려주고 번호 이름을 그대로 쓴다 (2026-09-16).
    """
    path = state_root / "menubar-room-catalog.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    rooms = raw.get("rooms") if isinstance(raw, dict) else None
    if not isinstance(rooms, list):
        return {}
    titles: dict[str, str] = {}
    for item in rooms:
        if not isinstance(item, dict):
            continue
        chat_id = str(item.get("chat_id") or "").strip()
        title = str(item.get("title") or "").strip()
        if not chat_id or not title:
            continue
        titles[chat_id] = title
        titles[f"그룹:{chat_id}"] = title
    return titles


def _room_key(state_root: Path, chat: str) -> str:
    """방을 가리키는 하나의 키로 모은다.

    같은 방이 색인에 두 이름으로 들어온다. 어떤 경로는 번호만 알아
    ``그룹:325472527151234``로 적고, 어떤 경로는 사람이 읽는 이름을 안다.
    그래서 같은 방에 뉴런이 둘 서고, 방 안의 사람도 두 벌로 갈렸다.
    카탈로그가 번호와 이름을 모두 알고 있으므로 번호를 이름으로 바꿔
    하나로 모은다. 카탈로그에 없으면 원래 키를 그대로 쓴다 (2026-09-16).
    """
    raw = (chat or "").strip()
    if not raw:
        return raw
    titles = _room_titles(state_root)
    return titles.get(raw) or raw


def _merge_seed_rooms(conn: sqlite3.Connection) -> int:
    """손으로 적은 방 뉴런을 색인 방 뉴런에 합친다.

    기본 뉴런에는 ``ent:channel:bujamentor``가 있고, 색인은 같은 방을
    ``chat:부자멘토멘티``로 만든다. 둘 다 남으면 같은 방이 그림에 두 번
    서고, 손으로 적은 쪽의 시냅스(알쫀쿠 추천 등)는 색인 쪽과 이어지지
    않아 그래프가 끊긴다. 색인 뉴런으로 옮겨 붙이고 옛 뉴런은 지운다.
    옮길 곳이 없으면 그대로 둔다 (2026-09-16).
    """
    moved = 0
    for entity_id, name in list(
        conn.execute(
            "SELECT entity_id, name FROM kg_entities"
            " WHERE entity_id LIKE 'ent:channel:%'"
        )
    ):
        target = f"chat:{name}"
        exists = conn.execute(
            "SELECT 1 FROM kg_entities WHERE entity_id = ?", (target,)
        ).fetchone()
        if not exists:
            continue
        # 시냅스를 옮긴다. 같은 짝이 이미 있으면 무거운 쪽을 남긴다.
        conn.execute(
            "UPDATE OR IGNORE kg_relations SET source_id = ? WHERE source_id = ?",
            (target, entity_id),
        )
        conn.execute(
            "UPDATE OR IGNORE kg_relations SET target_id = ? WHERE target_id = ?",
            (target, entity_id),
        )
        conn.execute("DELETE FROM kg_relations WHERE source_id = ? OR target_id = ?", (entity_id, entity_id))
        conn.execute("DELETE FROM kg_entities WHERE entity_id = ?", (entity_id,))
        moved += 1
    if moved:
        conn.commit()
    return moved


def index_chat_entities(
    conn: sqlite3.Connection,
    state_root: Path,
    *,
    chat: str = "",
    limit: int = 40,
) -> dict[str, int]:
    """채팅방 하나를 뉴런 하나로 세운다.

    방 뉴런이 있어야 "어디서 나눈 이야기인가"가 그래프에 남는다. 주제
    뉴런만 있으면 같은 주제를 여러 방에서 나눠도 한 덩어리로 뭉쳐, 답변이
    엉뚱한 방의 맥락을 끌어온다 (2026-09-16).
    """
    stats = {"chats": 0, "written": 0}
    index_path = _index_db_path(state_root)
    if not index_path.exists():
        return stats
    index_conn = None
    try:
        with _open_isolated_ro_conn(index_path) as index_conn:
            sql = (
                "SELECT chat, COUNT(*), MIN(date), MAX(date) FROM context_messages"
                " WHERE chat IS NOT NULL AND chat != ''"
            )
            params: list[Any] = []
            if chat:
                sql += " AND chat = ?"
                params.append(chat)
            sql += " GROUP BY chat HAVING COUNT(*) >= ? ORDER BY COUNT(*) DESC LIMIT ?"
            params.extend([INDEX_CHAT_MIN_MESSAGES, max(int(limit), 1)])
            rows = index_conn.execute(sql, params).fetchall()
    except sqlite3.Error:
        return stats
    finally:
        # with 블록이 실패하면 index_conn 이 할당되지 않아, 여기서 바로
        # 닫으면 UnboundLocalError 가 원래 예외를 덮어쓴다. 자식 프로세스가
        # exit 1 로만 죽어 원인을 알 수 없던 이유다 (2026-09-17).
        if index_conn is not None:
            try:
                index_conn.close()
            except sqlite3.Error:
                pass
    now = int(time.time())
    # 같은 방이 두 키로 들어오면 여기서 하나로 합친다. 합치지 않으면
    # 방 뉴런이 둘 서고 방 안의 사람도 두 벌로 갈린다 (2026-09-16).
    merged: dict[str, list[Any]] = {}
    for room, count, first, last in rows:
        key = _room_key(state_root, str(room))
        bucket = merged.setdefault(key, [0, "", ""])
        bucket[0] += int(count or 0)
        start, end = str(first or "")[:10], str(last or "")[:10]
        if start and (not bucket[1] or start < bucket[1]):
            bucket[1] = start
        if end and (not bucket[2] or end > bucket[2]):
            bucket[2] = end
    for key, (total, start, end) in merged.items():
        name = _chat_label(key)
        span = f"{start} ~ {end}" if start and end else ""
        facts = [f"이 방에 {total:,}건의 메시지가 색인되어 있습니다"]
        if span:
            facts.append(f"기록된 기간: {span}")
        conn.execute(
            """
            INSERT INTO kg_entities
                (entity_id, name, category, aliases_json, description,
                 key_facts_json, importance, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entity_id) DO UPDATE SET
                name=excluded.name,
                category=excluded.category,
                aliases_json=excluded.aliases_json,
                description=excluded.description,
                key_facts_json=excluded.key_facts_json,
                importance=excluded.importance,
                updated_at=excluded.updated_at
            """,
            (
                f"chat:{key}",
                name,
                "대화방",
                json.dumps([name, key], ensure_ascii=False),
                f"{name} 방입니다. 여기서 나눈 주제는 이 방의 맥락으로 남습니다.",
                json.dumps(facts, ensure_ascii=False),
                min(99, 45 + total // 4000),
                now,
            ),
        )
        stats["written"] += 1
    conn.commit()
    stats["chats"] = len(merged)
    return stats


def index_person_entities(
    conn: sqlite3.Connection,
    state_root: Path,
    *,
    chat: str = "",
    limit: int = 60,
) -> dict[str, int]:
    """대화 상대 하나를 뉴런 하나로 세운다.

    사람 뉴런은 방 뉴런에 매인다. 같은 이름이 여러 방에 있어도 방마다 다른
    뉴런이 되어, "성린이형 방의 성린이"와 다른 방의 동명이인이 섞이지 않는다
    (2026-09-16, 사용자 지시).
    """
    stats = {"persons": 0, "written": 0, "with_lines": 0}
    index_path = _index_db_path(state_root)
    if not index_path.exists():
        return stats
    per_room: dict[str, int] = {}
    selected: list[tuple[str, str, int, float, str, list[str]]] = []
    try:
        with _open_isolated_ro_conn(index_path) as index_conn:
            sql = (
                "SELECT chat, user_name, COUNT(*) FROM context_messages"
                " WHERE chat IS NOT NULL AND chat != ''"
                "   AND user_name IS NOT NULL AND user_name != ''"
            )
            params: list[Any] = []
            if chat:
                sql += " AND chat = ?"
                params.append(chat)
            sql += (
                " GROUP BY chat, user_name"
                " HAVING COUNT(*) >= ?"
                " ORDER BY COUNT(*) DESC LIMIT ?"
            )
            params.extend([INDEX_PERSON_MIN_MESSAGES, max(int(limit), 1)])
            rows = index_conn.execute(sql, params).fetchall()
            # 방마다 몇 명을 세울지 정하려면 방별 총량이 필요하다.
            totals: dict[str, int] = {}
            for room, count in index_conn.execute(
                "SELECT chat, COUNT(*) FROM context_messages"
                " WHERE chat IS NOT NULL AND chat != '' GROUP BY chat"
            ):
                totals[str(room)] = int(count or 0)
            # 방 키를 하나로 모은 뒤에 사람을 센다. 같은 방이 두 키로 들어오면
            # 한 사람이 두 뉴런으로 갈리고, 방마다 세는 상한도 두 번 적용된다
            # (2026-09-16).
            merged: dict[tuple[str, str], int] = {}
            raw_keys: dict[str, list[str]] = {}
            for room, user, count in rows:
                room_key = _room_key(state_root, str(room))
                key = (room_key, str(user).strip())
                merged[key] = merged.get(key, 0) + int(count or 0)
                bucket = raw_keys.setdefault(room_key, [])
                if str(room) not in bucket:
                    bucket.append(str(room))
            merged_totals: dict[str, int] = {}
            for room, count in totals.items():
                key = _room_key(state_root, room)
                merged_totals[key] = merged_totals.get(key, 0) + int(count or 0)
            # 사람이 많은 방부터 세운다. 상한에 먼저 닿는 쪽이 대화를 많이 한 방이어야
            # 그림이 그 방을 중심으로 읽힌다.
            for (room_key, user), count in sorted(merged.items(), key=lambda item: -item[1]):
                name = user
                if not name or name.endswith("봇"):
                    continue
                if any(name.casefold() == skip.casefold() for skip in INDEX_SAMPLE_SKIP_AUTHORS):
                    continue
                # 방 이름과 같은 화자가 곧 그 방 자체다.
                #
                # 은행·증권·쇼핑 앱은 방마다 알림을 보내고, 그 알림의 화자 이름이
                # 방 이름과 같다. 그래서 "커리어톡 (커리어톡)" 같은 뉴런이 방
                # 뉴런 바로 옆에 하나 더 서서, 같은 것을 가리키는 두 이름이 그림에
                # 겹쳐 보였다. 방 이름과 같은 화자는 방 뉴런이 이미 말하고 있으므로
                # 사람 뉴런으로 세우지 않는다 (2026-09-16, 6 Pro 지적).
                room_name = _chat_label(room_key)
                if name.casefold() == room_name.casefold():
                    continue
                taken = per_room.get(room_key, 0)
                if taken >= INDEX_PERSONS_PER_CHAT:
                    continue
                total = merged_totals.get(room_key, 0)
                share = (count / total) if total else 0.0
                if total >= INDEX_CHAT_MIN_MESSAGES and share < INDEX_PERSON_SHARE:
                    continue
                per_room[room_key] = taken + 1
                lines = _index_person_lines(
                    index_conn, raw_keys.get(room_key, [room_key]), name
                )
                selected.append((room_key, name, count, share, room_name, lines))
    except sqlite3.Error:
        return stats
    now = int(time.time())
    for room_key, name, count, share, room_name, lines in selected:
        if lines:
            stats["with_lines"] += 1
        facts = [f"{room_name}에서 {count:,}건의 메시지를 남겼습니다"]
        if share:
            facts.append(f"이 방 메시지의 {share * 100:.1f}%입니다")
        facts.extend(lines)
        conn.execute(
            """
            INSERT INTO kg_entities
                (entity_id, name, category, aliases_json, description,
                 key_facts_json, importance, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entity_id) DO UPDATE SET
                name=excluded.name,
                category=excluded.category,
                aliases_json=excluded.aliases_json,
                description=excluded.description,
                key_facts_json=excluded.key_facts_json,
                importance=excluded.importance,
                updated_at=excluded.updated_at
            """,
            (
                f"person:{room_key}:{name}",
                f"{name} ({room_name})",
                "대화 상대",
                json.dumps([name], ensure_ascii=False),
                f"{room_name}에서 {count:,}건을 남긴 대화 상대입니다.",
                json.dumps(facts, ensure_ascii=False),
                min(99, 35 + count // 3000),
                now,
            ),
        )
        stats["written"] += 1
    conn.commit()
    stats["persons"] = len(per_room)
    return stats



def _index_person_lines(
    conn: sqlite3.Connection,
    chats: str | list[str],
    user: str,
    *,
    limit: int = INDEX_PERSON_SAMPLE_LINES,
) -> list[str]:
    """이 사람이 실제로 남긴 최근 대화 몇 줄.

    요약이 아니라 발언 원문이다. 답변이 그 사람의 말투와 관심사를 그대로
    참고할 수 있어야 한다 (2026-09-16).
    """
    keys = [chats] if isinstance(chats, str) else list(chats)
    if not keys:
        return []
    marks = ",".join("?" * len(keys))
    try:
        # 같은 방이 두 키로 색인되어 있어도 한 사람의 발언을 모아 읽는다.
        rows = conn.execute(
            "SELECT date, message FROM context_messages"
            f" WHERE chat IN ({marks}) AND user_name = ?"
            " ORDER BY id DESC LIMIT ?",
            keys + [user, max(int(limit) * 6, 12)],
        ).fetchall()
    except sqlite3.Error:
        return []
    lines: list[str] = []
    seen: set[str] = set()
    for date, message in rows:
        line = " ".join((message or "").split())
        if not _sample_is_conversation(user, line) or line in seen:
            continue
        seen.add(line)
        when = str(date or "")[:10]
        lines.append(f"{when} · {line[:90]}" if when else line[:90])
        if len(lines) >= max(int(limit), 1):
            break
    return lines



def index_topic_entities(
    conn: sqlite3.Connection,
    state_root: Path,
    *,
    chat: str = "",
    limit: int = 40,
) -> dict[str, int]:
    """Turn indexed topics into neurons, with a sample of real messages.

    Each neuron carries the message count, the span of dates it covers, and up
    to three recent lines so the model sees what the room actually said rather
    than a summary someone typed once (2026-09-16).
    """
    stats = {"topics": 0, "written": 0, "with_samples": 0}
    index_path = _index_db_path(state_root)
    if not index_path.exists():
        return stats
    try:
        with _open_isolated_ro_conn(index_path) as index_conn:
            topics = _index_topic_rows(index_conn, chat=chat)[: max(int(limit), 1)]
            stats["topics"] = len(topics)
            now = int(time.time())
            for topic, count in topics:
                samples = _index_topic_samples(index_conn, topic, chat=chat)
                label = INDEX_TOPIC_LABELS.get(topic, topic)
                dates = [date for date, _user, _message in samples if date]
                span = ""
                if dates:
                    span = f"{dates[0][:10]} ~ {dates[-1][:10]}"
                facts: list[str] = []
                if count:
                    facts.append(f"이 방에서 {count}건의 메시지가 이 주제로 묶였습니다")
                if span:
                    facts.append(f"기록된 기간: {span}")
                # Recent lines, newest first, deduplicated, and short enough to
                # keep the prompt small.
                seen: set[str] = set()
                for _date, user, message in reversed(samples):
                    line = " ".join(message.split())
                    if not _sample_is_conversation(user, line) or line in seen:
                        continue
                    seen.add(line)
                    facts.append(f"{user or '누군가'}: {line[:90]}")
                    if len(facts) >= 6:
                        break
                if samples:
                    stats["with_samples"] += 1
                conn.execute(
                    """
                    INSERT INTO kg_entities
                        (entity_id, name, category, aliases_json, description,
                         key_facts_json, importance, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(entity_id) DO UPDATE SET
                        name=excluded.name,
                        category=excluded.category,
                        aliases_json=excluded.aliases_json,
                        description=excluded.description,
                        key_facts_json=excluded.key_facts_json,
                        importance=excluded.importance,
                        updated_at=excluded.updated_at
                    """,
                    (
                        f"topic:{topic}",
                        f"{label} (대화 주제)",
                        "대화 주제",
                        json.dumps(_topic_terms(topic, samples), ensure_ascii=False),
                        f"색인된 대화에서 {count}건이 묶인 주제입니다. 최근 대화가 이 주제 위에 있습니다.",
                        json.dumps(facts, ensure_ascii=False),
                        min(99, 40 + count // 200),
                        now,
                    ),
                )
                stats["written"] += 1
            conn.commit()
    except (sqlite3.Error, OSError, ValueError):
        return stats
    return stats



def index_topic_relations(
    conn: sqlite3.Connection,
    state_root: Path,
    *,
    chat: str = "",
    limit: int = 40,
) -> dict[str, int]:
    """Draw a synapse between two topics that share messages.

    Two topics mentioned in the same message are related, and the number of
    such messages is the weight. That is what makes the picture a network
    rather than a list of separate circles (2026-09-16).
    """
    stats = {"pairs": 0, "written": 0}
    index_path = _index_db_path(state_root)
    if not index_path.exists():
        return stats
    known = {
        row[0]
        for row in conn.execute(
            "SELECT entity_id FROM kg_entities WHERE entity_id LIKE 'topic:%'"
        )
    }
    if not known:
        return stats
    try:
        with _open_isolated_ro_conn(index_path) as index_conn:
            sql = (
                "SELECT a.topic, b.topic, COUNT(*) FROM context_message_topics a"
                " JOIN context_message_topics b"
                "   ON a.message_id = b.message_id AND a.topic < b.topic"
            )
            params: list[Any] = []
            if chat:
                sql += " JOIN context_messages m ON m.id = a.message_id WHERE m.chat = ?"
                params.append(chat)
            sql += " GROUP BY a.topic, b.topic ORDER BY COUNT(*) DESC LIMIT ?"
            params.append(max(int(limit), 1))
            rows = index_conn.execute(sql, params).fetchall()
    except sqlite3.Error:
        return stats
    now = int(time.time())
    for left, right, count in rows:
        left_id, right_id = f"topic:{left}", f"topic:{right}"
        if left_id not in known or right_id not in known:
            continue
        stats["pairs"] += 1
        _upsert_relation(
            conn,
            source_id=left_id,
            relation="CO_OCCURS",
            target_id=right_id,
            context=f"같은 메시지에서 {int(count)}번 함께 언급됨",
            weight=_synapse_weight(count),
            updated_at=now,
        )
        stats["written"] += 1
    conn.commit()
    return stats



def index_membership_relations(
    conn: sqlite3.Connection,
    state_root: Path,
    *,
    chat: str = "",
    limit: int = 400,
) -> dict[str, int]:
    """방·사람·주제를 서로 잇는다.

    세 종류의 시냅스를 만든다.
    - 사람 → 방 (TALKED_IN): 이 사람이 이 방에서 말한다
    - 방 → 주제 (DISCUSSED): 이 방에서 이 주제를 다뤘다
    - 사람 → 주제 (TALKS_ABOUT): 이 사람이 이 주제를 자주 꺼낸다

    세 번째가 답변에 가장 쓸모 있다. 같은 "주식"이라도 누가 꺼낸
    이야기인지에 따라 답이 달라지기 때문이다 (2026-09-16, 사용자 지시).
    """
    stats = {"person_chat": 0, "chat_topic": 0, "person_topic": 0}
    index_path = _index_db_path(state_root)
    if not index_path.exists():
        return stats
    known = {
        row[0]
        for row in conn.execute("SELECT entity_id FROM kg_entities")
    }
    if not known:
        return stats
    try:
        with _open_isolated_ro_conn(index_path) as index_conn:
            now = int(time.time())
            # 사람 → 방
            sql = (
                "SELECT chat, user_name, COUNT(*) FROM context_messages"
                " WHERE chat IS NOT NULL AND chat != ''"
                "   AND user_name IS NOT NULL AND user_name != ''"
            )
            params: list[Any] = []
            if chat:
                sql += " AND chat = ?"
                params.append(chat)
            sql += " GROUP BY chat, user_name ORDER BY COUNT(*) DESC LIMIT ?"
            params.append(max(int(limit), 1))
            try:
                for room, user, count in index_conn.execute(sql, params):
                    room_key, name = _room_key(state_root, str(room)), str(user).strip()
                    source = f"person:{room_key}:{name}"
                    target = f"chat:{room_key}"
                    if source not in known or target not in known:
                        continue
                    _upsert_relation(
                        conn,
                        source_id=source,
                        relation="TALKED_IN",
                        target_id=target,
                        context=f"이 방에서 {int(count or 0):,}건을 남김",
                        weight=_synapse_weight(int(count or 0)),
                        updated_at=now,
                        room_id=room_key,
                    )
                    stats["person_chat"] += 1
            except sqlite3.Error:
                pass
            # 방 → 주제. 방마다 그 주제가 얼마나 나왔는지가 곧 연결 강도다.
            sql = (
                "SELECT m.chat, t.topic, COUNT(*) FROM context_message_topics t"
                " JOIN context_messages m ON m.id = t.message_id"
                " WHERE m.chat IS NOT NULL AND m.chat != ''"
            )
            params = []
            if chat:
                sql += " AND m.chat = ?"
                params.append(chat)
            sql += " GROUP BY m.chat, t.topic ORDER BY COUNT(*) DESC LIMIT ?"
            params.append(max(int(limit), 1))
            try:
                for room, topic, count in index_conn.execute(sql, params):
                    source = f"chat:{_room_key(state_root, str(room))}"
                    target = f"topic:{topic}"
                    if source not in known or target not in known:
                        continue
                    _upsert_relation(
                        conn,
                        source_id=source,
                        relation="DISCUSSED",
                        target_id=target,
                        context=f"이 방에서 {int(count or 0):,}건이 이 주제로 묶임",
                        weight=_synapse_weight(int(count or 0)),
                        updated_at=now,
                        room_id=_room_key(state_root, str(room)),
                    )
                    stats["chat_topic"] += 1
            except sqlite3.Error:
                pass
            # 사람 → 주제. 사람 뉴런은 `person:방:이름` 꼴이라, 그 사람의 메시지에
            # 붙은 주제를 세면 곧 사람과 주제의 연결이 된다.
            sql = (
                "SELECT m.chat, m.user_name, t.topic, COUNT(*)"
                " FROM context_message_topics t"
                " JOIN context_messages m ON m.id = t.message_id"
                " WHERE m.chat IS NOT NULL AND m.chat != ''"
                "   AND m.user_name IS NOT NULL AND m.user_name != ''"
            )
            params = []
            if chat:
                sql += " AND m.chat = ?"
                params.append(chat)
            sql += " GROUP BY m.chat, m.user_name, t.topic ORDER BY COUNT(*) DESC LIMIT ?"
            params.append(max(int(limit), 1))
            try:
                for room, user, topic, count in index_conn.execute(sql, params):
                    source = f"person:{_room_key(state_root, str(room))}:{str(user).strip()}"
                    target = f"topic:{topic}"
                    if source not in known or target not in known:
                        continue
                    _upsert_relation(
                        conn,
                        source_id=source,
                        relation="TALKS_ABOUT",
                        target_id=target,
                        context=f"이 주제로 {int(count or 0):,}건을 말함",
                        weight=_synapse_weight(int(count or 0)),
                        updated_at=now,
                        room_id=_room_key(state_root, str(room)),
                    )
                    stats["person_topic"] += 1
            except sqlite3.Error:
                pass
    except sqlite3.Error:
        return stats
    conn.commit()
    return stats



def prune_indexed_entities(
    conn: sqlite3.Connection,
    *,
    cycle_started_at: int,
    min_keep_ratio: float = 0.5,
) -> dict[str, int]:
    """색인에서 사라진 뉴런을 지운다.

    색인 단계는 새 뉴런을 넣고 기존 것을 고쳐 쓸 뿐이라, 방 이름이 바뀌거나
    색인에서 빠진 대화가 그래프에 그대로 남았다. 그래서 같은 방을 가리키는
    두 뉴런("커리어톡"과 "커리어톡 (커리어톡)")이 나란히 서고, 지워진 주제도
    계속 그려졌다. 색인으로 만든 뉴런 중 이번 주기에 다시 쓰이지 않은 것을
    지우고, 그 뉴런에 매달린 시냅스도 함께 지운다 (2026-09-16, 사용자 지시).

    손으로 적은 기본 뉴런(ent:*)과 원장에서 온 근거는 건드리지 않는다.

    기준 시각은 이번 주기가 시작된 때다. "일주일보다 오래된 것"을 지우면
    이번에 방 이름이 바뀌어 다시 쓰이지 않은 옛 뉴런이 그대로 남는다
    (2026-09-16).

    색인을 읽지 못한 채 이 함수가 돌면 그래프가 통째로 비어 버린다.
    지우는 수가 절반을 넘으면 색인 쪽이 고장 난 것으로 보고 아무것도
    지우지 않는다 (2026-09-16).
    """
    cutoff = int(cycle_started_at)
    stale = [
        row[0]
        for row in conn.execute(
            "SELECT entity_id FROM kg_entities"
            " WHERE (entity_id LIKE 'chat:%'"
            "     OR entity_id LIKE 'person:%'"
            "     OR entity_id LIKE 'topic:%')"
            "   AND updated_at < ?",
            (cutoff,),
        )
    ]
    if not stale:
        return {"nodes": 0, "relations": 0}
    indexed_total = int(
        conn.execute(
            "SELECT COUNT(*) FROM kg_entities"
            " WHERE entity_id LIKE 'chat:%'"
            "    OR entity_id LIKE 'person:%'"
            "    OR entity_id LIKE 'topic:%'"
        ).fetchone()[0]
    )
    if indexed_total and len(stale) > max(int(indexed_total * max(min_keep_ratio, 0.0)), 0):
        return {"nodes": 0, "relations": 0, "skipped": len(stale)}
    removed_relations = 0
    for start in range(0, len(stale), 200):
        chunk = stale[start : start + 200]
        marks = ",".join("?" * len(chunk))
        cursor = conn.execute(
            f"DELETE FROM kg_relations WHERE source_id IN ({marks}) OR target_id IN ({marks})",
            chunk + chunk,
        )
        removed_relations += cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
        conn.execute(f"DELETE FROM kg_entities WHERE entity_id IN ({marks})", chunk)
    conn.commit()
    return {"nodes": len(stale), "relations": removed_relations}


def attach_ledger_evidence(
    conn: sqlite3.Connection,
    state_root: Path,
    *,
    max_rows: int = 4000,
) -> dict[str, int]:
    """Point every node at the real messages that mention it.

    The graph used to be a Python constant: no node knew which message or room
    it came from, so a reviewer could not check it. This walks the room
    ledgers, matches each entity's aliases against the message text, and stores
    the matching event ids with the room and the time they were recorded. A
    node with no match keeps the seed provenance and says so.
    """
    stats = {"scanned": 0, "matched": 0, "nodes_with_evidence": 0, "rooms": 0}
    entities = list(
        conn.execute("SELECT entity_id, name, aliases_json FROM kg_entities")
    )
    if not entities:
        return stats
    needles: dict[str, list[str]] = {}
    for entity_id, name, aliases_json in entities:
        try:
            aliases = json.loads(aliases_json)
        except (TypeError, ValueError):
            aliases = []
        if not isinstance(aliases, list):
            aliases = []
        terms = [str(name)] + [str(alias) for alias in aliases]
        needles[entity_id] = [term.casefold() for term in terms if len(term) >= 3]

    found: dict[str, list[dict[str, Any]]] = {key: [] for key in needles}
    for ledger in _room_ledgers(state_root):
        stats["rooms"] += 1
        chat_id = ledger.parent.name
        try:
            text = ledger.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if stats["scanned"] >= max_rows:
                break
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            message = str(row.get("message") or "").casefold()
            if not message:
                continue
            stats["scanned"] += 1
            event_id = str(row.get("event_id") or "").strip()
            if not event_id:
                continue
            recorded_at = str(row.get("recorded_at") or "")
            for entity_id, terms in needles.items():
                if not terms:
                    continue
                if any(term in message for term in terms):
                    bucket = found[entity_id]
                    if len(bucket) >= MAX_EVIDENCE_PER_NODE:
                        continue
                    bucket.append(
                        {
                            "event_id": event_id,
                            "chat_id": chat_id,
                            "recorded_at": recorded_at,
                        }
                    )
                    stats["matched"] += 1

    for entity_id, hits in found.items():
        if not hits:
            conn.execute(
                "UPDATE kg_entities SET evidence_json = ? WHERE entity_id = ?",
                (json.dumps(_seed_evidence(), ensure_ascii=False), entity_id),
            )
            continue
        evidence = {
            "kind": PROVENANCE_LEDGER,
            "source_event_ids": [hit["event_id"] for hit in hits],
            "chat_id": hits[0]["chat_id"],
            "confirmed_at": hits[0]["recorded_at"] or None,
            "retracted": False,
        }
        conn.execute(
            "UPDATE kg_entities SET evidence_json = ? WHERE entity_id = ?",
            (json.dumps(evidence, ensure_ascii=False), entity_id),
        )
        stats["nodes_with_evidence"] += 1

    # An edge is only as grounded as its two endpoints.
    conn.execute(
        """
        UPDATE kg_relations
        SET evidence_json = COALESCE(
            (SELECT e.evidence_json FROM kg_entities e WHERE e.entity_id = kg_relations.source_id),
            '{}'
        )
        """
    )
    conn.commit()
    return stats


# 재색인은 프로세스마다 하나만 돌린다. 메뉴바는 2초마다 폴링하고 감사도
# 같은 명령을 부르므로, 가드가 없으면 같은 작업이 여러 번 겹쳐 돈다.
_reindex_lock = threading.Lock()
_reindex_inflight = False
_reindex_last_started_at = 0.0


def wait_for_background_reindex(timeout: float = 30.0) -> bool:
    """Block until the in-flight reindex finishes.
    
    The menu bar must never wait, but a caller that needs a settled graph
    (a test, or a one-shot export) can. Returns True when nothing is left
    running (2026-09-17).
    """
    deadline = time.time() + max(0.0, timeout)
    while time.time() < deadline:
        with _reindex_lock:
            if not _reindex_inflight:
                return True
        time.sleep(0.05)
    with _reindex_lock:
        return not _reindex_inflight


def _start_background_reindex(
    conn: sqlite3.Connection,
    state_root: Path,
    *,
    chat: str = "",
    cycle_started_at: int | None = None,
    mode: str = "process",
) -> dict[str, Any]:
    """Kick the heavy reindex off the request path.

    Returns a small status dict the caller can report. A second call while one
    is already running returns {"started": False, "reason": "in_flight"} so the
    caller keeps serving the stored graph instead of stacking work
    (2026-09-17, 6 Pro 지적).
    """
    global _reindex_inflight, _reindex_last_started_at
    started_at = int(cycle_started_at or time.time())
    if mode == "process":
        # 프로세스 모드에서는 자식이 잠금 파일을 들고 있다. 부모의 메모리
        # 플래그는 쓰지 않는다. 부모는 자식이 언제 끝나는지 볼 수 없으므로,
        # 플래그를 잡으면 부모가 사는 동안 영원히 in_flight 로 남아 재색인이
        # 다시는 걸리지 않는다 (2026-09-17).
        if _reindex_process_running(state_root):
            return {"started": False, "reason": "in_flight"}
        if _spawn_reindex_process(state_root, chat=chat, cycle_started_at=started_at):
            return {
                "started": True,
                "chat": chat,
                "cycle_started_at": started_at,
                "mode": "process",
            }
        # 자식을 띄울 수 없으면 스레드로라도 돌린다. 메뉴가 길게 사는
        # 경우에는 이쪽으로도 끝난다.
        mode = "thread"
    with _reindex_lock:
        if _reindex_inflight:
            return {"started": False, "reason": "in_flight"}
        _reindex_inflight = True
        _reindex_last_started_at = time.time()

    def _run() -> None:
        global _reindex_inflight
        worker_conn = None
        try:
            # A fresh connection: the caller closes its own when the response is
            # built, and SQLite connections are not safe to share across threads.
            worker_conn = _connect_kg(state_root / KNOWLEDGE_GRAPH_DB_NAME)
            _reindex_all(worker_conn, state_root, chat=chat, cycle_started_at=started_at)
        except (OSError, sqlite3.Error, ValueError, ImportError, RuntimeError):
            # A failed refresh must not kill the process; the stored graph stays
            # and the next poll retries.
            pass
        finally:
            if worker_conn is not None:
                try:
                    worker_conn.close()
                except sqlite3.Error:
                    pass
            with _reindex_lock:
                _reindex_inflight = False

    # 메뉴바는 이 모듈을 짧게 사는 프로세스로 부른다. 데몬 스레드는 그
    # 프로세스가 끝나면 함께 죽어 재색인이 반쯤 돌다 만 채로 남는다. 그래서
    # 기본값(mode="process")은 스스로 살아남는 자식 프로세스다 (2026-09-17,
    # 사용자 지시). 테스트는 "thread", 일회성 내보내기는 "inline" 으로 돈다.
    thread = threading.Thread(target=_run, name="kg-reindex", daemon=True)
    thread.start()
    return {"started": True, "chat": chat, "cycle_started_at": started_at, "mode": mode}


def _reindex_process_running(state_root: Path) -> bool:
    """True when a detached child already holds the reindex lock.

    The lock file is the only thing the parent and the child share, so it is
    also the only honest answer to whether a walk is still going (2026-09-17).
    """
    lock_path = state_root / "knowledge-graph-reindex.lock"
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        return False
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        os.close(descriptor)
        return True
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError:
        pass
    os.close(descriptor)
    return False


def _spawn_reindex_process(
    state_root: Path,
    *,
    chat: str = "",
    cycle_started_at: int = 0,
) -> bool:
    """Detach one reindex run so it outlives the caller.

    Returns True when a child was started. The child holds the same lock file
    the in-process guard uses, so a burst of menu polls still starts one walk
    (2026-09-17).
    """
    lock_path = state_root / "knowledge-graph-reindex.lock"
    try:
        state_root.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        return False
    try:
        # A non-blocking exclusive lock: whoever holds it is already walking
        # the index. The descriptor stays open in the child for its lifetime.
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        os.close(descriptor)
        return False
    argv = [
        sys.executable,
        "-B",
        str(Path(__file__).resolve()),
        "--reindex-once",
        "--state-root",
        str(state_root),
        "--chat",
        str(chat),
        "--cycle-started-at",
        str(int(cycle_started_at or time.time())),
    ]
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
            pass_fds=(descriptor,),
            cwd=str(Path(__file__).resolve().parent),
        )
    except (OSError, ValueError):
        os.close(descriptor)
        return False
    # 부모 쪽 사본은 닫는다. 잠금은 자식이 들고 있다.
    os.close(descriptor)
    if process.poll() is not None:
        # 곧바로 죽었다면(인자 오류 등) 실패로 본다.
        return False
    return True


def _reindex_once_entry(argv: list[str]) -> int:
    """Run one reindex in this process. Used by the detached child."""
    import argparse

    parser = argparse.ArgumentParser(prog="auto_reply_knowledge_graph")
    parser.add_argument("--reindex-once", action="store_true")
    parser.add_argument("--state-root", default="")
    parser.add_argument("--chat", default="")
    parser.add_argument("--cycle-started-at", type=int, default=0)
    args = parser.parse_args(argv)
    root_raw = str(args.state_root or "").strip()
    if not root_raw:
        return 2
    root = Path(root_raw).expanduser()
    try:
        conn = _connect_kg(root / KNOWLEDGE_GRAPH_DB_NAME)
    except (OSError, sqlite3.Error):
        return 1
    try:
        _reindex_all(
            conn,
            root,
            chat=str(args.chat or ""),
            cycle_started_at=int(args.cycle_started_at or time.time()),
        )
    except Exception as error:
        # 자식은 어떤 경우에도 조용히 끝난다. 메뉴가 읽는 것은 상태 파일이지
        # 이 프로세스의 종료 코드가 아니다. 다만 왜 죽었는지는 남긴다
        # (2026-09-17).
        write_meta(conn, "last_index_error", f"detached: {type(error).__name__}: {error}"[:400])
        return 1
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass
    return 0


def _reindex_all(
    conn: sqlite3.Connection,
    state_root: Path,
    *,
    chat: str = "",
    cycle_started_at: int = 0,
) -> None:
    """Rebuild every indexed neuron and synapse for the graph.

    This is the body that used to run inline in collect_knowledge_graph.
    Each step is isolated: a failure in one indexer must not stop the others,
    because a graph with topics but no rooms is still better than no refresh
    (2026-09-16, 2026-09-17).

    A step that dies is recorded in kg_meta instead of being swallowed. The
    menu showed a freshly indexed, empty graph while every indexer had been
    failing on a NameError that only the real database path reached
    (2026-09-17, 사용자 지시).
    """
    failures: list[str] = []

    def _note(step: str, error: BaseException) -> None:
        failures.append(f"{step}: {type(error).__name__}: {error}"[:400])

    try:
        index_topic_entities(conn, state_root, chat=chat)
        index_topic_relations(conn, state_root, chat=chat)
    except Exception as error:  # noqa: BLE001 - 색인은 한 걸음이 죽어도 계속한다
        _note("topics", error)
    try:
        index_chat_entities(conn, state_root, chat=chat)
        index_person_entities(conn, state_root, chat=chat)
        index_membership_relations(conn, state_root, chat=chat)
        prune_indexed_entities(conn, cycle_started_at=cycle_started_at)
        _merge_seed_rooms(conn)
    except Exception as error:  # noqa: BLE001
        _note("rooms", error)
    try:
        attach_ledger_evidence(conn, state_root)
    except Exception as error:  # noqa: BLE001
        _note("evidence", error)
    if failures:
        # 실패를 남기고 "색인했다"는 도장은 찍지 않는다. 찍으면 다음 폴링이
        # 같은 일을 다시 하지 않아 그래프가 영영 낡은 채로 남는다
        # (2026-09-17).
        write_meta(conn, "last_index_error", " | ".join(failures))
        if any("isolated read-only snapshot unavailable" in failure for failure in failures):
            # 카카오 원본 DB로 폴백하지 않았음을 상태 화면에서도 확인할 수
            # 있게 남긴다. 이 값은 다음 정상 색인이 성공할 때만 해제된다.
            write_meta(conn, "last_snapshot_status", "fail_closed")
        return
    write_meta(conn, "last_index_error", "")
    write_meta(conn, "last_snapshot_status", "copy_ok")
    # 이번 색인이 언제 끝났는지 남긴다. 이 값이 없으면 다음 폴링이 방금
    # 끝난 색인을 모르고 같은 일을 다시 시작한다 (2026-09-17).
    write_meta(conn, "last_indexed_at", str(int(time.time())))



def collect_knowledge_graph(
    db_path: Path,
    *,
    state_root: Path | None = None,
    chat: str = "",
    focus_node_id: str = "",
    focus_k: int = DEFAULT_K_HOP,
    focus_limit: int = K_HOP_NEIGHBOR_LIMIT,
    force_reindex: bool = False,
    reindex_interval_seconds: int = 300,
    wait_for_reindex: bool = False,
    reindex_mode: str = "process",
) -> dict[str, Any]:
    """Return the whole graph as nodes and edges for a force-directed view.

    Node ids stay stable across calls so a viewer can animate the same layout
    instead of re-randomising every refresh. ``chat`` limits the indexed topics
    to one room; empty means every room the index covers.

    A refresh normally runs in the background so the menu bar never blocks on
    the 1.4GB context DB. Set ``wait_for_reindex`` when the caller needs a settled
    graph before reading rows: a one-shot export, or a test asserting on
    freshly indexed evidence (2026-09-17, 6 Pro 지적).

    reindex_mode picks who walks the index when wait_for_reindex is false:
    "process" detaches a child that outlives the short-lived menu call,
    "thread" keeps the walk in this process so a test can wait for it
    (2026-09-17).
    """
    kg_path = db_path.parent / KNOWLEDGE_GRAPH_DB_NAME
    try:
        kg_path.parent.mkdir(parents=True, exist_ok=True)
        conn = _connect_kg(kg_path)
    except (OSError, sqlite3.Error) as error:
        # A window that cannot reach its store must draw an empty graph and say
        # why, not take the menu down with it (2026-09-16).
        return {
            "ok": False,
            "nodes": [],
            "edges": [],
            "node_count": 0,
            "edge_count": 0,
            "grounded_nodes": 0,
            "reason": str(error) or "knowledge_graph_unavailable",
        }
    try:
        ensure_seeded(conn)
        # 이미 색인된 데이터가 있고 최근(기본 5분)에 갱신되었다면 매번
        # 1.4GB DB 전체를 다시 훑지 않고 저장된 그래프를 즉시 반환한다.
        # 메뉴바 폴링(2초 주기)과 감사에서 프로세스가 25초 타임아웃에
        # 걸려 빈 그래프로 떨어지는 병목을 해결한다 (2026-09-17).
        now = int(time.time())
        # 색인 시각은 메타에 남는다. 예전에는 뉴런의 updated_at 최댓값을
        # 썼는데, 손으로 적은 시드 뉴런만 있는 그래프에서는 그 값이 방금
        # 갱신된 것으로 보여 재색인이 영원히 걸리지 않았다. 반대로 색인할
        # 대화가 없으면 개수가 0이라 매 폴링마다 재색인이 걸렸다. 이제
        # "언제 색인을 끝냈는가"를 직접 기록해 둘 다 피한다 (2026-09-17).
        try:
            indexed_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM kg_entities"
                    " WHERE entity_id LIKE 'chat:%' OR entity_id LIKE 'topic:%'"
                ).fetchone()[0]
                or 0
            )
        except sqlite3.Error:
            indexed_count = 0
        last_indexed_raw = read_meta(conn, "last_indexed_at")
        try:
            last_updated = int(last_indexed_raw or 0)
        except ValueError:
            last_updated = 0
        # 메타가 없으면(=예전 그래프) 한 번은 다시 색인한다. 행의 updated_at
        # 으로 대신하면, 방금 심은 시드 뉴런의 시각이 "최신"으로 읽혀 색인이
        # 영영 돌지 않는다. 그게 6 Pro가 지적한 빈 그래프의 원인이었다
        # (2026-09-17).
        needs_reindex = force_reindex or (
            last_updated == 0 or now - last_updated >= reindex_interval_seconds
        )
        # 재색인을 백그라운드로 돌리면 이 응답은 저장된 그래프를 담는다.
        # 그 사실을 호출자에게 알려 화면이 "오래된 그림"이라고 말할 수 있게 한다.
        result_meta: dict[str, Any] = {
            "indexed_count": indexed_count,
            "indexed_at": last_updated,
            "needs_reindex": needs_reindex,
        }

        if needs_reindex and state_root is not None:
            # 재색인은 1.4GB 컨텍스트 DB를 훑는다. 예전에는 이 요청 안에서
            # 동기로 돌려, 캐시가 만료된 첫 조회가 25초 타임아웃에 걸려 빈
            # 그래프로 떨어졌다. 지금은 저장된 그래프를 즉시 돌려주고,
            # 재색인은 백그라운드에서 돌린 뒤 다음 폴링이 새 그림을 읽는다
            # (2026-09-17, 6 Pro 지적).
            if wait_for_reindex:
                # 동기 경로: 재색인이 끝난 뒤의 그래프를 이 응답에 담는다.
                _reindex_all(conn, state_root, chat=chat, cycle_started_at=now)
                result_meta["reindex"] = {"started": True, "chat": chat, "mode": "inline"}
                result_meta["stale"] = False
            else:
                started = _start_background_reindex(
                    conn,
                    state_root,
                    chat=chat,
                    cycle_started_at=now,
                    mode=reindex_mode,
                )
                result_meta["reindex"] = started
                result_meta["stale"] = True
        nodes: list[dict[str, Any]] = []
        for row in conn.execute(
            """
            SELECT entity_id, name, category, description, key_facts_json,
                   importance, evidence_json, updated_at
            FROM kg_entities
            ORDER BY importance DESC, entity_id ASC
            """
        ):
            entity_id, name, category, description, facts_json, importance, evidence_json, updated_at = row
            if not _is_graph_entity_id(entity_id):
                continue
            try:
                facts = json.loads(facts_json)
            except (TypeError, ValueError):
                facts = []
            evidence = _normalize_evidence(
                json.loads(evidence_json) if evidence_json else None
            )
            nodes.append(
                {
                    "id": entity_id,
                    "label": name,
                    "category": category,
                    "description": description,
                    "facts": facts if isinstance(facts, list) else [],
                    "importance": int(importance or 0),
                    "evidence": evidence,
                    "updated_at": int(updated_at or 0),
                }
            )
        node_ids = {node["id"] for node in nodes}
        edges: list[dict[str, Any]] = []
        for row in conn.execute(
            """
            SELECT source_id, relation, target_id, context, weight, evidence_json
            FROM kg_relations
            ORDER BY weight DESC, id ASC
            """
        ):
            source_id, relation, target_id, context, weight, evidence_json = row
            if source_id not in node_ids or target_id not in node_ids:
                continue
            edges.append(
                {
                    "source": source_id,
                    "relation": relation,
                    "target": target_id,
                    "context": context,
                    "weight": int(weight or 0),
                    "evidence": _normalize_evidence(
                        json.loads(evidence_json) if evidence_json else None
                    ),
                }
            )
        focus = None
        if str(focus_node_id or "").strip():
            focus = _k_hop_neighborhood_conn(conn, focus_node_id, focus_k, focus_limit)
            keep = set(focus["node_ids"])
            nodes = [node for node in nodes if node["id"] in keep]
            edges = [
                edge
                for edge in edges
                if edge["source"] in keep and edge["target"] in keep
            ]
        return {
            "ok": True,
            "nodes": nodes,
            "edges": edges,
            "reindex": result_meta.get("reindex"),
            "stale": bool(result_meta.get("stale")),
            "indexed_count": int(result_meta.get("indexed_count") or 0),
            "indexed_at": int(result_meta.get("indexed_at") or 0),
            "focus": focus,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "grounded_nodes": sum(
                1 for node in nodes if node["evidence"]["kind"] == PROVENANCE_LEDGER
            ),
        }
    finally:
        conn.close()


def collect_knowledge_graph_list(
    db_path: Path,
    *,
    query: str = "",
    chat: str = "",
    limit: int = 50,
    offset: int = 0,
    topic: str = "",
) -> dict[str, Any]:
    kg_path = db_path.parent / KNOWLEDGE_GRAPH_DB_NAME
    conn = _connect_kg(kg_path)
    try:
        ensure_seeded(conn)
        cursor = conn.cursor()
        
        # Query entities
        q_clean = query.strip().casefold()
        where_clauses = ["1=1"]
        params: list[Any] = []
        if q_clean:
            where_clauses.append("(name LIKE ? OR description LIKE ? OR aliases_json LIKE ?)")
            p = f"%{q_clean}%"
            params.extend([p, p, p])
        if topic and topic != "모든 주제":
            where_clauses.append("category = ?")
            params.append(topic)

        where_sql = " AND ".join(where_clauses)
        cursor.execute(f"SELECT COUNT(*) FROM kg_entities WHERE {where_sql}", params)
        total = int(cursor.fetchone()[0])

        cursor.execute(
            f"""
            SELECT entity_id, name, category, aliases_json, description, key_facts_json, importance, updated_at, evidence_json
            FROM kg_entities
            WHERE {where_sql}
            ORDER BY importance DESC, entity_id ASC
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        )
        rows = cursor.fetchall()

        # Load relations mapping
        cursor.execute("""
            SELECT source_id, relation, target_id, context FROM kg_relations
        """)
        relations_raw = cursor.fetchall()
        rel_map: dict[str, list[str]] = {}
        for s, r, t, c in relations_raw:
            rel_map.setdefault(s, []).append(f"[{r}] → {t} ({c})")
            rel_map.setdefault(t, []).append(f"← [{r}] {s} ({c})")

        # Extract topics
        cursor.execute("SELECT DISTINCT category, COUNT(*) FROM kg_entities GROUP BY category")
        topics = [{"key": row[0], "label": row[0], "count": row[1]} for row in cursor.fetchall()]

        items: list[dict[str, Any]] = []
        for row in rows:
            eid, name, cat, aliases_str, desc, facts_str, imp, up_at = row[:8]
            raw_evidence = row[8] if len(row) > 8 else None
            try:
                evidence = _normalize_evidence(
                    json.loads(raw_evidence) if raw_evidence else None
                )
            except (TypeError, ValueError):
                evidence = _seed_evidence()
            aliases = json.loads(aliases_str)
            facts = json.loads(facts_str)
            connected = rel_map.get(eid, [])
            
            body_parts = [
                f"【{name}】 ({cat})",
                f"설명: {desc}",
                f"주요 사실/맥락:",
            ]
            for fact in facts:
                body_parts.append(f"  • {fact}")
            if connected:
                body_parts.append(f"연결 관계 ({len(connected)}개):")
                for rel in connected:
                    body_parts.append(f"  ↔ {rel}")
            # Say where the node came from, so a reader can check it instead of
            # trusting the description (2026-09-16).
            if evidence["kind"] == PROVENANCE_LEDGER:
                body_parts.append(
                    "근거: 원문 메시지 "
                    + ", ".join(evidence["source_event_ids"][:3])
                    + f" (방 {evidence['chat_id']}"
                    + (f", {evidence['confirmed_at']}" if evidence["confirmed_at"] else "")
                    + ")"
                )
            else:
                body_parts.append("근거: 아직 원문 메시지에서 확인되지 않은 초기 노드")
            if evidence["retracted"]:
                body_parts.append("상태: 철회됨")

            formatted_message = "\n".join(body_parts)
            date_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(up_at))

            items.append({
                "id": stable_node_id(eid),
                "source": GRAPH_SOURCE_KIND,
                "origin_label": f"지식 노드 ({cat})",
                "chat": "전체 지식망",
                "date": date_str,
                "user_name": name,
                "message": formatted_message,
                "preview": f"[{cat}] {name} — {desc[:120]}",
                "editable": False,
                "vector_dim": 128,
                "vector_preview": f"graph-hash:{eid}",
                "kind": GRAPH_SOURCE_KIND,
                "topics": [cat],
                "topics_label": cat,
                "row_key": eid,
                "decision": "knowledge_node",
                "decision_label": "노드",
                "category": "knowledge_graph",
                "category_label": "지식 그래프",
                "status": "active",
                "status_label": (
                    f"중요도 {imp} · 근거 {len(evidence['source_event_ids'])}건"
                    if evidence["kind"] == PROVENANCE_LEDGER
                    else f"중요도 {imp} · 초기 노드"
                ),
                "reply": "",
                "reason_label": f"관계 {len(connected)}개",
                "deletable": False,
                "evidence": evidence,
            })

        # 목록 봉투는 다른 보기(messages·style·topics…)와 같은 모양이어야 한다.
        # 창은 ok·rows·count·truncated를 읽는다. 예전에는 items·has_more로
        # 돌려주어 디코딩이 통째로 실패했고, 지식 그래프를 고르면 69개 뉴런이
        # 있는데도 "기록 없음"만 떴다 (2026-09-16).
        return {
            "ok": True,
            "action": "vector-list",
            "privacy": "redacted",
            "source": GRAPH_SOURCE_KIND,
            "query": query,
            "chat": chat,
            "count": len(items),
            "total": total,
            "offset": max(int(offset), 0),
            "limit": max(int(limit), 1),
            "truncated": offset + len(items) < total,
            "rows": items,
            "topic": topic,
            "topics": topics,
        }
    finally:
        conn.close()


# 동의어 및 축약어 정규화 사전 (6 Pro 지적 및 GraphRAG 결합)
SYNONYM_DICTIONARY = {
    "알쫀쿠": ["알리바바 클라우드 쿠폰", "알리바바 클라우드 구독", "알리바바 클라우드", "alizonku"],
    "알리바바쿠폰": ["알쫀쿠", "알리바바 클라우드 구독"],
    "지피티": ["chatgpt", "gpt"],
    "챗지피티": ["chatgpt", "gpt"],
    "클로드": ["claude"],
    "큐웬": ["qwen", "qwen3.8"],
    "딥시크": ["deepseek", "deepseek v4"],
    "컴유": ["컴퓨터 유즈", "computer use"],
    "국장": ["주식", "국내주식"],
    "미장": ["주식", "미국주식"],
    "가상화폐": ["코인", "비트코인"],
    # 별칭이 영어 정식 이름이고 질문은 줄임말일 때도 만나야 한다. 사전이
    # 한글 키만 있으면 "Anthropic Claude" 같은 별칭은 "클로드"를 못 찾았다
    # (2026-09-19).
    "claude": ["클로드", "앤트로픽 클로드", "anthropic claude"],
    "chatgpt": ["지피티", "챗지피티", "gpt", "openai"],
    "qwen": ["큐웬", "큐웬3.8", "qwen3.8"],
    "deepseek": ["딥시크", "deepseek v4"],
    "computer use": ["컴퓨터 유즈", "컴유"],
}

CONTEXT_GATED_SYNONYM_KEYS = frozenset({"알쫀쿠", "알리바바쿠폰"})
ALIZONKU_CONTEXT_TERMS = (
    "알리바바",
    "클라우드",
    "cloud",
    "쿠폰",
    "구독",
    "서버",
    "인프라",
)

KOREAN_PARTICLES_PATTERN = (
    r"(?:$|[\s.,!?~/_\-()\[\]]|은|는|이|가|을|를|도|에|과|의|와|으로|로"
    r"|등|만|밖|부터|까지|처럼|보다|께)"
)
KOREAN_PREFIX_PATTERN = r"(?:^|[\s.,!?~/_\-()\[\]])"

# 오타·붙여쓰기 표준형. 사전은 닫아 둔다. 규칙을 넓히면 멀쩡한 말을 건드릴
# 수 있어서 실제로 관측된 것만 넣는다.
TYPO_DICTIONARY: dict[str, str] = {
    "러닝박에": "러닝밖에",
    "채팅내용": "채팅 내용",
}

# 시간대 표현은 같은 시각을 여러 표기로 말한다. 표준형으로 모아 두면 그래프의
# 시간 노드와 같은 말로 만난다.
TIME_EXPRESSION_RULES: tuple[tuple[str, str], ...] = (
    (r"(오늘\s*아침|아침에)", "아침(8시)"),
    (r"(점심\s*때|점심에)", "점심(12시)"),
    (r"(저녁\s*때|저녁에|밤에)", "저녁(20시)"),
)


def _message_datetime_kst(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).astimezone(KST)
        except (OverflowError, OSError, ValueError):
            return None
    raw = str(value).strip()
    if not raw:
        return None
    iso = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def _resolve_relative_time_kst(text: str, message_timestamp: Any) -> str:
    """Resolve relative Korean dates against the original message clock."""
    anchor = _message_datetime_kst(message_timestamp)
    if anchor is None:
        return text
    day_offsets = {"그저께": -2, "어제": -1, "오늘": 0, "내일": 1, "모레": 2}
    resolved = text
    for token, offset in day_offsets.items():
        if token not in resolved:
            continue
        target = datetime.fromtimestamp(anchor.timestamp() + offset * 86400, tz=KST)
        resolved = resolved.replace(token, target.strftime("%Y-%m-%d"))
    period_hours = {"아침": "08:00", "점심": "12:00", "저녁": "20:00", "밤": "22:00"}
    for token, clock in period_hours.items():
        if token in resolved:
            resolved = re.sub(
                rf"{re.escape(token)}(?:\s*때|에)?",
                f"{token}({clock} KST)",
                resolved,
            )
    return resolved


def normalize_text_query(text: str, *, message_timestamp: Any = None) -> str:
    """오타·붙여쓰기·시간대 표현을 표준형으로 바꾼다.

    답변 생성은 이 함수를 거친 질의로 그래프를 찾는다. 사용자가 "러닝박에"나
    "오늘 아침"처럼 써도 그래프의 "러닝"·"아침(8시)" 노드와 같은 말로 만나게
    하는 것이 목적이다. 여러 번 불러도 같은 결과가 나온다(2026-09-19).

    예전 본문의 시간 규칙에는 단어 경계를 뜻하는 역슬래시-b 대신 진짜
    백스페이스 문자가 들어가 있었다. 그 규칙은 아무것도 바꾸지 못했고,
    이 함수는 아무도 부르지 않아 통째로 죽어 있었다.
    """

    normalized = " ".join(str(text or "").split())
    if not normalized:
        return ""
    for wrong, right in TYPO_DICTIONARY.items():
        normalized = normalized.replace(wrong, right)
    if message_timestamp is not None:
        normalized = _resolve_relative_time_kst(normalized, message_timestamp)
    else:
        for pattern, replacement in TIME_EXPRESSION_RULES:
            normalized = re.sub(pattern, replacement, normalized)
    return normalized


def query_haystacks(
    query_text: str,
    also: "list[str] | tuple[str, ...] | None" = None,
    *,
    message_timestamp: Any = None,
) -> list[str]:
    """질의와 곁말을 원문과 표준형으로 함께 모은다.

    원문을 지우지 않는다. 오타 사전이 모르는 표기를 표준형이 지워 버릴 수
    있어서 둘 다 바늘더미에 남긴다. 원문이 먼저다(2026-09-19).
    """

    haystacks: list[str] = []
    for raw in (query_text, *(also or ())):
        for variant in (
            str(raw or ""),
            normalize_text_query(raw, message_timestamp=message_timestamp),
        ):
            folded = variant.casefold().strip()
            if folded and folded not in haystacks:
                haystacks.append(folded)
    return haystacks


def _alizonku_context_confirmed(haystacks: list[str] | tuple[str, ...]) -> bool:
    combined = " ".join(str(value or "").casefold() for value in haystacks)
    return any(term.casefold() in combined for term in ALIZONKU_CONTEXT_TERMS)


def _alias_matches(
    alias: str,
    haystack: str,
    *,
    context_haystacks: "list[str] | tuple[str, ...] | None" = None,
) -> bool:
    folded = alias.casefold().strip()
    if not folded:
        return False
    haystack_folded = haystack.casefold()
    expanded_terms = [folded]
    context_values = list(context_haystacks or (haystack,))
    for syn_key, syn_vals in SYNONYM_DICTIONARY.items():
        if folded == syn_key.casefold() or folded in [v.casefold() for v in syn_vals]:
            if (
                syn_key in CONTEXT_GATED_SYNONYM_KEYS
                and not _alizonku_context_confirmed(context_values)
            ):
                continue
            expanded_terms.append(syn_key.casefold())
            expanded_terms.extend([v.casefold() for v in syn_vals])
    for term in set(expanded_terms):
        if len(term) <= 2:
            if re.search(
                KOREAN_PREFIX_PATTERN + re.escape(term) + KOREAN_PARTICLES_PATTERN,
                haystack_folded,
            ) is not None:
                return True
        elif term in haystack_folded:
            return True
    return False


KNOWLEDGE_EMBEDDING_DIM = 128
VECTOR_CANDIDATE_MIN = 0.0


def _deterministic_text_embedding(
    text: str,
    *,
    dim: int = KNOWLEDGE_EMBEDDING_DIM,
) -> tuple[float, ...]:
    """외부 모델 없이 재현 가능한 문자/단어 해시 임베딩을 만든다."""
    if dim <= 0:
        raise ValueError("embedding dimension must be positive")
    normalized = normalize_text_query(text).casefold()
    tokens = re.findall(r"[0-9a-z가-힣]+", normalized)
    vector = [0.0] * dim
    for token in tokens:
        features = [f"w:{token}"]
        padded = f"^{token}$"
        for width in (2, 3):
            if len(padded) >= width:
                features.extend(
                    f"c{width}:{padded[index:index + width]}"
                    for index in range(len(padded) - width + 1)
                )
        for feature in features:
            digest = hashlib.blake2b(
                feature.encode("utf-8"),
                digest_size=8,
                person=b"kg-hybrid-v1",
            ).digest()
            vector[int.from_bytes(digest, "big") % dim] += 1.0
    norm = sum(value * value for value in vector) ** 0.5
    if not norm:
        return tuple(vector)
    return tuple(value / norm for value in vector)


def _vector_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    return sum(a * b for a, b in zip(left, right))


def _keyword_match_score(terms: list[str], haystacks: list[str]) -> float:
    score = 0.0
    seen: set[tuple[str, str]] = set()
    for haystack in haystacks:
        haystack_folded = haystack.casefold().strip()
        for term in terms:
            term_folded = term.casefold().strip()
            pair = (term_folded, haystack_folded)
            if not term_folded or pair in seen:
                continue
            seen.add(pair)
            if not _alias_matches(term, haystack, context_haystacks=haystacks):
                continue
            score += 1.0
            if term_folded == haystack_folded:
                score += 2.0
    return score


def _normalize_dense_vector(values: Any) -> tuple[float, ...]:
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError("dense embedding is empty")
    vector = tuple(float(value) for value in values)
    norm = math.sqrt(sum(value * value for value in vector))
    if not math.isfinite(norm) or norm <= 0.0:
        raise ValueError("dense embedding has zero norm")
    return tuple(value / norm for value in vector)


def _local_dense_embeddings(texts: list[str]) -> list[tuple[float, ...]]:
    """Embed text through a loopback-only multilingual embedding endpoint.

    The product path is local-only. A non-loopback URL is rejected before any
    request is made so a misconfigured environment cannot silently turn dense
    retrieval into a cloud dependency.
    """
    if not texts:
        return []
    parsed = urllib.parse.urlparse(DENSE_EMBEDDING_URL)
    host = (parsed.hostname or "").casefold()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("dense embedding endpoint must be loopback-local")
    payload = json.dumps(
        {"model": DENSE_EMBEDDING_MODEL, "input": [str(text) for text in texts]},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        DENSE_EMBEDDING_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=DENSE_EMBEDDING_TIMEOUT_SECONDS) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("local dense embedding unavailable") from error
    rows = body.get("data") if isinstance(body, dict) else None
    if not isinstance(rows, list) or len(rows) != len(texts):
        raise RuntimeError("local dense embedding response mismatch")
    ordered = sorted(
        rows,
        key=lambda row: int(row.get("index", 0)) if isinstance(row, dict) else 0,
    )
    vectors: list[tuple[float, ...]] = []
    for row in ordered:
        if not isinstance(row, dict):
            raise RuntimeError("local dense embedding row invalid")
        vectors.append(_normalize_dense_vector(row.get("embedding")))
    return vectors


def _ann_band_keys(vector: tuple[float, ...]) -> list[tuple[int, str]]:
    """Return deterministic random-hyperplane LSH buckets for one vector."""
    keys: list[tuple[int, str]] = []
    bits_total = ANN_BANDS * ANN_BITS_PER_BAND
    bits: list[int] = []
    for bit_index in range(bits_total):
        projection = 0.0
        # The sign matrix is generated deterministically from coordinates, so
        # the ANN index needs no external random-state file.
        for dim_index, value in enumerate(vector):
            digest = hashlib.blake2b(
                f"{bit_index}:{dim_index}".encode("ascii"),
                digest_size=1,
                person=b"kg-ann-v1",
            ).digest()[0]
            projection += value if digest & 1 else -value
        bits.append(1 if projection >= 0.0 else 0)
    for band in range(ANN_BANDS):
        start = band * ANN_BITS_PER_BAND
        bucket = 0
        for offset, bit in enumerate(bits[start : start + ANN_BITS_PER_BAND]):
            bucket |= bit << offset
        keys.append((band, f"{bucket:02x}"))
    return keys


def _connect_dense_index(state_root: Path) -> sqlite3.Connection:
    path = state_root / DENSE_INDEX_DB_NAME
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS dense_vectors ("
        " entity_id TEXT PRIMARY KEY, vector_json TEXT NOT NULL, dim INTEGER NOT NULL,"
        " model TEXT NOT NULL, index_version TEXT NOT NULL, watermark TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ann_buckets ("
        " band INTEGER NOT NULL, bucket TEXT NOT NULL, entity_id TEXT NOT NULL,"
        " PRIMARY KEY (band, bucket, entity_id))"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ann_bucket_lookup ON ann_buckets(band, bucket)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS dense_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    conn.commit()
    return conn


def _entity_dense_text(row: tuple[Any, ...]) -> str:
    _entity_id, name, _category, aliases_json, description, facts_json = row[:6]
    try:
        aliases = json.loads(aliases_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        aliases = []
    try:
        facts = json.loads(facts_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        facts = []
    return " ".join(
        [str(name or ""), str(description or "")]
        + [str(value) for value in aliases if str(value).strip()]
        + [str(value) for value in facts[:5] if str(value).strip()]
    ).strip()


def refresh_dense_index(
    kg_conn: sqlite3.Connection,
    state_root: Path,
    *,
    batch_size: int = 32,
) -> dict[str, Any]:
    """Persist local multilingual embeddings and an LSH ANN index."""
    rows = list(
        kg_conn.execute(
            "SELECT entity_id, name, category, aliases_json, description, key_facts_json"
            " FROM kg_entities ORDER BY entity_id"
        )
    )
    watermark = read_meta(kg_conn, "last_indexed_at") or str(
        max((int(row[0] or 0) for row in kg_conn.execute("SELECT MAX(updated_at) FROM kg_entities")), default=0)
    )
    if not rows:
        return {"status": "empty", "indexed": 0, "watermark": watermark}
    dense = _connect_dense_index(state_root)
    indexed = 0
    try:
        dense.execute("BEGIN")
        dense.execute("DELETE FROM ann_buckets")
        dense.execute("DELETE FROM dense_vectors")
        for start in range(0, len(rows), max(1, int(batch_size))):
            batch = rows[start : start + max(1, int(batch_size))]
            vectors = _local_dense_embeddings([_entity_dense_text(row) for row in batch])
            for row, vector in zip(batch, vectors):
                entity_id = str(row[0])
                dense.execute(
                    "INSERT INTO dense_vectors"
                    " (entity_id, vector_json, dim, model, index_version, watermark)"
                    " VALUES (?,?,?,?,?,?)",
                    (
                        entity_id,
                        json.dumps(vector, separators=(",", ":")),
                        len(vector),
                        DENSE_EMBEDDING_MODEL,
                        DENSE_INDEX_VERSION,
                        watermark,
                    ),
                )
                dense.executemany(
                    "INSERT INTO ann_buckets (band, bucket, entity_id) VALUES (?,?,?)",
                    [(band, bucket, entity_id) for band, bucket in _ann_band_keys(vector)],
                )
                indexed += 1
        dense.execute(
            "INSERT INTO dense_meta(key,value) VALUES('index_version',?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (DENSE_INDEX_VERSION,),
        )
        dense.execute(
            "INSERT INTO dense_meta(key,value) VALUES('watermark',?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (watermark,),
        )
        dense.commit()
    except Exception:
        dense.rollback()
        raise
    finally:
        dense.close()
    return {"status": "indexed", "indexed": indexed, "watermark": watermark}


def _dense_ann_query(
    state_root: Path,
    query_text: str,
    *,
    limit: int = 40,
) -> tuple[list[tuple[str, float]], str]:
    """Return an independently ranked dense candidate list from the ANN store."""
    path = state_root / DENSE_INDEX_DB_NAME
    if not path.is_file():
        raise RuntimeError("dense index unavailable")
    query_vector = _local_dense_embeddings([query_text])[0]
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only = ON")
    try:
        version_row = conn.execute(
            "SELECT value FROM dense_meta WHERE key='index_version'"
        ).fetchone()
        if not version_row or str(version_row[0]) != DENSE_INDEX_VERSION:
            raise RuntimeError("dense index version mismatch")
        watermark_row = conn.execute(
            "SELECT value FROM dense_meta WHERE key='watermark'"
        ).fetchone()
        watermark = str(watermark_row[0]) if watermark_row else ""
        clauses: list[str] = []
        params: list[Any] = []
        for band, bucket in _ann_band_keys(query_vector):
            clauses.append("(band=? AND bucket=?)")
            params.extend([band, bucket])
        candidate_rows = conn.execute(
            "SELECT entity_id, COUNT(*) AS hits FROM ann_buckets WHERE "
            + " OR ".join(clauses)
            + " GROUP BY entity_id ORDER BY hits DESC, entity_id LIMIT ?",
            params + [max(int(limit) * 4, int(limit), 1)],
        ).fetchall()
        candidate_ids = [str(row[0]) for row in candidate_rows]
        if not candidate_ids:
            return [], watermark
        marks = ",".join("?" for _ in candidate_ids)
        vectors = {
            str(entity_id): _normalize_dense_vector(json.loads(vector_json))
            for entity_id, vector_json in conn.execute(
                f"SELECT entity_id, vector_json FROM dense_vectors WHERE entity_id IN ({marks})",
                candidate_ids,
            )
        }
        ranked = [
            (entity_id, _vector_similarity(query_vector, vector))
            for entity_id, vector in vectors.items()
        ]
        ranked.sort(key=lambda item: (-item[1], item[0]))
        return ranked[: max(int(limit), 1)], watermark
    finally:
        conn.close()


def _fts_query_terms(haystacks: list[str]) -> str:
    terms: list[str] = []
    seen: set[str] = set()
    particles = ("으로", "부터", "까지", "처럼", "보다", "은", "는", "이", "가", "을", "를", "도", "에", "과", "와", "의", "로", "만")
    for haystack in haystacks:
        for raw in re.findall(r"[0-9a-z가-힣]+", haystack.casefold()):
            candidates = [raw]
            for particle in particles:
                if len(raw) > len(particle) + 1 and raw.endswith(particle):
                    candidates.append(raw[: -len(particle)])
                    break
            for term in candidates:
                if len(term) < 2 or term in seen:
                    continue
                seen.add(term)
                terms.append('"' + term.replace('"', '""') + '"')
    return " OR ".join(terms[:24])


def _bm25_candidates(
    conn: sqlite3.Connection,
    haystacks: list[str],
    *,
    limit: int = 40,
) -> list[tuple[str, float]]:
    query = _fts_query_terms(haystacks)
    if not query:
        return []
    try:
        rows = conn.execute(
            "SELECT entity_id, bm25(kg_entities_fts) AS score"
            " FROM kg_entities_fts WHERE kg_entities_fts MATCH ?"
            " ORDER BY score ASC, entity_id ASC LIMIT ?",
            (query, max(int(limit), 1)),
        ).fetchall()
    except sqlite3.Error:
        return []
    return [(str(entity_id), float(score)) for entity_id, score in rows]


def _rrf_merge(
    bm25: list[tuple[str, float]],
    dense: list[tuple[str, float]],
    *,
    k: int = RRF_K,
) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranked in (bm25, dense):
        for rank, (entity_id, _raw_score) in enumerate(ranked, start=1):
            scores[entity_id] = scores.get(entity_id, 0.0) + 1.0 / (max(int(k), 1) + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


SEARCH_MODE_RRF = "rrf"
SEARCH_MODE_BM25_ONLY = "bm25_only"


def _time_bucket_id(value: Any) -> str:
    """Bucket a timestamp to a daily KST node id. Never a per-message node."""
    stamp = _message_datetime_kst(value)
    if stamp is None:
        return ""
    return f"time:{stamp.strftime('%Y-%m-%d')}"


def _format_entity_fact(name: Any, category: Any, description: Any, facts: Any) -> str:
    fact_list = facts if isinstance(facts, list) else []
    snippet = "; ".join(str(item) for item in fact_list[:3] if str(item).strip())
    facts_snippet = f" (핵심 맥락: {snippet})" if snippet else ""
    return f"[{category}] {name}: {description}{facts_snippet}"


def _evidence_ids_from_json(raw: Any) -> list[str]:
    payload: Any = raw
    if isinstance(raw, str) and raw.strip():
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    if not isinstance(payload, dict):
        return []
    ids = payload.get("source_event_ids")
    if not isinstance(ids, list):
        return []
    return [str(item) for item in ids if str(item).strip()][:MAX_EVIDENCE_PER_NODE]


def _participant_in_scope(entity_id: str, participant_id: Any) -> bool:
    wanted = str(participant_id or "").strip()
    if not wanted:
        return True
    if entity_id.startswith("person:"):
        parts = entity_id.split(":")
        name = parts[-1] if parts else ""
        return wanted in {entity_id, name, ":".join(parts[2:])}
    if entity_id.startswith("author:"):
        return entity_id == f"author:{wanted}" or entity_id.endswith(f":{wanted}")
    return True


def _relation_time_in_scope(
    valid_from: Any,
    valid_to: Any,
    time_from: Any,
    time_to: Any,
) -> bool:
    if time_from is None and time_to is None:
        return True
    start = _message_datetime_kst(valid_from)
    end = _message_datetime_kst(valid_to)
    lower = _message_datetime_kst(time_from)
    upper = _message_datetime_kst(time_to)
    if start is None and end is None:
        return True
    if lower is not None and end is not None and end < lower:
        return False
    if upper is not None and start is not None and start > upper:
        return False
    return True


def _keyword_ranked_candidates(
    conn: sqlite3.Connection,
    haystacks: list[str],
    in_scope: Any,
    *,
    limit: int = 40,
) -> list[tuple[str, float]]:
    ranked: list[tuple[str, float]] = []
    for ent_id, name, aliases_str in conn.execute(
        "SELECT entity_id, name, aliases_json FROM kg_entities"
    ):
        entity_id = str(ent_id)
        if not in_scope(entity_id):
            continue
        try:
            aliases = json.loads(aliases_str)
        except (TypeError, ValueError, json.JSONDecodeError):
            aliases = []
        if not isinstance(aliases, list):
            aliases = []
        terms = [str(alias) for alias in aliases] + [str(name)]
        score = _keyword_match_score(terms, haystacks)
        if score <= 0:
            continue
        ranked.append((entity_id, score))
    ranked.sort(key=lambda item: (-item[1], item[0]))
    return ranked[: max(int(limit), 1)]


def _upsert_relation(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    relation: str,
    target_id: str,
    context: str,
    weight: int,
    updated_at: int,
    room_id: str = "",
    valid_from: str = "",
    valid_to: str = "",
    evidence_message_id: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO kg_relations (
            source_id, relation, target_id,
            subject_id, relation_type, object_id,
            room_id, valid_from, valid_to, evidence_message_id,
            context, weight, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_id, relation, target_id) DO UPDATE SET
            subject_id=excluded.subject_id,
            relation_type=excluded.relation_type,
            object_id=excluded.object_id,
            room_id=CASE WHEN excluded.room_id != '' THEN excluded.room_id ELSE kg_relations.room_id END,
            valid_from=CASE WHEN excluded.valid_from != '' THEN excluded.valid_from ELSE kg_relations.valid_from END,
            valid_to=excluded.valid_to,
            evidence_message_id=CASE WHEN excluded.evidence_message_id != '' THEN excluded.evidence_message_id ELSE kg_relations.evidence_message_id END,
            context=excluded.context,
            weight=excluded.weight,
            updated_at=excluded.updated_at
        """,
        (
            source_id,
            relation,
            target_id,
            source_id,
            relation,
            target_id,
            room_id,
            valid_from,
            valid_to,
            evidence_message_id,
            context,
            int(weight),
            int(updated_at),
        ),
    )


def _focused_relation_facts(
    focus: dict[str, Any],
    *,
    state_root: Path,
    chat_id: "int | str | None",
    limit: int,
) -> list[str]:
    """Render relation facts only from one bounded focus subgraph."""
    node_ids = [str(value) for value in focus.get("node_ids", []) if _is_graph_entity_id(value)]
    if not node_ids or limit <= 0:
        return []
    kg_path = state_root / KNOWLEDGE_GRAPH_DB_NAME
    if not kg_path.exists():
        return []

    chat_str = str(chat_id or "").strip()
    allowed_rooms: set[str] = set()
    if chat_str:
        for variant in (chat_str, _room_key(state_root, chat_str), _chat_label(chat_str)):
            if variant:
                allowed_rooms.add(str(variant))
        allowed_rooms = {_room_key(state_root, room) or room for room in allowed_rooms}

    def in_scope(entity_id: str) -> bool:
        if not allowed_rooms:
            return True
        if not (entity_id.startswith("chat:") or entity_id.startswith("person:")):
            return True
        parts = entity_id.split(":")
        room = parts[1] if len(parts) > 1 else ""
        return (_room_key(state_root, room) or room) in allowed_rooms

    conn = None
    try:
        conn = sqlite3.connect(f"file:{kg_path}?mode=ro", uri=True)
        conn.execute("PRAGMA query_only = ON")
        marks = ",".join("?" for _ in node_ids)
        names = {
            str(entity_id): str(name)
            for entity_id, name in conn.execute(
                f"SELECT entity_id, name FROM kg_entities WHERE entity_id IN ({marks})",
                node_ids,
            )
            if in_scope(str(entity_id))
        }
        rows = conn.execute(
            f"SELECT source_id, relation, target_id, context, weight FROM kg_relations"
            f" WHERE source_id IN ({marks}) AND target_id IN ({marks})"
            f" ORDER BY weight DESC, id ASC",
            node_ids + node_ids,
        ).fetchall()
        facts: list[str] = []
        for source, relation, target, context, _weight in rows:
            source = str(source)
            target = str(target)
            if source not in names or target not in names:
                continue
            facts.append(
                f"[관계] {names[source]} —({relation})→ {names[target]}: {str(context or '')}"
            )
            if len(facts) >= limit:
                break
        return facts
    except (OSError, sqlite3.Error, ValueError) as error:
        _trace_graph("focus_relation_failed", error=type(error).__name__)
        return []
    finally:
        if conn is not None:
            conn.close()


def retrieve_knowledge_bundle(
    query_text: str,
    state_root: Path | None = None,
    *,
    chat_id: "int | str | None" = None,
    also: "list[str] | tuple[str, ...] | None" = None,
    max_entities: int = 3,
    max_relations: int = 3,
) -> dict[str, Any]:
    """GraphRAG + BM25/Dense RRF 검색 번들 반환.

    엔티티와 관계(트리플)가 한쪽에 편중되지 않도록 균형 예산을 적용하며,
    방 ID와 방 이름을 상호 확인하여 방 간 데이터 격리를 보장한다 (2026-09-17).
    Dense 실패 시 모드는 bm25_only이며 hybrid/rrf로 표시하지 않는다.
    """
    ranked = _query_knowledge_ranked(
        query_text,
        state_root=state_root,
        chat_id=chat_id,
        also=also,
    )
    entity_facts = list(ranked.get("entity_facts") or [])
    relation_facts = list(ranked.get("relation_facts") or [])
    candidates = list(ranked.get("candidates") or [])
    focus: dict[str, Any] | None = None
    if candidates:
        root = state_root or Path.home() / "Library/Application Support/openkakao/bujamentor"
        focus = k_hop_neighborhood(
            candidates[0],
            DEFAULT_K_HOP,
            K_HOP_NEIGHBOR_LIMIT,
            state_root=root,
            chat_id=chat_id,
        )
        focused_ids = set(focus.get("node_ids", []))
        if focused_ids:
            entity_facts = [
                fact
                for entity_id, fact in zip(candidates, entity_facts)
                if entity_id in focused_ids
            ]
            focused_relations = _focused_relation_facts(
                focus,
                state_root=root,
                chat_id=chat_id,
                limit=max_relations,
            )
            if focused_relations:
                relation_facts = focused_relations
    balanced_facts = entity_facts[:max_entities] + relation_facts[:max_relations]
    return {
        "query": query_text,
        "chat_id": str(chat_id or ""),
        "facts": balanced_facts,
        "fact_count": len(balanced_facts),
        "candidate_count": len(candidates),
        "entities_count": len(entity_facts),
        "relations_count": len(relation_facts),
        "focus_node_id": str((focus or {}).get("root_id") or ""),
        "focus_k": int((focus or {}).get("k") or 0),
        "focus_node_count": len((focus or {}).get("node_ids", [])),
        "focus_edge_count": len((focus or {}).get("edges", [])),
        "search_mode": str(ranked.get("search_mode") or SEARCH_MODE_BM25_ONLY),
        "index_version": str(ranked.get("index_version") or SEARCH_INDEX_VERSION),
        "watermark": str(ranked.get("watermark") or ""),
        "evidence_ids": list(ranked.get("evidence_ids") or []),
    }

def query_knowledge_context(
    query_text: str,
    state_root: Path | None = None,
    *,
    chat_id: "int | str | None" = None,
    also: "list[str] | tuple[str, ...] | None" = None,
    include_relations: bool = True,
) -> list[str]:
    entity_facts, relation_facts, _ = _query_knowledge_structured(
        query_text,
        state_root=state_root,
        chat_id=chat_id,
        also=also,
    )
    if include_relations:
        return entity_facts + relation_facts
    return entity_facts

def _query_knowledge_structured(
    query_text: str,
    state_root: Path | None = None,
    *,
    chat_id: "int | str | None" = None,
    also: "list[str] | tuple[str, ...] | None" = None,
) -> tuple[list[str], list[str], list[str]]:
    ranked = _query_knowledge_ranked(
        query_text,
        state_root=state_root,
        chat_id=chat_id,
        also=also,
    )
    return (
        list(ranked.get("entity_facts") or []),
        list(ranked.get("relation_facts") or []),
        list(ranked.get("candidates") or []),
    )


def _query_knowledge_ranked(
    query_text: str,
    state_root: Path | None = None,
    *,
    chat_id: "int | str | None" = None,
    also: "list[str] | tuple[str, ...] | None" = None,
    participant_id: "int | str | None" = None,
    time_from: Any = None,
    time_to: Any = None,
) -> dict[str, Any]:
    """Retrieve relevant Knowledge Graph context nodes for a turn.

    ``query_text`` is the incoming message. ``also`` adds more text the turn is
    really about, and it matters more than it looks: a room discussed Alibaba
    Cloud pricing and then sent a bare photo. Matching only the incoming
    "사진" retrieved nothing, so the answer that followed had no idea the
    conversation was about cloud subscriptions, even though the graph held
    that fact (2026-09-16). The caller passes the recent conversation here.

    Every additional source is matched at lower precedence than the incoming
    message, and the returned lines keep their category and name so the model
    can tell which concept is being invoked.
    """
    # 오타·붙여쓰기·시간 표현을 표준형으로 바꾼 바늘더미까지 함께 쓴다.
    # 예전에는 원문만 썼고, 정규화 함수는 아무도 부르지 않았다.
    haystacks = query_haystacks(query_text, also)
    empty = {
        "entity_facts": [],
        "relation_facts": [],
        "candidates": [],
        "search_mode": SEARCH_MODE_BM25_ONLY,
        "index_version": SEARCH_INDEX_VERSION,
        "watermark": "",
        "evidence_ids": [],
        "bm25_count": 0,
        "dense_count": 0,
    }
    if not haystacks:
        # 빈 질의로도 불린다. 여기서 리스트 하나를 돌려주면 호출자의 세 값
        # 언패킹이 깨져 조회 전체가 실패한다(2026-09-19).
        return empty
    root = state_root or Path.home() / "Library/Application Support/openkakao/bujamentor"
    kg_path = root / KNOWLEDGE_GRAPH_DB_NAME
    if not kg_path.exists():
        return empty
    conn = _connect_kg(kg_path)
    try:
        ensure_seeded(conn)
        chat_str = str(chat_id or "").strip()
        # 방 격리: 방 식별자를 하나의 정규 키로 모은 뒤, 노드 ID의 방 부분과
        # 정확히 비교한다.
        #
        # 예전에는 부분 문자열로 비교했다. 그러면 방 이름이 다른 방 이름의
        # 일부일 때 정상 노드가 걸러지고, 반대로 짧은 키는 남의 방 노드를
        # 통과시킨다. 허용 집합을 만든 뒤 완전 일치만 통과시킨다
        # (2026-09-17, 6 Pro 지적).
        allowed_rooms: set[str] = set()
        if chat_str:
            allowed_rooms.add(chat_str)
            norm_key = _room_key(root, chat_str)
            if norm_key:
                allowed_rooms.add(norm_key)
                allowed_rooms.add(_chat_label(norm_key))
        # 어떤 표기가 들어와도 같은 정규 키로 모은다.
        room_aliases: dict[str, str] = {}
        for room in list(allowed_rooms):
            if not room:
                continue
            canonical = _room_key(root, room) or room
            for variant in (room, canonical, _chat_label(canonical)):
                if variant:
                    room_aliases[str(variant)] = canonical
        allowed_canonical = set(room_aliases.values())

        def _room_of(entity_id: str) -> str:
            """노드 ID에서 방 식별자를 꺼낸다.

            person:<room>:<name> 과 chat:<room> 두 모양만 방 스코프를 갖는다.
            """
            if entity_id.startswith("chat:") or entity_id.startswith("person:"):
                parts = entity_id.split(":")
                return parts[1] if len(parts) > 1 else ""
            return ""

        def _in_scope(entity_id: str) -> bool:
            """이 방에서 써도 되는 노드인가.

            방 스코프가 없는 노드(주제, 기술, 시드)는 모든 방에서 쓸 수 있다.
            스코프가 있는 노드는 정규 키가 허용 집합에 있을 때만 통과한다.
            """
            if not allowed_canonical:
                return True
            room = _room_of(entity_id)
            if not room:
                return True
            canonical = room_aliases.get(room)
            if canonical is None:
                canonical = _room_key(root, room) or room
            return canonical in allowed_canonical

        def _keep_entity(entity_id: str) -> bool:
            return _in_scope(entity_id) and _participant_in_scope(entity_id, participant_id)

        bm25_ranked = [
            (entity_id, score)
            for entity_id, score in _bm25_candidates(conn, haystacks, limit=40)
            if _keep_entity(entity_id)
        ]
        if not bm25_ranked:
            bm25_ranked = _keyword_ranked_candidates(
                conn, haystacks, _keep_entity, limit=40
            )

        search_mode = SEARCH_MODE_BM25_ONLY
        dense_ranked: list[tuple[str, float]] = []
        dense_watermark = ""
        try:
            dense_hits, dense_watermark = _dense_ann_query(
                root, " ".join(haystacks), limit=40
            )
            dense_ranked = [
                (entity_id, score)
                for entity_id, score in dense_hits
                if _keep_entity(entity_id)
            ]
            search_mode = SEARCH_MODE_RRF
        except Exception as error:  # noqa: BLE001 - dense is optional, never cloud-fallback
            _trace_graph("dense_query_failed", error=type(error).__name__)
            search_mode = SEARCH_MODE_BM25_ONLY
            dense_ranked = []

        if search_mode == SEARCH_MODE_RRF:
            merged = _rrf_merge(bm25_ranked, dense_ranked)
        else:
            merged = bm25_ranked

        ordered_ids = [entity_id for entity_id, _score in merged]
        entity_facts: list[str] = []
        relation_facts: list[str] = []
        candidates: list[str] = []
        evidence_ids: list[str] = []
        matched_entity_ids: set[str] = set()
        matched_entity_names: dict[str, str] = {}
        if ordered_ids:
            marks = ",".join("?" for _ in ordered_ids)
            rows = {
                str(entity_id): (name, category, description, facts_json, evidence_json)
                for entity_id, name, category, description, facts_json, evidence_json in conn.execute(
                    f"SELECT entity_id, name, category, description, key_facts_json, evidence_json"
                    f" FROM kg_entities WHERE entity_id IN ({marks})",
                    ordered_ids,
                )
            }
            for entity_id in ordered_ids:
                row = rows.get(entity_id)
                if row is None:
                    continue
                name, category, description, facts_json, evidence_json = row
                try:
                    facts = json.loads(facts_json)
                except (TypeError, ValueError, json.JSONDecodeError):
                    facts = []
                if not isinstance(facts, list):
                    facts = []
                candidates.append(entity_id)
                matched_entity_ids.add(entity_id)
                matched_entity_names[entity_id] = str(name)
                entity_facts.append(
                    _format_entity_fact(name, category, description, facts)
                )
                evidence_ids.extend(_evidence_ids_from_json(evidence_json))

        if matched_entity_ids:
            marks = ",".join("?" * len(matched_entity_ids))
            rel_cursor = conn.execute(
                f"SELECT source_id, relation, target_id, context, weight,"
                f" COALESCE(room_id,''), COALESCE(valid_from,''), COALESCE(valid_to,''),"
                f" COALESCE(evidence_message_id,'')"
                f" FROM kg_relations"
                f" WHERE source_id IN ({marks}) OR target_id IN ({marks})"
                f" ORDER BY weight DESC LIMIT 12",
                list(matched_entity_ids) + list(matched_entity_ids),
            )
            for src, rel, tgt, ctx, _weight, room_id, valid_from, valid_to, evidence_message_id in rel_cursor.fetchall():
                if not _in_scope(src) or not _in_scope(tgt):
                    continue
                if not _participant_in_scope(src, participant_id) and not _participant_in_scope(
                    tgt, participant_id
                ):
                    if str(participant_id or "").strip():
                        continue
                if not _relation_time_in_scope(valid_from, valid_to, time_from, time_to):
                    continue
                if allowed_canonical and str(room_id or "").strip():
                    canonical = room_aliases.get(str(room_id)) or (
                        _room_key(root, str(room_id)) or str(room_id)
                    )
                    if canonical not in allowed_canonical:
                        continue
                src_name = matched_entity_names.get(src, src.split(":")[-1])
                tgt_name = matched_entity_names.get(tgt, tgt.split(":")[-1])
                relation_facts.append(f"[관계] {src_name} —({rel})→ {tgt_name}: {ctx}")
                if evidence_message_id:
                    evidence_ids.append(str(evidence_message_id))

        watermark = read_meta(conn, "last_indexed_at") or dense_watermark
        # Preserve order, drop duplicates.
        seen_evidence: set[str] = set()
        ordered_evidence: list[str] = []
        for item in evidence_ids:
            if item in seen_evidence:
                continue
            seen_evidence.add(item)
            ordered_evidence.append(item)
        return {
            "entity_facts": entity_facts,
            "relation_facts": relation_facts,
            "candidates": candidates,
            "search_mode": search_mode,
            "index_version": SEARCH_INDEX_VERSION,
            "watermark": str(watermark or ""),
            "evidence_ids": ordered_evidence,
            "bm25_count": len(bm25_ranked),
            "dense_count": len(dense_ranked),
        }
    except Exception as error:  # noqa: BLE001 - retrieval must not kill a turn
        _trace_graph("query_ranked_failed", error=type(error).__name__)
        return empty
    finally:
        conn.close()


if __name__ == "__main__":
    import sys as _sys

    raise SystemExit(_reindex_once_entry(_sys.argv[1:]))
