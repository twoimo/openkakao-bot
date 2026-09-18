"""Tests for the knowledge graph.

The graph used to be a Python constant with no provenance: a node could not
name the message that backed it. These tests pin the behaviour that makes it
checkable — evidence is read from the room ledgers, a node with no match says
so instead of pretending, and a broken state root does not raise (2026-09-16).
"""

import fcntl
import importlib.util
import json
import os
import sqlite3
import sys
import subprocess
import tempfile
import time
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
            report = KG.collect_knowledge_graph(root / "context.sqlite3", state_root=root, wait_for_reindex=True)
        self.assertTrue(report["ok"])
        self.assertEqual(report["node_count"], len(report["nodes"]))
        self.assertEqual(report["edge_count"], len(report["edges"]))
        self.assertGreater(report["node_count"], 0)

    def test_every_node_has_a_stable_id_and_a_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_state_root(tmp)
            report = KG.collect_knowledge_graph(root / "context.sqlite3", state_root=root, wait_for_reindex=True)
        ids = [node["id"] for node in report["nodes"]]
        self.assertEqual(len(ids), len(set(ids)), "node ids must be unique")
        for node in report["nodes"]:
            with self.subTest(node=node["id"]):
                self.assertTrue(node["label"])
                self.assertIn(node["evidence"]["kind"], ("seed", "ledger"))

    def test_edges_reference_nodes_that_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_state_root(tmp)
            report = KG.collect_knowledge_graph(root / "context.sqlite3", state_root=root, wait_for_reindex=True)
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
                root / "context.sqlite3", state_root=root / "nowhere", wait_for_reindex=True
            )
        self.assertTrue(report["ok"])
        self.assertGreater(report["node_count"], 0)
        self.assertEqual(report["grounded_nodes"], 0)


class EvidenceTests(unittest.TestCase):
    def test_a_node_mentioned_in_the_ledger_is_grounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_state_root(tmp)
            report = KG.collect_knowledge_graph(root / "context.sqlite3", state_root=root, wait_for_reindex=True)
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
            report = KG.collect_knowledge_graph(root / "context.sqlite3", state_root=root, wait_for_reindex=True)
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
            report = KG.collect_knowledge_graph(root / "context.sqlite3", state_root=root, wait_for_reindex=True)
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
            report = KG.collect_knowledge_graph(root / "context.sqlite3", state_root=root, wait_for_reindex=True)
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
            report = KG.collect_knowledge_graph(root / "context.sqlite3", state_root=root, wait_for_reindex=True)
        self.assertTrue(report["ok"])


class BackgroundReindexTests(unittest.TestCase):
    """The refresh must not run inside the request that asks for the graph.

    A cold cache used to make the first read walk the 1.4GB context DB
    synchronously, so the menu bar hit its 25s timeout and drew an empty graph.
    These tests pin the contract that replaced it: serve the stored graph now,
    refresh behind the scenes, and tell the caller the picture is stale
    (2026-09-17, 6 Pro 지적).
    """

    def test_a_cold_cache_serves_a_stored_graph_and_says_it_is_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_state_root(tmp)
            report = KG.collect_knowledge_graph(
                root / "context.sqlite3", state_root=root, reindex_mode="thread"
            )
            try:
                self.assertTrue(report["ok"])
                self.assertTrue(report["stale"], "a background refresh must be reported as stale")
                self.assertTrue(report["reindex"]["started"])
                self.assertNotEqual(report["reindex"].get("mode"), "inline")
                # The seed graph is already readable even though the refresh is
                # still walking the ledger.
                self.assertGreater(report["node_count"], 0)
            finally:
                KG.wait_for_background_reindex()

    def test_a_second_read_while_one_is_running_does_not_stack_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_state_root(tmp)
            first = KG.collect_knowledge_graph(
                root / "context.sqlite3", state_root=root, reindex_mode="thread"
            )
            second = KG.collect_knowledge_graph(
                root / "context.sqlite3", state_root=root, reindex_mode="thread"
            )
            try:
                if first["reindex"]["started"]:
                    # The second call must reuse the running refresh rather than
                    # starting a second walk over the same database.
                    self.assertFalse(second["reindex"]["started"])
                    self.assertEqual(second["reindex"]["reason"], "in_flight")
            finally:
                KG.wait_for_background_reindex()

    def test_the_wait_helper_returns_immediately_when_nothing_is_running(self):
        self.assertTrue(KG.wait_for_background_reindex(timeout=0.0))

    def test_the_wait_helper_reports_a_settled_graph(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_state_root(tmp)
            KG.collect_knowledge_graph(
                root / "context.sqlite3", state_root=root, reindex_mode="thread"
            )
            self.assertTrue(KG.wait_for_background_reindex(timeout=30.0))
            self.assertTrue(KG.wait_for_background_reindex(timeout=0.0))

    def test_an_inline_read_is_not_stale_and_indexes_before_returning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_state_root(tmp)
            report = KG.collect_knowledge_graph(
                root / "context.sqlite3", state_root=root, wait_for_reindex=True
            )
            self.assertTrue(report["ok"])
            self.assertFalse(report["stale"])
            self.assertEqual(report["reindex"]["mode"], "inline")
            # Indexing ran before the rows were read, so the ledger evidence is
            # already attached in this very response.
            alizonku = next(node for node in report["nodes"] if "알쫀쿠" in node["label"])
            self.assertEqual(alizonku["evidence"]["kind"], "ledger")

    def test_a_warm_read_reports_when_the_graph_was_last_indexed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_state_root(tmp)
            KG.collect_knowledge_graph(
                root / "context.sqlite3", state_root=root, wait_for_reindex=True
            )
            warm = KG.collect_knowledge_graph(root / "context.sqlite3", state_root=root)
            self.assertFalse(warm["stale"], "a fresh index must not be reported as stale")
            self.assertIsNone(warm["reindex"], "a fresh graph needs no refresh")
            # "언제 기준인지"는 화면이 말할 수 있어야 한다 (6 Pro 지적).
            self.assertGreater(warm["indexed_at"], 0, "the view needs a last-indexed time")

    def test_the_detached_child_entrypoint_indexes_and_records_the_time(self):
        """The menu bar is a short-lived process, so the walk must survive it.

        The parent spawns a detached child through the module CLI. This pins
        both halves of that contract: the entrypoint runs at all (it used to
        sit above _reindex_all and die with a NameError), and it leaves the
        indexed time behind so the next poll stops asking for a refresh
        (2026-09-17, 사용자 지시).
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = make_state_root(tmp)
            completed = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(KG.__file__),
                    "--reindex-once",
                    "--state-root",
                    str(root),
                    "--chat",
                    "",
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr[-600:])
            conn = sqlite3.connect(str(root / KG.KNOWLEDGE_GRAPH_DB_NAME))
            try:
                value = KG.read_meta(conn, "last_indexed_at")
            finally:
                conn.close()
            self.assertTrue(value, "the child must record when the walk finished")
            warm = KG.collect_knowledge_graph(root / "context.sqlite3", state_root=root)
            self.assertFalse(warm["stale"], "the parent must see the child's work")
            self.assertIsNone(warm["reindex"], "a fresh index needs no second walk")

    def test_the_process_guard_reports_a_held_lock(self):
        """One walk at a time is enforced by the lock file, not by memory."""
        with tempfile.TemporaryDirectory() as tmp:
            root = make_state_root(tmp)
            self.assertFalse(KG._reindex_process_running(root))
            lock_path = root / "knowledge-graph-reindex.lock"
            descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertTrue(KG._reindex_process_running(root))
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
            self.assertFalse(KG._reindex_process_running(root))

class RealIndexPathTests(unittest.TestCase):
    """The indexers have to read the shared context database for real.

    Every other fixture puts context.sqlite3 inside the state root, but the
    indexers resolve it as a *sibling* of the state root and copy it aside
    before reading. So none of them ever exercised the copy path, and a
    missing tempfile import survived review: on the real 1.4GB database every
    indexer raised NameError, and the menu drew a freshly "indexed" empty
    graph (2026-09-17, 사용자 지시).
    """

    def _write_index(self, base: Path, *, rooms: int = 1) -> Path:
        index = base / "context.sqlite3"
        conn = sqlite3.connect(index)
        try:
            conn.execute(
                "CREATE TABLE context_messages (id INTEGER PRIMARY KEY, source TEXT,"
                " chat TEXT, date TEXT, user_name TEXT, message TEXT, vector BLOB)"
            )
            rows = []
            for room in range(rooms):
                chat = "부자멘토멘티" if room == 0 else f"방{room}"
                for i in range(60):
                    rows.append(
                        (
                            "kakao",
                            chat,
                            f"2026-09-{i % 28 + 1:02d} 10:00:00",
                            "문승현",
                            f"{chat} 대화 {i}",
                        )
                    )
            conn.executemany(
                "INSERT INTO context_messages"
                " (source, chat, date, user_name, message) VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
        finally:
            conn.close()
        return index

    def _state_root(self, base: Path) -> Path:
        root = base / "state"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def test_the_indexers_read_the_sibling_context_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._write_index(base)
            root = self._state_root(base)
            kg = KG._connect_kg(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
            try:
                KG.ensure_seeded(kg)
                KG.index_chat_entities(kg, root)
                KG.index_person_entities(kg, root)
                chats = [
                    row[0]
                    for row in kg.execute(
                        "SELECT entity_id FROM kg_entities WHERE entity_id LIKE 'chat:%'"
                    )
                ]
                people = [
                    row[0]
                    for row in kg.execute(
                        "SELECT entity_id FROM kg_entities WHERE entity_id LIKE 'person:%'"
                    )
                ]
            finally:
                kg.close()
        self.assertIn("chat:부자멘토멘티", chats, "방 뉴런이 실제 색인에서 서야 한다")
        self.assertTrue(people, "사람 뉴런이 실제 색인에서 서야 한다")

    def test_person_indexer_reads_while_source_database_is_exclusively_locked(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            index = self._write_index(base)
            root = self._state_root(base)
            holder = sqlite3.connect(index)
            try:
                holder.execute("BEGIN EXCLUSIVE")
                kg = KG._connect_kg(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
                try:
                    KG.ensure_seeded(kg)
                    KG.index_person_entities(kg, root)
                    people = [
                        row[0]
                        for row in kg.execute(
                            "SELECT entity_id FROM kg_entities WHERE entity_id LIKE 'person:%'"
                        )
                    ]
                finally:
                    kg.close()
            finally:
                holder.rollback()
                holder.close()
        self.assertTrue(people, "원본이 EXCLUSIVE여도 사람 색인이 복사본에서 서야 한다")

    def test_a_failed_index_step_is_recorded_not_swallowed(self):
        """색인이 죽으면 그 이유가 그래프에 남아야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._write_index(base)
            root = self._state_root(base)
            original = KG.index_chat_entities

            def _boom(*args, **kwargs):
                raise NameError("name 'tempfile' is not defined")

            KG.index_chat_entities = _boom
            try:
                conn = KG._connect_kg(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
                try:
                    KG.ensure_seeded(conn)
                    KG._reindex_all(conn, root, cycle_started_at=int(time.time()))
                    recorded = KG.read_meta(conn, "last_index_error")
                    stamped = KG.read_meta(conn, "last_indexed_at")
                finally:
                    conn.close()
            finally:
                KG.index_chat_entities = original
        self.assertIn("NameError", recorded, "실패 이유가 기록되어야 한다")
        self.assertEqual(stamped, "", "실패한 색인을 '색인했다'로 찍으면 안 된다")


class IsolatedReadOnlyConnectionTests(unittest.TestCase):
    def _write_source_db(self, root: Path) -> Path:
        db = root / "context.sqlite3"
        conn = sqlite3.connect(db)
        try:
            conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute("INSERT INTO sample (value) VALUES ('copied row')")
            conn.commit()
        finally:
            conn.close()
        return db

    def test_isolated_copy_is_read_only_and_removed_after_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self._write_source_db(Path(tmp))
            with KG._open_isolated_ro_conn(db) as conn:
                copied_path = Path(conn.execute("PRAGMA database_list").fetchone()[2])
                copied_dir = copied_path.parent
                self.assertNotEqual(copied_path.resolve(), db.resolve())
                self.assertTrue(copied_path.exists())
                self.assertEqual(
                    conn.execute("SELECT value FROM sample").fetchone()[0],
                    "copied row",
                )
                self.assertEqual(conn.execute("PRAGMA query_only").fetchone()[0], 1)
                with self.assertRaises(sqlite3.OperationalError):
                    conn.execute("INSERT INTO sample (value) VALUES ('blocked write')")
            self.assertFalse(copied_dir.exists(), "the temporary copy must be removed on close")

    def test_isolated_copy_reads_while_source_database_is_exclusively_locked(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self._write_source_db(Path(tmp))
            holder = sqlite3.connect(db)
            try:
                holder.execute("BEGIN EXCLUSIVE")
                with KG._open_isolated_ro_conn(db) as conn:
                    copied_path = Path(conn.execute("PRAGMA database_list").fetchone()[2])
                    self.assertNotEqual(copied_path.resolve(), db.resolve())
                    self.assertEqual(
                        conn.execute("SELECT value FROM sample").fetchone()[0],
                        "copied row",
                    )
            finally:
                holder.rollback()
                holder.close()


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

            report = KG.collect_knowledge_graph(root / "context.sqlite3", state_root=root, wait_for_reindex=True)
        labels = [node["label"] for node in report["nodes"]]
        self.assertIn("옛 노드", labels, "the existing row must survive the migration")
        old = next(node for node in report["nodes"] if node["label"] == "옛 노드")
        self.assertEqual(old["evidence"]["kind"], "seed")

    def test_relation_evidence_follows_its_source_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_state_root(tmp)
            report = KG.collect_knowledge_graph(root / "context.sqlite3", state_root=root, wait_for_reindex=True)
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

    def test_normalize_text_query_fixes_typos_and_time_words(self):
        """오타·붙여쓰기·시간 표현을 표준형으로 모은다."""

        self.assertEqual(KG.normalize_text_query('러닝박에   뛰었어'), '러닝밖에 뛰었어')
        self.assertEqual(KG.normalize_text_query('오늘 아침 봤어'), '아침(8시) 봤어')
        self.assertEqual(KG.normalize_text_query('점심 때 얘기'), '점심(12시) 얘기')
        self.assertEqual(KG.normalize_text_query('채팅내용 정리'), '채팅 내용 정리')
        # 같은 질의를 여러 번 정규화해 쓴다. 두 번째가 첫 번째를 바꾸면
        # 답변마다 다른 맥락을 찾는다.
        once = KG.normalize_text_query('오늘 아침 러닝박에 뛰었어')
        self.assertEqual(KG.normalize_text_query(once), once)
        self.assertEqual(KG.normalize_text_query(None), '')
        self.assertEqual(KG.normalize_text_query('   '), '')

    def test_query_haystacks_keep_the_raw_and_the_normalized_form(self):
        """원문을 지우면 오타 사전이 모르는 표기가 사라진다."""

        self.assertEqual(
            KG.query_haystacks('러닝박에 뛰었어'),
            ['러닝박에 뛰었어', '러닝밖에 뛰었어'],
        )
        self.assertEqual(KG.query_haystacks('', ['오늘 아침']), ['오늘 아침', '아침(8시)'])
        self.assertEqual(KG.query_haystacks('   '), [])

    def test_a_typo_and_a_particle_still_find_the_entity(self):
        """정규화와 조사 목록이 실제 조회 결과를 바꾼다."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = KG._connect_kg(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
            try:
                KG.ensure_seeded(conn)
            finally:
                conn.close()
            # 오타 표기는 정규화를 거쳐야 "러닝" 노드와 만난다.
            hits = KG.query_knowledge_context('러닝박에 뛰었어', state_root=root)
            self.assertTrue(any('러닝' in hit for hit in hits))
            # 조사 "밖에"가 붙은 짧은 이름도 같은 노드를 찾는다.
            hits = KG.query_knowledge_context('러닝밖에 못 뛰었어', state_root=root)
            self.assertTrue(any('러닝' in hit for hit in hits))

    def test_an_english_alias_matches_a_korean_abbreviation(self):
        """별칭이 영어 정식 이름이어도 줄임말 질문을 찾아야 한다."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = KG._connect_kg(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
            try:
                KG.ensure_seeded(conn)
                conn.execute(
                    "INSERT INTO kg_entities (entity_id, name, category, aliases_json,"
                    " description, key_facts_json, importance, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(entity_id) DO UPDATE SET"
                    " aliases_json=excluded.aliases_json",
                    (
                        'ent:tech:claude',
                        'Anthropic Claude',
                        'AI/모델',
                        json.dumps(['Anthropic Claude', 'Claude 4'], ensure_ascii=False),
                        '대화에서 자주 언급되는 모델',
                        json.dumps([], ensure_ascii=False),
                        50,
                        0,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
            hits = KG.query_knowledge_context('클로드 어떤 모델 써?', state_root=root)
            self.assertTrue(any('Claude' in hit for hit in hits))

    def test_an_empty_query_returns_three_empty_lists(self):
        """빈 질의는 빈 결과여야 한다. 예전에는 리스트 하나를 돌려주어
        호출자의 세 값 언패킹이 깨졌다."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(
                KG._query_knowledge_structured('', state_root=root), ([], [], [])
            )
            bundle = KG.retrieve_knowledge_bundle('   ', state_root=root)
            self.assertEqual(bundle['facts'], [])
            self.assertEqual(bundle['fact_count'], 0)

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

    def test_missing_graph_db_returns_three_empty_lists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(
                KG._query_knowledge_structured('코인', state_root=root),
                ([], [], []),
            )

    def test_hybrid_ranks_exact_alias_above_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = KG._connect_kg(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
            try:
                KG.ensure_seeded(conn)
                rows = [
                    (
                        'test:coin-exact',
                        '코인',
                        'topic',
                        json.dumps(['코인'], ensure_ascii=False),
                        '정확 일치 테스트',
                        json.dumps(['정확 일치 사실'], ensure_ascii=False),
                        10,
                        0,
                    ),
                    (
                        'test:coin-partial',
                        '알리바바 클라우드 쿠폰',
                        'topic',
                        json.dumps(['코인 쿠폰'], ensure_ascii=False),
                        '부분 일치 테스트',
                        json.dumps(['부분 일치 사실'], ensure_ascii=False),
                        10,
                        0,
                    ),
                ]
                conn.executemany(
                    "INSERT INTO kg_entities (entity_id, name, category, aliases_json, description,"
                    " key_facts_json, importance, updated_at) VALUES (?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(entity_id) DO UPDATE SET name=excluded.name,"
                    " aliases_json=excluded.aliases_json, description=excluded.description,"
                    " key_facts_json=excluded.key_facts_json, importance=excluded.importance",
                    rows,
                )
                conn.commit()
            finally:
                conn.close()

            facts, _, _ = KG._query_knowledge_structured('코인', state_root=root)
            self.assertTrue(facts)
            self.assertIn('코인', facts[0])
            self.assertNotIn('알리바바 클라우드 쿠폰', facts[0])

    def test_hybrid_helpers_are_deterministic(self):
        text = '코인 알리바바 클라우드'
        self.assertEqual(
            KG._deterministic_text_embedding(text),
            KG._deterministic_text_embedding(text),
        )
        exact = KG._keyword_match_score(['코인'], ['코인'])
        partial = KG._keyword_match_score(['코인'], ['코인 시세'])
        self.assertGreater(exact, partial)



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
