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
- [DREAM-RSI offline provenance and replay loop](docs/architecture/dream-rsi-provenance-loop.html)

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
- Graph and retrieval code is implemented in `scripts/auto_reply_knowledge_graph.py` and `scripts/auto_reply_reference_search.py`: query normalization feeds BM25/FTS5 and dense ANN candidate paths, RRF fuses successful dual rankings, graph expansion uses `k_hop_neighborhood`, and result metadata includes evidence IDs and index/watermark fields. 재색인은 같은 경로에서 로컬 dense ANN 색인(`knowledge-dense-ann.sqlite3`)을 갱신하고, 유효한 loopback 임베딩 endpoint가 있을 때만 `search_mode = "rrf"`를 보고하며 그 밖의 경우에는 `bm25_only`로 닫는다. Diagram source: [GraphRAG retrieval sequence](docs/architecture/graphrag-search-sequence.archify.json). 재색인에서 dense ANN 갱신까지의 단계는 [재색인·dense ANN 갱신 시퀀스](docs/architecture/graphrag-reindex-dense-refresh.html)로, dense 상태가 설정 창까지 도달하는 읽기 경로는 [dense 상태 노출 경로 시퀀스](docs/architecture/graphrag-dense-status-exposure.html)로 나눠 기록했다.
- 재색인·dense ANN 갱신 시퀀스는 `validate sequence --quality showcase`에서 9/9 artifact checks, 0 errors / 0 warnings로 통과했고, `deliver`는 artifact SHA-256 `4ae11d79e98d2dd9c975626e18a631f54f14a9b3cd0421eb7c05f00579908c3b` (806,740 bytes)로 성공했다. 표준 `visual-check`는 `status="pass"`, diagnostics 0이며 1440x900, 1600x1000, 1920x1080, 2048x1320 light containment와 readability, viewerChrome을 모두 통과했고 1440x900·2048x1320의 light/dark capture를 기록했다. 빈 그래프에서는 dense 연결 없이 `last_dense_status="empty"`·`last_dense_indexed_at=0`으로 닫히는 분기도 함께 담았다. Receipt: [graphrag-reindex-dense-refresh.visual-check.json](docs/architecture/graphrag-reindex-dense-refresh.visual-check.json).
- dense 상태 노출 경로 시퀀스는 같은 검사에서 9/9 artifact checks, 0 errors / 0 warnings로 통과했고, `deliver`는 artifact SHA-256 `7cc3b096f4b5b4f2b0a35e931b86ef6a0749d329dc922406a09e924250ed2078` (803,834 bytes)로 성공했다. 표준 `visual-check`는 `status="pass"`, diagnostics 0이며 같은 4개 viewport light containment와 1440x900·2048x1320 light/dark capture를 기록했다. Receipt: [graphrag-dense-status-exposure.visual-check.json](docs/architecture/graphrag-dense-status-exposure.visual-check.json).
- Dataset, DPO/fine-tune, and offline DREAM-RSI support is present in `scripts/auto_reply_golden_dataset.py`, `scripts/auto_reply_finetune.py`, `scripts/auto_reply_dream_rsi.py`, and `scripts/dream_rsi_alphaxiv.py`. DREAM-RSI 논문 provenance는 `--paper-source {auto,alphaxiv,orx}`로 `alphaxiv_cli`와 identity-verified OpenResearch `orx_cli` 두 provider를 구분하며, `auto`는 `alphaxiv`를 우선하고 없을 때 `orx`로 내려간다. Replay 평가는 기본 후보 집합에 `INCUMBENT_POLICY`를 포함하며, checkpoint의 `replay_guarantee`는 incumbent가 평가된 경우에만 `fixed_replay_set_only` 범위의 보증을 기록한다. caller가 incumbent 없는 후보 집합을 전달하면 `not_applicable`을 기록한다. 이 경로는 live model을 자동 승격하거나 교체하지 않는다. 분석: [Dream-RSI paper analysis](docs/architecture/dream-rsi-paper-analysis.md).
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
- Unit 11·12 쌍에 대한 parent 검증에서 desktop Vitest는 5개 파일 **77/77**, Rust는 **58/58** 통과했고 `tsc`와 Vite production build도 성공했다. `/Users/twoimo/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11 -m unittest tests.test_auto_reply_menubar`는 **165 tests, OK**였으며 `sh desktop/scripts/smoke.sh`는 exit 0이었다. 이 수치는 commit `c443ae9`(2026-09-21 KST) 기준으로 재확인했다.
- 읽기 전용 menubar snapshot에서 `pipeline`은 `{"active_index":null,"event_id":"none","outcome":"none","stages":[{"id":"detect","state":"idle"},{"id":"authorize","state":"idle"},{"id":"queue","state":"idle"},{"id":"context","state":"idle"},{"id":"model","state":"idle"},{"id":"delay","state":"idle"},{"id":"send","state":"idle"},{"id":"confirm","state":"idle"}]}`로 확인됐다. stage id 순서는 `detect`, `authorize`, `queue`, `context`, `model`, `delay`, `send`, `confirm`이고 stage state 집합은 `active`, `done`, `skipped`, `failed`, `blocked`, `idle`이다. 이 readback은 component-level snapshot-shape 증거이며 live KakaoTalk 전송이나 live model generation을 입증하지 않는다.
- DREAM-RSI paper provenance는 실제로 구동했다. 고정 Python 3.11에서 `scripts/dream_rsi_alphaxiv.py --paper-source orx --paper-id 2609.14858`는 exit 0, `status="ok"`, `provider="orx_cli"`, `reason="orx_report_verified"`, `selection_method="direct_paper_id"`, `string_similarity_used=false`였고 evidence summary는 14,515자였다. 같은 명령의 `--paper-source auto`도 `alphaxiv` 부재로 `orx_cli`로 내려가 `status="ok"`를 냈고, `--paper-source alphaxiv`는 exit 2, `status="paper_unavailable"`, `reason="cli_missing"`, `evidence=null`로 fail-closed했다. `/Users/twoimo/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11 -m unittest tests.test_dream_rsi_alphaxiv`는 commit `c443ae9`(2026-09-21 KST)에서 **34 tests, OK**였고, 여기 기록한 orx 지표는 외부 `orx` provider 응답에 의존한다. 이는 paper retrieval provenance 증거이며 live model generation이나 model 승격을 입증하지 않는다.
- DREAM-RSI provenance·replay 다이어그램은 `validate dataflow --quality showcase`에서 9/9 artifact checks, 0 errors / 0 warnings로 통과했고, `deliver`는 artifact SHA-256 `354653dbb8aa253b24a48cf282a6ff1ca6fc1280b012156f3a1ff5792fddc73a` (802,935 bytes)로 성공했다. 표준 `visual-check`는 `status="pass"`, diagnostics 0이며 1440x900, 1600x1000, 1920x1080, 2048x1320 light viewport에서 `scrollHeight == innerHeight` containment와 readability를 만족했고 1440x900·2048x1320의 light/dark capture를 기록했다. Receipt: [dream-rsi-provenance-loop.visual-check.json](docs/architecture/dream-rsi-provenance-loop.visual-check.json).
- 독립 서브에이전트 리뷰에서 나온 4개 결함을 수정하고 재검증했다. (1) `orx` provider의 실패 provenance가 실제 실행과 어긋나던 `isolated_context`를 false로 맞추고 alphaxiv 실패는 true를 유지한다. (2) 외부 CLI `stderr`는 전체 Unicode `C*` 클래스(Cc/Cf/Cs/Co/Cn, U+0080-U+009F C1과 bidi/zero-width formatter 포함)를 제거하되 Cc는 단어가 붙지 않도록 공백으로 치환하고, 공백 정규화, 32자 이상 token run redaction, 200자 상한을 거친 뒤에만 직렬화한다. (3) `collect_orx_paper_report`와 `collect_paper_report`가 극단 `timeout`에서도 `OverflowError`를 포함해 total하게 닫히고 `invalid_timeout`을 반환한다. (4) `replay_guarantee`에 `incumbent_included`를 추가하고 문서와 다이어그램 문구를 조건부로 바꿔, caller가 incumbent 없는 후보 집합을 넘기면 보증 대신 `not_applicable`을 기록한다. (5) 두 번째 독립 리뷰에서 U+0080-U+009F C1 처리가 빠진 점을 지적했고 이번 패스에서 해당 누락을 닫는 회귀 테스트를 추가했다. 고정 Python 3.11에서 `tests.test_dream_rsi_alphaxiv`와 `tests.test_auto_reply_dream_rsi`는 **75 tests, OK**, CI focused Python 목록은 당시 **287 tests, OK**였고 `deca45a`가 launcher 회귀 테스트를 추가해 commit `633a436` 기준으로는 **288 tests, OK**였고, 같은 commit `633a436` 기준 재측정에서도 `scripts/dream_rsi_alphaxiv.py --paper-source orx --paper-id 2609.14858`는 exit 0, status ok, provider orx_cli, summary 14,515자였으며, 이 값은 외부 `orx` provider 응답에 의존한다.
- `docs/architecture/unit5-live-proofs.md` records browser and Tauri/WKWebView hide probes where the outstanding RAF was cancelled and render-count delta remained zero while hidden.
- The same proof log records controlled browser, abort, and isolated voice/STT checks. Those records are bounded component evidence; they do not establish an end-to-end KakaoTalk reply flow.
- The same proof log holds the volatile cutover values (installed bundle paths, LaunchAgent state, individual probe timings, and per-run cutover counts) that this summary deliberately does not duplicate.
- 설치 앱 검증: staging 누락(`scripts/mlx_serve_lifecycle.py`)을 수정한 pre-commit working tree(그 변경은 이후 commit `deca45a`로 기록됨)에서 재빌드·재설치한 설치 번들의 Python bridge 경로가 정상 동작했다. 설치본 자체의 `auto-reply-menubar.py`를 당시 provisioned menubar runtime으로 실행한 read-only 프로브는 exit 0과 유효한 `schema_version=3` snapshot JSON을 반환했다. 설치 시각, 설치본 파일 수, runtime 크기·버전, snapshot byte 수, PID, LaunchAgent 상태처럼 시점에 따라 변하는 cutover 값은 바로 위 방침대로 이 요약에 옮기지 않고 날짜가 붙은 proof log에만 둔다. 이 검증은 설치 번들의 Python bridge 경로에 대한 것이며 live KakaoTalk 전송을 입증하지 않는다. 설치된 UI의 live 캡처는 아래 bullet에 기록했다.
- 실제 카카오톡 DB에 대한 read-only 경로도 2026-09-21 KST에 실행했다. 저장소에서 빌드한 `target/release/openkakao-cli`와 고정 Python 3.11로 `local-chats --json`(exit 0, 50개 방)과 `context-sync-local`(임시 `--db`: 이름 있는 방 12개 중 1개만 exit 0, 그 방은 430,080 bytes·`context_messages` 76행)을 실행하고, 그 색인을 `_reindex_all`로 색인해 `last_snapshot_status = "copy_ok"`, `last_index_error = ""`, `kg_entities` 2행, `kg_relations` 1행을 실측했다. dense endpoint가 없어 `search_mode`는 `bm25_only`였고, in-process loopback stub을 물린 재측정에서 `rrf`로 바뀌었다. 원본 DB는 직접 열지 않았고 KakaoTalk 전송은 없었다. 명령·수치는 `docs/architecture/unit5-live-proofs.md`의 같은 날짜 절에 있다.
- dense 상태가 앱 렌더 경로까지 전달된다. commit `d96aa7f`에서 `sanitize_knowledge_status()` allowlist에 `dense_status`·`dense_indexed_at`을 추가하고 카카오 DB 동기화·색인 카드에 `settings-sync-dense` 한 줄을 넣었으며(새 버튼·카드·토글 없음), 고정 Python 3.11로 CI focused **298 tests, OK**, `tests.test_auto_reply_menubar` **165 tests, OK**, `sh desktop/scripts/smoke.sh` exit 0(Vitest **78/78**, Rust **59/59**, `tsc`와 Vite production build 성공)을 재측정했다. 설치 번들은 2026-09-21 21:23:04에 재설치했고, 설치본 `knowledge-graph-status` 프로브가 `dense_status = "unavailable:RuntimeError:local dense embedding unavailable"`를 담은 payload를 exit 0/stderr 0 bytes로 반환했다. 이는 데이터 경로 증거이며 live model generation이나 live KakaoTalk 전송을 입증하지 않는다. 프로브에 쓴 정확한 state root와 실행 명령, stdout payload는 `docs/architecture/unit5-live-proofs.md`의 같은 날짜 절에 기록했다.
- 설치된 Tauri 빌드의 live UI를 직접 확인했다. 2026-09-21 KST에 설치본 `/Applications/OpenKakao Jarvis.app`(패널 276x260)의 메뉴바 패널과 설정 창(760x760)에서 Computer Use 스크린샷과 AX 트리를 받아 [jarvis-live-panel.png](docs/architecture/jarvis-live-panel.png)와 [jarvis-live-settings.png](docs/architecture/jarvis-live-settings.png)로 남겼다. 패널 창의 조작 요소는 톱니바퀴 버튼 1개뿐이고, 설정 창은 대상 채팅방·AI 모델·Voice·카카오 DB 동기화·색인·DREAM-RSI·Knowledge·GeekNews 슬롯·History만 담으며 대량 검증·기능 점검·권한 관리 화면은 없다. 같은 측정에서 종전 `-10005 timeoutReached` 원인도 격리했다: 패널은 `label() == "jarvis"`가 포커스를 잃으면 hide되므로(`desktop/src-tauri/src/main.rs`) 평소 창이 0개이고, `cua.getApp`은 창이 있으면 69 ms(경로)·43 ms(표시 이름)에 붙지만 창이 0개면 5.05초 뒤 실패한다. `LSUIElement`를 원인으로 적은 종전 설명은 이 실측으로 대체한다. 이 증거는 UI 렌더와 창 생명주기에 대한 것이며 live model generation이나 live KakaoTalk 전송을 입증하지 않는다. 프로브 명령과 AX 트리 전문은 `docs/architecture/unit5-live-proofs.md`의 같은 날짜 절에 있다.
- 실제 loopback 임베딩 endpoint로 dense ANN 색인과 RRF 하이브리드 검색을 처음 재현했다. 외부 소유 `mlx-serve`가 `127.0.0.1:11234/v1/embeddings`에서 2560차원 벡터를 반환했고, 앱 자체 그래프(50 entities·341 relations)의 read-only 복제본에서 `dense_vectors` 50행·`ann_buckets` 400행이 생성됐으며 네 개 질의가 모두 `search_mode = "rrf"`였다(`candidate_count` 23–40, `entities_count` 12–13). 같은 측정에서 기본 `batch_size=32`가 요청당 4.864초로 `DENSE_EMBEDDING_TIMEOUT_SECONDS=4.0`을 넘겨 기본값 그대로는 dense 색인이 fail-closed로 끝나는 것을 실측했고, 그 불일치는 이후 수정했다(아래 bullet). 명령과 수치는 `docs/architecture/unit5-live-proofs.md`의 같은 날짜 절에 있다.
- 로컬 모델 생성 경로를 다시 실측했다. 2026-09-21 KST에 `scripts/verify_local_models.py --timeout 60 --owner --json`로 Flash-Next는 `readiness true · generation true · reason ok`(15,842 ms), 27B는 `model_not_ready`(2 ms)였고 `owner = model_owner_unmanaged`였다. 설정 카드의 `실추론 통과` 문구는 `~/Library/Application Support/openkakao/bujamentor/ondevice-last-probe.json`(2026-09-19T18:47:40+00:00, engine `mlx-serve-gateway`, latency 15,617 ms, preview `OK`)에 영속된 기록을 읽은 것이다. 이 실측은 Flash-Next 텍스트 생성만 확인하며 27B 생성·비전과 실제 카카오톡 전송을 입증하지 않는다.
- 숨김 상태 렌더 정지를 설치본에서 다시 확인했다. 2026-09-21 KST에 설치본 pid 50870의 메뉴바 패널을 실제 클릭으로 열고 닫으면서 2초 간격 6회 `ps -p 50870 -o %cpu=`를 샘플링했다: 패널 열림(유휴, `count of windows` 1)에서 2.9–3.1, 패널 닫힘(`count of windows` 0)에서 6회 모두 0.0이었다. `ps`의 %cpu는 감쇠 평균이고 프로세스 전체 값이므로 RAF 호출 수 자체를 세는 증거는 아니며, 창 숨김 시 미해결 RAF 취소와 render-count delta 0을 직접 측정한 기존 기록을 대체하지 않는다.

- dense 재색인의 기본 배치·timeout 불일치를 수정했다. 기본 `batch_size`를 32에서 8로 낮추고(실측 8건 0.837–1.283초 vs 32건 4.864초) 요청당 timeout 4.0초는 유지했으며, timeout 실패에만 배치를 절반으로 나눠 순서를 보존하며 재시도하고 singleton까지 timeout이면 fail-closed한다. 응답 형식·차원·row index 오류는 분할 없이 즉시 닫고, `indexed != len(rows)`이면 색인을 저장하지 않는다. 실패 경로는 단일 트랜잭션 롤백만 쓰므로 직전 ANN 색인이 남는다. 커밋 대상 리비전에서 `tests.test_auto_reply_knowledge_graph`와 `tests.test_auto_reply_menubar`를 함께 실행해 **233 tests, OK**(54.606초)였고, 실측 지연을 재현한 real-HTTP stub에서 warm 50건은 7회 호출로 `indexed:50`(`dense_vectors` 50·`ann_buckets` 400), 0.6초/건 16건은 `[8,4,4,8,4,4]`로 분할해 순서를 보존한 `indexed:16`, 5.0초/건 3건은 `[3,1]`로 닫혀 `unavailable`과 직전 1행 유지, 응답 행 수가 어긋난 경우는 단일 호출 `[8]`로 즉시 닫혔다. 외부 `mlx-serve`가 포화되던 시점에는 이 리비전의 live 재측정을 얻지 못했으나 서버가 데워진 뒤 다시 실행해 성공했다: 기본 설정으로 10.269초에 `indexed:50`(`dense_vectors` 50/50·`ann_buckets` 400)이고 네 질의가 모두 `search_mode "rrf"`(candidate 24–40, entities 7–13, relations 3)였다. 같은 시각 endpoint 프로브는 1건 6.693초(콜드)·8건 0.2초(웜)였다. live 수정 전후 값과 stub 표는 `docs/architecture/unit5-live-proofs.md`의 같은 날짜 절에 있다.
- dense/RRF가 설치 앱 자신의 그래프에서 동작함을 처음 확인했다. 2026-09-22 KST에 설치본이 dense 기본 endpoint로 닫힌 `127.0.0.1:8000`을 호출해 `last_dense_status`가 계속 `unavailable`로 남는 결함을 찾아, 기본값을 앱 자신의 로컬 MLX 게이트웨이(`scripts/local_mlx_gateway.py`, `http://127.0.0.1:11234/v1/embeddings`)로 정렬하고(commit `5fcf47b`) 새 모듈을 번들 resource allowlist에 등록했다. 이어서 게이트웨이가 생성 작업과 겹칠 때 singleton 요청도 4.0초 예산을 넘는 실패를 실측하고, 재색인 1회의 첫 HTTP 시도에만 30.0초를 허용하도록 고쳤다(commit `d2948e3`). 재빌드·재설치 뒤 설치 번들의 `auto_reply_knowledge_graph.py`를 앱의 provisioned menubar 런타임으로 `--reindex-once` 실행해 exit 0(11초)을 확인했고, 앱 자신의 상태 action은 `dense_status "indexed:50"`·`dense_indexed_at 1790005911`·`stale false`·`snapshot_status "copy_ok"`를 반환했다. 앱 상태 root의 그래프는 `kg_entities` 50·`kg_relations` 341, dense 저장소는 `dense_vectors` 50·`ann_buckets` 400이다. 같은 설치 모듈로 네 개 한국어 질의가 모두 `search_mode "rrf"`였다(candidate 24–40, entities 7–13, relations 3). 검증은 `tests.test_auto_reply_knowledge_graph` **72 tests, OK**, 11개 모듈 합계 **474 tests, OK**, Vitest **78/78**, Rust **59/59**였다. 동시 실행으로 ANN DB 잠금(`unavailable:OperationalError:database is locked`)이 한 번 발생했고 fail-closed로 닫혔으며, 앱 경로의 재색인 가드와 달리 독립 `--reindex-once`에는 그 가드가 없으므로 앱 재색인이 진행 중이 아닐 때만 실행해야 한다.

- 재설치가 중복 인스턴스를 남기는 결함을 찾아 가드를 넣었다(commit `f2f1a77`). 2026-09-22 KST에 `openkakao-jarvis-desktop`이 두 개 실행 중이었다: pid 18028은 00:34:21 재설치가 만든 이전 번들 백업 경로(`/Applications/.openkakao-jarvis.previous.20260922T003421.52158.app`, 설치 스크립트가 이미 `rm -rf`로 삭제)에서 lsof 기준으로 계속 실행 중이었고, pid 98353이 LaunchAgent가 추적하는 새 인스턴스다. `scripts/install-jarvis-desktop.sh`는 `launchctl bootout` 뒤 `wait_for_absent`로 **서비스**만 확인하고 남은 **프로세스**는 확인하지 않은 채 이전 번들을 지운다. 그래서 bootout을 벗어난 인스턴스가 삭제된 번들에서 살아남아 메뉴바 아이템·폴러를 중복시키고, 같은 state root를 두 인스턴스가 색인하면 관촡된 `unavailable:OperationalError:database is locked`가 재현될 수 있다. 이 세션은 프로세스를 종료하지 않았으므로 계획의 전환 조건(중복 작업자 없음)은 이 호스트에서 아직 충족되지 않았다. 수정은 bootstrap 뒤 launchd pid를 유계 대기로 얻고, 살아 있는 앱 프로세스에서 그 pid와 자기 pid를 떼 나머지를 stray로 보아 있으면 이전 번들을 지우지 않고 exit 3으로 닫는 것이다. 판정은 경로가 아니라 pid 동일성이며, 삭제된 번들에서 실행 중인 프로세스에 대해서도 macOS `ps`는 exec 시점의 argv 경로를 그대로 보여주기 때문이다. 같은 날짜 CUA 크로스 체크(앱 등록·`isRunning true`·창 0개에서 `-10005 timeoutReached` 재현)와 프로세스 표는 `docs/architecture/unit5-live-proofs.md`에 있다.

- 중복 인스턴스 가드를 넣고 CI 회귀도 함께 고쳤다. `scripts/install-jarvis-desktop.sh`가 이제 `wait_for_pid`(10회, 0.5초 간격으로 `launchctl print` 재실행)로 새 launchd pid를 얻은 뒤, 살아 있는 앱 프로세스에서 그 pid와 자기 pid를 떼 stray가 남아 있으면 이전 번들을 삭제하지 않고 exit 3으로 닫는다(commit `f2f1a77`). 판정은 pid 동일성이고 경로는 lsof/ps 진단 문구로만 쓴다. `tests.test_jarvis_desktop_launchers`는 **10 tests, OK**(기존 6 + 신규 4: 소스 계약, 가짜 어댑터 stray 없음/있음, pid 경합 뒤 정상, pid 미보고 시 유지)이고 `/bin/sh -n`도 통과한다.

- 같은 작업에서 CI 회귀를 발견해 고쳤다. commit `5fcf47b`가 `auto_reply_ondevice`를 `from local_mlx_gateway import ...`로 바꾼 뒤 `tests/test_auto_reply_ondevice.py`만 `scripts/`를 `sys.path`에 넣지 않아 CI focused 목록의 첫 모듈이 ModuleNotFoundError로 죽었다. 다른 테스트 모듈과 같은 bootstrap을 복원한 뒤 CI focused 10개 모듈은 **313 tests, OK**다(commit `4b75c7e`).

### Known limitations

- Stray menubar instances are now detected but not terminated. `scripts/install-jarvis-desktop.sh` waits for the launchd pid and exits 3 without deleting the previous bundle when another live instance of the app remains (`f2f1a77`). On this host pid 18028 still runs from the already-deleted previous bundle while launchd tracks pid 98353, so the cutover precondition of no duplicate workers is still unmet and terminating it is left to the operator; no process was killed during this work. Detection matches the executable name in the process table, so an unrelated process whose command line contains `openkakao-jarvis-desktop` can produce a false positive that fails closed; a false negative was not observed, because argv[0] is fixed at exec (`docs/architecture/unit5-live-proofs.md`).

- Current repository evidence does not establish a live KakaoTalk send path for this Jarvis work, and `docs/architecture/unit5-live-proofs.md` explicitly records proof runs with no KakaoTalk or live AX send.
- Computer Use로 이 메뉴바 앱에 attach하려면 최소 1개의 visible window가 필요하다. 패널은 포커스를 잃으면 숨으므로 창이 0개인 상태의 attach는 5초 timeout으로 닫힌다. 이는 도구 쪽 제약이며 앱 결함으로 기록하지 않는다. 같은 이유로 이 저장소의 Computer Use 확인은 패널을 실제 클릭으로 연 뒤에만 유효하다.
- Dense retrieval needs a loopback-only embedding endpoint (`OPENKAKAO_LOCAL_EMBEDDING_URL`, default `http://127.0.0.1:11234/v1/embeddings`, shared with the on-device gateway constant in `scripts/local_mlx_gateway.py`). The reindex path refreshes the persistent ANN store as an optional fail-closed stage: a missing, malformed, timed-out, version-mismatched, or non-loopback endpoint leaves the graph index intact and keeps `search_mode = "bm25_only"` instead of falling back to a cloud service. 이 호스트에서 종전 기본 `127.0.0.1:8000`은 아무것도 서비스하지 않아 설치 앱의 dense 색인이 계속 실패했고, 기본값을 앱 자신의 로컬 MLX 게이트웨이 `127.0.0.1:11234`로 정렬한 뒤 설치본에서 실제 임베딩 색인이 완료됐다. 2560차원 벡터로 `dense_vectors` 50행·`ann_buckets` 400행이 만들어지고 네 개 질의가 모두 `search_mode = "rrf"`였다. 다만 종전 기본 `batch_size=32`는 엔티티 50건(5,702 prompt token)에서 요청당 4.864초가 걸려 `DENSE_EMBEDDING_TIMEOUT_SECONDS=4.0`을 넘기므로 기본값 그대로는 이 그래프의 dense 색인이 fail-closed로 끝난다(`batch_size=1`에서는 19.814초에 `indexed:50`). 이 불일치는 기본 배치 8 + timeout 시 배치 분할로 수정했고, 게이트웨이가 생성 작업과 겹쳐 singleton 요청조차 4.0초를 넘는 경우를 위해 재색인 1회의 첫 HTTP 시도에만 30.0초를 허용한다(`DENSE_EMBEDDING_FIRST_ATTEMPT_TIMEOUT_SECONDS`). 그래도 30초를 넘는 포화 상태에서는 fail-closed로 닫히고 다음 재색인 사이클(모듈 기본 300초)에서 다시 시도한다. dense 경로가 loopback endpoint를 소유한 외부 프로세스 상태에 의존한다는 점은 그대로 남는다.
- Jarvis의 read-only DB 색인은 auto-reply의 authoritative 승격 게이트를 그대로 지난다. `context-sync-local`은 방마다 owner 스타일 샘플 1개 이상과 응답 타이밍 샘플 2개 이상을 요구하고(`src/context/mod.rs`), 못 채우면 exit 1로 끝나며 그 페이지의 색인 행이 롤백된다. 2026-09-21 KST live 측정에서 이름 있는 방 12개 중 11개가 이 경로였고, 그 방들은 그래프와 검색에 아무것도 남기지 않았다.
- DREAM-RSI and DPO outputs are offline artifacts and do not replace the resident model automatically (`scripts/auto_reply_dream_rsi.py`, `scripts/dream_rsi_alphaxiv.py`).
- The repository files cited above do not by themselves prove live model generation or release signing/notarization. Local bundles are ad hoc signed (`OPENKAKAO_SIGN_IDENTITY=-`); Developer ID signing and notarization remain unverified.
- Qwen3.8 27B generation is unverified. A real 27B swap needs an app-owned resident model: a foreign MLX server holding `127.0.0.1:11234` fails closed as `model_owner_unmanaged` (`scripts/auto_reply_ondevice.py`, `scripts/verify_local_models.py`), and settings report `외부 소유 · 27B 전환 차단` until the operator stops that external server.
- Stock openWakeWord `hey_jarvis` misses the Korean "헤이 자비스" utterance. A bundled opt-in calibration model scores it above `WAKE_THRESHOLD=0.65` without lowering the threshold, but it is not enabled by default and human-speaker generalization is unverified (`scripts/jarvis_voice.py`, `scripts/train_jarvis_korean_wake.py`).
- 이 기록을 작성한 시점의 호스트에서 `alphaxiv` paper-analysis CLI는 persistent `PATH`에 없었으므로 `--paper-source auto`는 설치된 OpenResearch `orx` CLI로 fallback한다. `orx_cli` provider는 요청한 paper id와 `alphaxiv.org/abs/<id>` header가 일치할 때만 실제 DREAM-RSI alphaXiv report를 수용하며, 실패 시 unavailable로 닫히고 static paper copy를 대신 사용하지 않는다 ([dream-rsi-alphaxiv-provenance.md](docs/dream-rsi-alphaxiv-provenance.md)).

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
