import json
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from local_mlx_model_readiness import (  # noqa: E402
    MLX_MODELS_URL,
    RESIDENT_MODEL_ID,
    SWAP_MODEL_ID,
    _RejectRedirects,
    _local_only_urlopen,
    canonical_fixed_local_mlx_model_id,
    read_fixed_local_mlx_readiness,
    resolve_fixed_local_mlx_catalog_model,
)


class _Response:
    def __init__(self, payload):
        self.payload = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit):
        return self.payload[:limit]


class LocalMlxModelReadinessTests(unittest.TestCase):
    def test_default_opener_disables_proxy_and_redirects(self):
        request = urllib.request.Request(MLX_MODELS_URL, method="GET")
        director = mock.Mock()
        response = _Response({"data": []})
        director.open.return_value = response
        with mock.patch(
            "local_mlx_model_readiness.urllib.request.build_opener",
            return_value=director,
        ) as build_opener:
            self.assertIs(_local_only_urlopen(request, timeout=2.0), response)

        handlers = build_opener.call_args.args
        self.assertEqual(len(handlers), 2)
        self.assertIsInstance(handlers[0], urllib.request.ProxyHandler)
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsInstance(handlers[1], _RejectRedirects)
        self.assertIsNone(
            handlers[1].redirect_request(
                request, None, 302, "redirect", {}, "https://example.invalid/"
            )
        )
        director.open.assert_called_once_with(request, timeout=2.0)

    def test_prefix_alias_catalog_acceptance_is_fixed_local_only(self):
        self.assertEqual(
            resolve_fixed_local_mlx_catalog_model(
                RESIDENT_MODEL_ID, [f"mlx/{RESIDENT_MODEL_ID}"]
            ),
            RESIDENT_MODEL_ID,
        )
        self.assertEqual(
            resolve_fixed_local_mlx_catalog_model(
                f"mlx/{RESIDENT_MODEL_ID}", [RESIDENT_MODEL_ID]
            ),
            RESIDENT_MODEL_ID,
        )
        self.assertIsNone(
            resolve_fixed_local_mlx_catalog_model(
                "remote/arbitrary", ["mlx/remote/arbitrary"]
            )
        )
        self.assertIsNone(canonical_fixed_local_mlx_model_id("mlx/remote/arbitrary"))

    def _opener(self, payload, calls):
        def open_request(request, *, timeout):
            calls.append((request.full_url, request.get_method(), timeout))
            return _Response(payload)

        return open_request

    def test_ready_27b_requires_exact_loaded_ready_from_local_get(self):
        calls = []
        result = read_fixed_local_mlx_readiness(
            f"mlx/{SWAP_MODEL_ID}",
            opener=self._opener(
                {"data": [{"id": SWAP_MODEL_ID, "loaded": True, "state": "ready"}]},
                calls,
            ),
        )
        self.assertTrue(result.prepared)
        self.assertEqual(result.reason, "ready")
        self.assertEqual(calls, [(MLX_MODELS_URL, "GET", 2.0)])

    def test_unloaded_27b_fails_closed(self):
        result = read_fixed_local_mlx_readiness(
            SWAP_MODEL_ID,
            opener=self._opener(
                {"data": [{"id": SWAP_MODEL_ID, "loaded": False, "state": "unloaded"}]},
                [],
            ),
        )
        self.assertFalse(result.prepared)
        self.assertEqual(result.reason, "mlx_gateway_not_ready")

    def test_malformed_and_wrong_model_fail_closed(self):
        malformed = read_fixed_local_mlx_readiness(
            SWAP_MODEL_ID, opener=self._opener(b"{not-json", [])
        )
        wrong = read_fixed_local_mlx_readiness(
            SWAP_MODEL_ID,
            opener=self._opener(
                {"data": [{"id": RESIDENT_MODEL_ID, "loaded": True, "state": "ready"}]},
                [],
            ),
        )
        self.assertEqual(malformed.reason, "mlx_gateway_malformed")
        self.assertEqual(wrong.reason, "mlx_gateway_wrong_model")
        self.assertFalse(malformed.prepared)
        self.assertFalse(wrong.prepared)

    def test_decoder_limits_fail_closed_as_malformed(self):
        deeply_nested = (
            b'{"data":' + (b'{"x":' * 1_100) + b"0" + (b"}" * 1_100) + b"}"
        )
        result = read_fixed_local_mlx_readiness(
            SWAP_MODEL_ID,
            opener=self._opener(deeply_nested, []),
        )
        self.assertFalse(result.prepared)
        self.assertEqual(result.reason, "mlx_gateway_malformed")

    def test_timeout_fails_closed_without_body_or_secret(self):
        def timed_out(_request, *, timeout):
            self.assertEqual(timeout, 2.0)
            raise TimeoutError("private-body-must-not-escape")

        result = read_fixed_local_mlx_readiness(SWAP_MODEL_ID, opener=timed_out)
        self.assertFalse(result.prepared)
        self.assertEqual(result.reason, "mlx_gateway_timeout")
        self.assertNotIn("private", repr(result))

    def test_unavailable_and_oversized_payload_fail_closed(self):
        def unavailable(_request, *, timeout):
            self.assertEqual(timeout, 2.0)
            raise urllib.error.URLError("connection refused")

        unavailable_result = read_fixed_local_mlx_readiness(
            SWAP_MODEL_ID, opener=unavailable
        )
        oversized_result = read_fixed_local_mlx_readiness(
            SWAP_MODEL_ID,
            opener=self._opener(b"x" * (256 * 1024 + 1), []),
        )
        self.assertEqual(unavailable_result.reason, "mlx_gateway_unavailable")
        self.assertEqual(
            oversized_result.reason, "mlx_gateway_response_too_large"
        )
        self.assertFalse(unavailable_result.prepared)
        self.assertFalse(oversized_result.prepared)

    def test_flash_cannot_be_promoted_to_prepare_target(self):
        calls = []

        def should_not_open(*_args, **_kwargs):
            calls.append(True)
            raise AssertionError("unexpected gateway call")

        result = read_fixed_local_mlx_readiness(
            f"mlx/{RESIDENT_MODEL_ID}", opener=should_not_open
        )
        self.assertFalse(result.prepared)
        self.assertEqual(result.reason, "model_prepare_not_allowed")
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
