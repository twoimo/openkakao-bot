# Alden 0.3.10 — 로컬 임베딩 메모리

다양한 배치·입력 길이에 쓰인 E5의 MLX 버퍼가 요청 종료 뒤 빈 캐시로 누적됐다.
가중치 메모리와 별개로 기존 구현은 44개 요청 뒤 약 13GB의 빈 캐시를 유지했다.
전용 E5 프로세스의 캐시 상한을 512 MiB로 설정하고, 텐서를 해제한 뒤 상한 초과 캐시를 정리한다.
실패해도 직렬 추론 잠금을 해제하며 실제 진행 중 요청·활성 메모리·빈 캐시·최대 활성 메모리를 구분한다.
MLX의 상한은 다음 할당에서 적용되므로 요청 종료 시 초과분 정리도 필요하다.
[공식 API 설명](https://ml-explore.github.io/mlx/build/html/python/_autosummary/mlx.core.set_cache_limit.html).

## 동일 조건의 재현

M5 Max·128GB·AC 전원, 정확한 모델
`mlx-community/multilingual-e5-small-mlx@5030c7625865046d350eeea28f427d80353d0ac0`,
MLX 0.32.2·NumPy 2.5.3·tokenizers 0.22.2를 사용했다.
변형별 새 독립 프로세스 3개를 교대로 실행했다. 각각 배치 1/2/4/8 × 길이 11종으로
44개 요청·165개 벡터를 생성했고 모든 socket 연결을 금지했다. 상주 서비스를 호출·재시작하지 않았다.

| 항목 | 기존 `63267e9` | 0.3.10 후보 | 관측 변화 |
| --- | ---: | ---: | ---: |
| 요청 종료 뒤 kernel footprint 중앙값 | 14.453 GB | 1.517 GB | 89.50% 감소 |
| 44개 요청 처리 시간 중앙값 | 0.521초 | 0.726초 | 0.205초 증가·39.29% |
| Float32 출력 SHA-256 | `23c1d587…40c923` | 동일 | 모든 프로세스 일치 |
| 후보의 마지막 빈 캐시 최대값 | — | 0 bytes | 512 MiB 이내 |

GB는 십진 단위다. 감소율은 `(기존−후보)/기존×100`으로 계산했다.
kernel footprint·RSS·MLX 풀은 범위가 겹치므로 더하지 않는다.
이 비교는 E5 프로세스의 메모리이며 앱 전체·GPU 전력·배터리 수치가 아니다.
서로 다른 입력 형태 44개의 전체 시간을 정상 대화 p95로 해석하지 않는다.

캐시 회수는 재할당 비용을 만든다. 이전 세션의 처리 시간 악화 10% 이하 목표는 이 재현에서 미달이다.
메모리 감소를 우선 적용하되 시간 증가도 공개한다. 별도 확장 검사에서는 두 번 준비한 짧은 질의를
프로세스당 20회 측정했다. 기존의 프로세스별 중앙값은 2.77/5.96/5.30ms,
후보는 2.67/7.65/4.13ms였다. 확장 검사는 다른 로컬 검사·컴파일과 겹쳤으므로
정상 질의의 속도 개선·악화를 확정하지 않는다.

이전 세션의 44종×3회 근거는 14.250→1.670GB·88.28% 감소였다. 새 재현과 섞어 계산하지 않는다.
[재현 원자료](alden-embedding-memory-20261003/reproduction/comparison.json)와
[질의 확장 원자료](alden-embedding-memory-20261003/query-extension/comparison.json)는 별도다.

```sh
python3 scripts/measure_alden_embedding_memory.py \
  --python "$HOME/Library/Application Support/openkakao/alden-local-embedding/runtime/bin/python" \
  --output /private/tmp/alden-e5-new-comparison --repeats 3
```

출력 디렉터리는 새 경로여야 한다. 출력·모델·runtime·표본 수·오프라인 경계나 캐시 상한이 다르면
실패로 종료한다. 가중치 다운로드나 원본 대화 수집은 실행하지 않는다.

## 설치·검증·남은 범위

복구한 소스로 빌드·설치한 0.3.10의 35개 파일과 ad-hoc deep/strict 서명을 대조했다.
설정·enrollment·stable CLI의 inode/수정 시각/해시와 상주 27B/E5·기존 음성·카카오 세션의
프로세스 시작 시각을 보존했다. E5 서비스의 설치 adapter와 소스 해시도 일치한다.

설치 바이너리의 별도 WKWebView 팝업 검사에서 숨김·복원 10회×350ms의 추가 렌더는 0회였다.
첫 설치 판본의 숨김 요청→중단 관측 상한은 중앙값 9.604ms·최대 26.540ms다.
복구 소스로 재설치한 뒤 팝업·기본/최소 설정 검사도 통과했다. 사람의 메뉴바 클릭·OS 잠금·물리
단축키·마이크·스피커와 별도다. 현재 두 물리 화면은 배율 1.0이며 Retina는 미검증이다.
private 대화 그래프 캡처는 공개 Git/릴리즈에 넣지 않는다.

집중 검사 32개, 프런트엔드 231개, 네이티브 Rust 94개, CLI Rust 1150개/1 ignored,
두 Clippy·release build가 통과했다. Python 필수 1181개의 첫 실행은 설치 fixture 제한시간 1건
실패/28 skip였고 실패 사례는 단독 재실행에서 통과했다. 최종 원격 CI 결과는 전달 receipt에 별도 기록한다.

[Archify 순서도](alden-embedding-memory-20261003.html)는 실제 호출·직렬화·텐서 해제·캐시 회수를 나타낸다.
9개 artifact 검사와 composition 오류/경고 0, 실제 Chrome의 4개 기본 viewport·추가 대형 화면에서
넘침/가독성 검사를 통과했다. 1440×900 dark·2048×1320 light 이미지를 직접 확인했고,
타임라인·라벨·카드·도구의 잘림과 겹침은 관측하지 않았다.
[시각 검토 기록](alden-embedding-memory-20261003/diagram-visual-review.json).
작성 내용은 한국어, 고정 Viewer UI는 영어다.

현재 `cua.getApp('Alden')`은 `timeoutReached(-10005)`로 실패한다.
원본 Kakao 앱 데이터 접근 승인, 자연 음성/wake/물리 중단, Retina·전력, Developer ID/공증과
운영 작업자 적용은 남아 있다. 전체 목표는 [이어받기 기록](../ALDEN_CONTINUATION.md)에서 진행 중으로 유지한다.
