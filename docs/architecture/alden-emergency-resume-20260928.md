# Alden emergency resume integration — 2026-09-28 KST

The parent integrated partial source from native current-session Web children
Noether (desktop bridge and UI) and Fermat (Python abort state). Both requested
`chatgpt-web/gpt-5.6-sol` with `xhigh`. Their later turns terminated with
`missing cwd in trusted Codex environment context`; neither was resumed or
replaced with a base model. The parent completed compatibility and safety
refinements and ran the checks below. Child completion is not claimed.

## Shared state and explicit resume

- State: four schema-1 fields; epoch is an integer in `0..2^53-1`.
- Missing state is initial/unlatched. Corruption, wrong schema, duplicate
  fields, wrong owner/mode, hardlinks, symlinks and FIFOs fail closed.
- Root mode is `0700`; state and lock mode is `0600`, owned, regular,
  single-link. Reads stop at 4097 bytes; the state limit is 4096 bytes.
- Writers share `alden-abort.lock`. Nonblocking flock attempts wait at
  5 ms intervals for at most 250 ms before returning an error.
- Resume requires explicit `true`, a currently valid latched state, an
  incremented epoch, and readback. Atomic replacement uses a new private
  temporary file. Old cancellation flags and tokens remain cancelled.
- The settings header shows **일시 중지됨 / 다시 시작** only while latched.
  A newer stop event wins over an older asynchronous resume response.
  Opening/reopening the window never resumes background work.

## Checks actually executed

| Check | Result |
| --- | --- |
| Desktop Rust tests, locked/offline/source-check mode | 85/85 |
| Vitest, including five new emergency-control tests | 185/185 |
| Python state tests | 13/13 |
| Python tool runtime tests | 17/17 |
| Python Browser-Use tests | 26/26 |
| Python voice/abort unit tests, pinned voice interpreter | 42/42 |
| Desktop Clippy, all targets, `-D warnings` | pass |
| TypeScript/Vite production build | pass |

Rust's interoperability test holds the actual flock, verifies that the
Python writer times out without mutation, releases it, then reads a Python
custom-reason abort and resumes it from Rust at the next epoch. All state,
FIFO, process and UI tests use temporary/fake targets. No live Kakao send,
queue mutation, microphone input or global-latch transition was exercised.

## Remaining scope

The worker/supervisor do not yet consume this global state. Therefore these
results do not establish cancellation of Kakao replies or GeekNews, physical
shortcut operation, installed UI rendering, live voice operation, or full-goal
completion. Source verification is separate from deployment readback.
