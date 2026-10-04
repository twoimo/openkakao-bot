# 같은 27B 체크포인트의 실제 사진 읽기 — 2026-10-01

**별도 소유 프로세스의 실제 실행 검증까지 진행했다. 운영 적용은 미완료다.** 공유 MLX Core PID3273은 `--no-vision`으로 실행 중이다. 체크포인트에는 `vision_config`와 실제 `vision_tower.*` 가중치가 있다. 이를 변경하거나 다른 모델을 선택하지 않고 MLX Core 26.9.5의 같은 바이너리와 체크포인트로 임시 loopback 서버를 실행했다.

원문 목표의 맥락·로컬 모델 요구를 계속 진행하며, 전체 목표는 완료하지 않는다. 이 변경은 증거와 문서만 추가한다. 소스 구현, 설치본 0.1.6, 공유 서버, 운영 작업자와 전송 큐를 교체하지 않았다.

## 관측과 판단

| 경로 | 실제 결과 | 범위 |
| --- | --- | --- |
| 기존 공유 서버의 checkout 작업자 | GET2회, 생성 POST0회, `mlx_serve_vision_capability_unavailable` | 텍스트 모델이 사진을 본 것처럼 답하지 않음 |
| 임시 서버의 공개 합성 사진 | 색·종류·개수·위치·문자 질문4/4 정답 | 단순 smoke test, 일반 사진 품질 평가 아님 |
| 후속 질문 | 원3개 중 하나를 가리면 `2` | 이전 텍스트 답에서 계산 가능; 사진 기억 검증 아님 |
| namespace를 포함한 추가 실행 | 동일 체크포인트, 완전한 `ddalcu/Qwen3.8-27B-MLX-Serve-4bit` ID, 직접 질문1회 정답 | 모델 변경 없음 |
| checkout의 실제 worker 생성 함수 | 이미지1개와 strict `json_schema`를 실제 HTTP POST로 전달, 정확한 빨간 원3개 JSON 응답 | private discovery URL/catalog와 인증만 연결; 운영 caller·대기열·전송 미실행 |

public fixture와 응답은 다음과 같다. 정답 문자열은 최초 질문에 넣지 않았다. 독립 Astra/max 검토도 네 답변과 두 번째 실행의 실제 비전 토큰·스키마 처리 로그를 확인했다.

| 파일 | 응답 |
| --- | --- |
| [image-0](alden-vision-fixtures-20261001/image-0.png) | 사진에는 빨간색 원형 도형이 3개 있습니다. |
| [image-1](alden-vision-fixtures-20261001/image-1.png) | 파란색 원형 도형이 두 개 있습니다. |
| [image-2](alden-vision-fixtures-20261001/image-2.png) | 왼쪽은 녹색 사각형이고, 오른쪽은 주황색 삼각형입니다. |
| [image-3](alden-vision-fixtures-20261001/image-3.png) | ALDEN 7318 |

## 수치와 측정 한계

첫 실행의 사진 질문4건은 첫 콘텐츠까지 **0.923–1.475초**, 응답 완료까지 **1.166–2.004초**였다. 각 사진1회다. 후속 질문1회는 별도이며, p95·개선율·일반 성능 목표로 해석하지 않는다. 두 번째 worker 생성 함수 호출 전체는 **4.132초, n=1**이다. 이 실험은 `_unleased` 함수 경로로 공유 model lease·운영 scheduler를 실행하지 않았다.

스크립트 시작부터 ready 관측까지 첫 실행 **14.117초**, 두 번째 **7.642초**였다. preflight·프로세스 시작·polling을 포함하며 OS 파일 캐시와 다른 앱 부하를 통제하지 않았다. 콜드 로딩 시간이나 전후 개선 비교가 아니다.

첫 실행의 bare `--model` 카탈로그는 resident5MiB·disk미상으로 잘못 표시됐다. 이를 모델 메모리로 사용하지 않는다. 두 번째 실행은 private `models/ddalcu/<name>` symlink가 원래 체크포인트에 정확히 연결되도록 하고 그 namespace만 탐색했다. disk/resident 카탈로그 값은 **18,196,477,654bytes**였다. 이는 registry accounting이며 물리 메모리 측정이 아니다.

두 번째 실행의 MLX active allocation 관측 최대는 **17,990,840,264bytes (16.755GiB)**이며 마지막 running/waiting은0/0이었다. 샘플은4개로 실제 최고점, 앱 전체 메모리 또는 24GB 상한의 강제 집행을 증명하지 않는다. 두 실행에서 관측한 swap 점유량은 **25,414,273,597bytes**로 변하지 않았다. swap I/O가 없었다는 뜻은 아니다. 기존 음성 admission의 swap 기준은 유지한다.

## 실행 경계와 재현 자료

설치 바이너리는 `/Applications/MLX Core.app/Contents/MacOS/mlx-serve`, 체크포인트는 `/Users/twoimo/.mlx-serve/models/ddalcu/Qwen3.8-27B-MLX-Serve-4bit`다. [공식 26.9.5 API](https://github.com/ddalcu/mlx-serve/blob/v26.9.5/docs/api.md)의 `image_url` base64 입력을 사용했다. launch argv, 질문/응답, fixture SHA256, model metadata, 샘플, 실제 adapter 요청과 shared snapshot은 [JSON receipt](alden-local-vision-20261001.json)에 남겼다. 실제 가중치4개와 바이너리의 SHA256은 실행 후 별도로 기록했다. native cache에는 upstream revision 정보가 없어 **리비전 미확인**으로 남긴다. 실행 도중 불변성 증거와 구분한다.

두 번째 실행은 한 모델·4096컨텍스트·동시1·KV4bit·24GB registry budget, OS reserve8GiB·wired margin8GiB로 제한했다. MTP/drafter/PLD·disk prefix cache는 껐다. 기존 40GiB reclaimable admission과 native load preflight를 통과한 경우에만 진행했다. shared 요청 시작·OS reserve 부족·swap 점유128MiB 추가·총240초 제한 또는 관측 active allocation24GiB 초과 시 owned 프로세스 종료 경로를 두었다. 이것을 일반 모델 상주 정책으로 확정하지 않는다.

server child의 sandbox는 외부 주소 연결을 `PermissionError 1`로 거부했다. 두 번째 parent audit는 요청 URL이 `127.0.0.1:11234/11237`임을 확인했다. 첫 helper의 기본 proxy/redirect 처리, loopback proxy 가능성 및 strict loopback 인증의 실제 집행은 별도 검증이 남았다. 네트워크 포렌식이나 완전한 클라우드 우회 부재를 증명했다고 주장하지 않는다.

두 owned PID4106/8082는 종료코드0으로 끝났고 이후 ps에 없었다. 포트11237은 `connect_ex=61`로 연결 거부됐다. 공유 process argv·resident ID·success counter118→118·MLX allocation은 전후 snapshot에서 같았다. 지속적인 무경합이나 공유 파일 불변성의 근거는 아니다. [초기 로그](alden-vision-initial-20261001.log), [namespace/adapter 로그](alden-vision-namespaced-20261001.log)를 보존했다. 저장본은 줄 끝 공백만 제거했고 원본/저장본 SHA는 JSON에 함께 기록했다.

## 다음 완료 조건

기존 서버와 운영 작업자의 소유권/교체 승인 경계를 해결한 뒤 실제 배포 프로필에서 메모리·컨텍스트를 검증하고 사진 경로를 연결해야 한다. 공유786432컨텍스트에서 비전이 안전하다는 근거는 이번4096 실험으로 얻지 못했다. 임시 서버는 상주시켜 두지 않았다. 실제 Kakao attachment transport, 설치 UI의 사진 질의와 비텍스트 사진 후속 맥락은 미검증이다. 사람 음성·native 화면/물리 단축키·공개 signed release·production 반영도 계속 남아 있다.
