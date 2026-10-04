# Alden Korean GraphRAG retrieval evaluation — 2026-09-30

## Scope

This report evaluates 13 private-data-free synthetic queries (6 dev, 7 held-out) over 20 fixture entities and 3 relations.
The evaluator did not open a Kakao database, active queue, persistent graph, or production configuration.

`fixture-rrf` exercises the production BM25 path and production RRF merger with a fixed synthetic dense ranking. It does not invoke E5 and is not evidence of actual embedding quality. `live-rrf` is an explicit opt-in mode.

## Reproduction

```bash
python3 scripts/evaluate_alden_retrieval.py \
  --mode bm25 --mode fixture-rrf --mode live-rrf \
  --output docs/architecture/alden-retrieval-eval-after-20260930.json \
  --markdown-output docs/architecture/alden-retrieval-eval-after-20260930.md
python3 -m unittest tests.test_alden_retrieval_eval
```

## Results

Recall and nDCG are macro averages over positive queries. Forbidden@3 measures candidate leakage in the top three; Whole-context leak also inspects every returned candidate, relation endpoint, and forbidden relation text.

| Mode | Split | n | Recall@1 | nDCG@1 | Recall@3 | nDCG@3 | Forbidden@3 | Whole-context leak | p50 ms | p95 ms |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| bm25 | overall | 13 | 0.6818 | 0.7273 | 0.8182 | 0.7846 | 0.1429 | 0.1429 | 1.716 | 5.811 |
| bm25 | dev | 6 | 0.6667 | 0.6667 | 0.6667 | 0.6667 | 0.0 | 0.0 | 1.798 | 5.811 |
| bm25 | heldout | 7 | 0.7 | 0.8 | 1.0 | 0.9262 | 0.1667 | 0.1667 | 1.658 | 2.863 |
| fixture-rrf | overall | 13 | 0.8636 | 0.9091 | 1.0 | 0.9664 | 0.1429 | 0.1429 | 1.875 | 30.094 |
| fixture-rrf | dev | 6 | 0.8333 | 0.8333 | 1.0 | 0.9385 | 0.0 | 0.0 | 1.649 | 14.203 |
| fixture-rrf | heldout | 7 | 0.9 | 1.0 | 1.0 | 1.0 | 0.1667 | 0.1667 | 2.836 | 30.094 |
| live-rrf | overall | 13 | 0.8636 | 0.9091 | 0.9091 | 0.9091 | 0.1429 | 0.1429 | 25.694 | 29.97 |
| live-rrf | dev | 6 | 0.8333 | 0.8333 | 0.8333 | 0.8333 | 0.0 | 0.0 | 26.206 | 26.988 |
| live-rrf | heldout | 7 | 0.9 | 1.0 | 1.0 | 1.0 | 0.1667 | 0.1667 | 24.415 | 29.97 |

## Live E5 readback

The existing loopback service reported `mlx-community/multilingual-e5-small-mlx@5030c7625865046d350eeea28f427d80353d0ac0`. The evaluator indexed 23 synthetic/product-seed entities in an isolated temporary database in 470.345 ms without restarting the service.
Against BM25, live RRF quality deltas were {'1': {'macro_recall': 0.1818, 'macro_ndcg': 0.1818}, '3': {'macro_recall': 0.0909, 'macro_ndcg': 0.1245}, '5': {'macro_recall': 0.0909, 'macro_ndcg': 0.1245}}; latency deltas were {'p50': 23.978, 'p95': 24.159} ms (ratios {'p50': 14.973, 'p95': 5.157}).
RRF weight selection used only 6 dev queries. It selected {'bm25': 1.0, 'dense': 1.0}; the separate 7-query held-out result is {'1': {'macro_recall': 0.9, 'macro_ndcg': 1.0, 'forbidden_query_rate': 0.0}, '3': {'macro_recall': 1.0, 'macro_ndcg': 1.0, 'forbidden_query_rate': 0.1667}, '5': {'macro_recall': 1.0, 'macro_ndcg': 1.0, 'forbidden_query_rate': 0.1667}}. This bounded selection is recorded as analysis only and does not by itself promote production defaults.

## Observed production gaps

- `rrf_weights_bounded` — The merger accepts explicit BM25/dense weights, requires a positive sum, and rejects non-finite or out-of-range values outside 0.0..4.0. Source: `scripts/auto_reply_knowledge_graph.py:3606` (`_rrf_merge`).
- `candidate_provenance_preserved` — Each returned candidate carries its source kind, event IDs, room, confirmation time, retraction state, and updated_at value. Source: `scripts/auto_reply_knowledge_graph.py:4013` (`_query_knowledge_ranked`).
- `relation_provenance_preserved` — Each returned relation carries both endpoint IDs, evidence source and room, validity interval, retraction state, and updated_at value. Source: `scripts/auto_reply_knowledge_graph.py:4013` (`_query_knowledge_ranked`).
- `joined_spacing_requires_dense_rescue` — The joined Korean form misses the spaced lexical form in BM25/keyword fallback; fixed dense ranking rescues it. Source: `scripts/auto_reply_knowledge_graph.py:3538` (`_fts_query_terms`).
- `stale_nonretracted_candidate_remains` — Recency breaks equal-score ties, but an older non-retracted alias remains eligible without an explicit valid_to or stable identity link; the evaluator does not guess that the two aliases are identical. Source: `scripts/auto_reply_knowledge_graph.py:4013` (`_query_knowledge_ranked`). Modes: bm25, fixture-rrf, live-rrf.

## Exact limitations

- The corpus is synthetic and bounded; it does not estimate production traffic prevalence.
- Offline fixture RRF validates fusion, filtering, provenance aggregation, and ranking metrics. It does not validate the E5 encoder, ANN recall, or live model latency.
- Search latency here measures a tiny temporary SQLite graph in the current process. It is a regression signal, not an installed-app or production percentile.
- Host load averages during this run were [15.46, 14.28, 14.21] at start and [15.46, 14.28, 14.21] at end across 18 logical CPUs; no GPU attribution was collected.
- Candidate and relation provenance are structured in the product result. Whole-context leakage checks relation endpoint IDs and forbidden relation text; free-form entity fact text is not independently entity-linked beyond its candidate ID.
- The live dense result is one bounded run against the already-ready loopback adapter. It writes only a temporary synthetic index and does not establish production-traffic percentiles.
