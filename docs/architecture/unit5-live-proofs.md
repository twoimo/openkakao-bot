# UNIT 5 live proofs — Jarvis Tauri local AI

Measured on 2026-09-20 KST in `/Users/twoimo/Documents/projects/openkakao-bot` at HEAD `05c365e`; `origin/codex/jarvis-tauri-local-ai-20260920` also resolved to `05c365e` before the proof run. The worktree was clean before this proof file was created.

## 1. Hide-state render-call = 0

Source proof: `desktop/src/core/animation-loop.ts` routes every lifecycle state other than `visible` through `stop()`. `stop()` sets `running = false`, cancels the outstanding RAF id, and clears it. `desktop/src/__tests__/desktop.test.ts` covers `hidden`, `closed`, and `locked` plus duplicate-start prevention.

Exact test command:

```bash
cd /Users/twoimo/Documents/projects/openkakao-bot/desktop && npm test
```

Result: **PASS** — 1 test file, 10 tests passed, 0 failed.

For the second proof, the real `AnimationLoop` implementation was bundled to `/tmp` and executed under Node with a timer-backed `requestAnimationFrame`/`cancelAnimationFrame` polyfill. This is independent of the Vitest `FakeScheduler`.

Exact commands:

```bash
cd /Users/twoimo/Documents/projects/openkakao-bot/desktop
./node_modules/.bin/esbuild src/core/animation-loop.ts --bundle --platform=node --format=cjs --outfile=/tmp/unit5-animation-loop.cjs
node -e 'const {performance}=require("node:perf_hooks"); let nextId=1,cancelled=0; const pending=new Map(); globalThis.requestAnimationFrame=(cb)=>{const id=nextId++; const h=setTimeout(()=>{pending.delete(id);cb(performance.now())},16); pending.set(id,h); return id}; globalThis.cancelAnimationFrame=(id)=>{const h=pending.get(id); if(h!==undefined){clearTimeout(h);pending.delete(id);cancelled++}}; const {AnimationLoop}=require("/tmp/unit5-animation-loop.cjs"); const sleep=(ms)=>new Promise(r=>setTimeout(r,ms)); (async()=>{const out=[]; for(const state of ["hidden","closed","locked"]){cancelled=0; const loop=new AnimationLoop(()=>{}); loop.start(); await sleep(145); const before=loop.renderCount; const pendingBeforeStop=pending.size; loop.handleLifecycle(state); const pendingAfterStop=pending.size; await sleep(180); const after=loop.renderCount; out.push({state,renderCountBefore:before,renderCountAfter:after,deltaAfterStop:after-before,pendingBeforeStop,pendingAfterStop,cancelledRafCallbacks:cancelled,runningAfterStop:loop.isRunning()})} console.log(JSON.stringify(out,null,2))})().catch(e=>{console.error(e);process.exitCode=1})'
```

Measured result: **PASS**.

| Lifecycle state | renderCount before stop | renderCount after 180 ms | Added renders | RAF pending before | RAF pending after | Cancelled RAF callbacks | Running after stop |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| hidden | 2 | 2 | 0 | 1 | 0 | 1 | false |
| closed | 2 | 2 | 0 | 1 | 0 | 1 | false |
| locked | 2 | 2 | 0 | 1 | 0 | 1 | false |

The pass criterion is met: after hide/close/lock, further scheduler time added **0** render calls and the outstanding RAF callback was cancelled.

## 2. Voice and emergency abort

Read-only source inspection covered `scripts/jarvis_voice.py`, `scripts/jarvis_abort.py`, `desktop/src-tauri/src/main.rs`, and `desktop/src-tauri/src/python_bridge.rs`.

Exact Unit 3 command:

```bash
cd /Users/twoimo/Documents/projects/openkakao-bot
/Users/twoimo/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11 -m unittest tests.test_jarvis_unit3
```

Result: **PASS** — 6 tests passed, 0 failed. These cover abort during generation, abort during TTS, wake threshold/TTS echo rejection, latched global abort queue behavior, background AX abort behavior, and browser job cancellation.

Host dependency import probes used the requested UV Python 3.11:

```bash
/Users/twoimo/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11 -c 'import openwakeword; print("openwakeword: import PASS")'
/Users/twoimo/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11 -c 'import mlx_whisper; print("mlx_whisper: import PASS")'
/Users/twoimo/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11 -c 'import qwen_tts; print("qwen_tts: import PASS")'
```

Measured result: **all three unavailable in this host environment**.

- `openwakeword`: FAIL to import — `ModuleNotFoundError: No module named 'openwakeword'`
- `mlx_whisper`: FAIL to import — `ModuleNotFoundError: No module named 'mlx_whisper'`
- `qwen_tts` (Qwen3-TTS adapter package): FAIL to import — `ModuleNotFoundError: No module named 'qwen_tts'`

Because the model packages are absent, the wake/STT/TTS flow was exercised with the existing fake adapters from `tests.test_jarvis_unit3` while using the real `JarvisVoicePipeline`, `WakePhraseGate`, and abort token.

Exact probe command:

```bash
cd /Users/twoimo/Documents/projects/openkakao-bot
/Users/twoimo/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11 -c 'import sys; from pathlib import Path; from tempfile import TemporaryDirectory; sys.path.insert(0,"scripts"); from jarvis_abort import AbortController; from jarvis_voice import JarvisVoicePipeline, WAKE_PHRASE; from tests.test_jarvis_unit3 import FakeStt, FakeLlm, FakeTts; t=TemporaryDirectory(); c=AbortController(Path(t.name)); tts=FakeTts(); p=JarvisVoicePipeline(stt=FakeStt(), llm=FakeLlm(), tts=tts, token=c.token()); p.feed_audio(b"\x00\x00"*320, rms=0.1, speech=False, stock_wake_score=0.65); wake_state=p.state.value; r=p.process_utterance(b"\x01\x00"*320); print({"wake_phrase":WAKE_PHRASE,"wake_state":wake_state,"final_state":r.state.value,"transcript":r.transcript,"reply":r.reply,"tts_calls":tts.calls})'
```

Measured result: **PASS** — `wake_phrase='헤이 자비스'`, state after threshold acceptance `user_listen`, final state `ended`, fake STT transcript `테스트 요청`, fake reply `알겠습니다.`, fake TTS calls `1`.

Missing voice packages do not make the Tauri menubar snapshot path fail: the Python bridge reads the bounded `jarvis-voice-status.json` file and returns `SafeVoiceStatus::default()` when it is missing, invalid, or the schema is wrong. The model-specific imports are lazy inside the wake/STT/TTS runtime paths rather than imported by the Tauri menubar snapshot reader.

Emergency abort path: **present**. `desktop/src-tauri/src/main.rs` registers `SUPER | ALT + Escape` (⌘⌥Esc) and calls `PythonBridge::global_abort()` on key press. `desktop/src-tauri/src/python_bridge.rs` writes a latched `jarvis-abort.json`; `scripts/jarvis_abort.py` provides the matching `AbortController`/`AbortToken` behavior. The live global hotkey was not pressed.

## 3. Cutover and guardrails

Signed-app cutover remains pending. The installed Swift Extra at `/Applications/AutoReplyMenu.app` was not replaced, restarted, or rewritten, so this proof does not claim installed signed-Tauri behavior.

Before writing this file, Extra PID `20042` was still running from `/Applications/AutoReplyMenu.app`. No process matching `27B` or `gemma` was observed. The existing allowed local `Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit` server remained running and was not swapped or reloaded.

No Kakao send or live AX send was performed. No git commit or push was performed. No command in this proof used `/Users/twoimo/.codex/worktrees/5a1c/openkakao-bot` as a working directory, and no file there was written.

## Live window hide

Measured on 2026-09-20 KST from `/Users/twoimo/Documents/projects/openkakao-bot` at HEAD `c990ad78d92550c3177821d19b373b2f4da78497`. The desktop Vite page was served with the repository's existing `npm run dev` command on `127.0.0.1:1420`; `/Applications/AutoReplyMenu.app` was not launched or restarted.

`desktop/src/main.ts` now exposes the existing `JarvisCore.renderCount` as a tiny read-only browser debug getter, `window.__jarvisRenderCount`. A separate headless Chrome page was driven over the Chrome DevTools Protocol. Before page navigation, browser `requestAnimationFrame`/`cancelAnimationFrame` were wrapped only to count outstanding native browser RAF callbacks; this proof did not use the earlier Node timer RAF polyfill.

The page was first forced to the visible lifecycle state and allowed to render for 550 ms, then `document.visibilityState`/`document.hidden` were set to `hidden`/`true` and a real `visibilitychange` event was dispatched. The browser was observed for another 650 ms.

Measured result: **PASS**.

| Measurement | renderCount | Pending browser RAF |
| --- | ---: | ---: |
| Visible before hide | 9 | 1 |
| Immediately after `visibilitychange` to hidden | 9 | 0 |
| Hidden after 650 ms | 9 | 0 |

Hide-state render delta: **0**. The outstanding browser RAF callback was cancelled immediately and remained at **0** while hidden.

## Isolated voice environment

Created a dedicated voice virtual environment at `/Users/twoimo/Documents/projects/openkakao-bot/.venv-voice` with `/Users/twoimo/.local/bin/python3.12` (Python 3.12.13). This is separate from Extra pid 20042's UV Python 3.11 interpreter.

Installed into that environment:

- `openwakeword==0.6.0`
- `mlx-whisper==0.4.3` (import name `mlx_whisper`)
- `qwen-tts==0.1.1` (import name `qwen_tts`)

The import and environment-gate probe used the dedicated interpreter with `OPENKAKAO_VOICE_ENV=1` and returned:

```text
interpreter=/Users/twoimo/Documents/projects/openkakao-bot/.venv-voice/bin/python
OPENKAKAO_VOICE_ENV=1
openwakeword=0.6.0:PASS
mlx-whisper=0.4.3:PASS
qwen-tts=0.1.1:PASS
jarvis_voice_gate=PASS
stt_adapter_init=MlxWhisperAdapter:PASS
tts_adapter_init=Qwen3TtsAdapter:PASS
```

`qwen_tts` import also emitted two non-fatal runtime warnings: the system `sox` executable is not installed, and `flash-attn` is not installed. The package import and Jarvis isolated-environment gate still passed. No STT/TTS model smoke was run because it would require model download/load; no Qwen3.8 27B or Gemma model was loaded.

## Remaining UNIT 5 proofs at `ebd1c94`

Measured on 2026-09-20 KST from `/Users/twoimo/Documents/projects/openkakao-bot` at HEAD `ebd1c94e8b298ba93caaf6e9734d19f8c85963bb` on `codex/jarvis-tauri-local-ai-20260920`.

### Isolated STT/TTS model smoke

All probes used `/Users/twoimo/Documents/projects/openkakao-bot/.venv-voice/bin/python` with `OPENKAKAO_VOICE_ENV=1`. Model cache paths were confined to `/Users/twoimo/Documents/projects/openkakao-bot/.venv-voice/cache`; Extra pid 20042's UV Python 3.11 environment was not used.

Real STT adapter smoke: **PASS**. `MlxWhisperAdapter(model="mlx-community/whisper-tiny-mlx")` transcribed 0.25 seconds of 16 kHz PCM silence and returned the expected empty transcript (`STT_SMOKE_PASS ''`). The model files were fetched into the isolated voice cache.

Real TTS adapter smoke: **FAIL before model load**. `Qwen3TtsAdapter()._load()` raised:

```text
ImportError: cannot import name 'Qwen3TTS' from 'qwen_tts' (.../.venv-voice/lib/python3.12/site-packages/qwen_tts/__init__.py)
```

The installed `qwen-tts==0.1.1` exports `Qwen3TTSModel`, while `scripts/jarvis_voice.py` imports `Qwen3TTS`. The already-known missing-SoX warning also appeared, but it is not the pass/fail criterion and the adapter failed independently on the API mismatch. No TTS model weights were loaded.

Follow-up: `Qwen3TtsAdapter` now imports `Qwen3TTSModel` and calls `generate_custom_voice`. Unit 3 covers the API mapping with a fake engine.

### Isolated Qwen3-TTS 1.7B live smoke at `b007b74`

Measured on 2026-09-20 KST from `/Users/twoimo/Documents/projects/openkakao-bot` at HEAD `b007b74c6f8bfafbca542ea2d321e504d6836911`. The dedicated interpreter was `/Users/twoimo/Documents/projects/openkakao-bot/.venv-voice/bin/python` with `OPENKAKAO_VOICE_ENV=1`; model cache writes stayed under `.venv-voice/cache/huggingface`.

The configured default model id `Qwen/Qwen3-TTS-1.7B` is not a valid Hugging Face repository: `hf download Qwen/Qwen3-TTS-1.7B` returned `Model 'Qwen/Qwen3-TTS-1.7B' not found` before any weights were loaded. The installed `qwen-tts==0.1.1` examples advertise `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice` for the `generate_custom_voice` path.

Using that official 1.7B CustomVoice id as the `Qwen3TtsAdapter(model=...)` override, the same adapter loaded successfully with `torch.bfloat16` on CPU. Supported speakers were `aiden`, `dylan`, `eric`, `ono_anna`, `ryan`, `serena`, `sohee`, `uncle_fu`, and `vivian`; the adapter's first-speaker rule selected `aiden`.

The Korean phrase `안녕하세요. 음성 합성 테스트입니다.` synthesized successfully through `generate_custom_voice` and was written without playback to:

```text
/Users/twoimo/Documents/projects/openkakao-bot/.venv-voice/smoke/qwen3-tts-1.7b-ko.wav
153644 bytes
24000 Hz, mono, PCM_16
3.200 seconds
speaker=aiden
load+generate+write elapsed=169.24 seconds
```

SoX remained absent. `qwen-tts` printed its missing-SoX warning, but the model loaded and synthesized the WAV successfully, so no SoX installation was required. `flash-attn` was also absent and inference used the manual PyTorch path.

Memory guardrail: before the smoke, `memory_pressure -Q` reported 38% system-wide free memory. During the CPU inference process, observed RSS was about 3.9 GB and then 2.7 GB; sampled free-memory percentages were 16% and later 83%, with 93% after completion. No OOM/kill occurred. Readback from the resident MLX server after the smoke still showed Flash-Next loaded at 75,303,252,216 resident bytes and Krea loaded at 15,817,951,221 resident bytes. Qwen3.8 27B remained `loaded:false` with zero resident bytes, and Gemma remained `loaded:false` with zero resident bytes.

Extra pid `20042` remained alive as `/Applications/AutoReplyMenu.app/Contents/MacOS/AutoReplyMenu`. `/Applications/AutoReplyMenu.app` retained its pre-smoke stat (`mtime=1789860464`, directory size `96`), and no install/overwrite, restart, Kakao send, live AX send, or speaker playback was performed.

Result: the `Qwen3TTSModel.generate_custom_voice` adapter mapping is live-proven with the official 1.7B CustomVoice repository, but the configured default `QWEN3_TTS_MODEL_ID = "Qwen/Qwen3-TTS-1.7B"` remains a blocking model-id defect for the literal default path.

Follow-up: `QWEN3_TTS_MODEL_ID` now defaults to `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice`, the id that produced the 153,644-byte WAV.

### Local Tauri debug artifact

`./node_modules/.bin/tauri build --debug` completed successfully without installing the app. The artifacts remain under `desktop/src-tauri/target`:

- `/Users/twoimo/Documents/projects/openkakao-bot/desktop/src-tauri/target/debug/openkakao-jarvis-desktop`
- `/Users/twoimo/Documents/projects/openkakao-bot/desktop/src-tauri/target/debug/bundle/macos/OpenKakao Jarvis.app`

The debug executable was not running after the build. Extra pid `20042` still resolved to `/Applications/AutoReplyMenu.app/Contents/MacOS/AutoReplyMenu`. The installed Extra and the debug Tauri bundle both report `CFBundleIdentifier=com.openkakao.auto-reply.menu`; this identifier collision is recorded as a cutover blocker. No codesign, replacement, launch, restart, or `/Applications/AutoReplyMenu.app` mutation was performed.

### style.gallery retry

`https://style.gallery/` returned **HTTP 200**. Read-only inspection extracted these restrained tokens: `--paper:#f6f5f0`, `--ink:#2c302b`, and the UI font stack `"Pretendard", "Apple SD Gothic Neo", "Noto Sans KR", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`.

The paper/ink colors were not adopted because the existing oh-my-design palette already has the required warmer ivory/warm-black values and champagne-gold accent. The non-color font token fit the contract without neon, bloom, glow, or palette drift, so it was applied in `desktop/src/styles.css` as `--font-ui` and used for the root font family.

### Guardrail readback

- Extra pid `20042` remained the installed Swift Extra process.
- No process matching Qwen3.8 27B or Gemma was observed, and neither model was loaded by these probes.
- No Kakao send or live AX send was performed.
- Nothing was copied or installed into `/Applications/AutoReplyMenu.app`.
- No command used `/Users/twoimo/.codex/worktrees/5a1c/openkakao-bot` as its working directory and no file there was written.
- No git commit or push was performed.

## Parallel Tauri run at `09db90b`

Measured on 2026-09-20 KST from `/Users/twoimo/Documents/projects/openkakao-bot` starting at HEAD `09db90b`.

`desktop/src-tauri/tauri.conf.json` now uses `identifier = com.openkakao.jarvis.desktop`. `productName` remains `OpenKakao Jarvis`, and the rebuilt debug bundle reports `CFBundleIdentifier=com.openkakao.jarvis.desktop` with `LSUIElement=true`. The bundle remains at `/Users/twoimo/Documents/projects/openkakao-bot/desktop/src-tauri/target/debug/bundle/macos/OpenKakao Jarvis.app`; it was not installed into `/Applications`.

The rebuilt debug app was launched from that bundle alongside the existing signed Swift Extra. Final process readback was:

- Extra: pid `20042`, `/Applications/AutoReplyMenu.app/Contents/MacOS/AutoReplyMenu`
- Jarvis debug: pid `85174`, `/Users/twoimo/Documents/projects/openkakao-bot/desktop/src-tauri/target/debug/bundle/macos/OpenKakao Jarvis.app/Contents/MacOS/openkakao-jarvis-desktop`

The live hide probe ran inside the real Tauri/WKWebView. A temporary debug-only probe wrapped the browser RAF calls, showed the real `jarvis` Tauri window, measured `window.__jarvisRenderCount`, then hid that window through Tauri. The probe code was removed and the final debug bundle was rebuilt afterward. Result: **PASS**.

| Measurement | `__jarvisRenderCount` | Pending RAF |
| --- | ---: | ---: |
| Before Tauri hide | 8 | 1 |
| Immediately after hide returned | 8 | 1 |
| Hidden after 674 ms | 8 | 0 |

Hide-state render delta was **0** and the RAF wrapper observed **1 `cancelAnimationFrame` call**. This is a Tauri-window measurement rather than the earlier Vite/browser proof.

A screenshot was attempted through the local macOS capture command while preparing this proof, but the command remained blocked on Screen Recording access and was aborted; no screenshot artifact was retained.

Signed Extra cutover remains pending. The installed Extra was not restarted, rewritten, or replaced and continues to own the original `com.openkakao.auto-reply.menu` bundle id; the new Jarvis id is for the parallel Tauri app. No Kakao send/live AX send, `/Applications/AutoReplyMenu.app` install/copy, git commit, or push was performed, and `/Users/twoimo/.codex/worktrees/5a1c/openkakao-bot` was not used as a working directory or written.

## Jarvis Three.js core panel PNG

Captured on 2026-09-20 KST from `/Users/twoimo/Documents/projects/openkakao-bot` at HEAD `d20343e`. Vite served the real desktop panel on `127.0.0.1:1420`, and Google Chrome headless rendered the Three.js scene with a 2-second virtual-time budget before writing:

`/Users/twoimo/Documents/projects/openkakao-bot/docs/architecture/jarvis-core-panel.png`

The capture did not use macOS Screen Recording. Chrome's WebGL path emitted GPU `ReadPixels` activity while producing the PNG. Readback verified:

- PNG dimensions: **276 × 260**
- File size: **51,545 bytes**
- Unique RGB colors: **1,580**
- Background sample at `(0, 0)`: **(18, 16, 13)**
- Core-center sample at `(138, 130)`: **(196, 165, 104)**
- Pixels different from the top-left background: **25,208**

Direct visual inspection shows the champagne-gold/warm-amber spherical Jarvis core on the warm dark panel with the single settings gear at the top-right. The image is therefore non-empty and is not a blank or black unrendered canvas.

This is a browser-rendered proof of the Three.js core panel only. It does not claim signed Extra cutover. Extra pid `20042` remained the installed Swift Extra during capture.


## Load-driven Jarvis motion and settings panel at `32dc4b6`

Measured on 2026-09-20 KST at HEAD `32dc4b689cac7793814dc1162c08af9556ea7cd7`. The current `jarvis-core.ts` and `animation-loop.ts` were bundled to `/tmp`; the real `JarvisCore.prototype.render` update ran through the real `AnimationLoop` with a deterministic 60 Hz RAF-like scheduler and inert renderer/scene stubs.

| Measure | load 0 | load 1 |
| --- | ---: | ---: |
| steady render rate | 12.25 fps | 20.50 fps |
| frame ceiling | 15 fps | 30 fps |
| ring 0 target / settled | 0.170 / 0.170 rad/s | 0.408 / 0.408 rad/s |
| ring 1 target / settled | -0.120 / -0.120 rad/s | -0.318 / -0.318 rad/s |
| ring 2 target / settled | 0.090 / 0.090 rad/s | 0.261 / 0.261 rad/s |
| ring damping lambda | 1.80 / 2.35 / 2.90 s^-1 | 1.80 / 2.35 / 2.90 s^-1 |
| nucleus radius | 0.270 | 0.324 |
| neuron point size | 0.036 | 0.048 |
| synapse opacity | 0.090 | 0.250 |
| particle opacity | 0.200 | 0.650 |
| acoustic lattice scale | 1.000 | 1.000 |
| root Y angular velocity | 0.040 rad/s | 0.150 rad/s |

The three ring targets use distinct load multipliers and damping constants. Job load increases nucleus radius and visual activity density through neuron size and synapse/particle opacity. With `voiceRms=0`, the acoustic lattice stays at `1.0` because that pulse is voice driven.

Vite ran on `127.0.0.1:1420`. The live DOM route `index.html?view=settings` was rendered at **760 x 760** and captured to `/Users/twoimo/Documents/projects/openkakao-bot/docs/architecture/jarvis-settings-panel.png` (**87,872 bytes**). Visual and source readback show target rooms, AI model, Voice, Kakao DB sync/index plus Knowledge, DREAM-RSI, and History. The main panel remains gear-only and its gear invokes `open_settings`; no bulk verification or permissions chrome is present.

This section does not claim Extra cutover.

## Remaining Browser-Use abort and acoustic-lattice proofs at `282933b`

Measured on 2026-09-20 KST from `/Users/twoimo/Documents/projects/openkakao-bot` at HEAD `282933b`.

### Browser-Use local abort smoke

`scripts/jarvis_browser_use.py` was exercised through the real `BrowserUseRunner`. The available Python Playwright package did not have its bundled Chromium downloaded, so the smoke used a `DedicatedPlaywrightContext` subclass that changed only `start()` to launch the already-installed `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` headlessly. The runner still owned a fresh Playwright browser plus `browser.new_context()` and did not connect to a persistent profile or existing user tabs.

The owned context opened a transient controlled `file://` page under `docs/architecture/.jarvis-browser-smoke-*` with title `Jarvis Local Abort Smoke` and marker `owned-playwright-context`. The transient directory was removed when the probe exited. No Browser-Use LLM/model call was needed; a local `agent_factory` held the real runner job open until `jarvis_abort.AbortController.abort()` fired.

Exact readback:

| Measurement | Result |
| --- | --- |
| page/context before abort | 1 page, browser connected |
| runner result | `ok=false`, `error_code=global_abort`, empty result |
| abort token | cancelled = `true` |
| abort call -> runner return | **371.770 ms** |
| owned context after return | `None` |
| owned browser after return | `None` |
| owned Playwright after return | `None` |
| runner `_owned_context` after return | `None` |
| saved browser reference after return | disconnected |
| saved page reference after return | closed |

This confirms that a `jarvis_abort` cancellation reaches an in-flight `BrowserUseRunner` job and that its owned Playwright context/browser are closed by the runner cleanup path.

### Voice RMS -> acoustic lattice

`desktop/src/core/jarvis-core.ts` was bundled transiently with the repository's existing esbuild and instantiated as the real `JarvisCore` in a headless Chrome canvas. The probe called `JarvisCore.setSignals(0.5, voiceRms)` and ran the real private render update for 300 deterministic 60 Hz frames to settle the damped signal. `jobLoad` stayed fixed at **0.5**. After settling, deterministic phase samples read the real `lattice.scale.x`.

| `voiceRms` | settled RMS | lattice center (`sin=0`) | lattice min | lattice max | center amplitude above 1.0 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0.0 | 0.000000 | **1.000** | 1.000 | 1.000 | 0.000 |
| 0.8 | 0.800000 | **1.056** | **1.036** | **1.076** | **0.056** |

The measured values match the production update `1 + smoothVoiceRms * (0.07 + 0.025 * sin(...))`: at RMS 0 the lattice is static at 1.0, while RMS 0.8 produces a center scale of 1.056 and a 1.036-1.076 pulse range.

The existing hologram screenshot was not regenerated. Extra pid `20042` remained alive after both probes as `/Applications/AutoReplyMenu.app/Contents/MacOS/AutoReplyMenu`. No Extra/LaunchAgent restart, `open -a AutoReplyMenu`, Kakao send/live AX send, 27B/Gemma load, `/Applications` install, git commit/push, or write to `/Users/twoimo/.codex/worktrees/5a1c/openkakao-bot` was performed.

## Background-safe AX virtual cursor proof — 2026-09-20 KST

Measured at HEAD `fadb271dcb70d5a3407bf3168ae080eb9590b43c` with a temporary local AppKit accessory window exposing one `Background Safe` button. `scripts/auto_reply_ax_ui.py::background_virtual_cursor_action` targeted only that owned local button.

- Command: `python3 .unit5-ax-proof/proof.py` → exit **0**, `pass=true`.
- Frontmost before and after probe launch: **Aside pid 95964 → 95964**.
- Button rect: **(129, 925, 162, 34)**; virtual cursor center: **(210, 942)**.
- Background-safe path: `ok=true`, callback calls **1**, counter **0→1**, frontmost **95964→95964**.
- Focus-required path: `ok=false`, `error_code=ax_focus_steal_required` (`AX_ABORT_FOCUS_REQUIRED`), callback calls **0**, counter stayed **1**, frontmost **95964→95964**.
- Extra pid **20042** was alive before and after and was not frontmost after the proof.

## Stock openWakeWord score on Korean “헤이 자비스” — 2026-09-20 KST

Measured from `/Users/twoimo/Documents/projects/openkakao-bot` after HEAD `8f1c5d4`, with Extra pid **20042** still owning `/Applications/AutoReplyMenu.app`. The probe used `.venv-voice` Python 3.12 and `OPENKAKAO_VOICE_ENV=1`. No speaker playback, live microphone, Extra restart, 27B/Gemma load, Kakao send, or `/Applications` install was performed.

Qwen3-TTS `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice` synthesized the wake phrase without playback:

| Field | Value |
| --- | --- |
| path | `.venv-voice/smoke/hey-jarvis-ko.wav` (gitignored) |
| bytes | **46,124** |
| rate / duration | **24,000 Hz**, **0.96 s**, 23,040 source samples |
| speaker / language | `aiden` / Korean |
| load + generate | 5.499 s + 27.351 s |
| playback | false |

`OpenWakeVadFrontend.analyze()` previously forwarded raw `bytes` into `openwakeword.model.Model.predict()`, which requires a numpy int16 array. The frontend now converts each **640-byte / 320-sample / 20 ms** frame to int16 for the wake model and still passes the original bytes to WebRTC VAD. UV Python 3.11 `tests.test_jarvis_unit3` is **8 tests OK**, including `test_openwake_frontend_requires_20ms_int16_frames`.

The isolated voice env needed `webrtcvad-wheels` (the old `webrtcvad==2.0.10` import crashed on missing `pkg_resources` under setuptools 84). `voice/pyproject.toml` already pins `webrtcvad-wheels`.

Stock `hey_jarvis` scores versus `WAKE_THRESHOLD=0.65` (threshold was not lowered):

| Clip | 16 kHz samples | 20 ms frames | stock max | accepted |
| --- | ---: | ---: | ---: | --- |
| Korean TTS “헤이 자비스” | 15,360 | 48 | **0.001959** | no |
| Unrelated Korean TTS control | 51,200 | 160 | 0.001959 | no |
| 1 s silence | 16,000 | 50 | 0.000017 | no |

An 80 ms native-frame diagnostic on the same wake clip also peaked at **0.00136** for `hey_jarvis` (`hey_rhasspy` 0.00264). The English stock model misses this Korean pronunciation.

Readback after the probe: Extra pid **20042** alive; Flash-Next loaded 75,303,252,216 bytes; Krea loaded; Qwen3.8 27B and Gemma unloaded. Custom Korean wake-model training remains open. Live microphone and signed Extra cutover remain open.

## Bounded custom Korean wake model — 2026-09-20 KST

Measured from `/Users/twoimo/Documents/projects/openkakao-bot` at starting HEAD `f4b19f77a48b611f2f72526f301f5a1d6d72a453`. The stock `hey_jarvis` model remains enabled; the Korean model is an opt-in second model passed through `--custom-wake-model`. `WAKE_THRESHOLD` remains **0.65**.

The bounded training/export helper is `scripts/train_jarvis_korean_wake.py`. It reuses the local openWakeWord `1 x 16 x 96` embedding frontend and fits a ridge linear head from the existing synthesized wake clip against the unrelated Korean TTS control plus one second of silence. The resulting real ONNX artifact is:

- `voice/models/hey_jarvis_ko_ridge.onnx`
- **6,406 bytes**, below the runtime **64 MiB** maximum
- runtime validation rejects empty files, unsupported extensions, files larger than 64 MiB, and symbolic links before openWakeWord loads the custom model

Training used **7** post-warmup positive feature windows and **42** control/silence negative windows. Scoring then ran through the existing `OpenWakeVadFrontend` using 20 ms / 320-sample frames; no speaker playback or microphone capture was used.

| Clip | Frames | custom max | threshold | accepted |
| --- | ---: | ---: | ---: | --- |
| Korean TTS “헤이 자비스” | 48 | **0.893993** | 0.65 | yes |
| Unrelated Korean TTS control | 160 | **0.194481** | 0.65 | no |
| 1 s silence | 50 | **0.161939** | 0.65 | no |

This is a calibration/proof model trained from the same single synthesized positive clip used by the wake score check. It demonstrates a real loadable custom ONNX path and separation from the supplied control/silence samples; it does **not** establish generalization to human speakers, microphones, rooms, accents, or unseen negatives. Keep it opt-in until a multi-speaker positive/negative corpus is collected and independently evaluated. The threshold must not be lowered to compensate for future misses.

UV Python 3.11 verification:

```text
/Users/twoimo/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11 -m unittest tests.test_jarvis_unit3
..........
Ran 10 tests
OK
```

The two added tests cover accepted model path/extension, the 64 MiB bound, and symbolic-link rejection. Training/export used the isolated `.venv-voice` interpreter with the optional local `onnx` training dependency. No cloud inference fallback was added.
Parent re-score in a fresh `.venv-voice` process after Ramanujan returned: wake **0.895084** accept, control **0.218323** reject, silence **0.142663** reject. Decision vs 0.65 is unchanged; small score drift is preprocessor-state variance, not a threshold change.

## Bundled Korean wake wired into Voice settings and file pipeline — 2026-09-20 KST

Measured from `/Users/twoimo/Documents/projects/openkakao-bot` starting at HEAD `fb718c3`. Codex Web extra-high (Bernoulli) hit ChatGPT rate limit mid-task; the parent finished the wiring locally. Extra pid **20042** was not restarted. 27B/Gemma stayed unloaded.

`resolve_custom_wake_model()` now selects `voice/models/hey_jarvis_ko_ridge.onnx` when the file validates. The microphone session and the file pipeline both load stock + custom. Invalid/missing bundle fails closed to stock-only and does not lower `WAKE_THRESHOLD=0.65`.

Tauri Voice card copy (gear-only main panel unchanged):

- `voice-phrase`: 호출어 헤이 자비스
- `voice-threshold`: 0.65 고정
- `voice-custom`: bundled ONNX selected vs stock-only Korean miss

File pipeline (no microphone, no speaker playback) used `.venv-voice` with `OPENKAKAO_WHISPER_MODEL=mlx-community/whisper-tiny-mlx`:

| Field | Result |
| --- | --- |
| custom_model_selected | true |
| stock_max | 2.06e-06 |
| custom_max | **0.784400** |
| accepted | true (`wake_source=custom`) |
| threshold | 0.65 |
| STT/LLM/TTS | `state=error`, `error_code=generation_error` |
| Extra 20042 | alive |
| 27B | unloaded |

A direct Flash-Next `/v1/chat/completions` probe then timed out at 20 s with 0 bytes while Extra still owned the resident model. The wake-accept path is proven; a butler reply was not obtained without displacing Extra's Flash-Next. Live microphone and signed Extra cutover remain open.

UV Python 3.11 `tests.test_jarvis_unit3`: **11 OK**. Desktop vitest: **13 OK**. Rust `voice_status_is_bounded_and_clamped`: **OK**.

## File pipeline wake → STT → Flash-Next → TTS — 2026-09-20 KST

After adding `max_tokens=128` to `LocalMlxLlm`, the file pipeline completed without a microphone or speaker playback. Extra pid **20042** stayed alive. 27B/Gemma stayed unloaded. Whisper for this proof was `mlx-community/whisper-tiny-mlx` (production default remains large-v3-turbo).

| Field | Result |
| --- | --- |
| custom_model_selected | true |
| stock_max | 4.17e-06 |
| custom_max | **0.767606** |
| accepted | true, `wake_source=custom` |
| threshold | 0.65 |
| transcript | `안녕하세요. 분청 합성 테스트입니다.` (tiny STT misheard 음성→분청) |
| reply | `네, 분청 합성 정상 작동 중입니다.` |
| tts wav | `.venv-voice/smoke/jarvis-reply.wav` **145,964** bytes, 24 kHz, 3.04 s, playback false |
| Extra 20042 | alive |
| 27B | unloaded |

A bounded Flash-Next probe with `max_tokens=16` returned `확인` in 17.4 s prefill. The earlier `generation_error` was an unbounded completion against the same resident server, not a missing model. Live microphone and signed Extra cutover remain open.

## Parallel Tauri debug rebuild and Voice settings capture — 2026-09-20 KST

Measured from `/Users/twoimo/Documents/projects/openkakao-bot` at HEAD `38f77cb15d9691d0cdf47c990e18da4ece37e033`.

- Rebuilt debug bundle: `/Users/twoimo/Documents/projects/openkakao-bot/desktop/src-tauri/target/debug/bundle/macos/OpenKakao Jarvis.app` with `CFBundleIdentifier=com.openkakao.jarvis.desktop`; it was not installed into `/Applications`.
- The stale parallel Jarvis pid `85174` was stopped and only the Jarvis debug process was restarted. The rebuilt app is running as pid **62924** from the debug bundle above.
- Updated settings capture: `/Users/twoimo/Documents/projects/openkakao-bot/docs/architecture/jarvis-settings-panel.png`, **760 x 760**, **76,125 bytes**. The Voice card shows `호출어 헤이 자비스`, fixed threshold `0.65`, and the bundled ONNX custom-wake status.
- Extra pid **20042** remained alive at `/Applications/AutoReplyMenu.app/Contents/MacOS/AutoReplyMenu` and was not restarted.
- Process readback found no `Qwen3.8-27B` or Gemma process. The active `mlx-serve` pid **38868** is explicitly serving `Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit`, so 27B remained unloaded by process evidence. A fresh structured `/v1/models` confirmation was not obtained because the bounded 2 s request timed out.
- The real Tauri/WKWebView hide RAF probe was not repeated. Its temporary telemetry hook had already been removed; a fresh measurement would require reinstrumenting and rebuilding the app. The earlier real-window hide proof remains `renderCount=8→8`, pending RAF `1→0`, one cancellation, delta `0` after 674 ms.

No git commit/push, `/Applications` install, Extra restart, Kakao/live AX send, speaker playback, 27B/Gemma load, threshold lowering, or write to `/Users/twoimo/.codex/worktrees/5a1c/openkakao-bot` was performed for this refresh.
Parent verification after Hubble: Extra pid **20042** alive; rebuilt Jarvis debug pid **62924**; `http://127.0.0.1:11234/v1/models` showed Flash-Next `loaded=true` and Qwen3.8-27B/Gemma `loaded=false`. The 760×760 PNG is the real settings DOM with Voice copy; Chrome has no Tauri IPC so the bundled-ONNX line was set to the already-measured file-pipeline state before capture.
