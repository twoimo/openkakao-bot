# Backtrace-independent SQLite retry termination — 2026-09-30

The first replica patch passed focused local checks, but [CI run 36676596909](https://github.com/twoimo/openkakao-bot/actions/runs/36676596909) correctly exposed a protocol defect: `RUST_BACKTRACE=1` made Anyhow Debug append a backtrace to the exact retry marker. A second, separately scoped native Sol Web task fixed the root CLI termination path.

`run()` retains the command implementation. `main()` now returns the `ExitCode` supplied by `terminate_cli()`. Both replica copy exhaustion and persistent poll-gap exhaustion retain distinct typed provenance. The termination writer recognizes these types and emits exactly `Error: context_sync_snapshot_retry_exhausted` plus one newline. It uses no marker substring test. Other errors keep the existing rich Anyhow Debug/backtrace output and a failing exit code.

An isolated subprocess fixture runs the same termination helper for each of:

- Replica exhaustion and persistent gap exhaustion.
- `RUST_BACKTRACE=0/1/full` crossed with `RUST_LIB_BACKTRACE=0/1`: **12 combinations**.
- A permission error whose text happens to contain the marker: its rich backtrace remains present.

Parent independently repeated **245 passing CLI tests**, **zero failures**, with `RUST_BACKTRACE=1`. One subprocess entry is ignored by direct test discovery and invoked by the matrix test; it is not an omitted behavior check. Strict Clippy, cargo fmt and whitespace checks passed. These are fake-source checks, not a deliberately broken live database run.

Native route readback remained `chatgpt-web/gpt-5.6-sol`, `xhigh`. The initial Web stream was aborted while the parent delivered the additional typed poll-gap requirement (trace `78b5549940f7-f4bb7211`, 06:22:19.898 UTC); it is not counted as a completed result. The continuation trace `ca01a0d8eb46-1a778d57` records send accepted at **06:22:38.230 UTC**, response visible at **06:22:38.434 UTC**, and completed response at **06:27:05.756 UTC**, with **917 response characters** and two rendered completion actions. No 403/quota failure was recorded. This continuation completed the additional requirement; no completed task was replayed.

This source correction must be included in the next affected-path deployment. It does not resolve the separate image-capability or pre-send failures found in natural queue traffic.
