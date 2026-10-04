# Alden의 DPO 후보 최종 테스트 — 2026-10-03

이전에 학습한 고정 27B LoRA 후보를 아직 사용하지 않은 합성 test 3쌍으로 평가했다. **제품 모델은 교체하지 않는다.** 평균 DPO 손실은 동일 policy/reference의 이론 기준보다 조금 높았고, 3문항 중 2문항의 선호 마진이 악화됐다. 실제 greedy 답변의 사실 일치는 기준·후보 모두 3/3이며 출력도 3/3 동일했다. 독립적인 이득이 없으므로 승격 근거가 없다.

[정제한 실행 기록](alden-dpo-final-test-20261003.json)은 실제 응답 토큰 로그확률·생성 답변·가중치/adapter/source SHA·네트워크 거부·메모리 감시·private 원시 기록 SHA에 묶인다. test는 이미 소비했으며, 이후 후보를 이 세 문항에 맞춰 조정하고 다시 독립 최종 테스트라고 부르지 않는다.

## 조건과 수학

같은 `ddalcu/Qwen3.8-27B-MLX-Serve-4bit`, checkpoint 형식 `mlx-serve-qwen3_5-converted`, 고정 adapter SHA `6a73fa7c79f65f453d25a27edd3db1082ae86e7d358d241d0ee77631408c78a0`을 사용했다. source aggregate와 base/tokenizer SHA는 이전 학습 기록과 같다. 새 학습은 수행하지 않았다. 생성은 temperature 0, max 64 tokens이며 기준 3문항 뒤 후보 3문항을 같은 과정에서 실행했다.

응답 토큰의 실제 log-prob 합을 ℓ로 놓으면,

`Δ=(ℓpolicy(chosen)−ℓpolicy(rejected))−(ℓreference(chosen)−ℓreference(rejected))`

`L=−log σ(0.1Δ)`

동일 policy/reference는 Δ=0이므로 이론 기준 L=ln(2)=0.693147181이다. 이는 별도 품질 점수나 생성 정확도가 아니다. 후보 평균 L=0.693542887로 이 기준보다 **0.05709% 높다**. 토큰 길이가 다른 응답의 단순 log-prob 순위를 대화 정확도로 치환하지 않는다.

| 고정 test 문항 | 후보 Δ | 후보 L | 생성한 사실: 기준 / 후보 |
|---|---:|---:|---|
| 읽지 않은 파일의 결론 | −0.050820 | 0.695691 | 본문 미확인 / 본문 미확인 |
| 최신 예약 취소 | −0.062679 | 0.696286 | 방문 불필요 / 방문 불필요 |
| 도구 로그와 사용자 역할 | +0.090122 | 0.688651 | 새 사용자 질문 없음 / 없음 |

모든 실제 답변을 고정 fixture의 명시 사실과 대조했다. 마지막 답변에는 불필요한 마무리 문장이 있었고 후보도 동일했다. 이 plain prompt 생성 비교를 제품의 음성 persona·사람 대화·RAG 전체 품질의 검증으로 확대하지 않는다.

## 실행 범위

M5 Max/128GiB, AC 전원에서 공유 추론 서버가 유휴 상태일 때 실행했다. 초기 메모리 admission 40GiB, 남은 OS reserve 8GiB, swap 증가 128MiB, 공유 작업 시작 및 소유 프로세스 150초 hard deadline을 감시했다. 감시 중단 이유 0, swap 증가 0, 공유 서버 command 보존을 확인했다. 기존 서비스와 설정, 원본 가중치 및 후보 파일을 교체하지 않았다.

| 실행, 각각 n=1 | 시간 | peak RSS | 범위 |
|---|---:|---:|---|
| final-test teacher forcing | 34.430초 | 실행 JSON 참조 | 기준·후보의 실제 log-prob |
| 기준·후보 생성 6회 | 34.644초 | 실행 JSON 참조 | 텍스트까지, 오디오 미포함 |

채점 MLX peak는 **17,365,610,136 bytes**(model load 후 reset)다. 생성의 첫 표시 시간은 기준 0.382–0.796초, 후보 0.406–0.655초였다. 이 순서·작은 표본에서 p95나 인과적인 속도 개선율을 주장하지 않는다. MLX allocation·process RSS·앱 전체 메모리를 합산하지 않는다. 모든 child runtime의 network 시도는 0이며, 별도 거부 probe도 통과했다. 패키지 다운로드와 모델 추론의 네트워크 범위는 구분한다.

이전 임시 평가 경로에는 사용할 수 있는 패키지가 없어 첫 시도가 0문항으로 끝났다. 복사한 생성 wrapper의 오래된 후보 경로도 수정했다. 실패를 성공 표본에 섞지 않았다. 운영 음성 환경을 건드리지 않고 별도 durable 평가 환경에 이전과 같은 핵심 버전인 [MLX 0.32.2](https://pypi.org/project/mlx/0.32.2/), [MLX-LM 0.31.3](https://pypi.org/project/mlx-lm/0.31.3/), [Transformers 5.17.0](https://pypi.org/project/transformers/5.17.0/)을 설치했다. Python은 3.11.16이며 전체 transitive 패키지가 과거 환경과 동일하다는 주장은 하지 않는다. 이번 기준·후보 비교는 같은 현재 환경에서 수행했다.

이 결과는 전체 목표의 오프라인 독립 평가를 보완한다. 사람 음성·공개 Developer ID 공증·생산 worker 전환 및 별도 Dot 호출의 남은 검증을 대신하지 않는다.
