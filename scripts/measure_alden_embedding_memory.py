#!/usr/bin/env python3
"""Compare pinned E5 allocator retention in fresh offline, sequential processes.

Uses synthetic text only. Does not contact or restart the resident services.
The parent requires the installed embedding Python explicitly; each child loads
the exact checkpoint and denies all socket connections before heavy imports.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
LENGTHS = (8, 16, 32, 64, 128, 192, 256, 320, 384, 448, 496)
BATCHES = (1, 2, 4, 8)
DEFAULT_BASELINE = "63267e9fe4846afb8ceea2c605573c21b2e8614b"
FIELDS = (
    "user", "system", "idle", "interrupt", "pageins", "wired", "rss",
    "footprint", "start", "exit", "child_user", "child_system", "child_idle",
    "child_interrupt", "child_pageins", "child_elapsed", "bytes_read", "bytes_written",
)


class ProcessUsage(ctypes.Structure):
    _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [(field, ctypes.c_uint64) for field in FIELDS]


def write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def child_probe(source: Path, output: Path) -> None:
    if sys.platform != "darwin" or ctypes.sizeof(ProcessUsage) != 160:
        raise RuntimeError("the memory probe requires macOS rusage_info_v2")
    sys.path.insert(0, str(ROOT / "scripts"))
    from alden_voice import _read_voice_memory_budget

    budget = _read_voice_memory_budget()
    if budget.pressure_level != 1 or budget.reclaimable_bytes < 4 * 1024**3:
        raise RuntimeError("insufficient independent E5 probe memory admission")
    connection_attempts: list[str] = []

    def deny_network(event: str, values: tuple) -> None:
        if event == "socket.connect":
            connection_attempts.append(str(values[1]))
            raise RuntimeError("embedding_memory_probe_network_denied")

    sys.addaudithook(deny_network)
    import mlx.core as mx
    import numpy as np

    spec = importlib.util.spec_from_file_location("embedding_probe_engine", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("embedding source could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    lib = ctypes.CDLL("/usr/lib/libproc.dylib")
    lib.proc_pid_rusage.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_void_p)

    def memory() -> dict[str, int]:
        usage = ProcessUsage()
        if lib.proc_pid_rusage(os.getpid(), 2, ctypes.byref(usage)) != 0:
            raise RuntimeError("kernel process memory is unavailable")
        return {
            "rss_bytes": usage.rss,
            "footprint_bytes": usage.footprint,
            "mlx_active_bytes": int(mx.get_active_memory()),
            "mlx_cache_bytes": int(mx.get_cache_memory()),
            "mlx_peak_bytes": int(mx.get_peak_memory()),
        }

    load_start = time.perf_counter()
    engine = module.LocalE5Engine(module.default_model_path())
    load_seconds = time.perf_counter() - load_start
    digest = hashlib.sha256()
    rows = []
    vector_count = 0
    for batch in BATCHES:
        for length in LENGTHS:
            texts = ["공개 검사 " + "확인 " * length + str(index) for index in range(batch)]
            started = time.perf_counter()
            result = engine.embed(texts, "passage" if batch > 1 else "query")
            elapsed = time.perf_counter() - started
            vectors = np.asarray(result.vectors, dtype=np.float32)
            if vectors.shape != (batch, module.MODEL_DIMENSIONS) or not np.isfinite(vectors).all():
                raise RuntimeError("embedding output is invalid")
            digest.update(vectors.tobytes())
            vector_count += len(vectors)
            rows.append({"batch": batch, "length": length, "tokens": result.prompt_tokens,
                         "seconds": elapsed, **memory()})
    varied_idle = memory()
    query_seconds = []
    query_digest = hashlib.sha256()
    for index in range(22):
        started = time.perf_counter()
        result = engine.embed(["올든의 최신 대화를 검색한다"], "query")
        elapsed = time.perf_counter() - started
        if index >= 2:
            query_seconds.append(elapsed)
            query_digest.update(np.asarray(result.vectors, dtype=np.float32).tobytes())
    write_json(output, {
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "model": engine.model_id,
        "runtime": {name: importlib.metadata.version(name) for name in ("mlx", "numpy", "tokenizers")},
        "python": sys.version,
        "load_seconds": load_seconds,
        "requests": len(rows), "vectors": vector_count,
        "output_sha256": digest.hexdigest(),
        "idle": varied_idle, "rows": rows, "connection_attempts": connection_attempts,
        "warm_query_seconds": query_seconds, "warm_query_output_sha256": query_digest.hexdigest(),
    })


def compare_results(before: list[dict], after: list[dict]) -> dict:
    if not before or len(before) != len(after):
        raise ValueError("equal, nonempty baseline/candidate sample counts are required")
    samples = before + after
    if len({item["model"] for item in samples}) != 1 or len({json.dumps(item["runtime"], sort_keys=True) for item in samples}) != 1:
        raise ValueError("model or runtime changed between samples")
    if any(item["requests"] != 44 or item["vectors"] != 165 or item["connection_attempts"] for item in samples):
        raise ValueError("workload or offline boundary changed")
    if len({item["output_sha256"] for item in samples}) != 1:
        raise ValueError("baseline/candidate Float32 output bytes differ")
    base_memory = statistics.median(item["idle"]["footprint_bytes"] for item in before)
    new_memory = statistics.median(item["idle"]["footprint_bytes"] for item in after)
    cap = 512 * 1024**2
    if any(item["idle"]["mlx_cache_bytes"] > cap for item in after):
        raise ValueError("candidate idle allocator cache exceeded 512 MiB")
    return {
        "scope": "same pinned weights and runtime; fresh sequential offline processes; synthetic text; resident services untouched",
        "fresh_processes_per_variant": len(before), "requests_per_process": 44,
        "model": before[0]["model"], "runtime": before[0]["runtime"],
        "before_median_footprint_bytes": base_memory,
        "after_median_footprint_bytes": new_memory,
        "footprint_reduction_percent": (base_memory - new_memory) / base_memory * 100,
        "output_sha256": before[0]["output_sha256"], "output_bytes_match": True,
        "after_max_idle_cache_bytes": max(item["idle"]["mlx_cache_bytes"] for item in after),
        "before_load_seconds": [item["load_seconds"] for item in before],
        "after_load_seconds": [item["load_seconds"] for item in after],
        "before_workload_seconds": [sum(row["seconds"] for row in item["rows"]) for item in before],
        "after_workload_seconds": [sum(row["seconds"] for row in item["rows"]) for item in after],
        "limits": ["RSS, kernel footprint and MLX pools overlap; never sum them",
                   "few fresh processes and shared-host/cache variability; no general latency p95 or causal speedup claim",
                   "not whole-app power, resident LLM, microphone or speaker measurements"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, help="absolute installed embedding-runtime Python")
    parser.add_argument("--output", type=Path, required=True, help="new private result directory")
    parser.add_argument("--baseline-ref", default=DEFAULT_BASELINE)
    parser.add_argument("--repeats", type=int, default=3, choices=range(1, 11))
    parser.add_argument("--child-source", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child_source is not None:
        child_probe(args.child_source, args.output)
        return
    if args.python is None or not args.python.is_absolute() or not args.python.is_file():
        parser.error("--python must name the installed embedding Python explicitly")
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    baseline = subprocess.check_output(
        ["git", "show", f"{args.baseline_ref}:scripts/alden_local_embedding_server.py"], cwd=ROOT)
    before, after = [], []
    with tempfile.TemporaryDirectory(prefix="alden-e5-memory-") as scratch:
        base_file = Path(scratch) / "baseline.py"
        base_file.write_bytes(baseline)
        for index in range(args.repeats):
            for variant, source, samples in (
                ("baseline", base_file, before),
                ("candidate", ROOT / "scripts/alden_local_embedding_server.py", after),
            ):
                destination = args.output / f"{variant}-{index + 1:02}.json"
                process = subprocess.run(
                    [str(args.python), "-B", str(Path(__file__).resolve()), "--child-source",
                     str(source), "--output", str(destination)], cwd=ROOT,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
                (args.output / f"{variant}-{index + 1:02}.log").write_bytes(process.stdout + process.stderr)
                process.check_returncode()
                samples.append(json.loads(destination.read_text()))
    result = compare_results(before, after)
    if len({item["warm_query_output_sha256"] for item in before + after}) != 1:
        raise ValueError("warmed query output bytes differ")
    result["warm_query_samples_per_process"] = 20
    result["before_warm_query_median_seconds"] = [statistics.median(item["warm_query_seconds"]) for item in before]
    result["after_warm_query_median_seconds"] = [statistics.median(item["warm_query_seconds"]) for item in after]
    result["baseline_ref"] = args.baseline_ref
    result["host"] = {
        "chip": subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], text=True).strip(),
        "memory_bytes": int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True)),
        "power": subprocess.check_output(["pmset", "-g", "batt"], text=True).strip(),
    }
    write_json(args.output / "comparison.json", result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
