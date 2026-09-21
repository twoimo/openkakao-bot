<div align="center">

# openkakao-bot

**Local-first KakaoTalk auto-reply agent for macOS.**

[![CI](https://github.com/twoimo/openkakao-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/twoimo/openkakao-bot/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/twoimo/openkakao-bot?color=blue&logo=github)](https://github.com/twoimo/openkakao-bot/releases/latest)
[![Platform](https://img.shields.io/badge/platform-macOS-000000?logo=apple&logoColor=white)](https://github.com/twoimo/openkakao-bot)
[![Rust](https://img.shields.io/badge/core-Rust-dea584?logo=rust&logoColor=white)](https://www.rust-lang.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

<p align="center">
  <a href="#features"><b>Features</b></a> &bull;
  <a href="#architecture"><b>Architecture</b></a> &bull;
  <a href="#model-support"><b>Model Support</b></a> &bull;
  <a href="#quick-start"><b>Quick Start</b></a> &bull;
  <a href="#configuration"><b>Configuration</b></a> &bull;
  <a href="README.ko.md"><b>한국어 문서 (Korean)</b></a>
</p>

</div>

---

`openkakao-bot` is an autonomous local agent for the macOS KakaoTalk desktop client. It observes designated chatrooms, retrieves conversation context from local databases, and drafts replies using your preferred LLM runtime without calling Kakao servers.

<h2 id="architecture">Architecture</h2>

The Tauri menu-bar panel is a gold hologram core plus a top-right gear. All operator controls open from that gear into one settings window. The panel has no bulk-verify, feature-checklist, or permission-settings UI.

![Jarvis champagne-gold spherical core panel](docs/architecture/jarvis-core-panel.png)

GraphRAG indexing and production `context-sync-local` both copy the live KakaoTalk database plus WAL/SHM sidecars into a consistent temp snapshot, then open only that copy read-only with `PRAGMA query_only`; copy instability or failure fails closed without opening the source database. GraphRAG drill-down reads the existing `knowledge-graph.sqlite3` only; a node click does not copy KakaoTalk or reindex. DREAM-RSI on the settings card is checkpoint provenance, not a live trainer.

```text
KakaoTalk macOS (local SQLCipher DB)
        |
        v isolated copy, then mode=ro + query_only
openkakao-cli
        |
        +-> context.sqlite3 (vectors / keyword)
        +-> knowledge-graph.sqlite3 (GraphRAG k-hop 2/3/10)
        |
        v
reply worker
        |
        +-> recommended on-device: MLX Qwen3.8 Flash-Next
        +-> host-capable on-device: MLX Qwen3.8 27B
        |
        v AX local-send
KakaoTalk composer
```

Interactive diagrams (authored node/card/label copy is Korean; Archify Viewer UI and `<html lang>` fall back to English):

- [Jarvis Tauri architecture after cutover](docs/architecture/jarvis-openkakao-units1-4.html)
- [Jarvis Three.js render lifecycle](docs/architecture/jarvis-three-render-lifecycle.html)
- [GraphRAG drill-down sequence](docs/architecture/openkakao-graphrag.html)

The diagrams are architectural references, not a live generation trace. On-device generation still fails closed when a probe times out.

## Jarvis local-AI implementation status

Unit 5 documentation includes the measured live browser hide, isolated real-STT/TTS smoke, local Tauri bundle proof, and the read-only style.gallery review. The primary Tauri app is now installed locally; signed/notarized distribution remains a separate release step.

The 2026-09-21 cutover installs `/Applications/OpenKakao Jarvis.app` and runs
`com.openkakao.jarvis.desktop` from the user's GUI LaunchAgent. The former
Swift Extra is stopped, its LaunchAgent plist is backed up under
`~/Library/Application Support/openkakao/install-backups/jarvis-desktop/`, and
is not loaded. The installed local bundle is ad hoc signed by the toolchain;
this proves local execution and resource staging, not notarization.

| Unit | Landed SHA | Status |
| --- | --- | --- |
| 1 | 278b3a6 | Landed |
| 2 | 64b577c | Landed |
| 3 | 845f209 | Landed |
| 4 | b53bfb2 | Landed |

The table records the historical Units 1–4 landing commits; it is not a current HEAD marker. Verification recorded at the Unit 4 landing point (`b53bfb2`): **215 tests OK on UV Python 3.11**.

### Jarvis local tool-runtime boundary

`JarvisToolRuntime` is a local, non-UI boundary for bounded browser and background AX jobs. Browser jobs use a fresh owned `DedicatedPlaywrightContext` and the fixed loopback MLX endpoint only; they do not attach to a live browser profile. AX jobs return a virtual cursor coordinate and status without moving the real pointer or activating/focusing a window, and requests that require either fail closed as `ax_focus_steal_required`. Every job observes the latched global abort and the runtime never resumes it automatically. Task, rectangle, and result sizes are bounded, failures expose fixed error codes, and status events contain only `jobId`, `kind`, `stage`, `load`, `time`, and `errorCode`.

The Tauri `run_browser_tool` command now reaches this runtime through the internal `tool-browser` Python action. The command accepts only bounded job, task, and cancellation-token strings; the task body is passed to the owned child only through bounded UTF-8 stdin and never appears in child argv. A global abort publishes the durable latch first, then gives an active browser child a hard-capped two-second grace period to observe it, return the fixed `global_abort` envelope, and close its owned Playwright resources. Direct `cancel_python` remains an immediate hard cancellation, and failure to publish the abort latch fails closed by hard-cancelling the child. Model-swap marker recovery and snapshot cancellation retain their separate semantics. The bridge returns only `ok`, `status`, `errorCode`, and a bounded result string, exposes no model or browser-profile override, and adds no UI. Current verification is centered on fake owned-browser/agent adapters and bridge contract tests; it does not prove an authenticated website flow, perform an AX action, or send a KakaoTalk message.

### Retrieval and model-residency contracts

- Reference-pack retrieval identifies the previous 128-dimensional hash vectors as `legacy_lexical_hash`, separate from local Dense embeddings. BM25 and Dense produce candidates independently and RRF fuses their rankings; the result contract carries room, participant, and time filters together with evidence IDs, index version, and watermark.
- A missing, stale, mismatched, or failing local embedding engine/index degrades explicitly to `bm25_only`. There is no cloud embedding fallback.
- `ModelResidencyManager` rolls back an owned prior model when memory admission, target load, or target probe fails after unload. It commits `current_model` only after the target probe succeeds; a failed rollback clears the current model and reports a `*_rollback_failed` fail-closed state. The swap gate also blocks new leases during a swap and fails closed on target-unload failure.
- Rust startup now fails closed for local AutoReply unless the configured model is an exact allowlisted Flash-Next or 27B ID and the local profile is attested. It applies the same bounded, read-only readiness and generation probes only against `127.0.0.1:11234/v1/models` and `127.0.0.1:11234/v1/chat/completions`; the worker's local MLX path does not fall back to `127.0.0.1:10100`.
- Earlier contract-focused evidence included a focused local MLX/readiness/worker Python regression of **25 tests**. It is historical context and is not part of the fresh validation counts below.
- Current working-tree validation passes the focused local-AI Python suite (**159 tests**), root `cargo test --lib` (**624 tests**), desktop Vitest (**35 tests**), Tauri Rust (**24 tests**), the launcher contract suite (**5 tests**), the Vite build, and a release Tauri bundle. The previous broader selected Python regression result (**221 tests**) is historical evidence and was not rerun for this update. The release bundle contains `scripts/auto_reply_reference_search.py`. The CI workflow includes the focused checks, but no hosted run of the current workflow change has been recorded. These checks do not establish live 27B readiness, Developer ID signed/notarized distribution, alphaXiv availability, or end-to-end live-microphone success.

Current limits:

- The current live menu process is the installed Tauri executable at `/Applications/OpenKakao Jarvis.app/Contents/MacOS/openkakao-jarvis-desktop` under `com.openkakao.jarvis.desktop`; no `AutoReplyMenu` process or legacy menu LaunchAgent remains loaded after cutover.
- In the 2026-09-21 local read-only probe, Flash-Next reported `loaded=true` / `state=ready` and passed bounded generation. Qwen3.8 27B reported `loaded=false` / `state=unloaded`; local verification returned `model_not_ready`, and no 27B generation check was run.
- The repository-only `scripts/verify_local_models.py --json` diagnostic probes the fixed Flash-Next and 27B IDs through localhost, performs a bounded generation check only when the gateway already reports `loaded=true` / `state=ready`, and never loads or swaps models. Its exit status is nonzero when either check is not proven; it is not bundled into the Tauri app.
- The current LaunchAgent points to `runtime/20260920T224139Z-69184`, and the previous remote-runtime process has been removed. The service is `healthy=false` / `backoff` because the target KakaoTalk window was not open and the AX read-only preflight failed. It did not switch focus or open the window automatically and remains fail-closed.
- Developer ID signing/notarization and Qwen3.8 27B actual generation remain open. Fixed-runtime microphone frame capture now passes, but human wake acceptance, menu-button-to-session end-to-end startup, and noisy/real Korean speech generalization remain unverified.
- The existing Jarvis 27B `model-prepare` path remains a read-only readiness contract for compatibility. The UI calls the separate `model-swap` action only after the user presses the 27B button; Rust then requires the fixed 27B ID, an explicit opt-in flag, and a lowercase RFC 4122 UUID token.
- A real swap proceeds only when the current resident model has verified product ownership, request drain is proven, and the conservative memory budget admits 27B. It reuses `ModelResidencyManager` for owned unload → load → probe and rollback. A foreign or uncertain resident is never unloaded. A live `127.0.0.1:11234` MLX gateway without the app's private ownership record fails closed as `model_owner_unmanaged`; settings report `외부 소유 · 27B 전환 차단`, and the operator must stop that external MLX Core before retrying. Other unverified ownership states remain `model_owner_unknown`.
- Model-swap cancellation writes a private cooperative marker so Python can stop at a mutation boundary and roll back. The ordinary snapshot cancellation command keeps its immediate owned-child cancellation behavior; 120 seconds is the final hard timeout if cooperative completion does not return.
- This change was tested only through fake gateways/process seams. No live Qwen3.8 27B load, unload, swap, or resident-process mutation was executed while implementing it.
- On 2026-09-21 the release bundle was built and installed with `OPENKAKAO_SIGN_IDENTITY=-`; `codesign --verify --deep --strict '/Applications/OpenKakao Jarvis.app'` passed. The release `.app` contained `scripts/local_mlx_model_readiness.py`, `scripts/auto_reply_reference_search.py`, the local CLI, and the Korean wake model. This is ad hoc signing and validates local bundling and execution only; Developer ID signing and notarization remain unverified.
- 최종 local bundle readback에서 source/bundle `jarvis_voice.py` SHA-256 일치, strict codesign, bundled CLI·Korean wake model, 설치된 LaunchAgent `running`(pid 5823), `AutoReplyMenu` 부재를 확인했다. 이는 ad hoc 설치 증거이며 Developer ID/notarization 증거는 아니다. 상세 값은 [unit5-live-proofs.md](docs/architecture/unit5-live-proofs.md)의 최종 signed local bundle 섹션에 기록했다.
- DPO output does not promote or replace the live model automatically.
- DREAM-RSI paper provenance is available through `scripts/dream_rsi_alphaxiv.py`, but the alphaXiv paper-analysis CLI remains missing from the persistent `PATH`, so paper analysis continues to fail closed. PyPI `alphaxiv==0.0.13` is instead a TTY-based W&B experiment synchronization CLI and does not provide the required `search`, `context`, and `paper summary` commands. The installed `orx` openresearch CLI is not used as an alphaXiv CLI substitute. See [dream-rsi-alphaxiv-provenance.md](docs/dream-rsi-alphaxiv-provenance.md).
 - `Qwen3TtsAdapter` loads `Qwen3TTSModel.generate_custom_voice` with default id `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice`. A live bf16 smoke wrote a 3.2 s Korean WAV (153,644 bytes, speaker `aiden`) without speaker playback. Missing SoX remains a non-blocking warning.
- 고정 Python 3.11 음성 런타임은 `hey_jarvis` 전용 stock 리소스, 로컬 whisper silence smoke, repo ID→HF cache snapshot 해석을 통한 Qwen3-TTS bf16 무네트워크 합성을 통과했다. 패키지 버전·용량 정리·마이크 수치·20개 단위 테스트 증적은 [unit5-live-proofs.md](docs/architecture/unit5-live-proofs.md)의 2026-09-21 고정 런타임 섹션에 기록했다.
- style.gallery now loads read-only. Its restrained UI font stack is applied as `--font-ui`; the existing ivory/warm-black + champagne-gold palette remains unchanged.
- Live Vite/browser hide is measured: visible `renderCount=9`/RAF `1`, then hidden `renderCount=9`/RAF `0` after 650 ms (delta `0`). The real Tauri/WKWebView hide is also measured: `renderCount=8→8`, pending RAF `1→0`, one RAF cancellation, delta `0` after 674 ms. Dedicated `.venv-voice` imports pass, and a real `MlxWhisperAdapter` smoke with `mlx-community/whisper-tiny-mlx` passes. The local Tauri debug binary/app bundle remains under `desktop/src-tauri/target`; that screenshot/probe predates the installed release cutover. See [unit5-live-proofs.md](docs/architecture/unit5-live-proofs.md).
 - Settings Knowledge now renders an E-R-E Three.js hologram (24-node cap, click → 2-hop, hidden RAF=0). See [jarvis-knowledge-hologram.png](docs/architecture/jarvis-knowledge-hologram.png).
- Load-driven Jarvis motion at `32dc4b6` is measured in [unit5-live-proofs.md](docs/architecture/unit5-live-proofs.md): ring targets are `0.170/-0.120/0.090` rad/s at idle and `0.408/-0.318/0.261` rad/s at busy load; nucleus radius is `0.270→0.324`. The 760 x 760 settings capture is [jarvis-settings-panel.png](docs/architecture/jarvis-settings-panel.png) (87,872 bytes).
- Remaining local proofs at `282933b` are measured in [unit5-live-proofs.md](docs/architecture/unit5-live-proofs.md): a real `BrowserUseRunner` owned Playwright context opened a controlled `file://` page, `jarvis_abort` returned `global_abort` in **371.770 ms**, and the owned context/browser closed. With the real `JarvisCore`, `jobLoad=0.5` and `voiceRms=0→0.8` changed acoustic-lattice center scale from **1.000→1.056** (RMS 0.8 range **1.036-1.076**).
- Background AX handling at `fadb271` is live-measured in [unit5-live-proofs.md](docs/architecture/unit5-live-proofs.md): a controlled local accessory-window button succeeded in the background with frontmost **Aside pid 95964→95964**, while the focus-required path returned `ax_focus_steal_required` without invoking its callback; Extra pid **20042** remained alive.
- Stock openWakeWord `hey_jarvis` still misses the Korean TTS “헤이 자비스” at **0.001959** versus `WAKE_THRESHOLD=0.65`. An opt-in **6,406-byte** custom ONNX calibration model now scores that same synthesized wake clip at **0.893993**, while the supplied unrelated Korean control is **0.194481** and silence **0.161939**; the threshold was not lowered. This single-positive proof does not establish human-speaker generalization, so the custom model is not enabled by default. See [unit5-live-proofs.md](docs/architecture/unit5-live-proofs.md).
- Voice settings now show the locked 0.65 Korean wake path. Bundled `hey_jarvis_ko_ridge.onnx` is selected when it validates. A file pipeline accepted custom wake at **0.768**, then Flash-Next replied `네, 분청 합성 정상 작동 중입니다.` after `max_tokens=128`; TTS wrote a 3.04 s wav without playback. This is a historical pre-cutover smoke; full live microphone and human wake verification remain open.
- At commit `38f77cb`, the parallel Tauri debug bundle was rebuilt and restarted as pid **62924**. The updated **760 x 760** Voice settings capture is [jarvis-settings-panel.png](docs/architecture/jarvis-settings-panel.png) (**76,125 bytes**); Extra pid **20042** remained alive and process readback showed only Flash-Next on `mlx-serve`, with no Qwen3.8 27B or Gemma process. The real Tauri hide RAF probe was not repeated because its temporary telemetry hook had been removed.
- Snapshot Voice now selects bundled Korean ONNX from the file on disk when no session status exists. Production `whisper-large-v3-turbo` on the same TTS clip transcribed `안녕하세요 분성 합성 테스트입니다.` in 25.4 s (tiny had 분청). Extra pid **20042** unchanged.
- At `96153df`, a debug Tauri build was observed as pid **63463** (`com.openkakao.jarvis.desktop`) without replacing Extra.
- The rebuilt debug Jarvis SIGHUP check observed `open` pid **84521** survive `kill -HUP` with Extra **20042** unchanged. That historical process check does not supersede the current LaunchAgent/runtime state above.
- Settings Voice has an opt-in **마이크 세션 시작** control that spawns the isolated `.venv-voice` `jarvis_voice.py` session. It does not auto-start. Extra was not restarted.
- Debug Jarvis was rebuilt at `6f76876` as pid **40102**. The settings capture now includes **마이크 세션 시작**. Extra **20042** unchanged.
- Unavailability no longer paints Voice as stock-only; the default card shows bundled ONNX plus the mic start control.
- Opt-in voice sessions now set `OPENKAKAO_VOICE_TTS_OUT` to `state_root/jarvis-voice-out.wav`. `speak()` writes that WAV and does not call `sd.play`. Extra **20042** unchanged. Live mic was not opened.

Recovery and safety:

- SQLite acquisition is fail-closed: create an isolated consistent copy first; if that copy fails, do not open the live KakaoTalk database as a fallback.
- Emergency operator escape is **⌘⌥Esc**.
- A delivery_unknown result is never auto-retried.

Interactive Unit 5 diagrams:

- [Jarvis/openkakao architecture after Units 1–4](docs/architecture/jarvis-openkakao-units1-4.html)
- [Jarvis Three.js render lifecycle](docs/architecture/jarvis-three-render-lifecycle.html)
- [GraphRAG ranked search sequence](docs/architecture/graphrag-search-sequence.html)
- [Jarvis 27B model-swap lifecycle](docs/architecture/jarvis-model-swap-lifecycle.html) — validate 9/9 showcase, 0 errors/0 warnings; specification SHA `c94a0a02e004a9c1149e13db80cf53dce8e50406eac6dfacd8593c7c4830a25`; artifact SHA `d5472fd8b4aa72239056bdf4f532b104c6ac6b01667be379984a8d4252a3d995`; visual-check pass, no overflow at 1440x900/1600x1000/1920x1080/2048x1320, light/dark capture.

<h2 id="features">Features</h2>

- **Local-first privacy**: Chat history, vector memory, knowledge-graph rows, and credentials stay on your Mac.
- **Isolated read-only indexing**: KakaoTalk is copied to a temp snapshot, then opened read-only. Copy failure does not fall back to the live DB.
- **GraphRAG over the existing store**: Node focus retrieves a k-hop bundle (`2/3/10`) and fills `관련 사실·관계`. Failure returns empty `facts`.
- **Native macOS dispatch**: Replies go through Accessibility local-send, not Kakao server protocols.
- **Safety controls**: Chatroom allowlisting, rate limits, and a circuit breaker.

<h2 id="model-support">Model Support</h2>

The production default on Apple Silicon is **MLX Qwen3.8 Flash-Next**.

- Recommended: `mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit`
- Local image-reply and explicit replacement target (allowlisted and readiness-probed; actual generation unverified): `mlx/ddalcu/Qwen3.8-27B-MLX-Serve-4bit`
- DREAM-RSI: receipt / checkpoint provenance only (`settings-dream-rsi-card` after the sync card)

The production AutoReply worker and menubar permit only local MLX for text and image generation. Flash-Next is the default; Qwen3.8 27B is the image path and explicit operator-requested replacement target. Gemini and other cloud models are rejected as primary or fallback models and are never selected automatically.

Explicit network features such as fetching a posted URL are separate source-collection operations. Enabling them does not authorize cloud LLM execution or model/image egress.

A recommended model ID is not a completed live-generation run. Probes that time out fail closed and leave `last_probe` instead of sending.

<h2 id="quick-start">Quick Start</h2>

### 1. Build from Source

```bash
git clone https://github.com/twoimo/openkakao-bot.git
cd openkakao-bot
cargo build --release
```

### 2. Permissions

In macOS **System Settings -> Privacy & Security**:
- **Full Disk Access**: Grant to your Terminal (or `OpenKakao Jarvis.app`) to read local database files.
- **Accessibility**: Grant to allow typing replies into KakaoTalk.

<h3 id="configuration">3. Configuration</h3>

```bash
mkdir -p ~/.config/openkakao
cp config.example.toml ~/.config/openkakao/config.toml
```

Minimal `~/.config/openkakao/config.toml`:

```toml
[model]
privacy_mode = "local"
allow_egress = false
provider = "mlx-serve"

[auto_reply]
# Allowed chatrooms: ["bind:<chatId>:<exactOnScreenName>"]
chats = ["bind:123456789012345:TeamChannel"]

# Your display name in KakaoTalk
self_nickname = "Your Name"

# Recommended on-device engine (MLX), not Gemma / llama.cpp / Ollama
reply_runner = "/absolute/path/to/installed/opencodex"
reply_runner_kind = "opencodex"
reply_model = "ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit"
```

`reply_runner` is a validated transport placeholder for this local MLX profile and must point to the installed `opencodex` executable. The exact Flash-Next and 27B IDs, with or without the `mlx/` prefix, are allowlisted and use the same bounded localhost readiness and generation probes. The recorded 27B model was unloaded and not ready, so actual 27B generation remains unverified.

### 4. Run

```bash
# Verify environment and discover chat rooms
./target/release/openkakao-cli doctor
./target/release/openkakao-cli local-chats

# Build and install the primary Tauri menu-bar UI
sh scripts/build-jarvis-desktop.sh
sh scripts/install-jarvis-desktop.sh

# Start the installed LaunchAgent without rebuilding or reinstalling
sh scripts/start-auto-reply-menubar.command
```

Privacy paths, KakaoTalk table names, and Korean operator notes live in [README.ko.md](README.ko.md). Do not commit chat databases, `context.sqlite3`, `knowledge-graph.sqlite3`, or credentials.

---

## License

[MIT License](LICENSE)
