//! History-window and memory-window display-connection tests (Task 12.2).
//!
//! These verify the plain-language screen models the SwiftUI shell renders,
//! wired to the real redacted journal ([`openkakao_cli::logging`]) and the real
//! conversation-memory store ([`openkakao_cli::memory`]) — both opened
//! in-memory, so no Kakao database, network, or AX call is involved.
//!
//! History window (R2.3, R2.4, R2.6, R2.7):
//! * newest-first plain-language rows,
//! * a newly appended stage shows up in the list,
//! * an empty journal and a lookup failure each produce a beginner-friendly
//!   notice while preserving stored records.
//!
//! Memory window (R8.1–R8.3, R8.5, R8.8):
//! * lists conversation-memory notes, reference material, and RAG comparison
//!   results,
//! * a deletion runs only after confirmation, with a plain-language prompt.
//!
//! Validates: Requirements R1.3, R2.3, R2.4, R2.6, R2.7, R8.1, R8.2, R8.3,
//! R8.5, R8.8, R10.3

use openkakao_cli::logging::{
    FlowKind, HistoryStore, PipelineEvent, Stage, StageStatus, StoreError,
};
use openkakao_cli::logging::SqliteHistoryStore;
use openkakao_cli::memory::{
    MemoryDraft, MemoryError, MemoryItem, MemoryKind, MemoryStore, RagComparisonRow,
    SqliteMemoryStore,
};
use openkakao_cli::ui_shell::{
    delete_confirmation_message, history_view, memory_view, perform_delete, HistoryView,
    PendingDelete, HISTORY_APPEND_DEADLINE_MS, HISTORY_INITIAL_DEADLINE_MS, HISTORY_WINDOW_LIMIT,
    MEMORY_OPEN_DEADLINE_MS,
};
use rusqlite::Connection;

fn stage_event(stage: Stage, status: StageStatus, at: i64) -> PipelineEvent {
    PipelineEvent {
        trace_id: "flow".to_string(),
        flow: FlowKind::AutoReply,
        stage,
        status,
        result_code: "ok".to_string(),
        duration_ms: 5,
        at,
    }
}

/// R2.3/R2.7: opening the window shows the recorded stages newest-first in plain
/// Korean, and the initial-display deadline is within three seconds.
#[test]
fn history_shows_recorded_stages_newest_first() {
    assert!(HISTORY_INITIAL_DEADLINE_MS <= 3_000);
    let store = SqliteHistoryStore::open_in_memory().expect("store");
    store
        .append(stage_event(Stage::Detect, StageStatus::Success, 1_000))
        .expect("detect");
    store
        .append(stage_event(Stage::Authorize, StageStatus::Success, 2_000))
        .expect("authorize");

    match history_view(&store, HISTORY_WINDOW_LIMIT) {
        HistoryView::Rows(rows) => {
            assert_eq!(rows.len(), 2);
            assert!(rows[0].contains("보낼 수 있는지 확인"), "newest first: {rows:?}");
            assert!(rows[1].contains("메시지 확인"));
            assert!(rows.iter().all(|r| r.contains("자동 답변")));
        }
        other => panic!("expected rows, got {other:?}"),
    }
}

/// R2.4: a newly occurred stage is appended to the list; the append deadline is
/// within five seconds.
#[test]
fn history_appends_a_new_stage() {
    assert!(HISTORY_APPEND_DEADLINE_MS <= 5_000);
    let store = SqliteHistoryStore::open_in_memory().expect("store");
    store
        .append(stage_event(Stage::Detect, StageStatus::Success, 1_000))
        .expect("detect");

    let before = match history_view(&store, HISTORY_WINDOW_LIMIT) {
        HistoryView::Rows(rows) => rows.len(),
        other => panic!("expected rows, got {other:?}"),
    };
    assert_eq!(before, 1);

    // A new stage occurs and is journaled.
    store
        .append(stage_event(Stage::Commit, StageStatus::Success, 3_000))
        .expect("commit");

    match history_view(&store, HISTORY_WINDOW_LIMIT) {
        HistoryView::Rows(rows) => {
            assert_eq!(rows.len(), 2, "new stage appended (R2.4)");
            assert!(rows[0].contains("보내기 완료"));
        }
        other => panic!("expected rows, got {other:?}"),
    }
}

/// R2.5: an empty journal produces a plain-language empty notice.
#[test]
fn history_empty_shows_plain_notice() {
    let store = SqliteHistoryStore::open_in_memory().expect("store");
    match history_view(&store, HISTORY_WINDOW_LIMIT) {
        HistoryView::Empty(msg) => {
            assert!(msg.contains("기록이 없"));
            assert!(!msg.is_empty());
        }
        other => panic!("expected empty notice, got {other:?}"),
    }
}

/// A `HistoryStore` whose lookup always fails, without deleting anything, to
/// drive the query-failure path (R2.6).
struct FailingHistoryStore;

impl HistoryStore for FailingHistoryStore {
    fn recent(&self, _limit: usize) -> Result<Vec<PipelineEvent>, StoreError> {
        Err(StoreError::Malformed("simulated lookup failure".to_string()))
    }
    fn append(&self, _ev: PipelineEvent) -> Result<(), StoreError> {
        Ok(())
    }
}

/// R2.6: a lookup failure yields a plain-language notice and signals the caller
/// to keep the previously shown rows (stored records are not deleted).
#[test]
fn history_lookup_failure_shows_plain_notice_and_preserves() {
    let store = FailingHistoryStore;
    match history_view(&store, HISTORY_WINDOW_LIMIT) {
        HistoryView::Failed(msg) => {
            assert!(msg.contains("불러오지 못"));
            assert!(msg.contains("그대로"), "must reassure records are preserved (R2.6)");
        }
        other => panic!("expected failure notice, got {other:?}"),
    }
}

/// R8.1/R8.2/R8.3: opening the memory window lists notes, reference material,
/// and RAG comparison results together, within the three-second deadline.
#[test]
fn memory_window_lists_notes_references_and_comparisons() {
    assert!(MEMORY_OPEN_DEADLINE_MS <= 3_000);

    // A store that has a note, a reference pack, and one comparison row. The
    // reference and comparison tables are the ones the real schema uses; they
    // are seeded on a temp-file database through a raw connection first, then
    // the memory store opens the same file (matching the store's own tests).
    let dir = tempfile::tempdir().expect("tempdir");
    let db_path = dir.path().join("context.sqlite3");
    seed_reference_pack(&db_path, "코인 설명", 42);
    seed_comparison(&db_path);

    let store = SqliteMemoryStore::open(&db_path).expect("store");
    store
        .upsert(MemoryDraft::new_note("사용자 메모"))
        .expect("note");

    let view = memory_view(&store).expect("memory view");
    assert_eq!(view.notes.len(), 1);
    assert_eq!(view.notes[0].text, "사용자 메모");
    assert_eq!(view.references.len(), 1);
    assert_eq!(view.references[0].text, "코인 설명");
    assert_eq!(view.references[0].kind, MemoryKind::ReferencePack);
    assert_eq!(view.comparisons.len(), 1);
    let cmp: &RagComparisonRow = &view.comparisons[0];
    assert_eq!(cmp.quality_score, 80);
}

/// R8.5/R8.8: deleting a memory item shows a plain-language confirmation and
/// only removes the item once the user confirms.
#[test]
fn memory_delete_requires_confirmation() {
    let store = SqliteMemoryStore::open_in_memory().expect("store");
    let item: MemoryItem = store
        .upsert(MemoryDraft::new_note("지울지도 모르는 기억"))
        .expect("note");

    let prompt = delete_confirmation_message(&item);
    assert!(prompt.contains("지울까요"), "plain confirmation (R8.5/R8.8)");
    assert!(prompt.contains("되돌릴 수 없"));

    // The user cancels: nothing is removed.
    assert!(!perform_delete(&store, PendingDelete::new(item.id), false).expect("cancel"));
    assert_eq!(store.list(MemoryKind::Note).expect("list").len(), 1);

    // The user confirms: the item is removed.
    assert!(perform_delete(&store, PendingDelete::new(item.id), true).expect("delete"));
    assert!(store.list(MemoryKind::Note).expect("list").is_empty());
}

/// A store-error path (R8.7 spirit): a failure to build the view surfaces a
/// plain-language message instead of panicking.
#[test]
fn memory_view_failure_is_plain_language() {
    struct FailingMemoryStore;
    impl MemoryStore for FailingMemoryStore {
        fn list(&self, _kind: MemoryKind) -> Result<Vec<MemoryItem>, MemoryError> {
            Err(MemoryError::Malformed("simulated".to_string()))
        }
        fn upsert(&self, _draft: MemoryDraft) -> Result<MemoryItem, MemoryError> {
            Err(MemoryError::Malformed("simulated".to_string()))
        }
        fn delete(&self, _id: i64) -> Result<(), MemoryError> {
            Ok(())
        }
        fn rag_comparisons(&self) -> Result<Vec<RagComparisonRow>, MemoryError> {
            Ok(Vec::new())
        }
    }
    let err = memory_view(&FailingMemoryStore).expect_err("should fail");
    assert!(err.contains("불러오지 못"));
}

// --- helpers that seed the read-only tables the memory view reads ------------

/// Create and populate the `context_reference_packs` table on `db_path` the way
/// the memory store's read-only listing expects it (id, what_text, end_log_id).
fn seed_reference_pack(db_path: &std::path::Path, what_text: &str, end_log_id: i64) {
    let conn = Connection::open(db_path).expect("open context");
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS context_reference_packs(
            id INTEGER PRIMARY KEY,
            what_text TEXT NOT NULL,
            end_log_id INTEGER NOT NULL
        );",
    )
    .expect("create reference table");
    conn.execute(
        "INSERT INTO context_reference_packs(what_text, end_log_id) VALUES (?1, ?2)",
        rusqlite::params![what_text, end_log_id],
    )
    .expect("insert reference pack");
}

/// Create and populate the `experiment_result` table on `db_path` that the RAG
/// comparison listing reads.
fn seed_comparison(db_path: &std::path::Path) {
    let conn = Connection::open(db_path).expect("open context");
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS experiment_result(
            id INTEGER PRIMARY KEY,
            run_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            question_id TEXT NOT NULL,
            latency_ms INTEGER NOT NULL,
            quality_score INTEGER NOT NULL,
            prompt_version TEXT NOT NULL,
            outcome TEXT NOT NULL
        );",
    )
    .expect("create experiment table");
    conn.execute(
        "INSERT INTO experiment_result(
            run_id, provider, model, question_id,
            latency_ms, quality_score, prompt_version, outcome
        ) VALUES ('run-1', 'gjc', 'demo', 'q1', 1200, 80, 'v1', 'ok')",
        [],
    )
    .expect("insert experiment result");
}
