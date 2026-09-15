"""Deterministic Knowledge Graph for OpenKakao Bujamentor Auto-Reply.

Inspired by Andrej Karpathy's LLM Wiki & Algorithmic Knowledge Graph concepts:
- Entity nodes (People, Technologies, Concepts, Activities, Channels)
- Relation edges (DISCUSSED_IN, SUBSCRIBED_TO, OPINION_ON, ACTIVITY_OF)
- Grounded contextual facts with evidence identifiers and timestamps
- Deterministic, zero-hallucination graph queries for prompt context enrichment.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

KNOWLEDGE_GRAPH_DB_NAME = "knowledge-graph.sqlite3"
GRAPH_SOURCE_KIND = "knowledge_graph"

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
            updated_at INTEGER NOT NULL,
            UNIQUE(source_id, relation, target_id)
        )
    """)
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
            SELECT entity_id, name, category, aliases_json, description, key_facts_json, importance, updated_at
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
            eid, name, cat, aliases_str, desc, facts_str, imp, up_at = row
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
                "status_label": f"중요도 {imp}",
                "reply": "",
                "reason_label": f"관계 {len(connected)}개",
                "deletable": False,
            })

        return {
            "items": items,
            "topics": topics,
            "total": total,
            "has_more": offset + len(items) < total,
        }
    finally:
        conn.close()


def query_knowledge_context(query_text: str, state_root: Path | None = None) -> list[str]:
    """Retrieve relevant Knowledge Graph context nodes for a given message."""
    if not query_text:
        return []
    import re
    root = state_root or Path.home() / "Library/Application Support/openkakao/bujamentor"
    kg_path = root / KNOWLEDGE_GRAPH_DB_NAME
    if not kg_path.exists():
        return []
    conn = _connect_kg(kg_path)
    try:
        ensure_seeded(conn)
        cursor = conn.cursor()
        q = query_text.casefold()
        cursor.execute("SELECT name, category, aliases_json, description, key_facts_json FROM kg_entities")
        hits = []
        for name, cat, aliases_str, desc, facts_str in cursor.fetchall():
            aliases = json.loads(aliases_str)
            matched = False
            for alias in aliases:
                al = alias.casefold()
                if len(al) <= 2:
                    # Short alias: require word boundary or whitespace/punctuation boundary to avoid false positives like '런타임' for '런'
                    if re.search(r'(?:^|[\s.,!?~/_-])' + re.escape(al) + r'(?:$|[\s.,!?~/_-])', q):
                        matched = True
                        break
                else:
                    if al in q:
                        matched = True
                        break
            if matched or name.casefold() in q:
                facts = json.loads(facts_str)
                hits.append(f"[{cat}] {name}: {desc} (핵심 맥락: {'; '.join(facts)})")
        return hits
    except Exception:
        return []
    finally:
        conn.close()
