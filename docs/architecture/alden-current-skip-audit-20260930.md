# Current queue/skip audit — 2026-09-30

## Fixed interval and denominator

The read-only interval is **2026-09-29 14:00:00 ≤ created_at < 2026-09-30 14:00:00 KST**. It covers the three enrolled rooms' durable reply queues. At the 14:27 KST readback, the interval contained **24 jobs**, all in one room:

| Terminal reason | Jobs |
| --- | ---: |
| `stale_backlog` | 17 |
| `burst_superseded` | 7 |
| Total | 24 |

All seven supersession edges stayed inside the interval, collapsing 24 jobs into **17 terminal queue chains**. These chains are not independently identified human conversations. The 17 stale jobs' age at queue creation had median **77,888.570 s (21.64 h)** and range **11,848.156–166,952.709 s (3.29–46.38 h)**. Their median subsequent queue lifetime was **77.115 s**. These ages connect the skips to old records collected after the preceding outage; they do not establish when KakaoTalk inserted those records in its database. Historical messages were not replayed for sending.

## Current readiness and bounded tail comparison

All three room supervisors were running and ready. There were **zero unresolved send rows**, zero pending watcher IDs/gaps, and idle candidates. Local reads of the latest **100 rows per room** succeeded through the approved stable CLI. Two watcher checkpoints matched the greatest returned log ID; the third was behind exactly one self-authored row. Across all three bounded samples, **zero nonself rows** were ahead of the watcher checkpoint. This qualifies the raw 2/3 maximum-log-ID comparison and does not prove full-history ingestion completeness.

Recent successful poll-envelope intervals were **1.165, 1.480 and 1.174 s**. Previously recorded large candidate ingress ages are retained historical measurements and must not be presented as new-runtime latency. Neither poll timing nor `created_at − sent_at` measures the time KakaoTalk first inserted a local row.

The [paired abort runtime](alden-fence-activation-20260930.md) had **zero new queue jobs after activation** at this readback. The post-activation skip ratio therefore has denominator zero and is **undefined**, not an improvement to 0%. There is no measured new-turn latency or reply-quality gain yet. No test message was sent and no queue, candidate or watermark was changed.

## Remaining cause and implementation boundary

Previous startup logs had separately recorded SQLite busy and changing isolated-copy failures. The current copy consistency code includes source SHM metadata, while SQLite documents the wal-index as a transient coordination/cache structure ([WAL format](https://sqlite.org/walformat.html)). That supports investigating local SHM rebuilding while retaining strict DB/WAL coherence; it does not by itself prove that SHM caused every production outage. Implementation is delegated separately and is not claimed by this audit.

The [structured audit](alden-current-skip-audit-20260930.json) contains only counts, reason classes and timings. No room names/IDs, message bodies or credentials are included. The earlier [2026-09-27 audit](alden-skip-audit-20260927.md) uses a different fixed interval and must not be combined with this denominator.
