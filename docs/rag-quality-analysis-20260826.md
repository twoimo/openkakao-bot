# RAG Retrieval Quality Analysis — 2026-08-26

## 요약

두 채팅방(kakao-test, 부자멘토멘티)의 과거 결정 레저를 분석하여
검색 컨텍스트 윈도우 크기(RECENT_MESSAGE_LIMIT)가 실제 답변 품질에
미치는 영향을 측정했다.

## 핵심 발견

### 1. 인용 위치 분포 (citation age distribution)

답변이 참조한 `recent:*` 증거의 대화 내 위치(마지막 메시지로부터의 거리):

| 위치(age) | kakao-test | 부자멘토멘티 |
|-----------|-----------|------------|-----------|
| 0 (최신) | 61.9% | 43.2% |
| 0-1 | 82.1% | ~65% |
| 0-2 | 84.5% | ~72% |
| 0-4 | 90.5% | ~87% |
| 0-6 | 94.0% | ~95% |
| 0-12 (전체) | 100% | 100% |

### 2. recall@k 시뮬레이션

| k (메시지 수) | kakao-test | 부자멘토멘티 |
|-------------|-----------|------------|
| 3 | 84.5% | 85.8% |
| 5 | 90.5% | 92.0% |
| **7** | **94.0%** | **96.4%** |
| 10 | 98.8% | 98.2% |
| 13 (현재) | 100% | 100% |

### 3. 권장 사항

- **RECENT_MESSAGE_LIMIT 13 → 7로 축소**: recall 94~96% 유지하면서
  프롬프트 크기 ~46% 절감. 추론 속도·비용 개선 효과.
- k=5까지 축소 시 recall 90~92%로 추가 절감 가능하나,
  멀티턴 맥락 손실 위험이 있으므로 7을 권장.

### 4. 모델 간 검색 일관성

일반 대화 한정(긱뉴스 장문 제외):

| 모델 | 방 | n | citation_recall | pool_utilization |
|------|----|---|----------------|-----------------|
| Qwen 3.6 | kakao-test | 41 | 0.314 | 9.7% |
| Gemini 3.7 | kakao-test | 35 | 0.360 | 12.8% |
| Gemini 3.7 | 부자 | 121 | 0.289 | 14.3% |
| Qwen 3.6 | 부자 | 5 | 0.381 | 16.7% |

→ 모델에 관계없이 인용 패턴이 유사 (recall 0.29~0.38).
   체감 품질 차이는 검색이 아니라 생성 스타일에서 기인.
   따라서 검색 파라미터(k) 최적화는 모델 독립적으로 적용 가능.

### 5. 미해결 / 다음 단계

- `rerank_scores` 필드가 전부 빈 리스트: BGE 리랭크가 스코어를
  반환하지 않거나 비활성 상태. 이 로깅을 활성화해야
  정밀도(precision@k) 곡선을 그릴 수 있다.
- citation_recall은 프록시 지표. 진짜 precision/recall을 위해서는
  소규모 golden set(수동 라벨링)이 필요.
- GeekNews proactive 포스트는 일반 대화와 특성이 달라 분리 분석 필수.

## 재현 방법

```bash
python3 scripts/rag_eval.py --json          # 전체 지표
python3 scripts/rag_eval.py                 # 사람 읽기 형식
```

인용 위치 분포는 `scripts/rag_eval.py`에 통합됐다.
`python3 scripts/rag_eval.py` / `--json` 출력의 `citation_positions`(recall@k, precision@k, window_len_mean, rerank empty/scored)를 본다.

## 데이터 출처

- `rooms/<id>/reply-evidence.jsonl`: 스케줄된 결정당 1레코드
- 부자멘토멘티: 558건 (일반 254 / proactive 304)
- kakao-test: 84건 (일반 76 / proactive 8)
- 기간: 2026-08-22 ~ 2026-08-26

---

## 세션 복구 상태 (2026-08-27 18:05 KST)

### 현재 상태
자동응답은 enrollment `20260826T121422Z-3370`에서 양방 `ready`다.
같은 bake 디렉터리의 `openkakao-cli`와 `scripts/auto-reply-worker.py`는 2026-08-27 16:45–16:47 KST에 워크스페이스 릴리스와 동기화됐고, 프로세스는 그 시각에 다시 떠 있다. `OPENKAKAO_ALLOW_IMAGE_ANALYSIS=1`.

### 사진 비전
워크스페이스 `media.rs`는 Kakao JPEG EOI 뒤 Samsung SEF + NUL 패딩(최대 8KiB)을 잘라 디코드한다. 유닛 테스트 `kakao_padded_sef_trailer_that_does_not_end_with_seft_is_stripped` 통과. 라이브 바이너리로 kakao-test `download --local`도 PNG 정규화에 성공했다.

이미 fallback(`media_unavailable_clarification`)이 나간 사진은 재시도하지 않는다. 비전 확인은 kakao-test에서 MOM(`206894008`)이 **새 사진**을 보낼 때만 가능하다.

G004 / G005 / G009는 이 경로에서 제외한다.
