# Alden Korean GraphRAG retrieval evaluation — 2026-10-02

## Scope

This report evaluates 27 private-data-free synthetic queries (12 dev, 15 held-out) over 20 fixture entities and 3 relations.
The evaluator did not open a Kakao database, active queue, persistent graph, or production configuration.

`fixture-rrf` exercises the production BM25 path and production RRF merger with a fixed synthetic dense ranking. It does not invoke E5 and is not evidence of actual embedding quality. `live-rrf` is an explicit opt-in mode.

## Reproduction

```bash
python3 scripts/evaluate_alden_retrieval.py \
  --fixture-dir tests/fixtures/alden-retrieval-v2 --mode bm25 --mode live-rrf \
  --output docs/architecture/alden-retrieval-v2-20261002.json \
  --markdown-output docs/architecture/alden-retrieval-v2-20261002.md
python3 -m unittest tests.test_alden_retrieval_eval
```

## Results

Recall and nDCG are macro averages over positive queries. Forbidden@3 measures candidate leakage in the top three; Whole-context leak inspects the bounded public bundle's candidate provenance, relation endpoints, and forbidden relation text.

| Mode | Split | n | Recall@1 | nDCG@1 | Recall@3 | nDCG@3 | Context recall | Forbidden@3 | Whole-context leak | p50 ms | p95 ms |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| bm25 | overall | 27 | 0.8542 | 0.9167 | 0.9583 | 0.943 | 0.9167 | 0.0588 | 0.0588 | 2.061 | 2.426 |
| bm25 | dev | 12 | 0.875 | 0.9167 | 0.9167 | 0.9167 | 0.9167 | 0.0 | 0.0 | 2.174 | 2.452 |
| bm25 | heldout | 15 | 0.8333 | 0.9167 | 1.0 | 0.9692 | 0.9167 | 0.0833 | 0.0833 | 2.026 | 2.4 |
| live-rrf | overall | 27 | 0.9375 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0588 | 0.0 | 23.25 | 27.404 |
| live-rrf | dev | 12 | 0.9583 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 | 23.317 | 27.289 |
| live-rrf | heldout | 15 | 0.9167 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0833 | 0.0 | 22.8 | 27.954 |

## Live E5 readback

The existing loopback service reported `mlx-community/multilingual-e5-small-mlx@5030c7625865046d350eeea28f427d80353d0ac0`. The evaluator indexed 31 synthetic/product-seed entities in an isolated temporary database in 146.023 ms without restarting the service.
Against BM25, live RRF quality deltas were {'1': {'macro_recall': 0.0833, 'macro_ndcg': 0.0833}, '3': {'macro_recall': 0.0417, 'macro_ndcg': 0.057}, '5': {'macro_recall': 0.0417, 'macro_ndcg': 0.057}}; latency deltas were {'p50': 21.189, 'p95': 24.978} ms (ratios {'p50': 11.281, 'p95': 11.296}).
RRF weight selection used only 12 dev queries. It selected {'bm25': 1.0, 'dense': 1.0}; the separate 15-query held-out result is {'1': {'macro_recall': 0.9167, 'macro_ndcg': 1.0, 'forbidden_query_rate': 0.0}, '3': {'macro_recall': 1.0, 'macro_ndcg': 1.0, 'forbidden_query_rate': 0.0833}, '5': {'macro_recall': 1.0, 'macro_ndcg': 1.0, 'forbidden_query_rate': 0.0833}}. This bounded selection is recorded as analysis only and does not by itself promote production defaults.

## Bounded policy selection

Only the 12 dev queries selected from 81 combinations of weights, RRF k, candidate count and text budget. Selected: `{'rrf_k': 60, 'candidate_limit': 40, 'max_context_chars': 8000, 'rrf_weights': [1.0, 1.0]}`.
The 15 held-out queries were evaluated once after selection; context recall: 1.0, whole-context leak: 0.0.
Exact E5 outputs are memoized only during this policy grid. Grid replay timings exclude repeated encoder calls and must not be reported as fresh-query or production percentiles. The results table above uses unmemoized public requests.

## Observed production gaps

- `rrf_weights_bounded` — The merger accepts explicit BM25/dense weights, requires a positive sum, and rejects non-finite or out-of-range values outside 0.0..4.0. Source: `scripts/auto_reply_knowledge_graph.py:3882` (`_rrf_merge`).
- `candidate_provenance_preserved` — Each returned candidate carries its source kind, event IDs, room, confirmation time, retraction state, and updated_at value. Source: `scripts/auto_reply_knowledge_graph.py:4341` (`_query_knowledge_ranked`).
- `relation_provenance_preserved` — Each returned relation carries both endpoint IDs, evidence source and room, validity interval, retraction state, and updated_at value. Source: `scripts/auto_reply_knowledge_graph.py:4341` (`_query_knowledge_ranked`).
- `public_bundle_reuses_filtered_provenance` — The public bundle now slices facts, evidence IDs, and entity/relation provenance from the same already-filtered ranked records; focus traversal cannot replace them with raw database relations. Source: `scripts/auto_reply_knowledge_graph.py:4144` (`retrieve_knowledge_bundle`).
- `stale_nonretracted_candidate_remains` — Recency breaks equal-score ties, but an older non-retracted alias remains eligible without an explicit valid_to or stable identity link; the evaluator does not guess that the two aliases are identical. Source: `scripts/auto_reply_knowledge_graph.py:4341` (`_query_knowledge_ranked`). Modes: bm25, live-rrf.

## Exact limitations

- The corpus is synthetic and bounded; it does not estimate production traffic prevalence.
- Offline fixture RRF validates fusion, filtering, provenance aggregation, and ranking metrics. It does not validate the E5 encoder, ANN recall, or live model latency.
- Search latency here measures a tiny temporary SQLite graph in the current process. It is a regression signal, not an installed-app or production percentile.
- Host load averages during this run were [12.42, 12.76, 12.45] at start and [11.42, 12.54, 12.38] at end across 18 logical CPUs; no GPU attribution was collected.
- Candidate, relation, and per-fact provenance are structured in the public bundle. Whole-context leakage checks bundle relation endpoint IDs and forbidden relation text; free-form entity fact text is linked through its candidate provenance.
- The live dense result is one bounded run against the already-ready loopback adapter. It writes only a temporary synthetic index and does not establish production-traffic percentiles.
