# 올든 설정과 OSK 지식 관리

올든 0.1.7의 설정은 지식 그래프를 처음 표시한다. 기본 창은 1200×760, 최소 창은 640×680이다. 사이드바 오른쪽 전체가 그래프 작업 영역이며, 노드를 선택하면 출처와 관계를 작은 상세 영역에서 읽는다. 이전 탐색, 전체 보기, 주변 확장과 키보드 탐색을 유지한다. 대화·AI, 음성 대화, 최근 답변은 사이드바에서 연다.

## 디자인

직접 확인한 [Style Gallery Fieldwork](https://style.gallery/components/21st-49e34572c05d/)의 고정 내비게이션, 밝은 린넨 배경, 세이지 색상과 얇은 구분선을 적용했다. 어두운 모드는 차콜과 세이지를 사용한다. 실제 창의 크기에 맞춰 그래프를 배치하며, 겹치는 라벨은 제한된 화면에서 숨기고 모든 표시 노드는 접근성 버튼으로 탐색할 수 있다. 설정 그래프는 앱의 세이지 토큰을 상속하며 메인 코어의 기존 재질과 분리된다.

## 실제 OSK 통합

[osk-system v4.1.2](https://github.com/lpaiu-cs/osk-system/releases/tag/v4.1.2), commit `9bbf08febc5a1fb2af068006735ed79cbdb71178`의 공개 Python 엔진을 그대로 묶었다. `scripts/vendor/osk-v4.1.2.json`은 아카이브와 모든 파일의 해시를 고정한다. 라이선스와 변경하지 않은 PyYAML 6.0.3 순수 Python 소스를 포함하여 기존 CPython 3.11에서 네트워크 설치 없이 실행한다.

앱이 시작되면 자신의 작업 스레드에서 동기화를 실행하고, 실행 종료 후 60초 간격으로 다시 확인한다. 기존 대화 지식 인덱스의 증분 변경을 OSK `create_node`/`update_node` 계약으로 검증하여 개인 Markdown 저장소에 기록한다. 틱당 최대 32개 쓰기이며 노드마다 체크포인트를 저장한다. 그래프와 선택한 노드의 내용은 OSK `Index`와 `contract`로 다시 읽는다. 사용자 작성 일반 OSK 노트의 링크도 그래프에 반영한다. 기존 GraphRAG 출처 ID와 의미 있는 관계 유형을 유지하고, 일반 관계를 OSK의 권한 관계로 잘못 변환하지 않는다.

실행 저장소는 기존 enrollment를 가진 state root를 그대로 사용한다. 파일은 `$STATE_ROOT/knowledge/osk/vault/`, 진행 기록은 `knowledge/osk/sync.json`, 수정 전 원본은 `knowledge/osk/history/`에 저장된다. 개인 자료는 Git 또는 릴리즈에 포함하지 않는다. 원본 카카오톡 DB는 읽기 전용이며 OSK가 원본 메시지나 전송 큐를 수정하지 않는다.

자동 동기화는 로컬 작업이다. 추가 LaunchAgent, MCP 서버, 전역 에이전트 훅, 원격 Git 동기화 또는 클라우드 추론을 등록하지 않는다. 앱이 종료되면 이 스레드도 끝나며, 비상 중단 latch가 설정되면 다음 쓰기를 막는다. 기존 인덱서의 갱신 주기는 별도이므로 60초마다 새 카카오톡 메시지의 모든 추출이 완료된다는 보장은 아니다.

설정이 보이는 동안 15초마다 로컬 그래프를 확인한다. 숨김 또는 다른 페이지에서는 읽기 타이머와 렌더 루프가 멈춘다. 늦은 응답은 visibility epoch로 폐기하고 한 번에 조회 하나만 실행한다. 자료 갱신 시간, 가져오기 진행과 편집 충돌을 짧은 한국어로 표시한다.

## 편집과 복구

OSK 규격을 따르는 일반 Markdown 노트는 직접 작성할 수 있다. 자동으로 가져온 파일을 사람이 수정하거나 삭제하면 해시가 달라진다. 동기화는 그 항목을 보류하며 사람의 변경을 덮어쓰지 않는다. 충돌을 해결하려면 현재 파일을 별도로 보관한 뒤 `history`에서 마지막 자동 관리본을 복원한다. 다음 틱이 일치 여부를 확인한다. 임의로 체크포인트를 지워 재가져오기하지 않는다.

원본 지식 삭제는 새 인덱스가 확인된 경우에만 표시에서 철회한다. Markdown 파일은 복구할 수 있게 남긴다. 중단 또는 오래된 인덱스에서는 삭제 반영을 보류한다. 실패한 노드나 중단된 생성은 다음 틱의 파일·본문 대조로 복구한다. 전체 동기화에는 파일 잠금을 사용한다.

## 검증 범위

실제 기존 지식 50개를 OSK로 가져오고 `pending=0`, `conflicts=0`을 확인했다. 같은 자료의 다음 확인은 `changed=0`, `stale=false`였다. 이는 날짜가 있는 로컬 readback이며 실제 신규 메시지의 지속적인 검색 품질을 측정한 결과는 아니다.

UI 209개, OSK 실제 엔진 8개, desktop Rust 90개, 기존 메뉴바 회귀 177개가 통과했다. 필수 Python 전체 1,064개 중 기존 설정 제목 기대값 1개가 실패하여 새 제목에 맞췄고, 해당 모듈 33개가 재검사에 통과했다. 나머지 전체 검사는 통과 또는 26개 skip이었다. TypeScript/Vite, native build와 Clippy도 통과했다. 네이티브 감사는 실제 persisted graph의 별도 WKWebView에서 탐색·숨김·복원을 검사하며 다른 설정 backend와 focus 조회는 unavailable이다. 설치된 상시 앱의 자동 동기화가 여러 주기에 걸쳐 진행됐고, 설치된 OSK 조회는 50개 노드·pending 0·충돌 0을 반환했다. 설치된 focus helper는 선택한 지식의 사실 5개를 반환했다. 신규 메시지의 검색 품질·지연 개선율은 미측정이다.

[OSK 데이터 흐름](alden-osk-knowledge.html), [Archify 소스](alden-osk-knowledge.dataflow.json), [검증 receipt](alden-osk-knowledge-receipt.json). 다이어그램은 9/9 showcase, 4개 desktop viewport containment와 두 테마 endpoint 이미지 검토를 통과했다. 지식 그래프의 개인 스크린샷은 로컬에만 보관한다.

현재 변경은 설정과 지식 관리의 전달 범위다. 전체 목표의 사람 음성·웨이크워드, 기존 production worker 교체, Apple 서명·공증과 production 릴리즈 제한은 별도로 유지한다.

## 설치와 전달 readback

소스 `51a9cc08e9710667ee609853b2216f21d0e65772`로 빌드한 0.1.7을 `/Applications/Alden.app`에 설치했다. 최종 리소스 전체를 포함한 로컬 ad-hoc 서명의 deep/strict 검증이 통과했고, 설치·빌드·ZIP의 33개 파일이 모두 일치했다. 첫 설치는 linker 서명만 있어 전체 리소스 검증에 실패했으며, 올바르게 재서명한 패키지를 다시 설치하여 해결했다. 이전 앱과 LaunchAgent의 자동 백업을 보존했다.

[소스 CI](https://github.com/twoimo/openkakao-bot/actions/runs/36855790019) 4/4 성공과 PR의 GitGuardian 검사를 확인했다. 기존 모델 서버와 카카오톡 작업자의 process identity, 공유 설정·enrollment·CLI 해시는 유지됐다. 기존 음성 정책의 미완료 편집은 별도 worktree에 보존하고 이번 패키지에서 제외했다.

설치 바이너리의 별도 WKWebView 감사에서도 기본 1200px·최소 640px와 이전 탐색·확장·전체 보기·숨김350ms 렌더0·복원이 통과했다. 이 창은 실제 persisted SQLite 그래프를 읽는 고립된 감사이며 다른 backend/focus는 unavailable이다. 상시 앱의 화면 조작은 컴퓨터 제어 도구에서 시간 초과가 반복되어 미검증이다. 다른 제어 기술로 우회하지 않았다. [설치·자동 갱신·범위 receipt](alden-settings-osk-verification-20261001.json).

후보 ZIP, CPython sidecar, 정확한 소스 overlay, OSK 다이어그램, manifest, 숫자 verification, SHA256SUMS의 7개 파일을 GitHub 초안에 전달한다. private vault와 그래프 화면은 공개 산출물에서 제외한다. 공개 Developer ID 서명·Apple 공증·production 릴리즈는 여전히 차단돼 있다.
