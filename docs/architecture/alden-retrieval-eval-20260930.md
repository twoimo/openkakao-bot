# Alden Korean GraphRAG retrieval evaluation — 2026-09-30

## Scope

This report evaluates 13 private-data-free synthetic queries (6 dev, 7 held-out) over 20 fixture entities and 3 relations.
The evaluator did not open a Kakao database, active queue, persistent graph, or production configuration.

`fixture-rrf` exercises the production BM25 path and production RRF merger with a fixed synthetic dense ranking. It does not invoke E5 and is not evidence of actual embedding quality. `live-rrf` is an explicit opt-in mode.

## Reproduction

```bash
python3 scripts/evaluate_alden_retrieval.py \
  --mode bm25 --mode fixture-rrf \
  --output docs/architecture/alden-retrieval-eval-20260930.json \
  --markdown-output docs/architecture/alden-retrieval-eval-20260930.md
python3 -m unittest tests.test_alden_retrieval_eval
```

## Results

Recall and nDCG are macro averages over positive queries. Forbidden rate is the fraction of queries with an explicit forbidden judgment that leaked at least one forbidden entity into the top-k.

| Mode | Split | n | Recall@1 | nDCG@1 | Recall@3 | nDCG@3 | Forbidden@3 | p50 ms | p95 ms |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| bm25 | overall | 13 | 0.6818 | 0.7273 | 0.8182 | 0.7846 | 0.5714 | 2.252 | 3.964 |
| bm25 | dev | 6 | 0.6667 | 0.6667 | 0.6667 | 0.6667 | 0.0 | 2.571 | 3.964 |
| bm25 | heldout | 7 | 0.7 | 0.8 | 1.0 | 0.9262 | 0.6667 | 2.207 | 2.346 |
| fixture-rrf | overall | 13 | 0.7727 | 0.8182 | 1.0 | 0.9329 | 0.5714 | 1.857 | 2.392 |
| fixture-rrf | dev | 6 | 0.8333 | 0.8333 | 1.0 | 0.9385 | 0.0 | 1.941 | 2.392 |
| fixture-rrf | heldout | 7 | 0.7 | 0.8 | 1.0 | 0.9262 | 0.6667 | 1.779 | 2.219 |
| live-rrf | overall | 13 | 0.7727 | 0.8182 | 0.9091 | 0.8755 | 0.5714 | 31.42 | 40.564 |
| live-rrf | dev | 6 | 0.8333 | 0.8333 | 0.8333 | 0.8333 | 0.0 | 30.273 | 34.011 |
| live-rrf | heldout | 7 | 0.7 | 0.8 | 1.0 | 0.9262 | 0.6667 | 32.152 | 40.564 |

## Live E5 readback

The existing loopback service reported `mlx-community/multilingual-e5-small-mlx@5030c7625865046d350eeea28f427d80353d0ac0`. The evaluator indexed 23 synthetic/product-seed entities in an isolated temporary database in 235.56 ms without restarting the service.
Against BM25, live RRF quality deltas were {'1': {'macro_recall': 0.0909, 'macro_ndcg': 0.0909}, '3': {'macro_recall': 0.0909, 'macro_ndcg': 0.0909}, '5': {'macro_recall': 0.0909, 'macro_ndcg': 0.0909}}; latency deltas were {'p50': 29.168, 'p95': 36.6} ms (ratios {'p50': 13.952, 'p95': 10.233}).

## Observed production gaps

- `rrf_equal_weight_only` — The merger accepts k but no per-retriever weights, so evaluation cannot tune w_j without a production change. Source: `scripts/auto_reply_knowledge_graph.py:3574` (`_rrf_merge`).
- `candidate_provenance_is_aggregated` — The result exposes one aggregated evidence_ids list, so a caller cannot bind each candidate to its room and timestamp. Source: `scripts/auto_reply_knowledge_graph.py:3913` (`_query_knowledge_ranked`).
- `joined_spacing_requires_dense_rescue` — The joined Korean form misses the spaced lexical form in BM25/keyword fallback; fixed dense ranking rescues it. Source: `scripts/auto_reply_knowledge_graph.py:3534` (`_fts_query_terms`).
- `retracted_entity_not_filtered` — Entity evidence is loaded after ranking, but retracted=true is not used to reject candidates. Source: `scripts/auto_reply_knowledge_graph.py:3913` (`_query_knowledge_ranked`). Modes: bm25, fixture-rrf, live-rrf.
- `freshness_not_ranked` — updated_at is not used in BM25, RRF, or final candidate ordering. Source: `scripts/auto_reply_knowledge_graph.py:3913` (`_query_knowledge_ranked`). Modes: bm25, fixture-rrf, live-rrf.
- `entity_provenance_room_not_scoped` — Room filtering uses person/chat entity IDs; a global topic from another evidence room can pass. Source: `scripts/auto_reply_knowledge_graph.py:3913` (`_query_knowledge_ranked`). Modes: bm25, fixture-rrf, live-rrf.
- `entity_time_scope_not_applied` — Relation validity is filtered, but old time entities remain eligible candidates. Source: `scripts/auto_reply_knowledge_graph.py:3913` (`_query_knowledge_ranked`). Modes: bm25, fixture-rrf, live-rrf.

## Exact limitations

- The corpus is synthetic and bounded; it does not estimate production traffic prevalence.
- Offline fixture RRF validates fusion, filtering, provenance aggregation, and ranking metrics. It does not validate the E5 encoder, ANN recall, or live model latency.
- Search latency here measures a tiny temporary SQLite graph in the current process. It is a regression signal, not an installed-app or production percentile.
- Host load averages during this run were [15.77, 14.67, 15.08] at start and [15.77, 14.67, 15.08] at end across 18 logical CPUs; no GPU attribution was collected.
- Entity evidence IDs are returned as one aggregate list. Candidate-level source, room, timestamp, and retraction correctness cannot be fully audited from the public result contract.
- The live dense result is one bounded run against the already-ready loopback adapter. It writes only a temporary synthetic index and does not establish production-traffic percentiles.
