# DREAM-RSI 논문 분석

## Provenance

- Paper: arXiv `2609.14858`, *Dream-RSI: Recursive Self-Improvement through Evolving Worlds* (Tong Zheng et al., Google / Google DeepMind / UMD / UVA)
- alphaXiv: https://www.alphaxiv.org/abs/2609.14858
- GitHub: https://github.com/zhengkid/Dream-RSI
- Readback: 2026-09-21에 `orx paper 2609.14858`로 조회했으며 exit 0, report body 14,515 chars를 확인했다.

이 기록은 이미 조회된 alphaXiv report를 바탕으로 한 provenance 분석이다. 새 네트워크 조회는 수행하지 않는다.

## 논문의 세 단계

1. **Online exploration**: 고정 coding agent가 programmable orchestration policy 아래 실제 환경을 탐색하며 discovery tree를 만든다.
2. **Replay-simulator construction**: 탐색에서 얻은 parent-child link, workspace state, artifact, diagnostic, score, cost를 고정 replay history로 구성한다.
3. **Dreaming-based policy improvement**: LLM policy agent가 exploration-policy code를 다시 쓰고, 후보 policy들을 기록된 branch에서 replay해 비교한다.

논문의 replay objective는 best-node quality에서 공개된 non-root node 수에 대한 cost penalty를 빼고 decision round당 평균 attempt 수에 대한 parallelism bonus를 더한다. Policy는 prefix에서 관찰 가능한 revealed observation, legal action, structural metadata, diagnostic만 사용할 수 있으며 unrevealed score, winning branch hardcode, true optimum 가정은 허용되지 않는다.

현재 policy도 후보에 포함하고 고정 replay history의 평균 replay score가 가장 높은 policy를 선택하므로, 선택 policy는 **그 고정 replay history에서만** 현재 policy보다 나쁘지 않다. 논문은 이 성질이 미래 online performance로 확장되지 않는다고 명시한다. 또한 replay simulator는 실제로 traversed된 search space만 포함한다.

## 저장소 대응

| Paper concept | Repository implementation |
| --- | --- |
| Online exploration | live AutoReply worker와 menubar의 `detect → authorize → queue → context → model → delay → send → confirm` pipeline stage |
| Replay-simulator construction | golden replay row에서 구성되는 `scripts/auto_reply_dream_rsi.py`의 `DreamRsiSimulator` |
| Dreaming-based policy improvement | `_candidate_policies()` + `DreamRsiSimulator.replay_policy()` + `dream_policy_evaluation()` |
| Prefix observability | 후보 policy 입력을 `{prompt, room, window}`로 제한하고 `gold`/`source`를 전달하지 않는 allowlist |
| Fixed-history non-degradation | `_candidate_policies()` 기본 후보 집합에 `INCUMBENT_POLICY`를 포함하며, 해당 후보가 평가된 경우 `replay_guarantee`가 `fixed_replay_set_only` 범위에서 incumbent와 selected objective score를 비교. 사용자 후보 집합에 incumbent가 없으면 not_applicable |
| Traversed-space limitation | replay는 기록된 golden history 밖을 평가하지 못한다. 저장소의 retrieval도 dense path가 불가하면 BM25-only로 명시적 degradation하며, DREAM-RSI 결과는 live promote로 사용하지 않는다. |

저장소의 replay objective는 논문의 식을 그대로 복제하지 않는다. 기존 식 `V = mean(similarity) - beta1*coverage_cost + beta2*spread*0.1`과 기존 candidate set/winner rule을 유지하며, 이번 변경은 그 결과에 고정 replay 집합 한정 non-degradation 증거를 명시적으로 붙인다.

## 경계

이 경로는 offline provenance와 replay evaluation을 위한 것이다. 모델을 train, promote, replace, start하지 않는다. 이 분석은 live KakaoTalk send나 live model generation을 입증하지 않는다.
