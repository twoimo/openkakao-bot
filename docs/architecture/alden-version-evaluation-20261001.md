# Alden 버전별 로컬 평가 — 2026-10-01

명시한 로컬 평가 구성으로 앱을 빌드하면 제품 버전, 소스, 문항, policy/reference 가중치, checkpoint 형식, beta와 runtime 판본을 대조한다. 변경된 입력은 검증 분할을 채점하고, 같은 입력은 검증한 receipt를 재사용한다. 실제 27B 첫 평가와 캐시 조회, 구성된 실제 앱 빌드까지 확인했다. 이 경로는 가중치를 학습하거나 제품 모델을 교체하지 않는다.

[실제 모델 receipt](alden-version-evaluation-20261001.json), [필수 검사](alden-version-evaluation-tests-20261001.json), [Archify 흐름](alden-version-evaluation.html)을 함께 보존한다. 전체 11항목 목표는 진행 중이다.

## 구현과 경계

- 문항 JSON은 256KiB, 선호 쌍 32개 이내다. regular file만 nonblocking으로 읽고 중복 JSON key·동일 prompt·분할 사이의 동일 출처 그룹·모델 자체 승인 label을 거부한다. label은 확인 가능한 맥락/자료 또는 명시적 사람 피드백을 요구하지만, label 문자열 자체가 외부 자료의 정확성을 증명하지는 않는다.
- 기본 평가에는 `validation`만 사용한다. `train`을 채점하지 않으며, 최종 `test`는 `--evaluation-final-test`로 별도 실행한다. 이번 공개 합성 문항은 train/validation/test 각 3개다. 실제 카카오톡 대화나 사람 음성 품질을 대표하는 평가셋은 아니다.
- 기존 scoped MLX scorer가 고정 응답의 실제 토큰 로그확률과 DPO 손실을 계산한다. 원래 체크포인트와 기준 모델을 쓰기 없이 읽고, 같은 기준 가중치는 score를 재사용한다. MLX-Serve로 이미 변환된 Qwen3.5 구조에는 `mlx-serve-qwen3_5-converted`를 명시한다.
- private root0700/receipt0600, 단일 writer 잠금, nofollow/nonblocking cache read, checksum·모델·문항·분할·수치 일치 검사를 적용한다. 개별 delta/loss와 평균을 다시 계산하며, 동일 policy/reference의 `delta=0, loss=ln(2)`도 검증한다. checksum은 호스트 외부의 서명이나 attestation이 아니다.
- 결과 확정은 global abort의 같은 commit lock 안에서 마감·취소 epoch를 확인하고 준비된 파일을 원자적으로 교체한다. 실패 결과를 성공 cache로 재사용하지 않는다. 전용 CLI의 메모리 사전 검사는 최소 40GiB 및 checkpoint 크기 추정치이며 hard allocation cap은 아니다. 이번 실험에는 별도 150초 process 마감, OS reserve/swap/shared-work 감시와 network-deny sandbox를 추가했다.
- 기존 `--train`의 MLX-LM LoRA/DoRA 경로는 SFT다. 이번 평가 CLI는 그 경로와 golden 데이터 준비를 실행하지 않는다. 실제 DPO gradient update, adapter 저장/재로딩, adapter-aware held-out 평가와 제품 promotion은 미완료다.

## 실제 실행

같은 M5 Max/128GiB 장치, `ddalcu/Qwen3.8-27B-MLX-Serve-4bit`, 같은 체크포인트와 문항을 사용했다. Python3.11.16·MLX0.32.2·MLX-LM0.31.3·Transformers5.17.0 전용 runtime에서 수행했다. 모델 SHA는 `2607ed9ee6e6776d9a93d9937749ff1cabc5e6fa992656edd661f0f7bc019ae1`, 최종 평가 소스 aggregate SHA는 `6b4a176d97715434a74a044f7d3df62c6b43bf2d1e39efe10b694f6d96a0cf08`이다.

| 관측 | 첫 평가 | 같은 입력 캐시 조회 |
| --- | ---: | ---: |
| process 전체 시간, 각 n=1 | 24.772434초 | 8.566803초 |
| 실제 모델 load 호출 | 1 | 0 |
| 검증 문항 | 3개 실제 채점 | 같은 3개 결과 재사용 |
| process peak RSS | 16,918,544,384 bytes | 74,711,040 bytes |
| runtime network 시도 | 0 | 0 |
| 별도 network 거부 probe | 통과 | 통과 |

첫 scorer 구간은 15.504647초이고 MLX peak는 **17,354,615,326 bytes**다. 이 counter는 모델 load 이후 reset했으며 process RSS, 앱 전체 메모리와 다른 측정 범위다. 캐시 receipt 안의 scoring peak/time은 **원래 평가의 이력**이고, 캐시 호출의 top-level runtime은 새 조회 시간을 기록한다. 캐시 조회에서도 가중치 fingerprint를 읽으므로 시간이 0이 아니다.

같은 policy/reference의 평균 loss는 **0.6931471805599453**이다. 세 chosen 응답의 summed log-prob는 각 rejected보다 높았지만, 이는 작은 합성 고정 응답 관찰이다. 학습·대화 품질 향상·일반적 응답 속도 개선을 증명하지 않는다. 콜드 cache나 전원 상태를 통제한 실험이 아니며 각 작업 n=1이므로 p50/p95·개선율을 산출하지 않았다.

공유 MLX server command fingerprint와 성공 counter124→124를 대조했고 새 swap 사용량 증가는 없었다. 기존 swap 사용량은 **25,389,107,773 bytes**다. 공유 모델 unload·서비스 재시작·메시지 전송은 실행하지 않았다. 앞선 판본의 26.212/8.576초 기록은 소스 보강 전 이력으로 private 보존하고 위 최종 판본과 혼합하지 않는다.

## 재현과 빌드 연결

실행 전에 `ALDEN_EVALUATION_PYTHON`에 확인한 MLX Python 절대 경로를, 두 checkpoint 변수에 로컬 디렉터리를 지정한다. root는 해당 사용자가 소유한 private 절대 경로여야 한다. 전용 실험의 `ALDEN_EVALUATION_STATE_ROOT`는 global production state와 분리한 root였다. 실제 제품 빌드에서는 기본 global abort를 공유하거나 실행 목적에 맞는 state root를 명시한다.

```sh
"$ALDEN_EVALUATION_PYTHON" scripts/auto_reply_finetune.py \
  --model-evaluation-dataset tests/fixtures/alden-model-evaluation/preferences.json \
  --evaluation-version 0.1.6 --evaluation-root "$ALDEN_EVALUATION_ROOT" \
  --dpo-policy-dir "$ALDEN_EVALUATION_POLICY_DIR" \
  --dpo-reference-dir "$ALDEN_EVALUATION_REFERENCE_DIR" \
  --dpo-checkpoint-format mlx-serve-qwen3_5-converted \
  --state-root "$ALDEN_EVALUATION_STATE_ROOT" --json

export ALDEN_EVALUATION_PYTHON ALDEN_EVALUATION_ROOT
export ALDEN_EVALUATION_POLICY_DIR ALDEN_EVALUATION_REFERENCE_DIR
export ALDEN_EVALUATION_STATE_ROOT
export ALDEN_EVALUATION_DATASET="$PWD/tests/fixtures/alden-model-evaluation/preferences.json"
export ALDEN_EVALUATION_CHECKPOINT_FORMAT=mlx-serve-qwen3_5-converted
sh scripts/build-alden-desktop.sh
```

`ALDEN_EVALUATION_DATASET`가 설정된 빌드에서만 hook을 실행한다. 누락된 필수 구성과 평가 실패는 빌드를 멈춘다. 설정이 없으면 모델 다운로드·평가·학습을 시작하지 않는다. 별도 상주 daemon이나 대화 worker를 추가하지 않았다. 구성된 실제 network-deny 빌드는 같은 receipt를 **8.992949초**에 재사용하고 CLI/frontend/Tauri `Alden.app`까지 성공했다. 이는 source build hook 실행 근거이며 설치 앱의 상주 지속 평가 증거는 아니다.

[빌드 readback](alden-version-evaluation-build-20261001.json)에서 Alden0.1.6·기존 identifier·resource27/27 source 일치·strict ad-hoc signature를 확인했다. 기존 설치본의 27개 resource도 같은 bytes이며 설치 앱을 교체하지 않았다. evaluator는 별도 source CLI이고 설치 bundle에 포함하지 않았다. public notarization은 여전히 미완료다.

빌드 readback의 `invocation_provenance`는 부모의 실제 sandbox-exec 호출, `deny network*` 정책, 구성 변수 이름, 종료0과 private 원본/공개 정리 로그의 SHA를 기록한다. [공개 빌드 로그](alden-version-evaluation-build-20261001.log)는 같은 평가 ID와 source SHA에 묶이며 사용자 경로를 정리했다. sandbox 실행 근거와 모델 probe의 Python network audit는 별개의 검증이다.

## 검증과 전달

- 집중13개/실패0, 필수 workflow의 65 selectors에서 **1,040개/17skip/실패0/오류0**, pinned menu Python3.11.9, 119.144초. 격리 guard가 기존 검사 세 곳의 shared11234 연결을 차단했다. 모든 기존 fixture가 네트워크를 완전히 mock했다는 주장은 하지 않는다.
- 실제 MLX runtime의 scorer/evaluator46개/실패0, 2.878초. 실제 upstream tiny sanitizer 수치 검사와 fake checkpoint 검사이며 추가 27B inference가 아니다.
- Astra/max 독립 source 검토: 세 P2 수정 확인 및 새 actionable regression 없음. 검토 agent의 provider route는 별도 trace로 독립 입증하지 않았으며 해당 결과를 모델 구동 성능 근거로 쓰지 않는다.
- Archify deliver **9/9 showcase, 오류0/경고0**, 실제 Chrome 네 desktop viewport에서 overflow0. 부모가 실제 light1440×900/dark2048×1320 이미지를 읽어 경로·label·카드·여백을 확인했다. 최초 lifecycle 초안은 작은 viewport에서 overflow하여 private 보존하고 같은 의미의 workflow로 바꿨다. 최종 [receipt](alden-version-evaluation-deliver.json)와 [시각 검토](alden-version-evaluation-review.json)는 artifact SHA에 묶인다. 본문은 한국어이고 고정 Viewer UI는 English fallback이다.

Git/원격 CI 및 전달 파일은 해당 head의 별도 readback으로 확인한다. signed public release·사람 음성·native 설치 창·production worker와 모델 교체는 여전히 별도 미완료 항목이다.
