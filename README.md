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

See the [end-to-end local system map](docs/architecture/jarvis-system.html) and its [Archify source](docs/architecture/jarvis-system.architecture.json). The map shows the desktop shell, local tools, model boundary, and read-only conversation index in one view.

### The animated core

1. Tauri reports when the small Jarvis panel is visible.
2. Three.js draws the warm gold core and updates its rings while the panel is open. The center sphere uses a fixed-light `ShaderMaterial`; a paired local WebGL run measured 3.4% lower median CPU submission at 15 fps and 2.5% lower at 30 fps (about 3–5 μs per frame), with 8 draw calls in both variants. Pixel output matched in 11 of 12 theme, activity, and scale cases; the remaining case differed by one 8-bit channel level. These are CPU submission measurements, not GPU power or battery measurements.
3. Reply, voice, and sync activity change the rings' rotation and the core's pulse.
4. Hiding or closing the panel pauses drawing and status updates; reopening it resumes them.

See the [render lifecycle](docs/architecture/jarvis-three-render-lifecycle.html) and [activity-to-motion map](docs/architecture/jarvis-core-load-mapping.html).

Source-rendered capture (latest dark-theme source render; not a live installed-app capture):

![Jarvis champagne-gold spherical core](docs/architecture/jarvis-render-panel.dark.png)

### Finding related conversations

1. The app copies KakaoTalk's database and its side files to a temporary snapshot, then checks that the copy is stable.
2. It opens only the snapshot in read-only mode. A failed or unstable copy never falls back to the live database.
3. It normalizes shortened names and searches by both words and meaning. When both searches are available, reciprocal-rank fusion (RRF) combines their results; otherwise it keeps the working word-search results.
4. It stores people, topics, and their relationships as three-part facts (person — relationship — topic). Selecting a person or topic smoothly focuses nearby items, with at most 24 visible at once. This reads the existing index and does not start a new database copy or reindex.

The graph follows the human-readable, source-backed design in the [Threads reference](https://www.threads.com/share/BBIeDkkHei/) and its linked [OSK System](https://github.com/lpaiu-cs/osk-system): preserve each message in the local conversation index, keep derived knowledge as canonical entities and unique subject–relation–object triples, and retain bounded message evidence IDs for traceability. Room, person, and topic nodes provide navigable neighborhoods. Drill-down starts at two relationship steps, limits each step to ten neighbors, and caps the view at 24 nodes (up to three steps when expanded). BM25 remains available when local embeddings are unavailable; RRF is used when both ranked candidate lists are ready.

See the [GraphRAG search sequence](docs/architecture/graphrag-search-sequence.html) and [conversation-map drill-down](docs/architecture/openkakao-graphrag.html).

### KakaoTalk reply turns

Each incoming KakaoTalk row remains a durable queue item. Consecutive rows from the same room and numeric author, no more than 15 seconds apart, are assembled into one reply turn, capped at six rows and 8 KiB. The 15-second settle interval lets the newest fragment arrive before inference; a successor supersedes an earlier job only when its saved burst IDs include that earlier row. The assembled prompt keeps each included message in order. Author changes, older attachments, and size/count limits end the burst. Legacy v1 queue records retain their original 2-second interpretation.

Empty Kakao emoticon rows (message types 12, 20, and 22) enter the same durable queue as `[이모티콘]` instead of being acknowledged as empty input.

Punctuation-only follow-ups such as `???` are treated as pointers to the current thread. Every reply is checked for generic confusion text, including “무슨 말인지 모르겠네요”. When recent messages identify a topic, the worker replaces that dead end with a topic-specific clarification; when the referent is clear, the model can answer from the thread. Factual questions whose answer is absent keep the explicit unknown-answer path.

### Voice conversation

“Hey Jarvis” starts local speech recognition, a local model reply, and speech synthesis. Jarvis answers once and asks a follow-up only when essential information is missing. The voice pipeline keeps at most four recent question-and-answer turns in memory, up to 600 characters per message. It clears that context after ten idle minutes and resumes listening after each spoken reply.

Before loading Whisper or Qwen3-TTS, a local-only admission check requires at least 8 GiB or 10 GiB of reclaimable RAM respectively and 2 GiB of free swap. If either probe is unavailable or the budget is low, the voice session reports the condition and does not load the model. A synthetic local voice run on 2026-09-24 reached the safety stop before a complete turn; end-to-end voice remains unverified on this host.

Local MLX replies reject an explicit response model ID that differs from the requested model, after removing an optional `mlx/` transport prefix. Responses without a model ID remain accepted for gateway compatibility; generation readiness is still tracked separately.

The settings UI marks voice status unavailable when its heartbeat is more than five minutes old, instead of presenting a stale `wake_listen` state as live.

### Other architecture diagrams

- [Tauri menu-bar architecture](docs/architecture/jarvis-openkakao-units1-4.html)
- [Local MLX request drain and model swap](docs/architecture/jarvis-model-request-drain.html)
- [Offline DREAM-RSI review loop](docs/architecture/dream-rsi-provenance-loop.html)
- [Browser-use lifecycle](docs/architecture/jarvis-browser-use-lifecycle.html)

## Current runtime check

At 21:17 KST on 2026-09-24, read-only checks returned HTTP 200 from local MLX `/health`. The same-day `/v1/models` readback showed Flash-Next loaded, Qwen3.8 27B and Qwen3-TTS unloaded, and no embedding capability; live GraphRAG therefore remains BM25-only and dense/RRF is unavailable. The latest synthetic Flash-Next generation probe disconnected after 45.060 s with zero output tokens and was not repeated, so generation remains unverified. A post-probe memory read showed 1,040.38 MiB free swap, 1,007.62 MiB below the 2,048 MiB voice admission threshold; Whisper/TTS loading and a complete wake→STT→LLM→TTS turn remain unverified.

At that same 21:17 KST snapshot, `auto-reply-host --status --json` returned `healthy=true`: all three configured room supervisors were running and ready, each reported an available reply model, delivery was enabled, and no pending gaps were reported. The watchdog still showed eight restarts and five consecutive failures, while the LaunchAgent session-monitor job itself was not running. This status is not an end-to-end reply or latency measurement. The checks sent no messages, started no workers, loaded no models, and did not restart or take over a server. The current source changes are not installed in the immutable runtime. See [engineering status](docs/engineering-status.md) for measurements and limits.

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
