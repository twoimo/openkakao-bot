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
import zlib
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
    try:
        index_conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return stats
    try:
        index_conn.execute("PRAGMA query_only = ON")
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
        index_conn.close()
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
    try:
        index_conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return stats
    try:
        index_conn.execute("PRAGMA query_only = ON")
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
    except sqlite3.Error:
        return stats
    finally:
        index_conn.close()
    # 방마다 몇 명을 세울지 정하려면 방별 총량이 필요하다.
    totals: dict[str, int] = {}
    try:
        index_conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
        try:
            index_conn.execute("PRAGMA query_only = ON")
            for room, count in index_conn.execute(
                "SELECT chat, COUNT(*) FROM context_messages"
                " WHERE chat IS NOT NULL AND chat != '' GROUP BY chat"
            ):
                totals[str(room)] = int(count or 0)
        finally:
            index_conn.close()
    except sqlite3.Error:
        totals = {}
    per_room: dict[str, int] = {}
    now = int(time.time())
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
        lines = _index_person_lines(index_path, raw_keys.get(room_key, [room_key]), name)
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
    index_path: Path,
    chats: str | list[str],
    user: str,
    *,
    limit: int = INDEX_PERSON_SAMPLE_LINES,
) -> list[str]:
    """이 사람이 실제로 남긴 최근 대화 몇 줄.

    요약이 아니라 발언 원문이다. 답변이 그 사람의 말투와 관심사를 그대로
    참고할 수 있어야 한다 (2026-09-16).
    """
    try:
        conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    keys = [chats] if isinstance(chats, str) else list(chats)
    if not keys:
        conn.close()
        return []
    marks = ",".join("?" * len(keys))
    try:
        conn.execute("PRAGMA query_only = ON")
        # 같은 방이 두 키로 색인되어 있어도 한 사람의 발언을 모아 읽는다.
        rows = conn.execute(
            "SELECT date, message FROM context_messages"
            f" WHERE chat IN ({marks}) AND user_name = ?"
            " ORDER BY id DESC LIMIT ?",
            keys + [user, max(int(limit) * 6, 12)],
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
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
        index_conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return stats
    now = int(time.time())
    try:
        index_conn.execute("PRAGMA query_only = ON")
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
                        source,
                        "TALKED_IN",
                        target,
                        f"이 방에서 {int(count or 0):,}건을 남김",
                        _synapse_weight(int(count or 0)),
                        now,
                    ),
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
                        source,
                        "DISCUSSED",
                        target,
                        f"이 방에서 {int(count or 0):,}건이 이 주제로 묶임",
                        _synapse_weight(int(count or 0)),
                        now,
                    ),
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
                        source,
                        "TALKS_ABOUT",
                        target,
                        f"이 주제로 {int(count or 0):,}건을 말함",
                        _synapse_weight(int(count or 0)),
                        now,
                    ),
                )
                stats["person_topic"] += 1
        except sqlite3.Error:
            pass
    finally:
        index_conn.close()
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


def collect_knowledge_graph(
    db_path: Path,
    *,
    state_root: Path | None = None,
    chat: str = "",
    force_reindex: bool = False,
    reindex_interval_seconds: int = 300,
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
        # 이미 색인된 데이터가 있고 최근(기본 5분)에 갱신되었다면 매번
        # 1.4GB DB 전체를 다시 훑지 않고 저장된 그래프를 즉시 반환한다.
        # 메뉴바 폴링(2초 주기)과 감사에서 프로세스가 25초 타임아웃에
        # 걸려 빈 그래프로 떨어지는 병목을 해결한다 (2026-09-17).
        now = int(time.time())
        last_updated_row = conn.execute(
            "SELECT MAX(updated_at), COUNT(*) FROM kg_entities"
            " WHERE entity_id LIKE 'chat:%' OR entity_id LIKE 'topic:%'"
        ).fetchone()
        last_updated = int(last_updated_row[0] or 0)
        indexed_count = int(last_updated_row[1] or 0)
        needs_reindex = force_reindex or indexed_count == 0 or (now - last_updated >= reindex_interval_seconds)

        if needs_reindex and state_root is not None:
            # 이번 주기가 시작된 시각. 이 뒤에 다시 쓰이지 않은 색인 뉴런은
            # 색인에서 빠졌거나 이름이 바뀐 것이므로 지운다 (2026-09-16).
            cycle_started_at = now
            # 색인된 대화에서 뉴런과 시냅스를 다시 만든다. 손으로 적은 다섯 개만
            # 있으면 방에서 한 시간 동안 이야기한 주제도 그래프에 없어, 답변이
            # 그 맥락을 모른 채 나간다 (2026-09-16).
            try:
                index_topic_entities(conn, state_root, chat=chat)
                index_topic_relations(conn, state_root, chat=chat)
            except (OSError, sqlite3.Error, ValueError):
                pass
            # 방과 사람도 뉴런으로 세우고 주제와 잇는다. 주제만 있으면 "코인"은
            # 알아도 그것이 어느 방에서 누구와 나눈 이야기인지가 그래프에 없어,
            # 답변이 엉뚱한 방의 맥락을 끌어온다 (2026-09-16, 사용자 지시).
            try:
                index_chat_entities(conn, state_root, chat=chat)
                index_person_entities(conn, state_root, chat=chat)
                index_membership_relations(conn, state_root, chat=chat)
                # 색인에서 빠진 뉴런을 지운다. 지우지 않으면 이름이 바뀐
                # 방이 두 이름으로 남아 그림이 중복으로 채워진다
                # (2026-09-16, 사용자 지시).
                prune_indexed_entities(conn, cycle_started_at=cycle_started_at)
                # 손으로 적은 방 뉴런을 색인 뉴런에 합쳐 한 방이 한 번만
                # 서게 한다 (2026-09-16).
                _merge_seed_rooms(conn)
            except (OSError, sqlite3.Error, ValueError):
                pass
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
}

KOREAN_PARTICLES_PATTERN = r"(?:$|[\s.,!?~/_\-은는이가을를도에과의와로등만])"

def normalize_text_query(text: str) -> str:
    normalized = str(text or "").strip()
    normalized = re.sub(r"(오늘\s*아침|아침에)", "아침(8시)", normalized)
    normalized = re.sub(r"(점심\s*때|점심에)", "점심(12시)", normalized)
    normalized = re.sub(r"(저녁\s*때|저녁에|밤에)", "저녁(20시)", normalized)
    normalized = re.sub(r"러닝박에", "러닝밖에", normalized)
    normalized = re.sub(r"채팅내용", "채팅 내용", normalized)
    return normalized

def _alias_matches(alias: str, haystack: str) -> bool:
    folded = alias.casefold().strip()
    if not folded:
        return False
    haystack_folded = haystack.casefold()
    expanded_terms = [folded]
    for syn_key, syn_vals in SYNONYM_DICTIONARY.items():
        if folded == syn_key.casefold() or folded in [v.casefold() for v in syn_vals]:
            expanded_terms.append(syn_key.casefold())
            expanded_terms.extend([v.casefold() for v in syn_vals])
    for term in set(expanded_terms):
        if len(term) <= 2:
            if re.search(
                r"(?:^|[\s.,!?~/_\-])" + re.escape(term) + KOREAN_PARTICLES_PATTERN,
                haystack_folded,
            ) is not None:
                return True
        elif term in haystack_folded:
            return True
    return False


def retrieve_knowledge_bundle(
    query_text: str,
    state_root: Path | None = None,
    *,
    chat_id: "int | str | None" = None,
    also: "list[str] | tuple[str, ...] | None" = None,
    top_k: int = 5,
) -> dict[str, Any]:
    """GraphRAG + 키워드/트리플 하이브리드 검색 번들 반환."""
    contexts = query_knowledge_context(
        query_text,
        state_root=state_root,
        chat_id=chat_id,
        also=also,
        include_relations=True,
    )
    return {
        "query": query_text,
        "chat_id": str(chat_id or ""),
        "facts": contexts[:top_k],
        "fact_count": len(contexts),
    }

def query_knowledge_context(
    query_text: str,
    state_root: Path | None = None,
    *,
    chat_id: "int | str | None" = None,
    also: "list[str] | tuple[str, ...] | None" = None,
    include_relations: bool = True,
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
            "SELECT entity_id, name, category, aliases_json, description, key_facts_json"
            " FROM kg_entities ORDER BY importance DESC, entity_id ASC"
        )
        hits: list[str] = []
        matched_entity_ids = set()
        matched_entity_names = {}
        chat_str = str(chat_id or "").strip()
        for ent_id, name, cat, aliases_str, desc, facts_str in cursor.fetchall():
            if chat_str and (ent_id.startswith("person:") or ent_id.startswith("chat:")):
                if chat_str not in ent_id:
                    continue
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
            matched_entity_ids.add(ent_id)
            matched_entity_names[ent_id] = name
            facts_snippet = f" (핵심 맥락: {'; '.join(str(f) for f in facts[:3])})" if facts else ""
            hits.append(f"[{cat}] {name}: {desc}{facts_snippet}")

        if include_relations and matched_entity_ids:
            marks = ",".join("?" * len(matched_entity_ids))
            rel_cursor = conn.execute(
                f"SELECT source_id, relation, target_id, context, weight"
                f" FROM kg_relations"
                f" WHERE source_id IN ({marks}) OR target_id IN ({marks})"
                f" ORDER BY weight DESC LIMIT 6",
                list(matched_entity_ids) + list(matched_entity_ids),
            )
            for src, rel, tgt, ctx, w in rel_cursor.fetchall():
                src_name = matched_entity_names.get(src, src.split(":")[-1])
                tgt_name = matched_entity_names.get(tgt, tgt.split(":")[-1])
                hits.append(f"[관계] {src_name} —({rel})→ {tgt_name}: {ctx}")

        return hits
    except Exception:
        return []
    finally:
        conn.close()
