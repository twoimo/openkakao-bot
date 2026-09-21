# DREAM-RSI alphaXiv provenance

`scripts/dream_rsi_alphaxiv.py` keeps paper research separate from the existing
DREAM-RSI replay score and DPO preference evaluation. It does not train,
promote, replace, or start an operating model.

## alphaXiv CLI boundary

The integration targets the `alphaxiv` CLI and its stable JSON output. A bare
name is resolved through `PATH`; `--alphaxiv-cli` may also point to one absolute
executable path. Relative executable paths such as `../alphaxiv` are rejected.
All subprocess calls use an argv list with `shell=False`, a finite timeout, and
an isolated temporary `ALPHAXIV_HOME` so selecting a paper does not change the
user's saved alphaXiv context.

For a search request the read-only flow is:

```text
alphaxiv search papers <query> --json
alphaxiv context use paper <paper-id>
alphaxiv context show --json
alphaxiv paper summary --json
```

The CLI entry point defaults to the intended DREAM-RSI paper id
`2609.14858`, so its normal path starts with `context use paper 2609.14858`
instead of selecting search rank 1. The Python `collect_paper_report()` API
keeps `paper_id=None` as its generic search contract for callers that
explicitly need search behavior.

The first ranked alphaXiv search result is recorded as the selection method.
No local string-similarity score selects or substantiates a paper. The
`paper_analysis.evidence` field is populated only when alphaXiv confirms the
same paper id in context and returns a non-empty JSON summary. Missing CLI,
timeout, non-zero exit, oversized/invalid JSON, identity mismatch, or an
incomplete summary returns `paper_analysis.status="unavailable"` with
`evidence=null`.

As verified on 2026-09-21 in this repository environment, the persistent
`PATH` has no alphaXiv CLI that implements the paper-analysis commands above.
PyPI `alphaxiv==0.0.13` is instead a TTY-based W&B experiment synchronization
CLI and provides none of the required `search`, `context`, or `paper summary`
commands. `orx` is installed, but it is a different openresearch CLI and is not
used as a substitute. Therefore `scripts/dream_rsi_alphaxiv.py` continues to
fail closed with `paper_analysis.status="unavailable"` and `evidence=null`; no
successful alphaXiv paper analysis is claimed for the current machine state.

## Provenance report schema

The command prints one bounded JSON document to stdout. It accepts no report
output path, so it cannot traverse a caller-selected filesystem path. Optional
exploration/DPO input is accepted from stdin only with `--provenance-stdin` and
is capped at 256 KiB; alphaXiv JSON and final report sizes are capped as well.

Top-level fields:

- `paper_analysis`: alphaXiv query, selected paper identity, exact provider
  summary evidence, and per-command status.
- `dream_rsi_exploration`: candidate order, every continue/branch selection,
  evaluation status, explicit stop reason, and initial/consumed/remaining
  fixed budget. `promotion.allowed` is always `false`.
- `dpo_preference_evaluation`: standard DPO evaluation from response-token
  log probabilities. If chosen/rejected token log probabilities are absent,
  the status is `eval_unavailable`; string similarity is never substituted.
- `runtime_model_probe`: caller-supplied current probe evidence only. The
  report command does not contact the MLX gateway. Missing input is
  `status="not_supplied"`; supplied evidence is marked
  `source="caller_supplied_current_observation"`,
  `freshness="current_observation"`, `stale_success_reused=false`, and
  `probe_executed_by_report=false` so an old persisted success cannot be
  presented as a current probe.
- `model_policy`: `automatic_promotion=false` and
  `automatic_replacement=false`.

Current probe evidence supplied for 2026-09-21: the existing MLX gateway at
`http://127.0.0.1:11234/v1` returned `OK` from Qwen3.8 Flash-Next in **8211 ms**
with a **90000 ms** maximum timeout. Qwen3.8 27B was advertised with
`loaded=false` and `state=unloaded`; no model switch was performed. This is
recorded as current external probe evidence, not as a probe executed by this
reporter and not as reuse of a prior/stale success.

Example paper-only invocation:

```bash
python3 scripts/dream_rsi_alphaxiv.py \
  --alphaxiv-cli /absolute/path/to/alphaxiv
```

That invocation targets arXiv `2609.14858` by default. An explicit
`--paper-id <arXiv-id>` overrides the CLI target while retaining the same
identity-mismatch fail-closed check.

Example bounded provenance input:

```json
{
  "exploration": {
    "budget": 2,
    "candidates": [
      {"name": "policy-a", "evaluation": {"status": "ok", "factuality": false}},
      {"name": "policy-b", "evaluation": {"status": "eval_unavailable"}}
    ]
  },
  "dpo": {
    "tokenizer_id": "tokenizer-id",
    "base_model": "base-model-id",
    "pairs": [
      {
        "pair_id": "p1",
        "source": "human_authored",
        "chosen_logprobs": [-0.1, -0.2],
        "rejected_logprobs": [-1.0, -0.8]
      }
    ]
  },
  "runtime_model_probe": {
    "observed_date": "2026-09-21",
    "gateway": "http://127.0.0.1:11234/v1",
    "model": "mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit",
    "result": "OK",
    "latency_ms": 8211,
    "timeout_ms": 90000,
    "advertised_models": [
      {
        "model": "mlx/ddalcu/Qwen3.8-27B-MLX-Serve-4bit",
        "loaded": false,
        "state": "unloaded"
      }
    ]
  }
}
```

Pipe that JSON to the script with `--provenance-stdin`. A non-zero process exit
means the paper portion could not be verified or the report input was invalid;
the emitted JSON still carries the fail-closed reason.
