//! Embedding parity test (Task 4.1).
//!
//! Verifies that routing `context::search` through the pluggable `Embedder`
//! interface preserves search-result ordering exactly. The default `search`
//! entry point injects `LocalHashEmbedder` (which wraps the pre-existing
//! deterministic hash-vector logic), so its output must match both an
//! explicitly injected `LocalHashEmbedder` and a recorded golden ordering.
//! _Requirements: R11.1, R11.10_

use std::io::Write;
use std::path::{Path, PathBuf};

use openkakao_cli::context::{self, ContextResult, LocalHashEmbedder};
use tempfile::tempdir;

const CHAT: &str = "부자멘토멘티";

/// Fixed conversation fixture. Ordering of these rows is part of the golden
/// contract: `index_csv` assigns row ids in insertion order, and search
/// tie-breaks on those ids.
const FIXTURE_ROWS: &[(&str, &str, &str)] = &[
    ("2026-01-01 09:00:00", "민수", "프로젝트 회의 일정 언제 잡을까"),
    ("2026-01-01 09:05:00", "최연우", "회의 자료 먼저 정리할게"),
    ("2026-01-01 09:10:00", "민수", "점심 메뉴 뭐 먹을까"),
    ("2026-01-01 09:15:00", "최연우", "회의 끝나고 점심 회의 자료 공유"),
    ("2026-01-01 09:20:00", "민수", "주말에 등산 갈 사람"),
    ("2026-01-01 09:25:00", "최연우", "자료 준비 다 됐어 회의 하자"),
];

const QUERY: &str = "회의 자료";

fn write_fixture_csv(dir: &Path) -> PathBuf {
    let path = dir.join("conversation.csv");
    let mut file = std::fs::File::create(&path).unwrap();
    writeln!(file, "Date,User,Message").unwrap();
    for (date, user, message) in FIXTURE_ROWS {
        writeln!(file, "{date},{user},{message}").unwrap();
    }
    path
}

fn messages(results: &[ContextResult]) -> Vec<String> {
    results.iter().map(|r| r.message.clone()).collect()
}

/// Fully comparable projection of a result. `ContextResult` does not derive
/// `PartialEq`, and `f32` scores are compared bit-for-bit so parity is exact.
fn fingerprint(results: &[ContextResult]) -> Vec<(String, String, String, String, String, u32, String)> {
    results
        .iter()
        .map(|r| {
            (
                r.chat.clone(),
                r.source.clone(),
                r.date.clone(),
                r.user.clone(),
                r.message.clone(),
                r.score.to_bits(),
                r.mode.clone(),
            )
        })
        .collect()
}

fn run_modes(db_path: &Path) -> Vec<(&'static str, Vec<String>)> {
    let mut out = Vec::new();
    for mode in ["keyword", "vector", "hybrid"] {
        // Default entry point: injects LocalHashEmbedder internally.
        let default_results =
            context::search(db_path, Some(CHAT), None, QUERY, mode, 10).unwrap();
        // Explicitly injected default embedder.
        let injected_results = context::search_with_embedder(
            db_path,
            Some(CHAT),
            None,
            QUERY,
            mode,
            10,
            &LocalHashEmbedder,
        )
        .unwrap();

        // Parity: the default `search` must be identical to explicit injection,
        // including score and mode, not just message ordering.
        assert_eq!(
            fingerprint(&default_results),
            fingerprint(&injected_results),
            "mode {mode}: default search must match explicitly injected LocalHashEmbedder"
        );

        out.push((mode, messages(&default_results)));
    }
    out
}

#[test]
fn search_ordering_is_identical_before_and_after_embedder_injection() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("context.sqlite3");
    let csv = write_fixture_csv(dir.path());
    let indexed = context::index_csv(&db_path, CHAT, &csv).unwrap();
    assert_eq!(indexed, FIXTURE_ROWS.len());

    let results = run_modes(&db_path);

    // Golden fixture: the exact result ordering per mode. This is the recorded
    // behavior of the deterministic hash-vector pipeline; it must not change
    // when search is refactored to accept an injected Embedder.
    let keyword_golden = vec![
        "회의 끝나고 점심 회의 자료 공유".to_string(),
        "회의 자료 먼저 정리할게".to_string(),
        "자료 준비 다 됐어 회의 하자".to_string(),
        "프로젝트 회의 일정 언제 잡을까".to_string(),
    ];
    let vector_golden = vec![
        "회의 끝나고 점심 회의 자료 공유".to_string(),
        "자료 준비 다 됐어 회의 하자".to_string(),
        "회의 자료 먼저 정리할게".to_string(),
        "프로젝트 회의 일정 언제 잡을까".to_string(),
    ];
    let hybrid_golden = vec![
        "회의 끝나고 점심 회의 자료 공유".to_string(),
        "회의 자료 먼저 정리할게".to_string(),
        "자료 준비 다 됐어 회의 하자".to_string(),
        "프로젝트 회의 일정 언제 잡을까".to_string(),
    ];

    for (mode, actual) in &results {
        let golden = match *mode {
            "keyword" => &keyword_golden,
            "vector" => &vector_golden,
            "hybrid" => &hybrid_golden,
            other => panic!("unexpected mode {other}"),
        };
        assert_eq!(actual, golden, "golden ordering mismatch for mode {mode}");
    }
}
