"""On-device recommendation tests.

The recommender must never hand an engine a model id that engine cannot load,
and it must not call a non-Apple machine Apple Silicon. Both were real defects
found in review, so they are pinned here (2026-09-17).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.auto_reply_ondevice import (
    HardwareSpec,
    _is_apple_silicon_chip,
    detect_available_engines,
    detect_engine_paths,
    detect_hardware,
    download_command,
    generate_command,
    ondevice_summary_dict,
    recommend_ondevice_setup,
    verify_ondevice_setup,
)


def _hw(chip: str, memory_gb: float) -> HardwareSpec:
    return HardwareSpec(
        chip=chip,
        cores=18,
        memory_bytes=int(memory_gb * 1024**3),
        memory_gb=memory_gb,
        is_apple_silicon=_is_apple_silicon_chip(chip),
    )


class TestHardwareDetection(unittest.TestCase):
    def test_detect_hardware_returns_valid_spec(self):
        hw = detect_hardware()
        self.assertIsInstance(hw.chip, str)
        self.assertGreater(hw.cores, 0)
        self.assertGreater(hw.memory_bytes, 0)
        self.assertGreater(hw.memory_gb, 0)

    def test_apple_chips_are_recognised(self):
        for chip in ("Apple M1", "Apple M5 Max", "Apple M2 Ultra"):
            with self.subTest(chip=chip):
                self.assertTrue(_is_apple_silicon_chip(chip))

    def test_non_apple_chips_are_not_apple_silicon(self):
        # "AMD Ryzen" and "MediaTek" both contain an M, so a substring test on
        # "M" wrongly reported them as Apple Silicon.
        for chip in ("AMD Ryzen 9 7950X", "MediaTek Dimensity 9300", "Intel Core i9", ""):
            with self.subTest(chip=chip):
                self.assertFalse(_is_apple_silicon_chip(chip))

    def test_engine_paths_are_executables(self):
        import os

        for name, path in detect_engine_paths().items():
            with self.subTest(engine=name):
                self.assertTrue(os.path.isfile(path), f"{name} path must exist")
                self.assertTrue(os.access(path, os.X_OK), f"{name} path must be executable")


class TestGemmaRecommendation(unittest.TestCase):
    def test_large_apple_silicon_gets_the_gemma_4_qat_tier(self):
        rec = recommend_ondevice_setup(_hw("Apple M5 Max", 128.0), engines={"mlx": "/x/mlx_lm"})
        self.assertEqual(rec.primary_engine, "mlx")
        self.assertIn("gemma-4", rec.recommended_model)
        self.assertIn("qat", rec.recommended_model)
        self.assertIn("128.0GB", rec.reason)

    def test_mid_memory_gets_the_gemma_4_e4b_tier(self):
        rec = recommend_ondevice_setup(_hw("Apple M3 Pro", 36.0), engines={"mlx": "/x/mlx_lm"})
        self.assertEqual(rec.recommended_model, "mlx-community/gemma-4-e4b-it-4bit")

    def test_small_memory_gets_the_gemma_4_e2b_tier(self):
        rec = recommend_ondevice_setup(_hw("Apple M1", 16.0), engines={"mlx": "/x/mlx_lm"})
        self.assertEqual(rec.recommended_model, "mlx-community/gemma-4-e2b-it-4bit")
        self.assertNotIn("31b", rec.recommended_model)

    def test_ollama_never_receives_an_mlx_repo_id(self):
        rec = recommend_ondevice_setup(_hw("Apple M2", 32.0), engines={"ollama": "/x/ollama"})
        self.assertEqual(rec.primary_engine, "ollama")
        self.assertNotIn("mlx-community/", rec.recommended_model)
        self.assertTrue(rec.recommended_model.startswith("gemma3:"))

    def test_llamacpp_never_receives_an_mlx_repo_id(self):
        rec = recommend_ondevice_setup(_hw("Intel Core i9", 32.0), engines={"llama.cpp": "/x/llama-cli"})
        self.assertEqual(rec.primary_engine, "llama.cpp")
        self.assertNotIn("mlx-community/", rec.recommended_model)
        self.assertIn("GGUF", rec.recommended_quant)

    def test_worker_routing_id_is_reported_separately(self):
        rec = recommend_ondevice_setup(_hw("Apple M5 Max", 128.0), engines={"mlx": "/x/mlx_lm"})
        self.assertTrue(rec.worker_model_id)
        self.assertNotEqual(rec.worker_model_id, rec.recommended_model)
        self.assertIn(rec.worker_model_id, rec.fallback_models)

    def test_no_engine_installed_still_returns_a_usable_answer(self):
        rec = recommend_ondevice_setup(_hw("Apple M5 Max", 128.0), engines={})
        self.assertTrue(rec.recommended_model)
        self.assertIn("찾지 못해", rec.reason)


class TestCommands(unittest.TestCase):
    def test_mlx_download_and_generate_commands(self):
        rec = recommend_ondevice_setup(_hw("Apple M5 Max", 128.0), engines={"mlx": "/x/mlx_lm"})
        self.assertTrue(download_command(rec).startswith("hf download "))
        generate = generate_command(rec, "안녕")
        self.assertIn("mlx_lm.generate", generate)
        self.assertIn("--max-tokens", generate)

    def test_ollama_commands_use_pull_and_run(self):
        rec = recommend_ondevice_setup(_hw("Apple M2", 32.0), engines={"ollama": "/x/ollama"})
        self.assertTrue(download_command(rec).startswith("ollama pull "))
        self.assertTrue(generate_command(rec, "안녕").startswith("ollama run "))

    def test_summary_is_serializable(self):
        summary = ondevice_summary_dict()
        self.assertIn("hardware", summary)
        self.assertIn("recommendation", summary)
        self.assertIn("engine_paths", summary["recommendation"])
        self.assertIn("fallback_models", summary["recommendation"])
        self.assertIn("verification", summary)


class TestVerification(unittest.TestCase):
    def test_verify_fails_when_engine_path_is_empty(self):
        rec = recommend_ondevice_setup(_hw("Apple M5 Max", 128.0), engines={})

        result = verify_ondevice_setup(rec)

        self.assertFalse(result["ok"])
        self.assertTrue(result["errors"])

    def test_verify_fails_when_mlx_model_directory_is_missing(self):
        rec = recommend_ondevice_setup(
            _hw("Apple M5 Max", 128.0),
            engines={"mlx": sys.executable},
        )
        with TemporaryDirectory() as temp_home:
            with patch("scripts.auto_reply_ondevice.Path.home", return_value=Path(temp_home)):
                result = verify_ondevice_setup(rec)

        self.assertFalse(result["ok"])
        self.assertTrue(any(check["name"] == "model_directory" and not check["ok"] for check in result["checks"]))

    def test_verify_does_not_download_weights(self):
        rec = recommend_ondevice_setup(
            _hw("Apple M2", 32.0),
            engines={"ollama": sys.executable},
        )
        with patch("scripts.auto_reply_ondevice.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = f"NAME ID SIZE MODIFIED\n{rec.recommended_model} abc 1 GB now\n"
            result = verify_ondevice_setup(rec)

        self.assertTrue(result["ok"])
        run.assert_called_once_with(
            [sys.executable, "list"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )

    def test_verify_returns_stable_envelope(self):
        rec = recommend_ondevice_setup(_hw("Apple M5 Max", 128.0), engines={})

        result = verify_ondevice_setup(rec)

        self.assertEqual({"ok", "engine", "model", "checks", "errors"}, set(result))


if __name__ == "__main__":
    unittest.main()
