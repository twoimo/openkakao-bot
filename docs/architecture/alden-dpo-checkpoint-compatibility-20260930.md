# Alden 27B checkpoint compatibility — 2026-09-30 KST

## 결과

실제 로컬 `ddalcu/Qwen3.8-27B-MLX-Serve-4bit` 체크포인트를 정식 scorer로 읽고 두 공개 합성 응답 쌍을 teacher forcing으로 채점했다. 새 greedy 산술 응답은 `4`이며, 자동 형식 판별로 안전하게 해석할 수 없는 입력은 `checkpoint_layout_ambiguous`로 닫는다. [숫자와 소스 해시 receipt](alden-dpo-checkpoint-compatibility-20260930.json)에 경로, 실제 카카오톡 대화, 토큰 ID는 없다.

## 오류 원인과 수정 범위

해당 [제작자 체크포인트의 conversion 계약](https://huggingface.co/ddalcu/Qwen3.8-27B-MLX-Serve-4bit#conversion)은 decoder/final/qk RMS norm에 이미 `+1`을 반영하고, Conv1d를 `[C,1,K]`에서 `[C,K,1]`로 변환하며, MTP head를 보존한다. 반면 [MLX-LM 0.31.3의 upstream sanitizer](https://github.com/ml-explore/mlx-lm/blob/v0.31.3/mlx_lm/models/qwen3_5.py)는 MTP가 남아 있거나 Conv1d가 변환되지 않았으면 해당 norm에 `+1`을 적용한다. 보존된 MTP가 두 번째 보정을 유발했다.

```text
raw checkpoint:       w_effective = 1 + w_raw
converted checkpoint: w_stored    = 1 + w_raw
wrong second fold:    w_effective = 1 + w_stored = 2 + w_raw
RMSNorm(x) = x / sqrt(mean(x²) + epsilon) * w_effective
```

scorer 전용 subclass와 `load_model(get_model_classes=...)` 경계에서 이미 변환된 형식의 MTP만 먼저 제거한다. upstream class, 설치된 MLX-LM 파일, 체크포인트 파일, 상주 MLX Core 설정은 바꾸지 않는다. GDN `linear_attn.norm`에는 offset을 더하지 않는다. 이는 전역 monkeypatch나 새로운 모델 architecture 구현이 아니다.

형식 계약은 다음과 같다.

| 옵션 | 계약 |
| --- | --- |
| `auto` (기본) | 기존 upstream 판별을 사용하되, MTP + 변환된 Conv1d의 모호한 조합을 평가 불가로 처리 |
| `mlx-lm` | 원래 upstream 변환 계약을 호출자가 명시; raw norm의 필요한 `+1`을 유지 |
| `mlx-serve-qwen3_5-converted` | Qwen3.5 계열 구조, 이미 변환된 Conv1d와 이미 보정된 norm을 명시; MTP를 upstream 판별 이전에 제거 |

변환된 형식은 MTP를 이미 제거한 체크포인트도 허용한다. Conv1d의 rank/크기 오류, 혼합된 변환 여부, 변환되지 않은 explicit MLX-Serve 입력, 다른 모델 구조, 알 수 없는 형식, custom model code는 차단한다. CLI는 `--dpo-score-local`과 `--dpo-checkpoint-format mlx-serve-qwen3_5-converted`를 함께 지정한다. 이 형식은 현재 policy와 reference 양쪽에 적용하므로 서로 다른 변환 계약의 두 체크포인트를 혼용하지 않는다.

## 실제 모델 측정

같은 체크포인트 SHA-256와 같은 공개 합성 산술 prompt/응답을 사용했다. baseline의 `ln(2)`만으로 모델 호환성을 판정하면 잘못된 채점도 통과하므로 생성 smoke check를 분리했다.

| 관찰 | 기존 MLX-LM 직접 load | 수정한 정식 scorer |
| --- | --- | --- |
| `2 + 2` greedy 응답 (최대 12 tokens) | `B z1 noncaceci0j! horizontalc` | `4` |
| 고정 `4.` summed log-prob | -23.227267 | -8.255856 |
| 고정 `5.` summed log-prob | -18.213265 | -15.718867 |
| 동일 policy/reference DPO loss | 0.6931471805599453 | 0.6931471805599453 |

수정본 첫 `4` 토큰 log-prob는 **-0.005496626254171133**이다. 앞서 같은 날 상주 native MLX Core에서 측정한 **-0.005831**과의 절대 차이는 **0.0003343737458288672**이다. native 관찰을 재사용했으며 bit-identical logits를 주장하지 않는다.

- 정식 scorer: 두 합성 pair, **18.521636 s** (checkpoint hashing/load/scoring 포함).
- MLX scoring peak: **17,327,024,114 bytes** (model load 이후 reset한 counter).
- 전체 process peak RSS: **17,436,213,248 bytes**. 자동 판별 거부와 별도 greedy load를 포함한 probe 전체는 **34.543519 s**.
- policy/reference aggregate SHA-256: `2607ed9ee6e6776d9a93d9937749ff1cabc5e6fa992656edd661f0f7bc019ae1`.
- 동일 reference는 같은 score를 재사용했고 delta는 두 pair 모두 0이다. 학습을 실행하지 않았으므로 loss 수렴이나 답변 품질 향상 증거가 아니다.
- stderr 0 bytes. 모델 다운로드, cloud inference, KakaoTalk send는 실행하지 않았다.

## 회귀 검증과 위임 증거

```text
/private/tmp/alden-mlx-eval-20260927/bin/python -m unittest \
  tests.test_alden_dpo_scorer tests.test_auto_reply_finetune
99 tests: OK (33 scorer + 66 finetune)

python3 -m unittest tests.test_alden_dpo_scorer
32 tests: OK; MLX-only numerical class skipped
```

MLX numerical test는 실제 upstream의 tiny model sanitizer를 호출하여 folded decoder/final/qk norm 유지, raw norm의 정확히 한 번 보정, GDN norm 불변, Conv1d transpose, MTP 제거, 전역 class 불변을 고정한다. fake boundary tests는 형식 실패와 CLI 전달을 별도로 확인한다. 평가 환경은 Python 3.11.16, MLX 0.32.2, MLX-LM 0.31.3, Transformers 5.17.0이다.

native current-session Web Sol/xhigh child `01a0f030-2c77-7fa3-a7a9-27a914cc91c0`가 scoped sanitizer helper 패치를 남겼다. 이후 `ChatGPT model controls are unavailable`로 종료되어 재시도하지 않았다. 부모가 부분 패치를 통합하고 CLI, 오류 경계, numerical regression, 실제 모델 readback을 완료했다. 별도 worker child에는 같은 DPO 요청을 보내지 않았다.

## 남은 범위

이 scorer는 source CLI에 opt-in으로 연결되어 있다. installed desktop runtime에 continuous evaluator를 배치하거나 adapter training/promotion을 실행한 결과는 없다. 전용 평가 runtime은 production 음성/브라우저 runtime과 별개다. synthetic checkpoint 호환성 수치로 live 카카오톡 skip rate, 대화 톤, 전체 시스템 메모리, 배터리 개선을 주장하지 않는다.
