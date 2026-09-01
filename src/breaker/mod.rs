//! The emergency breaker — pure trip evaluation and restart-durable trip state
//! (task 4.1).
//!
//! Normal use must never be disturbed, but an abnormal situation must stop
//! automatic sends at once, so an unexpected burst cannot get the account
//! restricted (R3). Three structural guarantees hold here:
//!
//! * **Judgement is a pure function of exactly three values (R3.9).**
//!   [`EmergencyBreaker::evaluate`] takes only a [`BreakerObservation`], which
//!   carries nothing but the room identifier, the confirmed-send timestamps of
//!   the last five minutes, the last twenty result codes, and the alternation
//!   observation. There is no field for anything else, so "judge on only these
//!   three values" is enforced by the input type, not by convention.
//! * **A trip survives restart (R3.12).** The trip state is a row in the
//!   `breaker_trip` table, opened with `journal_mode = WAL` and
//!   `synchronous = FULL`, so a crash immediately after tripping still leaves
//!   the row. [`BreakerGateSource::breaker_gate`] reads that row on **every**
//!   authorization and never caches, so "applied before the first send decision
//!   after restart" cannot break on ordering.
//! * **Release is user-explicit only (R3.6, R3.13).** Only
//!   [`BreakerStore::clear_and_reset`] removes a trip, and it also resets the
//!   alternation / burst / spike observations. No code path clears a trip on
//!   elapsed time, restart, configuration change, or automatic retry.
//!
//! A read failure is fail-closed (R3.10): [`BreakerGateSource::breaker_gate`]
//! maps a load failure to [`BreakerGate::Unknown`], which the send guard treats
//! as blocked rather than open.
//!
//! The breaker owns the *authoritative* [`BreakReason`]/[`BreakScope`] here; the
//! send guard keeps mirror copies in [`crate::safety`] so that extending the
//! breaker never enlarges the six-input safety gate. The [`BreakerGateSource`]
//! implementation maps the authoritative values into the guard's mirror types.

use rusqlite::{params, Connection, OptionalExtension};
use thiserror::Error;

use crate::logging::{
    FlowKind, HistoryStore, PipelineEvent, Stage, StageStatus,
};
use crate::ports::Clock;
use crate::safety::{self, BreakerGate, BreakerGateSource};

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/// How many consecutive agent↔partner alternations mark a bot-to-bot loop
/// (R3.1).
pub const BOT_LOOP_ALTERNATIONS: u8 = 3;
/// The largest round-trip gap, in seconds, that still counts toward a bot loop
/// (R3.1).
pub const BOT_LOOP_MAX_GAP_SECS: i64 = 60;
/// The burst window, in seconds (R3.2).
pub const BURST_WINDOW_SECS: i64 = 300;
/// The confirmed-send count above which a burst trips within the window (R3.2).
pub const BURST_MAX_COMMITS: usize = 10;
/// How many recent result codes the error-spike check considers (R3.3).
pub const SPIKE_WINDOW: usize = 20;
/// The failed-ratio above which an error spike trips (R3.3).
pub const SPIKE_RATIO: f32 = 0.30;

// ---------------------------------------------------------------------------
// Authoritative reason / scope
// ---------------------------------------------------------------------------

/// Why the breaker tripped. The authoritative copy; [`crate::safety`] keeps a
/// mirror the send guard uses.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BreakReason {
    /// A bot-to-bot reply loop in one room (R3.1).
    BotLoop,
    /// A burst of confirmed sends to one room (R3.2).
    SendBurst,
    /// A spike in failed processings across all rooms (R3.3).
    ErrorSpike,
}

impl BreakReason {
    /// The stable string persisted in the `reason` column.
    fn as_str(self) -> &'static str {
        match self {
            BreakReason::BotLoop => "bot_loop",
            BreakReason::SendBurst => "send_burst",
            BreakReason::ErrorSpike => "error_spike",
        }
    }

    /// Parse a stored `reason` string.
    fn parse(s: &str) -> Option<Self> {
        match s {
            "bot_loop" => Some(BreakReason::BotLoop),
            "send_burst" => Some(BreakReason::SendBurst),
            "error_spike" => Some(BreakReason::ErrorSpike),
            _ => None,
        }
    }
}

/// The scope a trip covers. The authoritative copy; [`crate::safety`] keeps a
/// mirror.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BreakScope {
    /// One room, by chat id (Bot_Loop / Send_Burst).
    Room(i64),
    /// Every room (Error_Spike).
    All,
}

impl BreakScope {
    /// The stable string used as the `breaker_trip` primary key: `all` or
    /// `room:<chat_id>`.
    fn as_key(self) -> String {
        match self {
            BreakScope::All => "all".to_string(),
            BreakScope::Room(id) => format!("room:{id}"),
        }
    }

    /// Reconstruct a scope from its stored key.
    fn parse_key(key: &str) -> Option<Self> {
        if key == "all" {
            return Some(BreakScope::All);
        }
        key.strip_prefix("room:")
            .and_then(|rest| rest.parse::<i64>().ok())
            .map(BreakScope::Room)
    }

    /// Whether a trip with this scope covers `chat_id`.
    fn covers(self, chat_id: i64) -> bool {
        match self {
            BreakScope::All => true,
            BreakScope::Room(id) => id == chat_id,
        }
    }
}

// ---------------------------------------------------------------------------
// Observation and trip state
// ---------------------------------------------------------------------------

/// The agent↔partner alternation observation for one room: the run length of
/// consecutive human-free alternations and the gap (seconds) of each round-trip
/// in that run (R3.1).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Alternation {
    /// How many consecutive agent↔partner alternations have occurred without
    /// human intervention.
    pub run: u8,
    /// The gap of each counted round-trip, in seconds.
    pub gaps_secs: Vec<i64>,
}

impl Alternation {
    /// Whether this is a tight bot-to-bot loop: at least
    /// [`BOT_LOOP_ALTERNATIONS`] alternations, each round-trip gap within
    /// [`BOT_LOOP_MAX_GAP_SECS`] (R3.1).
    fn is_tight_loop(&self) -> bool {
        self.run >= BOT_LOOP_ALTERNATIONS
            && self.gaps_secs.len() >= BOT_LOOP_ALTERNATIONS as usize
            && self
                .gaps_secs
                .iter()
                .all(|gap| *gap >= 0 && *gap <= BOT_LOOP_MAX_GAP_SECS)
    }
}

/// Everything the breaker is allowed to judge on — and nothing else (R3.9).
///
/// Holding only these three kinds of value is what makes the "judge on only the
/// room identifier, the last-5-minutes confirmed-send timestamps, and the
/// last-20 result codes" rule a property of the type rather than a convention.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BreakerObservation {
    /// The room being observed.
    pub room_id: i64,
    /// Confirmed-send timestamps within the last five minutes, for this room.
    pub commits_last_5min: Vec<i64>,
    /// The most recent result codes (caller caps at [`SPIKE_WINDOW`]).
    pub recent_outcomes: Vec<StageStatus>,
    /// The agent↔partner alternation observation.
    pub alternation: Alternation,
}

/// A recorded trip: why, over what scope, and when.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TripState {
    /// Why the breaker tripped.
    pub reason: BreakReason,
    /// The scope the trip covers.
    pub scope: BreakScope,
    /// Logical time (ms) the trip fired.
    pub at: i64,
}

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

/// A failure from the breaker store.
#[derive(Debug, Error)]
pub enum BreakerError {
    /// The backing store failed.
    #[error("비상 브레이커 저장소 오류: {0}")]
    Backend(String),
}

fn db_err(e: rusqlite::Error) -> BreakerError {
    BreakerError::Backend(e.to_string())
}

// ---------------------------------------------------------------------------
// Pure evaluation
// ---------------------------------------------------------------------------

/// Decide whether an observation trips the breaker (R3.1–R3.3, R3.9).
///
/// This is a side-effect-free pure function: it returns the reason and scope
/// when Bot_Loop **or** Send_Burst **or** Error_Spike holds, and `None`
/// otherwise. When more than one condition holds it reports the broadest, most
/// protective one first — Error_Spike (all rooms), then Send_Burst, then
/// Bot_Loop (the room). Changing any value that is not one of the three inputs
/// cannot change the result, because no such value is available to read.
pub fn evaluate(obs: &BreakerObservation) -> Option<(BreakReason, BreakScope)> {
    // Error_Spike is the broadest scope (all rooms), so it wins when it holds.
    if is_error_spike(&obs.recent_outcomes) {
        return Some((BreakReason::ErrorSpike, BreakScope::All));
    }
    // Send_Burst: more than the cap of confirmed sends in the last five minutes.
    if obs.commits_last_5min.len() > BURST_MAX_COMMITS {
        return Some((BreakReason::SendBurst, BreakScope::Room(obs.room_id)));
    }
    // Bot_Loop: a tight run of human-free alternations.
    if obs.alternation.is_tight_loop() {
        return Some((BreakReason::BotLoop, BreakScope::Room(obs.room_id)));
    }
    None
}

/// Whether the most recent outcomes (capped at [`SPIKE_WINDOW`]) exceed the
/// failed ratio (R3.3). The ratio is measured over the considered window; with
/// no outcomes there is no spike.
fn is_error_spike(outcomes: &[StageStatus]) -> bool {
    let window = if outcomes.len() > SPIKE_WINDOW {
        &outcomes[outcomes.len() - SPIKE_WINDOW..]
    } else {
        outcomes
    };
    if window.is_empty() {
        return false;
    }
    let failed = window
        .iter()
        .filter(|s| **s == StageStatus::Failed)
        .count();
    (failed as f32 / window.len() as f32) > SPIKE_RATIO
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

/// The persistence boundary for breaker trips (R3.12, R3.13).
pub trait BreakerStore {
    /// Load all active trips. Survives restart (R3.12).
    fn load(&self) -> Result<Vec<TripState>, BreakerError>;

    /// Record a trip, overwriting any existing trip for the same scope.
    fn trip(&self, s: &TripState) -> Result<(), BreakerError>;

    /// User-explicit release only: remove the trip(s) covering `scope` and
    /// reset the alternation / burst / spike observations for that scope
    /// (R3.6, R3.13).
    fn clear_and_reset(&self, scope: BreakScope) -> Result<(), BreakerError>;
}

/// A [`BreakerStore`] backed by SQLite over the `breaker_trip` and
/// `breaker_observation` tables.
///
/// The connection is opened with `journal_mode = WAL` and `synchronous = FULL`
/// so a trip written just before a crash still survives the restart (R3.12).
pub struct SqliteBreakerStore {
    conn: Connection,
}

impl SqliteBreakerStore {
    /// Wrap a connection, applying the durability pragmas and ensuring the
    /// schema is present.
    pub fn new(conn: Connection) -> Result<Self, BreakerError> {
        // WAL + FULL: a trip survives a crash immediately after it fires
        // (R3.12). On an in-memory database these are effectively no-ops, which
        // is fine for tests.
        conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;")
            .map_err(db_err)?;
        ensure_breaker_schema(&conn)?;
        Ok(Self { conn })
    }

    /// Open (or create) a store at `path`.
    pub fn open(path: &std::path::Path) -> Result<Self, BreakerError> {
        let conn = Connection::open(path).map_err(db_err)?;
        Self::new(conn)
    }

    /// Open an in-memory store. Primarily for tests.
    pub fn open_in_memory() -> Result<Self, BreakerError> {
        let conn = Connection::open_in_memory().map_err(db_err)?;
        Self::new(conn)
    }

    /// Record or advance the alternation observation for a room. Wiring in the
    /// auto-reply flow uses this so [`evaluate`] can be fed a faithful
    /// [`Alternation`]; [`BreakerStore::clear_and_reset`] resets it (R3.13).
    pub fn upsert_observation(
        &self,
        room_id: i64,
        alternation_run: u8,
        last_partner_at: Option<i64>,
        last_agent_at: Option<i64>,
    ) -> Result<(), BreakerError> {
        self.conn
            .execute(
                "INSERT INTO breaker_observation(
                    room_id, alternation_run, last_partner_at, last_agent_at
                 ) VALUES (?1, ?2, ?3, ?4)
                 ON CONFLICT(room_id) DO UPDATE SET
                    alternation_run = excluded.alternation_run,
                    last_partner_at = excluded.last_partner_at,
                    last_agent_at = excluded.last_agent_at",
                params![room_id, alternation_run as i64, last_partner_at, last_agent_at],
            )
            .map_err(db_err)?;
        Ok(())
    }

    /// Read the stored alternation run length for a room, `0` when none.
    pub fn observation_run(&self, room_id: i64) -> Result<u8, BreakerError> {
        let run: Option<i64> = self
            .conn
            .query_row(
                "SELECT alternation_run FROM breaker_observation WHERE room_id = ?1",
                params![room_id],
                |row| row.get(0),
            )
            .optional()
            .map_err(db_err)?;
        Ok(run.unwrap_or(0).clamp(0, u8::MAX as i64) as u8)
    }
}

/// Create the `breaker_trip` and `breaker_observation` tables if absent (R3
/// schema).
pub fn ensure_breaker_schema(conn: &Connection) -> Result<(), BreakerError> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS breaker_trip(
            scope TEXT PRIMARY KEY,
            reason TEXT NOT NULL CHECK(reason IN ('bot_loop','send_burst','error_spike')),
            tripped_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS breaker_observation(
            room_id INTEGER PRIMARY KEY,
            alternation_run INTEGER NOT NULL DEFAULT 0,
            last_partner_at INTEGER,
            last_agent_at INTEGER
        );",
    )
    .map_err(db_err)
}

impl BreakerStore for SqliteBreakerStore {
    fn load(&self) -> Result<Vec<TripState>, BreakerError> {
        let mut stmt = self
            .conn
            .prepare("SELECT scope, reason, tripped_at FROM breaker_trip")
            .map_err(db_err)?;
        let rows = stmt
            .query_map([], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, i64>(2)?,
                ))
            })
            .map_err(db_err)?;
        let mut trips = Vec::new();
        for row in rows {
            let (scope, reason, at) = row.map_err(db_err)?;
            let (Some(scope), Some(reason)) =
                (BreakScope::parse_key(&scope), BreakReason::parse(&reason))
            else {
                // A malformed row is treated as unreadable — fail-closed at the
                // gate rather than silently ignored (R3.10).
                return Err(BreakerError::Backend(format!(
                    "malformed breaker_trip row: scope={scope}, reason={reason}"
                )));
            };
            trips.push(TripState { reason, scope, at });
        }
        Ok(trips)
    }

    fn trip(&self, s: &TripState) -> Result<(), BreakerError> {
        self.conn
            .execute(
                "INSERT INTO breaker_trip(scope, reason, tripped_at)
                 VALUES (?1, ?2, ?3)
                 ON CONFLICT(scope) DO UPDATE SET
                    reason = excluded.reason,
                    tripped_at = excluded.tripped_at",
                params![s.scope.as_key(), s.reason.as_str(), s.at],
            )
            .map_err(db_err)?;
        Ok(())
    }

    fn clear_and_reset(&self, scope: BreakScope) -> Result<(), BreakerError> {
        match scope {
            BreakScope::All => {
                // Releasing "all" clears every trip and resets every room's
                // observation (R3.13).
                self.conn
                    .execute("DELETE FROM breaker_trip", [])
                    .map_err(db_err)?;
                self.conn
                    .execute("DELETE FROM breaker_observation", [])
                    .map_err(db_err)?;
            }
            BreakScope::Room(id) => {
                self.conn
                    .execute(
                        "DELETE FROM breaker_trip WHERE scope = ?1",
                        params![scope.as_key()],
                    )
                    .map_err(db_err)?;
                self.conn
                    .execute(
                        "DELETE FROM breaker_observation WHERE room_id = ?1",
                        params![id],
                    )
                    .map_err(db_err)?;
            }
        }
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// EmergencyBreaker
// ---------------------------------------------------------------------------

/// The emergency breaker: pure evaluation over a store, with an optional
/// redacted journal.
///
/// It also serves as the [`BreakerGateSource`] the send guard consults on every
/// authorization, mapping the authoritative [`BreakReason`]/[`BreakScope`] into
/// the guard's mirror types.
pub struct EmergencyBreaker<'a> {
    store: &'a dyn BreakerStore,
    clock: &'a dyn Clock,
    journal: Option<&'a dyn HistoryStore>,
}

impl<'a> EmergencyBreaker<'a> {
    /// Build a breaker with no journal (evaluation and gating only).
    pub fn new(store: &'a dyn BreakerStore, clock: &'a dyn Clock) -> Self {
        Self {
            store,
            clock,
            journal: None,
        }
    }

    /// Build a breaker that also writes a redacted trip record to the pipeline
    /// journal on detection (R3.11).
    pub fn with_journal(
        store: &'a dyn BreakerStore,
        clock: &'a dyn Clock,
        journal: &'a dyn HistoryStore,
    ) -> Self {
        Self {
            store,
            clock,
            journal: Some(journal),
        }
    }

    /// Pure trip evaluation (see [`evaluate`]).
    pub fn evaluate(obs: &BreakerObservation) -> Option<(BreakReason, BreakScope)> {
        evaluate(obs)
    }

    /// Evaluate an observation and, if it trips, persist the trip and leave a
    /// redacted journal record within one second of detection (R3.1–R3.3,
    /// R3.11).
    ///
    /// Returns the recorded [`TripState`] when a trip fired, `None` otherwise.
    /// The journal record carries only the trip time, a reason code, and the
    /// scope (hashed into the trace id); it holds no chat body, reply, prompt,
    /// URL, absolute path, or account identifier.
    pub fn observe(&self, obs: &BreakerObservation) -> Result<Option<TripState>, BreakerError> {
        let Some((reason, scope)) = evaluate(obs) else {
            return Ok(None);
        };
        let trip = TripState {
            reason,
            scope,
            at: self.clock.now_ms(),
        };
        self.store.trip(&trip)?;

        if let Some(journal) = self.journal {
            // Best-effort redacted record. The scope key is hashed by the
            // journal's provenance step, so no raw identifier is stored.
            let event = PipelineEvent {
                trace_id: format!("breaker:{}", scope.as_key()),
                flow: FlowKind::AutoReply,
                stage: Stage::Authorize,
                status: StageStatus::Failed,
                result_code: format!("breaker_trip_{}", reason.as_str()),
                duration_ms: 0,
                at: trip.at,
            };
            // A journal failure must not lose the trip, which is already
            // persisted; the record is a report, not the source of truth.
            let _ = journal.append(event);
        }

        Ok(Some(trip))
    }

    /// User-explicit release of a scope (R3.6, R3.13). Resets the alternation /
    /// burst / spike observations and resumes sends for the scope.
    pub fn release(&self, scope: BreakScope) -> Result<(), BreakerError> {
        self.store.clear_and_reset(scope)
    }

    /// The trip covering `chat_id`, if any. A load failure surfaces as `Err`;
    /// the [`BreakerGateSource`] impl turns that into a fail-closed gate.
    fn trip_for(&self, chat_id: i64) -> Result<Option<TripState>, BreakerError> {
        let trips = self.store.load()?;
        // An all-scope trip is the most protective, so prefer it when present.
        if let Some(all) = trips.iter().find(|t| t.scope == BreakScope::All) {
            return Ok(Some(*all));
        }
        Ok(trips.into_iter().find(|t| t.scope.covers(chat_id)))
    }
}

/// Map an authoritative reason to the send guard's mirror type.
fn to_guard_reason(reason: BreakReason) -> safety::BreakReason {
    match reason {
        BreakReason::BotLoop => safety::BreakReason::BotLoop,
        BreakReason::SendBurst => safety::BreakReason::SendBurst,
        BreakReason::ErrorSpike => safety::BreakReason::ErrorSpike,
    }
}

/// Map an authoritative scope to the send guard's mirror type.
fn to_guard_scope(scope: BreakScope) -> safety::BreakScope {
    match scope {
        BreakScope::Room(id) => safety::BreakScope::Room(id),
        BreakScope::All => safety::BreakScope::All,
    }
}

/// The breaker is the send guard's per-chat gate source. It is queried on every
/// authorization (never cached), and a read failure becomes
/// [`BreakerGate::Unknown`] — fail-closed (R3.10, R3.12).
impl BreakerGateSource for EmergencyBreaker<'_> {
    fn breaker_gate(&self, chat_id: i64) -> BreakerGate {
        match self.trip_for(chat_id) {
            Ok(None) => BreakerGate::Open,
            Ok(Some(trip)) => BreakerGate::Tripped {
                reason: to_guard_reason(trip.reason),
                scope: to_guard_scope(trip.scope),
                at: trip.at,
            },
            Err(_) => BreakerGate::Unknown("브레이커 상태를 읽지 못했어요"),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fakes::VirtualClock;

    fn no_trip_obs(room_id: i64) -> BreakerObservation {
        BreakerObservation {
            room_id,
            commits_last_5min: vec![1, 2, 3],
            recent_outcomes: vec![StageStatus::Success; 20],
            alternation: Alternation::default(),
        }
    }

    // ---- evaluate: pure judgement ----

    #[test]
    fn evaluate_none_when_quiet() {
        assert_eq!(evaluate(&no_trip_obs(7)), None);
    }

    #[test]
    fn evaluate_bot_loop_when_tight_run() {
        let mut obs = no_trip_obs(7);
        obs.alternation = Alternation {
            run: BOT_LOOP_ALTERNATIONS,
            gaps_secs: vec![10, 20, 30],
        };
        assert_eq!(
            evaluate(&obs),
            Some((BreakReason::BotLoop, BreakScope::Room(7)))
        );
    }

    #[test]
    fn evaluate_no_bot_loop_when_a_gap_exceeds_max() {
        let mut obs = no_trip_obs(7);
        obs.alternation = Alternation {
            run: BOT_LOOP_ALTERNATIONS,
            gaps_secs: vec![10, 20, BOT_LOOP_MAX_GAP_SECS + 1],
        };
        assert_eq!(evaluate(&obs), None);
    }

    #[test]
    fn evaluate_no_bot_loop_when_run_below_threshold() {
        let mut obs = no_trip_obs(7);
        obs.alternation = Alternation {
            run: BOT_LOOP_ALTERNATIONS - 1,
            gaps_secs: vec![10, 20],
        };
        assert_eq!(evaluate(&obs), None);
    }

    #[test]
    fn evaluate_send_burst_when_over_cap() {
        let mut obs = no_trip_obs(9);
        obs.commits_last_5min = (0..=BURST_MAX_COMMITS as i64).collect(); // 11 > 10
        assert_eq!(
            evaluate(&obs),
            Some((BreakReason::SendBurst, BreakScope::Room(9)))
        );
    }

    #[test]
    fn evaluate_no_send_burst_at_exactly_cap() {
        let mut obs = no_trip_obs(9);
        obs.commits_last_5min = (0..BURST_MAX_COMMITS as i64).collect(); // exactly 10
        assert_eq!(evaluate(&obs), None);
    }

    #[test]
    fn evaluate_error_spike_when_over_ratio() {
        let mut obs = no_trip_obs(3);
        // 7 of 20 failed = 35% > 30%.
        let mut outcomes = vec![StageStatus::Failed; 7];
        outcomes.extend(vec![StageStatus::Success; 13]);
        obs.recent_outcomes = outcomes;
        assert_eq!(
            evaluate(&obs),
            Some((BreakReason::ErrorSpike, BreakScope::All))
        );
    }

    #[test]
    fn evaluate_no_error_spike_at_or_below_ratio() {
        let mut obs = no_trip_obs(3);
        // 6 of 20 failed = 30%, not over 30%.
        let mut outcomes = vec![StageStatus::Failed; 6];
        outcomes.extend(vec![StageStatus::Success; 14]);
        obs.recent_outcomes = outcomes;
        assert_eq!(evaluate(&obs), None);
    }

    #[test]
    fn evaluate_error_spike_uses_only_last_window() {
        let mut obs = no_trip_obs(3);
        // 21 outcomes: the oldest is failed but the last 20 are all success.
        let mut outcomes = vec![StageStatus::Failed; 1];
        outcomes.extend(vec![StageStatus::Success; 20]);
        obs.recent_outcomes = outcomes;
        assert_eq!(evaluate(&obs), None);
    }

    #[test]
    fn evaluate_error_spike_wins_over_room_conditions() {
        let mut obs = no_trip_obs(3);
        obs.commits_last_5min = (0..=BURST_MAX_COMMITS as i64).collect(); // would burst
        obs.recent_outcomes = vec![StageStatus::Failed; 20]; // also spikes
        assert_eq!(
            evaluate(&obs),
            Some((BreakReason::ErrorSpike, BreakScope::All))
        );
    }

    // ---- store: persistence and release ----

    #[test]
    fn trip_persists_and_loads() {
        let store = SqliteBreakerStore::open_in_memory().unwrap();
        let trip = TripState {
            reason: BreakReason::SendBurst,
            scope: BreakScope::Room(42),
            at: 1_000,
        };
        store.trip(&trip).unwrap();
        let loaded = store.load().unwrap();
        assert_eq!(loaded, vec![trip]);
    }

    #[test]
    fn trip_overwrites_same_scope() {
        let store = SqliteBreakerStore::open_in_memory().unwrap();
        store
            .trip(&TripState {
                reason: BreakReason::BotLoop,
                scope: BreakScope::Room(1),
                at: 1,
            })
            .unwrap();
        store
            .trip(&TripState {
                reason: BreakReason::SendBurst,
                scope: BreakScope::Room(1),
                at: 2,
            })
            .unwrap();
        let loaded = store.load().unwrap();
        assert_eq!(loaded.len(), 1);
        assert_eq!(loaded[0].reason, BreakReason::SendBurst);
        assert_eq!(loaded[0].at, 2);
    }

    #[test]
    fn clear_and_reset_room_only_clears_that_room() {
        let store = SqliteBreakerStore::open_in_memory().unwrap();
        store
            .trip(&TripState {
                reason: BreakReason::BotLoop,
                scope: BreakScope::Room(1),
                at: 1,
            })
            .unwrap();
        store
            .trip(&TripState {
                reason: BreakReason::BotLoop,
                scope: BreakScope::Room(2),
                at: 1,
            })
            .unwrap();
        store.clear_and_reset(BreakScope::Room(1)).unwrap();
        let loaded = store.load().unwrap();
        assert_eq!(loaded.len(), 1);
        assert_eq!(loaded[0].scope, BreakScope::Room(2));
    }

    #[test]
    fn clear_and_reset_all_clears_everything_and_observations() {
        let store = SqliteBreakerStore::open_in_memory().unwrap();
        store
            .trip(&TripState {
                reason: BreakReason::ErrorSpike,
                scope: BreakScope::All,
                at: 1,
            })
            .unwrap();
        store.upsert_observation(5, 2, Some(10), Some(20)).unwrap();
        assert_eq!(store.observation_run(5).unwrap(), 2);

        store.clear_and_reset(BreakScope::All).unwrap();
        assert!(store.load().unwrap().is_empty());
        assert_eq!(store.observation_run(5).unwrap(), 0);
    }

    #[test]
    fn observation_upsert_and_room_reset() {
        let store = SqliteBreakerStore::open_in_memory().unwrap();
        store.upsert_observation(5, 3, Some(100), Some(200)).unwrap();
        assert_eq!(store.observation_run(5).unwrap(), 3);
        store.clear_and_reset(BreakScope::Room(5)).unwrap();
        assert_eq!(store.observation_run(5).unwrap(), 0);
    }

    // ---- gate: mapping and fail-closed ----

    #[test]
    fn gate_open_when_no_trip() {
        let store = SqliteBreakerStore::open_in_memory().unwrap();
        let clock = VirtualClock::new(0);
        let breaker = EmergencyBreaker::new(&store, &clock);
        assert_eq!(breaker.breaker_gate(42), BreakerGate::Open);
    }

    #[test]
    fn gate_tripped_maps_room_scope() {
        let store = SqliteBreakerStore::open_in_memory().unwrap();
        let clock = VirtualClock::new(0);
        let breaker = EmergencyBreaker::new(&store, &clock);
        store
            .trip(&TripState {
                reason: BreakReason::SendBurst,
                scope: BreakScope::Room(42),
                at: 500,
            })
            .unwrap();
        assert_eq!(
            breaker.breaker_gate(42),
            BreakerGate::Tripped {
                reason: safety::BreakReason::SendBurst,
                scope: safety::BreakScope::Room(42),
                at: 500,
            }
        );
        // A different room is not covered by a room-scoped trip.
        assert_eq!(breaker.breaker_gate(43), BreakerGate::Open);
    }

    #[test]
    fn gate_all_scope_covers_every_room() {
        let store = SqliteBreakerStore::open_in_memory().unwrap();
        let clock = VirtualClock::new(0);
        let breaker = EmergencyBreaker::new(&store, &clock);
        store
            .trip(&TripState {
                reason: BreakReason::ErrorSpike,
                scope: BreakScope::All,
                at: 7,
            })
            .unwrap();
        assert_eq!(
            breaker.breaker_gate(999),
            BreakerGate::Tripped {
                reason: safety::BreakReason::ErrorSpike,
                scope: safety::BreakScope::All,
                at: 7,
            }
        );
    }

    #[test]
    fn gate_unknown_is_fail_closed_on_load_error() {
        struct FailingStore;
        impl BreakerStore for FailingStore {
            fn load(&self) -> Result<Vec<TripState>, BreakerError> {
                Err(BreakerError::Backend("boom".into()))
            }
            fn trip(&self, _s: &TripState) -> Result<(), BreakerError> {
                Ok(())
            }
            fn clear_and_reset(&self, _scope: BreakScope) -> Result<(), BreakerError> {
                Ok(())
            }
        }
        let clock = VirtualClock::new(0);
        let store = FailingStore;
        let breaker = EmergencyBreaker::new(&store, &clock);
        assert!(matches!(
            breaker.breaker_gate(1),
            BreakerGate::Unknown(_)
        ));
    }

    // ---- observe: detection persists a trip, survives a "restart" ----

    #[test]
    fn observe_persists_trip_and_survives_reload() {
        let store = SqliteBreakerStore::open_in_memory().unwrap();
        let clock = VirtualClock::new(1_234);
        let breaker = EmergencyBreaker::new(&store, &clock);

        let mut obs = no_trip_obs(9);
        obs.commits_last_5min = (0..=BURST_MAX_COMMITS as i64).collect();
        let trip = breaker.observe(&obs).unwrap().expect("should trip");
        assert_eq!(trip.reason, BreakReason::SendBurst);
        assert_eq!(trip.scope, BreakScope::Room(9));
        assert_eq!(trip.at, 1_234);

        // Simulate a restart: a fresh breaker over the same store still sees it.
        let breaker2 = EmergencyBreaker::new(&store, &clock);
        assert!(matches!(
            breaker2.breaker_gate(9),
            BreakerGate::Tripped { .. }
        ));
    }

    #[test]
    fn observe_none_does_not_trip() {
        let store = SqliteBreakerStore::open_in_memory().unwrap();
        let clock = VirtualClock::new(0);
        let breaker = EmergencyBreaker::new(&store, &clock);
        assert_eq!(breaker.observe(&no_trip_obs(1)).unwrap(), None);
        assert!(store.load().unwrap().is_empty());
    }

    #[test]
    fn observe_writes_redacted_journal_record() {
        let store = SqliteBreakerStore::open_in_memory().unwrap();
        let journal = crate::logging::SqliteHistoryStore::open_in_memory().unwrap();
        let clock = VirtualClock::new(50);
        let breaker = EmergencyBreaker::with_journal(&store, &clock, &journal);

        let mut obs = no_trip_obs(4);
        obs.recent_outcomes = vec![StageStatus::Failed; 20];
        breaker.observe(&obs).unwrap().expect("should trip");

        let recent = journal.recent(10).unwrap();
        assert_eq!(recent.len(), 1);
        assert_eq!(recent[0].result_code, "breaker_trip_error_spike");
        assert_eq!(recent[0].status, StageStatus::Failed);
        // The trace id is the provenance hash, never the raw scope key.
        assert_ne!(recent[0].trace_id, "breaker:all");
    }

    #[test]
    fn release_clears_trip() {
        let store = SqliteBreakerStore::open_in_memory().unwrap();
        let clock = VirtualClock::new(0);
        let breaker = EmergencyBreaker::new(&store, &clock);
        store
            .trip(&TripState {
                reason: BreakReason::BotLoop,
                scope: BreakScope::Room(3),
                at: 1,
            })
            .unwrap();
        assert!(matches!(
            breaker.breaker_gate(3),
            BreakerGate::Tripped { .. }
        ));
        breaker.release(BreakScope::Room(3)).unwrap();
        assert_eq!(breaker.breaker_gate(3), BreakerGate::Open);
    }
}
