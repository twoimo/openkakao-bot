# Alden OSK·읽기 전용 MCP 0.2.0

OSK 내부 동기화는 직접 API를 유지한다. MCP는 외부 클라이언트가 앱 판본·OSK 집계·고정 local endpoint 상태를 읽는 경계다. 구현·bundle staging·parent 회귀31개는 통과했다. 0.3.11 bundle36경로대조·공식CLI 공유설정등록·enabled get readback·설치STDIO init/list/call/EOF가확인됐다. 실제 Dot 호출은 미검증이다.

## 성능과 비용

AB/BA30쌍·variant별150 warm tick에서 OSK 요청별p50은151노드48.741→31.2977ms(35.79% 감소),512노드362.825→156.588ms(56.84% 감소)였다. 전체 edge를 노드마다 두 번 훑던 O(VE) 경로를 tick별incident O(V+E) 구축과 revision 재사용으로 바꿨다. 사용자 편집·삭제·self-edge·order와 취소 경계는 보존하고 tick 사이에는 캐시하지 않는다. Parent20회귀·peer 관련72회귀와16 differential이 통과했다. 전체 앱 메모리/추론/검색 품질 개선은 이 수치에서 주장하지 않는다.

| 최종0.2.0 상태 조회 cohort | 직접 API → MCP | 추가 비용95% CI |
|---|---:|---:|
| warm raw p50 | 1.415250 → 1.653271ms | +16.82% [8.08,25.72] |
| warm raw p95 | 2.140250 → 2.647602ms | +23.71% [15.58,52.64] |

Warmup5·worker당300호출·AB/BA30쌍으로 variant별9000요청이다. CI는 process pair를 유지하는10000 bootstrap이다. 정의401UTF-8bytes이며 모델별 schema token 수는 미측정이다. 이 결과는 약300KB synthetic checkpoint의 collector/STDIO 비교다. LLM 생성 transport 비교가 아니다. `T(N)=T_start+N×T_call`에 시작비용을 별도로 포함한다. 구판0.1.0의 +8.75%는 최종판 수치와 섞지 않는다.

구판→0.2.0의 별도9000요청 cohort에서p50 1.512313→1.599417ms(+5.76%, CI[-0.80,12.77]),p95 2.337054→2.460587ms(+5.29%)였다. Peak subprocess RSS는31.990→32.834MB(+0.844MB). 취소 지원에는 비용이 있으므로 내부hot path를 MCP로 바꾸지 않는다.

## 취소·마감·원문 보호

1.5초 monotonic 전체 예산, worker1·input queue16, 소유 socket 중단, late reply 폐기, EOF 정리를 구현했다. 무인수 도구1개만 제공한다. 대화/vault 본문·Dot 메모리·자격 증명·모델ID/경로·임의파일/URL은 반환하지 않는다. 고정11234/11236 GET `/v1/models`만 허용하고 `--offline`은 이 GET도 생략한다. 실행·발신·학습·프로세스 제어 도구는 없다. Symlink/oversize/duplicate JSON key/NaN을 거부한다.

같은 socketpair 강제조건 cancel/deadline/EOF 각각AB/BA10쌍(총60): 취소 중단0/10→10/10, 취소→ping p50 501.498→0.093ms, 소유socket정리501.539→0.158ms, EOF→자기 process종료514.008→7.963ms였다. Trickle 입력은2307.487ms의 성공 대신1502.696ms에 마감 오류를 반환한다. Deadline 뒤 정상 복구p95는1.900→3.742ms로 오히려 증가했다. 동일 성공 작업의 속도 개선으로 해석하지 않는다. OS scheduling·GIL·막힌stdout까지 hard realtime을 보장하지 않는다.

[원자료·CI·해시 manifest](alden-mcp-performance-20261003/evidence-manifest.json), [OSK](alden-mcp-performance-20261003/osk-benchmark-summary.json), [현재 API/MCP](alden-mcp-performance-20261003/status-benchmark-summary.json), [보완 전후](alden-mcp-performance-20261003/status-hardening-summary.json), [취소 조건](alden-mcp-performance-20261003/status-control-comparison.json)에 scope·표본·source hash를 보존했다. 재현 bundle은 승인된 별도 작업 `/Users/twoimo/Documents/Codex/2026-10-03/task-2/alden-local-review.zip`이며 기존 경로를 덮어쓰지 않는 `verification/prepare_sources.py`를 포함한다.

## 연결과 한계

[공식 MCP 문서](https://learn.chatgpt.com/docs/extend/mcp?surface=desktop)는 desktop/CLI/IDE의 공유config와 STDIO를 지원한다. [Dot 컴퓨터 연결](https://learn.chatgpt.com/docs/dots/computers-and-apps)의 접근은 실제 Dot 호출로 별도 확인해야 한다. 현재까지 local synthetic/임시STDIO 조회의 성공이며 Dot 클라우드 노출 성공으로 보고하지 않는다. CUA의 ChatGPT 자체 조작 차단을 우회하지 않는다. 공식 CLI로 alden_readonly를등록했고기존설정값을백업·exact의미비교로보존했다. 설치STDIO조회는app0.3.11·OSKpending/conflicts0/0였다. 실제Dotclient호출로확대해해석하지않는다.
