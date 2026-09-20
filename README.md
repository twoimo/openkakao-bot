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

Unit 5 documentation reflects the source state after Units 1–4; it does not claim the still-open live proofs below.

| Unit | Landed SHA | Status |
| --- | --- | --- |
| 1 | 278b3a6 | Landed |
| 2 | 64b577c | Landed |
| 3 | 845f209 | Landed |
| 4 | b53bfb2 | Landed |

Verification recorded for the landed implementation: **215 tests OK on UV Python 3.11**.

Current limits:

- Live Extra is still the Swift AutoReplyMenu, pid **20042**. The Tauri app has **not** been cut over as the signed live app.
- The Qwen3.8 27B model is unloaded.
- DPO output does not promote or replace the live model automatically.
- style.gallery was not applied.
- Live hide-state render-call=0, wake/STT/TTS operation, and signed Tauri cutover remain open Unit 5 live proofs and are not claimed as measured here.

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

