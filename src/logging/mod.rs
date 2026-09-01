//! Redacted pipeline logging — the transition journal that backs the history
//! window (R2) and preserves the redacted-logging safety gate (R11.8).
//!
//! Every automatic-reply and GeekNews processing step emits a
//! [`PipelineEvent`] carrying only a short, non-sensitive shape: the flow, the
//! stage, its status, a machine-readable result code, how long the stage took,
//! and when it happened. Raw chat bodies, generated replies, prompts, URLs,
//! absolute file paths, and account-identifying information are **never**
//! persisted.
//!
//! The invariant is enforced *before* a record is stored, not merely by
//! convention. [`HistoryStore::append`] runs each event through
//! [`redact_event`], which reuses the existing provenance/absolute-path
//! redaction helpers from [`crate::context`]:
//!
//! * `trace_id` is redacted through [`crate::context::provenance_id`] — the same
//!   stable hashing the reply-decision journal uses in its `redact_provenance`
//!   step — so a grouping key can never carry raw content while still remaining
//!   deterministic (events from one flow keep grouping together).
//! * `result_code` is validated with a scanner that reuses
//!   [`crate::context::redact_absolute_paths`] as the canonical absolute-path
//!   detector, and additionally rejects URLs and free text that looks like a
//!   raw body or prompt. A violation fails closed: the event is refused and
//!   nothing is written.
//!
//! Because the store owns this check, Correctness Property 4 holds by
//! construction: no stored record can contain a raw body, prompt, URL, or
//! absolute path.

use serde::{Deserialize, Serialize};
use std::path::Path;

use rusqlite::{params, Connection, OptionalExtension};
use thiserror::Error;

/// Which processing flow a [`PipelineEvent`] belongs to.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FlowKind {
    /// The automatic-reply pipeline.
    AutoReply,
    /// The GeekNews immediate-send pipeline.
    GeekNews,
    /// The link-forwarding pipeline (R6).
    LinkForward,
    /// The Telegram-to-KakaoTalk relay pipeline (R7).
    TelegramRelay,
    /// The dataset incremental-refresh pipeline (R4).
    DatasetRefresh,
    /// The self-improvement pipeline (R8).
    SelfImprove,
    /// The room-by-feature coverage-verification pipeline (R5).
    Coverage,
    /// The bulk durability-harness pipeline (R1).
    Durability,
}

impl FlowKind {
    /// The stable string persisted in the `flow` column.
    pub fn as_str(self) -> &'static str {
        match self {
            FlowKind::AutoReply => "auto_reply",
            FlowKind::GeekNews => "geeknews",
            FlowKind::LinkForward => "link_forward",
            FlowKind::TelegramRelay => "telegram_relay",
            FlowKind::DatasetRefresh => "dataset_refresh",
            FlowKind::SelfImprove => "self_improve",
            FlowKind::Coverage => "coverage",
            FlowKind::Durability => "durability",
        }
    }

    fn parse(value: &str) -> Option<Self> {
        match value {
            "auto_reply" => Some(FlowKind::AutoReply),
            "geeknews" => Some(FlowKind::GeekNews),
            "link_forward" => Some(FlowKind::LinkForward),
            "telegram_relay" => Some(FlowKind::TelegramRelay),
            "dataset_refresh" => Some(FlowKind::DatasetRefresh),
            "self_improve" => Some(FlowKind::SelfImprove),
            "coverage" => Some(FlowKind::Coverage),
            "durability" => Some(FlowKind::Durability),
            _ => None,
        }
    }
}

/// A single stage in a processing flow, in the order it can occur.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Stage {
    /// A new message / feed item was detected.
    Detect,
    /// Identity/target/state authorization was evaluated.
    Authorize,
    /// Context and style were retrieved.
    Retrieve,
    /// The model was called to generate a reply.
    Model,
    /// A delayed send was scheduled.
    Schedule,
    /// The final pre-send safety check ran.
    PreSend,
    /// The send was committed.
    Commit,
}

impl Stage {
    /// The stable string persisted in the `stage` column.
    pub fn as_str(self) -> &'static str {
        match self {
            Stage::Detect => "detect",
            Stage::Authorize => "authorize",
            Stage::Retrieve => "retrieve",
            Stage::Model => "model",
            Stage::Schedule => "schedule",
            Stage::PreSend => "pre_send",
            Stage::Commit => "commit",
        }
    }

    fn parse(value: &str) -> Option<Self> {
        match value {
            "detect" => Some(Stage::Detect),
            "authorize" => Some(Stage::Authorize),
            "retrieve" => Some(Stage::Retrieve),
            "model" => Some(Stage::Model),
            "schedule" => Some(Stage::Schedule),
            "pre_send" => Some(Stage::PreSend),
            "commit" => Some(Stage::Commit),
            _ => None,
        }
    }
}

/// The status of a stage.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StageStatus {
    /// The stage is running.
    InProgress,
    /// The stage completed successfully.
    Success,
    /// The stage failed.
    Failed,
}

impl StageStatus {
    /// The stable string persisted in the `status` column.
    pub fn as_str(self) -> &'static str {
        match self {
            StageStatus::InProgress => "in_progress",
            StageStatus::Success => "success",
            StageStatus::Failed => "failed",
        }
    }

    fn parse(value: &str) -> Option<Self> {
        match value {
            "in_progress" => Some(StageStatus::InProgress),
            "success" => Some(StageStatus::Success),
            "failed" => Some(StageStatus::Failed),
            _ => None,
        }
    }
}

/// A redacted transition-journal record for one processing stage (R2.1, R2.2,
/// R2.9). The struct intentionally holds no field that can carry a raw chat
/// body, generated reply, prompt, URL, absolute path, or account identifier.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PipelineEvent {
    /// Groups all stages of one processing flow together. Redacted through
    /// [`crate::context::provenance_id`] before storage.
    pub trace_id: String,
    /// Which flow this event belongs to.
    pub flow: FlowKind,
    /// Which stage this event describes.
    pub stage: Stage,
    /// The status of the stage.
    pub status: StageStatus,
    /// A short, machine-readable outcome code (no sensitive content).
    pub result_code: String,
    /// How long the stage took, in milliseconds.
    pub duration_ms: u64,
    /// Wall-clock time the stage occurred (Unix epoch milliseconds).
    pub at: i64,
}

/// Longest a `result_code` may be. Anything longer is treated as raw body /
/// prompt text and refused — result codes are short, stable tokens.
const RESULT_CODE_MAX_LEN: usize = 64;

/// Why an event failed redaction validation and was refused. Callers can map
/// this to a plain-language explanation without guessing.
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum RedactionError {
    /// A field contained something that looks like a URL.
    #[error("field `{field}` may not contain a URL")]
    ContainsUrl { field: &'static str },
    /// A field contained an absolute filesystem path.
    #[error("field `{field}` may not contain an absolute path")]
    ContainsAbsolutePath { field: &'static str },
    /// A field looked like raw body or prompt text (too long or multi-line).
    #[error("field `{field}` looks like raw body or prompt text")]
    LooksLikeRawContent { field: &'static str },
}

/// Errors returned by a [`HistoryStore`].
#[derive(Debug, Error)]
pub enum StoreError {
    /// An event was refused because it failed redaction validation. Fail-closed:
    /// nothing is written.
    #[error("pipeline event failed redaction validation: {0}")]
    Redaction(#[from] RedactionError),
    /// The underlying database returned an error.
    #[error("history store database error: {0}")]
    Db(#[from] rusqlite::Error),
    /// A stored row could not be decoded back into a [`PipelineEvent`].
    #[error("stored pipeline event row is malformed: {0}")]
    Malformed(String),
}

// ---------------------------------------------------------------------------
// Transition-journal event-id gate — a parity port of the Python
// `auto_reply_transition_journal.validated_event_id` (Task 13, R11.10).
// ---------------------------------------------------------------------------
//
// The Python transition journal remains the runtime authority for the reply
// queue (it owns the SQLite schema migration, triggers, and room binding used by
// `auto-reply-worker.py`, `auto-reply-supervisor.py`, and `auto-reply-db-watch.py`),
// so it is *not* removed. What is safe to port and pin behaviorally is the pure
// allow/deny gate that decides whether a `db:<chat_id>:<log_id>` event id is
// accepted. `validate_event_id` reproduces that gate exactly so a parity test
// can prove identical allow/deny results for the same inputs.

/// The exclusive upper bound the transition journal applies to chat/log ids:
/// an id must satisfy `0 < id < MAX_INT64`. This equals Python's
/// `MAX_INT64 = 2**63 - 1`.
pub const MAX_INT64: i64 = i64::MAX;

/// Why an event id was rejected by [`validate_event_id`]. The Python original
/// raises `ValueError` in every one of these cases; the split into variants is
/// purely for Rust callers and does not change the allow/deny result.
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum EventIdError {
    /// The id does not match the `db:<digits>:<digits>` shape (wrong prefix,
    /// leading zero, extra fields, whitespace, non-digits, …).
    #[error("transition event_id is invalid")]
    Malformed,
    /// A parsed chat/log id is outside `0 < id < MAX_INT64`.
    #[error("transition event_id is invalid")]
    OutOfRange,
    /// The caller-supplied `expected_chat_id` is itself invalid.
    #[error("transition expected chat_id is invalid")]
    ExpectedInvalid,
    /// The id is well-formed but belongs to a different room than expected.
    #[error("transition event_id belongs to another room")]
    WrongRoom,
}

/// Validate that `field` is a `[1-9][0-9]{0,18}` decimal (1..=19 digits, no
/// leading zero) and parse it, mirroring the Python regex + `int()` step.
fn parse_id_field(field: &str) -> Result<i128, EventIdError> {
    let len = field.len();
    if !(1..=19).contains(&len) {
        return Err(EventIdError::Malformed);
    }
    let mut chars = field.bytes();
    let first = chars.next().ok_or(EventIdError::Malformed)?;
    if !(b'1'..=b'9').contains(&first) {
        return Err(EventIdError::Malformed);
    }
    if !chars.all(|b| b.is_ascii_digit()) {
        return Err(EventIdError::Malformed);
    }
    field.parse::<i128>().map_err(|_| EventIdError::Malformed)
}

/// Validate one canonical database event id, optionally bound to one room —
/// a parity port of Python `validated_event_id`.
///
/// Accepts only `db:<chat_id>:<log_id>` where each id is a no-leading-zero
/// decimal satisfying `0 < id < MAX_INT64`. When `expected_chat_id` is
/// `Some`, the chat id must equal it (and the expected value must itself be in
/// `0 < v < MAX_INT64`), otherwise the id is rejected as belonging to another
/// room. Returns the input string on success so callers can use it directly.
pub fn validate_event_id(
    value: &str,
    expected_chat_id: Option<i64>,
) -> Result<&str, EventIdError> {
    // `_validated_expected_chat_id`: the expected room id, if given, must be in
    // range before it can be compared (Python validates it up front).
    if let Some(expected) = expected_chat_id {
        if !(0 < expected && expected < MAX_INT64) {
            return Err(EventIdError::ExpectedInvalid);
        }
    }
    let mut parts = value.split(':');
    let (Some(prefix), Some(chat_raw), Some(log_raw), None) =
        (parts.next(), parts.next(), parts.next(), parts.next())
    else {
        return Err(EventIdError::Malformed);
    };
    if prefix != "db" {
        return Err(EventIdError::Malformed);
    }
    let chat_id = parse_id_field(chat_raw)?;
    let log_id = parse_id_field(log_raw)?;
    let max = MAX_INT64 as i128;
    if !(0 < chat_id && chat_id < max) || !(0 < log_id && log_id < max) {
        return Err(EventIdError::OutOfRange);
    }
    if let Some(expected) = expected_chat_id {
        if chat_id != expected as i128 {
            return Err(EventIdError::WrongRoom);
        }
    }
    Ok(value)
}

/// The transition journal that stores and reads back redacted pipeline events.
pub trait HistoryStore {
    /// The most recent events, newest first, capped at `limit` (R2.3).
    fn recent(&self, limit: usize) -> Result<Vec<PipelineEvent>, StoreError>;

    /// Append an event. Redaction validation must pass first (R2.8, R11.8): the
    /// event is redacted and validated before any write, and a violating event
    /// is refused without touching the store.
    fn append(&self, ev: PipelineEvent) -> Result<(), StoreError>;

    /// Append an event together with the redacted image counts a flow reports
    /// (R6.11, R7.16): the source image count and the successfully-delivered
    /// image count. The two counts are plain integers, so they carry no chat
    /// body, reply, prompt, URL, path, or account identifier; the event itself
    /// still passes the same redaction validation as [`Self::append`].
    ///
    /// The default implementation ignores the counts and falls back to
    /// [`Self::append`], so an implementation that has no image columns keeps
    /// working unchanged; [`SqliteHistoryStore`] overrides it to persist the
    /// two columns.
    fn append_with_images(
        &self,
        ev: PipelineEvent,
        images_source: Option<u32>,
        images_delivered: Option<u32>,
    ) -> Result<(), StoreError> {
        let _ = (images_source, images_delivered);
        self.append(ev)
    }
}

/// True when `text` contains something that looks like a URL. Mirrors the URL
/// detection already used by the context indexer.
fn contains_url(text: &str) -> bool {
    let lower = text.to_ascii_lowercase();
    lower.contains("http://")
        || lower.contains("https://")
        || lower.contains("www.")
        || lower.contains("://")
}

/// True when `token` is an absolute path, decided by the same helper the
/// context layer uses to redact absolute paths. Reusing the helper keeps the
/// definition of "absolute path" identical across the codebase.
fn is_absolute_path(token: &str) -> bool {
    if token.is_empty() {
        return false;
    }
    let original = serde_json::Value::String(token.to_string());
    let mut candidate = original.clone();
    crate::context::redact_absolute_paths(&mut candidate);
    // If the helper rewrote the value, it recognized an absolute path.
    candidate != original
}

/// True when every character of `code` belongs to the machine-code alphabet: an
/// ASCII alphanumeric or one of `_`, `-`, `.`, `:`. Anything else (whitespace,
/// non-ASCII, `/`, `=`, `?`, …) means the value is not a redacted short code but
/// free text — a raw body, prompt, or account identifier.
fn is_machine_code(code: &str) -> bool {
    !code.is_empty()
        && code
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '-' | '.' | ':'))
}

/// Validate that a `result_code` is a short, redacted machine token.
///
/// The URL and absolute-path checks come first so a violation gets a precise
/// reason (the absolute-path check reuses [`crate::context::redact_absolute_paths`]).
/// The final machine-code allowlist is the fail-closed catch-all: any free text
/// — including a short raw chat body, prompt, or account identifier that carries
/// no URL or path — is refused because a result code is a stable token, never
/// prose.
fn validate_result_code(code: &str) -> Result<(), RedactionError> {
    const FIELD: &str = "result_code";
    if code.len() > RESULT_CODE_MAX_LEN || code.contains('\n') || code.contains('\r') {
        return Err(RedactionError::LooksLikeRawContent { field: FIELD });
    }
    if contains_url(code) {
        return Err(RedactionError::ContainsUrl { field: FIELD });
    }
    if is_absolute_path(code) || code.split_whitespace().any(is_absolute_path) {
        return Err(RedactionError::ContainsAbsolutePath { field: FIELD });
    }
    if !is_machine_code(code) {
        return Err(RedactionError::LooksLikeRawContent { field: FIELD });
    }
    Ok(())
}

/// Redact and validate an event before it is stored.
///
/// `trace_id` is always hashed through [`crate::context::provenance_id`] so it
/// can never carry raw content (and stays deterministic for grouping).
/// `result_code` is validated and refused if it carries a URL, absolute path,
/// or raw-looking text. The returned event is the exact value that will be
/// persisted.
pub fn redact_event(ev: &PipelineEvent) -> Result<PipelineEvent, RedactionError> {
    validate_result_code(&ev.result_code)?;
    Ok(PipelineEvent {
        trace_id: crate::context::provenance_id(&ev.trace_id),
        flow: ev.flow,
        stage: ev.stage,
        status: ev.status,
        result_code: ev.result_code.clone(),
        duration_ms: ev.duration_ms,
        at: ev.at,
    })
}

/// SQLite-backed [`HistoryStore`] over the `pipeline_events` table.
pub struct SqliteHistoryStore {
    conn: Connection,
}

impl SqliteHistoryStore {
    /// Wrap an existing connection, ensuring the schema is present.
    pub fn new(conn: Connection) -> Result<Self, StoreError> {
        ensure_schema(&conn)?;
        Ok(Self { conn })
    }

    /// Open (or create) a store at `path`.
    pub fn open(path: &Path) -> Result<Self, StoreError> {
        let conn = Connection::open(path)?;
        Self::new(conn)
    }

    /// Open an in-memory store. Primarily for tests.
    pub fn open_in_memory() -> Result<Self, StoreError> {
        let conn = Connection::open_in_memory()?;
        Self::new(conn)
    }
}

/// The current `pipeline_events` table definition: the full set of allowed
/// flows (the two original plus the six live-ops flows) and the nullable
/// `images_source` / `images_delivered` columns (R6.11, R7.16, R12.4).
///
/// `CREATE TABLE IF NOT EXISTS` makes a fresh database land directly on this
/// shape; an existing database on the old two-flow shape is upgraded by
/// [`migrate_pipeline_events`].
const CREATE_PIPELINE_EVENTS_SQL: &str = "CREATE TABLE IF NOT EXISTS pipeline_events(
    id INTEGER PRIMARY KEY,
    trace_id TEXT NOT NULL,
    flow TEXT NOT NULL CHECK(flow IN (
        'auto_reply', 'geeknews', 'link_forward', 'telegram_relay',
        'dataset_refresh', 'self_improve', 'coverage', 'durability'
    )),
    stage TEXT NOT NULL CHECK(stage IN (
        'detect', 'authorize', 'retrieve', 'model', 'schedule', 'pre_send', 'commit'
    )),
    status TEXT NOT NULL CHECK(status IN ('in_progress', 'success', 'failed')),
    result_code TEXT NOT NULL,
    duration_ms INTEGER NOT NULL CHECK(duration_ms >= 0),
    images_source INTEGER,
    images_delivered INTEGER,
    at INTEGER NOT NULL
);";

/// The index over `pipeline_events` used by [`SqliteHistoryStore::recent`].
const CREATE_PIPELINE_EVENTS_INDEX_SQL: &str = "CREATE INDEX IF NOT EXISTS idx_pipeline_events_recent
    ON pipeline_events(at DESC, id DESC);";

/// The stored-DDL markers that must all be present for the table to be
/// considered up to date: the six new flow values plus the two image columns.
const PIPELINE_EVENTS_SCHEMA_MARKERS: [&str; 8] = [
    "link_forward",
    "telegram_relay",
    "dataset_refresh",
    "self_improve",
    "coverage",
    "durability",
    "images_source",
    "images_delivered",
];

/// Create the `pipeline_events` table and its index if they do not exist, then
/// upgrade a legacy (two-flow, no-image-columns) table in place.
fn ensure_schema(conn: &Connection) -> Result<(), StoreError> {
    conn.execute_batch(CREATE_PIPELINE_EVENTS_SQL)?;
    conn.execute_batch(CREATE_PIPELINE_EVENTS_INDEX_SQL)?;
    migrate_pipeline_events(conn)?;
    Ok(())
}

/// Upgrade an existing `pipeline_events` table to the current shape.
///
/// SQLite cannot widen a `CHECK` constraint or add a column with `ALTER`, so
/// the widened `flow` constraint and the two image columns are applied by a
/// full table rebuild inside **one transaction**: create the new table, copy
/// every row with `INSERT INTO … SELECT`, verify the row count is unchanged,
/// drop the old table, rename the new one into place, and recreate the index.
/// If anything fails (including a row-count mismatch), the transaction is
/// rolled back and the original table is left untouched.
fn migrate_pipeline_events(conn: &Connection) -> Result<(), StoreError> {
    let existing_sql: Option<String> = conn
        .query_row(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'pipeline_events'",
            [],
            |row| row.get(0),
        )
        .optional()?;
    let Some(sql) = existing_sql else {
        // The table was just created with the current shape.
        return Ok(());
    };
    if PIPELINE_EVENTS_SCHEMA_MARKERS
        .iter()
        .all(|marker| sql.contains(marker))
    {
        // Already on the current shape; nothing to do.
        return Ok(());
    }

    let tx = conn.unchecked_transaction()?;
    let before: i64 =
        tx.query_row("SELECT COUNT(*) FROM pipeline_events", [], |row| row.get(0))?;

    // New table under a temporary name, carrying the widened flow constraint
    // and the two nullable image columns.
    tx.execute_batch(
        "CREATE TABLE pipeline_events_migrated(
            id INTEGER PRIMARY KEY,
            trace_id TEXT NOT NULL,
            flow TEXT NOT NULL CHECK(flow IN (
                'auto_reply', 'geeknews', 'link_forward', 'telegram_relay',
                'dataset_refresh', 'self_improve', 'coverage', 'durability'
            )),
            stage TEXT NOT NULL CHECK(stage IN (
                'detect', 'authorize', 'retrieve', 'model', 'schedule', 'pre_send', 'commit'
            )),
            status TEXT NOT NULL CHECK(status IN ('in_progress', 'success', 'failed')),
            result_code TEXT NOT NULL,
            duration_ms INTEGER NOT NULL CHECK(duration_ms >= 0),
            images_source INTEGER,
            images_delivered INTEGER,
            at INTEGER NOT NULL
        );
        INSERT INTO pipeline_events_migrated(
            id, trace_id, flow, stage, status, result_code, duration_ms, at
        )
        SELECT id, trace_id, flow, stage, status, result_code, duration_ms, at
        FROM pipeline_events;",
    )?;

    let after: i64 = tx.query_row(
        "SELECT COUNT(*) FROM pipeline_events_migrated",
        [],
        |row| row.get(0),
    )?;
    if before != after {
        // `tx` is dropped here, rolling the rebuild back and leaving the
        // original table intact.
        return Err(StoreError::Malformed(format!(
            "pipeline_events migration copied {after} rows but expected {before}"
        )));
    }

    tx.execute_batch(
        "DROP TABLE pipeline_events;
         ALTER TABLE pipeline_events_migrated RENAME TO pipeline_events;
         CREATE INDEX IF NOT EXISTS idx_pipeline_events_recent
            ON pipeline_events(at DESC, id DESC);",
    )?;
    tx.commit()?;
    Ok(())
}

impl HistoryStore for SqliteHistoryStore {
    fn append(&self, ev: PipelineEvent) -> Result<(), StoreError> {
        // Fail-closed: redaction validation happens before any write.
        let redacted = redact_event(&ev)?;
        self.conn.execute(
            "INSERT INTO pipeline_events(
                trace_id, flow, stage, status, result_code, duration_ms, at
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![
                redacted.trace_id,
                redacted.flow.as_str(),
                redacted.stage.as_str(),
                redacted.status.as_str(),
                redacted.result_code,
                redacted.duration_ms as i64,
                redacted.at,
            ],
        )?;
        Ok(())
    }

    fn append_with_images(
        &self,
        ev: PipelineEvent,
        images_source: Option<u32>,
        images_delivered: Option<u32>,
    ) -> Result<(), StoreError> {
        // Fail-closed: the same redaction validation runs before any write. The
        // two image counts are plain integers written into the dedicated
        // nullable columns (R6.11, R7.16, R12.4).
        let redacted = redact_event(&ev)?;
        self.conn.execute(
            "INSERT INTO pipeline_events(
                trace_id, flow, stage, status, result_code, duration_ms,
                images_source, images_delivered, at
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![
                redacted.trace_id,
                redacted.flow.as_str(),
                redacted.stage.as_str(),
                redacted.status.as_str(),
                redacted.result_code,
                redacted.duration_ms as i64,
                images_source.map(|v| v as i64),
                images_delivered.map(|v| v as i64),
                redacted.at,
            ],
        )?;
        Ok(())
    }

    fn recent(&self, limit: usize) -> Result<Vec<PipelineEvent>, StoreError> {
        let mut stmt = self.conn.prepare(
            "SELECT trace_id, flow, stage, status, result_code, duration_ms, at
             FROM pipeline_events
             ORDER BY at DESC, id DESC
             LIMIT ?1",
        )?;
        let rows = stmt.query_map(params![limit as i64], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, String>(4)?,
                row.get::<_, i64>(5)?,
                row.get::<_, i64>(6)?,
            ))
        })?;

        let mut events = Vec::new();
        for row in rows {
            let (trace_id, flow, stage, status, result_code, duration_ms, at) = row?;
            let flow = FlowKind::parse(&flow)
                .ok_or_else(|| StoreError::Malformed(format!("unknown flow `{flow}`")))?;
            let stage = Stage::parse(&stage)
                .ok_or_else(|| StoreError::Malformed(format!("unknown stage `{stage}`")))?;
            let status = StageStatus::parse(&status)
                .ok_or_else(|| StoreError::Malformed(format!("unknown status `{status}`")))?;
            if duration_ms < 0 {
                return Err(StoreError::Malformed(
                    "duration_ms stored as negative".to_string(),
                ));
            }
            events.push(PipelineEvent {
                trace_id,
                flow,
                stage,
                status,
                result_code,
                duration_ms: duration_ms as u64,
                at,
            });
        }
        Ok(events)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample(trace_id: &str, result_code: &str) -> PipelineEvent {
        PipelineEvent {
            trace_id: trace_id.to_string(),
            flow: FlowKind::AutoReply,
            stage: Stage::Detect,
            status: StageStatus::InProgress,
            result_code: result_code.to_string(),
            duration_ms: 12,
            at: 1_700_000_000_000,
        }
    }

    #[test]
    fn validate_event_id_allow_deny_matches_python_rules() {
        // Well-formed ids in range are accepted.
        assert!(validate_event_id("db:1:1", None).is_ok());
        assert!(validate_event_id("db:42:7", Some(42)).is_ok());
        assert!(validate_event_id(
            &format!("db:{}:{}", MAX_INT64 - 1, MAX_INT64 - 1),
            None
        )
        .is_ok());
        // Room binding: a well-formed id from another room is denied.
        assert_eq!(
            validate_event_id("db:42:7", Some(84)),
            Err(EventIdError::WrongRoom)
        );
        // Malformed shapes: leading zero, extra field, injection, wrong prefix.
        for bad in ["db:042:1", "db:42:1:2", "db:42:1 OR 1=1", "DB:1:1", "", "db:42:0"] {
            assert!(
                validate_event_id(bad, None).is_err(),
                "{bad:?} must be denied"
            );
        }
        // Out-of-range: == MAX_INT64 and 19-digit overflow.
        assert_eq!(
            validate_event_id(&format!("db:{MAX_INT64}:1"), None),
            Err(EventIdError::OutOfRange)
        );
        assert_eq!(
            validate_event_id("db:42:9999999999999999999", None),
            Err(EventIdError::OutOfRange)
        );
        // An invalid expected room id is rejected before any comparison.
        assert_eq!(
            validate_event_id("db:1:1", Some(0)),
            Err(EventIdError::ExpectedInvalid)
        );
    }

    #[test]
    fn append_then_recent_roundtrips_a_clean_event() {
        let store = SqliteHistoryStore::open_in_memory().expect("open");
        store.append(sample("flow-abc", "detected_ok")).expect("append");

        let recent = store.recent(10).expect("recent");
        assert_eq!(recent.len(), 1);
        let ev = &recent[0];
        assert_eq!(ev.flow, FlowKind::AutoReply);
        assert_eq!(ev.stage, Stage::Detect);
        assert_eq!(ev.status, StageStatus::InProgress);
        assert_eq!(ev.result_code, "detected_ok");
        assert_eq!(ev.duration_ms, 12);
    }

    #[test]
    fn trace_id_is_redacted_to_a_provenance_hash() {
        let store = SqliteHistoryStore::open_in_memory().expect("open");
        store.append(sample("flow-abc", "ok")).expect("append");
        let recent = store.recent(1).expect("recent");
        // The stored trace id is the stable provenance hash, not the raw input.
        assert_ne!(recent[0].trace_id, "flow-abc");
        assert_eq!(recent[0].trace_id, crate::context::provenance_id("flow-abc"));
    }

    #[test]
    fn recent_returns_newest_first() {
        let store = SqliteHistoryStore::open_in_memory().expect("open");
        let mut older = sample("flow-1", "first");
        older.at = 1_000;
        let mut newer = sample("flow-2", "second");
        newer.at = 2_000;
        store.append(older).expect("append older");
        store.append(newer).expect("append newer");

        let recent = store.recent(10).expect("recent");
        assert_eq!(recent[0].result_code, "second");
        assert_eq!(recent[1].result_code, "first");
    }

    #[test]
    fn append_refuses_url_result_code_and_stores_nothing() {
        let store = SqliteHistoryStore::open_in_memory().expect("open");
        let err = store
            .append(sample("flow", "see https://example.com/x"))
            .expect_err("should refuse url");
        assert!(matches!(
            err,
            StoreError::Redaction(RedactionError::ContainsUrl { .. })
        ));
        assert!(store.recent(10).expect("recent").is_empty());
    }

    #[test]
    fn append_refuses_absolute_path_result_code() {
        let store = SqliteHistoryStore::open_in_memory().expect("open");
        let err = store
            .append(sample("flow", "/Users/owner/secret.db"))
            .expect_err("should refuse absolute path");
        assert!(matches!(
            err,
            StoreError::Redaction(RedactionError::ContainsAbsolutePath { .. })
        ));
        assert!(store.recent(10).expect("recent").is_empty());
    }

    #[test]
    fn append_refuses_raw_body_result_code() {
        let store = SqliteHistoryStore::open_in_memory().expect("open");
        let long = "x".repeat(RESULT_CODE_MAX_LEN + 1);
        let err = store
            .append(sample("flow", &long))
            .expect_err("should refuse long code");
        assert!(matches!(
            err,
            StoreError::Redaction(RedactionError::LooksLikeRawContent { .. })
        ));

        let multiline = store
            .append(sample("flow", "line1\nline2"))
            .expect_err("should refuse multiline code");
        assert!(matches!(
            multiline,
            StoreError::Redaction(RedactionError::LooksLikeRawContent { .. })
        ));
        assert!(store.recent(10).expect("recent").is_empty());
    }

    #[test]
    fn flow_kind_as_str_and_parse_roundtrip_all_variants() {
        for flow in [
            FlowKind::AutoReply,
            FlowKind::GeekNews,
            FlowKind::LinkForward,
            FlowKind::TelegramRelay,
            FlowKind::DatasetRefresh,
            FlowKind::SelfImprove,
            FlowKind::Coverage,
            FlowKind::Durability,
        ] {
            assert_eq!(FlowKind::parse(flow.as_str()), Some(flow));
        }
        assert_eq!(FlowKind::parse("not_a_flow"), None);
    }

    #[test]
    fn append_and_recent_accept_new_flows() {
        let store = SqliteHistoryStore::open_in_memory().expect("open");
        for flow in [
            FlowKind::LinkForward,
            FlowKind::TelegramRelay,
            FlowKind::DatasetRefresh,
            FlowKind::SelfImprove,
            FlowKind::Coverage,
            FlowKind::Durability,
        ] {
            let mut ev = sample("flow", "ok");
            ev.flow = flow;
            store.append(ev).expect("append new flow");
        }
        let recent = store.recent(10).expect("recent");
        assert_eq!(recent.len(), 6);
    }

    #[test]
    fn append_with_images_persists_the_two_counts() {
        let store = SqliteHistoryStore::open_in_memory().expect("open");
        let mut ev = sample("flow-img", "forward_ok");
        ev.flow = FlowKind::LinkForward;
        store
            .append_with_images(ev, Some(5), Some(3))
            .expect("append with images");

        let (src, deliv): (Option<i64>, Option<i64>) = store
            .conn
            .query_row(
                "SELECT images_source, images_delivered FROM pipeline_events",
                [],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .expect("read counts");
        assert_eq!(src, Some(5));
        assert_eq!(deliv, Some(3));
        // The event still reads back through the normal path.
        assert_eq!(store.recent(1).expect("recent").len(), 1);
    }

    #[test]
    fn append_with_images_still_refuses_leaky_result_code() {
        let store = SqliteHistoryStore::open_in_memory().expect("open");
        let err = store
            .append_with_images(sample("flow", "https://leak.example/x"), Some(1), Some(1))
            .expect_err("should refuse url");
        assert!(matches!(
            err,
            StoreError::Redaction(RedactionError::ContainsUrl { .. })
        ));
        assert!(store.recent(10).expect("recent").is_empty());
    }

    #[test]
    fn fresh_schema_has_image_columns() {
        let store = SqliteHistoryStore::open_in_memory().expect("open");
        let columns: Vec<String> = store
            .conn
            .prepare("SELECT name FROM pragma_table_info('pipeline_events')")
            .expect("prepare")
            .query_map([], |row| row.get::<_, String>(0))
            .expect("query")
            .map(|r| r.expect("row"))
            .collect();
        assert!(columns.iter().any(|c| c == "images_source"));
        assert!(columns.iter().any(|c| c == "images_delivered"));
    }

    #[test]
    fn migrates_legacy_two_flow_table_preserving_rows() {
        let conn = Connection::open_in_memory().expect("open");
        // Recreate the old, pre-live-ops schema (two flows, no image columns).
        conn.execute_batch(
            "CREATE TABLE pipeline_events(
                id INTEGER PRIMARY KEY,
                trace_id TEXT NOT NULL,
                flow TEXT NOT NULL CHECK(flow IN ('auto_reply', 'geeknews')),
                stage TEXT NOT NULL CHECK(stage IN (
                    'detect', 'authorize', 'retrieve', 'model', 'schedule', 'pre_send', 'commit'
                )),
                status TEXT NOT NULL CHECK(status IN ('in_progress', 'success', 'failed')),
                result_code TEXT NOT NULL,
                duration_ms INTEGER NOT NULL CHECK(duration_ms >= 0),
                at INTEGER NOT NULL
            );
            CREATE INDEX idx_pipeline_events_recent ON pipeline_events(at DESC, id DESC);
            INSERT INTO pipeline_events(trace_id, flow, stage, status, result_code, duration_ms, at)
                VALUES ('t1', 'auto_reply', 'detect', 'success', 'ok_a', 5, 1000),
                       ('t2', 'geeknews', 'commit', 'success', 'ok_b', 7, 2000);",
        )
        .expect("seed legacy schema");

        // Wrapping in the store runs ensure_schema → the rebuild migration.
        let store = SqliteHistoryStore::new(conn).expect("migrate");

        // Row count preserved and legacy rows readable.
        let recent = store.recent(10).expect("recent");
        assert_eq!(recent.len(), 2);
        assert_eq!(recent[0].result_code, "ok_b");
        assert_eq!(recent[1].result_code, "ok_a");

        // New columns exist.
        let columns: Vec<String> = store
            .conn
            .prepare("SELECT name FROM pragma_table_info('pipeline_events')")
            .expect("prepare")
            .query_map([], |row| row.get::<_, String>(0))
            .expect("query")
            .map(|r| r.expect("row"))
            .collect();
        assert!(columns.iter().any(|c| c == "images_source"));
        assert!(columns.iter().any(|c| c == "images_delivered"));

        // A new flow that the old CHECK would have rejected now inserts.
        let mut ev = sample("t3", "ok_c");
        ev.flow = FlowKind::TelegramRelay;
        store.append(ev).expect("append new flow after migration");
        assert_eq!(store.recent(10).expect("recent").len(), 3);
    }

    #[test]
    fn migration_is_idempotent_on_current_schema() {
        let store = SqliteHistoryStore::open_in_memory().expect("open");
        store.append(sample("t", "ok")).expect("append");
        // Running ensure_schema again must not rebuild or lose rows.
        ensure_schema(&store.conn).expect("re-ensure");
        assert_eq!(store.recent(10).expect("recent").len(), 1);
    }

    #[test]
    fn append_refuses_short_free_text_result_code() {
        let store = SqliteHistoryStore::open_in_memory().expect("open");
        // A short raw chat body with no URL or path must still be refused —
        // a result code is a machine token, never prose.
        for prose in ["안녕하세요 비밀 메시지", "account_fp=owner-1234", "hi there"] {
            let err = store
                .append(sample("flow", prose))
                .expect_err("should refuse free text");
            assert!(matches!(
                err,
                StoreError::Redaction(RedactionError::LooksLikeRawContent { .. })
            ));
        }
        assert!(store.recent(10).expect("recent").is_empty());
    }
}
