//! Unit tests for the conversation-memory store (R8.2–R8.7).
//!
//! Every test uses an in-memory or temp-file SQLite database, so no live Kakao
//! database or network call is ever made.

use super::*;

use rusqlite::Connection;

use crate::experiment::{ExperimentResult, Outcome, SqliteExperimentStore};

/// A note draft with the given text.
fn note(text: &str) -> MemoryDraft {
    MemoryDraft::new_note(text)
}

#[test]
fn upsert_rejects_empty_value() {
    let store = SqliteMemoryStore::open_in_memory().expect("open");
    for empty in ["", "   ", "\n\t "] {
        let err = store.upsert(note(empty)).expect_err("should reject empty");
        assert!(matches!(err, MemoryError::Empty), "got {err:?}");
    }
    // Nothing was stored.
    assert!(store.list(MemoryKind::Note).expect("list").is_empty());
}

#[test]
fn upsert_rejects_over_limit_value() {
    let store = SqliteMemoryStore::open_in_memory().expect("open");

    // 1000 characters is allowed; 1001 is refused. Use a multi-byte Korean
    // character to prove the limit is counted in characters, not bytes.
    let ok_text: String = "가".repeat(MEMORY_NOTE_MAX_CHARS);
    store.upsert(note(&ok_text)).expect("1000 chars should be accepted");

    let too_long: String = "가".repeat(MEMORY_NOTE_MAX_CHARS + 1);
    let err = store
        .upsert(note(&too_long))
        .expect_err("1001 chars should be refused");
    match err {
        MemoryError::TooLong { len, max } => {
            assert_eq!(len, MEMORY_NOTE_MAX_CHARS + 1);
            assert_eq!(max, MEMORY_NOTE_MAX_CHARS);
        }
        other => panic!("expected TooLong, got {other:?}"),
    }

    // Only the accepted note is present; the refused one changed nothing (R8.6).
    let items = store.list(MemoryKind::Note).expect("list");
    assert_eq!(items.len(), 1);
    assert_eq!(items[0].text.chars().count(), MEMORY_NOTE_MAX_CHARS);
}

#[test]
fn upsert_then_list_roundtrips_newest_first() {
    let store = SqliteMemoryStore::open_in_memory().expect("open");

    let first = store.upsert(note("첫 번째 기억")).expect("insert first");
    let second = store.upsert(note("두 번째 기억")).expect("insert second");
    assert_eq!(first.kind, MemoryKind::Note);
    assert_ne!(first.id, second.id);

    let items = store.list(MemoryKind::Note).expect("list");
    assert_eq!(items.len(), 2);
    // Newest first: the second insert has a later-or-equal updated_at and a
    // higher id, so it sorts first.
    assert_eq!(items[0].id, second.id);
    assert_eq!(items[0].text, "두 번째 기억");
    assert_eq!(items[1].id, first.id);
    assert_eq!(items[1].text, "첫 번째 기억");
}

#[test]
fn upsert_edits_existing_note_in_place() {
    let store = SqliteMemoryStore::open_in_memory().expect("open");
    let created = store.upsert(note("원래 내용")).expect("insert");

    let edited = store
        .upsert(MemoryDraft::edit(created.id, "수정된 내용"))
        .expect("edit");
    assert_eq!(edited.id, created.id);
    assert_eq!(edited.text, "수정된 내용");

    // Still exactly one note, now carrying the edited text.
    let items = store.list(MemoryKind::Note).expect("list");
    assert_eq!(items.len(), 1);
    assert_eq!(items[0].text, "수정된 내용");
}

#[test]
fn edit_missing_note_reports_not_found_without_writing() {
    let store = SqliteMemoryStore::open_in_memory().expect("open");
    let err = store
        .upsert(MemoryDraft::edit(999, "없는 항목"))
        .expect_err("editing a missing id should fail");
    assert!(matches!(err, MemoryError::NotFound), "got {err:?}");
    assert!(store.list(MemoryKind::Note).expect("list").is_empty());
}

#[test]
fn delete_removes_the_note() {
    let store = SqliteMemoryStore::open_in_memory().expect("open");
    let a = store.upsert(note("남길 기억")).expect("insert a");
    let b = store.upsert(note("지울 기억")).expect("insert b");

    store.delete(b.id).expect("delete b");

    let items = store.list(MemoryKind::Note).expect("list");
    assert_eq!(items.len(), 1);
    assert_eq!(items[0].id, a.id);
    assert_eq!(items[0].text, "남길 기억");
}

#[test]
fn store_failure_preserves_existing_data() {
    // A temp-file database lets a second connection install triggers that make
    // the store's writes fail, without deleting the data already stored.
    let dir = tempfile::tempdir().expect("tempdir");
    let db_path = dir.path().join("memory.sqlite3");

    let store = SqliteMemoryStore::open(&db_path).expect("open");
    let kept_a = store.upsert(note("보존 기억 1")).expect("insert 1");
    let kept_b = store.upsert(note("보존 기억 2")).expect("insert 2");

    // Install triggers that abort any further insert or delete on memory_note.
    let saboteur = Connection::open(&db_path).expect("open saboteur");
    saboteur
        .execute_batch(
            "CREATE TRIGGER fail_note_insert BEFORE INSERT ON memory_note
                 BEGIN SELECT RAISE(ABORT, 'boom'); END;
             CREATE TRIGGER fail_note_delete BEFORE DELETE ON memory_note
                 BEGIN SELECT RAISE(ABORT, 'boom'); END;",
        )
        .expect("install triggers");

    // An insert now fails at the database layer (R8.7).
    let insert_err = store
        .upsert(note("실패해야 하는 새 기억"))
        .expect_err("insert should fail");
    assert!(matches!(insert_err, MemoryError::Db(_)), "got {insert_err:?}");

    // A delete now fails at the database layer (R8.7).
    let delete_err = store.delete(kept_a.id).expect_err("delete should fail");
    assert!(matches!(delete_err, MemoryError::Db(_)), "got {delete_err:?}");

    // Both original notes are still present and unchanged.
    let items = store.list(MemoryKind::Note).expect("list");
    let mut ids: Vec<i64> = items.iter().map(|item| item.id).collect();
    ids.sort_unstable();
    let mut expected = vec![kept_a.id, kept_b.id];
    expected.sort_unstable();
    assert_eq!(ids, expected);
}

#[test]
fn rag_comparisons_are_empty_without_any_experiment() {
    let store = SqliteMemoryStore::open_in_memory().expect("open");
    assert!(store.rag_comparisons().expect("rag").is_empty());
}

#[test]
fn rag_comparisons_lists_stored_experiment_results() {
    let dir = tempfile::tempdir().expect("tempdir");
    let db_path = dir.path().join("context.sqlite3");

    // The experiment runner persists results into the shared database.
    {
        let mut experiments = SqliteExperimentStore::open(&db_path).expect("open experiments");
        experiments
            .record(&[
                ExperimentResult {
                    run_id: "run-1".to_string(),
                    provider: "provider-a".to_string(),
                    model: "model-x".to_string(),
                    question_id: "q1".to_string(),
                    latency_ms: 120,
                    quality_score: 88,
                    prompt_version: "v1".to_string(),
                    outcome: Outcome::Ok,
                    answer: Some("in-memory only".to_string()),
                },
                ExperimentResult {
                    run_id: "run-1".to_string(),
                    provider: "provider-b".to_string(),
                    model: "model-y".to_string(),
                    question_id: "q1".to_string(),
                    latency_ms: 340,
                    quality_score: 0,
                    prompt_version: "v1".to_string(),
                    outcome: Outcome::Timeout,
                    answer: None,
                },
            ])
            .expect("record results");
    }

    // The memory window reads the same database.
    let store = SqliteMemoryStore::open(&db_path).expect("open memory store");
    let rows = store.rag_comparisons().expect("rag comparisons");
    assert_eq!(rows.len(), 2);

    let first = &rows[0];
    assert_eq!(first.provider, "provider-a");
    assert_eq!(first.latency_ms, 120);
    assert_eq!(first.quality_score, 88);
    assert_eq!(first.outcome, "ok");

    let second = &rows[1];
    assert_eq!(second.provider, "provider-b");
    assert_eq!(second.outcome, "timeout");
}

#[test]
fn reference_packs_are_empty_when_table_absent() {
    let store = SqliteMemoryStore::open_in_memory().expect("open");
    assert!(store.list(MemoryKind::ReferencePack).expect("list").is_empty());
}

#[test]
fn reference_packs_are_listed_from_context_table() {
    let dir = tempfile::tempdir().expect("tempdir");
    let db_path = dir.path().join("context.sqlite3");

    // Create the reference-pack table (the columns the memory store reads) and
    // insert a pack, mimicking what the context layer stores.
    {
        let conn = Connection::open(&db_path).expect("open context");
        conn.execute_batch(
            "CREATE TABLE context_reference_packs(
                id INTEGER PRIMARY KEY,
                what_text TEXT NOT NULL,
                end_log_id INTEGER NOT NULL
            );",
        )
        .expect("create reference pack table");
        conn.execute(
            "INSERT INTO context_reference_packs(what_text, end_log_id)
             VALUES (?1, ?2), (?3, ?4)",
            params!["오래된 근거", 10_i64, "최신 근거", 20_i64],
        )
        .expect("insert packs");
    }

    let store = SqliteMemoryStore::open(&db_path).expect("open memory store");
    let items = store.list(MemoryKind::ReferencePack).expect("list packs");
    assert_eq!(items.len(), 2);
    // Ordered by end_log_id DESC: the newest pack first.
    assert_eq!(items[0].kind, MemoryKind::ReferencePack);
    assert_eq!(items[0].text, "최신 근거");
    assert_eq!(items[0].updated_at, 20);
    assert_eq!(items[1].text, "오래된 근거");
}
