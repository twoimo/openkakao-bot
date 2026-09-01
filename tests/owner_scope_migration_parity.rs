//! Owner-scope migration parity test (Task 3.3).
//!
//! Feature: kakao-agent-live-ops
//!
//! Pins golden fixtures for the `choi_yeonwoo_*` → `owner_*` rename: the
//! search and style-lookup results produced from a legacy `choi_yeonwoo_*`
//! database are identical after `profile::migrate_owner_scope` renames the
//! tables to `owner_*`. This proves the owner-scope generalization (Decision 6,
//! R10.6) preserves retrieval and style behavior — the rename is data-only.
//!
//! The golden database is built by the current indexing pipeline (which writes
//! `owner_*` tables). A second database is reduced to the legacy layout by
//! renaming `owner_*` back to `choi_yeonwoo_*`, then migrated forward with the
//! real `migrate_owner_scope`. Both must yield identical style profiles and
//! search orderings.
//!
//! _Requirements: R10.6, R10.10, R10.11, R12.3_

use std::io::Write;
use std::path::{Path, PathBuf};

use openkakao_cli::context::{self, ContextResult};
use openkakao_cli::profile::migrate_owner_scope;
use rusqlite::Connection;
use tempfile::tempdir;

const CHAT: &str = "부자멘토멘티";
const OWNER: &str = "최연우";
const QUERY: &str = "회의 자료";

/// The `owner_*` ↔ legacy `choi_yeonwoo_*` table pairs.
const RENAMES: [(&str, &str); 4] = [
    ("choi_yeonwoo_style", "owner_style"),
    ("choi_yeonwoo_style_profile", "owner_style_profile"),
    (
        "choi_yeonwoo_recipient_style_profile",
        "owner_recipient_style_profile",
    ),
    (
        "choi_yeonwoo_recipient_style_samples",
        "owner_recipient_style_samples",
    ),
];

/// A conversation fixture with enough owner (최연우) turns to produce a style
/// profile. Row order is part of the golden contract.
const FIXTURE_ROWS: &[(&str, &str, &str)] = &[
    ("2026-01-01 09:00:00", "민수", "프로젝트 회의 일정 언제 잡을까"),
    ("2026-01-01 09:05:00", "최연우", "회의 자료 먼저 정리할게"),
    ("2026-01-01 09:10:00", "민수", "점심 메뉴 뭐 먹을까"),
    ("2026-01-01 09:15:00", "최연우", "회의 끝나고 점심 회의 자료 공유"),
    ("2026-01-01 09:20:00", "민수", "주말에 등산 갈 사람"),
    ("2026-01-01 09:25:00", "최연우", "자료 준비 다 됐어 회의 하자"),
    ("2026-01-01 09:30:00", "민수", "발표 순서 정했어"),
    ("2026-01-01 09:35:00", "최연우", "발표 자료 검토하고 회의 때 말할게"),
];

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

/// Search orderings for all three modes.
fn search_all_modes(db_path: &Path) -> Vec<(&'static str, Vec<String>)> {
    ["keyword", "vector", "hybrid"]
        .into_iter()
        .map(|mode| {
            let results = context::search(db_path, Some(CHAT), None, QUERY, mode, 10).unwrap();
            (mode, messages(&results))
        })
        .collect()
}

/// Rename `owner_*` tables back to their legacy `choi_yeonwoo_*` names to
/// reconstruct a pre-migration database.
fn reduce_to_legacy(db_path: &Path) {
    let conn = Connection::open(db_path).unwrap();
    let present = |name: &str| -> bool {
        conn.query_row(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?1",
            rusqlite::params![name],
            |_| Ok(()),
        )
        .is_ok()
    };
    for (legacy, renamed) in RENAMES {
        if present(renamed) && !present(legacy) {
            conn.execute_batch(&format!("ALTER TABLE {renamed} RENAME TO {legacy}"))
                .unwrap();
        }
    }
}

#[test]
fn owner_scope_migration_preserves_search_and_style_lookup() {
    let dir = tempdir().unwrap();
    let csv = write_fixture_csv(dir.path());

    // Golden database: the current pipeline writes `owner_*` tables directly.
    let golden_db = dir.path().join("golden.sqlite3");
    let indexed = context::index_csv(&golden_db, CHAT, &csv).unwrap();
    assert_eq!(indexed, FIXTURE_ROWS.len());

    let golden_style = context::style_profile_json(&golden_db, CHAT, OWNER, None).unwrap();
    // The fixture must actually produce a style profile, or the parity check is
    // vacuous.
    assert!(
        golden_style.is_some(),
        "golden style profile should exist for the fixture"
    );
    let golden_search = search_all_modes(&golden_db);

    // Legacy database: index, then reduce to the `choi_yeonwoo_*` layout.
    let legacy_db = dir.path().join("legacy.sqlite3");
    let indexed_legacy = context::index_csv(&legacy_db, CHAT, &csv).unwrap();
    assert_eq!(indexed_legacy, FIXTURE_ROWS.len());
    reduce_to_legacy(&legacy_db);

    // Apply the real migration (the thing under test) via a raw connection so
    // it — not the context module's on-open rename — performs the work.
    {
        let mut conn = Connection::open(&legacy_db).unwrap();
        // Confirm the legacy names are present and the `owner_*` names absent.
        for (legacy, renamed) in RENAMES {
            let legacy_present = conn
                .query_row(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?1",
                    rusqlite::params![legacy],
                    |_| Ok(()),
                )
                .is_ok();
            let renamed_present = conn
                .query_row(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?1",
                    rusqlite::params![renamed],
                    |_| Ok(()),
                )
                .is_ok();
            assert!(legacy_present, "legacy {legacy} missing before migration");
            assert!(!renamed_present, "renamed {renamed} present before migration");
        }

        let tx = conn.transaction().unwrap();
        migrate_owner_scope(&tx, OWNER).unwrap();
        tx.commit().unwrap();
    }

    // After the migration, style lookup and search must match the golden
    // database exactly.
    let migrated_style = context::style_profile_json(&legacy_db, CHAT, OWNER, None).unwrap();
    assert_eq!(
        migrated_style, golden_style,
        "style profile changed after choi_yeonwoo_* → owner_* migration"
    );

    let migrated_search = search_all_modes(&legacy_db);
    assert_eq!(
        migrated_search, golden_search,
        "search ordering changed after choi_yeonwoo_* → owner_* migration"
    );
}
