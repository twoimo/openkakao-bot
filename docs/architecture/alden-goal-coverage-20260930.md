# Alden 목표별 현재 근거 — 2026-09-30

2026-10-03 최신 이어받기: [0.3.10 메모리 재현·설치 근거](alden-embedding-memory-20261003.md), [현재 진행 기록](../ALDEN_CONTINUATION.md). 과거 기록은 당시 판본의 근거이며 전체 목표는 진행 중이다.

## 현재 판본 — 2026-10-03

전체 11항목은 **진행 중**이며 아래 날짜별 기록은 각 당시의 근거다. 설치된 0.3.7은 [우주 그래프·사이드바](alden-universe-20261002.md)를 유지하며 [실제 재생 PCM과 입력 진폭](alden-playback-amplitude-20261003.md)을 구분한다. [35개 설치 파일·ABI2와 초안 릴리즈6개 다운로드 대조](alden-playback-amplitude-install-20261003.json), 정확한 소스 SHA의 CI4/4 성공을 확인했다. 원본 DB 접근 승인은 여전히 대기 중이며 기존 게시 corpus만 사용한다.

| 범위 | 현재 단계와 근거 | 남은 확인 |
| --- | --- | --- |
| 1 실행·보존 | 전달 완료: 판본·모델·설정·enrollment·안정 CLI 대조 | 전체 목표 완료 판정은 보류 |
| 2 맥락·턴·취소 | 실제 실행 검증: [설치 음성 8턴·문맥 12회](alden-voice-sustained-20261002.md), 통제된 역할·방/시간 범위 회귀 | 자연 발화·에코·물리 끼어들기 |
| 3 로컬 모델 | 실제 실행 검증: 정확한 상주 27B/E5와 Whisper·MPS/BF16 TTS | Flash-Next 동시 admission, 제품 이미지 경로의 운영 적용 |
| 4 음성·자동화 | 구현/부분 실행: 8턴 파일 파이프라인; TTS 별도 ASR 7/8 정확 | 첫 파일 청취, 배포된 한국어 wake, 마이크·재생·물리 비상 중단 |
| 5 디자인 | 전달 완료: 우주 그래프·사이드바·설정·popover 0.3.7 | 현재 primary CUA timeout 때문에 물리 UI·Retina는 미검증 |
| 6 렌더·효율 | 전달 완료/실제 실행 검증: [0.3.7 PCM envelope](alden-playback-amplitude-20261003.md), 실제 SDK/기존 Qwen WAV144구간 진폭 일치·취소/시작 전0, 개발 fixture idle 1초×3 렌더0·숨김1초 조회/렌더0 | 실제 마이크·기기/스피커 진폭, 앱 전체 전력 |
| 7 지식 검색 | 전달 완료: [정규화·single snapshot](alden-graph-quality-20261002.md), [독립 heldout 평가](alden-retrieval-v2-20261002.md), 설치 RRF 범위 회귀 | 권한 의존 원본 최신 수집과 데이터 반영 지연 |
| 8 평가·학습 | 구현/부분 실제 실행: 독립 validation·실제 27B 오프라인 DPO | 최종 test와 제품 정확성/검색/지연, 승인된 promotion |
| 9 성능 | 부분 실제 실행: 표본·범위를 밝힌 검색·렌더·음성/LLM 지연 | tokens/s, 실제 UI/재생 p95, 물리 중단과 전력, 목표 미달 개선 |
| 10 문서·도식 | 전달 완료: 실제 코드 기반 Archify·캡처·receipt | 후속 구현이 바뀌면 해당 도식 갱신 |
| 11 전달 | 전달 완료: PR27·정상 push·정확 SHA CI·ad-hoc 설치·초안 asset 대조 | Developer ID/공증 자격 증명, 승인된 생산 작업자 전환 |

현재 source/설치 UI/개발 fixture/생산 작업자의 검증 범위를 혼합하지 않는다. 승인 대기 때문에 독립 작업까지 중단하지 않으며, 승인/TCC·생산 작업자·공유 모델을 우회하거나 교체하지 않는다.

2026-10-01 상시 설치본 추가: [실제 메뉴바·기본/최소 설정](alden-primary-ui-20261001.md)을 기존 primary process에서 확인했다. 코어276×260·normal-level 설정960×880/640×680 이미지3개를 직접 검토했고 크기 복원·native 가시 창0을 확인했다. AXPress만으로는 열리지 않아 짧은 실제 CG 마우스 이벤트를 사용했으며 독립 가상 커서로 보고하지 않는다. 제품 소스·운영 작업자·메시지 전송 변경0. 각1회 창 표시 관측은 rendered UI p95와 구분하며 graph navigation·Retina·물리 중단/잠금·음성·production의 남은 목표를 유지한다.

2026-10-01 실패 경로 추가 검증: 설치본의 실제 그래프 탐색·resize 뒤 compact PNG 충돌1회는 exit1·유효한 failure JSON·기존 파일 보존으로 종료했다(3.629초, 전체 자식 실행). [근거](alden-graph-navigation-failure-20261001.json). 이전 foreign-exception2회 원인은 여전히 미해결이며 코드 변경·운영 교체·전체 목표 완료로 계산하지 않는다.

전달: [Alden 그래프 탐색 후보 초안](https://github.com/twoimo/openkakao-bot/releases/tag/untagged-25ad469edd80663739aa)의 **7개 파일**을 다시 다운로드해 바이트·원격 digest를 대조했다. ZIP30파일은 설치본과 일치하고 overlay30파일도 hash 일치다. 소스`51f71c2`/증거`5cfea2b`의 [CI](https://github.com/twoimo/openkakao-bot/actions/runs/36799335869)4/4 성공·독립 설치/개인정보 검토 통과. [릴리즈 대조](alden-graph-navigation-release-20261001.json). 공개 공증/프로덕션 완료를 뜻하지 않는다.


2026-10-01 추가: [그래프 탐색 복원·설정 렌더링](alden-graph-navigation-20261001.md)을 소스`51f71c2`의 설치 바이너리에서 확인했다. 이전32단계·전체 보기·A→B→A epoch/dispose fence, 실제 persisted graph·기본/최소 WKWebView·숨김350ms 렌더0·재개 frame 진행, 설치30/30·resource27/27, UI199/Rust90/Python1056·Clippy/build 통과다. [Archify](alden-graph-navigation-20261001.html)9/9·4viewport·이미지4개 검토도 통과했다. 감사용 floating 창과 unavailable 다른 backend라는 범위, private native PNG·초기 foreign-exception2회 미해결을 기록한다. 전체 목표·물리UX·음성·worker·signed release/production은 미완료이며 Git/CI/초안 전달은 후속 readback으로 대조한다.


전달: [Alden 0.1.6 화면 가시성 후보 초안](https://github.com/twoimo/openkakao-bot/releases/tag/untagged-6730d9c69d5ac88931de)에 7개 파일을 올리고 다시 다운로드하여 모두 바이트 일치를 확인했다. 앱 ZIP 내부30파일·소스/증거 overlay23파일도 대조했다. 소스 `8791ea1`의 [CI](https://github.com/twoimo/openkakao-bot/actions/runs/36793165993)는 4/4 성공이다. 초안 target/overlay는 증거 checkout `b7bdda9`이며 공개 공증/프로덕션 완료를 뜻하지 않는다.


2026-10-01 추가: [화면 잠자기·세션 전환 처리](alden-workspace-20261001.md)를 소스 `8791ea1`의 Alden 0.1.6 설치본에서 검증했다. 별도 설치 바이너리의 두 **프로세스 내부 합성 알림** 모두 두 창 숨김·350ms 추가 렌더0, 일반 숨김·복원10회도 렌더0이다. 네이티브 숨김 뒤 DOM 재개 차단과 종료 구독 정리를 추가했으며 Rust90/UI194/Clippy/build 통과·설치30/30·리소스27/27 일치를 확인했다. 물리 잠자기·세션 전환·잠금과 설정 렌더러는 미검증이며 전체 목표/공개 릴리즈/프로덕션은 미완료다. 새 Archify 도식은 가독성 미통과로 미전달이며 기존 검증 도식을 보존한다. Git·CI·릴리즈의 최종 상태는 후속 readback으로 대조한다.


네이티브 전달 링크: [Alden 0.1.6 로컬 후보 초안 릴리즈](https://github.com/twoimo/openkakao-bot/releases/tag/untagged-179cb6167afd74a68bd5). 앱 ZIP·Python 동반 실행 환경·소스/증거·체크섬 **7개 산출물**을 다운로드하여 원본과 바이트 일치를 확인했다. 증거 커밋 `60fdb04`의 [CI](https://github.com/twoimo/openkakao-bot/actions/runs/36790224085)도 **4/4 성공**이다. [전달 readback](alden-native-render-release-20261001.json)에 소스·설치·릴리즈의 범위를 대조했으며 공개 공증/프로덕션 완료를 뜻하지 않는다.


2026-10-01 최신 네이티브 검증: 소스 `4838fff`의 Alden 0.1.6을 설치했고 **30/30 파일·27/27 리소스**가 일치했다. 설치 바이너리의 별도 WKWebView 인스턴스에서 실제 코어 이미지를 확인하고 **10회 × 350ms 숨김 추가 프레임 0**, 복원 3–4프레임/250ms를 관측했다. 숨김 요청→중단 확인 상한 median **14.622ms**, max **28.442ms**(n=10)다. [화면·측정·범위](alden-native-render-20261001.md)와 [Archify](alden-native-render-20261001.html)를 제공한다. 물리 화면은 [1.0]뿐이므로 Retina, 확장 설정, 상시 PID의 현재 화면, OS 잠금, 물리 단축키와 음성·생산·공개 서명 릴리즈는 미완료다. 기존 기록은 각 당시 결과로 보존한다.


2026-10-01 학습 추가: [실제27B 오프라인 DPO](alden-dpo-training-20261001.md)는 합성 train3쌍의 실제 업데이트3회·private adapter 저장·새 base 재로딩·adapter별 validation/cache를 검증했다. 최종학습37.907초, 별도평가24.809초/cache8.899초 각1회·network0·원본SHA/공유124→124/swap 유지. 집중126·필수1054/26skip/실패0. 평균손실 변화와 validation1문항 악화를 공개하며 최종test·대화정확성·검색·제품지연/메모리·promotion은 미완료다. 아래의 과거 “실제 DPO 학습 미완료”는 당시 판본의 기록이다. 전체11항목 목표는 계속 진행 중이다.

2026-10-01 평가 추가: [버전별 오프라인 validation과 빌드 hook](alden-version-evaluation-20261001.md), actual27B first24.772초/cache8.567초 각1회·load1→0·network0, 필수1040/17skip/실패0·독립source검토·network-deny 실제 앱build 성공. 아래 Sep30 표는 이력이다. 실제 DPO 학습·제품 promotion·설치 상주 평가·음성/nativeUI/공개 signed release는 미완료다.

2026-10-01 사진 문맥 추가: [원본 photo ID와 질문 분리·취소 복구](alden-photo-followup-20261001.md)는 미래/foreign 사진 선택을 차단하고 소유한 CLI·자식/scratch 정리를 구현했다. 같은 27B의 clean isolated 후속 1회 정답·16.325초를 확인했으며 private fixture seams와 실제 운영을 구분한다. 필수1027/17skip/실패0, worker 후보21/19·기존3selectors/config 일치·미활성화. 설치 앱 사진 기억·운영 적용과 11항목 전체 목표는 여전히 미완료다.

2026-10-01 사진 추가: [같은27B owned 비전 검증](alden-local-vision-20261001.md)은 같은 가중치로 public fixture4/4와 실제 worker 생성 함수의 image+JSON 요청1회를 확인했다. 기존 공유 서버는 `--no-vision`이며 운영 사진 경로는 여전히 미완료다. namespace catalog 용량과 MLX allocation, 텍스트 후속과 사진 기억, private adapter 경로와 운영 caller를 구분한다. 아래 Sep30 표의 실제vision 미완료는 당시 기록이다.


2026-10-01 음성 추가: [WAV 취소·게시](alden-voice-wav-20261001.md)는 설치 writer의 취소 중 덮어쓰기 재현·private atomic output·로컬/턴/세션 취소 및 새 root0700 수정과 집중58/필수1020·원격CI4/4, 설치30/27 대조를 마쳤다. 실제 STT admission은 기존 swap 조건 미달로 종료한다. 사람 음성 및 전체 pipeline 성공·native 화면·production 공개 전달은 미완료다. 현재 menubar Python3.11.9와 배포 sidecar3.11.16을 구분하며, 아래 표는 해당 시각의 이력이다.

2026-10-01 파일 추가: [내용 읽기](alden-file-content-20261001.md)는 구현·설치 파서·합성 문서의 실제 local27B 질의4/4까지 검증했다. code c29e263 원격CI4/4 성공, 설치30/27 일치. 실제 Kakao 파일 transport·운영 worker 활성화는 미완료다. 아래 Sep30 표는 이력이다.

2026-10-01 추가: [단일 snapshot·원자적 graph cycle](alden-graph-cycle-20261001.md), 필수981/16skip/실패0, 설치29/26 대조와 실제E5 1회 갱신을 확인했다. 기존 표의 Sep30 관측은 이력이며 native UI·사람 음성·production worker·공개 릴리즈의 미완료 범위를 유지한다.
전체 목표는 **진행 중**이다. 최신 진행 기록은 [ALDEN_DELIVERY](../ALDEN_DELIVERY.md)다.
과거 설치·공유 작업자 기록은 해당 날짜와 판본의 근거로만 사용한다. 이번 소스 변경은
기존 MLX/E5 서비스, 카카오 세션, 전송 큐, 다른 세션의 앱·설정을 재시작하거나 교체하지 않는다.

| 요구사항 | 이번 작업에서 확인한 근거 | 남은 완료 근거 |
| --- | --- | --- |
| 최신 맥락·턴·취소·출처·중복 | conversation/turn/context ID, 최신 pending slot, 취소 epoch와 늦은 결과 폐기. native processed input의 연속 3프레임으로 이전 재생/턴 취소·앞부분 보존, 집중 CI971/2skip/실패0 | 실제 설치 앱의 자연 대화와 사람의 음성 끼어들기·에코 품질 |
| Tauri v2 / Three.js / Alden | 기존 구현 재사용, 0.1.6 release CLI·frontend·native 오디오 번들. 26/26 resource·29/29 설치파일·단일PID22050·ad-hoc 서명 확인 | native 화면/Retina는 CUA 타임아웃으로 미검증 |
| 숨김/복원·GPU 자원 수명 | 191 frontend 검사, 실제 Chromium/WebGL2와 합성 Tauri bridge에서 숨김 750ms 추가 frame 0; 50회 복원 후 listener 1 | 설치 AppKit/WKWebView 숨김·잠금·Retina 관측, 앱 전체 GPU/배터리 측정 |
| 로컬 27B / Flash-Next | resident 27B에서 한국어 후속 질문 12/12 두 판본. 정확한 모델과 checkpoint 기록 유지 | Flash/iQ 현재 admission·생성 및 동일 조건 cold/warm 비교. 공유 서버 설정을 바꾸지 않음 |
| 웨이크 → STT → LLM → Qwen TTS | 체크포인트·로컬 runtime 존재, wake release gate 및 메모리 admission 유지. native 입출력·녹음된 기준음 재생/취소 일부 실제 검증, 설치 waiter 종료1회12.418ms | 입력은 RMS0. 검증된 wake 모델, 실제 음성 턴과 사람 발화·에코·소음·침묵·짧은 발화·끼어들기 |
| 비상 중단·명시적 재개 | 취소 전 epoch의 작업 재개 방지, owned HTTP socket 중단. 실제 SSE client 3.082–5.383ms 종료, reply 없음 | 물리 단축키, 재생·외부 작업 전체 중단. backend idle은 2.279–2.464초, 즉시 추론 중단 미달 |
| Browser-use / macOS AX | 설치된 Python browser entrypoint가 local27B로 공개 title1건48.556초 성공, 독립 title 일치. live send 없이 AX 회귀 | 실제 Tauri UI caller·macOS AX·포커스 영향. 네이티브 CUA app/inventory 조회는 timeout |
| 사진·링크·파일 맥락 | 실제 ledger 사진 실패16events helper replay, 이미지 누락/읽기 실패의 HTTP 요청 차단 및 recipient/register 전달. 이미지10검사 | active worker는기존판본. 링크recent-tail/refresh,파일metadata/후속맥락,off-tailquote수정완료. 최종통합959 tests/2skips/실패0. 실제vision/file내용읽기는미완료 |
| GraphRAG / BM25 + Dense RRF | 13syntheticqueries dev6/heldout7, finalfilteredbundle4cc536. 실제E5Recall3/nDCG3=.9091/.9091,24.941/45.865ms,whole-contextleak0 | 독립누출tiny2→0. 과거valid_to없는aliascandidate1/7유지. 설치graph시각drill-down및productionquality미검증 |
| 읽기 전용 DB/WAL 스냅샷 | 실제 암호화 DB+WAL(835MiB/4.46MiB) private SQLCipher4.6.1 read-only/query_only quick_check=ok. 설치 CLI3방/8회·실제 새4건0.535초 private ingest·production mirror4/4 digest/context row 대조. 설치graph6.522초1회 refresh·무결성·E5 watermark 일치 | 원본 DB metadata는 자연 writer 중 변경됨. 경합·삭제 반영·지속 최신성·생산 검색 quality는 미검증. [범위와 수치](alden-encrypted-snapshot-20260930.md) |
| 동시 DB writer/checkpoint | 독립 synthetic WAL 시험: 80 snapshots, 95 commits, 19 checkpoints, 실패/혼합 transaction 0 | 실제 생산 암호화 DB의 접근·경합 조건 |
| DREAM-RSI / DPO | 공식 논문 분석과 기존 teacher-forced 실제 로그확률 scorer 근거 유지. alphaXiv PATH 없음, orx help 사용 가능 | 선호쌍 평가·독립 검증·실제 학습·모델 교체를 각각 검증. ln(2) 불변식은 품질 개선 근거가 아님 |
| 디자인·README·Archify | voice/snapshot/search 새 흐름 deliver9/9 각각, browser bounds 검사와 실제 이미지 검토. 기존 8개 구조 검사 통과 | 설치 화면의 짧은 수정/재검토 루프. 구조 검사와 화면 검토를 구분 |
| Git / CI / 산출물 / 설치 / 공개 배포 | native codeeffa5c6 normalpush, 해당 원격CI4/4작업 성공. 로컬Python971/2skip·hosted971/73skip·실패0. 0.1.6설치29/29·resource26/26·ZIP29/29byte대조, 이전패키지보존 | PR #27 draft/main 미병합, 최종worker 미활성화. 공개 서명/공증은 Developer ID 및 workflow 필수 ALDEN_APPLE_* 6개 부재로 차단 |

수치 비교에서 캐시와 다른 세션 부하를 통제하지 못한 표본은 인과적인 성능 개선으로 해석하지 않는다.
스트림 최종 timing은 12개 요청 중 2개만 완료한 부분 표본이며, 전체 앱 CPU/GPU/전력과 음성 end-to-end는 미측정이다.
공개 production release와 로컬 ad-hoc 설치는 별개의 전달 단계다. 최신 native codeeffa5c6와 [원격 CI](alden-native-audio-remote-ci-20260930.json)를 기준으로 읽고 e03a678 worker 후보 및 b987ad1 fixture 이력은 별도로 보존한다. Hosted 건너뜀 수는 로컬 검사와 다르다. 전체 목표는 실제 음성·설치 창 검증과 생산 worker·공개 릴리즈 전달까지 계속 진행 중이다.
