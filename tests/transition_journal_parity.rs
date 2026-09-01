//! Transition-journal event-id gate parity test (Task 13, R11.10).
//!
//! The Python `auto_reply_transition_journal` module remains the runtime
//! authority for the reply queue (schema migration, triggers, and cross-room
//! binding used by the worker/supervisor/db-watch), so it is not removed. Its
//! pure, safety-relevant allow/deny gate — `validated_event_id`, which decides
//! whether a `db:<chat_id>:<log_id>` id is accepted and whether it belongs to
//! the expected room — is ported to `openkakao_cli::logging::validate_event_id`.
//!
//! This test loads allow/deny outcomes captured from the real Python function
//! and asserts the Rust port returns the identical allow/deny result for every
//! case (the "허용·차단" half of R11.10).

use std::path::PathBuf;

use openkakao_cli::logging::{validate_event_id, MAX_INT64};
use serde_json::Value;

fn fixture(name: &str) -> Value {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parity")
        .join(name);
    let raw = std::fs::read_to_string(&path)
        .unwrap_or_else(|e| panic!("read fixture {}: {e}", path.display()));
    serde_json::from_str(&raw).expect("fixture is valid JSON")
}

#[test]
fn event_id_gate_matches_python_golden() {
    let golden = fixture("transition_event_id_golden.json");

    // The Rust bound must be the same MAX_INT64 (2**63 - 1) the Python uses.
    assert_eq!(
        golden.get("max_int64").and_then(Value::as_i64),
        Some(MAX_INT64),
        "MAX_INT64 bound must match the Python transition journal"
    );

    let cases = golden
        .get("cases")
        .and_then(Value::as_array)
        .expect("cases array");
    assert!(!cases.is_empty(), "fixture must contain cases");

    let mut checked = 0usize;
    for case in cases {
        let value = case.get("value").and_then(Value::as_str).expect("value");
        let expected_chat_id = match case.get("expected_chat_id") {
            Some(Value::Null) | None => None,
            Some(v) => Some(v.as_i64().expect("expected_chat_id is an integer")),
        };
        let python_allowed = case
            .get("allowed")
            .and_then(Value::as_bool)
            .expect("allowed flag");

        let rust_allowed = validate_event_id(value, expected_chat_id).is_ok();

        assert_eq!(
            rust_allowed, python_allowed,
            "allow/deny mismatch for value={value:?} expected_chat_id={expected_chat_id:?}: \
             Rust allowed={rust_allowed}, Python allowed={python_allowed}"
        );
        checked += 1;
    }

    // Guard against silently testing nothing.
    assert_eq!(checked, cases.len());
}
