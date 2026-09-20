"""Unit 4: BM25+Dense RRF retrieval, DPO logprob eval, golden re-audit."""

from __future__ import annotations

import json
import sqlite3
import tempfile
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
