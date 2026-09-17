"""Tests for the knowledge graph.

The graph used to be a Python constant with no provenance: a node could not
name the message that backed it. These tests pin the behaviour that makes it
checkable — evidence is read from the room ledgers, a node with no match says
so instead of pretending, and a broken state root does not raise (2026-09-16).
"""

import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_graph():
    path = SCRIPTS / "auto_reply_knowledge_graph.py"
    spec = importlib.util.spec_from_file_location("kg_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


KG = load_graph()
CHAT_ID = "417780809780519"


def make_state_root(tmp: str) -> Path:
    """A state root with one room ledger, shaped like the real one."""
    root = Path(tmp)
    room = root / "rooms" / CHAT_ID
    room.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "recorded_at": "2026-08-18T17:42:25.297464+00:00",
            "event_id": "db:1",
            "chat": "부자멘토멘티",
            "author": "문승현",
            "message": "알쫀쿠 이게 훨씬 낫죠",
            "status": "sent",
        },
        {
            "recorded_at": "2026-08-19T09:59:39.478438+00:00",
            "event_id": "db:2",
            "chat": "부자멘토멘티",
            "author": "최연우",
            "message": "오늘 러닝 10km 뛰었습니다",
            "status": "sent",
        },
    ]
    (room / "reply-evidence.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    return root


class GraphShapeTests(unittest.TestCase):
    def test_graph_returns_nodes_and_edges(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_state_root(tmp)
            report = KG.collect_knowledge_graph(root / "context.sqlite3", state_root=root)
        self.assertTrue(report["ok"])
        self.assertEqual(report["node_count"], len(report["nodes"]))
        self.assertEqual(report["edge_count"], len(report["edges"]))
        self.assertGreater(report["node_count"], 0)

    def test_every_node_has_a_stable_id_and_a_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_state_root(tmp)
            report = KG.collect_knowledge_graph(root / "context.sqlite3", state_root=root)
        ids = [node["id"] for node in report["nodes"]]
        self.assertEqual(len(ids), len(set(ids)), "node ids must be unique")
        for node in report["nodes"]:
            with self.subTest(node=node["id"]):
                self.assertTrue(node["label"])
                self.assertIn(node["evidence"]["kind"], ("seed", "ledger"))

    def test_edges_reference_nodes_that_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_state_root(tmp)
            report = KG.collect_knowledge_graph(root / "context.sqlite3", state_root=root)
        ids = {node["id"] for node in report["nodes"]}
        for edge in report["edges"]:
            with self.subTest(edge=edge["relation"]):
                self.assertIn(edge["source"], ids)
                self.assertIn(edge["target"], ids)

    def test_a_broken_state_root_still_returns_the_seeded_graph(self):
        """A missing ledger must not blank the window."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = KG.collect_knowledge_graph(
                root / "context.sqlite3", state_root=root / "nowhere"
            )
        self.assertTrue(report["ok"])
        self.assertGreater(report["node_count"], 0)
        self.assertEqual(report["grounded_nodes"], 0)


class EvidenceTests(unittest.TestCase):
    def test_a_node_mentioned_in_the_ledger_is_grounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_state_root(tmp)
            report = KG.collect_knowledge_graph(root / "context.sqlite3", state_root=root)
        alizonku = next(
            node for node in report["nodes"] if "알쫀쿠" in node["label"]
        )
        self.assertEqual(alizonku["evidence"]["kind"], "ledger")
        self.assertIn("db:1", alizonku["evidence"]["source_event_ids"])
        self.assertEqual(alizonku["evidence"]["chat_id"], CHAT_ID)
        self.assertTrue(alizonku["evidence"]["confirmed_at"])
        self.assertFalse(alizonku["evidence"]["retracted"])

    def test_a_node_with_no_message_keeps_seed_provenance(self):
        """A concept nobody has mentioned must not claim a source."""
        with tempfile.TemporaryDirectory() as tmp:
            root = make_state_root(tmp)
            report = KG.collect_knowledge_graph(root / "context.sqlite3", state_root=root)
        seeds = [n for n in report["nodes"] if n["evidence"]["kind"] == "seed"]
        self.assertTrue(seeds, "the seed nodes should still be present")
        for node in seeds:
            with self.subTest(node=node["id"]):
                self.assertEqual(node["evidence"]["source_event_ids"], [])

    def test_evidence_is_capped_per_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            room = root / "rooms" / CHAT_ID
            room.mkdir(parents=True, exist_ok=True)
            rows = [
                {
                    "recorded_at": f"2026-08-18T17:42:{index:02d}.000000+00:00",
                    "event_id": f"db:{index}",
                    "message": "알쫀쿠 이야기",
                    "status": "sent",
                }
                for index in range(40)
            ]
            (room / "reply-evidence.jsonl").write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                encoding="utf-8",
            )
            report = KG.collect_knowledge_graph(root / "context.sqlite3", state_root=root)
        alizonku = next(node for node in report["nodes"] if "알쫀쿠" in node["label"])
        self.assertLessEqual(
            len(alizonku["evidence"]["source_event_ids"]),
            KG.MAX_EVIDENCE_PER_NODE,
        )

    def test_a_ledger_with_a_missing_event_id_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            room = root / "rooms" / CHAT_ID
            room.mkdir(parents=True, exist_ok=True)
            (room / "reply-evidence.jsonl").write_text(
                json.dumps({"recorded_at": "2026-08-18T17:42:25+00:00", "message": "알쫀쿠"})
                + "\n",
                encoding="utf-8",
            )
            report = KG.collect_knowledge_graph(root / "context.sqlite3", state_root=root)
        alizonku = next(node for node in report["nodes"] if "알쫀쿠" in node["label"])
        self.assertEqual(alizonku["evidence"]["kind"], "seed")

    def test_malformed_ledger_lines_do_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            room = root / "rooms" / CHAT_ID
            room.mkdir(parents=True, exist_ok=True)
            (room / "reply-evidence.jsonl").write_text(
                "not json\n[1,2,3]\n" + json.dumps({"event_id": "db:9", "message": "알쫀쿠"}),
                encoding="utf-8",
            )
            report = KG.collect_knowledge_graph(root / "context.sqlite3", state_root=root)
        self.assertTrue(report["ok"])


class MigrationTests(unittest.TestCase):
    def test_an_old_graph_gains_the_evidence_column_in_place(self):
        """An installed graph predates the column; it must not be rebuilt."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / KG.KNOWLEDGE_GRAPH_DB_NAME
            conn = sqlite3.connect(str(db))
            conn.execute(
                """
                CREATE TABLE kg_entities (
                    entity_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    aliases_json TEXT NOT NULL,
                    description TEXT NOT NULL,
                    key_facts_json TEXT NOT NULL,
                    importance INTEGER DEFAULT 50,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE kg_relations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    context TEXT NOT NULL,
                    weight INTEGER DEFAULT 50,
                    updated_at INTEGER NOT NULL,
                    UNIQUE(source_id, relation, target_id)
                )
                """
            )
            conn.execute(
                "INSERT INTO kg_entities VALUES ('ent:old', '옛 노드', '분류', '[]', '설명', '[]', 10, 1)"
            )
            conn.commit()
            conn.close()

            report = KG.collect_knowledge_graph(root / "context.sqlite3", state_root=root)
        labels = [node["label"] for node in report["nodes"]]
        self.assertIn("옛 노드", labels, "the existing row must survive the migration")
        old = next(node for node in report["nodes"] if node["label"] == "옛 노드")
        self.assertEqual(old["evidence"]["kind"], "seed")

    def test_relation_evidence_follows_its_source_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_state_root(tmp)
            report = KG.collect_knowledge_graph(root / "context.sqlite3", state_root=root)
        by_id = {node["id"]: node for node in report["nodes"]}
        for edge in report["edges"]:
            source = by_id.get(edge["source"])
            if source is None:
                continue
            with self.subTest(edge=edge["relation"]):
                self.assertEqual(
                    edge["evidence"]["kind"],
                    source["evidence"]["kind"],
                    "an edge cannot be better grounded than its source",
                )


class ListEnvelopeTests(unittest.TestCase):
    """목록은 창이 읽는 봉투로 돌려주어야 한다.

    예전에는 items·has_more로 돌려주어 디코딩이 통째로 실패했고, 뉴런이
    수십 개 있는데도 창에는 "기록 없음"만 떴다 (2026-09-16).
    """

    def test_list_returns_the_vector_list_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_state_root(tmp)
            report = KG.collect_knowledge_graph_list(
                root / "context.sqlite3", limit=50, offset=0
            )
        for key in ("ok", "action", "count", "total", "rows", "truncated", "topics"):
            with self.subTest(key=key):
                self.assertIn(key, report)
        self.assertTrue(report["ok"])
        self.assertEqual(report["count"], len(report["rows"]))
        self.assertIsInstance(report["truncated"], bool)

    def test_row_ids_are_stable_across_calls(self):
        """창은 이 번호로 고른 줄을 기억한다.

        프로세스마다 달라지면 새로고침할 때마다 선택이 풀린다. 예전에는
        파이썬 hash()를 써서 매번 값이 바뀌었다 (2026-09-16).
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = make_state_root(tmp)
            first = KG.collect_knowledge_graph_list(root / "context.sqlite3", limit=50)
            second = KG.collect_knowledge_graph_list(root / "context.sqlite3", limit=50)
        self.assertEqual(
            [row["id"] for row in first["rows"]],
            [row["id"] for row in second["rows"]],
        )
        self.assertEqual(
            [row["row_key"] for row in first["rows"]],
            [row["row_key"] for row in second["rows"]],
        )

    def test_an_empty_query_still_returns_the_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = KG.collect_knowledge_graph_list(
                root / "context.sqlite3", query="없는말", limit=10
            )
        self.assertTrue(report["ok"])
        self.assertEqual(report["rows"], [])
        self.assertEqual(report["count"], 0)


class DeduplicationTests(unittest.TestCase):
    """같은 방이 두 이름으로 서지 않게 한다."""

    def test_room_key_maps_a_number_to_its_catalog_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "menubar-room-catalog.json").write_text(
                json.dumps(
                    {"rooms": [{"chat_id": 325472527151234, "title": "NIMDA 방"}]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            self.assertEqual(KG._room_key(root, "그룹:325472527151234"), "NIMDA 방")
            self.assertEqual(KG._room_key(root, "325472527151234"), "NIMDA 방")

    def test_room_key_keeps_an_unknown_room_as_is(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(KG._room_key(root, "그룹:999"), "그룹:999")
            self.assertEqual(KG._room_key(root, ""), "")

    def test_room_titles_survives_a_broken_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "menubar-room-catalog.json").write_text("not json", encoding="utf-8")
            self.assertEqual(KG._room_titles(root), {})

    def test_a_person_named_like_the_room_is_not_a_separate_neuron(self):
        """알림 계정은 방 이름과 같은 이름으로 말한다."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            index = root / "context.sqlite3"
            conn = sqlite3.connect(index)
            conn.execute(
                "CREATE TABLE context_messages (id INTEGER PRIMARY KEY, chat TEXT,"
                " user_name TEXT, message TEXT, date TEXT)"
            )
            for i in range(60):
                conn.execute(
                    "INSERT INTO context_messages (chat, user_name, message, date)"
                    " VALUES ('커리어톡', '커리어톡', ?, '2026-09-01 10:00:00')",
                    (f"채용 공고 알림 {i}",),
                )
            conn.commit()
            conn.close()
            kg = KG._connect_kg(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
            try:
                KG.ensure_seeded(kg)
                KG.index_chat_entities(kg, root)
                KG.index_person_entities(kg, root)
                people = [
                    row[0]
                    for row in kg.execute(
                        "SELECT entity_id FROM kg_entities WHERE entity_id LIKE 'person:%'"
                    )
                ]
            finally:
                kg.close()
        self.assertEqual(people, [])


class PruneTests(unittest.TestCase):
    """색인에서 빠진 뉴런은 지운다. 단, 색인이 고장 나면 지우지 않는다."""

    def _seed(self, conn):
        conn.execute(
            "INSERT INTO kg_entities (entity_id, name, category, aliases_json,"
            " description, key_facts_json, importance, updated_at)"
            " VALUES ('chat:옛방', '옛방', '대화방', '[]', '', '[]', 50, 1)"
        )
        conn.execute(
            "INSERT INTO kg_entities (entity_id, name, category, aliases_json,"
            " description, key_facts_json, importance, updated_at)"
            " VALUES ('chat:새방', '새방', '대화방', '[]', '', '[]', 50, 9999999999)"
        )
        conn.execute(
            "INSERT INTO kg_relations (source_id, relation, target_id, context, weight, updated_at)"
            " VALUES ('chat:옛방', 'DISCUSSED', 'topic:코인', '옛 연결', 50, 1)"
        )
        conn.commit()

    def test_a_stale_node_and_its_synapses_are_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = KG._connect_kg(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
            try:
                KG.ensure_seeded(conn)
                self._seed(conn)
                result = KG.prune_indexed_entities(conn, cycle_started_at=1000)
                left = {
                    row[0]
                    for row in conn.execute("SELECT entity_id FROM kg_entities")
                }
                relations = conn.execute(
                    "SELECT COUNT(*) FROM kg_relations WHERE source_id = 'chat:옛방'"
                ).fetchone()[0]
            finally:
                conn.close()
        self.assertEqual(result["nodes"], 1)
        self.assertNotIn("chat:옛방", left)
        self.assertIn("chat:새방", left)
        self.assertEqual(relations, 0)

    def test_a_mostly_stale_graph_is_left_alone(self):
        """색인을 못 읽은 주기에 그래프가 비면 안 된다."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = KG._connect_kg(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
            try:
                KG.ensure_seeded(conn)
                for i in range(10):
                    conn.execute(
                        "INSERT INTO kg_entities (entity_id, name, category, aliases_json,"
                        " description, key_facts_json, importance, updated_at)"
                        " VALUES (?, ?, '대화방', '[]', '', '[]', 50, 1)",
                        (f"chat:방{i}", f"방{i}"),
                    )
                conn.commit()
                result = KG.prune_indexed_entities(conn, cycle_started_at=1000)
                remaining = conn.execute(
                    "SELECT COUNT(*) FROM kg_entities WHERE entity_id LIKE 'chat:%'"
                ).fetchone()[0]
            finally:
                conn.close()
        self.assertEqual(result["nodes"], 0)
        self.assertEqual(remaining, 10)

    def test_nothing_stale_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = KG._connect_kg(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
            try:
                KG.ensure_seeded(conn)
                result = KG.prune_indexed_entities(conn, cycle_started_at=1000)
            finally:
                conn.close()
        self.assertEqual(result, {"nodes": 0, "relations": 0})


class NormalizeTests(unittest.TestCase):
    def test_normalize_rejects_a_non_dict(self):
        self.assertEqual(KG._normalize_evidence(None)["kind"], "seed")
        self.assertEqual(KG._normalize_evidence("nope")["kind"], "seed")

    def test_normalize_keeps_ledger_kind_and_ids(self):
        result = KG._normalize_evidence(
            {
                "kind": "ledger",
                "source_event_ids": ["db:1", "", "db:2"],
                "chat_id": "1",
                "confirmed_at": "2026-08-18T00:00:00Z",
                "retracted": True,
            }
        )
        self.assertEqual(result["kind"], "ledger")
        self.assertEqual(result["source_event_ids"], ["db:1", "db:2"])
        self.assertTrue(result["retracted"])

    def test_normalize_treats_an_unknown_kind_as_seed(self):
        result = KG._normalize_evidence({"kind": "something-else"})
        self.assertEqual(result["kind"], "seed")



class KnowledgeGraphRagAndNormalizationTests(unittest.TestCase):
    def test_alias_matches_korean_particles(self):
        self.assertTrue(KG._alias_matches('AI', 'AI는 정말 유용하다'))
        self.assertTrue(KG._alias_matches('AI', '(AI는 유용하다)'))
        self.assertTrue(KG._alias_matches('주식', '주식으로 분류해야 해'))
        self.assertTrue(KG._alias_matches('주식', '주식은 변동성이 큽니다'))
        self.assertTrue(KG._alias_matches('코인', '코인도 공부해야겠어'))
        self.assertTrue(KG._alias_matches('알쫀쿠', '알쫀쿠를 써봤어'))
        self.assertFalse(KG._alias_matches('런', '런타임 에러'))

    def test_synonym_dictionary_expansion(self):
        self.assertTrue(KG._alias_matches('알쫀쿠', '알리바바 클라우드 구독 관련 질문'))
        self.assertTrue(KG._alias_matches('지피티', 'ChatGPT 활용법'))
        self.assertTrue(KG._alias_matches('컴유', 'computer use 기능'))

    def test_query_knowledge_context_includes_relations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = KG._connect_kg(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
            try:
                KG.ensure_seeded(conn)
            finally:
                conn.close()
            hits = KG.query_knowledge_context('최연우', state_root=root, include_relations=True)
            self.assertTrue(any('최연우' in h for h in hits))
            self.assertTrue(any(h.startswith('[관계]') for h in hits))

    def test_retrieve_knowledge_bundle_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = KG._connect_kg(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
            try:
                KG.ensure_seeded(conn)
            finally:
                conn.close()
            bundle = KG.retrieve_knowledge_bundle('코인 시세', state_root=root, chat_id=417780809780519)
            self.assertIn('query', bundle)
            self.assertIn('facts', bundle)
            self.assertIn('fact_count', bundle)
            self.assertEqual(bundle['chat_id'], '417780809780519')



class RoomIsolationTests(unittest.TestCase):
    """방 격리가 실제로 성립하는지 검사한다.

    6 Pro가 배포 차단 사유로 지적한 결함이다. 예전 비교는 부분 문자열이라
    (1) 이름이 겹치는 정상 방 노드를 걸러내고 (2) 짧은 키가 남의 방 노드를
    통과시켰다. 두 방향을 모두 고정한다 (2026-09-17).
    """

    def _graph(self, root: Path):
        conn = KG._connect_kg(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
        KG.ensure_seeded(conn)
        return conn

    def _add_entity(self, conn, entity_id, name, aliases=None, facts=None):
        conn.execute(
            "INSERT INTO kg_entities (entity_id, name, category, aliases_json, description,"
            " key_facts_json, importance, updated_at) VALUES (?,?,?,?,?,?,?,?)"
            " ON CONFLICT(entity_id) DO UPDATE SET name=excluded.name,"
            " aliases_json=excluded.aliases_json, key_facts_json=excluded.key_facts_json",
            (
                entity_id,
                name,
                "person",
                json.dumps(aliases or [], ensure_ascii=False),
                name + " 설명",
                json.dumps(facts or [], ensure_ascii=False),
                5,
                0,
            ),
        )
        conn.commit()

    def test_a_room_can_read_its_own_person_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = self._graph(root)
            try:
                self._add_entity(
                    conn,
                    "person:부자멘토멘티:민수",
                    "민수",
                    aliases=["민수"],
                    facts=["부자멘토멘티에서 활동"],
                )
            finally:
                conn.close()
            hits = KG.query_knowledge_context(
                "민수 어떻게 생각해",
                state_root=root,
                chat_id="부자멘토멘티",
                include_relations=False,
            )
            self.assertTrue(
                any("민수" in h for h in hits),
                "자기 방의 인물 노드는 검색되어야 한다: " + repr(hits),
            )

    def test_a_person_from_another_room_is_not_returned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = self._graph(root)
            try:
                self._add_entity(
                    conn,
                    "person:다른방:철수",
                    "철수",
                    aliases=["철수"],
                    facts=["다른 방 사람"],
                )
            finally:
                conn.close()
            hits = KG.query_knowledge_context(
                "철수 어때",
                state_root=root,
                chat_id="부자멘토멘티",
                include_relations=False,
            )
            self.assertFalse(
                any("철수" in h for h in hits),
                "다른 방 인물은 검색되면 안 된다: " + repr(hits),
            )

    def test_a_room_whose_name_is_a_prefix_of_another_still_matches(self):
        # "부자멘토멘티"는 "부자멘토멘티-스터디"의 접두사다. 부분 문자열 비교는
        # 이 정상 노드를 걸러냈다.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = self._graph(root)
            try:
                self._add_entity(
                    conn,
                    "person:부자멘토멘티-스터디:영희",
                    "영희",
                    aliases=["영희"],
                )
            finally:
                conn.close()
            hits = KG.query_knowledge_context(
                "영희 봤어",
                state_root=root,
                chat_id="부자멘토멘티",
                include_relations=False,
            )
            self.assertFalse(
                any("영희" in h for h in hits),
                "이름이 접두사로 겹쳐도 다른 방이다: " + repr(hits),
            )

    def test_a_relation_to_another_room_person_is_filtered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = self._graph(root)
            try:
                self._add_entity(conn, "topic:코인", "코인", aliases=["코인"])
                self._add_entity(conn, "person:다른방:철수", "철수", aliases=["철수"])
                conn.execute(
                    "INSERT INTO kg_relations (source_id, relation, target_id, context, weight, updated_at)"
                    " VALUES (?,?,?,?,?,?)"
                    " ON CONFLICT(source_id, relation, target_id) DO UPDATE SET weight=excluded.weight",
                    ("topic:코인", "TALKS_ABOUT", "person:다른방:철수", "다른 방 대화", 9, 0),
                )
                conn.commit()
            finally:
                conn.close()
            hits = KG.query_knowledge_context(
                "코인 어떻게 생각해",
                state_root=root,
                chat_id="부자멘토멘티",
                include_relations=True,
            )
            relations = [h for h in hits if h.startswith("[관계]")]
            self.assertFalse(
                any("철수" in h for h in relations),
                "관계의 반대편이 타 방 인물이면 제외해야 한다: " + repr(relations),
            )

    def test_topic_nodes_are_shared_across_rooms(self):
        # 방 스코프가 없는 주제 노드는 어느 방에서도 쓸 수 있어야 한다.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = self._graph(root)
            try:
                self._add_entity(
                    conn, "topic:알쫀쿠", "알쫀쿠", aliases=["알쫀쿠"], facts=["구독 서비스"]
                )
            finally:
                conn.close()
            hits = KG.query_knowledge_context(
                "알쫀쿠 써봤어?",
                state_root=root,
                chat_id="부자멘토멘티",
                include_relations=False,
            )
            self.assertTrue(any("알쫀쿠" in h for h in hits), repr(hits))

    def test_no_chat_id_keeps_every_node_visible(self):
        # 방을 지정하지 않은 호출은 격리 대상이 아니다.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = self._graph(root)
            try:
                self._add_entity(conn, "person:아무방:테스트", "테스트인물", aliases=["테스트인물"])
            finally:
                conn.close()
            hits = KG.query_knowledge_context(
                "테스트인물", state_root=root, include_relations=False
            )
            self.assertTrue(any("테스트인물" in h for h in hits), repr(hits))


if __name__ == "__main__":
    unittest.main()

