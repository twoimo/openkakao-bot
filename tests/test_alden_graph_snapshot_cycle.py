"""One committed source snapshot and atomic publication of a graph cycle."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_auto_reply_knowledge_graph import KG


class GraphSnapshotCycleTests(unittest.TestCase):
    def fixture(self, base):
        root = base / "state"
        root.mkdir()
        source = base / "context.sqlite3"
        writer = sqlite3.connect(source)
        writer.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA wal_autocheckpoint=0;
            CREATE TABLE context_messages(id INTEGER PRIMARY KEY, source TEXT,
                chat TEXT, date TEXT, user_name TEXT, message TEXT, vector BLOB);
            CREATE TABLE context_topic_stats(chat TEXT, topic TEXT, message_count INTEGER);
            CREATE TABLE context_message_topics(message_id INTEGER, topic TEXT);
            INSERT INTO context_messages(chat,message) VALUES('room','first');
        """)
        writer.commit()
        graph = KG._connect_kg(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
        KG.ensure_seeded(graph)
        KG.write_meta(graph, "last_indexed_at", "111")
        return root, source, writer, graph

    def stages(self, first=lambda *a, **k: None, second=lambda *a, **k: None):
        return mock.patch.multiple(
            KG, index_topic_entities=first, index_topic_relations=second,
            index_chat_entities=mock.DEFAULT, index_person_entities=mock.DEFAULT,
            index_membership_relations=mock.DEFAULT, prune_indexed_entities=mock.DEFAULT,
            _merge_seed_rooms=mock.DEFAULT, attach_ledger_evidence=mock.DEFAULT,
        )

    def insert(self, graph):
        graph.execute("INSERT INTO kg_entities(entity_id,name,category,aliases_json,"
                      "description,key_facts_json,importance,updated_at)"
                      " VALUES('ent:new','new','entity','[]','','[]',50,1)")
        graph.commit()

    def test_all_phases_see_the_same_wal_snapshot_and_copy_once(self):
        with tempfile.TemporaryDirectory() as td:
            root, source, writer, graph = self.fixture(Path(td))
            seen, replicas = [], []
            def read(*a, **k):
                with KG._open_isolated_ro_conn(source) as c:
                    seen.append(c.execute("SELECT count(*) FROM context_messages").fetchone()[0])
                    replicas.append(Path(c.execute("PRAGMA database_list").fetchone()[2]))
                    self.assertEqual(c.execute("PRAGMA query_only").fetchone()[0], 1)
                if len(seen) == 1:
                    writer.execute("INSERT INTO context_messages(chat,message) VALUES('room','second')")
                    writer.commit()
                    writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            try:
                with self.stages(read, read), mock.patch.object(KG, "refresh_dense_index"), \
                        mock.patch.object(KG, "_copy_consistent_sqlite_replica", wraps=KG._copy_consistent_sqlite_replica) as copy:
                    KG._reindex_all(graph, root)
                self.assertEqual(seen, [1, 1])
                self.assertEqual(copy.call_count, 1)
                self.assertEqual(replicas[0], replicas[1])
                self.assertFalse(replicas[0].parent.exists())
                with KG._open_isolated_ro_conn(source) as c:
                    self.assertEqual(c.execute("SELECT count(*) FROM context_messages").fetchone()[0], 2)
            finally:
                writer.close(); graph.close()

    def test_copy_failure_preserves_graph_watermark_and_skips_dense(self):
        with tempfile.TemporaryDirectory() as td:
            root, source, writer, graph = self.fixture(Path(td))
            try:
                before = graph.execute("SELECT * FROM kg_entities ORDER BY entity_id").fetchall()
                with mock.patch.object(KG, "_copy_consistent_sqlite_replica", side_effect=OSError("copy refused")), \
                        mock.patch.object(KG, "refresh_dense_index") as dense:
                    KG._reindex_all(graph, root)
                self.assertEqual(KG.read_meta(graph, "last_indexed_at"), "111")
                self.assertEqual(KG.read_meta(graph, "last_snapshot_status"), "fail_closed")
                self.assertTrue(KG.read_meta(graph, "last_index_error"))
                self.assertEqual(before, graph.execute("SELECT * FROM kg_entities ORDER BY entity_id").fetchall())
                dense.assert_not_called()
            finally:
                writer.close(); graph.close()

    def test_swallowed_source_sql_error_rolls_back_prior_stage_commits(self):
        with tempfile.TemporaryDirectory() as td:
            root, source, writer, graph = self.fixture(Path(td))
            def first(g, *a, **k):
                self.insert(g)
                with KG._open_isolated_ro_conn(source) as c:
                    # Membership indexers contain inner sqlite3.Error handlers.
                    try: c.execute("SELECT absent_column FROM context_messages")
                    except sqlite3.Error: pass
            try:
                with self.stages(first), mock.patch.object(KG, "refresh_dense_index") as dense:
                    KG._reindex_all(graph, root)
                self.assertEqual(graph.execute("SELECT count(*) FROM kg_entities WHERE entity_id='ent:new'").fetchone()[0], 0)
                self.assertEqual(KG.read_meta(graph, "last_indexed_at"), "111")
                self.assertTrue(KG.read_meta(graph, "last_index_error"))
                dense.assert_not_called()
            finally:
                writer.close(); graph.close()

    def test_swallowed_graph_sql_error_cannot_publish_a_fresh_watermark(self):
        with tempfile.TemporaryDirectory() as td:
            root, source, writer, graph = self.fixture(Path(td))
            def first(g, *a, **k):
                self.insert(g)
                try: g.execute("INSERT INTO absent_graph_table VALUES(1)")
                except sqlite3.Error: pass
            try:
                with self.stages(first), mock.patch.object(KG, "refresh_dense_index") as dense:
                    KG._reindex_all(graph, root)
                self.assertEqual(KG.read_meta(graph, "last_indexed_at"), "111")
                self.assertEqual(graph.execute("SELECT count(*) FROM kg_entities WHERE entity_id='ent:new'").fetchone()[0], 0)
                dense.assert_not_called()
            finally:
                writer.close(); graph.close()

    def test_late_failure_rolls_back_rows_and_fts(self):
        with tempfile.TemporaryDirectory() as td:
            root, source, writer, graph = self.fixture(Path(td))
            def fail(*a, **k): raise NameError("synthetic late failure")
            try:
                with self.stages(lambda g, *a, **k: self.insert(g), fail), mock.patch.object(KG, "refresh_dense_index") as dense:
                    KG._reindex_all(graph, root)
                self.assertEqual(graph.execute("SELECT count(*) FROM kg_entities WHERE entity_id='ent:new'").fetchone()[0], 0)
                self.assertEqual(graph.execute("SELECT count(*) FROM kg_entities_fts WHERE entity_id='ent:new'").fetchone()[0], 0)
                self.assertEqual(KG.read_meta(graph, "last_indexed_at"), "111")
                dense.assert_not_called()
            finally:
                writer.close(); graph.close()

    def test_other_readers_see_changes_only_after_whole_cycle_commits(self):
        with tempfile.TemporaryDirectory() as td:
            root, source, writer, graph = self.fixture(Path(td))
            other = sqlite3.connect(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
            observed = []
            def second(*a, **k):
                observed.append(other.execute("SELECT count(*) FROM kg_entities WHERE entity_id='ent:new'").fetchone()[0])
            try:
                with self.stages(lambda g, *a, **k: self.insert(g), second), mock.patch.object(KG, "refresh_dense_index") as dense:
                    KG._reindex_all(graph, root)
                self.assertEqual(observed, [0])
                self.assertEqual(other.execute("SELECT count(*) FROM kg_entities WHERE entity_id='ent:new'").fetchone()[0], 1)
                self.assertNotEqual(KG.read_meta(graph, "last_indexed_at"), "111")
                dense.assert_called_once()
            finally:
                other.close(); writer.close(); graph.close()

    def test_cancellation_rolls_back_even_if_the_caller_keeps_its_connection(self):
        with tempfile.TemporaryDirectory() as td:
            root, source, writer, graph = self.fixture(Path(td))
            def cancel(*a, **k): raise KeyboardInterrupt()
            try:
                with self.stages(lambda g, *a, **k: self.insert(g), cancel), mock.patch.object(KG, "refresh_dense_index") as dense:
                    with self.assertRaises(KeyboardInterrupt):
                        KG._reindex_all(graph, root)
                self.assertFalse(graph.in_transaction)
                self.assertEqual(graph.execute("SELECT count(*) FROM kg_entities WHERE entity_id='ent:new'").fetchone()[0], 0)
                self.assertEqual(KG.read_meta(graph, "last_indexed_at"), "111")
                dense.assert_not_called()
                with KG._open_isolated_ro_conn(source) as c:
                    self.assertEqual(c.execute("SELECT count(*) FROM context_messages").fetchone()[0], 1)
            finally:
                writer.close(); graph.close()

    def test_connection_open_failure_caught_by_indexer_cannot_prune_old_graph(self):
        with tempfile.TemporaryDirectory() as td:
            root, source, writer, graph = self.fixture(Path(td))
            graph.execute("INSERT INTO kg_entities(entity_id,name,category,aliases_json,"
                          "description,key_facts_json,importance,updated_at)"
                          " VALUES('chat:preserved','preserved','room','[]','','[]',50,1)")
            graph.commit()
            before = graph.execute("SELECT * FROM kg_entities ORDER BY entity_id").fetchall()
            connect = sqlite3.connect
            def fail_readonly(target, *args, **kwargs):
                if str(target).startswith("file:") and "mode=ro" in str(target):
                    raise sqlite3.OperationalError("synthetic open failure")
                return connect(target, *args, **kwargs)
            try:
                with mock.patch.object(KG.sqlite3, "connect", side_effect=fail_readonly), \
                        mock.patch.object(KG, "refresh_dense_index") as dense:
                    KG._reindex_all(graph, root, cycle_started_at=1000)
                self.assertEqual(before, graph.execute("SELECT * FROM kg_entities ORDER BY entity_id").fetchall())
                self.assertEqual(graph.execute("SELECT count(*) FROM kg_entities_fts WHERE entity_id='chat:preserved'").fetchone()[0], 1)
                self.assertEqual(KG.read_meta(graph, "last_indexed_at"), "111")
                self.assertEqual(KG.read_meta(graph, "last_snapshot_status"), "fail_closed")
                dense.assert_not_called()
            finally:
                writer.close(); graph.close()

    def test_failed_cycle_does_not_commit_the_callers_pending_transaction(self):
        with tempfile.TemporaryDirectory() as td:
            root, source, writer, graph = self.fixture(Path(td))
            other = sqlite3.connect(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
            try:
                graph.execute("BEGIN")
                graph.execute("INSERT INTO kg_meta(key,value) VALUES('caller_pending','yes')")
                with mock.patch.object(KG, "_copy_consistent_sqlite_replica", side_effect=OSError("copy refused")), \
                        mock.patch.object(KG, "refresh_dense_index") as dense:
                    KG._reindex_all(graph, root)
                self.assertTrue(graph.in_transaction)
                self.assertEqual(KG.read_meta(graph, "caller_pending"), "yes")
                self.assertEqual(KG.read_meta(other, "caller_pending"), "")
                self.assertEqual(KG.read_meta(graph, "last_indexed_at"), "111")
                graph.rollback()
                self.assertEqual(KG.read_meta(graph, "caller_pending"), "")
                dense.assert_not_called()
            finally:
                other.close(); writer.close(); graph.close()

    def test_success_inside_caller_transaction_defers_dense_until_graph_is_committed(self):
        with tempfile.TemporaryDirectory() as td:
            root, source, writer, graph = self.fixture(Path(td))
            other = sqlite3.connect(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
            try:
                graph.execute("BEGIN")
                with self.stages(lambda g, *a, **k: self.insert(g)), \
                        mock.patch.object(KG, "refresh_dense_index") as dense:
                    KG._reindex_all(graph, root)
                self.assertTrue(graph.in_transaction)
                self.assertEqual(other.execute("SELECT count(*) FROM kg_entities WHERE entity_id='ent:new'").fetchone()[0], 0)
                self.assertEqual(KG.read_meta(other, "last_indexed_at"), "111")
                self.assertEqual(KG.read_meta(graph, "last_dense_status"), "deferred:graph_transaction_uncommitted")
                dense.assert_not_called()
                graph.commit()
                self.assertEqual(other.execute("SELECT count(*) FROM kg_entities WHERE entity_id='ent:new'").fetchone()[0], 1)
                self.assertNotEqual(KG.read_meta(other, "last_indexed_at"), "111")
            finally:
                other.close(); writer.close(); graph.close()
