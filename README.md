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

Extra is a gold hologram core plus a top-right gear. All operator controls open from that gear into one settings window. Extra has no bulk-verify, feature-checklist, or permission-settings UI.

![Jarvis champagne-gold spherical core panel](docs/architecture/jarvis-core-panel.png)

KakaoTalk indexing copies the live database into a temp snapshot, then opens that copy with `mode=ro` and `PRAGMA query_only`. If the copy cannot be created, the live database is not opened. GraphRAG drill-down reads the existing `knowledge-graph.sqlite3` only; a node click does not copy KakaoTalk or reindex. DREAM-RSI on the settings card is checkpoint provenance, not a live trainer.

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

- [Jarvis Extra render and operator pipeline](docs/architecture/openkakao-auto-reply.html)
- [GraphRAG drill-down sequence](docs/architecture/openkakao-graphrag.html)

The diagrams reflect `efee7c8`. They are not a live generation trace. On-device generation still fails closed when a probe times out.

## Jarvis local-AI implementation status

Unit 5 documentation now includes the measured live browser hide, isolated real-STT/TTS smoke, local Tauri debug bundle proof, and the read-only style.gallery retry; signed Tauri cutover remains open.

| Unit | Landed SHA | Status |
| --- | --- | --- |
| 1 | 278b3a6 | Landed |
| 2 | 64b577c | Landed |
| 3 | 845f209 | Landed |
| 4 | b53bfb2 | Landed |

Verification recorded for the landed implementation: **215 tests OK on UV Python 3.11**.

Current limits:

- Live Extra is still the Swift AutoReplyMenu, pid **20042**. A local debug Jarvis can run alongside it with `CFBundleIdentifier=com.openkakao.jarvis.desktop` (observed pid **85174**), but the Tauri app has **not** been cut over as the signed live app; Extra keeps the original `com.openkakao.auto-reply.menu` bundle id.
- The Qwen3.8 27B model is unloaded.
- DPO output does not promote or replace the live model automatically.
 - `Qwen3TtsAdapter` loads `Qwen3TTSModel.generate_custom_voice` with default id `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice`. A live bf16 smoke wrote a 3.2 s Korean WAV (153,644 bytes, speaker `aiden`) without speaker playback. Missing SoX remains a non-blocking warning.
- style.gallery now loads read-only. Its restrained UI font stack is applied as `--font-ui`; the existing ivory/warm-black + champagne-gold palette remains unchanged.
- Live Vite/browser hide is measured: visible `renderCount=9`/RAF `1`, then hidden `renderCount=9`/RAF `0` after 650 ms (delta `0`). The real Tauri/WKWebView hide is also measured: `renderCount=8→8`, pending RAF `1→0`, one RAF cancellation, delta `0` after 674 ms. Dedicated `.venv-voice` imports pass, and a real `MlxWhisperAdapter` smoke with `mlx-community/whisper-tiny-mlx` passes. The local Tauri debug binary/app bundle remains under `desktop/src-tauri/target`; signed cutover remains open because the installed Extra retains the original bundle id. See [unit5-live-proofs.md](docs/architecture/unit5-live-proofs.md).
 - Settings Knowledge now renders an E-R-E Three.js hologram (24-node cap, click → 2-hop, hidden RAF=0). See [jarvis-knowledge-hologram.png](docs/architecture/jarvis-knowledge-hologram.png).
- Load-driven Jarvis motion at `32dc4b6` is measured in [unit5-live-proofs.md](docs/architecture/unit5-live-proofs.md): ring targets are `0.170/-0.120/0.090` rad/s at idle and `0.408/-0.318/0.261` rad/s at busy load; nucleus radius is `0.270→0.324`. The 760 x 760 settings capture is [jarvis-settings-panel.png](docs/architecture/jarvis-settings-panel.png) (87,872 bytes).
- Remaining local proofs at `282933b` are measured in [unit5-live-proofs.md](docs/architecture/unit5-live-proofs.md): a real `BrowserUseRunner` owned Playwright context opened a controlled `file://` page, `jarvis_abort` returned `global_abort` in **371.770 ms**, and the owned context/browser closed. With the real `JarvisCore`, `jobLoad=0.5` and `voiceRms=0→0.8` changed acoustic-lattice center scale from **1.000→1.056** (RMS 0.8 range **1.036-1.076**).
- Background AX handling at `fadb271` is live-measured in [unit5-live-proofs.md](docs/architecture/unit5-live-proofs.md): a controlled local accessory-window button succeeded in the background with frontmost **Aside pid 95964→95964**, while the focus-required path returned `ax_focus_steal_required` without invoking its callback; Extra pid **20042** remained alive.
- Stock openWakeWord `hey_jarvis` still misses the Korean TTS “헤이 자비스” at **0.001959** versus `WAKE_THRESHOLD=0.65`. An opt-in **6,406-byte** custom ONNX calibration model now scores that same synthesized wake clip at **0.893993**, while the supplied unrelated Korean control is **0.194481** and silence **0.161939**; the threshold was not lowered. This single-positive proof does not establish human-speaker generalization, so the custom model is not enabled by default. See [unit5-live-proofs.md](docs/architecture/unit5-live-proofs.md).
- Voice settings now show the locked 0.65 Korean wake path. Bundled `hey_jarvis_ko_ridge.onnx` is selected when it validates. A file pipeline accepted custom wake at **0.768**, then Flash-Next replied `네, 분청 합성 정상 작동 중입니다.` after `max_tokens=128`; TTS wrote a 3.04 s wav without playback. Extra still owns the live menu. Live mic and signed cutover remain open.

Recovery and safety:

- SQLite acquisition is fail-closed: create an isolated consistent copy first; if that copy fails, do not open the live KakaoTalk database as a fallback.
- Emergency operator escape is **⌘⌥Esc**.
- A delivery_unknown result is never auto-retried.

Interactive Unit 5 diagrams:

- [Jarvis/openkakao architecture after Units 1–4](docs/architecture/jarvis-openkakao-units1-4.html)
- [Jarvis Three.js render lifecycle](docs/architecture/jarvis-three-render-lifecycle.html)
- [GraphRAG ranked search sequence](docs/architecture/graphrag-search-sequence.html)

<h2 id="features">Features</h2>

- **Local-first privacy**: Chat history, vector memory, knowledge-graph rows, and credentials stay on your Mac.
- **Isolated read-only indexing**: KakaoTalk is copied to a temp snapshot, then opened read-only. Copy failure does not fall back to the live DB.
- **GraphRAG over the existing store**: Node focus retrieves a k-hop bundle (`2/3/10`) and fills `관련 사실·관계`. Failure returns empty `facts`.
- **Native macOS dispatch**: Replies go through Accessibility local-send, not Kakao server protocols.
- **Safety controls**: Chatroom allowlisting, rate limits, and a circuit breaker.

<h2 id="model-support">Model Support</h2>

The Apple Silicon on-device recommendation is **MLX Qwen3.8 Flash-Next**. Gemma, llama.cpp, and Ollama are not the primary engine.

- Recommended: `mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit`
- Host-capable larger local model: `mlx/ddalcu/Qwen3.8-27B-MLX-Serve-4bit`
- DREAM-RSI: receipt / checkpoint provenance only (`settings-dream-rsi-card` after the sync card)
- Cloud runners remain optional and explicit in `config.toml`; they are not the on-device default

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
- **Full Disk Access**: Grant to your Terminal (or `AutoReplyMenu.app`) to read local database files.
- **Accessibility**: Grant to allow typing replies into KakaoTalk.

<h3 id="configuration">3. Configuration</h3>

```bash
mkdir -p ~/.config/openkakao
cp config.example.toml ~/.config/openkakao/config.toml
```

Minimal `~/.config/openkakao/config.toml`:

```toml
[auto_reply]
# Allowed chatrooms: ["bind:<chatId>:<exactOnScreenName>"]
chats = ["bind:123456789012345:TeamChannel"]

# Your display name in KakaoTalk
self_nickname = "Your Name"

# Recommended on-device engine (MLX), not Gemma / llama.cpp / Ollama:
# reply_runner_kind = "mlx"
# reply_model = "mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit"
```

### 4. Run

```bash
# Verify environment and discover chat rooms
./target/release/openkakao-cli doctor
./target/release/openkakao-cli local-chats

# Run with Menubar UI
sh scripts/build-auto-reply-menubar.sh
open macos/AutoReplyMenu/AutoReplyMenu.app
```

Privacy paths, KakaoTalk table names, and Korean operator notes live in [README.ko.md](README.ko.md). Do not commit chat databases, `context.sqlite3`, `knowledge-graph.sqlite3`, or credentials.

---

## License

[MIT License](LICENSE)
