# Alden 실제 오프라인 DPO 학습 — 2026-10-01

대화 경로와 분리된 DPO 학습 CLI를 구현하고, 요청한 같은 27B 체크포인트에서 실제 gradient 업데이트 3회, adapter 저장, 새 기본 모델의 재로딩과 별도 버전 평가를 확인했다. 제품 모델은 교체하지 않았다. 공개 합성 문항의 손실 변화는 실제 대화 품질 향상을 증명하지 않는다.

[실제 실행 receipt](alden-dpo-training-20261001.json), [검사 기록](alden-dpo-training-tests-20261001.json), [독립 검토](alden-dpo-training-review.json), [Archify 흐름](alden-dpo-training.html)을 함께 보존한다. 전체 11항목 목표는 계속 진행 중이다.

## 구현과 실행 범위

- `--dpo-train-dataset`은 기존 SFT `--train`, 데이터 준비와 평가 명령에서 분리된다. train/validation/test 출처와 중복 계약을 검사하고 **train만 업데이트**한다. 고정 reference는 실제 응답 토큰 로그확률이다. prompt 토큰은 tensor 손실과 gradient에서 제외하며 `nn.value_and_grad`로 DPO 손실을 미분한다.
- 원본 모델을 freeze하고 마지막 1개 layer의 명시한 projection에 rank 2 LoRA를 연결했다. 이번 실행은 learning rate `1e-5`, beta `0.1`, seed `42`, 합성 train 3쌍에 각 1회 업데이트다. CLI 기본값 rank 8/10 steps와 이번 작은 실행 범위를 구분한다. `0/-1` layer, 비정상 수치, 512 token 초과 sequence와 100 step 초과를 거부한다.
- reference와 policy는 동일 tokenizer/가중치 SHA를 확인한다. reference 점수를 고정하고 원본 파라미터 객체·디스크 가중치가 유지됐는지 대조한다. train-mode의 uncached response-mask reference와 eval-mode의 cached teacher-forcing 평가를 구분한다. 학습 step 손실은 서로 다른 문항의 값이며 연속된 같은 문항의 감소 곡선이 아니다. BF16 연산의 서로 다른 forward/gradient 경로에서 나온 수치를 같은 관측으로 합치지 않는다.
- 학습 전에는 bare base와 아직 업데이트하지 않은 adapter의 평가 점수를 각각 기록한다. 이번 실행의 두 평가 기준선은 동일하다. 학습 후 새 기본 모델을 다시 로드하고 저장한 adapter의 모든 응답 token log-prob를 재현했다(허용 오차 `1e-5`).
- adapter는 원본 SHA·checkpoint 형식·layer/rank/key에 묶인다. owner-only root0700/files0600의 regular file을 nofollow/nonblocking으로 한 번 복사한 소유 snapshot에서 로드한다. 이름·shape·floating dtype·유한값을 확인하고 LoRA 이외의 파라미터 주입을 거부한다. 전체 모델 가중치를 덮어쓰거나 adapter를 fuse하지 않는다.
- 단계마다 취소/마감을 검사하고 모델 load 직전에도 확인한다. 학습·optimizer·gradient 참조는 실패 시에도 정리한다. 원본 root와 임시 directory inode를 확인하고 file/directory fsync 후 같은 global-abort commit guard에서 원자적으로 후보를 확정한다. 취소 시 소유한 임시 directory만 정리한다.
- version 평가의 key에는 adapter SHA가 들어간다. 같은 기본 모델이라도 policy에 adapter가 있으면 reference를 별도로 채점한다. `ALDEN_EVALUATION_ADAPTER_DIR`을 명시하면 기존 앱 build hook도 후보를 평가하며, 앱에 후보를 설치하거나 사용하도록 설정하지 않는다.

## 실제 27B 관측

같은 M5 Max/128GiB, AC 전원, `ddalcu/Qwen3.8-27B-MLX-Serve-4bit`와 기존 4-bit 가중치를 사용했다. checkpoint 형식은 `mlx-serve-qwen3_5-converted`다. Python3.11.16·MLX0.32.2·MLX-LM0.31.3·Transformers5.17.0에서 실행했고 모델 SHA는 `2607ed9ee6e6776d9a93d9937749ff1cabc5e6fa992656edd661f0f7bc019ae1`, 최종 source aggregate는 `060c3018c6deea64075a6bcd83a746172655e6ccf1990f3d2a6aa119747fb948`다. adapter SHA는 `6a73fa7c79f65f453d25a27edd3db1082ae86e7d358d241d0ee77631408c78a0`이다.

| 고정 응답 평가 | 원본 기준선 | 업데이트 없는 adapter | 학습 후 |
| --- | ---: | ---: | ---: |
| train 3쌍 평균 DPO loss | 0.693147181 | 0.693147181 | 0.688996808 |
| validation 3쌍 평균 DPO loss | 0.693147181 | 0.693147181 | 0.692679494 |
| validation에서 chosen log-prob가 더 높은 문항 | 3/3 | 3/3 | 3/3 |

validation의 회의 시각 문항은 손실이 **0.693147181→0.696600435로 악화**됐고 다른 두 문항은 낮아졌다. 최종 test 3쌍은 사용하지 않았다. 아래 작은 생성 비교 외에 실제 사용자 대화·검색 품질·사용자 피드백·최종 test와 제품 지연/메모리 검증을 통과한 후보가 아니므로 promotion과 품질 개선 주장을 차단한다.

| 실행 관측, 각각 n=1 | 결과 |
| --- | ---: |
| 학습 CLI runtime / process 전체 | 37.907485초 / 38.148209초 |
| 실제 gradient 업데이트 / 유한 양의 gradient L1 | 3회 / 3회 |
| 기본 모델 load(reference/policy/reload) | 3회 |
| 학습 process peak RSS | 17,507,188,736 bytes |
| 학습 MLX peak, model load 후 reset | 17,492,021,912 bytes |
| 별도 adapter validation process | 24.808660초, 모델 load 2회 |
| 동일 입력 cache 조회 process | 8.898715초, 모델 load 0회 |
| runtime network 시도 / 별도 거부 probe | 0 / 통과 |

MLX allocation, process RSS와 앱 전체 메모리는 서로 다른 범위다. cache receipt의 scoring peak/time은 원래 평가의 이력이다. 콜드 cache·다른 세션 부하를 통제한 반복 성능 시험이 아니므로 p50/p95와 속도 개선율은 계산하지 않는다. 첫 개발 실행 44.357초와 최종 37.907초를 속도 개선 비교로 사용하지 않는다.

공유 MLX command fingerprint와 성공 counter **124→124**, 기존 swap **25,389,107,773 bytes 유지**, network-deny sandbox와 audit negative probe를 대조했다. 실제 학습 시험에는 소유 process group의 270초 hard deadline, OS reserve/swap/shared-work 감시를 추가했다. 제품 CLI의 40GiB admission과 cooperative deadline은 hard allocation cap 또는 강제 kernel 중단 보장이 아니다. 기존 공유 서버·전송 큐·운영 worker는 교체하지 않았다.

## 실제 한국어 생성 비교

[별도 generation receipt](alden-dpo-generation-20261001.json)는 같은 고정 validation prompt 3개를 bare base와 adapter에 temperature0/max64tokens로 각각 생성한 결과다. source·base·adapter SHA는 학습/평가 receipt와 일치한다. 기준 모델의 출력이 후보의 정답을 정의하지 않으며, 학습 전에 존재한 fixture의 명시 사실로 답을 대조했다.

두 판본 모두 “오전 10시”, “두 권”, “이미지 바이트를 읽지 못했으므로, 사진의 색감을 확인할 수 없습니다.”를 생성했다. **사실 일치 3/3→3/3, 출력 동일 3/3**이다. 부모가 모든 실제 응답을 직접 읽었으며 실제 사용자 대화나 보편적 품질 개선으로 확대하지 않는다.

| 문항, 각 판본 n=1 | 첫 text: base → adapter | 전체 생성: base → adapter | 생성 tokens/s: base → adapter |
| --- | --- | --- | --- |
| 회의 시각 | 0.693 → 0.831초 | 0.911 → 1.062초 | 34.48 → 32.64 |
| 최신 수량 | 0.454 → 0.414초 | 0.534 → 0.496초 | 45.49 → 45.05 |
| 읽지 못한 사진 | 0.433 → 0.399초 | 1.074 → 1.129초 | 32.28 → 28.22 |

MLX generation peak는 base 최대17,314,574,254/adapter 최대17,328,280,049 bytes(model load 후 reset), 전체 비교 process peak RSS17,451,548,672 bytes다. 이 실행은 앱 전체 메모리나 GraphRAG 검색을 측정하지 않는다. process28.263초, network시도0/거부probe 통과, shared124→124/swap기존값유지를 확인했다. 표본과 cache/host조건이 제한돼 p95·속도/메모리 개선율·품질 gain을 주장하지 않는다.

## 재현과 전달

아래 경로는 사용자가 가진 로컬 checkpoint와 private output directory의 절대 경로로 지정한다. 원본과 adapter는 다운로드하거나 자동 교체하지 않는다.

```sh
/absolute/path/to/pinned-mlx-python scripts/auto_reply_finetune.py \
  --dpo-train-dataset "$PWD/tests/fixtures/alden-model-evaluation/preferences.json" \
  --dpo-policy-dir /absolute/path/to/local-27b \
  --dpo-reference-dir /absolute/path/to/local-27b \
  --dpo-checkpoint-format mlx-serve-qwen3_5-converted \
  --dpo-training-root /absolute/path/to/private-candidates \
  --dpo-training-steps 3 --dpo-training-rank 2 --dpo-training-layers 1 \
  --dpo-training-learning-rate 0.00001 --dpo-training-deadline 240 --json

/absolute/path/to/pinned-mlx-python scripts/auto_reply_finetune.py \
  --model-evaluation-dataset "$PWD/tests/fixtures/alden-model-evaluation/preferences.json" \
  --evaluation-version 0.1.6-dpo-candidate \
  --evaluation-root /absolute/path/to/private-evaluations \
  --dpo-policy-dir /absolute/path/to/local-27b \
  --dpo-reference-dir /absolute/path/to/local-27b \
  --dpo-checkpoint-format mlx-serve-qwen3_5-converted \
  --dpo-adapter-dir /absolute/path/to/private-candidates/dpo-candidate --json
```

MLX runtime 집중126개/실패0, pinned menubar Python3.11.9의 CI66selectors **1054개/26skip/실패0**, build adapter forwarding 후속1개/실패0를 확인했다. 작은 실제 MLX numerical fixture는 이 27B 실행과 별도 근거다. 독립 Astra/max 검토의 P2 4건은 load 직전 취소, 실패 참조/준비 정리, staging inode와 directory fsync로 수정했고 회귀 검사에 포함했다. 모델/provider route의 독립 response trace를 확보했다고 주장하지 않는다.

Archify deliver는 9/9, 오류·경고0이며 실제 Chrome의 4개 desktop 크기에서 overflow0을 확인했다. 부모가 1440×900 light와 2048×1320 dark 이미지를 직접 읽었다. 작성 콘텐츠는 한국어이고 고정 Viewer UI는 영어 fallback이다. 순차 edge는 양 끝 노드가 작업 순서를 표현하므로 반복 label을 넣지 않았다. 이 그림과 실행은 설치 앱의 화면·사람 음성·운영 worker·공개 signed release 증거가 아니다.

실제 adapter 구성과 network-deny sandbox로 앱 build도 exit0을 확인했다. 제품 version `0.1.6`의 별도 key로 validation을 실제 채점했고 평가 runtime은 23.465826초였다(이 호출은 cache가 아님). source↔built resource **27/27**, installed resource도 **27/27** 같았으며 built app의 strict ad-hoc signature 검사가 통과했다. [Build receipt와 실행 provenance](alden-dpo-training-build-20261001.json)는 실제 호출·완료 exit와 raw/redacted log SHA에 묶인다. 기존 설치 앱은 교체하지 않았고 offline trainer와 후보를 앱에 넣지 않았다.

소스 `ba24fdda0da9606bd1310463c8cd73cf3045ee65`를 정상 커밋·푸시했고 [원격 CI36784060725](https://github.com/twoimo/openkakao-bot/actions/runs/36784060725)의 4개 작업이 모두 통과했다. Hosted Python1054/83skip/실패0, frontend191/desktopRust85 통과를 [readback](alden-dpo-training-code-ci-20261001.json)에 기록했다. Hosted skip 수는 로컬의 26개와 다르며, CI에서 건너뛴 MLX 수치 검사는 위 전용 runtime의 실행 근거로 확인한다.

최종 문서 head/CI/산출물은 ignored 전달 readback에 별도로 기록한다. private 후보 3개 파일은 임시 경로에서 보존용 산출물 경로로 복사하고 adapter fingerprint와 모든 파일 바이트를 대조했으며 public Git/supplement에는 넣지 않았다. 학습 source CLI와 private adapter 후보는 준비됐지만 설치 앱 상주 학습·제품 promotion·전체 목표는 미완료다.
