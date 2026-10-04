# Alden renderer lifecycle handoff — 2026-09-30

## Result

The desktop renderer now owns one RAF callback per visible generation. If a
hide or hide→show sequence arrives while a frame callback is executing, the old
callback cannot schedule itself again. Repeated `visible` or repeated pause
signals also leave timer ownership unchanged.

`window.__aldenRenderPause` exposes a read-only pause receipt from the running
panel. It records the pause event, render counts, last rendered timestamp,
signed last-frame offset, late-frame latency, running state, and whether a RAF
is pending. A negative `lastFrameOffsetFromEventMs` means the last frame was
already complete before the pause event. `eventToLastFrameMs` is non-negative
and is zero when no frame completes after the event.

The first pause transition owns the measurement baseline. Duplicate hidden
signals return before changing the seed, so they cannot hide a late frame that
occurred after the original event. A regression advances the fake clock to
500ms and repeats hidden 50 times while asserting that the first `eventAtMs`
remains unchanged.

GPU objects remain allocated across ordinary hide/show because the same
`AldenCore` instance resumes. Final `dispose()` is idempotent, releases retained
geometry/material/renderer resources once, and prevents a disposed core from
starting again.

## Changed ownership

- `desktop/src/core/animation-loop.ts`: stable callback identity per visible
  generation; stale callbacks cannot rearm; read-only frame diagnostics.
- `desktop/src/core/lifecycle.ts`: deduplicated timer stop/start effects and
  pause measurements using `performance.now()`.
- `desktop/src/core/alden-core.ts`: diagnostics forwarding and one-time GPU
  disposal.
- `desktop/src/main.ts`: read-only `window.__aldenRenderPause` probe for browser
  and installed-app measurement.
- `desktop/src/__tests__/render-lifecycle.test.ts`: races, measurement, timer
  ownership, and GPU disposal regression cases.
- `desktop/src/__tests__/lifecycle-wiring.test.ts`: 25 attach/detach cycles leave
  no effective DOM or bridge listener.

This renderer task changed no Python, GraphRAG, package metadata, Cargo,
runtime, installed app, launch agent, model server, Kakao database, queue, or
configuration. Concurrent parent/subagent edits in those paths were preserved.

## Deterministic regression evidence

Before the fix, the new focused suite failed all four initial cases:

- hide during a frame left one RAF queued;
- hide→show during that frame left two RAFs queued;
- no hidden-event/last-frame measurement existed;
- 50 duplicate hidden signals invoked timer stop 51 times.

After the fix:

```text
npx vitest run
Test Files  15 passed (15)
Tests       191 passed (191)

npx tsc -p tsconfig.json --noEmit
exit 0
```

The focused lifecycle/render/desktop run also passed 99/99 tests before the
full suite.

## Real renderer, synthetic bridge measurement

The current TypeScript source ran through Vite on the owned temporary endpoint
`127.0.0.1:43179`. Aside Chromium created a real Three.js WebGL2 renderer:
`WebGL 2.0 (OpenGL ES 3.0 Chromium)` / `WebKit WebGL`. The Tauri invoke/event
bridge and runtime snapshot were synthetic and loaded before `main.ts`; this
isolated the renderer without touching the installed app or shared runtime.

First hide sample:

| Observation | Value |
|---|---:|
| render count before / synchronous after hide | 25 / 25 |
| render count after 750 ms hidden | 25 |
| renders after event | 0 |
| last frame offset from event | -35.10 ms |
| late-frame latency | 0 ms |
| running / pending RAF | false / false |

After 50 synthetic hide/show cycles, the bridge still had one listener. The
visible loop rendered 13 frames over the next one-second wall-clock window,
consistent with the existing idle 15fps cap rather than multiplied loops. A
final hide produced 0 additional frames over 500 ms; its last frame preceded
the event by 59.10 ms and no RAF remained pending.

Host load recorded immediately after the earlier browser sample was high
(`13.97 / 15.48 / 15.74`). Other sessions had active Chromium, Python,
virtualization, and Spark processes. No process was stopped. The frame count is
therefore a lifecycle sanity check, not a general frame-performance benchmark;
the zero late-frame result and callback ownership are the acceptance evidence.

The temporary Vite server, owned browser tab, and temporary harness HTML were
closed or removed after measurement.

## Evidence boundary

This work verifies source behavior, deterministic fake-scheduler races, and a
real Chromium/WebGL renderer driven by a synthetic Tauri bridge. It does not
verify the production Tauri WKWebView, a menu-bar hide event from AppKit, the
installed app, screen lock, Retina behavior, package/build output, or GPU and
battery use of the whole app. Native CUA app discovery was unavailable to the
parent session due repeated timeouts. Those items remain parent-owned final
integration checks and must not be inferred from this receipt.

Structured evidence: `docs/architecture/alden-render-lifecycle-20260930.json`.
