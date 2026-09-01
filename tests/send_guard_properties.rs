//! Property-based tests for the [`SendGuard`] precondition layer.
//!
//! Feature: kakao-agent-live-ops
//! Property 33 (SendGuard perspective): the owner-name and account-scope
//! preconditions gate ticket issuance.
//!
//! `SendGuard::authorize` mints a [`SendTicket`] only after every precondition
//! passes, in a fixed order: owner name → account scope → breaker → grade →
//! pacing → the unchanged safety gate. This test pins the first two links of
//! that chain: over arbitrary owner-name strings and scope memberships (with
//! every other collaborator permissive and the gate allowing), a ticket is
//! issued exactly when the owner name is set *and* the target is in scope, the
//! owner-name check precedes the scope check, and no real send or network
//! egress ever happens.
//!
//! Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.5, 10.8

use openkakao_cli::fakes::{BlockingRealSendPort, FakeSendPort, ForbiddenNetwork, VirtualClock};
use openkakao_cli::live_sample::{CommitReceipt, LiveSampleCollector, SqliteSampleStore};
use openkakao_cli::logging::{Stage, StageStatus};
use openkakao_cli::ports::{Clock, NetworkPort, SendPort};
use openkakao_cli::profile::{resolve_owner, OwnerName, OWNER_NAME_MAX_CHARS};
use openkakao_cli::safety::{
    BreakerGate, BreakerGateSource, DefaultSafetyGate, GradeLimit, GradePolicy, GuardFence, Origin,
    OwnerNameStatus, PacingSource, ProfileView, SafetyConfig, SendGrade, SendGuard, SendIntent,
    SendRequest, UnsetReason,
};
use proptest::prelude::*;

// ---------------------------------------------------------------------------
// Permissive collaborators: only the profile preconditions can fence here.
// ---------------------------------------------------------------------------

struct OpenBreaker;
impl BreakerGateSource for OpenBreaker {
    fn breaker_gate(&self, _chat_id: i64) -> BreakerGate {
        BreakerGate::Open
    }
}

struct AnyGrade;
impl GradePolicy for AnyGrade {
    fn check(&self, _intent: &SendIntent, _chat_id: i64) -> Result<(), GradeLimit> {
        Ok(())
    }
}

struct ReadyPacing;
impl PacingSource for ReadyPacing {
    fn next_allowed_at(&self) -> i64 {
        i64::MIN
    }
}

struct ZeroClock;
impl Clock for ZeroClock {
    fn now_ms(&self) -> i64 {
        0
    }
}

/// A profile view whose owner-name status comes from the *real*
/// [`resolve_owner`], tying the guard's decision to the actual resolver.
struct TestProfile {
    raw_owner: Option<String>,
    in_scope: bool,
}

impl ProfileView for TestProfile {
    fn owner_name_status(&self) -> OwnerNameStatus {
        match resolve_owner(self.raw_owner.as_deref()) {
            OwnerName::Set(_) => OwnerNameStatus::Set,
            OwnerName::Unset(reason) => OwnerNameStatus::Unset(reason),
        }
    }

    fn in_scope(&self) -> bool {
        self.in_scope
    }
}

/// One intent per product-send flow.
fn origin_by_index(i: usize) -> Origin {
    match i % 4 {
        0 => Origin::PartnerMessage {
            event_id: "evt".into(),
        },
        1 => Origin::GeekNewsSlot {
            marker: "slot".into(),
        },
        2 => Origin::UserComposer,
        _ => Origin::RelaySource {
            message_pid: "pid".into(),
        },
    }
}

/// A config/request pair the unchanged safety gate always allows.
fn allowing_gate_io(chat_id: i64) -> (SafetyConfig, SendRequest) {
    let cfg = SafetyConfig::new(true, true, [chat_id], "fp-owner");
    let req = SendRequest {
        chat_id,
        account_fp: "fp-owner".into(),
        state_ok: true,
    };
    (cfg, req)
}

/// A raw owner-name value covering every branch of [`resolve_owner`].
fn owner_raw_strategy() -> impl Strategy<Value = Option<String>> {
    prop_oneof![
        2 => Just(None),
        2 => Just(Some(String::new())),
        1 => Just(Some("   ".to_string())),
        4 => prop::string::string_regex("[a-zA-Z0-9]{1,20}").unwrap().prop_map(Some),
        2 => prop::string::string_regex("[a-zA-Z0-9]{1,12}")
                .unwrap()
                .prop_map(|s| Some(format!("  {s}  "))),
        2 => (OWNER_NAME_MAX_CHARS + 1..OWNER_NAME_MAX_CHARS + 32)
                .prop_map(|n| Some("a".repeat(n))),
    ]
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// A ticket is issued exactly when the owner name is set and the target is
    /// in scope; otherwise the guard fences with the precise precondition
    /// reason. The owner-name check runs before the scope check, so an unset
    /// name always surfaces as `OwnerNameUnset` regardless of scope. No path
    /// touches the real send port or the network.
    ///
    /// Validates: Requirements 10.1, 10.3, 10.4, 10.5, 10.8
    #[test]
    fn owner_and_scope_preconditions_gate_ticket(
        raw in owner_raw_strategy(),
        in_scope in any::<bool>(),
        origin_idx in 0usize..4,
        chat_id in 1i64..50,
    ) {
        let gate = DefaultSafetyGate;
        let profile = TestProfile { raw_owner: raw.clone(), in_scope };
        let guard = SendGuard {
            gate: &gate,
            breaker: &OpenBreaker,
            grade: &AnyGrade,
            pacing: &ReadyPacing,
            profile: &profile,
            clock: &ZeroClock,
        };
        let (cfg, req) = allowing_gate_io(chat_id);
        let intent = SendIntent {
            grade: SendGrade::Memo,
            origin: origin_by_index(origin_idx),
        };

        let fake_send = FakeSendPort::new();
        let blocked = BlockingRealSendPort::new();
        let net = ForbiddenNetwork::new();

        let owner_status = resolve_owner(raw.as_deref());
        let owner_set = matches!(owner_status, OwnerName::Set(_));

        let result = guard.authorize(&req, &cfg, &intent);
        let issued = result.is_ok();

        match result {
            Ok(ticket) => {
                // A ticket requires both preconditions to pass.
                prop_assert!(owner_set && in_scope);
                prop_assert_eq!(ticket.chat_id(), chat_id);
                let receipt = fake_send.send_text(&ticket, "body").expect("fake send");
                prop_assert_eq!(receipt.chat_id, chat_id);
            }
            Err(GuardFence::OwnerNameUnset(reason)) => {
                // Owner-name is checked first: this reason is reached whenever
                // the name is unset, no matter the scope.
                match owner_status {
                    OwnerName::Unset(expected) => prop_assert_eq!(expected, reason),
                    OwnerName::Set(_) => {
                        prop_assert!(false, "OwnerNameUnset for a set name")
                    }
                }
            }
            Err(GuardFence::ProfileScopeMismatch) => {
                // Scope mismatch is only reachable once the owner name is set.
                prop_assert!(owner_set);
                prop_assert!(!in_scope);
            }
            Err(other) => prop_assert!(false, "unexpected fence: {:?}", other),
        }

        // Ticket issuance is equivalent to both preconditions holding.
        prop_assert_eq!(issued, owner_set && in_scope);

        prop_assert_eq!(blocked.blocked_count(), 0);
        prop_assert_eq!(net.egress_count(), 0);
    }

    /// The owner-name precondition strictly precedes the account-scope
    /// precondition: when both fail, the guard reports the owner-name fence.
    ///
    /// Validates: Requirements 10.5, 10.8
    #[test]
    fn owner_name_precondition_precedes_scope(chat_id in 1i64..50) {
        let gate = DefaultSafetyGate;
        // Owner unset AND out of scope: owner-name must win.
        let profile = TestProfile { raw_owner: None, in_scope: false };
        let guard = SendGuard {
            gate: &gate,
            breaker: &OpenBreaker,
            grade: &AnyGrade,
            pacing: &ReadyPacing,
            profile: &profile,
            clock: &ZeroClock,
        };
        let (cfg, req) = allowing_gate_io(chat_id);
        let intent = SendIntent {
            grade: SendGrade::Memo,
            origin: Origin::PartnerMessage { event_id: "evt".into() },
        };

        prop_assert_eq!(
            guard.authorize(&req, &cfg, &intent),
            Err(GuardFence::OwnerNameUnset(UnsetReason::Missing))
        );
    }
}

// ===========================================================================
// Feature: kakao-agent-live-ops
// Property 10 (SendGuard perspective): 전송 등급·최소 간격 티켓 발급 한도
//
// The send-grade and minimum-interval limits are enforced during ticket
// issuance when the live-sample collector is wired as the guard's
// `GradePolicy` and `PacingSource`. With every other precondition permissive
// (breaker open, owner set, in scope, gate allowing), the collector is the sole
// decider of the grade and pacing steps of `SendGuard::authorize`:
//   * a `memo`-grade send to any chat other than the memo chat is fenced with
//     `GradeLimit(MemoOnly)` (R2.6),
//   * a smoke run past three confirmed sends is fenced with
//     `GradeLimit(SmokeCapReached)` (R2.7), and
//   * a send that arrives before the minimum interval elapses is fenced with
//     `TooSoon` — a rejection, not a delay — and issues a ticket only once the
//     interval has passed (R2.8).
//
// No path ever reaches the real send port or the network.
//
// Validates: Requirements 2.6, 2.7, 2.8, 3.8

/// An owner-set, in-scope profile so only the grade/pacing steps can fence.
struct OwnerSetProfile;
impl ProfileView for OwnerSetProfile {
    fn owner_name_status(&self) -> OwnerNameStatus {
        OwnerNameStatus::Set
    }
    fn in_scope(&self) -> bool {
        true
    }
}

/// A `commit`/`success` receipt for advancing the collector's state.
fn guard_commit_receipt(trace: &str, grade: SendGrade, at: i64) -> CommitReceipt {
    CommitReceipt {
        trace_pid: trace.to_string(),
        room_pid: "room-pid".to_string(),
        grade,
        quality_score: 50,
        latency_ms: 10,
        at,
        stage: Stage::Commit,
        status: StageStatus::Success,
    }
}

/// A partner-message intent carrying `grade`.
fn guard_partner_intent(grade: SendGrade) -> SendIntent {
    SendIntent {
        grade,
        origin: Origin::PartnerMessage {
            event_id: "db:1:2".into(),
        },
    }
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// Through the guard, a `memo`-grade send issues a ticket only for the memo
    /// chat; any other target is fenced with `GradeLimit(MemoOnly)` (R2.6).
    ///
    /// Validates: Requirements 2.6
    #[test]
    fn guard_memo_grade_limited_to_memo_chat(chat_id in 1i64..50) {
        let memo_chat = 10i64;
        let store = SqliteSampleStore::open_in_memory().unwrap();
        let clock = VirtualClock::new(1_000);
        let collector =
            LiveSampleCollector::new(&store, &clock, Some(10), 1000, Some(memo_chat), 7);

        let gate = DefaultSafetyGate;
        let profile = OwnerSetProfile;
        let guard = SendGuard {
            gate: &gate,
            breaker: &OpenBreaker,
            grade: &collector,
            pacing: &collector,
            profile: &profile,
            clock: &clock,
        };
        let (cfg, req) = allowing_gate_io(chat_id);
        let intent = guard_partner_intent(SendGrade::Memo);

        let fake_send = FakeSendPort::new();
        let blocked = BlockingRealSendPort::new();
        let net = ForbiddenNetwork::new();

        match guard.authorize(&req, &cfg, &intent) {
            Ok(ticket) => {
                // A ticket is only issued for the memo chat.
                prop_assert_eq!(chat_id, memo_chat);
                prop_assert_eq!(ticket.chat_id(), chat_id);
                let receipt = fake_send.send_text(&ticket, "body").expect("fake send");
                prop_assert_eq!(receipt.chat_id, chat_id);
            }
            Err(fence) => {
                prop_assert_ne!(chat_id, memo_chat);
                prop_assert_eq!(fence, GuardFence::GradeLimit(GradeLimit::MemoOnly));
            }
        }

        prop_assert_eq!(blocked.blocked_count(), 0);
        prop_assert_eq!(net.egress_count(), 0);
    }

    /// Through the guard, a smoke run past three confirmed sends is fenced with
    /// `GradeLimit(SmokeCapReached)` — the grade step precedes pacing, so the
    /// cap wins regardless of the interval (R2.7).
    ///
    /// Validates: Requirements 2.7
    #[test]
    fn guard_smoke_cap_fences_fourth_send(chat_id in 1i64..50) {
        let store = SqliteSampleStore::open_in_memory().unwrap();
        let clock = VirtualClock::new(1_000);
        let collector =
            LiveSampleCollector::new(&store, &clock, Some(10), 1000, None, 7);

        // Three confirmed smoke sends reach the run's cap.
        for i in 0..3 {
            collector
                .on_commit(&guard_commit_receipt(&format!("s{i}"), SendGrade::Smoke, 0))
                .unwrap();
        }

        let gate = DefaultSafetyGate;
        let profile = OwnerSetProfile;
        let guard = SendGuard {
            gate: &gate,
            breaker: &OpenBreaker,
            grade: &collector,
            pacing: &collector,
            profile: &profile,
            clock: &clock,
        };
        let (cfg, req) = allowing_gate_io(chat_id);
        let intent = guard_partner_intent(SendGrade::Smoke);

        let blocked = BlockingRealSendPort::new();
        let net = ForbiddenNetwork::new();

        prop_assert_eq!(
            guard.authorize(&req, &cfg, &intent),
            Err(GuardFence::GradeLimit(GradeLimit::SmokeCapReached))
        );

        prop_assert_eq!(blocked.blocked_count(), 0);
        prop_assert_eq!(net.egress_count(), 0);
    }

    /// Through the guard, a send that arrives before the minimum interval
    /// elapses is fenced with `TooSoon` (a rejection, not a delay); once the
    /// clock reaches the next-allowed time, the same request issues a ticket
    /// (R2.8).
    ///
    /// Validates: Requirements 2.8, 3.8
    #[test]
    fn guard_rejects_before_min_interval(base in 10u64..120, seed in any::<u64>()) {
        let memo_chat = 5i64;
        let store = SqliteSampleStore::open_in_memory().unwrap();
        let clock = VirtualClock::new(1_000);
        let collector =
            LiveSampleCollector::new(&store, &clock, Some(base), 1000, Some(memo_chat), seed);

        // One memo commit at t=1000 paces the next send forward.
        collector
            .on_commit(&guard_commit_receipt("t0", SendGrade::Memo, 1_000))
            .unwrap();

        let gate = DefaultSafetyGate;
        let profile = OwnerSetProfile;
        let guard = SendGuard {
            gate: &gate,
            breaker: &OpenBreaker,
            grade: &collector,
            pacing: &collector,
            profile: &profile,
            clock: &clock,
        };
        let (cfg, req) = allowing_gate_io(memo_chat);
        let intent = guard_partner_intent(SendGrade::Memo);

        let fake_send = FakeSendPort::new();
        let blocked = BlockingRealSendPort::new();
        let net = ForbiddenNetwork::new();

        // The clock has not advanced past the paced next-allowed time.
        match guard.authorize(&req, &cfg, &intent) {
            Err(GuardFence::TooSoon { next_allowed_at }) => {
                prop_assert!(next_allowed_at >= 1_000 + base as i64 * 1_000);
                // Advancing to the next-allowed time lets the ticket issue.
                clock.set_ms(next_allowed_at);
                let ticket = guard
                    .authorize(&req, &cfg, &intent)
                    .expect("ticket after interval elapses");
                prop_assert_eq!(ticket.chat_id(), memo_chat);
                let _ = fake_send.send_text(&ticket, "body").expect("fake send");
            }
            other => prop_assert!(false, "expected TooSoon, got {:?}", other),
        }

        prop_assert_eq!(blocked.blocked_count(), 0);
        prop_assert_eq!(net.egress_count(), 0);
    }
}
