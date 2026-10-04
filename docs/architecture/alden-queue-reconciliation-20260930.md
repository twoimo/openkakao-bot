# Alden queue reconciliation — 2026-09-30

## Result

The parent repaired one durably `sending` GeekNews job without retransmission. A fresh local KakaoTalk database read returned 66 rows and exactly one self-authored outgoing row with the same full reply, same numeric room, and a log ID after the source. Its timestamp was within the original send attempt window, **8.887 seconds** after the queue entered `sending`.

The repair changed exactly that job to `sent`, appended `local_db_confirmed`, recovery and terminal journal records, restored its reply-state digest, and persisted the confirmed GeekNews IDs and the original posting slot. **Zero new sends** occurred. Other queue jobs were unchanged. The queue passed its existing schema validator and `PRAGMA quick_check`.

A read-only aggregate preflight using the **existing enrollment's three `bind:` selectors** then passed in **2.641 seconds**: `valid=true`, `network=false`, `will_send=false`, `workers_started=false`. `ax_runtime_readiness=not_evaluated` means this is not a live AX success claim. Plain `id:` selectors were rejected for unnamed local group-room records; no binding exception or permission was invented.

## Procedure and preservation

1. Opened each queue read-only and identified the single `sending` job. The previous fixed-window audit found no jobs newly inserted between 2026-09-29 12:45 and 2026-09-30 12:45 KST; this does not imply no human conversations occurred.
2. Acquired the affected room's existing supervisor and generation locks nonblocking and verified a stopped supervisor with all children exited.
3. Re-read the exact outgoing confirmation through the TCC-approved stable CLI at `~/Library/Application Support/openkakao/bin/openkakao-cli`. The separate `~/.local/bin/openkakao-cli` is an older binary; its host-status repository-discovery error is not evidence about the stable host.
4. Backed up the queue with SQLite backup and copied the existing state/cursor/supervisor/database-watch metadata into a private `reconciliation-backups/` directory under the operator root. Ran the same change on the backup first.
5. Re-read the original row, compared every field, and used the existing `_apply_recovered_terminal` helper with an exact `event_id/status/updated_at` compare-and-swap. The confirmation checkpoint and row transition committed in one short transaction.
6. Verified all other job rows, queue integrity, reply digest, and confirmed feed IDs. Preserved the database-watch ACK/observed sets and enrollment; no cursor was advanced to a newer room tail.

The local one-shot helper and private raw preflight output remain under `/private/tmp/`; they contain operational identities and are not published. The [public receipt](alden-queue-reconciliation-20260930.json) contains only aggregate metadata.

## Runtime recovery and remaining risks

The initial readback found all three supervisors stopped for about 48 hours. The existing watchdog was `circuit_open/preflight_failed` after **552 consecutive failures**, blocked by the unresolved `sending` row.

After reconciliation, that watchdog automatically passed its prior startup gate and attempted its existing restart policy. The initial readback observed it alternate between `running` and `circuit_open/child_exited`, with **0/3 reply-ready supervisor records**. Database-watch logs contain SQLite lock contention, source-change rejection during isolated copying, and `reconcile_required:delivery_ack_uncertain`.

It subsequently recovered. **Four readbacks over 76.227 seconds** all reported the stable host's `healthy=true`, exit code zero, and **3/3 running, ready supervisors** with fresh status files. The public receipt preserves both the initial failure and this final bounded observation. No test message or new watchdog was started by the parent. This restores the observed service readiness; it does **not** establish lower skip rates, reply quality, future uptime, or the absence of the earlier lock/ACK failure paths.

The newly committed Python abort integration and the Rust send fence under review have not been activated in the immutable production worker runtime. The historical 2026-09-27 `3/3 ready` observations elsewhere in the README are dated evidence, not current health.
