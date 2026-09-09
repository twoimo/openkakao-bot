# openkakao-bot

Local-first KakaoTalk auto-reply agent for macOS.

It watches **rooms you allow**, drafts replies in your voice, and types them into the KakaoTalk desktop app. Binary name: `openkakao-cli`.

This repository is **source only**. Chat history, local databases, embeddings, and credentials stay on your Mac.

It does **not** log into Kakao’s servers. It reads the encrypted local DB created by the KakaoTalk Mac app.

```text
KakaoTalk for Mac
    │  (messages land in a local SQLCipher DB)
    ▼
openkakao-cli reads the DB  ──►  vector memory (created on this Mac only)
    │
    ▼
auto-reply worker + LLM
    │
    ▼
types into the KakaoTalk composer (Accessibility)
```

Korean guide: [README.ko.md](README.ko.md)

---

## Requirements

| Need | Why |
|------|-----|
| macOS | KakaoTalk Mac app + Accessibility |
| KakaoTalk Mac, signed in | The local chat DB is filled by the app |
| Rust (`cargo`) | Builds `openkakao-cli` |
| Python 3.11–3.13 | Auto-reply scripts |
| Full Disk Access | Read the KakaoTalk container DB |
| Accessibility | Type into KakaoTalk |

For unattended `launchd` runs, point Python at the **real Homebrew binary**, not a keg symlink. Example: `/opt/homebrew/opt/python@3.13/bin/python3.13`

---

## Keep off GitHub

Copy none of this into the repo.

| Data | Default location |
|------|------------------|
| KakaoTalk chat DB | Encrypted files under `~/Library/Containers/com.kakao.KakaoTalkMac/Data/Library/Application Support/com.kakao.KakaoTalkMac/` |
| KakaoTalk cache | `~/Library/Containers/com.kakao.KakaoTalkMac/Data/Library/Caches/Cache.db` |
| Vector / memory DB | `~/Library/Application Support/openkakao/context.sqlite3` |
| Auto-reply state | `~/Library/Application Support/openkakao/auto-reply/` |
| Credentials | `~/.config/openkakao/credentials.json` |

The vector DB is created on first sync. Do not vendor it.

---

## Local KakaoTalk tables

Do not put chat files in git. Sign into KakaoTalk on Mac, **open each room the agent should see**, then confirm the tables exist.

The DB is SQLCipher. Filenames are per-account hex. The CLI finds them from the Mac UUID and Kakao user id. You do not copy files or put the passphrase in the README.

Path hint:

```text
~/Library/Containers/com.kakao.KakaoTalkMac/Data/Library/Application Support/com.kakao.KakaoTalkMac/<per-account DB>
```

| Table | Role | Columns used |
|-------|------|----------------|
| **NTChatRoom** | Rooms | `chatId`, `type`, `chatName`, `activeMembersCount`, `lastUpdatedAt`, `lastLogId`, `countOfNewMessage`, `hidden`, `linkId`, `directChatMemberUserId`, `displayMemberIds`, `extra` |
| **NTChatMessage** | Messages | `chatId`, `logId`, `authorId`, `message`, `attachment`, `type`, `sentAt` |
| **NTUser** | Senders | `userId`, `linkId`, `displayName`, `friendNickName`, `nickName` |
| **NTChatMeta** | Group titles | `chatId`, `kakaoGroupName`, `groupNickname`, `content` |
| **NTOpenLink** | Open-chat names | `linkId`, `linkName` |

```bash
./target/release/openkakao-cli doctor
./target/release/openkakao-cli local-chats
./target/release/openkakao-cli local-read <chatId>
```

If a room is missing from `local-chats`, open it in KakaoTalk and retry. KakaoTalk owns the schema.

`NTChatMessage.type` is numeric (text is usually `1`, photos around `2`). Control rows at `0` or below are skipped as reply candidates.

`Cache.db` is HTTP cache, not chat. Optional for diagnostics. Auto-reply depends on the local chat DB.

Runtime DBs the agent creates (never commit):

**Memory** — `~/Library/Application Support/openkakao/context.sqlite3`

| Table | Role |
|-------|------|
| `context_messages` | Chunks + embeddings |
| `context_messages_fts` | Keyword search |
| `context_live_events` | Live events from the local DB |
| `context_sources` | Which local DB a memory came from |
| `context_reference_packs` | Long-form reference packs |
| `context_operator_prompts` | Operator prompts from the menu bar |
| `context_retrieval_meta` | Index version |
| `context_message_topics` / `context_topic_stats` | Topic tags |
| `owner_style` / `owner_style_profile` | Your reply style |
| `owner_recipient_style_samples` / `owner_recipient_style_profile` | Per-recipient style |
| `response_time_stats` / `response_time_samples` | Typical reply latency |
| `reply_decisions` | Prior reply / skip decisions |
| `memory_note` | Short operator notes |

**Reply queue** — `~/Library/Application Support/openkakao/auto-reply/`

| File / table | Role |
|--------------|------|
| `reply-queue.sqlite3` → `reply_jobs` | Outbound jobs |
| `reply_job_tombstones` / `reply_job_supersessions` | Finished / replaced jobs |
| `pipeline_transitions` | Stage log |
| `model-circuit.sqlite3` → `model_circuit_breaker` | Model circuit breaker |

---

## Install

```bash
cd openkakao-bot
cargo build --release
./target/release/openkakao-cli --help
```

Grant **Full Disk Access** and **Accessibility** to Terminal (or `AutoReplyMenu`) in System Settings → Privacy & Security. KakaoTalk must be running.

```bash
./target/release/openkakao-cli doctor
./target/release/openkakao-cli local-chats
```

```bash
mkdir -p ~/.config/openkakao
cp config.example.toml ~/.config/openkakao/config.toml
```

Minimum `config.toml`:

```toml
[auto_reply]
# chats = ["bind:123456789012345:ExactRoomName"]
self_nickname = "your KakaoTalk display name"
python_interpreter = "/opt/homebrew/opt/python@3.13/bin/python3.13"
# reply_runner = "/Users/you/.local/lib/openkakao/gjc.js"
# reply_runner_kind = "gjc"
# reply_model = "model-name"
```

Only rooms in `chats` get replies. `self_nickname` must match the name KakaoTalk shows for you. Link-opening and image-to-model options default off.

Menu bar:

```bash
sh scripts/build-auto-reply-menubar.sh
```

Unattended `launchd`: `docs/auto-reply-launchd-supervision.md` and `scripts/install-auto-reply-launchd.sh`.

---

## Layout

| Path | Contents |
|------|----------|
| `src/` | Rust core: local DB, send, auto-reply host |
| `scripts/` | Python workers, DB watch, menu bar, install |
| `macos/AutoReplyMenu/` | Swift menu-bar app |
| `tests/` | Tests |
| `docs/` | Ops notes |
| `config.example.toml` | Config template |
| `examples/launchd/` | Background-run examples |

---

## Troubleshooting

**`doctor` cannot open the local DB**  
Launch KakaoTalk, open the target rooms, then re-check Full Disk Access for the CLI.

**Empty room title**  
Some group titles are missing in the DB. Bind with `bind:<chatId>:<exact on-screen name>`.

**No outbound replies**  
Accessibility, KakaoTalk focused, room listed in `chats`, menu bar or supervisor running.

**Empty vector search**  
Expected until sync has run. Leave the agent on; `context.sqlite3` is created locally.

```bash
cargo test
cargo build --release
./target/release/openkakao-cli doctor --json
```

---

## License

MIT. See `LICENSE`.

KakaoTalk is a Kakao product. This is an unofficial tool for the Mac app and rooms you already belong to.
