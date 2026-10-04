# Installed GraphRAG helper readback — 2026-09-30

The installed Alden Python 3.11 helper queried the existing graph store through the ready, local `multilingual-e5-small-mlx` embedding service at loopback port 11236. Four public, synthetic query strings were used. No conversation facts or evidence IDs are included in this receipt; no Kakao source reindex, manual send or cloud model call was performed.

| Query | Actual mode | Elapsed | Ranked candidates | Focus nodes | Focus steps |
| --- | --- | ---: | ---: | ---: | ---: |
| AI | RRF | 1.631956 s | 40 | 21 | 2 |
| 인공지능 | RRF | 0.119357 s | 40 | 21 | 2 |
| RAG | RRF | 0.039160 s | 40 | 21 | 2 |
| 주식 | RRF | 0.041775 s | 40 | 21 | 2 |

All **4/4** queries returned `search_mode=rrf`, six bounded facts each (up to three entity facts plus three relation facts). The persisted graph status read `snapshot_status=copy_ok`, `dense_status=indexed:50`, and `stale=true`. Successful reads do not make an old index fresh. Cold/warm effects and different queries are mixed here; these four timings are not a percentile benchmark or a measured speedup. RRF mode proves that the hybrid path was used, not that every returned fact is relevant or correct. This invoked installed Python code, not a Tauri UI event or the visible drill-down camera.

The independent browser-runtime readiness probe also found pinned Browser-use **0.13.10**, Playwright **1.63.0**, Chromium installed and the `browser` binding available. That probe loads no browser and makes no navigation; installed browser execution remains a separate check.

See the [structured helper receipt](alden-graphrag-readback-20260930.json).
