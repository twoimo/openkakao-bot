#!/usr/bin/env python3
"""Bounded, read-only checks for the two fixed Jarvis MLX models."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from local_mlx_gateway import MlxRequestAdmissionClosed, mlx_model_request_lease


RESIDENT_MODEL_ID = "ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit"
SWAP_MODEL_ID = "ddalcu/Qwen3.8-27B-MLX-Serve-4bit"
FIXED_LOCAL_MLX_MODEL_IDS = frozenset({RESIDENT_MODEL_ID, SWAP_MODEL_ID})
MLX_MODELS_URL = "http://127.0.0.1:11234/v1/models"
MLX_MODELS_TIMEOUT_SECS = 2.0
MLX_MODELS_MAX_BYTES = 256 * 1024
MLX_MODELS_MAX_ROWS = 256
MODEL_ID_MAX_CHARS = 200


@dataclass(frozen=True)
class MlxReadiness:
    prepared: bool
    reason: str


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


def _local_only_urlopen(request: urllib.request.Request, *, timeout: float):
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _RejectRedirects(),
    )
    return opener.open(request, timeout=timeout)


def canonical_fixed_local_mlx_model_id(value: Any) -> str | None:
    """Return the prefixless contract ID only for the two fixed local models."""

    model = str(value or "").strip()
    if (
        not model
        or len(model) > MODEL_ID_MAX_CHARS
        or any(ch.isspace() for ch in model)
    ):
        return None
    candidate = model.removeprefix("mlx/")
    return candidate if candidate in FIXED_LOCAL_MLX_MODEL_IDS else None


def resolve_fixed_local_mlx_catalog_model(
    requested: Any, catalog_model_ids: Iterable[Any]
) -> str | None:
    """Resolve a fixed local request against prefixless or ``mlx/`` catalog aliases."""

    wanted = canonical_fixed_local_mlx_model_id(requested)
    if wanted is None:
        return None
    for item in catalog_model_ids:
        if canonical_fixed_local_mlx_model_id(item) == wanted:
            return wanted
    return None


def _timeout_error(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    if isinstance(exc, urllib.error.URLError):
        return isinstance(exc.reason, (TimeoutError, socket.timeout))
    return False


def read_fixed_local_mlx_readiness(
    model: Any,
    *,
    opener: Callable[..., Any] = _local_only_urlopen,
    timeout: float = MLX_MODELS_TIMEOUT_SECS,
    state_root: Path | str | None = None,
) -> MlxReadiness:
    """Read exact 27B readiness from the localhost gateway without loading it."""

    wanted = canonical_fixed_local_mlx_model_id(model)
    if wanted != SWAP_MODEL_ID:
        return MlxReadiness(False, "model_prepare_not_allowed")

    request = urllib.request.Request(
        MLX_MODELS_URL,
        method="GET",
        headers={"Accept": "application/json"},
    )
    try:
        with mlx_model_request_lease(state_root):
            with opener(request, timeout=float(timeout)) as response:
                raw = response.read(MLX_MODELS_MAX_BYTES + 1)
    except MlxRequestAdmissionClosed as exc:
        return MlxReadiness(False, exc.code)
    except Exception as exc:
        reason = "mlx_gateway_timeout" if _timeout_error(exc) else "mlx_gateway_unavailable"
        return MlxReadiness(False, reason)

    if len(raw) > MLX_MODELS_MAX_BYTES:
        return MlxReadiness(False, "mlx_gateway_response_too_large")
    try:
        payload = json.loads(raw)
    # CPython 3.11 raises RecursionError for deeply nested JSON that is still
    # well below the byte limit, and may raise a plain ValueError for numeric
    # conversion limits. Treat every decoder rejection as malformed input so
    # a bounded localhost probe cannot escape the fail-closed contract.
    except (ValueError, TypeError, RecursionError):
        return MlxReadiness(False, "mlx_gateway_malformed")
    if not isinstance(payload, dict):
        return MlxReadiness(False, "mlx_gateway_malformed")
    rows = payload.get("data")
    if not isinstance(rows, list) or len(rows) > MLX_MODELS_MAX_ROWS:
        return MlxReadiness(False, "mlx_gateway_malformed")

    target: dict[str, Any] | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        if canonical_fixed_local_mlx_model_id(row.get("id")) == wanted:
            target = row
            break
    if target is None:
        return MlxReadiness(False, "mlx_gateway_wrong_model")
    if target.get("loaded") is not True or target.get("state") != "ready":
        return MlxReadiness(False, "mlx_gateway_not_ready")
    return MlxReadiness(True, "ready")
