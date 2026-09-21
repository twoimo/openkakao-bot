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

### Implementation status

- The desktop app is a Tauri v2 menu-bar application with TypeScript and Three.js (`desktop/package.json`, `desktop/src-tauri/Cargo.toml`, `desktop/src/`). `RenderLifecycle`, `AnimationLoop`, `RuntimeSnapshotPoller`, and `JarvisCore` implement the visible/hidden render path and load/voice signal handling (`desktop/src/core/lifecycle.ts`, `desktop/src/core/animation-loop.ts`, `desktop/src/runtime-poller.ts`, `desktop/src/core/jarvis-core.ts`). Diagram source: [Jarvis render lifecycle](docs/architecture/jarvis-three-render-lifecycle.archify.json).
- Local MLX hardware detection, model residency/ownership checks, leases, and swap rollback live in `scripts/auto_reply_ondevice.py` through `ModelResidencyManager` and the swap gate.
- Bounded tool runtime, owned Playwright browser use, voice control, and the emergency abort latch are implemented in `scripts/jarvis_tool_runtime.py`, `scripts/jarvis_browser_use.py`, `scripts/jarvis_voice.py`, and `scripts/jarvis_abort.py`.
- Graph and retrieval code is implemented in `scripts/auto_reply_knowledge_graph.py` and `scripts/auto_reply_reference_search.py`: query normalization feeds BM25/FTS5 and dense ANN candidate paths, RRF fuses successful dual rankings, graph expansion uses `k_hop_neighborhood`, and result metadata includes evidence IDs and index/watermark fields. Diagram source: [GraphRAG retrieval sequence](docs/architecture/graphrag-search-sequence.archify.json).
- Dataset, DPO/fine-tune, and offline DREAM-RSI support is present in `scripts/auto_reply_golden_dataset.py`, `scripts/auto_reply_finetune.py`, `scripts/auto_reply_dream_rsi.py`, and `scripts/dream_rsi_alphaxiv.py`. The DREAM-RSI provenance path explicitly does not promote or replace a live model automatically.
- style.gallery was reviewed read-only and only its restrained UI font stack is applied, as `--font-ui` in `desktop/src/styles.css`. The ivory / warm-black palette with champagne gold reserved for the core and selection is unchanged, and no neon, bloom, or glow treatment is used.

### Verified results

- Render-lifecycle diagram: `validate lifecycle --quality showcase` reports 9/9 artifact checks with 0 errors / 0 warnings, `deliver` succeeds, and `visual-check` passes with no diagnostics at 1440x900, 1600x1000, 1920x1080, and 2048x1320 in light and dark themes (`scrollHeight <= innerHeight` at every viewport). Receipt: [jarvis-three-render-lifecycle.visual-check.json](docs/architecture/jarvis-three-render-lifecycle.visual-check.json).
- GraphRAG retrieval diagram: the same 9/9 showcase validation, successful delivery, and four-viewport visual-check pass with zero diagnostics. Receipt: [graphrag-search-sequence.visual-check.json](docs/architecture/graphrag-search-sequence.visual-check.json).
- `docs/architecture/unit5-live-proofs.md` records browser and Tauri/WKWebView hide probes where the outstanding RAF was cancelled and render-count delta remained zero while hidden.
- The same proof log records controlled browser, abort, and isolated voice/STT checks. Those records are bounded component evidence; they do not establish an end-to-end KakaoTalk reply flow.
- The same proof log holds the volatile cutover values (installed bundle paths, LaunchAgent state, individual probe timings, and per-run counts) that this summary deliberately does not duplicate.

### Known limitations

- Current repository evidence does not establish a live KakaoTalk send path for this Jarvis work, and `docs/architecture/unit5-live-proofs.md` explicitly records proof runs with no KakaoTalk or live AX send.
- Dense retrieval can become unavailable because of index/version/watermark or embedding failures; `scripts/auto_reply_knowledge_graph.py` and `scripts/auto_reply_reference_search.py` keep this explicit as a BM25-only degradation instead of a cloud fallback.
- DREAM-RSI and DPO outputs are offline artifacts and do not replace the resident model automatically (`scripts/auto_reply_dream_rsi.py`, `scripts/dream_rsi_alphaxiv.py`).
- The repository files cited above do not by themselves prove live model generation or release signing/notarization. Local bundles are ad hoc signed (`OPENKAKAO_SIGN_IDENTITY=-`); Developer ID signing and notarization remain unverified.
- Qwen3.8 27B generation is unverified. A real 27B swap needs an app-owned resident model: a foreign MLX server holding `127.0.0.1:11234` fails closed as `model_owner_unmanaged` (`scripts/auto_reply_ondevice.py`, `scripts/verify_local_models.py`), and settings report `외부 소유 · 27B 전환 차단` until the operator stops that external server.
- Stock openWakeWord `hey_jarvis` misses the Korean "헤이 자비스" utterance. A bundled opt-in calibration model scores it above `WAKE_THRESHOLD=0.65` without lowering the threshold, but it is not enabled by default and human-speaker generalization is unverified (`scripts/jarvis_voice.py`, `scripts/train_jarvis_korean_wake.py`).
- The alphaXiv paper-analysis CLI is still absent from the persistent `PATH`, so DREAM-RSI paper analysis fails closed; the installed `orx` openresearch CLI is not used as a substitute ([dream-rsi-alphaxiv-provenance.md](docs/dream-rsi-alphaxiv-provenance.md)).

### Recovery

- Emergency operator escape is **⌘⌥Esc**. It cancels input, TTS, and queued browser/AX work through the latch in `scripts/jarvis_abort.py`, is registered in `desktop/src-tauri/src/main.rs`, and never auto-resumes.
- A `delivery_unknown` result is never auto-retried; the operator confirms the real delivery state first (`scripts/auto-reply-tui.py`).
- On a model swap failure, `ModelResidencyManager` in `scripts/auto_reply_ondevice.py` attempts rollback to the owned prior model and fails closed if rollback cannot be proven.
- If dense GraphRAG lookup fails, `scripts/auto_reply_knowledge_graph.py` retains the BM25 candidate path and reports the degraded search mode while preserving evidence/index metadata.
- After a hidden window becomes visible again, `RenderLifecycle` restarts the render loop and runtime polling path (`desktop/src/core/lifecycle.ts`, `desktop/src/main.ts`); the transition is represented in the [Jarvis render lifecycle source](docs/architecture/jarvis-three-render-lifecycle.archify.json).

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
