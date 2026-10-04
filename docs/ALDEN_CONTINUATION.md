# Alden 작업 이어받기 — 2026-10-03 KST

전체 목표는 **진행 중**이다. 현재 목표 원문은
`/Users/twoimo/.codex/attachments/0eef40c0-ffdb-45f1-93cd-e9497437da8a/goal-objective.md`이며,
이전 채팅 `01a0f115-35c9-7df2-95d8-561697e115eb`의 작업을 이어받는다.

## 복구와 현재 상태

- 기존 작업 폴더 `alden-settings-wide`는 삭제되어 있다. 보관된 Git 스냅샷
  `c9cd870`과 원격 PR27의 `63267e9`를 비교해 이전 세션의 변경 9개를 복구했다.
- 현재 작업 폴더: `/Users/twoimo/.codex/worktrees/a0c2/openkakao-bot`.
  분기: `codex/alden-continuation-20261003`. 관련 없는 변경은 없었다.
- 장치: Apple M5 Max, 통합 메모리 128GB, macOS 26.6.2. 전원 연결 상태.
- 설치본은 이미 Alden 0.3.10이다. 상주 27B PID58780, 전용 E5 PID11016,
  기존 카카오 세션과 음성 PID25865를 발견했다. 실행 중인 작업은 이어받기에서 재시작하지 않았다.
- 이전 수정의 Python 필수 검사 1149개/28 skip 및 추가 집중 검사 29개는 통과했다.
  경로가 사라진 이전 빌드 자체를 현재 checkout의 빌드라고 보고하지 않는다.
- 현재 Computer Use의 `getApp('Alden')`도 `timeoutReached(-10005)`로 실패했다.
  별도 네이티브 검사, 개발 화면, 상시 설치본의 직접 조작은 다른 검증 범위다.

## 단계

| 항목 | 단계 | 남은 작업 |
| --- | --- | --- |
| 맥락·턴·출처·취소 | 실제 실행 검증 | 자연 발화·물리 끼어들기·에코 |
| Tauri v2·Three.js·Alden | 전달 완료 | 최신 0.3.10 원격 산출물 대조 |
| 정확한 로컬 모델 | 실제 실행 검증 | Flash-Next 메모리 admission, 운영 이미지 경로 |
| 음성·자동화·비상 중단 | 구현/부분 실제 실행 검증 | 배포 한국어 wake, 물리 마이크·재생·단축키 |
| 디자인·오브·노드 근거 | 전달 완료 | 0.3.10 설치본·현재 primary/Retina |
| 렌더·전력·메모리 | 구현/부분 실제 실행 검증 | E5 수정 전달, 실제 UI/전력·지연 |
| GraphRAG·DB | 실제 실행 검증 | 원본 앱 데이터 접근 승인·최신 수집 |
| DREAM-RSI·평가·DPO | 구현/부분 실제 실행 검증 | 독립 최종 test·제품 품질 평가·promotion |
| 성능 | 부분 실제 실행 검증 | 재현 가능한 E5 비교·tokens/s·제품 음성 p95 |
| 문서·도식 | 전달 완료 | 이번 변경·수치·범위를 갱신 |
| commit·push·release·설치·production | 구현 | 정상 push/CI·산출물·자격 증명·안전한 전환 |

과거 상세 근거는 [목표별 이력](architecture/alden-goal-coverage-20260930.md),
[오브와 노드 설명](architecture/alden-orbs-20261003.md),
[전달 이력](ALDEN_DELIVERY.md)을 사용한다. 지나간 판본의 성공을 현재 판본의 성공으로 대체하지 않는다.

## 다음 작업

1. 완료: E5 변형별 새 프로세스3회·44요청/165벡터, 동일 출력, 14.453→1.517GB·89.50% 감소.
   전체 요청 시간 0.521→0.726초·39.29% 증가도 공개했다. 처리 시간10% 목표는 미달이다.
2. 완료: 복구 checkout의 release build·0.3.10 재설치·35파일·ad-hoc deep/strict·기존5프로세스 보존.
   재설치 후 별도 native 팝업10회/설정 검사 통과, 숨김 추가 렌더0.
3. 완료: UI231·desktop Rust94·CLI Rust1150/1ignored·Clippy.
   필수 Python1181/28skip의 첫 실행은 설치 fixture timeout1건, 해당 단독 재실행 통과.
4. 완료: 메모리 측정 스크립트·32집중 검사·측정 원자료·Archify9checks와 실제 Chrome/이미지 검토.
5. 다음: 0.3.10 commit/push·정확 SHA CI·릴리즈 자산/다운로드/설치 판본 대조.
6. 모델·음성·운영 적용의 남은 독립 작업을 계속하고 준비된 권한/서명 차단만 구체적으로 요청한다.

기존 공유 모델 설정·설정 파일·enrollment·전송 큐를 보존한다. 확인되지 않은 외부 쓰기는 재시도 전에 읽어 확인한다.

## 추가된 사용자 범위 — 2026-10-03

- 사이드바5페이지의 사용 경험·컨셉을 검토하고 실제 앱에서 수정한다. 불필요한 여백·간격·문구를 줄이고 정보 밀도를 높인다.
- OSK v4.1.2 실제 엔진·설치 리소스 해시 일치 확인. 현재 sync managed151, pending0/conflicts0. 브라우저는 synthetic 개발 예시다.
- MCP의 직접 API 대비 시작/호출/메모리/토큰 비용을 실측하고 적절한 경로를 구현한다.
- twoimoui-MacBookPro.local은 현재 Mac이다. Alden0.3.10과 ChatGPT26.930.21537 실행 중. Alden-dot 연동을 구현·검증한다.
- ChatGPT 앱 자체의 CUA 조작은 도구 안전 정책으로 차단됐다. 우회하지 않고 공식 connected-computer/plugin 경로를 준비한다.
- 자기 평가와 수학적 성능 최적화: 비교 조건·식·표본·미달을 공개한다. 세계 최고라는 검증되지 않은 순위를 주장하지 않는다.
- 세션 OSK 포착은 착지 미정으로 저장되지 않았고 검토 대기/계수는 유지된다. 사용자가 한 번 안내받았다. 제품 vault와 세션 포착 대상을 혼동하지 않는다.

현재 UI 수정: 공통 잉크색 토큰, 중복 topbar 제거, 좁은 sidebar/간격, 최근 기록 자동 선택, 빈/실패 상태, 음성 페이지에 마이크 동선, DB→기억 정리, 저장 결과 표시, 숨김 상태 보존.
수정 후 build 통과. UI231 첫 재검사에서 텍스트 기대값2건만 실패했고 기대값 수정 중. Label overlay 경계는 resize에서 캐시한다.
브라우저 screenshot 일부가 잘리거나 blank여서 거부했다. 현재 app에 적용 완료/시각 검증 완료라고 보고하지 않는다.

0.3.10 전달은 완료: 6e7022c·CI37099249366 4/4, 설치35경로·release6개 다운로드/ZIP대조.
릴리즈초안 https://github.com/twoimo/openkakao-bot/releases/tag/untagged-19d2ec0cadd04f2cfb13 .
LLM actual12회 사실12/12·오류0, TTFTp50 .194794/p95 3.530862초, 답변p50 .361727/p95 3.688151초, 관측decode p50 60.673454 tokens/s.
같은 설치 voice adapter SHA756f8ab7; socket11234 only. 첫 요청 tail이 목표미달이며 모델 변경/전후개선으로 계산하지 않는다.
측정추가 code/scripts/measure_alden_voice_llm.py·tests2·CI모듈은 아직 작업변경이다.

다음: UI 기능회귀·5페이지/최소창 실제 캡처 → 0.3.11 native 설치/CI/전달, MCP benchmark와 local bridge, Dot 최종연결/확인.

협업 승인: 사용자가 ‘공유하고 작업을 조정해줘’라고 답했다. durable task01a1006a-47ce-7609-bec2-7af9dc651098(‘Alden 연동과 성능 최적화’)에 상태·분담을 전송했다.
중앙작업은 UI/native0.3.11/릴리즈, 해당분리candidate는 MCP·Dot/비교측정 담당. 기존모델·서비스/포트/큐 변경 금지 및 reports/coordination-status.json 공유를 요청했다.
ChatGPT 자체 UI조작 차단은 우회하지 않는다. 필요시 실제 준비된 plugin의 최종 사용자 UI단계만 남긴다.

## 0.3.11 최종 통합 — 진행 중

- UI237/237·desktop Rust95/95·Clippy 통과. 정상/실패 상태와 마지막 메시지·음성 re-entry 회귀를 포함한다.
- 동일1200×760 synthetic 기록1000건/최근100건: 본문 높이549.563→636.156px(+15.76%), 면적+19.14%, 완전 표시4→6(+50%). n1 UI 비교이며 모델/앱 처리 속도 개선이 아니다.
- 실제640×680 브라우저 치수와5페이지 overflow0 확인. 기존 viewport capability가1200×760을 반환한 잘못된 ‘compact’ 캡처는 배제했다. CDP viewport-capture로 실제640×680을 재확인했다.
- 좁은 창에서 잘린 그래프를 발견해 투영 경계로 overview camera 거리를 계산한다. 두 projection 회귀 통과.
- native0.3.11 읽기 전용5페이지 검사: 첫 실패는 고정 focusSlot0 가정. 실제 복원된 camera/selection/targets가 일치함을 진단 후 서로 다른 유효slot+전체복원 조건으로 교정했다. default/min/hidden 검사 통과하였으나 실제 대화 페이지가 오류여서 완료로 간주하지 않고 조사했다.
- 실제 history 목록1182행의 nonpositive system행과 JS safe-integer 초과 ID 발견. 기존 list_all_chats 공용 계약을 보존하고 history-rooms 경계만 유효positive·recent-first·문자열chat_id/last_log_id로 수정했다. CLI 회귀와 actual 다시 읽기를 진행한다.
- native screenshot에서 tab 선택 paint가 한 프레임 늦은 증거를 발견해 두RAF 이후 캡처하도록 교정했다. 원본/private 캡처는 외부/Git에 올리지 않는다.
- MCP0.2.0 2파일 patch74dd1504… 적용·stdlib/pinnedPython parent31검사 통과. 절대1.5초deadline·협력취소·late reply 억제 포함. bundle allowlist/CI에 추가, sourceSHA3c65936b…. 지속등록·Dot호출은 아직 미검증이다.
- OSK는 incident edge를tick별1회 구축(O(V+E))하고revision을tick안에서 재사용한다. parent20회귀 통과; peer64회귀/16differential 및 AB/BA30쌍 원자료를 최종 보고와 통합한다.
- 기존5개 모델/voice/Kakao 프로세스·설정/enrollment·stableCLI는 설치 전후 보존해 확인한다. 설치/정확SHA CI/릴리즈 자산·다운로드·postinstall native 검증은 남았다.

중단 뒤 재확인: 직전 goal turn은 history-ID/정렬·MCP0.2·native 실제100건 조회로 진전했다. 중단된exec handle은 사라졌고ps에서 해당검사프로세스 부재를 확인했다. UI237/237·CLI1151/1ignored·desktop95·각Clippy 로그의 실제완료를 읽었다.
필수Python목록 추출시23개 지정메서드를 모듈 전체로 축약·중복 실행한 명령 오류를 발견했다. 제품 regression으로 오해하지 않는다. `.github/workflows/ci.yml`의77개 full selector를 보존한 재실행은 exit0이다. 이전 불완전/범위가다른 로그는 통과근거로 사용하지 않는다.
최신 source build·ad-hoc deep/strict 완료,0.3.11 설치를 진행한다. private snapshots·비공개 대화원문은git에 넣지 않는다. source commit/push/CI/릴리즈·설치filematch·postinstall native가남았다.

## 갱신 목표·0.3.11 설치·MCP등록 —2026-10-03

사용자가 목표원문을0eef40c0…로 교체했다. 원문 전체를 다시 읽었고 기존11범위에 신경가소성·시냅스·실시간물리리모델링·원논문/정량검증이 추가됐다. 화면상단은 제목/항목수/갱신시각 한 줄을 요청했다. 전체목표는 진행중이며 완료/차단으로 바꾸지 않는다.
현재 설치0.3.11, build/install36경로일치·deep/strict·config/enrollment/stableCLI inode와SHA·기존5프로세스보존을 확인했다. pinnedPython의정확CI77selector1207/28skip 통과. UI237·desktop95·CLI1151/1ignored·각Clippy통과.
`codex mcp add alden_readonly` 공식CLI로 공유설정등록·enabled get readback완료. 기존node_repl의빈args를CLI가생략한것을narrow복구해다른모든기존설정값일치 확인. 설정backup/private receipt는alden-sidebar-preservation-20261003.
설치STDIO init/list/call/EOF를 실제persisted command/args로확인했다: server0.2.0·app0.3.11·OSKready/pending0/conflicts0, E5reachable. 생성metadata0.5s제한은unavailable1회였으나별도GET11234는200/7rows이고둘다loaded boolean필드를제공했다. 서비스/모델변경0. 실제Dotclient호출은미확인.
최종read-only native캡처10장은확보됐다. 데이터조회/가로넘침은확인했으나후반notification-before-draw의고정250ms기다림조건1건은실패하여전체native검사완료라고하지않는다. 결과null을wrapper가 .get한오류는검사실패와분리한다. 이전state가성공한것을이번source완료증거로대체하지않는다.
다음: 이소스를0.3.11기준선commit/push·정확SHA CI로보존하면서, 상단한줄과source변화에반응하는신경형그래프를0.3.12에서진행한다. 추가프로세스/모델/실전발신을만들지않는다. OSKsessioncapture미결속·검토대기/계수는기존처럼남아있다.

0.3.11 기준선31e97e6을정상commit/push해PR27의원격CI를시작했다. 모델/서비스/설정변경은없다. 사용자상단한줄요청은ui.ts/main.ts/settings.css/hologram.ts에수정했으며UI237·tsc/Vite build통과. 아직새한줄앱설치/렌더검증은남았다.
신경형새구현을진행한다. Trachtenberg2002 fullauthorlabPDF(7p), Matsuzaki2004 publisherabstract, Turrigiano1998 publisherabstract를확인했고Holtmaat2009는publisher본문접근실패/검색초록범위를구분한다. 논문근거는생성/제거·strength/size·stabilization의시각적영감이며Alden학습/생물학적재현성공주장이아니다.
새knowledge/plasticity.ts: 24노드/144pair cap,source트리플불변,pairbundling,deletion/retraction필터,degree-normalizedspring+softrepulsion+anchor,damping11/s,h1/120,max8steps/frame,typedbuffers,settle/quiet종료를구현했다. 아직renderer에연결하지않았고집중검사·측정은남았다. 다음은strength/lifecycle를gpuinstancebridge로보내고node좌표의지속성을지키며cortical형상·nativecheaprevision(기존PythonBridge stateRootfixedmetadata)으로실시간갱신을연결한다. 반복 fullPython/process polling을늘리지않는다.

## 신경형 그래프 통합 — 현재 상태

0.3.11 commit31e97e6의 원격 CI37114137368은 success다. 기존 자산 링크와 0.3.10 초안은 보존했다. 새 헤더는 `지식 그래프 · 항목 수 · HH:mm 갱신` 한 줄로 구현했고, IAB1280×720의 실제 header height20px·nowrap·오류 overlay없음·console error0을 확인했다. `/private/tmp/alden-neural-20261003/header-one-line-fixture.png`는 예시 화면이다.
0.3.12를 build/install했고 config/enrollment/stableCLI와 기존5프로세스 보존을 재확인했다. 이 설치는 상단 한 줄과 기존 renderer이며, 그 뒤 개발 중인 신경형 renderer의 설치 증거로 사용하지 않는다.
`plasticity.ts`는 정적 검토의 경계 잔류력·NaN 전파·elapsed time 손실·빈 진단 문제를 수정했다. 활성 endpoint와 validTo를 확인하며 fixed arrays로 source 기반 힘을 적용한다. 집중6개 통과. 느린10fps에서도 같은 elapsed time을 처리하고 정상30/60fps 좌표가 일치한다. 단위/시간은 UI 기계 모델이며 생물학적 실험 재현 주장이 아니다.
`synapses.ts`는 하나의 GPU instanced ribbon batch·144active/288transient cap·성장/수축·강도 평활·퇴역 좌표 보존·reduced motion을 구현했다. GPU별 슬롯 제거 뒤 위치 섞임과 GLSL length 이름 충돌을 수정했다. Header-only 설치에는 아직 연결되지 않았었다.
현재 개발 renderer는 Hologram에 physics와synapsebatch를 연결했고 stable ID hash anchors·source별질량·접촉력·기존cameraanchor·정적folded cortex를 사용한다. 기존per-edgeLine을제거하고재구성때도bridgeMesh를보존한다. 실제jobLoad와 별개인interactive frame flag를 추가했다. IAB의 첫 신경형 화면과console error0을 확인했으나, 수렴 뒤의 다리·드릴다운·숨김/복원·지속 자원·실제 수정 감지·전후프레임 측정은 아직 남았다.
Cua 세션이 중단 후 reset되어 예전 sidebarAuditTab은 사라졌다. 새 neuralBrowser ID2·neuralTab1·neuralFs·neuralCdp가 현재 binding이다. 검증용 tab1은 새로 만들었으며 user tab을 닫지 않았다. owned devserver1420 session98050, `/private/tmp/alden-neural-dev-20261003.log`. transient viewport는 아직 새 override가 없다.
다음: 현재 GPU/수렴 상태의 실제 진단 → 생성/철회 case와리소스검사 → PythonBridge stateRoot의고정sync.json metadata만읽는revision경로(추가Python프로세스없는1s감지·15s복구) → native앞화면/restore timing의250ms가정수정 →0.3.12 전체neurobuild/install36파일·checks·정확SHA CI·릴리즈. 현재prototype모듈이생긴것을전체goal완료로계산하지않는다.


## 동적 그래프 0.3.12 전달 — 2026-10-03

사용자는 정적인 그래프를 세련되고 빠르며 역동적으로 고도화하도록 요청했다. 이전의 가시 상태 수렴 후 렌더 중지 정책을 갱신했다. 물리 계산은 수렴 후 멈추고, 보이는 동안에는 작은 노드 움직임과 다리의 표현 흐름을 30fps 상한으로 유지한다. 숨김·닫힘·잠김, 동작 줄이기와 비상 중지의 연산 중지 원칙은 유지한다. 실제 학습·추론 신호를 만들어내지 않는다.

카메라 감쇠 12/s, 다리 전이 상수 0.16s, 24노드/144활성 다리, persistent instance batch와 안정적인 ID 위치를 통합했다. 원본 관계·근거는 바꾸지 않는다. 현재 evidence의 철회/기한을 표시에서 제외한다. 기존 PythonBridge의 고정 checkpoint metadata를 1초마다 확인하며, 변경 시 전체 read/15초 fallback을 사용한다. 숨김 epoch의 늦은 응답을 버리고 추가 Python 프로세스로 metadata를 읽지 않는다.

동일 1200×760 예시의 5초 표본 각 1회에서 cadence 수정 전후 22.99→30.17fps(+31.22%), 프레임 간격 p95 50.1→35.2ms(−29.74%)다. 이 간격은 CPU/GPU 렌더 시간이나 추론 속도가 아니다. 33.33ms 목표 간격보다 p95가 1.87ms 길다. 물리·다리 배열은 18,960바이트이며 전체 앱 메모리와 구분한다. 숨김 2초·모션 감소 3초·예시 일시 중지 1.5초에서 추가 렌더 0회를 확인했다.

UI 250·desktop Rust 96·Clippy·TypeScript/Vite build가 통과했다. 최신 0.3.12를 ad-hoc build/install하고 전체 bundle 37파일 SHA/deep strict, 기존 config/enrollment/stable CLI의 inode·mtime·SHA와 5프로세스 보존을 확인했다. 마지막 설치 로그는 `/private/tmp/alden-dynamic-0312-install-latest-20261003.log`, receipt는 `/private/tmp/alden-dynamic-20261003/installed-final.json`이다.

설치본의 독립 read-only workspace/popover 검사가 모두 통과했다. 기본/최소 다섯 페이지·탐색 복원·합성 숨김/복원을 확인했고, native 전환 첫 프레임 n=10 p95/최대 13ms다. 실제 primary 메뉴바 입력·물리 잠금·사람 음성과 혼동하지 않는다. private 결과는 `/private/tmp/alden-dynamic-native-delivered-0312-20261003` 및 `/private/tmp/alden-dynamic-popover-delivered-0312-20261003`이며, 공개 파일에는 안전한 집계와 예시 데이터만 넣었다.

연구·수식·범위는 `docs/architecture/alden-neural-plasticity-20261003.md`, 예시 원시 관측은 같은 이름의 디렉터리에 있다. Archify 순서도는 showcase 9/9, composition 오류/경고 0, 네 크기 브라우저 검사 통과, 밝은/어두운 테마 판독 완료다. Viewer 고정 제어는 영어다. UI 미리보기 포트1420은 이 작업이 만든 session22737이며, Cua browser ID2/tab1을 결과로 남겼고 viewport/media override를 복구했다.

다음은 이 판본의 정상 commit/push·정확 SHA CI·unsigned 초안 asset manifest/download 대조다. 전체 11항 목표는 active다. 실제 Dot client 호출, 자연 음성/wake, 공개 Developer ID 공증과 기존 운영 worker 전환 등의 남은 항목을 완료로 표시하지 않는다. OSK 세션 착지 미정과 검토 대기·계수도 유지한다.


## 독립 최종 평가와 Raw 재정리 요청 — 2026-10-03

이전 goal 턴은 progress다. code de5cdf53e83f44efc2a8ffd34b7e8379e093a633과 원격 branch/tag가 일치하고 CI37124654188의 4개 작업이 성공했다. 0.3.12의 초안 릴리즈402518340에서 6개 파일을 다시 내려받아 SHA/ZIP CRC 및 설치 bundle 37파일을 대조했다. URL은 https://github.com/twoimo/openkakao-bot/releases/tag/untagged-c8580978538ef83a6d78 이다. 실제 alden_readonly MCP 호출이 이 Codex 세션에서 성공했으며 별도 Dot client는 아직 미확인이다. 새 네이티브 app inventory 요청은 30초 timeout/kernel reset이었다. 변함없는 CUA native 요청을 반복하거나 self-app 안전 차단을 우회하지 않는다.

고정 DPO 후보의 아직 사용하지 않은 최종 합성 test 3쌍을 실제로 채점했고, 기준/후보 각 3개의 greedy 답변도 생성했다. 평균 loss0.693542887은 동일 policy/reference의 이론 ln(2)보다 0.05709% 높고 2/3의 선호 마진이 악화됐다. 실제 사실은 3/3→3/3, 출력3/3 동일하므로 후보를 승격하지 않는다. 상세 근거는 alden-dpo-final-test-20261003.md/.json이다. 원시 성공 기록은 /private/tmp/alden-dpo-final-test-20261003-ljm5t3ff/report.json 및 /private/tmp/alden-dpo-final-generation-20261003-6ptmt08c/report.json 이다. 두 실패 시도도 보존했으며 성공 표본에 합치지 않았다. 기존 임시 평가 환경의 패키지 부재와 생성 wrapper의 오래된 후보 경로를 수정했다. 운영 환경을 바꾸지 않고 별도 /Users/twoimo/Library/Application Support/openkakao/evaluation/runtime-cp31116-mlx0322 환경에 Python3.11.16/MLX0.32.2/MLX-LM0.31.3/Transformers5.17.0을 준비했다. 해당 환경의 모든 transitive pin은 /private/tmp/alden-dpo-runtime-20261003-pins.txt 에 있다. 학습·제품 모델 변경·cloud inference0, network-deny/negative probe·메모리 guard·공유 command 보존·swap 증가0을 확인했다. 이 test를 이후 후보의 독립 test로 재사용하지 않는다.

사용자는 서명 준비 질문에 ‘기존 계정으로 준비 가능’이라고 답했다. 실제 인증서/Secrets가 설정됐다는 증거나 새 보안 자격 증명 발급 승인을 뜻하지 않는다. 현재 keychain에는 Apple Development만 있고 GitHub Secrets는 비어 있다. 정식 workflow는 Developer ID Application과 ALDEN_APPLE_* 6개를 요구한다. 계정의 기존 준비 경로를 이어 확인하되 private key를 채팅/로그로 받지 않는다.

최신 사용자 요청은 ‘지식 그래프를 Raw 레벨부터, 참조 OSK 저장소 방식으로 다시 정리’다. 원래 전체 11항 목표에 대한 추가 지시로 보존한다. 현재 scripts/alden_osk.py는 OSK v4.1.2/commit9bbf08febc5a1fb2af068006735ed79cbdb71178을 실제 포함하지만 집계된 ERE를 노트로 복사하며, 원문 좌표와 derived-from 근거 배선이 부족하다. 공식 GitHub commit은 API로 확인했다. Raw의 upstream 규약은 _governance/Bylaws.md §2 및 Mechanism.md §8/9다. 에이전트 세션은 Scope/_raw/.records append-only 라운드, 외부 원자료는 _sources 비노드로 구분한다. 카카오 peer/outgoing_unclassified를 가짜 user/agent 라운드로 만들지 않는다. 원문·직접 편집 노트·승인 대장을 보존한다. 다음은 실제 published corpus 원본 행/ledger와 계정 scope를 확인하고, private 원자료 보존·정확한 근거 좌표·SDK의 create_node(edges)/update_node(add_edges)로 재생성하는 stage→검증→반영 경로를 구현한다. Raw/개인 원문은 public Git/릴리즈에 넣지 않는다.

OSK user turn9 통합 검토는 본 작업 호출과 함께 수행했다. 새 Raw 요청은 명시 지시, 자동 goal 계속은 새 인간 발화가 아니다. capture 착지 미정과 검토 대기/계수는 유지하며 완료 raw 라운드가 없었다. 전역 OSK session scope를 추측해 결속하지 않는다. Alden 제품 vault는 그 별도 데이터다. 전체 goal은 active다.


## Raw 계층 재구축 착수 — 2026-10-04

새 scripts/alden_osk_sources.py와 tests/test_alden_osk_sources.py를 구현했다. 실제 OSK v4.1.2의 _sources 비노드 규약을 사용해 private의 외부 Kakao 원자료를 전체 보존하며, agent user/assistant 라운드로 재라벨하지 않는다. 원문을 12,000자 note 한도로 잘라 저장하지 않는다. Snapshot은 published corpus의 SQLite Online Backup API/읽기 전용으로 복제했으며 original Kakao DB에 접촉하지 않았다. private stage는 /Users/twoimo/Library/Application Support/openkakao/bujamentor/knowledge/alden-raw-rebuild-20261004-_kqn4fev 이다. snapshot 2,050,483 rows/quick_check ok/7.228초. Source 원래 corpus는 자연 writer에 의해 계속 증가하므로 이 값은 캡처 시점 기준이다.

현재 첫 전체 Raw export도 완료됐다: 2,050,483 records, 4,031 immutable text chunks, 235.106초, generation7fdb19becdd047eeaf722a58e211f879. /private/tmp/alden-raw-rebuild-20261004-stage.json 과 stage/raw-export-receipt.json 에 묶인다. vault는 stage/knowledge/osk/vault 이고 각 source heading은 message-rowid-recordsha, source-index.sqlite3는 exact source ID의 모든 좌표를 저장한다. source code SHA48b513cd8b7f3bdc7e2ce6ff3ca3c1c211c856df5e0944ca8fd9c0140c224817로 실행했다. OSK 원본 secret filter를 적용했으며 raw/개인 원문은 Git/릴리즈에 넣지 않는다. 실제 모델/GUI active corpus/기존 노트는 아직 바꾸지 않았다.

집중5개 통과: 긴 원문/큰 ID/역할/원본 bytes 보존, chunk completeness/기존 generation 불변, cancel stage 미사용, 중복 source ID 무병합, symlink 거부. 같은 녹색 검사를 변화 없이 반복하지 않는다. 실행 session3205는 exit0 완료다.

미완료/다음: corpus의 rooms/meta와 결정 ledger도 정확한 원자료 좌표로 연결 → raw-backed 새 ERE를 stage에서 재생성(기존 요약 graph JSON을 Raw로 대체하지 않는다) → SDK write.create_node(edges={derived-from:refs}) / update_node(add_edges=...)로 source-grounded notes 갱신 → 직접 편집/보호/승인 대장과 stable IDs를 보존하며 live overlay/CAS → 실제 repo validator/좌표/데이터 해시/노트 coverage/누락/새 데이터 catch-up 검증 → bundle/설치/CI/릴리즈. 카카오 외부 수집 자료는 _sources, Alden이 참여한 실제 agent 세션만 _raw/.records 원형을 사용한다. _raw append_rounds로 peer/outgoing_unclassified를 가짜 agent 사용자 발화로 만들지 않는다.

Root 실제 state는 bujamentor이며 published corpus _index_db_path는 knowledge/corpus/current.json의 account별 context.sqlite3다. 캡처 전 live alden_messages/context_messages 2,050,469, alden_rooms1181, managed151/active118/held0/groups4였고, 복제 시 14건 자연 증가가 있었다. 행별 source ID는 kakao:account:room:chat_id:log:log_id로 기존 evidence.source_event_ids와 일치한다. source rooms/meta에는 message가 아닌 metadata 원자료 좌표를 추가해야 한다. deprecated sourceEvent IDs의 ledger mapping은 producer helper를 읽고 정확히 연결한다.


### 2026-10-04 Raw/history delivery checkpoint

Latest requested changes: separate 카카오톡 답변 / 긱뉴스 전송 histories, searchable room chooser, explicit fixed local model names and removal of the sidebar subtitle. App 0.3.13 installed, 40-file SHA parity, deep/strict ad-hoc signature; three configuration/CLI files and five long-lived processes preserved. Raw baseline 2,050,483 rows retained; 115 active SDK notes with real derived-from source coordinates, 111 shared IDs retained, pending/conflicts 0. Duplicate identity/conflicts 0; 2,298 statistical length flags retained; six display label normalizations; 89 of 213 unnamed rooms receive participant-based display aliases. See `architecture/alden-raw-history-20261004.md` and its sanitized aggregate JSON. Original encrypted Kakao DB access, real sends, model promotion and signing credentials were not changed. Broader 11-part goal remains active with the previously documented voice/model/primary/Dot/production work pending.

Private live receipt: `~/Library/Application Support/openkakao/bujamentor/knowledge/raw-rebuild-receipt-20261004.json`. Private reversible backup: `knowledge/raw-rebuild-backups/20261004-source-before`. Initial full Raw archive is an as-of snapshot; current referenced new/corrected records append immutable versions. Full-delta archive catch-up remains a distinct follow-up if needed; do not claim the frozen snapshot contains later unreferenced rows.

### 2026-10-04 Full Raw delta and producer delivery checkpoint

0.3.14 is installed with 41-file built/installed SHA parity and deep/strict ad-hoc signature validation. New `scripts/alden_osk_delta.py` indexes the frozen baseline and archives all changed original rows, direction/identity corrections and removal tombstones from complete published versions. The live index reached 2,050,732 rows (249 beyond the 2,050,483-row base); 115 active notes passed the actual SDK contract with Raw references, pending/conflicts 0. No Raw payload, source ID or account fingerprint is committed. Private frozen baseline and `raw-sources.json` configuration remain in the existing product state; `base_snapshot` points to the already verified private snapshot.

Original collection still reports `LocalDataAccessWaiting`. `sync_cycle` now synchronizes the valid saved corpus independently and backs waiting collection off for five minutes. Fresh producer checks preserve the real materialization timestamp and separately attest the current published snapshot/signature. The installed fresh MCP collector reads `collection_state: waiting` and `freshness_scope: current_published_corpus`; a long-lived previously loaded MCP process can retain the older response shape until reconnection. Dot invocation is still unattested.

Warm observations: Raw check median 0.555 ms, full saved sync median 27.174 ms, n=5 each, 0 new Raw chunks or SDK revisions. Cold bootstrap 24.746 seconds and first derivation refresh 19.731 seconds are different workloads, not a before/after speedup. Installer executable-identity checks reject actual duplicates and unresolved live candidates while ignoring proven other executables and exited matches; 40 fake-adapter tests pass. Inline graph-refresh failure freshness regression and all 101 graph tests pass. Existing three configuration/CLI baselines and five model/voice/automation process identities stayed unchanged. See `architecture/alden-raw-delta-20261004.md` and its aggregate JSON. The prior 0.3.13 rendering audit is not relabeled as a 0.3.14 audit. Full goal remains active with the prior signing, physical interaction/voice, Dot and model/media limits unchanged.

### 2026-10-04 Installed MLX rename compatibility

Previous goal turn was progress: 0.3.14 commit 7ff1d80f88cae1c6f0453ed58cfa70326dca8dc7, CI 37164307706 4/4, release draft `https://github.com/twoimo/openkakao-bot/releases/tag/untagged-8493baf088675c71e581`, six downloaded asset hashes/ZIP CRC and 41 installed files matched. Private current delivery receipt records that result.

Current authoritative machine state changed: MLX Core.app is absent, signed MLX-Serve.app 26.10.1 with the same `com.dalcu.mlx-core` bundle ID is installed; the existing 27B PID 58780 still runs from the previous mapped executable. No model service was restarted or adopted. The source launcher now recognizes the fixed renamed path when the legacy path is absent, preserving legacy preference and all existing signature/ownership/memory checks. Focused lifecycle/action 68 and complete menu 177 tests pass. A real isolated-state CLI preflight for the exact iQ model reached memory_check and rejected insufficient memory without starting a server. Separate observation: 53,005,877,248 bytes available versus 64,424,509,440 required. Existing model preferences, three configuration/CLI baselines and five process identities are being retained through 0.3.15 delivery. See `architecture/alden-mlx-app-rename-20261004.md`. Do not label this as new successful Flash-Next inference or a measured latency gain. Full goal remains active; no additional permissions or model substitution were inferred.

### 2026-10-04 Manual microphone path

0.3.15 delivery finished: e126c49a44cb593a65946e91be5a50f39c88da43, CI 37166325092 4/4; draft `https://github.com/twoimo/openkakao-bot/releases/tag/untagged-b23945c00792edbd11a1`, six downloads/ZIP CRC and 41-file installed parity. Source inspection then found every voice start blocked by the unreleased wake head. A natural voice test question was premature and explicitly corrected. It remains unanswered; do not interpret elapsed time or synthetic clips as physical speech.

0.3.16 now implements a separate explicit manual microphone command and owned stop, while start_voice_session and RELEASED_WAKE_MODEL=None retain the automatic-wake gate. It preserves two-turn context and restores only the explicitly selected local conversation across mic sessions. User-listen uses 20ms frames, 0.2s onset buffer, 0.6s end silence, 15s idle stop and a 12s bounded PCM buffer; overflow rejects rather than dropping the beginning. The owned child has a 300s deadline, two-second cooperative cleanup, parent-PID exit detection and no foreign-process signalling. Global stop remains latched.

Installed 0.3.16 is an exact 41-file match with strict/deep ad-hoc signature, preserving three configuration/CLI files, three model preference files and five existing processes. Voice core 55 total/5 skip, native 98, full UI 257 before final changes plus final affected 98, TypeScript/Vite and clippy pass. Owned native seven-page audit succeeds at default/minimum sizes, zero errors/overflow, first-frame p95/max 14ms across 14 heterogeneous navigations. It registers no start/stop commands and does not prove primary or physical voice operation. A public Computer Use getApp attempt after the upgrade again timed out; do not repeat it through private APIs or restart the app solely for that observation. User can perform the natural microphone test in the installed app after clicking its manual control. The preview is explicit about installed-app-only recording.

Archify final v3 lifecycle has nine showcase checks, zero errors/warnings and passing four-size automated browser measurements. Its 1440x900 light capture was visually reviewed; earlier overflowing candidates are privately archived. See `architecture/alden-manual-voice-20261004.md` and aggregate JSON. Full goal remains active; actual natural/primary interaction, automatic-wake release, Dot invocation, remaining model/media targets and notarized production remain unverified.
