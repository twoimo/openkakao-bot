"""Tests for the Apple Silicon MLX Core/Serve recommendation and probe."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from scripts.auto_reply_ondevice import (
    FLASH_NEXT_MODEL_ID,
    QWEN38_27B_MODEL_ID,
    EngineRecommendation,
    HardwareSpec,
    _is_apple_silicon_chip,
    detect_engine_paths,
    detect_hardware,
    detect_mlx_gateway_models,
    download_command,
    generate_command,
    ondevice_summary_dict,
    probe_ondevice_generation,
    read_last_probe,
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


def _gateway_models() -> list[dict[str, str]]:
    return [
        {"id": QWEN38_27B_MODEL_ID, "owned_by": "mlx-serve"},
        {"id": FLASH_NEXT_MODEL_ID, "owned_by": "mlx-serve"},
    ]


def _gateway_rec(memory_gb: float = 128.0) -> EngineRecommendation:
    return recommend_ondevice_setup(
        _hw("Apple M5 Max", memory_gb),
        engines={
            "mlx_lm": sys.executable,
            "mlx-serve": sys.executable,
            "mlx-gateway": "http://127.0.0.1:10100/v1",
        },
        gateway_models=_gateway_models(),
    )


class _HTTPResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


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
        for chip in ("AMD Ryzen 9 7950X", "MediaTek Dimensity 9300", "Intel Core i9", ""):
            with self.subTest(chip=chip):
                self.assertFalse(_is_apple_silicon_chip(chip))

    def test_engine_paths_only_report_mlx_runtime_entries(self):
        paths = detect_engine_paths(gateway_models=[])
        self.assertTrue(set(paths).issubset({"mlx_lm", "mlx-serve", "mlx-gateway"}))
        for name, path in paths.items():
            with self.subTest(engine=name):
                if name != "mlx-gateway":
                    self.assertTrue(Path(path).is_file())

    def test_gateway_detection_filters_to_mlx_models(self):
        payload = {
            "data": [
                {"id": FLASH_NEXT_MODEL_ID, "owned_by": "mlx-serve"},
                {"id": "remote/model", "owned_by": "remote"},
            ]
        }
        with patch(
            "scripts.auto_reply_ondevice.urllib.request.urlopen",
            return_value=_HTTPResponse(payload),
        ):
            models = detect_mlx_gateway_models()
        self.assertEqual(models, [{"id": FLASH_NEXT_MODEL_ID, "owned_by": "mlx-serve"}])


class TestQwenRecommendation(unittest.TestCase):
    def test_large_apple_silicon_prefers_actual_served_qwen38_27b(self):
        rec = _gateway_rec(128.0)
        self.assertEqual(rec.primary_engine, "mlx-serve")
        self.assertEqual(rec.recommended_model, QWEN38_27B_MODEL_ID)
        self.assertEqual(rec.worker_model_id, FLASH_NEXT_MODEL_ID)
        self.assertIn(FLASH_NEXT_MODEL_ID, rec.fallback_models)
        self.assertNotIn("gemma", json.dumps(rec.__dict__).casefold())

    def test_smaller_apple_silicon_prefers_flash_next(self):
        rec = _gateway_rec(24.0)
        self.assertEqual(rec.primary_engine, "mlx-serve")
        self.assertEqual(rec.recommended_model, FLASH_NEXT_MODEL_ID)
        self.assertEqual(rec.recommended_quant, "mixed 4/8bit")

    def test_mlx_lm_uses_discovered_local_path_not_serving_id(self):
        with TemporaryDirectory() as temp_dir:
            model_path = Path(temp_dir) / "Qwen3.8-27B-local"
            model_path.mkdir()
            rec = recommend_ondevice_setup(
                _hw("Apple M5 Max", 64.0),
                engines={"mlx_lm": sys.executable},
                local_models={"Qwen3.8-27B-local": str(model_path)},
            )
        self.assertEqual(rec.primary_engine, "mlx_lm")
        self.assertEqual(rec.recommended_model, str(model_path))
        self.assertFalse(rec.recommended_model.startswith("mlx/"))

    def test_non_mlx_engines_never_become_primary(self):
        rec = recommend_ondevice_setup(
            _hw("Apple M2", 32.0),
            engines={"ollama": "/x/ollama", "browser": "/x/browser"},
        )
        self.assertEqual(rec.primary_engine, "mlx-serve")
        self.assertNotIn("ollama", rec.available_engines)
        self.assertNotIn("browser", rec.available_engines)
        self.assertNotIn("gemma", rec.reason.casefold())

    def test_non_apple_machine_is_not_routed_to_mlx_generation(self):
        rec = recommend_ondevice_setup(
            _hw("Intel Core i9", 64.0),
            engines={"mlx_lm": sys.executable},
        )
        self.assertEqual(rec.primary_engine, "mlx-unavailable")
        self.assertEqual(rec.recommended_model, "")

    def test_no_engine_installed_is_fail_closed_but_keeps_serving_recommendation(self):
        rec = recommend_ondevice_setup(_hw("Apple M5 Max", 128.0), engines={})
        self.assertEqual(rec.primary_engine, "mlx-serve")
        self.assertEqual(rec.recommended_model, QWEN38_27B_MODEL_ID)
        self.assertIn("찾지 못함", rec.reason)


class TestCommandsAndSummary(unittest.TestCase):
    def test_download_command_is_disabled(self):
        self.assertEqual(download_command(_gateway_rec()), "")

    def test_generate_description_uses_mlx_serve(self):
        text = generate_command(_gateway_rec(), "안녕")
        self.assertIn("MLX Serve", text)
        self.assertIn(QWEN38_27B_MODEL_ID, text)
        self.assertNotIn("gemma", text.casefold())

    def test_summary_is_serializable_and_includes_last_probe_field(self):
        rec = _gateway_rec()
        with TemporaryDirectory() as temp_dir:
            with patch("scripts.auto_reply_ondevice.detect_hardware", return_value=_hw("Apple M5 Max", 128.0)), patch(
                "scripts.auto_reply_ondevice.recommend_ondevice_setup", return_value=rec
            ), patch(
                "scripts.auto_reply_ondevice.verify_ondevice_setup",
                return_value={"ok": True, "engine": "mlx-serve", "model": rec.recommended_model, "checks": [], "errors": []},
            ):
                summary = ondevice_summary_dict(Path(temp_dir))
        self.assertIn("last_probe", summary)
        self.assertIsNone(summary["last_probe"])
        self.assertIn("MLX Core/Serve", summary["status_label"])
        self.assertIn("Qwen3.8 27B", summary["status_label"])
        json.dumps(summary, ensure_ascii=False)


class TestVerification(unittest.TestCase):
    def test_verify_fails_when_mlx_runtime_is_missing(self):
        rec = recommend_ondevice_setup(_hw("Apple M5 Max", 128.0), engines={})
        result = verify_ondevice_setup(rec)
        self.assertFalse(result["ok"])
        self.assertTrue(result["errors"])

    def test_verify_served_model_is_read_only(self):
        rec = _gateway_rec()
        with patch("scripts.auto_reply_ondevice.detect_mlx_gateway_models", return_value=_gateway_models()), patch(
            "scripts.auto_reply_ondevice.subprocess.Popen"
        ) as popen:
            result = verify_ondevice_setup(rec)
        self.assertTrue(result["ok"])
        popen.assert_not_called()

    def test_verify_returns_stable_envelope(self):
        result = verify_ondevice_setup(
            recommend_ondevice_setup(_hw("Apple M5 Max", 128.0), engines={})
        )
        self.assertEqual({"ok", "engine", "model", "checks", "errors"}, set(result))


class TestProbe(unittest.TestCase):
    def test_probe_prefers_flash_next_gateway_and_persists_tiny_record(self):
        rec = _gateway_rec()
        response = _HTTPResponse(
            {"choices": [{"message": {"content": "LOCAL_OK from local MLX"}}]}
        )
        with TemporaryDirectory() as temp_dir, patch(
            "scripts.auto_reply_ondevice.urllib.request.urlopen", return_value=response
        ) as urlopen:
            result = probe_ondevice_generation(rec, state_root=Path(temp_dir), timeout=2)
            persisted = read_last_probe(Path(temp_dir))
        self.assertTrue(result["ok"])
        self.assertEqual(result["engine"], "mlx-serve-gateway")
        self.assertEqual(result["model"], FLASH_NEXT_MODEL_ID)
        self.assertEqual(persisted["model"], FLASH_NEXT_MODEL_ID)
        self.assertLessEqual(len(persisted["preview"]), 240)
        self.assertNotIn("errors", persisted)
        urlopen.assert_called_once()

    def test_probe_missing_engine_fails_closed(self):
        rec = EngineRecommendation(
            primary_engine="mlx-serve",
            available_engines=[],
            recommended_model=QWEN38_27B_MODEL_ID,
            recommended_quant="4bit",
            reason="test",
            engine_paths={},
            fallback_models=[FLASH_NEXT_MODEL_ID],
            worker_model_id=FLASH_NEXT_MODEL_ID,
        )
        with TemporaryDirectory() as temp_dir, patch(
            "scripts.auto_reply_ondevice.detect_local_qwen_models", return_value={}
        ), patch("scripts.auto_reply_ondevice.subprocess.Popen") as popen:
            result = probe_ondevice_generation(rec, state_root=Path(temp_dir), timeout=1)
        self.assertFalse(result["ok"])
        self.assertIn("missing_engine_or_local_weights", result["errors"])
        popen.assert_not_called()

    def test_probe_timeout_kills_child(self):
        with TemporaryDirectory() as temp_dir:
            model_path = Path(temp_dir) / "Qwen3.8-Flash-Next-local"
            model_path.mkdir()
            rec = EngineRecommendation(
                primary_engine="mlx_lm",
                available_engines=["mlx_lm"],
                recommended_model=str(model_path),
                recommended_quant="local",
                reason="test",
                engine_paths={"mlx_lm": sys.executable},
                fallback_models=[FLASH_NEXT_MODEL_ID],
                worker_model_id=FLASH_NEXT_MODEL_ID,
            )
            proc = MagicMock()
            proc.communicate.side_effect = [
                subprocess.TimeoutExpired(cmd="mlx_lm", timeout=0.1),
                ("", ""),
            ]
            with patch(
                "scripts.auto_reply_ondevice.detect_local_qwen_models",
                return_value={model_path.name: str(model_path)},
            ), patch("scripts.auto_reply_ondevice.subprocess.Popen", return_value=proc):
                result = probe_ondevice_generation(rec, state_root=Path(temp_dir), timeout=0.1)
        self.assertFalse(result["ok"])
        self.assertIn("probe_timeout", result["errors"])
        proc.kill.assert_called_once()

    def test_probe_nonzero_exit_fails_closed(self):
        with TemporaryDirectory() as temp_dir:
            model_path = Path(temp_dir) / "Qwen3.8-Flash-Next-local"
            model_path.mkdir()
            rec = EngineRecommendation(
                "mlx_lm", ["mlx_lm"], str(model_path), "local", "test",
                {"mlx_lm": sys.executable}, [FLASH_NEXT_MODEL_ID], FLASH_NEXT_MODEL_ID,
            )
            proc = MagicMock(returncode=7)
            proc.communicate.return_value = ("", "failure")
            with patch(
                "scripts.auto_reply_ondevice.detect_local_qwen_models",
                return_value={model_path.name: str(model_path)},
            ), patch("scripts.auto_reply_ondevice.subprocess.Popen", return_value=proc):
                result = probe_ondevice_generation(rec, state_root=Path(temp_dir), timeout=1)
        self.assertFalse(result["ok"])
        self.assertIn("mlx_lm_exit_7", result["errors"])

    def test_probe_empty_output_fails_closed(self):
        with TemporaryDirectory() as temp_dir:
            model_path = Path(temp_dir) / "Qwen3.8-Flash-Next-local"
            model_path.mkdir()
            rec = EngineRecommendation(
                "mlx_lm", ["mlx_lm"], str(model_path), "local", "test",
                {"mlx_lm": sys.executable}, [FLASH_NEXT_MODEL_ID], FLASH_NEXT_MODEL_ID,
            )
            proc = MagicMock(returncode=0)
            proc.communicate.return_value = ("  \n", "")
            with patch(
                "scripts.auto_reply_ondevice.detect_local_qwen_models",
                return_value={model_path.name: str(model_path)},
            ), patch("scripts.auto_reply_ondevice.subprocess.Popen", return_value=proc):
                result = probe_ondevice_generation(rec, state_root=Path(temp_dir), timeout=1)
        self.assertFalse(result["ok"])
        self.assertIn("empty_output", result["errors"])

    def test_probe_sanitizes_injection_like_prompt_before_http(self):
        rec = _gateway_rec()
        captured: dict[str, str] = {}

        def fake_urlopen(request, timeout):
            payload = json.loads(request.data.decode("utf-8"))
            captured["prompt"] = payload["messages"][1]["content"]
            captured["timeout"] = str(timeout)
            return _HTTPResponse({"choices": [{"message": {"content": "OK\n\x00`unsafe`"}}]})

        injection = "hello\n$(touch /tmp/pwned); `whoami`\x00 && still text"
        with TemporaryDirectory() as temp_dir, patch(
            "scripts.auto_reply_ondevice.urllib.request.urlopen", side_effect=fake_urlopen
        ):
            result = probe_ondevice_generation(
                rec,
                state_root=Path(temp_dir),
                prompt=injection,
                timeout=999,
            )
        self.assertTrue(result["ok"])
        self.assertNotIn("\n", captured["prompt"])
        self.assertNotIn("\x00", captured["prompt"])
        self.assertNotIn("$", captured["prompt"])
        self.assertNotIn(";", captured["prompt"])
        self.assertNotIn("`", captured["prompt"])
        self.assertLessEqual(len(captured["prompt"]), 160)
        self.assertLessEqual(float(captured["timeout"]), 45.0)
        self.assertNotIn("`", result["preview"])


if __name__ == "__main__":
    unittest.main()
