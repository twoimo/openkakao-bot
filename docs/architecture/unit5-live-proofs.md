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

## Bundled ONNX without a prior voice session, plus large-v3 STT — 2026-09-20 KST

`python_bridge.rs` now treats a missing/invalid `jarvis-voice-status.json` as `custom_model_selected=true` only when `voice/models/hey_jarvis_ko_ridge.onnx` validates (regular file, not symlink, `.onnx`/`.tflite`, 1..64 MiB). Wake phrase defaults to 헤이 자비스 and threshold 0.65. The settings Voice card uses that flag even when `voice.available` is false, so Chrome/Tauri no longer need a session file overlay.

Rust: `missing_voice_status_selects_valid_bundled_wake_model` and `missing_voice_status_rejects_missing_or_invalid_bundled_wake_model` **OK**. UV 3.11 unit3 **12 OK**. Desktop vitest **13 OK**.

Production STT on the existing Korean TTS utterance (`.venv-voice/smoke/qwen3-tts-1.7b-ko.wav`), no microphone:

| Field | Result |
| --- | --- |
| model | `mlx-community/whisper-large-v3-turbo` |
| transcript | `안녕하세요 분성 합성 테스트입니다.` |
| elapsed | **25.382 s** |
| tiny earlier | `안녕하세요. 분청 합성 테스트입니다.` |

Both models mis-hear 음성 as 분성/분청 on this aiden TTS clip. That is an audio/model limitation, not a tiny-only defect. Extra pid **20042** stayed alive. 27B stayed unloaded. Signed Extra cutover remains open.

## Rebuilt parallel Tauri at HEAD 96153df — 2026-09-20 KST

`tauri build --debug` finished at `desktop/src-tauri/target/debug/bundle/macos/OpenKakao Jarvis.app` (`com.openkakao.jarvis.desktop`). Extra pid **20042** was not restarted. The previous stale debug pid 36294 was replaced; a nohup launch exited silently, then a foreground launch kept pid **63463** alive with Extra still at 20042.

`resolve_custom_wake_model(None)` on this tree returns the 6,406-byte bundled ONNX. Rust missing-status tests already cover the same selection rule. Hide RAF was not re-measured. Signed Extra cutover remains open. 27B stayed unloaded.

## Menubar SIGHUP survival — 2026-09-20 KST

`ignore_terminal_hangup()` now ignores SIGHUP at process start. After `tauri build --debug`, `open` launched `OpenKakao Jarvis.app` as pid **84521** with ppid **1**. `kill -HUP 84521` left that pid alive. Extra pid **20042** and LaunchAgent `com.openkakao.auto-reply.menu` stayed running. The unused template `desktop/launchd/com.openkakao.jarvis.desktop.plist.example` was not `launchctl load`ed. 27B stayed unloaded. Signed Extra cutover remains open.

## Opt-in microphone session command — 2026-09-20 KST

Settings Voice now has `voice-start` (마이크 세션 시작). The gear-only main panel does not. `PythonBridge::start_voice_session` spawns `.venv-voice/bin/python scripts/jarvis_voice.py --state-root …` with `OPENKAKAO_VOICE_ENV=1` and does not wait. Missing venv → `voice_environment_missing`; missing/symlink script → `voice_script_missing`. App setup does not auto-start the mic. ⌘⌥Esc still writes the existing abort latch.

Rust `plan_voice_session_requires_isolated_interpreter` **OK**. Desktop vitest **13 OK**. Extra pid **20042** and debug pid **84521** were not restarted, so this button is in source until the next debug rebuild. The live microphone was not opened. 27B stayed unloaded.

## Debug rebuild with opt-in mic button — 2026-09-20 KST

Rebuilt `OpenKakao Jarvis.app` at HEAD `6f76876` and replaced only the parallel debug process. New pid **40102**, ppid 1, `com.openkakao.jarvis.desktop`. Extra pid **20042** stayed on LaunchAgent. The live microphone button was not clicked.

Updated capture `docs/architecture/jarvis-settings-panel.png` **760×760 / 75,913 bytes** from the built `dist` settings view. Voice shows 호출어 헤이 자비스, 임계값 0.65, and **마이크 세션 시작**. This static capture has no Tauri IPC, so the custom-head line is the no-snapshot stock-only copy; the live Rust snapshot still selects bundled ONNX when the file validates. 27B stayed unloaded.

## Settings default copy matches bundled Korean wake — 2026-09-21 KST

When the runtime snapshot is unavailable, Voice no longer overwrites the custom-head line to stock-only. Markup default is bundled ONNX selected. Capture `jarvis-settings-panel.png` **760×760 / 76,526 bytes** shows 헤이 자비스, 0.65, bundled ONNX, and 마이크 세션 시작. Extra pid **20042** and debug pid **40102** were not restarted. Live mic was not opened. 27B stayed unloaded.

## Speaker-safe TTS output for opt-in voice sessions — 2026-09-21 KST

`Qwen3TtsAdapter.speak()` now writes a WAV when `OPENKAKAO_VOICE_TTS_OUT` is a writable non-symlink `.wav` path, and returns without `sd.play`. Invalid env values fail closed with `voice_tts_output_invalid` instead of falling back to speakers. `PythonBridge::start_voice_session` sets that env to `state_root/jarvis-voice-out.wav` together with `OPENKAKAO_VOICE_ENV=1`.

UV Python 3.11 `tests.test_jarvis_unit3` with numpy: **13 OK**, including `test_speak_writes_env_wav_without_playback` (RIFF WAV, `play` not called). Rust `plan_voice_session_requires_isolated_interpreter` **OK** (command env includes `OPENKAKAO_VOICE_TTS_OUT`). Extra pid **20042** stayed alive. Live microphone was not opened. 27B stayed unloaded.

## Tauri primary menu-bar cutover — 2026-09-21 KST

The parent release path completed `sh scripts/build-jarvis-desktop.sh`. The
release bundle was created at:

```text
/Users/twoimo/Documents/projects/openkakao-bot/desktop/src-tauri/target/release/bundle/macos/OpenKakao Jarvis.app
```

Bundle readback passed:

- `CFBundleIdentifier=com.openkakao.jarvis.desktop`
- `LSUIElement=true`
- `openkakao-jarvis-desktop`, the release `openkakao-cli`, GraphRAG/reference
  scripts, `jarvis_voice.py`, and `voice/models/hey_jarvis_ko_ridge.onnx` are
  present and executable/readable as required
- local signature is ad hoc (`TeamIdentifier=not set`); notarization was not
  attempted

`sh scripts/install-jarvis-desktop.sh` then installed the bundle at
`/Applications/OpenKakao Jarvis.app`. LaunchAgent readback shows:

```text
gui/501/com.openkakao.jarvis.desktop = running
program = /Applications/OpenKakao Jarvis.app/Contents/MacOS/openkakao-jarvis-desktop
pid = 11863
```

The compatibility start wrapper was then run against the installed app. It
performed a LaunchAgent kickstart without rebuilding or reinstalling; the
follow-up readback showed the same program and one installed Tauri process at
pid **40266**.

`gui/501/com.openkakao.auto-reply.menu` is absent, the old plist was moved to
`~/Library/Application Support/openkakao/install-backups/jarvis-desktop/`, and
no `AutoReplyMenu` process remains. A temporary Applications/LaunchAgents
harness also passed the legacy backup, atomic staging, plist path readback,
and bootstrap/kickstart sequence. No KakaoTalk send, live AX send, microphone
session, model swap, or speaker playback was performed by this cutover proof.

## Fixed Python 3.11 voice runtime and wake resources — 2026-09-21 KST

음성 런타임을 다음 고정 인터프리터로 검증했다.

```text
/Users/twoimo/Library/Application Support/openkakao/runtimes/voice/bin/python3.11
Python 3.11.9
```

고정 런타임에서 다음 패키지 import가 모두 통과했다.

| Package | Version | Result |
| --- | --- | --- |
| `openwakeword` | 0.6.0 | PASS |
| `mlx-whisper` | 0.4.3 | PASS |
| `qwen-tts` | 0.1.1 | PASS |
| `sounddevice` | 0.5.1 | PASS |
| `numpy` | 2.4.6 | PASS |

공식 `openwakeword.utils.download_models(['hey_jarvis'])`를 사용해 아래 리소스만 설치했다. 다른 stock wake 모델은 다운로드하지 않았다.

- `embedding_model.tflite`
- `embedding_model.onnx`
- `melspectrogram.tflite`
- `melspectrogram.onnx`
- `silero_vad.onnx`
- `hey_jarvis_v0.1.tflite`
- `hey_jarvis_v0.1.onnx`

고정 런타임에 중복으로 남아 있던 `lib/python3.12` 1.4 GB를 제거했다. 런타임 크기는 **2.9 GB → 1.5 GB**로 줄었고, 최종 `sys.path`에는 Python 3.11 경로만 남았다. `OpenWakeVadFrontend`의 stock 경로는 `hey_jarvis` 하나만 명시적으로 로드한다. 이 고정 Python 3.11에서 `python3.11 -m unittest tests.test_jarvis_unit3` 최종 결과는 **20 tests OK**다.

실제 MacBook Pro 내장 마이크 입력 스트림은 원음을 저장하지 않고 20 ms 프레임만 처리했다.

| Field | Result |
| --- | --- |
| frames | 100 × 20 ms |
| format | 16 kHz, mono, int16, 320 samples / 640 bytes |
| elapsed | **2.207 s** |
| speech frames | 0 |
| overflow | 0 |
| stock `hey_jarvis` max | **0.000047** |
| bundled custom Korean max | **0.154974** |
| RMS max | 0 |
| raw audio persisted | no |

이 측정은 실제 장치 스트림 초기화와 bounded frame 처리를 입증하지만, 무음 구간이었다. 인간 발화로 호출어를 수락한 증적은 아니다.

고정 Python 3.11과 로컬 `mlx-community/whisper-tiny-mlx`를 사용한 silence smoke도 통과했다. transcript는 빈 문자열이었고 소요 시간은 **1.372 s**였다. 클라우드 또는 네트워크 fallback은 사용하지 않았다.

고정 Python 3.11에서 로컬 캐시된 Qwen3-TTS 1.7B CustomVoice의 bf16 합성도 통과했다. `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice` repo ID를 Hugging Face 캐시의 로컬 snapshot으로 자동 해석한 뒤 `from_pretrained`에 해당 디렉터리를 전달했다.

| Field | Result |
| --- | --- |
| resolved snapshot | `…/snapshots/0c0e3051f131929182e2c023b9537f8b1c68adfe` |
| elapsed | **64.025 s** |
| WAV bytes | **157,484** |
| audio | 24 kHz, mono, 78,720 frames |
| speaker playback | no |
| network / `snapshot_download` | none |

이전에는 offline 모드에서도 qwen-tts 0.1.1의 `from_pretrained(repo-id, local_files_only=True)`가 API 조회를 시도해 `OfflineModeIsEnabled`로 실패했다. local-only model path resolver는 `OPENKAKAO_QWEN3_TTS_MODEL_PATH`로 명시한 유효 로컬 디렉터리 또는 자동 발견한 Hugging Face cache 경로를 사용한다. 명시한 absolute local directory는 cache 밖이어도 허용하고, 자동 repo-id discovery만 `HF_HUB_CACHE`·`HUGGINGFACE_HUB_CACHE`·`HF_HOME/hub`·기본 Hugging Face cache 안의 `refs/main`과 40자리 revision snapshot으로 제한한다. 자동 cache 경로가 없으면 기존 model ID를 유지해 offline fail-closed 동작을 보존한다. 캐시 해석 성공, 캐시 부재, cache 밖 explicit path 허용, 환경 경로 우선, invalid explicit path 거부, 자동 repo-id 경로의 cache-root 제한을 helper 단위 테스트로 검증했다.

SoX와 `flash-attn` 경고는 이 smoke를 막지 않은 비차단 경고였다. 다음 항목은 여전히 미검증이다.

- 인간 발화로 “헤이 자비스” 호출어를 수락하는 동작
- 메뉴의 **마이크 세션 시작** 버튼에서 실제 세션 시작까지의 end-to-end 동작
- 잡음 환경과 실제 한국어 발화에 대한 wake/STT 일반화

## Signed local bundle readback — 2026-09-21 KST (이후 재설치로 대체됨)

다음 명령으로 release bundle을 다시 생성했다.

```bash
OPENKAKAO_SIGN_IDENTITY=- sh scripts/build-jarvis-desktop.sh
```

생성된 bundle과 설치된 앱의 최종 readback은 다음과 같다.

| Field | Result |
| --- | --- |
| `codesign --verify --deep --strict` | PASS |
| `CFBundleIdentifier` | `com.openkakao.jarvis.desktop` |
| `LSUIElement` | `true` |
| `Signature` | `adhoc` |
| `TeamIdentifier` | `not set` |
| source `scripts/jarvis_voice.py` SHA-256 | `05cfbe98a07b251b3c167115afc095d821d0f376dc57b8cf3339b7bd84a6c8d1` |
| bundle `Contents/Resources/scripts/jarvis_voice.py` SHA-256 | `05cfbe98a07b251b3c167115afc095d821d0f376dc57b8cf3339b7bd84a6c8d1` |
| bundled Korean wake model | `voice/models/hey_jarvis_ko_ridge.onnx` present |
| bundled CLI | `bin/openkakao-cli` present |

bundle을 `/Applications/OpenKakao Jarvis.app`에 설치한 뒤 `gui/501/com.openkakao.jarvis.desktop` LaunchAgent를 다시 읽었다.

```text
state = running
program = /Applications/OpenKakao Jarvis.app/Contents/MacOS/openkakao-jarvis-desktop
pid = 5823
```

동일 시점에 `AutoReplyMenu` 프로세스는 없었다. 이 readback은 source와 local release bundle의 음성 스크립트 일치, ad hoc 서명 검증, 로컬 설치 및 LaunchAgent 실행을 입증한다. `Signature=adhoc`이고 `TeamIdentifier=not set`이므로 Developer ID 서명과 notarization은 여전히 미검증이다.

## Default menubar background snapshot readback — 2026-09-21 KST

백그라운드 소스별 신호 경로의 입력 shape를 읽기 전용으로 확인했다.

```bash
/Users/twoimo/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11 scripts/auto-reply-menubar.py --state-root <temp dir>
```

출력 JSON의 top-level keys에 `background`가 포함되었고, 해당 값은 다음과 같았다.

```json
{"activity":0.0,"caption":"","db_sync":{"activity":0.0,"age_seconds":null,"capability_state":"","caption":"","fence_reason":"","state":"unknown"},"geeknews":{"activity":0.0,"age_seconds":null,"caption":"","posted_slots":0,"state":"unknown"},"rooms":[],"schema_version":1}
```

이는 menubar snapshot이 `background` 객체를 내보내는지 확인한 component-level snapshot-shape 증거다. 실제 KakaoTalk 전송이나 live model generation을 입증하지 않는다.

## On-device hardware snapshot readback — 2026-09-21 KST

온디바이스 하드웨어 자동 감지 경로의 입력 shape를 같은 read-only menubar snapshot 명령으로 확인했다.

```bash
/Users/twoimo/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11 scripts/auto-reply-menubar.py --state-root <temp dir>
```

출력의 `ondevice_hardware.hardware` 블록은 다음과 같았다.

```json
{"chip":"Apple M5 Max","cores":18,"is_apple_silicon":true,"memory_bytes":137438953472,"memory_gb":128.0}
```

같은 객체에서 `recommendation.primary_engine`은 `"mlx-serve"`, `recommendation.recommended_model`은 `"ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit"`, `recommendation.recommended_quant`은 `"mixed 4/8bit"`, `last_probe`는 `null`이었다. `recommendation.engine_paths`에는 절대 로컬 경로도 포함되어 있으므로 desktop bridge는 이를 포함한 원본 상세 필드를 전달하지 않고 allowlist 요약만 전달한다.

이는 snapshot shape와 하드웨어 감지 결과에 대한 component-level 증거다. live model generation이나 KakaoTalk 전송을 입증하지 않는다.

## Reply pipeline and bridge job-ring snapshot readback — 2026-09-21 KST

답변 파이프라인 입력 shape와 bridge가 자체 long-running 작업을 전달하는 bounded job-ring 계약을 component 단위로 확인했다. 사용한 read-only snapshot 명령은 다음과 같다.

```bash
/Users/twoimo/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11 scripts/auto-reply-menubar.py --state-root <temp dir>
```

readback의 raw `pipeline` 값은 다음과 같았다.

```json
{"active_index":null,"event_id":"none","outcome":"none","stages":[{"id":"detect","state":"idle"},{"id":"authorize","state":"idle"},{"id":"queue","state":"idle"},{"id":"context","state":"idle"},{"id":"model","state":"idle"},{"id":"delay","state":"idle"},{"id":"send","state":"idle"},{"id":"confirm","state":"idle"}]}
```

고정 stage id 순서는 `detect`, `authorize`, `queue`, `context`, `model`, `delay`, `send`, `confirm`이며 stage state 집합은 `active`, `done`, `skipped`, `failed`, `blocked`, `idle`이다. `desktop/src-tauri/src/python_bridge.rs`의 `sanitize_pipeline`은 이 원본에서 `active`, 닫힌 stage id 또는 `none`/`unknown`, `stageIndex` 0..16, `stageTotal` 0..16, allowlisted `outcome`만 전달하고 `event_id`와 stage별 state는 직렬화하지 않는다. `active_index`가 유효하면 이를 우선하고, 아니면 첫 `active` stage를 선택하며, 둘 다 없으면 `none`으로 처리한다.

같은 bridge의 in-flight job registry는 최신순 최대 8건(`JOB_EVENT_CAP = 8`), 300초 초과 제거(`JOB_EVENT_MAX_AGE_SECS = 300.0`)를 적용한다. 각 event의 직렬화 키는 정확히 `{jobId, kind, stage, load, time, errorCode}`이며 prompt, task text, token/secret, 대화 내용은 포함하지 않는다. browser 작업은 `kind="browser"`, `stage="running"`, `load=0.7`, model swap은 `kind="model_swap"`, `stage="swap"`, `load=0.9`로 등록된다.

이 두 단위에 대한 측정 검증은 desktop Vitest **77/77**(5 files), Rust **58/58**, clean `tsc`, 성공한 Vite production build, Python menubar suite **165 tests, OK**, `sh desktop/scripts/smoke.sh` exit 0이었다. 이 기록은 component-level snapshot-shape와 bounded bridge contract 증거이며 live KakaoTalk 전송이나 live model generation을 입증하지 않는다.

## DREAM-RSI paper provenance and replay guarantee — 2026-09-21 KST

이미 조회된 DREAM-RSI alphaXiv report의 provenance는 arXiv `2609.14858`, alphaXiv `https://www.alphaxiv.org/abs/2609.14858`, GitHub `https://github.com/zhengkid/Dream-RSI`다. 2026-09-21에 아래 read-only 명령으로 report를 조회했으며 exit 0, report body **14,515 chars**가 측정됐다.

```bash
orx paper 2609.14858
```

고정 replay history 한정 non-degradation 계약은 `scripts/auto_reply_dream_rsi.py`의 `INCUMBENT_POLICY`와 checkpoint `replay_guarantee`로 기록한다. `replay_guarantee.scope`는 정확히 `fixed_replay_set_only`이며, incumbent와 selected policy의 유한한 objective score를 비교할 수 있을 때만 `status="verified"`를 사용한다. `NaN`/`Infinity`, 누락 incumbent, 비정상 입력은 성공 비교로 취급하지 않는다.

검증 명령은 다음과 같다.

```bash
/Users/twoimo/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11 -m unittest tests.test_auto_reply_dream_rsi tests.test_dream_rsi_alphaxiv -v
```

결과는 `Ran 68 tests in 0.033s`, `OK`였다. 별도 `py_compile scripts/auto_reply_dream_rsi.py`도 `COMPILE_OK`였다.

이 기록은 paper retrieval provenance와 offline fixed-replay evaluation에 대한 component-level 증거다. live KakaoTalk send, live model generation, future online performance의 non-degradation, 모델 train/promote/replace/start를 입증하지 않는다.

## 두 번째 독립 리뷰 패스와 Computer Use attach 프로브 — 2026-09-21 KST

두 번째 독립 서브에이전트 리뷰는 직전 HEAD `91010ff`에서 fix 1·3·4를 완전 수정, fix 2를 부분 수정으로 판정하고 AHP **96/100**을 부여했다. 남은 지적은 `_sanitize_stderr`가 ASCII C0/DEL만 제거해 Unicode C1 control(U+0080-U+009F, category `Cc`)이 직렬화된 provenance에 남는다는 점이었고, 이번 패스에서 전체 Unicode `C*` 클래스(Cc는 공백 치환, Cf/Cs/Co/Cn은 삭제)로 확장하고 회귀 테스트 4개를 추가했다.

parent 재측정 결과는 다음과 같다.

```bash
/Users/twoimo/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11 -m unittest tests.test_dream_rsi_alphaxiv tests.test_auto_reply_dream_rsi
/Users/twoimo/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11 -m unittest tests.test_auto_reply_ondevice tests.test_auto_reply_reference_search tests.test_auto_reply_knowledge_graph tests.test_jarvis_unit4 tests.test_jarvis_desktop_launchers tests.test_local_mlx_model_readiness tests.test_verify_local_models tests.test_mlx_serve_lifecycle tests.test_auto_reply_dream_rsi tests.test_dream_rsi_alphaxiv
/Users/twoimo/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11 scripts/dream_rsi_alphaxiv.py --paper-source orx --paper-id 2609.14858
```

결과는 `Ran 75 tests ... OK`, 당시 CI focused Python 목록은 `Ran 287 tests ... OK`였고(`deca45a`가 launcher 회귀 테스트 1개를 추가해 commit `633a436` 기준으로는 `Ran 288 tests ... OK`) paper 명령은 exit 0, `status="ok"`, `provider="orx_cli"`, `reason="orx_report_verified"`, summary 14,515 chars였다. U+0000-U+00FF 전체와 U+200B-U+200F, U+202A-U+202E, U+2066-U+2069, U+FEFF, U+061C, U+00AD, U+E000, U+0378, lone surrogate에 대한 직접 프로브에서 `C*` category 잔존은 0건이었고, JSON 직렬화 결과에도 control escape가 없었다.

### Computer Use attach 프로브 (negative)

- 이 Computer Use 프로브 당시 설치 번들의 빌드 시각 관측값은 2026-09-21 14:50:30이었다. commit `4b25149` 기준으로 그 시각 뒤에 17개 커밋이 있었고 가장 이른 커밋은 `7387aa1`(15:13)이었다. 이 커밋에 고정한 관측으로 당시 설치본은 pipeline stage, bridge job ring, on-device hardware 카드, DREAM-RSI provenance를 포함하지 않았다.
- 같은 프로브에서 LaunchAgent `com.openkakao.jarvis.desktop`(`KeepAlive=false`, `RunAtLoad=true`)는 pid 68708로 관측됐고 child process는 없었다. unattended host는 별도 `com.openkakao.auto-reply.session-monitor`이며 이 프로브는 어떤 프로세스도 종료하거나 재시작하지 않았다. 빌드 시각 14:50:30과 pid 68708은 모두 이 프로브 실행 당시의 관측값이며, 설치 번들은 이후 2026-09-21 19:15:27에 재설치됐다.
- `cua.getState()`는 `OpenKakao Jarvis`(`com.openkakao.jarvis.desktop`, `isRunning=true`)를 앱 목록에 노출했지만 `cua.getApp("OpenKakao Jarvis")`는 약 5.0초 뒤 오류 **-10005 timeoutReached**로 실패했다. 2026-09-21 21:23:04 설치본(번들 바이너리 21:22, `dense_status` 노출 포함)에 대해 같은 프로브를 다시 실행했고 결과는 동일했다: `cua.getState()`는 실행 중 앱을 나열했지만 `cua.getApp("OpenKakao Jarvis")`는 약 5.08초 뒤 **-10005 timeoutReached**로 실패했다. 따라서 이번 재설치 이후에도 live UI 스크린샷은 확보하지 못했고, 메뉴바 패널을 여는 사용자 클릭 없이 스크린샷을 대체하지 않았다. 이를 `LSUIElement` 메뉴바 앱 특성에 기인한 것으로 보는 설명은 한 환경에서 1회 관측한 결과에 대한 미검증 attribution이며 원인은 격리되지 않았다. (이 attribution은 아래 `live UI 스크린샷 확보와 Computer Use attach 실패 원인 격리` 절의 실측으로 대체됐다: attach timeout은 visible window 개수로 재현·격리됐고, 같은 날 패널과 설정 창 스크린샷을 확보했다.) 당시 이 프로브에서는 live UI 스크린샷을 얻지 못했고 스크린샷 기반 크로스 체크로 대체하지 않았다(그 제한은 아래 절에서 닫혔다).

### 설치 번들 Python 스크립트 패키징 누락 — 2026-09-21 KST

2026-09-21 19:15:27에 source `4b25149`에서 설치되고 ad hoc signed 된 재설치 번들 `/Applications/OpenKakao Jarvis.app`은 `Contents/Resources/scripts` 아래에 21개 파일을 포함했지만 `scripts/mlx_serve_lifecycle.py`는 포함하지 않았다. 설치 번들의 자체 `scripts/auto-reply-menubar.py`를 provisioned menubar runtime으로 실행하면 line 54에서 `ModuleNotFoundError: No module named 'mlx_serve_lifecycle'`가 재현됐고 exit 1이었다.

menubar CPython runtime도 문서화된 `~/Library/Application Support/openkakao/runtimes/menubar/bin/python3.11` 경로에 없었다. `desktop/README.md`는 이 위치에 별도 provisioned CPython 3.11을 요구하며, 이번 작업에서 uv CPython 3.11.9 설치본으로 provision했다. 크기는 63 MB였고 `bin/python3.11`은 symlink가 아닌 regular Mach-O arm64 executable이었다. `python3.11 -E -B -s -c 'import sys; print(sys.version)'`은 3.11.9를 보고했다.

이 runtime과 repository copy의 스크립트를 함께 사용한 menubar command는 exit 0으로 끝났고, 39,553-byte JSON snapshot을 출력했다. top-level key에는 `available_chats`, `background`, `pipeline`, `open_jobs`, `ondevice_hardware`, `reply_model`, `watermark`가 포함됐으며 temporary state root에는 `gjc-agent/`와 `gjc-global-model-cache.json`이 생겼다.

이 누락은 기능 공백이지만 fail closed로 동작해 조용히 전송하거나 누출하지 않는다. 수정은 `desktop/src-tauri/src/resource_layout.rs`와 `desktop/src-tauri/tauri.conf.json`의 staging 목록에 `scripts/mlx_serve_lifecycle.py`를 추가하고, staged entry script의 로컬 Python module closure와 두 staging 목록의 일치를 검증하는 회귀 테스트를 추가하는 것이다. 수정 후 설치 앱 검증은 아래 parent 측정 절에 기록한다.

### 수정 후 설치 번들 재검증 (parent 측정) — 2026-09-21 KST

staging 수정을 포함한 pre-commit working tree(그 변경은 이후 commit `deca45a`로 기록됨)에서 `OPENKAKAO_SIGN_IDENTITY=- sh scripts/build-jarvis-desktop.sh`(exit 0)와 `sh scripts/install-jarvis-desktop.sh`로 2026-09-21 19:28:08에 재설치했다. `/Applications/OpenKakao Jarvis.app/Contents/Resources/scripts`는 22개 파일을 포함했고 `mlx_serve_lifecycle.py`는 repo 사본과 byte-identical이었다.

설치 번들 자체의 `scripts/auto-reply-menubar.py`를 provisioned menubar runtime으로 실행한 read-only 프로브(별도 temp state/logs root)는 exit 0이었고 stderr는 비어 있었으며 stdout은 39,552-byte JSON이었다. parsed snapshot은 `schema_version=3`, top-level key 31개였고 `pipeline`, `background`, `open_jobs`를 포함했다. LaunchAgent `com.openkakao.jarvis.desktop`은 `state = running`(pid 86344)이었다.

이 검증은 설치 번들의 Python bridge 경로에 대한 read-only 증거이며 live UI 스크린샷(Computer Use attach 불가), live KakaoTalk 전송, live model generation, Developer ID signing을 입증하지 않는다.

## 실제 카카오톡 DB read-only 동기화·색인 — 2026-09-21 KST

저장소에서 2026-09-21 11:21 KST에 빌드된 `target/release/openkakao-cli`(마지막 `src/` 변경 `bc46908` 2026-09-21 08:29 KST, 그 뒤 `src/` 커밋 0건)와 같은 날 커밋된 `scripts/auto_reply_knowledge_graph.py`, 고정 Python 3.11로 실제 카카오톡 DB에 대해 처음 실행했다. state/logs root는 전부 임시 디렉터리였고 원본 DB 파일은 어느 명령에서도 직접 열지 않았으며 KakaoTalk 전송은 없었다.

- `openkakao-cli local-chats --json`: exit 0, stderr 0 bytes, 채팅방 50개(그중 이름 있는 방 12개).
- `context-sync-local --chat-id <id> --chat <room> --db <temp>/context.sqlite3`: 이름 있는 방 12개 중 1개만 exit 0이었고 그 방은 430,080 bytes, `context_messages` 76행이었다. 나머지 11개는 exit 1과 `live context source lacks required style or timing samples`로 끝났다. 이름 없는 그룹방은 별도 측정에서 exit 0, stdout `Synchronized 6 local pages ... (authoritative=false)`, temp 색인 696,320 bytes였다.
- 그 색인을 `_reindex_all(state_root=<temp>/menubar, chat=<room>)`로 색인: 0.01초, `kg_entities` 2행, `kg_relations` 1행, `kg_meta.last_snapshot_status = "copy_ok"`, `last_index_error = ""`, `last_indexed_at` 기록됨. `k_hop_neighborhood(<root>, 2, 10)`은 노드 1개를 반환했다.
- dense endpoint가 없는 이 호스트에서 `last_dense_status = "unavailable:RuntimeError:local dense embedding unavailable"`였고 `retrieve_knowledge_bundle`은 `search_mode = "bm25_only"`였다. 같은 그래프에 in-process loopback `/v1/embeddings` stub을 물리자 `refresh_dense_index`가 `status = "indexed"`, `indexed = 2`를 반환하고 ANN 저장소에 `dense_vectors` 2행·`ann_buckets` 16행(엔티티 수 × 8 band)이 생겼으며 `search_mode`가 `"rrf"`로 바뀌었다. 프로덕션 호출자가 없어 도달 불가였던 RRF 경로가 재색인만으로 활성화된다는 뜻이다.
- 이 측정이 입증하는 것은 원본 DB 잠금 없이 복제본에서만 읽는 동기화·색인·k-hop·dense RRF 경로가 실제 데이터에서 동작한다는 점이다. 설치된 앱 UI 경로, KakaoTalk 전송, live 모델 생성, 실제 bge-m3 서버 가동은 여전히 미입증이다.

### 같은 측정에서 남은 두 제한 — 2026-09-21 KST

- Jarvis의 read-only 색인은 auto-reply의 authoritative 승격 게이트를 그대로 지난다. `src/context/mod.rs`의 `live_source_has_required_summaries`는 방마다 owner 스타일 샘플 1개 이상과 응답 타이밍 샘플 2개 이상을 요구하고, 못 채우면 exit 1로 끝나며 그 페이지의 색인 행이 롤백된다. 위 측정에서 이름 있는 방 12개 중 11개가 이 경로였고(temp 색인 `context_messages = 0`, `context_sources = 0`), 한 방은 메시지 199행이 먼저 커밋된 뒤 exit 1이었다. 스타일·타이밍 샘플이 없는 방은 그래프와 검색에 아무것도 남기지 않는다. 이 게이트는 발신 승인 경로를 지키는 장치이므로 Jarvis 색인만을 위해 완화하지 않았고, 완화 여부는 운영자 판단으로 남긴다.
- dense RRF는 유효한 loopback 임베딩 endpoint를 전제로 한다. 이 호스트에는 그 endpoint가 없었으므로(`127.0.0.1:8000` closed) live 하이브리드는 in-process stub으로만 검증했고, 해시 벡터 stub을 쓴 측정에서는 dense 후보가 ANN 버킷에 충돌하지 않아 `search_mode = "rrf"`이면서 후보가 0개였다. `search_mode`는 dense 조회가 성공했음을 뜻하지 dense 후보가 존재함을 뜻하지 않는다.

## dense 상태의 앱 노출과 설치 번들 재검증 — 2026-09-21 KST

commit `d96aa7f`에서 두 가지를 바꿨다. (1) `desktop/src-tauri/src/python_bridge.rs`의 `sanitize_knowledge_status()` allowlist에 `dense_status`(400자 상한, 제어문자 제거, 값이 없으면 `unknown`)와 `dense_indexed_at`(비음수 정수)를 추가하고 Rust 단위 테스트로 긴 문자열·제어문자·누락 필드·음수/실수/문자열 경계값을 고정했다. (2) `desktop/src/ui.ts`·`desktop/src/main.ts`의 카카오 DB 동기화·색인 카드에 `settings-sync-dense` 한 줄을 추가해 `dense: <status> · indexed_at <n>` 또는 `dense: 확인 불가`로 닫는다. 새 버튼·카드·토글은 만들지 않았고 메인 패널은 톱니바퀴 하나라는 규칙을 유지했다.

부모 재측정(고정 Python 3.11): CI focused 10개 모듈 **298 tests, OK**, `tests.test_auto_reply_menubar` **165 tests, OK**, `sh desktop/scripts/smoke.sh` exit 0으로 Vitest **78/78**, Rust **59/59**, `tsc`와 Vite production build 성공.

설치 번들 재검증: `OPENKAKAO_SIGN_IDENTITY=- sh scripts/build-jarvis-desktop.sh`(exit 0) 뒤 `sh scripts/install-jarvis-desktop.sh`로 2026-09-21 21:23:04에 재설치했고, 번들 바이너리(21:22)에서 `dense_indexed_at` 심볼이, 번들 `scripts/auto_reply_knowledge_graph.py`에서 `last_dense_status`가 확인됐다. 설치본의 `--action knowledge-graph-status`를 provisioned menubar runtime으로 실행한 read-only 프로브는 exit 0, stderr 0 bytes였고 payload에 `dense_status = "unavailable:RuntimeError:local dense embedding unavailable"`, `dense_indexed_at = 0`, `snapshot_status = "copy_ok"`가 들어 있었다. 같은 payload의 `node_count`/`edge_count`는 이 action이 노드 목록을 읽지 않기 때문에 설계상 0이며, 노드 목록은 별도 `knowledge-graph` action이 제공한다.

이 검증이 덮는 범위는 Python → menubar action → Rust allowlist 데이터 경로와 Rust/Vitest 단위 증거까지다. live UI 스크린샷은 여전히 확보하지 못했으므로 화면에 실제로 그려진 문구는 육안으로 확인하지 않았다.

### 설치 번들 프로브의 재현 경로 — 2026-09-21 KST

위 프로브는 제3자가 같은 값을 재현할 수 있도록 state root와 명령을 남긴다. 프로브에 쓴 state root는 `/var/folders/8d/nwv_19w124zbq0dxqx2r1jn40000gn/T/jarvis-installed-kg-h72ztio1/menubar`이고, 같은 임시 부모 아래 `/var/folders/8d/nwv_19w124zbq0dxqx2r1jn40000gn/T/jarvis-installed-kg-h72ztio1/context.sqlite3`(1,077,248 bytes)가 색인 원본으로 놓여 있었다. 실행한 명령은 다음과 같다.

```

    "/Users/twoimo/Library/Application Support/openkakao/runtimes/menubar/bin/python3.11" \
      "/Applications/OpenKakao Jarvis.app/Contents/Resources/scripts/auto-reply-menubar.py" \
      --action knowledge-graph-status \
      --state-root "/var/folders/8d/nwv_19w124zbq0dxqx2r1jn40000gn/T/jarvis-installed-kg-h72ztio1/menubar"
```

exit 0, stderr 0 bytes였고 stdout은 한 줄로 다음 payload였다.

```
{"dense_indexed_at": 0, "dense_status": "unavailable:RuntimeError:local dense embedding unavailable", "edge_count": 0, "edges": [], "grounded_nodes": 0, "indexed_at": 1789993434, "indexed_count": 1, "indexing_mode": "wal+isolated-copy+mode=ro+query_only", "node_count": 0, "nodes": [], "ok": true, "snapshot_status": "copy_ok", "stale": true}
```

그 state root의 `knowledge-graph.sqlite3` kg_meta 행은 `last_index_error`(빈 값), `last_snapshot_status=copy_ok`, `last_indexed_at=1789993434`, `last_dense_status=unavailable:RuntimeError:local dense embedding unavailable`였고, `knowledge-dense-ann.sqlite3`(32,768 bytes)와 `knowledge-graph.sqlite3`(53,248 bytes)의 mtime이 모두 2026-09-21 21:23 KST였다. 이 payload는 2026-09-21 21:23:04 재설치 뒤에 이 state root로 프로브를 다시 실행해 재확인했다.

체인을 다시 만드는 순서는 (1) 부모 디렉터리와 그 아래 `menubar` 하위 디렉터리를 만들고, (2) 부모의 `context.sqlite3`를 앱 자체 동기화로 채우고(`openkakao-cli context-sync-local --chat-id <id> --chat <방이름> --db <부모>/context.sqlite3`), (3) 설치 번들의 `scripts/auto_reply_knowledge_graph.py`를 `--reindex-once --state-root <부모>/menubar`로 실행하고, (4) 위 프로브 명령을 실행하는 것이다.

이 state root는 `mktemp -d` 방식 임시 디렉터리라 macOS가 정리할 수 있고 durable copy를 남기지 않았다. 그래서 값 자체보다 재현 경로와 명령을 남긴다. 빈 state root로 같은 프로브를 실행하면 `snapshot_status`와 `dense_status`가 모두 `unknown`이 된다(부모 관측). 최초 프로브 기록은 state root를 임시 디렉터리로만 적고 경로를 남기지 않았으며, 이 절이 그 공백을 닫는다.

재색인·dense ANN 갱신 단계와 dense 상태의 설정 노출 경로는 두 개의 sequence 다이어그램으로 기록했다. [재색인·dense ANN 갱신 시퀀스](graphrag-reindex-dense-refresh.html)는 `validate sequence --quality showcase`에서 9/9 artifact checks, 0 errors / 0 warnings, `deliver` artifact SHA-256 `4ae11d79e98d2dd9c975626e18a631f54f14a9b3cd0421eb7c05f00579908c3b`(806,740 bytes), 표준 `visual-check` `status="pass"`와 diagnostics 0, 1440x900·1600x1000·1920x1080·2048x1320 light containment와 1440x900·2048x1320 light/dark capture를 기록했다. 빈 그래프에서는 dense 연결 없이 `last_dense_status="empty"`·`last_dense_indexed_at=0`으로 닫히는 분기도 함께 담았다. [dense 상태 노출 경로 시퀀스](graphrag-dense-status-exposure.html)도 같은 검사에서 9/9 artifact checks, 0 errors / 0 warnings, artifact SHA-256 `7cc3b096f4b5b4f2b0a35e931b86ef6a0749d329dc922406a09e924250ed2078`(803,834 bytes), `visual-check` `status="pass"`, diagnostics 0을 기록했다. 두 다이어그램은 1080x560과 1080x500 viewBox로 나눠 dense 단계의 실패 분기와 상태 노출 읽기 경로를 각각 담았고, 발광·네온 계열 표현은 쓰지 않았다.

## live UI 스크린샷 확보와 Computer Use attach 실패 원인 격리 — 2026-09-21 KST

앞선 `Computer Use attach 프로브 (negative)` 절이 남긴 제한(live UI 스크린샷 미확보)을 이 절에서 닫는다. 2026-09-21 KST에 설치본 `/Applications/OpenKakao Jarvis.app`(번들 바이너리 mtime 2026-09-21 21:22, `CFBundleShortVersionString=0.1.0`, `LSUIElement=1`)에서 메뉴바 패널과 설정 창이 실제로 렌더한 스크린샷과 AX 트리를 확보했다. 대상 프로세스는 pid 50870, 기동 시각 21:23:15였고 이 프로브는 어떤 프로세스도 종료하거나 재시작하지 않았다.

### 원인 격리 — attach timeout은 visible window 개수 문제였다

`-10005 timeoutReached`는 메뉴바 앱 성격이나 `LSUIElement` 때문이 아니라 attach 시점의 visible window 개수 때문이었다. `desktop/src-tauri/src/main.rs`는 패널 창(`label() == "jarvis"`)이 포커스를 잃으면 즉시 hide한다.

```rust
WindowEvent::Focused(false) if window.label() == "jarvis" => {
    let _ = window.hide();
}
```

따라서 사용자가 패널이나 설정 창을 열어 두지 않은 평소 상태에서 그 프로세스의 창은 0개다. 같은 앱에 대해 조건만 바꿔 측정한 결과는 다음과 같다.

| 조건 | `cua.getApp` 결과 |
| --- | --- |
| visible window 1개(패널 또는 `Jarvis 설정` 창) | 경로 지정 **69 ms** 성공 · 표시 이름 지정 **43 ms** 성공 |
| visible window 0개(`AXCloseButton` 클릭 후 창 수 0) | **5.05초** 뒤 `Computer Use server error -10005: timeoutReached` |

즉 경로 조회와 표시 이름 조회 모두 창이 있으면 즉시 붙고, 창이 0개면 둘 다 5초 timeout으로 닫힌다. 종전 절이 적은 `LSUIElement` 기인 설명은 미검증 attribution이었고 이 실측으로 대체한다. `LSUIElement=true`는 Info.plist 사실로 남지만 Dock·앱 전환기 노출을 끄는 accessory 설정이며 attach timeout의 원인이 아니었다.

### 패널 열기 재현 절차

상태 아이템 위치는 AX로 읽고 클릭은 합성 HID 이벤트로 보낸다. AppleScript의 `click menu bar item 1 of menu bar 2`와 `perform action "AXPress"`는 success를 반환했지만 Tauri tray handler를 발화시키지 못했고 창 수는 0으로 남았다(handler가 `MouseButtonState::Up`, 즉 실제 마우스 up 이벤트를 요구한다).

```bash
osascript -e 'tell application "System Events" to tell process "openkakao-jarvis-desktop" to get {position, size} of menu bar item 1 of menu bar 2'
# -> 1030, 3, 36, 24

swiftc -O -o /tmp/jarvis_click /tmp/jarvis_click.swift
/tmp/jarvis_click 1048 15
# -> posted click at 1048.0,15.0
# -> System Events count of windows: 0 -> 1, window size 276x260
```

```swift
// /tmp/jarvis_click.swift
import CoreGraphics
import Foundation

let args = CommandLine.arguments
guard args.count >= 3, let x = Double(args[1]), let y = Double(args[2]) else {
    FileHandle.standardError.write("usage: clicker x y".data(using: .utf8)!)
    exit(2)
}
let point = CGPoint(x: x, y: y)
guard let move = CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: point, mouseButton: .left),
      let down = CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown, mouseCursorPosition: point, mouseButton: .left),
      let up = CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp, mouseCursorPosition: point, mouseButton: .left) else {
    FileHandle.standardError.write("event create failed\n".data(using: .utf8)!)
    exit(3)
}
move.post(tap: .cghidEventTap)
usleep(40_000)
down.post(tap: .cghidEventTap)
usleep(80_000)
up.post(tap: .cghidEventTap)
print("posted click at \(x),\(y)")
```

그 뒤 `cua.getApp("/Applications/OpenKakao Jarvis.app")`은 약 0.1초에 붙고 `getScreenshot()`과 `getAXState()`를 그대로 쓸 수 있다.

### 관측 결과 — 패널

패널은 276x260(`desktop/src-tauri/tauri.conf.json`의 패널 크기와 일치)이며 캡처는 [jarvis-live-panel.png](jarvis-live-panel.png)다. AX 트리는 `0 standard window` → `1 scroll area` → `2 HTML content`(URL `tauri://localhost`) → `3 container Jarvis` → `4 image Jarvis core` + `5 button 설정 열기`였다. 창 안의 조작 요소는 톱니바퀴 버튼 1개뿐이다. 렌더는 어두운 배경 위 샴페인 골드 구체 코어, 다중 동심원 짐벌 링, 얇은 시냅스 네트워크, 작은 입자로 관측됐고 bloom 후처리나 발광 텍스트는 없었다.

### 관측 결과 — 설정 창

`5 button 설정 열기` 클릭은 별도 창 `Jarvis 설정`(760x760, `tauri://localhost/index.html?view=settings`)을 연다. `getAXStateAndScreenshot()`으로 트리와 이미지를 함께 받았다(760x760, [jarvis-live-settings.png](jarvis-live-settings.png)). 그 창의 내용은 아래가 전부다.

| 카드 | 값 |
| --- | --- |
| 대상 채팅방 | pop up `NIMDA 인수인계 임원방 ⚠` · `등록 3 · live 3 · 본문 미전달` |
| AI 모델 | toggle `Flash-Next ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit 기본 상주` = on · toggle `Qwen3.8 27B ddalcu/Qwen3.8-27B-MLX-Serve-4bit 온디맨드 스왑 · 미로딩` = off |
| AI 모델 상태 | `현재 선택: ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit` · `외부 소유 · 27B 전환 차단` · `외부 런타임이 11234 포트를 점유 중 · 앱은 시작/중지하지 않습니다` · `온디바이스 감지: Apple M5 Max (128GB RAM) · MLX Core/Serve · Qwen3.8 Flash-Next · 설정 검증 통과 · 실추론 통과 (Qwen3.8 Flash-Next)` |
| Voice | `음성 런타임 상태를 아직 받지 못했습니다.` · `호출어: 헤이 자비스` · `임계값: 0.65 고정` · `한국어 커스텀 헤드: bundled ONNX 선택됨 (TTS 보정, 사람 음성 일반화 아님)` · `마이크 세션 시작` 버튼 1개 |
| 카카오 DB 동기화 · 색인 | `동기화: stale` · `백그라운드 · 답변 대기 0 · 긱뉴스 idle · DB 동기화 stalled` · `격리 복제: copy_ok` · `색인 모드: wal+isolated-copy+mode=ro+query_only` · `마지막 색인: 1789986516 · indexed 31` · `dense: unknown` |
| DREAM-RSI | `status: evaluated · selected_policy: mirror_prompt_tail` · `gold_rows: 400 · gold_source_policy: human_only` |
| Knowledge | `GraphRAG · stale` · `E-R-E 50 nodes · 341 relations · 화면 최대 24 nodes` · 홀로그램 image 1개 · `overview · 최대 24 nodes` · 비활성 `+1 hop` |
| GeekNews 슬롯 | `아침 대기 점심 대기 저녁 대기` |
| History | `최근 12건 · 본문·프롬프트 제외` + 12개 행(`검색 ok` 11개, `검색 error` 1개) |

이 창에는 대량 검증, 기능 점검, 권한 관리, 자기개선 조작 화면이 없다. `dense: unknown`은 그 state root에 dense 결과 기록이 아직 없다는 뜻이며, 같은 시각 `http://127.0.0.1:8000/v1/embeddings`는 curl 응답 `000`(closed)이었다. 그래서 이 실행 경로의 검색은 여전히 BM25 한정이다.

프로브 중 AX 창 목록에는 정체를 확인하지 못한 66x20 창 하나(`AXTitle` 없음, 위치 586,181)가 함께 있었고 설정 창을 닫자 함께 사라졌다. 이 창은 패널이 아니며(크기가 276x260과 다르다) 기능 판정에 쓰지 않았다.

### 캡처 경로와 도구 제약

`getScreenshot()`이 돌려준 버퍼는 PNG가 아니라 JPEG(`ff d8 ff e0 00 10 4a 46 49 46`)였다. 이 호스트에서 `screencapture -x /tmp/jv_probe.png`는 실행 후 파일을 만들지 않았다. 그래서 화면 기록은 Computer Use 버퍼만 사용했고, 파일로 남기려고 두 파일명만 허용하는 allowlist를 둔 loopback sink(`127.0.0.1:8799`)에 POST한 뒤 `sips -s format png`로 PNG로 변환했다. sink는 이 호스트 loopback에만 바인딩했고 작업 후 종료했다. 남긴 파일은 패널 276x260(72,037 bytes)과 설정 760x760(199,920 bytes)이다.

### 이 절의 한계

이 증거는 설치된 Tauri 빌드의 UI 렌더 결과, 창 생명주기, 설정 표면 구성에 대한 것이다. live model generation, live KakaoTalk 전송, Developer ID 서명·notarization, 사람 음성에 대한 한국어 호출어 일반화는 입증하지 않는다. 또 `cua.getApp`이 성공하려면 대상 앱에 최소 1개의 visible window가 있어야 한다는 제약을 이 실측 범위에서 기록한다. 창이 0개인 메뉴바 상주 앱은 Computer Use로 attach할 수 없으므로 같은 확인을 다시 하려면 위 클릭 절차로 패널을 먼저 열어야 한다.

## 실제 loopback 임베딩 endpoint에서의 live dense ANN·RRF 검증 — 2026-09-21 KST

이 호스트의 외부 소유 `mlx-serve`(`127.0.0.1:11234`)가 `/v1/embeddings`를 실제로 제공한다는 것을 확인하고, 같은 그래프에서 dense ANN 색인과 RRF 하이브리드 검색을 실제 임베딩으로 재현했다. in-process stub이 아니라 살아 있는 loopback 서버를 쓴 첫 측정이다. 대상 원본은 앱 자체 state root의 `bujamentor/knowledge-graph.sqlite3`이며 sqlite backup API로 임시 state root에 read-only 복제만 했고 원본에 write하지 않았다. 앱과 외부 `mlx-serve`의 설정·프로세스는 바꾸지 않았다.

- endpoint 프로브: `POST http://127.0.0.1:11234/v1/embeddings` body `{"model": "BAAI/bge-m3", "input": [...]}` → HTTP 200, 각 row에 `index`와 2560차원 `embedding`, `usage.prompt_tokens` 반환. 앱 클라이언트가 요구하는 `index`·`embedding`·차원 일치 조건을 만족한다.
- 복제한 그래프는 `kg_entities` 50행, `kg_relations` 341행으로 설정 카드의 `E-R-E 50 nodes · 341 relations`와 일치했고 `kg_meta.last_dense_status`는 `unavailable:RuntimeError:local dense embedding unavailable`였다.
- 엔티티 밀집 텍스트 50건은 총 9,154자(최대 396자, `usage.prompt_tokens` 5,702)였다. 요청당 지연 실측은 1건 2.2초, 8건 0.837초, 32건 4.864초, 50건 8.314초였고 idle 서버의 첫 요청은 12.0초였다.
- 기본값 `batch_size=32`는 `DENSE_EMBEDDING_TIMEOUT_SECONDS=4.0`을 넘겨 `refresh_dense_index`가 `{"indexed": 0, "reason": "unavailable:RuntimeError:local dense embedding unavailable", "status": "unavailable"}`로 fail-closed했고 `dense_vectors` 0행·`ann_buckets` 0행이 남았다. 실제 endpoint가 있어도 기본 배치 크기로는 이 그래프의 dense 색인이 완료되지 않는다는 뜻이다.
- 같은 그래프를 `batch_size=1`로 색인하면 19.814초에 `{"indexed": 50, "status": "indexed", "watermark": "1789993399"}`를 반환했고, `kg_meta.last_dense_status = indexed:50`, `last_dense_indexed_at = 1789999715`, ANN 저장소에 `dense_vectors` 50행·`ann_buckets` 400행(50 × 8 band)·`dim` 2560이 생겼다.
- 그 색인으로 `retrieve_knowledge_bundle`을 호출하면 네 개 질의가 모두 `search_mode = "rrf"`였다. `"알쫀쿠"`는 `candidate_count 23`, `"가성비 좋은 클라우드 추천"`·`"야외 러닝 사진 자주 올리는 사람"`·`"누가 마라톤 훈련 기록을 공유하나"`는 각각 `candidate_count 40`이었고 `entities_count` 12–13, `relations_count` 3, `index_version = unit4-rrf-bm25-dense-v1`, `watermark = 1789993399`였다.
- RRF가 실제로 기여한 부분도 관측됐다. `"알쫀쿠"`에서 BM25는 `ent:tech:alizonku`·`ent:person:moon_seunghyun` 2건만 찾았지만 dense 후보 23건에는 `person:변우중:최연우`, `chat:변우중`, `topic:computer_use`가 섞여 있었고, `"누가 마라톤 훈련 기록을 공유하나"`에서는 BM25 후보에 없던 `ent:person:choi_yeonwoo`가 dense 상위에 올랐다.

이 절이 입증하는 것은 살아 있는 loopback 임베딩 서버에 대해 dense ANN 색인과 RRF 결합이 실제로 동작한다는 점이다. 남은 결함은 기본 배치 크기와 요청당 timeout의 불일치이며, 그 수정과 수정 후 검증은 바로 아래 절에 기록한다. live 모델 생성, live KakaoTalk 전송, 클라우드 폴백 제거 상태의 최종 서명·notarization은 이 절의 범위가 아니다.
## dense 배치 크기·timeout 정책 수정과 stub 재검증 — 2026-09-21 KST

직전 절에서 실측한 기본 `batch_size=32`와 `DENSE_EMBEDDING_TIMEOUT_SECONDS=4.0`의 불일치를 수정했다. 변경 파일은 `scripts/auto_reply_knowledge_graph.py`와 `tests/test_auto_reply_knowledge_graph.py` 두 개이다. 커밋 대상 모듈 리비전의 sha256은 `a1ec95b7e0520b2160b008033aef8a25f8b020dde0f4936bfe906a9b99b93896`, 수정 전 리비전은 `93ee760e2c2a137e39aa6a367f9a585a65221afdf0f2944cb5d67ac0db1e05fe`였다.

- `refresh_dense_index`의 기본 `batch_size`를 32에서 8로 낮췄다. 실측 요청 지연이 8건 0.837–1.283초, 32건 4.864초였으므로 8건이 4.0초 예산 안에 들어온다. 요청당 timeout 기본값 4.0초는 그대로 유지했다.
- `_local_dense_embeddings`의 실패를 `_DenseEmbeddingTimeoutError`(배치 분할로 회복 가능)와 `_DenseEmbeddingPermanentError`(즉시 fail-closed)로 나눴다. 응답 형식·차원·row index·중복 index 문제는 permanent로 분류되어 분할 재시도를 일으키지 않는다. 두 클래스 모두 `RuntimeError` 하위이고 메시지 텍스트가 기존과 같아 `last_dense_status`의 `unavailable:RuntimeError:...` 형태가 유지된다.
- 새 함수 `_local_dense_embeddings_adaptive`가 timeout에서만 배치를 절반으로 나눠 재시도하고 순서를 보존한다. 시도 상한은 최초 배치 크기 N당 `2N-1`이며 singleton까지 timeout이면 그대로 fail-closed한다.
- 실패 경로의 의미는 바뀌지 않았다. dense 재구성은 `BEGIN`부터 `commit`까지 단일 트랜잭션이므로 실패 시 롤백만으로 직전 색인이 그대로 남고, 실패를 이유로 이전 ANN 색인을 삭제하지 않는다. `last_dense_status`에는 `unavailable:...`이 기록된다.
- `indexed != len(rows)`이면 오류로 닫는 방어를 추가해, 행 수가 어긋난 부분 색인이 조용히 저장되지 않게 했다.

검증은 고정 Python 3.11에서 실행했다. 커밋 대상 리비전에서 `tests.test_auto_reply_knowledge_graph`와 `tests.test_auto_reply_menubar`를 함께 실행해 **233 tests, OK**(54.606초), CI focused 10개 모듈(`tests.test_auto_reply_ondevice`·`tests.test_auto_reply_reference_search`·`tests.test_auto_reply_knowledge_graph`·`tests.test_jarvis_unit4`·`tests.test_jarvis_desktop_launchers`·`tests.test_local_mlx_model_readiness`·`tests.test_verify_local_models`·`tests.test_mlx_serve_lifecycle`·`tests.test_auto_reply_dream_rsi`·`tests.test_dream_rsi_alphaxiv`)을 실행해 **305 tests, OK**(15.581초)를 확인했다. 같은 리비전의 live dense 전후 비교는 아래와 같다(같은 스크립트 `/tmp/jarvis_dense_default_check.py`, 같은 그래프 = 앱 자체 state root의 read-only 복제본 50 entities, 같은 endpoint).

| 시점 | 모듈 sha256 | 기본 refresh | ANN 저장소 | 네 질의 mode |
| --- | --- | --- | --- | --- |
| 수정 전 | `93ee760e2c2a137e39aa6a367f9a585a65221afdf0f2944cb5d67ac0db1e05fe` | 4.009초 `unavailable` | `dense_vectors` 0 | 전부 `bm25_only` (candidate 2–4) |
| 수정 후 | `ca2e1c1ff6437993bed204a2959f764d690948f77a3d09b75a26803893ca54b0` | 11.533초 `indexed:50` | `dense_vectors` 50 · `ann_buckets` 400 | 전부 `rrf` (candidate 24–40, entities 7–13) |

위 `수정 후` 행은 색인 행 수 방어를 추가하기 전 작업 트리 리비전에서 얻은 값이다. 방어까지 포함한 커밋 대상 리비전 `a1ec95b7…`에 대한 live 재측정은 처음에 외부 `mlx-serve`가 포화되어(아래 cold 지연 참조) 얻지 못했으나, 서버가 데워진 뒤 같은 스크립트(`/tmp/jarvis_dense_default_check.py`, 같은 read-only 복제본 50 entities, `OPENKAKAO_LOCAL_EMBEDDING_URL=http://127.0.0.1:11234/v1/embeddings`)를 다시 실행해 성공했다: `refresh_default_s 10.269`, `{"status": "indexed", "indexed": 50, "watermark": "1789993399"}`, `last_dense_status`가 `unavailable:RuntimeError:local dense embedding unavailable`에서 `indexed:50`으로 바뀌었고 `dense_vectors` 50/50·`ann_buckets` 400이 생성됐으며 네 질의가 모두 `search_mode "rrf"`(candidate 24–40, entities 7–13, relations 3)였다. 같은 실행 직전의 endpoint 프로브는 1건 6.693초(콜드)·8건 0.2초(웜)로, 식은 서버의 첫 요청은 여전히 4.0초 예산을 넘는다는 점도 함께 재현됐다.

### 커밋 대상 리비전의 real-HTTP stub 재검증 — 2026-09-21 KST

`/tmp/jarvis_dense_stub_check2.py`는 loopback `127.0.0.1:8791`에 `ThreadingHTTPServer`를 띄워 `POST /v1/embeddings`에 `{"model": "BAAI/bge-m3", "input": [...]}`를 받고, 실측 분당 지연을 재현해 2560차원 벡터와 `index`를 돌려준다. 앱 코드는 수정 없이 그대로 쓰고, 모듈 sha256·timeout·기본 배치를 함께 출력해 대상 리비전을 고정했다.

| 케이스 | 요청당 지연 | HTTP 호출 | 결과 | ANN 저장소 | `last_dense_status` |
| --- | --- | --- | --- | --- | --- |
| warm 50 | 0.05초/건 | `[8,8,8,8,8,8,2]` | 4.977초 `indexed:50` | `dense_vectors` 50 · `ann_buckets` 400 | `indexed:50` |
| slow 16 | 0.6초/건 | `[8,4,4,8,4,4]` | 18.419초 `indexed:16` | `dense_vectors` 16 · `ann_buckets` 128 | `indexed:16` |
| cold 3 | 5.0초/건 | `[3,1]` | 8.007초 `unavailable` | 직전 `dense_vectors` 1행 유지 · `ann_buckets` 0 | `unavailable:RuntimeError:...` |
| permanent 8 | 0.05초/건(행 1건 누락 응답) | `[8]` | 0.413초 `unavailable` | `dense_vectors` 0 | `unavailable:RuntimeError:local dense embedding response mismatch` |

- warm 케이스는 기본 배치 8이 그대로 유지되고 50건이 7회 호출로 전부 색인되는지 고정한다. 응답 순서가 뒤섞여도 `index`로 재배열해 `ent:test:000`부터 순서대로 영속된다.
- slow 케이스는 8건이 4.0초 예산을 넘을 때만 배치가 4+4로 갈라지고(추가 재시도 없이 6회 호출), 전체 16건이 순서대로 색인되는지 고정한다.
- cold 케이스는 배치 3이 timeout되고 singleton까지 timeout되면 더 분할하지 않고 fail-closed하는지, 그리고 실패가 트랜잭션 롤백으로 닫혀 미리 넣어 둔 직전 `dense_vectors` 1행(`ent:prior:001`)이 삭제되지 않고 남는지 고정한다. `ann_buckets`는 0행이므로 부분 색인이 남지 않는다.
- permanent 케이스는 응답 행 수가 요청과 다른 경우 분할 재시도 없이 단일 호출로 즉시 닫히는지 고정한다.

이 수정으로 닫히지 않는 외부 요인도 있다. 같은 날 idle 이후 첫 요청 지연을 직접 측정했는데 첫 요청 60.008초 timeout, 다음 요청 26.341초, 이후 0.475초였고(같은 조건의 앞선 측정에서는 첫 요청 12.0초), 8건 1.283초·50건 13.751초였다. 서버가 식은 직후에는 singleton 요청조차 4.0초 예산을 넘으므로 배치 정책으로는 회복할 수 없고, 이때는 fail-closed로 닫혀 `last_dense_status`에 `unavailable:...`이 남는다. 재색인 판정은 `now - last_updated >= reindex_interval_seconds`(모듈 기본 300초)이고 `last_indexed_at`은 dense 성공 여부와 독립적으로 그래프 색인이 성공할 때만 찍히므로, dense 단계는 그래프 내용이 바뀌지 않아도 매 재색인 사이클(최대 300초 간격)마다 다시 시도된다. 이 값들은 외부 프로세스 상태에 의존하며 앱은 그 프로세스를 시작·정지·전환하지 않는다. 이 절은 live model generation이나 live KakaoTalk 전송을 입증하지 않는다.


## 로컬 모델 생성·전환 상태 재검증 — 2026-09-21 KST

`scripts/verify_local_models.py`의 bounded localhost 프로브(`LOCAL_BASE_URL = http://127.0.0.1:11234/v1`)로 두 고정 모델을 다시 확인했다.

```bash
/Users/twoimo/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11 scripts/verify_local_models.py \
  --model ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit \
  --model ddalcu/Qwen3.8-27B-MLX-Serve-4bit \
  --timeout 60 --owner --json
```

결과는 exit 1(두 모델 모두 `ok`는 아님), stderr 0 bytes, stdout은 한 줄 JSON이었다.

```json
{"ok": false, "owner": "model_owner_unmanaged", "results": [{"elapsed_ms": 15842, "generation": true, "model": "ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit", "readiness": true, "reason": "ok"}, {"elapsed_ms": 2, "generation": false, "model": "ddalcu/Qwen3.8-27B-MLX-Serve-4bit", "readiness": false, "reason": "model_not_ready"}]}
```

- Flash-Next는 `readiness true`·`generation true`·`reason ok`, 15,842 ms였다. 앱 자신의 probe 경로로 실제 로컬 생성을 재현한 값이며, 설치 앱 설정 카드의 `실추론 통과` 표기와 독립적으로 같은 결론을 낸다.
- 27B는 `readiness false`·`generation false`·`reason model_not_ready`, 2 ms였고 `owner = model_owner_unmanaged`였다. 외부 `mlx-serve`가 11234를 점유하므로 앱은 시작·정지·전환을 하지 않고 fail-closed하며, 이는 설정 카드의 `외부 소유 · 27B 전환 차단`과 일치한다.
- 설정 카드의 `실추론 통과 (Qwen3.8 Flash-Next)` 문자열 출처는 `~/Library/Application Support/openkakao/bujamentor/ondevice-last-probe.json`에 영속된 레코드다: `timestamp 2026-09-19T18:47:40+00:00`, `engine mlx-serve-gateway`, `model ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit`, `latency_ms 15617`, `ok true`, `preview OK`(파일 mtime 2026-09-20 03:47 KST). 카드 문구는 과거에 성공한 게이트웨이 생성을 가리키는 기록이며, 위 프로브가 그 결론을 새 시각에 재현했다.
- 이 절은 Flash-Next 텍스트 생성만 확인한다. 27B 생성과 비전, 실제 카카오톡 전송, Developer ID 서명·notarization은 여전히 미입증이다.

## 숨김 상태 렌더 루프 정지의 live CPU 확인 — 2026-09-21 KST

설치본(pid 50870)에서 메뉴바 패널을 실제 클릭으로 열고 닫으면서 프로세스 CPU를 2초 간격 6회 샘플링했다. 상태 아이템 클릭은 Tauri `toggle_panel`을 발화시키므로 같은 좌표를 다시 클릭하면 닫힌다.

| 상태 | `count of windows` | `ps -p 50870 -o %cpu=` 샘플 6회 |
| --- | --- | --- |
| 패널 열림(유휴) | 1 | 3.0, 3.0, 2.9, 3.1, 2.9, 3.0 |
| 패널 닫힘 | 0 | 0.0, 0.0, 0.0, 0.0, 0.0, 0.0 |

패널을 닫은 뒤 12초 동안 프로세스 CPU는 6회 모두 0.0이었다. 이 값은 macOS `ps`의 감쇠 평균이라 순간값이 아니고 프로세스 전체를 재는 값이므로 RAF 호출 수를 직접 세는 증거는 아니다. 창 숨김 시 미해결 RAF 취소와 render-count delta 0을 직접 측정한 기존 기록은 그대로 유효하며, 이 절은 같은 결론을 설치본 CPU에서 독립적으로 확인한 보조 증거다.

이 프로브도 어떤 프로세스를 종료·재시작하지 않았고 앱 설정을 바꾸지 않았다. 닫힘 상태 샘플 뒤 패널은 다시 열지 않았으므로 프로브 종료 시점에 창은 0개였다.

## 설치 번들의 dense endpoint 정렬과 콜드 스타트 허용 — 2026-09-22 KST

설치본이 스스로 dense 색인을 끝내지 못하는 결함을 찾아 원인을 분리하고, 두 번의 수정 뒤 설치 번들에서 다시 읽어 확인했다.

### 결함과 원인 — 2026-09-22 00:13 KST 설치본

- 2026-09-22 00:13:48에 설치한 번들(모듈 sha256 `a1ec95b7e0520b2160b008033aef8a25f8b020dde0f4936bfe906a9b99b93896`, `batch_size` 기본 8)은 앱 자신의 state root(`~/Library/Application Support/openkakao/bujamentor`)에서 그래프 재색인을 마치고도 `last_dense_status = unavailable:RuntimeError:local dense embedding unavailable`, `dense_vectors` 0행을 남겼다.
- 원인은 배치·timeout이 아니라 endpoint 기본값이다. 당시 `DENSE_EMBEDDING_URL` 기본값은 `http://127.0.0.1:8000/v1/embeddings`였고, 이 호스트에서 `nc -z 127.0.0.1 8000`은 실패(닫힘), `11234`는 성공이다. LaunchAgent plist `~/Library/LaunchAgents/com.openkakao.jarvis.desktop.plist`에는 `EnvironmentVariables` 절이 없고 앱 프로세스 환경에도 `OPENKAKAO_LOCAL_EMBEDDING_URL`이 없어, 설치 앱은 항상 닫힌 포트만 호출했다.
- 반면 같은 앱의 온디바이스 경로는 이미 로컬 게이트웨이를 고정해 쓰고 있었다(`scripts/auto_reply_ondevice.py`의 `MLX_GATEWAY_BASE_URL = "http://127.0.0.1:11234/v1"`). 그 11234가 `/v1/embeddings`에서 `BAAI/bge-m3` 2560차원 벡터를 `index`와 함께 실제로 반환한다.

### 수정 1 — 기본 endpoint를 앱 자신의 게이트웨이로 정렬 (commit `5fcf47b`)

- `scripts/local_mlx_gateway.py`에 `MLX_GATEWAY_BASE_URL`과 `MLX_GATEWAY_EMBEDDINGS_URL`을 단일 출처로 두고, `auto_reply_knowledge_graph.py`의 dense 기본값과 `auto_reply_ondevice.py`가 같은 상수를 참조하게 했다.
- 새 모듈을 번들 resource allowlist에 등록해 설치본에도 포함되게 했다(`desktop/src-tauri/tauri.conf.json`, `desktop/src-tauri/src/resource_layout.rs`).
- `OPENKAKAO_LOCAL_EMBEDDING_URL` 오버라이드 우선순위, loopback-only 검증, fail-closed, `unavailable:RuntimeError:...` 상태 형식은 그대로다.
- 설치본 재검증(00:34:21 재설치): 설치 번들의 모듈을 앱 런타임(`~/Library/Application Support/openkakao/runtimes/menubar/bin/python3.11`)으로 import하면 `module_file = /Applications/OpenKakao Jarvis.app/Contents/Resources/scripts/auto_reply_knowledge_graph.py`, `DEFAULT_DENSE_EMBEDDING_URL`과 실제 해석값이 모두 `http://127.0.0.1:11234/v1/embeddings`, `timeout_s 4.0`, `batch_default`가 `batch_size` 8이었다.

### 수정 2 — 재색인 1회당 콜드 스타트 예산 1회 (commit `d2948e3`)

- endpoint가 정렬된 뒤에도 실패가 남았다. 같은 설치 런타임에서 `_local_dense_embeddings`를 직접 호출하면 1건 4.009초 `_DenseEmbeddingTimeoutError`, 8건 4.002초 같은 오류였다. 즉 게이트웨이가 생성 작업과 겹치거나 임베딩 모델을 다시 올릴 때는 singleton 요청도 4.0초 예산을 넘어 배치 분할로 회복할 수 없다.
- `DENSE_EMBEDDING_FIRST_ATTEMPT_TIMEOUT_SECONDS = 30.0`을 추가해 재색인 1회의 첫 HTTP 시도에만 이 예산을 쓰고, 이후 시도와 분할된 자식 요청은 기존 4.0초를 그대로 쓴다. `2N-1` 시도 상한, 순서 보존, permanent 실패는 분할하지 않는 규칙, singleton timeout의 즉시 fail-closed, 롤백만 수행하는 실패 경로는 바뀌지 않았다.

### 설치본 최종 readback — 2026-09-22 00:51:21 재설치

재빌드(`OPENKAKAO_SIGN_IDENTITY=- sh scripts/build-jarvis-desktop.sh`)와 재설치(`sh scripts/install-jarvis-desktop.sh`, 백업 `.../install-backups/jarvis-desktop/20260922T005121-98225`) 뒤, 설치 번들의 스크립트를 앱 provisioned 런타임으로 실행했다.

```
RB="$HOME/Library/Application Support/openkakao/runtimes/menubar/bin/python3.11"
ROOT="$HOME/Library/Application Support/openkakao/bujamentor"
"$RB" "/Applications/OpenKakao Jarvis.app/Contents/Resources/scripts/auto_reply_knowledge_graph.py" --reindex-once --state-root "$ROOT"
"$RB" "/Applications/OpenKakao Jarvis.app/Contents/Resources/scripts/auto-reply-menubar.py" --action knowledge-graph-status --state-root "$ROOT"
```

- 재색인은 exit 0, 11초였다.
- 앱 상태 action은 exit 0으로 다음 payload를 반환했다.

```json
{"dense_indexed_at": 1790005911, "dense_status": "indexed:50", "edge_count": 0, "edges": [], "grounded_nodes": 0, "indexed_at": 1790005905, "indexed_count": 31, "indexing_mode": "wal+isolated-copy+mode=ro+query_only", "node_count": 0, "nodes": [], "ok": true, "snapshot_status": "copy_ok", "stale": false}
```

- 앱 state root의 그래프·dense 저장소 실측: `kg_entities` 50, `kg_relations` 341, `last_index_error` 빈 값, `last_dense_status` `indexed:50`, `last_dense_indexed_at` `1790005911`, `dense_vectors` 50행, `ann_buckets` 400행, `dense_meta` `index_version=bge-m3-lsh-v1`·`watermark=1790005898`.
- 같은 설치 모듈로 질의 경로를 확인하면 해석 endpoint가 `http://127.0.0.1:11234/v1/embeddings`이고 네 개 한국어 질의가 모두 `search_mode "rrf"`였다: `"알쫀쿠"` candidate 24·entities 7, `"가성비 좋은 클라우드 추천"` 40·12, `"야외 러닝 사진 자주 올리는 사람"` 40·12, `"누가 마라톤 훈련 기록을 공유하나"` 40·13, relations는 모두 3이다. 설치 앱 자신의 그래프에서 dense 기반 하이브리드 검색이 확인된 첫 기록이다.
- 검증 명령: `tests.test_auto_reply_knowledge_graph` **72 tests, OK**, 11개 모듈 합계 **474 tests, OK**, `sh desktop/scripts/smoke.sh`의 Vitest **78/78**·Rust **59/59**·Vite build 성공.

### 이 절이 닫지 않는 것

- 30.0초도 부족한 포화 상태는 그대로 fail-closed다. 앞선 측정에서 첫 요청 60.008초 timeout 뒤 다음 요청 26.341초가 나온 사례가 있었고, 그 조건에서는 배치 정책으로도 dense 색인이 끝나지 않는다. 이때는 `unavailable:...`이 남고 다음 재색인 사이클(모듈 기본 300초)에서 다시 시도한다.
- 동시 실행 주의: 앱이 재색인 중일 때 같은 state root로 `--reindex-once`를 직접 실행하면 ANN 저장소에서 `unavailable:OperationalError:database is locked`가 기록됐다. fail-closed로 닫히고 직전 색인은 유지되지만, `collect_knowledge_graph` 경로의 재색인 가드와 달리 이 독립 실행 경로에는 가드가 없으므로 앱 재색인이 없는 시점에만 실행해야 한다.
- dense 경로는 여전히 앱이 소유하지 않는 외부 `mlx-serve` 프로세스의 상태에 의존한다. 앱은 그 프로세스를 시작·정지·전환하지 않는다.
- 이 절은 실제 카카오톡 전송, Qwen3.8 27B 생성, Developer ID 서명·notarization을 입증하지 않는다. 설치본 서명은 여전히 ad-hoc이다(`codesign -dv`에서 `flags=adhoc,runtime`, `TeamIdentifier=not set`).

### 재설치본 Computer Use 크로스 체크 — 2026-09-22 KST

- CUA 인벤토리에서 `OpenKakao Jarvis`(`com.openkakao.jarvis.desktop`)가 등록되고 `isRunning: true`임을 확인했다.
- 패널이 숨겨진 상태(창 0개)에서 `cua.getApp("OpenKakao Jarvis")`는 5.2초 뒤 `Computer Use server error -10005: timeoutReached`로 실패했다. 이는 포커스를 잃으면 패널을 숨기는 설계(`desktop/src-tauri/src/main.rs`의 `WindowEvent::Focused(false)`) 때문에 창이 0개인 상태의 attach가 실패한다는 기존 격리 결과를 재설치본에서 그대로 재현한 것으로, 새 빌드에서의 회귀가 아니다.
- 설치본은 LaunchAgent `gui/501/com.openkakao.jarvis.desktop`에서 `state = running`, `program = /Applications/OpenKakao Jarvis.app/Contents/MacOS/openkakao-jarvis-desktop`, `pid = 98353`(2026-09-22 00:51:32 시작)로 확인됐고, 앱 번들 설치는 00:51:21이다.
- 이번 세션의 커밋은 UI 코드를 바꾸지 않았으므로 2026-09-21에 캡처한 패널·설정 창 스크린샷을 그대로 근거로 쓴다. 새 스크린샷을 새로 만들었다고 주장하지 않는다.

### 재설치 뒤 남은 중복 인스턴스 — 2026-09-22 KST (미해결, 이후 readback으로 대체)

재설치 뒤 프로세스를 확인한 결과 `openkakao-jarvis-desktop`이 **두 개** 실행 중이었다.

| pid | ppid | 시작 시각 | 실행 경로 | LaunchAgent 추적 |
| --- | --- | --- | --- | --- |
| 18028 | 1 | 2026-09-22 00:24:58 | `/Applications/.openkakao-jarvis.previous.20260922T003421.52158.app/Contents/MacOS/openkakao-jarvis-desktop` (lsof 기준, 그 경로는 이미 삭제됨) | 아니오 |
| 98353 | 1 | 2026-09-22 00:51:32 | `/Applications/OpenKakao Jarvis.app/Contents/MacOS/openkakao-jarvis-desktop` | 예 (`launchctl print` pid) |

- pid 18028은 00:34:21 재설치가 만든 이전 번들 백업 경로에서 실행되고 있다. 그 번들은 설치 스크립트가 마지막에 `rm -rf`로 지웠지만 프로세스는 삭제된 경로를 그대로 물고 살아 있다. `ps` 기준 CPU는 두 프로세스 모두 0.0이고, 확인 시점에 둘 다 `knowledge-*` sqlite 파일을 열고 있지 않았다.
- 원인: `scripts/install-jarvis-desktop.sh`는 `launchctl bootout` 뒤 `wait_for_absent`로 **서비스**가 사라진 것만 확인하고, 남아 있는 **프로세스**는 확인하지 않는다. 그 다음 단계에서 이전 번들을 `rm -rf`한다. 그래서 bootout을 벗어난 인스턴스가 삭제된 번들에서 계속 실행된다.
- 영향: 같은 state root를 두 인스턴스가 색인하면 dense ANN 저장소에서 `unavailable:OperationalError:database is locked`가 기록될 수 있다. 이번 세션에서 관측한 lock 실패와 일치하는 시나리오다. 또한 메뉴바 상태 아이템과 폴러가 중복될 수 있다.
- 계획이 요구한 전환 조건(“중복 작업자가 없음을 확인한 상태”)은 이 호스트에서 현재 충족되지 않았다. 이 세션은 프로세스를 종료하지 않았다(기존 규칙: 프로세스를 죽이지 않는다). 운영자가 남은 인스턴스를 종료해야 한다.
- 권장 수정(다음 단계): 재설치 스크립트가 bootstrap 뒤에 대상 실행 파일 경로와 bundle id로 프로세스를 열거해 새 pid 외의 인스턴스가 남아 있으면 fail-closed로 보고하고, 이전 번들 삭제 전에도 같은 검사를 수행하도록 한다. 테스트는 기존 프로젝트 규칙대로 가짜 `launchctl`/`ps` 어댑터로 수행한다.

### 중복 인스턴스 가드 수정 — 2026-09-22 KST

- 라이브 재확인: 재설치 뒤 `openkakao-jarvis-desktop`이 두 개 실행 중이다. `launchctl print gui/501/com.openkakao.jarvis.desktop`은 `state = running`·`pid = 98353`만 보고하지만, `ps -axo pid,ppid,lstart,command`는 pid 18028(ppid 1, 00:24:58 시작)과 pid 98353(ppid 1, 00:51:32 시작)을 함께 보여준다. `lsof -p 18028`의 txt 세그먼트는 `/Applications/.openkakao-jarvis.previous.20260922T003421.52158.app/Contents/MacOS/openkakao-jarvis-desktop`이고 그 디렉토리는 `ls -d /Applications/.openkakao-jarvis*`가 no matches found를 반환하듯이 이미 삭제됐다.
- 경로 증거의 함정: 같은 프로세스에 대해 `ps -p 18028 -o comm=`는 `/Applications/OpenKakao Jarvis.app/Contents/MacOS/openkakao-jarvis-desktop`을 출력한다. macOS `ps`는 exec 시점의 argv 경로를 그대로 보여주며, 재설치 뒤 그 경로가 다시 유효해지므로 삭제된 번들에서 실행 중이라는 사실이 드러나지 않는다. 그래서 이번 가드는 프로세스 pid 동일성으로만 판정하고, 경로는 lsof(없으면 ps) 기반 진단 문구로만 쓴다.
- 가드 구현(commit `f2f1a77`): `scripts/install-jarvis-desktop.sh`가 `wait_for_pid`(10회, 0.5초 간격, 매 시도마다 `launchctl print` 재실행)로 새 pid를 얻은 뒤 `list_live_app_pids`(pgrep `-f`, 없으면 ps+awk)의 pid 집합에서 launchd pid와 자기 `$$`를 떼 나머지를 stray로 본다. stray가 있으면 이전 번들을 지우지 않고 exit 3으로 닫으며 각 stray pid와 진단 경로를 stderr에 출력한다. `OPENKAKAO_PS`/`OPENKAKAO_PGREP`/`OPENKAKAO_LSOF`로 재현 가능하고 프로세스를 죽이지 않는다.
- 라이브 열거 실측: 이 호스트에서 `/usr/bin/pgrep -f openkakao-jarvis-desktop`은 정확히 두 앱 pid만 반환했고(WebKit helper 등 오검출 없음), 따라서 이 가드는 지금 이 호스트에서 pid 18028을 stray로 판정하고 exit 3으로 닫는다. 남은 인스턴스 종료는 운영자 몫이며 이 세션은 프로세스를 죽이지 않았다.
- 테스트: `tests.test_jarvis_desktop_launchers` **10 tests, OK**(기존 6 + 신규 4). 신규 4개는 (1) 소스 계약 검사, (2) 가짜 어댑터 실행으로 stray 없음 exit 0·이전 번들 삭제, stray 있음 exit 3·pid 7777 보고·이전 번들 유지(진단 ps가 현재 설치 경로를 잘못 보고하는 경우 포함), (3) launchd pid가 3번째 print에서야 나오는 경합 exit 0, (4) pid가 끝까지 안 나옴 11회 시도 뒤 exit 3·이전 번들 유지.
- CI 회귀 발견 및 수정(commit `4b75c7e`): commit `5fcf47b`가 `auto_reply_ondevice.py`의 지역 상수를 `from local_mlx_gateway import ...`로 바꾼 뒤, `tests/test_auto_reply_ondevice.py`만 `scripts/`를 `sys.path`에 넣지 않아 `python3 -m unittest tests.test_auto_reply_ondevice`가 ModuleNotFoundError ‘local_mlx_gateway’로 죽었다. 이 모듈이 CI focused 목록의 첫 항목이라 작업 전체가 error 1건으로 실패했다. 다른 테스트 모듈과 같은 bootstrap을 추가한 뒤 CI focused 10개 모듈은 **313 tests, OK**다.
- 검증 명령: 고정 Python 3.11로 CI focused 10개 모듈을 `-m unittest`로 실행, `/bin/sh -n scripts/install-jarvis-desktop.sh`, `git diff --check`.
- 한계: 가드는 이름/argv 매칭이라 설치 시점에 같은 이름을 argv에 담은 무관한 프로세스가 있으면 false positive로 fail-closed할 수 있다(안전한 방향). 반대 방향의 false negative는 argv[0]이 exec 시점에 고정되므로 관측되지 않았다. 또한 이 호스트의 중복 인스턴스는 아직 살아 있어 전환 조건은 여전히 미충족이다.

### 테스트 하네스 bootstrap 누락 추가 수정 — 2026-09-22 KST

- `tests.test_jarvis_unit4`도 같은 누락이었다. 이 파일은 `scripts.auto_reply_knowledge_graph`를 import하고, 그 모듈은 `5fcf47b`가 넣은 bare `from local_mlx_gateway import MLX_GATEWAY_EMBEDDINGS_URL`를 갖는다. 그래서 단독 `python3 -m unittest tests.test_jarvis_unit4`는 `ModuleNotFoundError: No module named 'local_mlx_gateway'`로 죽었고, CI focused 목록에서는 앞선 `tests.test_auto_reply_reference_search`가 `scripts/`를 `sys.path`에 넣어 준 뒤라 통과했다(모듈 순서 의존). bootstrap을 추가한 뒤 단독 실행은 **24 tests, OK**이고 CI focused 10개 모듈은 **313 tests, OK**를 유지한다.
- 같은 유형을 전수 확인했다: `tests/test_*.py` 28개 중 `from scripts.` 형태로 import하면서 `sys.path` bootstrap이 없는 파일은 6개였고, 그 중 실제로 실패한 것은 `test_auto_reply_ondevice`와 `test_jarvis_unit4` 두 개다(나머지 4개는 bare sibling import가 없는 모듈만 import해서 안전하다). 수정 뒤 연관 7개 모듈(`test_auto_reply_ondevice`, `test_jarvis_unit4`, `test_auto_reply_dream_rsi`, `test_auto_reply_finetune`, `test_auto_reply_golden_dataset`, `test_dream_rsi_alphaxiv`, `test_mlx_serve_lifecycle`)을 각각 단독 실행해 모두 OK임을 확인했다.


### 열거 오류 fail-closed·rollback·복구 artifact 수정과 현재 호스트 readback — 2026-09-22 KST

- 독립 리뷰(commit `7d69b65` 기준)가 지적한 설치·전환 경로 결함을 수정했다. 대상은 `scripts/install-jarvis-desktop.sh`와 `tests/test_jarvis_desktop_launchers.py`다.
- 열거 오류 fail-closed: 종전 구현은 `"$PGREP" -f "$APP_EXECUTABLE" 2>/dev/null || true`였다. `pgrep`가 2/3으로 죽으면 `|| true`가 그 실패를 빈 출력으로 바꾸고, 빈 출력은 살아 있는 앱 프로세스 없음으로 해석돼 중복 가드가 조용히 통과했다. 이제 `pgrep` 종료 코드를 보존해 0만 출력을 신뢰하고(출력이 비면 2), 1은 일치 없음, 2/3과 기타는 `ps` fallback으로 간다. `ps` 실패나 PID 컬럼이 하나도 없는 파싱 불가 출력도 2다. `list_live_app_pids`가 2를 반환하면 `check_duplicate_guard`는 `cannot prove there is no duplicate instance; process enumeration is unknown`을 출력하고 rollback한다.
- 후보 확인: 열거된 각 pid는 `reported_app_path`(lsof txt, 없으면 `ps -p <pid> -o comm=`)로 확인하고, 경로가 `$APPLICATIONS_DIR` 밖임이 확증된 경우에만 제외한다. 해석되지 않은 후보는 남긴다.
- pid 출처: `launchctl print` 텍스트 파싱은 pid를 보고하지 않는 상태에서 fail-closed로 이어졌다. 이제 `launchctl kickstart -kp`가 반환한 pid를 우선 쓰고, `-kp`가 실패하거나 파싱되지 않을 때만 `kickstart -k`와 유계 `wait_for_pid`(10회, 0.5초)로 내려간다.
- rollback: `post_activation_failure`는 bootout, 이전 번들 복원 또는 신규 설치 제거, 이전 plist 복원 또는 설치 plist 제거를 수행하고 대상·plist·서비스 readback과 backup 경로를 stderr에 출력한 뒤 exit 3으로 닫는다. 이전 번들이 남아 있는 시점에만 호출된다.
- 복구 artifact: `com.openkakao.jarvis.desktop.installed.txt`는 중복 가드 통과 직후(이전 번들이 남아 있는 동안) 기록한다. 유계 대기 원문은 `.wait.txt`에 두고 `installed.txt`의 `wait-readback:` 절에 함께 묶는다. 실패 경로의 `rollback.txt`와 분리된다.
- 시그널: `trap cleanup EXIT`는 정리 전용이고, `HUP`·`INT`·`TERM`은 cleanup 뒤 각각 129·130·143으로 종료한다. 종전 `trap cleanup EXIT HUP INT TERM`은 시그널에서 종료하지 않았다.
- 신규 테스트 8건: pgrep 2/3 → `ps` fallback 정상, 열거 불명 rollback, 비파싱 `ps` rollback, stray rollback(app·service·plist), pid 미보고 rollback, 최초 설치 정상 경로의 stdout·stderr 정확성, 최초 설치 가드 실패 시 신규 상태 제거, `kickstart -kp` pid 사용 시 print 대기 없음과 `wait-readback` 기록.
- 재검증: `/bin/sh -n scripts/install-jarvis-desktop.sh` 통과, `git diff --check` clean, `tests.test_jarvis_desktop_launchers` **18 tests, OK**, CI focused 10개 모듈 **321 tests, OK**(고정 Python 3.11, 74.4초).

#### 현재 호스트 readback — 2026-09-22 KST

이전 절들이 기록한 인스턴스 pid 18028과 pid 98353은 이번 확인 시점에 존재하지 않는다. 저장소에 앱 인스턴스를 종료하는 코드가 없으므로 사라진 원인은 검증되지 않았다.

```
$ /usr/bin/pgrep -fl openkakao-jarvis-desktop
84125 /Applications/OpenKakao Jarvis.app/Contents/MacOS/openkakao-jarvis-desktop

$ launchctl list | grep -i jarvis
-	0	com.openkakao.jarvis.desktop
84125	0	application.com.openkakao.jarvis.desktop.286915917.286915922

$ launchctl print gui/501/com.openkakao.jarvis.desktop
gui/501/com.openkakao.jarvis.desktop = {
	state = not running
	program = /Applications/OpenKakao Jarvis.app/Contents/MacOS/openkakao-jarvis-desktop
	runs = 2
	last exit code = 0
	# 이 출력에는 pid = N 줄이 없다.
}

$ /bin/ps -p 84125 -o pid=,lstart=,etime=,%cpu=,rss=,comm=
84125 Tue Sep 22 01:44:30 2026 47:52 0.0 85296 /Applications/OpenKakao Jarvis.app/Contents/MacOS/openkakao-jarvis-desktop

$ launchctl print gui/501/application.com.openkakao.jarvis.desktop.286915917.286915922
	path = (submitted by runningboardd.418)
	type = Submitted
	managed_by = com.apple.runningboard
	state = running
	bundle id = com.openkakao.jarvis.desktop
```

- 현재 살아 있는 앱 인스턴스는 pid 84125 하나이고 그것은 LaunchServices(RunningBoard)가 제출한 job이다. 우리 LaunchAgent `com.openkakao.jarvis.desktop`은 `state = not running`·`runs = 2`·`last exit code = 0`이며 `launchctl list`에서 `-`로 표시된다. 중복 작업자는 없지만 그 단일 인스턴스는 launchd가 추적하지 않는다.
- 이 호스트에 지금 재설치하면 `check_duplicate_guard`는 84125를 stray로 판정하고 exit 3으로 rollback한다. 이는 의도한 fail-closed 동작이고, 운영자가 그 인스턴스를 종료해야 전환이 진행된다. 이 세션은 프로세스를 종료하지 않았다.
- `ps`의 argv와 경로는 exec 시점 값이며 executable identity의 authoritative 증거가 아니다. 앞선 절들이 lsof·ps 경로로 삭제된 번들 실행을 추적한 것은 호스트 관측이고, 가드 자체는 pid 동일성만 쓴다. 복사한 `/bin/sleep`으로 삭제 경로 argv를 재현하려던 시도는 코드서명 때문에 커널이 프로세스를 종료해 재현하지 못했으므로 그것도 재현 불가 호스트 관측으로만 기록한다.

### 설치·전환 수명주기 다이어그램 — 2026-09-22 KST

- `docs/architecture/jarvis-install-cutover-lifecycle.archify.json`(lane: main rail·번들 교체·rollback controller·결과)과 렌더 HTML을 추가했다. `validate lifecycle --quality showcase`는 9/9 artifact checks, 0 errors / 0 warnings이고 `deliver`는 artifact SHA-256 `f8066ddbe502215ff628035bdbad720826bef04cf466bfa7404f70d04e7e48f8`(811,997 bytes)로 성공했다.
- `visual-check`는 status `pass`다. 1440x900·1600x1000·1920x1080·2048x1320 light viewport에서 scrollWidth/scrollHeight가 viewport를 넘지 않고 projected node text 최소값이 6px 이상이며 legend·navigation dock 교차 면적이 0이다. sidecar는 `jarvis-install-cutover-lifecycle.visual-check.html`(contact sheet)과 `.visual-check.json`(receipt)이고 1440x900·2048x1320 light/dark PNG 캡처가 함께 남았다.
- 이 절의 수치는 자동 브라우저 증거이며 perceptual review는 별도다. receipt의 `visualReview`는 `pending`이고, 캡처를 직접 본 결과 겹침·잘림·빈 하단 밴드는 관측되지 않았다(사람 판단).
- 다이어그램은 코드 계약을 그린 것이며 이 절의 rollback 경로가 실제로 실행됐다는 증거가 아니다. 이 호스트에서 rollback은 실행하지 않았다(미추적 인스턴스가 있어 재설치가 rollback으로 닫히는 조건이지만 실행하지 않았다).
### 시그널 rollback·이전 런타임 복원·pid 신원 재확인 — 2026-09-22 KST

- 독립 리뷰(commit `df514f8` 기준)가 남긴 설치·전환 경로 결함을 수정했다. 대상 파일은 `scripts/install-jarvis-desktop.sh`와 `tests/test_jarvis_desktop_launchers.py`이고 커밋은 `577a7ad`다.
- rollback 멱등화: 실패 경로가 `perform_rollback <reason>` 하나로 모였다. `ROLLBACK_ATTEMPTED`로 두 번 실행되지 않고, 시작 직후 `trap '' HUP INT TERM`으로 rollback 도중의 신호를 무시한다. `post_activation_failure`와 신호 트랩이 같은 함수를 호출하므로 종전의 중복 rollback 경로가 사라졌다.
- 신호 rollback 확대: 종전 `signal_exit`는 `cleanup`만 하고 끝나, `launchctl bootout` 뒤 활성화 이전 구간에서 `HUP`·`INT`·`TERM`이 오면 우리 LaunchAgent와 legacy LaunchAgent가 내려간 채 남을 수 있었다. 이제 런타임을 건드리기 직전에 `RUNTIME_TOUCHED=1`을 두고, `signal_exit`는 `RUNTIME_TOUCHED`·`APP_ACTIVATED`·`JARVIS_PLIST_INSTALLED`·`JARVIS_BOOTSTRAPPED` 중 하나라도 참이면 rollback을 돌린다.
- 이전 런타임 복원: rollback은 종전에 활성 Jarvis bootout·번들 복원·plist 복원만 했다. 이제 이전에 로드돼 있던 `JARVIS_LOADED`·`LEGACY_LOADED` job을 각각 `launchctl bootstrap`으로 되돌리고, 옮겨 둔 `$LEGACY_LABEL.disabled.plist`를 `$LEGACY_PLIST`로 되돌린 뒤 `launchctl print "$DOMAIN"` 출력에서 두 label을 grep해 로드 여부를 `yes`/`no`/`unknown`으로 stderr에 남긴다. `print` 자체가 실패하면 `unknown`이고, 이는 로드되지 않았다는 뜻이 아니다.
- installed artifact: rollback은 성공 전환의 복구 artifact `<JARVIS_LABEL>.installed.txt`도 지운다. 그래서 rollback 뒤 backup 디렉터리에 잘못된 설치 성공 기록이 남지 않는다.
- 중복 가드 강화: `$APPLICATIONS_DIR` 경로 필터(`case "$candidate_path" in`)를 제거했다. 다른 경로를 보고하는 살아 있는 인스턴스가 배포 경로 밖에 있으면 필터가 그 pid를 제외해 진짜 중복을 놓칠 수 있었다. 이제 실행 파일 이름을 argv에 가진 프로세스는 경로와 무관하게 stray 후보이고, 경로는 `reported_app_path` 진단 문구로만 쓴다. 이 호스트에서 `/usr/bin/pgrep -f openkakao-jarvis-desktop`은 앱 pid만 반환하므로 false positive 위험은 이름 충돌 프로세스가 있을 때로 한정된다.
- pid 신원 재확인: `wait_for_pid` 이후 `LAUNCHD_START_MARKER=$("$PS" -p "$LAUNCHD_PID" -o lstart=)`를 저장하고, 이전 번들 삭제 직전에 같은 값을 다시 읽어 비었거나 달라지면 `launchd pid identity changed before deletion`으로 fail-closed하고 rollback한다.
- 신규 테스트 6건: (1) pid 대기 중 `SIGTERM`이면 exit 143·이전 번들 복원·rollback readback 기록, (2) 런타임 변경 구간에서 `SIGTERM`이면 이전에 로드돼 있던 Jarvis·legacy LaunchAgent가 다시 로드되고 legacy plist가 복원됨, (3) 2차 가드에서 stray가 나타나면 `installed.txt`가 제거됨, (4) 배포 경로 밖(`/tmp/...`) 경로를 보고하는 후보도 stray로 판정됨, (5) `lstart`가 바뀌면 rollback, (6) 정적 계약 검사(`perform_rollback() {`, `ROLLBACK_ATTEMPTED=1`, `RUNTIME_TOUCHED=1`, `[ "$RUNTIME_TOUCHED" -eq 1 ]`, `printf '%s\n' "$candidate_pids"`, `-o lstart=`, 경로 필터 부재).
- 하네스는 `OPENKAKAO_FAKE_BLOCK_BOOTOUT`으로 fake `bootout`에서 블록해 신호 시점을 결정적으로 만들고, 살아 있는 프로세스를 죽이지 않는다. `send_signal`은 설치 스크립트 프로세스에만 보내며 그 자식(가짜 `launchctl`)에는 보내지 않는다.

검증 명령과 출력:

```
$ /Users/twoimo/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11 \
    -m unittest tests.test_jarvis_desktop_launchers
......................
----------------------------------------------------------------------
Ran 24 tests in 33.515s

OK
```

```
$ /Users/twoimo/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11 \
    -m unittest tests.test_auto_reply_ondevice tests.test_auto_reply_reference_search \
    tests.test_auto_reply_knowledge_graph tests.test_jarvis_unit4 \
    tests.test_jarvis_desktop_launchers tests.test_local_mlx_model_readiness \
    tests.test_verify_local_models tests.test_mlx_serve_lifecycle \
    tests.test_auto_reply_dream_rsi tests.test_dream_rsi_alphaxiv
----------------------------------------------------------------------
Ran 327 tests in 64.440s

OK
```

- `sh scripts/test-auto-reply-launchd-artifacts.sh`는 `launchd artifact harness passed`로 통과했고, `/bin/sh -n scripts/install-jarvis-desktop.sh`와 `git diff --check`도 clean이다.
- 회귀 검출력 대조: 신규 (2) 테스트는 `RUNTIME_TOUCHED=1`을 `0`으로 되돌린 대조 실행에서 `AssertionError: 'signal exit 143' not found in ''`로 실패했다. 즉 이 테스트는 수정 없이는 통과하지 않는다.
- 이 절의 한계: 이 호스트에서 실제 재설치·rollback은 실행하지 않았다. 신호는 가짜 `launchctl`·`ps` 어댑터에 보낸 것이고 실제 LaunchAgent 상태 변화를 관측한 것은 아니다. `wait_for_pid`의 fallback은 여전히 `launchctl print` 텍스트에서 pid를 파싱하며 이는 외부 출력 형식 계약이다(`kickstart -kp`가 pid를 주면 쓰지 않는다). 앞 절의 미해결 stray 인스턴스(pid 84125, RunningBoard job)는 그대로 살아 있으므로 이 호스트의 전환 조건은 여전히 미충족이고, 재설치하면 가드가 그 인스턴스를 stray로 보아 exit 3으로 rollback한다.
