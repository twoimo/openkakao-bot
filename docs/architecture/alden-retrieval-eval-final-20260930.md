# Alden Korean GraphRAG retrieval evaluation — 2026-09-30

## Scope

This report evaluates 13 private-data-free synthetic queries (6 dev, 7 held-out) over 20 fixture entities and 3 relations.
The evaluator did not open a Kakao database, active queue, persistent graph, or production configuration.

`fixture-rrf` exercises the production BM25 path and production RRF merger with a fixed synthetic dense ranking. It does not invoke E5 and is not evidence of actual embedding quality. `live-rrf` is an explicit opt-in mode.

## Reproduction

```bash
python3 scripts/evaluate_alden_retrieval.py \
  --mode bm25 --mode fixture-rrf \
  --output docs/architecture/alden-retrieval-eval-final-20260930.json \
  --markdown-output docs/architecture/alden-retrieval-eval-final-20260930.md
python3 -m unittest tests.test_alden_retrieval_eval
```

## Results

Recall and nDCG are macro averages over positive queries. Forbidden@3 measures candidate leakage in the top three; Whole-context leak inspects the bounded public bundle's candidate provenance, relation endpoints, and forbidden relation text.

| Mode | Split | n | Recall@1 | nDCG@1 | Recall@3 | nDCG@3 | Forbidden@3 | Whole-context leak | p50 ms | p95 ms |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| bm25 | overall | 13 | 0.6818 | 0.7273 | 0.8182 | 0.7846 | 0.1429 | 0.1429 | 2.22 | 5.679 |
| bm25 | dev | 6 | 0.6667 | 0.6667 | 0.6667 | 0.6667 | 0.0 | 0.0 | 2.583 | 5.679 |
| bm25 | heldout | 7 | 0.7 | 0.8 | 1.0 | 0.9262 | 0.1667 | 0.1667 | 2.094 | 3.479 |
| fixture-rrf | overall | 13 | 0.8636 | 0.9091 | 1.0 | 0.9664 | 0.1429 | 0.0 | 1.482 | 3.023 |
| fixture-rrf | dev | 6 | 0.8333 | 0.8333 | 1.0 | 0.9385 | 0.0 | 0.0 | 1.382 | 1.982 |
| fixture-rrf | heldout | 7 | 0.9 | 1.0 | 1.0 | 1.0 | 0.1667 | 0.0 | 1.482 | 3.023 |

## Observed production gaps

- `rrf_weights_bounded` — The merger accepts explicit BM25/dense weights, requires a positive sum, and rejects non-finite or out-of-range values outside 0.0..4.0. Source: `scripts/auto_reply_knowledge_graph.py:3606` (`_rrf_merge`).
- `candidate_provenance_preserved` — Each returned candidate carries its source kind, event IDs, room, confirmation time, retraction state, and updated_at value. Source: `scripts/auto_reply_knowledge_graph.py:3994` (`_query_knowledge_ranked`).
- `relation_provenance_preserved` — Each returned relation carries both endpoint IDs, evidence source and room, validity interval, retraction state, and updated_at value. Source: `scripts/auto_reply_knowledge_graph.py:3994` (`_query_knowledge_ranked`).
- `public_bundle_reuses_filtered_provenance` — The public bundle now slices facts, evidence IDs, and entity/relation provenance from the same already-filtered ranked records; focus traversal cannot replace them with raw database relations. Source: `scripts/auto_reply_knowledge_graph.py:3836` (`retrieve_knowledge_bundle`).
- `joined_spacing_requires_dense_rescue` — The joined Korean form misses the spaced lexical form in BM25/keyword fallback; fixed dense ranking rescues it. Source: `scripts/auto_reply_knowledge_graph.py:3538` (`_fts_query_terms`).
- `stale_nonretracted_candidate_remains` — Recency breaks equal-score ties, but an older non-retracted alias remains eligible without an explicit valid_to or stable identity link; the evaluator does not guess that the two aliases are identical. Source: `scripts/auto_reply_knowledge_graph.py:3994` (`_query_knowledge_ranked`). Modes: bm25, fixture-rrf.

## Exact limitations

- The corpus is synthetic and bounded; it does not estimate production traffic prevalence.
- Offline fixture RRF validates fusion, filtering, provenance aggregation, and ranking metrics. It does not validate the E5 encoder, ANN recall, or live model latency.
- Search latency here measures a tiny temporary SQLite graph in the current process. It is a regression signal, not an installed-app or production percentile.
- Host load averages during this run were [10.08, 11.33, 12.51] at start and [10.08, 11.33, 12.51] at end across 18 logical CPUs; no GPU attribution was collected.
- Candidate, relation, and per-fact provenance are structured in the public bundle. Whole-context leakage checks bundle relation endpoint IDs and forbidden relation text; free-form entity fact text is linked through its candidate provenance.
- A live dense run can be scheduled after competing GPU benchmarks finish: add `--mode live-rrf`. That mode writes only a temporary index but invokes the loopback embedding adapter.
