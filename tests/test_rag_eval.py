import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import rag_eval


class RagEvalTests(unittest.TestCase):
    def test_citation_position_recall_and_precision(self):
        records = [
            {
                "evidence_ids": ["recent:a", "recent:b", "timing:1"],
                "recent_conversation": [
                    {"evidence_id": "recent:c"},
                    {"evidence_id": "recent:b"},
                    {"evidence_id": "recent:a"},
                ],
                "rerank_scores": [0.9, 0.1],
                "fallback": "none",
            },
            {
                "evidence_ids": ["recent:z"],
                "recent_conversation": [
                    {"evidence_id": "recent:x"},
                    {"evidence_id": "recent:y"},
                    {"evidence_id": "recent:z"},
                ],
                "rerank_scores": [],
                "fallback": "timeout",
            },
        ]
        result = rag_eval.citation_position_analysis(records)
        self.assertEqual(result["citations"], 3)
        self.assertEqual(result["recall_at_k"][3], 1.0)
        self.assertEqual(result["precision_at_k"][3], 0.5)
        self.assertEqual(result["rerank_empty"], 1)
        self.assertEqual(result["rerank_scored"], 1)
        self.assertEqual(result["rerank_fallbacks"]["timeout"], 1)

    def test_single_candidate_window_precision(self):
        records = [
            {
                "evidence_ids": ["recent:only"],
                "recent_conversation": [{"evidence_id": "recent:only"}],
                "rerank_scores": [],
                "fallback": "none",
            }
        ]
        result = rag_eval.citation_position_analysis(records)
        self.assertEqual(result["precision_at_k"][3], 1.0)
        self.assertEqual(result["window_len_mean"], 1.0)


if __name__ == "__main__":
    unittest.main()
