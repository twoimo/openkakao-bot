#!/usr/bin/env python3
"""Fail-closed alphaXiv paper provenance for the offline DREAM-RSI loop.

The existing DREAM-RSI simulator and fine-tune module keep their public
behaviour.  This module only composes three kinds of provenance:

* paper evidence returned by the ``alphaxiv`` CLI,
* fixed-budget DREAM-RSI candidate/branch/stop history, and
* DPO preference evaluation from response-token log probabilities.

It never promotes or replaces a live model.  It also never turns DREAM-RSI's
character-similarity replay score into paper evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Sequence

try:  # scripts.* imports in tests; sibling imports when run as a flat script.
    from scripts.auto_reply_dream_rsi import run_fixed_budget_loop
    from scripts.auto_reply_finetune import (
        DPO_EVAL_UNAVAILABLE,
        evaluate_preference_pairs,
    )
except ImportError:  # pragma: no cover - flat script execution
    from auto_reply_dream_rsi import run_fixed_budget_loop
    from auto_reply_finetune import DPO_EVAL_UNAVAILABLE, evaluate_preference_pairs


REPORT_SCHEMA_VERSION = 1
DEFAULT_QUERY = "DREAM-RSI recursive self-improvement dreaming"
DREAM_RSI_PAPER_ID = "2609.14858"
DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_TIMEOUT_SECONDS = 60.0
MAX_QUERY_CHARS = 512
MAX_CLI_JSON_BYTES = 512 * 1024
MAX_INPUT_JSON_BYTES = 256 * 1024
MAX_REPORT_JSON_BYTES = 512 * 1024
MAX_SUMMARY_CHARS = 64 * 1024
MAX_CANDIDATES = 32
MAX_DPO_PAIRS = 128
MAX_RUNTIME_MODELS = 16

_ARXIV_ID_RE = re.compile(
    r"^(?:\d{4}\.\d{4,5}|[A-Za-z][A-Za-z0-9._-]*/\d{7})(?:v\d+)?$"
)
_PAPER_CONTAINER_KEYS = ("papers", "results", "items", "data")


def _valid_plain_text(value: Any, *, limit: int, allow_empty: bool = False) -> str:
    text = str(value or "").strip()
    if not text and not allow_empty:
        raise ValueError("empty_text")
    if len(text) > limit:
        raise ValueError("text_too_long")
    if any(ord(char) < 32 for char in text):
        raise ValueError("control_character")
    return text


def _validate_query(query: Any) -> str:
    text = _valid_plain_text(query, limit=MAX_QUERY_CHARS)
    # A leading option token could be reinterpreted by a third-party CLI even
    # though subprocess is invoked without a shell.
    if text.startswith("-"):
        raise ValueError("query_looks_like_option")
    return text


def _validate_paper_id(value: Any) -> str:
    paper_id = _valid_plain_text(value, limit=96)
    if ".." in paper_id or not _ARXIV_ID_RE.fullmatch(paper_id):
        raise ValueError("invalid_paper_id")
    return paper_id


def _resolve_alphaxiv_cli(requested: Any) -> tuple[str | None, str | None]:
    """Resolve one executable without allowing option/path injection."""
    try:
        value = _valid_plain_text(requested, limit=4096)
    except ValueError:
        return None, "invalid_cli_path"

    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        if ".." in Path(value).parts:
            return None, "invalid_cli_path"
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            return None, "cli_missing"
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            return None, "cli_missing"
        return str(resolved), None

    # Relative paths are deliberately rejected.  A bare executable name is
    # resolved through PATH; embedded separators would otherwise permit ../.
    if "/" in value or "\\" in value or value in {".", ".."}:
        return None, "invalid_cli_path"
    found = shutil.which(value)
    if not found:
        return None, "cli_missing"
    return found, None


def _bytes(value: Any) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    return str(value).encode("utf-8", errors="replace")


def _decode_small(value: Any, *, limit: int) -> tuple[str | None, str | None]:
    raw = _bytes(value)
    if len(raw) > limit:
        return None, "output_too_large"
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "invalid_utf8"


def _run_alphaxiv(
    executable: str,
    args: Sequence[str],
    *,
    timeout: float,
    env: dict[str, str],
    expect_json: bool,
    operation: str,
) -> tuple[Any | None, dict[str, Any]]:
    command = [executable, *[str(arg) for arg in args]]
    record: dict[str, Any] = {
        "operation": operation,
        "argv": list(args),
        "status": "unavailable",
        "returncode": None,
    }
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            shell=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        record["reason"] = "cli_timeout"
        return None, record
    except OSError as exc:
        record["reason"] = f"cli_exec_error:{type(exc).__name__}"
        return None, record

    record["returncode"] = int(completed.returncode)
    stderr_text, _ = _decode_small(completed.stderr, limit=4096)
    if stderr_text:
        record["stderr"] = stderr_text.strip()[:2000]
    if completed.returncode != 0:
        record["reason"] = "cli_nonzero"
        return None, record

    stdout_text, decode_error = _decode_small(
        completed.stdout, limit=MAX_CLI_JSON_BYTES
    )
    if decode_error:
        record["reason"] = decode_error
        return None, record
    if not expect_json:
        record["status"] = "ok"
        return stdout_text or "", record

    try:
        payload = json.loads(stdout_text or "")
    except (json.JSONDecodeError, ValueError):
        record["reason"] = "invalid_json"
        return None, record
    if not isinstance(payload, (dict, list)):
        record["reason"] = "invalid_json_shape"
        return None, record
    record["status"] = "ok"
    return payload, record


def _iter_paper_nodes(payload: Any):
    if isinstance(payload, list):
        for item in payload:
            yield from _iter_paper_nodes(item)
        return
    if not isinstance(payload, dict):
        return

    if any(key in payload for key in ("paper_id", "paperId", "arxiv_id", "arxivId")):
        yield payload
    for key in _PAPER_CONTAINER_KEYS:
        child = payload.get(key)
        if isinstance(child, (dict, list)):
            yield from _iter_paper_nodes(child)


def _paper_id_from_node(node: dict[str, Any]) -> str | None:
    for key in ("paper_id", "paperId", "arxiv_id", "arxivId", "id"):
        value = node.get(key)
        if value is None:
            continue
        try:
            return _validate_paper_id(value)
        except ValueError:
            continue
    for key in ("url", "paper_url", "arxiv_url"):
        value = str(node.get(key) or "")
        match = re.search(
            r"/(?:abs|pdf)/((?:\d{4}\.\d{4,5}|[A-Za-z][A-Za-z0-9._-]*/\d{7})(?:v\d+)?)",
            value,
        )
        if match:
            try:
                return _validate_paper_id(match.group(1))
            except ValueError:
                pass
    return None


def _first_direct_text(node: Any, keys: Sequence[str], *, limit: int) -> str:
    if not isinstance(node, dict):
        return ""
    for key in keys:
        value = node.get(key)
        if isinstance(value, str):
            text = value.strip()
            if 0 < len(text) <= limit:
                return text
    return ""


def _extract_search_papers(payload: Any) -> list[dict[str, str]]:
    papers: list[dict[str, str]] = []
    seen: set[str] = set()
    for node in _iter_paper_nodes(payload):
        paper_id = _paper_id_from_node(node)
        title = _first_direct_text(node, ("title", "paper_title", "name"), limit=1000)
        if not paper_id or not title or paper_id in seen:
            continue
        url = _first_direct_text(node, ("url", "paper_url", "arxiv_url"), limit=2048)
        papers.append({"paper_id": paper_id, "title": title, "url": url})
        seen.add(paper_id)
        if len(papers) >= 20:
            break
    return papers


def _find_value(payload: Any, keys: set[str]) -> Any:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in keys and value not in (None, "", [], {}):
                return value
        for key in ("paper", "data", "result", "context", "summary"):
            child = payload.get(key)
            if isinstance(child, (dict, list)):
                found = _find_value(child, keys)
                if found not in (None, "", [], {}):
                    return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_value(item, keys)
            if found not in (None, "", [], {}):
                return found
    return None


def _context_identity(payload: Any) -> tuple[str | None, str]:
    raw_id = _find_value(payload, {"paper_id", "paperId", "arxiv_id", "arxivId"})
    paper_id: str | None = None
    if raw_id is not None:
        try:
            paper_id = _validate_paper_id(raw_id)
        except ValueError:
            paper_id = None
    raw_title = _find_value(payload, {"title", "paper_title"})
    title = ""
    if isinstance(raw_title, str):
        title = raw_title.strip()[:1000]
    return paper_id, title


def _summary_text(payload: Any) -> str | None:
    value = _find_value(payload, {"summary", "summary_text"})
    if isinstance(value, dict):
        value = _find_value(value, {"summary", "text", "content"})
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > MAX_SUMMARY_CHARS:
        return None
    return text


def _paper_unavailable(
    *,
    requested_cli: str,
    resolved_cli: str | None,
    query: str,
    reason: str,
    commands: list[dict[str, Any]] | None = None,
    selected_paper: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "reason": reason,
        "analysis_available": False,
        "provider": "alphaxiv_cli",
        "query": query,
        "selection_method": "none",
        "selected_paper": selected_paper,
        "evidence": None,
        "string_similarity_used": False,
        "cli": {
            "requested": requested_cli,
            "resolved": resolved_cli,
            "isolated_context": True,
            "commands": commands or [],
        },
    }


def collect_paper_report(
    query: str = DEFAULT_QUERY,
    *,
    alphaxiv_cli: str = "alphaxiv",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    paper_id: str | None = None,
) -> dict[str, Any]:
    """Collect deterministic paper evidence through the alphaXiv CLI only."""
    requested_cli = str(alphaxiv_cli)
    try:
        safe_query = _validate_query(query)
    except ValueError as exc:
        return _paper_unavailable(
            requested_cli=requested_cli,
            resolved_cli=None,
            query=str(query)[:MAX_QUERY_CHARS],
            reason=f"invalid_query:{exc}",
        )
    try:
        timeout_value = float(timeout)
    except (TypeError, ValueError):
        timeout_value = -1.0
    if not (0.1 <= timeout_value <= MAX_TIMEOUT_SECONDS):
        return _paper_unavailable(
            requested_cli=requested_cli,
            resolved_cli=None,
            query=safe_query,
            reason="invalid_timeout",
        )

    executable, resolve_error = _resolve_alphaxiv_cli(alphaxiv_cli)
    if resolve_error or not executable:
        return _paper_unavailable(
            requested_cli=requested_cli,
            resolved_cli=None,
            query=safe_query,
            reason=resolve_error or "cli_missing",
        )

    commands: list[dict[str, Any]] = []
    selected: dict[str, str] | None = None
    selection_method = "direct_paper_id" if paper_id else "alphaxiv_search_rank_1"
    with tempfile.TemporaryDirectory(prefix="openkakao-alphaxiv-") as temp_home:
        env = dict(os.environ)
        env["ALPHAXIV_HOME"] = temp_home

        if paper_id:
            try:
                selected_id = _validate_paper_id(paper_id)
            except ValueError:
                return _paper_unavailable(
                    requested_cli=requested_cli,
                    resolved_cli=executable,
                    query=safe_query,
                    reason="invalid_paper_id",
                )
            selected = {"paper_id": selected_id, "title": "", "url": ""}
        else:
            search_payload, search_record = _run_alphaxiv(
                executable,
                ("search", "papers", safe_query, "--json"),
                timeout=timeout_value,
                env=env,
                expect_json=True,
                operation="search_papers",
            )
            commands.append(search_record)
            if search_payload is None:
                return _paper_unavailable(
                    requested_cli=requested_cli,
                    resolved_cli=executable,
                    query=safe_query,
                    reason=str(search_record.get("reason") or "search_unavailable"),
                    commands=commands,
                )
            papers = _extract_search_papers(search_payload)
            if not papers:
                return _paper_unavailable(
                    requested_cli=requested_cli,
                    resolved_cli=executable,
                    query=safe_query,
                    reason="incomplete_search_response",
                    commands=commands,
                )
            selected = papers[0]

        assert selected is not None
        context_payload, context_record = _run_alphaxiv(
            executable,
            ("context", "use", "paper", selected["paper_id"]),
            timeout=timeout_value,
            env=env,
            expect_json=False,
            operation="context_use_paper",
        )
        commands.append(context_record)
        if context_payload is None:
            return _paper_unavailable(
                requested_cli=requested_cli,
                resolved_cli=executable,
                query=safe_query,
                reason=str(context_record.get("reason") or "context_unavailable"),
                commands=commands,
                selected_paper=selected,
            )

        shown, show_record = _run_alphaxiv(
            executable,
            ("context", "show", "--json"),
            timeout=timeout_value,
            env=env,
            expect_json=True,
            operation="context_show",
        )
        commands.append(show_record)
        if shown is None:
            return _paper_unavailable(
                requested_cli=requested_cli,
                resolved_cli=executable,
                query=safe_query,
                reason=str(show_record.get("reason") or "context_unavailable"),
                commands=commands,
                selected_paper=selected,
            )
        context_id, context_title = _context_identity(shown)
        if context_id != selected["paper_id"]:
            return _paper_unavailable(
                requested_cli=requested_cli,
                resolved_cli=executable,
                query=safe_query,
                reason="paper_identity_mismatch",
                commands=commands,
                selected_paper=selected,
            )
        if not selected["title"]:
            selected["title"] = context_title
        if not selected["title"]:
            return _paper_unavailable(
                requested_cli=requested_cli,
                resolved_cli=executable,
                query=safe_query,
                reason="incomplete_paper_metadata",
                commands=commands,
                selected_paper=selected,
            )

        summary_payload, summary_record = _run_alphaxiv(
            executable,
            ("paper", "summary", "--json"),
            timeout=timeout_value,
            env=env,
            expect_json=True,
            operation="paper_summary",
        )
        commands.append(summary_record)
        if summary_payload is None:
            return _paper_unavailable(
                requested_cli=requested_cli,
                resolved_cli=executable,
                query=safe_query,
                reason=str(summary_record.get("reason") or "summary_unavailable"),
                commands=commands,
                selected_paper=selected,
            )
        summary = _summary_text(summary_payload)
        if summary is None:
            return _paper_unavailable(
                requested_cli=requested_cli,
                resolved_cli=executable,
                query=safe_query,
                reason="incomplete_summary_response",
                commands=commands,
                selected_paper=selected,
            )

    return {
        "status": "ok",
        "reason": "alphaxiv_summary_verified",
        "analysis_available": True,
        "provider": "alphaxiv_cli",
        "query": safe_query,
        "selection_method": selection_method,
        "selected_paper": selected,
        "evidence": {
            "kind": "alphaxiv_paper_summary",
            "summary": summary,
            "source_operation": "paper_summary",
        },
        "string_similarity_used": False,
        "cli": {
            "requested": requested_cli,
            "resolved": executable,
            "isolated_context": True,
            "commands": commands,
        },
    }


def _safe_eval_report(report: Any) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {"status": "eval_unavailable", "reason": "invalid_candidate_evaluation"}
    result: dict[str, Any] = {
        "status": str(report.get("status") or "eval_unavailable")[:64],
    }
    for key in ("reason", "loss", "factuality", "self_talk", "tone", "grounding", "latency"):
        if key in report and isinstance(report[key], (str, int, float, bool, type(None))):
            result[key] = report[key]
        failed_key = f"{key}_failed"
        if failed_key in report and isinstance(report[failed_key], bool):
            result[failed_key] = report[failed_key]
    return result


def build_exploration_provenance(exploration: Any) -> dict[str, Any]:
    """Run the existing fixed-budget selector over bounded precomputed evals."""
    if not isinstance(exploration, dict):
        return {
            "status": "not_supplied",
            "fixed_budget": {"initial": 0, "consumed": 0, "remaining": 0},
            "candidate_order": [],
            "events": [],
            "stop": {"reason": "not_supplied"},
            "promotion": {"allowed": False, "observed": False},
        }
    budget = exploration.get("budget", 0)
    if isinstance(budget, bool) or not isinstance(budget, int) or not (0 <= budget <= MAX_CANDIDATES):
        return {"status": "invalid_input", "reason": "invalid_budget"}
    raw_candidates = exploration.get("candidates")
    if not isinstance(raw_candidates, list) or len(raw_candidates) > MAX_CANDIDATES:
        return {"status": "invalid_input", "reason": "invalid_candidates"}

    names: list[str] = []
    evaluations: dict[str, dict[str, Any]] = {}
    for raw in raw_candidates:
        if not isinstance(raw, dict):
            return {"status": "invalid_input", "reason": "invalid_candidate"}
        try:
            name = _valid_plain_text(raw.get("name"), limit=128)
        except ValueError:
            return {"status": "invalid_input", "reason": "invalid_candidate_name"}
        if name in evaluations:
            return {"status": "invalid_input", "reason": "duplicate_candidate"}
        names.append(name)
        evaluations[name] = _safe_eval_report(raw.get("evaluation"))

    def evaluate(name: str) -> dict[str, Any]:
        return dict(evaluations.get(name) or {"status": "eval_unavailable", "reason": "missing_candidate"})

    loop = run_fixed_budget_loop(candidates=names, evaluate_fn=evaluate, budget=budget)
    history = loop.get("history") if isinstance(loop.get("history"), list) else []
    events: list[dict[str, Any]] = []
    for index, item in enumerate(history, start=1):
        if not isinstance(item, dict):
            continue
        decision = item.get("decision") if isinstance(item.get("decision"), dict) else {}
        report = item.get("report") if isinstance(item.get("report"), dict) else {}
        events.append(
            {
                "step": index,
                "event": "candidate_evaluated",
                "candidate": str(item.get("candidate") or "")[:128],
                "selection": {
                    "action": str(decision.get("action") or "")[:64],
                    "reason": str(decision.get("reason") or "")[:128],
                },
                "evaluation": _safe_eval_report(report),
            }
        )
    stop_reason = str(loop.get("reason") or "")[:128]
    events.append(
        {
            "step": len(history) + 1,
            "event": "stop",
            "reason": stop_reason,
        }
    )
    consumed = len(history)
    promoted = bool(loop.get("promoted", False))
    return {
        "status": "policy_violation" if promoted else str(loop.get("status") or "stopped"),
        "fixed_budget": {
            "initial": budget,
            "consumed": consumed,
            "remaining": max(0, budget - consumed),
        },
        "candidate_order": names,
        "events": events,
        "stop": {"reason": stop_reason},
        "promotion": {"allowed": False, "observed": promoted},
    }


def build_dpo_provenance(dpo: Any) -> dict[str, Any]:
    """Evaluate preference pairs only from response-token log probabilities."""
    if not isinstance(dpo, dict):
        return {
            "status": DPO_EVAL_UNAVAILABLE,
            "reason": "not_supplied",
            "evaluated": 0,
            "unavailable": 0,
            "mean_loss": None,
            "method": "response_token_logprob",
            "string_similarity_used": False,
            "pairs": [],
        }
    pairs = dpo.get("pairs")
    if not isinstance(pairs, list) or len(pairs) > MAX_DPO_PAIRS:
        return {
            "status": "invalid_input",
            "reason": "invalid_pairs",
            "method": "response_token_logprob",
            "string_similarity_used": False,
            "pairs": [],
        }
    try:
        tokenizer_id = _valid_plain_text(dpo.get("tokenizer_id"), limit=256, allow_empty=True)
        base_model = _valid_plain_text(dpo.get("base_model"), limit=512, allow_empty=True)
        beta = float(dpo.get("beta", 0.1))
    except (ValueError, TypeError):
        return {
            "status": "invalid_input",
            "reason": "invalid_dpo_metadata",
            "method": "response_token_logprob",
            "string_similarity_used": False,
            "pairs": [],
        }
    if not (0.0 < beta <= 10.0):
        return {
            "status": "invalid_input",
            "reason": "invalid_beta",
            "method": "response_token_logprob",
            "string_similarity_used": False,
            "pairs": [],
        }
    if any(not isinstance(pair, dict) for pair in pairs):
        return {
            "status": "invalid_input",
            "reason": "invalid_pair",
            "method": "response_token_logprob",
            "string_similarity_used": False,
            "pairs": [],
        }

    evaluated = evaluate_preference_pairs(
        pairs,
        tokenizer_id=tokenizer_id,
        base_model=base_model,
        beta=beta,
    )
    pair_reports: list[dict[str, Any]] = []
    for report in evaluated.get("pairs", []):
        if not isinstance(report, dict):
            continue
        pair_reports.append(
            {
                "pair_id": str(report.get("pair_id") or "")[:128],
                "source": str(report.get("source") or "")[:128],
                "status": str(report.get("status") or DPO_EVAL_UNAVAILABLE)[:64],
                "reason": str(report.get("reason") or "")[:128],
                "loss": report.get("loss") if isinstance(report.get("loss"), (int, float)) else None,
            }
        )
    result = {
        "status": str(evaluated.get("status") or DPO_EVAL_UNAVAILABLE),
        "evaluated": int(evaluated.get("evaluated") or 0),
        "unavailable": int(evaluated.get("unavailable") or 0),
        "mean_loss": evaluated.get("mean_loss"),
        "tokenizer_id": tokenizer_id,
        "base_model": base_model,
        "beta": beta,
        "method": "response_token_logprob",
        "string_similarity_used": False,
        "pairs": pair_reports,
    }
    if result["status"] == DPO_EVAL_UNAVAILABLE:
        result["reason"] = "missing_logprobs" if pairs else "no_pairs"
    return result


def build_runtime_model_probe_provenance(probe: Any) -> dict[str, Any]:
    """Record bounded caller-supplied probe evidence without reusing stale success."""
    base = {
        "status": "not_supplied",
        "source": "none",
        "freshness": "unknown",
        "stale_success_reused": False,
        "probe_executed_by_report": False,
        "model_switch_performed": False,
    }
    if probe is None:
        return base
    if not isinstance(probe, dict):
        return {**base, "status": "invalid_input", "reason": "invalid_runtime_model_probe"}

    try:
        gateway = _valid_plain_text(probe.get("gateway"), limit=2048)
        model = _valid_plain_text(probe.get("model"), limit=512)
        result = _valid_plain_text(probe.get("result"), limit=64)
        observed_date = _valid_plain_text(
            probe.get("observed_date"), limit=32, allow_empty=True
        )
        latency_ms = int(probe.get("latency_ms"))
        timeout_ms = int(probe.get("timeout_ms"))
    except (TypeError, ValueError):
        return {**base, "status": "invalid_input", "reason": "invalid_runtime_model_probe"}
    if latency_ms < 0 or timeout_ms <= 0 or latency_ms > timeout_ms:
        return {**base, "status": "invalid_input", "reason": "invalid_probe_timing"}

    raw_advertised = probe.get("advertised_models", [])
    if not isinstance(raw_advertised, list) or len(raw_advertised) > MAX_RUNTIME_MODELS:
        return {**base, "status": "invalid_input", "reason": "invalid_advertised_models"}
    advertised: list[dict[str, Any]] = []
    for item in raw_advertised:
        if not isinstance(item, dict):
            return {**base, "status": "invalid_input", "reason": "invalid_advertised_model"}
        try:
            item_model = _valid_plain_text(item.get("model"), limit=512)
            state = _valid_plain_text(item.get("state"), limit=64)
        except ValueError:
            return {**base, "status": "invalid_input", "reason": "invalid_advertised_model"}
        loaded = item.get("loaded")
        if not isinstance(loaded, bool):
            return {**base, "status": "invalid_input", "reason": "invalid_advertised_model"}
        advertised.append({"model": item_model, "loaded": loaded, "state": state})

    return {
        "status": "observed",
        "source": "caller_supplied_current_observation",
        "freshness": "current_observation",
        "observed_date": observed_date,
        "gateway": gateway,
        "model": model,
        "result": result,
        "latency_ms": latency_ms,
        "timeout_ms": timeout_ms,
        "advertised_models": advertised,
        "stale_success_reused": False,
        "probe_executed_by_report": False,
        "model_switch_performed": False,
    }


def build_provenance_report(
    *,
    paper_analysis: dict[str, Any],
    exploration: Any = None,
    dpo: Any = None,
    runtime_model_probe: Any = None,
) -> dict[str, Any]:
    dream = build_exploration_provenance(exploration)
    preference = build_dpo_provenance(dpo)
    runtime_probe = build_runtime_model_probe_provenance(runtime_model_probe)
    status = "ok" if paper_analysis.get("status") == "ok" else "paper_unavailable"
    if (
        dream.get("status") in {"invalid_input", "policy_violation"}
        or preference.get("status") == "invalid_input"
        or runtime_probe.get("status") == "invalid_input"
    ):
        status = "input_invalid"
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "kind": "dream_rsi_research_provenance",
        "generated_at_unix": int(time.time()),
        "status": status,
        "paper_analysis": paper_analysis,
        "dream_rsi_exploration": dream,
        "dpo_preference_evaluation": preference,
        "runtime_model_probe": runtime_probe,
        "model_policy": {
            "automatic_promotion": False,
            "automatic_replacement": False,
        },
    }


def _read_bounded_stdin(enabled: bool) -> dict[str, Any]:
    if not enabled:
        return {}
    raw = sys.stdin.buffer.read(MAX_INPUT_JSON_BYTES + 1)
    if len(raw) > MAX_INPUT_JSON_BYTES:
        raise ValueError("input_too_large")
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("invalid_input_json") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid_input_shape")
    return payload


def _emit_bounded_json(payload: dict[str, Any]) -> bool:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    encoded = rendered.encode("utf-8")
    if len(encoded) > MAX_REPORT_JSON_BYTES:
        fallback = {
            "schema_version": REPORT_SCHEMA_VERSION,
            "kind": "dream_rsi_research_provenance",
            "status": "report_unavailable",
            "reason": "report_too_large",
        }
        print(json.dumps(fallback, ensure_ascii=False, indent=2, sort_keys=True))
        return False
    print(rendered)
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build fail-closed DREAM-RSI paper/exploration/DPO provenance JSON."
    )
    parser.add_argument("--query", default=DEFAULT_QUERY, help="alphaXiv paper search query")
    parser.add_argument(
        "--paper-id",
        default=DREAM_RSI_PAPER_ID,
        help=f"exact arXiv paper id (default: DREAM-RSI {DREAM_RSI_PAPER_ID})",
    )
    parser.add_argument(
        "--alphaxiv-cli",
        default="alphaxiv",
        help="bare alphaxiv executable name or absolute executable path",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--provenance-stdin",
        action="store_true",
        help="read bounded exploration/DPO JSON from stdin",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = _read_bounded_stdin(args.provenance_stdin)
    except ValueError as exc:
        _emit_bounded_json(
            {
                "schema_version": REPORT_SCHEMA_VERSION,
                "kind": "dream_rsi_research_provenance",
                "status": "input_invalid",
                "reason": str(exc),
                "model_policy": {
                    "automatic_promotion": False,
                    "automatic_replacement": False,
                },
            }
        )
        return 2

    paper = collect_paper_report(
        args.query,
        alphaxiv_cli=args.alphaxiv_cli,
        timeout=args.timeout,
        paper_id=args.paper_id,
    )
    report = build_provenance_report(
        paper_analysis=paper,
        exploration=payload.get("exploration"),
        dpo=payload.get("dpo"),
        runtime_model_probe=payload.get("runtime_model_probe"),
    )
    emitted = _emit_bounded_json(report)
    return 0 if emitted and report["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
