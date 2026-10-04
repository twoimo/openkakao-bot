# Alden local embeddings

## Scope

Alden GraphRAG can use a dedicated loopback embedding adapter instead of the
managed MLX generation gateway. The adapter performs inference only from a
pinned local checkpoint and exposes the discovery contract already consumed by
`scripts/auto_reply_knowledge_graph.py`.

Pinned model:

- Repository: `mlx-community/multilingual-e5-small-mlx`
- Revision: `5030c7625865046d350eeea28f427d80353d0ac0`
- License: MIT
- Architecture: `BertModel`, hidden size 384, maximum positions 512
- Expected weight file: `weights.00.safetensors`, 235,330,776 bytes
- Local snapshot: `~/.cache/huggingface/hub/models--mlx-community--multilingual-e5-small-mlx/snapshots/5030c7625865046d350eeea28f427d80353d0ac0`
- Runtime model ID: `mlx-community/multilingual-e5-small-mlx@5030c7625865046d350eeea28f427d80353d0ac0`

The adapter validates the pinned revision, model configuration, weight size,
model-card license, `tokenizer.json`, and pinned special-token IDs before
loading. Tokenization uses the installed lightweight `tokenizers` package
directly from that local `tokenizer.json`; the runtime does not import
Transformers. Hugging Face/Transformers offline environment guards remain set,
and the adapter never downloads weights or performs external inference.

## E5 retrieval roles

Multilingual E5 retrieval requires `query: ` for queries and `passage: ` for
indexed documents. The GraphRAG client therefore sends an explicit
`input_type` field:

- readiness probe and ANN lookup: `input_type="query"`
- dense index refresh: `input_type="passage"`

The adapter rejects requests without an explicit `query` or `passage` role and
adds the corresponding E5 prefix internally. This prevents raw, unprefixed E5
vectors from being treated as a valid production index.

## HTTP contract and bounds

The foreground adapter defaults to `127.0.0.1:11236` and refuses any other bind
host. Port `11235` is reserved by Alden's fixed iQ Flash-Next MLX server and
must not be used by this adapter. It implements:

- `GET /v1/models`: exactly one `loaded=true`, `state="ready"` model with
  `embedding`/`embeddings` capability and the exact pinned model ID.
- `POST /v1/embeddings`: exact model echo, ordered rows with integer `index`,
  and finite nonzero normalized vectors.

Bounds are fail closed: batch 1..8, request body at most 64 KiB, each input at
most 8,192 characters, tokenized input at most 512 tokens, and response at most
512 KiB. HTTP 500 responses do not expose local paths or runtime internals.

GraphRAG's no-environment default is now
`http://127.0.0.1:11236/v1/embeddings`. `OPENKAKAO_LOCAL_EMBEDDING_URL` remains
an explicit override for a different loopback endpoint. If the adapter is not
running, does not advertise exactly one ready embedding-capable model, returns
the wrong model identity, produces malformed/non-finite vectors, or the URL is
not loopback-local, dense retrieval is unavailable and search continues with
BM25 only. There is no automatic fallback to the generation gateway on `11234`,
to the reserved Flash-Next port on `11235`, or to external inference.

## Foreground activation

Use the dedicated minimal embedding runtime and existing checkpoint. No model
download is required for the prepared host:

```bash
"$HOME/Library/Application Support/openkakao/alden-local-embedding/runtime/bin/python" \
  scripts/alden_local_embedding_server.py \
  --host 127.0.0.1 \
  --port 11236 \
  --model-path "$HOME/.cache/huggingface/hub/models--mlx-community--multilingual-e5-small-mlx/snapshots/5030c7625865046d350eeea28f427d80353d0ac0"
```

The standard Alden GraphRAG path needs no URL environment variable because its
default already targets `11236`. Use the override only when intentionally
testing another loopback endpoint:

```bash
export OPENKAKAO_LOCAL_EMBEDDING_URL=http://127.0.0.1:11236/v1/embeddings
export OPENKAKAO_DENSE_EMBEDDING_MODEL=mlx-community/multilingual-e5-small-mlx@5030c7625865046d350eeea28f427d80353d0ac0
```

The foreground command is for isolated diagnosis. The dedicated LaunchAgent
below is the resident path and runs alongside the 27B generation service on a
separate loopback port. A dense index refresh is required after changing
encoder identity before RRF retrieval can be considered live.

## Dedicated LaunchAgent manager

`scripts/manage-alden-local-embedding-launchd.py` manages only the E5 adapter
under `com.openkakao.alden.local-embedding`. Production paths are pinned to:

- `/Applications/Alden.app/Contents/Resources/scripts/alden_local_embedding_server.py`
- `$HOME/Library/Application Support/openkakao/alden-local-embedding/runtime/bin/python`
- the pinned `5030c762...d0ac0` HF snapshot above
- `127.0.0.1:11236`

`$HOME` above is descriptive only. The manager derives the effective account
home from `pwd.getpwuid(os.geteuid()).pw_dir`, ignores the `HOME` environment
variable, and requires the fixed Python, HF snapshot, LaunchAgent plist, state,
log, and backup paths to remain inside that account home. Existing symlink
targets for the Python and HF files must also resolve inside it. The dedicated
runtime directory and `pyvenv.cfg` must be user-owned and must not be group- or
world-writable. The production manager no longer depends on the Python venv
under `~/Documents`, removing the path implicated by the observed launchd
startup stall while opening that interpreter.

The installed app script is the deliberate `/Applications` exception. Its
SHA-256 is deployment-pinned in `DEPLOYMENT_APP_SCRIPT_SHA256`. After an Alden
rebuild, first verify that the repository adapter and the installed app resource
have the same SHA-256; only then update that single reviewed pin for the new
deployment. A mismatch fails preflight rather than accepting a rebuilt script
implicitly.

The manager has read-only `status` and `preflight` actions. `preflight` checks
the installed adapter SHA-256, executable and model ownership/modes, pinned
model metadata, any existing plist, launchd identity, port ownership, and exact
model readiness when the service is already running. Any foreign listener or
ambiguous plist/service identity fails closed. Readiness HTTP disables
environment proxies and rejects redirects so the probe cannot leave the exact
loopback endpoint; the production contract value-checks `127.0.0.1:11236`.

Installation and activation are separate reversible steps:

```bash
python3 scripts/manage-alden-local-embedding-launchd.py status
python3 scripts/manage-alden-local-embedding-launchd.py preflight
python3 scripts/manage-alden-local-embedding-launchd.py install
python3 scripts/manage-alden-local-embedding-launchd.py start
```

`install` never loads the job. It accepts an absent plist or a plist that is
already provably for the same fixed adapter, makes a private backup before any
replacement, writes atomically, runs `plutil -lint`, reads the plist back, and
rolls the plist back if installation cannot finish. `start` requires the
manager install-state hash, refuses a pre-existing unmanaged `11236` listener,
then bootstraps only the dedicated label. The generated plist sets
`RunAtLoad=true` and deliberately omits `KeepAlive`, so login loading and an
explicit `bootstrap` start the adapter once without a crash-restart loop.
`start` does not issue `kickstart -k`; it waits up to 30 seconds for the exact
managed PID to own the sole `127.0.0.1:11236` listener and for the pinned model
readiness probes to pass. A job that was already loaded but is still starting
uses the same bounded wait without a restart.

If readiness fails after `start` itself successfully bootstrapped the job, the
manager boots out only that newly loaded dedicated label. If the job was loaded
before `start`, timeout leaves it loaded and reports failure, avoiding a
destructive restart of a process another invocation may still be loading. With
no `KeepAlive`, an adapter that later exits is not relaunched automatically;
an operator can use the owned `stop`/`start` sequence after diagnosis. The
immediately prior manager plist (`RunAtLoad=false`, no `KeepAlive`) has one
bounded inactive-state migration path: `install` verifies its exact prior hash,
preserves the original uninstall backup metadata, and replaces only that plist.

Readiness requires both `GET /v1/models` and a synthetic Korean
`POST /v1/embeddings` probe to echo the exact pinned model, revision, MIT
license, ready/loaded state, 384 dimensions, and a finite nonzero vector.
`stop` verifies the loaded plist path, process uid/arguments, and listener
ownership before bootout. Because launchd bootout completion is asynchronous,
`stop` then waits up to 2 seconds for both the dedicated service and the fixed
`11236` listener to disappear. During that wait it rechecks the loaded plist
identity, PID continuity, and any remaining listener ownership; an identity
change or timeout fails closed. `uninstall` requires manager install-state
ownership and restores the backed-up prior plist when one existed.

The manager contains no discovery or mutation path for the generation gateway,
the Flash-Next service, or Kakao workers/configuration. Parent review should run
`preflight` first; deployment is intentionally a separate operational step.

## Local verification — 2026-09-27 KST

With the pinned local checkpoint, the MLX adapter produced 384-dimensional
query and passage vectors. Against a PyTorch `BertModel` loaded from the **same
weight file**, cosine similarity was **0.99999952** for a Korean query and
**0.99999964** for a Korean passage. The only unused checkpoint entry in the
PyTorch comparison was the nonparameter `embeddings.position_ids` buffer.

A private-data-free, three-entity synthetic graph completed an actual dense
refresh: **3/3 vectors** persisted with the exact model ID and matching graph
watermark. A Korean search returned **one BM25 candidate, three dense
candidates, and `search_mode=rrf`**. This checks the adapter-to-GraphRAG path.

After the LaunchAgent was ready, a locked live-index refresh backed up both
SQLite files through SQLite's backup API under the private state directory.
It indexed **50/50** existing graph entities with **384-dimensional** vectors,
the exact pinned E5 model ID, and `local-embedding-lsh-v2`. A readback query
returned **two BM25** and **40 dense** candidates with `search_mode=rrf`.
This checks the persisted graph/index and local adapter. The three Kakao reply
workers were then cut over from immutable runtime `20260927T034834Z-12017` to
`20260927T082218Z-23471` after a clean drain: all three reported
`stopped_clean`, their prior watchdog and worker PIDs exited, and the new
runtime came up **3/3 ready** on watchdog attempt 1 with zero restarts. The
configured room-selector hash matched, pending gaps and active jobs stayed at
zero, and acknowledged watermarks did not regress. A read-only query through
the staged source used by the new workers returned `search_mode=rrf` with two
BM25 and 40 dense candidates. One completed `sent` and two completed `skipped`
queue rows crossed the worker's 30-day retention boundary during startup and
were preserved as three durable tombstones. No new conversation job arrived
after cutover, so this proves runtime wiring and retrieval availability, not
reply quality or a measured skip-rate reduction.

The installed Alden bundle now runs the pinned adapter through the dedicated
LaunchAgent. Its first start with the voice venv under `~/Documents` timed out
after 30 seconds during Python path initialization; the manager booted out that
job and left the generation service untouched. An offline `uv` install placed
the required 19 packages in a **256 MiB** runtime under Application Support.
With that runtime, `start` returned ready in **3.5 seconds**. Readback showed
the same PID as the sole `127.0.0.1:11236` listener, the exact pinned model and
384 dimensions, and an idempotent second `start` reported `changed=false`.
After four minutes, the resident process had **688,736 KiB RSS** and 0.0% CPU
at the sampled instant. The 27B generation listener remained on `11234`.
The dedicated LaunchAgent was subsequently migrated from `RunAtLoad=false` to
`RunAtLoad=true`. `plutil -lint` passed, the installed plist read back `true`,
and a fresh bootstrap returned the pinned model ready with 384 dimensions in
**22.3 seconds**. Readback found the service and sole `11236` listener owned by
the same new PID; all three Kakao workers remained ready and idle with no
pending DB-watch gaps. This verifies the installed login-start configuration
and current service, while an actual logout/login cycle remains unobserved.
The live graph was refreshed as described above; simultaneous Flash-Next
residency remains unverified.

On the same host and voice Python environment, replacing the Transformers
tokenizer import with the pinned `tokenizer.json` through `tokenizers` changed
fresh-process maximum RSS from **1,028.4 to 670.7 MiB** (−357.7 MiB,
−34.8%). One local run measured load time **5,802.9 → 420.5 ms**. For ten
short warmed Korean queries after two warmups, median latency was **4.56 →
4.17 ms**; another final fresh run measured **7.62 ms**, so query-latency
improvement is not established. RSS is the embedding process's maximum, not
whole-system memory or a simultaneous 27B/Flash-Next measurement.

## Primary references

- https://huggingface.co/mlx-community/multilingual-e5-small-mlx
- https://github.com/waybarrios/vllm-mlx/blob/main/docs/guides/embeddings.md
