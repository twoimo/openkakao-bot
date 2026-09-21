#!/usr/bin/env python3
"""Bounded generation checks for Jarvis's two fixed localhost MLX models."""

from __future__ import annotations

import argparse
import json
import math
import socket
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Any, Callable, Iterable, TextIO


RESIDENT_MODEL_ID = "ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit"
SWAP_MODEL_ID = "ddalcu/Qwen3.8-27B-MLX-Serve-4bit"
FIXED_MODEL_IDS = (RESIDENT_MODEL_ID, SWAP_MODEL_ID)
FIXED_MODEL_ID_SET = frozenset(FIXED_MODEL_IDS)

LOCAL_BASE_URL = "http://127.0.0.1:11234/v1"
MODELS_URL = f"{LOCAL_BASE_URL}/models"
CHAT_COMPLETIONS_URL = f"{LOCAL_BASE_URL}/chat/completions"
ALLOWED_URLS = frozenset({MODELS_URL, CHAT_COMPLETIONS_URL})

DIAGNOSTIC_PROMPT = "확인이라고만 답하세요."
DIAGNOSTIC_MAX_TOKENS = 8
DEFAULT_TIMEOUT_SECS = 30.0
MIN_TIMEOUT_SECS = 0.1
MAX_TIMEOUT_SECS = 60.0
MAX_RESPONSE_BYTES = 64 * 1024
MAX_MODEL_ROWS = 256
MAX_CHOICES = 8
MAX_CONTENT_CHARS = 1_024
MAX_SELECTED_MODELS = len(FIXED_MODEL_IDS)
MAX_ELAPSED_MS = int(MAX_TIMEOUT_SECS * 1_000) + 1_000

OWNER_STATE_CODES = frozenset(
    {
        "app_owned",
        "model_owner_unknown",
        "model_owner_unmanaged",
        "model_owner_state_invalid",
        "model_owner_state_stale",
        "model_gateway_unavailable",
        "model_residency_mismatch",
        "model_drain_unverified",
    }
)
OWNER_PROBE_UNAVAILABLE = "model_owner_unknown"

OpenRequest = Callable[..., Any]


@dataclass(frozen=True)
class VerificationResult:
    model: str
    readiness: bool
    generation: bool
    reason: str
    elapsed_ms: int

    @property
    def ok(self) -> bool:
        return self.readiness and self.generation and self.reason == "ok"

    def as_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "readiness": self.readiness,
            "generation": self.generation,
            "reason": self.reason,
            "elapsed_ms": self.elapsed_ms,
        }


class _ProbeFailure(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _TransportPolicyError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


def _local_only_urlopen(request: urllib.request.Request, *, timeout: float):
    if request.full_url not in ALLOWED_URLS:
        raise _TransportPolicyError("non_local_endpoint")
    if request.has_proxy():
        raise _TransportPolicyError("proxy_forbidden")
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _RejectRedirects(),
    )
    return opener.open(request, timeout=timeout)


def canonical_fixed_model_id(value: Any) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 200:
        return None
    if value in FIXED_MODEL_ID_SET:
        return value
    if value.startswith("mlx/") and value[4:] in FIXED_MODEL_ID_SET:
        return value[4:]
    return None


def _valid_timeout(value: Any) -> float | None:
    try:
        timeout = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if (
        not math.isfinite(timeout)
        or timeout < MIN_TIMEOUT_SECS
        or timeout > MAX_TIMEOUT_SECS
    ):
        return None
    return timeout


def _elapsed_ms(started: float, clock: Callable[[], float]) -> int:
    try:
        elapsed = int(max(0.0, clock() - started) * 1_000)
    except (TypeError, ValueError, OverflowError):
        return 0
    return min(elapsed, MAX_ELAPSED_MS)


def _remaining_timeout(
    deadline: float,
    clock: Callable[[], float],
    stage: str,
) -> float:
    remaining = deadline - clock()
    if not math.isfinite(remaining) or remaining <= 0:
        raise _ProbeFailure(f"{stage}_timeout")
    return remaining


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-standard JSON constant")


def _response_status(response: Any) -> int | None:
    status = getattr(response, "status", None)
    if status is None:
        getcode = getattr(response, "getcode", None)
        status = getcode() if callable(getcode) else None
    if status is None:
        return None
    try:
        return int(status)
    except (TypeError, ValueError, OverflowError) as exc:
        raise _ProbeFailure("malformed_http_status") from exc


def _read_json_response(
    request: urllib.request.Request,
    *,
    opener: OpenRequest,
    deadline: float,
    clock: Callable[[], float],
    stage: str,
) -> Any:
    if request.full_url not in ALLOWED_URLS:
        raise _ProbeFailure("non_local_endpoint")

    try:
        with opener(
            request,
            timeout=_remaining_timeout(deadline, clock, stage),
        ) as response:
            geturl = getattr(response, "geturl", None)
            final_url = geturl() if callable(geturl) else request.full_url
            if final_url != request.full_url:
                raise _ProbeFailure(f"{stage}_redirect_rejected")

            status = _response_status(response)
            if status is not None and 300 <= status <= 399:
                raise _ProbeFailure(f"{stage}_redirect_rejected")
            if status is not None and status != 200:
                raise _ProbeFailure(f"{stage}_http_error")

            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except _ProbeFailure:
        raise
    except _TransportPolicyError as exc:
        code = (
            exc.code
            if exc.code in {"non_local_endpoint", "proxy_forbidden"}
            else "transport_policy_error"
        )
        raise _ProbeFailure(code) from None
    except urllib.error.HTTPError as exc:
        suffix = "redirect_rejected" if 300 <= exc.code <= 399 else "http_error"
        try:
            exc.close()
        except Exception:
            pass
        raise _ProbeFailure(f"{stage}_{suffix}") from None
    except (TimeoutError, socket.timeout):
        raise _ProbeFailure(f"{stage}_timeout") from None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise _ProbeFailure(f"{stage}_timeout") from None
        raise _ProbeFailure(f"{stage}_network_error") from None
    except Exception:
        raise _ProbeFailure(f"{stage}_network_error") from None

    if not isinstance(raw, (bytes, bytearray)):
        raise _ProbeFailure(f"{stage}_malformed_json")
    if len(raw) > MAX_RESPONSE_BYTES:
        raise _ProbeFailure(f"{stage}_response_too_large")
    try:
        return json.loads(raw, parse_constant=_reject_json_constant)
    except (ValueError, TypeError, RecursionError):
        raise _ProbeFailure(f"{stage}_malformed_json") from None


def _require_ready_model(payload: Any, model: str) -> None:
    if not isinstance(payload, dict):
        raise _ProbeFailure("models_malformed_json")
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise _ProbeFailure("models_malformed_json")
    if len(rows) > MAX_MODEL_ROWS:
        raise _ProbeFailure("models_too_many_rows")

    matches: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise _ProbeFailure("models_malformed_json")
        if canonical_fixed_model_id(row.get("id")) == model:
            matches.append(row)

    if not matches:
        raise _ProbeFailure("model_not_found")
    if len(matches) != 1:
        raise _ProbeFailure("models_ambiguous_model")

    target = matches[0]
    if target.get("loaded") is not True or target.get("state") != "ready":
        raise _ProbeFailure("model_not_ready")


def _require_generation(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise _ProbeFailure("generation_malformed_json")
    choices = payload.get("choices")
    if not isinstance(choices, list):
        raise _ProbeFailure("generation_malformed_json")
    if len(choices) > MAX_CHOICES:
        raise _ProbeFailure("generation_too_many_choices")
    if not choices or not isinstance(choices[0], dict):
        raise _ProbeFailure("generation_missing_content")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise _ProbeFailure("generation_missing_content")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise _ProbeFailure("generation_missing_content")
    if len(content) > MAX_CONTENT_CHARS:
        raise _ProbeFailure("generation_content_too_large")


def verify_local_model(
    requested_model: Any,
    *,
    opener: OpenRequest = _local_only_urlopen,
    timeout: float = DEFAULT_TIMEOUT_SECS,
    base_url: str = LOCAL_BASE_URL,
    clock: Callable[[], float] = time.monotonic,
) -> VerificationResult:
    started = clock()
    model = canonical_fixed_model_id(requested_model)
    if model is None:
        return VerificationResult(
            "invalid", False, False, "invalid_model_id", _elapsed_ms(started, clock)
        )
    if base_url != LOCAL_BASE_URL:
        return VerificationResult(
            model,
            False,
            False,
            "non_local_endpoint",
            _elapsed_ms(started, clock),
        )
    bounded_timeout = _valid_timeout(timeout)
    if bounded_timeout is None:
        return VerificationResult(
            model, False, False, "invalid_timeout", _elapsed_ms(started, clock)
        )

    deadline = started + bounded_timeout
    readiness = False
    try:
        models_request = urllib.request.Request(
            MODELS_URL,
            method="GET",
            headers={"Accept": "application/json"},
        )
        models_payload = _read_json_response(
            models_request,
            opener=opener,
            deadline=deadline,
            clock=clock,
            stage="models",
        )
        _require_ready_model(models_payload, model)
        readiness = True

        body = json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": DIAGNOSTIC_PROMPT}],
                "max_tokens": DIAGNOSTIC_MAX_TOKENS,
                "temperature": 0,
                "stream": False,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        generation_request = urllib.request.Request(
            CHAT_COMPLETIONS_URL,
            data=body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        generation_payload = _read_json_response(
            generation_request,
            opener=opener,
            deadline=deadline,
            clock=clock,
            stage="generation",
        )
        _require_generation(generation_payload)
    except _ProbeFailure as exc:
        return VerificationResult(
            model,
            readiness,
            False,
            exc.code,
            _elapsed_ms(started, clock),
        )

    return VerificationResult(
        model, True, True, "ok", _elapsed_ms(started, clock)
    )


def verify_local_models(
    models: Iterable[Any],
    *,
    opener: OpenRequest = _local_only_urlopen,
    timeout: float = DEFAULT_TIMEOUT_SECS,
    base_url: str = LOCAL_BASE_URL,
    clock: Callable[[], float] = time.monotonic,
) -> list[VerificationResult]:
    selected = list(islice(models, MAX_SELECTED_MODELS + 1))
    if not selected:
        return [VerificationResult("invalid", False, False, "no_models", 0)]
    if len(selected) > MAX_SELECTED_MODELS:
        return [VerificationResult("invalid", False, False, "too_many_models", 0)]
    return [
        verify_local_model(
            model,
            opener=opener,
            timeout=timeout,
            base_url=base_url,
            clock=clock,
        )
        for model in selected
    ]


def _default_state_root() -> Path:
    parent = Path.home() / "Library" / "Application Support" / "openkakao"
    modern = parent / "auto-reply"
    legacy = parent / "bujamentor"
    if (modern / "enrollment.json").is_file() or not (legacy / "enrollment.json").is_file():
        return modern
    return legacy


def _owner_status(state_root: Path) -> str:
    """Read-only MLX owner code. Never raises and never loads a model.

    Only a fixed reason code leaves this function so the diagnostic can be
    pasted into a report without leaking process arguments or filesystem paths.
    """

    try:
        scripts_dir = Path(__file__).resolve().parent
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from auto_reply_ondevice import managed_residency_status

        payload = managed_residency_status(Path(state_root))
    except Exception:
        return OWNER_PROBE_UNAVAILABLE
    if not isinstance(payload, dict):
        return OWNER_PROBE_UNAVAILABLE
    state = payload.get("owner_state")
    if isinstance(state, str) and state in OWNER_STATE_CODES:
        return state
    return OWNER_PROBE_UNAVAILABLE


def _timeout_argument(value: str) -> float:
    timeout = _valid_timeout(value)
    if timeout is None:
        raise argparse.ArgumentTypeError(
            f"timeout must be between {MIN_TIMEOUT_SECS} and {MAX_TIMEOUT_SECS} seconds"
        )
    return timeout


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify the two fixed Jarvis models through localhost only."
    )
    parser.add_argument(
        "--model",
        action="append",
        dest="models",
        help="fixed model ID; repeat to check both (optional mlx/ prefix)",
    )
    parser.add_argument(
        "--timeout",
        type=_timeout_argument,
        default=DEFAULT_TIMEOUT_SECS,
        help=f"per-model GET+POST deadline in seconds (max {MAX_TIMEOUT_SECS:g})",
    )
    parser.add_argument("--json", action="store_true", help="emit structured JSON")
    parser.add_argument(
        "--owner",
        action="store_true",
        help="also report the bounded on-device MLX owner code (read-only)",
    )
    parser.add_argument(
        "--state-root",
        default=None,
        help="state root holding the private MLX residency record (optional)",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    opener: OpenRequest = _local_only_urlopen,
    stdout: TextIO | None = None,
    clock: Callable[[], float] = time.monotonic,
    owner_probe: Callable[[Path], str] | None = None,
) -> int:
    args = _parser().parse_args(argv)
    selected = args.models if args.models is not None else FIXED_MODEL_IDS
    results = verify_local_models(
        selected,
        opener=opener,
        timeout=args.timeout,
        clock=clock,
    )
    ok = all(result.ok for result in results)
    output = stdout if stdout is not None else sys.stdout
    owner: str | None = None
    if args.owner:
        probe = owner_probe or _owner_status
        try:
            state_root = (
                Path(args.state_root).expanduser()
                if args.state_root
                else _default_state_root()
            )
            candidate = probe(state_root)
        except Exception:
            candidate = OWNER_PROBE_UNAVAILABLE
        owner = (
            candidate
            if isinstance(candidate, str) and candidate in OWNER_STATE_CODES
            else OWNER_PROBE_UNAVAILABLE
        )

    if args.json:
        payload: dict[str, object] = {
            "ok": ok,
            "results": [result.as_dict() for result in results],
        }
        if owner is not None:
            payload["owner"] = owner
        print(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            file=output,
        )
    else:
        for result in results:
            print(
                " ".join(
                    (
                        f"model={result.model}",
                        f"readiness={str(result.readiness).lower()}",
                        f"generation={str(result.generation).lower()}",
                        f"reason={result.reason}",
                        f"elapsed_ms={result.elapsed_ms}",
                    )
                ),
                file=output,
            )
        if owner is not None:
            print(f"owner={owner}", file=output)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
