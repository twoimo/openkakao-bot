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

The Tauri menu-bar panel is a gold hologram core plus a top-right gear. All operator controls open from that gear into one settings window. The panel has no bulk-verify, feature-checklist, or permission-settings UI.

![Jarvis champagne-gold spherical core panel](docs/architecture/jarvis-core-panel.png)

GraphRAG indexing and production `context-sync-local` both copy the live KakaoTalk database plus WAL/SHM sidecars into a consistent temp snapshot, then open only that copy read-only with `PRAGMA query_only`; copy instability or failure fails closed without opening the source database. GraphRAG drill-down reads the existing `knowledge-graph.sqlite3` only; a node click does not copy KakaoTalk or reindex. DREAM-RSI on the settings card is checkpoint provenance, not a live trainer.

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

- [Jarvis Tauri architecture after cutover](docs/architecture/jarvis-openkakao-units1-4.html)
- [Jarvis Three.js render lifecycle](docs/architecture/jarvis-three-render-lifecycle.html)
- [GraphRAG drill-down sequence](docs/architecture/openkakao-graphrag.html)

The diagrams are architectural references, not a live generation trace. On-device generation still fails closed when a probe times out.

## Jarvis local-AI implementation status

### Implementation status

- The desktop app is a Tauri v2 menu-bar application with TypeScript and Three.js (`desktop/package.json`, `desktop/src-tauri/Cargo.toml`, `desktop/src/`). `RenderLifecycle`, `AnimationLoop`, `RuntimeSnapshotPoller`, and `JarvisCore` implement the visible/hidden render path and load/voice signal handling (`desktop/src/core/lifecycle.ts`, `desktop/src/core/animation-loop.ts`, `desktop/src/runtime-poller.ts`, `desktop/src/core/jarvis-core.ts`). Diagram source: [Jarvis render lifecycle](docs/architecture/jarvis-three-render-lifecycle.archify.json).
- 백그라운드 소스별 신호는 menubar snapshot의 `background` 객체에서 시작해 `desktop/src-tauri/src/python_bridge.rs`의 `sanitize_background`에서 allowlist·범위 제한을 거치고, `desktop/src/contracts.ts`의 `parseBackground`와 `desktop/src/core/load-mapping.ts`를 통과해 `JarvisCore`에 전달된다. 답변 대기·GeekNews·DB 동기화가 각각 독립 ring target velocity를 구동한다. Diagram: [Jarvis core load mapping](docs/architecture/jarvis-core-load-mapping.html), source: [Archify JSON](docs/architecture/jarvis-core-load-mapping.archify.json).
- 온디바이스 하드웨어 요약은 menubar snapshot의 `ondevice_hardware`에서 시작해 `desktop/src-tauri/src/python_bridge.rs`의 `sanitize_ondevice`에서 allowlist와 범위 제한을 거치고, `desktop/src/contracts.ts`의 `parseOnDevice`를 통해 `desktop/src/ui.ts`의 `renderHardware`가 기존 AI 모델 설정 카드에 한 줄로 표시한다. `chip` 64자, `cores` 0..1024, `memoryGb` 0..4096 및 소수점 1자리, `engine` 32자, `recommendedModel` 128자, `quant` 32자, `statusLabel`/`statusDetail` 240자로 제한하며 `memory_bytes`, `reason`, `engine_paths`, `fallback_models`, `worker_model_id`, `last_probe`는 전달하지 않는다. 메인 패널은 core + gear 구성을 유지한다.
- 답변 파이프라인 신호는 menubar snapshot의 `pipeline` 객체에서 시작해 `desktop/src-tauri/src/python_bridge.rs`의 `sanitize_pipeline`이 `active`, 닫힌 stage id, `stageIndex`/`stageTotal` 0..16, allowlisted `outcome`만 전달하고 `event_id`와 stage별 state는 제거한다. `desktop/src/core/load-mapping.ts`의 `pipelineLoad`는 detect .10, authorize .15, queue .20, delay .05, context .35, model .85, send .60, confirm .25를 사용해 reply load와 `max`로 합치며, `desktop/src/ui.ts`는 활성 stage만 설정 활동 줄에 표시한다. 비활성 상태에서는 기존 reply ring 동작을 그대로 유지한다.
- bridge 자체의 장기 작업은 `desktop/src-tauri/src/python_bridge.rs`의 bounded `SafeJobEvent` registry가 추적한다. browser 작업은 `browser/running/0.7`, model swap은 `model_swap/swap/0.9`로 등록되고 최신순 최대 8건, 300초 만료를 적용해 snapshot의 `jobs`로 합친다. 직렬화 키는 `{jobId, kind, stage, load, time, errorCode}`뿐이며 대화 내용, prompt, task text, token/secret은 포함하지 않는다. `desktop/src/core/load-mapping.ts`와 `desktop/src/runtime-poller.ts`는 job load를 전역 core load에 반영하고, `desktop/src/ui.ts`는 설정 활동 줄에 진행 중 작업 수를 표시한다.
- Local MLX hardware detection, model residency/ownership checks, leases, and swap rollback live in `scripts/auto_reply_ondevice.py` through `ModelResidencyManager` and the swap gate.
- Bounded tool runtime, owned Playwright browser use, voice control, and the emergency abort latch are implemented in `scripts/jarvis_tool_runtime.py`, `scripts/jarvis_browser_use.py`, `scripts/jarvis_voice.py`, and `scripts/jarvis_abort.py`.
- Graph and retrieval code is implemented in `scripts/auto_reply_knowledge_graph.py` and `scripts/auto_reply_reference_search.py`: query normalization feeds BM25/FTS5 and dense ANN candidate paths, RRF fuses successful dual rankings, graph expansion uses `k_hop_neighborhood`, and result metadata includes evidence IDs and index/watermark fields. Diagram source: [GraphRAG retrieval sequence](docs/architecture/graphrag-search-sequence.archify.json).
- Dataset, DPO/fine-tune, and offline DREAM-RSI support is present in `scripts/auto_reply_golden_dataset.py`, `scripts/auto_reply_finetune.py`, `scripts/auto_reply_dream_rsi.py`, and `scripts/dream_rsi_alphaxiv.py`. DREAM-RSI 논문 provenance는 `--paper-source {auto,alphaxiv,orx}`로 `alphaxiv_cli`와 identity-verified OpenResearch `orx_cli` 두 provider를 구분하며, `auto`는 `alphaxiv`를 우선하고 없을 때 `orx`로 내려간다. Replay 평가는 `INCUMBENT_POLICY`를 후보에 포함하고 checkpoint의 `replay_guarantee`가 선택 정책이 incumbent보다 나쁘지 않은지 `fixed_replay_set_only` 범위에서만 기록한다. 이 경로는 live model을 자동 승격하거나 교체하지 않는다. 분석: [Dream-RSI paper analysis](docs/architecture/dream-rsi-paper-analysis.md).
- style.gallery was reviewed read-only and only its restrained UI font stack is applied, as `--font-ui` in `desktop/src/styles.css`. The ivory / warm-black palette with champagne gold reserved for the core and selection is unchanged, and no neon, bloom, or glow treatment is used.

### Verified results

- Render-lifecycle diagram: `validate lifecycle --quality showcase` reports 9/9 artifact checks with 0 errors / 0 warnings, `deliver` succeeds, and `visual-check` exits 0 with zero diagnostics; its receipt records light containment at 1440x900, 1600x1000, 1920x1080, and 2048x1320, plus light and dark captures at 1440x900 and 2048x1320. Receipt: [jarvis-three-render-lifecycle.visual-check.json](docs/architecture/jarvis-three-render-lifecycle.visual-check.json).
- GraphRAG retrieval diagram: the same 9/9 showcase validation and successful delivery, and `visual-check` exits 0 with zero diagnostics; its receipt records light containment at 1440x900, 1600x1000, 1920x1080, and 2048x1320, plus light and dark captures at 1440x900 and 2048x1320. Receipt: [graphrag-search-sequence.visual-check.json](docs/architecture/graphrag-search-sequence.visual-check.json).
- Core-load mapping diagram: `validate dataflow --quality showcase`는 9/9 artifact checks, 0 errors / 0 warnings로 통과했고, `deliver`는 artifact SHA-256 `fb51d156dd630e66f7e876c0c5c6295e2dd00edc395753fb84519b2c64bd7a18`로 성공했다. 표준 `visual-check`는 1440x900, 1600x1000, 1920x1080, 2048x1320의 light containment와 1440x900·2048x1320의 light/dark capture를 모두 통과했고 diagnostics는 0이다. Receipt: [jarvis-core-load-mapping.visual-check.json](docs/architecture/jarvis-core-load-mapping.visual-check.json).
- 기본 menubar snapshot의 readback에서도 `background` shape를 확인했다. 실행 명령은 다음과 같다.

  ```bash
  /Users/twoimo/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11 scripts/auto-reply-menubar.py --state-root <temp dir>
  ```

  출력 JSON의 top-level keys에 `background`가 포함되었으며 해당 값은 아래와 같았다.

  ```json
  {"activity":0.0,"caption":"","db_sync":{"activity":0.0,"age_seconds":null,"capability_state":"","caption":"","fence_reason":"","state":"unknown"},"geeknews":{"activity":0.0,"age_seconds":null,"caption":"","posted_slots":0,"state":"unknown"},"rooms":[],"schema_version":1}
  ```
- 온디바이스 하드웨어도 같은 read-only menubar snapshot 경로에서 확인했다. 실행 명령은 다음과 같다.

  ```bash
  /Users/twoimo/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11 scripts/auto-reply-menubar.py --state-root <temp dir>
  ```

  출력의 `ondevice_hardware.hardware` 블록은 아래와 같았고, `recommendation.primary_engine`은 `"mlx-serve"`, `recommendation.recommended_model`은 `"ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit"`, `recommendation.recommended_quant`은 `"mixed 4/8bit"`, `last_probe`는 `null`이었다.

  ```json
  {"chip":"Apple M5 Max","cores":18,"is_apple_silicon":true,"memory_bytes":137438953472,"memory_gb":128.0}
  ```

  같은 `ondevice_hardware` 객체에는 절대 로컬 경로가 들어 있는 `engine_paths`도 포함된다. 이 때문에 desktop bridge는 해당 원본 객체를 그대로 전달하지 않고 위 allowlist 필드만 전달한다.
- Unit 11·12 쌍에 대한 parent 검증에서 desktop Vitest는 5개 파일 **77/77**, Rust는 **58/58** 통과했고 `tsc`와 Vite production build도 성공했다. `/Users/twoimo/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11 -m unittest tests.test_auto_reply_menubar`는 **165 tests, OK**였으며 `sh desktop/scripts/smoke.sh`는 exit 0이었다.
- 읽기 전용 menubar snapshot에서 `pipeline`은 `{"active_index":null,"event_id":"none","outcome":"none","stages":[{"id":"detect","state":"idle"},{"id":"authorize","state":"idle"},{"id":"queue","state":"idle"},{"id":"context","state":"idle"},{"id":"model","state":"idle"},{"id":"delay","state":"idle"},{"id":"send","state":"idle"},{"id":"confirm","state":"idle"}]}`로 확인됐다. stage id 순서는 `detect`, `authorize`, `queue`, `context`, `model`, `delay`, `send`, `confirm`이고 stage state 집합은 `active`, `done`, `skipped`, `failed`, `blocked`, `idle`이다. 이 readback은 component-level snapshot-shape 증거이며 live KakaoTalk 전송이나 live model generation을 입증하지 않는다.
- DREAM-RSI paper provenance는 실제로 구동했다. 고정 Python 3.11에서 `scripts/dream_rsi_alphaxiv.py --paper-source orx --paper-id 2609.14858`는 exit 0, `status="ok"`, `provider="orx_cli"`, `reason="orx_report_verified"`, `selection_method="direct_paper_id"`, `string_similarity_used=false`였고 evidence summary는 14,515자였다. 같은 명령의 `--paper-source auto`도 `alphaxiv` 부재로 `orx_cli`로 내려가 `status="ok"`를 냈고, `--paper-source alphaxiv`는 exit 2, `status="paper_unavailable"`, `reason="cli_missing"`, `evidence=null`로 fail-closed했다. `/Users/twoimo/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11 -m unittest tests.test_dream_rsi_alphaxiv`는 **27 tests, OK**였다. 이는 paper retrieval provenance 증거이며 live model generation이나 model 승격을 입증하지 않는다.
- `docs/architecture/unit5-live-proofs.md` records browser and Tauri/WKWebView hide probes where the outstanding RAF was cancelled and render-count delta remained zero while hidden.
- The same proof log records controlled browser, abort, and isolated voice/STT checks. Those records are bounded component evidence; they do not establish an end-to-end KakaoTalk reply flow.
- The same proof log holds the volatile cutover values (installed bundle paths, LaunchAgent state, individual probe timings, and per-run counts) that this summary deliberately does not duplicate.

### Known limitations

- Current repository evidence does not establish a live KakaoTalk send path for this Jarvis work, and `docs/architecture/unit5-live-proofs.md` explicitly records proof runs with no KakaoTalk or live AX send.
- Dense retrieval can become unavailable because of index/version/watermark or embedding failures; `scripts/auto_reply_knowledge_graph.py` and `scripts/auto_reply_reference_search.py` keep this explicit as a BM25-only degradation instead of a cloud fallback.
- DREAM-RSI and DPO outputs are offline artifacts and do not replace the resident model automatically (`scripts/auto_reply_dream_rsi.py`, `scripts/dream_rsi_alphaxiv.py`).
- The repository files cited above do not by themselves prove live model generation or release signing/notarization. Local bundles are ad hoc signed (`OPENKAKAO_SIGN_IDENTITY=-`); Developer ID signing and notarization remain unverified.
- Qwen3.8 27B generation is unverified. A real 27B swap needs an app-owned resident model: a foreign MLX server holding `127.0.0.1:11234` fails closed as `model_owner_unmanaged` (`scripts/auto_reply_ondevice.py`, `scripts/verify_local_models.py`), and settings report `외부 소유 · 27B 전환 차단` until the operator stops that external server.
- Stock openWakeWord `hey_jarvis` misses the Korean "헤이 자비스" utterance. A bundled opt-in calibration model scores it above `WAKE_THRESHOLD=0.65` without lowering the threshold, but it is not enabled by default and human-speaker generalization is unverified (`scripts/jarvis_voice.py`, `scripts/train_jarvis_korean_wake.py`).
- `alphaxiv` paper-analysis CLI는 여전히 persistent `PATH`에 없으므로 `--paper-source auto`는 설치된 OpenResearch `orx` CLI로 fallback한다. `orx_cli` provider는 요청한 paper id와 `alphaxiv.org/abs/<id>` header가 일치할 때만 실제 DREAM-RSI alphaXiv report를 수용하며, 실패 시 unavailable로 닫히고 static paper copy를 대신 사용하지 않는다 ([dream-rsi-alphaxiv-provenance.md](docs/dream-rsi-alphaxiv-provenance.md)).

### Recovery

- Emergency operator escape is **⌘⌥Esc**. It cancels input, TTS, and queued browser/AX work through the latch in `scripts/jarvis_abort.py`, is registered in `desktop/src-tauri/src/main.rs`, and never auto-resumes.
- A `delivery_unknown` result is never auto-retried; the operator confirms the real delivery state first (`scripts/auto-reply-tui.py`).
- On a model swap failure, `ModelResidencyManager` in `scripts/auto_reply_ondevice.py` attempts rollback to the owned prior model and fails closed if rollback cannot be proven.
- If dense GraphRAG lookup fails, `scripts/auto_reply_knowledge_graph.py` retains the BM25 candidate path and reports the degraded search mode while preserving evidence/index metadata.
- After a hidden window becomes visible again, `RenderLifecycle` restarts the render loop and runtime polling path (`desktop/src/core/lifecycle.ts`, `desktop/src/main.ts`); the transition is represented in the [Jarvis render lifecycle source](docs/architecture/jarvis-three-render-lifecycle.archify.json).

<h2 id="features">Features</h2>

- **Local-first privacy**: Chat history, vector memory, knowledge-graph rows, and credentials stay on your Mac.
- **Isolated read-only indexing**: KakaoTalk is copied to a temp snapshot, then opened read-only. Copy failure does not fall back to the live DB.
- **GraphRAG over the existing store**: Node focus retrieves a k-hop bundle (`2/3/10`) and fills `관련 사실·관계`. Failure returns empty `facts`.
- **Native macOS dispatch**: Replies go through Accessibility local-send, not Kakao server protocols.
- **Safety controls**: Chatroom allowlisting, rate limits, and a circuit breaker.

<h2 id="model-support">Model Support</h2>

The production default on Apple Silicon is **MLX Qwen3.8 Flash-Next**.

- Recommended: `mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit`
- Local image-reply and explicit replacement target (allowlisted and readiness-probed; actual generation unverified): `mlx/ddalcu/Qwen3.8-27B-MLX-Serve-4bit`
- DREAM-RSI: receipt / checkpoint provenance only (`settings-dream-rsi-card` after the sync card)

The production AutoReply worker and menubar permit only local MLX for text and image generation. Flash-Next is the default; Qwen3.8 27B is the image path and explicit operator-requested replacement target. Gemini and other cloud models are rejected as primary or fallback models and are never selected automatically.

Explicit network features such as fetching a posted URL are separate source-collection operations. Enabling them does not authorize cloud LLM execution or model/image egress.

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
