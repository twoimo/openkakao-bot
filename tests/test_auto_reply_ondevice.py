"""Tests for the Apple Silicon MLX Core/Serve recommendation and probe."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from scripts.auto_reply_ondevice import (
    FLASH_NEXT_MODEL_ID,
    FLASH_NEXT_REQUIRED_BYTES,
    ManagedModelResidency,
    MemoryBudget,
    ModelResidencyManager,
    ModelResidencyUncertain,
    MODEL_RESIDENCY_STATE_NAME,
    QWEN38_27B_MODEL_ID,
    QWEN38_27B_REQUIRED_BYTES,
    EngineRecommendation,
    HttpMlxModelGateway,
    HardwareSpec,
    _is_apple_silicon_chip,
    _read_mlx_gateway_models,
    detect_engine_paths,
    detect_hardware,
    detect_mlx_gateway_models,
    download_command,
    generate_command,
    ondevice_summary_dict,
    probe_ondevice_generation,
    read_last_probe,
    read_managed_model_residency,
    recommend_ondevice_setup,
    verify_ondevice_setup,
    write_managed_model_residency,
)


FLASH_NEXT_ADVERTISED_ID = FLASH_NEXT_MODEL_ID.removeprefix("mlx/")
QWEN38_27B_ADVERTISED_ID = QWEN38_27B_MODEL_ID.removeprefix("mlx/")


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


def _prefixless_gateway_models() -> list[dict]:
    return [
        {
            "id": QWEN38_27B_ADVERTISED_ID,
            "owned_by": "mlx-serve",
            "loaded": False,
            "state": "unloaded",
        },
        {
            "id": FLASH_NEXT_ADVERTISED_ID,
            "owned_by": "mlx-serve",
            "loaded": True,
            "state": "ready",
        },
    ]


def _gateway_rec(memory_gb: float = 128.0) -> EngineRecommendation:
    return recommend_ondevice_setup(
        _hw("Apple M5 Max", memory_gb),
        engines={
            "mlx_lm": sys.executable,
            "mlx-serve": sys.executable,
            "mlx-gateway": "http://127.0.0.1:11234/v1",
        },
        gateway_models=_gateway_models(),
    )


class _HTTPResponse:
    def __init__(self, payload: object):
        self._body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self, _limit=None) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class TestHardwareDetection(unittest.TestCase):
    def test_mlx_gateway_rejects_non_fixed_or_remote_endpoints(self):
        for endpoint in (
            "http://127.0.0.1:10100/v1",
            "https://example.invalid/v1",
            "http://127.0.0.1:11234/v1?redirect=1",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaisesRegex(ValueError, "mlx_gateway_endpoint_invalid"):
                    HttpMlxModelGateway(endpoint)

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
            "scripts.auto_reply_ondevice._local_only_urlopen",
            return_value=_HTTPResponse(payload),
        ):
            models = detect_mlx_gateway_models()
        self.assertEqual(models, [{"id": FLASH_NEXT_MODEL_ID, "owned_by": "mlx-serve"}])

    def test_gateway_detection_accepts_prefixless_mlx_serve_id(self):
        payload = {"data": _prefixless_gateway_models()}
        with patch(
            "scripts.auto_reply_ondevice._local_only_urlopen",
            return_value=_HTTPResponse(payload),
        ):
            models = detect_mlx_gateway_models(base_url="http://127.0.0.1:11234/v1")
        self.assertEqual(models[1]["id"], FLASH_NEXT_ADVERTISED_ID)
        self.assertEqual(models[1]["owned_by"], "mlx-serve")

    def test_gateway_detection_fails_closed_for_non_object_json(self):
        with patch(
            "scripts.auto_reply_ondevice._local_only_urlopen",
            return_value=_HTTPResponse([]),
        ):
            result = _read_mlx_gateway_models(base_url="http://127.0.0.1:11234/v1")
        self.assertEqual(result, (False, []))

    def test_engine_paths_prefers_discovered_11234_gateway(self):
        seen_urls: list[str] = []

        def fake_urlopen(request, timeout):
            seen_urls.append(request.full_url)
            return _HTTPResponse({"data": _prefixless_gateway_models()})

        with patch(
            "scripts.auto_reply_ondevice._local_only_urlopen", side_effect=fake_urlopen
        ), patch("scripts.auto_reply_ondevice._find_executable", return_value=""):
            paths = detect_engine_paths()
        self.assertEqual(paths, {"mlx-gateway": "http://127.0.0.1:11234/v1"})
        self.assertEqual(seen_urls, ["http://127.0.0.1:11234/v1/models"])

    def test_gateway_discovery_fails_closed_when_no_candidate_answers(self):
        seen_urls: list[str] = []

        def fake_urlopen(request, timeout):
            seen_urls.append(request.full_url)
            raise OSError("closed")

        with patch(
            "scripts.auto_reply_ondevice._local_only_urlopen", side_effect=fake_urlopen
        ), patch("scripts.auto_reply_ondevice._find_executable", return_value=""):
            paths = detect_engine_paths()
        self.assertEqual(paths, {})
        self.assertEqual(
            seen_urls,
            [
                "http://127.0.0.1:11234/v1/models",
            ],
        )


class TestQwenRecommendation(unittest.TestCase):
    def test_prefixless_ready_flash_is_canonical_recommendation(self):
        rec = recommend_ondevice_setup(
            _hw("Apple M5 Max", 128.0),
            engines={"mlx-gateway": "http://127.0.0.1:11234/v1"},
            gateway_models=_prefixless_gateway_models(),
        )
        self.assertEqual(rec.recommended_model, FLASH_NEXT_ADVERTISED_ID)
        self.assertEqual(rec.worker_model_id, FLASH_NEXT_ADVERTISED_ID)
        self.assertIn(FLASH_NEXT_MODEL_ID, rec.fallback_models)

    def test_large_apple_silicon_keeps_flash_next_resident_default(self):
        rec = _gateway_rec(128.0)
        self.assertEqual(rec.primary_engine, "mlx-serve")
        self.assertEqual(rec.recommended_model, FLASH_NEXT_MODEL_ID)
        self.assertEqual(rec.worker_model_id, FLASH_NEXT_MODEL_ID)
        self.assertIn(FLASH_NEXT_MODEL_ID, rec.fallback_models)
        self.assertNotIn(QWEN38_27B_MODEL_ID, rec.fallback_models)
        self.assertNotIn("gemma", json.dumps(rec.__dict__).casefold())

    def test_smaller_apple_silicon_prefers_flash_next(self):
        rec = _gateway_rec(24.0)
        self.assertEqual(rec.primary_engine, "mlx-serve")
        self.assertEqual(rec.recommended_model, FLASH_NEXT_MODEL_ID)
        self.assertEqual(rec.recommended_quant, "mixed 4/8bit")

    def test_mlx_lm_does_not_promote_local_27b_to_default(self):
        with TemporaryDirectory() as temp_dir:
            model_path = Path(temp_dir) / "Qwen3.8-27B-local"
            model_path.mkdir()
            rec = recommend_ondevice_setup(
                _hw("Apple M5 Max", 64.0),
                engines={"mlx_lm": sys.executable},
                local_models={"Qwen3.8-27B-local": str(model_path)},
            )
        self.assertNotEqual(rec.recommended_model, str(model_path))
        self.assertNotIn("27b", rec.recommended_model.casefold())

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
        self.assertEqual(rec.recommended_model, FLASH_NEXT_MODEL_ID)
        self.assertIn("찾지 못함", rec.reason)


class TestCommandsAndSummary(unittest.TestCase):
    def test_download_command_is_disabled(self):
        self.assertEqual(download_command(_gateway_rec()), "")

    def test_generate_description_uses_mlx_serve(self):
        text = generate_command(_gateway_rec(), "안녕")
        self.assertIn("MLX Serve", text)
        self.assertIn(FLASH_NEXT_MODEL_ID, text)
        self.assertNotIn(QWEN38_27B_MODEL_ID, text)
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
        self.assertIn("Qwen3.8 Flash-Next", summary["status_label"])
        json.dumps(summary, ensure_ascii=False)


class TestModelResidencySwap(unittest.TestCase):
    class FakeGateway:
        def __init__(self):
            self.calls: list[tuple[str, str]] = []
            self.load_failures: set[str] = set()
            self.unload_failures: set[str] = set()
            self.probe_results: dict[str, bool] = {}

        def unload(self, model_id: str) -> None:
            self.calls.append(("unload", model_id))
            if model_id in self.unload_failures:
                raise RuntimeError("unload failed")

        def load(self, model_id: str) -> None:
            self.calls.append(("load", model_id))
            if model_id in self.load_failures:
                raise RuntimeError("load failed")

        def probe(self, model_id: str) -> bool:
            self.calls.append(("probe", model_id))
            return self.probe_results.get(model_id, True)

    def test_27b_is_never_loaded_by_default(self):
        gateway = self.FakeGateway()
        manager = ModelResidencyManager(
            gateway,
            current_model=FLASH_NEXT_MODEL_ID,
            owned_models=[FLASH_NEXT_MODEL_ID],
            memory_budget=lambda: MemoryBudget(120 * 1024**3),
        )
        result = manager.swap(QWEN38_27B_MODEL_ID)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "27b_human_opt_in_required")
        self.assertEqual(gateway.calls, [])

    def test_explicit_swap_drains_then_unloads_loads_and_probes(self):
        gateway = self.FakeGateway()
        manager = ModelResidencyManager(
            gateway,
            current_model=FLASH_NEXT_MODEL_ID,
            owned_models=[FLASH_NEXT_MODEL_ID],
            memory_budget=lambda: MemoryBudget(
                120 * 1024**3,
                kv_cache_bytes=2 * 1024**3,
                voice_models_bytes=4 * 1024**3,
                other_resident_bytes=8 * 1024**3,
            ),
            required_bytes={QWEN38_27B_MODEL_ID: 40 * 1024**3},
        )
        result = manager.swap(QWEN38_27B_MODEL_ID, allow_27b=True)
        self.assertTrue(result.ok)
        self.assertEqual(
            gateway.calls,
            [
                ("unload", FLASH_NEXT_MODEL_ID),
                ("load", QWEN38_27B_MODEL_ID),
                ("probe", QWEN38_27B_MODEL_ID),
            ],
        )
        self.assertEqual(
            result.stages,
            ("drain", "unload", "memory_check", "load", "probe", "ready"),
        )

    def test_request_lease_waits_until_swap_finishes(self):
        class BlockingGateway(self.FakeGateway):
            def __init__(self):
                super().__init__()
                self.unload_started = threading.Event()
                self.release_unload = threading.Event()

            def unload(self, model_id: str) -> None:
                super().unload(model_id)
                if model_id == FLASH_NEXT_MODEL_ID:
                    self.unload_started.set()
                    if not self.release_unload.wait(1.0):
                        raise RuntimeError("test unload release timed out")

        gateway = BlockingGateway()
        manager = ModelResidencyManager(
            gateway,
            current_model=FLASH_NEXT_MODEL_ID,
            owned_models=[FLASH_NEXT_MODEL_ID],
            memory_budget=lambda: MemoryBudget(120 * 1024**3),
        )
        swap_results = []
        lease_attempted = threading.Event()
        lease_entered = threading.Event()

        swap_thread = threading.Thread(
            target=lambda: swap_results.append(
                manager.swap(QWEN38_27B_MODEL_ID, allow_27b=True)
            ),
            daemon=True,
        )
        swap_thread.start()
        self.assertTrue(gateway.unload_started.wait(1.0))

        def take_lease() -> None:
            lease_attempted.set()
            with manager.request_lease():
                lease_entered.set()

        lease_thread = threading.Thread(target=take_lease, daemon=True)
        lease_thread.start()
        self.assertTrue(lease_attempted.wait(1.0))
        self.assertFalse(lease_entered.wait(0.05))

        gateway.release_unload.set()
        swap_thread.join(1.0)
        lease_thread.join(1.0)
        self.assertFalse(swap_thread.is_alive())
        self.assertFalse(lease_thread.is_alive())
        self.assertEqual(len(swap_results), 1)
        self.assertTrue(swap_results[0].ok)
        self.assertTrue(lease_entered.is_set())

    def test_foreign_resident_model_is_never_unloaded(self):
        gateway = self.FakeGateway()
        manager = ModelResidencyManager(
            gateway,
            current_model=FLASH_NEXT_MODEL_ID,
            owned_models=[],
            memory_budget=lambda: MemoryBudget(120 * 1024**3),
        )
        result = manager.swap(QWEN38_27B_MODEL_ID, allow_27b=True)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "model_owner_unknown")
        self.assertEqual(gateway.calls, [])

    def test_memory_budget_reserves_kv_voice_and_other_residents(self):
        gateway = self.FakeGateway()
        manager = ModelResidencyManager(
            gateway,
            current_model=FLASH_NEXT_MODEL_ID,
            owned_models=[FLASH_NEXT_MODEL_ID],
            memory_budget=MagicMock(side_effect=[
                MemoryBudget(50 * 1024**3, 8 * 1024**3, 8 * 1024**3, 8 * 1024**3),
                MemoryBudget(120 * 1024**3),
            ]),
            required_bytes={QWEN38_27B_MODEL_ID: 30 * 1024**3},
        )
        result = manager.swap(QWEN38_27B_MODEL_ID, allow_27b=True)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "insufficient_free_memory")
        self.assertEqual(
            gateway.calls,
            [
                ("unload", FLASH_NEXT_MODEL_ID),
                ("load", FLASH_NEXT_MODEL_ID),
                ("probe", FLASH_NEXT_MODEL_ID),
            ],
        )

    def test_cancellation_after_target_load_unloads_target_and_rolls_back(self):
        cancelled = threading.Event()

        class CancellingGateway(self.FakeGateway):
            def load(self, model_id: str) -> None:
                super().load(model_id)
                if model_id == QWEN38_27B_MODEL_ID:
                    cancelled.set()

        gateway = CancellingGateway()
        manager = ModelResidencyManager(
            gateway,
            current_model=FLASH_NEXT_MODEL_ID,
            owned_models=[FLASH_NEXT_MODEL_ID],
            memory_budget=lambda: MemoryBudget(120 * 1024**3),
            cancel_check=cancelled.is_set,
        )

        result = manager.swap(QWEN38_27B_MODEL_ID, allow_27b=True)

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "cancelled")
        self.assertEqual(manager.current_model, FLASH_NEXT_MODEL_ID)
        self.assertEqual(
            gateway.calls,
            [
                ("unload", FLASH_NEXT_MODEL_ID),
                ("load", QWEN38_27B_MODEL_ID),
                ("unload", QWEN38_27B_MODEL_ID),
                ("load", FLASH_NEXT_MODEL_ID),
                ("probe", FLASH_NEXT_MODEL_ID),
            ],
        )

    def test_cancellation_during_target_probe_rolls_back(self):
        cancelled = threading.Event()

        class CancellingProbeGateway(self.FakeGateway):
            def probe(self, model_id: str) -> bool:
                result = super().probe(model_id)
                if model_id == QWEN38_27B_MODEL_ID:
                    cancelled.set()
                return result

        gateway = CancellingProbeGateway()
        manager = ModelResidencyManager(
            gateway,
            current_model=FLASH_NEXT_MODEL_ID,
            owned_models=[FLASH_NEXT_MODEL_ID],
            memory_budget=lambda: MemoryBudget(120 * 1024**3),
            cancel_check=cancelled.is_set,
        )

        result = manager.swap(QWEN38_27B_MODEL_ID, allow_27b=True)

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "cancelled")
        self.assertEqual(manager.current_model, FLASH_NEXT_MODEL_ID)
        self.assertIn(("unload", QWEN38_27B_MODEL_ID), gateway.calls)

    def test_load_failure_with_failed_restore_is_fail_closed(self):
        gateway = self.FakeGateway()
        gateway.load_failures.update({QWEN38_27B_MODEL_ID, FLASH_NEXT_MODEL_ID})
        manager = ModelResidencyManager(
            gateway,
            current_model=FLASH_NEXT_MODEL_ID,
            owned_models=[FLASH_NEXT_MODEL_ID],
            memory_budget=lambda: MemoryBudget(120 * 1024**3),
        )

        result = manager.swap(QWEN38_27B_MODEL_ID, allow_27b=True)

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "load_failed_rollback_failed")
        self.assertIsNone(manager.current_model)

    def test_probe_failure_with_failed_restore_is_fail_closed(self):
        gateway = self.FakeGateway()
        gateway.probe_results[QWEN38_27B_MODEL_ID] = False
        gateway.load_failures.add(FLASH_NEXT_MODEL_ID)
        manager = ModelResidencyManager(
            gateway,
            current_model=FLASH_NEXT_MODEL_ID,
            owned_models=[FLASH_NEXT_MODEL_ID],
            memory_budget=lambda: MemoryBudget(120 * 1024**3),
        )

        result = manager.swap(QWEN38_27B_MODEL_ID, allow_27b=True)

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "probe_failed_rollback_failed")
        self.assertIsNone(manager.current_model)

    def test_memory_check_failure_after_unload_restores_previous_model(self):
        gateway = self.FakeGateway()
        manager = ModelResidencyManager(
            gateway,
            current_model=FLASH_NEXT_MODEL_ID,
            owned_models=[FLASH_NEXT_MODEL_ID],
            memory_budget=MagicMock(side_effect=[RuntimeError("memory unavailable"), MemoryBudget(120 * 1024**3)]),
        )

        result = manager.swap(QWEN38_27B_MODEL_ID, allow_27b=True)

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "memory_budget_unavailable")
        self.assertEqual(manager.current_model, FLASH_NEXT_MODEL_ID)
        self.assertEqual(manager.owned_models, {FLASH_NEXT_MODEL_ID})
        self.assertEqual(
            gateway.calls,
            [
                ("unload", FLASH_NEXT_MODEL_ID),
                ("load", FLASH_NEXT_MODEL_ID),
                ("probe", FLASH_NEXT_MODEL_ID),
            ],
        )
        self.assertEqual(result.stages, ("drain", "unload", "memory_check", "rollback"))

    def test_target_load_failure_restores_previous_model(self):
        gateway = self.FakeGateway()
        gateway.load_failures.add(QWEN38_27B_MODEL_ID)
        manager = ModelResidencyManager(
            gateway,
            current_model=FLASH_NEXT_MODEL_ID,
            owned_models=[FLASH_NEXT_MODEL_ID],
            memory_budget=lambda: MemoryBudget(120 * 1024**3),
        )

        result = manager.swap(QWEN38_27B_MODEL_ID, allow_27b=True)

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "load_failed")
        self.assertEqual(manager.current_model, FLASH_NEXT_MODEL_ID)
        self.assertEqual(manager.owned_models, {FLASH_NEXT_MODEL_ID})
        self.assertEqual(
            gateway.calls,
            [
                ("unload", FLASH_NEXT_MODEL_ID),
                ("load", QWEN38_27B_MODEL_ID),
                ("load", FLASH_NEXT_MODEL_ID),
                ("probe", FLASH_NEXT_MODEL_ID),
            ],
        )

    def test_target_probe_failure_unloads_target_and_restores_previous_model(self):
        gateway = self.FakeGateway()
        gateway.probe_results[QWEN38_27B_MODEL_ID] = False
        manager = ModelResidencyManager(
            gateway,
            current_model=FLASH_NEXT_MODEL_ID,
            owned_models=[FLASH_NEXT_MODEL_ID],
            memory_budget=lambda: MemoryBudget(120 * 1024**3),
        )

        result = manager.swap(QWEN38_27B_MODEL_ID, allow_27b=True)

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "probe_failed")
        self.assertEqual(manager.current_model, FLASH_NEXT_MODEL_ID)
        self.assertEqual(manager.owned_models, {FLASH_NEXT_MODEL_ID})
        self.assertEqual(
            gateway.calls,
            [
                ("unload", FLASH_NEXT_MODEL_ID),
                ("load", QWEN38_27B_MODEL_ID),
                ("probe", QWEN38_27B_MODEL_ID),
                ("unload", QWEN38_27B_MODEL_ID),
                ("load", FLASH_NEXT_MODEL_ID),
                ("probe", FLASH_NEXT_MODEL_ID),
            ],
        )

    def test_target_unload_failure_does_not_restore_previous_model(self):
        gateway = self.FakeGateway()
        gateway.probe_results[QWEN38_27B_MODEL_ID] = False
        gateway.unload_failures.add(QWEN38_27B_MODEL_ID)
        manager = ModelResidencyManager(
            gateway,
            current_model=FLASH_NEXT_MODEL_ID,
            owned_models=[FLASH_NEXT_MODEL_ID],
            memory_budget=lambda: MemoryBudget(120 * 1024**3),
        )

        result = manager.swap(QWEN38_27B_MODEL_ID, allow_27b=True)

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "probe_failed_rollback_failed")
        self.assertIsNone(manager.current_model)
        self.assertEqual(manager.owned_models, {QWEN38_27B_MODEL_ID})
        self.assertEqual(
            gateway.calls,
            [
                ("unload", FLASH_NEXT_MODEL_ID),
                ("load", QWEN38_27B_MODEL_ID),
                ("probe", QWEN38_27B_MODEL_ID),
                ("unload", QWEN38_27B_MODEL_ID),
            ],
        )
        self.assertEqual(
            result.stages,
            ("drain", "unload", "memory_check", "load", "probe"),
        )


class TestHttpSwapContract(unittest.TestCase):
    """Exercise the real HTTP adapter and manager with an in-memory transport."""

    def setUp(self):
        transport = patch("scripts.auto_reply_ondevice._local_only_urlopen")
        self.http = transport.start()
        self.addCleanup(transport.stop)
        self.http.side_effect = AssertionError("test must supply every HTTP response")
        self.gateway = HttpMlxModelGateway()

    @staticmethod
    def catalog(loaded, state):
        return {"data": [{"id": QWEN38_27B_ADVERTISED_ID,
                          "owned_by": "mlx-serve", "loaded": loaded, "state": state}]}

    def test_control_requires_exact_postcondition_and_unique_catalog_identity(self):
        for action, loaded, state in (("load", True, "ready"), ("unload", False, "unloaded")):
            with self.subTest(action=action, valid=True):
                self.http.reset_mock()
                self.http.side_effect = [_HTTPResponse({}), _HTTPResponse(self.catalog(loaded, state))]
                getattr(self.gateway, action)(QWEN38_27B_MODEL_ID)
                requests = [call.args[0] for call in self.http.call_args_list]
                self.assertEqual([req.get_method() for req in requests], ["POST", "GET"])
                self.assertTrue(requests[0].full_url.endswith(f"Qwen3.8-27B-MLX-Serve-4bit/{action}"))
                self.assertEqual(requests[1].full_url, "http://127.0.0.1:11234/v1/models")
            duplicate = self.catalog(loaded, state)
            duplicate["data"].append({**duplicate["data"][0], "id": QWEN38_27B_MODEL_ID})
            for catalog in (
                self.catalog(not loaded, state), self.catalog(loaded, "loading"),
                self.catalog(int(loaded), state), self.catalog(loaded, state.upper()),
                {"data": []}, {"data": [None]}, {"data": {}}, duplicate,
            ):
                with self.subTest(action=action, catalog=catalog):
                    self.http.reset_mock()
                    self.http.side_effect = [_HTTPResponse({}), _HTTPResponse(catalog)]
                    with self.assertRaises(ModelResidencyUncertain):
                        getattr(self.gateway, action)(QWEN38_27B_MODEL_ID)
                    self.assertEqual(self.http.call_count, 2)

    def test_uncertain_control_or_generation_never_triggers_compensating_load(self):
        for failure in ("unload", "load", "probe"):
            with self.subTest(failure=failure):
                self.http.reset_mock()
                resident = {FLASH_NEXT_ADVERTISED_ID: True, QWEN38_27B_ADVERTISED_ID: False}
                actions = []

                def transport(request, timeout):
                    if request.get_method() == "GET":
                        return _HTTPResponse({"data": [
                            {"id": model, "owned_by": "mlx-serve", "loaded": loaded,
                             "state": "ready" if loaded else "unloaded"}
                            for model, loaded in resident.items()
                        ]})
                    action = request.full_url.rsplit("/", 1)[-1]
                    action = "probe" if action == "completions" else action
                    actions.append(action)
                    if action == failure:
                        # The server may have committed before its response was lost.
                        raise TimeoutError("response lost")
                    model = FLASH_NEXT_ADVERTISED_ID if action == "unload" else QWEN38_27B_ADVERTISED_ID
                    resident[model] = action == "load"
                    return _HTTPResponse({})

                self.http.side_effect = transport
                manager = ModelResidencyManager(
                    self.gateway, owned_models=[FLASH_NEXT_MODEL_ID],
                    memory_budget=lambda: MemoryBudget(120 * 1024**3),
                )
                commit = MagicMock()
                result = manager.swap(QWEN38_27B_MODEL_ID, allow_27b=True, commit=commit)
                self.assertEqual(result.reason, "model_residency_uncertain")
                self.assertFalse(result.ok)
                self.assertIsNone(manager.current_model)
                self.assertEqual(actions, ["unload", "load", "probe"][:["unload", "load", "probe"].index(failure) + 1])
                commit.assert_not_called()

    def test_probe_requires_ready_before_generation_and_exact_text_response_model(self):
        self.http.side_effect = [_HTTPResponse(self.catalog(False, "unloaded"))]
        self.assertFalse(self.gateway.probe(QWEN38_27B_MODEL_ID))
        self.assertEqual(self.http.call_count, 1)
        for model, content, expected in (
            (FLASH_NEXT_ADVERTISED_ID, "LOCAL_OK", False),
            (None, "LOCAL_OK", False),
            (QWEN38_27B_ADVERTISED_ID, "   ", False),
            (QWEN38_27B_ADVERTISED_ID, {"text": "LOCAL_OK"}, False),
            (QWEN38_27B_ADVERTISED_ID, "LOCAL_OK", True),
        ):
            with self.subTest(model=model, content=content):
                self.http.reset_mock()
                ready = _HTTPResponse(self.catalog(True, "ready"))
                self.http.side_effect = [ready, _HTTPResponse({
                    "model": model, "choices": [{"message": {"content": content}}],
                }), ready]
                self.assertEqual(self.gateway.probe(QWEN38_27B_MODEL_ID), expected)
                body = json.loads(self.http.call_args_list[1].args[0].data)
                self.assertEqual(body["model"], QWEN38_27B_ADVERTISED_ID)
                self.assertIs(body["stream"], False)
                self.assertEqual(self.http.call_count, 3 if expected else 2)

    def test_probe_does_not_accept_generation_after_residency_changed(self):
        self.http.side_effect = [
            _HTTPResponse(self.catalog(True, "ready")),
            _HTTPResponse({"model": QWEN38_27B_ADVERTISED_ID,
                           "choices": [{"message": {"content": "LOCAL_OK"}}]}),
            _HTTPResponse(self.catalog(False, "unloaded")),
        ]
        self.assertFalse(self.gateway.probe(QWEN38_27B_MODEL_ID))
        self.assertEqual(self.http.call_count, 3)


class TestManagedResidencyAdmission(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        write_managed_model_residency(
            self.root, ManagedModelResidency(FLASH_NEXT_MODEL_ID, (FLASH_NEXT_MODEL_ID,), 4242, True, True, "ready"),
            accepting_requests=False, in_flight=0, updated_at=100.0,
        )

    def read(self, extra=()):
        return read_managed_model_residency(
            self.root, now=100.0, process_probe=lambda pid: pid == 4242,
            gateway_reader=lambda: (True, _prefixless_gateway_models() + list(extra)),
        )

    def test_unknown_loaded_models_must_have_measured_resident_memory(self):
        for amount in (None, 0, -1, True, "123", 1.5):
            with self.subTest(amount=amount):
                result = self.read([{"id": "other/image", "loaded": True, "state": "ready", "bytes_resident": amount}])
                self.assertFalse(result.owner_verified)
                self.assertEqual(result.reason, "model_residency_mismatch")
        known_bytes = self.read([
            {"id": "other/image", "loaded": True, "state": "ready", "bytes_resident": 16 * 1024**3},
            {"id": "other/voice", "loaded": True, "state": "ready", "bytes_resident": 4 * 1024**3},
            {"id": "other/unloaded", "loaded": False, "state": "unloaded"},
        ])
        self.assertTrue(known_bytes.owner_verified)
        self.assertEqual(known_bytes.other_resident_bytes, 20 * 1024**3)
        self.assertEqual(known_bytes.owned_models, (FLASH_NEXT_MODEL_ID,))

    def test_catalog_mismatch_never_attests_owner_or_drain(self):
        for extra in (
            [{"id": FLASH_NEXT_MODEL_ID, "loaded": True, "state": "ready"}],
            [{"id": "other/image", "loaded": False, "state": "ready"}],
            [{"id": "other/image", "loaded": True, "state": "loading", "bytes_resident": 123}],
        ):
            with self.subTest(extra=extra):
                result = self.read(extra)
                self.assertFalse(result.owner_verified)
                self.assertFalse(result.drain_verified)

    def test_nonfinite_or_stale_owner_evidence_is_rejected_before_gateway(self):
        path = self.root / MODEL_RESIDENCY_STATE_NAME
        original = json.loads(path.read_text())
        for stamp in (float("nan"), float("inf"), 0.0, 106.0):
            with self.subTest(stamp=stamp):
                path.write_text(json.dumps({**original, "updated_at": stamp}))
                reader = MagicMock(side_effect=AssertionError("stale owner cannot reach gateway"))
                result = read_managed_model_residency(self.root, now=100.0, gateway_reader=reader)
                self.assertFalse(result.owner_verified)
                reader.assert_not_called()

    def test_default_admission_and_rollback_cannot_be_disabled_by_small_override(self):
        gateway = TestModelResidencySwap.FakeGateway()
        manager = ModelResidencyManager(
            gateway, owned_models=[FLASH_NEXT_MODEL_ID],
            required_bytes={QWEN38_27B_MODEL_ID: 1},
            memory_budget=lambda: MemoryBudget(QWEN38_27B_REQUIRED_BYTES - 1),
        )
        result = manager.swap(QWEN38_27B_MODEL_ID, allow_27b=True)
        self.assertEqual(result.reason, "insufficient_free_memory_rollback_failed")
        self.assertEqual(gateway.calls, [("unload", FLASH_NEXT_MODEL_ID)])
        self.assertIsNone(manager.current_model)

    def test_rollback_rechecks_memory_before_restoring_the_larger_model(self):
        for available in (FLASH_NEXT_REQUIRED_BYTES - 1, FLASH_NEXT_REQUIRED_BYTES):
            with self.subTest(available=available):
                gateway = TestModelResidencySwap.FakeGateway()
                gateway.probe_results[QWEN38_27B_MODEL_ID] = False
                manager = ModelResidencyManager(
                    gateway, owned_models=[FLASH_NEXT_MODEL_ID],
                    memory_budget=MagicMock(side_effect=[MemoryBudget(120 * 1024**3), MemoryBudget(available)]),
                )
                result = manager.swap(QWEN38_27B_MODEL_ID, allow_27b=True)
                admitted = available == FLASH_NEXT_REQUIRED_BYTES
                self.assertEqual(("load", FLASH_NEXT_MODEL_ID) in gateway.calls, admitted)
                self.assertEqual(result.reason, "probe_failed" if admitted else "probe_failed_rollback_failed")


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
        rec = recommend_ondevice_setup(
            _hw("Apple M5 Max", 128.0),
            engines={"mlx-gateway": "http://127.0.0.1:11234/v1"},
            gateway_models=_prefixless_gateway_models(),
        )
        captured: dict[str, str] = {}

        def fake_urlopen(request, timeout):
            if request.full_url.endswith("/models"):
                return _HTTPResponse({"data": _prefixless_gateway_models()})
            payload = json.loads(request.data.decode("utf-8"))
            captured["model"] = payload["model"]
            captured["url"] = request.full_url
            captured["max_tokens"] = str(payload["max_tokens"])
            return _HTTPResponse(
                {"choices": [{"message": {"content": "LOCAL_OK from local MLX"}}]}
            )

        with TemporaryDirectory() as temp_dir, patch(
            "scripts.auto_reply_ondevice._local_only_urlopen", side_effect=fake_urlopen
        ) as urlopen:
            result = probe_ondevice_generation(rec, state_root=Path(temp_dir), timeout=2)
            persisted = read_last_probe(Path(temp_dir))
        self.assertTrue(result["ok"])
        self.assertEqual(result["engine"], "mlx-serve-gateway")
        self.assertEqual(result["model"], FLASH_NEXT_ADVERTISED_ID)
        self.assertEqual(persisted["model"], FLASH_NEXT_ADVERTISED_ID)
        self.assertEqual(captured["model"], FLASH_NEXT_ADVERTISED_ID)
        self.assertNotEqual(captured["model"], FLASH_NEXT_MODEL_ID)
        self.assertEqual(captured["url"], "http://127.0.0.1:11234/v1/chat/completions")
        self.assertLessEqual(int(captured["max_tokens"]), 16)
        self.assertLessEqual(len(persisted["preview"]), 240)
        self.assertNotIn("errors", persisted)
        self.assertEqual(urlopen.call_count, 2)

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
            if request.full_url.endswith("/models"):
                return _HTTPResponse({"data": _gateway_models()})
            payload = json.loads(request.data.decode("utf-8"))
            captured["prompt"] = payload["messages"][1]["content"]
            captured["timeout"] = str(timeout)
            return _HTTPResponse({"choices": [{"message": {"content": "OK\n\x00`unsafe`"}}]})

        injection = "hello\n$(touch /tmp/pwned); `whoami`\x00 && still text"
        with TemporaryDirectory() as temp_dir, patch(
            "scripts.auto_reply_ondevice._local_only_urlopen", side_effect=fake_urlopen
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
        self.assertLessEqual(float(captured["timeout"]), 90.0)
        self.assertNotIn("`", result["preview"])


if __name__ == "__main__":
    unittest.main()
