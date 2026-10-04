"""Reject comparisons that could falsely attest the E5 memory change."""

import copy
import unittest

from scripts.measure_alden_embedding_memory import compare_results


def sample(footprint, cache=0):
    return {
        "model": "pinned-e5@revision", "runtime": {"mlx": "same"},
        "requests": 44, "vectors": 165, "connection_attempts": [],
        "output_sha256": "same-float32-bytes", "load_seconds": 1,
        "idle": {"footprint_bytes": footprint, "mlx_cache_bytes": cache},
        "rows": [{"seconds": 1}],
    }


class EmbeddingMemoryMeasurementTests(unittest.TestCase):
    def test_uses_per_process_median_without_summing_memory_pools(self):
        result = compare_results([sample(100), sample(300), sample(200)],
                                 [sample(20), sample(10), sample(30)])
        self.assertEqual(result["before_median_footprint_bytes"], 200)
        self.assertEqual(result["after_median_footprint_bytes"], 20)
        self.assertEqual(result["footprint_reduction_percent"], 90)
        self.assertEqual(result["fresh_processes_per_variant"], 3)

    def test_rejects_model_runtime_output_workload_or_network_changes(self):
        for field, changed in (
            ("model", "other-model"), ("runtime", {"mlx": "other"}),
            ("output_sha256", "different"), ("requests", 43), ("vectors", 164),
            ("connection_attempts", ["external"]),
        ):
            with self.subTest(field=field):
                candidate = sample(20)
                candidate[field] = changed
                with self.assertRaises(ValueError):
                    compare_results([sample(200)], [candidate])

    def test_rejects_missing_samples_and_idle_cache_overshoot(self):
        for baseline, candidate in (
            ([], []), ([sample(200)], []),
            ([sample(200)], [sample(20, 512 * 1024**2 + 1)]),
        ):
            with self.subTest(candidate=copy.deepcopy(candidate)):
                with self.assertRaises(ValueError):
                    compare_results(baseline, candidate)
