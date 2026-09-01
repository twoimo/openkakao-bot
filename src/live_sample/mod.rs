//! Live learning-sample accumulation (parent task 4).
//!
//! Real learning samples must accumulate slowly, inside real conversations,
//! never in a short burst that endangers the account (R2). This module counts
//! confirmed automatic-reply results as learning samples, paces confirmed sends
//! with a minimum interval plus jitter, and enforces the three send grades.
//!
//! Three structural guarantees hold here:
//!
//! * **Redacted by construction (R2.10).** [`SampleRecord`] has no field for a
//!   chat body, generated reply, prompt, URL, absolute path, or account
//!   identifier — only provenance-hashed pids, a grade, a score, a latency, and
//!   a timestamp. The `live_sample` table mirrors that shape.
//! * **Idempotent counting (R2.2).** `trace_pid` is `UNIQUE` and
//!   [`SampleStore::record`] uses `INSERT OR IGNORE`, so re-applying the same
//!   commit receipt any number of times increases the count by at most one.
//! * **Pacing depends only on receipt timing (R2.12).** [`plan_pacing`] takes
//!   the configured base interval and the last commit time — never the
//!   remaining target or a deadline — so accumulation speed is driven purely by
//!   incoming partner messages and the base interval. The signature is the
//!   guarantee.
//!
//! [`SendGrade`] is **reused** from [`crate::safety`], not redefined here: both
//! the send ticket and the send intent already carry it. This module implements
//! the [`PacingSource`] and [`GradePolicy`] seams so
//! [`SendGuard`](crate::safety::SendGuard) can enforce the minimum interval and
//! grade limits during authorization.

use std::cell::{Cell, RefCell};
use std::collections::BTreeMap;

use rand::rngs::StdRng;
use rand::{Rng, SeedableRng};
use rusqlite::{params, Connection};
use thiserror::Error;

use crate::logging::{validate_event_id, Stage, StageStatus};
use crate::ports::{Clock, SendAuthority};
use crate::safety::{GradeLimit, GradePolicy, Origin, PacingSource, SendGrade, SendIntent};

/// The confirmed-send cap for one smoke run (R2.7).
pub const SMOKE_CAP: u32 = 3;
/// The floor on the base send interval, in seconds (R2.3).
pub const MIN_INTERVAL_SECS: u64 = 10;
/// The default target sample count when none is configured (R2.2).
pub const DEFAULT_TARGET: u32 = 1_000;
/// The maximum configurable target sample count (R2.2).
pub const MAX_TARGET: u32 = 100_000;

// ---------------------------------------------------------------------------
// Grade <-> string
// ---------------------------------------------------------------------------

/// The stable string persisted in the `grade` column.
fn grade_str(g: SendGrade) -> &'static str {
    match g {
        SendGrade::Fake => "fake",
        SendGrade::Memo => "memo",
        SendGrade::Smoke => "smoke",
    }
}

/// Parse a stored `grade` string.
fn parse_grade(s: &str) -> Option<SendGrade> {
    match s {
        "fake" => Some(SendGrade::Fake),
        "memo" => Some(SendGrade::Memo),
        "smoke" => Some(SendGrade::Smoke),
        _ => None,
    }
}

/// Clamp a raw configured target into `1..=MAX_TARGET`, defaulting to
/// [`DEFAULT_TARGET`] when unset or zero (R2.2).
pub fn resolve_target(raw: Option<u32>) -> u32 {
    match raw {
        None | Some(0) => DEFAULT_TARGET,
        Some(v) => v.min(MAX_TARGET),
    }
}

// ---------------------------------------------------------------------------
// Value types
// ---------------------------------------------------------------------------

/// A redacted learning sample (R2.10). It deliberately holds no chat body,
/// generated reply, prompt, URL, absolute path, or account identifier — only
/// provenance-hashed pids and non-sensitive scalars.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SampleRecord {
    /// Provenance id of the processing (the `commit`/`success` trace). `UNIQUE`
    /// in storage, so one processing accumulates at most once (R2.2).
    pub trace_pid: String,
    /// Provenance id of the room the sample came from.
    pub room_pid: String,
    /// The send grade (R2.4).
    pub grade: SendGrade,
    /// Local quality score, 0..=100.
    pub quality_score: u8,
    /// End-to-end processing latency in milliseconds.
    pub latency_ms: u64,
    /// Logical time the sample was confirmed.
    pub at: i64,
}

/// Progress toward the sample target (R2.2, R2.9).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Progress {
    /// Accumulated samples, **excluding** the `fake` grade (R2.5).
    pub accumulated: u32,
    /// The target sample count (1..=100_000, default 1000).
    pub target: u32,
    /// Sample count per grade (all three grades always present).
    pub by_grade: BTreeMap<SendGrade, u32>,
    /// The last confirmed-send time, if any (KST rendering is the UI's job).
    pub last_commit_at: Option<i64>,
    /// The earliest time the next confirmed send may happen, if paced.
    pub next_allowed_at: Option<i64>,
}

impl Progress {
    /// The `accumulated/target` display string, e.g. `"342/1000"` (R2.2).
    pub fn as_ratio(&self) -> String {
        format!("{}/{}", self.accumulated, self.target)
    }
}

/// The base interval and jitter for the next confirmed send (R2.3).
///
/// The total interval is `base_secs + jitter_secs`, which lies in
/// `[base, 2·base]` because `jitter_secs ∈ [0, base]`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Pacing {
    /// The base interval, `>= 10` seconds.
    pub base_secs: u64,
    /// The random jitter added on top, `0..=base_secs`.
    pub jitter_secs: u64,
    /// The last confirmed-send time this pacing was planned from.
    pub last_commit_at: i64,
    /// The earliest logical time (ms) the next confirmed send may happen.
    pub next_allowed_at: i64,
}

/// Whether a [`SampleStore::record`] actually inserted a new row.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Inserted {
    /// A new sample was inserted; the count increased by one.
    Inserted,
    /// A sample with the same `trace_pid` already existed; the count is
    /// unchanged (R2.2).
    Duplicate,
}

/// Plan the next send's pacing (R2.3).
///
/// `base = max(configured, 10s)`. The jitter is drawn uniformly from
/// `[0, base]`, so the total interval `base + jitter` lies in `[base, 2·base]`.
/// The signature takes **only** the configured base and the last commit time —
/// never the remaining target or a deadline — which is exactly what makes
/// accumulation depend solely on partner-message timing and the base interval
/// (R2.12).
pub fn plan_pacing(
    base_secs_cfg: Option<u64>,
    last_commit_at: Option<i64>,
    rng: &mut impl Rng,
) -> Pacing {
    let base = base_secs_cfg.unwrap_or(0).max(MIN_INTERVAL_SECS);
    let jitter = rng.gen_range(0..=base);
    let total_ms = (base + jitter) as i64 * 1_000;
    let (last, next) = match last_commit_at {
        Some(t) => (t, t.saturating_add(total_ms)),
        // With no prior commit there is nothing to pace against yet.
        None => (0, 0),
    };
    Pacing {
        base_secs: base,
        jitter_secs: jitter,
        last_commit_at: last,
        next_allowed_at: next,
    }
}

// ---------------------------------------------------------------------------
// Errors and rejections
// ---------------------------------------------------------------------------

/// Errors from the sample store and collector.
#[derive(Debug, Error)]
pub enum SampleError {
    /// A receipt that was not `commit`/`success` was handed to
    /// [`LiveSampleCollector::on_commit`]; nothing is accumulated (R2.1).
    #[error("커밋으로 확정된 처리 결과만 표본으로 쌓을 수 있어요")]
    NotACommit,
    /// The backing store failed. The count is left unchanged (R2.13).
    #[error("표본 저장소 오류: {0}")]
    Backend(String),
}

/// Why [`LiveSampleCollector::admit`] refused a send request. Every rejection
/// leaves the accumulated and per-grade counts unchanged (R2.8, R2.11).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AdmitRejection {
    /// The minimum interval has not elapsed. A rejection, not a delay (R2.8).
    TooSoon {
        /// The earliest logical time the next send may happen.
        next_allowed_at: i64,
    },
    /// The request did not originate from an incoming partner message — it was
    /// an artificial send to fill the count, hit the target early, or demo
    /// (R2.11).
    Synthetic,
    /// The send grade could not be confirmed (R2.13).
    GradeUnknown,
    /// A `memo`-grade send targeted something other than the memo chat (R2.6).
    MemoOnlyTarget,
    /// The current smoke run already reached its confirmed-send cap (R2.7).
    SmokeCapReached {
        /// The per-run cap ([`SMOKE_CAP`]).
        cap: u32,
    },
}

// ---------------------------------------------------------------------------
// SmokeRun
// ---------------------------------------------------------------------------

/// One user-started smoke execution, tracking its confirmed sends so the cap of
/// three per run is enforced (R2.7). A smoke run is started by the user each
/// execution, so the counter lives with the run, not in durable storage.
#[derive(Debug)]
pub struct SmokeRun {
    run_id: String,
    confirmed: Cell<u32>,
}

impl SmokeRun {
    /// Start a fresh run with zero confirmed sends.
    pub fn new(run_id: impl Into<String>) -> Self {
        Self {
            run_id: run_id.into(),
            confirmed: Cell::new(0),
        }
    }

    /// The run's identifier.
    pub fn run_id(&self) -> &str {
        &self.run_id
    }

    /// How many sends this run has confirmed.
    pub fn confirmed(&self) -> u32 {
        self.confirmed.get()
    }

    /// Record one more confirmed send.
    pub fn record_confirmed(&self) {
        self.confirmed.set(self.confirmed.get().saturating_add(1));
    }
}

// ---------------------------------------------------------------------------
// SampleStore
// ---------------------------------------------------------------------------

/// The persistence boundary for learning samples and send pacing.
pub trait SampleStore {
    /// Record a sample. Uses `INSERT OR IGNORE`, so a duplicate `trace_pid` is
    /// ignored and the count is unchanged (R2.2).
    fn record(&self, s: &SampleRecord) -> Result<Inserted, SampleError>;

    /// Current progress toward `target` (R2.2, R2.9).
    fn progress(&self, target: u32) -> Result<Progress, SampleError>;

    /// The last confirmed-send time, if any.
    fn last_commit_at(&self) -> Result<Option<i64>, SampleError>;

    /// How many smoke samples have accumulated (reporting aid; the per-run cap
    /// is enforced by [`SmokeRun`]).
    fn smoke_count_in_run(&self, run_id: &str) -> Result<u32, SampleError>;

    /// Load the persisted pacing row, if any.
    fn load_pacing(&self) -> Result<Option<Pacing>, SampleError>;

    /// Persist the pacing row (single global scope).
    fn save_pacing(&self, p: &Pacing) -> Result<(), SampleError>;
}

/// A [`SampleStore`] backed by SQLite over the `live_sample` and `send_pacing`
/// tables.
pub struct SqliteSampleStore {
    conn: Connection,
}

impl SqliteSampleStore {
    /// Wrap a connection, ensuring the schema is present.
    pub fn new(conn: Connection) -> Result<Self, SampleError> {
        ensure_sample_schema(&conn)?;
        Ok(Self { conn })
    }

    /// Open (or create) a store at `path`.
    pub fn open(path: &std::path::Path) -> Result<Self, SampleError> {
        let conn = Connection::open(path).map_err(db_err)?;
        Self::new(conn)
    }

    /// Open an in-memory store. Primarily for tests.
    pub fn open_in_memory() -> Result<Self, SampleError> {
        let conn = Connection::open_in_memory().map_err(db_err)?;
        Self::new(conn)
    }
}

fn db_err(e: rusqlite::Error) -> SampleError {
    SampleError::Backend(e.to_string())
}

/// Create the `live_sample` and `send_pacing` tables if absent (R2 schema).
pub fn ensure_sample_schema(conn: &Connection) -> Result<(), SampleError> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS live_sample(
            id INTEGER PRIMARY KEY,
            trace_pid TEXT NOT NULL UNIQUE,
            room_pid TEXT NOT NULL,
            grade TEXT NOT NULL CHECK(grade IN ('fake','memo','smoke')),
            quality_score INTEGER NOT NULL CHECK(quality_score BETWEEN 0 AND 100),
            latency_ms INTEGER NOT NULL CHECK(latency_ms >= 0),
            at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_live_sample_grade ON live_sample(grade, at DESC);
        CREATE TABLE IF NOT EXISTS send_pacing(
            scope TEXT PRIMARY KEY CHECK(scope = 'global'),
            last_commit_at INTEGER NOT NULL,
            next_allowed_at INTEGER NOT NULL,
            base_secs INTEGER NOT NULL CHECK(base_secs >= 10),
            jitter_secs INTEGER NOT NULL CHECK(jitter_secs >= 0)
        );",
    )
    .map_err(db_err)
}

impl SampleStore for SqliteSampleStore {
    fn record(&self, s: &SampleRecord) -> Result<Inserted, SampleError> {
        let changed = self
            .conn
            .execute(
                "INSERT OR IGNORE INTO live_sample(
                    trace_pid, room_pid, grade, quality_score, latency_ms, at
                ) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                params![
                    s.trace_pid,
                    s.room_pid,
                    grade_str(s.grade),
                    s.quality_score as i64,
                    s.latency_ms as i64,
                    s.at,
                ],
            )
            .map_err(db_err)?;
        Ok(if changed == 1 {
            Inserted::Inserted
        } else {
            Inserted::Duplicate
        })
    }

    fn progress(&self, target: u32) -> Result<Progress, SampleError> {
        let mut by_grade: BTreeMap<SendGrade, u32> = BTreeMap::new();
        by_grade.insert(SendGrade::Fake, 0);
        by_grade.insert(SendGrade::Memo, 0);
        by_grade.insert(SendGrade::Smoke, 0);

        let mut stmt = self
            .conn
            .prepare("SELECT grade, COUNT(*) FROM live_sample GROUP BY grade")
            .map_err(db_err)?;
        let rows = stmt
            .query_map([], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?))
            })
            .map_err(db_err)?;
        for row in rows {
            let (grade, count) = row.map_err(db_err)?;
            if let Some(g) = parse_grade(&grade) {
                by_grade.insert(g, count as u32);
            }
        }

        let accumulated = by_grade[&SendGrade::Memo] + by_grade[&SendGrade::Smoke];
        let pacing = self.load_pacing()?;
        let last_commit_at = match &pacing {
            Some(p) => Some(p.last_commit_at),
            None => self.last_commit_at()?,
        };
        let next_allowed_at = pacing.as_ref().map(|p| p.next_allowed_at);

        Ok(Progress {
            accumulated,
            target,
            by_grade,
            last_commit_at,
            next_allowed_at,
        })
    }

    fn last_commit_at(&self) -> Result<Option<i64>, SampleError> {
        let max_at: Option<i64> = self
            .conn
            .query_row("SELECT MAX(at) FROM live_sample", [], |row| row.get(0))
            .map_err(db_err)?;
        Ok(max_at)
    }

    fn smoke_count_in_run(&self, _run_id: &str) -> Result<u32, SampleError> {
        // The `live_sample` table does not partition by run (it holds no run
        // column), so this reports the cumulative smoke count. Per-run capping
        // is enforced by the in-memory `SmokeRun` counter (R2.7).
        let count: i64 = self
            .conn
            .query_row(
                "SELECT COUNT(*) FROM live_sample WHERE grade = 'smoke'",
                [],
                |row| row.get(0),
            )
            .map_err(db_err)?;
        Ok(count as u32)
    }

    fn load_pacing(&self) -> Result<Option<Pacing>, SampleError> {
        let row = self
            .conn
            .query_row(
                "SELECT last_commit_at, next_allowed_at, base_secs, jitter_secs
                 FROM send_pacing WHERE scope = 'global'",
                [],
                |row| {
                    Ok(Pacing {
                        last_commit_at: row.get(0)?,
                        next_allowed_at: row.get(1)?,
                        base_secs: row.get::<_, i64>(2)? as u64,
                        jitter_secs: row.get::<_, i64>(3)? as u64,
                    })
                },
            )
            .map_err(|e| match e {
                rusqlite::Error::QueryReturnedNoRows => {
                    SampleError::Backend("no-row".to_string())
                }
                other => db_err(other),
            });
        match row {
            Ok(p) => Ok(Some(p)),
            Err(SampleError::Backend(ref s)) if s == "no-row" => Ok(None),
            Err(e) => Err(e),
        }
    }

    fn save_pacing(&self, p: &Pacing) -> Result<(), SampleError> {
        self.conn
            .execute(
                "INSERT INTO send_pacing(scope, last_commit_at, next_allowed_at, base_secs, jitter_secs)
                 VALUES ('global', ?1, ?2, ?3, ?4)
                 ON CONFLICT(scope) DO UPDATE SET
                    last_commit_at = excluded.last_commit_at,
                    next_allowed_at = excluded.next_allowed_at,
                    base_secs = excluded.base_secs,
                    jitter_secs = excluded.jitter_secs",
                params![
                    p.last_commit_at,
                    p.next_allowed_at,
                    p.base_secs as i64,
                    p.jitter_secs as i64,
                ],
            )
            .map_err(db_err)?;
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// CommitReceipt and the collector
// ---------------------------------------------------------------------------

/// A confirmed processing result handed to [`LiveSampleCollector::on_commit`].
/// Only `commit`/`success` receipts are accepted for accumulation (R2.1).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommitReceipt {
    /// The processing trace provenance id (the sample's `trace_pid`).
    pub trace_pid: String,
    /// The room provenance id.
    pub room_pid: String,
    /// The send grade.
    pub grade: SendGrade,
    /// Local quality score, 0..=100.
    pub quality_score: u8,
    /// End-to-end latency in milliseconds.
    pub latency_ms: u64,
    /// Logical time of the confirmation.
    pub at: i64,
    /// The journal stage this receipt reports (must be [`Stage::Commit`]).
    pub stage: Stage,
    /// The journal status this receipt reports (must be
    /// [`StageStatus::Success`]).
    pub status: StageStatus,
}

/// Accumulates confirmed automatic-reply results as learning samples and
/// enforces the send preconditions the guard delegates: minimum interval
/// ([`PacingSource`]) and grade limits ([`GradePolicy`]).
pub struct LiveSampleCollector<'a> {
    store: &'a dyn SampleStore,
    clock: &'a dyn Clock,
    base_secs_cfg: Option<u64>,
    target: u32,
    /// The database-authoritative memo chat id, if known (R2.6).
    memo_chat_id: Option<i64>,
    /// The current smoke run's confirmed-send counter (R2.7).
    smoke_confirmed: Cell<u32>,
    rng: RefCell<StdRng>,
}

impl<'a> LiveSampleCollector<'a> {
    /// Build a collector.
    ///
    /// `base_secs_cfg` is the configured base interval (clamped to `>= 10s`),
    /// `target` the sample target, `memo_chat_id` the database-authoritative
    /// memo chat, and `seed` the deterministic jitter seed.
    pub fn new(
        store: &'a dyn SampleStore,
        clock: &'a dyn Clock,
        base_secs_cfg: Option<u64>,
        target: u32,
        memo_chat_id: Option<i64>,
        seed: u64,
    ) -> Self {
        Self {
            store,
            clock,
            base_secs_cfg,
            target,
            memo_chat_id,
            smoke_confirmed: Cell::new(0),
            rng: RefCell::new(StdRng::seed_from_u64(seed)),
        }
    }

    /// Reset the smoke-run counter at the start of a new user-started run
    /// (R2.7).
    pub fn begin_smoke_run(&self) {
        self.smoke_confirmed.set(0);
    }

    /// How many smoke sends the current run has confirmed.
    pub fn smoke_confirmed(&self) -> u32 {
        self.smoke_confirmed.get()
    }

    /// Pre-screen a send request before authorization (R2.8, R2.11).
    ///
    /// Order: origin (artificial sends are refused), then the grade limit, then
    /// the minimum interval. `fake`-grade sends are never real sends and carry
    /// no cap, so they are not paced. Every rejection leaves the counts
    /// unchanged.
    pub fn admit(
        &self,
        intent: &SendIntent,
        authority: &SendAuthority,
    ) -> Result<(), AdmitRejection> {
        // 1. The send must trace back to an incoming partner message with a
        //    valid event id; anything else is an artificial send (R2.11).
        match &intent.origin {
            Origin::PartnerMessage { event_id } => {
                if validate_event_id(event_id, None).is_err() {
                    return Err(AdmitRejection::Synthetic);
                }
            }
            _ => return Err(AdmitRejection::Synthetic),
        }

        // 2. Grade limits (R2.5, R2.6, R2.7).
        match intent.grade {
            SendGrade::Fake => {}
            SendGrade::Memo => {
                if !authority.is_memo {
                    return Err(AdmitRejection::MemoOnlyTarget);
                }
            }
            SendGrade::Smoke => {
                if self.smoke_confirmed.get() >= SMOKE_CAP {
                    return Err(AdmitRejection::SmokeCapReached { cap: SMOKE_CAP });
                }
            }
        }

        // 3. Minimum interval — a rejection, not a delay (R2.8). Fake-grade
        //    bulk sends are exempt so the durability harness can run fast.
        if intent.grade != SendGrade::Fake {
            let next = self.next_allowed_at();
            if self.clock.now_ms() < next {
                return Err(AdmitRejection::TooSoon {
                    next_allowed_at: next,
                });
            }
        }

        Ok(())
    }

    /// Accumulate a confirmed processing result (R2.1, R2.2, R2.13).
    ///
    /// Only `commit`/`success` receipts are accepted. Recording is idempotent
    /// (`INSERT OR IGNORE` on `trace_pid`), so re-applying the same receipt
    /// increases the count by at most one. If the store fails, the count is
    /// left unchanged and the error is propagated (R2.13). On a fresh insert
    /// the pacing is re-planned from this commit's time (non-fake grades only),
    /// and the smoke-run counter advances for smoke sends.
    pub fn on_commit(&self, receipt: &CommitReceipt) -> Result<Progress, SampleError> {
        if receipt.stage != Stage::Commit || receipt.status != StageStatus::Success {
            return Err(SampleError::NotACommit);
        }

        let record = SampleRecord {
            trace_pid: receipt.trace_pid.clone(),
            room_pid: receipt.room_pid.clone(),
            grade: receipt.grade,
            quality_score: receipt.quality_score,
            latency_ms: receipt.latency_ms,
            at: receipt.at,
        };

        // On store failure the count is unchanged (the insert did not commit).
        let inserted = self.store.record(&record)?;

        if inserted == Inserted::Inserted {
            // `fake` sends are not real confirmed sends, so they do not pace the
            // next real send (R2.5).
            if receipt.grade != SendGrade::Fake {
                let pacing = {
                    let mut rng = self.rng.borrow_mut();
                    plan_pacing(self.base_secs_cfg, Some(receipt.at), &mut *rng)
                };
                self.store.save_pacing(&pacing)?;
            }
            if receipt.grade == SendGrade::Smoke {
                self.smoke_confirmed.set(self.smoke_confirmed.get().saturating_add(1));
            }
        }

        self.store.progress(self.target)
    }
}

/// The minimum-interval pacing seam the guard consults (R2.8).
///
/// A missing pacing row (the first send) means "allowed now" (`0`). A store
/// read failure is fail-closed: it returns [`i64::MAX`] so the guard rejects
/// the send rather than letting an unpaced send through.
impl PacingSource for LiveSampleCollector<'_> {
    fn next_allowed_at(&self) -> i64 {
        match self.store.load_pacing() {
            Ok(Some(p)) => p.next_allowed_at,
            Ok(None) => 0,
            Err(_) => i64::MAX,
        }
    }
}

/// The send-grade policy seam the guard consults (R2.5, R2.6, R2.7).
impl GradePolicy for LiveSampleCollector<'_> {
    fn check(&self, intent: &SendIntent, chat_id: i64) -> Result<(), GradeLimit> {
        match intent.grade {
            SendGrade::Fake => Ok(()),
            SendGrade::Memo => {
                if self.memo_chat_id == Some(chat_id) {
                    Ok(())
                } else {
                    Err(GradeLimit::MemoOnly)
                }
            }
            SendGrade::Smoke => {
                if self.smoke_confirmed.get() < SMOKE_CAP {
                    Ok(())
                } else {
                    Err(GradeLimit::SmokeCapReached)
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn receipt(trace: &str, grade: SendGrade, at: i64) -> CommitReceipt {
        CommitReceipt {
            trace_pid: trace.to_string(),
            room_pid: "room-pid".to_string(),
            grade,
            quality_score: 80,
            latency_ms: 42,
            at,
            stage: Stage::Commit,
            status: StageStatus::Success,
        }
    }

    fn authority(chat_id: i64, is_memo: bool) -> SendAuthority {
        SendAuthority {
            chat_id,
            chat_type: if is_memo { 0 } else { 1 },
            is_memo,
            owner_display_name: Some("owner".into()),
        }
    }

    fn partner_intent(grade: SendGrade) -> SendIntent {
        SendIntent {
            grade,
            origin: Origin::PartnerMessage {
                event_id: "db:42:7".into(),
            },
        }
    }

    #[test]
    fn resolve_target_defaults_and_clamps() {
        assert_eq!(resolve_target(None), DEFAULT_TARGET);
        assert_eq!(resolve_target(Some(0)), DEFAULT_TARGET);
        assert_eq!(resolve_target(Some(500)), 500);
        assert_eq!(resolve_target(Some(u32::MAX)), MAX_TARGET);
    }

    #[test]
    fn plan_pacing_total_interval_within_base_and_double_base() {
        let mut rng = StdRng::seed_from_u64(1);
        for _ in 0..200 {
            let p = plan_pacing(Some(20), Some(1_000), &mut rng);
            assert!(p.base_secs >= MIN_INTERVAL_SECS);
            assert!(p.jitter_secs <= p.base_secs);
            let total = p.next_allowed_at - 1_000;
            let base_ms = p.base_secs as i64 * 1_000;
            assert!(total >= base_ms && total <= 2 * base_ms);
        }
    }

    #[test]
    fn plan_pacing_floors_base_at_ten_seconds() {
        let mut rng = StdRng::seed_from_u64(2);
        let p = plan_pacing(Some(3), Some(0), &mut rng);
        assert_eq!(p.base_secs, MIN_INTERVAL_SECS);
    }

    #[test]
    fn record_is_idempotent_on_duplicate_trace_pid() {
        let store = SqliteSampleStore::open_in_memory().unwrap();
        let s = SampleRecord {
            trace_pid: "t1".into(),
            room_pid: "r1".into(),
            grade: SendGrade::Memo,
            quality_score: 50,
            latency_ms: 10,
            at: 100,
        };
        assert_eq!(store.record(&s).unwrap(), Inserted::Inserted);
        assert_eq!(store.record(&s).unwrap(), Inserted::Duplicate);
        let p = store.progress(1000).unwrap();
        assert_eq!(p.accumulated, 1);
    }

    #[test]
    fn fake_grade_excluded_from_accumulation() {
        let store = SqliteSampleStore::open_in_memory().unwrap();
        store
            .record(&SampleRecord {
                trace_pid: "f1".into(),
                room_pid: "r".into(),
                grade: SendGrade::Fake,
                quality_score: 10,
                latency_ms: 1,
                at: 1,
            })
            .unwrap();
        store
            .record(&SampleRecord {
                trace_pid: "m1".into(),
                room_pid: "r".into(),
                grade: SendGrade::Memo,
                quality_score: 10,
                latency_ms: 1,
                at: 2,
            })
            .unwrap();
        let p = store.progress(1000).unwrap();
        assert_eq!(p.accumulated, 1); // fake excluded
        assert_eq!(p.by_grade[&SendGrade::Fake], 1);
        assert_eq!(p.by_grade[&SendGrade::Memo], 1);
        assert_eq!(p.as_ratio(), "1/1000");
    }

    #[test]
    fn on_commit_rejects_non_commit_receipt_without_counting() {
        let store = SqliteSampleStore::open_in_memory().unwrap();
        let clock = crate::fakes::VirtualClock::new(0);
        let collector = LiveSampleCollector::new(&store, &clock, Some(10), 1000, Some(1), 7);
        let mut r = receipt("t", SendGrade::Memo, 100);
        r.status = StageStatus::Failed;
        assert!(matches!(
            collector.on_commit(&r),
            Err(SampleError::NotACommit)
        ));
        assert_eq!(store.progress(1000).unwrap().accumulated, 0);
    }

    #[test]
    fn on_commit_accumulates_once_and_paces() {
        let store = SqliteSampleStore::open_in_memory().unwrap();
        let clock = crate::fakes::VirtualClock::new(0);
        let collector = LiveSampleCollector::new(&store, &clock, Some(10), 1000, Some(1), 7);
        let r = receipt("t1", SendGrade::Memo, 5_000);
        let p = collector.on_commit(&r).unwrap();
        assert_eq!(p.accumulated, 1);
        // Re-applying the same receipt does not double count.
        let p2 = collector.on_commit(&r).unwrap();
        assert_eq!(p2.accumulated, 1);
        // Pacing was planned forward of the commit time.
        assert!(collector.next_allowed_at() >= 5_000 + 10_000);
    }

    #[test]
    fn admit_rejects_synthetic_origin() {
        let store = SqliteSampleStore::open_in_memory().unwrap();
        let clock = crate::fakes::VirtualClock::new(0);
        let collector = LiveSampleCollector::new(&store, &clock, Some(10), 1000, Some(1), 7);
        let intent = SendIntent {
            grade: SendGrade::Memo,
            origin: Origin::UserComposer,
        };
        assert_eq!(
            collector.admit(&intent, &authority(1, true)),
            Err(AdmitRejection::Synthetic)
        );
    }

    #[test]
    fn admit_memo_only_targets_memo_chat() {
        let store = SqliteSampleStore::open_in_memory().unwrap();
        let clock = crate::fakes::VirtualClock::new(0);
        let collector = LiveSampleCollector::new(&store, &clock, Some(10), 1000, Some(1), 7);
        assert_eq!(
            collector.admit(&partner_intent(SendGrade::Memo), &authority(2, false)),
            Err(AdmitRejection::MemoOnlyTarget)
        );
        assert!(collector
            .admit(&partner_intent(SendGrade::Memo), &authority(2, true))
            .is_ok());
    }

    #[test]
    fn admit_smoke_cap_reached_after_three() {
        let store = SqliteSampleStore::open_in_memory().unwrap();
        let clock = crate::fakes::VirtualClock::new(0);
        let collector = LiveSampleCollector::new(&store, &clock, Some(10), 1000, None, 7);
        // Simulate three confirmed smoke sends.
        for i in 0..3 {
            let r = receipt(&format!("s{i}"), SendGrade::Smoke, 1);
            collector.on_commit(&r).unwrap();
        }
        assert_eq!(
            collector.admit(&partner_intent(SendGrade::Smoke), &authority(9, false)),
            Err(AdmitRejection::SmokeCapReached { cap: SMOKE_CAP })
        );
    }

    #[test]
    fn admit_too_soon_is_rejection() {
        let store = SqliteSampleStore::open_in_memory().unwrap();
        let clock = crate::fakes::VirtualClock::new(1_000);
        let collector = LiveSampleCollector::new(&store, &clock, Some(10), 1000, Some(1), 7);
        // A memo commit at t=1000 paces the next send well into the future.
        collector
            .on_commit(&receipt("t1", SendGrade::Memo, 1_000))
            .unwrap();
        match collector.admit(&partner_intent(SendGrade::Memo), &authority(1, true)) {
            Err(AdmitRejection::TooSoon { next_allowed_at }) => {
                assert!(next_allowed_at >= 1_000 + 10_000);
            }
            other => panic!("expected TooSoon, got {other:?}"),
        }
    }

    #[test]
    fn grade_policy_matches_authorization_rules() {
        let store = SqliteSampleStore::open_in_memory().unwrap();
        let clock = crate::fakes::VirtualClock::new(0);
        let collector = LiveSampleCollector::new(&store, &clock, Some(10), 1000, Some(5), 7);
        // memo grade allowed only for the memo chat id.
        assert!(collector
            .check(&partner_intent(SendGrade::Memo), 5)
            .is_ok());
        assert_eq!(
            collector.check(&partner_intent(SendGrade::Memo), 6),
            Err(GradeLimit::MemoOnly)
        );
        // fake grade always allowed.
        assert!(collector.check(&partner_intent(SendGrade::Fake), 999).is_ok());
    }
}
