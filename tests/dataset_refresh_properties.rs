//! Property-based tests for the incremental dataset refresher (task 5.1).
//!
//! Feature: kakao-agent-live-ops
//! Property 13: 증분 갱신의 원자성·소진성·마감
//!   (incremental refresh atomicity, exhaustiveness, and deadline)
//! Property 14: 갱신 주기 클램프와 동시성 한도
//!   (refresh-period clamp and concurrency limit)
//!
//! Property 13 drives [`DatasetRefresher::refresh_now`] over arbitrary message
//! streams and arbitrary cursor states and pins:
//!   * every message a refresh processes lies strictly after the prior cursor
//!     and the processed count never exceeds [`MAX_MESSAGES_PER_REFRESH`]
//!     (R4.3),
//!   * repeated refreshes exhaust the backlog with zero re-processing (each
//!     message is consumed exactly once) (R4.3, R4.4),
//!   * a committed refresh advances the cursor and appends the dataset rows in
//!     the same transaction, so the cursor and the dataset become visible
//!     together (R4.4, R4.9),
//!   * a missing or unfindable/unparseable cursor processes the full range
//!     (R4.16), and
//!   * a failure past the 10-minute commit deadline (and any read failure)
//!     leaves the cursor **and** the dataset at their pre-refresh values —
//!     nothing is committed (R4.15, and R4.6 for the read stage).
//!
//! Property 14 pins the period clamp — any configured value resolves to
//! `900..=86_400` seconds or exactly the 3600-second default (R4.2, exercised
//! here as supporting context for the concurrency guarantees) — and the
//! [`RefreshLease`] admission contract: at most one running refresh plus at
//! most one pending, with every further request refused while showing the
//! running/queued state (R4.8). It also pins that a DB-open failure (KakaoTalk
//! not running, or an open that overruns [`DB_OPEN_TIMEOUT_SECS`]) changes no
//! state and is retriable on the next cycle (R4.7).
//!
//! Every refresh path asserts `ForbiddenNetwork::egress_count() == 0`: the whole
//! refresh, from the local-DB read to the commit, stays on-device (R1.12, R4.11,
//! R12.9). The refresher never sends, so there is no send port on these paths.
//!
//! Validates: Requirements 1.12, 4.2, 4.3, 4.4, 4.5, 4.7, 4.8, 4.9, 4.11, 4.14,
//! 4.15, 4.16, 12.9

use std::cell::{Cell, RefCell};

use openkakao_cli::context::LocalHashEmbedder;
use openkakao_cli::dataset::refresh::{
    DB_OPEN_TIMEOUT_SECS, DEFAULT_PERIOD_SECS, MAX_MESSAGES_PER_REFRESH, MAX_PERIOD_SECS,
    MIN_PERIOD_SECS,
};
use openkakao_cli::dataset::{
    DatasetMessage, DatasetRefresher, LeaseGrant, MessageKind, RefreshConfig, RefreshLease,
    RefreshOutcome, RefreshStage, SkipReason,
};
use openkakao_cli::fakes::{ForbiddenNetwork, VirtualClock};
use openkakao_cli::ports::{
    Clock, MessageCursor, MessageSource, NetworkPort, OwnerCandidate, PortError, RoomRef,
    SendAuthority,
};
use openkakao_cli::profile::{AccountScope, UserProfile};
use proptest::prelude::*;
use rusqlite::Connection;

const OWNER: &str = "owner";

// ---------------------------------------------------------------------------
// Test doubles
// ---------------------------------------------------------------------------

/// A clock returning a scripted sequence of times (one per `now_ms` call),
/// repeating the last value once exhausted. Lets a single synchronous
/// `refresh_now` observe elapsed time for the open/commit deadlines.
struct ScriptedClock {
    seq: Vec<i64>,
    idx: Cell<usize>,
}
impl ScriptedClock {
    fn new(seq: Vec<i64>) -> Self {
        Self {
            seq,
            idx: Cell::new(0),
        }
    }
}
impl Clock for ScriptedClock {
    fn now_ms(&self) -> i64 {
        let i = self.idx.get();
        self.idx.set(i + 1);
        if i < self.seq.len() {
            self.seq[i]
        } else {
            *self.seq.last().unwrap_or(&0)
        }
    }
}

/// A controllable in-memory [`MessageSource`] with a fixed room set (so the
/// source fingerprint stays stable across incremental refreshes) plus optional
/// failure injection.
struct TestSource {
    rooms: Vec<RoomRef>,
    messages: RefCell<Vec<DatasetMessage>>,
    fail_rooms: Cell<bool>,
    fail_messages: Cell<bool>,
}
impl TestSource {
    fn new(messages: Vec<DatasetMessage>) -> Self {
        Self {
            rooms: vec![RoomRef {
                chat_id: 1,
                title: "room-1".into(),
            }],
            messages: RefCell::new(messages),
            fail_rooms: Cell::new(false),
            fail_messages: Cell::new(false),
        }
    }
    fn extend(&self, more: Vec<DatasetMessage>) {
        self.messages.borrow_mut().extend(more);
    }
}
impl MessageSource for TestSource {
    fn rooms(&self) -> Result<Vec<RoomRef>, PortError> {
        if self.fail_rooms.get() {
            Err(PortError::Backend("kakaotalk not running".into()))
        } else {
            Ok(self.rooms.clone())
        }
    }

    fn messages_after(
        &self,
        after: Option<&MessageCursor>,
        limit: usize,
    ) -> Result<Vec<DatasetMessage>, PortError> {
        if self.fail_messages.get() {
            return Err(PortError::Backend("read failed".into()));
        }
        let mut all = self.messages.borrow().clone();
        all.sort_by(|a, b| a.at.cmp(&b.at).then(a.chat_id.cmp(&b.chat_id)));
        Ok(all
            .into_iter()
            .filter(|m| match after {
                Some(c) => m.at > c.at,
                None => true,
            })
            .take(limit)
            .collect())
    }

    fn authority(&self, chat_id: i64) -> Result<SendAuthority, PortError> {
        Ok(SendAuthority {
            chat_id,
            chat_type: 1,
            is_memo: false,
            owner_display_name: Some(OWNER.into()),
        })
    }

    fn owner_candidates(&self, _limit: usize) -> Result<Vec<OwnerCandidate>, PortError> {
        Ok(vec![])
    }
}

fn profile() -> UserProfile {
    UserProfile {
        owner_display_name: OWNER.to_string(),
        account_scope: AccountScope::new("/tmp/openkakao-refresh-scope"),
    }
}

// ---------------------------------------------------------------------------
// Cursor / dataset visibility helpers (read the committed state directly).
// ---------------------------------------------------------------------------

/// The committed cursor position `(last_message_at)`, or `None` when no cursor
/// row is visible. Reading the table directly is how the test observes that the
/// cursor became visible together with the dataset rows.
fn cursor_at(conn: &Connection) -> Option<i64> {
    conn.query_row(
        "SELECT last_message_at FROM refresh_cursor WHERE id = 1",
        [],
        |row| row.get::<_, i64>(0),
    )
    .ok()
}

/// The committed Q&A-pair count, or 0 when the table is absent.
fn qa_count(conn: &Connection) -> i64 {
    conn.query_row("SELECT COUNT(*) FROM qa_pair", [], |row| row.get(0))
        .unwrap_or(0)
}

// ---------------------------------------------------------------------------
// Message strategy
// ---------------------------------------------------------------------------

/// One message spec: whether it is owner-sent and which canned body it carries.
type MsgSpec = (bool, u8);

/// A canned body for a spec index. Index 2 carries a URL so the refresh also
/// exercises attachment extraction.
fn body_for(idx: u8) -> (MessageKind, &'static str) {
    match idx % 4 {
        0 => (MessageKind::Text, "안녕 오늘 회의 몇 시야"),
        1 => (MessageKind::Text, "확인 부탁해"),
        2 => (MessageKind::Text, "이거 봐 https://github.com/foo/bar"),
        _ => (MessageKind::Image, ""),
    }
}

/// Turn a spec into a [`DatasetMessage`] on chat 1 at time `at`.
fn make_message(at: i64, spec: MsgSpec) -> DatasetMessage {
    let (is_owner, body_idx) = spec;
    let (kind, text) = body_for(body_idx);
    DatasetMessage {
        chat_id: 1,
        at,
        sender: if is_owner {
            OWNER.to_string()
        } else {
            "member-1".to_string()
        },
        is_owner,
        kind,
        text: text.to_string(),
        provenance: format!("test:1:{at}"),
    }
}

/// Batches of specs; each batch models one "arrival" processed by one refresh.
fn batches_strategy() -> impl Strategy<Value = Vec<Vec<MsgSpec>>> {
    prop::collection::vec(
        prop::collection::vec((any::<bool>(), 0u8..4), 0..8),
        1..6,
    )
}

/// A flat message list from a batch layout, with globally increasing `at`.
fn flatten(batches: &[Vec<MsgSpec>]) -> Vec<Vec<DatasetMessage>> {
    let mut at = 0i64;
    batches
        .iter()
        .map(|batch| {
            batch
                .iter()
                .map(|spec| {
                    at += 1;
                    make_message(at, *spec)
                })
                .collect()
        })
        .collect()
}

// ===========================================================================
// Property 13 — atomicity, exhaustiveness, deadline.
// ===========================================================================

proptest! {
    #![proptest_config(ProptestConfig::with_cases(120))]

    /// Repeated refreshes over incrementally arriving messages exhaust the
    /// backlog with zero re-processing: each refresh processes only messages
    /// strictly after the prior cursor, the processed count never exceeds the
    /// per-refresh cap, the total processed equals the total that arrived, and a
    /// final refresh over an exhausted stream processes nothing and adds no
    /// duplicate rows. Every committed refresh makes the cursor and dataset
    /// visible together. No network egress occurs.
    ///
    /// Validates: Requirements 4.3, 4.4, 4.9, 1.12, 12.9
    #[test]
    fn repeated_refreshes_exhaust_without_duplicates(batches in batches_strategy()) {
        let arrivals = flatten(&batches);
        let total: usize = arrivals.iter().map(|b| b.len()).sum();

        let source = TestSource::new(Vec::new());
        let embedder = LocalHashEmbedder::new();
        let clock = VirtualClock::new(1_000);
        let net = ForbiddenNetwork::new();
        let profile = profile();
        let mut refresher = DatasetRefresher::new(&source, &embedder, &clock, &net, &profile);
        let mut conn = Connection::open_in_memory().unwrap();

        let mut prior_cursor: Option<i64> = None;
        let mut processed_total = 0usize;
        let mut first = true;

        for arrival in &arrivals {
            let cursor_before = prior_cursor;
            source.extend(arrival.clone());

            match refresher.refresh_now(&mut conn) {
                RefreshOutcome::Committed(report) => {
                    // Per-refresh cap holds (R4.3).
                    prop_assert!(report.messages_processed <= MAX_MESSAGES_PER_REFRESH);

                    // The very first refresh has no cursor → full range (R4.16).
                    if first {
                        prop_assert!(report.full_scan);
                    } else {
                        prop_assert!(!report.full_scan);
                    }

                    // A refresh processes exactly the messages strictly after
                    // the prior cursor — i.e. only the just-arrived batch, since
                    // earlier batches were already consumed (R4.3, R4.4).
                    let expected = match cursor_before {
                        Some(c) => arrival.iter().filter(|m| m.at > c).count(),
                        None => arrival.len(),
                    };
                    prop_assert_eq!(report.messages_processed, expected);

                    processed_total += report.messages_processed;

                    // The cursor advanced and is visible alongside the dataset
                    // rows committed in the same transaction (R4.4, R4.9).
                    let now = cursor_at(&conn);
                    if let Some(max_at) = arrival.iter().map(|m| m.at).max() {
                        prop_assert_eq!(now, Some(max_at));
                    } else {
                        // An empty arrival still commits a cursor row, keeping
                        // the prior position (0 when there was no prior cursor).
                        prop_assert_eq!(now, Some(cursor_before.unwrap_or(0)));
                    }
                    prior_cursor = now;
                }
                other => prop_assert!(false, "expected Committed, got {other:?}"),
            }
            first = false;
            // The refresh never left the device.
            prop_assert_eq!(net.egress_count(), 0);
        }

        // Every arrived message was consumed exactly once (exhaustive, no
        // duplicates at the message level).
        prop_assert_eq!(processed_total, total);

        // A further refresh over the exhausted stream processes nothing and
        // adds no rows (no re-processing / no duplicate pairs).
        let qa_before_extra = qa_count(&conn);
        let cursor_before_extra = cursor_at(&conn);
        match refresher.refresh_now(&mut conn) {
            RefreshOutcome::Committed(report) => {
                prop_assert_eq!(report.messages_processed, 0);
                prop_assert!(!report.full_scan);
            }
            other => prop_assert!(false, "expected Committed(empty), got {other:?}"),
        }
        prop_assert_eq!(qa_count(&conn), qa_before_extra);
        prop_assert_eq!(cursor_at(&conn), cursor_before_extra);
        prop_assert_eq!(net.egress_count(), 0);
    }

    /// A deadline overrun after a prior successful refresh fails without
    /// committing, so the cursor and dataset stay at their pre-refresh values —
    /// the two states move together or not at all (R4.15). No egress occurs.
    ///
    /// Validates: Requirements 4.15, 4.9, 1.12, 12.9
    #[test]
    fn deadline_overrun_preserves_prior_state(
        batch in prop::collection::vec((any::<bool>(), 0u8..4), 1..8),
        more in prop::collection::vec((any::<bool>(), 0u8..4), 1..8),
    ) {
        let arrivals = flatten(&[batch]);
        let source = TestSource::new(arrivals.into_iter().flatten().collect());
        let embedder = LocalHashEmbedder::new();
        let net = ForbiddenNetwork::new();
        let profile = profile();
        let mut conn = Connection::open_in_memory().unwrap();

        // First refresh commits: capture the resulting cursor + dataset.
        {
            let clock = VirtualClock::new(5_000);
            let mut refresher =
                DatasetRefresher::new(&source, &embedder, &clock, &net, &profile);
            prop_assert!(matches!(
                refresher.refresh_now(&mut conn),
                RefreshOutcome::Committed(_)
            ));
        }
        let cursor_snapshot = cursor_at(&conn);
        let qa_snapshot = qa_count(&conn);
        prop_assert!(cursor_snapshot.is_some());

        // New messages arrive, but the next refresh overruns the commit deadline
        // (open ok at 0→5ms, pre-commit far past 600s).
        let next_at = cursor_snapshot.unwrap();
        let extra: Vec<DatasetMessage> = more
            .iter()
            .enumerate()
            .map(|(i, spec)| make_message(next_at + 1 + i as i64, *spec))
            .collect();
        source.extend(extra);

        {
            let clock = ScriptedClock::new(vec![0, 5, 700_000]);
            let mut refresher =
                DatasetRefresher::new(&source, &embedder, &clock, &net, &profile);
            match refresher.refresh_now(&mut conn) {
                RefreshOutcome::Failed(f) => prop_assert_eq!(f.stage, RefreshStage::Deadline),
                other => prop_assert!(false, "expected Failed(Deadline), got {other:?}"),
            }
        }

        // Both states are exactly what they were before the failed refresh.
        prop_assert_eq!(cursor_at(&conn), cursor_snapshot);
        prop_assert_eq!(qa_count(&conn), qa_snapshot);
        prop_assert_eq!(net.egress_count(), 0);
    }

    /// A read-stage failure after a prior successful refresh leaves the cursor
    /// and dataset at their pre-refresh values (R4.6), and no egress occurs.
    ///
    /// Validates: Requirements 4.6, 4.9, 1.12, 12.9
    #[test]
    fn read_failure_preserves_prior_state(
        batch in prop::collection::vec((any::<bool>(), 0u8..4), 1..8),
    ) {
        let arrivals = flatten(&[batch]);
        let source = TestSource::new(arrivals.into_iter().flatten().collect());
        let embedder = LocalHashEmbedder::new();
        let clock = VirtualClock::new(1_000);
        let net = ForbiddenNetwork::new();
        let profile = profile();
        let mut refresher = DatasetRefresher::new(&source, &embedder, &clock, &net, &profile);
        let mut conn = Connection::open_in_memory().unwrap();

        prop_assert!(matches!(
            refresher.refresh_now(&mut conn),
            RefreshOutcome::Committed(_)
        ));
        let cursor_snapshot = cursor_at(&conn);
        let qa_snapshot = qa_count(&conn);

        // The next read fails; nothing must change.
        source.fail_messages.set(true);
        match refresher.refresh_now(&mut conn) {
            RefreshOutcome::Failed(f) => {
                prop_assert_eq!(f.stage, RefreshStage::Read);
                prop_assert!(!f.next_step.is_empty());
            }
            other => prop_assert!(false, "expected Failed(Read), got {other:?}"),
        }
        prop_assert_eq!(cursor_at(&conn), cursor_snapshot);
        prop_assert_eq!(qa_count(&conn), qa_snapshot);
        prop_assert_eq!(net.egress_count(), 0);
    }

    /// A cursor whose point can no longer be found in the source (its
    /// fingerprint no longer matches) processes the full range (R4.16). No
    /// egress occurs.
    ///
    /// Validates: Requirements 4.16, 1.12, 12.9
    #[test]
    fn unfindable_cursor_processes_full_range(
        batch in prop::collection::vec((any::<bool>(), 0u8..4), 1..8),
    ) {
        let arrivals = flatten(&[batch]);
        let messages: Vec<DatasetMessage> = arrivals.into_iter().flatten().collect();
        let total = messages.len();
        let source = TestSource::new(messages);
        let embedder = LocalHashEmbedder::new();
        let clock = VirtualClock::new(1_000);
        let net = ForbiddenNetwork::new();
        let profile = profile();
        let mut refresher = DatasetRefresher::new(&source, &embedder, &clock, &net, &profile);
        let mut conn = Connection::open_in_memory().unwrap();

        // First refresh commits a cursor.
        prop_assert!(matches!(
            refresher.refresh_now(&mut conn),
            RefreshOutcome::Committed(_)
        ));

        // Corrupt the stored fingerprint so the cursor point is "unfindable".
        conn.execute(
            "UPDATE refresh_cursor SET source_fingerprint = 'bogus-fingerprint' WHERE id = 1",
            [],
        )
        .unwrap();

        // The next refresh must fall back to the full range (R4.16).
        match refresher.refresh_now(&mut conn) {
            RefreshOutcome::Committed(report) => {
                prop_assert!(report.full_scan);
                prop_assert_eq!(report.messages_processed, total);
            }
            other => prop_assert!(false, "expected Committed(full_scan), got {other:?}"),
        }
        prop_assert_eq!(net.egress_count(), 0);
    }
}

// ===========================================================================
// Property 14 — period clamp and concurrency limit.
// ===========================================================================

/// One lease operation applied to both the real lease and a reference model.
#[derive(Debug, Clone, Copy)]
enum LeaseOp {
    Acquire,
    Finish,
}

fn lease_op_strategy() -> impl Strategy<Value = LeaseOp> {
    prop_oneof![Just(LeaseOp::Acquire), Just(LeaseOp::Finish)]
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// The applied period is always in `900..=86_400` seconds or exactly the
    /// 3600-second default: an in-range value is applied verbatim, and anything
    /// else (including `None`) falls back to the default (R4.2).
    ///
    /// Validates: Requirements 4.2
    #[test]
    fn period_clamps_to_range_or_default(raw in prop::option::of(0u64..200_000)) {
        let cfg = RefreshConfig::new(raw);
        let p = cfg.period_secs;

        // Always in range or exactly the default.
        prop_assert!((MIN_PERIOD_SECS..=MAX_PERIOD_SECS).contains(&p) || p == DEFAULT_PERIOD_SECS);

        match raw {
            Some(v) if (MIN_PERIOD_SECS..=MAX_PERIOD_SECS).contains(&v) => {
                // In range → applied verbatim (never silently clamped).
                prop_assert_eq!(p, v);
            }
            _ => {
                // Missing or out of range → the default, not a clamped bound.
                prop_assert_eq!(p, DEFAULT_PERIOD_SECS);
            }
        }
    }

    /// Under any sequence of acquire/finish requests the lease keeps at most one
    /// running refresh and at most one pending, refusing every further request
    /// while surfacing the running/queued state (R4.8).
    ///
    /// Validates: Requirements 4.8
    #[test]
    fn lease_admits_at_most_one_running_and_one_pending(
        ops in prop::collection::vec(lease_op_strategy(), 0..40),
    ) {
        let lease = RefreshLease::new();
        // Reference model: 0 idle, 1 running, 2 running+pending.
        let mut model: u8 = 0;

        for op in ops {
            match op {
                LeaseOp::Acquire => {
                    let grant = lease.acquire();
                    let expected = match model {
                        0 => LeaseGrant::Granted,
                        1 => LeaseGrant::Queued,
                        _ => LeaseGrant::Rejected { running: true, queued: 1 },
                    };
                    prop_assert_eq!(grant, expected);
                    if model < 2 {
                        model += 1;
                    }
                }
                LeaseOp::Finish => {
                    lease.finish();
                    model = model.saturating_sub(1);
                }
            }

            // Invariants: never more than one running + one pending.
            prop_assert_eq!(lease.running(), model >= 1);
            prop_assert_eq!(lease.queued(), model.saturating_sub(1));
            prop_assert!(lease.queued() <= 1);
        }
    }

    /// A DB-open failure — KakaoTalk not running, or an open that overruns the
    /// 10-second budget — skips the cycle without changing the cursor or
    /// dataset, and is retriable once the source recovers. No egress occurs.
    ///
    /// Validates: Requirements 4.7, 4.14, 1.12, 12.9
    #[test]
    fn db_open_failure_changes_nothing_and_is_retriable(
        batch in prop::collection::vec((any::<bool>(), 0u8..4), 1..8),
        timeout_variant in any::<bool>(),
    ) {
        let arrivals = flatten(&[batch]);
        let messages: Vec<DatasetMessage> = arrivals.into_iter().flatten().collect();
        let source = TestSource::new(messages);
        let embedder = LocalHashEmbedder::new();
        let net = ForbiddenNetwork::new();
        let profile = profile();
        let mut conn = Connection::open_in_memory().unwrap();

        // Trigger the chosen open failure.
        if timeout_variant {
            // Open probe overruns the 10s budget (0 → 20s).
            let clock = ScriptedClock::new(vec![0, (DB_OPEN_TIMEOUT_SECS as i64 + 10) * 1_000]);
            let mut refresher =
                DatasetRefresher::new(&source, &embedder, &clock, &net, &profile);
            prop_assert_eq!(
                refresher.refresh_now(&mut conn),
                RefreshOutcome::Skipped(SkipReason::DbOpenTimeout)
            );
        } else {
            source.fail_rooms.set(true);
            let clock = VirtualClock::new(1_000);
            let mut refresher =
                DatasetRefresher::new(&source, &embedder, &clock, &net, &profile);
            prop_assert_eq!(
                refresher.refresh_now(&mut conn),
                RefreshOutcome::Skipped(SkipReason::KakaoTalkNotRunning)
            );
        }

        // Nothing was written on a skip and no egress happened.
        prop_assert_eq!(qa_count(&conn), 0);
        prop_assert_eq!(cursor_at(&conn), None);
        prop_assert_eq!(net.egress_count(), 0);

        // Retriable: with the source healthy and time within budget, the next
        // cycle commits.
        source.fail_rooms.set(false);
        let clock = VirtualClock::new(2_000);
        let mut refresher = DatasetRefresher::new(&source, &embedder, &clock, &net, &profile);
        prop_assert!(matches!(
            refresher.refresh_now(&mut conn),
            RefreshOutcome::Committed(_)
        ));
        prop_assert_eq!(net.egress_count(), 0);
    }
}
