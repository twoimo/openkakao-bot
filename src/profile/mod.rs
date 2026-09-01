//! User-profile generalization and owner-scope migration (tasks 3, 3.1).
//!
//! The previous code hard-coded a single owner — the `dataset::OWNER_NAME`
//! constant `"최연우"` and the `choi_yeonwoo_*` style tables. This module
//! removes the built-in identity and replaces it with a per-account
//! [`UserProfile`]. The owner display name is read **only** from configuration
//! (R10.1); there is no built-in name or command default fallback. When the
//! owner is unset, the four product-send features fence with no fallback value
//! — that fencing is enforced by [`SendGuard`](crate::safety::SendGuard)
//! through the [`ProfileView`] this module implements.
//!
//! [`migrate_owner_scope`] renames the four legacy `choi_yeonwoo_*` tables to
//! `owner_*` inside a single transaction, adds an `owner_key` column to each,
//! and fills it with the owner display name's provenance id. It is idempotent
//! (guarded by a `schema_migration` marker) and count-preserving: if the
//! style-profile, Q&A-pair, or attachment-evidence counts differ before and
//! after, it refuses so the caller rolls the transaction back (R10.6, R10.10,
//! R10.11).

use std::path::{Path, PathBuf};

use rusqlite::{params, Connection, OptionalExtension, Transaction};
use thiserror::Error;

use crate::context::provenance_id;
use crate::safety::{OwnerNameStatus, ProfileView, UnsetReason};

/// The maximum owner-display-name length, in characters (R10.5).
pub const OWNER_NAME_MAX_CHARS: usize = 64;

/// The migration marker recorded in `schema_migration` once the owner-scope
/// rename has been applied (R10.6).
pub const OWNER_SCOPE_MIGRATION: &str = "owner_scope_v1";

/// The four legacy → renamed table pairs migrated by [`migrate_owner_scope`].
const OWNER_TABLE_RENAMES: [(&str, &str); 4] = [
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

/// The macOS account a profile belongs to: the home-directory root everything
/// is stored under. Reads and writes outside this root are refused (R10.3,
/// R10.4, R10.8).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AccountScope {
    /// The home-directory root of the running macOS user account.
    pub home_root: PathBuf,
}

impl AccountScope {
    /// Build a scope from an explicit home root.
    pub fn new(home_root: impl Into<PathBuf>) -> Self {
        Self {
            home_root: home_root.into(),
        }
    }

    /// The scope of the currently running macOS user account.
    pub fn current() -> Result<Self, ProfileError> {
        let home = dirs::home_dir()
            .ok_or_else(|| ProfileError::Scope("홈 디렉터리를 찾지 못했어요".to_string()))?;
        Ok(Self { home_root: home })
    }

    /// Serialize to a stable string for the `user_profile.account_scope` column.
    fn encode(&self) -> String {
        self.home_root.display().to_string()
    }

    /// Reconstruct from the stored string.
    fn decode(raw: &str) -> Self {
        Self {
            home_root: PathBuf::from(raw),
        }
    }
}

/// A user profile: one macOS account's owner display name and storage scope.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UserProfile {
    /// The owner display name (trimmed, 1..=64 chars). The single source of
    /// truth for who the owner is (R10.1).
    pub owner_display_name: String,
    /// The account this profile is scoped to.
    pub account_scope: AccountScope,
}

impl UserProfile {
    /// The owner-name status this profile presents to the send guard.
    pub fn owner_name_status(&self) -> OwnerNameStatus {
        match resolve_owner(Some(&self.owner_display_name)) {
            OwnerName::Set(_) => OwnerNameStatus::Set,
            OwnerName::Unset(reason) => OwnerNameStatus::Unset(reason),
        }
    }

    /// Whether this profile is scoped to the running macOS account. Used by the
    /// send guard's account-scope precondition (R10.8).
    pub fn in_running_account(&self) -> bool {
        match AccountScope::current() {
            Ok(current) => current == self.account_scope,
            Err(_) => false,
        }
    }
}

/// A [`UserProfile`] can act as the send guard's owner-profile view (R10.5,
/// R10.8). An unset name or an out-of-scope account fences the four product
/// send features with no fallback value.
impl ProfileView for UserProfile {
    fn owner_name_status(&self) -> OwnerNameStatus {
        UserProfile::owner_name_status(self)
    }

    fn in_scope(&self) -> bool {
        self.in_running_account()
    }
}

/// The owner display name resolved from configuration (R10.1).
///
/// There is deliberately no built-in name and no command default: an absent or
/// invalid configured value yields [`OwnerName::Unset`], never a substitute.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum OwnerName {
    /// A usable owner name (already trimmed).
    Set(String),
    /// No usable owner name, with the reason.
    Unset(UnsetReason),
}

/// Resolve the owner display name from a raw configured value.
///
/// * `None` → [`UnsetReason::Missing`] (no configuration value at all).
/// * trimmed length `< 1` char → [`UnsetReason::TooShort`].
/// * trimmed length `> 64` chars → [`UnsetReason::TooLong`].
/// * otherwise → [`OwnerName::Set`] with the trimmed value.
pub fn resolve_owner(raw: Option<&str>) -> OwnerName {
    let Some(raw) = raw else {
        return OwnerName::Unset(UnsetReason::Missing);
    };
    let trimmed = raw.trim();
    let len = trimmed.chars().count();
    if len == 0 {
        OwnerName::Unset(UnsetReason::TooShort)
    } else if len > OWNER_NAME_MAX_CHARS {
        OwnerName::Unset(UnsetReason::TooLong)
    } else {
        OwnerName::Set(trimmed.to_string())
    }
}

/// The database-authoritative identity of a message sender, used to decide
/// whether a message is the owner's (R10.7).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SenderIdentity {
    /// True when the local database authoritatively marks this sender as the
    /// account owner.
    pub db_authoritative_owner: bool,
    /// The sender's display name.
    pub display_name: String,
}

/// Whether a message is from the owner (R10.7).
///
/// A message is the owner's when the database authoritatively says so, **or**
/// when the sender's display name matches the profile's owner display name
/// exactly after trimming both. Owner messages are excluded from reply targets
/// and used only for context/style learning.
pub fn is_owner_message(profile: &UserProfile, sender: &SenderIdentity) -> bool {
    if sender.db_authoritative_owner {
        return true;
    }
    sender.display_name.trim() == profile.owner_display_name.trim()
}

/// Errors from profile loading, scope checks, and migration.
#[derive(Debug, Error)]
pub enum ProfileError {
    /// The profile store backend failed.
    #[error("사용자 프로필 저장소 오류: {0}")]
    Backend(String),
    /// The confirmed owner name was not usable (R10.5).
    #[error("소유자 이름을 확정하지 못했어요. 1자 이상 64자 이하의 이름을 골라 주세요.")]
    OwnerNameUnset(UnsetReason),
    /// A path was outside the running account's scope (R10.8).
    #[error("이 자료는 지금 로그인한 계정의 것이 아니에요: {0}. 지금 계정의 자료만 사용해요.")]
    OutOfScope(String),
    /// The running account scope could not be resolved.
    #[error("계정 범위를 확인하지 못했어요: {0}")]
    Scope(String),
    /// Migration counts did not match before and after; caller must roll back
    /// (R10.6, R10.10).
    #[error("소유자 자료 옮기기에서 개수가 맞지 않아 되돌렸어요: {0}")]
    MigrationCountMismatch(String),
}

/// The persistence boundary for a [`UserProfile`].
pub trait ProfileStore {
    /// Load the stored profile, or `None` if the owner is not yet confirmed.
    fn load(&self) -> Result<Option<UserProfile>, ProfileError>;

    /// Confirm an owner display name, storing the trimmed value scoped to the
    /// running account (R10.2). Rejects an unusable name (R10.5).
    fn confirm_owner(&self, raw: &str) -> Result<UserProfile, ProfileError>;
}

/// Assert `path` lies within `scope`'s home root. Out-of-scope means zero reads
/// or writes and a fence (R10.3, R10.4, R10.8).
pub fn assert_in_scope(path: &Path, scope: &AccountScope) -> Result<(), ProfileError> {
    let candidate = normalize(path);
    let root = normalize(&scope.home_root);
    if candidate.starts_with(&root) {
        Ok(())
    } else {
        Err(ProfileError::OutOfScope(path.display().to_string()))
    }
}

/// Best-effort path normalization that does not require the path to exist:
/// canonicalize when possible, otherwise use the path as given.
fn normalize(path: &Path) -> PathBuf {
    path.canonicalize().unwrap_or_else(|_| path.to_path_buf())
}

// ---------------------------------------------------------------------------
// SQLite-backed profile store
// ---------------------------------------------------------------------------

/// Create the `user_profile` and `schema_migration` tables if absent.
pub fn ensure_profile_schema(conn: &Connection) -> Result<(), ProfileError> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS user_profile(
            id INTEGER PRIMARY KEY CHECK(id = 1),
            owner_display_name TEXT NOT NULL,
            account_scope TEXT NOT NULL,
            onboarded_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS schema_migration(
            name TEXT PRIMARY KEY,
            applied_at INTEGER NOT NULL
        );",
    )
    .map_err(|e| ProfileError::Backend(e.to_string()))
}

/// A [`ProfileStore`] backed by a SQLite connection.
pub struct SqliteProfileStore {
    conn: Connection,
    scope: AccountScope,
}

impl SqliteProfileStore {
    /// Wrap a connection, ensuring the schema is present.
    pub fn new(conn: Connection, scope: AccountScope) -> Result<Self, ProfileError> {
        ensure_profile_schema(&conn)?;
        Ok(Self { conn, scope })
    }

    /// Open (or create) a store at `path`, scoped to the running account.
    pub fn open(path: &Path) -> Result<Self, ProfileError> {
        let scope = AccountScope::current()?;
        assert_in_scope(path, &scope)?;
        let conn = Connection::open(path).map_err(|e| ProfileError::Backend(e.to_string()))?;
        Self::new(conn, scope)
    }

    /// Open an in-memory store. Primarily for tests.
    pub fn open_in_memory(scope: AccountScope) -> Result<Self, ProfileError> {
        let conn =
            Connection::open_in_memory().map_err(|e| ProfileError::Backend(e.to_string()))?;
        Self::new(conn, scope)
    }
}

impl ProfileStore for SqliteProfileStore {
    fn load(&self) -> Result<Option<UserProfile>, ProfileError> {
        let row = self
            .conn
            .query_row(
                "SELECT owner_display_name, account_scope FROM user_profile WHERE id = 1",
                [],
                |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)),
            )
            .optional()
            .map_err(|e| ProfileError::Backend(e.to_string()))?;
        Ok(row.map(|(owner_display_name, account_scope)| UserProfile {
            owner_display_name,
            account_scope: AccountScope::decode(&account_scope),
        }))
    }

    fn confirm_owner(&self, raw: &str) -> Result<UserProfile, ProfileError> {
        let name = match resolve_owner(Some(raw)) {
            OwnerName::Set(name) => name,
            OwnerName::Unset(reason) => return Err(ProfileError::OwnerNameUnset(reason)),
        };
        self.conn
            .execute(
                "INSERT INTO user_profile(id, owner_display_name, account_scope, onboarded_at)
                 VALUES (1, ?1, ?2, NULL)
                 ON CONFLICT(id) DO UPDATE SET
                    owner_display_name = excluded.owner_display_name,
                    account_scope = excluded.account_scope",
                params![name, self.scope.encode()],
            )
            .map_err(|e| ProfileError::Backend(e.to_string()))?;
        Ok(UserProfile {
            owner_display_name: name,
            account_scope: self.scope.clone(),
        })
    }
}

// ---------------------------------------------------------------------------
// Owner-scope migration (task 3.1)
// ---------------------------------------------------------------------------

/// Counts preserved across the owner-scope migration (R10.6).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct MigrationReport {
    /// Number of style-profile rows (`owner_style_profile`).
    pub style_profiles: usize,
    /// Number of Q&A pairs (`qa_pair`).
    pub qa_pairs: usize,
    /// Number of attachment-evidence rows (`attachment_reference`).
    pub attachments: usize,
}

/// Whether a table exists in the connection.
fn table_exists(tx: &Transaction, name: &str) -> Result<bool, ProfileError> {
    tx.query_row(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?1",
        params![name],
        |_| Ok(()),
    )
    .optional()
    .map(|row| row.is_some())
    .map_err(|e| ProfileError::Backend(e.to_string()))
}

/// Whether a column exists on a table.
fn column_exists(tx: &Transaction, table: &str, column: &str) -> Result<bool, ProfileError> {
    let mut stmt = tx
        .prepare(&format!("PRAGMA table_info({table})"))
        .map_err(|e| ProfileError::Backend(e.to_string()))?;
    let found = stmt
        .query_map([], |row| row.get::<_, String>(1))
        .map_err(|e| ProfileError::Backend(e.to_string()))?
        .filter_map(Result::ok)
        .any(|name| name == column);
    Ok(found)
}

/// Count the rows of a table, returning 0 when the table is absent.
fn count_rows(tx: &Transaction, table: &str) -> Result<usize, ProfileError> {
    if !table_exists(tx, table)? {
        return Ok(0);
    }
    tx.query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |row| {
        row.get::<_, i64>(0)
    })
    .map(|count| count as usize)
    .map_err(|e| ProfileError::Backend(e.to_string()))
}

/// Whether a `schema_migration` marker is recorded.
fn migration_applied(tx: &Transaction, name: &str) -> Result<bool, ProfileError> {
    if !table_exists(tx, "schema_migration")? {
        return Ok(false);
    }
    tx.query_row(
        "SELECT 1 FROM schema_migration WHERE name = ?1",
        params![name],
        |_| Ok(()),
    )
    .optional()
    .map(|row| row.is_some())
    .map_err(|e| ProfileError::Backend(e.to_string()))
}

/// The style-profile row count, reading whichever of the legacy or renamed
/// table currently exists.
fn style_profile_count(tx: &Transaction) -> Result<usize, ProfileError> {
    if table_exists(tx, "owner_style_profile")? {
        count_rows(tx, "owner_style_profile")
    } else {
        count_rows(tx, "choi_yeonwoo_style_profile")
    }
}

/// Migrate `OWNER_NAME`/`choi_yeonwoo_*` to the owner-scoped structure (R10.6,
/// R10.10, R10.11).
///
/// Runs only when `schema_migration` lacks the [`OWNER_SCOPE_MIGRATION`] marker,
/// so it is idempotent. Within the caller's single transaction it:
///
/// 1. Renames each `choi_yeonwoo_*` table to its `owner_*` name.
/// 2. Adds `owner_key TEXT NOT NULL DEFAULT ''` to each renamed table.
/// 3. Fills `owner_key` with the owner display name's provenance id.
///
/// It counts style-profile, Q&A-pair, and attachment-evidence rows before and
/// after; if any differ it returns [`ProfileError::MigrationCountMismatch`] so
/// the caller drops the transaction, leaving the previous data untouched and
/// the migration marker unrecorded (R10.10).
pub fn migrate_owner_scope(
    tx: &Transaction,
    owner: &str,
) -> Result<MigrationReport, ProfileError> {
    // Idempotent: if already applied, report current counts without changes.
    if migration_applied(tx, OWNER_SCOPE_MIGRATION)? {
        return Ok(MigrationReport {
            style_profiles: style_profile_count(tx)?,
            qa_pairs: count_rows(tx, "qa_pair")?,
            attachments: count_rows(tx, "attachment_reference")?,
        });
    }

    // Counts before the rename. Style rows may still live under the legacy name.
    let style_before = style_profile_count(tx)?;
    let qa_before = count_rows(tx, "qa_pair")?;
    let attach_before = count_rows(tx, "attachment_reference")?;

    let owner_key = provenance_id(owner);

    for (legacy, renamed) in OWNER_TABLE_RENAMES {
        // Rename the legacy table if it still carries the old name. A fresh
        // install already has the `owner_*` name, so there is nothing to do.
        if table_exists(tx, legacy)? && !table_exists(tx, renamed)? {
            tx.execute_batch(&format!("ALTER TABLE {legacy} RENAME TO {renamed}"))
                .map_err(|e| ProfileError::Backend(e.to_string()))?;
        }

        // Add and fill the owner_key column on the renamed table.
        if table_exists(tx, renamed)? && !column_exists(tx, renamed, "owner_key")? {
            tx.execute_batch(&format!(
                "ALTER TABLE {renamed} ADD COLUMN owner_key TEXT NOT NULL DEFAULT ''"
            ))
            .map_err(|e| ProfileError::Backend(e.to_string()))?;
            tx.execute(
                &format!("UPDATE {renamed} SET owner_key = ?1"),
                params![owner_key],
            )
            .map_err(|e| ProfileError::Backend(e.to_string()))?;
        }
    }

    // Counts after the rename. Renaming preserves rows, so mismatches only
    // arise from an unexpected fault — the count check is the safety net.
    let style_after = style_profile_count(tx)?;
    let qa_after = count_rows(tx, "qa_pair")?;
    let attach_after = count_rows(tx, "attachment_reference")?;

    if style_after != style_before || qa_after != qa_before || attach_after != attach_before {
        return Err(ProfileError::MigrationCountMismatch(format!(
            "style {style_before}->{style_after}, qa {qa_before}->{qa_after}, \
             attachments {attach_before}->{attach_after}"
        )));
    }

    // Ensure the marker table exists, then record the migration so it never
    // runs twice (R10.11).
    tx.execute_batch(
        "CREATE TABLE IF NOT EXISTS schema_migration(
            name TEXT PRIMARY KEY,
            applied_at INTEGER NOT NULL
        )",
    )
    .map_err(|e| ProfileError::Backend(e.to_string()))?;
    tx.execute(
        "INSERT OR IGNORE INTO schema_migration(name, applied_at) VALUES (?1, ?2)",
        params![OWNER_SCOPE_MIGRATION, chrono::Utc::now().timestamp()],
    )
    .map_err(|e| ProfileError::Backend(e.to_string()))?;

    Ok(MigrationReport {
        style_profiles: style_after,
        qa_pairs: qa_after,
        attachments: attach_after,
    })
}

#[cfg(test)]
mod tests;
