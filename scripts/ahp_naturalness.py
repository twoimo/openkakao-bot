#!/usr/bin/env python3
"""AHP (Analytic Hierarchy Process) scoring for the AutoReply naturalness goal.

Goal: decide, with a reproducible number instead of an opinion, whether the
부자멘토멘티 auto-reply is "natural" enough to keep running unattended.

Hierarchy
  Goal      자연스럽게 자동 답변 중 (Pass)
  Criteria  근거 전달 / 말투·스타일 / 개입 시점·횟수 / 대화 적합성 / 운영 안정성 / 관측·추적성
  Scales    per-criterion score in [0, 1] measured from live artifacts

Weights come from a Saaty pairwise matrix (1-9). The script derives the
priority vector by power iteration, reports λmax, CI and the consistency ratio
(CR must stay below 0.10 for the matrix to count as "consistent"), and then
aggregates the measured criterion scores into a single weighted score.

Verdict rule (documented, so the evaluator cannot move the goalposts):
  PASS  when weighted_score >= 0.80 AND every criterion score >= 0.50
  HOLD  when weighted_score >= 0.60 but some criterion is below its floor
  FAIL  otherwise

Usage:
  python3 scripts/ahp_naturalness.py --json
  python3 scripts/ahp_naturalness.py --scores scores.json --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

CRITERIA: tuple[str, ...] = (
    "evidence_delivery",
    "register_style",
    "intervention_timing",
    "content_fit",
    "reliability",
    "observability",
)

CRITERIA_LABEL: dict[str, str] = {
    "evidence_delivery": "근거 전달(검색 번들이 프롬프트에 도달)",
    "register_style": "말투·스타일(현준 해요체·어미 다양화·길이)",
    "intervention_timing": "개입 시점·횟수(버스트 1건·페이싱)",
    "content_fit": "대화 적합성(상대 관심사·링크 캡션 금지)",
    "reliability": "운영 안정성(전송 성공·실패 기록·유실 방지)",
    "observability": "관측·추적성(이벤트별 근거 연결)",
}

# Saaty pairwise matrix. Row i vs column j: how much more important i is.
# Rationale: if the retrieved evidence never reaches the prompt, every other
# criterion is scored on a reply that had no material -- so evidence delivery
# dominates. Intervention count/timing is next (the room noticed double
# replies), then register, then content, then reliability, then observability.
PAIRWISE: tuple[tuple[float, ...], ...] = (
    (1.0, 3.0, 2.0, 4.0, 5.0, 5.0),
    (1.0 / 3.0, 1.0, 1.0 / 2.0, 2.0, 2.0, 3.0),
    (1.0 / 2.0, 2.0, 1.0, 2.0, 3.0, 3.0),
    (1.0 / 4.0, 1.0 / 2.0, 1.0 / 2.0, 1.0, 2.0, 2.0),
    (1.0 / 5.0, 1.0 / 2.0, 1.0 / 3.0, 1.0 / 2.0, 1.0, 2.0),
    (1.0 / 5.0, 1.0 / 3.0, 1.0 / 3.0, 1.0 / 2.0, 1.0 / 2.0, 1.0),
)

# Random consistency index (Saaty) for n = 1..10.
RI: tuple[float, ...] = (0.0, 0.0, 0.58, 0.90, 1.12, 1.24, 1.32, 1.41, 1.45, 1.49)

DEFAULT_SCORES: dict[str, float] = {
    "evidence_delivery": 0.0,
    "register_style": 0.0,
    "intervention_timing": 0.0,
    "content_fit": 0.0,
    "reliability": 0.0,
    "observability": 0.0,
}


def priority_vector(matrix: list[list[float]], iterations: int = 200) -> list[float]:
    size = len(matrix)
    vector = [1.0 / size] * size
    for _ in range(iterations):
        candidate = [
            sum(matrix[i][j] * vector[j] for j in range(size)) for i in range(size)
        ]
        total = sum(candidate)
        if total <= 0:
            raise ValueError("pairwise matrix has no positive weight")
        vector = [value / total for value in candidate]
    return vector


def consistency(matrix: list[list[float]], weights: list[float]) -> dict[str, float]:
    size = len(matrix)
    lambda_max = 0.0
    for i in range(size):
        row = sum(matrix[i][j] * weights[j] for j in range(size))
        lambda_max += row / weights[i]
    lambda_max /= size
    ci = (lambda_max - size) / (size - 1) if size > 1 else 0.0
    ri = RI[size - 1] if 0 < size <= len(RI) else 0.0
    cr = ci / ri if ri else 0.0
    return {
        "lambda_max": lambda_max,
        "consistency_index": ci,
        "consistency_ratio": cr,
    }


def load_scores(path: Path | None) -> tuple[dict[str, float], dict[str, str]]:
    if path is None:
        return dict(DEFAULT_SCORES), {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    scores: dict[str, float] = {}
    notes: dict[str, str] = {}
    for key in CRITERIA:
        item = raw.get(key)
        if isinstance(item, dict):
            value = item.get("score")
            note = item.get("note")
        else:
            value = item
            note = None
        try:
            score = float(value)
        except (TypeError, ValueError):
            raise SystemExit(f"missing or non-numeric score for {key!r}")
        if not 0.0 <= score <= 1.0:
            raise SystemExit(f"score for {key!r} must be within 0..1")
        scores[key] = score
        if isinstance(note, str):
            notes[key] = note
    return scores, notes


def evaluate(scores: dict[str, float], notes: dict[str, str]) -> dict[str, Any]:
    matrix = [list(row) for row in PAIRWISE]
    if len(matrix) != len(CRITERIA):
        raise SystemExit("pairwise matrix does not match the criteria list")
    weights = priority_vector(matrix)
    stats = consistency(matrix, weights)
    weighted = sum(weights[index] * scores[key] for index, key in enumerate(CRITERIA))
    floors = {key: scores[key] < 0.5 for key in CRITERIA}
    if weighted >= 0.80 and not any(floors.values()):
        verdict = "pass"
    elif weighted >= 0.60:
        verdict = "hold"
    else:
        verdict = "fail"
    if stats["consistency_ratio"] > 0.10:
        verdict = "hold"
    rows = [
        {
            "criterion": key,
            "label": CRITERIA_LABEL[key],
            "weight": round(weights[index], 4),
            "score": round(scores[key], 3),
            "weighted": round(weights[index] * scores[key], 4),
            "below_floor": floors[key],
            "note": notes.get(key, ""),
        }
        for index, key in enumerate(CRITERIA)
    ]
    rows.sort(key=lambda row: row["weighted"])
    return {
        "verdict": verdict,
        "weighted_score": round(weighted, 4),
        "consistency": {key: round(value, 5) for key, value in stats.items()},
        "criteria": rows,
        "weakest": rows[0]["criterion"] if rows else None,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ahp_naturalness.py")
    parser.add_argument("--scores", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv[1:])
    scores, notes = load_scores(args.scores)
    result = evaluate(scores, notes)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(f"verdict={result['verdict']} weighted={result['weighted_score']:.4f}")
    print(
        "consistency: lambda_max={lambda_max:.3f} CI={consistency_index:.4f} CR={consistency_ratio:.4f}".format(
            **result["consistency"]
        )
    )
    for row in result["criteria"]:
        print(
            f"  {row['criterion']:<22} w={row['weight']:.3f} score={row['score']:.2f} "
            f"weighted={row['weighted']:.3f}{'  <floor' if row['below_floor'] else ''}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
