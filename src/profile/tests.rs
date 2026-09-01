//! Unit tests for the user-profile module and owner-scope migration.

use super::*;
use rusqlite::Connection;

// ---------------------------------------------------------------------------
// resolve_owner (R10.1, R10.5)
// ---------------------------------------------------------------------------

#[test]
fn resolve_owner_missing_when_none() {
    assert_eq!(resolve_owner(None), OwnerName::Unset(UnsetReason::Missing));
}

#[test]
fn resolve_owner_too_short_when_blank() {
    assert_eq!(
        resolve_owner(Some("   ")),
        OwnerName::Unset(UnsetReason::TooShort)
    );
}

#[test]
fn resolve_owner_too_long_over_64_chars() {
    let long = "가".repeat(65);
    assert_eq!(
        resolve_owner(Some(&long)),
        OwnerName::Unset(UnsetReason::TooLong)
    );
}

#[test]
fn resolve_owner_trims_and_sets() {
    assert_eq!(
        resolve_owner(Some("  최연우  ")),
        OwnerName::Set("최연우".to_string())
    );
}

#[test]
fn resolve_owner_never_falls_back_to_builtin_name() {
    // An absent value yields Unset, never a substitute like "최연우" (R10.1).
    assert!(matches!(resolve_owner(None), OwnerName::Unset(_)));
    assert!(matches!(resolve_owner(Some("")), OwnerName::Unset(_)));
}

#[test]
fn owner_name_status_matches_resolution() {
    let profile = UserProfile {
        owner_display_name: "지훈".to_string(),
        account_scope: AccountScope::new("/home/test"),
    };
    assert_eq!(profile.owner_name_status(), OwnerNameStatus::Set);
    let unset = UserProfile {
        owner_display_name: "  ".to_string(),
        account_scope: AccountScope::new("/home/test"),
    };
    assert!(matches!(
        unset.owner_name_status(),
        OwnerNameStatus::Unset(_)
    ));
}

// ---------------------------------------------------------------------------
// is_owner_message (R10.7)
// ---------------------------------------------------------------------------

#[test]
fn owner_message_via_db_authority() {
    let profile = UserProfile {
        owner_display_name: "지훈".to_string(),
        account_scope: AccountScope::new("/home/test"),
    };
    let sender = SenderIdentity {
        db_authoritative_owner: true,
        display_name: "완전히 다른 이름".to_string(),
    };
    assert!(is_owner_message(&profile, &sender));
}

#[test]
fn owner_message_via_exact_display_name() {
    let profile = UserProfile {
        owner_display_name: "지훈".to_string(),
        account_scope: AccountScope::new("/home/test"),
    };
    let sender = SenderIdentity {
        db_authoritative_owner: false,
        display_name: "  지훈  ".to_string(),
    };
    assert!(is_owner_message(&profile, &sender));
}

#[test]
fn non_owner_message_not_matched() {
    let profile = UserProfile {
        owner_display_name: "지훈".to_string(),
        account_scope: AccountScope::new("/home/test"),
    };
    let sender = SenderIdentity {
        db_authoritative_owner: false,
        display_name: "민수".to_string(),
    };
    assert!(!is_owner_message(&profile, &sender));
}

// ---------------------------------------------------------------------------
// assert_in_scope (R10.3, R10.4, R10.8)
// ---------------------------------------------------------------------------

#[test]
fn in_scope_allows_paths_under_home_root() {
    let scope = AccountScope::new("/home/alice");
    assert!(assert_in_scope(Path::new("/home/alice/.config/openkakao"), &scope).is_ok());
}

#[test]
fn out_of_scope_rejects_other_account() {
    let scope = AccountScope::new("/home/alice");
    let err = assert_in_scope(Path::new("/home/bob/.config/openkakao"), &scope);
    assert!(matches!(err, Err(ProfileError::OutOfScope(_))));
}

// ---------------------------------------------------------------------------
// ProfileStore round-trip (R10.2)
// ---------------------------------------------------------------------------

#[test]
fn confirm_owner_stores_trimmed_and_loads() {
    let scope = AccountScope::new("/home/alice");
    let store = SqliteProfileStore::open_in_memory(scope.clone()).unwrap();
    assert!(store.load().unwrap().is_none());

    let profile = store.confirm_owner("  지훈  ").unwrap();
    assert_eq!(profile.owner_display_name, "지훈");
    assert_eq!(profile.account_scope, scope);

    let loaded = store.load().unwrap().unwrap();
    assert_eq!(loaded.owner_display_name, "지훈");
    assert_eq!(loaded.account_scope, scope);
}

#[test]
fn confirm_owner_rejects_unusable_name() {
    let store = SqliteProfileStore::open_in_memory(AccountScope::new("/home/alice")).unwrap();
    assert!(matches!(
        store.confirm_owner("   "),
        Err(ProfileError::OwnerNameUnset(_))
    ));
    // Nothing persisted after a rejected confirmation.
    assert!(store.load().unwrap().is_none());
}

// ---------------------------------------------------------------------------
// migrate_owner_scope (R10.6, R10.10, R10.11)
// ---------------------------------------------------------------------------

/// Build a legacy database with the four `choi_yeonwoo_*` tables populated.
fn legacy_db() -> Connection {
    let conn = Connection::open_in_memory().unwrap();
    conn.execute_batch(
        "CREATE TABLE choi_yeonwoo_style(id INTEGER PRIMARY KEY, message TEXT NOT NULL);
         CREATE TABLE choi_yeonwoo_style_profile(chat TEXT PRIMARY KEY, sample_count INTEGER NOT NULL);
         CREATE TABLE choi_yeonwoo_recipient_style_profile(recipient TEXT PRIMARY KEY);
         CREATE TABLE choi_yeonwoo_recipient_style_samples(id INTEGER PRIMARY KEY, style_message_id INTEGER);
         CREATE TABLE qa_pair(id INTEGER PRIMARY KEY);
         CREATE TABLE attachment_reference(id INTEGER PRIMARY KEY);
         INSERT INTO choi_yeonwoo_style(message) VALUES ('a'), ('b');
         INSERT INTO choi_yeonwoo_style_profile(chat, sample_count) VALUES ('room', 5);
         INSERT INTO choi_yeonwoo_recipient_style_profile(recipient) VALUES ('민수');
         INSERT INTO choi_yeonwoo_recipient_style_samples(style_message_id) VALUES (1), (2), (3);
         INSERT INTO qa_pair DEFAULT VALUES;
         INSERT INTO qa_pair DEFAULT VALUES;
         INSERT INTO attachment_reference DEFAULT VALUES;",
    )
    .unwrap();
    conn.execute_batch(
        "CREATE TABLE schema_migration(name TEXT PRIMARY KEY, applied_at INTEGER NOT NULL)",
    )
    .unwrap();
    conn
}

fn table_present(conn: &Connection, name: &str) -> bool {
    conn.query_row(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?1",
        params![name],
        |_| Ok(()),
    )
    .optional()
    .unwrap()
    .is_some()
}

#[test]
fn migration_renames_tables_adds_owner_key_and_preserves_counts() {
    let mut conn = legacy_db();
    let report = {
        let tx = conn.transaction().unwrap();
        let report = migrate_owner_scope(&tx, "지훈").unwrap();
        tx.commit().unwrap();
        report
    };

    // Counts preserved (R10.6).
    assert_eq!(report.style_profiles, 1);
    assert_eq!(report.qa_pairs, 2);
    assert_eq!(report.attachments, 1);

    // Legacy names gone, renamed names present.
    for (legacy, renamed) in super::OWNER_TABLE_RENAMES {
        assert!(!table_present(&conn, legacy), "legacy {legacy} still present");
        assert!(table_present(&conn, renamed), "renamed {renamed} missing");
    }

    // owner_key filled with the owner display name's provenance id.
    let expected = provenance_id("지훈");
    let key: String = conn
        .query_row("SELECT owner_key FROM owner_style LIMIT 1", [], |row| {
            row.get(0)
        })
        .unwrap();
    assert_eq!(key, expected);

    // Style rows preserved through the rename.
    let style_rows: i64 = conn
        .query_row("SELECT COUNT(*) FROM owner_style", [], |row| row.get(0))
        .unwrap();
    assert_eq!(style_rows, 2);
}

#[test]
fn migration_is_idempotent() {
    let mut conn = legacy_db();
    {
        let tx = conn.transaction().unwrap();
        migrate_owner_scope(&tx, "지훈").unwrap();
        tx.commit().unwrap();
    }
    // Second run changes nothing and still reports the same counts.
    let report = {
        let tx = conn.transaction().unwrap();
        let report = migrate_owner_scope(&tx, "지훈").unwrap();
        tx.commit().unwrap();
        report
    };
    assert_eq!(report.style_profiles, 1);
    assert_eq!(report.qa_pairs, 2);
    assert_eq!(report.attachments, 1);

    // Only one marker row.
    let markers: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM schema_migration WHERE name = ?1",
            params![OWNER_SCOPE_MIGRATION],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(markers, 1);
}

#[test]
fn migration_on_fresh_db_records_marker_without_error() {
    // A fresh install already has the owner_* names (or none at all): the
    // migration should record the marker and report zero counts.
    let mut conn = Connection::open_in_memory().unwrap();
    let report = {
        let tx = conn.transaction().unwrap();
        let report = migrate_owner_scope(&tx, "지훈").unwrap();
        tx.commit().unwrap();
        report
    };
    assert_eq!(report.style_profiles, 0);
    assert!(migration_applied_public(&conn));
}

fn migration_applied_public(conn: &Connection) -> bool {
    conn.query_row(
        "SELECT 1 FROM schema_migration WHERE name = ?1",
        params![OWNER_SCOPE_MIGRATION],
        |_| Ok(()),
    )
    .optional()
    .unwrap()
    .is_some()
}
