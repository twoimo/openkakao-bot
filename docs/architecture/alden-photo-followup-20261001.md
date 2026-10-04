# Alden 사진 후속 질문과 복구 취소 — 2026-10-01

원본 사진과 현재 질문의 출처를 구분하는 worker 수정이다. source 구현과 private 후보 준비를 완료했으며, 같은 cached 27B의 실제 pixel 질의 1회도 확인했다. 운영 worker 교체, 실제 Kakao 첨부 수신, 설치 앱 UI의 사진 기억과 전체 Alden 목표는 미완료다.

기존 `_same_author_recent_photo_events`는 미래 log/time, 명시적으로 다른 방의 행, 현재 시각 누락·malformed·NaN, source NaN을 선택했다. 중복 행도 두 번 선택했고 원본 시각 대신 현재 질문 시각을 복사했다. 공개 합성 입력의 [수정 전](alden-photo-context-baseline-20261001.json)과 [수정 후](alden-photo-context-after-20261001.json)를 비교했다. 각각 1회 실행한 기능 재현이며 성능 기준선은 아니다.

| 입력 | 수정 전 선택 ID | 수정 후 선택 ID |
| --- | --- | --- |
| 정상 직전 사진 | 99 | 99 |
| 미래 log / 미래 시각 / 다른 방 | 101 / 99 / 99 | 각각 없음 |
| 현재 시각 누락 / malformed / NaN | 각각 99 | 각각 없음 |
| 사진 시각 NaN | 99 | 없음 |
| 동일 행 중복 | 99, 99 | 99 |

현재 room/author/log/time은 양의 JSON 정수여야 한다. 사진은 같은 작성자, 현재보다 앞선 log, 0–300초 이내의 과거 시각만 허용하고 명시적 foreign chat은 거부한다. 과거 scoped tail에서 chat_id가 생략된 형식은 유지하되 실제 CLI 복구에서 정확한 room/log/expected-author를 다시 확인한다. 정상 flagged image type과 300초 경계도 회귀 검사에 포함한다.

`current_inbound_evidence.source_log_id=100`과 `media_evidence.source_log_id=99`, `source_log_ids=[99]`를 분리한다. 복구된 DB 사진은 AX 화면 캡처보다 우선하며, 전체 이미지 개수와 합계 20 MiB 예산을 검사한다. CLI 복구는 bounded stdout/stderr, 45초 제한, 소유한 process group 취소를 사용한다. 새 0700 scratch와 0600 marker 안의 regular file만 받아 외부 경로·symlink·중복 inode를 거부한다. 첫 사진 성공 뒤 둘째 복구가 취소되어도 첫 bundle을 정리하고, defer된 이벤트에는 원래 producer bundle만 보존한다.

실제 검증에는 [공개 사진](alden-vision-fixtures-20261001/image-2.png)의 왼쪽 녹색 사각형·오른쪽 주황 삼각형을 사용했다. 합성 tail 텍스트는 `사진`, 현재 질문은 `왼쪽 색은?`으로 색상 답을 포함하지 않는다. selector → 실제 fixture-download subprocess → checkout `analyze_event`/`generate_reply` → native MLX HTTP JSON-schema 경로를 실행했다. 원본 photo99의 한 image block으로 **“초록색이네”**가 나왔고, 원본/current ID와 scratch 삭제를 독립 확인했다. [원시 receipt](alden-photo-followup-20261001.json), [로그](alden-photo-followup-20261001.log)를 보존한다.

모델은 `<USER_HOME>/.mlx-serve/models/ddalcu/Qwen3.8-27B-MLX-Serve-4bit`의 기존 같은 가중치다. native MLX Core 26.9.5, owned namespace, context8192, concurrency1, registry24GB, OS/wired reserve8GiB, KV4bit, MTP/drafter/PLD/cache 비활성화 조건이다. 이전 context4096 비전 실험과 속도를 비교하지 않는다. 원본 가중치 정보와 post-run fingerprints는 [앞선 비전 근거](alden-local-vision-20261001.md)에 있다.

| 관측 | 값 / 범위 |
| --- | --- |
| clean isolated 요청 | 1 POST / 1 정답, 추가 재시도 없음 |
| 모델 생성 / 전체 analyze | 15.78초 / 16.325초, 각각 n=1 |
| script 시작 → ready | 3.350초, uncontrolled warm cache; cold-load 수치 아님 |
| MLX active allocation sampled max | 19,474,942,132 bytes, 9 samples; 진짜 peak나 전체 앱 memory 아님 |
| swap occupied | 25,389,107,773 bytes 전후 동일; zero-swap 주장 아님 |
| 공유 successful request counter | 124 → 124, argv/resident ID 유지 |
| owned cleanup | 4개의 실험 PID 없음, port11237 connection refused, watchdog join, child rc0 |

실험의 room/DB·retrieval·turn timing·trust/lease·gateway discovery/auth는 명시적인 private fixture seams다. GraphRAG는 빈 fixture, reranker는 `fixture_rerank_not_executed`이며 실제 retrieval/reranker 품질을 검증하지 않는다. native worker image와 JSON transport는 실제이고, 운영 caller·installed UI나 실제 Kakao attachment를 검증한 것은 아니다. 미래/foreign 후보는 selector에서 거부한 뒤 valid fixture tail로만 모델 요청을 구성했다.

parent HTTP는 proxy와 redirect를 차단하고 audit hook으로 loopback 지정 두 port만 허용하며 공유 port는 GET만 허용했다. child는 minimal env와 network sandbox로 owned port bind/inbound만 허용하고 outbound를 거부했다. 별도 sandbox 외부 연결 probe는 `PermissionError 1`, owned catalog의 missing/wrong bearer는 각각 HTTP401이었다. 이 실행의 transport/정책 근거이며 전체 컴퓨터의 forensic network 감사는 아니다.

검증 중 한계를 숨기지 않는다. 첫 helper 시도는 interpreter 경로 공백과 timing fixture 오류로 0 POST 종료했다. 다음 시도는 정답 1회를 생성했으나 기존 graph를 읽고 dense 연결이 차단됐으며 reranker가 `sidecar_crash`라 clean fixture 근거로 사용하지 않는다. 빈 graph를 추가한 시도도 import ordering 오류로 0 POST 종료했다. 위 표는 그 뒤의 최종 isolated 1회만 다룬다. 별도로 초기 unittest 모듈에서 imported legacy TestCase가 의도치 않게 전체 수집되어 실제 shared local MLX 호출이 실행됐다. 소유한 test process만 SIGINT로 중단하고 module-alias import로 수정했다. 따라서 그 초기 실행을 inference/network 무접촉으로 보고하지 않는다. 앞선 시각의 shared counter118과 현재124의 차이는 구간 관측이며 전부 이 테스트 때문이라고 단정하지 않는다.

집중 photo 7개는 graph를 fake adapter로 격리하고 fake CLI의 실제 shell/child 취소, 둘째 복구 취소 뒤 첫 bundle 삭제, foreign 파일 보존, 현재/사진 ID 분리를 확인한다. 두 기존 lease-boundary tests는 파일이 admission 이후 사라지는 fake 응답을 유지하도록 initial file을 만들었다. admission 전에 파일이 없는 새 사례는 lease acquisition0을 별도 확인한다. 최종 필수 검사와 원격 SHA/CI는 아래 전달 receipt와 `docs/ALDEN_DELIVERY.md`에서 대조한다. 전체 old runtime test module을 다시 호출하지 않는다.

[private worker 후보](alden-photo-worker-candidate-20261001.json)는 21 asset/19 source bytes, 기존 3 selectors와 config를 대조했으며 미활성화 상태다. 준비 전후 enrollment/config/stable CLI/활성 3방 queue의 ID/status digest가 같았다. 이 동등성은 후보 준비 구간만 보증하며 앞선 테스트 전체에 소급하지 않는다. private config는 배포 archive에 넣지 않는다. 설치된 Alden0.1.6 native bundle은 이번 외부 worker를 소비하는 증거가 아니며 다시 빌드한 것으로 보고하지 않는다.

최종 code `247c676`의 worker SHA-256은 `20bc0ac046dc022fecbf34596d81784eea513a49ab92784b85446de96d0dafbb`다. Astra/max code 검토는 PASS였다. [최종 로컬 검사 receipt](alden-photo-context-tests-20261001.json): pinned Python3.11.9, 필수1027/17skip/실패0/오류0/104.957초, 집중7/실패0/1.098초. 필수 검사 socket guard는 기존 fixture의 shared port 연결 시도3개를 연결 전 차단했다. 따라서 구 fixture 모두가 hermetic하다고 주장하지 않는다.

公開用 receiptでは home prefix と 방별 production 집계·queue fingerprint를 제거했다. 원본은 private stage/experiment root에 보존한다. Astra/max 검토에서 이 publication 경계를 수정했으며, 기존 커밋은 정상 Git 이력에 남는다.
