# Native AX cancellation fence — 2026-09-30

## Source contract

Database-authoritative worker `local-send` accepts a paired, worker-only environment relay:

| Variable | Authority |
| --- | --- |
| `OPENKAKAO_ALDEN_ABORT_STATE_ROOT` | The original Python job token's private operator root; Rust compares it with the configured/default AutoReply root. |
| `OPENKAKAO_ALDEN_ABORT_EPOCH` | The original captured epoch, an integer in `0..2^53-1`; never a new epoch read at send time. |

Both absent preserve the existing manual/legacy path. A partial pair, nonworker caller, mismatched root or malformed epoch is rejected. The native reader mirrors the persisted Python schema: exactly `schema_version`, `epoch`, `latched`, `reason`; version 1; 4096-byte bound; 96-character reason bound; private user-owned directory and regular single-link file; no final symlink following; nonblocking file opens; malformed/duplicate fields fail closed. An absent state file is allowed only at expected epoch zero in an existing private operator root.

The authorization condition at every effect boundary is:

`valid_private_state ∧ !latched ∧ current_epoch = captured_epoch`

Stopping and resuming increments the persisted epoch, so an old token never adopts the new resume epoch. Reading the state does not hold the abort writer's lock across AX work.

## Effects and uncertainty

The bound send checks before composer focus, AX text assignment, keyboard fallback, primary submit, button/cursor fallback, and escalation. Once an AX/OS effect may be entered, `mutation_started` remains true even if a later checkpoint cancels. The worker can therefore retain `delivery_unknown` instead of treating focus or an uncertain text write as proven no mutation.

Setup cancellation before any effect uses the existing `pre_send_unavailable` response. Preflight remains read-only. Numeric room and transcript binding, human-draft preservation, composer verification, confirmed local outgoing-row requirements, and the existing manual send behavior remain in place.

This is an effect-boundary fence. It cannot atomically interrupt an OS call that has already started, undo a submitted message, or guarantee that a stop arriving immediately after a successful check prevents that next call.

## Parent verification

| Command | Result |
| --- | --- |
| `cargo test --lib alden_abort` | 6 passed |
| `cargo test --lib ax_send::match_tests` | 58 passed |
| `cargo test --bin openkakao-cli commands::local_send::tests` | 6 passed |
| `cargo test --bin openkakao-cli worker_actual_send_setup_failure_finishes_before_ax_call` | 1 passed |
| `cargo test --bin openkakao-cli worker_setup_lock_contention_is_immediate_and_never_reaches_ax` | 1 passed |
| `cargo fmt --check` | passed |
| `cargo clippy --all-targets -- -D warnings` | passed |

Total: **72 focused Rust cases**. Five parent-added fake callback cases prove zero effects before initial cancellation, conservative uncertainty after focus, zero Return after pre-submit cancellation, zero keyboard typing after fallback cancellation, and zero escalation after an already-issued first submit. These tests never drive KakaoTalk or send a message.

The parent also ran Python 3.11 worker cancellation tests (**20 passed**), six targeted existing `send_reply` compatibility tests (**6 passed**), and immutable session packager tests (**13 passed**). The Python relay materializes one environment from the active job token and reuses it for preflight and actual send. No new epoch is captured. The default operator-root selection follows the modern/legacy enrollment rule shared with Rust; an explicit custom-root mismatch remains a native fail-closed result.

After a lint failure for eight function arguments, the parent grouped the abort fence and mutation flag into `SendEffectState`. All **72 Rust cases** and formatting were repeated successfully after this call-site-only refactor; strict Clippy then passed. The combined focused integration count is **111 cases** (72 Rust + 39 Python), without live Kakao sends.

## Integration and delegation limits

The first native Web Sol child ended with a response-stall error after forwarding the Rust task into an existing chat. Its partial source was preserved, reviewed and verified by the parent; it did not return a completed native-child implementation result. The separate current-session child Fermat completed the narrow Python token-relay integration with `chatgpt-web/gpt-5.6-sol`, `xhigh`, and changed only the three assigned Python/test files. The child was closed after its completed result was reviewed. No base-model fallback or standalone `codex exec` session was used by the parent.

The parent inspected the launcher trace `5e4c23580977-d971fa12`: send accepted at **04:39:34.644 UTC**, response visible at **04:39:34.672 UTC**, and turn completed at **04:49:52.466 UTC** on 2026-09-30. The final assistant render contains 2293 text characters and two rendered completion controls. The native child's turn-context metadata independently records the requested Web route and `xhigh`; its final task completion was **04:49:52.561 UTC**. The launcher had a 60-second response-stall checkpoint before completion; no parent retry was sent. This receipt covers the successful relay child, not completion of the earlier Rust child.

The paired stable CLI and immutable worker runtime were subsequently [activated together](alden-fence-activation-20260930.md). Its receipt preserves one fenced startup sample followed by three ready samples from the new runtime. The earlier four-sample [production restoration readback](alden-queue-reconciliation-20260930.md) covers the older runtime and remains separate. A physical global-shortcut-to-AX cancellation event, installed-screen capture and live cancellation latency remain unverified.
