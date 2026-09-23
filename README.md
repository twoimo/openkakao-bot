<div align="center">

# openkakao-bot

**A private assistant for the KakaoTalk macOS app.**

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

`openkakao-bot` helps you find context and prepare replies in KakaoTalk. Conversation data and AI requests stay on your Mac; replies go through the KakaoTalk app already installed there.

<h2 id="architecture">Architecture</h2>

Jarvis is a macOS menu-bar assistant. Its compact display is drawn with Tauri v2 and Three.js. KakaoTalk messages, local search, and model requests stay on this Mac; sending a reply uses the installed KakaoTalk app.

### The animated core

1. Tauri reports when the small Jarvis panel is visible.
2. Three.js draws the warm gold core and updates its rings while the panel is open.
3. Reply, voice, and sync activity change the rings' rotation and the core's pulse.
4. Hiding or closing the panel pauses drawing and status updates; reopening it resumes them.

See the [render lifecycle](docs/architecture/jarvis-three-render-lifecycle.html) and [activity-to-motion map](docs/architecture/jarvis-core-load-mapping.html).

![Jarvis champagne-gold spherical core](docs/architecture/jarvis-core-panel.png)

### Finding related conversations

1. The app copies KakaoTalk's database and its side files to a temporary snapshot, then checks that the copy is stable.
2. It opens only the snapshot in read-only mode. A failed or unstable copy never falls back to the live database.
3. It normalizes shortened names and searches by both words and meaning. When both searches are available, reciprocal-rank fusion (RRF) combines their results; otherwise it keeps the working word-search results.
4. It stores people, topics, and their relationships as three-part facts (person — relationship — topic). Selecting a person or topic smoothly focuses nearby items, with at most 24 visible at once. This reads the existing index and does not start a new database copy or reindex.

See the [GraphRAG search sequence](docs/architecture/graphrag-search-sequence.html) and [conversation-map drill-down](docs/architecture/openkakao-graphrag.html).

### Voice conversation

“Hey Jarvis” starts local speech recognition, a local model reply, and speech synthesis. Jarvis answers once and asks a follow-up only when essential information is missing. The voice pipeline keeps at most four recent question-and-answer turns in memory, up to 600 characters per message. It clears that context after ten idle minutes and resumes listening after each spoken reply.

Before loading Whisper or Qwen3-TTS, a local-only admission check requires at least 8 GiB or 10 GiB of reclaimable RAM respectively and 2 GiB of free swap. If either probe is unavailable or the budget is low, the voice session reports the condition and does not load the model. A synthetic local voice run on 2026-09-24 reached the safety stop before a complete turn; end-to-end voice remains unverified on this host.

The settings UI marks voice status unavailable when its heartbeat is more than five minutes old, instead of presenting a stale `wake_listen` state as live.

### Other architecture diagrams

- [Tauri menu-bar architecture](docs/architecture/jarvis-openkakao-units1-4.html)
- [Offline DREAM-RSI review loop](docs/architecture/dream-rsi-provenance-loop.html)
- [Browser-use lifecycle](docs/architecture/jarvis-browser-use-lifecycle.html)

## Current runtime check

Read-only host checks on 2026-09-24 report a 128 GiB Mac with Flash-Next loaded and ready. The latest synthetic generation probe timed out at 60.009 s, so current generation is not verified. Qwen3.8 27B and Qwen3-TTS are unloaded; the 27B readiness probe returned `model_not_ready` while the MLX server owner is `model_owner_unmanaged`. Free swap was 1,178.44 MiB, below the 2,048 MiB voice admission threshold, so Whisper/TTS loading is blocked and a complete wake→STT→LLM→TTS turn remains unverified. The loaded model does not advertise embeddings; GraphRAG search is BM25-only and dense/RRF is unavailable. No model load or server takeover was performed in this readback. See [engineering status](docs/engineering-status.md) for measurements and limits.

The detailed implementation notes and dated verification records are kept in [engineering status](docs/engineering-status.md). They describe source checks, automated tests, and live runtime observations separately.

<h2 id="features">Features</h2>

- **Private processing**: Conversation search and AI replies run on this Mac.
- **Safe conversation reading**: The app reads a temporary, read-only copy of KakaoTalk's local data.
- **Conversation map**: Select a person or topic to see nearby names and related messages.
- **KakaoTalk integration**: Replies are entered in the KakaoTalk app.
- **Protected sending**: The app pauses when it cannot confirm which reply or destination is safe.

<h2 id="model-support">Model Support</h2>

The menu app offers a fast local model for everyday replies and a larger local model when requested. It does not automatically send conversation data to a cloud AI service. Exact model names and setup details are in the [Korean setup guide](README.ko.md); current readiness and generation limits are in [Current runtime check](#current-runtime-check).

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
