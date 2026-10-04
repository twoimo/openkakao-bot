# Alden local tools: evidence and remaining work

Date: 2026-09-27 KST. This record separates source checks from actual browser and macOS permission observations. No KakaoTalk message was sent for these checks.

## Actual Browser-Use runs

The pinned browser runtime used CPython 3.11.9, browser-use 0.13.10 and Playwright 1.63.0. Its dependency probe reported ready. `BrowserUseRunner` used the local Qwen3.8 27B endpoint on `127.0.0.1:11234` and a dedicated ephemeral Chromium context.

| Read-only task | Agent result | Independent DOM readback | Outcome |
| --- | --- | --- | --- |
| Open example.com and return its page title | `example.com`, runner `ok=true`, about 48 s | HTTP 200; `document.title` and h1 both `Example Domain` | Navigation succeeded; requested title was wrong |
| Search Wikipedia for Tauri software and return the first result title | `Tauri (software framework)`, runner `ok=true`, about 43 s | HTTP 200; first `.mw-search-result-heading` was `Tauri (software framework)` | Requested field matched |

Navigation succeeded in **2/2** cases; exact requested-field accuracy was **1/2**. `ok=true` indicates that the agent finished; it does not prove factual correctness. The direct DOM probes used the same dedicated Playwright context implementation without an LLM. These approximate times are individual observations, not latency percentiles or a performance improvement. BrowserSession reset/cleanup appeared in both agent logs.

The native Web GPT-5.6 Sol child implemented bounded DOM evidence for document titles and the first Wikipedia search result. Its session recorded `chatgpt-web/gpt-5.6-sol`, `xhigh`; parent launcher inspection confirmed actual send, visible response and completion for trace `cb8ba0262d54-4b72912c`. The parent additionally rejected supported single-field requests when DOM evidence was absent. An earlier resumed child drifted to base `gpt-6-sol`; it was shut down and its uncommitted patch was saved outside the checkout and excluded. That patch was not used for this correction.

The final source passed **85/85** focused browser, tool-runtime and unit3 checks on the full installed CPython 3.11 voice runtime, with no skips. An initial combined run in the minimal browser venv failed because the voice warmup test requires NumPy; that environment was not expanded. The browser-only child suite passed 25/25 before the parent's missing-evidence regression was added.

Parent actual rechecks on the final source returned **2/2 exact-field matches** against independent Playwright DOM reads: `Example Domain` and `Tauri (software framework)`. In the final example.com run the LLM itself still returned the wrong `example.com`; the DOM-derived value corrected the product result. Both outputs included the actual URL and extraction source in their Python evidence sidecars. Total times including separate DOM probes were **33.872 s** and **47.307 s**. These two cases do not establish general web factual accuracy or a latency improvement. The [live receipt](alden-browser-grounding-live-20260927.json) binds both cases to source SHA-256 `2f272249857cf3f393a158a411e3435346a619d0cb04e0a9007f3d488015fd7e`.

At the earlier 22:33 KST installation, the menu-app CPython 3.11.9 runtime lacked both `browser_use` and `playwright`, and the Rust browser command selected that interpreter. An installed `tool-browser` invocation returned valid JSON with `ok=false`, `status=failed`, and `errorCode=browser_job_failed`. Direct `BrowserUseRunner` success in `browser/.venv` therefore did not establish installed operation. The later dedicated-runtime correction and installed backend check are recorded below. The Python evidence sidecar is still not propagated through the desktop bridge's plain-string tool result.

At approximately 23:24 KST, a dedicated CPython 3.11.16/browser-use 0.13.10/Playwright 1.63.0 runtime was installed and the app rebuilt from `51ebd9c` with fixed browser-specific routing. All **24** bundled Python resources matched the commit, and the built/installed app compared with **zero byte differences**. An installed Python-entrypoint title task returned **Example Domain**, `ok=true`, pure JSON stdout, in **28.770 s**; a separate HTTP 200 DOM read matched. Owned Chromium counts were **0 before / 0 after**. This is one installed backend check, not a graphical Tauri invoke or general accuracy/latency improvement. See the [runtime evidence](alden-browser-runtime-20260927.md) and [raw receipt](alden-installed-browser-live-20260927.json).

## Exact background AX source

The production CLI in `scripts/alden_tool_runtime.py` now resolves a target by exact PID, bundle identifier, window title, AX role, and at least one element identity selector. Zero matches or multiple matches are rejected. It requires `--allow-background-ax`, supports only a fixed `AXPress` action on the allowlisted button/check box/radio button/disclosure roles, and has a total timeout of at most five seconds. This opt-in does not establish authorization for the particular target's effect.

`SystemEventsBackgroundAxAdapter` resolves the target again immediately before pressing it. It contains no app activation, real pointer movement, click synthesis or keystroke synthesis. The virtual cursor is only the element rectangle's center: **(x + w/2, y + h/2)**. A frontmost target is refused. An action can still cause its own app to activate; if that happens after pressing, the effect is reported as uncertain.

The exact runtime reads the same enrollment-selected state root as Tauri: `auto-reply` when enrolled, otherwise the enrolled legacy `bujamentor` root. The global abort latch is checked before resolving and before/after action. A timeout, unexpected post-press result, or abort after pressing yields **`ax_action_effect_unknown`**, not a claim that nothing happened. The caller must read back the target before retrying. No automatic retry or durable AX idempotency ledger is implemented.

Parent verification on the installed CPython 3.11 voice runtime: **59/59** focused tool-runtime and unit3 tests passed, with no skips. Generated resolve and press AppleScripts compiled successfully in a prior parent check; compilation did not execute them. Tests use fake AX adapters. **No real AXPress action was performed.** The standalone CLI is implemented, but a desktop bridge/UI invocation and installed-bundle deployment of these changes remain unverified.

## Local deployment readback at approximately 22:33 KST

The app was rebuilt from committed source `62b2749` and installed with the existing reviewed installer. The local bundle has an ad-hoc signature, not Developer ID signing or Apple notarization. `codesign --verify --deep --strict` passed before installation. The installer retained the recovery directory `~/Library/Application Support/openkakao/install-backups/alden-desktop/20260927T223221-73679`.

The built app and `/Applications/Alden.app` compared with **zero differences**. The installed Browser-Use, exact AX runtime, AX adapter and voice files each matched their source SHA-256. LaunchAgent readback reported one Alden process, PID **73818**, running from the installed app. This ships the corrected source and standalone AX CLI; it does not fill the missing browser dependency/runtime connection or add a desktop AX caller.

The Kakao host reported `healthy=true`, with **3/3** rooms ready, model available and workers idle. Its watchdog was on attempt **3**, with **2** cumulative restarts and last child exit code **1**. The cause of the earlier exits was not established by this readback; this is not evidence of zero-restart stability. No test message was sent. The installed app's Computer Use screen-read attempt still timed out, so this receipt does not verify its rendered UI, microphone or physical shortcut.

## Main-process resource observation at 22:52 KST

The installed Alden main process, PID 73818, was sampled five times over **20.01494325 s**. Its cumulative CPU counters did not advance during that interval, yielding **0.0 s** observed CPU time and **0.0% of one logical core**. Its physical footprint stayed at **27.689 MiB**; resident size stayed at **99.625 MiB**. These describe the main process only: WebKit, Chromium, MLX and other processes are excluded, and window visibility was not observed. This is neither a before/after memory saving nor a GPU/battery measurement.

The sampler used `proc_pid_rusage(RUSAGE_INFO_V0)` with the installed Xcode SDK structure layout. On this Apple Silicon host, treating native CPU counters as nanoseconds directly failed a POSIX `getrusage` crosscheck. Multiplication by `mach_timebase_info` **125/3** made the native/POSIX CPU-duration ratio **1.0002536** (about **0.0254%** difference). The reported CPU percentage is **100 × ΔCPU_ticks × (125/3) / Δwall_nanoseconds**. The [raw resource receipt](alden-main-resource-sample-20260927.json) preserves the counters and measurement boundaries.

## Emergency-stop coverage still required

Current source registers **⌘⌥⇧Esc** and `PythonBridge.global_abort()` writes `alden-abort.json` while cancelling app-owned bridge jobs. Voice and the standalone browser/AX tool runtime read this latch. A source scan of the Kakao worker, supervisor and DB watcher found no corresponding latch reader; the existing background Kakao reply host is outside that app-owned cancellation registry. Thus the source does not yet establish that this shortcut stops every Kakao/GeekNews write path. A further scan found the Python `resume_after_human_action()` helper but no desktop bridge command or UI control that exposes it; render-loop lifecycle resumes are unrelated to this emergency latch. That broader stop integration, explicit operator resume path and physical shortcut verification remain required; no live abort or test send was performed during this audit.

## Current permission UI observations

Computer Use opened macOS System Settings and inspected the actual permission switches:

- Accessibility: Terminal, openkakao-cli and osascript were already enabled. No Alden row or pending approval button was present.
- Microphone: the older display label `OpenKakao Jarvis.app` was enabled. The installed `/Applications/Alden.app` has bundle identifier `com.openkakao.alden.desktop`; the older label alone does not prove permission for this bundle.
- Automation: osascript → System Events and python3.11 → System Events were enabled.

No permission was newly granted. App inventory calls timed out, so these observations do not prove that no other app has a permission popup. Alden microphone operation remains unverified, and the wake-model release gate still blocks voice startup independently of permission state.
