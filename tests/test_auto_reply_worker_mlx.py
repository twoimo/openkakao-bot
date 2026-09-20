import importlib.util
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
WORKER = SCRIPTS / "auto-reply-worker.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


class _Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class AutoReplyWorkerMlxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("auto_reply_worker_mlx", WORKER)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        cls.module = module

    @staticmethod
    def _advertised_models():
        return {
            "data": [
                {
                    "id": "mlx/ddalcu/Qwen3.8-27B-MLX-Serve-4bit",
                    "owned_by": "mlx-serve",
                    "loaded": False,
                    "state": "unloaded",
                },
                {
                    "id": "ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit",
                    "owned_by": "mlx-serve",
                    "loaded": True,
                    "state": "ready",
                },
            ]
        }

    def test_mlx_generation_uses_discovered_gateway_and_advertised_id(self):
        module = self.module
        for selected_model in (
            "mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit",
            "ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit",
        ):
            with self.subTest(selected_model=selected_model):
                calls = []
                payloads = []

                def fake_urlopen(request, timeout=None):
                    calls.append((request.full_url, request.data, timeout))
                    if request.full_url == "http://127.0.0.1:11234/v1/models":
                        return _Response(self._advertised_models())
                    if request.full_url == "http://127.0.0.1:11234/v1/chat/completions":
                        payloads.append(json.loads(request.data.decode("utf-8")))
                        return _Response({"choices": [{"message": {"content": "ok"}}]})
                    raise AssertionError(f"unexpected URL: {request.full_url}")

                with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
                    code, stdout, stderr = module._run_opencodex_generation(
                        selected_model,
                        "system",
                        b'{"inbound":"hello"}',
                        timeout=90.0,
                    )

                self.assertEqual((code, stdout, stderr), (0, b"ok", b""))
                self.assertEqual(
                    payloads[0]["model"],
                    "ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit",
                )
                self.assertEqual(calls[-1][0], "http://127.0.0.1:11234/v1/chat/completions")
                self.assertTrue(all("10100" not in url for url, _data, _timeout in calls))
                self.assertTrue(all("/load" not in url for url, _data, _timeout in calls))

    def test_missing_gateway_fails_closed_in_candidate_order(self):
        module = self.module
        calls = []

        def fake_urlopen(request, timeout=None):
            calls.append((request.full_url, request.data, timeout))
            raise urllib.error.URLError("offline")

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            code, stdout, stderr = module._run_opencodex_generation(
                "mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit",
                "system",
                b'{"inbound":"hello"}',
                timeout=90.0,
            )

        self.assertEqual(code, 1)
        self.assertEqual(stdout, b"")
        self.assertEqual(stderr, b"mlx_serve_gateway_unavailable")
        self.assertEqual(
            [url for url, _data, _timeout in calls],
            [
                "http://127.0.0.1:11234/v1/models",
                "http://127.0.0.1:10100/v1/models",
            ],
        )
        self.assertTrue(all(data is None for _url, data, _timeout in calls))

    def test_attested_local_flash_profile_never_falls_back_to_10100(self):
        module = self.module
        for selected_model in (
            "mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit",
            "ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit",
        ):
            with self.subTest(selected_model=selected_model):
                calls = []

                def fake_urlopen(request, timeout=None):
                    calls.append((request.full_url, request.data, timeout))
                    raise urllib.error.URLError("offline")

                with (
                    mock.patch.object(module, "REPLY_RUNNER_KIND", "opencodex"),
                    mock.patch.object(module, "MODEL_PRIVACY_MODE", "local"),
                    mock.patch.object(module, "MODEL_ALLOW_EGRESS", "0"),
                    mock.patch.object(module, "MODEL_PROVIDER", "mlx-serve"),
                    mock.patch("urllib.request.urlopen", side_effect=fake_urlopen),
                ):
                    code, stdout, stderr = module._run_opencodex_generation(
                        selected_model,
                        "system",
                        b'{"inbound":"hello"}',
                        timeout=90.0,
                    )

                self.assertEqual(code, 1)
                self.assertEqual(stdout, b"")
                self.assertEqual(stderr, b"mlx_serve_gateway_unavailable")
                self.assertEqual(
                    [url for url, _data, _timeout in calls],
                    ["http://127.0.0.1:11234/v1/models"],
                )
                self.assertTrue(all(data is None for _url, data, _timeout in calls))

    def test_generation_candidate_blocks_product_cloud_fallback(self):
        module = self.module
        command = ["gjc", "--model", "primary/model"]
        mlx_model = "ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit"

        with (
            mock.patch.object(module, "REPLY_RUNNER_KIND", "gjc"),
            mock.patch.object(
                module,
                "_run_opencodex_generation",
                return_value=(0, b"mlx", b""),
            ) as http_runner,
            mock.patch.object(
                module,
                "_run_bounded_process",
                return_value=(0, b"gjc", b""),
            ) as process_runner,
        ):
            result = module._run_generation_candidate(
                mlx_model,
                "system",
                b"{}",
                command=command,
                env={},
                model_stdin_bytes=b"{}",
                image_paths=None,
                timeout=90.0,
            )
            self.assertEqual(result, (0, b"mlx", b""))
            http_runner.assert_called_once()
            process_runner.assert_not_called()

            http_runner.reset_mock()
            process_runner.reset_mock()
            result = module._run_generation_candidate(
                "google-antigravity/gemini-3.8-flash",
                "system",
                b"{}",
                command=command,
                env={},
                model_stdin_bytes=b"{}",
                image_paths=None,
                timeout=45.0,
            )
            self.assertEqual(result, (1, b"", b"product_cloud_fallback_disabled"))
            http_runner.assert_not_called()
            process_runner.assert_not_called()

    def test_prefixless_mlx_keeps_local_generation_timeout(self):
        module = self.module
        prefixless = "ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit"
        prefixed = "mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit"
        self.assertEqual(module._model_generation_timeout(prefixless), 90.0)
        self.assertEqual(module._model_generation_timeout(prefixed), 90.0)
        self.assertEqual(
            module._model_generation_timeout("google-antigravity/gemini-3.8-flash"),
            45.0,
        )


if __name__ == "__main__":
    unittest.main()
