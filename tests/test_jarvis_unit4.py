"""Unit 4: BM25+Dense RRF retrieval, DPO logprob eval, golden re-audit."""

from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from scripts import auto_reply_knowledge_graph as KG
from scripts.auto_reply_dream_rsi import (
    answer_similarity,
    run_fixed_budget_loop,
    select_experiment_action,
)
from scripts.auto_reply_finetune import (
    DPO_EVAL_UNAVAILABLE,
    dpo_loss_from_logprobs,
    evaluate_preference_pairs,
)
from scripts.auto_reply_golden_dataset import (
    QUALITY_HUMAN_AUTHORED,
    QUALITY_MODEL_GENERATED,
    audit_golden_record,
    audit_golden_records,
)


class _EmbeddingHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - stdlib handler contract
        size = int(self.headers.get("Content-Length", "0") or 0)
        payload = json.loads(self.rfile.read(size).decode("utf-8"))
        body = self.server.response_factory(payload)
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        try:
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, _format, *_args):
        pass


@contextmanager
def _local_embedding_server(response_factory):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _EmbeddingHandler)
    server.daemon_threads = True
    server.response_factory = response_factory
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}/v1/embeddings"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


class RrfMergeTests(unittest.TestCase):
    def test_semantic_only_id_survives_when_keywords_miss(self):
        merged = KG._rrf_merge(
            [("keyword-hit", 1.2)],
            [("semantic-only", 0.91), ("keyword-hit", 0.10)],
        )
        ids = [entity_id for entity_id, _score in merged]
        self.assertIn("semantic-only", ids)
        self.assertIn("keyword-hit", ids)
        self.assertEqual(ids[0], "keyword-hit")


class LiveRetrievalTests(unittest.TestCase):
    def _seed(self, root: Path) -> None:
        conn = KG._connect_kg(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
        try:
            KG.ensure_seeded(conn)
            conn.execute(
                "INSERT INTO kg_entities (entity_id, name, category, aliases_json, description,"
                " key_facts_json, importance, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    "ent:semantic-only",
                    "월 사용료 안내",
                    "topic",
                    json.dumps(["요금 안내"], ensure_ascii=False),
                    "키워드가 겹치지 않는 의미 후보",
                    json.dumps(["클라우드 과금 맥락"], ensure_ascii=False),
                    40,
                    0,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def test_dense_failure_is_bm25_only_not_hybrid_or_rrf(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed(root)
            bundle = KG.retrieve_knowledge_bundle("최연우", state_root=root)
            self.assertEqual(bundle["search_mode"], KG.SEARCH_MODE_BM25_ONLY)
            self.assertNotEqual(bundle["search_mode"], "hybrid")
            self.assertNotEqual(bundle["search_mode"], "rrf")
            self.assertNotIn("hybrid", str(bundle["search_mode"]).lower())
            self.assertEqual(bundle["index_version"], KG.SEARCH_INDEX_VERSION)
            self.assertIn("evidence_ids", bundle)
            self.assertTrue(any("최연우" in fact for fact in bundle["facts"]))

    def test_semantic_only_candidate_survives_live_rrf_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed(root)

            def fake_dense(state_root, query_text, *, limit=40):
                self.assertEqual(Path(state_root), root)
                return [("ent:semantic-only", 0.97)], "dense-wm"

            with mock.patch.object(KG, "_dense_ann_query", side_effect=fake_dense):
                ranked = KG._query_knowledge_ranked("이번달 청구금액", state_root=root)
            self.assertEqual(ranked["search_mode"], KG.SEARCH_MODE_RRF)
            self.assertIn("ent:semantic-only", ranked["candidates"])
            self.assertTrue(any("월 사용료" in fact for fact in ranked["entity_facts"]))
            self.assertEqual(ranked["watermark"], "dense-wm")

    def test_keyword_zero_no_longer_drops_dense_hits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed(root)

            def fake_dense(state_root, query_text, *, limit=40):
                return [("ent:semantic-only", 0.88)], "wm"

            with mock.patch.object(KG, "_dense_ann_query", side_effect=fake_dense):
                facts, _rels, candidates = KG._query_knowledge_structured(
                    "xyzzy-no-keyword",
                    state_root=root,
                )
            self.assertIn("ent:semantic-only", candidates)
            self.assertTrue(facts)

    def test_dense_refresh_endpoint_absence_is_fail_closed_and_preserves_graph(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed(root)
            conn = KG._connect_kg(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
            before = (
                conn.execute("SELECT COUNT(*) FROM kg_entities").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM kg_relations").fetchone()[0],
            )
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
                blocker.bind(("127.0.0.1", 0))
                port = blocker.getsockname()[1]
                with mock.patch.object(
                    KG,
                    "DENSE_EMBEDDING_URL",
                    f"http://127.0.0.1:{port}/v1/embeddings",
                ):
                    result = KG.refresh_dense_index(conn, root)
                    bundle = KG.retrieve_knowledge_bundle("최연우", state_root=root)
            after = (
                conn.execute("SELECT COUNT(*) FROM kg_entities").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM kg_relations").fetchone()[0],
            )
            dense_status = KG.read_meta(conn, "last_dense_status")
            conn.close()

            self.assertEqual(result["status"], "unavailable")
            self.assertTrue(dense_status.startswith("unavailable:"))
            self.assertLessEqual(len(dense_status), KG.DENSE_STATUS_MAX_LENGTH)
            self.assertEqual(before, after)
            self.assertEqual(bundle["search_mode"], KG.SEARCH_MODE_BM25_ONLY)

            status = KG.collect_knowledge_graph_status(root / "context.sqlite3", state_root=root)
            report = KG.collect_knowledge_graph(root / "context.sqlite3")
            self.assertEqual(status["dense_status"], dense_status)
            self.assertEqual(report["dense_status"], dense_status)
            self.assertEqual(status["dense_indexed_at"], 0)
            self.assertEqual(report["dense_indexed_at"], 0)

    def test_real_loopback_refresh_populates_ann_and_enables_rrf_semantic_hit(self):
        def embeddings(payload):
            data = []
            for index, text in enumerate(payload.get("input") or []):
                semantic = "월 사용료 안내" in text or "이번달 청구금액" in text
                data.append(
                    {"index": index, "embedding": [1.0, 0.0] if semantic else [-1.0, 0.0]}
                )
            return {"data": data}

        with tempfile.TemporaryDirectory() as tmp, _local_embedding_server(embeddings) as url:
            root = Path(tmp)
            self._seed(root)
            conn = KG._connect_kg(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
            KG.write_meta(conn, "last_indexed_at", "12345")
            entity_count = conn.execute("SELECT COUNT(*) FROM kg_entities").fetchone()[0]
            with mock.patch.object(KG, "DENSE_EMBEDDING_URL", url):
                result = KG.refresh_dense_index(conn, root, batch_size=3)
                ranked = KG._query_knowledge_ranked("이번달 청구금액", state_root=root)
                bundle = KG.retrieve_knowledge_bundle("이번달 청구금액", state_root=root)
            conn.close()

            dense = sqlite3.connect(root / KG.DENSE_INDEX_DB_NAME)
            try:
                vector_count = dense.execute("SELECT COUNT(*) FROM dense_vectors").fetchone()[0]
                bucket_count = dense.execute("SELECT COUNT(*) FROM ann_buckets").fetchone()[0]
                version = dense.execute(
                    "SELECT value FROM dense_meta WHERE key='index_version'"
                ).fetchone()[0]
                watermark = dense.execute(
                    "SELECT value FROM dense_meta WHERE key='watermark'"
                ).fetchone()[0]
            finally:
                dense.close()

            self.assertEqual(result["status"], "indexed")
            self.assertEqual(result["indexed"], entity_count)
            self.assertEqual(vector_count, entity_count)
            self.assertEqual(bucket_count, entity_count * KG.ANN_BANDS)
            self.assertEqual(version, KG.DENSE_INDEX_VERSION)
            self.assertEqual(watermark, "12345")
            self.assertEqual(ranked["bm25_count"], 0)
            self.assertGreater(ranked["dense_count"], 0)
            self.assertIn("ent:semantic-only", ranked["candidates"])
            self.assertEqual(bundle["search_mode"], KG.SEARCH_MODE_RRF)
            self.assertTrue(any("월 사용료 안내" in fact for fact in bundle["facts"]))

    def test_dense_version_mismatch_closes_to_bm25_without_embedding_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed(root)
            kg = KG._connect_kg(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
            KG.write_meta(kg, "last_dense_status", "indexed:1")
            KG.write_meta(kg, "last_dense_indexed_at", "123")
            kg.close()
            dense = KG._connect_dense_index(root)
            dense.execute(
                "INSERT INTO dense_meta(key,value) VALUES('index_version','wrong-version')"
            )
            dense.execute("INSERT INTO dense_meta(key,value) VALUES('watermark','123')")
            dense.commit()
            dense.close()

            with mock.patch.object(
                KG,
                "_local_dense_embeddings",
                side_effect=AssertionError("version mismatch must fail before embedding"),
            ) as embeddings:
                ranked = KG._query_knowledge_ranked("최연우", state_root=root)
                bundle = KG.retrieve_knowledge_bundle("최연우", state_root=root)
            embeddings.assert_not_called()
            self.assertEqual(ranked["search_mode"], KG.SEARCH_MODE_BM25_ONLY)
            self.assertEqual(ranked["dense_count"], 0)
            self.assertEqual(bundle["search_mode"], KG.SEARCH_MODE_BM25_ONLY)

    def test_reindex_all_calls_dense_refresh_as_an_isolated_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = KG._connect_kg(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
            KG.ensure_seeded(conn)
            with mock.patch.multiple(
                KG,
                index_topic_entities=mock.DEFAULT,
                index_topic_relations=mock.DEFAULT,
                index_chat_entities=mock.DEFAULT,
                index_person_entities=mock.DEFAULT,
                index_membership_relations=mock.DEFAULT,
                prune_indexed_entities=mock.DEFAULT,
                _merge_seed_rooms=mock.DEFAULT,
                attach_ledger_evidence=mock.DEFAULT,
            ), mock.patch.object(
                KG,
                "refresh_dense_index",
                side_effect=RuntimeError("dense unavailable"),
            ) as refresh:
                KG._reindex_all(conn, root, cycle_started_at=1)
            refresh.assert_called_once_with(conn, root)
            self.assertEqual(KG.read_meta(conn, "last_index_error"), "")
            self.assertIn("dense unavailable", KG.read_meta(conn, "last_dense_status"))
            conn.close()

    def test_reindex_all_graph_failure_still_runs_dense_without_success_stamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = KG._connect_kg(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
            KG.ensure_seeded(conn)
            KG.write_meta(conn, "last_indexed_at", "12345")
            KG.write_meta(conn, "last_snapshot_status", "previous_snapshot")
            with mock.patch.object(
                KG,
                "index_topic_entities",
                side_effect=RuntimeError("graph unavailable"),
            ), mock.patch.multiple(
                KG,
                index_topic_relations=mock.DEFAULT,
                index_chat_entities=mock.DEFAULT,
                index_person_entities=mock.DEFAULT,
                index_membership_relations=mock.DEFAULT,
                prune_indexed_entities=mock.DEFAULT,
                _merge_seed_rooms=mock.DEFAULT,
                attach_ledger_evidence=mock.DEFAULT,
            ), mock.patch.object(
                KG,
                "refresh_dense_index",
                side_effect=RuntimeError("dense unavailable"),
            ) as refresh:
                KG._reindex_all(conn, root, cycle_started_at=1)

            refresh.assert_called_once_with(conn, root)
            index_error = KG.read_meta(conn, "last_index_error")
            self.assertIn("topics: RuntimeError: graph unavailable", index_error)
            self.assertNotIn("dense unavailable", index_error)
            self.assertEqual(KG.read_meta(conn, "last_indexed_at"), "12345")
            self.assertEqual(KG.read_meta(conn, "last_snapshot_status"), "previous_snapshot")
            self.assertIn("dense unavailable", KG.read_meta(conn, "last_dense_status"))
            conn.close()

    def test_empty_graph_marks_dense_empty_without_creating_dense_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = KG._connect_kg(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM kg_entities").fetchone()[0], 0)
            result = KG.refresh_dense_index(conn, root)
            status = KG.read_meta(conn, "last_dense_status")
            indexed_at = KG.read_meta(conn, "last_dense_indexed_at")
            conn.close()
            self.assertEqual(result["status"], "empty")
            self.assertEqual(status, "empty")
            self.assertEqual(indexed_at, "0")
            self.assertFalse((root / KG.DENSE_INDEX_DB_NAME).exists())

    def test_dense_status_payload_is_bounded_sanitized_and_bad_timestamp_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = KG._connect_kg(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
            KG.write_meta(conn, "last_dense_status", "unavailable:" + ("x" * 1000) + "\nextra")
            KG.write_meta(conn, "last_dense_indexed_at", "not-an-integer")
            conn.close()

            status = KG.collect_knowledge_graph_status(root / "context.sqlite3", state_root=root)
            report = KG.collect_knowledge_graph(root / "context.sqlite3")
            for payload in (status, report):
                self.assertEqual(len(payload["dense_status"]), KG.DENSE_STATUS_MAX_LENGTH)
                self.assertNotIn("\n", payload["dense_status"])
                self.assertEqual(payload["dense_indexed_at"], 0)

    def test_dense_refresh_malformed_responses_and_timeout_fail_closed(self):
        scenarios = {
            "invalid-vector": lambda payload: {
                "data": [
                    {"index": index, "embedding": []}
                    for index, _text in enumerate(payload.get("input") or [])
                ]
            },
            "invalid-format": lambda _payload: {"data": "not-a-list"},
            "count-mismatch": lambda _payload: {"data": []},
        }
        for name, response_factory in scenarios.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self._seed(root)
                conn = KG._connect_kg(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
                with _local_embedding_server(response_factory) as url, mock.patch.object(
                    KG, "DENSE_EMBEDDING_URL", url
                ):
                    result = KG.refresh_dense_index(conn, root)
                    bundle = KG.retrieve_knowledge_bundle("최연우", state_root=root)
                status = KG.read_meta(conn, "last_dense_status")
                conn.close()
                self.assertEqual(result["status"], "unavailable")
                self.assertTrue(status.startswith("unavailable:"))
                self.assertEqual(bundle["search_mode"], KG.SEARCH_MODE_BM25_ONLY)

        def slow_embeddings(payload):
            time.sleep(0.05)
            return {
                "data": [
                    {"index": index, "embedding": [1.0, 0.0]}
                    for index, _text in enumerate(payload.get("input") or [])
                ]
            }

        with tempfile.TemporaryDirectory() as tmp, _local_embedding_server(slow_embeddings) as url:
            root = Path(tmp)
            self._seed(root)
            conn = KG._connect_kg(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
            with mock.patch.object(KG, "DENSE_EMBEDDING_URL", url), mock.patch.object(
                KG, "DENSE_EMBEDDING_TIMEOUT_SECONDS", 0.01
            ), mock.patch.object(
                KG, "DENSE_EMBEDDING_FIRST_ATTEMPT_TIMEOUT_SECONDS", 0.01
            ):
                result = KG.refresh_dense_index(conn, root)
                bundle = KG.retrieve_knowledge_bundle("최연우", state_root=root)
            status = KG.read_meta(conn, "last_dense_status")
            conn.close()
            self.assertEqual(result["status"], "unavailable")
            self.assertIn("local dense embedding unavailable", status)
            self.assertEqual(bundle["search_mode"], KG.SEARCH_MODE_BM25_ONLY)

    def test_empty_dense_entity_text_fails_closed_before_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = KG._connect_kg(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
            conn.execute(
                "INSERT INTO kg_entities (entity_id, name, category, aliases_json, description,"
                " key_facts_json, importance, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                ("ent:blank", "", "entity", "[]", "", "[]", 1, 1),
            )
            conn.commit()
            with mock.patch.object(KG.urllib.request, "urlopen") as urlopen:
                result = KG.refresh_dense_index(conn, root)
                bundle = KG.retrieve_knowledge_bundle("anything", state_root=root)
            urlopen.assert_not_called()
            self.assertEqual(result["status"], "unavailable")
            self.assertIn("dense embedding input must be non-empty", KG.read_meta(conn, "last_dense_status"))
            self.assertEqual(bundle["search_mode"], KG.SEARCH_MODE_BM25_ONLY)
            conn.close()

    def test_non_loopback_dense_url_is_rejected_before_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed(root)
            conn = KG._connect_kg(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
            with mock.patch.object(
                KG, "DENSE_EMBEDDING_URL", "https://example.com/v1/embeddings"
            ), mock.patch.object(KG.urllib.request, "urlopen") as urlopen:
                result = KG.refresh_dense_index(conn, root)
                bundle = KG.retrieve_knowledge_bundle("최연우", state_root=root)
            urlopen.assert_not_called()
            self.assertEqual(result["status"], "unavailable")
            self.assertIn("loopback-local", KG.read_meta(conn, "last_dense_status"))
            self.assertEqual(bundle["search_mode"], KG.SEARCH_MODE_BM25_ONLY)
            conn.close()


class TripleSchemaTests(unittest.TestCase):
    def test_seed_relations_fill_ere_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = KG._connect_kg(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
            try:
                KG.ensure_seeded(conn)
                row = conn.execute(
                    "SELECT subject_id, relation_type, object_id FROM kg_relations LIMIT 1"
                ).fetchone()
                self.assertTrue(row[0])
                self.assertTrue(row[1])
                self.assertTrue(row[2])
                message_nodes = conn.execute(
                    "SELECT entity_id FROM kg_entities WHERE entity_id LIKE 'message:%'"
                ).fetchall()
                self.assertEqual(message_nodes, [])
            finally:
                conn.close()
        self.assertFalse(KG._is_graph_entity_id("message:raw:1"))
        self.assertTrue(KG._time_bucket_id("2026-09-20T12:00:00+09:00").startswith("time:2026-09-20"))


class IsolatedCopyTests(unittest.TestCase):
    def test_mutated_source_retries_then_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "context.sqlite3"
            conn = sqlite3.connect(src)
            conn.execute("CREATE TABLE t (id INTEGER)")
            conn.commit()
            conn.close()
            signatures = [
                (("a", True, 1, 1, 1),),
                (("b", True, 2, 2, 2),),
                (("c", True, 3, 3, 3),),
                (("c", True, 3, 3, 3),),
            ]

            def fake_signature(_path):
                return signatures.pop(0)

            dest = Path(tmp) / "copy"
            dest.mkdir()
            with mock.patch.object(KG, "_snapshot_signature", side_effect=fake_signature):
                copied = KG._copy_consistent_sqlite_replica(src, dest)
            self.assertEqual(copied, dest / src.name)
            self.assertTrue(copied.exists())
            self.assertEqual(signatures, [])


class DpoTests(unittest.TestCase):
    def test_missing_logprobs_are_eval_unavailable(self):
        report = dpo_loss_from_logprobs(
            chosen_logprobs=None,
            rejected_logprobs=[-0.2, -0.1],
            tokenizer_id="tok",
            base_model="base",
        )
        self.assertEqual(report["status"], DPO_EVAL_UNAVAILABLE)
        self.assertIsNone(report["loss"])
        self.assertEqual(report["reason"], "missing_logprobs")

    def test_standard_dpo_loss_from_token_logprobs(self):
        report = dpo_loss_from_logprobs(
            chosen_logprobs=[-0.1, -0.1],
            rejected_logprobs=[-1.0, -1.0],
            beta=0.1,
            tokenizer_id="tok",
            base_model="flash-next",
        )
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["reason"], "dpo_logprob")
        self.assertIsInstance(report["loss"], float)
        self.assertLess(report["loss"], 0.7)

    def test_evaluate_preference_pairs_does_not_use_string_similarity(self):
        result = evaluate_preference_pairs(
            [
                {
                    "pair_id": "p1",
                    "preferred": "좋은 답",
                    "dispreferred": "나쁜 답",
                    "source": "human_authored",
                }
            ],
            tokenizer_id="tok",
            base_model="flash-next",
        )
        self.assertEqual(result["status"], DPO_EVAL_UNAVAILABLE)
        self.assertEqual(result["unavailable"], 1)
        self.assertIsNone(result["mean_loss"])


class GoldenAuditTests(unittest.TestCase):
    def test_human_authored_is_re_audited_not_auto_accepted(self):
        disconnected = {
            "quality": QUALITY_HUMAN_AUTHORED,
            "prompt": "오늘 러닝 몇 킬로 뛰었어?",
            "completion": "주식 계좌 비밀번호를 초기화하는 방법입니다.",
        }
        report = audit_golden_record(disconnected)
        self.assertFalse(report["accepted"])
        self.assertIn("disconnected_human_row", report["reasons"])

    def test_unapproved_model_answer_is_excluded(self):
        report = audit_golden_record(
            {
                "quality": QUALITY_MODEL_GENERATED,
                "prompt": "안녕",
                "completion": "안녕하세요",
            }
        )
        self.assertFalse(report["accepted"])
        self.assertIn("unapproved_model_answer", report["reasons"])

    def test_connected_human_row_can_pass(self):
        report = audit_golden_record(
            {
                "quality": QUALITY_HUMAN_AUTHORED,
                "prompt": "알리바바 클라우드 구독 어때",
                "completion": "알리바바 클라우드 구독이 가성비는 괜찮지",
            }
        )
        self.assertTrue(report["accepted"])
        summary = audit_golden_records(
            [
                {
                    "quality": QUALITY_HUMAN_AUTHORED,
                    "prompt": "알리바바 클라우드 구독 어때",
                    "completion": "알리바바 클라우드 구독이 가성비는 괜찮지",
                },
                {
                    "quality": QUALITY_MODEL_GENERATED,
                    "prompt": "q",
                    "completion": "a",
                },
            ]
        )
        self.assertEqual(summary["accepted_count"], 1)
        self.assertEqual(summary["rejected_count"], 1)


class DreamRsiPolicyTests(unittest.TestCase):
    def test_budget_and_unavailable_stop_without_promoting(self):
        stop = select_experiment_action(
            remaining_budget=0,
            candidates=["a", "b"],
        )
        self.assertEqual(stop["action"], "stop")
        self.assertEqual(stop["reason"], "budget_exhausted")
        unavailable = select_experiment_action(
            remaining_budget=3,
            last_status="eval_unavailable",
            candidates=["a"],
        )
        self.assertEqual(unavailable["action"], "stop")
        self.assertEqual(unavailable["reason"], "eval_unavailable")

    def test_loop_never_promotes_and_does_not_score_by_string_similarity(self):
        def evaluate(name: str):
            return {"status": "ok", "loss": 0.4, "candidate": name}

        result = run_fixed_budget_loop(
            candidates=["policy-a", "policy-b"],
            evaluate_fn=evaluate,
            budget=2,
        )
        self.assertFalse(result["promoted"])
        self.assertEqual(len(result["history"]), 2)
        self.assertNotIn("avg_similarity", result)
        similarity = answer_similarity("좋은데?", "좋은데?")
        self.assertAlmostEqual(similarity, 1.0, places=6)


if __name__ == "__main__":
    unittest.main()
