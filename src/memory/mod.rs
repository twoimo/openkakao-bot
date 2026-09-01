//! Conversation-memory store — the local-database CRUD that backs the memory
//! window (R8).
//!
//! The memory window lists two kinds of data and lets the user manage the first
//! (R8.2, R8.3, R8.4, R8.5):
//!
//! 1. **Notes** ([`MemoryKind::Note`]) — free-text memory the user adds, edits,
//!    and deletes. Notes live in the new `memory_note` table and are the only
//!    editable kind.
//! 2. **Reference packs** ([`MemoryKind::ReferencePack`]) — the derived
//!    what/how/why explanation packs already stored in
//!    `context_reference_packs`. These are read-only here; the window only lists
//!    them so the user can see what the agent remembers.
//!
//! It also surfaces the RAG comparison experiment results
//! ([`MemoryStore::rag_comparisons`], R8.3) so the memory window can list them
//! next to the memory data.
//!
//! **Validation and fail-closed behavior**
//!
//! * [`MemoryStore::upsert`] rejects an empty value and any value over
//!   [`MEMORY_NOTE_MAX_CHARS`] characters *before* touching the database, so a
//!   rejected edit never changes stored data (R8.6).
//! * Every write runs through the database in one statement; on a database
//!   failure the change is not applied and the existing data is preserved
//!   (R8.7). Validation happens first, so a validation failure is likewise a
//!   no-op.
//! * [`MemoryStore::delete`] assumes the UI already confirmed the deletion
//!   (R8.5); the store itself just deletes.
//!
//! **Testability**
//!
//! The store is backed by SQLite and can be opened in-memory or over a temp
//! file, so unit tests never touch a live Kakao database or the network.

use std::path::Path;

use rusqlite::{params, Connection};
use thiserror::Error;

/// The longest a single memory note may be, in characters (R8.6). Korean text
/// is counted by characters, not bytes, so this is a `chars().count()` limit.
pub const MEMORY_NOTE_MAX_CHARS: usize = 1000;

/// Which kind of memory a [`MemoryItem`] is.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MemoryKind {
    /// A free-text note the user manages (editable).
    Note,
    /// A derived what/how/why reference pack (read-only here).
    ReferencePack,
}

impl MemoryKind {
    /// A short, stable machine label.
    pub fn as_str(self) -> &'static str {
        match self {
            MemoryKind::Note => "note",
            MemoryKind::ReferencePack => "reference_pack",
        }
    }
}

/// One listed memory item (R8.2). For a [`MemoryKind::Note`], `updated_at` is
/// the Unix-epoch-millisecond time the note was last saved. For a
/// [`MemoryKind::ReferencePack`], `text` is the pack's plain-language "what"
/// summary and `updated_at` carries the pack's last log id as a recency key.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MemoryItem {
    /// Row id within its table.
    pub id: i64,
    /// Which kind of memory this is.
    pub kind: MemoryKind,
    /// The display text.
    pub text: String,
    /// Recency key used for ordering (see the type docs).
    pub updated_at: i64,
}

/// A draft note to add or edit (R8.4). `id` is `None` to add a new note and
/// `Some(id)` to edit an existing one.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MemoryDraft {
    /// The note being edited, or `None` to add a new note.
    pub id: Option<i64>,
    /// The note text. Validated before any write (R8.6).
    pub text: String,
}

impl MemoryDraft {
    /// A draft that adds a new note.
    pub fn new_note(text: impl Into<String>) -> Self {
        Self {
            id: None,
            text: text.into(),
        }
    }

    /// A draft that edits an existing note.
    pub fn edit(id: i64, text: impl Into<String>) -> Self {
        Self {
            id: Some(id),
            text: text.into(),
        }
    }
}

/// One RAG comparison experiment result to list in the memory window (R8.3).
/// Carries only the redacted metrics the experiment runner persisted — never a
/// generated answer or prompt.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RagComparisonRow {
    /// The run this measurement belongs to.
    pub run_id: String,
    /// Provider id.
    pub provider: String,
    /// Model id string.
    pub model: String,
    /// Question id.
    pub question_id: String,
    /// Measured round-trip latency in ms.
    pub latency_ms: u64,
    /// Local quality score 0..100.
    pub quality_score: u8,
    /// The prompt version this measurement used.
    pub prompt_version: String,
    /// The stored outcome string (`ok` | `failed:<reason>` | `timeout`).
    pub outcome: String,
}

/// Errors the memory store can return. Each carries a plain-language Korean
/// message suitable for showing a beginner (R8.6, R8.7, R10.3).
#[derive(Debug, Error)]
pub enum MemoryError {
    /// The value is empty, so the save is refused (R8.6).
    #[error("내용이 비어 있어요. 저장할 내용을 입력한 뒤 다시 시도해 주세요.")]
    Empty,
    /// The value is longer than [`MEMORY_NOTE_MAX_CHARS`] (R8.6).
    #[error("내용이 너무 길어요(최대 {max}자, 지금 {len}자). 조금 줄인 뒤 다시 시도해 주세요.")]
    TooLong {
        /// How many characters the value has.
        len: usize,
        /// The maximum allowed characters.
        max: usize,
    },
    /// The note to edit or delete no longer exists.
    #[error("해당 기억을 찾을 수 없어요. 목록을 새로 불러온 뒤 다시 시도해 주세요.")]
    NotFound,
    /// A database operation failed; the existing data is preserved (R8.7).
    #[error("작업을 끝내지 못했어요: {0}. 변경 내용은 저장되지 않았고 기존 데이터는 그대로 두었어요.")]
    Db(#[from] rusqlite::Error),
    /// A stored row could not be decoded.
    #[error("저장된 기억을 읽어 올 수 없어요: {0}")]
    Malformed(String),
}

/// The conversation-memory store (R8.2–R8.7).
pub trait MemoryStore {
    /// List every item of one kind (R8.2). Notes are newest-first; reference
    /// packs are most-recent-first by their recency key.
    fn list(&self, kind: MemoryKind) -> Result<Vec<MemoryItem>, MemoryError>;

    /// Add or edit a note (R8.4). Empty and over-limit values are refused
    /// before any write, so the existing data is preserved (R8.6).
    fn upsert(&self, draft: MemoryDraft) -> Result<MemoryItem, MemoryError>;

    /// Delete a note (R8.5). The UI is assumed to have already confirmed the
    /// deletion; the store just deletes.
    fn delete(&self, id: i64) -> Result<(), MemoryError>;

    /// List the RAG comparison experiment results (R8.3). Returns an empty list
    /// when no experiment has been run yet.
    fn rag_comparisons(&self) -> Result<Vec<RagComparisonRow>, MemoryError>;
}

/// Validate a note's text before any write (R8.6). Emptiness is decided on the
/// trimmed value; length is the character count of the value as given.
fn validate_note_text(text: &str) -> Result<(), MemoryError> {
    if text.trim().is_empty() {
        return Err(MemoryError::Empty);
    }
    let len = text.chars().count();
    if len > MEMORY_NOTE_MAX_CHARS {
        return Err(MemoryError::TooLong {
            len,
            max: MEMORY_NOTE_MAX_CHARS,
        });
    }
    Ok(())
}

/// Current wall-clock time in Unix-epoch milliseconds.
fn now_millis() -> i64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}

/// SQLite-backed [`MemoryStore`] over the `memory_note` table, with read-only
/// lookups into the existing `context_reference_packs` and `experiment_result`
/// tables that share the same database.
pub struct SqliteMemoryStore {
    conn: Connection,
}

impl SqliteMemoryStore {
    /// Wrap an existing connection, ensuring the `memory_note` schema is present.
    pub fn new(conn: Connection) -> Result<Self, MemoryError> {
        ensure_schema(&conn)?;
        Ok(Self { conn })
    }

    /// Open (or create) a store at `path`.
    pub fn open(path: &Path) -> Result<Self, MemoryError> {
        let conn = Connection::open(path)?;
        Self::new(conn)
    }

    /// Open an in-memory store. Primarily for tests.
    pub fn open_in_memory() -> Result<Self, MemoryError> {
        let conn = Connection::open_in_memory()?;
        Self::new(conn)
    }

    /// List the free-text notes, newest first.
    fn list_notes(&self) -> Result<Vec<MemoryItem>, MemoryError> {
        let mut stmt = self.conn.prepare(
            "SELECT id, text, updated_at
             FROM memory_note
             ORDER BY updated_at DESC, id DESC",
        )?;
        let rows = stmt.query_map([], |row| {
            Ok(MemoryItem {
                id: row.get::<_, i64>(0)?,
                kind: MemoryKind::Note,
                text: row.get::<_, String>(1)?,
                updated_at: row.get::<_, i64>(2)?,
            })
        })?;
        collect_rows(rows)
    }

    /// List the derived reference packs (read-only). Returns an empty list when
    /// the `context_reference_packs` table has not been created yet.
    fn list_reference_packs(&self) -> Result<Vec<MemoryItem>, MemoryError> {
        if !table_exists(&self.conn, "context_reference_packs")? {
            return Ok(Vec::new());
        }
        let mut stmt = self.conn.prepare(
            "SELECT id, what_text, end_log_id
             FROM context_reference_packs
             ORDER BY end_log_id DESC, id DESC",
        )?;
        let rows = stmt.query_map([], |row| {
            Ok(MemoryItem {
                id: row.get::<_, i64>(0)?,
                kind: MemoryKind::ReferencePack,
                text: row.get::<_, String>(1)?,
                updated_at: row.get::<_, i64>(2)?,
            })
        })?;
        collect_rows(rows)
    }
}

/// Collect a `query_map` iterator into a vec, mapping row errors to
/// [`MemoryError::Db`].
fn collect_rows<I>(rows: I) -> Result<Vec<MemoryItem>, MemoryError>
where
    I: Iterator<Item = rusqlite::Result<MemoryItem>>,
{
    let mut items = Vec::new();
    for row in rows {
        items.push(row?);
    }
    Ok(items)
}

/// True when a table with `name` exists in the connected database.
fn table_exists(conn: &Connection, name: &str) -> Result<bool, MemoryError> {
    let count: i64 = conn.query_row(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = ?1",
        params![name],
        |row| row.get(0),
    )?;
    Ok(count > 0)
}

/// Create the `memory_note` table if it does not exist.
fn ensure_schema(conn: &Connection) -> Result<(), MemoryError> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS memory_note(
            id INTEGER PRIMARY KEY,
            text TEXT NOT NULL CHECK(length(text) > 0),
            updated_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_memory_note_recent
            ON memory_note(updated_at DESC, id DESC);",
    )?;
    Ok(())
}

impl MemoryStore for SqliteMemoryStore {
    fn list(&self, kind: MemoryKind) -> Result<Vec<MemoryItem>, MemoryError> {
        match kind {
            MemoryKind::Note => self.list_notes(),
            MemoryKind::ReferencePack => self.list_reference_packs(),
        }
    }

    fn upsert(&self, draft: MemoryDraft) -> Result<MemoryItem, MemoryError> {
        // Validate before any write so a rejected edit never changes data (R8.6).
        validate_note_text(&draft.text)?;
        let now = now_millis();
        match draft.id {
            Some(id) => {
                let affected = self.conn.execute(
                    "UPDATE memory_note SET text = ?1, updated_at = ?2 WHERE id = ?3",
                    params![draft.text, now, id],
                )?;
                if affected == 0 {
                    return Err(MemoryError::NotFound);
                }
                Ok(MemoryItem {
                    id,
                    kind: MemoryKind::Note,
                    text: draft.text,
                    updated_at: now,
                })
            }
            None => {
                self.conn.execute(
                    "INSERT INTO memory_note(text, updated_at) VALUES (?1, ?2)",
                    params![draft.text, now],
                )?;
                Ok(MemoryItem {
                    id: self.conn.last_insert_rowid(),
                    kind: MemoryKind::Note,
                    text: draft.text,
                    updated_at: now,
                })
            }
        }
    }

    fn delete(&self, id: i64) -> Result<(), MemoryError> {
        // The UI confirmed the deletion (R8.5); just delete. A database failure
        // leaves the row in place (R8.7).
        self.conn
            .execute("DELETE FROM memory_note WHERE id = ?1", params![id])?;
        Ok(())
    }

    fn rag_comparisons(&self) -> Result<Vec<RagComparisonRow>, MemoryError> {
        if !table_exists(&self.conn, "experiment_result")? {
            return Ok(Vec::new());
        }
        let mut stmt = self.conn.prepare(
            "SELECT run_id, provider, model, question_id,
                    latency_ms, quality_score, prompt_version, outcome
             FROM experiment_result
             ORDER BY run_id ASC, question_id ASC, provider ASC, model ASC",
        )?;
        let rows = stmt.query_map([], |row| {
            Ok(RagComparisonRow {
                run_id: row.get::<_, String>(0)?,
                provider: row.get::<_, String>(1)?,
                model: row.get::<_, String>(2)?,
                question_id: row.get::<_, String>(3)?,
                latency_ms: row.get::<_, i64>(4)?.max(0) as u64,
                quality_score: row.get::<_, i64>(5)?.clamp(0, 100) as u8,
                prompt_version: row.get::<_, String>(6)?,
                outcome: row.get::<_, String>(7)?,
            })
        })?;

        let mut results = Vec::new();
        for row in rows {
            results.push(row?);
        }
        Ok(results)
    }
}

#[cfg(test)]
mod tests;
