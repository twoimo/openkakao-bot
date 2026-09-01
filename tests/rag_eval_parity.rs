//! RAG-evaluation parity test (Task 13, R11.1/R11.10).
//!
//! Proves the Rust port in `openkakao_cli::rag_eval` produces results identical
//! to the Python `scripts/rag_eval.py` for the same inputs. The fixtures under
//! `tests/fixtures/parity/` were captured by running the *real* Python module
//! over a fixed record set that exercises every branch (recent/non-recent
//! citations, missing model keys, empty windows, duplicate ids, empty rerank
//! scores, and terminal-status joins).
//!
//! Ordered maps (Python `Counter.most_common()` / `sorted`) are captured as
//! ordered `[key, value]` pair-lists, so this test checks **ordering** parity
//! (the "정렬" half of R11.10), not just values.

use std::path::PathBuf;

use openkakao_cli::rag_eval;
use serde_json::{json, Value};

fn fixture(name: &str) -> Value {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parity")
        .join(name);
    let raw = std::fs::read_to_string(&path)
        .unwrap_or_else(|e| panic!("read fixture {}: {e}", path.display()));
    serde_json::from_str(&raw).expect("fixture is valid JSON")
}

fn pairs_value(pairs: &[(String, u64)]) -> Value {
    Value::Array(pairs.iter().map(|(k, v)| json!([k, v])).collect())
}

#[test]
fn rust_rag_eval_matches_python_golden() {
    let input = fixture("rag_eval_input.json");
    let golden = fixture("rag_eval_golden.json");

    let records: Vec<Value> = input
        .get("records")
        .and_then(Value::as_array)
        .expect("records array")
        .clone();
    let status = rag_eval::status_map_from_value(
        input.get("status_by_event").expect("status_by_event"),
    );

    // citation_metrics: unrounded (recall, utilization) per record.
    let citation_metrics: Vec<Value> = records
        .iter()
        .map(|record| {
            let (recall, util) = rag_eval::citation_metrics(record);
            json!([recall, util])
        })
        .collect();

    let per_model: Vec<Value> = rag_eval::per_model(&records, &status)
        .into_iter()
        .map(|(name, metrics)| json!([name, metrics]))
        .collect();

    let actual = json!({
        "citation_metrics": citation_metrics,
        "evidence_composition": pairs_value(&rag_eval::evidence_composition(&records)),
        "citation_positions": rag_eval::citation_position_analysis(&records),
        "cohort": rag_eval::cohort_metrics(&records, &status),
        "per_model": per_model,
    });

    assert_eq!(
        actual, golden,
        "Rust rag_eval output must match the Python golden fixture exactly"
    );
}
