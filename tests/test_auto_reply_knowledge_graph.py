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


if __name__ == "__main__":
    unittest.main()
