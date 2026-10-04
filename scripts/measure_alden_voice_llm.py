#!/usr/bin/env python3
"""Measure the actual voice LLM adapter with public, fixed Korean context cases.

No microphone, playback, Kakao send, model swap, or cloud inference. Record
full-answer and, when supported by the adapter, streamed first-token timing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from unittest.mock import patch

import alden_voice as voice
from alden_abort import AbortController
from local_mlx_gateway import resolve_mlx_state_root


CASES = (
    {
        "id": "place_followup",
        "history": [
            {"role": "user", "content": "오늘 회의 장소는 3층 회의실로 정했어요."},
            {"role": "assistant", "content": "오늘 회의는 3층 회의실입니다."},
        ],
        "text": "그럼 어디로 가면 되죠?",
        "expected": ("3층",),
    },
    {
        "id": "latter_selection",
        "history": [
            {"role": "user", "content": "가능한 시간 두 개를 정리해 주세요."},
            {"role": "assistant", "content": "화요일 오전 10시 또는 목요일 오후 2시입니다."},
        ],
        "text": "후자로 정리해 주세요.",
        "expected": ("목요일", "2시"),
    },
    {
        "id": "arithmetic_followup",
        "history": [
            {"role": "user", "content": "책 7권을 사려고 해요. 한 권에 9000원입니다."},
            {"role": "assistant", "content": "책 7권, 권당 9000원으로 확인했습니다."},
        ],
        "text": "총 얼마죠?",
        "expected": ("63000",),
    },
)


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    return round(sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)], 6)


def observed_decode_rate(metrics: dict[str, object]) -> float | None:
    """Reported completion tokens after the first / observed model decode time.

    Includes the model's reasoning tokens when they are in completion usage;
    this is not visible Korean characters/s or speaker throughput.
    """
    usage = metrics.get("usage")
    if not isinstance(usage, dict):
        return None
    tokens = usage.get("completion_tokens")
    first = metrics.get("first_model_token_seconds")
    elapsed = metrics.get("elapsed_seconds")
    if type(tokens) is not int or tokens < 2:
        return None
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in (first, elapsed)):
        return None
    if first < 0 or elapsed <= first:
        return None
    return round((tokens - 1) / (elapsed - first), 6)


def load_snapshot() -> dict[str, object]:
    """Read concurrent host load and engine allocator scope, without inference."""
    with urllib.request.urlopen("http://127.0.0.1:11234/metrics", timeout=2) as response:
        raw = response.read(300_000).decode()
    names = {"vllm:num_requests_running", "mlx_serve:mlx_active_bytes", "vllm:request_cancelled_total"}
    engine = {line.split()[0]: float(line.split()[1]) for line in raw.splitlines() if line and not line.startswith("#") and line.split()[0] in names}
    return {"host_cpu_load": subprocess.run(["/usr/sbin/sysctl", "-n", "vm.loadavg"], capture_output=True, text=True, check=False).stdout.strip(), "engine": engine}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=4)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--drain-timeout", type=float, default=1.0, help="Bounded wait for the shared engine to become idle between cases (seconds)")
    args = parser.parse_args()
    if not 1 <= args.repeats <= 20:
        parser.error("--repeats must be 1..20")
    if not math.isfinite(args.drain_timeout) or not 0 <= args.drain_timeout <= 10:
        parser.error("--drain-timeout must be finite and in 0..10 seconds")
    if args.out.exists():
        parser.error("receipt already exists; use a distinct path")
    root = resolve_mlx_state_root()
    adapter_sha256_at_start = hashlib.sha256(Path(voice.__file__).read_bytes()).hexdigest()
    token = AbortController(root).token()
    token.raise_if_cancelled()
    sockets: list[dict[str, object]] = []

    def audit(event: str, values: tuple[object, ...]) -> None:
        if event != "socket.connect":
            return
        address = values[1]
        if not isinstance(address, tuple) or address[0] != "127.0.0.1" or address[1] != 11234:
            raise RuntimeError("benchmark_nonlocal_connection")
        sockets.append({"host": address[0], "port": address[1]})

    sys.addaudithook(audit)
    actual_open = voice._local_urlopen
    rows: list[dict[str, object]] = []
    observed: dict[str, object] = {}
    interrupted = ""

    class RecordingResponse:
        def __init__(self, response: object, request: object):
            self.response, self.request = response, request

        def __enter__(self):
            self.response.__enter__()
            return self

        def __exit__(self, *values):
            return self.response.__exit__(*values)

        def __getattr__(self, name):
            return getattr(self.response, name)

        def read(self, limit: int = -1) -> bytes:
            raw = self.response.read(limit)
            body = json.loads(raw)
            if self.request.get_method() == "POST":
                observed["usage"] = body.get("usage")
                observed["response_model"] = body.get("model")
                observed["finish_reason"] = body.get("choices", [{}])[0].get("finish_reason")
            else:
                observed["catalog"] = [row for row in body.get("data", []) if row.get("id") == voice.QWEN38_27B_MODEL_ID.removeprefix("mlx/")]
            return raw

    def recording_open(request, *, timeout):
        return RecordingResponse(actual_open(request, timeout=timeout), request)

    with patch.object(voice, "_local_urlopen", recording_open):
        for repeat in range(args.repeats):
            for case in CASES:
                token.raise_if_cancelled()
                observed.clear()
                load_before = load_snapshot()
                # The completed SSE response can precede the engine's idle
                # counter update. Observe only the explicitly bounded drain;
                # never start a second generation while a request is running.
                drain_deadline = time.monotonic() + args.drain_timeout if rows else time.monotonic()
                while load_before["engine"].get("vllm:num_requests_running") != 0 and time.monotonic() < drain_deadline:
                    time.sleep(.1)
                    load_before = load_snapshot()
                if load_before["engine"].get("vllm:num_requests_running") != 0:
                    interrupted = "existing inference active; benchmark stopped before next case"
                    break
                started = time.perf_counter()
                reply, error = "", ""
                adapter = voice.LocalMlxLlm(state_root=root)
                try:
                    reply = adapter.generate(case["text"], token, history=case["history"])
                except RuntimeError as exc:
                    error = str(exc)[:96]
                normalized = reply.replace(",", "").replace(" ", "")
                ok = bool(reply) and all(word in normalized for word in case["expected"])
                row = {"repeat": repeat, "case": case["id"], "elapsed_seconds": round(time.perf_counter() - started, 6), "reply": reply, "error": error, "expected_fact_present": ok, "adapter_metrics": getattr(adapter, "last_metrics", {}), **observed}
                row["observed_decode_tokens_per_second"] = observed_decode_rate(row["adapter_metrics"]) if not error else None
                row["load_before"] = load_before
                row["load_after"] = load_snapshot()
                rows.append(row)
                print(json.dumps({k: row[k] for k in ["repeat", "case", "elapsed_seconds", "error", "expected_fact_present"]}), flush=True)
            if interrupted:
                break

    elapsed = [row["elapsed_seconds"] for row in rows]
    first_visible = [row["adapter_metrics"]["first_visible_token_seconds"] for row in rows if "first_visible_token_seconds" in row["adapter_metrics"]]
    decode_rates = [row["observed_decode_tokens_per_second"] for row in rows if row["observed_decode_tokens_per_second"] is not None]
    receipt = {
        "schema_version": 1,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model": voice.QWEN38_27B_MODEL_ID,
        "adapter_sha256": adapter_sha256_at_start,
        "adapter_source_unchanged": adapter_sha256_at_start == hashlib.sha256(Path(voice.__file__).read_bytes()).hexdigest(),
        "case_sha256": hashlib.sha256(json.dumps(CASES, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
        "architecture": platform.machine(),
        "power": subprocess.run(["/usr/bin/pmset", "-g", "batt"], capture_output=True, text=True, check=False).stdout.strip(),
        "condition": "existing resident model; prefix cache and other host jobs remain active",
        "samples": len(rows),
        "correct_fact_count": sum(row["expected_fact_present"] for row in rows),
        "adapter_error_count": sum(bool(row["error"]) for row in rows),
        "full_answer_seconds": {"p50": round(statistics.median(elapsed), 6) if elapsed else None, "p95_nearest_rank": percentile(elapsed, .95)},
        "first_visible_token_seconds": {"samples": len(first_visible), "p50": percentile(first_visible, .5), "p95_nearest_rank": percentile(first_visible, .95)},
        "observed_decode_tokens_per_second": {"samples": len(decode_rates), "p50": round(statistics.median(decode_rates), 6) if decode_rates else None, "p95_nearest_rank": percentile(decode_rates, .95), "scope": "reported completion tokens minus first over adapter-observed decode window; includes reported reasoning; excludes prompt tokens"},
        "socket_connections": sockets,
        "rows": rows,
        "interrupted": interrupted,
        "requested_samples": len(CASES) * args.repeats,
        "drain_timeout_seconds": args.drain_timeout,
        "limits": ["Public synthetic cases; no natural microphone trial", "Fact-presence checks are not a general language-quality score", "Full-answer and first-visible-token latency include catalog/admission", "First visible content excludes a private reasoning channel", "Small-sample p95 is descriptive only", "Different cache state or concurrent host load can confound before/after speed comparisons"],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: receipt[k] for k in ["samples", "correct_fact_count", "adapter_error_count", "full_answer_seconds"]}), flush=True)
    return 1 if interrupted else 0


if __name__ == "__main__":
    raise SystemExit(main())
