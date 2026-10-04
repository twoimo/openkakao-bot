# Alden local teacher-forced DPO scorer (2026-09-27)

2026-09-30 업데이트: 실제 27B MLX-Serve 체크포인트에서 발견한 norm 중복 보정을 수정했다. 아래의 9월 27일 fake 검증 기록과 별도로 [실제 모델 호환성 검증 및 수치](alden-dpo-checkpoint-compatibility-20260930.md)를 기록했다. `auto`는 MTP가 남아 있고 Conv1d가 이미 변환된 모호한 입력을 차단하며, 이 체크포인트에는 `--dpo-checkpoint-format mlx-serve-qwen3_5-converted`를 명시해야 한다.

## 목적

기존 `auto_reply_finetune.py`의 DPO 산술은 응답 토큰 로그확률이 주어졌을 때 표준 DPO loss를 계산할 수 있다. 그러나 로컬 OpenAI-compatible gateway의 응답 logprobs는 모델이 샘플링해 생성한 토큰에 대한 값이며, `/v1/completions`의 `echo=true`도 저장된 chosen/rejected 응답을 teacher forcing으로 채점하는 계약을 제공하지 않는다. 기존 probe는 이 경로를 `echo_unsupported`로 닫는다.

이번 단위는 `scripts/alden_dpo_scorer.py`에서 MLX-LM 모델을 직접 forward하여 동일 prompt의 지정된 chosen/rejected 응답을 policy와 frozen reference 양쪽에서 teacher forcing으로 채점한다. 문자열 유사도나 새 생성 샘플은 사용하지 않는다.

## 채점 계약

- checkpoint는 사용자가 명시한 **절대경로 로컬 디렉터리**만 허용한다.
- `config.json`과 `model*.safetensors`가 있어야 한다.
- `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`을 설정하고 tokenizer는 `trust_remote_code=False`, `local_files_only=True`로 연다.
- `mlx`, `mlx-lm`, `transformers`는 scorer 실행 시에만 lazy import한다.
- chat template는 pair마다 동일하게 `add_generation_prompt=True`, `enable_thinking=False`로 렌더한다.
- 렌더된 prompt만 토큰화한 ID가 `prompt + fixed response` 토큰열의 정확한 prefix여야 한다.
- 응답 첫 토큰은 마지막 prompt 위치의 logit에서 채점한다. 즉 입력은 `full_ids[:-1]`, target은 `full_ids[1:]`, 첫 score index는 `len(prompt_ids) - 1`이다.
- logits는 float32로 변환한 뒤 `log_softmax`와 `take_along_axis`로 지정 target token의 log-probability를 gather한다.
- 기본 chunk는 64 tokens, 허용 최대는 128이다. 응답은 최대 256 tokens, prompt+response 전체는 최대 2048 tokens, pair는 최대 32개다.
- chosen/rejected는 같은 prompt token prefix를 가져야 하며 policy/reference도 prompt, chosen response, rejected response token IDs가 정확히 같아야 한다.
- NaN/Inf, 양수 log-probability, token/logprob 길이 불일치, prompt/reference token 불일치는 모두 평가 불가로 닫는다.

## policy/reference 메모리 규칙

policy와 reference checkpoint가 다르면 한 helper 안에서 순차로 load → score → close → `gc.collect()` → MLX cache clear를 수행한다. 두 weight set을 동시에 유지하지 않는다.

두 checkpoint의 aggregate SHA-256가 같으면 policy score를 frozen reference score로 재사용하고 모델을 한 번만 연다. 이 경우 표준 DPO의 policy/reference 차이는 정확히 0이어야 하며 loss는 `ln(2) = 0.6931471805599453`이어야 한다. 이것은 scorer correctness invariant이며 품질 향상 증거가 아니다.

checkpoint SHA-256는 `config.json`, tokenizer 파일, 선택적 `generation_config.json`, 모든 `model*.safetensors`의 상대경로와 파일 바이트를 포함한다. 따라서 27B급 checkpoint에서는 provenance를 얻는 대신 checkpoint 전체를 한 번 읽는 I/O 비용이 있다. 이 child에서는 실제 27B checkpoint를 hash/load하지 않았다.

## 공개 report

공개 JSON에는 prompt 본문, chosen/rejected 본문, token IDs, checkpoint 경로를 넣지 않는다. 결과는 pair ID, response token counts, policy/reference summed log-probs, DPO delta/loss, runtime, peak bytes, checkpoint/tokenizer SHA-256, package versions와 reference reuse/frozen 상태만 포함한다. `peak_bytes_scope=scoring_after_model_load`를 함께 기록한다. MLX peak counter는 model load가 끝난 뒤 reset하므로 `peak_bytes`는 teacher-forced scoring 구간의 peak이며, checkpoint load를 포함한 프로세스 전체 최대 메모리가 아니다.

reference가 없거나 검증이 실패하면 `status=eval_unavailable`, `mean_loss=null`, `pairs=[]`로 반환하며 수치를 추정하지 않는다.

## CLI 연결

`scripts/auto_reply_finetune.py`에 아래 opt-in 옵션만 추가했다.

```text
--dpo-score-local
--dpo-policy-dir /absolute/local/policy
--dpo-reference-dir /absolute/local/reference
--dpo-checkpoint-format auto|mlx-lm|mlx-serve-qwen3_5-converted
```

`--dpo-score-local`이 없으면 기존 gateway DPO 경로를 그대로 사용한다. local scorer는 `--dpo-pairs`와 함께 사용할 때만 동작하며 기존 `dpo_loss_from_logprobs(..., require_reference=True)`에 teacher-forced policy/reference log-probs를 전달한다.

`auto_reply_finetune.py`와 scorer의 DPO helper import는 모두 `scripts.*` package import를 먼저 시도하고, `python scripts/auto_reply_finetune.py ...`처럼 `scripts/`가 sibling import root가 되는 직접 실행에서는 flat sibling import로 fallback한다.

## 검증

실제 27B weight load/inference는 parent 전용으로 남겼다. 이 child가 실행한 검증은 fake backend/tokenizer와 설치된 MLX-LM 소스 계약 확인뿐이다.

```text
python3 -m unittest tests.test_alden_dpo_scorer
21 tests: OK

/private/tmp/alden-mlx-eval-20260927/bin/python -m unittest tests.test_alden_dpo_scorer
21 tests: OK

python3 -m unittest tests.test_auto_reply_finetune
66 tests: OK
```

전용 테스트는 다음을 고정한다: first/last response-token logit alignment, prompt boundary mask, chosen/rejected same prompt, chunk equivalence, chunk limit, response/total token limits, NaN/positive/length failures, policy/reference prompt/response token mismatch, missing reference, tokenizer fingerprint mismatch, tokenizer hashing의 abort/deadline 전파, same-checkpoint single-load + exact `ln(2)`, distinct-checkpoint sequential loading, CLI default-off/local scorer 연결, 직접 script 실행의 sibling scorer import, scorer에서 기본 DPO helper로 이어지는 sibling import.

설치된 `mlx-lm 0.31.3`의 `evaluate.py`도 chunk별 model forward → float32 `log_softmax` → target gather → `mx.eval` → `mx.clear_cache` 패턴을 사용한다. `utils.py`는 전달 경로가 이미 존재하는 로컬 path이면 Hugging Face snapshot download를 수행하지 않는다. 실제 Qwen3.8-27B cache/weight 호환성은 parent의 synthetic live evaluation에서 최종 확인해야 한다.

## 아직 입증하지 않은 것

이 단위는 teacher-forced fixed-response scoring과 DPO loss 연결만 검증한다. adapter 학습 성공, tuned policy가 reference보다 좋아졌다는 주장, Kakao live send, worker/runtime 재시작, production promotion은 이 결과로 입증되지 않는다.
