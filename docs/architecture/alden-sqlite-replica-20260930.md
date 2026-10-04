# Isolated SQLite snapshot correction — 2026-09-30

## Source change and scope

The replica copies the main DB and optional WAL into a private temporary directory. It never copies the source SHM. SQLite opens the replica read-only and rebuilds its transient SHM in that private, writable directory. The source connection is not locked or opened by this copy routine.

SQLite documents SHM as the transient WAL-index coordination cache, rather than durable database content ([WAL file format](https://sqlite.org/walformat.html)). A read-only WAL database can recreate the local SHM when its directory is writable ([WAL read-only behavior](https://sqlite.org/wal.html)). Source SHM churn was therefore an unnecessary snapshot rejection condition. This is an identified source defect; it is not proof that every earlier production outage had this cause.

For each attempt, let `S = (S_DB, S_WAL)` and `S_f = (exists, device, inode, size, mtime_ns, ctime_ns)`. A clone is accepted only when `S_before = S_after` and its copied file sizes match. A changed main DB device/inode is terminal. Genuine DB/WAL copy races consume at most **3 attempts**; exhaustion becomes the typed `SnapshotRetryExhausted` error and the exact one-line CLI diagnostic `Error: context_sync_snapshot_retry_exhausted`. Permission, disk-space, missing main DB, nonregular files, symlinks and identity changes remain terminal. The watcher retries only the exact diagnostic; it retains pending IDs and acknowledged watermarks, and keeps delivery fenced during backoff.

The routine uses APFS copy-on-write cloning for the main DB when available, with a regular-file-only byte-copy fallback for unsupported/cross-device cloning. The fallback does not swallow permission or disk errors. No live message-send permission, cursor reset or queue reset is added.

## Independent Web implementation and parent verification

Native current-session child Plato used **`chatgpt-web/gpt-5.6-sol`, `xhigh`**. Parent readback verified the native turn context and launcher trace `cf762fee397c-265312df`: send accepted at **05:39:13.749 UTC**, response visible at **05:39:13.784 UTC**, completed at **05:57:56.023 UTC**, with **1,980 response characters** and two rendered completion actions. The parent also observed the completed response in the actual Codex Web GPT application. No approval or rate-limit dialog was present.

An earlier closed-child resume had silently selected `gpt-6.1-sol`. It was stopped when detected; its partial draft was preserved and independently reviewed/refined by the freshly spawned, correctly routed Web child. That resume is not counted as Web implementation evidence. No completed Web task was resubmitted and no Astra request was made for this SQLite change.

Parent repeated the checks after the child stopped editing:

- **16/16 Rust context-sync cases** passed.
- **17/17 watcher Python cases** passed under both Python 3.13 and 3.11: 17 unique cases, 34 interpreter executions.
- Strict binary Clippy (`-D warnings`), rustfmt and `git diff --check` passed.

The fixtures prove committed WAL-tail visibility (main DB alone: **1 row**, replica DB+WAL: **2 rows**), private SHM recreation, unchanged source DB/WAL/SHM bytes, first-attempt acceptance under SHM-only churn, exactly three attempts under real content/copy races, and exclusion of uncommitted rows during a source exclusive transaction. No live KakaoTalk message was sent by these checks.

## Delivery boundary

These results initially establish reviewed source and fixture behavior. A subsequent activation receipt must identify the installed CLI, immutable three-room runtime, preserved code identity and queue/watermark readback before claiming production activation. Post-fix skip-rate and reply-latency improvement require new natural events; neither is inferred from this test count.
