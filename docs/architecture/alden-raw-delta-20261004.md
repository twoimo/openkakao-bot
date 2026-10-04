# Alden 0.3.14 — complete Raw catch-up and honest freshness

The installed Alden producer now updates the saved, account-scoped corpus even when original Kakao account metadata access is waiting for macOS permission. Collection is reported as `waiting`; saved-corpus readiness is a separate observation. Other collection failures still stop the cycle. Waiting collection retries after five minutes, while the existing graph loop continues. No new worker, model change, permission grant or message send is part of this update.

## Complete source versions

The initial 2,050,483-row archive remains immutable. A compact SQLite index compares row identity, producer digest and message direction with the next complete published version. Every added or corrected message appends its full source fields; removals append tombstones and retain previous Raw files. Room and author metadata are versioned separately. Account mismatches and incomplete publications fail closed. Cancellation never advances the version index before durable source files exist. External Kakao records remain OSK `_sources`, with the SDK's existing secret filter; they are not invented agent conversation rounds.

The first catch-up captured **135** new messages and **3,809** metadata records in **8** source chunks. Installed 0.3.14 subsequently caught another published version: its index contains **2,050,732** current messages, **249** more than the frozen base. All **115** active notes passed the actual OSK contract and retained Raw references; pending and conflicts were **0**. This is an observation of the saved publication, not proof that original Kakao collection is currently permitted.

## Cost and freshness

For an unchanged publication, Raw capture avoids the corpus scan and creates **0** source chunks. A matching source/derivation signature and verified note hashes also skip graph reindexing and SDK writes. Actual warm, same-process measurements used five repeated observations:

| Operation | Median | Range | Writes |
| --- | ---: | ---: | --- |
| Raw version check | 0.555 ms | 0.516–0.725 ms | 0 new source chunks |
| Entire saved-corpus sync cycle | 27.174 ms | 26.895–34.907 ms | 0 new source chunks or SDK revisions |

The initial digest-index bootstrap and capture took **24,745.666 ms**; the first derivation refresh took **19,731.425 ms**. Cold initialization and unchanged checks do different work, so these values do not establish a before/after speedup. For a loop interval of 60 seconds, the observed warm median has a duty fraction of `27.174 / 60,000 = 0.0453%`; this is elapsed work fraction, not measured process CPU or energy use. A changed publication still requires a complete identity/digest scan and may take seconds.

MCP preserves the real graph materialization age and adds the most recent verification age, `collection_state` and `freshness_scope: current_published_corpus` only when source signature and archived snapshot agree. The installed one-shot collector confirmed both endpoints reachable, saved corpus ready and original collection waiting. Existing long-lived MCP clients may retain the previously loaded Python module until reconnection; their older response shape is separate from the fresh installed collector. Alden-dot's own invocation remains unattested.

## Installation and checks

The installer now verifies executable identity after a command-line process match. Diagnostic processes and candidates that exited before readback do not cause a false duplicate. Actual Alden executables outside `/Applications` still block installation, as do unresolved live candidates and unknown process enumeration. Launchd PID identity and the final deletion recheck remain enforced.

Installed **0.3.14** matches the built app's **41** files by SHA-256 and passes deep/strict ad-hoc signature verification. All **3** configuration/CLI baselines and **5** existing model, embedding, voice and automation process identities were preserved. Installer tests **40/40** passed, including false matches, exited candidates, unresolved live processes, real duplicates and rollback. The affected Raw/MCP/source tests passed on the pinned CPython 3.11 runtime; the inline graph refresh regression confirms that a failed refresh retains its old timestamp and reports stale data.

The separate history pages and the 0.3.13 native page audit remain documented in [the Raw/history report](alden-raw-history-20261004.md). That rendering audit was performed on 0.3.13. This delivery does not establish notarization, physical primary interaction, microphone/wake validation, production model latency targets or Alden-dot attestation. The full goal remains active.
