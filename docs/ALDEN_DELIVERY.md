# Alden 전달 진행 기록

2026-10-03 최신 이어받기: [0.3.10 메모리 재현·설치 근거](architecture/alden-embedding-memory-20261003.md), [현재 진행 기록](ALDEN_CONTINUATION.md). 과거 기록은 당시 판본의 근거이며 전체 목표는 진행 중이다.

2026-10-01 상시 설치본 추가: [실제 메뉴바·기본/최소 설정](architecture/alden-primary-ui-20261001.md)을 기존 primary process에서 확인했다. 코어276×260·normal-level 설정960×880/640×680 이미지3개를 직접 검토했고 크기 복원·native 가시 창0을 확인했다. AXPress만으로는 열리지 않아 짧은 실제 CG 마우스 이벤트를 사용했으며 독립 가상 커서로 보고하지 않는다. 제품 소스·운영 작업자·메시지 전송 변경0. 각1회 창 표시 관측은 rendered UI p95와 구분하며 graph navigation·Retina·물리 중단/잠금·음성·production의 남은 목표를 유지한다.

2026-10-01 실패 경로 추가 검증: 설치본의 실제 그래프 탐색·resize 뒤 compact PNG 충돌1회는 exit1·유효한 failure JSON·기존 파일 보존으로 종료했다(3.629초, 전체 자식 실행). [근거](architecture/alden-graph-navigation-failure-20261001.json). 이전 foreign-exception2회 원인은 여전히 미해결이며 코드 변경·운영 교체·전체 목표 완료로 계산하지 않는다.

전달: [Alden 그래프 탐색 후보 초안](https://github.com/twoimo/openkakao-bot/releases/tag/untagged-25ad469edd80663739aa)의 **7개 파일**을 다시 다운로드해 바이트·원격 digest를 대조했다. ZIP30파일은 설치본과 일치하고 overlay30파일도 hash 일치다. 소스`51f71c2`/증거`5cfea2b`의 [CI](https://github.com/twoimo/openkakao-bot/actions/runs/36799335869)4/4 성공·독립 설치/개인정보 검토 통과. [릴리즈 대조](architecture/alden-graph-navigation-release-20261001.json). 공개 공증/프로덕션 완료를 뜻하지 않는다.


2026-10-01 추가: [그래프 탐색 복원·설정 렌더링](architecture/alden-graph-navigation-20261001.md)을 소스`51f71c2`의 설치 바이너리에서 확인했다. 이전32단계·전체 보기·A→B→A epoch/dispose fence, 실제 persisted graph·기본/최소 WKWebView·숨김350ms 렌더0·재개 frame 진행, 설치30/30·resource27/27, UI199/Rust90/Python1056·Clippy/build 통과다. [Archify](architecture/alden-graph-navigation-20261001.html)9/9·4viewport·이미지4개 검토도 통과했다. 감사용 floating 창과 unavailable 다른 backend라는 범위, private native PNG·초기 foreign-exception2회 미해결을 기록한다. 전체 목표·물리UX·음성·worker·signed release/production은 미완료이며 Git/CI/초안 전달은 후속 readback으로 대조한다.


전달: [Alden 0.1.6 화면 가시성 후보 초안](https://github.com/twoimo/openkakao-bot/releases/tag/untagged-6730d9c69d5ac88931de)에 7개 파일을 올리고 다시 다운로드하여 모두 바이트 일치를 확인했다. 앱 ZIP 내부30파일·소스/증거 overlay23파일도 대조했다. 소스 `8791ea1`의 [CI](https://github.com/twoimo/openkakao-bot/actions/runs/36793165993)는 4/4 성공이다. 초안 target/overlay는 증거 checkout `b7bdda9`이며 공개 공증/프로덕션 완료를 뜻하지 않는다.


2026-10-01 추가: [화면 잠자기·세션 전환 처리](architecture/alden-workspace-20261001.md)를 소스 `8791ea1`의 Alden 0.1.6 설치본에서 검증했다. 별도 설치 바이너리의 두 **프로세스 내부 합성 알림** 모두 두 창 숨김·350ms 추가 렌더0, 일반 숨김·복원10회도 렌더0이다. 네이티브 숨김 뒤 DOM 재개 차단과 종료 구독 정리를 추가했으며 Rust90/UI194/Clippy/build 통과·설치30/30·리소스27/27 일치를 확인했다. 물리 잠자기·세션 전환·잠금과 설정 렌더러는 미검증이며 전체 목표/공개 릴리즈/프로덕션은 미완료다. 새 Archify 도식은 가독성 미통과로 미전달이며 기존 검증 도식을 보존한다. Git·CI·릴리즈의 최종 상태는 후속 readback으로 대조한다.


네이티브 전달 링크: [Alden 0.1.6 로컬 후보 초안 릴리즈](https://github.com/twoimo/openkakao-bot/releases/tag/untagged-179cb6167afd74a68bd5). 앱 ZIP·Python 동반 실행 환경·소스/증거·체크섬 **7개 산출물**을 다운로드하여 원본과 바이트 일치를 확인했다. 증거 커밋 `60fdb04`의 [CI](https://github.com/twoimo/openkakao-bot/actions/runs/36790224085)도 **4/4 성공**이다. [전달 readback](architecture/alden-native-render-release-20261001.json)에 소스·설치·릴리즈의 범위를 대조했으며 공개 공증/프로덕션 완료를 뜻하지 않는다.


2026-10-01 최신 네이티브 검증: 소스 `4838fff`의 Alden 0.1.6을 설치했고 **30/30 파일·27/27 리소스**가 일치했다. 설치 바이너리의 별도 WKWebView 인스턴스에서 실제 코어 이미지를 확인하고 **10회 × 350ms 숨김 추가 프레임 0**, 복원 3–4프레임/250ms를 관측했다. 숨김 요청→중단 확인 상한 median **14.622ms**, max **28.442ms**(n=10)다. [화면·측정·범위](architecture/alden-native-render-20261001.md)와 [Archify](architecture/alden-native-render-20261001.html)를 제공한다. 물리 화면은 [1.0]뿐이므로 Retina, 확장 설정, 상시 PID의 현재 화면, OS 잠금, 물리 단축키와 음성·생산·공개 서명 릴리즈는 미완료다. 기존 기록은 각 당시 결과로 보존한다.


최신 목표 원문: `/Users/twoimo/.codex/attachments/ba814151-0eb9-40ae-9f7f-cbdf53d734b7/goal-objective.md`.
이전 목표 원문 `576db5bd-37d1-4ab4-853a-fb1cf0803774`의 미디어 수정과 기존 보존 조건도 이어간다.
2026-09-30 작업 기준: PR #27의 `74149a41b6c86ead9a1a2794bd1e5c1c2fadfb98`에서
`codex/alden-delivery-20260930` 분기. 원래 작업 폴더는 main `8012f90`의 깨끗한 detached checkout이었다.
다른 checkout의 `ed.hup`, 실행 중인 카카오 세션과 전송 큐는 보존한다.

단계는 **미착수 / 구현 / 실제 실행 검증 / 전달 완료**로 기록한다. 테스트 통과는 실제 실행 검증과 별개다.
과거 문서의 결과는 날짜와 실행 범위가 일치할 때만 재사용한다. 전체 목표는 계속 진행 중이다.

## 최신 전달 상태

- 2026-10-01 추가: [실제27B 오프라인 DPO 학습](architecture/alden-dpo-training-20261001.md)의 합성train3쌍/실제update3회, 고정reference, 원본SHA/파라미터 유지, private adapter 저장·새base 재로딩, adapter-aware versionvalidation/cache를 확인했다. 학습37.907초/process38.148초, 별도평가24.809초/cache8.899초 각1회·network0·swap기존값유지·shared124→124. train평균loss.693147→.688997, validation.693147→.692679지만 회의문항은.696600으로 악화; 별도실제한국어생성3문항은3/3→3/3·출력동일이고 지연은혼재. 품질개선/promotion 없음, 최종test 미사용. 독립P2 4건 closure·MLX집중126/실패0·필수1054/26skip/실패0·buildforwarding 후속1pass, Archify9/9/browser4viewport/부모실제image읽기. 최종Git/CI/산출물은 별도readback. 설치상주학습·최종test/제품정확성/검색/제품지연/메모리·사람음성/nativeUI/운영worker/signed release와 전체목표는 미완료다.

- 2026-10-01 추가: [버전별 로컬 평가](architecture/alden-version-evaluation-20261001.md)를 소스 빌드에 명시 구성으로 연결했다. 변경된 버전/소스/가중치/문항/runtime은 validation 평가, 같은 입력은 검증 cache 재사용. 실제27B 최종 first24.772초·cache8.567초 각1회, load1→0·network시도0·shared124→124·swap증가0. 집중13·필수1040/17skip/실패0·MLX runtime46/실패0·Astra/max source3P2 closure. network-deny 실제 구성 build가 cache8.993초 및 Alden.app까지 성공. Archify9/9·browser4viewport·실제image읽기 확인. Git/CI 최종head는 별도readback으로 기록한다. 평가만 실행했으며 기존SFT·실제DPO학습·제품promotion·설치상주평가·사람음성·nativeUI·signed release와 전체목표는 미완료다.

- 2026-10-01 05:25 KST: [사진 후속 질문·복구 취소](architecture/alden-photo-followup-20261001.md)의 code `247c676`을 커밋했다. 미래/foreign/stale/NaN 사진 선택과 원본 시각 복사를 수정하고 photo99/current100을 분리했다. CLI·자식 process 취소와 첫 성공/둘째 취소 cleanup, 총 이미지 예산·private 경계를 검증했다. Astra/max code PASS; 집중7·필수1027/17skip/실패0. 같은27B·context8192의 clean isolated pixel 후속1회 “초록색이네”, 전체16.325초이며 p95/개선율을 계산하지 않는다. shared124→124·owned4PIDs없음/port61·strict auth negative401, 후보21/19·기존3selectors/config·준비 구간 3queue digest 동일·미활성화. 초기 unittest의 잘못된 전체 TestCase 수집이 실제 local inference를 실행했고, 이후 module alias·빈 graph·socket guard로 격리했다. 앞선 제한된 실험과 최종 clean 근거를 구분한다. 원격CI/최종 전달 SHA는 확인 후 별도 기록한다. 운영 서버는 계속 `--no-vision`; 설치UI·실제Kakao 첨부·사람 음성·signed release·전체 목표는 미완료다.

- 2026-10-01 03:40 KST: [같은27B의 실제 사진 읽기](architecture/alden-local-vision-20261001.md)를 owned process에서 검증했다. 공유 서버의 `--no-vision`이 기존 사진 경로를 막는다. native26.9.5·같은 체크포인트의 합성 사진4/4, 텍스트 후속1회, namespace 직접 질의1회 및 실제 checkout worker의 이미지+strict JSON 질의1회가 성공했다. 사진 첫 콘텐츠0.923–1.475초(각1회), worker4.132초1회로 개선율·p95는 산출하지 않았다. 첫 bare-model registry5MiB 오류는 namespace 탐색으로 정상 ID/용량을 확보한 뒤 물리 MLX allocation 샘플과 구분했다. 가중치4개·바이너리 post-run SHA와 fixture/로그/JSON을 보존했다. Astra/max 독립검토, owned PID2개 종료·port11237 연결거부·shared snapshot/counter118 유지 확인. 운영 서버/작업자·큐는 교체하지 않았고 전체 목표·운영 사진 경로·실제 사진 기억·사람 음성·native UI·공개 signed release는 미완료다.

- 2026-10-01 02:57 KST: [음성 WAV 취소·게시](architecture/alden-voice-wav-20261001.md)를 수정했다. 기존 설치 writer의 취소 중 덮어쓰기를 실제 NumPy/fake-synthesis로 재현한 뒤 private atomic WAV 및 local/turn/session 취소 잠금, 새 root0700 경계를 적용했다. 집중58·필수1020/17skip·실패0, Astra/max 독립4건 pass. source bd551b5 normalpush·원격CI36753986921 4/4 성공(hosted1020/74skip). 설치30/30·resource27/27·PID60225·strict signature 및 설치 writer2cases 확인; enrollment/queue5/localCLI·후보 준비 중 sharedCLI 보존. ZIP9e688164·7/7checksum·이전package 보존, 후보21/19·3selectors/config 일치·미활성화. 실제 STT adapter는 swap-free1,429,272,002<2,147,483,648로 `voice_memory_budget_low`, heavy import/network0. 전체 목표·사람 음성·native UI/물리 shortcut·production worker·공개 signed release는 미완료다. 현재 shared menubar Python3.11.9를 보존했으며 배포 sidecar3.11.16과 구분한다.

- 2026-10-01 01:59 KST: 파일의 정확한 DB 행·작성자·첨부 digest에 묶인 다운로드, 취소 가능한 문서 파서와 부분 읽기·출처 receipt를 구현했다. [파일 내용 읽기 근거](architecture/alden-file-content-20261001.md): 필수 Python1011/16skip/실패0 및 마지막 집중31/실패0, Rust1141/1ignored, clippy 통과. 실제 local27B 합성 문서 질의4/4, 생성4.342–11.396초(각1회). code c29e263 normalpush·원격CI4/4 성공, 설치30/30·resource27/27·PID11451·strict signature·설치 파서 digest 일치. enrollment/queue5/stableCLI 보존·backend3/3ready. private worker 후보21assets/19source 준비·미활성화. 실제 Kakao file transport·native 화면·사람 음성·공개 signed release와 전체목표는 미완료다.

- 2026-10-01 00:36 KST: source code1a728e8은 주기당 단일 snapshot·원자적 graph/FTS 확정·실패/취소 rollback·호출자 transaction 보존을 구현했다. 필수981개/16skip/실패0. 실제 고정 mirror 교대5쌍의 graph-only 중앙5.383→3.390초(관측37.04%)·결과10/10동일·복제5→1회. Dense는 이 비교에서 stub이다. 설치29/29·resource26/26·PID92542·strict signature 확인 후 백업/전용lock 아래 실제E5 1회3.335초 refresh·무결성/watermark일치, enrollment/queue5개식별자 보존·backend3/3ready. [근거](architecture/alden-graph-cycle-20261001.md). 설치 창/사람 음성/worker 교체/공개 signed release는 미완료이며 전체목표를 축소하지 않는다. 최신 worker후보도 private state에20/20assets·18/18source·기존3selectors/config 일치로 준비했으며 활성화하지 않았다. 최종 전달 SHA·원격CI는 ignored dist/alden-0.1.6-local/alden-delivery-readback-v1.json으로 대조한다.

- 23:24 KST 새 목표 ba814151의 11개 항목 전체를 읽고 기존 범위를 유지했다. 실제 암호화 DB 874,995,712 bytes와 WAL 4,676,208 bytes의 private read-only SQLCipher 4.6.1 복제본에서 quick_check=ok. 설치 CLI의 3방/8회 증분 조회 모두 성공했고, 새 실제 메시지 4건을 private index에 0.535초로 반영·재조회 추가/중복0. 기존 production mirror에서도 4/4 digest/context row를 독립 대조했다. [측정 근거](architecture/alden-encrypted-snapshot-20260930.md)는 sent_at→mirror receipt n=4 중앙37.625초를 DB insertion latency와 구분한다. 보존 사본·기존 전용 잠금 아래 설치 GraphRAG를 6.522초에 1회 갱신하고 graph/E5 watermark·무결성을 대조했다. 백업 보존, worker 재시작·프로브 전송0, backend3/3ready·설치 strict 서명 valid. 지속 최신성·native 화면·사람 음성·최종worker 교체·공개 릴리즈는 미완료다.

- 22:33 KST 새 목표483991c3 전체 확인: 추가된 색감 실패를 기존 승인 방 ledger에서 조사했다. 실제 응답7행/고유5events/과거sent2행을 찾았고, 현재 worker c9b576의 pure helper 재현에서 근거 없는 시각 판단0이다. 새 모델 호출·전송·DB/큐 변경0. [색감 재현 기록](architecture/alden-color-claim-replay-20260930.json)은 운영 교체나 실제 pixel 해석 성공과 구분한다. 기존 색감 금지 assert가 현재971개 CI에 이미 포함돼 있어 통과한 source 검사를 다시 실행하지 않는다.

- 22:17 KST 네이티브 코드 **effa5c629955093dc64f7370295e789422783ad4**를 normal push하고 [원격 CI36719629616](https://github.com/twoimo/openkakao-bot/actions/runs/36719629616)의 **4/4 작업 성공**을 확인했다. Hosted Python은 971개/73 skip/실패0이고 로컬 pinned 환경은 971개/2 skip/실패0이다. frontend191·desktop Rust85도 통과했다. 설치29/29 파일·26/26 resource·단일PID22050·ad-hoc 서명 및 ZIP29/29 바이트 대조를 확인했다. [오디오 범위](architecture/alden-native-audio-20260930.md), [설치](architecture/alden-native-audio-install-20260930.json), [원격 기록](architecture/alden-native-audio-remote-ci-20260930.json)을 기준으로 읽는다. 이전 패키지는 ignored dist/alden-0.1.6-local-history에 보존했으며, 이하 이전 판본의 관측은 시간순 이력이다.
- 코드 판본은 `e03a678`, fixture 격리 후 원격 판본은 `b987ad151e97010411f346e2c0aec34839551eba`다. [PR #27](https://github.com/twoimo/openkakao-bot/pull/27)은 draft이며 main merge와 공개 release는 아직 수행하지 않았다.
- [원격 CI 36706854755](https://github.com/twoimo/openkakao-bot/actions/runs/36706854755)는 성공했고, 해당 SHA의 보안 검사까지 **5/5 성공**이다. Hosted Python은 959개 실행, 70개 건너뜀, 실패 0이다. 로컬 전체 의존성 환경의 959개/2개 건너뜀과 구분한다.
- Alden 0.1.6 로컬 설치, 28/28 bundle 파일 및 25/25 resource 대조, 27B 한국어 후속질문과 설치된 Python browser entrypoint의 공개 title 1건은 검증됐다. 네이티브 CUA inventory 재조회도 30초 timeout으로 실패해 설치 화면·물리 단축키·마이크의 증거를 추가하지 못했다.
- 최종 worker 후보 `e03a678`은 독립 scoped Astra 검토와 20개 자산/18개 소스 hash 대조를 마쳤다. 기존 타세션 runtime `20260930T0650Z-termination-74149a4`는 유지한다. 큐와 3개 방을 보존한 교체 허가를 요청했으며 아직 답변을 받지 않았다.
- 재생 중 microphone 차단은 처리되지 않은 입력에만 유지한다. 실제 native processing 활성화·입출력·녹음된 기준음 재생을 확인했고, 최종 설치 Python의 1회 취소 waiter 종료는 **12.418ms**였다. 입력은 모두 무음이므로 사람의 음성 끼어들기와 에코 품질은 미검증이다. `RELEASED_WAKE_MODEL=None`과 기존 메모리 admission은 유지한다.
- 공개 릴리즈는 Developer ID Application 인증서와 기존 workflow의 필수 `ALDEN_APPLE_*` 6개 secrets 부재로 차단된다. 로컬 ad-hoc ZIP/CPython sidecar/manifest/SHA256SUMS는 준비됐다. 후보 worker 활성화, main merge, 공개 서명·공증 릴리즈를 전달 완료로 표기하지 않는다.
- 아래 관측은 시간순 이력이며, 중간 SHA의 CI·후보·설치는 최신 판본의 근거로 대체하지 않는다.

| 요구사항 | 현재 단계 | 근거 / 다음 확인 |
| --- | --- | --- |
| 장치·브랜치·설치본·모델·실행 프로세스 확인 | 실제 실행 검증 | M5 Max, 128GB, AC 연결. Alden 0.1.6 최신 native-audio 설치(29/29 파일·26/26 resource 일치, 단일PID22050). 27B/E5 공유 서비스와 기존 Jarvis 음성 PID25865는 보존. |
| 맥락 오류 재현·최신 턴·역할·취소·중복·끼어들기 | 구현 | conversation/turn/context·출처·중복·취소 token, 단일 worker/최신 pending slot 구현. 집중 97/97. 한국어 후속 질문 12/12 두 판본. 실제 재생 중 끼어들기와 자연 발화는 미검증. |
| Tauri v2·Three.js·Alden 명칭·기존 데이터 호환 | 구현 | PR #27 구현 재사용. 설치본 화면과 권한/저장 경로 호환 재확인 필요. |
| 정확한 27B·Flash 가중치·리비전·로컬 전용 추론 | 구현 | 기존 27B와 Flash의 합성 생성 기록 있음. 27B 한국어 후속질문12/12×2와 설치 browser title1건 실제 성공. Flash는 현재메모리admission미달·listener없음. 둘을동시상주시킬근거는없음. |
| openWakeWord·mlx-whisper·Qwen3-TTS·음성 타이밍 | 구현·실제 실행 검증(오디오 일부) | 단일 native 입출력·녹음된 기준음 재생/취소 확인. 모든 입력 RMS0, 사람 발화·에코 품질 미검증. wake release gate 미달·메모리 gate 유지로 실제 STT/LLM/TTS 전체 턴은 미검증. |
| 로컬 browser-use·Playwright·macOS AX | 실제 실행 검증(일부) | 설치된 Python entrypoint가 local27B로 공개 title1건48.556초 성공, 독립 title 일치. 실제 Tauri caller·macOS AX·포커스 영향은 미검증. |
| 비상 중단·명시적 재개·외부 작업 중단 | 구현 | worker/native AX epoch fence 존재. 실제 단축키 입력과 응답 시간·중단 후 재실행 없음 확인 필요. |
| Alden 시각 디자인·토큰·3D 코어·창 크기·Retina | 구현 | 기존 DESIGN.md와 Archify 자료 존재. oh-my-design/style.gallery 직접 확인. 설치 창의 캡처·검토·동일 조건 재확인 필요. |
| 실제 부하·음량 매핑·숨김/잠금 렌더 중단·복원 | 구현 | 이전 Chromium stub 확인은 설치 WKWebView 증거가 아님. 현재 설치본 숨김 중단 지연·호출 수·루프/리스너/GPU 누적 검증 필요. |
| GraphRAG 정규화·별칭·출처·시간·삭제·BM25/Dense/RRF | 실제 실행 검증(일부) | 최종 source4cc536·실E5 synthetic13query Recall/nDCG=.9091/.9091·public context leak0. 설치 graph 6.522초 refresh·무결성·E5 watermark 일치. 지속 최신성·생산 quality·설치 그래프 탐색 UI는 별도. Threads 조회 실패. |
| 원본 DB 보호·일관된 스냅샷·증분 색인 | 실제 실행 검증 | 실제 암호화 DB+WAL private read-only/query_only 복제본 quick_check=ok. 설치 CLI 3방/8회·실제 새4건 private ingest·production mirror4/4 대조. 원본 연결/체크포인트 없이 진행. 실제 경합·삭제 반영·지속 최신성은 남음. |
| alphaXiv·DREAM-RSI·독립 평가·실제 DPO 로그확률 | 구현 | 논문/CLI 분석과 teacher-forced scorer 있음. 선호쌍·독립 검증·학습/모델 교체를 구분하고 실제 실행 증거 점검 필요. |
| 동일 조건 기준선·수치 목표·회귀·CPU/GPU/전력 | 구현 | 27B 동일 case 12×2, stream smoke3 및 final2/12, 취소2경계·실snapshot1·render hidden관측. 공유 캐시/부하 통제 불가. 전체 앱 CPU/GPU/전력과 음성 end-to-end는 미측정. |
| README·실제 캡처·구현에 맞는 6개 Archify 흐름 | 구현 | 기존 9개 다이어그램 점검 후 바뀐 경로만 갱신. 화면 캡처를 직접 검토해야 시각 검증으로 기록. |
| 커밋·푸시·PR·릴리즈 산출물·설치·프로덕션 대조 | 실제 실행 검증(일부) | code e03a678/fixture b987ad1 push, 원격5/5checks 성공. 0.1.6 local ZIP+runtime+설치28/28. PR draft/main 미병합, 공개 서명6credentials 부재, 기존 타세션 worker 미교체. |

## 이번 작업의 관측

- 2026-09-30: 목표 원문과 기존 구현/문서/원격 PR을 읽고 실행 프로세스·모델 카탈로그를 확인했다.
- 네이티브 CUA의 Alden 앱 조회와 앱 목록 호출은 timeout으로 종료했다. 앱 화면이나 사용자 입력 성공 증거가 아니다.
- 현재 27B 카탈로그는 `loaded=true, state=ready`, E5도 ready. 이 관측만으로 실제 생성 성공을 선언하지 않는다.
- 기존 Jarvis voice 상태의 `updated_at=1790168334`는 오래된 기록이다. PID의 존재만으로 정상 음성 대기를 판단하지 않는다.

## 다음 작업

1. 기존 타세션 worker 교체 허가가 도착하면 최신 큐/불명 전송을 읽어 확인하고, 최종 후보의 생산 경로 준비·안전 종료·활성화·3개 방 readback을 수행한다.
2. 컴퓨터 제어 연결 복구 후 설치 화면, 창 숨김/복원과 물리 비상 중단을 검증한다. 같은 timeout을 계속 재시도하지 않는다.
3. 검증된 wake 모델, 메모리 admission과 독립적인 재생 에코/끼어들기 경로를 갖춘 뒤 실제 음성 턴을 검증한다. 기존 공유 서비스나 다른 세션을 중단하여 조건을 만들지 않는다.
4. 기존 서명/공증 prerequisites가 제공되면 정상 PR/CI/main/release 흐름과 산출물·설치·production 버전 대조를 마무리한다.

22:17 KST 추가: native 코드effa5c6의 커밋·푸시·원격CI·설치·ZIP대조를 마쳤다. 전달 기록의 마지막 문서 커밋과 asset checksum을 정리한다. 사람이 발화할 수 있는 시점에 8초 기준음 시험을 수행한다. 원음/발화 내용은 저장하지 않으며 기존 모델 gate를 우회하지 않는다.

## 병렬 담당 (2026-09-30 사용자 추가 지침)

다른 저장소 세션의 완료를 기다리지 않는다. 설치·배포·커밋과 통합 담당은 부모 한 명이다.
측정 시 다른 세션의 CPU/GPU 부하를 기록하고 큰 빌드/추론이 겹친 표본은 별도로 취급한다.
하위 에이전트는 공유 서버·인증·앱·설정·원본 DB·자동응답 큐를 변경/재시작하지 않는다.

| 담당 | 목적·입력 | 쓰기 범위 | 중간 완료 / 인계 기준 |
| --- | --- | --- | --- |
| 부모 | 현재 음성 새 턴·취소·SSE·늦은 결과 폐기, snapshot 통합 | scripts/alden_voice.py, tests/test_alden_voice_turns.py, 기존 unit3/runtime 검사, snapshot 변경, 측정 CLI, 메타데이터·README·전달 기록 | 경합 검사 → 실제 로컬 모델 취소 카운터/실행 수 → 설치본 검증 → 커밋/PR/CI/릴리즈. 재생·마이크·설치 미검증을 명시. |
| Dirac / `01a0f13a-2cd8-7cf2-a391-2572afe34397` | 숨김/복원·단일 렌더 루프·자원 누적; 기존 renderer/lifecycle 입력 | desktop/src/core/**, 관련 desktop lifecycle/animation/render tests, 전용 render receipt/doc (main.ts 변경 필요 시 보고) | 필요한 구현·집중 검사·real renderer vs fixture 범위 및 수정 파일 목록. 빌드/설치는 부모가 실행. |
| Parfit / `01a0f13a-2d24-7520-93c6-c238f16f7050` | 한국어 검색 별칭·방/시점·출처·삭제/최신성 평가 | scripts/evaluate_alden_retrieval.py, tests/test_alden_retrieval_eval.py, 전용 fixtures/receipt/doc, 승인 후 production graph의 query 함수와 관련 query tests | 기존 평가 코드 재사용, 정답셋·Recall/nDCG·provenance 평가. 부모 snapshot 함수/검사는 보존. GPU 요청 전 조율. |
| Cicero / `01a0f13a-2d84-7b42-a6da-4b6849daf11f` | Astra 취소/맥락 경합 및 WAL snapshot 독립 검토 | 읽기 전용; 작은 synthetic 검증 허용 | 요청 `model=gpt-6-astra, reasoning_effort=max`. 실제 응답/가능한 route provenance 확인 후 판본·재현·필요 수정 보고. 모델 선택만으로 사용 성공 주장 금지. |

현재 기존 native 하위 에이전트는 없었으므로 위 세 개를 생성했다. 이후 해당 에이전트와 결과를 재사용한다.
새 사용자 채팅이나 standalone `codex exec`를 생성하지 않는다.

## 이번 변경의 검증 기록

- GraphRAG: 기존 snapshot 검사 7개 → SHM churn/rollback journal 결함을 두 새 fixture에서 재현 → 수정 후 graph 모듈 **94/94** 통과.
- 실제 plaintext context mirror 1,399,140,352 bytes: private replica `quick_check=ok`, `query_only=1`, 26 tables, source SQLite connection 없음. 갱신 감지 후 두 번째 copy 성공. 복사+검사 **5.314160초**, 표본 1. 전체 기간 source signature는 writer 활동으로 달라져 원본 무변경을 전체 기간 hash로 입증하지 않는다.
- 27B installed-voice adapter baseline SHA가 설치본과 일치. 한국어 3개 후속 질문×4, **12/12** 사실 포함, adapter 오류 0. full answer p50 **1.510852초**, 관측 p95 **2.016853초**. 해당 transport는 첫 토큰을 노출하지 않음.
- per-turn 취소 + owned socket 변경 후 동일 cases **12/12**, p50 **0.969950초**, 관측 p95 **1.268899초**. 캐시/동시 host 부하 통제 부족으로 변화율을 인과적인 성능 개선으로 주장하지 않음.
- 실제 non-stream 취소는 client **22.624ms** 종료, reply 없음이나 3초 후 backend running=1, cancel-counter delta=0. GPU 추론 중단 미달을 확인하여 SSE 경로로 수정했다. 아래 후속 SSE 관측에서도 backend 즉시 중단은 미달이다. 이 실패 receipt를 보존한다.
- pinned CPython 3.11 음성 runtime에서 race/socket 포함 **54/54** 통과. source/fake 검증은 실제 재생 중 끼어들기·마이크·STT/TTS 성공과 별개다.
- 현재 voice admission: reclaimable 약 53.14GiB, swap-free **1,211,168,194 bytes**. 기존 2GiB swap-free gate에 미달하므로 STT/TTS 실가동을 강제로 시작하지 않는다. 다른 세션 앱/프로세스를 중단해 메모리를 확보하지 않는다.

- 독립 Astra 검토는 초기 6건과 후속 3건을 재현했다. captured epoch queue, 지연 callback의 stale-only 제거, 전역 commit 경계, 종료 진입 차단, 최신 대기 slot, readiness GET 취소, detector 격리/초기화, state/processing lock 역전 제거와 취소 reply 정리를 구현했다. **97/97** 관련 검사 및 bare Python의 CI subset **27/27** 통과. voice scoped 최종 리뷰는 추가 지적 없이 통과했다. 그 뒤 accepted-turn 상태 즉시 게시 5줄과 관련 assert를 부모가 추가했으며 97/97 재검증했다. 검색 scoped 리뷰는 별도 진행 중이다.
- 실제 SSE cancel: 첫 token 전 **3.082ms**, 첫 token 뒤 **5.383ms** client 종료, reply 없음. backend idle **2.279/2.464초**, cancellation-counter 증가 0. 전역 물리 단축키·GPU 즉시 중단 성공으로 보고하지 않는다. 서버/API 문서는 chat stream을 지원하지만 명시적인 chat 취소 endpoint는 제공하지 않는다. 공유 MLX Core 재시작/수정 없이 남은 제한을 유지한다.
- 최종 스트림 timing은 요청 12개 중 **2개** 완료(사실2/2, 오류0), 다른 active inference 감지로 중단. first-visible **0.390/1.120초**, full-answer p50 **1.024초**, 관측 p95 **1.382초**. 작은 부분 표본으로 목표 달성·일반 성능 개선을 주장하지 않는다. receipt에 CPU load와 engine allocator scope 포함.
- 렌더링: **191/191**, 실제 Chromium/WebGL2 + synthetic bridge에서 hidden750ms 추가frame0;50cycles후listener1. 임시 server/tab 정리. 설치 AppKit/WKWebView·Retina·잠금은 미검증.
- voice Archify: 최종 deliver **9/9**, showcase 오류/경고0, browser visual-check pass. 1440×900 light capture를 부모가 직접 검토하고 별도 review sidecar에 기록. 사용자 콘텐츠는 한국어, 고정 Viewer UI는 영어 fallback.
- 현재 GitHub secrets 이름 조회 결과 **0개**, keychain에는 Apple Development만 있으며 Developer ID Application 없음. production workflow 필수 **ALDEN_APPLE_* 6개**가 없어 서명/공증 공개 릴리즈 불가. 가능한 local unsigned/ad-hoc 산출물과 PR/CI는 별도로 준비한다.
- 네이티브 CUA bundle-ID 앱 조회도 `timeoutReached(-10005)`. 설치 화면을 얻지 못한 세 번째 시도이며 화면 확인으로 취급하지 않는다.

- 최종 Astra scoped review: 새 3개 경합 재현 **3/3 통과**, 추가 지적 없음. 실제 provider response metadata는 비공개이므로 local child 요청 설정 `gpt-6-astra/max`까지만 provenance 확인.
- CI focused Python **931개** 최초 실행에서 legacy isolated-copy fake signature **1개 오류**, 기대 형식을 실제 DB/WAL/journal 3파일로 수정한 재현 통과. 최종 통합 CI는 검색 변경 뒤 실행한다. launchd artifact harness는 exit0.
- alphaXiv CLI는 현재 PATH에 없음. `orx --help`는 exit0, paper 명령 제공. 이미 읽은 공식 arXiv 원문과 저장소의 논문 분석/27B teacher-forced scorer 증거를 구분한다. 새로운 학습·제품 가중치 교체를 수행했다고 주장하지 않는다.
- CPython 3.11.16 arm64 sidecar는 기존 pinned upstream archive SHA를 검증해 `/private/tmp/alden-release-0.1.6-20260930`에 준비. 이미 설치된 공유 Python runtime은 변경하지 않음.

- 0.1.6 metadata 5개 일치, release CLI(1.8.6) build2m08s, frontend build통과. root Rust42suites **1,137passed/0failed/1ignored**. nativeapp build완료. 이후 voice/query 수정이 추가되어 최종resource재빌드와 source SHA대조 전 설치·배포완료로표기하지않음.

- 현재 원격 PR #27 head는 `74149a4`, 기존 5개 CI 성공 유지. 새 변경은 아직 미커밋이며 해당 CI를 새 변경의 통과 근거로 사용하지 않는다.
- 검색 독립 리뷰 4건: relation evidence 방/철회 우회, 다른 방 interval의 시간 필터 오염, malformed provenance의 seed 간주, dense 실패 시 BM25 weight=0 무시. 후보 지표만으로 관계 fact 누출을 놓치지 않도록 whole-context 평가를 추가 중이다.
- snapshot 새 Archify도 deliver9/9·browser bounds 통과 후 1440×900 light image를 직접 검토했다. 두 diagram의 실제 검토는 별도 review sidecar에 기록하고 자동 visual-check 원본 receipt는 변경하지 않았다.

- 갱신 목표의 사진 실패를 실제 ledger에서 확인: 화질 문구10행·구도8행, 각각 sent-state1건 포함(동일 event의 scheduled/sent 중복 제외 사진16events). 모델 미응답·인증·한도 실패 때 no-pixel helper가 임의 시각 판단을 생성한 원인이다. helper는 실제 미확인과 하나의 글 설명 질문으로 변경. 16 helper replay에서 근거 없는 시각 판단16→0, 새 전송0·modelcall0·sharedwrite0. 집중5개 통과. active immutable worker는 아직 기존 코드이므로 생산 수정 완료는 아님.
- Parfit 최종 graph source66bad941, 독립4finding 회귀 포함35집중검사 통과. 실제 E5 임시 synthetic graph13queries의 Recall3=.9091, nDCG3=.9091, p50/p95=25.694/29.970ms; heldout7 Recall3/nDCG3=1.0. 금지 후보/whole-context leak은 전체1/7 남음(철회/valid_to 없는 과거alias), 임의 merge로 숨기지 않음. 생산 인덱스와 큐는 미변경.

- 첫 소스 커밋 `8ceeea8`: 최신 턴/취소·worker no-pixel fallback·이미지 전달 실패 차단·renderer 수명 및0.1.6 metadata. 이미지 집중10/10. 실패한 사진은 partial/text-only 요청으로 우회하지 않고, 동일 입력으로 fallback 모델을 호출하지 않으며 이전 model-circuit 실패를 보존한 채 해당 lease만 종료한다. 아직 push/worker활성화 완료는 아님.
- 통합 focused CI 첫최종판본 **941 tests / 2 skips / 실패0, 100.534초**. 이후 독립리뷰에서 public `retrieve_knowledge_bundle` focus-expansion이 filtered relation을 덮어쓰는 P1 추가발견하여 후속수정 중. 941pass를 이 후속수정판본의 근거로 확대하지 않는다.
- 갱신 목표의 링크·파일 맥락은 Dirac가 읽기전용 source/fixture audit 담당. 사진은 부모, final graph bundle은 Parfit가 각각 맡는다.

- 최종 GraphRAG4cc536: public bundle이 filtered ranked record에서 facts/evidence/provenance를 같은 예산으로 반환하도록 raw focused-relation 대체 제거. Astra 독립 tiny fixture에서 다른 방/철회 relation 누출2→0, 정상 relation2와 provenance2 유지. FINAL 오프라인 RRF 전체 nDCG3=.9664, public whole-context leak0; BM25 과거 nonretracted alias1/7은 유지. 실제 E525.694/29.970ms는 이전66bad941판본의 근거이고 최종public수정의 실측으로 확대하지 않음.
- 통합후속 CI948 tests / 2 skips / 실패0,111.616초. Graph4cc536와 workerb69bd 판본. 이후 새 링크·파일 audit 결함과 cooldown fallback 이미지error수정은 별도 집중검사 대상. Native0.1.6 finalgraph bundle build20초 완료; 링크·파일 변경후 필요한 resource재빌드 예정이다.
- 링크·파일 audit: persisted recent-tail에서 카드 URL소실, refresh에서도URL복원누락, 일반file빈본문 empty_message, type26 file/quote shape계약누락, off-tail quote 정확 DBrow 검증없음을 확인. Dirac URL경로와 Parfit파일/quote계약 수정 담당. 네트워크·실DB·공유서비스·전송없음.
- 이미지 독립리뷰P2: 기존 primary쿨다운시 fallback의 image_input_unavailable을 providerfailure로닫고 다음모델시도. Parent가 terminalresult로바꿔 해당lease만release하고 다음시도없음. 새경계포함4집중검사 .168초pass; 마지막 scoped검토중.
- 새검색 Archify deliver9/9·showcase오류/경고0, browservisual-checkpass, 새1440×900light캡처직접검토및reviewSHA기록.

- Alden0.1.6 local ad-hoc 설치완료: 전체기존bundle추가백업 후owned수동0.1.5PID96077만정상종료. installerLaunchAgent활성화성공, PID50010단일/정확exe/28파일byte일치/launchd running. 설치후CUAgetApp도timeoutReached(-10005): native화면·물리shortcut·mic증거아님. 설치backendread-onlysnapshot수집, browserentrypoint1공개title경로측정진행.
- 이미지fallback최종Astra2tiny재현pass: cooldown후 no2ndfallback/nofailure/model_invokedFalse; syntheticprimary429후 primaryfailure1회/fallbackfailure0/model_invokedTrue. fallbacklease1회release, priorfailure3→3, evidence[]유지. SHA6cd1a59, fake/privateDB만사용.

- 갱신목표576db5bd전체읽음: 최우선항목에 이미지·링크·파일 실패제거가명시됨. 진행중인미디어출처/후속맥락수정과설치·전달범위를유지. 설치된browserPython진입점+고정27B공개title1건48.556초성공, 독립HTTPtitle일치. Tauri실제UIinvoke/포커스미측정. 등록enrollment가있는기존legacybujamentor경로backend에서3/3ready, healthAX/model/supervisor/watchdog/worker모두ok, delivery_unknown0. 잘못지정한modernauto-reply빈rootprobe는실제app상태근거에서제외.

- 최종installedgraph동일SHA4cc536 실제E5재평가13syntheticqueries: Recall3/nDCG3=.9091/.9091, p50/p9524.941/45.865ms, publicwhole-contextleak0. 기존modelrev5030c762,23tempentitiesindex132.382ms, 생산graph/queue미변경. hostload16.0→16.7/18logical로증가; latency개선율주장없음.
- native현재processdescendants포함20샘플: ps평생평균CPU중앙0.0%, RSS합중앙105,660,416bytes. visibility는CUA미확인, sharedpage중복/engine별도/실제intervalCPU·GPU·power아님. 효율개선비교근거로사용하지않음.
- 최신localZIP11,514,743bytes SHA40056f0e와CPython3.11.16sidecar3자산·notes·SHA256SUMS를ignored dist/alden-0.1.6-local에보존. 중간zip도임시directory별도로보존. 최종commitSHA대조예정.

- 실제27Bcatalogloaded/ready지만vision capability없음을확인. 이미지adapter는이제명시적vision없으면generationPOST를하지않고 truthfulimage_unavailable로종료, fallback실패/cooldown으로기록하지않음. 실제catalogGET2개만사용한경계검증POST0; vision선언모델의기존imageattachfake경로는보존. 21집중검사pass. 현재로컬pixel해석은vision모델비상주/기능미제공으로미완료.
- GraphRAG최종소스·평가셋·회귀·측정기록을별도commit11bfae7로보존. media파일변경은아직에이전트마무리중으로별도통합예정.

- source8ceeea8와graph11bfae7을기존PR27branch에normalfast-forwardpush완료. remotehead11bfae70686897dcf3da1c15f93d0e8028598745 readback일치. CI36700931809진행중. 아직finalmediafix미커밋이며이CI를그수정판본통과로표기하지않음.
- 19:07KST read-onlyadmission: reclaimable63,026,053,120bytes, swap-free1,227,945,410bytes<기존2,147,483,648조건. STT/TTS별reclaimableminimum은8/10GiB로충족하지만swap조건미달. RELEASED_WAKE_MODEL=None유지. gate완화/다른세션종료/모델재시작없음.

- 최종 파일/인용 소스 인계: watcher b3b858ce, worker64114bb4, 집중16/16. type26 quote/file 분리, authoritative poll exact row, metadata-only 파일과 바로 다음 같은 작성자 300초 내 후속 질문은 내용 미확인 안내. 다운로드/파서/실파일 요약은 미구현. 최종 통합 focused CI와 Astra 1회 검토 진행.
- 원격CI36700931809 head11bfae7 completed/success 확인. 이 CI는 미커밋 최종 미디어 변경을 포함하지 않는다.

- 최종 CI59 selectors/958 tests/2skips/실패0, unittest102.504초(process102.823초), pinnedCPython3.11.9. watcher b3b858ce/worker64114bb4/graph4cc536. 변경된CLI test9개 모두CI에포함. 이검사를실제마이크·AX전송·설치화면성공으로확대하지않음.

- 최종worker후보를격리private state에prepare:20assets/18source완전일치,기존3selectors/configexact일치, sharedstableCLI inode/mtime/SHA불변,activatedFalse. 다른세션의현재runtime20260930T0650Z-termination-74149a4유지.

- 0.1.6 built assets 실제Chromium/WebGL2/public-empty fixture 캡처3장(default/compact/2x)을부모가직접판독. 코어frame내위치,compact한열/가로잘림없음확인. 2x는browser DPR에뮬레이션이며installedRetina/WKWebView증거아님. 관련PNG/hash/review를README에연결;nativeCUA재시도없음.

- 중단후복원된Cicero실행metadata가gpt-6.1-sol/max로달라추가검증중단. tiny2개에서최근파일출처 P2발견: is_self누락/outgoing/cross-room도참조. 부모가명시적non-self/incoming·동일방검증과8negativeassert추가,4focusedpass. 요청된Astra/max새native review와수정판exactCI만재실행중. 이전958receipt/후보는해당P2이전판본으로보존.

- 최근파일출처 P2수정판 exactCI59selectors/958tests/2skips/실패0, unittest93.497초(process93.728초). 모든프로젝트필수선택자와기존graph/voice범위포함. 수정판receipt를별도보존하며Astra최종독립검증과합산하지않음.

- 새Astra/max actualturnmetadata확인, bounded3fakegroups:최근file출처10negative등pass. 추가P2:일반질문+recentfile에서instructions초기화전참조로UnboundLocalError. publicgenerate_reply새test에서수정전재현→block순서만수정후23focusedpass/.194초. 새selectorCI추가,최종60selectors실행중.

- 최종prompt순서수정 commit e03a678. exactCI60selectors/959tests/2skips/실패0, unittest97.265초(process97.498초). 변경CLI selector10개모두CI에포함. localpackage source SHA e03a678/25resources설치일치/ZIP40056f0e유지; 별도worker는app미포함. 현재최종Astra singlefollow-up만남음.

- 최종Astra1/1/.152초pass:promptblock외byte불변(역이동SHA로이전판본일치). worker c9b576a9/watcher b3b858ce. commit e03a678후보20assets/18source일치/3selectorsprivateexact/activatedFalse/sharedCLI불변. code/tests/독립review/후보준비완료. docscommit/push/해당SHA CI계속진행,기존타세션worker교체는명시owner허가대기.

- 최종delivery docs e485c42 normalpush/readback일치. remote CI36705693575에서2fixture실패:operator-state미격리로alden_global_abort보류. production guard유지,3tests를owner-private-temp에격리. 관련23tests1.021초pass,CPython3.11.9 -S exact959tests/13skips/실패0(100.396초). hostedrunner와같다고주장하지않고수정commit의새remoteCI에서확인예정.
