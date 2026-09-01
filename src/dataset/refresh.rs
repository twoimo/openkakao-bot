//! Periodic incremental dataset refresh (task 5) — R4.
//!
//! While the app is running, newly accumulated KakaoTalk conversations must be
//! reflected into the dataset and vectors without any manual step, so replies
//! always draw on the latest talk (R4). This module reads the local KakaoTalk
//! database again on a schedule, processes only the messages after the last
//! successful [`RefreshCursor`], and commits the dataset reflection **and** the
//! cursor move as one transaction.
//!
//! Four structural guarantees hold here:
//!
//! * **Atomic reflection (R4.4, R4.9).** [`DatasetRefresher::refresh_now`]
//!   appends the newly built Q&A pairs and attachment references and advances
//!   the [`RefreshCursor`] inside a **single** transaction on one connection. A
//!   reply processing that started before the commit reads only the pre-refresh
//!   state; one that starts after reads only the post-refresh state. There is no
//!   window where a partial reflection is visible.
//! * **Local only (R4.10, R4.11, R4.14).** The refresh refuses to start unless
//!   the injected [`Embedder`] reports [`Embedder::is_local_only`] `== true`,
//!   and it performs no network I/O. A [`NetworkPort`] is injected so the
//!   harness can assert egress stayed at zero for the whole refresh.
//! * **Owner excluded from reply targets (R4.12).** Every message's owner flag
//!   is re-derived through [`crate::profile::is_owner_message`], so an
//!   owner-sent message is never treated as a question (a reply target) but is
//!   still used for context and style learning.
//! * **Fail-closed, state-preserving (R4.6, R4.7, R4.15, R4.16).** A missing or
//!   unparseable cursor processes the full range; KakaoTalk not running or a
//!   slow DB open skips this cycle; a deadline overrun or any stage error leaves
//!   the cursor, dataset, and vectors at their pre-refresh values because
//!   nothing is committed.

use rusqlite::{params, Connection, OptionalExtension};
use thiserror::Error;

use crate::context::{self, Embedder};
use crate::dataset::DatasetMessage;
use crate::ports::{Clock, MessageCursor, MessageSource, NetworkPort, PortError};
use crate::profile::{is_owner_message, SenderIdentity, UserProfile};

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/// The most messages one refresh processes; the rest wait for the next refresh
/// (R4.3).
pub const MAX_MESSAGES_PER_REFRESH: usize = 50_000;
/// How many seconds a local-DB open may take before the cycle is skipped
/// (R4.7).
pub const DB_OPEN_TIMEOUT_SECS: u64 = 10;
/// How many seconds a started refresh has to commit before it fails (R4.15).
pub const COMMIT_DEADLINE_SECS: u64 = 600;

/// The lowest applied refresh period, in seconds (R4.2).
pub const MIN_PERIOD_SECS: u64 = 900;
/// The highest applied refresh period, in seconds (R4.2).
pub const MAX_PERIOD_SECS: u64 = 86_400;
/// The default refresh period, applied when the configured value is missing or
/// out of range, in seconds (R4.2).
pub const DEFAULT_PERIOD_SECS: u64 = 3_600;

// ---------------------------------------------------------------------------
// Config and cursor
// ---------------------------------------------------------------------------

/// The refresh period. Only a value in `900..=86_400` seconds is applied; a
/// missing or out-of-range value falls back to [`DEFAULT_PERIOD_SECS`] (R4.2).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RefreshConfig {
    /// The applied period, in seconds. Always in range or exactly the default.
    pub period_secs: u64,
}

impl RefreshConfig {
    /// Resolve a raw configured period into the applied [`RefreshConfig`].
    ///
    /// A value inside `900..=86_400` is applied verbatim; anything else
    /// (including `None`) yields [`DEFAULT_PERIOD_SECS`] (R4.2).
    pub fn new(raw: Option<u64>) -> Self {
        let period_secs = match raw {
            Some(v) if (MIN_PERIOD_SECS..=MAX_PERIOD_SECS).contains(&v) => v,
            _ => DEFAULT_PERIOD_SECS,
        };
        Self { period_secs }
    }
}

impl Default for RefreshConfig {
    fn default() -> Self {
        Self {
            period_secs: DEFAULT_PERIOD_SECS,
        }
    }
}

/// The last successful incremental-refresh position (R4.4).
///
/// Because [`DatasetMessage`] carries no separate log id, `at` is the ordering
/// key and `last_log_id` is kept for the schema/tie-breaking contract.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RefreshCursor {
    /// The `at` of the last processed message.
    pub last_message_at: i64,
    /// The tie-breaking log id of the last processed message.
    pub last_log_id: i64,
    /// A fingerprint of the source at cursor time; a mismatch means the cursor
    /// point can no longer be found, so the full range is processed (R4.16).
    pub source_fingerprint: String,
    /// Logical time (ms) the last successful refresh completed.
    pub last_success_at: i64,
}

// ---------------------------------------------------------------------------
// Report and outcome
// ---------------------------------------------------------------------------

/// What a successful refresh produced (R4.5). Every count is `0` when there were
/// no target messages.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct RefreshReport {
    /// Newly added Q&A pairs.
    pub qa_pairs_added: usize,
    /// Newly added attachment references.
    pub attachments_added: usize,
    /// Style profiles updated (distinct recipients seen this refresh).
    pub styles_updated: usize,
    /// Messages processed this refresh.
    pub messages_processed: usize,
    /// Elapsed time in milliseconds.
    pub duration_ms: u64,
    /// True when this refresh processed the full range because the cursor was
    /// missing or unparseable (R4.16).
    pub full_scan: bool,
}

/// Why a refresh was skipped without changing any state (R4.7, R4.14).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SkipReason {
    /// KakaoTalk was not running / the local DB was unreadable (R4.7).
    KakaoTalkNotRunning,
    /// The local DB could not be opened within [`DB_OPEN_TIMEOUT_SECS`] (R4.7).
    DbOpenTimeout,
    /// No `is_local_only == true` embedder is available (R4.14).
    NoLocalEmbedder,
}

impl SkipReason {
    /// A plain-language explanation with what to do next (R4.7, R4.14).
    pub fn message(self) -> &'static str {
        match self {
            SkipReason::KakaoTalkNotRunning => {
                "카카오톡이 꺼져 있어 이번 갱신은 넘어갔어요. 카카오톡을 켠 뒤 다음 갱신을 기다려 주세요."
            }
            SkipReason::DbOpenTimeout => {
                "대화 기록을 제때 열지 못해 이번 갱신은 넘어갔어요. 잠시 뒤 다음 갱신에서 다시 시도할게요."
            }
            SkipReason::NoLocalEmbedder => {
                "기기 안에서만 쓰는 변환기를 찾지 못해 갱신을 멈췄어요. 설정에서 로컬 변환기를 켜 주세요."
            }
        }
    }
}

/// The stage a refresh failed at (R4.6, R4.15).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RefreshStage {
    /// Reading the local DB failed (R4.6).
    Read,
    /// Building the dataset failed (R4.6).
    Build,
    /// Generating vectors failed (R4.6).
    Vector,
    /// Committing the transaction failed (R4.6).
    Commit,
    /// The refresh did not commit within [`COMMIT_DEADLINE_SECS`] (R4.15).
    Deadline,
}

impl RefreshStage {
    /// A stable stage code, never carrying any sensitive value.
    pub fn as_str(self) -> &'static str {
        match self {
            RefreshStage::Read => "read",
            RefreshStage::Build => "build",
            RefreshStage::Vector => "vector",
            RefreshStage::Commit => "commit",
            RefreshStage::Deadline => "deadline",
        }
    }
}

/// A refresh failure: which stage, why, and what to do next — all preserved
/// state, no partial reflection (R4.6, R4.15).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RefreshFailure {
    /// The stage that failed.
    pub stage: RefreshStage,
    /// A plain-language reason (never a chat body, URL, path, or account id).
    pub reason: String,
    /// At least one thing the user can do next.
    pub next_step: String,
}

/// How a refresh ended.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RefreshOutcome {
    /// The refresh committed; the dataset, vectors, and cursor advanced
    /// together (R4.4).
    Committed(RefreshReport),
    /// The refresh was skipped and all state is unchanged (R4.7, R4.14).
    Skipped(SkipReason),
    /// The refresh failed and all state is unchanged (R4.6, R4.15).
    Failed(RefreshFailure),
}

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

/// A failure from the refresh store surface.
#[derive(Debug, Error)]
pub enum RefreshError {
    /// The backing store failed.
    #[error("데이터셋 갱신 저장소 오류: {0}")]
    Backend(String),
}

fn db_err(e: rusqlite::Error) -> RefreshError {
    RefreshError::Backend(e.to_string())
}

// ---------------------------------------------------------------------------
// Concurrency lease
// ---------------------------------------------------------------------------

/// Whether a refresh start request was admitted (R4.8).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LeaseGrant {
    /// No refresh was running; this request runs now.
    Granted,
    /// A refresh is running; this request is the single pending one.
    Queued,
    /// A refresh is running and one is already pending, so this request is
    /// refused with the running/queued state shown.
    Rejected {
        /// Whether a refresh is currently running.
        running: bool,
        /// How many refreshes are pending (at most one).
        queued: u8,
    },
}

/// Admission control that keeps at most one refresh running and at most one
/// pending (R4.8).
///
/// The state is a single atomic: `0` idle, `1` running with none pending, `2`
/// running with one pending. A third start request while in state `2` is
/// [`LeaseGrant::Rejected`].
#[derive(Debug, Default)]
pub struct RefreshLease {
    state: std::sync::atomic::AtomicU8,
}

impl RefreshLease {
    /// A fresh, idle lease.
    pub fn new() -> Self {
        Self {
            state: std::sync::atomic::AtomicU8::new(0),
        }
    }

    /// Try to admit a refresh start request (R4.8).
    pub fn acquire(&self) -> LeaseGrant {
        use std::sync::atomic::Ordering::{Acquire, SeqCst};
        loop {
            match self.state.load(Acquire) {
                0 => {
                    if self
                        .state
                        .compare_exchange(0, 1, SeqCst, Acquire)
                        .is_ok()
                    {
                        return LeaseGrant::Granted;
                    }
                }
                1 => {
                    if self
                        .state
                        .compare_exchange(1, 2, SeqCst, Acquire)
                        .is_ok()
                    {
                        return LeaseGrant::Queued;
                    }
                }
                _ => {
                    return LeaseGrant::Rejected {
                        running: true,
                        queued: 1,
                    }
                }
            }
        }
    }

    /// Mark one refresh finished: a pending refresh (if any) becomes the running
    /// one, otherwise the lease returns to idle.
    pub fn finish(&self) {
        use std::sync::atomic::Ordering::{Acquire, SeqCst};
        loop {
            let cur = self.state.load(Acquire);
            let next = cur.saturating_sub(1);
            if self
                .state
                .compare_exchange(cur, next, SeqCst, Acquire)
                .is_ok()
            {
                return;
            }
        }
    }

    /// Whether a refresh is currently running.
    pub fn running(&self) -> bool {
        self.state.load(std::sync::atomic::Ordering::Acquire) >= 1
    }

    /// How many refreshes are pending (at most one).
    pub fn queued(&self) -> u8 {
        self.state
            .load(std::sync::atomic::Ordering::Acquire)
            .saturating_sub(1)
    }
}

// ---------------------------------------------------------------------------
// Cursor storage schema
// ---------------------------------------------------------------------------

/// Create the `refresh_cursor` table if absent (R4 schema).
pub fn ensure_refresh_schema(conn: &Connection) -> Result<(), RefreshError> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS refresh_cursor(
            id INTEGER PRIMARY KEY CHECK(id = 1),
            last_message_at INTEGER NOT NULL,
            last_log_id INTEGER NOT NULL,
            source_fingerprint TEXT NOT NULL,
            last_success_at INTEGER NOT NULL
        );",
    )
    .map_err(db_err)
}

/// Load the stored cursor, if any. A malformed/unreadable row is reported as
/// `None` so the caller processes the full range (R4.16).
fn load_cursor(conn: &Connection) -> Option<RefreshCursor> {
    conn.query_row(
        "SELECT last_message_at, last_log_id, source_fingerprint, last_success_at
         FROM refresh_cursor WHERE id = 1",
        [],
        |row| {
            Ok(RefreshCursor {
                last_message_at: row.get(0)?,
                last_log_id: row.get(1)?,
                source_fingerprint: row.get(2)?,
                last_success_at: row.get(3)?,
            })
        },
    )
    .optional()
    .ok()
    .flatten()
}

// ---------------------------------------------------------------------------
// DatasetRefresher
// ---------------------------------------------------------------------------

/// The incremental dataset refresher (R4).
///
/// It reads through a [`MessageSource`], embeds with a local-only [`Embedder`],
/// times itself with a [`Clock`], and holds a [`NetworkPort`] purely to make the
/// "no egress during refresh" boundary explicit (R4.11). A [`UserProfile`]
/// re-derives each message's owner flag so owner-sent messages stay out of
/// reply targets (R4.12).
pub struct DatasetRefresher<'a> {
    source: &'a dyn MessageSource,
    embedder: &'a dyn Embedder,
    clock: &'a dyn Clock,
    net: &'a dyn NetworkPort,
    profile: &'a UserProfile,
}

impl<'a> DatasetRefresher<'a> {
    /// Build a refresher over its injected boundaries.
    pub fn new(
        source: &'a dyn MessageSource,
        embedder: &'a dyn Embedder,
        clock: &'a dyn Clock,
        net: &'a dyn NetworkPort,
        profile: &'a UserProfile,
    ) -> Self {
        Self {
            source,
            embedder,
            clock,
            net,
            profile,
        }
    }

    /// The logical time (ms) the next refresh is due, given the last successful
    /// completion time and the applied period (R4.1).
    pub fn due_at(last_success_at: i64, cfg: &RefreshConfig) -> i64 {
        last_success_at.saturating_add(cfg.period_secs as i64 * 1_000)
    }

    /// Whether a refresh is due at `now`, given the last successful completion
    /// time and the applied period (R4.1).
    pub fn is_due(now: i64, last_success_at: i64, cfg: &RefreshConfig) -> bool {
        now >= Self::due_at(last_success_at, cfg)
    }

    /// The outbound-egress count observed on the injected network boundary.
    /// During a refresh this must stay at its starting value (R4.11).
    pub fn net_egress(&self) -> usize {
        self.net.egress_count()
    }

    /// Run one incremental refresh, committing the dataset reflection and the
    /// cursor move as a single transaction on `conn` (R4.3, R4.4, R4.9).
    ///
    /// The connection owns the `qa_pair`, `attachment_reference`, and
    /// `refresh_cursor` tables; the schema is ensured before use. On any skip or
    /// failure nothing is committed, so the dataset, vectors, and cursor keep
    /// their pre-refresh values (R4.6, R4.7, R4.15).
    pub fn refresh_now(&mut self, conn: &mut Connection) -> RefreshOutcome {
        // R4.10 / R4.14: a local-only embedder is required; do not start
        // otherwise, and leave every state untouched.
        if !self.embedder.is_local_only() {
            return RefreshOutcome::Skipped(SkipReason::NoLocalEmbedder);
        }

        if let Err(e) = ensure_refresh_schema(conn) {
            return RefreshOutcome::Failed(RefreshFailure {
                stage: RefreshStage::Commit,
                reason: e.to_string(),
                next_step: "잠시 뒤 다시 시도해 주세요.".to_string(),
            });
        }
        if let Err(e) = super::ensure_schema(conn) {
            return RefreshOutcome::Failed(RefreshFailure {
                stage: RefreshStage::Commit,
                reason: e.to_string(),
                next_step: "잠시 뒤 다시 시도해 주세요.".to_string(),
            });
        }

        // Open probe (R4.7): reading the room list stands in for opening the
        // local DB. A failure means "not running"; taking too long means the
        // open timed out. `started` doubles as the refresh deadline base.
        let started = self.clock.now_ms();
        let rooms = match self.source.rooms() {
            Ok(rooms) => rooms,
            Err(_) => return RefreshOutcome::Skipped(SkipReason::KakaoTalkNotRunning),
        };
        let open_done = self.clock.now_ms();
        if open_done.saturating_sub(started) > (DB_OPEN_TIMEOUT_SECS as i64) * 1_000 {
            return RefreshOutcome::Skipped(SkipReason::DbOpenTimeout);
        }
        let fingerprint = fingerprint_rooms(&rooms);

        // Determine the processing range (R4.3, R4.16). A missing cursor, or one
        // whose fingerprint no longer matches the source, processes the full
        // range.
        let cursor = load_cursor(conn);
        let full_scan = match &cursor {
            None => true,
            Some(c) => c.source_fingerprint != fingerprint,
        };
        let after = if full_scan {
            None
        } else {
            cursor.as_ref().map(|c| MessageCursor {
                at: c.last_message_at,
                log_id: c.last_log_id,
            })
        };

        // Read only the messages after the cursor, capped per refresh (R4.3).
        let raw = match self
            .source
            .messages_after(after.as_ref(), MAX_MESSAGES_PER_REFRESH)
        {
            Ok(messages) => messages,
            Err(e) => return RefreshOutcome::Failed(read_failure(&e)),
        };

        // Re-derive the owner flag through the profile so owner-sent messages
        // are excluded from reply targets but kept for context/style (R4.12).
        let messages: Vec<DatasetMessage> = raw
            .into_iter()
            .map(|mut m| {
                let sender = SenderIdentity {
                    db_authoritative_owner: m.is_owner,
                    display_name: m.sender.clone(),
                };
                m.is_owner = is_owner_message(self.profile, &sender);
                m
            })
            .collect();

        // Build the dataset slice in memory with the local embedder (R4.10).
        let built = super::build_in_memory(&messages, self.embedder);

        // Deadline (R4.15): if the work did not reach commit in time, fail
        // without committing so all state stays at its pre-refresh value.
        let pre_commit = self.clock.now_ms();
        if pre_commit.saturating_sub(started) > (COMMIT_DEADLINE_SECS as i64) * 1_000 {
            return RefreshOutcome::Failed(RefreshFailure {
                stage: RefreshStage::Deadline,
                reason: "정해진 시간 안에 갱신을 마치지 못했어요.".to_string(),
                next_step: "대화가 많을 수 있어요. 다음 갱신에서 이어서 처리할게요.".to_string(),
            });
        }

        // The next cursor position: the largest processed `at`, or the prior
        // value when nothing was processed.
        let prior_at = cursor.as_ref().map(|c| c.last_message_at).unwrap_or(0);
        let prior_log = cursor.as_ref().map(|c| c.last_log_id).unwrap_or(0);
        let new_at = messages.iter().map(|m| m.at).max().unwrap_or(prior_at);
        let new_log = if messages.is_empty() { prior_log } else { 0 };

        // One transaction: append the built slice and advance the cursor
        // together (R4.4, R4.9). A failure drops the transaction, so the
        // previous dataset/vectors/cursor are preserved (R4.6).
        let tx = match conn.transaction() {
            Ok(tx) => tx,
            Err(e) => return RefreshOutcome::Failed(commit_failure(&e.to_string())),
        };

        let commit_result = (|| -> rusqlite::Result<()> {
            for pair in &built.qa_pairs {
                tx.execute(
                    "INSERT INTO qa_pair(recipient_key, question_pid, answer_pid, vector, at)
                     VALUES (?1, ?2, ?3, ?4, ?5)",
                    params![
                        pair.recipient_key,
                        pair.question_pid,
                        pair.answer_pid,
                        super::vector_to_bytes(&pair.vector),
                        pair.at,
                    ],
                )?;
            }
            for attachment in &built.attachments {
                tx.execute(
                    "INSERT INTO attachment_reference(
                        chat_id, at, kind, link_class, local_ref, provenance_id
                     ) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                    params![
                        attachment.chat_id,
                        attachment.at,
                        attachment.kind.as_str(),
                        attachment.link_class.map(|c| c.as_str()),
                        attachment.local_ref,
                        attachment.provenance_id,
                    ],
                )?;
            }
            tx.execute(
                "INSERT INTO refresh_cursor(
                    id, last_message_at, last_log_id, source_fingerprint, last_success_at
                 ) VALUES (1, ?1, ?2, ?3, ?4)
                 ON CONFLICT(id) DO UPDATE SET
                    last_message_at = excluded.last_message_at,
                    last_log_id = excluded.last_log_id,
                    source_fingerprint = excluded.source_fingerprint,
                    last_success_at = excluded.last_success_at",
                params![new_at, new_log, fingerprint, pre_commit],
            )?;
            Ok(())
        })();

        if let Err(e) = commit_result {
            drop(tx);
            return RefreshOutcome::Failed(commit_failure(&e.to_string()));
        }
        if let Err(e) = tx.commit() {
            return RefreshOutcome::Failed(commit_failure(&e.to_string()));
        }

        // With no target messages every count is reported as zero (R4.5).
        let report = if messages.is_empty() {
            RefreshReport {
                full_scan,
                ..RefreshReport::default()
            }
        } else {
            RefreshReport {
                qa_pairs_added: built.qa_pairs.len(),
                attachments_added: built.attachments.len(),
                styles_updated: built.recipients_profiled,
                messages_processed: messages.len(),
                duration_ms: pre_commit.saturating_sub(started).max(0) as u64,
                full_scan,
            }
        };
        RefreshOutcome::Committed(report)
    }
}

/// A stable fingerprint of the source's room set, hashed so no raw identifier is
/// stored (R4.16).
fn fingerprint_rooms(rooms: &[crate::ports::RoomRef]) -> String {
    let mut ids: Vec<i64> = rooms.iter().map(|r| r.chat_id).collect();
    ids.sort_unstable();
    let joined = ids
        .iter()
        .map(|id| id.to_string())
        .collect::<Vec<_>>()
        .join(",");
    context::provenance_id(&format!("rooms:{joined}"))
}

/// Build a read-stage failure with plain-language guidance (R4.6).
fn read_failure(e: &PortError) -> RefreshFailure {
    RefreshFailure {
        stage: RefreshStage::Read,
        reason: e.to_string(),
        next_step: "카카오톡이 켜져 있는지 확인한 뒤 다음 갱신을 기다려 주세요.".to_string(),
    }
}

/// Build a commit-stage failure with plain-language guidance (R4.6).
fn commit_failure(reason: &str) -> RefreshFailure {
    RefreshFailure {
        stage: RefreshStage::Commit,
        reason: reason.to_string(),
        next_step: "이전 데이터는 그대로 두었어요. 잠시 뒤 다시 시도할게요.".to_string(),
    }
}

#[cfg(test)]
mod tests;
