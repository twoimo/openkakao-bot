# Alden worker cancellation — 2026-09-30 KST

## Source result

The Python AutoReply worker now captures one `AbortToken` from the private operator root when a job begins. Generation, link retrieval, bounded owned subprocesses, read-only send preflight and the final Python send boundary use the same token. A latch, malformed state or changed epoch cancels it permanently; a rapid stop/resume does not revive an old job.

Before a send process can start, cancellation preserves the queue row, watermark and retry state through durable deferral (`alden_global_abort`). Once the durable phase is `sending`, the worker preserves `delivery_unknown` and does not retry automatically. The child implementation added these paths and tests; parent integration additionally fixed cancellation being swallowed by the model fallback/turn guard and a durable model-call lease left waiting for its 180-second expiry.

## Call lease and transport lifetime

Already-entered blocking Python HTTP/DNS calls can outlive the cancelled waiter. The daemon's result is discarded, and its inner MLX request lease remains active until the transport unwinds. The cancellation exception carries that completion event to the durable call-lease cleanup. Cleanup runs after completion and matches the exact model key and lease token. A late cleanup cannot clear a new owner.

Cancellation neither increments model failures nor reports model-call success. Existing failure history is retained; a failure-free cancelled call releases its row. A cleanup read/write failure leaves admission fenced. The code does not forcibly stop the shared MLX server or claim that already-entered network/OS calls end immediately.

Bounded process waits and link joins check cancellation every 100 ms. This is a polling interval, not an end-to-end latency measurement: filesystem contention, scheduling, a blocking transport and owned-child cleanup can add time. Owned process groups retain the existing 0.5-second TERM grace followed by KILL when needed and bounded reap. No unrelated process is signalled.

```text
continue(job) = !latched && state_valid && current_epoch == captured_epoch
old token after stop/resume: current_epoch != captured_epoch => cancelled
clear(lease)  = transport_finished && current_lease_token == captured_lease_token
```

## Verification

- **32 tests passed in 3.667 s**: 14 worker-abort tests, 13 complete session-packager tests and five selected existing send-state tests.
- **87 tests passed in 0.664 s** across existing MLX worker, fallback lease, retry policy and turn-hold modules. This run emitted file-resource warnings; it did not fail.
- The packager already stages `alden_abort.py` as an immutable payload. The new regression confirms staged bytes and SHA-256 equal the source. The packager source needed no change.
- CI now includes the new worker-abort and DPO scorer modules. Numerical MLX tests require the separately pinned evaluation environment; no model weights are loaded by the new worker tests.
- Two other existing send tests failed with `context_freshness_unavailable` in the child run and failed identically against the pre-change HEAD worker. They are `test_scheduled_send_failure_requeues_only_before_sending_transition` and `test_post_send_failure_with_successor_stays_delivery_unknown`. They remain unresolved and are excluded from the passing counts above.

All new worker cases use temporary private state, fake generation/AX adapters and owned test children. No real model inference, KakaoTalk send, runtime cutover, live watermark mutation or shared permission/configuration change occurred. See the [source/trace receipt](alden-worker-abort-20260930.json).

## Web delegation evidence

Native child `01a0f028-561c-7c83-9264-b26e53e72766` used `chatgpt-web/gpt-5.6-sol` with `xhigh` in all five recorded turn contexts. The parent inspected the real Web application, showing `5.6 Sol / Extra High`, and launcher trace `4955aa3506c8-09fd2e04`: send accepted at `03:06:08.260Z`, response visible at `03:06:08.334Z`, turn completed at `03:16:08.889Z`. The native child completed at `03:16:09.095Z` and was closed after parent review. These are Web transport observations, not a standalone CLI or picker-only health check. A distinct Rust-fence request was delegated afterward; the completed Python request was not repeated.

## Remaining delivery boundary

The latest Python source is not deployed to the older immutable production worker runtime. The native Rust AX check is the next independent unit; its captured epoch must be relayed from Python before activation. A Python check alone cannot prevent an already-launched stale child from reaching AX after stop/resume. Final native fencing, bridge relay, idle/empty-queue preflight, controlled runtime activation and live metadata readback remain required. No live skip-rate or reply-latency improvement is inferred from these source tests.
