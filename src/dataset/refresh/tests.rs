//! Unit tests for the incremental dataset refresher (task 5).

use std::cell::{Cell, RefCell};

use rusqlite::Connection;

use super::*;
use crate::context::{Embedder, LocalHashEmbedder};
use crate::dataset::{DatasetMessage, MessageKind};
use crate::fakes::{ForbiddenNetwork, VirtualClock};
use crate::ports::{
    Clock, MessageCursor, MessageSource, OwnerCandidate, PortError, RoomRef, SendAuthority,
};
use crate::profile::{AccountScope, UserProfile};

// ---------------------------------------------------------------------------
// Test doubles
// ---------------------------------------------------------------------------

/// A non-local embedder used to exercise the local-only refusal (R4.14).
struct NonLocalEmbedder;
impl Embedder for NonLocalEmbedder {
    fn dim(&self) -> usize {
        8
    }
    fn embed(&self, _text: &str) -> Vec<f32> {
        vec![0.0; 8]
    }
    fn is_local_only(&self) -> bool {
        false
    }
}

/// A clock that returns a scripted sequence of times (one per `now_ms` call),
/// repeating the last value once the script is exhausted. Lets a single
/// synchronous `refresh_now` observe elapsed time for the open/commit deadlines.
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

/// A controllable in-memory [`MessageSource`].
struct TestSource {
    rooms: Vec<RoomRef>,
    messages: RefCell<Vec<DatasetMessage>>,
    fail_rooms: bool,
    fail_messages: bool,
}
impl TestSource {
    fn new(messages: Vec<DatasetMessage>) -> Self {
        Self {
            rooms: vec![RoomRef {
                chat_id: 1,
                title: "room-1".into(),
            }],
            messages: RefCell::new(messages),
            fail_rooms: false,
            fail_messages: false,
        }
    }
}
impl MessageSource for TestSource {
    fn rooms(&self) -> Result<Vec<RoomRef>, PortError> {
        if self.fail_rooms {
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
        if self.fail_messages {
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
            owner_display_name: Some("owner".into()),
        })
    }

    fn owner_candidates(&self, _limit: usize) -> Result<Vec<OwnerCandidate>, PortError> {
        Ok(vec![])
    }
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const OWNER: &str = "owner";

fn profile() -> UserProfile {
    UserProfile {
        owner_display_name: OWNER.to_string(),
        account_scope: AccountScope::new("/tmp/openkakao-test-scope"),
    }
}

fn partner(chat_id: i64, at: i64, text: &str) -> DatasetMessage {
    DatasetMessage {
        chat_id,
        at,
        sender: format!("member-{chat_id}"),
        is_owner: false,
        kind: MessageKind::Text,
        text: text.to_string(),
        provenance: format!("test:{chat_id}:{at}"),
    }
}

fn owner_msg(chat_id: i64, at: i64, text: &str) -> DatasetMessage {
    DatasetMessage {
        chat_id,
        at,
        sender: OWNER.to_string(),
        is_owner: true,
        kind: MessageKind::Text,
        text: text.to_string(),
        provenance: format!("test:{chat_id}:{at}"),
    }
}

fn count_qa(conn: &Connection) -> i64 {
    conn.query_row("SELECT COUNT(*) FROM qa_pair", [], |row| row.get(0))
        .unwrap()
}

// ---------------------------------------------------------------------------
// RefreshConfig
// ---------------------------------------------------------------------------

#[test]
fn config_applies_in_range_and_defaults_out_of_range() {
    // In range → applied verbatim.
    assert_eq!(RefreshConfig::new(Some(900)).period_secs, 900);
    assert_eq!(RefreshConfig::new(Some(3_600)).period_secs, 3_600);
    assert_eq!(RefreshConfig::new(Some(86_400)).period_secs, 86_400);
    // Out of range or missing → default 3600 (not clamped to the bound).
    assert_eq!(RefreshConfig::new(Some(899)).period_secs, DEFAULT_PERIOD_SECS);
    assert_eq!(
        RefreshConfig::new(Some(86_401)).period_secs,
        DEFAULT_PERIOD_SECS
    );
    assert_eq!(RefreshConfig::new(Some(0)).period_secs, DEFAULT_PERIOD_SECS);
    assert_eq!(RefreshConfig::new(None).period_secs, DEFAULT_PERIOD_SECS);
}

#[test]
fn due_at_and_is_due_follow_period() {
    let cfg = RefreshConfig::new(Some(900));
    assert_eq!(DatasetRefresher::due_at(1_000, &cfg), 1_000 + 900_000);
    assert!(!DatasetRefresher::is_due(1_000 + 899_999, 1_000, &cfg));
    assert!(DatasetRefresher::is_due(1_000 + 900_000, 1_000, &cfg));
}

// ---------------------------------------------------------------------------
// RefreshLease
// ---------------------------------------------------------------------------

#[test]
fn lease_admits_one_running_and_one_pending() {
    let lease = RefreshLease::new();
    assert_eq!(lease.acquire(), LeaseGrant::Granted);
    assert!(lease.running());
    assert_eq!(lease.queued(), 0);

    assert_eq!(lease.acquire(), LeaseGrant::Queued);
    assert_eq!(lease.queued(), 1);

    assert_eq!(
        lease.acquire(),
        LeaseGrant::Rejected {
            running: true,
            queued: 1
        }
    );

    // Finishing the running one promotes the pending one; a slot frees up.
    lease.finish();
    assert!(lease.running());
    assert_eq!(lease.queued(), 0);
    lease.finish();
    assert!(!lease.running());
}

// ---------------------------------------------------------------------------
// refresh_now — skip paths
// ---------------------------------------------------------------------------

#[test]
fn refresh_refuses_non_local_embedder() {
    let source = TestSource::new(vec![]);
    let embedder = NonLocalEmbedder;
    let clock = VirtualClock::new(1_000);
    let net = ForbiddenNetwork::new();
    let profile = profile();
    let mut refresher = DatasetRefresher::new(&source, &embedder, &clock, &net, &profile);
    let mut conn = Connection::open_in_memory().unwrap();

    assert_eq!(
        refresher.refresh_now(&mut conn),
        RefreshOutcome::Skipped(SkipReason::NoLocalEmbedder)
    );
}

#[test]
fn refresh_skips_when_kakaotalk_not_running() {
    let mut source = TestSource::new(vec![]);
    source.fail_rooms = true;
    let embedder = LocalHashEmbedder;
    let clock = VirtualClock::new(1_000);
    let net = ForbiddenNetwork::new();
    let profile = profile();
    let mut refresher = DatasetRefresher::new(&source, &embedder, &clock, &net, &profile);
    let mut conn = Connection::open_in_memory().unwrap();

    assert_eq!(
        refresher.refresh_now(&mut conn),
        RefreshOutcome::Skipped(SkipReason::KakaoTalkNotRunning)
    );
}

#[test]
fn refresh_skips_on_slow_db_open() {
    let source = TestSource::new(vec![partner(1, 1, "hi"), owner_msg(1, 2, "hello")]);
    let embedder = LocalHashEmbedder;
    // open_start = 0, open_done = 20s → over the 10s open budget.
    let clock = ScriptedClock::new(vec![0, 20_000]);
    let net = ForbiddenNetwork::new();
    let profile = profile();
    let mut refresher = DatasetRefresher::new(&source, &embedder, &clock, &net, &profile);
    let mut conn = Connection::open_in_memory().unwrap();

    assert_eq!(
        refresher.refresh_now(&mut conn),
        RefreshOutcome::Skipped(SkipReason::DbOpenTimeout)
    );
    // Nothing was written.
    assert_eq!(count_qa(&conn), 0);
    assert!(load_cursor(&conn).is_none());
}

// ---------------------------------------------------------------------------
// refresh_now — failure paths
// ---------------------------------------------------------------------------

#[test]
fn refresh_reports_read_failure_and_preserves_state() {
    let mut source = TestSource::new(vec![]);
    source.fail_messages = true;
    let embedder = LocalHashEmbedder;
    let clock = VirtualClock::new(1_000);
    let net = ForbiddenNetwork::new();
    let profile = profile();
    let mut refresher = DatasetRefresher::new(&source, &embedder, &clock, &net, &profile);
    let mut conn = Connection::open_in_memory().unwrap();

    match refresher.refresh_now(&mut conn) {
        RefreshOutcome::Failed(f) => {
            assert_eq!(f.stage, RefreshStage::Read);
            assert!(!f.next_step.is_empty());
        }
        other => panic!("expected Failed(Read), got {other:?}"),
    }
    assert_eq!(count_qa(&conn), 0);
    assert!(load_cursor(&conn).is_none());
}

#[test]
fn refresh_fails_on_deadline_and_preserves_state() {
    let source = TestSource::new(vec![partner(1, 1, "q"), owner_msg(1, 2, "a")]);
    let embedder = LocalHashEmbedder;
    // open ok (0 → 5ms), pre-commit far past the 600s commit deadline.
    let clock = ScriptedClock::new(vec![0, 5, 700_000]);
    let net = ForbiddenNetwork::new();
    let profile = profile();
    let mut refresher = DatasetRefresher::new(&source, &embedder, &clock, &net, &profile);
    let mut conn = Connection::open_in_memory().unwrap();

    match refresher.refresh_now(&mut conn) {
        RefreshOutcome::Failed(f) => assert_eq!(f.stage, RefreshStage::Deadline),
        other => panic!("expected Failed(Deadline), got {other:?}"),
    }
    // Deadline overrun commits nothing (R4.15).
    assert_eq!(count_qa(&conn), 0);
    assert!(load_cursor(&conn).is_none());
}

// ---------------------------------------------------------------------------
// refresh_now — commit paths
// ---------------------------------------------------------------------------

#[test]
fn full_scan_processes_all_and_advances_cursor_together() {
    let source = TestSource::new(vec![
        partner(1, 1, "q1"),
        owner_msg(1, 2, "a1"),
        partner(1, 3, "q2"),
        owner_msg(1, 4, "a2"),
    ]);
    let embedder = LocalHashEmbedder;
    let clock = VirtualClock::new(5_000);
    let net = ForbiddenNetwork::new();
    let profile = profile();
    let mut refresher = DatasetRefresher::new(&source, &embedder, &clock, &net, &profile);
    let mut conn = Connection::open_in_memory().unwrap();

    match refresher.refresh_now(&mut conn) {
        RefreshOutcome::Committed(report) => {
            assert!(report.full_scan); // no cursor yet (R4.16)
            assert_eq!(report.qa_pairs_added, 2);
            assert_eq!(report.messages_processed, 4);
        }
        other => panic!("expected Committed, got {other:?}"),
    }
    // Dataset rows and the advanced cursor are visible together (R4.4, R4.9).
    assert_eq!(count_qa(&conn), 2);
    let cursor = load_cursor(&conn).expect("cursor advanced");
    assert_eq!(cursor.last_message_at, 4);
    assert_eq!(cursor.last_success_at, 5_000);
}

#[test]
fn incremental_refresh_processes_only_new_messages_without_duplicates() {
    let source = TestSource::new(vec![
        partner(1, 1, "q1"),
        owner_msg(1, 2, "a1"),
        partner(1, 3, "q2"),
        owner_msg(1, 4, "a2"),
    ]);
    let embedder = LocalHashEmbedder;
    let clock = VirtualClock::new(1_000);
    let net = ForbiddenNetwork::new();
    let profile = profile();
    let mut refresher = DatasetRefresher::new(&source, &embedder, &clock, &net, &profile);
    let mut conn = Connection::open_in_memory().unwrap();

    // First refresh: full scan of 4 messages → 2 pairs.
    let first = refresher.refresh_now(&mut conn);
    assert!(matches!(first, RefreshOutcome::Committed(_)));
    assert_eq!(count_qa(&conn), 2);

    // New conversation arrives after the cursor.
    source
        .messages
        .borrow_mut()
        .extend(vec![partner(1, 5, "q3"), owner_msg(1, 6, "a3")]);

    // Second refresh is incremental (fingerprint matches) and adds only 1 pair.
    match refresher.refresh_now(&mut conn) {
        RefreshOutcome::Committed(report) => {
            assert!(!report.full_scan);
            assert_eq!(report.messages_processed, 2);
            assert_eq!(report.qa_pairs_added, 1);
        }
        other => panic!("expected Committed, got {other:?}"),
    }
    // No re-processing: total is the two original pairs plus one new pair.
    assert_eq!(count_qa(&conn), 3);
    assert_eq!(load_cursor(&conn).unwrap().last_message_at, 6);
}

#[test]
fn zero_target_messages_reports_all_zeros_and_still_commits() {
    let source = TestSource::new(vec![]);
    let embedder = LocalHashEmbedder;
    let clock = VirtualClock::new(2_000);
    let net = ForbiddenNetwork::new();
    let profile = profile();
    let mut refresher = DatasetRefresher::new(&source, &embedder, &clock, &net, &profile);
    let mut conn = Connection::open_in_memory().unwrap();

    match refresher.refresh_now(&mut conn) {
        RefreshOutcome::Committed(report) => {
            assert_eq!(report.qa_pairs_added, 0);
            assert_eq!(report.attachments_added, 0);
            assert_eq!(report.styles_updated, 0);
            assert_eq!(report.messages_processed, 0);
            assert_eq!(report.duration_ms, 0);
        }
        other => panic!("expected Committed, got {other:?}"),
    }
    // A successful empty refresh still records its completion time.
    assert_eq!(load_cursor(&conn).unwrap().last_success_at, 2_000);
}

#[test]
fn owner_message_is_excluded_from_reply_targets_via_profile() {
    // The answering message carries the owner display name but the source's
    // is_owner flag is false. Profile re-derivation (R4.12) must still treat it
    // as the owner, so the partner question becomes an answered pair rather than
    // an unanswered (context-only) question.
    let mut answer = partner(1, 2, "answer text");
    answer.sender = OWNER.to_string();
    answer.is_owner = false;
    let source = TestSource::new(vec![partner(1, 1, "question"), answer]);

    let embedder = LocalHashEmbedder;
    let clock = VirtualClock::new(1_000);
    let net = ForbiddenNetwork::new();
    let profile = profile();
    let mut refresher = DatasetRefresher::new(&source, &embedder, &clock, &net, &profile);
    let mut conn = Connection::open_in_memory().unwrap();

    match refresher.refresh_now(&mut conn) {
        RefreshOutcome::Committed(report) => assert_eq!(report.qa_pairs_added, 1),
        other => panic!("expected Committed, got {other:?}"),
    }
    assert_eq!(count_qa(&conn), 1);
}

#[test]
fn refresh_performs_no_network_egress() {
    let source = TestSource::new(vec![partner(1, 1, "q"), owner_msg(1, 2, "a")]);
    let embedder = LocalHashEmbedder;
    let clock = VirtualClock::new(1_000);
    let net = ForbiddenNetwork::new();
    let profile = profile();
    let mut refresher = DatasetRefresher::new(&source, &embedder, &clock, &net, &profile);
    let mut conn = Connection::open_in_memory().unwrap();

    let _ = refresher.refresh_now(&mut conn);
    // The whole refresh stayed on-device (R4.11).
    assert_eq!(refresher.net_egress(), 0);
}
