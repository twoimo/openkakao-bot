import io
import json
import os
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

from verify_local_models import (  # noqa: E402
    CHAT_COMPLETIONS_URL,
    DIAGNOSTIC_MAX_TOKENS,
    DIAGNOSTIC_PROMPT,
    FIXED_MODEL_IDS,
    LOCAL_BASE_URL,
    MAX_MODEL_ROWS,
    MAX_RESPONSE_BYTES,
    MODELS_URL,
    RESIDENT_MODEL_ID,
    SWAP_MODEL_ID,
    _RejectRedirects,
    _TransportPolicyError,
    _local_only_urlopen,
    main,
    verify_local_model,
    verify_local_models,
)


class _Response:
    def __init__(self, payload, *, status=200, url=None):
        self.payload = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.status = status
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return self.url

    def read(self, limit):
        return self.payload[:limit]


class _FakeOpener:
    def __init__(self, catalog, completion=None):
        self.catalog = catalog
        self.completion = completion or {
            "choices": [{"message": {"content": "확인"}}]
        }
        self.calls = []

    def __call__(self, request, *, timeout):
        self.calls.append((request, timeout))
        if request.full_url == MODELS_URL and request.get_method() == "GET":
            return _Response(self.catalog, url=MODELS_URL)
        if (
            request.full_url == CHAT_COMPLETIONS_URL
            and request.get_method() == "POST"
        ):
            return _Response(self.completion, url=CHAT_COMPLETIONS_URL)
        raise AssertionError(f"unexpected request: {request.get_method()} {request.full_url}")


class VerifyLocalModelsTests(unittest.TestCase):
    @staticmethod
    def _ready_catalog():
        return {
            "data": [
                {
                    "id": RESIDENT_MODEL_ID,
                    "loaded": True,
                    "state": "ready",
                },
                {
                    "id": f"mlx/{SWAP_MODEL_ID}",
                    "loaded": True,
                    "state": "ready",
                },
            ]
        }

    def test_both_fixed_models_succeed_with_fake_opener(self):
        opener = _FakeOpener(self._ready_catalog())

        results = verify_local_models(FIXED_MODEL_IDS, opener=opener, timeout=5.0)

        self.assertEqual([result.model for result in results], list(FIXED_MODEL_IDS))
        self.assertTrue(all(result.ok for result in results))
        self.assertEqual(
            [(request.full_url, request.get_method()) for request, _ in opener.calls],
            [
                (MODELS_URL, "GET"),
                (CHAT_COMPLETIONS_URL, "POST"),
                (MODELS_URL, "GET"),
                (CHAT_COMPLETIONS_URL, "POST"),
            ],
        )
        bodies = [
            json.loads(request.data)
            for request, _timeout in opener.calls
            if request.get_method() == "POST"
        ]
        self.assertEqual([body["model"] for body in bodies], list(FIXED_MODEL_IDS))
        self.assertTrue(
            all(
                body["messages"]
                == [{"role": "user", "content": DIAGNOSTIC_PROMPT}]
                for body in bodies
            )
        )
        self.assertTrue(
            all(body["max_tokens"] == DIAGNOSTIC_MAX_TOKENS <= 8 for body in bodies)
        )
        self.assertTrue(all(body["stream"] is False for body in bodies))
        self.assertTrue(all(0 < timeout <= 5.0 for _request, timeout in opener.calls))

    def test_optional_mlx_prefix_is_accepted_but_unknown_model_is_not_opened(self):
        opener = _FakeOpener(self._ready_catalog())
        prefixed = verify_local_model(f"mlx/{RESIDENT_MODEL_ID}", opener=opener)
        invalid = verify_local_model("mlx/remote/secret-model", opener=opener)

        self.assertTrue(prefixed.ok)
        self.assertEqual(prefixed.model, RESIDENT_MODEL_ID)
        self.assertEqual(invalid.as_dict()["model"], "invalid")
        self.assertEqual(invalid.reason, "invalid_model_id")
        self.assertEqual(len(opener.calls), 2)

    def test_not_ready_stops_before_generation(self):
        opener = _FakeOpener(
            {
                "data": [
                    {
                        "id": RESIDENT_MODEL_ID,
                        "loaded": False,
                        "state": "unloaded",
                    }
                ]
            }
        )

        result = verify_local_model(RESIDENT_MODEL_ID, opener=opener)

        self.assertFalse(result.readiness)
        self.assertFalse(result.generation)
        self.assertEqual(result.reason, "model_not_ready")
        self.assertEqual(len(opener.calls), 1)

    def test_malformed_models_and_generation_responses_fail_closed(self):
        malformed_models = verify_local_model(
            RESIDENT_MODEL_ID,
            opener=lambda request, *, timeout: _Response(
                b"{not-json", url=request.full_url
            ),
        )
        missing_content = verify_local_model(
            RESIDENT_MODEL_ID,
            opener=_FakeOpener(self._ready_catalog(), {"choices": [{}]}),
        )

        self.assertEqual(malformed_models.reason, "models_malformed_json")
        self.assertFalse(malformed_models.readiness)
        self.assertEqual(missing_content.reason, "generation_missing_content")
        self.assertTrue(missing_content.readiness)
        self.assertFalse(missing_content.generation)

    def test_oversized_json_and_row_count_are_bounded(self):
        oversized = verify_local_model(
            RESIDENT_MODEL_ID,
            opener=lambda request, *, timeout: _Response(
                b"x" * (MAX_RESPONSE_BYTES + 1), url=request.full_url
            ),
        )
        too_many_rows = verify_local_model(
            RESIDENT_MODEL_ID,
            opener=_FakeOpener({"data": [{}] * (MAX_MODEL_ROWS + 1)}),
        )

        self.assertEqual(oversized.reason, "models_response_too_large")
        self.assertEqual(too_many_rows.reason, "models_too_many_rows")

    def test_timeout_and_http_error_are_fixed_codes_without_exception_text(self):
        def timed_out(_request, *, timeout):
            self.assertGreater(timeout, 0)
            raise TimeoutError("PRIVATE_TIMEOUT_DETAIL")

        def http_error(request, *, timeout):
            del timeout
            raise urllib.error.HTTPError(
                request.full_url,
                503,
                "PRIVATE_HTTP_DETAIL",
                {},
                io.BytesIO(),
            )

        timeout_result = verify_local_model(RESIDENT_MODEL_ID, opener=timed_out)
        http_result = verify_local_model(RESIDENT_MODEL_ID, opener=http_error)

        self.assertEqual(timeout_result.reason, "models_timeout")
        self.assertEqual(http_result.reason, "models_http_error")
        rendered = json.dumps([timeout_result.as_dict(), http_result.as_dict()])
        self.assertNotIn("PRIVATE", rendered)

    def test_default_opener_disables_environment_proxies_and_rejects_redirects(self):
        request = urllib.request.Request(MODELS_URL, method="GET")
        director = mock.Mock()
        response = _Response({"data": []}, url=MODELS_URL)
        director.open.return_value = response

        with (
            mock.patch.dict(
                os.environ,
                {
                    "HTTP_PROXY": "http://proxy.example.invalid:9999",
                    "HTTPS_PROXY": "http://proxy.example.invalid:9999",
                },
            ),
            mock.patch(
                "verify_local_models.urllib.request.build_opener",
                return_value=director,
            ) as build_opener,
        ):
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

    def test_proxy_marked_request_and_redirect_response_fail_closed(self):
        proxied = urllib.request.Request(MODELS_URL, method="GET")
        proxied.set_proxy("proxy.example.invalid:9999", "http")
        with self.assertRaisesRegex(_TransportPolicyError, "proxy_forbidden"):
            _local_only_urlopen(proxied, timeout=1.0)

        def redirected(request, *, timeout):
            del timeout
            return _Response(
                self._ready_catalog(),
                status=302,
                url="https://model-hub.example.invalid/models",
            )

        result = verify_local_model(RESIDENT_MODEL_ID, opener=redirected)
        self.assertEqual(result.reason, "models_redirect_rejected")
        self.assertFalse(result.readiness)

    def test_non_local_endpoint_override_and_too_many_models_never_open(self):
        calls = []

        def should_not_open(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("network must not be called")

        non_local = verify_local_model(
            RESIDENT_MODEL_ID,
            opener=should_not_open,
            base_url="https://model-hub.example.invalid/v1",
        )
        too_many = verify_local_models(
            (*FIXED_MODEL_IDS, RESIDENT_MODEL_ID), opener=should_not_open
        )

        self.assertEqual(non_local.reason, "non_local_endpoint")
        self.assertEqual(too_many[0].reason, "too_many_models")
        self.assertEqual(calls, [])

    def test_result_and_json_cli_never_expose_generation_or_raw_details(self):
        secret_text = "GENERATED_SECRET headers=Bearer-SECRET raw-server-output"
        opener = _FakeOpener(
            self._ready_catalog(),
            {"choices": [{"message": {"content": secret_text}}]},
        )
        stdout = io.StringIO()

        exit_code = main(
            ["--model", RESIDENT_MODEL_ID, "--json"],
            opener=opener,
            stdout=stdout,
        )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(
            set(payload["results"][0]),
            {"model", "readiness", "generation", "reason", "elapsed_ms"},
        )
        rendered = stdout.getvalue()
        self.assertNotIn(secret_text, rendered)
        self.assertNotIn(DIAGNOSTIC_PROMPT, rendered)
        self.assertNotIn("Bearer", rendered)
        self.assertNotIn("raw-server-output", rendered)

    def test_cli_defaults_to_both_and_exits_nonzero_when_any_fails(self):
        catalog = self._ready_catalog()
        catalog["data"][1]["loaded"] = False
        catalog["data"][1]["state"] = "unloaded"
        opener = _FakeOpener(catalog)
        stdout = io.StringIO()

        exit_code = main(["--json"], opener=opener, stdout=stdout)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(
            [row["model"] for row in payload["results"]], list(FIXED_MODEL_IDS)
        )
        self.assertEqual(payload["results"][0]["reason"], "ok")
        self.assertEqual(payload["results"][1]["reason"], "model_not_ready")


if __name__ == "__main__":
    unittest.main()
