"""Deterministic Knowledge Graph for OpenKakao Bujamentor Auto-Reply.

Inspired by Andrej Karpathy's LLM Wiki & Algorithmic Knowledge Graph concepts:
- Entity nodes (People, Technologies, Concepts, Activities, Channels)
- Relation edges (DISCUSSED_IN, SUBSCRIBED_TO, OPINION_ON, ACTIVITY_OF)
- Grounded contextual facts with evidence identifiers and timestamps
- Deterministic, zero-hallucination graph queries for prompt context enrichment.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

KNOWLEDGE_GRAPH_DB_NAME = "knowledge-graph.sqlite3"
GRAPH_SOURCE_KIND = "knowledge_graph"

# Provenance kinds. "seed" is a hand-written starting node that no message
# backs yet; "ledger" means a real room message was found to mention the node.
# The 6 Pro review rejected nodes that carried no source message id, room id,
# confirmation time or retraction state, so every node and edge now reports
# which of the two it is and what backs it (2026-09-16).
PROVENANCE_SEED = "seed"
PROVENANCE_LEDGER = "ledger"
MAX_EVIDENCE_PER_NODE = 8


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
        CREATE TABLE IF NOT EXISTS kg_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            relation TEXT NOT NULL,
            target_id TEXT NOT NULL,
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
    conn.commit()
    return conn


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
        conn.execute(
            """
            INSERT INTO kg_relations (source_id, relation, target_id, context, weight, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, relation, target_id) DO UPDATE SET
                context=excluded.context,
                weight=excluded.weight,
                updated_at=excluded.updated_at
            """,
            (
                r["source_id"],
                r["relation"],
                r["target_id"],
                r["context"],
                r["weight"],
                now,
            ),
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
        index_conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return stats
    try:
        index_conn.execute("PRAGMA query_only = ON")
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
    finally:
        index_conn.close()
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
        index_conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return stats
    try:
        index_conn.execute("PRAGMA query_only = ON")
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
    finally:
        index_conn.close()
    now = int(time.time())
    for left, right, count in rows:
        left_id, right_id = f"topic:{left}", f"topic:{right}"
        if left_id not in known or right_id not in known:
            continue
        stats["pairs"] += 1
        conn.execute(
            """
            INSERT INTO kg_relations
                (source_id, relation, target_id, context, weight, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, relation, target_id) DO UPDATE SET
                context=excluded.context,
                weight=excluded.weight,
                updated_at=excluded.updated_at
            """,
            (
                left_id,
                "CO_OCCURS",
                right_id,
                f"같은 메시지에서 {int(count)}번 함께 언급됨",
                _synapse_weight(count),
                now,
            ),
        )
        stats["written"] += 1
    conn.commit()
    return stats


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


def collect_knowledge_graph(
    db_path: Path,
    *,
    state_root: Path | None = None,
    chat: str = "",
) -> dict[str, Any]:
    """Return the whole graph as nodes and edges for a force-directed view.

    Node ids stay stable across calls so a viewer can animate the same layout
    instead of re-randomising every refresh. ``chat`` limits the indexed topics
    to one room; empty means every room the index covers.
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
        # 색인된 대화에서 뉴런과 시냅스를 다시 만든다. 손으로 적은 다섯 개만
        # 있으면 방에서 한 시간 동안 이야기한 주제도 그래프에 없어, 답변이
        # 그 맥락을 모른 채 나간다 (2026-09-16).
        if state_root is not None:
            try:
                index_topic_entities(conn, state_root, chat=chat)
                index_topic_relations(conn, state_root, chat=chat)
            except (OSError, sqlite3.Error, ValueError):
                pass
        if state_root is not None:
            try:
                attach_ledger_evidence(conn, state_root)
            except (OSError, sqlite3.Error, ValueError):
                pass
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
        edges: list[dict[str, Any]] = []
        for row in conn.execute(
            """
            SELECT source_id, relation, target_id, context, weight, evidence_json
            FROM kg_relations
            ORDER BY weight DESC, id ASC
            """
        ):
            source_id, relation, target_id, context, weight, evidence_json = row
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
        return {
            "ok": True,
            "nodes": nodes,
            "edges": edges,
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
                "id": abs(hash(eid)) % 10000000,
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

        return {
            "items": items,
            "topics": topics,
            "total": total,
            "has_more": offset + len(items) < total,
        }
    finally:
        conn.close()


def _alias_matches(alias: str, haystack: str) -> bool:
    """Whether one alias appears in the text.

    Korean attaches particles straight to the noun ("알쫀쿠는", "러닝도"), so a
    plain substring test is right for anything longer than two characters. A
    one or two character alias is too easy to hit inside another word, so it
    needs a boundary on both sides ("런" must not match "런타임").
    """
    folded = alias.casefold().strip()
    if not folded:
        return False
    if len(folded) <= 2:
        return re.search(
            r"(?:^|[\s.,!?~/_-])" + re.escape(folded) + r"(?:$|[\s.,!?~/_-])",
            haystack,
        ) is not None
    return folded in haystack


def query_knowledge_context(
    query_text: str,
    state_root: Path | None = None,
    *,
    also: "list[str] | tuple[str, ...] | None" = None,
) -> list[str]:
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
    haystacks = [query_text.casefold()]
    for extra in also or ():
        folded = str(extra or "").casefold().strip()
        if folded:
            haystacks.append(folded)
    if not any(haystacks):
        return []
    root = state_root or Path.home() / "Library/Application Support/openkakao/bujamentor"
    kg_path = root / KNOWLEDGE_GRAPH_DB_NAME
    if not kg_path.exists():
        return []
    conn = _connect_kg(kg_path)
    try:
        ensure_seeded(conn)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name, category, aliases_json, description, key_facts_json"
            " FROM kg_entities ORDER BY importance DESC, entity_id ASC"
        )
        hits: list[str] = []
        for name, cat, aliases_str, desc, facts_str in cursor.fetchall():
            try:
                aliases = json.loads(aliases_str)
            except (TypeError, ValueError):
                aliases = []
            if not isinstance(aliases, list):
                aliases = []
            terms = [str(alias) for alias in aliases] + [str(name)]
            matched = any(
                _alias_matches(term, haystack)
                for haystack in haystacks
                for term in terms
            )
            if not matched:
                continue
            try:
                facts = json.loads(facts_str)
            except (TypeError, ValueError):
                facts = []
            if not isinstance(facts, list):
                facts = []
            hits.append(f"[{cat}] {name}: {desc} (핵심 맥락: {'; '.join(str(f) for f in facts)})")
        return hits
    except Exception:
        return []
    finally:
        conn.close()
