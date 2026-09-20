from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
STORE_PATH = SCRIPTS / "auto_reply_reference_store.py"
SEARCH_PATH = SCRIPTS / "auto_reply_reference_search.py"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class FakeEmbeddingEngine:
    model = "local-test-embedding"

    def embed(self, texts):
        vectors = []
        for text in texts:
            value = str(text)
            if "금리 전망" in value or "정책 비용" in value:
                vectors.append([1.0, 0.0, 0.0, 0.0])
            elif "금리 인상" in value:
                vectors.append([0.0, 1.0, 0.0, 0.0])
            else:
                vectors.append([0.0, 0.0, 1.0, 0.0])
        return vectors


class BrokenEmbeddingEngine:
    model = "broken-local-embedding"

    def embed(self, _texts):
        raise RuntimeError("local engine unavailable")


class ReferenceSearchTests(unittest.TestCase):
    def setUp(self):
        self.store = load("auto_reply_reference_store_search_test", STORE_PATH)
        self.search = load("auto_reply_reference_search_test", SEARCH_PATH)

    def _database(self, root: str) -> Path:
        db = Path(root) / "context.sqlite3"
        connection = sqlite3.connect(db)
        self.store.ensure_reference_schema(connection)
        connection.commit()
        connection.close()
        return db

    def _insert_pack(
        self,
        db: Path,
        *,
        pack_key: str,
        chat: str,
        chat_id: int,
        participant: str,
        started_at: str,
        body: str,
        what_text: str,
        context_message_id: int,
    ) -> int:
        connection = sqlite3.connect(db)
        blob = self.store.encode_vector_blob("\n".join([participant, what_text, body]))
        connection.execute(
            f"""
            INSERT INTO {self.store.PACK_TABLE}(
                pack_key, source, chat, chat_id, user_name, started_at, ended_at,
                start_log_id, end_log_id, message_count, image_count, quality_score,
                topics, what_text, how_text, why_text, body, vector,
                context_message_id, policy_version, created_at
            ) VALUES (?, 'live', ?, ?, ?, ?, ?, ?, ?, 3, 1, 8,
                      'investing', ?, '자료를 비교해 설명', '시장 변화 때문', ?, ?, ?, ?, ?)
            """,
            (
                pack_key,
                chat,
                chat_id,
                participant,
                started_at,
                started_at,
                context_message_id * 10,
                context_message_id * 10 + 2,
                what_text,
                body,
                blob,
                context_message_id,
                self.store.PACK_POLICY_VERSION,
                started_at,
            ),
        )
        ident = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        connection.commit()
        connection.close()
        return ident

    def _seed(self, db: Path) -> tuple[int, int, int]:
        lexical = self._insert_pack(
            db,
            pack_key="101:10:12:문승현",
            chat="부자멘토멘티",
            chat_id=101,
            participant="문승현",
            started_at="2026-09-20 10:00:00",
            what_text="금리 인상 대응",
            body="금리 인상 시 채권 가격과 현금 비중을 같이 확인한다.",
            context_message_id=11,
        )
        semantic = self._insert_pack(
            db,
            pack_key="101:20:22:성린이형",
            chat="부자멘토멘티",
            chat_id=101,
            participant="성린이형",
            started_at="2026-09-20 11:00:00",
            what_text="중앙은행 정책 비용 변화",
            body="정책 비용 상승과 시장 유동성 축소의 연결을 설명한다.",
            context_message_id=21,
        )
        other_room = self._insert_pack(
            db,
            pack_key="202:30:32:성린이형",
            chat="다른방",
            chat_id=202,
            participant="성린이형",
            started_at="2026-09-20 11:30:00",
            what_text="중앙은행 정책 비용 변화",
            body="정책 비용 상승과 시장 유동성 축소의 연결을 설명한다.",
            context_message_id=31,
        )
        return lexical, semantic, other_room

    def test_legacy_hash_vector_is_explicitly_separate_from_dense(self):
        self.assertEqual(self.store.LEGACY_HASH_VECTOR_DIM, 128)
        self.assertEqual(self.store.VECTOR_DIM, self.store.LEGACY_HASH_VECTOR_DIM)
        self.assertEqual(self.store.LEGACY_VECTOR_KIND, "legacy_lexical_hash")
        self.assertEqual(
            self.store.encode_vector_blob("테스트"),
            self.store.encode_legacy_hash_vector_blob("테스트"),
        )

    def test_missing_dense_dependencies_returns_explicit_bm25_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            db = self._database(temporary)
            lexical, _semantic, _other = self._seed(db)
            result = self.search.search_reference_packs(
                db,
                query="금리 전망",
                chat="부자멘토멘티",
                use_environment=False,
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "bm25_only")
        self.assertTrue(result["degraded"])
        self.assertEqual(result["dense"]["status"], "embedding_engine_unavailable")
        self.assertGreaterEqual(result["candidate_counts"]["bm25"], 1)
        self.assertEqual(result["candidate_counts"]["dense"], 0)
        self.assertEqual(result["rows"][0]["id"], lexical)
        self.assertEqual(result["rows"][0]["index_version"], result["index_version"])
        self.assertEqual(result["rows"][0]["watermark"], result["watermark"])
        self.assertIn("pack:", result["rows"][0]["evidence_ids"][0])
        self.assertIn("context_message:11", result["rows"][0]["evidence_ids"])

    def test_dense_and_bm25_candidates_are_independent_then_rrf_fused(self):
        engine = FakeEmbeddingEngine()
        with tempfile.TemporaryDirectory() as temporary:
            db = self._database(temporary)
            lexical, semantic, other_room = self._seed(db)
            rebuilt = self.search.rebuild_dense_index(db, embedding_engine=engine)
            self.assertTrue(rebuilt["ok"])
            result = self.search.search_reference_packs(
                db,
                query="금리 전망",
                chat="부자멘토멘티",
                participant="",
                embedding_engine=engine,
                use_environment=False,
            )
        self.assertEqual(result["mode"], "hybrid_rrf")
        self.assertFalse(result["degraded"])
        self.assertEqual(result["dense"]["status"], "active")
        by_id = {row["id"]: row for row in result["rows"]}
        self.assertIn(lexical, by_id)
        self.assertIn(semantic, by_id)
        self.assertNotIn(other_room, by_id)
        self.assertIsNotNone(by_id[lexical]["bm25_rank"])
        self.assertIsNone(by_id[semantic]["bm25_rank"])
        self.assertIsNotNone(by_id[semantic]["dense_rank"])
        self.assertGreater(result["candidate_counts"]["bm25"], 0)
        self.assertGreater(result["candidate_counts"]["dense"], 0)

    def test_filters_apply_to_both_candidate_sources(self):
        engine = FakeEmbeddingEngine()
        with tempfile.TemporaryDirectory() as temporary:
            db = self._database(temporary)
            _lexical, semantic, _other_room = self._seed(db)
            self.search.rebuild_dense_index(db, embedding_engine=engine)
            result = self.search.search_reference_packs(
                db,
                query="금리 전망",
                chat_id=101,
                participant="성린이형",
                start_time="2026-09-20 10:30:00",
                end_time="2026-09-20 11:15:00",
                embedding_engine=engine,
                use_environment=False,
            )
        self.assertEqual(result["mode"], "hybrid_rrf")
        self.assertEqual([row["id"] for row in result["rows"]], [semantic])
        self.assertEqual(result["filters"]["chat_id"], 101)
        self.assertEqual(result["filters"]["participant"], "성린이형")

    def test_stale_dense_watermark_fails_closed_to_bm25(self):
        engine = FakeEmbeddingEngine()
        with tempfile.TemporaryDirectory() as temporary:
            db = self._database(temporary)
            self._seed(db)
            self.search.rebuild_dense_index(db, embedding_engine=engine)
            connection = sqlite3.connect(db)
            connection.execute(
                f"UPDATE {self.store.PACK_TABLE} SET body = body || ' 갱신' WHERE chat_id = 101"
            )
            connection.commit()
            connection.close()
            result = self.search.search_reference_packs(
                db,
                query="금리",
                chat_id=101,
                embedding_engine=engine,
                use_environment=False,
            )
        self.assertEqual(result["mode"], "bm25_only")
        self.assertTrue(result["degraded"])
        self.assertEqual(result["dense"]["status"], "dense_index_stale")
        self.assertGreater(result["candidate_counts"]["bm25"], 0)
        self.assertEqual(result["candidate_counts"]["dense"], 0)

    def test_embedding_failure_fails_closed_to_bm25(self):
        with tempfile.TemporaryDirectory() as temporary:
            db = self._database(temporary)
            self._seed(db)
            result = self.search.search_reference_packs(
                db,
                query="금리",
                chat_id=101,
                embedding_engine=BrokenEmbeddingEngine(),
                use_environment=False,
            )
        self.assertEqual(result["mode"], "bm25_only")
        self.assertEqual(result["dense"]["status"], "embedding_engine_failed")
        self.assertGreater(result["candidate_counts"]["bm25"], 0)

    def test_embedding_endpoint_must_be_loopback(self):
        with self.assertRaises(self.search.DenseUnavailable) as raised:
            self.search.LoopbackOpenAIEmbeddingEngine(
                "https://api.openai.com/v1/embeddings", "remote-model"
            )
        self.assertEqual(raised.exception.code, "embedding_endpoint_not_loopback")

    def test_store_entrypoint_exposes_search_contract(self):
        previous_endpoint = os.environ.pop("OPENKAKAO_EMBEDDING_ENDPOINT", None)
        previous_url = os.environ.pop("OPENKAKAO_EMBEDDING_URL", None)
        previous_model = os.environ.pop("OPENKAKAO_EMBEDDING_MODEL", None)
        try:
            with tempfile.TemporaryDirectory() as temporary:
                db = self._database(temporary)
                self._seed(db)
                result = self.store.search_reference_packs(
                    db, query="금리", chat_id=101, use_environment=False
                )
            self.assertEqual(result["action"], "reference-search")
            self.assertEqual(result["mode"], "bm25_only")
        finally:
            if previous_endpoint is not None:
                os.environ["OPENKAKAO_EMBEDDING_ENDPOINT"] = previous_endpoint
            if previous_url is not None:
                os.environ["OPENKAKAO_EMBEDDING_URL"] = previous_url
            if previous_model is not None:
                os.environ["OPENKAKAO_EMBEDDING_MODEL"] = previous_model


if __name__ == "__main__":
    unittest.main()
