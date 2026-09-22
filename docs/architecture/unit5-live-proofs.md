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
- 개정 — 2026-09-22 KST(round-4 반영): 복구 lane에 `signal`(`R0`, `signal_exit`) 상태를 추가해 `backup → signal → rollback` 전이를 그렸고, `guard`/`delete`/`rollback`/`restored` sublabel을 `2회 · pid 동일성 확인`, `가드 + lstart 통과`, `bootout → 번들·plist 복원`, `exit 3 · 이전 job 복원`으로 갱신했으며 카드 3개도 round-4 계약(경로 무관 pid 동일성·lstart 재확인, 이전 job·legacy plist 재기동과 installed.txt/rollback.txt 분리, 24 tests·327 tests)으로 바꿔 아래 절의 수치와 일치한다. `validate lifecycle --quality showcase`는 9/9 artifact checks에 0 errors / 0 warnings(status `pass`, minLabelRouteClearance 26.4)이고, `deliver`는 specification SHA-256 `eb702b7f4197fb5df8a60dc55dbd9631a31e9b8ef1c65ac70e819e06f71d4a1c`(6,214 bytes)·artifact SHA-256 `d1b627f8fd494915a37b248d24e45ac2eca561ace557caeaccdb95fde5a9d4b0`(814,606 bytes)로 성공했다. `visual-check`는 status `pass`로 1440x900·1600x1000·1920x1080·2048x1320 light viewport에서 overflow 0이고 최소 projected node text는 6.0px(1440x900)–7.0px(2048x1320)였으며 screenshot은 1440x900·2048x1320 light/dark 4장이다. receipt의 `visualReview`는 `pending`이고, 캐처를 직접 본 결과 새 R0 상태와 12개 전이가 겹침·잘림 없이 그려졌다(사람 판단). 이 개정은 다이어그램 소스와 생성 HTML이며, 실제 재설치나 rollback을 실행한 증거가 아니다.

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

### 전환 pid 생존 확인 추가 — 2026-09-22 KST

- 결함: `check_duplicate_guard`는 *추가* 장애 인스턴스만 찾았다. 활성화한 앱 프로세스가 죽으면 `pgrep -f`이 일치 없음(status 1, 출력 없음)을 반환하고, `list_live_app_pids`는 이 경우를 알 수 없는 것으로 간주하지 않고 빈 목록으로 끝난다(목록이 비어 있어도 status 0). 그래서 stray 판정이 존재하지 않는 상태가 가드 통과로 끝나고, 그 다음 단계인 `record_installed_readback`가 `installed.txt`를 기록한 뒤 이전 번들이 `rm -rf`로 삭제되고 `installed:` 출력까지 이어진다. 결과적으로 앱은 죽은 채 복구용 이전 번들이 사라지고 스크립트는 성공을 보고한다.
- 수정: `check_duplicate_guard`가 stray 분석 전에 살아 있는 앱 pid 목록에 launchd pid가 있는지 먼저 확인하고, `kickstart -kp` 직후의 exec 창(아직 exec 전이라 argv가 앱 이름으로 보이지 않을 수 있는 구간)을 위해 상한 5회·0.2초 반복한다. 끝까지 없으면 `launchd pid N is not a live app process`를 stderr에 남기고 가드가 1을 반환해 `post_activation_failure`로 rollback한다(exit 3). 가드는 활성화 직후와 이전 번들 삭제 직전 두 번 실행되므로, 그 사이에 프로세스가 죽어도 뒤쪽 가드가 같은 방법으로 다시 닫는다.
- 신규 테스트 2건: `test_installer_dead_launchd_pid_rolls_back`(하네스가 실패를 그대로 모델링해 `pgrep` status 1·출력 없음), `test_installer_late_live_pid_does_not_roll_back`(첫 2회 열거에서는 pid가 없고 3회째에 나타나 정상 완료). 하네스에 `OPENKAKAO_FAKE_LAUNCHD_PID_ABSENT`·`OPENKAKAO_FAKE_LAUNCHD_PID_DELAY` 노브를 추가했다.
- 회귀 검출력 대조: 가드의 awk 생존 확인을 제거하고 같은 테스트를 돌리면 `AssertionError: 0 != 3`로 실패한다. 즉 수정 없이는 앱이 죽은 상태에서도 설치가 성공 exit 0으로 끝난다.
- 검증: 고정 Python 3.11로 `tests.test_jarvis_desktop_launchers` **26 tests, OK**(34.5초), CI focused 10개 모듈 **329 tests, OK**(86.3초), `/bin/sh -n scripts/install-jarvis-desktop.sh` 통과, `git diff --check` clean.
- 이 절의 한계: 이 호스트에서 실제 재설치나 rollback을 실행하지 않았고 프로세스도 종료하지 않았다. 생존 확인은 가드가 이미 쓰는 가정(앱 프로세스의 argv에 실행 파일 이름이 남는다)을 그대로 쓴다. 또한 `list_live_app_pids`가 판단할 수 없는 pgrep 종료 코드는 여전히 2(불명)로 닫힌다.
- 다이어그램과 README도 같이 갱신해 `guard` sublabel을 `2회 · pid 생존·동일성 확인`으로, 가드 카드에 생존 확인을 추가했고 재배포(receipt) 수치는 specification `eb702b7f4197fb5df8a60dc55dbd9631a31e9b8ef1c65ac70e819e06f71d4a1c`(6,214 bytes)·artifact `d1b627f8fd494915a37b248d24e45ac2eca561ace557caeaccdb95fde5a9d4b0`(814,606 bytes)다.

### 설치본 live 지식 그래프 readback — 2026-09-22 KST

위 절들이 기록한 00:51 설치본을 그대로 둔 채 03:44:31 KST에 read-only 상태 action만 다시 실행했다. 이번 확인에서 재설치·재색인·전송은 실행하지 않았다.

- 앱 프로세스는 pid 84125(01:44:30 시작, RunningBoard 제출 job)이고 같은 시각 01:44:37에 BM25 색인이 갱신돼 있었다.
- 상태 action은 exit 0으로 다음 payload를 반환했다.

```json
{"dense_indexed_at": 1790005911, "dense_status": "unavailable:RuntimeError:local dense embedding unavailable", "edge_count": 0, "edges": [], "grounded_nodes": 0, "indexed_at": 1790009077, "indexed_count": 31, "indexing_mode": "wal+isolated-copy+mode=ro+query_only", "node_count": 0, "nodes": [], "ok": true, "snapshot_status": "copy_ok", "stale": true}
```

- 시각 환산: `indexed_at` 1790009077 = 01:44:37 KST, `dense_indexed_at` 1790005911 = 00:51:51 KST(마지막 성공), dense watermark 1790005898 = 00:51:38 KST.
- `indexing_mode`가 `wal+isolated-copy+mode=ro+query_only`이고 `snapshot_status`가 `copy_ok`다. 원본 DB의 journal mode를 바꾸지 않고 임시 복제본을 read-only·query_only로 읽는 경로가 이 시각에도 그대로 쓰였다.
- 그래프 실측: 원본 `knowledge-graph.sqlite3`를 `file:...?mode=ro`로 열어 `kg_entities` 50 · `kg_relations` 341을 확인했다. 이는 00:51 기록과 같은 값이다.
- dense endpoint: `lsof -nP -iTCP:11234 -sTCP:LISTEN`은 외부 소유 `mlx-serve`(pid 38868)를 보고하지만, `curl -X POST http://127.0.0.1:11234/v1/embeddings`(6초 상한, `bge-m3` 1건)는 `http_code=000`(무응답)이었다. 그래서 이 시각 dense 단계는 fail-closed로 닫혔고 응답 본문은 저장되지 않았다.
- 직전 ANN 저장소는 `knowledge-dense-ann.sqlite3`(00:51, 2,940,928 bytes)이다. `/tmp` 격리 복사본에서 `dense_vectors` 50 · `ann_buckets` 400 · `dense_meta` `bge-m3-lsh-v1`·watermark 1790005898을 읽어, dense 재색인이 실패한 뒤에도 직전 색인이 남아 있음을 확인했다.
- 이 절이 닫지 않는 것: 이번 측정은 BM25·그래프 상태와 dense의 현재 실패만 보여준다. dense 하이브리드 검색(`search_mode "rrf"`)은 00:51 기록이며 이번에 재현하지 않았고, endpoint가 응답하지 않는 동안 검색은 `bm25_only`다. `stale` true는 원본 대비 색인이 오래됐다는 뜻이며 이번 절에서 원인을 분해하지 않았다.
- 관측(정정 기록): 상태 action 자체는 read-only지만, 이 절의 확인 과정에서 `sqlite3`로 dense 저장소를 직접 열었을 때 SQLite가 0바이트 `knowledge-dense-ann.sqlite3-wal`과 32,768바이트 `-shm`을 만들었다. 본 DB 파일(2,940,928 bytes, 00:51)은 바뀌지 않았고, 그 뒤 dense 확인은 `/tmp`로 복사한 사본에서만 수행했다. 앱 데이터 디렉터리에는 그 두 파일이 남아 있으며 삭제하지 않았다.

### 설치본 UI Computer Use 재크로스체크 — 2026-09-22 KST

2026-09-22 03:47 KST에 설치본 /Applications/OpenKakao Jarvis.app을 Computer Use(cua_repl)로 다시 확인했다. 설치 번들은 00:51:21 이후 변경되지 않았고 앱 pid는 84125(01:44:30 시작)다. 이번 확인에서 재빌드·재설치·재색인·전송은 실행하지 않았다.

- 상태 항목을 CGEvent 클릭으로 열고 cua.getApp("OpenKakao Jarvis.app")로 붙어 AX 트리를 받았다. 트리는 0 standard window → 1 scroll area → 2 HTML content(URL tauri://localhost) → 3 container Jarvis → 4 image Jarvis core + 5 button 설정 열기 였다. 패널 창의 조작 요소는 톱니바퀴 버튼 1개뿐이다.
- 패널 캡처(276x260, docs/architecture/jarvis-live-panel.png, 이번에 갱신)에서 웜 블랙 배경, 샴페인 골드 구체 코어, 다중 동심원 짐벌 링, 얇은 시냅스 선, 작은 입자가 관측됐다. bloom 후처리·발광 텍스트·네온은 없었다.
- 5 button 설정 열기 클릭은 별도 창 Jarvis 설정(760x760, tauri://localhost/index.html?view=settings)을 연다. 03:47:09 캡처로 docs/architecture/jarvis-live-settings.png를 760x760으로 갱신했다. 이 창에 있는 섹션은 아래가 전부다.
  - 대상 채팅방: pop up button 대상 채팅방(NIMDA 인수인계 임원방) · 등록 3 · live 3 · 본문 미전달
  - AI 모델: 온디바이스 · Flash-Next 토글 on(기본 상주) · Qwen3.8 27B 토글 off(온디맨드 스왑 · 미로딩) · 현재 선택 ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit · 외부 소유 · 27B 전환 차단 · 외부 런타임이 11234 포트를 점유 중 · 앱은 시작/중지하지 않습니다 · 온디바이스 감지 Apple M5 Max (128GB RAM) · MLX Core/Serve · Qwen3.8 Flash-Next · 설정 검증 통과 · 실추론 통과 (Qwen3.8 Flash-Next)
  - Voice: 로컬 전용 · 상태 wake_listen · wake none · RMS 0.000 · 호출어 헤이 자비스 · 임계값 0.65 고정 · 한국어 커스텀 헤드 bundled ONNX 선택됨(TTS 보정, 사람 음성 일반화 아님) · 마이크 세션 시작 버튼
  - 카카오 DB 동기화 · 색인: GraphRAG 준비 · 동기화 stale · 백그라운드 · 답변 대기 0 · 긱뉴스 idle · DB 동기화 stalled · 격리 복제 copy_ok · 색인 모드 wal+isolated-copy+mode=ro+query_only · 마지막 색인 1790005905 · indexed 31 · dense indexed:50 · indexed_at 1790005911
  - DREAM-RSI: 체크포인트 provenance · status evaluated · selected_policy mirror_prompt_tail · gold_rows 400 · gold_source_policy human_only
  - Knowledge: GraphRAG · stale · E-R-E 50 nodes · 341 relations · 화면 최대 24 nodes · 홀로그램 그래프 이미지 · +1 hop 버튼(비활성) · GeekNews 슬롯(아침 대기 · 점심 대기 · 저녁 대기)
  - History: 안전 요약 · 최근 12건 · 본문·프롬프트 제외
- 대량 검증·기능 점검·권한 관리 화면은 이 창에 없고 설치본에서 노출되지 않는다.
- 설정 창의 close button을 누른 뒤 앱은 창 0개 · isRunning true(app id com.openkakao.jarvis.desktop)였고 pid 84125는 살아 있었다. 이 리비전도 포커스 상실·닫기에서 패널을 hide하고 프로세스를 유지한다.
- 캡처 출처: 이번 PNG는 screencapture 대신 CUA 서비스가 자체 저장한 캡처(임시 디렉터리 com.openai.sky.CUAService, 03:47:04 · 03:47:09 JPEG)를 sips로 PNG 변환해 기록했다. 이 호스트 세션에서 screencapture -l windowID는 30초 안에 끝나지 않아 중단했다(호스트 프로세스에 화면 기록 권한 없음). 갱신한 PNG SHA-256은 jarvis-live-panel.png 0cedac6cc227464b06ae1801cfb386c97630b883714f0ecd7876fe6ed9c60903, jarvis-live-settings.png eac575b0b316398c636b6da171dd2853de86ff1bacd75de649efd6fa5c77532b이다.
- 이 절의 한계: 이번 확인은 AX 트리·렌더 픽셀·창 생명주기에 대한 것이다. live 모델 생성, dense 재색인, 실제 카카오톡 전송을 입증하지 않는다. 설정 화면의 dense indexed:50 · indexed_at 1790005911과 마지막 색인 값은 00:51 성공 시점의 영속 상태를 읽은 것이며 이번에 재측정한 값이 아니다.

### 전환 전제 미충족 상태 재확인 — 2026-09-22 KST

위 UI 크로스체크와 같은 시각(03:5x KST)에 launchd와 프로세스 상태를 read-only로만 읽었다. 재설치·bootout·kickstart·종료는 실행하지 않았다.

- launchd job gui/501/com.openkakao.jarvis.desktop는 state = not running, job state = exited, runs = 2, last exit code = 0이다. plist는 /Users/twoimo/Library/LaunchAgents/com.openkakao.jarvis.desktop.plist(00:51 작성)이고 program은 설치 번들 실행 파일 /Applications/OpenKakao Jarvis.app/Contents/MacOS/openkakao-jarvis-desktop이다.
- 같은 번들 경로에서 살아 있는 앱 프로세스는 pid 84125 하나뿐이다(01:44:30 시작, ppid 1, RSS 약 100 MB). 이 프로세스는 launchd가 추적하지 않는다.
- 직전 설치(2026-09-22 00:51:21)의 백업 receipt install-backups/jarvis-desktop/20260922T005121-98225/com.openkakao.jarvis.desktop.installed.txt는 그 시각 launchd가 state = xpcproxy, pid = 98353이었다고 기록한다. 즉 00:51에 활성화한 인스턴스는 이후 종료됐고(exit code 0), 84125는 그 뒤 별도로 시작된 인스턴스다.
- 함의: 재설치를 실행하면 kickstart -kp가 새 launchd pid를 만들고 84125가 살아 있는 stray로 판정되어, 가드가 이전 번들을 지우지 않고 exit 3으로 닫는다. 계획 5단계의 전환 전제(중복 작업자 없음, launchd가 소유한 단일 인스턴스)는 이 호스트에서 아직 미충족이다. 이 세션은 프로세스를 종료하지 않았고 84125도 그대로 두었다.
- 이번 UI 크로스체크는 이 untracked 인스턴스(같은 설치 번들 경로, 번들 mtime 00:51)를 본 것이므로 위 UI 증거는 그 번들에 대한 것이지만, launchd 감독 상태나 자동 재시작을 입증하지는 않는다.

### 독립 리뷰 차단과 동기화 stall 진단 — 2026-09-22 KST

독립 서브에이전트 리뷰(Codex Web, 모델 chatgpt-web/extra-high, reasoning xhigh, 동시성 1)는 이 호스트에서 완료되지 못했다. 새 스레드로 만든 재시도(agent 01a0c551-7e40-7983-af09-290e9fc58102)도 5.9초 만에 같은 오류로 끝났다:

    stream disconnected before completion: page.goto: net::ERR_ABORTED at https://chatgpt.com/?temporary-chat=true

직전 스레드(01a0c547-6cde-74b3-947c-07330b645a37)의 종료 상태도 같은 문자열이었고, 같은 시각 이 호스트에서 https://chatgpt.com/ 는 curl로 http=403(일반 UA와 브라우저 UA 모두)이었으며 DNS 해석은 정상이었다. 규칙(동일 오류 3회 연속이면 blocked로 기록하고 다른 작업을 진행하며 웹 동시성은 1 유지)에 따라 이 항목을 blocked로 기록하고 웹 요청을 중단했다. 따라서 이번 세션은 독립 리뷰의 AHP 점수를 얻지 못했고, 98점 달성을 주장하지 않는다. 두 스레드는 close_agent로 닫아 슬롯을 비웠다.

같은 시각 앱 state root /Users/twoimo/Library/Application Support/openkakao/bujamentor 를 읽기 전용으로만 확인해 동기화 stall의 내용을 특정했다. 재색인 실행·프로세스 시작·파일 삭제는 하지 않았다.

- 앱은 살아 있고 jarvis-voice-status.json을 03:54 KST에 갱신했다(updated_at 1790016882, state wake_listen, 호출어 헤이 자비스, threshold 0.65).
- knowledge-graph.sqlite3(mtime 01:45, 401,408 bytes)의 kg_meta는 last_indexed_at 1790009077(01:44:37 KST), last_dense_indexed_at 1790005911(00:51:51 KST), last_dense_status unavailable:RuntimeError:local dense embedding unavailable, last_index_error 빈 값, last_snapshot_status copy_ok이다. 같은 DB의 kg_entities 50행 · kg_relations 341행은 00:51·03:44 기록과 같은 값이다.
- knowledge-graph-reindex.lock(0 bytes, mtime 2026-09-17 19:29)이 남아 있지만 이 가드는 flock 기반이다(_reindex_process_running, _spawn_reindex_process). 파일 존재 자체는 차단 요인이 아니고, 소유 프로세스가 없으면 다음 재색인은 정상적으로 잠금을 얻는다. 즉 이 stale 파일은 stall의 원인이 아니다.
- 데스크톱 코드에는 재색인 트리거가 없다. desktop/src-tauri/src/python_bridge.rs는 knowledge-graph-status 읽기 action만 쓰고 reindex 계열 문자열이 없다. 재색인 주기를 판정하는 곳은 scripts/auto_reply_knowledge_graph.py의 now - last_updated >= reindex_interval_seconds(모듈 기본 300초)이고, 이를 호출하는 쪽은 scripts/auto-reply-menubar.py의 collect_knowledge_graph 경로다.
- 따라서 01:44:37 이후 색인 사이클을 실제로 구동한 호출자가 없다는 것이 이 시각 상태의 내용이다. 설정 카드의 stale·DB 동기화 stalled·dense indexed:50/00:51은 이 상태를 그대로 표시한 것이며 가짜 정상 표시가 아니다.
- 확인 방법: kg_meta는 앱 디렉터리가 아니라 /tmp에 만든 사본에서 읽었다. 이번 읽기로 앱 디렉터리에 새 -wal·-shm이 생기지 않았고, 직전 03:44 절이 만든 0바이트 knowledge-dense-ann.sqlite3-wal(32,768 bytes -shm)과 knowledge-graph.sqlite3-wal(0 bytes)은 그대로 두었다.
- 이번 세션은 재색인 호출자를 새로 시작하지 않았고 0바이트 lock 파일도 삭제하지 않았다. 자동 색인 소유권을 앱으로 옮기는 변경도 하지 않았다: 앱이 외부 소유 모델·프로세스를 임의로 시작·중지하지 않는다는 기존 계약과 충돌하고, 동시 재색인에서 ANN 저장소 잠금(unavailable:OperationalError:database is locked)이 관측된 전례가 있기 때문이다. 이 항목은 미해결로 남긴다.

### 두 모델 실제 생성 검증과 style.gallery 재확인 — 2026-09-22 KST

이 절은 04:00~04:05 KST에 실행한 실측이다. 재설치·재색인·카카오톡 전송은 하지 않았다.

#### Qwen3.8 27B 텍스트·비전 실측 (신규)

앱이 쓰는 11234 서버를 건드리지 않고 별도 포트 12345에 27B를 직접 적재해 실측했다. 바이너리와 주요 인자는 앱 번들이 쓰는 것과 같다: /Applications/MLX Core.app/Contents/MacOS/mlx-serve 에 --serve, --model <27B 디렉터리>, --model-dir /Users/twoimo/.mlx-serve/models, --host 127.0.0.1, --port 12345, --ctx-size 8192, --skip-mem-preflight 를 주었다.

- 1차 시도는 적재 전 pre-flight에서 거부됐다: weights 약 16.95 GB, available 8.90 GB, InsufficientMemory. 같은 시각 이 호스트는 swap 23,552 MB 중 22,211 MB 사용, 압축 페이지 2,850,223개(약 43.6 GB), free 10,455 페이지였다. 즉 거부는 현재 메모리 압박을 반영한 것이고, MLX buffer-pool cap 8192 MB 로그는 별도 계산이다(11234 로그에도 같은 cap 줄이 남아 있다).
- 그래서 프로덕트 자신의 설정 노브(SKIP_PREFLIGHT, ~/.mlx-serve/ops/profile.conf)와 바이너리 안내(검사는 보수적이며 macOS가 file cache를 회수한다)에 따라 --skip-mem-preflight로 적재했다. 결과: Warmup complete (2165 ms), Model ready (loaded on inference thread), Server listening on http://127.0.0.1:12345. 모델은 qwen3_5_moe(64 layers, 5120-dim, head_dim=256, 24h/4kv, 4-bit affine quant)로 인식됐다.
- 텍스트 생성: POST /v1/chat/completions, 38 prompt tokens, 응답 content는 두 줄로 첫 줄 '안녕하세요.', 둘째 줄 '4'. prompt 1114.9 ms(34.1 tok/s), decode 6 tokens 861.3 ms(6.97 tok/s), http=200 total 1.985 s.
- 비전 생성: 128x128 PNG(흰 바탕, 빨간 원)을 data URL로 넣고 도형과 색을 물었다. 응답 content는 '빨간 원'. prompt 106 tokens(이미지 때문에 40에서 106으로 증가), 1881 ms에 완료, decode 41.0 tok/s, http=200 total 1.887 s. 서버 로그에 64 image soft tokens 삽입과 M-RoPE 1 images 처리가 남았다.
- 정리: 내가 띄운 프로세스(session 66639, pid 49257)만 Ctrl-C로 정상 종료했고 포트 12345는 반납됐다. 앱의 11234 서버(pid 38868)와 ops 상태는 시작·중지·전환하지 않았고, 실측 후에도 같은 pid로 LISTEN 중임을 확인했다.
- 비용 관측(정직 기록): 27B 적재 동안 이 호스트의 swap 사용이 22,211 MB에서 50,099 MB로 늘었다. 11234 로그에는 같은 구간에 client_disconnect로 끝난 0+0 tokens 요청(5,001 ms, 20,004 ms)이 남았고, prefix cache가 데워진 뒤에는 같은 요청이 1,671 ms에 완료됐다. 두 모델 동시 상주(75.3 GB + 16.9 GB + KV)는 이 호스트 예산에서 사실상 불가하므로, 계획의 전환 순서(요청 배출, 기존 모델 해제, 대상 로드, 짧은 검증)가 타당하다는 관측이다.

#### Qwen3.8 Flash-Next 실측 (재확인)

- GET /health: http=200, 0.4 ms.
- GET /v1/models: ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit, loaded true, state ready, bytes_resident 75,303,252,216, context_length 786432, batched_decode true, capabilities chat/tool_use/streaming/reasoning/json_schema, input_modalities text(프로파일 NO_VISION=1과 일치).
- POST /v1/chat/completions 응답 content는 첫 줄 '안녕하세요.', 둘째 줄 '42'. prompt 39 tokens(38 cached), predicted 7 tokens 84.3 ms(83.0 tok/s), http=200 total 1.671 s.
- 이 값들은 외부 프로세스 상태에 따라 변하며 앱은 그 프로세스를 시작·정지·전환하지 않는다.

#### style.gallery 재확인과 적용 (계획 항목)

계획은 style.gallery를 읽기 전용으로 검토하고, 접근하지 못한 스타일을 적용했다고 기록하지 말라고 했다. 이번에는 접근에 성공했다.

- GET https://style.gallery/ 는 http=200, 240,456 bytes였다(2026-09-22 04:03 KST). 문서 제목은 '다음 화면의 시작점 · Style Gallery'이고 github.com/changeroa/StyleGallery 를 가리킨다.
- 이 사이트는 색·토큰 중심의 테마 시스템이 아니라 인터페이스 제작 지식 라이브러리다. llms.txt는 'Layout defines spatial behavior; the consuming product owns visual styling.'이라고 경계를 명시한다. 그래서 여기서 가져올 것은 시각 토큰이 아니라 상호작용·상태·플랫폼 계약이다. 현재 UI의 ivory·warm black·샴페인 골드 결정은 oh-my-design 절차를 따른 결과이며 style.gallery를 근거로 색을 바꾸지 않았다.
- 적용 1, Design Engineering Latest Request Wins(수명주기 experimental): 핵심 계약은 'identity decides acceptance', 'Both success and failure must match the current request', 'Do not use this policy to discard the outcomes of independent writes that all matter'이다. 우리 구현 대조: conversation_advanced_past_event (scripts/auto-reply-worker.py:4226)는 안정적 워터마크가 아니면 None으로 닫는 fail-closed 판정이고, _superseded_by(같은 파일 4207)는 영속 테이블 reply_job_supersessions에서 대체 관계를 읽는다. finish_burst_superseded(같은 파일 14533)는 대체된 이벤트 자신의 행만 status, due_at, reply=None으로 갱신하고 현재 턴의 결과를 건드리지 않는다. 전송은 write이므로 이 정책으로 버리지 않으며, 전송 직전 워터마크 재검사 뒤 AX local-send만 쓰고 delivery_unknown은 자동 재전송하지 않는다. 즉 stale 실패가 현재 상태를 지우는 경로가 없다.
- 적용 2, Platform Guides Input And Focus: 설치본은 글로벌 탈출 단축키를 tauri_plugin_global_shortcut으로 등록하고 desktop/src-tauri/src/main.rs:148에서 Modifiers::SUPER | Modifiers::ALT 와 Code::Escape(즉 ⌘⌥Esc)로 고정한다. AX 백그라운드 제어는 전면 활성화가 필요한 동작을 거부하므로 포커스 계약을 넘지 않는다.
- 한계: 위 두 항목은 style.gallery가 experimental로 표기한 지침과의 대조이며, 사이트가 우리 제품을 검증한 것이 아니다. 라이브 카카오톡 전송과 독립 리뷰 점수는 여전히 미확인이다.

### DPO 로그확률 수집과 위임 경로 재측정 — 2026-09-22 KST (세션 01a0b7f6)

이 절은 commit 36bc56b 작업의 실측이다. 재설치·재색인·카카오톡 전송·프로세스 종료는 하지 않았다.

- 위임 경로는 다시 차단으로 확인됐다. chatgpt-web/extra-high 서브에이전트 3회 연속 실패(동일 문자열 `stream disconnected before completion: page.goto: net::ERR_ABORTED at https://chatgpt.com/?temporary-chat=true`), gpt-5.6-sol은 사용량 한도로 `You've hit your usage limit ... try again at Sep 26th, 2026 5:37 PM`. 같은 시각 curl로 https://chatgpt.com/ 와 /codex/settings/usage 는 모두 http=403이었다. 규칙(동일 오류 3회 연속이면 blocked로 기록하고 다른 작업 진행, 웹 동시성 1)에 따라 이 항목을 blocked로 기록하고 웹 요청을 중단했으며, 구현은 부모가 로컬에서 수행했다.
- 로컬 게이트웨이 127.0.0.1:11234/v1는 요청 본문에 `logprobs=true`를 주면 `choices[0].logprobs.content`에 생성 토큰별 logprob을 담아 돌려준다. 예: `1+1=?` → `1 + 1 = **2**` 8토큰. 같은 엔드포인트에서 echo는 동작하지 않는다. /v1/completions에 `echo=true`로 `OPENKAKAO_ECHO_PROBE alpha beta gamma delta`를 보내면 프롬프트가 아니라 생성 토큰 `" epsilon"`만 돌아왔다. assistant prefill도 이어쓰기로 처리되지 않는다(마지막 assistant 메시지가 프롬프트에 포함될 뿐 새 턴을 생성한다). 따라서 이미 저장된 임의의 응답 문자열은 이 엔드포인트로 채점할 수 없다. `probe_response_scoring`이 이 사실을 `reason=echo_unsupported`, `supports_response_scoring=false`로 실측한다.
- 실제 수집(고정 Python 3.11, scripts/auto_reply_finetune.py): 프롬프트 `한 문장으로 답하세요: 오늘 서울 날씨 어때?`에 `max_tokens=1` → `저` 1토큰 합계 `-0.351563`, `max_tokens=6` → `저는 실시간 데이터에 접근` 6토큰 합계 `-1.313843`.
- 이 두 값을 `--dpo-pairs`·`--dpo-ref-pairs`(동일 기준값)로 넣은 CLI 실행은 exit 0, `report['dpo'].evaluation` = status `ok`, evaluated 1, objective `dpo`, loss `0.6931471805599453`(마진 0일 때의 정확한 값 ln 2), `string_similarity_used=false`였다. 기준 파일을 빼면 같은 실행이 `missing_reference_logprobs`로 평가 불가가 된다.
- 기준 값 없이 계산하는 경로는 `objective=reference_free_preference`, `reference_free=true`로 표시되며 표준 DPO 수치로 보고하지 않는다. 문자열 유사도로 대체하는 경로는 어디에도 없다.
- 테스트: `tests.test_auto_reply_finetune` **60 tests, OK**(기존 30 + 신규 30), `tests.test_auto_reply_dream_rsi` **43 tests, OK**(기존 41 + 신규 2), CI focused 11개 모듈 **391 tests, OK**.
- 크로스체크(Computer Use, 2026-09-22 04:29 KST): 패널을 클릭으로 열어 창 204649(910,30, 276x260)의 AX 트리와 스크린샷을 받았다. 트리는 `1 standard window → scroll area → HTML content(tauri://localhost) → container Jarvis → image Jarvis core + button 설정 열기`이고 조작 요소는 톱니바퀴 1개뿐이다. 스크린샷은 warm black 배경에 샴페인 골드 구형 코어·짐벌 링·시냅스 메시만 보이고 네온·bloom·발광 텍스트는 없다. 창이 0개인 상태의 `get_app_state`는 이전과 같은 5.03초 `-10005 timeoutReached`로 닫히고, 다시 토글하면 창 0개로 돌아왔다. desktop/ 소스는 이번 단위에서 바뀌지 않았으므로 이는 회귀 없음 확인이며 새 UI 변경의 증거가 아니다. 같은 시각 앱 pid 84125는 살아 있고, launchctl job gui/501/com.openkakao.jarvis.desktop은 여전히 `state not running`·`job state exited`·`runs 2`이고, 11234는 외부 mlx-serve pid 38868이 점유 중이다. 따라서 이전에 기록한 전환 전제 미충족과 `외부 소유 · 27B 전환 차단` 상태는 그대로다.
- 미해결: 어댑터 학습·승격과 독립 리뷰 AHP 점수는 여전히 미확인이다. 이번 단위는 로그확률 수집과 표준 DPO 산술의 실측이며 어댑터 품질을 입증하지 않는다.
### 메뉴바 레이아웃 감사 green 전환과 위임 경로 재차단 — 2026-09-22 KST (세션 01a0b7f6 계속)

이 절은 commit 8765d66 작업의 실측이다. 재설치·재색인·카카오톡 전송·프로세스 종료는 하지 않았고, 실행 중인 앱 pid 84125와 외부 mlx-serve pid 38868도 건드리지 않았다.

- `macOS cargo test` job이 이 브랜치에서 처음으로 green이 됐다(run `35650276817` / `8765d66`: `Launchd and Python harness`·`Tauri desktop and focused Python tests`·`macOS cargo test` 모두 success, macOS job의 `Audit menu layout`·`Upload menu layout audit` step 포함). 종전 실행 3건(`35647043056` `b975b80`, `35647929057` `590d239`, `35648448105` `a6decfc`)은 같은 step에서 `layout: settings-dark: 375~399행이 비어 있습니다 (25pt)` 한 건으로 실패했다.
- 실패 원인을 로컬 재현으로 확정했다. `git archive HEAD`로 만든 추적 파일만의 새 체크아웃(번들 `*.pyc` 0개, 즉 CI와 같은 조건)에서 같은 감사를 돌리면 `layout-audit: 1532 views, 14 images` / `layout ok: 14 windows, 1532 views`로 로컬과 동일하게 통과했다. 데이터 부재 가설도 반증했다: `--bin`을 항상 exit 1 하는 stub으로 바꿔도 views가 1532에서 변하지 않았다(감사 창은 합성 레이아웃을 그린다). 따라서 실패는 frozen 런타임이나 KakaoTalk DB 부재가 아니라 macOS 26과 Sonoma 러너 사이의 글꼴 계측 차이다(로컬 아래 여백 17px, 러너 25px).
- 러너의 실제 값을 artifact로 확인했다(run `35650276817`의 `menu-layout-audit`). `settings`는 640×400에 top 16·bottom 23·`empty_row_bands` [], `settings-dark`는 top 16·bottom 25·`empty_row_bands` [[375, 399, 25]]다. 띠의 끝 `399`는 이미지 마지막 행이므로 그 25px는 창 자신의 아래 여백이고, 바로 그 값을 위아래 여백 규칙은 40pt까지 허용하고 있었다. 즉 24pt 초과를 보고하는 행 띠 규칙이 같은 여백을 여백 규칙보다 엄하게, 그리고 두 렌더링 사이(23/25 대 17)에 걸치게 재고 있었다.
- 수정은 `scripts/check-menubar-layout.py`에 두 함수를 분리해 두 규칙이 한 허용값을 공유하도록 했다: `padding_limit_px`(40pt·scale, 종전 여백 규칙의 값을 그대로 함수로 뺀 것)와 `empty_row_band_problems`. 창 첫 줄/마지막 줄에 붙은 띠는 여백 허용값으로, 내용 사이의 띠는 종전 24pt로 판단한다. 내용이 한쪽으로 붕괴한 창은 여백 규칙이 계속 잡는다(`main`의 `rooms` 348pt가 그 예이고, 새 테스트가 396px 창의 226px 붕괴를 회귀 케이스로 고정한다).
- 신규 `tests/test_menubar_layout_check.py`는 경계값 8건을 고정한다: 아래 25px 통과·41px 실패, 위 25px 통과·41px 실패, 내부 25px 실패, 2x 캡처에서 60px 통과·81px 실패, `scale` 누락 시 1.0 대체, 붕괴 226px 실패. 고정 Python 3.11에서 **8 tests, OK**이고 CI focused 목록에 추가했다. 회귀 없음도 확인했다: `tests.test_auto_reply_menubar` **165 tests, OK**, 수정한 검사기로 로컬 감사 산출물 재판정 시 `layout ok: 14 windows, 1532 views`.
- `.github/workflows/ci.yml`의 macOS job에 `actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02`(v4.6.2) step을 `if: always()`로 넣어 `/tmp/layout-audit`을 남긴다. 창을 볼 수 없는 러너에서 감사가 실패하면 그 이유는 PNG 안에만 있으므로, 다음 실패는 추측 대신 artifact로 판정한다.
- 위임 경로는 이번에도 차단으로 확인됐다. chatgpt-web/extra-high 새 스레드 3회 연속 동일 오류 `stream disconnected before completion: page.goto: net::ERR_ABORTED at https://chatgpt.com/?temporary-chat=true`(agent `01a0c59f-cd94-7121-b1e1-ca0ccef23ce8`, `01a0c5a0-3bf4-77c3-a9ee-05e597c682bd`, `01a0c5a0-7e09-77f2-92e5-d532a9e25032`, 모두 close). 같은 시각 curl은 `https://chatgpt.com/` http=403, `https://api.github.com/repos/twoimo/openkakao-bot` http=200이었다. 규칙(동일 오류 3회 연속이면 blocked로 기록하고 다른 작업 진행, 웹 동시성 1)에 따라 이 항목을 blocked로 기록하고 웹 요청을 중단했으며, 구현·검증은 부모가 로컬에서 수행했다. 누적 5세션째 차단이다.
- 참조 사이트 도달성은 같은 시각 다시 측정해 모두 http=200이었다: `https://style.gallery/`, `https://github.com/kwakseongjae/oh-my-design`, `https://www.alphaxiv.org/abs/2609.14858`, `https://github.com/tt-a1i/archify`. `desktop/DESIGN.md`가 기록한 style.gallery 2026-09-20 재확인(HTTP 200) 주장은 현재 도달성과 모순되지 않는다.
- 사용자가 지시한 `gpt-5.6-luna`(Luna Max)는 이 세션의 `spawn_agent` override 목록에 없다. 브리지 쪽에는 `chatgpt-web/luna` 슬러그와 `gpt-5.6-luna` backend id가 존재하지만 노출된 override는 `gpt-6-astra`, `gpt-5.6-sol`, `chatgpt-web/{medium,high,extra-high}`뿐이고, `gpt-5.6-sol`은 사용량 한도(`try again at Sep 26th, 2026 5:37 PM`)로 열리지 않는다. 따라서 "단순 수정은 Luna Max" 지시는 이 호스트에서 이행할 수 없었고, 단순 수정은 부모가 로컬에서 수행했다.
- 문서 커밋도 같은 러너에서 확인했다: run `35650777861`(`4fae17e`)도 3개 job 모두 success이고, 이 시점 HEAD가 green이다.
- 감사 artifact의 PNG를 직접 판독해 크로스체크했다(`settings.png`·`settings-dark.png`, 둘 다 640×400). 내용은 실제로 다 그려져 있다: 대상 채팅방 카드, 카카오 DB 동기화·색인 카드, DREAM-RSI 카드, GeekNews 슬롯 카드, 4개 동작 버튼(`온디바이스 모델 설정`·`채팅방 관리`·`답변 기록`·`지식 그래프`). 따라서 25px 띠는 버튼 줄 아래의 창 여백이고 내용 누락이 아니다. 관찰 하나를 남긴다: 이 legacy Swift 감사 렌더링에서 동기화·DREAM-RSI·GeekNews 카드는 폭이 좁고 오른쪽 정렬이며 대상 채팅방 카드와 버튼 줄은 왼쪽 정렬이다. 감사는 정렬을 검사하지 않고(전폭 대상 카드가 모든 열에 잉크를 주므로 왼쪽 공백이 빈 열 띠로 잡히지 않는다), 의도 여부는 확인하지 않았다. 실제 제품 UI는 Tauri 앱이므로 이 관찰은 병행 중인 `5a1c` 작업트리 몫으로 기록하고 이 브랜치에서 `main.swift`를 다시 고치지 않았다.
- 미해결(변동 없음): 독립 리뷰 AHP 점수는 여전히 미확인이고, 전환은 여전히 차단이다(설치본 pid 84125가 launchd 추적 밖이라 재설치 시 stray로 판정되어 rollback, 외부 `mlx-serve` pid 38868이 11234를 점유해 27B 전환 차단). live KakaoTalk 전송, Developer ID 서명·notarization, Qwen3.8 27B 생성은 이번에도 검증하지 않았다.

### 오프라인 DREAM-RSI 수렴 루프·파인튜닝 분할·DPO fail-closed 실측 — 2026-09-22 KST (세션 01a0b7f6 계속)

이 절은 commit `47bbad2`(HEAD, clean, origin과 동기)에서 수행한 오프라인 실측이다. 앱 운영 state root(`~/Library/Application Support/openkakao/bujamentor`)에는 아무것도 쓰지 않았고, 재설치·재색인·카카오톡 전송·프로세스 종료도 하지 않았다. 실행 중인 앱 pid 84125와 외부 `mlx-serve` pid 38868은 건드리지 않았다.

#### 1. 오프라인 DREAM-RSI 수렴 루프 (임시 state root)

```bash
TMPD=$(mktemp -d /tmp/dreamrsi.XXXXXX)
mkdir -p "$TMPD/golden"
cp "$HOME/Library/Application Support/openkakao/bujamentor/golden/reply-golden.jsonl" "$TMPD/golden/reply-golden.jsonl"
OPENKAKAO_STATE_ROOT="$TMPD" /Users/twoimo/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11 \
  scripts/auto_reply_dream_rsi.py
```

- 입력 정답지는 400행이고, `state_root`는 `OPENKAKAO_STATE_ROOT`로만 임시 root를 가리켰다.
- exit 0. checkpoint는 `$TMPD/dream-rsi-policy.json`에만 쓰였고 운영 root의 `dream-rsi-policy.json`(mtime `Sep 20 12:02`)은 변경되지 않았다.
- 결과 요약: `replay_rows 400` · `gold_rows 400` · `excluded_model_gold 0` · `parse_errors 0` · `gold_source_policy "human_only"` · `status "evaluated"`.
- 후보 점수(`objective_score`): `echo_last_message 0.048953`(avg 0.038953) · `mirror_prompt_tail 0.058419`(avg 0.048419) · `longest_window_message 0.046121`(avg 0.036121). 세 후보 모두 `evaluated_rows 400` · `answered_rows 400` · `room_spread 7` · `candidate_errors {}`.
- `selected_policy "mirror_prompt_tail"`. `replay_guarantee`는 `incumbent_policy "echo_last_message"`(0.048953)를 포함해 `incumbent_included true` · `selected_score 0.058419` · `non_degradation_on_replay true` · `status "verified"` · `scope "fixed_replay_set_only"`다. scope note는 보증이 고정 replay 이력에만 적용되고 미래 온라인 성능으로 확장되지 않는다고 명시한다.
- 지표 표기: `metric "character_bigram_cosine_replay"` · `string_similarity_used true` · `string_similarity_scope "replay_answer_distribution"` · `preference_evaluation "separate_dpo_logprob_path"`. 즉 이 점수는 문자열 유사도 기반 replay 랭킹이며 선호도 손실이 아니다.
- 정답지 분포(`distribution_report`): `rows 400` · `length_p10 9` · `length_median 28` · `length_p90 136` · `mean_length 58.36` · `status "measured"`. 방별 분포는 Vision AI 경진대회 195, `그룹:325472527151234` 127, `그룹:301481831369871` 28, NIMDA 인수인계 임원방 24, `그룹:437046948660911` 18, `kakao-test` 7, `변우중` 1이다.

#### 2. 파인튜닝 데이터 분할 (`--prepare-only`)

```bash
TMPD=$(mktemp -d /tmp/finetune.XXXXXX)
/Users/twoimo/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11 scripts/auto_reply_finetune.py \
  --prepare-only --json \
  --golden "$HOME/Library/Application Support/openkakao/bujamentor/golden/reply-golden.jsonl" \
  --state-root "$TMPD"
```

- exit 0. 분할: `train 311` · `valid 39` · `test 28`(합 378) · `dropped 22`(`duplicate_prompts 22`).
- 누출 방지: `groups 173`을 `train_groups 147` · `valid_groups 14` · `test_groups 12`로 세션 단위 분리(`valid_ratio 0.1`, `test_ratio 0.05`, `seed 42`, `session_gap 1800.0`).
- 학습 계획: `mlx_lm.lora`, 모델 `mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit`, `iters 155`, `batch_size 4`, `learning_rate 1e-05`, `num_layers 16`, `max_seq_length 1024`, `fine_tune_type lora`, `--mask-prompt`.
- 산출물: `$TMPD/finetune/data/{train,valid,test}.jsonl` · `split-summary.json` · `finetune-report.json`. 실제 학습(`--train`)과 어댑터 승격은 실행하지 않았다.

#### 3. DPO fail-closed (합성 선호 쌍 + 실 게이트웨이)

합성 3쌍은 골든 정답을 `preferred`로, 상투적 문장을 `dispreferred`로 두고 `--dpo-pairs`로 넣었다. 게이트웨이는 앱이 쓰는 것과 같은 `http://127.0.0.1:11234/v1`을 읽기 전용 추론으로만 호출했다.

- 결과: `dpo.status "eval_unavailable"` · `string_similarity_used false`.
- `scoring_probe`: `mode "completions_echo"` · `probe_executed true` · `status "ok"` · `reason "echo_unsupported"` · `supports_response_scoring false` · `echo_preview " epsilon"`. 즉 이 엔드포인트는 echo를 구현하지 않아 이미 저장된 임의 응답 문자열을 채점할 수 없다.
- `capture`: `captured 0` · `unavailable 3` · `samples_per_prompt 2` · `sample_temperature 0.7`. 쌍마다 `capture_status "eval_unavailable"` · `capture_reason "unattributed_samples"` · `attributed_by "unattributed"`.
- `evaluation`: `evaluated 0` · `mean_loss null` · `require_reference true` · `reference_free false` · `string_similarity_used false`.
- 따라서 이 호스트의 생성 전용 게이트웨이에서는 표준 DPO 수치를 얻을 수 없고, 그 경로는 문자열 유사도로 대체하지 않고 평가 불가로 닫힌다. 앞서 기록한 `--dpo-ref-pairs` 동일 기준값 실측(`loss 0.6931471805599453`)은 기준 로그확률을 caller가 공급한 경우에만 성립한다.

#### 4. alphaXiv provenance + 고정 예산 탐색 + DPO 통합 리포트

```bash
/Users/twoimo/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11 scripts/dream_rsi_alphaxiv.py \
  --paper-source orx --timeout 20 --provenance-stdin < /tmp/ax-payload.json
```

- exit 0 · `status "ok"`.
- `paper_analysis.status "ok"` · provider `/Users/twoimo/.cargo/bin/orx` · `reason "orx_report_verified"` · `selection_method "direct_paper_id"` · `evidence.kind "orx_alphaxiv_paper_report"` · summary 14,515자 · `argv ["paper","2609.14858","--source","alphaxiv","--no-telemetry"]` returncode 0.
- `dream_rsi_exploration.status "stopped"` · `fixed_budget {initial 3, consumed 3, remaining 0}` · `stop {reason "budget_exhausted"}` · `candidate_order ["echo_last_message","mirror_prompt_tail","longest_window_message"]` · `promotion {allowed false, observed false}`. 4개 event는 후보 3건 `candidate_evaluated`(selection `continue / next_candidate`)와 `stop budget_exhausted`다.
- `dpo_preference_evaluation.status "eval_unavailable"` · `string_similarity_used false`.
- `model_policy {automatic_promotion false, automatic_replacement false}`.

#### 5. 이 절이 닫지 않는 것

- 어댑터 학습·승격, live model 생성 품질, 어댑터 AHP 비교는 이번에도 검증하지 않았다.
- 독립 리뷰(AHP ≥98)는 이번 턴에도 차단됐다. `chatgpt-web/extra-high`(reasoning xhigh, 동시성 1) 새 스레드 3회가 모두 동일 오류 `stream disconnected before completion: page.goto: net::ERR_ABORTED at https://chatgpt.com/?temporary-chat=true`로 끝났다(agent `01a0c75b-780e-7972-882a-300f0ad547e9`, `01a0c75b-f311-7f02-8bcd-4d4e8bef67bb`, `01a0c75c-8f1c-7612-8f29-5838488bead0`, 모두 close). 규칙(동일 오류 3회 연속이면 blocked로 기록하고 다른 작업 진행)에 따라 이 항목을 blocked로 기록하고 웹 요청을 중단했으며, 구현·검증은 부모가 로컬에서 수행했다. 누적 12세션째 차단이다.
- 사용자가 지정한 `gpt-5.6-luna`(Luna Max / Luan 최대)는 `spawn_agent` override 목록(`gpt-6-astra`, `gpt-5.6-sol`, `chatgpt-web/{medium,high,extra-high}`)에 없어 선택할 수 없었고, `gpt-5.6-sol`은 사용량 한도로 닫힌다. 그래서 단순 수정 폴백도 이 호스트에서 이행할 수 없었다.
- 이번 단위는 순수 오프라인 artifact 증거이며 live KakaoTalk 전송·설치본 재배포·Developer ID 서명·notarization을 입증하지 않는다.

### Browser-Use 전용 Chromium 결합·텍스트 전용 추론·오프라인 실측 — 2026-09-22 KST (세션 01a0b7f6 계속)

이 절은 commit `58bd43a` 작업의 실측이다. 앱 운영 state root와 실행 중인 앱 pid 84125, 외부 `mlx-serve` pid 38868은 건드리지 않았고 재설치·카카오톡 전송도 하지 않았다. 검증용 의존성은 기존 Python 작업 환경과 분리한 격리 venv(`uv venv` + `uv pip install playwright browser_use` → `playwright 1.63.0`, `browser_use 0.13.10`)에만 설치했고, Playwright 브라우저는 이미 캐시된 `~/Library/Caches/ms-playwright`를 재사용했다(Chromium 153.0.8010.12).

#### 1. 결함: 전용 컨텍스트가 조용히 버려졌다

browser_use 0.13.10의 `Agent.__init__` 파라미터에는 `browser`와 `**kwargs`가 있고 `browser_context`는 없다. 종전 `scripts/jarvis_browser_use.py`는 `Agent(task=task, llm=llm, browser_context=context)`를 호출했다. 실측:

```
Agent(task=..., llm=..., browser_context=<playwright context>)  -> accepted, agent.browser is None
Agent(task=..., llm=..., browser=<playwright context>)          -> AttributeError: 'BrowserContext' object has no attribute 'browser_profile'
```

즉 첫 호출은 `**kwargs`로 흡수돼 무시되고 에이전트는 전용 브라우저에 묶이지 않았다. 두 번째 호출은 0.13 계열이 자체 `BrowserSession`을 요구한다는 사실을 보여준다.

수정 후 어댑터는 설치된 시그니처를 읽는다. `browser_context`가 선언되면 그대로 넘기고, `browser`가 선언되면 이 모듈이 띄운 Chromium의 loopback DevTools endpoint에 붙는 `BrowserSession(cdp_url=...) `을 만든다. 어느 쪽도 선언되지 않거나 endpoint가 없으면 `BrowserUseApiUnsupported`를 올려 `BrowserJobResult(False, "browser_job_failed")`로 닫는다. `**kwargs`만 있는 시그니처는 지원으로 세지 않는다.

`DedicatedPlaywrightContext.start()`는 이제 `--remote-debugging-port=<free loopback port>`와 `--remote-debugging-address=127.0.0.1`로만 띄우고, endpoint를 `cdp_url`에 담아 Playwright 컨텍스트에 stamp한다. 종전 docstring이 적은 "never connects over CDP"는 우리 자신의 임시 브라우저에 한정된 설명으로 대체한다(사용자 Chrome/Safari와 외부 CDP endpoint에는 여전히 붙지 않는다).

#### 2. 결함: 텍스트 전용 게이트웨이에 스크린샷을 보냈다

수정한 기본 경로를 실제로 돌리자 매 스텝이 실패했다:

```
❌ Result failed 1/6 times: Error code: 400 - {'error': {'message':
   'This model is serving without its vision tower (--no-vision or no vision weights);
    image/video content is not supported', ...}}
...
ERROR [Agent] ❌ Stopping due to 5 consecutive failures
```

`Agent`의 `use_vision` 기본값이 True이고 11234의 Flash-Next는 vision tower 없이 서빙되기 때문이다. 수정 후 어댑터는 `use_vision=False`·`generate_gif=False`·`use_judge=False`를 릴리스가 **실제로 선언한** 파라미터에만 전달한다. 같은 이유로 텔레메트리와 기본 확장도 끈다: import 전에 `ANONYMIZED_TELEMETRY=False`를 강제하고, 이미 import된 패키지에는 `CONFIG.ANONYMIZED_TELEMETRY=False`를 적용하며, `BrowserProfile(enable_default_extensions=False, accept_downloads=False, headless=True)`를 쓴다. 종전 기본값은 네트워크에서 uBlock Origin Lite·I don't care about cookies·Force Background Tab 확장을 내려받고 쿠키 확장 데이터를 채웠다.

#### 3. 실측 — 컨텍스트·취소·fail-closed

격리 venv에서 실제 Chromium을 띄운 결과:

| 단계 | 결과 |
| --- | --- |
| `DedicatedPlaywrightContext` 시작 | Chromium 153.0.8010.12, contexts 1, fresh context pages 0, cookies 0 |
| 로컬 페이지 읽기 | `file://` 마커 일치, title "Jarvis probe" |
| 컨텍스트 종료 | `is_connected false`, `cdp_url ""` |
| `BrowserUseRunner` + Playwright 전용 agent | `ok true`, 모델 `mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit`, base_url `http://127.0.0.1:11234/v1`, 마커 반환, 소유 컨텍스트 해제 |
| in-process 취소(중간) | `global_abort`, 0.641초, 소유 컨텍스트 해제 |
| 파일 latch 취소(교차 프로세스, `AbortController.abort`) | `global_abort`, 0.646초, `latched true`, `epoch 1`, 소유 컨텍스트 해제 |
| 미리 latch된 토큰 | `global_abort`, `browser_launches 0` (브라우저를 띄우지 않음) |
| resume 이후 새 토큰 | `latched false`, 취소되지 않음 |
| Chromium 프로세스 | 실행 전 0 / 실행 후 0 |

#### 4. 실측 — 실제 기본 경로가 로컬 모델로 완주

`agent_factory`를 주입하지 않은 실제 기본 경로를 로컬 Flash-Next로 돌렸다. 작업은 로컬 `http://127.0.0.1:18785/index.html`에서 h1 텍스트를 보고하는 것이고, 결과는 정답 `JARVIS_FIXED_ADAPTER_MARKER_a91c`였다(`ok true`, 69.62초). 실행 내내 Playwright가 보는 브라우저 context 수는 1로 유지됐고(샘플 최대 1), 종료 뒤 `cdp_url`은 `""`, `_owned_context`는 None이었다. 즉 Browser-Use는 이 모듈이 소유한 단일 Chromium을 구동했고 별도 브라우저를 띄우지 않았다. 종전 코드는 `use_vision` 기본값 때문에 6스텝 전부 HTTP 400으로 실패해 `final_result`가 None이었다.

#### 5. 회귀 테스트와 CI

`tests/test_jarvis_browser_use.py` **11 tests, OK**를 추가하고 CI focused 목록에 넣었다(20 → 21개 모듈). 고정 Python 3.11에서 같은 목록 전체는 **Ran 556 tests in 102.802s, OK**이고, 이 브랜치 CI(run `35688129823` / `2fbf703`)는 3개 job 모두 success이며 focused step 로그는 **Ran 556 tests in 61.046s, OK (skipped=51)**다(51개 skip은 전부 `test_auto_reply_service_entry` 몫이고 나머지 20개 모듈은 러너에서 전부 실행된다). 앞선 커밋 `58bd43a`의 run `35688032379`는 같은 브랜치의 뒤 커밋 push로 concurrency에 의해 cancelled됐고(그 시점 `Launchd and Python harness` job은 success), 최신 커밋의 run이 권위 있는 게이트다. 테스트가 고정하는 경계값은 다음과 같다: `browser_context` 선언 시 그대로 사용, `browser` 선언 시 CDP endpoint에 묶인 세션 생성(`headless true`, `enable_default_extensions false`, `accept_downloads false`), `**kwargs`만 있는 시그니처 fail-closed, endpoint 없는 `browser` fail-closed, 텔레메트리 env·config 강제 off, `--user-data-dir` 없는 launch args, 기본 어댑터가 `use_vision/generate_gif/use_judge`를 전부 False로 전달하고 `browser`만 남기는지.

#### 6. 이 절이 닫지 않는 것

- 설치본 재배포·Developer ID 서명·notarization은 이번에도 검증하지 않았다. 이 실측은 격리 venv의 로컬 브라우저와 로컬 게이트웨이에 대한 것이며 설치된 `OpenKakao Jarvis.app`의 런타임을 입증하지 않는다.
- `browser_use` 패키지는 앱 번들에 포함되어 있지 않다. 설치본에서 이 경로를 쓰려면 별도 provisioned 런타임에 `playwright`·`browser_use`를 고정해야 한다.
- 독립 리뷰(AHP ≥98)는 이번 턴에도 차단됐다(`chatgpt-web/extra-high` 3회 연속 `page.goto: net::ERR_ABORTED at https://chatgpt.com/?temporary-chat=true`). 누적 12세션째 차단이며 AHP 점수는 얻지 못했다.
- live KakaoTalk 전송, 27B 전환(외부 소유 11234), 어댑터 학습·승격은 이번에도 검증하지 않았다.


### Browser-Use 전용 브라우저 수명주기 다이어그램 — 2026-09-22 KST (세션 01a0b7f6 계속)

`scripts/jarvis_browser_use.py`의 소유권·바인딩·취소·해제 경로를 archify lifecycle로 시각화했다. Diagram: [jarvis-browser-use-lifecycle.html](jarvis-browser-use-lifecycle.html), source: [jarvis-browser-use-lifecycle.archify.json](jarvis-browser-use-lifecycle.archify.json).

lane은 웹 작업 단계·취소 감시·결과 3개이고, main rail의 5단계(웹 작업 요청 → 전용 Chromium 기동 → 에이전트 바인딩 → 텍스트 전용 실행 → 결과 확정)는 열 순서로 연결된다. 분기 전이는 4개다.

- `bind → failed` (미지원 signature): 설치된 릴리스가 `browser_context`도 `browser`도 선언하지 않으면 agent를 만들지 않고 `BrowserUseApiUnsupported` → `browser_job_failed`로 닫는다.
- `run → watch` → `abort` (토큰 취소): 실행과 동시에 0.02초 폴링과 크로스 프로세스 파일 래치가 취소를 감지하고 `global_abort`로 끝난다.
- `run → failed` (실행 예외): 명시적 `via`로 하단 채널을 돌아 `failed`의 아래쪽 포트로 들어가며, `watch`·`abort` 열과 8px 이상 떨어진다.
- `settle → done`: 성공 경로.

receipt:

- `validate lifecycle --quality showcase`: 9/9 artifact checks, 0 errors / 0 warnings, composition profile `showcase` status `pass`
- `deliver`: specification SHA-256 `985840202f4a3a29c311af410267621ac1d176dc2f3548d17b0e61d6f2063a91` (5,320 bytes), artifact SHA-256 `51fdc982539b32d2f63637e7850a7d84521723ec28c78b34d1573ad0b65d4e55` (810,324 bytes)
- `visual-check`: exit 0, status `pass`, diagnostics 0; light containment at 1440x900 (reader 960 / diagram 930), 1600x1000, 1920x1080, 2048x1320에서 `scrollWidth/scrollHeight ≤ viewport`이고 최소 투영 node 텍스트는 6.36px(1440x900) → 6.55px(1600x1000) → 7.0px(1920 이상)이다. light/dark capture는 1440x900·2048x1320에 있다. Receipt: [jarvis-browser-use-lifecycle.visual-check.json](jarvis-browser-use-lifecycle.visual-check.json).

레이아웃에서 확인한 제약 세 가지를 남긴다. 첫째, 이 Viewer는 viewBox 종횡비가 1.55 이상일 때만 높이에 맞춰 reader 폭을 줄이는 adaptive 레이아웃을 켠다. 자동 계산 viewBox는 980x660(비율 1.485)이었고 그 상태에서는 1440x900 문서 높이가 1207px로 넘쳤다. 둘째, viewBox 폭 1024는 node context 텍스트가 최소 6px로 투영되는 상한이다(폭 1085면 5.74px로 떨어진다). 셋째, 높이 620에서 terminal 행이 허용 밴드(y ≤ 498) 안에 있으려면 terminal 상태에 `yOffset: -24`가 필요하다. 최종 spec은 viewBox [1024, 620] + terminal `yOffset -24` + 카드 1항목씩으로 고정했다.

지각(perceptual) 리뷰는 이 세션에서 이미지 판독으로 수행했다(receipt의 `visualReview`는 계약대로 `pending`이며 자동 판정 `status` `pass`를 덮어쓰지 않는다). 검사 대상은 artifact SHA-256 `51fdc982…`(810,324 bytes)의 1440x900 dark와 2048x1320 light 캡처다. 노드·라벨·관계선의 겹침이나 잘림이 없고, 실패 분기 3개가 점선 security 스타일로 구분되며 legend와 PATH/MAP/LENS dock이 stage와 교차하지 않는다. 관찰 하나: 1440x900에서 node context 텍스트가 6.36px로 하한(6px)에 근접해 작게 보이고 1920 이상에서 7px로 올라간다. 이 다이어그램은 실제 실행 추적이 아니라 코드 기준 구조 참조다.


### 한국어 wake 헤드 held-out 일반화 실측 — 2026-09-22 KST (세션 01a0b7f6 계속)

`scripts/evaluate_jarvis_korean_wake.py` 를 추가해 번들 한국어 wake 헤드가 **학습에 쓰지 않은 합성 음성**에서 얼마나 버티는지 실측했고, 원본 수치를 [jarvis-wake-heldout-eval.json](jarvis-wake-heldout-eval.json) 으로 커밋했다. 프로덕트 계약은 건드리지 않았다. 임계값은 상수 `WAKE_THRESHOLD = 0.65` 그대로이고 `threshold_changed: false`, `human_speakers: false`, `microphones_or_rooms: false` 다. 측정 대상은 번들 헤드 `voice/models/hey_jarvis_ko_ridge.onnx` (6,406 bytes, SHA-256 `ada722ad83ff87538a43cfb2c9b774fa98ab6f1e79a7976dd83a90d5ce06e9ec`) 이고 평가기 리비전 SHA-256 도 함께 남겼다.

세 런의 결과는 다음과 같다.

- **Qwen3-TTS CustomVoice 9화자 · 36 클립**: `positive_accept_rate` 0.777778 (7/9). 통과는 `aiden` 0.89242 · `dylan` 0.833754 · `eric` 0.816246 · `ryan` 0.792483 · `serena` 0.792483 · `sohee` 0.879343 · `uncle_fu` 0.869058 이고, 미스는 `ono_anna` 0.64375 과 `vivian` 0.521097 로 둘 다 임계값 아래다. 부정 27개는 `negative_false_accept_rate` 0 이며 임계값에 가장 근접한 부정은 `serena` "자비스 봇이야" 0.642705 와 `vivian` "자비스 봇이야" 0.628359 다. stock openWakeWord 헤드는 같은 코퍼스에서 양성 0/9, 부정 오수락 0/27 이다.
- **Qwen3-TTS 같은 두 화자 독립 재렌더 · 8 클립**: `dylan` "자비스 봇이야" 가 0.672780 으로 임계값을 넘어 **오수락 1건**이 재현됐다(9화자 런의 같은 문장은 0.547610 이었다). 같은 텍스트·화자라도 TTS 샘플링만으로 결정 경계를 넘을 수 있고, 그 여유가 얇다는 뜻이다. 이 런은 양성 2/2 통과이므로 이 값을 음성 일반화 실패가 아니라 렌더 분산의 하한으로 기록한다.
- **macOS `say` 9보이스**: 한국어가 실제로 렌더되는 보이스는 Yuna 하나뿐이라 양성 3/3 통과(0.865792 / 0.826543 / 0.745961)와 최근접 부정 0.645362(`안녕하세요`)만 채점됐고, 나머지 8개 보이스는 0.016초 무음으로 `clip_shorter_than_one_frame` 사유로 **채점 없이 거부**됐다(33건). 합성 실패를 wake 미스로 세지 않는다는 스크립트 계약이 그대로 동작한 것이다.

테스트는 두 겹이다. `tests/test_jarvis_wake_eval.py` (23 tests) 는 프레임 단위 경계(640 bytes = 1 프레임, 0 프레임 무크래시, 임계값 정확히 일치·미만), 임계값 불변, 헤드·백엔드·numpy 부재 시 fail-closed, 무음·단편·빈 클립 거부, 8 kHz → 16 kHz 리샘플, 스테레오 거부를 고정한다. `tests/test_jarvis_wake_evidence.py` (10 tests) 는 커밋된 증거 번들이 측정한 헤드와 같은 바이트인지 재해시하고, 모든 비율을 원본 클립 행에서 다시 계산하며, `accepted` 가 `custom_max >= 0.65` 로 유도되는지, 거부된 렌더가 채점되지 않았는지, 번들에 임시 경로가 남지 않았는지를 검사한다. 고정 Python 3.11 에서 CI focused 목록 22개 모듈 전체는 **Ran 589 tests in 110.460s, OK (skipped=9)** 다(9개 skip 은 numpy 부재 게이트 몫이다).

이 절이 닫지 않는 것:

- 사람 음성·마이크·방 잔향은 여전히 미검증이다. 여기 수치는 전부 합성 음성이고, 헤드 자체가 합성 양성 1개 + 명시적 부정으로 적합됐으므로 합성 일관 구간의 일반화만 한정한다.
- `ono_anna`·`vivian` 미스와 두 번의 "자비스 봇이야" 근접값은 임계값을 낮춰 해결할 대상이 아니다. 다화자 양성 코퍼스가 쌓이기 전까지 이 헤드는 승격 대상이 아니라는 운영 메모로 남긴다.
- `say` 백엔드는 한국어 실보이스가 Yuna뿐이라 독립 합성기로서의 증거력이 제한적이다.
- 이 실측은 로컬 격리 venv와 로컬 합성기의 결과이며 설치된 `OpenKakao Jarvis.app` 런타임이나 실제 마이크 입력을 입증하지 않는다.
- 독립 리뷰(AHP ≥98)는 이번 턴에도 차단됐다(Codex Web 서브에이전트 3회 연속 `page.goto: net::ERR_ABORTED at https://chatgpt.com/?temporary-chat=true`, 이 호스트의 `curl https://chatgpt.com/` 도 403). 누적 14세션째 차단이며 AHP 점수는 얻지 못했다.


### Browser-Use 전용 런타임 고정과 준비 프로브 — 2026-09-22 KST (세션 01a0b7f6 계속)

종전 한계 기록이 "`playwright` 와 `browser_use` 는 저장소 어디에도 버전이 고정되어 있지 않다"고 적었던 상태를 닫았다. `browser/pyproject.toml` 에 실측에 쓴 쌍을 정확히 고정하고(`browser-use==0.13.10`, `playwright==1.63.0`, `requires-python = ">=3.11,<4"`), `scripts/run-jarvis-browser.sh` 가 uv로 그 전용 환경을 실행하며, `scripts/jarvis_browser_runtime.py` 가 웹 작업 전에 세 가지를 확인한다.

1. 두 배포판이 정확히 고정 버전으로 설치되어 있는지(`missing_distribution:<name>` / `version_mismatch:<name>:<found>`)
2. 어댑터 자신의 `browser_agent_kwargs` 가 여전히 바인딩을 만들어내는지. 설치되어 있어도 시그니처가 바뀐 릴리스는 작업 도중이 아니라 시작 전에 닫는다(`binding:unsupported:...`, `binding:session_construction_failed`, `binding:no_browser_argument`, `binding:adapter_import_failed`, `binding:browser_use_import_failed`).
3. Playwright Chromium이 실제로 있는지. 3상태다(`true` / `chromium_not_installed` / `chromium_unknown`). 프로브가 돌지 못한 경우는 미설치가 아니라 불확실로 기록하고, 둘 다 준비되지 않은 상태로 취급한다.

`uv run --project browser --python 3.11 python scripts/jarvis_browser_runtime.py --json` 실측(2026-09-22 KST, CPython 3.11.9):

- provisioned 환경: `{"binding": "ok", "binding_arguments": ["browser"], "chromium_installed": true, "installed": {"browser-use": "0.13.10", "playwright": "1.63.0"}, "pinned": {...동일...}, "ready": true, "reasons": []}`, exit 0. `binding_arguments` 가 `browser` 인 것은 이 릴리스가 modern 바인딩을 쓴다는 종전 실측과 일치한다.
- provision되지 않은 고정 Python 3.11: `ready=false`, `reasons=["missing_distribution:browser-use", "missing_distribution:playwright", "binding:browser_use_import_failed", "chromium_unknown"]`, exit 1.

`tests/test_jarvis_browser_runtime.py` 17 tests는 (1) `PINNED_DISTRIBUTIONS` 가 `browser/pyproject.toml` 과 정확히 같은지, (2) 핀 형식이 3단 정확 버전인지, (3) 실 어댑터의 legacy(`browser_context`)·`**kwargs`·modern 경로 판정이 각각 `ok`·`unsupported:...`·`session_construction_failed` 로 닫히는지, (4) 모든 reason 코드, (5) Chromium 3상태와 프로브 예외 → `chromium_unknown`, (6) CLI의 exit code와 crash guard를 고정한다. 고정 Python 3.11에서 CI focused 24개 모듈 전체는 **Ran 606 tests in 99.697s, OK (skipped=9)** 다.

재현성은 잠금 파일로 한 겹 더 조였다. `browser/uv.lock` (890,452 bytes)을 커밋하고 `scripts/run-jarvis-browser.sh` 가 `uv run --project browser --python 3.11 --frozen ...` 으로 실행한다. 실측 두 가지: 잠금 파일이 있는 provisioned 환경에서는 같은 `ready: true` JSON과 exit 0이 나오고, 잠금 파일 없이 pyproject만 복사한 임시 디렉터리에서는 `error: Unable to find lockfile at uv.lock, but --frozen was provided` 메시지로 즉시 닫혀 재해석으로 새 의존성을 끌어오지 않는다. 따라서 고정 버전과 해석된 전이 의존 트리가 함께 커밋되며, 핀을 올릴 때는 `uv lock` 을 다시 돌려야 한다.

이 절이 닫지 않는 것:

- 이 환경은 앱 번들에 포함되지 않는다. 설치본에서 Browser-Use를 쓰려면 배포 시 `browser` 프로젝트로 환경을 provision해야 하고, 설치된 `OpenKakao Jarvis.app` 자체의 이 경로는 아직 실측하지 않았다.
- Chromium 존재 확인은 Playwright의 `chromium.executable_path` 를 읽는다. 실제 launch와 웹 탐색의 end-to-end 검증은 종전 격리 venv 실측(2026-09-22, 위 Browser-Use 절)이 담당한다.
- 핀은 지금 최신 릴리스(`playwright 1.63.0`, `browser-use 0.13.10`)이고 PyPI 메타데이터로 존재를 확인했지만, 그 버전이 나중에 yank되면 설치는 실패한다. 그때는 핀을 올리고 프로브를 다시 통과시켜야 한다.


### 삭제된 UI 표면을 테스트로 고정 — 2026-09-22 KST (세션 01a0b7f6 계속)

"대량 검증·기능 점검·권한 설정·자기개선 항목을 전면 삭제"는 이번 목표의 핵심 요구인데, 지금까지는 그 삭제가 **마크업의 성질**일 뿐이었다. `desktop/src/ui.ts` 에 그런 섹션이 다시 들어와도 실패하는 검사가 없었고, `design-contract.test.ts` 는 팔레트와 금지 표현만 막았다. `desktop/src/__tests__/ui-removal-contract.test.ts` (7 tests)를 추가해 세 표면을 함께 고정했다.

- 메인 패널: 인터랙티브 요소가 `<button>`·`<select>`·`<input>`·`<textarea>`·`<a>`·`<details>`·`<summary>` 전체에서 정확히 1개이고, 그 하나가 `aria-label="설정 열기"` 인 `gear` 다(`MAIN_PANEL_CONTROLS` 는 `["gear"]`).
- 설정 창: `<h2>` 섹션이 `대상 채팅방 · AI 모델 · Voice · 카카오 DB 동기화 · 색인 · DREAM-RSI · Knowledge · History` 일곱 개와 **순서까지** 정확히 일치하고, `<main>` 셸은 `settings-shell` 하나뿐이며 `<iframe>` 이 없다.
- 금지 토큰: `검증·점검·권한·자기개선·자가개선·자동개선·일괄·대량·permission·verify·self-improv·selfimprov·bulk·approve·audit` 를 메인 패널 마크업·설정 마크업·`desktop/src/styles.css` 에서 대소문자 무시로 검사한다. 탐지기가 비어 있으면 계약이 영원히 통과하므로, 금지 토큰이 실제로 걸리는지 먼저 확인하는 테스트를 둔다(`"<section>일괄 검증</section>"` → `["검증", "일괄"]`).

회귀 검증은 주입으로 했다. `ui.ts` 의 `settingsMarkup()` 이 돌려주는 마크업에 `대량 검증 · 권한` 섹션과 `점검 실행` 버튼을 임시로 넣고 이 모듈만 실행하면 **7개 중 2개가 실패**한다(설정 마크업 토큰 검사, 섹션 순서 검사). 백업에서 바이트 단위로 복원한 뒤 SHA-256이 주입 전과 동일함(`79a051ace4ef7c48…`)을 확인했고, 전체 desktop 스위트는 **93 tests**(7 files)로 다시 통과했다.

이 절이 닫지 않는 것:

- 이 검사는 마크업·스타일시트 문자열 계약이다. 화면에 실제로 픽셀이 그려지는지, 또는 런타임에 동적으로 생성되는 노드가 있는지는 검증하지 않는다. 설치본 앱 창의 시각 확인은 Computer Use 경로가 이번 턴에도 도구 표면에서 노출되지 않아 여전히 미수행이다.
- 금지 토큰 목록은 지금 저장소에 없는 단어들을 열거한 것이다. 한국어 동의어를 새로 만들면(예: "심사") 목록에 추가해야 잡힌다.


### 렌더된 화면 자체를 브라우저에서 교차 검증 — 2026-09-22 KST (세션 01a0b7f6 계속)

`ui-removal-contract.test.ts` 는 마크업 **문자열**을 고정한다. "제품이 실제로 그리는 창에 조작 요소가 톱니바퀴 하나뿐인가", "코어가 실제 GPU 경로에서 프레임을 만드는가"는 다른 주장인데 지금까지 그 증거가 없었다. `scripts/jarvis_desktop_render_check.py` 를 추가해 빌드 산출물(`desktop/dist`)을 Chromium에 올리고 Tauri 브리지를 결정적 stub으로 대체한 뒤, 렌더된 DOM과 실제 WebGL 컨텍스트를 그대로 기록했다. 측정 시점 트리는 HEAD `c985c4b` 이고, 이 절이 추가하는 파일은 커밋 직전 상태였다.

- 기대값은 TS 계약 파일에서 읽는다(`REMOVED_TOKENS`, `SETTINGS_SECTIONS`, `aria-label`). 영수증에 그 파일의 SHA-256을 함께 남기므로 두 검사가 같은 단일 출처를 공유한다. `desktop/src/ui.ts` 와 `desktop/src/styles.css` 의 h2 순서·금지 토큰을 파이썬에서 다시 확인하는 `tests/test_jarvis_desktop_render_check.py` (20 tests)도 붙였고, CI focused 목록에 추가했다.
- 패널: 조작 요소가 정확히 1개(`button#gear`, `aria-label="설정 열기"`), `tabindex`/`contenteditable` 로 늘어난 포커스 대상 0개, `<main>` 1개(`jarvis-panel`), iframe 0개, 렌더된 텍스트(`⚙︎`)와 마크업 양쪽에 금지 토큰 0개. 톱니바퀴를 실제로 클릭하면 stub이 `open_settings` 호출을 기록했다.
- 설정 창: `<h2>` 7개가 `대상 채팅방 / AI 모델 / Voice / 카카오 DB 동기화 · 색인 / DREAM-RSI / Knowledge / History` 순서로 정확히 일치했고 `<main>` 1개(`settings-shell`), iframe 0개, 금지 토큰 0개였다.
- WebGL: `WebGL 2.0 (OpenGL ES 3.0 Chromium)`, unmasked renderer `ANGLE (Apple, ANGLE Metal Renderer: Apple M5 Max, Unspecified Version)`, `gl.getError()` 0. 이 렌더는 소프트웨어 래스터가 아니라 이 호스트의 Metal 경로를 통과한다.
- 프레임: 같은 페이지에서 유휴(작업 부하 0) 13.48 fps, 부하(`job_load 0.9` + 파이프라인 `model` 단계 + 긱뉴스 전송 + DB 동기화) 22.47 fps. 코드의 상한(15/30)과 `AnimationLoop.frameIntervalMs()` 경계가 그대로 관측됐고, 부하가 프레임 밀도를 올린다는 목표 동작이 수치로 확인됐다. `renderCount` 는 유휴 16 → 부하 106이었다.
- 밝기 관찰(주장 아님): 밝은 테마 패널의 평균 휘도가 유휴 237.96 → 부하 234.14로 낮아졌다. 액센트 골드 입자·시냅스 불투명도가 부하에서 커지므로 방향은 일치하지만 링 회전 위상도 프레임마다 달라, 이 수치만으로 밀도 증가를 분리해 주장하지 않는다.
- 지식 그래프 드릴다운: 홀로그램은 회전하고 빗나간 클릭은 `reset()` → `rebuildGraph()` 를 부르므로 좌표를 미리 잡아 두고 나중에 클릭하는 방식은 성립하지 않는다. 실제로 합성 `pointerdown` 으로 4px 격자 13,680개를 훑어도 첫 miss 뒤 새로 만들어진 mesh의 `matrixWorld` 가 렌더 없이는 갱신되지 않아 노드 하나만 맞았다. 그래서 스캔 자체를 실제 마우스 클릭으로 수행했다. 233번째 실제 클릭에서 `최연우` 가 선택됐고 `2-hop · 4 nodes · 3 relations`, 관계 행 2개, `+1 hop` 활성 상태가 됐다. `+1 hop` 도 실제 클릭으로 눌러 `3-hop · 5/24 nodes` 로 확장되고 버튼이 다시 비활성화됐다. 선택 시 `retrieve rrf · facts 2 · 알쫀쿠 = 알리바바 클라우드 축약` 이 표시돼 GraphRAG 검색 경로까지 이어졌다.
- 홀로그램 렌더 루프도 따로 샘플했다: 1.502초에 20프레임(13.31 fps).
- 캡처 7장(`jarvis-render-panel.{light,dark,idle,busy}.png`, `jarvis-render-settings.{light,dark,focus}.png`)과 영수증 `jarvis-desktop-render-check.json`. 영수증의 22개 검사는 전부 PASS다.

이 절이 닫지 않는 것:

- 이 화면은 설치된 `OpenKakao Jarvis.app` 창이 아니라 **같은 빌드 산출물을 Chromium에 올린 것**이다. Tauri webview와 Chromium for Testing은 렌더러가 다르고, 메뉴바 창의 276×260 배치·`alwaysOnTop`·hide-on-blur 같은 창 수명주기는 여기서 검증되지 않는다. 설치본 창의 시각 확인은 별도 항목으로 남는다.
- Tauri 브리지는 stub이다. 실제 Rust 브리지가 돌려주는 스냅샷 형식(6키 작업 이벤트 등)은 기존 Rust 테스트가 담당한다.
- DREAM-RSI·색인·모델 소유권 값은 stub payload이므로 실측이 아니다. `model-swap` 은 이 호스트의 실제 상태(외부 `mlx-serve` 가 11234 점유 → 27B 전환 차단)를 그대로 stub에 옮긴 것이고, 앱이 실제로 전환을 거부하는지는 이 절이 입증하지 않는다.
- fps는 headless Chromium의 RAF 환경에서 측정한 값이다. 실제 webview의 합성 주기와 같다고 주장하지 않는다.

### 설치본 설정 창이 드러낸 백엔드 문자열 누출 — 2026-09-22 KST (세션 01a0b7f6 계속)

`ui-removal-contract.test.ts` 와 `scripts/jarvis_desktop_render_check.py` 는 마크업과 번들이 그리는 화면을 고정한다. 그런데 설정 창 AI 모델 카드의 온디바이스 한 줄은 마크업이 아니라 **Tauri 브리지가 `status_label` 로 주입하는 문자열** 이고, 두 검사 모두 그 값을 실제 제품에서 읽지 않았다. 이번 턴에 Computer Use로 설치본을 직접 열어 읽으면서 그 구멍이 뚫려 있는 것을 확인했다.

재현과 관측:

- 설치본 pid 84125는 평소 창이 0개다(패널은 `label() == "jarvis"` 가 포커스를 잃으면 hide된다). 트레이 아이템은 AX로 `2917, 3, 36, 24` 였고 `/tmp/jarvis_click 2935 15` 로 합성 HID 클릭을 보냈다. 앱이 한가할 때 패널은 즉시 뜨지 않고 **약 5–8초 뒤**에 뜨며(창 수를 0.25초 간격으로 40회 샘플링해 마지막 샘플에서 1이 됐다), 뜬 뒤에도 포커스를 잃으면 다시 hide된다. 종전 기록의 "클릭 0.1초 뒤 바인딩"은 창이 이미 떠 있던 조건의 값이다.
- 그 창이 떠 있는 동안 `cua.getApp("/Applications/OpenKakao Jarvis.app")` 는 즉시 붙었다. 패널 AX 트리는 종전과 같다: `0 standard window` → `1 scroll area` → `2 HTML content (tauri://localhost)` → `3 container Jarvis` → `4 image Jarvis core` + `5 button 설정 열기`, 즉 조작 요소는 톱니바퀴 1개다. 스크린샷도 같은 톤이었다: 아이보리 배경, 샴페인 골드 다중 짐벌 링 + 구형 코어, 얇은 시냅스 선과 작은 입자. bloom 후처리·네온·발광 텍스트는 없었다.
- 같은 세션에서 gear(요소 5)를 실제로 클릭해 설정 창(760x760)을 열었다. AX 트리는 `대상 채팅방 · AI 모델 · Voice · 카카오 DB 동기화 · 색인 · DREAM-RSI · Knowledge · History` 7개 `h2` 와 heading 없는 `GeekNews 슬롯` 컨테이너만 담았고 대량 검증·기능 점검·권한 관리 화면은 없었다.
- 그런데 AI 모델 카드의 온디바이스 줄은 `온디바이스: 온디바이스 감지: Apple M5 Max (128GB RAM) · MLX Core/Serve · Qwen3.8 Flash-Next · 설정 검증 통과 · 실추론 통과 (Qwen3.8 Flash-Next)` 였다. `검증` 은 데스크톱 계약이 UI에서 금지한 토큰인데, 이 문자열은 `scripts/auto_reply_ondevice.py` 가 만들고 브리지의 `status_label` 로 들어오므로 마크업 스캔에 걸리지 않는다. 렌더 검사의 stub 도 `status_label` 을 손으로 적어 두어(`실추론 통과` 만 포함) 같은 이유로 통과했다.
- 설정 창은 CUA의 close button(요소 92) 클릭으로 닫았다. 클릭 뒤 관측 호출이 `-10005 timeoutReached` 로 끝난 것은 창이 사라져서이고, 곧이어 AX 창 수 0 · pid 84125 생존을 확인해 이 턴이 찾은 상태로 되돌렸다.

수정은 세 갈래다.

- `scripts/auto_reply_ondevice.py` 의 상태 문자열을 순수 함수 `ondevice_status_strings(...)` 로 분리하고 라벨을 `런타임·가중치 확인됨` / `런타임 미확인` 으로 바꿨다(종전 `설정 검증 통과` / `설정 미확인`). 예외 진단 문구 `온디바이스 검증 실패` 도 `온디바이스 구성 확인 실패` 로 맞췄다. 이 함수는 파일·네트워크·시계에 접근하지 않으므로 렌더되는 문자열을 그대로 검사할 수 있다.
- `tests/test_auto_reply_ondevice.py` 에 `TestOnDeviceRenderedTextGuard` 5개를 추가했다. 금지 토큰은 `desktop/src/__tests__/ui-removal-contract.test.ts` 의 `REMOVED_TOKENS` 에서 직접 읽고, 검출기가 비지 않음도 먼저 확인한다(`일괄 검증 · 권한` → `["검증","권한","일괄"]`). verified/미확인 × probe 있음/없음 4조합 모두에서 상태·상세 문자열에 금지 토큰이 없고, `ondevice_summary_dict` 가 그 순수 함수를 실제로 쓰는지도 확인한다.
- `scripts/jarvis_desktop_render_check.py` 의 Tauri stub 은 이제 같은 함수를 호출해 `status_label` / `status_detail` 을 만든다. 새 check `settings.ondevice_status_is_product_formatted` 는 설정 창이 그 제품 문자열을 정말 렌더하는지 보므로, 토큰 스캔이 렌더되지 않은 문자열에 대해 공허하게 통과할 수 없다. `tests/test_jarvis_desktop_render_check.py` 에는 stub 라벨이 그 함수의 출력과 같은지, 금지 토큰이 없는지, busy 스냅샷도 같은 문자열을 쓰는지 확인하는 4개를 추가했다.

검사에 이빨이 있는지는 주입으로 확인했다. stub 라벨을 종전 문자열(`설정 검증 통과` 포함)로 바꾼 별도 실행에서 렌더 검사는 `status fail` 이고 `settings.no_removed_control_tokens` 가 `['검증']` 로 떨어졌다. 같은 리비전의 수정 상태에서는 **24/24 checks pass** 이다. 영수증 [jarvis-desktop-render-check.json](jarvis-desktop-render-check.json) 과 캡처 7장을 이 실행으로 갱신했다.
- 같은 리비전에서 CI는 3/3 green이다(run 35695406175, 2026-09-22 KST): `macOS cargo test` · `Tauri desktop and focused Python tests` · `Launchd and Python harness` 가 모두 success다. 로컬 focused 25개 모듈은 `Ran 635 tests in 133.711s` / `OK (skipped=9)` 였다.

이 절이 닫지 않는 것:

- 설치는 수정 전 번들 그대로다. pid 84125는 여전히 `설정 검증 통과` 를 렌더하므로, 이 수정은 다음 재빌드·재설치(전환) 때 화면에 반영된다. `docs/architecture/jarvis-live-settings.png` 는 수정 전 설치본을 담은 그대로 둔다.
- 이 셸에서 `screencapture` 는 신뢰할 수 없다: `-R` 은 15–20초 뒤에야 파일을 쓰고 그동안 화면이 바뀌면 다른 창을 담았으며(실제로 Spark 창을 담아 되돌렸다), `-l <windowID>` 는 25초를 넘겨도 끝나지 않아 스스로 종료해야 했다. 창 ID 조회는 `CGWindowListCopyWindowInfo` 를 쓰는 `/tmp/jarvis_win <pid> [title]` 로 됐다. CUA 스크린샷은 대화에만 남고 파일로 저장할 수 없다(`nodeRepl` 에 파일 API가 없고 `nodeRepl.rpc` 는 service 식별자를 요구한다).
- `macos/AutoReplyMenu/main.swift` 에는 같은 부류의 문자열이 아직 7개 있다(`점검 중` 5개, `검증 통과` 계열 2개). 그 앱은 아직 CI에서 빌드되지만 제품 런타임은 Tauri이고, 전환 시 함께 사라질 대상이라 이번 패스에서는 건드리지 않았다.

### 렌더 교차 검증 경로 시각화 — 2026-09-22 KST (세션 01a0b7f6 계속)

`scripts/jarvis_desktop_render_check.py`가 무엇을 검사하는지는 README 문장으로만 남아 있었다. 그 경로 자체를 dataflow로 그려 [jarvis-rendered-cross-check.html](jarvis-rendered-cross-check.html) / [source](jarvis-rendered-cross-check.archify.json)로 남겼다. stage는 `Built bundle → Loopback serve → Browser render → Page probes → Compare and record` 다섯 단계이고, 주 경로는 `desktop/dist → static server → Playwright Chromium → page probes → check set` 이다. `Tauri stub → Chromium`(결정적 스냅샷 주입)과 `ui-removal-contract → check set`(기대값 주입)이 각각 행 0에서 내려와 붙고, 마지막 행에서 `check set → receipt + captures`가 증거를 닫는다.

- 첫 후보(8노드·카드 항목 12개·viewBox `[1068, 580]`)는 `validate`에서 4건의 오류를 냈다: 같은 stage 수직 흐름 3개의 라벨이 각각 `stub`·`contract`·`checks`를 덮었고, `contract-checks`가 같은 행의 `stub`을 2px 여유로 관통했다.
- 수리는 단계적이었다. 먼저 주 경로를 행 1에 모으고 `stub`·`contract`·`receipt`를 같은 stage의 위·아래에 두어 모든 흐름을 인접 구간으로 바꿨고(diagnostic이 지시한 `labelDy: 24` 적용), 그 리비전에서 9/9 checks·0 errors/0 warnings가 됐다.
- 그 후보의 `deliver`는 artifact SHA-256 `c604f65c8cac81689269d169a63055e0427496828b1633f92a230c68b618985b` (810,770 bytes)로 성공했지만 `visual-check`는 `viewer/viewport-overflow` 3건으로 `status fail`이었다(1440x900 light/dark 981px, 1600x1000 1057px). 원인은 카드 항목 수였다: 항목 한 줄이 약 23px이고, 같은 viewBox·같은 카드 3개에 항목 9개인 `jarvis-core-load-mapping`은 네 viewport를 모두 통과한다.
- 그래서 카드마다 항목을 3개로 줄이고(측정값 `gl.getError() == 0`·유휴 13.48fps·`renderCount 16 to 106`·계약 SHA-256은 그대로 남겼다) viewBox를 `[1068, 556]`로 20px 압축해 1440x900 환산 약 24px을 확보했다. 최종 리비전: `validate` 9/9·0/0, `deliver` success(artifact `86d5808101ad37619a99dc1174cdc7d736e443d439d6ab6dfcf5b5e1622e7f73`, 810,487 bytes, spec `1456692d94377a73578fcef88e6491f3929f31c232d7343970cf961b810a8789`), `visual-check` exit 0·`status pass`·diagnostics 0.
- containment: 1440x900 light scroll 1440x900 · 1600x1000 light scroll 1600x1000 · 1920x1080 light scroll 1920x1080 · 2048x1320 light scroll 2048x1320. readability(투영 노드 텍스트 ≥ 6px): 1440x900 light 6.18px · 1600x1000 light 6.48px · 1920x1080 light 6.90px · 2048x1320 light 6.90px. viewer chrome(dock↔stage gap)도 네 viewport 모두 통과했고 캡처 4장과 contact sheet가 함께 갱신됐다.
- 같은 리비전에서 `scripts/jarvis_desktop_render_check.py`를 다시 실행해 **24/24 checks pass**를, `tests.test_jarvis_desktop_render_check`를 실행해 **24 tests, OK**를 재확인했다. README의 종전 "23개 check"는 `c264535`가 `settings.ondevice_status_is_product_formatted`를 더하기 전 수치이며 현재 검사 수는 24개다.

이 절이 닫지 않는 것:

- receipt의 `visualReview`는 계약대로 `pending`이다. 이 세션은 1440x900 light/dark와 2048x1320 light 캡처 3장을 판독해 노드·라벨·관계선의 겹침과 잘림, 빈 하단 띠가 없음을 확인했지만 사람 리뷰를 대신하지 않는다.
- 이 다이어그램은 검사 경로의 구조 증거다. 브라우저에서 실제로 통과했다는 주장은 위 24/24 수치와 [jarvis-desktop-render-check.json](jarvis-desktop-render-check.json) 영수증이 담당한다.
- 카드 항목을 줄인 것은 세로 예산 때문이며, 줄어든 세 항목이 담고 있던 내용(무계정·무네트워크 조건, Rust 쪽 스냅샷 검증 위치, exit 2 폐쇄)은 영수증과 위 절에 그대로 남아 있다.

### 죽은 코드·미사용 항목 감사와 Rust clippy 정리 — 2026-09-22 KST (세션 01a0b7f6 계속)

"죽은 코드와 미사용 변수·함수, 네이밍과 타입 오류를 리팩터링"은 종전까지 테스트 통과로만 주장됐다. 정적 감사를 실제로 돌려 결과를 남긴다. CI는 `cargo test`·`cargo check`·`tsc -p tsconfig.json`·`vite build`를 돌리지만 clippy는 게이트하지 않으므로, 데스크톱 크레이트에 `cargo clippy --all-targets`를 실행했다.

- 감사 결과 전체 경고는 **3건**이고 모두 같은 부류다: `too_many_arguments (8/7)` 3곳(`python_bridge.rs:358` 의 `fetch_settings_action`, `python_bridge.rs:650` 의 `run_python_with_output_limit`, `main.rs:26` 의 Tauri 커맨드). 미사용 import·미사용 함수·죽은 코드 경고는 0건이다. Rust 쪽에는 리팩터링 대상으로 남은 죽은 항목이 없다.
- 그중 사설 함수 `run_python_with_output_limit` 만 수정 대상이었다. 인자 7개를 `PythonRun` 구조체로 묶고 `Default` 를 종전 공통 경로가 위치 인자로 넘기던 값(`OUTPUT_LIMIT_BYTES`, `stdin_payload: None`, `global_abort_grace: None`)으로 정의해, 평범한 호출에서 꼬리의 `None, None` 이 사라졌다. 브라우저 도구 호출부는 `cooperative_cancel: false` 와 `output_limit: BROWSER_TOOL_OUTPUT_LIMIT_BYTES`·`stdin_payload`·`global_abort_grace` 를 그대로 명시한다.
- 나머지 2곳은 계약 형태라 이름을 바꾸거나 묶으면 프런트엔드 호출이 바뀐다. Tauri 커맨드와 브리지 메서드는 settings-action invoke payload의 필드를 하나씩 그대로 받으므로, 두 곳에 사유를 적은 `#[allow(clippy::too_many_arguments)]` 를 달았다(은폐가 아니라 계약 유지의 명시적 선택).
- 검증: 같은 리비전에서 `cargo clippy --all-targets -- -D warnings` 가 경고 0으로 `Finished` 이고, `cargo test` 는 **59 passed; 0 failed** 다. 종전 호출부와 대조해 `cooperative_cancel`·output limit·stdin payload·abort grace 값이 호출자별로 보존됨을 확인했다.
- 남는 한계: clippy는 CI 게이트가 아니므로 이 청정 상태는 로컬 측정이다. CI에 clippy를 추가하는 변경은 이번 패스에서 하지 않았다.
- TypeScript 쪽은 `npm run build` 가 `tsc -p tsconfig.json` 을 먼저 돌리므로 타입 오류는 이미 게이트된다. Python 쪽은 핀 인터프리터에 pyflakes/ruff/vulture가 없어 같은 감사를 실행하지 못했고, 도구를 임의로 설치하지 않았다.

### clippy 게이트를 CI에 추가 — 2026-09-22 KST (세션 01a0b7f6 계속)

앞 절은 "clippy는 CI 게이트가 아니므로 이 청정 상태는 로컬 측정"으로 끝났다. 저장소가 `rust-toolchain.toml` 에서 `channel = "1.95.0"` 과 `components = ["clippy", "rustfmt"]` 를 고정하므로 로컬과 러너의 clippy가 같은 버전이다(로컬 `clippy 0.1.95 (59807616e1 2026-04-14)`, `rustc 1.95.0`). 버전 드리프트로 게이트가 흔들릴 수 없다는 전제가 서서 두 크레이트를 게이트했다.

- 루트 크레이트(`openkakao-cli`)의 clippy 경고는 **2건**이었다: `src/reply_receipt.rs:424` 의 `manual_pattern_char_comparison` 과 `src/context/mod.rs:2933` 의 `needless_range_loop`.
- 앞의 한 건은 고쳤다. 응답 영수증의 코드/설명 분리 지점을 찾는 `split_explained_reason` 에서 콜론과 세미콜론을 직접 비교하던 클로저 패턴을 char 배열 패턴으로 바꿨다(`code.find([':', ';'])`). 두 형태가 찾는 위치는 같다.
- 뒤의 한 건은 사유를 적어 남겼다. 그 루프 변수는 반복 횟수가 아니라 **답**이다: 이긴 위치가 `optima` 에 저장되고 재귀 호출의 경계(`best_position` → `second_end`)로 다시 쓰이므로, 슬라이스를 `enumerate` 로 돌리면 이름만 바뀐다. 같은 함수에는 이미 `too_many_arguments` 허용이 같은 방식으로 붙어 있다.
- `.github/workflows/ci.yml` 에 두 스텝을 넣었다: macOS job의 `Lint CLI crate (clippy)`(`cargo clippy --manifest-path $MANIFEST --all-targets -- -D warnings`, `Cargo test` 뒤)와 데스크톱 job의 `Lint desktop Rust bridge offline`(다른 Rust 스텝과 같이 `--locked --offline` 과 `OPENKAKAO_TAURI_SOURCE_CHECK=1`).
- 로컬 검증: 두 명령이 모두 경고 0으로 `Finished` 였고(`cargo clippy --manifest-path Cargo.toml --all-targets -- -D warnings`, `cargo clippy --manifest-path desktop/src-tauri/Cargo.toml --all-targets --locked --offline -- -D warnings`), 루트 크레이트 `cargo test` 는 42개 테스트 타깃에서 **1087 passed; 0 failed; 0 ignored** 다(데스크톱 크레이트는 같은 리비전 59 passed).
- 남는 한계: 이 게이트는 Rust 두 크레이트만 덮는다. Python은 핀 인터프리터에 pyflakes/ruff/vulture가 없어 같은 정적 감사를 돌리지 않았고, 도구를 임의로 설치하지 않았다.
- CI 확인: 이 게이트를 넣은 리비전은 run 35697623043에서 3/3 green이고, 새로 넣은 두 스텝 `Lint CLI crate (clippy)` 와 `Lint desktop Rust bridge offline` 이 각각 success다(데스크톱 job의 focused Python 테스트·Vitest·Vite build·Rust test/check도 함께 green).
- 아티팩트 무결성: 커밋한 `jarvis-rendered-cross-check.html` 과 `.archify.json` 의 SHA-256이 deliver 영수증(`86d58081…`, `1456692d…`)과 바이트 단위로 일치하고, visual-check 영수증의 artifact 해시도 같다.

## DPO 수치 안정성 guard와 WAL sidecar 복제 검증 — 2026-09-22 KST

부모 에이전트가 로컬에서 수행했다. 같은 날 ChatGPT Web 서브에이전트 경로는 `https://chatgpt.com/`이 403을 돌려 닫혀 있었다.

### 1. 발견한 결함과 수정 전후 측정

`scripts/auto_reply_finetune.py`의 `dpo_loss_from_logprobs`는 softplus 형태(`log1p(exp(-|delta|)) + max(-delta, 0)`)를 써서 큰 |delta|에서도 안정적이지만 입력 검증이 비어 있었다. 수정 전 리비전은 `git show HEAD:scripts/auto_reply_finetune.py`로 임시 경로에 꺼내 같은 함수를 직접 호출해 측정했고, 작업 트리는 건드리지 않았다.

| 입력 | 수정 전 (HEAD `a362de8`) | 수정 후 |
| --- | --- | --- |
| `beta=nan` | `status ok`, `loss nan` | `eval_unavailable`, `invalid_beta` |
| `beta="abc"` | `ValueError: could not convert string to float: 'abc'` | `eval_unavailable`, `invalid_beta` |
| `beta=None` | `TypeError: float() argument must be a string or a real number` | `eval_unavailable`, `invalid_beta` |
| `chosen=[-1e308]`, `rejected=[1e308]` | `status ok`, `loss 0.0` | `eval_unavailable`, `non_finite_delta` |
| 위 두 쌍을 `evaluate_preference_pairs`에 함께 투입 | `mean_loss 0.30359586242039094`, `evaluated 2`, `unavailable 0` | `mean_loss 0.6071917248407819`, `evaluated 1`, `unavailable 1` |

`loss 0.0`이 특히 위험하다. 이 함수의 계약은 "로그확률이 없으면 수치를 만들지 않는다"인데, 넘친 차이값이 `softplus(inf) = 0.0`, 즉 "완벽한 응답"으로 둔갑해 `mean_loss`를 끌어내렸고 그 쌍을 `evaluated`로 셌다. `--dpo-pairs`로 들어오는 JSONL은 외부 입력이므로 이 경로는 실제로 도달 가능했다.

가드는 세 곳이다. beta를 유한 실수로 검증(`invalid_beta`), delta를 유한값으로 검증(`non_finite_delta`), loss를 유한값으로 검증(`non_finite_loss`). 세 번째는 delta가 유한하면 도달하지 않는 방어 분기이며, 어떤 입력에도 비유한 수치를 `status="ok"`로 돌려주지 않는다는 계약을 코드에 남기기 위한 것이다.

회귀 검출력은 대조 실행으로 확인했다. 신규 테스트가 요구하는 4개 단언을 수정 전 모듈에 그대로 물리면 `RESULT all-detected`로 네 개가 모두 실패했고, 수정 후 모듈에는 `RESULT no-defect-detected`로 모두 통과했다.

### 2. WAL sidecar 복제 경로의 첫 직접 검증

기존 격리 복제 테스트 4건은 모두 rollback journal 모드 소스(`sqlite3.connect(db)` 기본값)를 썼기 때문에 `-wal`/`-shm` sidecar 복사 루프가 실제 파일로 한 번도 실행되지 않았다. 세 건을 추가해 그 경로를 닫았다.

측정값(고정 Python 3.11, 실제 `_snapshot_signature`):

| 검증 | 관측 |
| --- | --- |
| WAL 모드 소스의 미체크포인트 행 | `journal_mode wal`, `wal exists True`, `shm exists True`, 복제본 `rows [('wal only row',)]`, `wal sidecar copied True` |
| 복사 중 sidecar 이동 | `isolated_copy_mutated {"attempt": 1}` → `isolated_copy_retry_succeeded {"attempt": 2}` |
| 매 복사마다 이동 | `isolated_copy_mutated` 1·2·3 → `OperationalError: consistent isolated snapshot unavailable` |

### 3. 실행 기록과 문서 정정

```bash
/Users/twoimo/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11 -m unittest tests.test_auto_reply_finetune.DpoNumericStabilityTests tests.test_auto_reply_knowledge_graph.IsolatedReadOnlyConnectionTests
/Users/twoimo/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11 -m unittest tests.test_auto_reply_finetune tests.test_jarvis_unit4
# 그리고 CI focused 25개 모듈 전체(.github/workflows/ci.yml의 같은 목록)
```

결과: 두 클래스 **13 tests, OK**, 두 모듈 **90 tests, OK**, CI focused 25개 모듈 **Ran 644 tests in 113.283s / OK (skipped=9)**다. 같은 목록의 직전 측정은 635 tests(`c264535`)이므로 신규 9건이 그대로 더해졌다.

README의 렌더 교차 검증 bullet은 "23개 check"로만 적혀 있어 같은 bullet 뒤쪽의 24/24와 어긋났다. "렌더 교차 검증은 23개 check로 처음 통과했고(이후 `status_label` 렌더 check가 더해져 24개가 된다)"로 정정해 두 수치의 관계를 명시했다.

### 4. CI와 위임 리뷰 상태

- 같은 리비전의 CI: run [35699214811](https://github.com/twoimo/openkakao-bot/actions/runs/35699214811) at `d11a01f` → 3/3 jobs success. 직전 `a362de8` run 35697996000 → 3/3 success, `568c6ee` run 35697623043 → 3/3 success. run 35696758313(`9aa2e2d`)은 branch concurrency로 cancelled이므로 green으로 세지 않는다.
- 위임 리뷰는 이번 턴에도 성립하지 않았다. `multi_agent_v1__spawn_agent`의 model override 목록은 여전히 `gpt-6-astra`, `gpt-5.6-sol`, `chatgpt-web/{medium,high,extra-high}` 뿐이고 사용자가 지정한 `GPT-5.6 Luna`는 없다. `chatgpt-web/extra-high`(xhigh) 새 스레드 3개(Fermat `01a0c7fe…`, Sartre `01a0c804…`, Bohr `01a0c80a…`)가 3회 모두 `stream disconnected before completion: page.goto: Timeout 60000ms exceeded`(navigating to `https://chatgpt.com/?temporary-chat=true`)로 끝났고, 같은 시각 `curl https://chatgpt.com/`은 http=403, `curl https://api.github.com/repos/twoimo/openkakao-bot`은 http=200이었다. 누적 실패는 23회(abort 19 · timeout 4)다. 따라서 AHP 점수를 얻지 못했으므로 98점 달성을 주장하지 않는다.

- 러너에서도 같은 수치를 확인했다. run 35699214811의 `Tauri desktop and focused Python tests` job에서 `Run focused local AI tests` step은 `Ran 644 tests in 63.562s` / `OK (skipped=60)`을 보고했다(로컬은 같은 644건에 skip 9건; 러너에는 없는 인터프리터·하드웨어 때문에 skip 수가 다르다). 이 기록을 담은 docs 커밋 `52319d8`의 run 35700981610도 3/3 success다.

## 렌더 프레임·질의 경로 병목 실측과 창 숨김 렌더 정지 신호 — 2026-09-22 KST

부모 에이전트가 로컬에서 수행했다. 위임 경로는 이번 턴에도 성립하지 않아 4절에 상태를 남긴다.

### 1. 질의 경로: LSH 부호 행렬 캐시와 동의어 조회표

`scripts/auto_reply_knowledge_graph.py`의 `_ann_band_keys`는 8밴드 x 8비트 = 64개 하이퍼플레인의 부호를 투영 루프 안에서 매번 `blake2b`로 다시 계산했다. 부호는 `"{bit}:{dim}"` 두 좌표와 salt `kg-ann-v1`로만 결정되므로 2560차원 질의 벡터 하나에 64 x 2560 = 163,840회 해시가 필요했다. 이제 (비트, 차원) 모양별로 서명 행렬을 한 번 만들어 재사용한다(`_ann_sign_matrix` + `_ANN_SIGN_MATRIX_CACHE`, 최대 8개 모양).

측정 방법: 고정 인터프리터 `/Users/twoimo/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11`에서 HEAD 리비전(`66c104b`)의 모듈 사본(`/private/tmp/okb-kghead-17631/scripts/auto_reply_knowledge_graph.py`)과 작업 트리 모듈을 같은 프로세스에 로드해 같은 입력으로 재었다. 입력 벡터는 `random.seed(7)`의 표준정규 2560차원이다.

| 측정 | 수정 전 (HEAD `66c104b`) | 수정 후 |
| --- | --- | --- |
| `_ann_band_keys` 2560차원 1회, 중위값(60회) | 71.139 ms | 6.032 ms |
| 같은 연산 50개 벡터, 중위값(3회) | 3,560.9 ms | 320.9 ms |
| `_ann_sign_matrix(64, 2560)` | 매 질의 재계산 | 콜드 63.41 ms(64행 x 2560바이트) · 웜 2.4 µs |
| `_alias_matches` (낱말, 바늘더미) 1쌍, 중위값(400회 x 5낱말) | 4.34 µs | 1.10 µs |
| 모듈 import(접힌 사전 빌드 포함) | — | 1.35 ms |

`_alias_matches`는 호출마다 사전 전체를 훑으면서 항목마다 `[v.casefold() for v in syn_vals]`를 새로 만들었다. 이제 casefold 조회표 `_SYNONYM_INDEX`(사전 16개 키 → 접힌 낱말 33개)를 모듈 로드 때 한 번 만들고 `_SYNONYM_INDEX.get(folded, ())`만 본다. 접힌 항목이 원래 키를 그대로 들고 있어 문맥 게이트(`CONTEXT_GATED_SYNONYM_KEYS`)는 예전과 같은 원문 키로 비교한다.

동등성은 같은 입력 집합에서 확인했다. 불일치 0건이다.

- `_ann_band_keys`: 합성 25개 벡터(차원 1·3·8·64·128·2560, 빈 벡터, 부호 혼합 3차원, NaN 포함 3차원, 난수 16개).
- 앱 자신의 ANN 저장소(`~/Library/Application Support/openkakao/bujamentor/knowledge-dense-ann.sqlite3`, `index_version` `bge-m3-lsh-v1`·watermark `1790005898`·`dense_vectors` 50행·`ann_buckets` 400행·8밴드)에서 복제한 2560차원 벡터 5개. **이미 만들어진 버킷 테이블과의 호환은 이 비교가 근거다.** 복제본은 SQLite backup 후 `mode=ro`·`query_only`로만 읽었고 원본은 바꾸지 않았다.
- `_alias_matches`: 낱말 7 x 바늘더미 5 x 문맥 3 = 105조합.

회귀 검출력은 변이로 확인했다(각 변이를 물린 뒤 복원까지 확인, 세 모양 8·64·2560 모두 동일한 결과). 같은 모듈 사본으로 잰 기준선은 고정 키와 일치했고, 부호 극성 반전 변이는 세 모양 모두 검출됐고 salt를 `kg-ann-v9`로 바꾼 변이도 세 모양 모두 검출됐으며, `CONTEXT_GATED_SYNONYM_KEYS`를 비운 변이는 문맥 게이트 테스트의 기대를 뒤집었다.

남는 한계: 서명 행렬은 프로세스 단위 캐시다. 답변 생성 경로는 짧게 사는 CLI 프로세스라 실행마다 콜드 63.41 ms를 한 번 지불하고, 그 대신 질의마다 65 ms를 되돌려받는다. 색인 루프처럼 프로세스가 오래 사는 경로에서는 그 비용이 한 번뿐이다.

### 2. 렌더 프레임: 프레임당 할당 0회와 실측

프레임 신호 계산을 `desktop/src/core/render-state.ts`의 `CoreRenderState`로 옮기면서 15-30fps 루프가 프레임마다 만들던 네 개의 힙 객체가 사라졌다: `[reply, geeknews, dbSync]` 배열, `base.map` 결과 배열, `forEach` 클로저, `latticePulse` 객체 리터럴. `desktop/src/core/load-mapping.ts`의 `writeRingTargetVelocities`·`writeLatticePulse`가 호출자가 소유한 버퍼에 직접 쓰고, `step`은 새 객체를 만들지 않고 자기 자신을 프레임 결과로 돌려준다.

측정은 `desktop/src/perf/render-path-node.ts`를 esbuild로 번들해 plain node로 돌리는 `cd desktop && npm run bench:render`다(2,000,000회 x 5라운드, 중위값). vitest bench는 SSR interop 때문에 같은 함수를 3.8-4.0 M ops/s로 재서 실제 V8 동작을 반영하지 못했으므로 쓰지 않았다.

| 케이스(프레임당) | 수정 전 | 수정 후 |
| --- | --- | --- |
| 링 목표 각속도 | `base.map` + 클로저 44.1-47.2 ns | 재사용 배열 8.5-12.3 ns |
| 격자 펄스 | 객체 리터럴 5.1-7.0 ns | 재사용 객체 6.1-7.0 ns |
| 프레임 전체 신호 계산 | 인라인 렌더 본문 253.0-269.2 ns | `CoreRenderState.step` 218.6-233.2 ns |

프레임 전체는 5회 실행 중위값 263.5 ns → 224.8 ns(−38.7 ns, −14.7%)이고 다섯 번의 실행 모두 같은 방향이었다. 정직하게 남길 두 가지가 있다.

1. 이 벤치마크로 처음 얻은 결론은 "프레임 전체가 21% 느려졌다"였다. 그 측정의 수정 전 복사본이 실제 옛 본문에 있던 음향 펄스 항(`1 + smoothVoiceRms * (0.07 + 0.025 * sin(nowMs * 0.012))`)을 빠뜨려 수정 전을 실제보다 싸게 재고 있었다. 그 항을 복원한 뒤에는 위처럼 수정 후가 빠르다. 즉 수정 전후 비교는 양쪽이 같은 계산을 할 때만 유효하고, 이번 수치는 그 조건을 맞춘 값이다.
2. 격자 펄스는 재사용 객체 쪽이 0.5-1.5 ns 느리게 나온 실행이 더 많다. 이 항목의 이득을 주장하지 않는다. 실제 이득은 할당 제거이며, 해제된 프레임당 할당은 네 개다. 같은 성질을 테스트로도 못 박았다: `desktop/src/__tests__/render-state.test.ts`가 `Array.prototype.map`·`slice`·`concat`을 0회 호출로 고정한다.

### 3. 창 숨김 시 렌더 정지: 이제 셸이 상태를 알린다

종전에는 숨김을 `blur`·`visibilitychange`·`pagehide` 같은 DOM 이벤트로만 추론했다. AppKit이 창을 order out 하는 방식에 따라 그 이벤트가 아예 오지 않을 수 있고, 그러면 3D 루프가 숨은 창 뒤에서 계속 돈다. 이제 셸이 `show()`/`hide()`가 돌아온 뒤 `jarvis://visibility`(`{"visible": <bool>}` 한 값만)를 그 창에 보내고, 프런트엔드는 그 신호를 권위 있는 값으로 쓴다.

- Rust(`desktop/src-tauri/src/main.rs`): `set_window_visible`이 `show`/`hide`를 호출하고 OS 호출이 성공한 뒤에만 알린다(실패하면 알리지 않고 웹뷰는 이미 가진 상태를 유지한다). `toggle_panel`, `open_settings`(표시), `Focused(false)`(jarvis 숨김), `CloseRequested`(jarvis·settings 숨김) 네 경로가 모두 이 함수를 지난다.
- TS(`desktop/src/core/lifecycle-wiring.ts`): `wireRenderLifecycle`가 blur/focus/visibilitychange/pagehide와 Rust 이벤트를 한 곳에서 처리하고, 이벤트 핸들러 밖으로 예외가 새지 않도록 `transition`을 감싼다. 브리지가 없는 일반 브라우저에서도 DOM 기반 동작이 그대로 남고, detach는 멱등이다.
- `desktop/src/core/lifecycle.ts`: `RenderLifecycle.transition`이 `startTimers`·`loop.start`·`stopTimers`·`loop.stop`을 각각 감싼다. 종전에는 `stopTimers`가 던지면 `loop.stop()`이 건너뛰어져 숨은 창 뒤에 렌더 루프가 남을 수 있었다.

검증: 신규 `desktop/src/__tests__/lifecycle-wiring.test.ts` 10건이 blur→정지, focus→재개(RAF 정확히 1회), `document.visibilityState`가 visible인데도 명시적 hidden이 오면 정지, pagehide 1회 close와 멱등 detach, 브리지 실패 시 DOM 경로 유지, 종료 시 구독 해제를 확인한다. Rust의 `#[cfg(test)]` 3건은 payload에 불리언 하나만 있는지, `include_str!`로 읽은 프런트엔드 파일이 같은 이벤트 이름을 쓰는지(두 언어에 걸친 계약), 트레이 아이콘이 18x18 RGBA인지 검사한다.

남는 한계: 이번 턴에 설치본을 다시 띄워 숨김 상태의 렌더 호출 0회를 재지는 않았다. 숨김 창의 렌더 호출 0회는 기존 실기기 기록(`jarvis-three-render-lifecycle`)이고, 이번 변경이 바꾼 것은 그 정지를 DOM 추론이 아니라 셸 신호로 확정한 점이다. 같은 턴에 그 정지·재개 계약 자체를 빌드된 번들의 브라우저 하네스에서 다시 쟀고 결과는 6절에 있다(설치본 측정은 아니다).

### 4. 검증 수치, CI, 위임 상태

- Python: `tests.test_auto_reply_knowledge_graph`는 HEAD `66c104b`에서 75 tests(같은 사본 워크트리 `/private/tmp/okb-kghead-17631`로 측정)이고 이번 리비전은 **82 tests, OK**(19.331초)다. CI focused 25개 모듈은 로컬 **Ran 651 tests in 214.359s, OK (skipped=9)**이고, 러너에서는 같은 651건이 **Ran 651 tests in 60.629s / OK (skipped=60)**로 보고됐다(러너에 없는 인터프리터·하드웨어 때문에 skip 수가 다르며, 실행된 테스트 수는 같다).
- 데스크톱: `npx vitest run` **113 tests / 9 files**(이전 93 tests / 7 files; 신규 `render-state.test.ts` 10 + `lifecycle-wiring.test.ts` 10), `npx tsc -p tsconfig.json --noEmit` clean. 러너 로그도 `Test Files 9 passed (9)` / `Tests 113 passed (113)`이다.
- Rust: `cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked --offline` **62 passed**(이전 59). 러너의 `Test desktop Rust bridge offline` step도 `62 passed; 0 failed; 0 ignored`다. `cargo clippy --manifest-path desktop/src-tauri/Cargo.toml --all-targets --locked --offline -- -D warnings`와 루트 크레이트의 같은 명령이 모두 경고 0으로 `Finished`였다.
- 커밋 3개로 나눠 push했다: `7b8cb7b`(질의 경로), `c478907`(렌더 프레임·숨김 신호), `c304fe5`(문서). CI는 `c304fe5` run [35704737359](https://github.com/twoimo/openkakao-bot/actions/runs/35704737359) → 3개 job 모두 success이고, 직전 `66c104b` run 35701418189도 3/3 success다.
- 이번 턴의 검증 범위: 소스 변경, 로컬 테스트, 러너 CI까지다. 설치된 앱을 다시 띄워 숨김 상태의 렌더 호출 0회나 실제 카카오톡 전송을 재지는 않았고 그 두 가지는 여전히 미검증으로 남는다.
- 러너 기준 최종 확인: 수정·문서 커밋 `20f1014`의 CI run [35706369013](https://github.com/twoimo/openkakao-bot/actions/runs/35706369013)이 3개 job 모두 success다. 러너 로그의 수치는 Python focused **Ran 651 tests in 61.408s / OK (skipped=60)**, 데스크톱 `Test Files 9 passed (9)` / `Tests 121 passed (121)`, Rust 데스크톱 크레이트(`Test desktop Rust bridge offline`) **63 passed; 0 failed; 0 ignored**다. 즉 5절의 리뷰 수정 뒤 로컬 수치(121·63)는 러너에서도 같다.

### 5. 서브에이전트 리뷰 반영: pause-on-hide 계약 4건 수정

`multi_agent_v1__spawn_agent`로 `chatgpt-web/extra-high` 서브에이전트 1개(Carver, `01a0c838-9b4b-73c2-bc59-301a9c6d2701`)를 스폰해 HEAD `c304fe5`의 pause-on-hide 계약과 렌더 상태 수학을 독립 검토시켰다. 누적 24회 실패 뒤 25번째 시도에서 처음으로 결과가 돌아왔고, 결함 4건(P0 1·P1 2·P2 1)을 받았다. 네 건 모두 실제 도달 경로였으므로 수정하고 회귀 테스트로 고정했다.

**P0 — initial/native 상태 동기화 부재.** `jarvis://visibility`는 변화 알림일 뿐 현재 상태 스냅샷이 아니다. 두 창은 `visible: false`로 생성되므로, 숨은 webview가 `document.visibilityState === "visible"`을 보고 부팅하면 셸의 `hidden` 이벤트를 받은 적 없이 `transition("visible")`로 시작해 숨은 창 뒤에서 렌더가 돈다. 리스너 등록 전에 발생한 show/hide도 같은 방식으로 유실된다. 수정: 셸에 `window_is_visible` 커맨드를 추가하고(현재 창의 `is_visible()`), `wireRenderLifecycle`이 `readVisibility`를 받으면 부팅 상태를 **hidden으로 시작**한 뒤 리스너가 살아난 다음에 읽은 스냅샷으로 확정한다. 순서가 핵심이다. 리스너 등록 → 스냅샷 읽기 순서이므로 그 이전의 모든 셸 상태는 스냅샷이 덮고, 그 이후의 변화는 이벤트로 도착한다. 읽기가 실패하면 DOM 판단으로 되돌아가고(셸이 답하지 못하는 것이 패널을 얼리지는 않는다), 스냅샷이 발행된 뒤 이벤트가 먼저 도착하면 스냅샷을 버린다(이벤트가 더 새롭다). `readVisibility`가 없으면 기존처럼 DOM에서 부팅 상태를 정하므로 일반 브라우저와 기존 테스트 동작은 그대로다.

**P1 — hide/show 결과와 반대로 통보되는 경로.** `Focused(false)`와 `CloseRequested`는 `window.hide()`의 반환값을 버리고 무조건 `false`를 알렸다. hide가 실패하면 창은 보이는데 렌더러는 멈춰 그대로 얼 수 있다. 수정: 두 경로 모두 hide가 성공한 경우에만 알린다(`Focused(false) if ... && window.hide().is_ok()`). 반대 방향도 있었다. `open_settings`는 `set_focus`까지 성공해야 `true`를 알렸으므로 focus가 거부되면 보이는 설정 창이 멈춘 채 남았다. 수정: `show()` 성공 직후 알리고 focus는 그 뒤 best-effort로 처리한다(`settings_focus_failed` 반환 계약은 유지). 추가로 이미 보이는 창이 다시 focus를 받으면 `visible`을 다시 알린다: webview가 리스너를 등록하는 동안 도착한 tray 클릭의 알림이 유실되는 경우를 덮는다.

**P1 — teardown이 late subscription을 무효화하지 않음.** `detach()`는 `closed`를 세우지 않았으므로 아직 pending인 `listen()`이 나중에 resolve하면 `release`를 다시 설치했고, 그 리스너는 `transition`을 계속 호출할 수 있었다(패널은 teardown에서 `detach()` → `transition("closed")` 순서를 쓴다). 수정: `detach()`가 `detached`를 세우고 `transition`이 그 플래그를 존중하며, 늦게 도착한 구독은 즉시 해제한다. 같은 이유로 늦은 스냅샷도 버린다. 리뷰가 제안한 `RenderLifecycle`의 `"closed"` terminal latch는 채택하지 않았다: 이 저장소의 기존 계약(`desktop/src/__tests__/desktop.test.ts`의 `it.each(["hidden","closed","locked"])`)이 closed를 정지로 정의하고 있고, 위 플래그로 재시작 경로가 이미 막히므로 계약을 불필요하게 바꾸지 않았다.

**P2 — start 실패 후 `active` 잔류.** `transition("visible")`이 `active = true`를 `loop.start()`보다 먼저 기록했다. `start()`가 던지면 예외는 삼켜지지만 `active`는 남아 이후의 모든 visible 신호가 조기 반환하고, 다음 hide/show까지 아무것도 렌더되지 않는다. 수정: `loop.start()`가 성공한 뒤에만 `active`를 세우고, 실패하면 비활성으로 남겨 다음 신호가 재시도한다.

리뷰가 이상 없음으로 확인한 부분도 기록한다: `jarvis://visibility` payload는 `{"visible": boolean}` 하나뿐이고, 반복 show/hide에서 RAF 체인이 둘로 늘지 않으며, `CoreRenderState.step`의 0.05초 dt clamp·비유한 입력 처리·링 배열 리사이즈는 건전하다. 리뷰가 남긴 선택 항목 하나는 받지 않았다: 극단적으로 큰 유한 base/gain의 곱이 `Infinity`로 넘칠 수 있으나, 프로덕션 호출자 `JarvisCore`는 고정 3개 base/gain만 쓰므로 도달 경로가 없고, 프레임마다 검사를 더하면 이미 측정한 프레임 비용을 늘린다.

검출력은 되돌림 실험으로 확인했다. 수정한 4개 파일(`core/lifecycle-wiring.ts`·`core/lifecycle.ts`·`main.ts`·`src-tauri/src/main.rs`)을 `git checkout HEAD --`로 되돌리고 새 테스트만 남겨 돌리면 **8건이 실패**한다: 이벤트 이름 pin, boot handshake pin, open_settings 순서 pin, 부팅 시 hidden 유지, 셸이 visible이라고 답할 때 시작, 셸이 답하지 못할 때 DOM 유지, detach 후 late subscription 해제, start 실패 후 재시도. 복원 후에는 `npx vitest run` **121 tests / 9 files**가 통과한다(수정 전 113; 신규 8건 = wiring 7 + lifecycle 1).

이 수정 뒤의 수치는 데스크톱 `npx vitest run` **121 tests / 9 files**, Rust 데스크톱 크레이트 **63 passed**, `npx tsc -p tsconfig.json --noEmit` clean, `cargo clippy --manifest-path desktop/src-tauri/Cargo.toml --all-targets --locked --offline -- -D warnings` 경고 0, 편집한 `src-tauri/src/main.rs`의 `rustfmt --check` clean이다(같은 크레이트의 `python_bridge.rs`에는 이 호스트 rustfmt 버전이 요구하는 포맷 차이가 남아 있으나 이번 수정 대상이 아니며 건드리지 않았다. 루트 크레이트도 같은 이유로 `cargo fmt --check` 차이가 246건 있어 CI가 fmt를 게이트하지 않는다). Python focused 25개 모듈 651건은 이번 수정이 TS/Rust에만 닿았으므로 그대로다.
### 6. 숨김 정지·재개를 빌드된 번들에서 브라우저 하네스로 재측정

부모 에이전트가 로컬에서 수행했다. 3절의 셸 신호는 소스와 단위 테스트로만 고정돼 있었고, 실기기 정지는 이전 턴 기록(`jarvis-three-render-lifecycle`과 설치본 `ps` 표본)에 의존했다. 이번 턴에 `cd desktop && npm run build`로 `desktop/dist`를 만들고 loopback(`python3 -m http.server 8765 --bind 127.0.0.1`)에 띄운 뒤, 앱 내 브라우저 탭에서 Computer Use로 CDP `Runtime.evaluate`를 **main world**에서 실행해 제품 훅 `window.__jarvisRenderCount`를 직접 읽었다. 이 훅은 `scripts/jarvis_desktop_render_check.py`가 쓰는 것과 같은 제품 카운터다.

측정 함정 두 개를 먼저 기록한다. (1) `Runtime.evaluate`는 `{"result": {"type": "number", "value": N}}` 모양으로 답하므로 값을 `r.result.value`에서 읽어야 한다. (2) Playwright의 `evaluate`는 isolated world라 이 훅을 -1로 본다. 제품 카운터를 보려면 main world CDP가 필요하다.

#### 측정 결과

`wireRenderLifecycle`이 실제로 듣는 신호를 main world에서 합성해 발화하고 구간마다 카운터 증가율을 쟀다. 새로 로드한 한 페이지에서 한 번에 재었다.

| 구간 | 카운터 | 증가율 |
| --- | --- | --- |
| 기준(가시·유휴) | 222 → 241 (1.4초) | 19프레임 / 13.3 fps |
| `blur` 합성 후 1.6초 | 242 → 242 | **0프레임 / 0 fps** |
| `focus` 뒤 재개 #1 | 247 → 266 | 19프레임 / 13.4 fps |
| `focus` 뒤 재개 #2 | 266 → 285 | 19프레임 / 13.4 fps |
| `blur`→`focus` 5회 반복(숨김 구간 각 약 400 ms) | 숨김 구간별 증가 | 0, 0, 0, 0, 0 |
| 5회 반복 직후 | 305 → 325 | 20프레임 / 14.0 fps |
| `pagehide` 합성 후 1.2초 | 325 → 325 | **0프레임 / 0 fps** |

세 가지를 이 표에서 읽는다.

1. 숨김 구간에서 렌더 호출이 정확히 0회다. 그 구간에서 `document.visibilityState`는 `blur` 때도 `focus` 때도 `visible`이었다. 즉 이 정지는 Chromium의 탭 스로틀이 아니라 앱 자신의 신호 배선에서 나온다. 이 구분이 이 측정의 요점이다.
2. 5회 반복 뒤 증가율이 기준과 사실상 같다(14.0 fps vs 13.3 fps). `RenderLifecycle`의 `start`/`stop`이나 `detach`가 RAF 체인을 누적시켰다면 카운터가 프레임당 2회씩 올라 약 27 fps로 보였을 것이다. 체인이 중복되지 않는다는 증거다.
3. `pagehide`도 0 fps로 멈춘다. 종료 경로도 같은 정지 계약을 지난다. 다만 이 신호는 멱등 teardown이라, 이후 `focus`를 다시 보내도 루프가 되살아나지 않는다(이번 측정에서 페이지가 1949에서 멈춘 채 유지됐다). 이는 테스트로 고정한 계약과 일치한다.

#### 단순화 레이아웃의 재확인

같은 시점에 잡힌 이 탭의 AX 트리는 `container Jarvis → image Jarvis core` + `button 설정 열기(ID: gear)`가 전부였다. 제거 대상인 대량 검증·기능 점검·자기개선·권한 설정 표면이 빌드본에 남아 있지 않다는 것을 렌더된 픽셀이 아니라 접근성 트리에서 확인한 것이다.

#### 이 측정이 닫지 않는 것

- **AppKit order-out 경로 자체는 이 하네스로 재현되지 않는다.** 이 Chromium은 창이 가려지거나 IAB에서 숨겨져도 rAF를 멈추지 않는다. 직접 확인한 두 가지: 브라우저 수준 `capabilities.get("visibility").set(false)` 뒤에도 카운터가 21프레임 / 12.9 fps로 계속 올랐고 그때 `document.visibilityState`는 `visible`이었다(탭 수준에는 이 capability가 아예 없다: `Capability is not available: visibility`). 따라서 여기서 증명한 것은 계약의 DOM/이벤트 절반이다. 셸 절반(`show()`/`hide()`가 성공한 뒤에만 `jarvis://visibility`를 보낸다는 부분)은 Rust `#[cfg(test)]` 4건과 이전 턴 설치본의 `ps` 표본(패널 열림 2.9-3.1, 닫힘 6회 모두 0.0)이 담당한다.
- **설치본 재측정이 아니다.** 이번 하네스는 `desktop/dist`를 일반 브라우저에 띄운 것이고 설치된 앱 번들의 WKWebView에서 잰 값이 아니다. 설치본의 숨김 렌더 정지에 대한 실기기 근거는 여전히 이전 턴의 `ps` 표본 하나다.
- 하네스 정리: 합성용 scratch 탭은 닫았고 loopback 서버는 중지했으며(포트 8765 리스너 0 확인) 패널 탭은 정상 가시 상태로 되돌렸다.
### 7. 루프 wakeup 낭비·프레임당 힙 순증 실측과 레거시 메뉴바 표면 확인

부모 에이전트가 로컬에서 수행했다. 측정과 확인만 하고 제품 코드는 바꾸지 않았다. 6절과 같은 하네스(`desktop/dist` + loopback + 앱 내 브라우저 main world CDP)를 다시 띄워 재었다.

#### 1. 루프: rAF callback의 77%가 렌더 없이 끝난다

`AnimationLoop.tick`은 매 vsync마다 `scheduler.request`를 다시 걸고, 목표 간격(유휴 15fps·부하 시 30fps)이 지났을 때만 렌더한다. 그래서 rAF callback 수가 곧 브라우저 vsync wakeup 수가 된다. main world에서 `window.requestAnimationFrame`을 계수 래퍼로 교체하고(`browserScheduler`가 호출 시점에 전역을 해석하므로 패치가 즉시 적용된다) 3초간 세었다.

| 측정(가시·유휴, 3.00초) | 값 | 초당 |
| --- | --- | --- |
| rAF wakeup | 181 | 60.4/s |
| 실제 렌더 | 41 | 13.7/s |
| 렌더 없이 끝난 callback | 140 | **77.3%** |

이 하네스의 표시 장치는 60Hz다. 120Hz ProMotion 기기라면 같은 코드가 wakeup만 두 배로 받아 낭비 비율이 약 87%가 될 것으로 추정되지만 **추정이며 이 호스트에서 120Hz 조건으로 재지는 않았다.** 이 항목은 개선 대상으로 확정했고 구현은 위임 경로로 넘긴다. 이 절은 측정만 한다.

#### 2. 메모리: 렌더 중 프레임당 약 2.98 KB 순증, 정지 상태는 사실상 0

같은 페이지에서 `performance.memory.usedJSHeapSize`를 두 구간으로 나눠 쟀다. 이 값은 마지막 GC 이후의 순증이므로 garbage와 retention의 합계다.

| 구간 | 프레임 | 힙 변화 | 프레임당 |
| --- | --- | --- | --- |
| 렌더 중 8초 | 106 | +315,748 B | **+2,978.8 B/프레임** |
| `blur` 정지 8초 | 1 | +156 B | +156 B/8초 |

두 가지를 읽는다.

1. 정지 상태의 순증이 8초에 156바이트다. 2.5초 주기 폴러를 포함해 배경 경로가 사실상 아무것도 할당하지 않는다는 뜻이고, 6절의 정지 계약을 메모리 쪽에서도 독립 확인한 셈이다.
2. 렌더 중 순증은 프레임당 약 2.98 KB다. 이 값은 프레임 신호 계산 코드의 몫이 아니다. 그 코드는 `desktop/src/__tests__/render-state.test.ts`가 `Array.prototype.map`·`slice`·`concat` 0회 호출로 고정하고 있고, 매 프레임 실제로 도는 것은 `renderer.render(this.scene, this.camera)`(three.js 내부)다. `desktop/src/core/jarvis-core.ts`를 읽으면 지오메트리·머티리얼·조명은 전부 생성자에서 한 번만 만들고(20-103행) `dispose()`가 지오메트리·머티리얼·렌더러를 해제한다. 즉 이 순증은 three.js 렌더러 내부 할당으로 보인다.

한계를 명시한다. 이 하네스에서는 GC를 강제할 수 없다. `window.gc`는 `undefined`이고 `HeapProfiler.collectGarbage`는 이 CDP 브리지에서 `This method is not supported through raw CDP`로 거부된다. 따라서 위 2.98 KB/프레임이 곧 회수될 garbage인지 실제로 보유되는 retention인지 **구분하지 못했다.** 8초 창에서 순증이 한 번도 떨어지지 않은 것은 그 사이 scavenge가 없었다는 뜻일 뿐 누수 증거가 아니다. 다만 유휴 15fps에서 2.98 KB/프레임은 약 45 KB/s, 부하 30fps에서는 약 89 KB/s의 순증 속도이므로 패널을 오래 열어두는 사용 패턴에서는 GC churn이 실제 비용이 될 수 있다. 이 항목도 개선 후보로 남긴다.

#### 3. 레거시 Swift 메뉴바는 더 이상 설치되지 않는다(문자열 정리 보류의 근거)

계획은 기능 점검·검증 표면의 전면 제거를 요구한다. `macos/AutoReplyMenu/main.swift`에는 같은 부류의 한국어 문자열 7건이 남아 있다(점검 5건: 답변 프로그램·서비스·감시 서비스·기록·연결 상태, 검증 통과 2건). 이번에 그 표면이 실제로 살아 있는지 확인했다.

- `~/Library/LaunchAgents/`에서 레거시 `com.openkakao.auto-reply.menu.plist`는 `.bak-20260916T204935`로 옮겨져 있다(2026-09-16 20:49 KST).
- `launchctl list`에 `com.openkakao.auto-reply.menu` 항목이 없다. 있는 것은 `com.openkakao.jarvis.desktop`(Tauri, 상태 `- 0`), `application.com.openkakao.jarvis.desktop.286915917.286915922`(pid 84125), `com.openkakao.auto-reply.session-monitor`뿐이다.
- `macos/AutoReplyMenu/build/`는 git 추적 대상이 아니다(추적 파일 0건). 그 안의 `설정 검증 통과` 문자열은 빌드 산출물이다.
- 추적되는 `macos/` 파일은 2건뿐이다(`main.swift`·`Info.plist`).

따라서 그 7건은 사용자에게 도달하지 않는 죽은 표면의 문자열이다. 지금 고치면 사용자 가시 효과 없이 리뷰 비용만 늘어나므로, 의도적으로 보류하고 근거를 여기 남긴다. 레거시 앱을 되살리거나 다시 설치하는 변경이 생기면 그때 함께 정리해야 한다.

#### 4. 이번 절의 검증 범위와 CI

이 절이 기록하는 것은 측정과 확인 결과뿐이고 제품 코드는 바뀌지 않았다. 커밋 `fff3bd3`의 CI run [35707345093](https://github.com/twoimo/openkakao-bot/actions/runs/35707345093)이 3개 job 모두 success이고, 러너 수치는 `Ran 651 tests in 60.343s / OK (skipped=60)`, `Test Files 9 passed (9)` / `Tests 121 passed (121)`, Rust 데스크톱 크레이트 `63 passed; 0 failed; 0 ignored`다. 하네스 정리: 합성용 scratch 탭 close, loopback 서버 중지(포트 8765 리스너 0 확인).

## 패널 코어 전용 전환과 자동답변·GeekNews 중단 복구 — 2026-09-22 KST

### 1. 요청과 계약 변경

메뉴바 패널의 톱니바퀴 버튼을 없애고, 설정 창을 **메뉴바 트레이 아이콘 우클릭**으로 열도록 바꿨다. 이전에는 패널의 유일한 조작 요소가 `button#gear`였고 `open_settings`도 그 버튼에서만 호출됐다.

- `desktop/src/ui.ts`: `MAIN_PANEL_CONTROLS`가 빈 frozen tuple이 되고 `mainPanelMarkup()`은 `<main class="jarvis-panel">` + 코어 캔버스만 렌더한다.
- `desktop/src/main.ts`: 패널 DOM의 `open_settings` 경로와 그 부속 상태(`settingsPending`, `aria-busy`, `clearPanelUnavailable`)를 삭제했다. `RenderLifecycle`·스냅샷 폴러·`window.__jarvisRenderCount`는 그대로다.
- `desktop/src-tauri/src/main.rs`: `on_tray_icon_event`에 좌클릭 `Up → toggle_panel`을 유지하고 `MouseButton::Right` + `Up → open_settings` 분기를 추가했다. `open_settings` 본문의 announce-before-`set_focus` 순서는 그대로다(테스트가 고정).
- `desktop/src/tokens.ts`의 `gearSize`, `styles.css`의 `.gear` 규칙, `desktop/DESIGN.md`의 gear 서술도 함께 정리했다.

### 2. 렌더 교차 검증 재실행 (24/24 checks pass)

`desktop/dist`를 `npm run build`로 다시 만들고 `scripts/jarvis_desktop_render_check.py`를 실행했다. 이 환경의 고정 인터프리터들에는 `playwright`가 없어서(`browser unavailable: No module named 'playwright'`) 격리 venv `/private/tmp/okb-pw-venv`에 `playwright 1.63.0`만 설치해 돌렸고, 캐시된 Chromium 대신 시스템 Google Chrome(`chrome 153.0.8010.53`)을 썼다.

| 항목 | 값 |
| --- | --- |
| verdict | `status: pass`, checks 24/24 |
| 패널 interactive | `[]` (이전: gear 1개) |
| 패널 extra focusable | `[]` |
| 패널이 호출한 명령 | `fetch_runtime_snapshot`, `plugin:event|listen` — `open_settings` 없음 |
| 패널 평문 텍스트 | `''` |
| 트레이 라우팅 | 소스 검사: `left_up_toggles_panel true`, `right_up_opens_settings true`, `browser_exercised false` |
| 설정 `<h2>` 순서 | `대상 채팅방 / AI 모델 / Voice / 카카오 DB 동기화 · 색인 / DREAM-RSI / Knowledge / History` |
| WebGL | `WebGL 2.0 (OpenGL ES 3.0 Chromium)`, `gl.getError() == 0` |
| FPS | 유휴 13.48 / 부하 22.99, renderCount 16 → 107 |
| 드릴다운 | `최연우`, `2-hop · 4 nodes · 3 relations`, hop label `2-hop · 4/24 nodes`, retrieve `rrf · facts 2 · 알쫀쿠 = 알리바바 클라우드 축약` |

한계를 명시한다. macOS 메뉴바 트레이 항목은 브라우저 페이지가 클릭할 수 없다. 그래서 하네스는 `desktop/src-tauri/src/main.rs`를 별도로 읽어 좌클릭/우클릭 분기가 있는지만 확인하고 receipt에 `browser_exercised: false`와 `notes: ['macOS tray routing is source-inspected only; Chromium does not exercise TrayIconEvent']`를 기록한다. **실제 트레이 우클릭 동작은 이 하네스로 검증되지 않았다.** 재생성물: [jarvis-desktop-render-check.json](jarvis-desktop-render-check.json) (`generated_at 2026-09-22T09:41:43Z`), 캡처 `jarvis-render-*.png`. 패널 캡처를 직접 판독해 우측 상단 버튼이 사라지고 골드 코어만 남은 것을 확인했다.

로컬 검증 수치: `tests.test_jarvis_desktop_render_check` **26 tests OK**, 데스크톱 Vitest `Test Files 9 passed (9)` / `Tests 121 passed (121)`, `tsc -p tsconfig.json --noEmit` clean, `cargo test` **63 passed**.

### 3. 이 변경이 만든 CI 실패와 그 정정

`2f88b59`의 CI run [35709884829](https://github.com/twoimo/openkakao-bot/actions/runs/35709884829)은 `Tauri desktop and focused Python tests`에서 실패했다. `Ran 651 tests in 62.688s / FAILED (failures=1, skipped=60)`이고 유일한 실패는 정확히 옛 계약을 고정하던 `test_panel_source_keeps_the_single_gear`(`AssertionError: 0 != 1`)다. `macOS cargo test`와 `Launchd and Python harness`는 success였다. 위 하네스·테스트 정정이 그 실패를 닫는다.

### 4. 자동답변·GeekNews 중단의 원인 두 가지

사용자 보고는 "카카오톡 자동 답변과 긱뉴스 자동 전송이 안 된다"였다. 로그·상태·재현으로 원인 두 개를 분리했다.

**원인 A — 배치된 런타임의 스크립트 복사 목록이 불완전했다.** `scripts/prepare-auto-reply-session-runtime.py`의 `RUNTIME_SCRIPT_NAMES`는 11개였는데 진입 스크립트의 로컬 import 폐포는 17개다. 누락은 `auto_reply_knowledge_graph`, `auto_reply_ondevice`, `auto_reply_reference_search`, `auto_reply_reference_store`, `jarvis_abort`, `local_mlx_gateway` 6개다. 재현:

```text
/opt/homebrew/opt/python@3.13/bin/python3.13 -E -B "<runtime>/scripts/auto-reply-worker.py" --help
  File ".../auto-reply-worker.py", line 68, in <module>
    from auto_reply_ondevice import (
ModuleNotFoundError: No module named 'auto_reply_ondevice'
```

`~/Library/Application Support/openkakao/bujamentor/nimda-geeknews.log`의 마지막 성공 슬롯은 2026-09-20 19:50 KST(`status accepted_unconfirmed`, `confirmed log_id=3933957100273530883`)이고, 2026-09-21 08:40:01 KST 슬롯부터 같은 traceback으로 `exit=1`이 반복된다. launchd가 newest runtime을 고르는 `nimda-geeknews-slot.sh`의 선택(`ls -td runtime/*/ | head -1`)이 정확히 그 불완전한 런타임이었다.

**원인 B — catalog preflight가 방마다 1개씩 호출돼 항상 실패했다.** `scripts/auto-reply-service.py`의 `_filter_catalog_selectors`는 후보를 하나씩 `auto-reply --check`로 검사했지만, CLI는 선택한 방 집합이 `[auto_reply.room_reply_authors]` 키 집합과 정확히 일치할 것을 요구한다. 그래서 단일 방 호출이 결정론적으로 실패한다. 실측:

```text
--chat bind:417780809780519:부자멘토멘티  ->  "error":"AutoReply room_reply_authors contains unselected chat ID 325472527151234"
--chat (417..., 325...)                    ->  "error":"... unselected chat ID 437046948660911"
--chat (417..., 325..., 437...)            ->  "valid":true
```

결과적으로 모든 방이 drop되고 `_perform_preflight`가 `auto-reply preflight failed for every catalog room`으로 끝나며, `session-watchdog-status.json`이 `attempt 392`, `consecutive_failures 392`, `reason preflight_failed`, `state circuit_open`에 머물렀다. `~/.config/openkakao/config.toml`(mtime 2026-09-21 07:34)에 3방 `room_reply_authors` 맵이 들어간 뒤 `07:41` bake가 그 config를 복사하면서 시작된 회귀다.

한편 `preflight-skipped.json`(same day 18:13 KST)에는 `send bounded local MLX probe request … operation timed out`도 기록돼 있었다. `LOCAL_MLX_PROBE_TIMEOUT`은 15초다. 실측한 프로브 지연은 2.74s / 0.32s / 0.28s(`max_tokens 8`, 직접 curl)이고 `mlx-serve`(pid 38868)는 2026-09-18 21:15부터 상주 중이라, 그 타임아웃은 만성 원인이 아니라 일시적 이상치로 판단한다. **따라서 프로브 timeout 상수는 바꾸지 않았고, 이 항목은 잔여 위험으로 남긴다.**

### 5. 수정과 검증

커밋 `6413835`가 두 결함을 함께 고쳤다.

- 런타임: 누락 6개를 명시 tuple에 추가하고, packager가 stdlib `ast`로 진입 스크립트의 import 폐포를 계산해 복사 목록이 폐포를 덮지 못하면 `PackagingError`로 **staging 자체를 거부**한다. 즉 새 모듈이 생기면 배포된 런타임이 아니라 bake가 먼저 깨진다.
- service: catalog 후보 **전체를 한 번에** preflight하고, 통과하면 `skipped == []`로 전부 수용한다. 기존 방별 루프는 전체 호출이 실패했을 때만 도는 fallback으로 남겨 "나쁜 방 하나가 호스트 전체를 멈추지 않게" 하는 2026-09-12 보호를 유지했다.
- `.github/workflows/ci.yml`의 focused 목록에 `tests.test_auto_reply_session_packager`를 등록했다. 이 CI는 테스트 모듈을 명시적으로 열거하므로 등록하지 않으면 새 packager 테스트가 게이트가 되지 않는다.

검증(원문):

- `tests.test_auto_reply_session_packager tests.test_auto_reply_service_entry` → `Ran 69 tests in 26.846s / OK` (수정 에이전트 실행은 `Ran 69 tests in 30.827s / OK`).
- 사설 임시 state root(`/private/tmp/okb-bake-verify-2/state`, mode 700)로 bake한 새 런타임 `20260922T092900Z-14713`의 `scripts/`에 18개 asset(모듈 17 + `auto-reply-schema.json`)이 모두 있고, 세 진입 스크립트가 엄격 플래그에서 import된다:
  - `python3.13 -E -B -S <runtime>/scripts/auto-reply-worker.py --geeknews --help` → `usage: auto-reply-worker.py --geeknews …` (ModuleNotFoundError 없음)
  - `auto-reply-service.py --help`, `auto-reply-session-monitor.py --help` → 각각 정상 usage
- 수정된 `_filter_catalog_selectors`를 실제 binary·실제 config로 직접 호출: `catalog=[417…, 325…, 437…]`, `accepted=[417…, 325…, 437…]`(전체 1회 호출), `skipped=[]`. 같은 함수를 두 번 더 직접 호출한 `_preflight_cli`는 `ok=True`(10.6s / 4.7s).

### 6. 아직 배포되지 않았다 (남은 단계)

이 절의 수정은 **저장소와 새로 bake한 사설 런타임에만** 반영됐다. 실제 `~/Library/Application Support/openkakao/bujamentor`의 운영 런타임은 `20260920T224139Z-69184`이고, LaunchAgent `com.openkakao.auto-reply.session-monitor`는 그 runtime의 `session-monitor-manifest.json`을 가리킨다. 또한 `start-auto-reply-session.command`(pid 71513)와 `auto-reply-service.py --mode session`(pid 71515)가 2026-09-21 07:42부터 `session-watchdog.owner.lock`을 쥔 채 circuit_open으로 재시도 중이라, 새 런타임을 설치해도 그 owner lock이 풀리기 전에는 새 watchdog이 시작되지 않는다. 따라서 운영 복구는 다음 순서를 요구하며, 이 문서는 그것을 실행했다고 주장하지 않는다.

1. 실제 state root로 완전한 런타임 bake(운영 config의 `[auto_reply].chats`와 정확히 같은 순서의 `--chat` 3개).
2. LaunchAgent 교체: `launchctl bootout gui/$(id -u)/com.openkakao.auto-reply.session-monitor` → 기존 plist 백업 → 새 plist bootstrap.
3. owner lock을 쥔 pid 71513/71515 중단(진행 중 전송이 없음을 먼저 확인해야 한다. 상태는 `reason preflight_failed`로, 392회 연속 자식을 띄우지 못한 상태다).
4. GeekNews는 `nimda-geeknews-slot.sh`가 newest runtime을 고르므로 1번만으로 다음 슬롯(08:40/12:35/19:50 KST)부터 복구된다. 다만 이는 실제 방으로 나가는 전송이므로 별도 확인 후 진행한다.

## 자동답변·GeekNews 운영 복구 — 2026-09-22 KST

### 1. 증상과 사전 상태 (2026-09-22 18:45–18:47 KST)

- 사용자 보고: 카카오톡 자동 답변과 GeekNews 자동 전송이 동작하지 않음.
- `~/Library/Application Support/openkakao/bujamentor/session-watchdog-status.json`: `attempt 398`, `consecutive_failures 398`, `state circuit_open`, `reason preflight_failed`, `service_pid 71515`, `backoff_seconds 300.0`.
- `session-service/watchdog.err.log`가 `session watchdog: preflight_failed: auto-reply preflight failed for every catalog room`를 반복.
- LaunchAgent `com.openkakao.auto-reply.session-monitor`의 plist는 runtime `20260920T224139Z-69184`의 `session-monitor-manifest.json`을 가리켰고, 그 runtime의 `start-auto-reply-session.command`를 실행하는 pid 71513(`/bin/sh`)와 pid 71515(python3.13 `auto-reply-service.py --mode session`)가 PPID 1로 2026-09-21 07:42부터 고아 상태로 남아 `session-watchdog.owner.lock`(6 bytes, 값 `71515`)을 쥐고 있었다.
- `nimda-geeknews.log`: 마지막 성공 슬롯은 2026-09-20 19:50 KST(`status accepted_unconfirmed`, `confirmed log_id=3933957100273530883`). 2026-09-21 08:40:01 KST 슬롯부터 매 슬롯 `ModuleNotFoundError: No module named 'auto_reply_ondevice'`로 `exit=1`.
- `nimda-geeknews-slot.sh` 12행 `RT=$(ls -td "$STATE"/runtime/*/ 2>/dev/null | head -1)`가 고르는 newest runtime은 `20260920T225025Z-92735`였고 그 `scripts/`는 12개 파일뿐이라 누락 6개 모듈이 없었다.

### 2. 실행한 복구 (2026-09-22 18:47–18:59 KST)

1. `cargo build --release --bin openkakao-cli` (40.23 s). 디스크의 바이너리(Sep 21 11:21)보다 `src/`가 앞서 있었기 때문이다(커밋 `afe728e`의 lint 정리로 `src/context/mod.rs`, `src/reply_receipt.rs`만 변경됨).
2. `./target/release/openkakao-cli auto-reply-host --bake --json` → `runtime_root /Users/twoimo/Library/Application Support/openkakao/bujamentor/runtime/20260922T094747Z-78013`, `activated false`, `exit_code 0`.
3. 새 runtime 검증: `scripts/` 18개 asset(모듈 17 + `auto-reply-schema.json`), 누락 6개(`auto_reply_knowledge_graph`·`auto_reply_ondevice`·`auto_reply_reference_search`·`auto_reply_reference_store`·`jarvis_abort`·`local_mlx_gateway`) 모두 존재. `python3.13 -E -B -S`로 `auto-reply-service.py --help`, `auto-reply-session-monitor.py --help`가 usage를 출력했고 `auto-reply-worker.py --help`는 `{"ack": "skipped", "reason": "invalid_event"}`를 냈으며 어느 쪽도 ModuleNotFoundError가 없었다. runtime 자체 `openkakao-cli`는 `openkakao-cli 1.8.0`으로 실행됐다.
4. 읽기 전용 preflight 실측: 새 runtime의 `_filter_catalog_selectors`를 새 runtime의 `config.toml`과 `openkakao-cli`로 호출 → `accepted` 3/3(`bind:417780809780519:부자멘토멘티`, `bind:325472527151234:NIMDA 인수인계 임원방 ⚠`, `bind:437046948660911:Vision AI 경진대회`), `skipped []`, 4.3 s. 원시 CLI로 같은 세 `--chat`을 주면 `"valid":true`와 target 3건이 나왔다.
5. 라이브 plist를 `/private/tmp/okb-cutover-20260922/plist.before.plist`(SHA-256 `ee1e315e0c0b99294e1c345540c13fd6b789f8d367d6a5ce934849e5b1bdc078`)와 `~/Library/LaunchAgents/com.openkakao.auto-reply.session-monitor.plist.bak-20260922T185500`로 백업했다.
6. `kill -TERM 71515` → 기존 watchdog이 스스로 `state stopped`, `reason stop_requested`를 기록하고 종료했다(강제 종료 아님).
7. 경합 기록: 교체 직전 tick 한 번이 아직 로드돼 있던 옛 job 정의로 18:50:53에 옛 runtime(`20260920T224139Z-69184`)의 watchdog(pid 95849/95850)을 띄워 owner lock을 다시 잡았다. 교체 후 tick은 18:51:03에 `watchdog_running / owner_lock_held`를 `command_sha256 296fe656d10ecbcded1189e450170265fbf3e1427f79be1617aa27905db45177`(새 command)로 기록했다. 따라서 한 번 더 정지와 kickstart가 필요했다.
8. `launchctl bootout gui/501/com.openkakao.auto-reply.session-monitor` → 새 runtime의 plist를 `~/Library/LaunchAgents/`로 복사 → `launchctl bootstrap` → `launchctl kickstart -k`. 로드된 job의 `arguments`가 새 runtime의 `session-monitor-manifest.json`을 가리키는 것을 `launchctl print`로 확인했다.
9. `kill -TERM 95850` → `launchctl kickstart -k` → 18:53:06 tick이 새 runtime의 watchdog을 띄웠다.

### 3. 복구 후 상태

- `session-watchdog-status.json`: `attempt 2`, `state running`, `child_pid 38978`, `service_pid 11550`, `started_at` 18:53:30 KST.
- 프로세스 트리(단일 소유자): 11506 `/bin/sh <새 runtime>/start-auto-reply-session.command`(PPID 1) → 11550 python3.13 `auto-reply-service.py --mode session`(새 runtime) → 38978 세션 자식 → 39018 `~/Library/Application Support/openkakao/bin/openkakao-cli auto-reply` → 39019 `/usr/bin/caffeinate -i …`. `auto-reply-service.py --mode session` 소유자는 정확히 1개다.
- 방 `417780809780519`의 `db-watch-state.json`(18:59 KST): `capability_state ready`, `delivery_enabled true`, `fence ready`, `fence_reason ""`, `acked_watermark`가 2026-09-21 이후 3934011820824459265에 멈춰 있다가 3934560893236758528로 전진했고 `heartbeat_at`도 갱신됐다.
- `db-watch.log`가 실제 감시 동작을 보인다: `context_sync_transient:SqliteBusyTransient:database command failed:     Error code 5: The database file is locked`, `image download failed: DbFence: database command failed: Error: PNG IEND is invalid`, `skip_ack_unconfirmed:self_author:none`.
- `supervisor-status.json`: `ax_state healthy`, `reply_worker_state running`, `reply_model_state available`, `auto_reply_enabled true`, `auto_reply_reason local_model`, `delivery_state fenced_db_authoritative`. 18:59:25 KST 시점 `readiness`는 `fenced`, `fence_reason db_heartbeat_stale`이었다(방금 시작한 supervisor의 heartbeat 확인이 아직 따라오는 중).
- GeekNews: `ls -td "$STATE"/runtime/*/ | head -1`이 이제 `20260922T094747Z-78013`을 고르고, 그 runtime의 `auto-reply-worker.py --geeknews --help`가 usage를 출력한다(ModuleNotFoundError 없음).
- 같은 시각 `sh scripts/status-auto-reply-service.sh`는 `healthy=false`와 `problems=room_417780809780519:db_pending,room_417780809780519:db_candidate_active,room_417780809780519:db_stale,room_437046948660911:db_stale`를 냈다. 이는 18:56 KST의 `db_capability/db_delivery/db_fenced` 문제가 capability `ready`로 해소된 뒤 남은 방별 일시 상태이고, 417 방에는 후보 답변이 진행 중(`db_candidate_active`)이었다.

### 4. 한계와 잔여 위험

- 새 watchdog의 **attempt 1은 preflight에 실패했고 attempt 2에서 성공**했다. 즉 이 경로는 결정론적이지 않고 재시도로 자가 회복한다.
- 그 실패의 관측된 형태는 겹쳐 실행된 `auto-reply --check`가 방 하나를 target 집합에서 떨어뜨려 `AutoReply room_reply_authors contains unselected chat ID …`가 되는 것이다(두 실행이 겹칠 때 재현, 직렬 실행에서는 `"valid":true`). 재시도가 이를 닫는다는 사실만 확인했고 완전한 원인 규명이나 직렬화 패치는 이번 범위에 없다.
- **실제 카카오톡 전송은 아직 관측하지 않았다.** 다음 GeekNews 슬롯은 19:50 KST이고, 자동 답변은 실제 수신 메시지에 반응하므로 이 절은 "서비스가 fence 없이 상주하고 워커가 돌아간다"까지의 증거다.
- `auto-reply-host --status`의 `healthy`는 위 방별 problem 목록에서 계산되므로 시점에 따라 false가 될 수 있다.
- `db-watch.log`의 SQLite `Error code 5: The database file is locked`는 실제 카카오톡 DB 잠금이며 transient로 기록되고 있다.

### 5. CI

- 커밋 `3e198e8`의 CI run [35712115257](https://github.com/twoimo/openkakao-bot/actions/runs/35712115257)이 3개 job 모두 success다(2 m 34 s). 직전 `6413835`의 run 35710797187은 `Tauri desktop and focused Python tests`만 실패했고, 그 실패는 옛 계약 `test_panel_source_keeps_the_single_gear`(`AssertionError: 0 != 1`, `Ran 665 tests / FAILED (failures=1, skipped=60)`)이며 `3e198e8`이 닫았다.

## 자동답변 무응답 원인 분리와 GeekNews 슬롯 복구 — 2026-09-22 KST (2차)

### 1. 사용자 보고 3건과 이 호스트의 실제 상태

- 보고: (a) 메뉴바 패널 우상단의 "설정 확인 불가" 버튼 제거, (b) 메뉴바 우클릭으로 설정 진입, (c) 카카오톡 자동 답변과 GeekNews 자동 전송 복구.
- 사용자가 보고한 화면은 `http://127.0.0.1:8765/index.html`이고, 이 절을 작성한 시점에 8765에는 리스너가 없다(정지된 dev 프리뷰). 설치본 `/Applications/OpenKakao Jarvis.app`(실행 파일 2026-09-22 19:01 KST)의 Resources에는 `설정 확인 불가` 문자열이 없고, 제품 소스에는 `gear`/`⚙` 참조가 0건이다(남은 1건은 제거를 고정하는 계약 테스트 `desktop/src/__tests__/ui-removal-contract.test.ts`). 따라서 보고된 버튼은 설치본의 UI가 아니라 정지된 프리뷰의 잔상이다.
- 우클릭 설정 경로는 `desktop/src-tauri/src/main.rs:246-251`(`MouseButton::Right` + `MouseButtonState::Up` → `open_settings`)이며 커밋 `2f88b59`와 그 계약 테스트 `3e198e8`에 포함된다.

### 2. 커밋 481fc65: rc=0 미파싱 출력을 버리지 않고 유계 재시도

- 기전: 로컬 MLX가 rc=0으로 응답했지만 그 본문이 decision JSON이 아니면 `generate_reply`가 `reason="unparsed_model_decision"`, `category="uncertain"`, `model_invoked=True`만 기록하고 `model_failure_class`를 남기지 않았다. 그래서 `_model_defer_due_at()`이 `None`을 반환해 그 턴은 회복 불가로 끝났다.
- 실측(room `417780809780519`, 2026-09-22 KST): `reply-evidence.jsonl` 마지막 행이 event `db:417780809780519:3935464744318957569`("아아", author 현준), `recorded_at 2026-09-22T12:48:10Z`, `generation_seconds 42.5`, `prompt_sha256 30e79145fbc550d3`, `status skipped`, `reason unparsed_model_decision`이다. `reply-worker.log`는 `[reply-gen] unparsed_output head=b'{\\n  "result": "fail", ...'`를 남겼다.
- 같은 방에서 모델 타임아웃도 같은 경로로 소실됐다: `[reply-gen] prompt_bytes=20621 gen_elapsed=150.62s rc=1 ...` 뒤 `runner_failed rc=1 stderr='timed out'` → `delivery_unknown` → `reconcile_gave_up`.
- 수정(`scripts/auto-reply-worker.py`): `MODEL_DEFERRABLE_NON_CIRCUIT_FAILURE_CLASSES`를 신설하고(`call_in_flight`, `circuit_unavailable`, `runner_untrusted`, `unparsed_output`), 종전에 세 곳에 인라인으로 흩어져 있던 같은 집합을 이 상수로 통일했다. rc=0 salvage 반환에 `model_failure_class="unparsed_output"`과 `model_defer_until`을 추가했다. `unparsed_output`은 계정 단위 회로(`MODEL_CIRCUIT_FAILURE_CLASSES`)에 넣지 않았다.
- 함께 넣은 것: `_run_opencodex_generation`이 `local_mlx` 응답 본문의 `model`이 요청 target과 다르면 그 응답을 `mlx_serve_response_model_mismatch`로 거부한다(필드가 없으면 fail-open).
- 테스트: `tests/test_auto_reply_retry_policy.py` +2, `tests/test_auto_reply_worker_mlx.py` +3. 두 파일 실행 결과 `Ran 26 tests in 0.121s / OK`.
- CI: 커밋 `481fc65` run [35732454666](https://github.com/twoimo/openkakao-bot/actions/runs/35732454666) success.

### 3. GeekNews 슬롯의 런타임 선택 실패와 전송 경로

- 실패 슬롯(모두 `exit=1`): 2026-09-21 08:40·12:35·19:50, 2026-09-22 08:40·12:35가 `ModuleNotFoundError: No module named 'auto_reply_ondevice'`(runtime `20260920T225025Z-92735`의 `scripts/`는 12개뿐). 2026-09-22 19:50는 다른 오류로 `Error: could not find the message input field in the already-open chat "NIMDA 인수인계 임원방 ⚠"`.
- 원인: `nimda-geeknews-slot.sh` 12행의 `RT=$(ls -td "$STATE"/runtime/*/ | head -1)`가 **mtime** 기준 최신을 골랐다. 런타임 디렉터리 이름은 `YYYYMMDDTHHMMSSZ-<pid>`이므로 mtime 순서와 시간 순서가 다를 수 있고, 실제로 09-20의 불완전 런타임이 선택됐다.
- 수정(호스트 스크립트, 저장소 커밋 아님): 이름순 비교로 바꾸고 `auto-reply-worker.py`·`auto_reply_ondevice.py` 존재와 `--geeknews` 지원을 모두 만족하는 후보만 채택하며, 없으면 이름을 남기고 사전 점검에서 실패하며, 모든 로그 줄에 `runtime=<name>`을 남긴다. 원본은 `nimda-geeknews-slot.sh.bak-20260922T2210`로 백업했다(원본 sha256 `d37666f3807159c449efdeb2b041c3348e9b01a16ea44a59b47a77dee0625131`).
- 선택기 검증: 같은 조건의 셸 루프를 `/bin/sh`로 실행하면 `selector_would_pick=20260922T134657Z-13152`를 고르고, 그 런타임의 `scripts/auto-reply-worker.py` sha256이 저장소 현재 파일과 `4a9293aed6b76d7726881e3b34d636846d9fc5da3ebae5119241409813258028`로 일치한다. (같은 루프를 zsh로 실행하면 `[ "$n" \> "$RT_NAME" ]`가 "condition expected: >"로 깨져 항목을 고르지 못한다. 스크립트는 `#!/bin/sh`이므로 이 함정은 저장소 밖 스크립트에만 해당한다.)
- 전송 경로 정정: `--geeknews --send`는 답변 작업 큐를 거치지 않는다. `geeknews_operator_cli`(`scripts/auto-reply-worker.py:16971`)가 `<bin> local-send "<chat>" "<digest>" -y --json`을 직접 실행하고, 성공하면 `local-search "GeekNews TOP5"`로 자기 행(log_id)을 되읽어 확인한 뒤에만 `seen_ids`·`posted_slots`를 갱신한다. 즉 이 슬롯 경로는 방의 `delivery_enabled` fence를 통과하지 않으므로(별도의 설계 경계), room 325가 `context_sync_transient`로 fenced여도 08:40 슬롯은 나갈 수 있다.
- 읽기 전용 실측: 새 런타임에서 `--geeknews --preview`가 exit 0으로 `ids=[34120,34119,34118,34116,34115]` 다이제스트를 만들었고, `rooms/325472527151234/geeknews-rss-cursor.json`의 sha256 `82948c473f75c69209982a8cad839556be503ca88de5f66b1003812bd4ec5db4`가 실행 전후로 동일했다(preview는 상태를 쓰지 않는다).
- 실제 방 전송은 수행하지 않았다. 다음 자동 슬롯은 2026-09-23 08:40 KST이며 그것이 이 수정의 첫 실전 증거다.

### 4. 커밋 4a7085d: 컨텍스트 최신성을 알 수 없을 때 종결 대신 유계 재시도

- 기전: `conversation_advanced_past_event(event)`는 방 워터마크를 안정적으로 읽지 못하면 `None`을 반환한다. 분석 단계는 이 값을 회복 가능한 hold로 다룬다(`_reply_turn_hold_reason` → `context_freshness_unavailable`, `TURN_HOLD_REASONS`의 원소, `retrieval_bundle`의 `RetrievalError`). 반면 전송 직전의 두 지점은 같은 값에 `finish_delivery_unknown()`을 호출해 **종결**로 처리했다. 그 시점의 durable 행은 아직 `processing`이므로 AX 변경도 Return도 일어날 수 없는데 "전송 여부 불명"으로 기록한 것이다.
- 실측(room 417, event `db:417780809780519:3935451706400995228`): 21:32:32 `model_call` → 21:35:03 `model_result`(150.62s, rc=1 `timed out`) → 21:35:08 `processing → delivery_unknown`. 21:36:07 `model_result`(6.47s, rc=0) → 21:36:12 `delivery_unknown`. 21:36:18 evidence `context_freshness_unavailable`. 21:36:38 `model_result`(7.14s, rc=0)에 정상 초안 `비율이 미쳤네`가 나왔고 21:36:39 다시 `delivery_unknown`, 21:37:40 `delivery_unknown → skipped`로 최종 `stale_backlog`가 되며 초안이 폐기됐다. 방의 db-watch 상태는 이 구간에 `context_sync_transient` fence로 요동쳤다.
- 수정: `defer_scheduled_pre_send_unavailable()`에 허용 목록으로 검증하는 `error_class` 인자를 추가하고(기본값 `pre_send_unavailable`이라 기존 호출자 동작은 그대로), 두 `advanced is None` 지점을 이 유계 재시연으로 보냈다. 행은 응답 창 안에서 `scheduled` + `due_at`으로 다시 잡히고, 창+유예가 지나면 전송 없이 `stale_backlog`로 끝난다. 최신성이 불명한 동안 전송·작곡기 변경·Return은 여전히 전혀 일어나지 않는다.
- 테스트: `tests/test_auto_reply_retry_policy.py`에 두 경계의 `scheduled` 전이와 no-send, 유예 만료 `stale_backlog`, 잘못된 창의 종결 fallback, `advanced=True/False` 회귀를 추가했다. `tests.test_auto_reply_retry_policy tests.test_auto_reply_worker_mlx`는 `Ran 30 tests in 0.231s / OK`.
- CI: 커밋 `4a7085d` run [35735765880](https://github.com/twoimo/openkakao-bot/actions/runs/35735765880) success.

### 5. 공유 context DB 잠금 — 원인 규명, 수정 보류

- 잠긴 파일은 공유 컨텍스트 DB `~/Library/Application Support/openkakao/context.sqlite3`이다(1.394 GB, `-journal` 파일 존재 → rollback-journal/DELETE 모드, `-wal`/`-shm` 없음).
- 이 절 작성 시점에 이 DB는 **읽기 전용 열기조차** `sqlite3.OperationalError: database is locked`로 실패했다. 즉 writer 하나가 배타 잠금을 쥐고 있는 동안 모든 독자가 막힌다.
- `lsof`는 이 DB를 연 `auto-reply-worker.py --worker`(그 시점 pid 54933·54934)를 보였고, `sample` 스택은 두 프로세스 모두 `sqlite3LockAndPrepare → sqlite3Prepare → sqlite3ReadSchema → sqlite3Init → sqlite3PagerSharedLock → busy handler`에서 대기 중이었다.
- 재현: `openkakao-cli context-sync-local --chat-id 417780809780519 --chat 부자멘토멘티 --queue <room>/reply-queue.sqlite3 --json`은 exit 1과 stderr `Error: database is locked` / `Caused by: Error code 5: The database file is locked`를 냈다.
- `db-watch.log` 누적: room 417에서 `context_sync_transient` 218행(그중 `SqliteBusyTransient` 158, `context-sync-local` TimeoutExpired 42, `context_sync_writer_lock_timeout` 5). room 437 200/165, room 325 226/179.
- 수정하지 않은 이유: 안전한 근본 수정은 컨텍스트 DB의 잠금 처리(예: `src/context/mod.rs`의 writer 경로 busy 처리 또는 저널 모드 변경)에 있고, 이번 단위의 쓰기 범위는 Python 워커/감시자로 제한했다. `scripts/auto-reply-db-watch.py`의 fail-closed fence 정책은 **변경하지 않았다**. 컨텍스트 색인이 권위적이지 않다고 판단될 때 전송을 막는 규칙은 그대로다.
- 이 fence는 영구적이지 않다. 같은 날 22:55:40 KST에 room 417과 437이 `fence=ready`/`delivery_enabled=true`로 스스로 복구됐고, room 325(GeekNews 방)만 `context_sync_transient`로 남았다. 즉 이 결함의 실제 영향은 "전송 불가"가 아니라 "수십 초~수 분 단위로 전송이 막히는 요동"이며, 4절의 수정이 그 요동에서 초안을 잃지 않게 만든다.

### 6. 배포와 실측

- `cargo build --release --bin openkakao-cli`: 디스크 바이너리가 최신이라 0.70 s에 종료(no-op).
- `sh scripts/rebake-and-restart.sh`: 새 런타임 `20260922T134657Z-13152`(scripts 18개, `binary check: ok`)를 bake하고 plist를 교체한 뒤 `launchctl bootout → bootstrap → kickstart -k`를 수행했다. 스크립트 자체의 10×10 s 건강 확인 루프는 `healthy=false`로 끝났다(콜드 스타트 중).
- 교체 후 프로세스 소유권: `auto-reply-service.py --mode session`은 pid 13434 하나뿐이고 같은 런타임의 `--mode session-guardian`이 pid 13933이며, 방별 `auto-reply-db-watch.py`·`auto-reply-worker.py --worker`가 22:49:03에 새로 시작됐다. `--mode session` 문자열은 `--mode session-guardian`에도 부분 일치하므로 개수 세기로 중복 소유자를 판정하면 안 된다.
- 워커가 실제로 실행하는 코드: 세 워커의 `cwd`가 `/Users/twoimo/Documents/projects/openkakao-bot`이고 명령은 상대 경로 `scripts/auto-reply-worker.py --worker`이므로, 저장소 파일이 곧 라이브 코드다. 즉 이 커밋들은 슈퍼바이저/워커 재시작만으로 운영 경로에 반영된다.
- 교체 직후 세 방이 모두 `fence=starting`/`delivery=false`였다가 22:55:40 KST에 417·437이 `ready`로 올라왔다.

### 7. 검증하지 못한 것

- 실제 카카오톡 AX 전송은 이번 단위에서 수행하지 않았다(제품 규칙: 테스트는 fake 어댑터로). GeekNews의 첫 실전 전송은 2026-09-23 08:40 KST 슬롯이고, 자동 답변은 실제 수신 메시지에 반응해야 관측된다.
- 트레이 아이콘 우클릭은 사용 가능한 Computer Use 표면으로 실행할 수 없다(NSStatusItem을 클릭하는 도구가 없다). 근거는 소스(`main.rs:246-251`)와 설치본 바이너리 시각(2026-09-22 19:01 KST)에 한정된다.
- 사용자가 보고한 `http://127.0.0.1:8765/index.html` 탭은 죽은 프리뷰이므로 그 탭에서 "제거됨"을 확인하는 것은 이 단위의 증거로 쓰지 않았다.
- 전체 CI 파이썬 목록을 로컬에서 실행하면 `Ran 693 tests in 396.175s / FAILED (failures=2, skipped=9)`이고, 실패 2건은 모두 `tests.test_auto_reply_knowledge_graph.BackgroundReindexTests`(`wait_for_background_reindex`가 in-flight 재색인을 본 경우)다. 같은 모듈을 단독 실행하면 `Ran 82 tests in 35.046s / OK`이므로 모듈 간 간섭/부하에 따른 순서 의존 실패로 판단하며, 이번 변경과 공유 상태가 없다. HEAD 기준 재현 비교는 하지 않았으므로 "선재 실패"라고 단정하지 않는다.


## GeekNews 호스트 슬롯의 두 번째 실패 축과 방 워커 프로액티브 슬롯 — 2026-09-22 KST (3차)

### 1. 호스트 슬롯 로그 전량 분류

- `~/Library/Application Support/openkakao/bujamentor/nimda-geeknews.log` 227행을 전부 분류했다. 이 로그가 NIMDA 인수인계 임원방(room `325472527151234`) 슬롯의 유일한 자동 판정 기록이다.
- 2026-09-21 08:40 / 12:35 / 19:50, 2026-09-22 08:40 / 12:35는 `ModuleNotFoundError: No module named 'auto_reply_ondevice'`, `exit=1`이었다(2차 절의 런타임 선택 결함).
- 2026-09-22 19:50는 런타임 선택을 통과했지만 `Error: could not find the message input field in the already-open chat "NIMDA 인수인계 임원방 ⚠"`, `exit=1`이었다. 즉 호스트 슬롯에는 런타임 선택과 **AX 전송**이라는 서로 다른 두 실패 축이 있다.
- 그 AX 실패는 반복 계열이다: 2026-09-14 19:50 `Accessibility permission is not granted to this terminal app.`, 2026-09-15 12:35 같은 composer 오류, 2026-09-19 08:40·12:35 `chat window did not open in time`, 2026-09-19 19:50 `exit=3`, 2026-09-20 08:40 같은 composer 오류. 반대로 2026-09-15 08:40부터 2026-09-18 19:50까지 9개 슬롯은 `exit=0`이었다.
- 이 계열을 다루는 커밋은 2026-09-22 20:03 `97a39ad`(`fix(ax-send): retry the composer walk before reporting it missing`)와 20:10 `53d72f6`(`fix(ax-send): let an exact open-window read outlast AX contention`)이다. 19:50 슬롯은 두 커밋보다 먼저 실행됐다. 슬롯이 호출하는 `bin/openkakao-cli`는 22:46에 재빌드되어 두 수정을 포함한다.
- 2026-09-22 22:00:17의 `--preview` 실행은 `runtime=20260922T123949Z-50443`으로 `exit=0`이고 `ids=[34120, 34119, 34118, 34116, 34115]` 다이제스트를 만들었다. preview는 방 상태를 쓰지 않는다.

### 2. 방 워커의 프로액티브 슬롯은 별개 경로이며 417·437이 계속 비어 있다

- 각 방 큐(`rooms/<chat>/reply-queue.sqlite3`)의 프로액티브 행 전량(`created_at >= 1789900000` = 2026-09-20 19:46 KST):
  - room `417780809780519`(부자멘토멘티): 3건 — `similar_recent_self` 1, `stale_backlog` 2
  - room `437046948660911`(Vision AI 경진대회): 5건 — 전부 `stale_backlog`
  - room `325472527151234`(NIMDA): 2건 — 전부 `similar_recent_self`(호스트 슬롯이 같은 슬롯을 먼저 보내므로 정상 중복 제거)
- DB 실측: `openkakao-cli local-search "GeekNews TOP5"`가 돌려준 20행 중 가장 최근 발송은 room 325의 2026-09-20 19:50 KST다. room 437은 최근 400개 메시지에 `GeekNews` 문자열이 0건이고, room 417의 마지막 발송은 2026-09-20 12:43 KST다. 즉 09-21·09-22의 방 워커 슬롯은 하나도 발송되지 않았다.
- 이 경로는 호스트 스크립트와 무관하다. 프로액티브 이벤트의 분석은 모델을 호출하지 않는다(`scripts/auto-reply-worker.py:15304-15312`: `event["message"]`를 그대로 `reply`로 쓰고 `reason="geeknews_rss"`, `category="proactive"`). 다이제스트 텍스트는 네이티브이며 로컬 모델 가용성과 무관하다.
- room 437 저녁 슬롯 durable 행 실측: `sent_at=1790075434`(20:10:34), `response_window_upper_seconds=600.0`, `operator_forced` 없음, `created_at=1790075434.87`, `updated_at=1790076649.53`(20:30:49), 최종 `skipped` / `stale_backlog` / `error_class` NULL / `reply` 빈 값. 같은 슬롯의 다음 행은 20:30:50에 생성되어 21:09:24에 또 `stale_backlog`가 됐다. `_geeknews_slot_has_attempt()`는 `skipped/stale_backlog` 행을 창 점유로 세지 않으므로(15652-15671) 창이 닫힐 때까지 재생성된다.
- `error_class IS NULL`로 끝나는 `stale_backlog` 종결 지점은 두 갈래다. (a) `finish_scheduled_stale_backlog()`(14640) — 호출자는 14720(`PRE_SEND_RETRY_GRACE_SECONDS` 만료)와 14956(stale 분기). (b) delivery_unknown 회복 분류기 `leftover_pre_send_unknown_skip_fields()`(3025-3100)의 `_proactive_unix_leftover_job` 분기와 `BLANK_UNKNOWN_MAX_ATTEMPTS` 분기.
- 437 행의 숫자는 (a)의 만료 산술과 맞지 않는다: `sent_at + upper + grace = 20:10:34 + 600 + 120 = 20:40:34`인데 관측된 종결은 20:30:49다. 그래서 (b) 쪽으로 보이지만, 어느 분기가 실제로 발화했는지는 재현 계측 없이 확정하지 못했다. 이번 단위는 여기까지 규명하고 정책 변경은 보류했다.
- 두 경로가 공유하는 정책 자체는 코드 주석에 명시돼 있다: "Closed-slot formed digests must skip, not sit in delivery_unknown and occupy the next GeekNews window". 문제는 창과 지연의 산술이다. 슬롯 창은 `anchor`부터 30분인데(`_geeknews_slot_window()`), 프로액티브 방 조용 시간 `GEEKNEWS_ROOM_QUIET_SECONDS`가 10분이다. 슬롯 창 안에서 만들어진 잡이 10분 뒤에 발송을 시도하므로, 그 시도가 2분만 늦어도 창 끝을 넘겨 폐기된다. 실제로 room 437 저녁 잡은 20:10:34에 생성되어 20:30:49에 폐기됐다.

### 3. 이번 단위에서 하지 않은 것

- 프로액티브 슬롯 정책(창 길이, 조용 시간, 닫힌 슬롯 건너뛰기)은 바꾸지 않았다. 2절의 미확정 종결 경로를 재현으로 확정하기 전에 창을 넓히면 "오래된 다이제스트를 늦게 보내는" 동작이 생길 수 있다.
- 실제 방 전송은 수행하지 않았다(제품 규칙: 테스트는 fake 어댑터). 호스트 슬롯의 AX 축 수정은 2026-09-23 08:40 KST 슬롯이 첫 실전 검증이고, 자동 답변은 실제 수신 메시지가 있어야 관측된다.


## 사용자 보고 3건의 판정, local-send 게이트 드리프트, archify 재검증 — 2026-09-22 KST (4차)

### 1. 사용자가 인앱 브라우저에서 남긴 3건의 판정

- 톱니바퀴 설정 버튼 제거: **반영됨**. 저장소 `desktop/` 아래 `gear` / `⚙` 참조는 `desktop/src/__tests__/ui-removal-contract.test.ts:64` 의 부재 단언 하나뿐이고, 사용자가 본 문자열 `설정 확인 불가` 는 구현과 마크업에 없다. 설치된 `/Applications/OpenKakao Jarvis.app/Contents/MacOS/openkakao-jarvis-desktop` (2026-09-22 19:01 KST, `2f88b59` 18:21보다 최신)의 문자열에도 0건이고 `open_settings` 는 남아 있다.
- 메뉴바 우클릭으로 설정 열기: **구현됨, 실클릭 미검증**. `desktop/src-tauri/src/main.rs:246-251` 이 `TrayIconEvent::Click { button: MouseButton::Right, button_state: MouseButtonState::Up }` 에서 `open_settings(tray.app_handle().clone())` 를 호출한다. NSStatusItem 우클릭을 조작할 수 있는 Computer Use 표면이 없어 이번 단위에서 실행 검증하지 못했다.
- 사용자가 본 `http://127.0.0.1:8765/index.html` 탭은 **죽은 프리뷰**다. 8765에 LISTEN 중인 프로세스가 없다. 따라서 그 탭에서 제거를 확인하는 것은 증거로 쓰지 않았고, 판정은 저장소 소스와 설치 번들 문자열로만 했다.

### 2. 결함: 발송 직전 게이트와 파이프라인 게이트의 판정 불일치

- 같은 정책을 판정하는 두 함수가 다르게 동작했다.
  - `src/config.rs:448 validate_auto_reply_startup`: `safety.allowed_send_chats` (이름 완전 일치 또는 `id:<n>` · `bind:<n>:` 형태) **또는** `<auto_reply.state_root>/menubar-room-catalog.json` 에서 `chat_id` 나 `title` 이 대상과 같고 `auto_reply` 나 `geeknews` 가 `true` 인 방이면 허용한다.
  - `src/main.rs:6007 require_allowed_send_chat`: `safety.allowed_send_chats` 만 이름 완전 일치로 검사한다(호출자 `:7478` LocalSend, `:7585` LocalDelete). 카탈로그를 보지 않는다.
- 그 결과 room `437046948660911` (Vision AI 경진대회, `[bujamentor] chats` 와 카탈로그에 `auto_reply=true, geeknews=true`)는 파이프라인 검증과 워커 기동을 통과한 뒤 매 발송이 막혔다.
- 실측: `rooms/437046948660911/reply-worker.log` 4194행 중 `preflight_unavailable` 3639행, 그중 마지막은 3900행이며 본문은 `Error: chat "Vision AI 경진대회" is not in the local-send allowlist` 다. 사용자가 본 자동 답변·긱뉴스 미발송의 room 437 원인이 이 게이트다.
- room 417(부자멘토멘티)과 room 325(NIMDA 인수인계 임원방)는 이름이 `allowed_send_chats` 에 있어 이 결함의 대상이 아니다. room 417의 프리플라이트 오류는 원인이 다르다(`scheduled reply source row is unavailable`).

### 3. 수정과 테스트

- 카탈로그 판정을 `src/config.rs::allowed_send_chat_targets(config, chat_name, chat_id) -> bool` 하나로 추출하고 `validate_auto_reply_startup` 과 `require_allowed_send_chat` 이 같은 함수를 쓰게 했다. `require_allowed_send_chat` 은 chat id를 모르므로 `None` 을 넘겨 이름 경로만 쓴다.
- 보존한 성질: 이름 완전 일치(부분 일치·앞뒤 공백·대소문자 무시 없음), `auto_reply` · `geeknews` 가 JSON 불리언 `true` 인 방만, `title` 이 빈 문자열인 항목은 불일치, `state_root` 미설정·카탈로그 파일 없음·읽기 실패·JSON 오류·`rooms` 비배열·개별 항목 비객체는 전부 거부. 실패 문구와 `config.toml` 안내 문구는 그대로다.
- 테스트: `cargo test --lib` -> `626 passed; 0 failed`, `cargo test --bins` -> `224 passed; 0 failed`. 추가한 테스트는 `config::tests::allowed_send_chat_targets_matches_allowlist_and_catalog_policy` (allowlist 일치, 카탈로그 활성 제목 일치, 같은 id의 비활성 방 거부, JSON 오류 거부), `config::tests::allowed_send_chat_targets_fails_closed_without_a_catalog` (`state_root` 미설정, 파일 없음, 빈 `title` 이 빈 이름과 일치하지 않음), `tests::require_allowed_send_chat_accepts_a_catalog_enabled_room_title` (직접 호출 경로)이다.
- 하위 에이전트가 처음 넣은 단언 `!allowed_send_chat_targets(&config, "Vision AI 경진대회 extra", Some(437046948660911))` 은 잘못이었다. id가 같으면 제목이 달라도 허용되는 것이 기존 설계(`bind:<id>:<name>` 선택자)이며, 이 단언을 `None` (이름 경로)으로 바꾸고 id 경로 허용 단언을 추가해 바로잡았다. 이 오류는 `cargo test --bins` 에서 `1 failed` 로 드러났다.
- 사용자의 `~/.config/openkakao/config.toml` 은 수정하지 않았고 MLX Serve와 자동 답변 세션은 재시작하지 않았다. 따라서 이 수정은 아직 실행 중인 `bin/openkakao-cli` 에 반영되지 않았다.

### 4. archify 다이어그램 재검증

- `docs/architecture/openkakao-auto-reply-turn.archify.json` (viewBox `[1080, 580]`, `column_fit "spread"`, participants 6, messages 12, segments 3, views 3, cards 3)에서 `deliver sequence ... --quality showcase --json` 이 exit 0, `ok=true`, `errors` 0 · `warnings` 0, 산출물 813571 bytes sha256 `3596e3756a8545b926afd6d773ea5299fadd5aae1062a5623bb97c72ef585dff` 다. `archify check` 도 모든 항목 ok다.
- `visual-check` 는 1440×900 · 1600×1000 · 1920×1080 · 2048×1320에서 `ok=true`, `containment` · `readability` · `viewerChrome` 모두 `pass`, 진단 0건이다. `captures.screenshots` 4건(1440×900과 2048×1320의 light/dark)도 모두 `ok=true`, `scrollHeight == innerHeight`, `overflowY=false` 이며 PNG 사이드카가 저장소에 함께 있다.
- 실패했던 상태를 원인까지 확정했다. 뷰어의 `Archify.readerLayout` 은 `ratio = viewBox.width/viewBox.height`, `fixedHeight = chrome + header + guided-views + cards`, `availableSvgHeight = innerHeight - fixedHeight`, `desiredWidth = availableSvgHeight*ratio` 로 폭을 정하고 하한 `MIN_READER_WIDTH = 960` px로 클램프한다. 클램프가 걸리면 높이가 남아 `viewer/viewport-overflow` 가 된다. 변형 렌더 실측: cards 4 + views 3 -> `scrollHeight 1247`, cards 3 + views 3(원문) -> 1037, cards 0 + views 3 -> 900(pass, reader 1324), cards 4 + views 0 -> 1186, cards 3(짧은 문구) + views 3 -> 900(pass, reader 1034, 최소 투영 텍스트 6.51px). 카드 1장 추가는 그리드 2행을 만들어 약 210px를 더 쓰고 views는 약 61px이며 예산은 약 390px다.
- 통과한 형제 산출물 11종은 모두 `scrollHeight == innerHeight` 로 보고되므로 뷰어는 항상 화면에 맞춰 축소한다. 이번 산출물의 `viewBox` 높이 580은 형제들과 같은 계열(548~1085)이며 문제는 고정 크롬 높이였다.

### 5. 발송 실적 실측

- 큐는 읽기 전용 복사본으로 열었다(원본은 writer가 잠금을 쥘 수 있어 직접 열지 않았다).
- 최근 24시간 생성 행과 종결 사유: room 417 41건(`conversation_advanced` 31, `stale_backlog` 5 + error_class `stale_backlog` 1, `burst_superseded` 3, `reconcile_gave_up` 1), room 437 212건(`conversation_advanced` 187, `burst_superseded` 14, `stale_backlog` 6+1, `reconcile_gave_up` 4), room 325 1건(`similar_recent_self`). 세 방 모두 최근 24시간 `sent` 0건이다.
- room 417의 마지막 정상 발송은 2026-09-20 15:12:46 KST(`media_reaction`)이고 누적 `sent` 는 `social_reply` 55, `geeknews_rss` 16 등이다. room 437은 큐에 `sent` 행이 0건이며 `geeknews-rss-cursor.json` 자체가 없다. room 325는 `geeknews_rss` 1건(2026-09-16 08:50)만 있다.
- GeekNews 실측: `openkakao-cli local-search "GeekNews TOP5"` 20행의 최신은 room 325의 2026-09-20 19:50 KST, room 417은 2026-09-20 12:46 KST다. room 417의 `posted_slots` 는 `2026-09-20:lunch` 까지, room 325는 `2026-09-16:morning` 까지로 기록돼 있어 09-21·09-22 슬롯이 발송 없이 지나갔음이 커서와 일치한다.
- room 437의 `composer-allowlist.json` 에 남은 후보 8개 중 하나(`까먹을까봐 다 적어둠`)를 `local-search` 로 찾으면 0건이다. 이 파일은 발송 직전에 쓰이므로 시도는 있었지만 전달은 없었다는 근거다.
- room 417의 `scheduled reply source row is unavailable` 202건은 로컬 모델 전환 이전 구간에 몰려 있다(마지막이 11083행 중 10964행). 로컬 모델 구간의 마지막 호출은 `gen_elapsed=150.62s rc=1` 과 `runner_failed stderr=timed out` (150s 예산 초과), 그리고 `[reply-gen] unparsed_output` 이며 후자의 본문은 `result` 가 `fail` 인 자기 검열 문장이다. 같은 `prompt_sha256` 으로 재시도해도 같은 결과가 나온다.

### 6. 이번 단위에서 하지 않은 것

- 자동 답변 미발송의 지배적 원인인 `conversation_advanced` 정책(초안 취소 조건), 모델 생성 예산 150s, `reply_model` 선택은 바꾸지 않았다. 최근 24시간 종결 사유 1위가 room 437 187건·room 417 31건으로 이 정책이고, 로컬 추론이 42.5~150.62s 걸리는 부하에서 방의 간격보다 느리다는 것이 직접 원인이지만 취소 조건을 느슨하게 하면 오래된 맥락에 답하는 위험이 생기므로 별도 판단이 필요하다.
- 모델이 결정 JSON 대신 `result: fail` 문장을 돌려주는 문제를 프롬프트 수정으로 덮지 않았다. 재현 근거만 남겼다.
- 실행 중 바이너리 재배포(rebake)와 자동 답변 세션 재시작을 하지 않았다. 따라서 이 수정의 런타임 효과는 아직 미검증이다.
- 실제 카카오톡 전송을 수행하지 않았다(프로젝트 규칙: 테스트는 fake 어댑터). 호스트 GeekNews 슬롯의 AX 축 첫 실전 검증은 2026-09-23 08:40 KST 슬롯이다.
- 웹 위임 리뷰는 이번에도 AHP 점수를 산출하지 않았으므로 98점 달성을 주장하지 않는다. 이번 위임은 `chatgpt-web/high` 로 수행했고(요청에 있던 GPT-5.6 Luna 계열은 도구 카탈로그에 없고 `gpt-6-astra` · `gpt-5.6-sol` 은 사용량 한도로 차단) 결과는 코드 정확성 검토와 패치·테스트이며 순위 점수가 아니다.

