# Actual encrypted snapshots and index catch-up — 2026-09-30

The installed **Alden 0.1.6** CLI was exercised against the real encrypted
KakaoTalk database, writing only a private copy of Alden's context mirror.
The original source database was never opened by SQLite in these paths.
The [aggregate receipt](alden-encrypted-snapshot-20260930.json) contains no
messages, author names, attachment contents, raw room IDs or encryption keys.

| Observation | Result | Scope |
| --- | --- | --- |
| Encrypted source DB / copied WAL | 874,995,712 / 4,676,208 bytes | One accepted DB+WAL snapshot |
| Snapshot copy / SQLCipher open | 42.235 / 30.234 ms | One sample; no paired baseline |
| Encrypted snapshot integrity | `quick_check=ok`, SQLCipher 4.6.1, read-only and `query_only=1` | 10.007 s integrity scan; private replica removed |
| Installed incremental sync | 8 successful commands across 3 enrolled rooms | Initial six empty polls: 0.246–0.417 s |
| Real new-message catch-up | 4 events and 4 context rows in 0.535 s | Naturally arriving traffic; no test message |
| Immediate recheck | 0 additional / duplicate events in 0.253 s | Same private destination and advanced cursor |
| Existing production mirror | 4/4 events, digests and context rows matched | Independent consistent-copy readback; probe did not write it |
| Existing mirror receipt age | 33.271 s min / 37.625 s median / 40.806 s max, n=4 | `created_at − sent_at`; not SQLite insertion latency |
| Installed GraphRAG refresh | 6.522 s, process peak RSS 97,959,936 bytes | One coordinated local refresh; engine memory excluded |
| Graph / Dense readback | Both `quick_check=ok`; matching watermark | 50 E5 vectors, 384 dimensions, exact revision below |
| Post-refresh backend | Healthy, 3/3 enrolled rooms ready | Dated persisted status; no worker restart |

The graph was 4,689 seconds old at its pre-refresh readback and exceeded the
existing 300-second stale threshold. The refresh used the **installed** graph
module and pinned CPython 3.11.16, under the existing exclusive reindex lock.
It preserved enrollment and queue identities, and used the existing local E5
service, `mlx-community/multilingual-e5-small-mlx@5030c7625865046d350eeea28f427d80353d0ac0`.
Its watermark became `1790777937`. This confirms a one-shot catch-up, not
continuous freshness, retrieval quality or installed graph navigation.

Both prior index stores, their WAL files when present, and integrity-checked
receipts are preserved in the private backup directory:
`~/Library/Application Support/openkakao/install-backups/alden-graph/20260930T1416Z-avndokmy/`.
The measurement did not send messages, replace room workers or load a new model.
The installed app still passed deep, strict code-signature verification afterward.

## Reproducing the bounded encrypted inspection

[The opt-in Cargo example](../../examples/alden_encrypted_snapshot.rs) includes
the production cloner and SQLCipher opener unchanged. Its added inspection stays
inside those modules; it adds no key-export or arbitrary-SQL product API.
Create a private JSON file containing `rooms`, with one to three exact enrolled
`chat_id` and existing positive `checkpoint_log_id` values. A zero cursor and
duplicate IDs are rejected before source discovery. No conversation content is
needed in this input.

Build with `cargo build --locked --release --example alden_encrypted_snapshot`.
Run `target/release/examples/alden_encrypted_snapshot /absolute/private-input.json`
with a private `TMPDIR` and an external 90-second subprocess timeout. Each poll
is capped at 128 rows; the example never ingests or exports those rows. The
fixture for source-path rejection and enforced query-only access passed 1/1.

Source DB/WAL and mirror metadata changed while their existing writers were
active. Those observations do not attribute the changes or establish zero
contention. These paths perform no original SQLite connection, journal-mode
change or checkpoint; filesystem-level mutation provenance was not traced.
Embedding URLs are constrained to loopback by the existing code, but no
process-wide network trace was collected. There is no same-condition baseline
for claiming a latency, memory, battery or quota improvement.

The full goal remains active. Human voice, a released wake model, installed
WKWebView/Retina and physical shortcut verification, final room-worker cutover,
and the signed/notarized public release remain separate unfinished deliveries.
