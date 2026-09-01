# openkakao-bot

macOS 카카오톡에서 **지정한 채팅방만** 읽고, 내 말투에 가깝게 **자동 답장**을 보내는 에이전트입니다.

이 저장소에는 **프로그램 소스만** 들어 있습니다.
내 카카오톡 대화, 로컬 DB, 벡터 DB, 로그인 정보는 **절대 올리지 않습니다.**
그런 데이터는 모두 **내 Mac 안의 원래 위치**에 그대로 둡니다.

> 실행 파일 이름은 기존과 같이 `openkakao-cli` 입니다.
> GitHub 저장소 이름만 `openkakao-bot` 입니다.

---

## 이 프로그램이 하는 일

1. 맥용 카카오톡이 이미 저장해 둔 **로컬 대화 DB**를 읽기 전용으로 봅니다.
2. 새 메시지가 오면 답장 후보인지 판단합니다.
3. 예전 대화와 내 말투를 참고해 답 초안을 만듭니다.
4. 카카오톡 입력창에 대신 입력해 보냅니다. (접근성 API)
5. 메뉴바에서 켜고 끄고, 방을 고를 수 있습니다.

카카오 서버에 별도로 로그인해서 메시지를 빼 오는 봇이 **아닙니다.**
**맥에 설치된 카카오톡 앱**이 있어야 하고, 그 앱이 만든 로컬 DB를 읽습니다.

```text
카카오톡 맥 앱
    │  (대화가 로컬 DB에 저장됨)
    ▼
openkakao-cli 가 DB를 읽음  ──►  벡터 기억(내 Mac에만 생성)
    │
    ▼
자동 답장 워커 + LLM
    │
    ▼
카카오톡 입력창에 전송 (손쉬운 사용 권한 필요)
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
| 자동 답장 상태 | `~/Library/Application Support/openkakao/auto-reply/` |
| 로그인 정보 | `~/.config/openkakao/credentials.json` |

벡터 DB는 처음 동기화할 때 **프로그램이 알아서 만듭니다.** 미리 복사해 올 필요가 없습니다.

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

1. **전체 디스크 접근 권한** — 터미널(또는 메뉴바 앱 `AutoReplyMenu`) 허용
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
[auto_reply]
# 예시: 채팅방 ID와 화면에 보이는 정확한 방 이름
# chats = ["bind:123456789012345:채팅방이름"]
self_nickname = "카카오톡에 보이는 내 닉네임"
python_interpreter = "/opt/homebrew/opt/python@3.13/bin/python3.13"
# reply_runner = "/Users/나/.local/lib/openkakao/gjc.js"
# reply_runner_kind = "gjc"
# reply_model = "사용할 모델 이름"
```

- `chats`에 없는 방에는 답을 보내지 않습니다.
- `self_nickname`은 **내가 보낸 메시지**를 구분하는 데 씁니다. 카카오톡에 보이는 이름과 같아야 합니다.
- 링크를 열어보거나 사진을 모델에 보내는 옵션은 기본이 꺼져 있습니다. 필요할 때만 켜세요.

### 5. 메뉴바로 켜기 (초보자에게 추천)

```bash
sh scripts/build-auto-reply-menubar.sh
```

만들어진 `AutoReplyMenu.app`을 실행하면 메뉴바에서 방 선택, 모델, 시작/중지를 다룰 수 있습니다.

무인 실행(launchd)은 `docs/auto-reply-launchd-supervision.md`와 `scripts/install-auto-reply-launchd.sh`를 보세요. 처음이면 메뉴바부터 시작하는 편이 안전합니다.

---

## 폴더 안내

| 경로 | 내용 |
|------|------|
| `src/` | Rust 코어. 로컬 DB 읽기, 전송, 자동 답장 호스트 |
| `scripts/` | 파이썬 워커, DB 감시, 메뉴바, 설치 스크립트 |
| `macos/AutoReplyMenu/` | 메뉴바 앱 (Swift) |
| `tests/` | 동작이 깨지지 않는지 확인하는 테스트 |
| `docs/` | 운영·개선 메모 |
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
