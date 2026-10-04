import json
import math
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import evaluate_alden_retrieval as EVAL


FIXTURE_DIR = ROOT / "tests" / "fixtures" / "alden-retrieval"


class MetricTests(unittest.TestCase):
    def test_recall_and_graded_ndcg(self):
        relevant = {"a": 3, "b": 1}
        ranked = ["b", "noise", "a"]
        self.assertEqual(EVAL.recall_at_k(ranked, relevant, 1), 0.5)
        self.assertEqual(EVAL.recall_at_k(ranked, relevant, 3), 1.0)
        expected_dcg = 1.0 + 7.0 / math.log2(4)
        ideal_dcg = 7.0 + 1.0 / math.log2(3)
        self.assertAlmostEqual(EVAL.ndcg_at_k(ranked, relevant, 3), expected_dcg / ideal_dcg)

    def test_negative_queries_do_not_inflate_positive_metrics(self):
        self.assertIsNone(EVAL.recall_at_k(["a"], {}, 1))
        self.assertIsNone(EVAL.ndcg_at_k(["a"], {}, 1))

    def test_rrf_tuning_uses_dev_and_reports_heldout_separately(self):
        def row(split, relevant, bm25, dense):
            return {
                "split": split,
                "relevant": {relevant: 3},
                "forbidden": [],
                "bm25_candidates": bm25,
                "dense_candidates": dense,
                "candidate_provenance": [
                    {"entity_id": entity_id, "updated_at": 0}
                    for entity_id in dict.fromkeys(bm25 + dense)
                ],
            }

        dev = row(
            "dev",
            "z-lexical",
            ["z-lexical", "a-semantic"],
            ["a-semantic", "z-lexical"],
        )
        heldout = row(
            "heldout",
            "a-semantic",
            ["z-lexical", "a-semantic"],
            ["a-semantic", "z-lexical"],
        )
        tuning = EVAL._tune_live_rrf_weights([dev, heldout], (1, 3))
        self.assertEqual(tuning["tuning_split"], "dev")
        self.assertEqual(tuning["tuning_queries"], 1)
        self.assertEqual(tuning["heldout_queries"], 1)
        self.assertGreater(
            tuning["selected_weights"]["bm25"], tuning["selected_weights"]["dense"]
        )
        self.assertEqual(
            tuning["selected_heldout"]["metrics"]["1"]["macro_recall"], 0.0
        )


class FixtureContractTests(unittest.TestCase):
    def test_fixture_is_bounded_synthetic_and_split(self):
        corpus, judgments = EVAL.load_fixture(FIXTURE_DIR)
        self.assertTrue(corpus["synthetic"])
        self.assertEqual(len(corpus["entities"]), 20)
        self.assertEqual(len(corpus["relations"]), 3)
        self.assertEqual(
            {query["split"] for query in judgments["queries"]},
            {"dev", "heldout"},
        )
        self.assertEqual(len(judgments["queries"]), 13)
        serialized = json.dumps([corpus, judgments], ensure_ascii=False)
        self.assertNotIn("db:", serialized)
        self.assertNotIn("reply-evidence", serialized)

    def test_non_synthetic_evidence_id_fails_closed(self):
        corpus = json.loads((FIXTURE_DIR / "corpus.json").read_text(encoding="utf-8"))
        queries = json.loads((FIXTURE_DIR / "queries.json").read_text(encoding="utf-8"))
        corpus["entities"][0]["evidence"]["source_event_ids"] = ["db:real-looking"]
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp)
            (fixture / "corpus.json").write_text(json.dumps(corpus), encoding="utf-8")
            (fixture / "queries.json").write_text(json.dumps(queries), encoding="utf-8")
            with self.assertRaises(EVAL.FixtureError):
                EVAL.load_fixture(fixture)

    def test_snapshot_rejects_duplicate_overflow_and_noncanonical_identities(self):
        corpus,queries=EVAL.load_fixture(FIXTURE_DIR.parent/'alden-retrieval-v2')
        malformed=[]
        for value in ('0','01','４２',str(2**63),True):
            row=deepcopy(corpus);row['snapshot']['rooms'][0]['chat_id']=value;malformed.append(row)
        row=deepcopy(corpus);row['snapshot']['rooms'].append(deepcopy(row['snapshot']['rooms'][0]));malformed.append(row)
        row=deepcopy(corpus);row['snapshot']['messages'].append(deepcopy(row['snapshot']['messages'][0]));malformed.append(row)
        row=deepcopy(corpus);row['snapshot']['messages'][0]['is_self']='false';malformed.append(row)
        with tempfile.TemporaryDirectory() as tmp:
            fixture=Path(tmp);(fixture/'queries.json').write_text(json.dumps(queries))
            for corpus in malformed:
                (fixture/'corpus.json').write_text(json.dumps(corpus))
                with self.subTest(snapshot=corpus['snapshot']['rooms'][0]['chat_id']),self.assertRaises(EVAL.FixtureError):EVAL.load_fixture(fixture)


class PublishedSnapshotEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report=EVAL.evaluate_fixture(FIXTURE_DIR.parent/'alden-retrieval-v2',modes=('bm25','fixture-rrf'))

    def test_numeric_rooms_and_temporal_quotes_use_real_public_bundle_filters(self):
        checked=0
        for result in self.report['modes']['fixture-rrf']['query_results']:
            if 'snapshot' not in ' '.join(result['tags']):continue
            checked+=1
            self.assertFalse(result['violations'],result['id'])
            self.assertFalse(result['public_bundle']['boundary_errors'],result['id'])
            self.assertEqual(result['public_bundle']['context_recall'],1 if result['relevant'] else None,result['id'])
        self.assertGreaterEqual(checked,10)

    def test_named_disconnected_subjects_survive_the_neighborhood_budget(self):
        for result in self.report['modes']['fixture-rrf']['query_results']:
            if 'context_coverage' in result['tags']:
                self.assertEqual(result['public_bundle']['context_recall'],1,result['id'])

    def test_text_budget_preserves_fact_and_provenance_alignment(self):
        corpus,queries=EVAL.load_fixture(FIXTURE_DIR)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);EVAL._write_synthetic_graph(root,corpus)
            query=next(q for q in queries['queries'] if q['id']=='dev-spacing-joined')
            result=EVAL._evaluate_query(root,corpus,query,mode='fixture-rrf',ks=(1,3),policy={'max_context_chars':256})
            bundle=result['public_bundle'];self.assertLessEqual(bundle['context_chars'],256);self.assertFalse(bundle['boundary_errors'])
            self.assertEqual(len(bundle['facts']),len(bundle['fact_provenance']))
            for policy in ({'max_context_chars':True},{'candidate_limit':129},{'rrf_k':0}):
                with self.assertRaises(ValueError):EVAL._evaluate_query(root,corpus,query,mode='fixture-rrf',ks=(1,3),policy=policy)

    def test_policy_selection_does_not_use_heldout_to_choose(self):
        corpus,queries=EVAL.load_fixture(FIXTURE_DIR)
        dev=next(q for q in queries['queries'] if q['split']=='dev')
        held=next(q for q in queries['queries'] if q['split']=='heldout')
        seen=[];template=deepcopy(self.report['modes']['fixture-rrf']['query_results'][0])
        def probe(root,corpus,q,*,mode,ks,policy):
            seen.append(q['split']);row=deepcopy(template);row['id']=q['id'];row['split']=q['split']
            row['public_bundle']['context_recall']=int(policy['candidate_limit']==(12 if q['split']=='dev' else 80))
            return row
        with mock.patch.object(EVAL,'_evaluate_query',side_effect=probe):
            tuning=EVAL._tune_retrieval_policy(Path('/synthetic'),corpus,{'queries':[dev,held]},(1,3))
        self.assertEqual(tuning['selected_policy']['candidate_limit'],12)
        self.assertEqual(seen.count('heldout'),1)


class ProductionPathEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = EVAL.evaluate_fixture(
            FIXTURE_DIR,
            modes=("bm25", "fixture-rrf"),
            ks=(1, 3, 5),
        )

    def result(self, mode, query_id):
        return next(
            item
            for item in self.report["modes"][mode]["query_results"]
            if item["id"] == query_id
        )

    def test_modes_record_exact_dense_scope(self):
        bm25 = self.report["modes"]["bm25"]
        fixture_rrf = self.report["modes"]["fixture-rrf"]
        self.assertFalse(bm25["scope"]["encoder_invoked"])
        self.assertFalse(fixture_rrf["scope"]["encoder_invoked"])
        self.assertEqual(
            fixture_rrf["scope"]["dense_ranking_source"],
            "fixed_synthetic_fixture",
        )
        self.assertEqual(fixture_rrf["overall"]["search_mode_counts"], {"rrf": 13})
        self.assertEqual(bm25["overall"]["search_mode_counts"], {"bm25_only": 13})

    def test_fixed_dense_ranking_improves_bounded_recall(self):
        bm25 = self.report["modes"]["bm25"]["overall"]["metrics"]
        fixture_rrf = self.report["modes"]["fixture-rrf"]["overall"]["metrics"]
        self.assertGreaterEqual(bm25["1"]["macro_recall"], 0.6818)
        self.assertGreaterEqual(fixture_rrf["1"]["macro_recall"], 0.8636)
        self.assertGreaterEqual(bm25["3"]["macro_recall"], 0.8182)
        self.assertEqual(fixture_rrf["3"]["macro_recall"], 1.0)
        self.assertGreaterEqual(self.report["offline_comparison"]["3"]["macro_recall"], 0.0)

    def test_joined_spacing_requires_dense_rescue(self):
        bm25 = self.result("bm25", "dev-spacing-joined")
        fixture_rrf = self.result("fixture-rrf", "dev-spacing-joined")
        self.assertEqual(bm25["metrics"]["1"]["recall"], 0.0)
        self.assertEqual(fixture_rrf["metrics"]["1"]["recall"], 1.0)

    def test_room_and_participant_homonyms_stay_separate(self):
        room = self.result("fixture-rrf", "dev-homonym-room-isolation")
        participant = self.result("fixture-rrf", "heldout-participant-disambiguation")
        prefix = self.result("fixture-rrf", "heldout-room-prefix-isolation")
        self.assertEqual(room["candidates"][0], "person:알파방:minsu-alpha")
        self.assertNotIn("person:베타방:minsu-beta", room["candidates"])
        self.assertEqual(participant["candidates"], ["person:알파방:dev-001"])
        self.assertNotIn("person:알파방-스터디:younghee-study", prefix["candidates"])

    def test_candidate_filters_close_leaks_and_keep_uncertain_freshness_visible(self):
        observed = {item["code"] for item in self.report["production_observations"]}
        self.assertIn("stale_nonretracted_candidate_remains", observed)
        self.assertFalse(
            {
                "retracted_entity_not_filtered",
                "entity_provenance_room_not_scoped",
                "entity_time_scope_not_applied",
            }
            & observed
        )
        for query_id in (
            "heldout-retracted-entity",
            "heldout-provenance-room-leak",
            "heldout-relation-time-scope",
        ):
            result = self.result("fixture-rrf", query_id)
            self.assertFalse(result["violations"], query_id)
        latest = self.result("fixture-rrf", "heldout-latest-entity")
        self.assertTrue(
            any(item["code"] == "forbidden_entity_returned" for item in latest["violations"])
        )

    def test_time_filter_removes_expired_relation_and_old_entity(self):
        result = self.result("fixture-rrf", "heldout-relation-time-scope")
        relation_text = "\n".join(result["relation_facts"])
        self.assertIn("10월 15일 출시가 현재 유효함", relation_text)
        self.assertNotIn("9월 30일 출시는 철회됨", relation_text)
        self.assertNotIn("time:release:a-old", result["candidates"])
        self.assertEqual(len(result["relation_provenance"]), 1)
        relation = result["relation_provenance"][0]
        self.assertEqual(relation["target_id"], "time:release:z-current")
        self.assertEqual(
            relation["source_event_ids"], ["synthetic:relation:release:current"]
        )
        self.assertFalse(relation["retracted"])
        self.assertTrue(relation["provenance_valid"])

    def test_whole_context_leak_metric_includes_relation_endpoints_and_text(self):
        result = {
            "relevant": {},
            "forbidden": ["secret"],
            "expected_evidence_ids": [],
            "metrics": {"3": {"recall": None, "ndcg": None, "forbidden_hits": []}},
            "violations": [
                {"code": "forbidden_context_entity_returned", "values": ["secret"]}
            ],
            "public_bundle": {"boundary_errors": []},
            "search_mode": "rrf",
            "elapsed_ms": 1.0,
        }
        aggregate = EVAL._aggregate([result], (3,))
        self.assertEqual(aggregate["metrics"]["3"]["forbidden_query_rate"], 0.0)
        self.assertEqual(aggregate["whole_context_forbidden_query_rate"], 1.0)

    def test_public_bundle_facts_and_provenance_share_one_boundary(self):
        for mode in ("bm25", "fixture-rrf"):
            mode_report = self.report["modes"][mode]
            self.assertEqual(
                mode_report["overall"]["public_bundle_boundary_pass_rate"], 1.0
            )
            for result in mode_report["query_results"]:
                bundle = result["public_bundle"]
                self.assertFalse(bundle["boundary_errors"], result["id"])
                self.assertEqual(len(bundle["facts"]), len(bundle["fact_provenance"]))
                self.assertEqual(
                    len(bundle["facts"]),
                    len(bundle["candidate_provenance"])
                    + len(bundle["relation_provenance"]),
                )

    def test_markdown_keeps_actual_scope_and_commands(self):
        markdown = EVAL.render_markdown(self.report)
        self.assertIn("13 private-data-free synthetic queries", markdown)
        self.assertIn("--mode bm25 --mode fixture-rrf", markdown)
        self.assertIn("does not invoke E5", markdown)
        self.assertIn("candidate_provenance_preserved", markdown)
        self.assertIn("relation_provenance_preserved", markdown)
        self.assertIn("public_bundle_reuses_filtered_provenance", markdown)
        self.assertIn("Whole-context leak", markdown)


if __name__ == "__main__":
    unittest.main()
