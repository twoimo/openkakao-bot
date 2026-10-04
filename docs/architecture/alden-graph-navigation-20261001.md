# Alden 그래프 탐색 복원과 네이티브 설정 검증

소스 `51f71c2fd7b5efd2c56a1e063d102779c5825343`의 Alden 0.1.6을 설치했다. 빌드와 설치의 **30/30 파일·27/27 리소스**가 바이트 일치하고 ad-hoc 서명 strict 검증을 통과했다. 상시 설치본과 동일한 바이너리의 별도 인스턴스에서 실제 WKWebView·Three.js를 확인했다. 상시 앱의 현재 화면이나 전체 제품 검증으로 확대하지 않는다.

## 원인과 수정

이전 그래프에는 돌아가기 기록이 없었다. 전체 보기 뒤 상세가 남고, A→B→A 선택에서는 처음 A의 늦은 조회가 현재 A를 덮어쓸 수 있었다. 이전 위치를 최대32단계 보관하고 **이전·전체 보기**를 추가했다. 초점·확장 깊이와 결정적인 카메라 목표를 복원한다. 이전 애니메이션 프레임의 정확한 카메라 자세를 저장하는 기능은 아니다.

선택마다 증가하는 epoch로 성공·실패·reset·back·dispose 경합을 막는다. dispose 이후에는 조회/렌더를 다시 시작하지 않고 버튼 listener도 해제한다. 최대24노드·3단계 깊이 예산은 유지한다.

네이티브 설정 감사는 fixed bundled script의 `mode=ro`·`query_only` 경로만 등록한다. seed·migration·reindex·snapshot·Dense/LLM·자동 전송을 호출하지 않는다. 실제 저장된 그래프의 렌더링과 DOM 버튼 동작을 검사하며, GraphRAG focus 조회와 나머지 설정 backend는 unavailable이다. 이 경로의 성공을 실제 검색 품질이나 전체 설정 기능의 성공으로 보고하지 않는다.

## 실제 설치본 결과

| 확인 | 결과 | 표본/범위 |
| --- | --- | --- |
| 선택→확장→다른 선택→이전3회 | 초점·깊이·카메라 목표·버튼 상태 복원 | 설치 바이너리1회, DOM 동작 |
| 전체 보기 | 제목·관계·조회 상세 초기화 | 같은 실행 |
| 기본/최소 설정 | DOM960×848 /640×648, 가로 넘침·context loss 없음 | 실제 콘텐츠960×880 /640×680 |
| 합성 화면 잠자기 알림 | 중단 관측 상한 13.051ms, 숨김350ms 렌더0 | 프로세스 내부1회 |
| 합성 세션 비활성 알림 | 상한 14.569ms, 렌더0 | 프로세스 내부1회 |
| 코어 일반 숨김·복원 | 렌더0, 상한 median 14.629ms /max 26.517ms | 10회×350ms |

설정 감사 4.906초, 코어 감사 8.581초 각1회이며 exit0와 `success=true`를 함께 확인했다. p95·전후 개선율·앱 전체 전력 절감률은 미측정이다. 물리 모니터 배율은1.0으로 Retina2×를 확인하지 못했다.

설정 창은 감사 인스턴스에서만 floating level로 앞에 두고 키보드 활성화 호출을 하지 않았다. DOM focus=false, visibility=visible에서 실제 프레임 진행을 요구했다. 이 조건은 production normal-level 창의 물리 포커스 동작을 증명하지 않는다. 네이티브 contentLayoutRect를 읽어 DOM 치수와 비교하며32px을 상수로 가정하지 않는다.

초기 창 치수 가정과 가려진 창의 blank graph는 검사에서 실패했으며 합격으로 계산하지 않았다. 그 준비 과정에서 foreign-exception abort2회가 있었고 원인은 미해결이다. 최종 후보·설치 실행 성공이 해당 실패 경로의 원인 해결을 증명하지 않는다. 실패 로그와 native PNG는 private에 보존한다.

같은 설치 바이너리에 compact PNG 파일 충돌을 유도해 실제 탐색·resize 뒤의 실패 경로를 확인했다. **exit1·유효한 failure JSON·기존 파일 보존**, 강제 종료와 abort 없음(1회·3.629초, 자식 실행 전체 시간). [실패 readback](alden-graph-navigation-failure-20261001.json). 과거 두 crash의 main thread는 Tao 외부 예외 cleanup, 감사 worker는 `openat`/`Output::write`에 있었지만 macOS 예외 사유와 원래 실패 바이너리는 확보하지 못했다. 이는 쓰기나 종료가 원인이라는 증거가 아니며 모든 실패 경로의 안전성을 입증하지 않는다. 원인 미해결과 전체 목표의 남은 gates를 유지한다.

## 검사·화면·다이어그램

UI199/199, desktop Rust90/90, pinned CPython3.11.9 필수1,056개/26skip/실패0(108.049초), Clippy all-targets `-D warnings`, TypeScript/Vite·offline native build를 통과했다. UI 경합 테스트는 controlled adapters이며 네이티브 WebGL 증거와 구분한다. RO 회귀는 writable connector·seed·migration·reindex·embedding 경로를 금지하고 실제 테스트 DB SHA 불변·missing root fail-closed를 확인했다.

부모가 실제 기본·최소 설정과 코어 PNG를 이미지로 읽었다. 설정 PNG와 원본 process receipts는 개인 자료 노출을 피하기 위해 Git·릴리즈에서 제외한다. 공개 숫자 JSON에는 그래프 label/id/fact, PID, 개인 실행 경로와 process samples가 없다.

[실제 구현의 Archify workflow](alden-graph-navigation-20261001.html)는9/9 showcase, 오류0·경고0이다. 자동 browser evidence는1440×900·1600×1000·1920×1080·2048×1320 containment를 통과했고, 두 endpoint의 밝은/어두운 실제 이미지4개를 직접 확인했다. 수정2회, visual review passed. 한국어 본문과 고정 영어 Viewer UI/html lang fallback을 구분한다. [artifact/SHA receipt](alden-graph-navigation-archify-20261001.json).

## 전달과 남은 범위

[후보 초안](https://github.com/twoimo/openkakao-bot/releases/tag/untagged-25ad469edd80663739aa)에7파일을 올리고 모두 다시 다운로드해 byte/digest를 확인했다. ZIP30파일은 설치본과 일치하고 소스/증거 overlay30파일 hash도 일치한다. 코드`51f71c2`, 증거/릴리즈target`5cfea2b`, [CI](https://github.com/twoimo/openkakao-bot/actions/runs/36799335869)4/4 성공(hosted Python1056/83skip/실패0·45.479초). [CI 기록](alden-graph-navigation-ci-20261001.json), [릴리즈 대조](alden-graph-navigation-release-20261001.json). 부모·독립 reviewer가 실제 설치 이미지4개·숫자·개인정보 제거를 확인했다. 요청된gpt-6-astra/max 경로는 attestation 불가이며 대체하지 않았다. [설정 감사](alden-graph-navigation-settings-20261001.json), [코어 감사](alden-graph-navigation-core-20261001.json), [측정 요약](alden-graph-navigation-summary-20261001.json), [설치](alden-graph-navigation-install-20261001.json), [검사](alden-graph-navigation-tests-20261001.json), [독립 검토](alden-graph-navigation-review-20261001.json).

| 항목 | 단계 | 범위 |
| --- | --- | --- |
| 그래프 이전·전체 보기·epoch/dispose fence | 구현 | 집중·필수 회귀 통과 |
| 설치·별도 WKWebView 설정/코어 | 실제 실행 검증 | real persisted graph, 감사 전용 가시성 조건 |
| Git·후보 초안 산출물 | 전달 완료 | 7asset byte/digest readback, CI4/4; 공개 공증/production과 구분 |
| 전체11개 목표·생산·공개 signed release | 미완료 | 기존 gates 유지 |

MLX와 기존 자동 답변 작업자의 process identity는 유지했다. 물리 잠금·세션 전환·tray·비상 단축키·사람 음성·Retina·전체 설정 backend와 production worker 교체는 미완료다. swap-free1,387.06M는 음성2GiB gate 미만이며 wake release/human mic gate도 유지한다. Developer ID Application0개와 release workflow의 Apple secret6개가 없어 공개 서명·공증/프로덕션 릴리즈는 차단돼 있다. 전체11개 목표는 계속 진행 중이다.
