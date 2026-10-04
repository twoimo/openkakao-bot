#!/usr/bin/env python3
"""Cancel one owned synthetic inference and read MLX's actual cancellation counter."""
import argparse
import hashlib
import json
import pathlib
import subprocess
import threading
import time
import urllib.request

import alden_voice
from alden_abort import AbortController
from local_mlx_gateway import resolve_mlx_state_root


def metrics():
    with urllib.request.urlopen("http://127.0.0.1:11234/metrics", timeout=2) as response:
        raw = response.read(300_000).decode()
    names = {"vllm:request_cancelled_total", "vllm:num_requests_running", "mlx_serve:mlx_active_bytes"}
    return {line.split()[0]: float(line.split()[1]) for line in raw.splitlines() if line and not line.startswith("#") and line.split()[0] in names}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--after-token", action="store_true", help="Wait for a streamed model token before cancelling")
    args = parser.parse_args()
    if args.out.exists():
        parser.error("receipt already exists")
    root = resolve_mlx_state_root()
    token = AbortController(root).token()
    token.raise_if_cancelled()
    before = metrics()
    if before.get("vllm:num_requests_running") != 0:
        raise SystemExit("existing inference active; probe not started")
    result = {}
    adapter = alden_voice.LocalMlxLlm(state_root=root)

    def request():
        try:
            result["reply"] = adapter.generate("서로 다른 한국어 문장으로 맥락, 발화, 검색, 취소에 관한 설명을 100개 작성해 주세요. 각 문장 앞에 번호를 붙이고 줄을 나누세요.", token)
        except Exception as error:
            result["exception"] = type(error).__name__

    worker = threading.Thread(target=request)
    worker.start()
    running = {}
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and worker.is_alive():
        running = metrics()
        if running.get("vllm:num_requests_running") == 1 and (not args.after_token or "first_model_token_seconds" in adapter.last_metrics):
            break
        time.sleep(.05)
    if running.get("vllm:num_requests_running") != 1 or (args.after_token and "first_model_token_seconds" not in adapter.last_metrics):
        token.cancel()
        worker.join(3)
        raise SystemExit("owned live inference was not observed; no backend cancellation claim")
    started = time.perf_counter()
    token.cancel()
    worker.join(3)
    client_ms = (time.perf_counter() - started) * 1000
    after = metrics()
    deadline = time.monotonic() + 3
    while after.get("vllm:num_requests_running") != 0 and time.monotonic() < deadline:
        time.sleep(.05)
        after = metrics()
    receipt = {"schema_version": 1, "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "model": alden_voice.QWEN38_27B_MODEL_ID, "adapter_sha256": hashlib.sha256(pathlib.Path(alden_voice.__file__).read_bytes()).hexdigest(), "samples": 1, "host_cpu_load": subprocess.run(["/usr/sbin/sysctl", "-n", "vm.loadavg"], capture_output=True, text=True, check=False).stdout.strip(), "before": before, "running_before_cancel": running, "after": after, "client_exit_ms": round(client_ms, 3), "server_idle_observed_ms": round((time.perf_counter() - started) * 1000, 3), "client_thread_stopped": not worker.is_alive(), "exception": result.get("exception"), "reply_returned": "reply" in result, "backend_cancel_counter_delta": after.get("vllm:request_cancelled_total", 0) - before.get("vllm:request_cancelled_total", 0), "limits": ["One owned synthetic inference; not a physical shortcut measurement", "No mic/TTS/Kakao send/model swap/global latch change", "Shared model service; other requests may confound metrics", "Allocator bytes include cached model resources and are not process RSS"]}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    receipt["cancel_phase"] = "after_first_model_token" if args.after_token else "server_running_before_first_token"
    receipt["adapter_metrics"] = adapter.last_metrics
    args.out.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(receipt, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
