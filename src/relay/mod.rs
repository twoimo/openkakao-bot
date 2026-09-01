//! The Telegram-desktop → KakaoTalk relay (텔레그램중계기) — task 8 (R7).
//!
//! The relay reads a registered Telegram desktop conversation through macOS
//! Accessibility ([`AxReadPort`]) and forwards each new message — text, links,
//! and images — to one or more KakaoTalk rooms, almost immediately, so the owner
//! does not have to switch between the two apps.
//!
//! Several safety and correctness guarantees hold structurally here:
//!
//! * **Strictly increasing, one-at-a-time delivery (R7.9, R12.8).**
//!   [`TelegramRelay::tick`] reads messages in ascending `(at, display_order)`
//!   order and processes exactly one at a time. The [`RelayCursor`] advances
//!   **only** after a message settles as [`Settlement::Committed`] or
//!   [`Settlement::MissFinal`]; a [`Settlement::MissRetrying`] leaves the cursor
//!   in place, so the confirmed-send sequence is always a subsequence of the
//!   original time order.
//! * **Retry only the missing images, then give up after three (R7.8, R7.18).**
//!   An image-count mismatch keeps the cursor pinned and retries on the next
//!   tick, re-acquiring the missing images; three consecutive mismatches settle
//!   the message as [`Settlement::MissFinal`] and the cursor advances.
//! * **Deliver each (message, room) at most once (R7.10, R12.7).** The
//!   [`RelayLedger`] records a confirmed delivery keyed by `(chat_id,
//!   provenance-hashed message id)`, so an app restart, AX reconnect, or retry
//!   never re-sends an already-delivered message.
//! * **Real URLs are preferred (R7.3, R7.4).** Links arrive as [`LinkRef`];
//!   [`extract_links`] emits the resolved URL when present, and otherwise the
//!   display text flagged as unconfirmed.
//! * **The journal stays redacted (R7.16).** Only the stage, result code, and
//!   elapsed time are written — never the Telegram body, a URL, an image path,
//!   or an account identifier. Image counts are **not** journaled for the relay
//!   (unlike the link forwarder).
//!
//! Image acquisition reuses the shared [`ImageLadder`] from [`crate::forward`],
//! so the relay rests on the same image-count-parity guarantee as the link
//! forwarder (R12.6).

use std::collections::VecDeque;

use rusqlite::{params, Connection, OptionalExtension};
use thiserror::Error;

use crate::forward::{ImageLadder, ImageTally};
use crate::logging::{FlowKind, HistoryStore, PipelineEvent, Stage, StageStatus};
use crate::ports::{AxReadPort, Clock, DetectMode, ImageBlob};

pub use crate::ports::{ExcludedAttachment, LinkRef};

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/// The maximum registered relay pairs (R7.1).
pub const MAX_PAIRS: usize = 8;
/// The maximum text characters relayed from one message (R7.2).
pub const MAX_TEXT_CHARS: usize = 4_000;
/// The maximum links relayed from one message (R7.2).
pub const MAX_LINKS: usize = 20;
/// The maximum images relayed from one message (R7.2).
pub const MAX_IMAGES: usize = 10;
/// The per-image-acquisition-step time bound, in milliseconds (R7.5). Matches
/// [`crate::forward::STEP_TIMEOUT_MS`].
pub const STEP_TIMEOUT_MS: u64 = 2_000;
/// How many consecutive image-count mismatches settle a message as a final miss
/// (R7.18).
pub const MISMATCH_STREAK_LIMIT: u8 = 3;

/// The window within which missing images are retried before the streak forces
/// a final miss (R7.8).
pub const MISS_RETRY_WINDOW_SECS: i64 = 30;
/// How long the relay waits to confirm the AX notification subscription before
/// switching to polling, in milliseconds (R7.13).
pub const NOTIFICATION_CONFIRM_MS: i64 = 5_000;
/// The maximum polling interval once the relay falls back from notifications,
/// in milliseconds (R7.13).
pub const POLLING_MAX_INTERVAL_MS: u64 = 2_000;

/// How many recent latency samples the window keeps (R7.11).
pub const LATENCY_WINDOW_MAX: usize = 100;
/// The minimum number of samples before latency targets are judged (R7.11).
pub const LATENCY_READY_MIN: usize = 20;
/// The P50 relay-latency target, in milliseconds (R7.11).
pub const P50_TARGET_MS: u64 = 3_000;
/// The P95 relay-latency target, in milliseconds (R7.11).
pub const P95_TARGET_MS: u64 = 10_000;

// ---------------------------------------------------------------------------
// Pair
// ---------------------------------------------------------------------------

/// A reference to a Telegram desktop conversation in a relay pair.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TelegramChatRef {
    /// A stable identifier for the Telegram conversation (never stored raw).
    pub id: String,
}

/// A registered relay pair: one Telegram conversation and the KakaoTalk rooms it
/// relays into (R7.1).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RelayPair {
    /// The Telegram desktop conversation to read.
    pub telegram: TelegramChatRef,
    /// The target KakaoTalk room chat ids (each on the automation allowlist).
    pub kakao_rooms: Vec<i64>,
}

impl RelayPair {
    /// A stable, provenance-hashed key for this pair, used as the cursor row's
    /// primary key. Hashing keeps the raw Telegram id out of storage.
    pub fn pair_key(&self) -> String {
        let mut rooms = self.kakao_rooms.clone();
        rooms.sort_unstable();
        let rooms: Vec<String> = rooms.iter().map(|r| r.to_string()).collect();
        crate::context::provenance_id(&format!("tg:{}|rooms:{}", self.telegram.id, rooms.join(",")))
    }
}

// ---------------------------------------------------------------------------
// Cursor
// ---------------------------------------------------------------------------

/// The last relayed position in a pair's Telegram stream (R7.1).
///
/// Distinct from [`crate::ports::RelayCursor`] (the read boundary's ordering
/// key): this durable form also remembers the provenance-hashed id of the last
/// settled message.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct RelayCursor {
    /// Opaque timestamp of the last settled message.
    pub last_at: i64,
    /// Tie-breaking display order of the last settled message (R7.1).
    pub last_display_order: i64,
    /// The provenance-hashed id of the last settled message.
    pub last_message_pid: String,
}

impl RelayCursor {
    /// The read boundary's ordering key for this position.
    fn as_port_cursor(&self) -> crate::ports::RelayCursor {
        crate::ports::RelayCursor {
            at: self.last_at,
            display_order: self.last_display_order,
        }
    }
}

// ---------------------------------------------------------------------------
// Latency window
// ---------------------------------------------------------------------------

/// A rolling window of the most recent relay latencies (R7.11).
///
/// It keeps at most [`LATENCY_WINDOW_MAX`] samples and reports percentiles only
/// once at least [`LATENCY_READY_MIN`] samples exist ([`LatencyWindow::ready`]).
#[derive(Debug, Clone, Default)]
pub struct LatencyWindow {
    samples: VecDeque<u64>,
}

impl LatencyWindow {
    /// A fresh, empty window.
    pub fn new() -> Self {
        Self {
            samples: VecDeque::new(),
        }
    }

    /// Record one latency sample (ms), evicting the oldest beyond the cap.
    pub fn record(&mut self, latency_ms: u64) {
        if self.samples.len() == LATENCY_WINDOW_MAX {
            self.samples.pop_front();
        }
        self.samples.push_back(latency_ms);
    }

    /// How many samples are currently held.
    pub fn len(&self) -> usize {
        self.samples.len()
    }

    /// Whether the window holds no samples.
    pub fn is_empty(&self) -> bool {
        self.samples.is_empty()
    }

    /// Whether there are enough samples to judge the latency targets (R7.11).
    pub fn ready(&self) -> bool {
        self.samples.len() >= LATENCY_READY_MIN
    }

    /// The P50 (median) latency, or `None` when not [`Self::ready`].
    pub fn p50(&self) -> Option<u64> {
        self.percentile(50.0)
    }

    /// The P95 latency, or `None` when not [`Self::ready`].
    pub fn p95(&self) -> Option<u64> {
        self.percentile(95.0)
    }

    /// Whether the window is ready and either percentile exceeds its target
    /// (R7.12). The relay keeps going regardless; this only drives a display.
    pub fn exceeds_target(&self) -> bool {
        self.ready()
            && (self.p50().is_some_and(|v| v > P50_TARGET_MS)
                || self.p95().is_some_and(|v| v > P95_TARGET_MS))
    }

    /// A snapshot for the tick result and the History window.
    pub fn snapshot(&self) -> LatencySnapshot {
        LatencySnapshot {
            samples: self.samples.len(),
            ready: self.ready(),
            p50_ms: self.p50(),
            p95_ms: self.p95(),
            exceeds_target: self.exceeds_target(),
        }
    }

    /// Nearest-rank percentile over the current samples; `None` when not ready.
    fn percentile(&self, p: f64) -> Option<u64> {
        if !self.ready() {
            return None;
        }
        let mut sorted: Vec<u64> = self.samples.iter().copied().collect();
        sorted.sort_unstable();
        let n = sorted.len();
        let rank = (p / 100.0 * n as f64).ceil() as usize;
        let idx = rank.saturating_sub(1).min(n - 1);
        Some(sorted[idx])
    }
}

/// A read-only view of the latency window at a point in time (R7.11, R7.12).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LatencySnapshot {
    /// How many samples back the snapshot.
    pub samples: usize,
    /// Whether the targets can be judged yet (≥ 20 samples).
    pub ready: bool,
    /// The P50 latency, or `None` when not ready.
    pub p50_ms: Option<u64>,
    /// The P95 latency, or `None` when not ready.
    pub p95_ms: Option<u64>,
    /// Whether a target is exceeded (relaying continues regardless).
    pub exceeds_target: bool,
}

// ---------------------------------------------------------------------------
// Ledger + cursor store
// ---------------------------------------------------------------------------

/// A failure from the relay ledger or cursor store.
#[derive(Debug, Error)]
pub enum LedgerError {
    /// The backing store failed.
    #[error("중계 기록 저장소 오류: {0}")]
    Backend(String),
}

fn db_err(e: rusqlite::Error) -> LedgerError {
    LedgerError::Backend(e.to_string())
}

/// Records confirmed deliveries and image-mismatch streaks so each (message,
/// room) is relayed at most once (R7.10) and a message settles as a final miss
/// after three consecutive mismatches (R7.18).
///
/// Keys are the provenance-hashed message id, so no raw Telegram identifier is
/// persisted (R7.16).
pub trait RelayLedger {
    /// Whether `message_pid` has been confirmed as delivered to `chat_id`.
    fn is_delivered(&self, chat_id: i64, message_pid: &str) -> bool;

    /// Record a confirmed delivery. Only a fully-delivered (message, room) is
    /// ever marked, so an image mismatch stays retryable (R7.8, R7.10).
    fn mark(&self, chat_id: i64, message_pid: &str, at: i64) -> Result<(), LedgerError>;

    /// The current consecutive image-mismatch streak for a (message, room).
    fn mismatch_streak(&self, chat_id: i64, message_pid: &str) -> u8;

    /// Increment and return the mismatch streak for a (message, room), capped at
    /// [`MISMATCH_STREAK_LIMIT`] (R7.18).
    fn bump_mismatch(&self, chat_id: i64, message_pid: &str) -> Result<u8, LedgerError>;
}

/// Loads and stores the per-pair [`RelayCursor`] (R7.1). Kept separate so the
/// cursor advances only on a settled message (R7.9).
pub trait RelayCursorStore {
    /// Load the cursor for `pair_key`, or `None` when the pair has never
    /// relayed.
    fn load(&self, pair_key: &str) -> Result<Option<RelayCursor>, LedgerError>;

    /// Persist the cursor for `pair_key`.
    fn save(&self, pair_key: &str, cursor: &RelayCursor) -> Result<(), LedgerError>;
}

/// A SQLite-backed [`RelayLedger`] and [`RelayCursorStore`] over the
/// `relay_ledger` and `relay_cursor` tables.
pub struct SqliteRelayStore {
    conn: Connection,
}

impl SqliteRelayStore {
    /// Wrap a connection, ensuring the schema is present.
    pub fn new(conn: Connection) -> Result<Self, LedgerError> {
        ensure_relay_schema(&conn)?;
        Ok(Self { conn })
    }

    /// Open (or create) a store at `path`.
    pub fn open(path: &std::path::Path) -> Result<Self, LedgerError> {
        let conn = Connection::open(path).map_err(db_err)?;
        Self::new(conn)
    }

    /// Open an in-memory store. Primarily for tests.
    pub fn open_in_memory() -> Result<Self, LedgerError> {
        let conn = Connection::open_in_memory().map_err(db_err)?;
        Self::new(conn)
    }
}

/// Create the `relay_cursor` and `relay_ledger` tables if absent (R7 schema).
pub fn ensure_relay_schema(conn: &Connection) -> Result<(), LedgerError> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS relay_cursor(
            pair_key TEXT PRIMARY KEY,
            last_at INTEGER NOT NULL,
            last_display_order INTEGER NOT NULL,
            last_message_pid TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS relay_ledger(
            id INTEGER PRIMARY KEY,
            chat_id INTEGER NOT NULL,
            message_pid TEXT NOT NULL,
            mismatch_streak INTEGER NOT NULL DEFAULT 0 CHECK(mismatch_streak <= 3),
            settled INTEGER NOT NULL DEFAULT 0,
            at INTEGER NOT NULL,
            UNIQUE(chat_id, message_pid)
        );
        CREATE INDEX IF NOT EXISTS idx_relay_ledger_msg
            ON relay_ledger(chat_id, message_pid);",
    )
    .map_err(db_err)
}

impl RelayLedger for SqliteRelayStore {
    fn is_delivered(&self, chat_id: i64, message_pid: &str) -> bool {
        self.conn
            .query_row(
                "SELECT 1 FROM relay_ledger
                 WHERE chat_id = ?1 AND message_pid = ?2 AND settled = 1",
                params![chat_id, message_pid],
                |_| Ok(()),
            )
            .is_ok()
    }

    fn mark(&self, chat_id: i64, message_pid: &str, at: i64) -> Result<(), LedgerError> {
        self.conn
            .execute(
                "INSERT INTO relay_ledger(chat_id, message_pid, mismatch_streak, settled, at)
                 VALUES (?1, ?2, 0, 1, ?3)
                 ON CONFLICT(chat_id, message_pid) DO UPDATE SET
                    settled = 1,
                    at = excluded.at",
                params![chat_id, message_pid, at],
            )
            .map_err(db_err)?;
        Ok(())
    }

    fn mismatch_streak(&self, chat_id: i64, message_pid: &str) -> u8 {
        let streak: Option<i64> = self
            .conn
            .query_row(
                "SELECT mismatch_streak FROM relay_ledger
                 WHERE chat_id = ?1 AND message_pid = ?2",
                params![chat_id, message_pid],
                |row| row.get(0),
            )
            .optional()
            .ok()
            .flatten();
        streak.unwrap_or(0).clamp(0, MISMATCH_STREAK_LIMIT as i64) as u8
    }

    fn bump_mismatch(&self, chat_id: i64, message_pid: &str) -> Result<u8, LedgerError> {
        // Insert at 1 on first miss, otherwise bump but never past the CHECK
        // ceiling of 3 (R7.18).
        self.conn
            .execute(
                "INSERT INTO relay_ledger(chat_id, message_pid, mismatch_streak, settled, at)
                 VALUES (?1, ?2, 1, 0, ?3)
                 ON CONFLICT(chat_id, message_pid) DO UPDATE SET
                    mismatch_streak = MIN(mismatch_streak + 1, 3),
                    at = excluded.at",
                params![chat_id, message_pid, 0_i64],
            )
            .map_err(db_err)?;
        Ok(self.mismatch_streak(chat_id, message_pid))
    }
}

impl RelayCursorStore for SqliteRelayStore {
    fn load(&self, pair_key: &str) -> Result<Option<RelayCursor>, LedgerError> {
        self.conn
            .query_row(
                "SELECT last_at, last_display_order, last_message_pid
                 FROM relay_cursor WHERE pair_key = ?1",
                params![pair_key],
                |row| {
                    Ok(RelayCursor {
                        last_at: row.get(0)?,
                        last_display_order: row.get(1)?,
                        last_message_pid: row.get(2)?,
                    })
                },
            )
            .optional()
            .map_err(db_err)
    }

    fn save(&self, pair_key: &str, cursor: &RelayCursor) -> Result<(), LedgerError> {
        self.conn
            .execute(
                "INSERT INTO relay_cursor(pair_key, last_at, last_display_order, last_message_pid)
                 VALUES (?1, ?2, ?3, ?4)
                 ON CONFLICT(pair_key) DO UPDATE SET
                    last_at = excluded.last_at,
                    last_display_order = excluded.last_display_order,
                    last_message_pid = excluded.last_message_pid",
                params![
                    pair_key,
                    cursor.last_at,
                    cursor.last_display_order,
                    cursor.last_message_pid,
                ],
            )
            .map_err(db_err)?;
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Sender seam
// ---------------------------------------------------------------------------

/// The outcome of relaying one message to one KakaoTalk room (R7.15).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RelaySendOutcome {
    /// The message was sent and this many images were delivered.
    Sent {
        /// How many attached images were confirmed as delivered.
        images_delivered: usize,
    },
    /// The safety gate fenced this room; no send happened (R7.15).
    Fenced {
        /// The plain-language fence reason.
        reason: String,
    },
    /// The send failed at some stage.
    Failed {
        /// A short, stable failure code.
        code: &'static str,
    },
}

/// The room-send seam (R7.15).
///
/// In production this wraps [`SendGuard`](crate::safety::SendGuard)
/// authorization (with [`Origin::RelaySource`](crate::safety::Origin::RelaySource))
/// plus the [`SendPort`](crate::ports::SendPort); a fenced room reports
/// [`RelaySendOutcome::Fenced`] and performs no send. Tests inject a scripted
/// sender so no real KakaoTalk send happens.
pub trait RelaySender {
    /// Authorize and send `text` with `images` to `chat_id`.
    fn send_relay(&self, chat_id: i64, text: &str, images: &[ImageBlob]) -> RelaySendOutcome;
}

// ---------------------------------------------------------------------------
// Settlement
// ---------------------------------------------------------------------------

/// How a message's relay settled (R7.7, R7.8, R7.18).
///
/// The cursor advances only on [`Settlement::Committed`] or
/// [`Settlement::MissFinal`]; [`Settlement::MissRetrying`] pins the cursor so the
/// message is retried (R7.9).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Settlement {
    /// Every source image was delivered to every target room.
    Committed(ImageTally),
    /// The image counts disagree; the missing images are retried within the
    /// window and the cursor stays put (R7.8).
    MissRetrying(ImageTally),
    /// Three consecutive mismatches (or an undeliverable target) — the miss is
    /// final and the cursor advances (R7.18).
    MissFinal(ImageTally),
}

impl Settlement {
    /// Whether the cursor may advance past this message (R7.9).
    fn advances_cursor(&self) -> bool {
        matches!(self, Settlement::Committed(_) | Settlement::MissFinal(_))
    }

    /// The image tally this settlement carries.
    pub fn tally(&self) -> ImageTally {
        match self {
            Settlement::Committed(t) | Settlement::MissRetrying(t) | Settlement::MissFinal(t) => *t,
        }
    }
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

/// Extract the links to relay, preferring the real URL over display text
/// (R7.3, R7.4), capped at [`MAX_LINKS`].
///
/// Returns the rendered link strings in original appearance order and how many
/// of them are display-only (unconfirmed) links.
pub fn extract_links(links: &[LinkRef]) -> (Vec<String>, usize) {
    let mut out = Vec::new();
    let mut unresolved = 0;
    for link in links.iter().take(MAX_LINKS) {
        match link {
            LinkRef::Resolved(url) => out.push(url.clone()),
            LinkRef::DisplayOnly(text) => {
                unresolved += 1;
                out.push(format!("{text} (실제 주소 확인 불가)"));
            }
        }
    }
    (out, unresolved)
}

/// A plain-language note about the attachments excluded from relaying, or `None`
/// when there are none (R7.19).
pub fn summarize_excluded(excluded: &[ExcludedAttachment]) -> Option<String> {
    if excluded.is_empty() {
        return None;
    }
    let (mut video, mut file, mut voice, mut sticker) = (0, 0, 0, 0);
    for e in excluded {
        match e {
            ExcludedAttachment::Video => video += 1,
            ExcludedAttachment::File => file += 1,
            ExcludedAttachment::Voice => voice += 1,
            ExcludedAttachment::Sticker => sticker += 1,
        }
    }
    let mut parts = Vec::new();
    if video > 0 {
        parts.push(format!("동영상 {video}개"));
    }
    if file > 0 {
        parts.push(format!("파일 {file}개"));
    }
    if voice > 0 {
        parts.push(format!("음성 {voice}개"));
    }
    if sticker > 0 {
        parts.push(format!("스티커 {sticker}개"));
    }
    Some(format!("{}는 중계하지 않았어요.", parts.join(", ")))
}

/// Build the KakaoTalk message body: the text (capped at [`MAX_TEXT_CHARS`])
/// followed by each rendered link on its own line.
fn render_body(text: &str, links: &[String]) -> String {
    let mut body: String = text.chars().take(MAX_TEXT_CHARS).collect();
    for link in links {
        if !body.is_empty() {
            body.push('\n');
        }
        body.push_str(link);
    }
    body
}

// ---------------------------------------------------------------------------
// Tick result
// ---------------------------------------------------------------------------

/// One message's outcome within a [`RelayTick`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MessageOutcome {
    /// The provenance-hashed id of the message.
    pub message_pid: String,
    /// How the message settled.
    pub settlement: Settlement,
    /// The attachments excluded from relaying (R7.19).
    pub excluded: Vec<ExcludedAttachment>,
    /// How many links were display-only / unconfirmed (R7.4).
    pub unresolved_links: usize,
    /// How many images exceeded the per-message cap and were dropped (R7.2).
    pub dropped_images: usize,
}

/// The result of one [`TelegramRelay::tick`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RelayTick {
    /// The detection mode in use this tick (R7.13).
    pub detect_mode: DetectMode,
    /// The messages processed this tick, in order.
    pub processed: Vec<MessageOutcome>,
    /// The latency window snapshot after this tick (R7.11, R7.12).
    pub latency: LatencySnapshot,
    /// The cursor after this tick.
    pub cursor: RelayCursor,
    /// Set when the Telegram window or Accessibility permission was unavailable
    /// this tick; the cursor is left untouched (R7.14).
    pub ax_unavailable: bool,
}

// ---------------------------------------------------------------------------
// TelegramRelay
// ---------------------------------------------------------------------------

/// The Telegram → KakaoTalk relay for one pair (R7).
pub struct TelegramRelay<'a> {
    ax: &'a dyn AxReadPort,
    ladder: &'a ImageLadder<'a>,
    sender: &'a dyn RelaySender,
    ledger: &'a dyn RelayLedger,
    cursor_store: &'a dyn RelayCursorStore,
    journal: &'a dyn HistoryStore,
    clock: &'a dyn Clock,
    pair: RelayPair,
    screen_recording_granted: bool,
    cursor: RelayCursor,
    latency: LatencyWindow,
    notification_started: i64,
    notification_confirmed: bool,
}

impl<'a> TelegramRelay<'a> {
    /// Build a relay for `pair`, loading its persisted cursor (R7.1).
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        ax: &'a dyn AxReadPort,
        ladder: &'a ImageLadder<'a>,
        sender: &'a dyn RelaySender,
        ledger: &'a dyn RelayLedger,
        cursor_store: &'a dyn RelayCursorStore,
        journal: &'a dyn HistoryStore,
        clock: &'a dyn Clock,
        pair: RelayPair,
        screen_recording_granted: bool,
    ) -> Self {
        let cursor = cursor_store
            .load(&pair.pair_key())
            .ok()
            .flatten()
            .unwrap_or_default();
        let now = clock.now_ms();
        Self {
            ax,
            ladder,
            sender,
            ledger,
            cursor_store,
            journal,
            clock,
            pair,
            screen_recording_granted,
            cursor,
            latency: LatencyWindow::new(),
            notification_started: now,
            notification_confirmed: false,
        }
    }

    /// The current cursor (mainly for inspection and tests).
    pub fn cursor(&self) -> &RelayCursor {
        &self.cursor
    }

    /// A snapshot of the latency window (R7.11).
    pub fn latency(&self) -> LatencySnapshot {
        self.latency.snapshot()
    }

    /// One detection cycle (R7.9).
    ///
    /// Reads messages in ascending `(at, display_order)` order and processes one
    /// at a time. A committed or final-miss message advances the cursor and the
    /// loop continues to the next; a retrying message pins the cursor and ends
    /// the tick, so it is retried on the following tick.
    pub fn tick(&mut self) -> RelayTick {
        let detect_mode = self.resolve_detect_mode();
        let mut processed = Vec::new();
        let mut ax_unavailable = false;

        // A generous safety cap; the loop normally ends when the stream drains
        // or a message needs retrying.
        for _ in 0..(LATENCY_WINDOW_MAX * 16 + 16) {
            let port_cursor = self.cursor.as_port_cursor();
            let message = match self.ax.messages_after(&port_cursor, 1) {
                Ok(mut batch) => batch.drain(..).next(),
                Err(_) => {
                    // Window not found or permission missing: stop and keep the
                    // cursor untouched so relay resumes after recovery (R7.14).
                    ax_unavailable = true;
                    self.journal_stage(
                        &self.pair.pair_key(),
                        Stage::Detect,
                        StageStatus::Failed,
                        "relay_ax_unavailable",
                        self.clock.now_ms(),
                    );
                    None
                }
            };
            let Some(message) = message else {
                break;
            };

            // Reading a message confirms the notification subscription (R7.13).
            self.notification_confirmed = true;

            let outcome = self.process_message(&message);
            let advance = outcome.settlement.advances_cursor();
            processed.push(outcome);

            if advance {
                self.cursor = RelayCursor {
                    last_at: message.at,
                    last_display_order: message.display_order,
                    last_message_pid: crate::context::provenance_id(&message.pid),
                };
                let _ = self.cursor_store.save(&self.pair.pair_key(), &self.cursor);
            } else {
                // MissRetrying: leave the cursor pinned and end the tick so the
                // same head message is retried next tick (R7.8, R7.9).
                break;
            }
        }

        RelayTick {
            detect_mode,
            processed,
            latency: self.latency.snapshot(),
            cursor: self.cursor.clone(),
            ax_unavailable,
        }
    }

    /// Relay one message to every target room, applying the once-per-room,
    /// image-parity, and mismatch-streak rules (R7.7, R7.8, R7.10, R7.18).
    fn process_message(&mut self, message: &crate::ports::TelegramMessage) -> MessageOutcome {
        let clock = self.clock;
        let ledger = self.ledger;
        let sender = self.sender;
        let ladder = self.ladder;
        let screen = self.screen_recording_granted;
        let rooms = self.pair.kakao_rooms.clone();

        let start = clock.now_ms();
        let pid_hash = crate::context::provenance_id(&message.pid);

        // Links (real URL preferred, R7.3/R7.4) and the excluded-attachment note
        // (R7.19).
        let (rendered_links, unresolved_links) = extract_links(&message.links);
        let excluded = message.excluded.clone();

        // Images: cap at MAX_IMAGES and count the overflow as dropped (R7.2).
        // Parity compares against the full source count (R7.7).
        let source_images = message.images.len();
        let dropped_images = source_images.saturating_sub(MAX_IMAGES);
        let mut blobs = Vec::new();
        for image in message.images.iter().take(MAX_IMAGES) {
            let outcome = ladder.acquire(image, screen);
            if let Some(blob) = outcome.acquired {
                blobs.push(blob);
            }
        }

        let body = render_body(&message.text, &rendered_links);
        let nothing_to_send = body.is_empty() && source_images == 0;

        // Aggregate over target rooms.
        let mut all_delivered = true; // every room fully delivered (or already)
        let mut retrying = false; // some room has a retryable image miss
        let mut delivered_min = source_images; // worst delivered across sent rooms
        let mut saw_send = false;

        for &room in &rooms {
            // Dedup: an already-delivered (message, room) is never re-sent
            // (R7.10). It counts as delivered for this message.
            if ledger.is_delivered(room, &pid_hash) {
                continue;
            }

            if nothing_to_send {
                // Only excluded attachments — nothing relayable; settle the room
                // so the message can advance (R7.19).
                let _ = ledger.mark(room, &pid_hash, clock.now_ms());
                continue;
            }

            match sender.send_relay(room, &body, &blobs) {
                RelaySendOutcome::Sent { images_delivered } => {
                    saw_send = true;
                    delivered_min = delivered_min.min(images_delivered);
                    let complete = images_delivered == source_images && dropped_images == 0;
                    if complete {
                        let _ = ledger.mark(room, &pid_hash, clock.now_ms());
                    } else {
                        // Image-count mismatch: retryable up to the streak limit
                        // (R7.8, R7.18).
                        all_delivered = false;
                        let streak = ledger
                            .bump_mismatch(room, &pid_hash)
                            .unwrap_or(MISMATCH_STREAK_LIMIT);
                        if streak < MISMATCH_STREAK_LIMIT {
                            retrying = true;
                        }
                    }
                }
                RelaySendOutcome::Fenced { .. } => {
                    // Fenced rooms never receive a send (R7.15). Fencing is not
                    // an image miss, so it does not trigger a retry.
                    all_delivered = false;
                    delivered_min = 0;
                }
                RelaySendOutcome::Failed { .. } => {
                    all_delivered = false;
                    delivered_min = 0;
                }
            }
        }

        let delivered = if all_delivered {
            source_images
        } else if saw_send {
            delivered_min
        } else {
            0
        };
        let tally = ImageTally {
            source: source_images,
            delivered,
        };

        let (settlement, stage, status, code) = if retrying {
            (
                Settlement::MissRetrying(tally),
                Stage::Commit,
                StageStatus::Failed,
                "relay_miss_retry",
            )
        } else if all_delivered {
            (
                Settlement::Committed(tally),
                Stage::Commit,
                StageStatus::Success,
                "relay_committed",
            )
        } else {
            (
                Settlement::MissFinal(tally),
                Stage::Commit,
                StageStatus::Failed,
                "relay_miss_final",
            )
        };

        // Record a confirmed relay's latency (R7.11): from the message's
        // appearance time to the confirmed send.
        if matches!(settlement, Settlement::Committed(_)) {
            let latency = clock.now_ms().saturating_sub(message.at).max(0) as u64;
            self.latency.record(latency);
        }

        // Redacted journal record: stage, result code, elapsed only (R7.16).
        self.journal_stage(&pid_hash, stage, status, code, start);

        MessageOutcome {
            message_pid: pid_hash,
            settlement,
            excluded,
            unresolved_links,
            dropped_images,
        }
    }

    /// Resolve the detection mode: prefer AX notifications, and switch to ≤ 2s
    /// polling if the subscription is not confirmed within five seconds (R7.13).
    fn resolve_detect_mode(&self) -> DetectMode {
        match self.ax.detect_mode() {
            DetectMode::Notification => {
                if self.notification_confirmed {
                    DetectMode::Notification
                } else if self.clock.now_ms().saturating_sub(self.notification_started)
                    >= NOTIFICATION_CONFIRM_MS
                {
                    DetectMode::Polling {
                        interval_ms: POLLING_MAX_INTERVAL_MS,
                    }
                } else {
                    DetectMode::Notification
                }
            }
            DetectMode::Polling { interval_ms } => DetectMode::Polling {
                interval_ms: interval_ms.min(POLLING_MAX_INTERVAL_MS),
            },
        }
    }

    /// Append a redacted journal record (R7.16). `trace_seed` is hashed again by
    /// the journal's redaction step, so no raw identifier is stored.
    fn journal_stage(
        &self,
        trace_seed: &str,
        stage: Stage,
        status: StageStatus,
        result_code: &str,
        start: i64,
    ) {
        let now = self.clock.now_ms();
        let event = PipelineEvent {
            trace_id: format!("relay:{trace_seed}"),
            flow: FlowKind::TelegramRelay,
            stage,
            status,
            result_code: result_code.to_string(),
            duration_ms: now.saturating_sub(start).max(0) as u64,
            at: now,
        };
        let _ = self.journal.append(event);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fakes::{FakeAxReadPort, VirtualClock};
    use crate::logging::SqliteHistoryStore;
    use crate::ports::{
        AcquireError, ImageAcquisition, ImageAcquirer, ImageRef, TelegramMessage,
    };
    use std::cell::RefCell;

    // ---- helpers ----

    fn msg(pid: &str, at: i64, order: i64, text: &str) -> TelegramMessage {
        TelegramMessage {
            pid: pid.into(),
            at,
            display_order: order,
            text: text.into(),
            links: Vec::new(),
            images: Vec::new(),
            excluded: Vec::new(),
        }
    }

    fn img_ref(i: usize) -> ImageRef {
        ImageRef {
            locator: format!("img-{i}"),
        }
    }

    /// An acquirer that always yields a non-empty image.
    struct OkAcquirer {
        clock: VirtualClock,
    }
    impl ImageAcquirer for OkAcquirer {
        fn kind(&self) -> ImageAcquisition {
            ImageAcquisition::OriginalSave
        }
        fn acquire(&self, _r: &ImageRef) -> Result<ImageBlob, AcquireError> {
            self.clock.advance_ms(5);
            Ok(ImageBlob {
                bytes: vec![1, 2, 3],
                mime: "image/png".into(),
            })
        }
    }

    /// An acquirer that never yields an image.
    struct NeverAcquirer {
        kind: ImageAcquisition,
    }
    impl ImageAcquirer for NeverAcquirer {
        fn kind(&self) -> ImageAcquisition {
            self.kind
        }
        fn acquire(&self, _r: &ImageRef) -> Result<ImageBlob, AcquireError> {
            Err(AcquireError::NotAvailable)
        }
    }

    /// Holds the three ladder rungs so their references outlive the ladder.
    struct LadderRig {
        clock: VirtualClock,
        ok: OkAcquirer,
        n1: NeverAcquirer,
        n2: NeverAcquirer,
    }
    impl LadderRig {
        fn acquiring(clock: VirtualClock) -> Self {
            Self {
                ok: OkAcquirer {
                    clock: clock.clone(),
                },
                n1: NeverAcquirer {
                    kind: ImageAcquisition::CacheFolder,
                },
                n2: NeverAcquirer {
                    kind: ImageAcquisition::ScreenCapture,
                },
                clock,
            }
        }
        fn ladder_acquiring(&self) -> ImageLadder<'_> {
            ImageLadder {
                steps: [&self.ok, &self.n1, &self.n2],
                clock: &self.clock,
            }
        }
    }

    /// A never-acquiring ladder (all three rungs fail).
    struct FailLadderRig {
        clock: VirtualClock,
        n0: NeverAcquirer,
        n1: NeverAcquirer,
        n2: NeverAcquirer,
    }
    impl FailLadderRig {
        fn new(clock: VirtualClock) -> Self {
            Self {
                n0: NeverAcquirer {
                    kind: ImageAcquisition::OriginalSave,
                },
                n1: NeverAcquirer {
                    kind: ImageAcquisition::CacheFolder,
                },
                n2: NeverAcquirer {
                    kind: ImageAcquisition::ScreenCapture,
                },
                clock,
            }
        }
        fn ladder(&self) -> ImageLadder<'_> {
            ImageLadder {
                steps: [&self.n0, &self.n1, &self.n2],
                clock: &self.clock,
            }
        }
    }

    /// A sender that returns a scripted outcome and records every send target.
    struct ScriptedSender {
        outcome: RelaySendOutcome,
        sends: RefCell<Vec<i64>>,
    }
    impl ScriptedSender {
        fn new(outcome: RelaySendOutcome) -> Self {
            Self {
                outcome,
                sends: RefCell::new(Vec::new()),
            }
        }
        fn send_count(&self) -> usize {
            self.sends.borrow().len()
        }
    }
    impl RelaySender for ScriptedSender {
        fn send_relay(&self, chat_id: i64, _text: &str, _images: &[ImageBlob]) -> RelaySendOutcome {
            self.sends.borrow_mut().push(chat_id);
            self.outcome.clone()
        }
    }

    fn pair(rooms: &[i64]) -> RelayPair {
        RelayPair {
            telegram: TelegramChatRef { id: "tg-1".into() },
            kakao_rooms: rooms.to_vec(),
        }
    }

    // ---- LatencyWindow ----

    #[test]
    fn latency_window_not_ready_below_twenty() {
        let mut w = LatencyWindow::new();
        for i in 0..19 {
            w.record(i);
        }
        assert!(!w.ready());
        assert_eq!(w.p50(), None);
        assert_eq!(w.p95(), None);
        assert!(!w.exceeds_target());
    }

    #[test]
    fn latency_window_percentiles_match_nearest_rank() {
        let mut w = LatencyWindow::new();
        for i in 1..=100 {
            w.record(i);
        }
        assert!(w.ready());
        assert_eq!(w.p50(), Some(50));
        assert_eq!(w.p95(), Some(95));
    }

    #[test]
    fn latency_window_keeps_only_last_hundred() {
        let mut w = LatencyWindow::new();
        for i in 0..150 {
            w.record(i);
        }
        assert_eq!(w.len(), LATENCY_WINDOW_MAX);
        // Oldest evicted: the retained samples are 50..=149, so the nearest-rank
        // median (rank 50) is 99.
        assert_eq!(w.p50(), Some(99));
    }

    #[test]
    fn latency_window_flags_target_exceeded() {
        let mut w = LatencyWindow::new();
        for _ in 0..20 {
            w.record(5_000); // above the 3s P50 target
        }
        assert!(w.exceeds_target());
    }

    // ---- extract_links / summarize_excluded ----

    #[test]
    fn extract_links_prefers_real_url_and_flags_display_only() {
        let links = vec![
            LinkRef::Resolved("https://example.com/a".into()),
            LinkRef::DisplayOnly("클릭".into()),
        ];
        let (rendered, unresolved) = extract_links(&links);
        assert_eq!(rendered[0], "https://example.com/a");
        assert!(rendered[1].contains("클릭"));
        assert!(rendered[1].contains("확인 불가"));
        assert_eq!(unresolved, 1);
    }

    #[test]
    fn extract_links_caps_at_max() {
        let links: Vec<LinkRef> = (0..(MAX_LINKS + 5))
            .map(|i| LinkRef::Resolved(format!("https://x/{i}")))
            .collect();
        let (rendered, _) = extract_links(&links);
        assert_eq!(rendered.len(), MAX_LINKS);
    }

    #[test]
    fn summarize_excluded_counts_kinds() {
        let note = summarize_excluded(&[
            ExcludedAttachment::Video,
            ExcludedAttachment::Video,
            ExcludedAttachment::Sticker,
        ])
        .unwrap();
        assert!(note.contains("동영상 2개"));
        assert!(note.contains("스티커 1개"));
        assert!(summarize_excluded(&[]).is_none());
    }

    // ---- SqliteRelayStore ----

    #[test]
    fn ledger_mark_and_is_delivered() {
        let store = SqliteRelayStore::open_in_memory().unwrap();
        assert!(!store.is_delivered(1, "pid-a"));
        store.mark(1, "pid-a", 100).unwrap();
        assert!(store.is_delivered(1, "pid-a"));
        assert!(!store.is_delivered(2, "pid-a"));
    }

    #[test]
    fn ledger_bump_mismatch_caps_at_three() {
        let store = SqliteRelayStore::open_in_memory().unwrap();
        assert_eq!(store.mismatch_streak(1, "pid"), 0);
        assert_eq!(store.bump_mismatch(1, "pid").unwrap(), 1);
        assert_eq!(store.bump_mismatch(1, "pid").unwrap(), 2);
        assert_eq!(store.bump_mismatch(1, "pid").unwrap(), 3);
        // Never exceeds the CHECK ceiling.
        assert_eq!(store.bump_mismatch(1, "pid").unwrap(), 3);
    }

    #[test]
    fn cursor_store_roundtrip() {
        let store = SqliteRelayStore::open_in_memory().unwrap();
        assert_eq!(store.load("pair-1").unwrap(), None);
        let cursor = RelayCursor {
            last_at: 42,
            last_display_order: 1,
            last_message_pid: "hash".into(),
        };
        store.save("pair-1", &cursor).unwrap();
        assert_eq!(store.load("pair-1").unwrap(), Some(cursor));
    }

    // ---- tick ----

    fn build<'a>(
        ax: &'a FakeAxReadPort,
        ladder: &'a ImageLadder<'a>,
        sender: &'a ScriptedSender,
        store: &'a SqliteRelayStore,
        journal: &'a SqliteHistoryStore,
        clock: &'a VirtualClock,
        p: RelayPair,
    ) -> TelegramRelay<'a> {
        TelegramRelay::new(ax, ladder, sender, store, store, journal, clock, p, true)
    }

    #[test]
    fn tick_commits_a_text_message_and_advances_cursor() {
        let clock = VirtualClock::new(1_000);
        let ax = FakeAxReadPort::new(vec![msg("m1", 1, 0, "hello")]);
        let rig = LadderRig::acquiring(clock.clone());
        let ladder = rig.ladder_acquiring();
        let sender = ScriptedSender::new(RelaySendOutcome::Sent { images_delivered: 0 });
        let store = SqliteRelayStore::open_in_memory().unwrap();
        let journal = SqliteHistoryStore::open_in_memory().unwrap();
        let mut relay = build(&ax, &ladder, &sender, &store, &journal, &clock, pair(&[10]));

        let tick = relay.tick();
        assert_eq!(tick.processed.len(), 1);
        assert!(matches!(
            tick.processed[0].settlement,
            Settlement::Committed(_)
        ));
        assert_eq!(sender.send_count(), 1);
        // Cursor advanced to the message.
        assert_eq!(relay.cursor().last_at, 1);
        // Delivered — recorded in the ledger.
        let pid_hash = crate::context::provenance_id("m1");
        assert!(store.is_delivered(10, &pid_hash));
    }

    #[test]
    fn tick_processes_messages_in_time_order() {
        let clock = VirtualClock::new(0);
        // Out of order input; the fake sorts by (at, display_order).
        let ax = FakeAxReadPort::new(vec![
            msg("m3", 3, 0, "c"),
            msg("m1", 1, 0, "a"),
            msg("m2", 2, 0, "b"),
        ]);
        let rig = LadderRig::acquiring(clock.clone());
        let ladder = rig.ladder_acquiring();
        let sender = ScriptedSender::new(RelaySendOutcome::Sent { images_delivered: 0 });
        let store = SqliteRelayStore::open_in_memory().unwrap();
        let journal = SqliteHistoryStore::open_in_memory().unwrap();
        let mut relay = build(&ax, &ladder, &sender, &store, &journal, &clock, pair(&[10]));

        let tick = relay.tick();
        // All three settled as committed, and the cursor walked 1 → 2 → 3, so the
        // confirmed order is the ascending time order (R7.9, R12.8).
        assert_eq!(tick.processed.len(), 3);
        assert!(tick
            .processed
            .iter()
            .all(|o| matches!(o.settlement, Settlement::Committed(_))));
        assert_eq!(*sender.sends.borrow(), vec![10, 10, 10]);
        assert_eq!(relay.cursor().last_at, 3);
    }

    #[test]
    fn tick_dedups_already_delivered_message() {
        let clock = VirtualClock::new(0);
        let ax = FakeAxReadPort::new(vec![msg("m1", 1, 0, "a")]);
        let rig = LadderRig::acquiring(clock.clone());
        let ladder = rig.ladder_acquiring();
        let sender = ScriptedSender::new(RelaySendOutcome::Sent { images_delivered: 0 });
        let store = SqliteRelayStore::open_in_memory().unwrap();
        // Pre-mark as delivered.
        store
            .mark(10, &crate::context::provenance_id("m1"), 1)
            .unwrap();
        let journal = SqliteHistoryStore::open_in_memory().unwrap();
        let mut relay = build(&ax, &ladder, &sender, &store, &journal, &clock, pair(&[10]));

        let tick = relay.tick();
        assert!(matches!(
            tick.processed[0].settlement,
            Settlement::Committed(_)
        ));
        // No new send happened — already delivered (R7.10).
        assert_eq!(sender.send_count(), 0);
        assert_eq!(relay.cursor().last_at, 1);
    }

    #[test]
    fn image_mismatch_retries_then_settles_final_after_three() {
        let clock = VirtualClock::new(0);
        // Two source images, but the sender only ever delivers one.
        let mut m = msg("m1", 1, 0, "with images");
        m.images = vec![img_ref(0), img_ref(1)];
        let ax = FakeAxReadPort::new(vec![m]);
        let rig = LadderRig::acquiring(clock.clone());
        let ladder = rig.ladder_acquiring();
        let sender = ScriptedSender::new(RelaySendOutcome::Sent { images_delivered: 1 });
        let store = SqliteRelayStore::open_in_memory().unwrap();
        let journal = SqliteHistoryStore::open_in_memory().unwrap();
        let mut relay = build(&ax, &ladder, &sender, &store, &journal, &clock, pair(&[10]));

        // Ticks 1 and 2: retrying, cursor pinned.
        for _ in 0..2 {
            let tick = relay.tick();
            assert!(matches!(
                tick.processed[0].settlement,
                Settlement::MissRetrying(_)
            ));
            assert_eq!(relay.cursor().last_at, 0); // not advanced
        }
        // Tick 3: third consecutive mismatch → final miss, cursor advances.
        let tick = relay.tick();
        assert!(matches!(
            tick.processed[0].settlement,
            Settlement::MissFinal(_)
        ));
        assert_eq!(relay.cursor().last_at, 1);
        // Never marked delivered (R7.8/R7.10).
        assert!(!store.is_delivered(10, &crate::context::provenance_id("m1")));
    }

    #[test]
    fn all_images_unacquired_still_sends_text_and_reports_miss() {
        let clock = VirtualClock::new(0);
        let mut m = msg("m1", 1, 0, "text stays");
        m.images = vec![img_ref(0)];
        let ax = FakeAxReadPort::new(vec![m]);
        let rig = FailLadderRig::new(clock.clone());
        let ladder = rig.ladder();
        // The sender is reached (text/links still relayed, R7.17) but delivers
        // zero images.
        let sender = ScriptedSender::new(RelaySendOutcome::Sent { images_delivered: 0 });
        let store = SqliteRelayStore::open_in_memory().unwrap();
        let journal = SqliteHistoryStore::open_in_memory().unwrap();
        let mut relay = build(&ax, &ladder, &sender, &store, &journal, &clock, pair(&[10]));

        let tick = relay.tick();
        // The send still happened (text + links continue, R7.17).
        assert_eq!(sender.send_count(), 1);
        assert!(matches!(
            tick.processed[0].settlement,
            Settlement::MissRetrying(_)
        ));
    }

    #[test]
    fn fenced_room_advances_as_final_miss_without_delivery() {
        let clock = VirtualClock::new(0);
        let ax = FakeAxReadPort::new(vec![msg("m1", 1, 0, "a")]);
        let rig = LadderRig::acquiring(clock.clone());
        let ladder = rig.ladder_acquiring();
        let sender = ScriptedSender::new(RelaySendOutcome::Fenced {
            reason: "안전 설정이 꺼져 있어요".into(),
        });
        let store = SqliteRelayStore::open_in_memory().unwrap();
        let journal = SqliteHistoryStore::open_in_memory().unwrap();
        let mut relay = build(&ax, &ladder, &sender, &store, &journal, &clock, pair(&[10]));

        let tick = relay.tick();
        assert!(matches!(
            tick.processed[0].settlement,
            Settlement::MissFinal(_)
        ));
        // Fenced never marks delivered, and the cursor still advances so the
        // queue is not blocked (R7.15).
        assert!(!store.is_delivered(10, &crate::context::provenance_id("m1")));
        assert_eq!(relay.cursor().last_at, 1);
    }

    #[test]
    fn dropped_images_over_cap_report_and_never_commit() {
        let clock = VirtualClock::new(0);
        let mut m = msg("m1", 1, 0, "many images");
        m.images = (0..(MAX_IMAGES + 2)).map(img_ref).collect();
        let ax = FakeAxReadPort::new(vec![m]);
        let rig = LadderRig::acquiring(clock.clone());
        let ladder = rig.ladder_acquiring();
        // Even delivering the capped 10 leaves the two overflow images missing.
        let sender = ScriptedSender::new(RelaySendOutcome::Sent {
            images_delivered: MAX_IMAGES,
        });
        let store = SqliteRelayStore::open_in_memory().unwrap();
        let journal = SqliteHistoryStore::open_in_memory().unwrap();
        let mut relay = build(&ax, &ladder, &sender, &store, &journal, &clock, pair(&[10]));

        let tick = relay.tick();
        assert_eq!(tick.processed[0].dropped_images, 2);
        assert!(matches!(
            tick.processed[0].settlement,
            Settlement::MissRetrying(_)
        ));
    }

    #[test]
    fn excluded_attachments_are_reported() {
        let clock = VirtualClock::new(0);
        let mut m = msg("m1", 1, 0, "with attachments");
        m.excluded = vec![ExcludedAttachment::Video, ExcludedAttachment::File];
        let ax = FakeAxReadPort::new(vec![m]);
        let rig = LadderRig::acquiring(clock.clone());
        let ladder = rig.ladder_acquiring();
        let sender = ScriptedSender::new(RelaySendOutcome::Sent { images_delivered: 0 });
        let store = SqliteRelayStore::open_in_memory().unwrap();
        let journal = SqliteHistoryStore::open_in_memory().unwrap();
        let mut relay = build(&ax, &ladder, &sender, &store, &journal, &clock, pair(&[10]));

        let tick = relay.tick();
        assert_eq!(
            tick.processed[0].excluded,
            vec![ExcludedAttachment::Video, ExcludedAttachment::File]
        );
    }

    #[test]
    fn detect_mode_switches_to_polling_after_five_seconds_unconfirmed() {
        let clock = VirtualClock::new(0);
        // No messages, so the notification subscription is never confirmed.
        let ax = FakeAxReadPort::new(vec![]);
        let rig = LadderRig::acquiring(clock.clone());
        let ladder = rig.ladder_acquiring();
        let sender = ScriptedSender::new(RelaySendOutcome::Sent { images_delivered: 0 });
        let store = SqliteRelayStore::open_in_memory().unwrap();
        let journal = SqliteHistoryStore::open_in_memory().unwrap();
        let mut relay = build(&ax, &ladder, &sender, &store, &journal, &clock, pair(&[10]));

        // Immediately: still on notifications.
        assert_eq!(relay.tick().detect_mode, DetectMode::Notification);
        // After five seconds without confirmation: polling ≤ 2s (R7.13).
        clock.advance_ms(NOTIFICATION_CONFIRM_MS);
        let tick = relay.tick();
        match tick.detect_mode {
            DetectMode::Polling { interval_ms } => assert!(interval_ms <= POLLING_MAX_INTERVAL_MS),
            other => panic!("expected polling, got {other:?}"),
        }
    }

    #[test]
    fn polling_interval_is_clamped() {
        let clock = VirtualClock::new(0);
        let ax = FakeAxReadPort::new(vec![]).with_mode(DetectMode::Polling { interval_ms: 9_999 });
        let rig = LadderRig::acquiring(clock.clone());
        let ladder = rig.ladder_acquiring();
        let sender = ScriptedSender::new(RelaySendOutcome::Sent { images_delivered: 0 });
        let store = SqliteRelayStore::open_in_memory().unwrap();
        let journal = SqliteHistoryStore::open_in_memory().unwrap();
        let mut relay = build(&ax, &ladder, &sender, &store, &journal, &clock, pair(&[10]));

        match relay.tick().detect_mode {
            DetectMode::Polling { interval_ms } => assert_eq!(interval_ms, POLLING_MAX_INTERVAL_MS),
            other => panic!("expected polling, got {other:?}"),
        }
    }

    #[test]
    fn journal_records_only_redacted_relay_events() {
        let clock = VirtualClock::new(0);
        let ax = FakeAxReadPort::new(vec![msg("secret-pid", 1, 0, "sensitive body")]);
        let rig = LadderRig::acquiring(clock.clone());
        let ladder = rig.ladder_acquiring();
        let sender = ScriptedSender::new(RelaySendOutcome::Sent { images_delivered: 0 });
        let store = SqliteRelayStore::open_in_memory().unwrap();
        let journal = SqliteHistoryStore::open_in_memory().unwrap();
        let mut relay = build(&ax, &ladder, &sender, &store, &journal, &clock, pair(&[10]));

        relay.tick();
        let recent = journal.recent(10).unwrap();
        assert!(!recent.is_empty());
        for ev in recent {
            assert_eq!(ev.flow, FlowKind::TelegramRelay);
            assert!(!ev.trace_id.contains("secret-pid"));
            assert!(!ev.result_code.contains("sensitive"));
        }
    }

    #[test]
    fn resumes_from_persisted_cursor_after_restart() {
        let clock = VirtualClock::new(0);
        let ax = FakeAxReadPort::new(vec![msg("m1", 1, 0, "a"), msg("m2", 2, 0, "b")]);
        let rig = LadderRig::acquiring(clock.clone());
        let ladder = rig.ladder_acquiring();
        let sender = ScriptedSender::new(RelaySendOutcome::Sent { images_delivered: 0 });
        let store = SqliteRelayStore::open_in_memory().unwrap();
        let journal = SqliteHistoryStore::open_in_memory().unwrap();

        {
            let mut relay =
                build(&ax, &ladder, &sender, &store, &journal, &clock, pair(&[10]));
            relay.tick();
            assert_eq!(relay.cursor().last_at, 2);
        }
        // A fresh relay over the same store resumes past both messages — no
        // re-delivery (R7.10).
        let sends_before = sender.send_count();
        let mut relay2 = build(&ax, &ladder, &sender, &store, &journal, &clock, pair(&[10]));
        assert_eq!(relay2.cursor().last_at, 2);
        relay2.tick();
        assert_eq!(sender.send_count(), sends_before);
    }
}
