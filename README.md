<div align="center">

# openkakao-bot

**Local-first KakaoTalk auto-reply agent for macOS.**

[![CI](https://github.com/twoimo/openkakao-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/twoimo/openkakao-bot/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/twoimo/openkakao-bot?color=blue&logo=github)](https://github.com/twoimo/openkakao-bot/releases/latest)
[![Platform](https://img.shields.io/badge/platform-macOS-000000?logo=apple&logoColor=white)](https://github.com/twoimo/openkakao-bot)
[![Rust](https://img.shields.io/badge/core-Rust-dea584?logo=rust&logoColor=white)](https://www.rust-lang.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

<p align="center">
  <a href="#features"><b>Features</b></a> •
  <a href="#architecture"><b>Architecture</b></a> •
  <a href="#model-support"><b>Model Support</b></a> •
  <a href="#quick-start"><b>Quick Start</b></a> •
  <a href="#configuration"><b>Configuration</b></a> •
  <a href="README.ko.md"><b>한국어 문서 (Korean)</b></a>
</p>

</div>

---

`openkakao-bot` is an autonomous local agent for the macOS KakaoTalk desktop client. It observes designated chatrooms, retrieves conversation context from local databases, and drafts replies using your preferred LLM runtime without calling Kakao servers.

<h2 id="architecture">Architecture</h2>

```text
KakaoTalk macOS (Local SQLCipher DB)
        │
        ▼ (Read-only Local Ingestion)
openkakao-cli (Rust Engine)
        │
        ├─► Local Vector & Semantic Memory (context.sqlite3)
        │
        ▼
Agent Decision Pipeline
        │
        ├─► Open Weights (Google Gemma 4, Alibaba Qwen 3.8, DeepSeek V4, Kimi K3)
        ├─► Frontier APIs (Anthropic Claude 3.7, OpenAI GPT-6 / 5, Google Gemini 3)
        │
        ▼ (Native macOS Accessibility Dispatch)
KakaoTalk Composer
```

---

<h2 id="features">Features</h2>

- **100% On-Device Privacy**: Chat history, local vector memories, and credentials never leave your Mac.
- **Fast Local Ingestion**: High-performance Rust core decrypts and reads local SQLite databases directly.
- **Local Memory & RAG**: On-device vector embeddings and full-text search retrieve relevant context per contact.
- **Native macOS Dispatch**: Sends replies using macOS Accessibility APIs without reverse-engineering server protocols.
- **Safety Controls**: Strict chatroom allowlisting, rate limits, and an automated circuit breaker.

---

<h2 id="model-support">Model Support</h2>

`openkakao-bot` supports the latest local open-weight models and frontier cloud APIs:

### 1. Local & Open Weights (Apple Silicon Optimized)
Optimized for low-latency, private on-device inference via MLX, Ollama, and local servers:
- **Google Gemma**: Gemma 4 (31B, 12B Unified, E4B, E2B), Gemma 3
- **Alibaba Qwen**: Qwen 3.8 (Qwen3.8-27B, Qwen3.8-Max), Qwen 3, Qwen 2.5-Coder
- **DeepSeek**: DeepSeek V4-Flash, V4, V3, R1
- **Moonshot & Zhipu**: Kimi K3, K2.7 Code, GLM-5.2

### 2. Frontier Model Providers
Direct integration with cloud model providers:
- **Anthropic**: Claude 3.7 Sonnet, Claude 3.5 Sonnet / Haiku
- **OpenAI**: GPT-6 Astra, GPT-5, GPT-4.5, o3 / o1
- **Google**: Gemini 3 Pro / Flash, Gemini 2.5

---

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

# Runtime and Model Selection
# Local Open Weights:
# reply_runner_kind = "ollama" # or "mlx"
# reply_model = "gemma4:12b" # or "qwen3.8:27b", "deepseek-v4-flash"

# Cloud Frontier APIs:
# reply_runner_kind = "anthropic" # or "openai", "gemini"
# reply_model = "claude-3-7-sonnet" # or "gpt-6-astra", "gemini-3-flash"
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

---

## License

[MIT License](LICENSE)
