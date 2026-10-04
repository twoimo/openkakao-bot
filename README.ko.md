# openkakao-bot

English: [README.md](README.md)

macOS 카카오톡에서 **지정한 채팅방만** 읽고, 내 말투에 가깝게 **자동 답장**을 보내는 에이전트입니다.

이 저장소에는 **프로그램 소스만** 들어 있습니다.
내 카카오톡 대화, 로컬 DB, 벡터 DB, 로그인 정보는 **절대 올리지 않습니다.**
그런 데이터는 모두 **내 Mac 안의 원래 위치**에 그대로 둡니다.

> 실행 파일 이름은 기존과 같이 `openkakao-cli` 입니다.
> GitHub 저장소 이름만 `openkakao-bot` 입니다.
> 메뉴바 앱 이름은 **Alden(올든)** 입니다.

---

## 이 프로그램이 하는 일

1. 맥용 카카오톡이 이미 저장해 둔 **로컬 대화 DB**를 읽기 전용으로 봅니다.
2. 새 메시지가 오면 답장 후보인지 판단합니다.
3. 예전 대화와 내 말투를 참고해 답 초안을 만듭니다.
4. 카카오톡 입력창에 대신 입력해 보냅니다. (접근성 API)
5. Tauri 메뉴바 패널은 골드 코어만 표시하고 인터랙티브 요소를 두지 않습니다. 통합 설정은 메뉴바 트레이 아이콘을 우클릭해서 엽니다.

카카오 서버에 별도로 로그인해서 메시지를 빼 오는 봇이 **아닙니다.**
**맥에 설치된 카카오톡 앱**이 있어야 하고, 그 앱이 만든 로컬 DB를 읽습니다.

```text
카카오톡 맥 앱
    │  (대화가 로컬 DB에 저장됨)
    ▼ 임시 복사 후 mode=ro + query_only
openkakao-cli
    ├─► 벡터 기억 (context.sqlite3, 내 Mac에만 생성)
    └─► 지식 그래프 (knowledge-graph.sqlite3, GraphRAG k-hop 2/3/10)
    │
    ▼
자동 답장 워커 + 현재 로컬 MLX Qwen3.8 27B
    │
    ▼
카카오톡 입력창에 전송 (AX local-send)
```

---

## 준비물

| 항목 | 왜 필요한가 |
|------|-------------|
| macOS | 카카오톡 맥 앱과 손쉬운 사용 API를 씁니다 |
| 카카오톡 맥 앱에 로그인 | 대화가 로컬 DB에 쌓여야 합니다 |
| Rust (`cargo`) | `openkakao-cli` 를 빌드합니다 |
| Python 3.11 ~ 3.13 | 자동 답장 스크립트를 실행합니다 |
| 전체 디스크 접근 권한 | 카카오톡 컨테이너 안의 DB를 읽습니다 |
| 손쉬운 사용(Accessibility) | 카카오톡 창에 답을 입력합니다 |

24시간 무인 실행을 쓰려면 Python 경로는 Homebrew keg의 **심볼릭 링크가 아닌 실제 파일**이어야 합니다. 예: `/opt/homebrew/opt/python@3.13/bin/python3.13`

---

## 중요: 이 저장소에 넣으면 안 되는 것

아래는 **내 컴퓨터에만** 있어야 합니다. GitHub에 복사하지 마세요.

| 종류 | 기본 위치 |
|------|-----------|
| 카카오톡 대화 DB | `~/Library/Containers/com.kakao.KakaoTalkMac/Data/Library/Application Support/com.kakao.KakaoTalkMac/` 아래의 암호화된 DB 파일 |
| 카카오톡 캐시 | `~/Library/Containers/com.kakao.KakaoTalkMac/Data/Library/Caches/Cache.db` |
| 벡터/기억 DB | `~/Library/Application Support/openkakao/context.sqlite3` |
| 지식 그래프 | `~/Library/Application Support/openkakao/bujamentor/knowledge-graph.sqlite3` |
| 자동 답장 상태 | `~/Library/Application Support/openkakao/auto-reply/` |
| 로그인 정보 | `~/.config/openkakao/credentials.json` |

벡터 DB와 지식 그래프는 처음 동기화할 때 **프로그램이 알아서 만듭니다.** 미리 복사해 올 필요가 없습니다. 그래프 클릭은 이미 있는 저장소만 읽으며, 그 클릭으로 카카오톡 원본을 복사하거나 재색인하지 않습니다.

---

## 카카오톡에서 확보해야 하는 데이터베이스 테이블

대화 파일을 이 저장소에 넣는 것이 아닙니다.
**맥 카카오톡을 로그인하고, 봇이 볼 채팅방을 한 번 이상 연 뒤** 아래 테이블이 로컬 DB에 채워져 있는지만 확인하면 됩니다.

로컬 DB는 SQLCipher로 암호화되어 있습니다. 파일 이름은 계정마다 다른 16진수 이름이고, 이 프로그램이 맥 UUID와 카카오 사용자 번호로 찾아 엽니다. 직접 파일을 복사하거나 암호를 README에 적을 필요는 없습니다.

### 1) 필수: 카카오톡 로컬 대화 DB

경로 힌트:

```text
~/Library/Containers/com.kakao.KakaoTalkMac/Data/Library/Application Support/com.kakao.KakaoTalkMac/<계정별 DB 파일>
```

| 테이블 | 역할 | 이 프로그램이 읽는 주요 컬럼 |
|--------|------|------------------------------|
| **NTChatRoom** | 채팅방 목록 | `chatId`, `type`, `chatName`, `activeMembersCount`, `lastUpdatedAt`, `lastLogId`, `countOfNewMessage`, `hidden`, `linkId`, `directChatMemberUserId`, `displayMemberIds`, `extra` |
| **NTChatMessage** | 메시지 본문 | `chatId`, `logId`, `authorId`, `message`, `attachment`, `type`, `sentAt` |
| **NTUser** | 보낸 사람 이름 | `userId`, `linkId`, `displayName`, `friendNickName`, `nickName` |
| **NTChatMeta** | 그룹방 제목·별명 | `chatId`, `kakaoGroupName`, `groupNickname`, `content` |
| **NTOpenLink** | 오픈채팅 이름 | `linkId`, `linkName` |

어떻게 채워지나요?

1. 맥에서 카카오톡에 로그인합니다.
2. 봇이 감시할 채팅방을 **실제로 엽니다.** (한 번도 안 연 방은 로컬 DB에 없거나 비어 있을 수 있습니다.)
3. 터미널에 **전체 디스크 접근 권한**을 줍니다.
4. 아래 명령으로 방이 보이는지 확인합니다.

```bash
./target/release/openkakao-cli doctor
./target/release/openkakao-cli local-chats
./target/release/openkakao-cli local-read <채팅방ID>
```

`local-chats`에 원하는 방이 안 보이면, 카카오톡에서 그 방을 연 다음 다시 시도하세요.
테이블을 엑셀처럼 직접 만들 필요는 없습니다. **카카오톡 앱이 만듭니다.**

메시지 종류(`NTChatMessage.type`)는 숫자입니다. 텍스트는 보통 `1`, 사진은 `2` 근처입니다. 편집/시스템 제어 행은 0 이하인 경우가 있어 자동 답장 후보에서 빼 둡니다.

### 2) 있으면 좋은 것: Cache.db

```text
~/Library/Containers/com.kakao.KakaoTalkMac/Data/Library/Caches/Cache.db
```

카카오톡이 남긴 HTTP 캐시입니다. 대화 테이블이 아닙니다.
일부 진단·로그인 보조에 쓰이며, **자동 답장의 필수 조건은 로컬 대화 DB**입니다.

### 3) 프로그램이 나중에 만드는 DB (복사 금지)

처음 실행하면 내 Mac에만 생깁니다. GitHub에 올리지 마세요.

**벡터/기억 DB** — `~/Library/Application Support/openkakao/context.sqlite3`

| 테이블 | 역할 |
|--------|------|
| `context_messages` | 대화 조각 + `vector` 임베딩 |
| `context_messages_fts` | 키워드 검색용 전문 검색 |
| `context_live_events` | 로컬 DB에서 들어온 실시간 이벤트 |
| `context_sources` | 이 기억이 어느 로컬 DB에서 왔는지 |
| `context_reference_packs` | 긴 설명/강의형 내용을 정리한 참고 팩 |
| `context_operator_prompts` | 메뉴바에서 고치는 운영자 프롬프트 |
| `context_retrieval_meta` | 검색 인덱스 버전 정보 |
| `context_message_topics` / `context_topic_stats` | 주제 태그 |
| `owner_style` / `owner_style_profile` | 내 말투 샘플과 요약 |
| `owner_recipient_style_samples` / `owner_recipient_style_profile` | 상대별 말투 |
| `response_time_stats` / `response_time_samples` | 내가 평소 얼마나 빨리 답하는지 |
| `reply_decisions` | 이전에 답할지 말지 판단한 기록 |
| `memory_note` | 짧은 운영 메모 |

**자동 답장 큐** — `~/Library/Application Support/openkakao/auto-reply/`

| 파일/테이블 | 역할 |
|-------------|------|
| `reply-queue.sqlite3` → `reply_jobs` | 보낼 답장 작업 |
| `reply_job_tombstones` / `reply_job_supersessions` | 끝난 작업·대체된 작업 |
| `pipeline_transitions` | 처리 단계 일지 |
| `model-circuit.sqlite3` → `model_circuit_breaker` | 모델 장애 시 잠시 멈추는 회로 |

**지식 그래프** — `~/Library/Application Support/openkakao/bujamentor/knowledge-graph.sqlite3`

채팅방·참여자·주제 엔티티와 관계를 담습니다. 메뉴바 드릴다운과 답장 GraphRAG가 이 파일을 읽습니다. GitHub에 올리지 마세요.

이 파일들은 실행하면 자동으로 만들어집니다. **백업이 필요하면 내 디스크에서만** 하세요.

---

## 설치와 첫 실행

### 1. 소스 빌드

```bash
cd openkakao-bot
cargo build --release
./target/release/openkakao-cli --help
```

### 2. 권한

macOS **시스템 설정 → 개인정보 보호 및 보안**에서:

1. **전체 디스크 접근 권한** — 터미널(또는 메뉴바 앱 `Alden`) 허용
2. **손쉬운 사용** — 답을 카카오톡에 입력하려면 허용

카카오톡은 **실행 중**이어야 합니다.

### 3. 로컬 DB가 보이는지 확인

```bash
./target/release/openkakao-cli doctor
./target/release/openkakao-cli local-chats
```

방이 보이면 `chat_id`를 적어 둡니다. 설정 파일에서 이 번호로 방을 지정합니다.

### 4. 설정 파일

```bash
mkdir -p ~/.config/openkakao
cp config.example.toml ~/.config/openkakao/config.toml
```

`config.toml`에서 최소한 아래를 채웁니다.

```toml
[model]
privacy_mode = "local"
allow_egress = false
provider = "mlx-serve"

[auto_reply]
# 예시: 채팅방 ID와 화면에 보이는 정확한 방 이름
# chats = ["bind:123456789012345:채팅방이름"]
self_nickname = "카카오톡에 보이는 내 닉네임"
python_interpreter = "/opt/homebrew/opt/python@3.13/bin/python3.13"
reply_runner = "/opt/homebrew/bin/opencodex"
reply_runner_kind = "opencodex"
reply_model = "ddalcu/Qwen3.8-27B-MLX-Serve-4bit"
```

- `chats`에 없는 방에는 답을 보내지 않습니다.
- `self_nickname`은 **내가 보낸 메시지**를 구분하는 데 씁니다. 카카오톡에 보이는 이름과 같아야 합니다.
- 운영 자동 답장 워커와 메뉴바의 텍스트·이미지 생성은 로컬 MLX만 허용합니다. 현재 운영 기본 모델은 Qwen3.8 27B입니다. Flash-Next는 상주·준비 상태를 확인한 뒤 명시적으로 선택할 수 있습니다. Gemini와 기타 클라우드 모델은 주 모델이나 대체 모델로 거부되며 자동 선택되지 않습니다.
- 게시된 URL 원문 수집은 별도의 명시적 네트워크 작업입니다. 이를 허용해도 클라우드 LLM 실행이나 모델·이미지 외부 전송이 허용되지는 않습니다.
- 링크 원문 수집과 로컬 이미지 분석 옵션은 기본이 꺼져 있습니다. 필요할 때만 켜세요.

### 5. 메뉴바로 켜기 (초보자에게 추천)

```bash
sh scripts/build-alden-desktop.sh
sh scripts/install-alden-desktop.sh
sh scripts/start-auto-reply-menubar.command
```

`/Applications/Alden.app`이 메뉴바 앱으로 실행되며, 패널에는 골드 코어만 보입니다. 방 선택, 쉬운 답변 모드, 음성 상태, 대화 검색, 최근 답변은 메뉴바 트레이 아이콘을 우클릭해 여는 960×880 설정 창에서 다룹니다. 음성 시작은 호출어 모델 출시 기준을 통과할 때까지 비활성화됩니다. 넓은 화면은 황금비에 가까운 61.8:38.2 두 열과 16px 간격을 쓰고, 보조 열은 최소 300px로 유지합니다. 800px 아래에서는 한 열로 바뀝니다. 어디서든 **⌘⌥⇧Esc**를 눌러 전체 백그라운드 작업을 긴급 중단할 수 있습니다. 기술 진단·권한·대량 점검 화면은 두지 않습니다. 기존 Swift Extra는 설치 시 백업 후 비활성화됩니다.

현재 로컬 운영 설정은 MLX Qwen3.8 27B입니다. Flash-Next는 메모리와 상주 상태가 확인될 때 명시적으로 선택할 수 있습니다. 모델 상태를 확인하지 못하면 요청을 중단합니다. 권장 ID는 완료된 실생성을 뜻하지 않습니다.

## 소스 준비 상태 (2026-09-27)

**2026-09-27 KST에 Alden(올든) 0.1.5를 다시 빌드해 로컬에 설치했습니다.** 설치 번들은 빌드 산출물과 바이트 단위로 일치하고 Alden 프로세스는 1개입니다. 설치본의 백엔드는 세 방 모두 답변 준비 상태로 보고했습니다. 새 불변 워커 런타임의 자산 20개는 패키지 해시와 일치했고, 실행 중인 워치독도 그 런타임을 사용합니다. 후속 호스트 조회에서 **3/3 방 준비**, DB 누락 대기 0건, 진행 중 후보 0건을 확인했습니다. [설치 근거와 남은 확인 항목](docs/architecture/alden-install-20260925.md)을 참고하세요. 실제 카카오톡 답변 지연과 전송 성공률을 측정한 결과는 아닙니다.

가상 데이터로 실행한 Chromium 렌더 검사 **32/32개**가 통과했습니다. 설정 창 너비 960px에서 두 열은 실측 **553.719px·342.266px**이고 문서의 가로 넘침은 없었습니다. 창 숨김 신호를 모의한 1.503초 동안 렌더 프레임은 **0개**였습니다. 이 결과는 모의 Tauri 연결을 쓴 프런트엔드 검사이며, 설치된 메뉴바 창의 GPU 사용량 측정은 아닙니다.

Flash-Next는 재시작 당시 약 **77.1 GB**가 필요한 메모리 점검에서 가용 **51.8–54.4 GB**로 적재가 거부됐습니다. 현재 로컬 운영 모델은 Qwen3.8 27B이며, 모델 목록에서 **18,196,477,654바이트 상주**로 확인했습니다. 합성 localhost 추론 한 건은 HTTP 200, 생성 1토큰, **0.553초**였습니다. MLX 관리자의 다음 정기 실행도 종료 코드 0으로 마쳤습니다. 실제 대화 지연·음성 파이프라인·공개 릴리스 검증은 남아 있습니다. 깨끗한 체크아웃에서 메뉴바 번들을 재현하는 데 필요한 바이트코드 3개의 유지보수 가능한 원본도 아직 없습니다.

호출어 모델이 출시 기준을 통과할 때까지 음성 시작을 차단합니다. 실험용 모델은 합성 양성 3/3을 감지했지만, 합성 음성 대조군 3개 중 1개를 호출어로 잘못 받아들였습니다(임계값 0.65, 오탐률 33.333%). 실제 화자나 마이크·공간 녹음 검증은 없습니다. 후보 모델은 앱 번들에서 제외되어 있습니다. 이 소스·평가 결과가 실제 마이크나 전체 음성 대화의 정상 동작을 입증하지는 않습니다.

### 과거 런타임 관측 (2026-09-24~25)

2026-09-24 23:43 KST 읽기 전용 확인에서 로컬 MLX `/health`, `/v1/models`가 HTTP 200을 반환했습니다. Flash-Next는 적재 상태(상주 75.3 GB)이고 Qwen3.8 27B와 Qwen3-TTS는 내려가 있습니다. Flash-Next에 로컬 진단 생성을 한 번 요청해 `OK`를 **56.849초** 만에 받았습니다. 짧은 단일 생성은 확인했지만 일반 대화의 보통 응답 속도까지 입증한 것은 아닙니다. 앞서 45.060초 연결 종료·출력 0토큰 실패도 별도 기록으로 남아 있습니다. 적재 모델은 임베딩을 제공하지 않아 라이브 GraphRAG는 BM25만 사용하며 dense/RRF는 사용할 수 없습니다.

생성 확인 직후의 관측에서는 자동 답변 세션 감시기가 정상 상태였고 3개 방 모두 준비됨, 모델 사용 가능, 전송 활성, 대기 누락 없음으로 읽혔습니다. 당시 남은 스왑은 **1,322.19 MiB**로 음성 모델 적재 기준 2,048 MiB보다 **725.81 MiB** 부족했습니다. 관측한 음성 heartbeat는 25시간 넘게 갱신되지 않아 이 확인으로 Whisper/TTS 적재와 호출어→STT→LLM→TTS 전체 대화를 검증하지 못했습니다. 설치된 불변 런타임의 worker SHA-256은 당시 소스 스냅샷과 일치했으며, 이후 수정분까지 입증하는 비교는 아닙니다. 진단 생성은 대화 데이터를 쓰지 않고 로컬에서 실행했으며 메시지 발송이나 모델 적재·교체는 하지 않았습니다. 최근 큐 집계, 혼란 응답의 과거 사례 재생, 측정 한계는 [engineering status](docs/engineering-status.md)에 기록합니다.

2026-09-24 23:36 KST에 Tauri 앱 v0.1.5를 설치했고 LaunchAgent 프로세스가 실행 중임을 확인했습니다. 설치된 번들 27개 파일은 당시 빌드 산출물과 모두 일치했습니다. 설정된 글로벌 긴급 중단은 **⌘⌥⇧Esc**이며, **⌘⌥Esc**는 macOS 강제 종료에 예약되어 있습니다. 이 번들 확인에서는 실제 글로벌 단축키 입력을 실행하지 않았습니다.

2026-09-25 00:28 KST의 후속 읽기 전용 호스트 확인은 `healthy=false`였습니다. 당시 세 방 중 두 워커는 준비 상태이고 한 워커는 일시적인 컨텍스트 동기화 실패로 전송이 차단되어 있었습니다. 주 자동 답장 방은 준비됨·전송 활성·대기 누락 없음으로 확인됐습니다. 이 관측은 현재 소스 수정 이전의 기록으로, 수정분의 배포를 입증하지 않습니다.

무인 실행(launchd)은 `docs/auto-reply-launchd-supervision.md`와 `scripts/install-auto-reply-launchd.sh`를 보세요. 처음이면 메뉴바부터 시작하는 편이 안전합니다.

---

## Alden Tauri와 지식 그래프

Tauri 메뉴바 창은 골드 홀로그램 코어만 두며 인터랙티브 요소는 없습니다. 설정은 960×880 창에서 채팅방·답변 선택, 음성·대화 상태, 대화 찾기·최근 답변을 황금비에 가까운 61.8:38.2 비율과 16px 간격으로 나란히 배치하고, 보조 열은 최소 300px로 유지합니다. 800px 아래 화면에서는 한 열로 전환합니다. 전문 용어와 모델 식별자는 숨기고, 모델 전환 안전 확인은 사용자가 선택했을 때 내부에서 수행합니다. 첫 화면 진입은 기존 7개 병렬 호출에서 스냅샷과 검색 자료를 포함한 3개로 줄었습니다(57.1%).

음성은 현재 체크아웃 소스에서 시작할 수 없습니다. 호출어 모델 출시 기준 미달로 설정의 시작 컨트롤은 비활성화되어 있고 Rust 브리지는 음성 세션 시작을 거부합니다. 소스에는 최근 4회 대화, 메시지당 최대 600자, 10분 유휴 초기화 제한이 있지만 마이크·STT·모델·TTS 전체 흐름은 검증되지 않았습니다. 오래된 하트비트를 실시간 청취로 표시하지 않습니다.

카카오톡 DB 색인은 임시 복사본을 `mode=ro`와 `PRAGMA query_only`로만 엽니다. 복사에 실패하면 원본을 열지 않습니다.

노드 클릭은 `knowledge-graph-focus`만 호출하고, 이미 있는 `knowledge-graph.sqlite3`에서 k-hop `2/3/10` 번들을 읽습니다. 클릭 경로에는 원본 복사와 재색인이 없습니다. 조회 실패 시 `facts`는 빈 배열이고 `관련 사실·관계`만 갱신합니다.

현재 로컬 모델은 임베딩을 제공하지 않아 검색이 BM25 모드로 제한됩니다. dense/RRF는 실제 ready 상태의 로컬 임베딩 모델이 확인된 뒤에만 사용할 수 있습니다.

자동 답장 워커는 문장부호만 있는 후속 입력(`???`)과 짧은 지시어형 질문(`뭐지 저건`, `이게 뭐야`)을 직전 대화의 참조로 처리합니다. 최근 발화가 있으면 그 발화의 근거 ID를 보존한 한 문장 확인 질문을 만들며, 일반 혼란 문구로 대체하지 않습니다. MLX 모델이 `json_schema` 기능을 광고하면 생성 요청에 엄격한 출력 스키마를 넣고, 그래도 JSON을 읽지 못한 이벤트는 최대 한 번만 재시도합니다. 이 변경은 소스 기준이며, 실행 중인 자동 답장 런타임 적용은 별도 읽기 검증 전까지 보장하지 않습니다.

다이어그램 본문(노드·카드·레이블)은 한국어로 작성했습니다. Archify Viewer UI와 `<html lang>`은 영어 폴백입니다. 이 HTML은 로컬 showcase validate / deliver / visual-check를 통과한 산출물이며, 지각적 AHP나 설치된 앱 재빌드를 증명하지 않습니다.

- [Tauri cutover 아키텍처](docs/architecture/alden-openkakao-units1-4.html)
- [Alden Three.js 렌더 생명주기](docs/architecture/alden-three-render-lifecycle.html)
- [GraphRAG 드릴다운 시퀀스](docs/architecture/openkakao-graphrag.html)

---
## 폴더 안내

| 경로 | 내용 |
|------|------|
| `src/` | Rust 코어. 로컬 DB 읽기, 전송, 자동 답장 호스트 |
| `scripts/` | 파이썬 워커, DB 감시, 메뉴바, 설치 스크립트 |
| `desktop/` | Tauri v2 + Three.js 메뉴바 앱과 Rust bridge |
| `macos/AutoReplyMenu/` | legacy Swift Extra (명시적 opt-in 경로) |
| `tests/` | 동작이 깨지지 않는지 확인하는 테스트 |
| `docs/` | 운영 메모와 Archify 다이어그램 (`docs/architecture/`) |
| `config.example.toml` | 설정 예시. 이걸 복사해 씁니다 |
| `examples/launchd/` | macOS 백그라운드 실행 예시 |

---

## 자주 막히는 곳

**`doctor`가 로컬 DB를 못 연다**
카카오톡을 한 번 실행하고 원하는 방을 연 다음, 터미널에 전체 디스크 접근 권한을 다시 확인하세요.

**채팅방 이름이 비어 있다**
그룹방 이름이 DB에 없는 경우가 있습니다. 그때는 `bind:<채팅방ID>:<화면에 보이는 정확한 이름>` 형식으로 적습니다.

**답장이 안 나간다**
손쉬운 사용 권한, 카카오톡이 앞에 있는지, `chats`에 그 방이 있는지, 메뉴바/슈퍼바이저가 켜져 있는지 확인하세요.

**벡터 검색이 비어 있다**
정상입니다. 대화가 아직 동기화되지 않은 것입니다. 카카오톡 DB를 이 저장소에 넣을 필요는 없고, 에이전트를 켜 두면 `context.sqlite3`가 내 Mac에 만들어집니다.

**그래프를 눌렀는데 관련 사실·관계가 비어 있다**
클릭은 이미 있는 `knowledge-graph.sqlite3`만 읽습니다. 재색인하거나 카카오톡 원본을 복사하지 않습니다. 조회가 실패하면 `facts`는 빈 배열입니다.

**로컬 생성이 안 된다**
현재 온디바이스 기본 모델은 MLX Qwen3.8 27B입니다. 모델 준비 상태 확인이 실패하거나 시간 초과하면 요청을 중단합니다. 27B의 단일 합성 생성은 확인했지만 실제 대화 지연은 아직 측정하지 않았습니다.

---

## 개발용 명령

```bash
cargo test
cargo build --release
./target/release/openkakao-cli doctor --json
```

운영 메모는 `docs/auto-reply-launchd-supervision.md`를 참고하세요.

---

## 라이선스

MIT. 자세한 내용은 `LICENSE`를 보세요.

카카오톡은 Kakao의 제품입니다. 이 프로젝트는 비공식 도구이며, 내 맥에 설치된 앱과 내가 속한 채팅방에서만 사용하세요.
