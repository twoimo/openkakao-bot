//! Property-based tests for the redacted pipeline-logging store and, more
//! broadly, for the redaction invariant that must hold across *every*
//! persistence table the live-ops feature stores records in.
//!
//! * Correctness Property 4 (redacted logging): for arbitrary pipeline-flow
//!   inputs, every record the `pipeline_events` store persists is free of raw
//!   chat bodies, prompts, URLs, and absolute paths.
//! * Correctness Property 5 (all-tables redaction invariant): for arbitrary
//!   processing flows, no marker string planted in a chat body / generated
//!   answer / prompt, and no URL, absolute path, or account-identifying string,
//!   ever appears in *any column* of `pipeline_events` (including the new
//!   `images_source` / `images_delivered` columns), `experiment_result`,
//!   `live_sample`, `problem_signal`, `improve_history`, `knob_generation`,
//!   `relay_ledger`, or `forward_ledger`. An independent, table-name-driven
//!   scanner reads back whatever tables actually exist and asserts the
//!   invariant over all of their columns, so the test stays valid as later
//!   tasks add the tables that do not exist yet.
//!
//! Validates: Requirements R2.8, R2.10, R3.11, R5.8, R6.11, R7.16, R8.16,
//! R11.8, R12.4

use openkakao_cli::experiment::{
    ExperimentRunner, ModelId, Provider, SqliteExperimentStore, StyleExperimentRunner, StyleTarget,
};
use openkakao_cli::fakes::{FakeProvider, ScriptedOutcome};
use openkakao_cli::logging::{
    FlowKind, HistoryStore, PipelineEvent, SqliteHistoryStore, Stage, StageStatus,
};
use proptest::prelude::*;
use rusqlite::{Connection, OptionalExtension};
use std::path::Path;

/// Sensitive fragments deliberately injected into arbitrary inputs. If any of
/// these ever appears verbatim in a stored record, redaction failed.
const SENSITIVE_FRAGMENTS: &[&str] = &[
    "https://evil.example.com/leak?token=abc",
    "http://internal.host/path",
    "www.tracker.example",
    "/Users/owner/Library/kakao.sqlite3",
    "/private/var/secret",
    "안녕하세요 이건 원문 채팅 본문입니다",
    "SYSTEM PROMPT: reply exactly as owner",
    "account_fp=owner-fingerprint-1234",
];

/// Independent URL scanner (kept separate from the store's own logic).
fn scanner_finds_url(text: &str) -> bool {
    let lower = text.to_ascii_lowercase();
    lower.contains("http://")
        || lower.contains("https://")
        || lower.contains("www.")
        || lower.contains("://")
}

/// Independent absolute-path scanner: any whitespace token that is an absolute
/// path, or the whole string being one.
fn scanner_finds_absolute_path(text: &str) -> bool {
    if Path::new(text).is_absolute() {
        return true;
    }
    text.split_whitespace()
        .any(|token| Path::new(token).is_absolute())
}

/// Assert a single stored record carries no sensitive content.
fn assert_record_is_redacted(ev: &PipelineEvent) {
    for field in [ev.trace_id.as_str(), ev.result_code.as_str()] {
        assert!(
            !scanner_finds_url(field),
            "stored record leaked a URL: {field:?}"
        );
        assert!(
            !scanner_finds_absolute_path(field),
            "stored record leaked an absolute path: {field:?}"
        );
        assert!(
            !field.contains('\n') && !field.contains('\r'),
            "stored record leaked multi-line raw text: {field:?}"
        );
        for fragment in SENSITIVE_FRAGMENTS {
            assert!(
                !field.contains(fragment),
                "stored record leaked a sensitive fragment {fragment:?}: {field:?}"
            );
        }
    }
}

/// A free-text field that is sometimes a benign short code and sometimes a
/// hostile value carrying a URL, absolute path, or raw body/prompt.
fn payload_strategy() -> impl Strategy<Value = String> {
    prop_oneof![
        // Benign short codes (what real instrumentation emits).
        prop::string::string_regex("[a-z][a-z0-9_]{0,20}").expect("valid regex"),
        // A sensitive fragment, verbatim.
        (0usize..SENSITIVE_FRAGMENTS.len()).prop_map(|i| SENSITIVE_FRAGMENTS[i].to_string()),
        // A benign prefix glued to a sensitive fragment.
        (
            prop::string::string_regex("[a-z]{0,8}").expect("valid regex"),
            0usize..SENSITIVE_FRAGMENTS.len(),
        )
            .prop_map(|(prefix, i)| format!("{prefix} {}", SENSITIVE_FRAGMENTS[i])),
        // Arbitrary text that may include newlines and long bodies.
        prop::string::string_regex("[\\PC\\s]{0,120}").expect("valid regex"),
    ]
}

fn flow_strategy() -> impl Strategy<Value = FlowKind> {
    prop_oneof![Just(FlowKind::AutoReply), Just(FlowKind::GeekNews)]
}

fn stage_strategy() -> impl Strategy<Value = Stage> {
    prop_oneof![
        Just(Stage::Detect),
        Just(Stage::Authorize),
        Just(Stage::Retrieve),
        Just(Stage::Model),
        Just(Stage::Schedule),
        Just(Stage::PreSend),
        Just(Stage::Commit),
    ]
}

fn status_strategy() -> impl Strategy<Value = StageStatus> {
    prop_oneof![
        Just(StageStatus::InProgress),
        Just(StageStatus::Success),
        Just(StageStatus::Failed),
    ]
}

fn event_strategy() -> impl Strategy<Value = PipelineEvent> {
    (
        payload_strategy(),
        flow_strategy(),
        stage_strategy(),
        status_strategy(),
        payload_strategy(),
        0u64..1_000_000,
        0i64..2_000_000_000_000,
    )
        .prop_map(
            |(trace_id, flow, stage, status, result_code, duration_ms, at)| PipelineEvent {
                trace_id,
                flow,
                stage,
                status,
                result_code,
                duration_ms,
                at,
            },
        )
}

proptest! {
    /// Correctness Property 4: no matter what arbitrary flow inputs are fed to
    /// the store (including URLs, absolute paths, and raw bodies/prompts),
    /// every record that ends up persisted is fully redacted.
    #[test]
    fn stored_records_never_contain_raw_content(events in prop::collection::vec(event_strategy(), 0..24)) {
        let store = SqliteHistoryStore::open_in_memory().expect("open in-memory store");

        // Feed every event. Some are refused (fail-closed) — that is expected
        // and fine; we only assert about what actually gets stored.
        for ev in events {
            let _ = store.append(ev);
        }

        let stored = store.recent(10_000).expect("recent");
        for ev in &stored {
            assert_record_is_redacted(ev);
        }
    }
}

// ---------------------------------------------------------------------------
// Correctness Property 5: the redaction invariant over *all* stored tables.
// ---------------------------------------------------------------------------

/// Every table Property 5 requires to stay free of raw content. The scanner
/// asserts over whichever of these actually exist in the database, so tables
/// that belong to later tasks (and do not exist yet) are transparently skipped
/// — and are picked up automatically once those tasks create them.
///
/// Present today: `pipeline_events` (Task 2.1, incl. the new `images_source` /
/// `images_delivered` columns) and `experiment_result` (existing). Deferred:
/// `live_sample` (Task 4), `problem_signal` / `improve_history` /
/// `knob_generation` (Task 9), `relay_ledger` (Task 8), `forward_ledger`
/// (Task 7.1).
const REDACTED_TABLES: &[&str] = &[
    "pipeline_events",
    "experiment_result",
    "live_sample",
    "problem_signal",
    "improve_history",
    "knob_generation",
    "relay_ledger",
    "forward_ledger",
];

/// True when `name` is a real table in `conn`. Missing tables are skipped so
/// the scanner remains valid before the later tasks create them.
fn table_exists(conn: &Connection, name: &str) -> bool {
    conn.query_row(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?1",
        [name],
        |_| Ok(()),
    )
    .optional()
    .expect("query sqlite_master")
    .is_some()
}

/// The column names of `table`, in definition order.
fn column_names(conn: &Connection, table: &str) -> Vec<String> {
    let mut stmt = conn
        .prepare(&format!("SELECT name FROM pragma_table_info('{table}')"))
        .expect("prepare pragma_table_info");
    let names = stmt
        .query_map([], |row| row.get::<_, String>(0))
        .expect("query pragma_table_info")
        .map(|r| r.expect("column name row"))
        .collect();
    names
}

/// Render any stored cell as the text an auditor (or a leak) would see. Numbers
/// and NULLs cannot carry a raw body/URL/path, but they are rendered anyway so
/// the scanner covers *every* column without knowing a table's types up front.
fn cell_to_text(value: &rusqlite::types::Value) -> String {
    use rusqlite::types::Value;
    match value {
        Value::Null => String::new(),
        Value::Integer(i) => i.to_string(),
        Value::Real(r) => r.to_string(),
        Value::Text(t) => t.clone(),
        Value::Blob(b) => String::from_utf8_lossy(b).into_owned(),
    }
}

/// Assert one stored cell carries none of the forbidden shapes: a planted
/// sensitive marker (raw body / answer / prompt / account id), a URL, or an
/// absolute path.
fn assert_cell_is_redacted(text: &str, table: &str, column: &str) {
    assert!(
        !scanner_finds_url(text),
        "table `{table}` column `{column}` leaked a URL: {text:?}"
    );
    assert!(
        !scanner_finds_absolute_path(text),
        "table `{table}` column `{column}` leaked an absolute path: {text:?}"
    );
    for fragment in SENSITIVE_FRAGMENTS {
        assert!(
            !text.contains(fragment),
            "table `{table}` column `{column}` leaked a sensitive fragment \
             {fragment:?}: {text:?}"
        );
    }
}

/// Scan one table's every column of every row and assert redaction.
fn scan_table(conn: &Connection, table: &str) {
    let columns = column_names(conn, table);
    if columns.is_empty() {
        return;
    }
    let mut stmt = conn
        .prepare(&format!("SELECT * FROM \"{table}\""))
        .expect("prepare table scan");
    let mut rows = stmt.query([]).expect("query table scan");
    while let Some(row) = rows.next().expect("scan row") {
        for (idx, column) in columns.iter().enumerate() {
            let value: rusqlite::types::Value = row.get(idx).expect("read cell");
            assert_cell_is_redacted(&cell_to_text(&value), table, column);
        }
    }
}

/// Table-name-driven scan of every present target table. Absent tables (later
/// tasks) are skipped.
fn scan_all_redacted_tables(conn: &Connection) {
    for &table in REDACTED_TABLES {
        if table_exists(conn, table) {
            scan_table(conn, table);
        }
    }
}

proptest! {
    /// Correctness Property 5: for arbitrary processing-flow inputs, no marker
    /// string, URL, absolute path, or account identifier appears in any column
    /// of any present persistence table.
    ///
    /// Two persistence layers are exercised against one shared database:
    ///
    /// * `pipeline_events` is fed arbitrary events (some carrying URLs, paths,
    ///   raw bodies, prompts, and account ids). The store fails closed on the
    ///   hostile ones and stores only redacted records — so the scan of its
    ///   columns, including the new `images_source` / `images_delivered`
    ///   columns, must be clean.
    /// * `experiment_result` is populated by the real experiment runner driven
    ///   by a fake provider whose generated answer *always* embeds a sensitive
    ///   fragment. The runner records only redacted metrics and never the
    ///   answer text, so the scan of `experiment_result` must be clean too.
    ///
    /// The remaining tables do not exist yet; the scanner skips them. When
    /// their tasks land, the same scan will cover them without changes here.
    #[test]
    fn all_present_tables_stay_redacted(
        events in prop::collection::vec(event_strategy(), 0..24),
        answer_fragment in 0usize..SENSITIVE_FRAGMENTS.len(),
        answer_extra in payload_strategy(),
    ) {
        let dir = tempfile::tempdir().expect("tempdir");
        let db_path = dir.path().join("live_ops.sqlite3");

        // 1) pipeline_events — redaction enforced before every write.
        {
            let store = SqliteHistoryStore::open(&db_path).expect("open history store");
            for ev in events {
                let _ = store.append(ev);
            }
        }

        // 2) experiment_result — the runner persists only redacted metrics.
        //    The fake provider's answer always carries a sensitive fragment, so
        //    a regression that ever persisted answer text would be caught.
        {
            let answer = format!("{} {answer_extra}", SENSITIVE_FRAGMENTS[answer_fragment]);
            let providers: Vec<Box<dyn Provider>> = vec![Box::new(FakeProvider::new(
                "gjc",
                vec![ModelId::new("m1")],
                120,
                ScriptedOutcome::Reply(answer),
            ))];
            let runner = StyleExperimentRunner::with_defaults(StyleTarget::default());
            let results = runner.run("v1", &providers).expect("run experiment");
            let mut store =
                SqliteExperimentStore::open(&db_path).expect("open experiment store");
            store.record(&results).expect("record experiment results");
        }

        // 3) Scan every present target table's every column.
        let conn = Connection::open(&db_path).expect("open scan connection");
        scan_all_redacted_tables(&conn);
    }
}
