## Alden 0.3.16 — 직접 시작하는 음성 대화와 자동화 히스토리

음성 페이지에서 **마이크 켜기·끄기**로 대화를 시작하고 중단합니다. 선택한 음성 대화의 맥락을 이어받고, 긴 입력을 잘라 해석하지 않도록 했습니다. 자동 호출어의 검증 기준은 유지합니다. 설치본 41개 파일과 7개 페이지의 기본·최소 창 검사가 통과했으며 실제 사용자 음성 검증은 남아 있습니다. [구현·검증·범위](docs/architecture/alden-manual-voice-20261004.md) · [음성 상태 흐름](docs/architecture/alden-manual-voice-20261004.v3.html).

사이드바에 **카카오톡 답변**과 **긱뉴스 전송**을 분리했습니다. 채팅방·맥락·내용·전송 결과를 검색하고 이전 기록을 읽습니다. 채팅방 선택도 검색할 수 있으며 로컬 모델 두 개를 명확한 이름으로 표시합니다.

설치된 MLX 앱이 `MLX-Serve.app`으로 바뀌어도 기존 서명·소유권 검사로 인식하도록 수정했습니다. 기존 27B 실행과 모델 설정을 보존합니다. 실제 Flash-Next 준비 요청은 현재 메모리 기준에서 중단됐으며 모델 구동 성공으로 표시하지 않습니다. [원인·실측·검증 범위](docs/architecture/alden-mlx-app-rename-20261004.md).

205만 Raw 기록과 이후 변경을 보존하고 OSK API로 활성 지식 115개를 실제 출처 좌표에 연결했습니다. 설치본 0.3.14의 41개 파일을 대조했으며 현재 저장 자료 2,050,732건, 대기·충돌 0건을 확인했습니다. 같은 자료의 재확인 중앙값은 27.17ms(n=5)이며 새 Raw 파일과 SDK 변경은 0건입니다. 원본 수집의 권한 대기는 별도로 표시합니다. [Raw 변경 추적·측정 범위](docs/architecture/alden-raw-delta-20261004.md) · [히스토리·7개 페이지 검증](docs/architecture/alden-raw-history-20261004.md) · [현재 이어받기 상태](docs/ALDEN_CONTINUATION.md). 로컬 ad-hoc 서명 설치이며 공증·Dot 실제 호출·물리 음성과 전체 목표의 남은 검증은 계속 진행합니다.

2026-10-03 · [Alden 0.3.10 로컬 임베딩·설치 근거](docs/architecture/alden-embedding-memory-20261003.md): 같은 가중치·44개 입력·변형별 새 프로세스 3회에서 E5 메모리 중앙값 14.453→1.517GB(−89.50%)를 재현했고 출력이 일치했습니다. 44개 요청 시간은 0.521→0.726초로 늘었습니다. 35개 설치 파일·별도 네이티브 숨김/설정 검사를 대조했습니다. [메모리 순서도](docs/architecture/alden-embedding-memory-20261003.html) · [현재 이어받기 상태](docs/ALDEN_CONTINUATION.md). 상시 앱 직접 조작·자연 음성·권한·공증·프로덕션 적용은 미완료입니다.

## Alden 0.3.12 — 동적 지식 그래프

확인된 관계가 시냅스 다리로 성장·수축하고, 노드 배치가 실시간으로 재조정됩니다. 화면에서는 은은한 흐름을 유지하며, 숨김·동작 줄이기·일시 중지에서는 멈춥니다. 상단 정보는 한 줄입니다. 같은 1200×760 예시의 5초 관측에서 프레임 타이밍 수정 전후 23.0→30.2fps, 프레임 간격 p95 50.1→35.2ms를 확인했습니다(변형별 1회; 추론·전력 개선 수치가 아닙니다).

[연구·수학·측정 근거](docs/architecture/alden-neural-plasticity-20261003.md) · [변경 감지와 렌더 순서도](docs/architecture/alden-neural-plasticity-20261003.sequence.html) · [예시 화면](docs/architecture/alden-neural-plasticity-20261003/graph-default-fixture.png). 해당 검증 당시의 로컬 설치본은 0.3.12였습니다. 공개 서명·공증, 실제 Dot 호출과 사람의 음성 검증은 별도로 남아 있습니다.

## Alden 0.3.11 — 설정 밀도·실제 기록·읽기 전용 MCP

설정 5페이지를 통일하고 중복 여백을 줄였다. 같은 1200×760 예시에서 본문 면적 +19.14%, 완전히 보이는 메시지 4→6(n=1). 실제 history ID는 문자열·유효 방·최신 순서로 전달하며 시스템 알림의 원문은 접어 읽을 수 있다. OSK 151/512노드의 합성 warm p50은 35.79%/56.84% 감소했다. MCP 0.2.0은 취소와 1.5초 deadline을 지원하고 외부 상태 조회에만 쓴다. 같은 collector의 API→MCP 비용은 1.415→1.653ms(+16.82%, 변형별 9000요청)다.

로컬0.3.11설치36경로·기존설정/5프로세스보존, UI237·desktop95·CLI1151/1ignored·Python1207/28skip을확인했다. 공유MCP등록과설치STDIO는확인했으며실제Dot호출·물리음성·전체native notification검사·서명/최종운영적용은미완료다. [5페이지검토](docs/architecture/alden-sidebar-audit-20261003.md), [OSK/MCP수치와범위](docs/architecture/alden-mcp-performance-20261003.md), [이어가기](docs/ALDEN_CONTINUATION.md).


2026-10-03 · [Alden 0.3.9 전달·근거](docs/architecture/alden-orbs-20261003.md): 정지한 장면을 정상으로 다루도록 네이티브 검사기를 수정하고 실제 WKWebView 기본·최소 설정, 탐색 복원, 합성 가시성 알림의 숨김·복원을 확인했습니다. 별도 검사 인스턴스의 결과이며 상시 primary 조작·물리 잠금·음성·Retina와 구분합니다.

2026-10-03 · Alden 0.3.8: [Thinking Orbs](https://libraries.dev/orbs)의 공식 엔진을 지식 그래프 주요 노드·사이드바·메뉴바에 적용했습니다. 실제 작업 상태에 연결하며 대기·숨김·모션 감소에서는 정지합니다. 노드 선택 시 전체 저장 설명과 채팅방·작성자·날짜가 있는 대표 원문을 보여주고, 네이티브 브리지까지 계정·인물·방 범위를 검증합니다. 같은 검색 화면의 1초 표본 3회에서 라벨 변경 138→0, 대기 렌더 0을 관측했습니다. 이는 소스의 명시적 예시 데이터 측정이며 설치·물리 화면·전체 전력과 구분합니다. [디자인](desktop/DESIGN.md) · [렌더 수명주기](docs/architecture/alden-three-render-lifecycle.html). 원본 DB 최신화·웨이크/물리 음성·공개 서명·프로덕션 전환의 기존 게이트는 유지합니다.

2026-10-03 음성 진폭: [0.3.7의 재생 PCM·실제 SDK 커서·가벼운 상태 읽기](docs/architecture/alden-playback-amplitude-20261003.md)를 검증했다. 물리 스피커·자연 음성 검증과 구분한다.

2026-10-02 우주 스타일: [지식 그래프·사이드바](docs/architecture/alden-universe-20261002.md)에 이어 [0.3.6 입력 진폭 연결](docs/architecture/alden-voice-envelope-20261002.md)을 검증했다. 멈춘 장면은 새 프레임이 필요할 때까지 대기한다. [설치본 8턴 음성·12회 문맥 측정](docs/architecture/alden-voice-sustained-20261002.md)은 WAV 생성과 실제 재생의 범위를 구분한다.

2026-10-02 지식 개선: [0.3.4 방 제목·정규화·검색 품질과 색인 비교](docs/architecture/alden-graph-quality-20261002.md). 제목이 없는 실제 방을 구별하고, 방·인물·기간을 보존하며 반복 복사와 정렬 비용을 줄였다. 설치·OSK·릴리스 결과는 별도 전달 기록으로 확인한다.

2026-10-02 음성 개선: [0.3.3 디코더 취소와 한국어 발음 검증](docs/architecture/alden-tts-cancellation-20261002.md). 같은 1.7B BF16 모델에서 한국어 원어 화자와 숫자 읽기를 검증하고, 실제 디코더 단계에서 취소를 연결했다. 사람 음성·wake·재생과 전체 응답성 검증은 미완료다.

2026-10-02 설치·검색: [0.3.2 전달 검증](docs/architecture/alden-voice-retrieval-delivery-20261002.json). 설치본 35개 경로와 CI 4개 작업을 확인했고, 로컬 실모델의 방별 검색·주제 전환 5개 턴을 검증했다. 0.3.2 첫 음성 파일의 내용 검사 실패를 위 음성 개선의 기준 사례로 사용했다.

2026-10-01 음성 추가: [MPS·메모리 진입 수정과 실제 합성 실행](docs/architecture/alden-voice-mps-20261001.md). 파일 생성 성공과 발음·내용 품질 검증은 구분한다.

Alden Desktop 0.3.9 includes the redesigned workspaces and reorganizes the wide window around a 3D knowledge graph and four companion pages: KakaoTalk history, voice history, DB updates, and Settings. Settings opens with room automation CRUD, a contextual editor, answer choices, and voice controls. The sidebar footer contains one operating control and a centered version. Left-click opens the 560×420 graph; right-click opens the wide window.

History reads all messages available in the local Kakao DB through fixed-anchor pages; it does not restore messages absent from that DB. Confirmed voice text is stored locally by session. The local corpus now stores every message in a fixed DB+WAL snapshot with full-text search, scoped numeric identities, resumable batches, and atomic publication. Dense/model-assisted retrieval remains a separate capability and is not inferred from a complete raw corpus. An initial real isolated snapshot contained 2,034,371 messages in 1,181 rooms with messages (1,182 roster entries). It produced a 1,913,323,520-byte published store with a successful quick check; this is a dated data snapshot, not a fixed current total. Existing foreground automation workers and send gates are preserved; catalog changes take effect when automation restarts.

The [updated data flow](docs/architecture/alden-history-osk-20261002.html) and [design decisions](desktop/DESIGN.md) describe the current screens. Version 0.1.9 is an internal source checkpoint; 0.2.0 is the UI delivery milestone. Local ad-hoc installation is distinct from a Developer ID signed/notarized public release, which still requires the unavailable Apple credentials.

2026-10-01 설정 개편: [넓은 설정과 OSK 지식 관리](docs/architecture/alden-settings-osk-20261001.md). 기본 설정은 전체 지식 그래프이며 Style Gallery의 린넨·세이지 디자인을 적용한다. 실제 OSK v4.1.2가 개인 Markdown 지식을 증분 관리한다. 올든 0.1.7 설치·33파일 일치·상시 앱 자동 갱신과 설치된 지식 50개 readback을 확인했다. 상시 창의 직접 조작은 도구 시간 초과로 미검증이며 공개 공증/production은 미완료다.

<div align="center">

2026-10-01 상시 설치본 추가: [실제 메뉴바·기본/최소 설정](docs/architecture/alden-primary-ui-20261001.md)을 기존 primary process에서 확인했다. 코어276×260·normal-level 설정960×880/640×680 이미지3개를 직접 검토했고 크기 복원·native 가시 창0을 확인했다. AXPress만으로는 열리지 않아 짧은 실제 CG 마우스 이벤트를 사용했으며 독립 가상 커서로 보고하지 않는다. 제품 소스·운영 작업자·메시지 전송 변경0. 각1회 창 표시 관측은 rendered UI p95와 구분하며 graph navigation·Retina·물리 중단/잠금·음성·production의 남은 목표를 유지한다.

전달: [Alden 그래프 탐색 후보 초안](https://github.com/twoimo/openkakao-bot/releases/tag/untagged-25ad469edd80663739aa)의 **7개 파일**을 다시 다운로드해 바이트·원격 digest를 대조했다. ZIP30파일은 설치본과 일치하고 overlay30파일도 hash 일치다. 소스`51f71c2`/증거`5cfea2b`의 [CI](https://github.com/twoimo/openkakao-bot/actions/runs/36799335869)4/4 성공·독립 설치/개인정보 검토 통과. [릴리즈 대조](docs/architecture/alden-graph-navigation-release-20261001.json). 공개 공증/프로덕션 완료를 뜻하지 않는다.


2026-10-01 추가: [그래프 탐색 복원·설정 렌더링](docs/architecture/alden-graph-navigation-20261001.md)을 소스`51f71c2`의 설치 바이너리에서 확인했다. 이전32단계·전체 보기·A→B→A epoch/dispose fence, 실제 persisted graph·기본/최소 WKWebView·숨김350ms 렌더 0·재개 frame 진행, 설치30/30·resource27/27, UI199/Rust90/Python1056·Clippy/build 통과다. [Archify](docs/architecture/alden-graph-navigation-20261001.html)9/9·4viewport·이미지4개 검토도 통과했다. 감사용 floating 창과 unavailable 다른 backend라는 범위, private native PNG·초기 foreign-exception2회 미해결을 기록한다. 전체 목표·물리UX·음성·worker·signed release/production은 미완료이며 Git/CI/초안 전달은 후속 readback으로 대조한다.


전달: [Alden 0.1.6 화면 가시성 후보 초안](https://github.com/twoimo/openkakao-bot/releases/tag/untagged-6730d9c69d5ac88931de)에 7개 파일을 올리고 다시 다운로드하여 모두 바이트 일치를 확인했다. 앱 ZIP 내부30파일·소스/증거 overlay23파일도 대조했다. 소스 `8791ea1`의 [CI](https://github.com/twoimo/openkakao-bot/actions/runs/36793165993)는 4/4 성공이다. 초안 target/overlay는 증거 checkout `b7bdda9`이며 공개 공증/프로덕션 완료를 뜻하지 않는다.


2026-10-01 추가: [화면 잠자기·세션 전환 처리](docs/architecture/alden-workspace-20261001.md)를 소스 `8791ea1`의 Alden 0.1.6 설치본에서 검증했다. 별도 설치 바이너리의 두 **프로세스 내부 합성 알림** 모두 두 창 숨김·350ms 추가 렌더 0, 일반 숨김·복원10회도 렌더 0이다. 네이티브 숨김 뒤 DOM 재개 차단과 종료 구독 정리를 추가했으며 Rust90/UI194/Clippy/build 통과·설치30/30·리소스27/27 일치를 확인했다. 물리 잠자기·세션 전환·잠금과 설정 렌더러는 미검증이며 전체 목표/공개 릴리즈/프로덕션은 미완료다. 새 Archify 도식은 가독성 미통과로 미전달이며 기존 검증 도식을 보존한다. Git·CI·릴리즈의 최종 상태는 후속 readback으로 대조한다.


네이티브 전달 링크: [Alden 0.1.6 로컬 후보 초안 릴리즈](https://github.com/twoimo/openkakao-bot/releases/tag/untagged-179cb6167afd74a68bd5). 앱 ZIP·Python 동반 실행 환경·소스/증거·체크섬 **7개 산출물**을 다운로드하여 원본과 바이트 일치를 확인했다. 증거 커밋 `60fdb04`의 [CI](https://github.com/twoimo/openkakao-bot/actions/runs/36790224085)도 **4/4 성공**이다. [전달 readback](docs/architecture/alden-native-render-release-20261001.json)에 소스·설치·릴리즈의 범위를 대조했으며 공개 공증/프로덕션 완료를 뜻하지 않는다.


2026-10-01 최신 네이티브 검증: 소스 `4838fff`의 Alden 0.1.6을 설치했고 **30/30 파일·27/27 리소스**가 일치했다. 설치 바이너리의 별도 WKWebView 인스턴스에서 실제 코어 이미지를 확인하고 **10회 × 350ms 숨김 추가 프레임 0**, 복원 3–4프레임/250ms를 관측했다. 숨김 요청→중단 확인 상한 median **14.622ms**, max **28.442ms**(n=10)다. [화면·측정·범위](docs/architecture/alden-native-render-20261001.md)와 [Archify](docs/architecture/alden-native-render-20261001.html)를 제공한다. 물리 화면은 [1.0]뿐이므로 Retina, 확장 설정, 상시 PID의 현재 화면, OS 잠금, 물리 단축키와 음성·생산·공개 서명 릴리즈는 미완료다. 기존 기록은 각 당시 결과로 보존한다.



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

Alden 음성 파일 출력의 취소 중 덮어쓰기 결함을 [수정·설치·원격 검증](docs/architecture/alden-voice-wav-20261001.md)했습니다. 필수 Python 1,020개 통과(17 skip), code CI4/4 성공. STT/TTS admission과 출시 wake 조건은 유지하며 사람 음성 전체 흐름은 미검증입니다.

Alden 첨부 문서 읽기는 [구현·설치·실제 로컬 모델 측정 기록](docs/architecture/alden-file-content-20261001.md)에서 확인할 수 있습니다. 실제 카카오 파일 transport와 운영 worker 적용은 아직 미검증입니다.

---

`openkakao-bot` is the project and CLI name; **Alden** is its macOS menu-bar assistant. It helps you find context and prepare replies in KakaoTalk. Conversation data and AI requests stay on your Mac; replies go through the KakaoTalk app already installed there.

<h2 id="architecture">Architecture</h2>

Alden is a macOS menu-bar assistant. Its compact display is drawn with Tauri v2 and Three.js. KakaoTalk messages, local search, and model requests stay on this Mac; sending a reply uses the installed KakaoTalk app. Press **⌘⌥⇧Esc** from any app to latch the global emergency abort.

Browser jobs use a dedicated, fixed CPython runtime with owned Chromium, separate from menu and voice dependencies. See [browser runtime provisioning and evidence](docs/architecture/alden-browser-runtime-20260927.md) for the pinned dependencies, offline installer and verification boundaries.

The 2026-09-27 installed Python browser backend returned `Example Domain` in **28.770 s**, matched an independent HTTP 200 DOM read, emitted one JSON result and left zero owned Chromium processes. This single check does not exercise the graphical Tauri caller or establish general accuracy or a latency improvement.

See the [end-to-end local system map](docs/architecture/alden-system.html) and its [Archify source](docs/architecture/alden-system.architecture.json). The map shows the desktop shell, local tools, model boundary, and read-only conversation index in one view.

### The knowledge observatory

1. Tauri supplies native visibility; the sidebar also selects which graph surface is visible.
2. Three.js renders a bounded 24-node view, static sky and projected Thinking Orbs on real node positions. Selecting a node shows its stored description and scoped original evidence.
3. Fresh voice, reply, retrieval and DB work select the orb state. PCM input/output retain their separate envelope. Quiet, paused and reduced-motion orbs use a static frame.
4. Hidden windows stop render callbacks and owned timers. Restoring a window reuses the bounded frame cache and existing source identities.

The retained legacy gold-core shader benchmark measured 3.4%/2.5% lower CPU submission at 15/30fps (approximately 3–5μs/frame,8 draw calls), with 11/12 pixel cases identical. It concerns that legacy component, not the current graph or GPU/battery power. The current 0.3.8 source fixture instead measured repeated label mutations 138→0 in three 1-second active samples, with idle graph and canvas-orb draws 0 in three 1-second samples.

See the [render lifecycle](docs/architecture/alden-three-render-lifecycle.html) and [activity-to-motion map](docs/architecture/alden-core-load-mapping.html).

Built 0.1.6 rendered in Chromium/WebGL2 with an empty public fixture; all three images were directly reviewed. This is not an installed WKWebView capture. [Compact settings](docs/architecture/alden-renderer-0.1.6-compact.png), [browser-emulated 2x panel](docs/architecture/alden-renderer-0.1.6-retina2x.png), and [review scope](docs/architecture/alden-renderer-0.1.6-review.json).

![Alden champagne-gold spherical core, 0.1.6 browser rendering](docs/architecture/alden-renderer-0.1.6-default.png)

### Finding related conversations

1. The app copies KakaoTalk's database and its side files to a temporary snapshot, then checks that the copy is stable.
2. It opens only the snapshot in read-only mode. A failed or unstable copy never falls back to the live database.
3. It normalizes shortened names and searches by both words and meaning. When both searches are available, reciprocal-rank fusion (RRF) combines their results; otherwise it keeps the working word-search results.
4. It stores people, topics, and their relationships as three-part facts (person — relationship — topic). Selecting a person or topic smoothly focuses nearby items, with at most 24 visible at once. This reads the existing index and does not start a new database copy or reindex.

The requested [Threads reference](https://www.threads.com/share/BBIeDkkHei/) was inaccessible during the 2026-09-30 check, so this delivery does not claim a verified interpretation of that post. The implementation retains message source evidence, canonical entities and unique subject–relation–object triples with bounded evidence IDs. Room, person, and topic nodes provide navigable neighborhoods. Drill-down starts at two relationship steps, limits each step to ten neighbors, and caps the view at 24 nodes (up to three steps when expanded). BM25 remains available when local embeddings are unavailable; RRF is used when both ranked candidate lists are ready.

GraphRAG defaults to the dedicated loopback embedding adapter at `http://127.0.0.1:11236/v1/embeddings`; `OPENKAKAO_LOCAL_EMBEDDING_URL` can explicitly override that URL. Dense retrieval fails closed: if the adapter is absent, not ready, advertises the wrong model, returns invalid vectors, or is configured off loopback, GraphRAG keeps BM25 and does not fall back to the `11234` generation gateway, the `11235` Flash-Next server, or external inference. See [Alden local embeddings](docs/architecture/alden-local-embeddings.md) for the pinned model and bounded HTTP contract.

The local synthetic GraphRAG refresh persisted **3/3** E5 vectors and combined **one BM25** and **three dense** candidates with RRF. The adapter's fresh-process maximum RSS fell from **1,028.4 to 670.7 MiB** after replacing the Transformers tokenizer import, a measured **34.8%** reduction on this host. The installed app's dedicated LaunchAgent now serves the exact E5 model on `11236`: it reached readiness in **3.5 seconds** from the **256 MiB** minimal runtime and remained resident after four minutes. Its installed plist now has `RunAtLoad=true`; a fresh bootstrap reached readiness in **22.3 seconds**, while actual logout/login startup remains unverified. A backed-up, locked refresh of the live graph indexed **50/50** entities. The three Kakao reply workers were cut over after a clean drain to immutable runtime `20260927T082218Z-23471`; readback found **3/3 ready**, no active jobs or uncertain deliveries, and no watermark regression. A read-only query through that exact runtime combined **two BM25** and **40 dense** candidates in RRF mode. No new conversation job had arrived after the cutover, so real reply quality and skip-rate changes remain unmeasured.

See the [GraphRAG search sequence](docs/architecture/graphrag-search-sequence.html) and [conversation-map drill-down](docs/architecture/openkakao-graphrag.html).

### KakaoTalk reply turns

Each incoming KakaoTalk row remains a durable queue item. Consecutive rows from the same room and numeric author, no more than 15 seconds apart, are assembled into one reply turn, capped at six rows and 8 KiB. The 15-second settle interval lets the newest fragment arrive before inference; a successor supersedes an earlier job only when its saved burst IDs include that earlier row. The assembled prompt keeps each included message in order. Author changes, older attachments, and size/count limits end the burst. Legacy v1 queue records retain their original 2-second interpretation.

Empty Kakao emoticon rows (message types 12, 20, and 22) enter the same durable queue as `[이모티콘]` instead of being acknowledged as empty input.

Punctuation-only follow-ups such as `???` and short referential questions such as `뭐지 저건` are treated as pointers to the current thread. When recent messages give a topic, the worker answers from that context or asks one brief, topic-specific clarification tied to its recent-message evidence; a generic “I don't understand” reply is rejected. If local generation returns malformed output, referential follow-ups use this grounded clarification directly, while other malformed outputs are retried at most once per durable queue event. Factual questions whose answer is absent keep the existing explicit unknown-answer path.

### Voice conversation

Voice is currently unavailable in the checked-out app: the wake-word model failed its release gate, the settings control is disabled, and the Rust bridge rejects voice-session startup. The source keeps the intended local pipeline bounded to four recent turns and 600 characters per message, with a ten-minute idle reset; those limits do not mean live microphone, STT, model, or TTS use has been validated.

Before loading Whisper or Qwen3-TTS, a local-only admission check requires at least 8 GiB or 10 GiB of reclaimable RAM respectively and 2 GiB of free swap. If either probe is unavailable or the budget is low, the voice session reports the condition and does not load the model. A synthetic local voice run on 2026-09-24 reached the safety stop before a complete turn; end-to-end voice remains unverified on this host.

Local MLX replies reject an explicit response model ID that differs from the requested model, after removing an optional `mlx/` transport prefix. Responses without a model ID remain accepted for gateway compatibility; generation readiness is still tracked separately.

The settings UI marks voice status unavailable when its heartbeat is more than five minutes old, instead of presenting a stale `wake_listen` state as live.

### Other architecture diagrams

- [Tauri menu-bar architecture](docs/architecture/alden-openkakao-units1-4.html)
- [Local MLX request drain and model swap](docs/architecture/alden-model-request-drain.html)
- [Offline DREAM-RSI review loop](docs/architecture/dream-rsi-provenance-loop.html)
- [Current local DPO scoring evidence and remaining integration](docs/architecture/alden-dpo-scoring-20260927.md)
- [27B checkpoint compatibility and corrected local DPO scoring](docs/architecture/alden-dpo-checkpoint-compatibility-20260930.md)
- [Versioned offline evaluation, real 27B receipts, and build integration](docs/architecture/alden-version-evaluation-20261001.md)
- [Versioned evaluation workflow](docs/architecture/alden-version-evaluation.html)
- [Actual offline 27B DPO updates, adapter reload, and evaluation](docs/architecture/alden-dpo-training-20261001.md)
- [Offline training workflow and product promotion boundary](docs/architecture/alden-dpo-training.html)
- [Browser-use lifecycle](docs/architecture/alden-browser-use-lifecycle.html)

The [dated local-tool evidence](docs/architecture/alden-local-tools-evidence-20260927.md) records two real Browser-use navigations through the local 27B model: navigation succeeded in **2/2** cases, but the requested field was correct in **1/2**. The source now includes an opt-in exact-target background AX CLI with a five-second maximum and explicit uncertain-effect reporting; **59/59** focused fake-adapter tests passed. No real AX press or installed desktop invocation is established by those tests.

After the DOM-grounding correction, the same two task types returned **2/2** exact matches against independent page reads, with **85/85** focused source checks passing. This covers document titles and the first Wikipedia search result, not general web factual accuracy. The [live receipt](docs/architecture/alden-browser-grounding-live-20260927.json) records the source hash and field evidence. The installed menubar Python lacks the browser dependencies, so installed desktop browser execution remains incomplete.

The corrected source and standalone AX CLI were rebuilt and installed locally at approximately 22:33 KST on 2026-09-27. The installed bundle matched the build with zero differences and one Alden process running; the Kakao host reported **3/3** rooms ready. The installed browser command itself returned `browser_job_failed`, confirming the separate runtime connection gap. This local ad-hoc-signed build is not the signed/notarized public release.

## Source readiness

The [2026-10-01 local vision evidence](docs/architecture/alden-local-vision-20261001.md) confirms actual pixel interpretation with the same cached 27B weights in a temporary owned MLX Core process: four public fixture answers were correct, and the checkout worker's image plus strict JSON generation path succeeded once through isolated discovery/auth seams. The shared server still runs with `--no-vision`, so production photo handling remains unavailable. The experiment does not verify installed UI interaction or retained visual history. It records request timings, post-run weight fingerprints, sampled MLX allocations, cleanup and the first run's invalid registry accounting; it makes no speedup or peak-memory claim.

The [photo follow-up fix](docs/architecture/alden-photo-followup-20261001.md) rejects future and foreign-room photo sources, preserves the original photo ID separately from the current question, and cancels/cleans owned download processes and scratch files. The same 27B answered one isolated pixel-grounded follow-up through the checkout worker in 16.325 seconds (n=1; fixture retrieval/lease/rerank seams). Local mandatory checks ran 1,027 tests with 17 skips and no failures; an inactive 21-asset candidate preserves the existing three selectors. This external worker is not activated in production or verified through the installed UI.

The [2026-10-01 atomic graph-cycle update](docs/architecture/alden-graph-cycle-20261001.md) shares one DB+WAL snapshot across all stages and rolls back partial graph/FTS changes on failure or cancellation. Five alternating pairs on the same frozen real data reduced graph-only median time from **5.383 to 3.390 seconds** (observed **37.04%**); dense was stubbed and host workloads were not controlled. Full entity/relation/FTS digests matched in all ten runs. Mandatory Python checks passed **981 tests, 16 skips, zero failures**. The [current installation receipt](docs/architecture/alden-graph-cycle-install-20261001.json) verifies **29/29 files, 26/26 resources**, signature and LaunchAgent executable; a separate backed-up real-E5 refresh passed in **3.335 seconds**, with matching graph/dense watermarks. These checks do not verify native window interaction, human voice or production worker activation. Earlier measurements below remain dated evidence.

**Earlier 2026-09-30 installation: Alden 0.1.6.** The [native-audio installation readback](docs/architecture/alden-native-audio-install-20260930.json) found 29/29 matching files, 26/26 resources and one LaunchAgent process, with ad-hoc signature verification. Backend readback retained 3/3 ready rooms and zero uncertain deliveries. The earlier public title task through the [installed browser entrypoint](docs/architecture/alden-installed-browser-0.1.6-20260930.json) matched an independent read in 48.556 seconds using the local 27B model. Native screen capture still times out; human speech, the physical shortcut and installed WKWebView behavior remain unverified. The local ZIP, offline Python sidecar and exact source/checksum manifest are in the ignored `dist/alden-0.1.6-local/` delivery directory; public signing/notarization is blocked by the existing workflow credentials.

The [recorded image-fallback replay](docs/architecture/alden-image-failure-replay-20260930.json) traces historical quality/composition claims to a no-pixel fallback after model failure. The source now states that the image could not be inspected and asks for its relevant text. Sixteen recorded-event helper replays reduced unsupported visual claims from 16 to 0, with no send or model call. The active immutable Kakao worker has not been replaced by this source change. The [actual model-catalog check](docs/architecture/alden-vision-capability-boundary-20260930.json) found that the resident 27B does not advertise vision; the new worker source rejects an image request before generation in that state. This remains the shared server's current boundary; the separate owned vision experiment above is not a production activation.

The earlier link/file boundary retained validated attachment metadata and exact row provenance. The [2026-10-01 file-content implementation](docs/architecture/alden-file-content-20261001.md) subsequently added bounded local parsing and confirmed four synthetic document questions through the actual local 27B model. Metadata alone still cannot substantiate a document answer. The shared production worker has not been replaced, and actual Kakao attachment transport remains unverified.

The [final live E5 evaluation](docs/architecture/alden-retrieval-eval-live-final-20260930.md) tested 13 synthetic queries against the source bytes installed on 2026-09-30: overall Recall@3/nDCG@3 were 0.9091/0.9091, with 24.941/45.865 ms p50/p95 and zero whole-context leaks. The [offline evaluation](docs/architecture/alden-retrieval-eval-final-20260930.md) separately checks fusion and provenance using fixed dense ranks. One older alias without retraction or a validity end remains among seven forbidden candidate judgments. This small corpus and concurrent host load do not establish production relevance or a latency improvement.

The [final focused source checks](docs/architecture/alden-source-verification-final-20260930.json) ran 959 tests with 2 skips and no failures on pinned CPython 3.11.9. They include general questions after a metadata-only file, explicit incoming/same-room file provenance, quoted-source validation and terminal image failures. The fixture-isolated commit `b987ad1` also passed all five remote checks: hosted Python ran 959 tests with 70 skips, the frontend passed 191 tests and the desktop Rust bridge passed 85. See the [exact remote CI receipt](docs/architecture/alden-remote-ci-20260930.json). These checks do not confirm live message delivery, microphone behavior or installed window interaction.

The [current delivery record](docs/ALDEN_DELIVERY.md) tracks the full outcome through installation and release. The voice source assigns each accepted user input a conversation ID, turn ID, context version and cancellation ticket. One worker retains only the newest pending input. Late STT/model/playback results cannot overwrite a newer turn, duplicate events are rejected, and jobs captured before a global abort do not revive after resume. A native voice-processing AVAudioEngine now supplies microphone input and playback inside the existing Python process. Three consecutive processed speech frames cancel the owned playback and preserve the start of the new utterance. Unprocessed input remains suppressed. The [native audio evidence](docs/architecture/alden-native-audio-20260930.md) records 971 focused tests, 191 frontend tests, 85 desktop Rust tests and one installed recorded-playback cancellation: its waiter returned in 12.418 ms. All captured input was zero, so human interruption, echo quality and full voice turns remain unverified. Wake release and memory gates stay enforced. The [voice cancellation diagram](docs/architecture/alden-voice-cancellation.html) reflects this implementation and its remaining limits.

The native-audio code commit `effa5c6` passed all four remote CI jobs, including the unsigned arm64 bundle, native library verification and offline runtime smoke. Hosted Python ran 971 tests with 73 skips; those optional-dependency skips differ from the two local skips. See the [exact native-audio CI receipt](docs/architecture/alden-native-audio-remote-ci-20260930.json).

On this Mac, the existing resident 4-bit 27B adapter answered all **12/12** fixed Korean follow-up cases before and after the socket change. Baseline full-answer p50/p95 were **1.511/2.017 seconds**, versus **0.970/1.269 seconds** in the later nonstream run; cache and competing load were not controlled, so no causal speedup is claimed. The final streamed timing attempt stopped at **2/12** cases when another inference was active. Owned streaming cancellation stopped the client in **3.082–5.383 ms**, but the engine became idle only after **2.279–2.464 seconds** and its cancellation counter did not increase. Immediate backend generation cancellation remains unmet.

The [renderer lifecycle update](docs/architecture/alden-render-lifecycle-20260930.md) prevents stale RAF callbacks from rearming and makes final GPU disposal idempotent. Its 191 frontend tests passed; a real Chromium/WebGL renderer with a synthetic Tauri bridge produced zero additional frames over 750 ms after hiding and retained one listener after 50 hide/show cycles. These are not installed WKWebView or whole-app GPU/battery measurements. The separate Python GraphRAG copier now snapshots stable DB+WAL files and rebuilds SHM only in the private replica. A **1.30 GiB** active plaintext context mirror passed `quick_check` in **5.314 seconds**, with no source SQLite connection. The later [actual encrypted DB and index catch-up](docs/architecture/alden-encrypted-snapshot-20260930.md) verified an **835 MiB** encrypted DB+WAL replica, imported **4 real new events in 0.535 s** through the installed CLI into a private mirror, and independently found the same 4 events in the existing production mirror. A backed-up, coordinated **6.522 s** refresh through the installed GraphRAG module aligned graph/E5 watermarks; it establishes dated catch-up, not continuous freshness or retrieval quality.

The [2026-09-30 requirement coverage table](docs/architecture/alden-goal-coverage-20260930.md) keeps the full goal active and lists the evidence still needed for voice, installed visual behavior, model residency, live replies and public release. A current [installed GraphRAG helper readback](docs/architecture/alden-graphrag-readback-20260930.md) used real local E5 embeddings and returned RRF in **4/4** queries; the persisted index was stale, so freshness and visible drill-down are not inferred.

The [2026-09-30 SQLite replica correction](docs/architecture/alden-sqlite-replica-20260930.md) uses stable DB+WAL signatures and recreates SHM privately. Copy races are bounded to three attempts; only typed exhaustion becomes the watcher's exact retry marker, preserving pending IDs and ACK state. Parent verification passed **16 Rust and 17 unique Python cases**, with the Python suite run on both 3.13 and 3.11. The [activated runtime and app readback](docs/architecture/alden-sqlite-activation-20260930.md) verified **20/20 runtime assets** and **28/28 app files**, with three ready rooms in all four samples. Natural traffic made the strict idle receipt fail, and CI exposed a backtrace-dependent termination diagnostic requiring a follow-up. New inbound jobs were captured, but a live skip-rate improvement is not established.

**2026-09-30 local update:** Alden 0.1.5 was rebuilt from `18a6d4b` and reinstalled; all **28 installed files** matched the built bundle, and LaunchAgent readback showed the new app running ([installation readback](docs/architecture/alden-install-readback-20260930.json)). This ad-hoc-signed local installation does not establish a notarized public release or a direct installed-screen check. A separate, offline teacher-forced DPO scorer now handles the already-converted 27B checkpoint without shifting its RMS norms twice: **99 focused tests passed**, **2/2 synthetic pairs** were scored in **18.522 s**, and greedy arithmetic returned `4`. Its **17,327,024,114-byte MLX peak** covers scoring after model load. Identical policy/reference weights yield exactly `ln(2)` and demonstrate an arithmetic invariant, not improved reply quality. The scorer is an opt-in source CLI, separate from the installed desktop bundle. See the [compatibility receipt](docs/architecture/alden-dpo-checkpoint-compatibility-20260930.json).

The [Python worker cancellation update](docs/architecture/alden-worker-abort-20260930.md) retains one captured abort epoch through generation, owned children and the send boundary. It preserves cancelled jobs and `delivery_unknown`, discards late generation results and releases only the completed request's model-call lease. **32 cancellation/packaging/send-state tests** and a separate **87 existing engine tests** passed. Two pre-existing context-freshness test failures remain documented. The paired Python/Rust runtime was subsequently activated; its [dated readback](docs/architecture/alden-fence-activation-20260930.md) does not establish a measured live skip-rate reduction.

The [native AX effect fence](docs/architecture/alden-native-ax-fence-20260930.md) reads the original job epoch through a strict paired relay and checks it before composing/submitting effects. **72 focused Rust cases**, **20 worker cancellation tests**, **6 send compatibility tests** and **13 packaging tests** passed. A current-session Web Sol child completed the Python relay, and the parent verified its actual Web send and completed-response trace. The replacement runtime retained the CLI's code identity and verified **20/20 packaged assets**. Readback preserved one startup fence followed by three **3/3 ready** samples with zero watchdog restarts; the rebuilt desktop matched **28/28 installed files**. See the [activation receipt](docs/architecture/alden-fence-activation-20260930.md) for limits: a physical shortcut event, installed screen rendering and live cancellation latency remain unverified.

**2026-09-30 live worker correction:** all three supervisors were found stopped for about 48 hours, with the watchdog blocked by one old `sending` job. An exact local self-row confirmation allowed that job to be reconciled to `sent` with **zero retransmissions** and an unchanged remainder of the queue. The existing three-room read-only preflight then passed in **2.641 s**. After further lock/ACK startup failures, the existing watchdog recovered: **four readbacks over 76.227 s** all found the stable host healthy and **3/3 running, reply-ready rooms**. See the [reconciliation evidence and bounded recovery](docs/architecture/alden-queue-reconciliation-20260930.md). A subsequent [fixed-window audit](docs/architecture/alden-current-skip-audit-20260930.md) found **17 stale and 7 superseded jobs**; stale ingress age had median **21.64 h**. The latest 100 local rows per room contained **zero unseen nonself rows** above the watcher checkpoints. With **zero new jobs after activation**, a post-fix skip ratio is undefined and reply-latency improvement remains unproved.

**Alden 0.1.5 was rebuilt and installed locally on 2026-09-27 KST.** The latest installed app matched the built bundle byte for byte, one Alden process was running, and its installed backend snapshot reported **3/3 reply-ready rooms** with Qwen3.8 27B selected. At 11:17 KST, the three live room workers were cut over to the immutable runtime containing the exact iQ/27B routing source: one watchdog, three workers, **3/3 rooms ready**, zero in-flight candidates, and no watermark regression. At approximately 12:28 KST, the bounded degraded-context fix was activated from a second immutable runtime after a three-room read-only preflight and idle/empty-queue check. Readback again showed one watchdog on attempt 1 with zero restarts, **3/3 rooms ready and idle**, no watermark regression, and the stable CLI inode, mtime, and SHA-256 unchanged. The third room recovered from a transient context-sync fence during startup. Around 13:00 KST, the watcher-only timing diagnostic was activated from a third immutable runtime; after its startup, all three room workers again read back ready and idle with numeric-only poll/ingress timing fields, no watermark regression, and the same stable CLI SHA-256. These fields use Δpoll = time between successful poll envelopes and Δingress = first candidate-state write time − KakaoTalk sent_at; neither is a KakaoTalk SQLite insertion timestamp. The scheduled GeekNews slot still selects its worker from the separate `runtime/` directory. See the [dated Flash-Next runtime check](docs/architecture/alden-flash-next-local-eval-20260927.md) and [installation evidence](docs/architecture/alden-install-20260925.md). This does not measure live conversational latency or KakaoTalk delivery.

The widened settings layout passed a fresh synthetic Chromium run on 2026-09-27: **32/32 checks**. At the settings window's 960 px width, its measured columns were 553.719 px and 342.266 px with no horizontal overflow; the knowledge canvas used **872 px** of CSS width. A simulated hidden-window signal produced **zero frames and zero snapshot polls over 1.503 seconds**; blur also produced zero frames over 1.503 seconds. The settings CSS contract separately passed 10/10 checks. These results cover the built frontend with a stubbed Tauri bridge, not the installed WKWebView or whole-machine GPU use. The [render receipt](docs/architecture/alden-desktop-render-check.json) and [settings capture](docs/architecture/alden-render-settings.light.png) use fictional chat and graph data.

The existing mixed 4/8-bit Flash-Next checkpoint could not restart when its memory preflight required about **77.1 GB** and found only **51.8–54.4 GB** available. A smaller Flash-Next 3.3bpw checkpoint was downloaded at a fixed revision and answered a synthetic request on an isolated localhost server while 27B remained loaded; that temporary server was then stopped. The installed Alden app and the active room workers route the exact iQ fast choice through `11235`, with a **60 GiB** app admission gate and exact loaded/ready check before saving. A later app-owned launch failed MLX Core's own memory preflight: **57.6 GB needed, 53.97 GB available**. No iQ listener or live iQ reply remains. On 2026-09-30, the real 27B LLM adapter returned `4` for one warmed arithmetic prompt in **1.485 s**; its prior memory estimate was **58.82 GB**, below the unchanged **64.42 GB** iQ admission requirement. See the [current adapter readback](docs/architecture/alden-local-model-readback-20260930.md) and [dated Flash-Next local check](docs/architecture/alden-flash-next-local-eval-20260927.md). These different procedures do not establish a comparative speedup, general reply-latency gain, voice operation, or a public release. The three bundled menubar bytecode files are pinned in this repository and hash-checked during packaging.

The saved reply model was a legacy mixed Flash pack that was not loaded. After backing up that setting, the menu backend accepted the exact ready 27B ID on `11234`; the active worker runtime read back the new 27B choice. A synthetic 27B request then timed out twice (**30 s** and **15 s**) while one server request remained stuck in prefill. The managed server was restarted with its existing profile, and a fresh localhost request returned HTTP **200** with `OK` in **1.343 s**; prefill returned to zero. These are synthetic observations, not a before/after KakaoTalk reply-latency or skip-rate measurement.

The `Alden Desktop Release` workflow now gates macOS arm64 packages on Developer ID signing, Apple notarization, and published-asset checksums. As of 2026-09-27, it has not produced a release: this repository has no configured signing/notarization secrets, and the local keychain has only a development identity. The app ZIP needs a separate CPython 3.11 runtime on a fresh Mac; the workflow packages a hash-pinned, offline-installable menubar runtime sidecar. On PR commit `c439129`, **4/4 CI jobs passed** with a separate successful GitGuardian check, including an unsigned macOS arm64 bundle build and an offline sidecar install into an isolated temporary home. That sidecar does not provision MLX models or voice dependencies and does not inherit the app's Apple notarization. See the [desktop runtime contract](desktop/README.md).

A 2026-09-27 read-only queue audit separated **9 proactive GeekNews sent** jobs from **2 conversation sent** and **21 conversation skipped** jobs. All 32 predated the 03:00:58 worker cutover. An 08:51 KST readback then found only scheduled GeekNews jobs after that cutover. At 11:44 KST, three new conversation jobs entered the queue after the 11:17 worker-runtime cutover: **2/3 were burst-superseded and the final 1/3 was skipped as stale backlog; 0/3 received a reply**. Their KakaoTalk `sent_at` values preceded queue insertion by **160.435–162.955 s**. The final job's context lookup fell back to degraded `recent_only` after a timeout, then the stale gate ended it before generation. The transition journal first recorded these ingress candidates at 11:44:29–39 KST. A separate read-only `local-poll` probe returned its first envelope in **0.169 s** and four more at roughly one-second intervals; that probe does not explain when those three rows first became visible to the watcher. This is a measured skip regression, with no live reply-latency or skip-rate improvement to report. The queue state alone does not independently establish KakaoTalk delivery. Two fake-adapter watermark regression cases and the turn-hold suite previously passed **40/40** on commit `d41546d`; these checks predate the new regression. See the [queue timing audit](docs/architecture/alden-install-20260925.md).

The source fix for this 11:44 failure bounds the `recent_only` timeout response window to verified queue ingress lag (at most 180 s) + 15 s burst settle + 2 s context timeout + 8 s residual. The observed 160 s case therefore receives a 185 s analysis window; missing or older ingress proof keeps the former 8 s fail-closed window. Seven focused fake-adapter regressions passed locally, including later-self and advancing-watermark guards. The source fix is active in the three local room workers; a real subsequent KakaoTalk conversation is still needed to measure whether it reduces skips.

A separate local 27B API request used a fictional recent-message fact (a meeting at 15:00 in building B, floor 2) followed by `???`. The model returned a clarification that correctly carried the meeting time and place in **3.712 s** (HTTP 200, 142 prompt and 25 completion tokens). This demonstrates one synthetic context reference through the selected local model, not the queue, recipient, or live KakaoTalk reply path.

The reply source now rejects generic confusion drafts such as `무슨 말인지 모르겠네요` in both strict and lenient selection. It uses the nearest grounded topic for `???` or `그래서?`; when no topic is available, it asks one direct clarification. The scheduled pre-send gate validates that exact fallback. A read-only replay of the earlier generic-reply event through the previously installed runtime selected a grounded clarification from six normalized recent rows; **9/9** focused Python checks pass for the new source. This is source and replay evidence, not a measured live reply-quality change.

On 2026-09-27 KST, the reply host was drained and activated from immutable runtime `20260927T105026Z-confusion` after commit `0003e92`. The candidate worker and committed source had the same SHA-256 (`a34376f0…6c042c2deb252766fa8d2`); the installed LaunchAgent pointed to that candidate. The old watchdog and all three room supervisors stopped cleanly before activation. The new watchdog reported one running attempt with zero restarts; **3/3** rooms reported ready with idle workers, **zero** nonterminal or uncertain queue jobs, **zero** pending DB gaps, and nonregressed acknowledged watermarks. This verifies deployment and operational readiness; no new natural conversation reply was observed, so the generic-reply rate remains unmeasured.

A fixed 24-hour read-only audit (2026-09-26 14:00 to 2026-09-27 14:00 KST) found **36 queue jobs: 11 `sent` and 25 `skipped`**. Fourteen skips were stale, nine were superseded fragments of a message burst, and two were already-commented duplicates. The nine supersession edges collapse the 36 jobs to **27 terminal queue chains**; neither count is a count of distinct human conversations. The first evidence for **11/14 stale jobs** recorded `model_temporarily_unavailable` (ten Flash-Next, one 27B); these reached terminal stale state after a median **934.7 s** from queue creation. The 11 `sent` jobs comprised nine GeekNews jobs and two model-unavailable reactions, not verified normal conversation replies. No job was created after the 13:00 diagnostic cutover within this window, so a post-fix skip-rate improvement remains unmeasured. See the [dated skip audit](docs/architecture/alden-skip-audit-20260927.md) for the denominator and method.

Voice startup is deliberately blocked until a wake model passes its release gate. The original six-clip evaluation had carried openWakeWord temporal state from one independent WAV to the next. After resetting and silence-warming the detector before each WAV, the **same six WAV hashes** gave **2/3** synthetic positive accepts and **0/3** negative false accepts at the unchanged 0.65 threshold; the old contaminated results were 3/3 and 1/3. Positive recall now fails the synthetic gate, and there are no human-speaker or microphone/room trials. The candidate remains excluded from the app bundle. These measurements do not establish a working microphone or a complete voice conversation.

A separate experimental v5 wake head was compared with v4 on 12 held-out synthetic WAVs, with voice, negative-phrase, and clip-hash splits disjoint from training. Re-scoring the **exact prior 12 WAV hashes** after the reset gave v5 **2/2** positive accepts and **1/10** false accepts, versus v4 **1/2** and **1/10**. A separately regenerated 12-WAV corpus gave v5 **1/2** and **0/10**, versus v4 **0/2** and **1/10**; all 12 WAV hashes differed from the prior corpus, so these two corpus runs are not a paired before/after comparison. Neither candidate passes the release gate, and neither has human-speaker or microphone/room evidence. See the [paired reset audit](docs/architecture/alden-wake-reset-paired-audit-20260927.md) and [new-corpus evaluation](docs/architecture/alden-wake-v5-heldout-eval.json).

### Historical runtime observations (2026-09-24–25)

At 23:43 KST on 2026-09-24, read-only checks returned HTTP 200 from local MLX `/health` and `/v1/models`. Flash-Next was loaded (75.3 GB resident); Qwen3.8 27B and Qwen3-TTS were unloaded. One bounded local Flash-Next generation returned `OK` in **56.849 s**. This verifies a single short local generation, not normal conversational latency; the earlier 45.060-second zero-token disconnect remains a separate failed attempt. The loaded model has no embedding capability, so live GraphRAG remains BM25-only and dense/RRF is unavailable.

The post-probe session-monitor readback was healthy at that time: all three configured room workers were ready, their reply model was available, delivery was enabled, and there were no pending database gaps. Free swap was **1,322.19 MiB**, **725.81 MiB** below the 2,048 MiB voice-model admission threshold. The observed voice heartbeat was over 25 hours old, so this check did not verify Whisper/TTS loading or a complete wake→STT→LLM→TTS turn. The installed immutable worker matched the source snapshot at the time by SHA-256; that comparison does not cover subsequent edits. The diagnostic request was local-only and used no conversation data; no message was sent and no model was loaded or swapped. See [engineering status](docs/engineering-status.md) for queue counts, historical confusion-reply replay, and measurement limits.

At 23:36 KST on 2026-09-24, Tauri app v0.1.5 was installed and its LaunchAgent process was running. All 27 installed bundle files matched that build. The configured global emergency shortcut is **⌘⌥⇧Esc**; the adjacent **⌘⌥Esc** chord belongs to macOS Force Quit. This bundle check did not exercise a physical global shortcut event.

On 2026-09-28 KST, the emergency-state source gained an explicit **다시 시작** control, shown only while a valid stop is latched. Resume requires a click and an incremented epoch readback; it never revives old cancelled tokens. Python and Rust now share a private, bounded `flock` protocol, reject corrupt or unsafe state without replacing it, and read FIFOs without blocking. **85 Rust, 185 UI and 98 focused Python checks passed**, including a real Python/Rust lock interoperability check over temporary files. Desktop Clippy with `-D warnings` and the frontend production build passed. See the [emergency-state protocol](docs/architecture/alden-abort-state-20260927.md) and [resume integration evidence](docs/architecture/alden-emergency-resume-20260928.md). These checks cover app-owned jobs and the state protocol; Kakao worker stop integration and a physical shortcut event remain unverified.

At 00:28 KST on 2026-09-25, a later read-only host check returned `healthy=false`: two of three room workers were ready and one was fenced during a transient context-sync failure. The primary reply room remained ready, with delivery enabled and no pending gaps. That observation predates the current source edits and does not verify their deployment.

The detailed implementation notes and dated verification records are kept in [engineering status](docs/engineering-status.md). They describe source checks, automated tests, and live runtime observations separately.

<h2 id="features">Features</h2>

- **Private processing**: Conversation search and AI replies run on this Mac.
- **Safe conversation reading**: The app reads a temporary, read-only copy of KakaoTalk's local data.
- **Conversation map**: Select a person or topic to see nearby names and related messages.
- **KakaoTalk integration**: Replies are entered in the KakaoTalk app.
- **Protected sending**: The app pauses when it cannot confirm which reply or destination is safe.

<h2 id="model-support">Model Support</h2>

The menu app offers a fast local model for everyday replies and a larger local model when requested. It does not automatically send conversation data to a cloud AI service. Exact model names and setup details are in the [Korean setup guide](README.ko.md); current source gates and dated runtime evidence are in [Source readiness](#source-readiness).

<h2 id="quick-start">Quick Start</h2>

### 1. Build from Source

```bash
git clone https://github.com/twoimo/openkakao-bot.git
cd openkakao-bot
cargo build --release
```

### 2. Permissions

In macOS **System Settings -> Privacy & Security**:
- **Full Disk Access**: Grant to your Terminal (or `Alden.app`) to read local database files.
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
reply_model = "ddalcu/Qwen3.8-27B-MLX-Serve-4bit"
```

`reply_runner` is a validated transport placeholder for this local MLX profile and must point to the installed `opencodex` executable. The exact 27B and Flash-Next IDs, with or without the `mlx/` prefix, are allowlisted. The current 27B deployment passed one synthetic localhost completion; Flash-Next can be selected explicitly when it is resident and ready. This does not establish normal conversational latency.

### 4. Run

```bash
# Verify environment and discover chat rooms
./target/release/openkakao-cli doctor
./target/release/openkakao-cli local-chats

# Build and install the primary Tauri menu-bar UI
sh scripts/build-alden-desktop.sh
sh scripts/install-alden-desktop.sh

# Start the installed LaunchAgent without rebuilding or reinstalling
sh scripts/start-auto-reply-menubar.command
```

Privacy paths, KakaoTalk table names, and Korean operator notes live in [README.ko.md](README.ko.md). Do not commit chat databases, `context.sqlite3`, `knowledge-graph.sqlite3`, or credentials.

---

## License

[MIT License](LICENSE)

The latest objective also names unsupported color judgments. A [read-only historical color-claim replay](docs/architecture/alden-color-claim-replay-20260930.json) found seven reply records across five events, including two persisted sent receipts. The current pure missing-image helper produced zero color/composition/quality judgments. It made no model or send calls and does not establish active-worker correction or pixel understanding.

The corpus lives separately at `<state-root>/knowledge/corpus/<opaque-account>/`. `working.sqlite3` is resumable; `context.sqlite3` is the last complete published version. The original shared `context.sqlite3`, enrollment, automation queues and source Kakao DB are preserved. Cached encrypted snapshots are reused until their bounded import completes, then refreshed on a five-minute cadence. Edits and removals are reconciled before publishing the new version. The app records incomplete corpus counts as pending. Do not confuse quoted outgoing history with confirmed human speech or delivered assistant output.

The background app currently waits at macOS app-data access confirmation before it can open KakaoTalk's account plist. Direct authorized terminal collection completed, but that does not establish background-app authorization. Metadata discovery is now bounded to three seconds and the DB history reports the access-wait state. Approve the macOS access prompt only if you intend to grant Alden that access; its scope may include other applications' data. The existing published corpus remains readable. Human approval is pending; no TCC grants, resets or database edits are performed by the installer.

The existing pinned E5 adapter was restored after launchd reported it registered but not running. It loaded the exact local 384-dimensional encoder; a backed-up live graph refresh persisted 118 vectors and four public query strings used RRF. Query timing and source freshness are separate from the pending live Kakao collection permission. Voice knowledge requests quote local corpus documents and graph facts, keep the latest actual speech last, preserve room identity, and decline ambiguous equal-name room selection. Changing topics ends the prior retrieval chain. See the [voice retrieval verification](docs/architecture/alden-voice-retrieval-20261002.md) and [installed voice file baseline](docs/architecture/alden-installed-voice-file-20261002.md).
