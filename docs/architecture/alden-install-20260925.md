# Alden installation evidence — 2026-09-25 KST

This records an actual local menu-bar app cutover from the working tree. It is not a release receipt or a measurement of conversational performance.

## Latest local rebuild — 2026-09-30 KST

The `18a6d4b` desktop source was rebuilt and installed through the existing rollback-capable installer. The post-build source snapshot covered 174 build inputs with zero changes. Fresh readback at 12:11 KST found all **28 installed files** equal to the built bundle, strict code-signature verification exit 0, and `com.openkakao.alden.desktop` running with PID 22124. See the [metadata-only receipt](alden-install-readback-20260930.json). The previous bundle/service backup is retained in the private installer backup directory.

This is an ad-hoc signature and an owned local installation. Computer Use inventory/accessory-app observation timed out; no fresh installed-screen, physical abort-shortcut or GPU/battery result is established. The opt-in DPO source scorer's separate numerical verification is recorded in the [27B compatibility evidence](alden-dpo-checkpoint-compatibility-20260930.md); it is not bundled into this installation. Existing room workers were not restarted by this desktop cutover.

## Offline installer verification

`python3.11 -m unittest discover -s tests -p test_alden_desktop_launchers.py -v` completed with exit 0: **34 tests passed in 59.467 seconds**. The interpreter was the existing menu-bar Python 3.11 runtime. The installer fixtures used temporary app, LaunchAgent, and backup directories plus fake launchd, process enumeration, and executable-path adapters. They did not send KakaoTalk messages or stop live services.

The cases cover loaded/unloaded Jarvis migration, missing or unresolved ownership, unknown process enumeration, duplicate or dead app processes, delayed PID publication, changed PID identity, rollback of bundles/plists/services, and interruption during runtime mutation.

## Actual cutover and readback

| Check | Observed result |
| --- | --- |
| Prior app | Jarvis service running PID 3120 from `/Applications/OpenKakao Jarvis.app` |
| Prior Alden and Swift menu services | Both service lookups returned not found |
| Built bundle metadata | Alden, version 0.1.5, identifier `com.openkakao.alden.desktop`, executable `openkakao-alden-desktop` |
| Installer | `sh scripts/install-alden-desktop.sh` exited 0 and reported `/Applications/Alden.app` installed |
| Recovery directory | `~/Library/Application Support/openkakao/install-backups/alden-desktop/20260925T132245-93391` |
| Alden service readback | `state = running`, PID 93566, executable `/Applications/Alden.app/Contents/MacOS/openkakao-alden-desktop` |
| Process enumeration | Exactly one matching Alden/Jarvis process, PID 93566 (Alden) |
| Old Jarvis service | `launchctl print gui/501/com.openkakao.jarvis.desktop` exited 113: service not found |
| Installed payload | `diff -qr desktop/src-tauri/target/release/bundle/macos/Alden.app /Applications/Alden.app` exited 0 without differences |

The installer backs up the previous service/plist state before mutation and writes its successful activation record inside the recovery directory. Its old Jarvis bundle retention is a recovery mechanism; this receipt does not claim that every historical Jarvis artifact has been removed.

## Installed screen and room readback

The installed settings screenshot was captured at 960 by 880 pixels and inspected. It shows the Alden name, two columns, the ivory/warm-black/gold palette, and no visible horizontal clipping at that size. Lower graph and answer-history content continues below the viewport. The voice control explicitly reports that the Alden wake model is unavailable. The screenshot is kept as a private local artifact because room names appear in it; it is not a public release asset.

Fresh room-state readback on 2026-09-25 at approximately 13:49 KST found **two of three rooms delivery-enabled**. Their last authoritative context updates were approximately 38 and 18 seconds old. The remaining room was fenced with `context_sync_transient`, with **711 transient failures** and an authoritative context age of **48,424 seconds (13.45 hours)**. All three rooms had empty pending-gap and pending-log lists and no in-flight candidate at that observation. These empty lists do not establish a complete context snapshot or authorize clearing the fence.

The settings screenshot says three rooms are connected. That display is not evidence that three rooms are ready to answer. Connection visibility and delivery readiness require separate verification.

A subsequent bounded, read-only `local-poll` observation returned a version-3 `sqlite_snapshot_rowset` envelope. It reported 21,277 available rows and returned the requested first 50, correctly marked `partial` with `has_more=true`. Its `available_max_log_id` was less than the room metadata's `chat_last_log_id`. A separate watcher readback showed that the context checkpoint, last-observed cursor, cursor floor, and acknowledged watermark all equalled that available maximum. This narrows the unresolved diagnosis to the advertised tail/completeness discrepancy; it is not evidence that raising the cursor or disabling the delivery fence is safe. The IDs are in a `global_sparse` domain, so subtracting them cannot establish a missing-message count. The owned diagnostic subprocess was stopped after one envelope; no messages were sent and no live state was written.

The repository-built CLI's read-only `auto-reply-host --status --json` command returned exit 1 and `healthy=false`. Invoking the stable installed CLI for the same status operation instead failed before obtaining host status: `could not find the openkakao-cli repository root`. This is an installed-command discovery defect, not proof that the host has stopped.

The earlier blocked screen/host queries no longer describe all available evidence. Later combined diagnostic reads were also blocked by automatic safety review; those calls yielded no additional source or runtime evidence.

## Reproducible packaging gap

The independent source-packaging audit completed without changing product files. The current loader and product resource manifests depend on three ignored, untracked CPython 3.11 bytecode files: `_auto_reply_menubar_wrapper.cpython-311.pyc`, `_bujamentor_menubar_overlay.cpython-311.pyc`, and `_bujamentor_menubar_impl.cpython-311.pyc`. The audit found no corresponding reviewed source or supported generation path in repository history or registered worktrees. Its 16 offline assertions confirmed the missing-input failure; they do not constitute a passing product build.

A clean source checkout therefore does not yet reproduce this installation. Recovering reviewed source or implementing the missing capabilities in maintained source remains required before publishing a reproducible Alden product artifact.

## Follow-up source verification

The room-summary frontend now accepts only explicit `ready`, `blocked`, or `unknown` readiness. Missing, invalid, or conflicting readiness aliases resolve to `unknown`. The ready count additionally requires a live room with automatic replies enabled. Identical refreshes retain the selected room, option nodes, and unchanged summary text.

The new display fixtures initially omitted the existing asynchronous context contract, causing six display tests to receive an unavailable snapshot. The fixtures now supply `context_sync: { mode: "async", waited: false }` and assert availability. The production availability guard was not relaxed. The focused frontend run passed **86 tests across three files in 0.798 seconds**, and TypeScript checking exited 0. This is source verification; the updated summary has not yet been verified in the installed app.

The service now derives `OPENKAKAO_AUTO_REPLY_RUNTIME_ROOT` from the attested private runtime entry and propagates it through preflight, production, watchdog, and guardian execution. Receipt and guardian identities bind the runtime root and asset digests. The independent implementation run covered 16 distinct tests. The subsequent parent run of the complete service-entry and session-packager modules passed **73 tests in 27.691 seconds** using Python 3.13 and temporary/fake adapters. These test sets overlap and must not be added together as distinct coverage.

A metadata-only room readback at **14:13:53 KST** still found two delivery-enabled rooms and one room fenced with `context_sync_transient`. All three watcher files were approximately 0.6–1.1 seconds old, with no pending gaps, pending log IDs, or in-flight candidate. Fresh heartbeats do not establish successful context synchronization. No live runtime cutover or measured improvement is established by these source changes.

## Unverified requirements

The menu app installation and the readbacks above do not establish any of the following:

- Layout at other window widths, working graph-node interaction, or live hidden-window render-loop pause.
- Deployment of the latest context-sync/worker changes into the active immutable room runtime, or a reduction in actual latency, skipped replies, or self-questioning loops.
- A working wake-to-STT-to-LLM-to-TTS conversation, dense/RRF retrieval, or a physical global-abort input.
- A committed source snapshot, a reproducible clean-checkout product build, signing/notarization, or a published Alden release.

Earlier frontend/Rust/context-sync test results remain separate source evidence. They are not substitutes for the missing live checks above.

## Follow-up local rollout — 2026-09-27 KST

The rebuilt Alden.app installed successfully. A directory comparison found no differences between the release bundle and `/Applications/Alden.app`; LaunchAgent readback found one running Alden process. The installed menubar Python snapshot returned three rooms with `reply_readiness=ready` and no stderr. The focused `local_poll_` Rust run passed seven selected tests, including the control-row-tail completeness cases. These checks did not exercise a live KakaoTalk send or the installed panel's visual interaction.

Before the worker cutover, the active runtime independently reported **3/3 rooms ready**, with current heartbeats, no pending gaps and no in-flight candidate. A newly prepared runtime passed all **20 packaged asset** hash and mode checks and an exact-selector, read-only preflight (`valid=true`, `check=true`, `will_send=false`, `workers_started=false`). The current source scripts, data asset and configuration matched the staged digests; the packaged CLI was signed during staging and matched the stable signed CLI. The previous stable CLI and monitor plist were copied to a private recovery directory before staging.

The first new worker activation failed: the shared local MLX endpoint was unavailable, so the watchdog entered `circuit_open` and all three rooms became `stopped_clean`. MLX Core's own startup log showed that Flash-Next needed about **77.1 GB** of available memory and found only **51.8–54.4 GB**. The failure was left fenced; cursors, ACKs, queues, and send permissions were not forced forward. A manual shell launch of Qwen3.8 27B briefly reported ready but its child process did not persist after that shell exited. A one-shot LaunchAgent with `AbandonProcessGroup=true` then launched the same installed MLX Core binary; the 27B server survived removal of that temporary job. The user MLX profile, MLX Core preferences and reply configuration were changed to 27B after private backups were made.

The 27B configuration was baked into a second immutable runtime. Its **20 assets** passed hash and mode checks, and its exact-selector read-only preflight again returned three valid targets without starting workers. The prior failed watchdog received a verified-owner `SIGTERM`, released its owner lock, and the monitor plist was replaced and bootstrapped. Readback showed the new runtime entry in the active process command, watchdog `state=running` with zero restarts and zero consecutive failures, and **3/3 rooms ready** with fresh watcher heartbeats, delivery enabled, no pending gaps and no in-flight candidates. A later scheduled MLX management run exited 0 and its profile verification returned `RESULT: OK`.

The local `/v1/models` catalog returned HTTP 200 and reported Qwen3.8 27B `ready` with **18,196,477,654 resident bytes**. A synthetic localhost completion returned HTTP 200, one completion token and **0.553 s** client elapsed time. Flash-Next's prior reported resident size was **75,303,252,216 bytes**: the two model footprints differ by **57,106,774,562 bytes (53.185 GiB; 75.84%)**. These are model-catalog footprints at different times, not a measured reduction in whole-system memory or normal reply latency. Current free swap was about **1.17 GiB**, below the 2 GiB voice admission threshold.

The production worker is now on the new source-derived runtime, but no comparable post-cutover inbound-to-reply latency or skip-rate window has been collected. A real wake→STT→LLM→TTS turn, installed graph interaction, hidden-panel GPU pause, physical abort shortcut, maintained source for three bundled menubar bytecode files, signing/notarization and public release remain unverified.

### Menu bundle reproducibility follow-up

The three CPython 3.11 menubar bytecode inputs were found in the working tree and matched the installed Alden.app copies byte for byte (173,952 + 63,704 + 59,187 = 296,843 bytes). Commit `a9c59fa` tracks these exact files and exempts them from the repository-wide `*.pyc` ignore rule. Their SHA-256 digests are pinned in `scripts/menubar-bytecode.sha256`, and the Alden build script checks them before compiling. The local digest check and shell syntax check passed. This commit makes the three inputs available in a future checkout; it does not prove a clean-checkout build or recover maintainable source for the bytecode. Those release gates remain open.

### Render fixture privacy and width check

The Chromium render fixture previously used a real KakaoTalk room identifier and title. Its source and regenerated screenshots/JSON now use synthetic room, person, organization, term and document values. The rebuilt frontend passed **32/32 rendered checks**. At the configured **960 px** settings width, the measured columns were **553.719 px** and **342.266 px**, with **960 px** document width and no horizontal overflow. The synthetic visibility event yielded **0 rendered frames in 1.503 s** while hidden and **34 frames in 1.503 s** after showing the panel. This establishes browser-rendered behavior with a stubbed Tauri bridge, not the installed AppKit panel's GPU or battery consumption.

### Voice model selection follow-up

The local voice source now defaults to Qwen3.8 27B and accepts only that model or an explicitly selected Flash-Next. Inside the MLX request lease it reads the bounded local model catalog and requires one matching `loaded=true, state=ready` row before sending a completion request. A later live catalog read returned HTTP 200 with exactly one ready, loaded 27B row. Independent local runs passed **11 focused LLM-request tests** and **6 voice runtime tests**; the source and tests were inspected after the Web agent's upload. This does not exercise the microphone or TTS, and the live wake model remains unreleased (`RELEASED_WAKE_MODEL=None`).

Browser-Use source now defaults to Qwen3.8 27B. An explicit Flash-Next choice is accepted only when the bounded localhost model catalog reports one loaded, ready row. The check runs inside the request lease before an owned Chromium context opens. The voice, Browser-Use, tool runtime and unit-3 offline suite passed **78/78 tests** with cached Python 3.11 and NumPy. These are source checks; no autonomous browser job or installed-app voice turn was run.

### Archify receipt refresh

Nine Alden diagrams were validated at showcase quality (**9/9 artifact checks each, zero composition errors and warnings**), delivered from their current specifications, and passed a fresh automated Chromium visual check. Their JSON receipts now identify the sibling HTML by relative path and bind its current SHA-256 and size. The system overview's ten source references were verified against commit `35eabebe28c1e2caa5e125c821ccce048e98f31d` before delivery. Browser checks remain separate from a perceptual review.

### Current read-only queue window — 2026-09-27 06:15 KST

The session monitor and read-only TUI reported **3/3 rooms ready**, zero active jobs, zero `delivery_unknown`, and zero journal errors. Three room queue databases contained **21 skipped** and **11 sent** jobs created in the preceding 24 hours; these are queue outcomes, not independently observed KakaoTalk delivery. All 32 jobs predate the worker cutover at 03:00:58 KST. No room queue contained a job created after that cutover. The latest 150 locally readable KakaoTalk rows in each room also had `sent_at` before the cutover. Thus this window supplies no post-cutover inbound-to-reply latency or skip-rate sample, and cannot establish an improvement in skipped conversations.

### Conversation queue timing audit — 2026-09-27 07:40 KST

A second read-only sweep of the retained queue databases found the same **32** jobs in the preceding 24 hours: **9 proactive GeekNews sent**, **2 conversation sent**, and **21 conversation skipped**. The skip reasons were **12 `stale_backlog`**, **7 `burst_superseded`**, and **2 `already_commented`**. These are job outcomes rather than unique conversation turns: burst coalescing and an existing reply can deliberately close several jobs. A queue `sent` state is not independent KakaoTalk delivery evidence.

For the 12 stale jobs, inbound timestamp to queue creation was **90.93 s median / 166.90 s nearest-rank p90**. Queue creation to terminal skip was **916.20 s median / 1,009.82 s p90**; inbound to terminal skip was **1,037.24 s median / 1,039.54 s p90**. All 12 terminal rows had no formed reply. The bounded transition journal shows **13 `model_call`/`model_result` pairs across 6** of these jobs, with observed pair durations **0.02–0.44 s**, and one job had **70** `delay_scheduled` transitions. The journal is truncated, so absence of a model pair for another job is not proof it never called the model. These timings establish prolonged pre-reply deferral in the historical window; they do not isolate a single root cause or prove the newer 27B worker fixed it.

At this readback the local MLX catalog returned HTTP 200 with Qwen3.8 27B `loaded=true, state=ready`, while Flash-Next and Qwen3-TTS were unloaded. The host still reported **3/3 rooms ready** and zero post-cutover queue jobs. The latest 150 readable KakaoTalk rows per active room still had no post-cutover timestamp. No queue row, model state, worker, or KakaoTalk message was changed during the audit.

A further read-only classification found that **11 of the 12 stale jobs** were conversation jobs with **36 total processing attempts and 36 `context_lookup` transitions**. Their common terminal signature is consistent with context freshness or refresh retries expiring. The remaining job was proactive, with **71 attempts and 70 scheduled delays**, consistent with the closed-slot path. These transition counts do not prove the precise terminal failure class of each job; older model failures could also have contributed. A separate source defect made the failure-row TCP probe check port **1337** while the local MLX gateway uses **11234**. Correcting that probe improves diagnostics only; it does not establish fewer skipped replies.

### Post-cutover queue readback — 2026-09-27 08:51 KST

The repository-built CLI returned `healthy=true` for the unattended session monitor and **3/3 room workers ready**. Read-only SQLite connections found one queue job created after the 03:00:58 KST cutover in each room, at approximately 08:31 KST. All three were scheduled GeekNews jobs in `sent` state; there were **zero post-cutover conversation jobs**. A queue `sent` state is not independent KakaoTalk delivery confirmation. Consequently, this readback cannot quantify a change in conversation skip rate or response latency.

Commit `d41546d` adds two fake-adapter cases: repeatedly advancing context watermark defers only until the fixed response deadline plus its 120-second grace and then yields `stale_backlog`; a stable refreshed watermark reaches `scheduled` with one `context_lookup`. The full `tests.test_auto_reply_turn_holds` module passed **40/40** locally. This validates source policy for those cases, not the cause of any individual historical stale job or a live improvement.

### Widened settings render readback — 2026-09-27 08:55 KST

The built desktop frontend passed the synthetic Playwright Chromium suite **32/32** after the full-width knowledge and recent-answer layout change. The 960 px settings viewport had two columns of **553.719 px** and **342.266 px** without horizontal overflow, while the knowledge graph canvas occupied **872 px** of CSS width. The product render-call counter recorded **0 frames and 0 snapshot polls in 1.503 s hidden**, and **0 frames in 1.503 s blurred**. WebGL reported ANGLE Metal on Apple M5 Max; the knowledge drilldown focused one fictional node. The committed receipt and captures were regenerated from the current built frontend with fictional data. These are browser-stub measurements, not installed WKWebView, global GPU, or battery measurements.
