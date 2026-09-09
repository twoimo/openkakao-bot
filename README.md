<div align="center">

# openkakao-bot

**On-device, local-first KakaoTalk auto-reply agent for macOS.**

[![CI](https://github.com/twoimo/openkakao-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/twoimo/openkakao-bot/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/twoimo/openkakao-bot?color=blue&logo=github)](https://github.com/twoimo/openkakao-bot/releases/latest)
[![Platform](https://img.shields.io/badge/platform-macOS%20(Apple%20Silicon%20%7C%20Intel)-000000?logo=apple&logoColor=white)](https://github.com/twoimo/openkakao-bot)
[![Rust](https://img.shields.io/badge/built%20with-Rust-dea584?logo=rust&logoColor=white)](https://www.rust-lang.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[English](README.md) • [한국어 (README.ko.md)](README.ko.md)

</div>

---

`openkakao-bot` is an autonomous local agent designed for the macOS KakaoTalk desktop client. It monitors user-whitelisted chatrooms, retrieves contextual conversation history from on-device databases, drafts personalized responses matching your tone, and dispatches them via native macOS Accessibility APIs.

**Zero third-party cloud dependency. No reverse-engineered server protocol. 100% data sovereignty.**

```text
┌─────────────────────────┐
│   KakaoTalk (macOS)     │
│   (Local SQLCipher DB)  │
└────────────┬────────────┘
             │ Read-Only (Local Decryption)
             ▼
┌─────────────────────────┐      ┌─────────────────────────────┐
│   openkakao-cli (Rust)  │ ──►  │  Vector & FTS Context DB    │
│   Fast Ingestion Engine │      │  (context.sqlite3 on Mac)   │
└────────────┬────────────┘      └─────────────────────────────┘
             │
             ▼
┌─────────────────────────┐      ┌─────────────────────────────┐
│   Agent Runtime & LLM   │ ──►  │  Adaptive Style & Persona   │
│   Decision Pipeline     │      │  Circuit Breaker & Safety   │
└────────────┬────────────┘      └─────────────────────────────┘
             │
             ▼ Native AX Dispatch (No Server Emulation)
┌─────────────────────────┐
│   KakaoTalk Composer    │
└─────────────────────────┘
```

---

## Key Highlights

- **🔒 Privacy-First by Design**: Chat transcripts, contact books, memory indices, and credentials never leave your Mac. No network calls to unauthorized third-party backends.
- **⚡️ Native Performance**: Core ingestion and accessibility automation built with high-efficiency **Rust**, paired with a lightweight **Swift** menubar app and Python agent runner.
- **🧠 Local Memory & RAG**: Semantic vector embeddings and SQLite FTS5 full-text search index historical conversations locally to retrieve relevant context.
- **🎯 Style & Tone Matching**: Adaptive persona profiling calibrates tone, response latency, and terminology on a per-contact basis.
- **🛡️ Multi-Tiered Safety Architecture**:
  - Explicit chatroom whitelisting (`chats = ["bind:<id>:<name>"]`).
  - Model circuit breaker with automated backoff and failure isolation.
  - Idempotent queueing and deduplication (`reply-queue.sqlite3`).
- **🎛️ Dual Operation Modes**: Manage visually from the macOS Menubar app (`AutoReplyMenu.app`) or run headless via `launchd` supervisor.

---

## Architecture & Data Sovereignty

`openkakao-bot` operates purely on local app artifacts created by the official macOS KakaoTalk container:

| Local Artifact | Purpose | Storage Boundary |
|---|---|---|
| **Chat DB (`SQLCipher`)** | Encrypted message and room tables | Read directly from container via Mac UUID |
| **Vector DB (`context.sqlite3`)** | Local semantic chunks, embeddings, topics | Generated strictly on local disk |
| **Reply Queue (`reply-queue.sqlite3`)** | Outbound task states and transition logs | Local runtime state |
| **Config & Keys (`~/.config/openkakao/`)** | Local room filters, operator prompts, credentials | User-managed local storage |

---

## Quick Start

### 1. Installation

#### Pre-built Binary (Apple Silicon)
Download the latest executable from [Releases](https://github.com/twoimo/openkakao-bot/releases/latest):

```bash
curl -fsSL https://github.com/twoimo/openkakao-bot/releases/latest/download/openkakao-cli-darwin-arm64 -o /usr/local/bin/openkakao-cli
chmod +x /usr/local/bin/openkakao-cli
```

#### Build from Source
```bash
git clone https://github.com/twoimo/openkakao-bot.git
cd openkakao-bot
cargo build --release
./target/release/openkakao-cli --help
```

### 2. Permissions

In macOS **System Settings → Privacy & Security**:
1. **Full Disk Access**: Grant to your Terminal (or `AutoReplyMenu.app`) to read the local container database.
2. **Accessibility**: Grant to enable dispatching replies into the KakaoTalk window.

> *Note: KakaoTalk desktop client must be running.*

### 3. Diagnostics & Room Discovery

Verify local DB connectivity and list active chat rooms:

```bash
openkakao-cli doctor
openkakao-cli local-chats
```

### 4. Configuration

Initialize your configuration template:

```bash
mkdir -p ~/.config/openkakao
cp config.example.toml ~/.config/openkakao/config.toml
```

Edit `~/.config/openkakao/config.toml`:

```toml
[auto_reply]
# Whitelist specific chatrooms: ["bind:<chatId>:<exactOnScreenName>"]
chats = ["bind:123456789012345:TargetRoom"]

# Your exact display name in KakaoTalk (used to distinguish self-messages)
self_nickname = "Your Name"

# Python runtime interpreter
python_interpreter = "/opt/homebrew/bin/python3.13"

# Model provider and engine
# reply_runner_kind = "gjc"
# reply_model = "gpt-4o"
```

### 5. Running the Agent

#### Option A: Menubar Interface (Recommended for interactive use)
Build and launch the lightweight Swift menubar app:

```bash
sh scripts/build-auto-reply-menubar.sh
open macos/AutoReplyMenu/AutoReplyMenu.app
```

#### Option B: Headless Background Daemon (`launchd`)
Install supervisor daemon for 24/7 background execution:

```bash
sh scripts/install-auto-reply-launchd.sh
```

See [docs/auto-reply-launchd-supervision.md](docs/auto-reply-launchd-supervision.md) for advanced process supervision details.

---

## Repository Structure

```text
.
├── src/                      # Rust core: SQLCipher reader, AX automation, CLI
├── macos/AutoReplyMenu/      # Native Swift menubar application
├── scripts/                  # Python agent workers, ingestion, daemon installers
├── docs/                     # Technical specifications and operational blueprints
├── tests/                    # Integration and unit test suites
└── config.example.toml       # Baseline configuration template
```

---

## Development & Verification

```bash
# Run unit and integration tests
cargo test

# Run diagnostic verification suite
./target/release/openkakao-cli doctor --json
```

---

## Disclaimer & License

- **License**: [MIT License](LICENSE).
- **Disclaimer**: KakaoTalk is a registered trademark of Kakao Corp. This project is an independent, unofficial research and automation tool designed solely for personal workflow automation on authorized accounts and devices.
