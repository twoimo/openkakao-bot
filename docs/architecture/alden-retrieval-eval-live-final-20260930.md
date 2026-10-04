# Alden Korean GraphRAG retrieval evaluation — 2026-09-30

## Scope

This report evaluates 13 private-data-free synthetic queries (6 dev, 7 held-out) over 20 fixture entities and 3 relations.
The evaluator did not open a Kakao database, active queue, persistent graph, or production configuration.

`fixture-rrf` exercises the production BM25 path and production RRF merger with a fixed synthetic dense ranking. It does not invoke E5 and is not evidence of actual embedding quality. `live-rrf` is an explicit opt-in mode.

## Reproduction

```bash
python3 scripts/evaluate_alden_retrieval.py \
  --mode live-rrf \
  --output docs/architecture/alden-retrieval-eval-live-final-20260930.json \
  --markdown-output docs/architecture/alden-retrieval-eval-live-final-20260930.md
python3 -m unittest tests.test_alden_retrieval_eval
```

## Results

Recall and nDCG are macro averages over positive queries. Forbidden@3 measures candidate leakage in the top three; Whole-context leak inspects the bounded public bundle's candidate provenance, relation endpoints, and forbidden relation text.

| Mode | Split | n | Recall@1 | nDCG@1 | Recall@3 | nDCG@3 | Forbidden@3 | Whole-context leak | p50 ms | p95 ms |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| live-rrf | overall | 13 | 0.8636 | 0.9091 | 0.9091 | 0.9091 | 0.1429 | 0.0 | 24.941 | 45.865 |
| live-rrf | dev | 6 | 0.8333 | 0.8333 | 0.8333 | 0.8333 | 0.0 | 0.0 | 30.134 | 45.865 |
| live-rrf | heldout | 7 | 0.9 | 1.0 | 1.0 | 1.0 | 0.1667 | 0.0 | 21.056 | 24.941 |

## Live E5 readback

The existing loopback service reported `mlx-community/multilingual-e5-small-mlx@5030c7625865046d350eeea28f427d80353d0ac0`. The evaluator indexed 23 synthetic/product-seed entities in an isolated temporary database in 132.382 ms without restarting the service.
Against BM25, live RRF quality deltas were {}; latency deltas were {} ms (ratios {}).
RRF weight selection used only 6 dev queries. It selected {'bm25': 1.0, 'dense': 1.0}; the separate 7-query held-out result is {'1': {'macro_recall': 0.9, 'macro_ndcg': 1.0, 'forbidden_query_rate': 0.0}, '3': {'macro_recall': 1.0, 'macro_ndcg': 1.0, 'forbidden_query_rate': 0.1667}, '5': {'macro_recall': 1.0, 'macro_ndcg': 1.0, 'forbidden_query_rate': 0.1667}}. This bounded selection is recorded as analysis only and does not by itself promote production defaults.

## Observed production gaps

- `rrf_weights_bounded` — The merger accepts explicit BM25/dense weights, requires a positive sum, and rejects non-finite or out-of-range values outside 0.0..4.0. Source: `scripts/auto_reply_knowledge_graph.py:3606` (`_rrf_merge`).
- `candidate_provenance_preserved` — Each returned candidate carries its source kind, event IDs, room, confirmation time, retraction state, and updated_at value. Source: `scripts/auto_reply_knowledge_graph.py:3994` (`_query_knowledge_ranked`).
- `relation_provenance_preserved` — Each returned relation carries both endpoint IDs, evidence source and room, validity interval, retraction state, and updated_at value. Source: `scripts/auto_reply_knowledge_graph.py:3994` (`_query_knowledge_ranked`).
- `public_bundle_reuses_filtered_provenance` — The public bundle now slices facts, evidence IDs, and entity/relation provenance from the same already-filtered ranked records; focus traversal cannot replace them with raw database relations. Source: `scripts/auto_reply_knowledge_graph.py:3836` (`retrieve_knowledge_bundle`).
- `stale_nonretracted_candidate_remains` — Recency breaks equal-score ties, but an older non-retracted alias remains eligible without an explicit valid_to or stable identity link; the evaluator does not guess that the two aliases are identical. Source: `scripts/auto_reply_knowledge_graph.py:3994` (`_query_knowledge_ranked`). Modes: live-rrf.

## Exact limitations

- The corpus is synthetic and bounded; it does not estimate production traffic prevalence.
- Offline fixture RRF validates fusion, filtering, provenance aggregation, and ranking metrics. It does not validate the E5 encoder, ANN recall, or live model latency.
- Search latency here measures a tiny temporary SQLite graph in the current process. It is a regression signal, not an installed-app or production percentile.
- Host load averages during this run were [16.02, 13.41, 12.39] at start and [16.74, 13.6, 12.46] at end across 18 logical CPUs; no GPU attribution was collected.
- Candidate, relation, and per-fact provenance are structured in the public bundle. Whole-context leakage checks bundle relation endpoint IDs and forbidden relation text; free-form entity fact text is linked through its candidate provenance.
- The live dense result is one bounded run against the already-ready loopback adapter. It writes only a temporary synthetic index and does not establish production-traffic percentiles.
