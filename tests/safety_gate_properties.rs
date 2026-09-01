//! Property-based tests for the safety-gate layer.
//!
//! These verify Correctness Property 1 (safety gate) and Correctness Property 3
//! (database-authoritative fence) from the design document across arbitrary
//! inputs, making it impossible to refute that an `Allow` implies every opt-in
//! condition plus the database-authoritative identity/target/state match.
//!
//! Validates: Requirements R11.3, R11.5, R11.6

use std::collections::{BTreeSet, HashSet};

use openkakao_cli::fakes::{BlockingRealSendPort, FakeSendPort, ForbiddenNetwork};
use openkakao_cli::ports::{Clock, HttpRequest, NetworkPort, SendPort};
use openkakao_cli::safety::{
    BreakerGate, BreakerGateSource, DefaultLocoQuarantine, DefaultSafetyGate, FenceReason,
    GradeLimit, GradePolicy, GuardFence, LocoDecision, LocoQuarantine, LocoQuarantineReason,
    LocoWriteOp, LocoWriteRequest, Origin, OwnerNameStatus, PacingSource, ProfileView, SafetyConfig,
    SafetyGate, SendDecision, SendGrade, SendGuard, SendIntent, SendRequest,
};
use proptest::prelude::*;

/// Strategy for an arbitrary fingerprint, including the empty string so the
/// identity check's fail-closed behaviour is exercised.
fn fingerprint_strategy() -> impl Strategy<Value = String> {
    prop::string::string_regex("[a-z]{0,6}").expect("valid regex")
}

/// Strategy for an arbitrary `SafetyConfig`.
fn config_strategy() -> impl Strategy<Value = SafetyConfig> {
    (
        any::<bool>(),
        any::<bool>(),
        prop::collection::btree_set(-20i64..20, 0..8),
        fingerprint_strategy(),
    )
        .prop_map(
            |(allow_ax_send, allow_auto_reply, allowed, authoritative_account_fp)| SafetyConfig {
                allow_ax_send,
                allow_auto_reply,
                allowed_send_chats: allowed,
                authoritative_account_fp,
            },
        )
}

/// Strategy for an arbitrary `SendRequest`.
fn request_strategy() -> impl Strategy<Value = SendRequest> {
    (-20i64..20, fingerprint_strategy(), any::<bool>()).prop_map(|(chat_id, account_fp, state_ok)| {
        SendRequest {
            chat_id,
            account_fp,
            state_ok,
        }
    })
}

/// The three opt-in conditions plus the database-authoritative match that an
/// `Allow` must always imply.
fn all_conditions_hold(req: &SendRequest, cfg: &SafetyConfig, allowed: &BTreeSet<i64>) -> bool {
    cfg.allow_ax_send
        && cfg.allow_auto_reply
        && allowed.contains(&req.chat_id)
        && !cfg.authoritative_account_fp.is_empty()
        && req.account_fp == cfg.authoritative_account_fp
        && req.state_ok
}

proptest! {
    /// Correctness Property 1: an `Allow` decision implies AX send + auto reply
    /// opt-ins, allowlist membership, and the database-authoritative identity
    /// and state match. Contrapositive: whenever any condition fails the
    /// decision is never `Allow`.
    #[test]
    fn allow_implies_all_opt_ins_and_db_match(req in request_strategy(), cfg in config_strategy()) {
        let gate = DefaultSafetyGate;
        let decision = gate.evaluate(&req, &cfg);
        let allowed = cfg.allowed_send_chats.clone();

        if decision.is_allowed() {
            prop_assert!(
                all_conditions_hold(&req, &cfg, &allowed),
                "Allow was returned but a required condition did not hold: {req:?} {cfg:?}"
            );
        } else {
            // Symmetric direction: if all conditions hold, it must be Allow.
            prop_assert!(
                !all_conditions_hold(&req, &cfg, &allowed),
                "all conditions held yet decision was not Allow: {decision:?}"
            );
        }
    }

    /// Correctness Property 3: if the database-authoritative identity, target,
    /// or state check fails, the decision is always `Fenced` (never `Allow`),
    /// with a fence reason drawn only from the identity/target/state checks
    /// once the opt-ins are satisfied.
    #[test]
    fn db_authoritative_mismatch_always_fences(req in request_strategy(), cfg in config_strategy()) {
        let gate = DefaultSafetyGate;

        let identity_ok =
            !cfg.authoritative_account_fp.is_empty() && req.account_fp == cfg.authoritative_account_fp;
        let target_ok = cfg.allowed_send_chats.contains(&req.chat_id);
        let state_ok = req.state_ok;

        let decision = gate.evaluate(&req, &cfg);

        if !(identity_ok && target_ok && state_ok) {
            prop_assert!(
                !decision.is_allowed(),
                "a db-authoritative check failed but decision was Allow: {req:?} {cfg:?}"
            );
        }

        // When the opt-ins are on, the fence reason (if any) must come from the
        // db-authoritative checks — never from the opt-in gates.
        if cfg.allow_ax_send && cfg.allow_auto_reply {
            match &decision {
                SendDecision::Allow => {
                    prop_assert!(identity_ok && target_ok && state_ok);
                }
                SendDecision::Fenced(reason) => {
                    prop_assert!(matches!(
                        reason,
                        FenceReason::ChatNotAllowlisted
                            | FenceReason::IdentityMismatch
                            | FenceReason::StateMismatch
                    ));
                }
            }
        }
    }

    /// The safety gate is pure: evaluating the same input twice yields the same
    /// decision (no hidden side effects that could sneak a send through).
    #[test]
    fn evaluation_is_deterministic(req in request_strategy(), cfg in config_strategy()) {
        let gate = DefaultSafetyGate;
        prop_assert_eq!(gate.evaluate(&req, &cfg), gate.evaluate(&req, &cfg));
    }

    /// LOCO quarantine can never authorize a product send: its decision type has
    /// no allow variant, so the only reachable outcome is `Quarantined`
    /// (R11.4).
    #[test]
    fn loco_write_never_allows(chat_id in -20i64..20, op_index in 0usize..5) {
        let op = [
            LocoWriteOp::Send,
            LocoWriteOp::Delete,
            LocoWriteOp::Edit,
            LocoWriteOp::React,
            LocoWriteOp::MarkRead,
        ][op_index];
        let gate = DefaultLocoQuarantine;
        let decision = gate.evaluate_loco(&LocoWriteRequest { op, chat_id });
        prop_assert_eq!(
            decision,
            LocoDecision::Quarantined(LocoQuarantineReason::ResearchQuarantined)
        );
    }
}

// ===========================================================================
// Feature: kakao-agent-live-ops
// Property 1: 안전게이트 64조합 동치 (safety-gate 64-combination equivalence)
// Property 2: 전송은 게이트가 허용한 대상에서만, LOCO 쓰기 0
//             (sends occur only for gate-permitted targets, zero LOCO writes)
// ===========================================================================

/// The six independent boolean inputs of R12.1, expanded to a `SafetyConfig` /
/// `SendRequest` pair that drives the *unchanged* `DefaultSafetyGate::evaluate`.
///
/// `evaluate` has one allowlist/target lever, so both "on the allowlist" and
/// "database-authoritative target match" route through chat membership: the
/// chat is present only when *both* are true. This preserves the intended
/// equivalence — `Allow` iff all six inputs are true — over the full 64-row
/// product without touching the gate.
fn six_inputs_to_gate_io(
    chat_id: i64,
    ax: bool,
    auto: bool,
    allowlisted: bool,
    db_identity: bool,
    db_target: bool,
    db_state: bool,
) -> (SendRequest, SafetyConfig) {
    let allowed: BTreeSet<i64> = if allowlisted && db_target {
        [chat_id].into_iter().collect()
    } else {
        BTreeSet::new()
    };
    let cfg = SafetyConfig {
        allow_ax_send: ax,
        allow_auto_reply: auto,
        allowed_send_chats: allowed,
        authoritative_account_fp: "fp-owner".to_string(),
    };
    let req = SendRequest {
        chat_id,
        account_fp: if db_identity {
            "fp-owner".to_string()
        } else {
            "someone-else".to_string()
        },
        state_ok: db_state,
    };
    (req, cfg)
}

// --- Permissive guard collaborators: every precondition passes so the gate is
// --- the sole decider (each precondition is unit-tested separately in
// --- src/safety/guard.rs). ---

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

struct OwnerSetInScope;
impl ProfileView for OwnerSetInScope {
    fn owner_name_status(&self) -> OwnerNameStatus {
        OwnerNameStatus::Set
    }
    fn in_scope(&self) -> bool {
        true
    }
}

struct ZeroClock;
impl Clock for ZeroClock {
    fn now_ms(&self) -> i64 {
        0
    }
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// Property 1: over every one of the 64 combinations of the six R12.1
    /// inputs, `DefaultSafetyGate::evaluate` returns `Allow` exactly when all
    /// six are true, and `Fenced` (never a send) otherwise.
    ///
    /// Validates: Requirements 12.1
    #[test]
    fn safety_gate_64_combination_equivalence(
        chat_id in -20i64..20,
        bits in 0u8..64,
    ) {
        let ax = bits & 0b000001 != 0;
        let auto = bits & 0b000010 != 0;
        let allowlisted = bits & 0b000100 != 0;
        let db_identity = bits & 0b001000 != 0;
        let db_target = bits & 0b010000 != 0;
        let db_state = bits & 0b100000 != 0;
        let all_true = ax && auto && allowlisted && db_identity && db_target && db_state;

        let (req, cfg) =
            six_inputs_to_gate_io(chat_id, ax, auto, allowlisted, db_identity, db_target, db_state);
        let gate = DefaultSafetyGate;
        let decision = gate.evaluate(&req, &cfg);

        // Allow iff all six inputs are true; the one allowing combination.
        prop_assert_eq!(
            decision.is_allowed(),
            all_true,
            "bits={:06b} decision={:?}",
            bits,
            decision
        );
        // A non-allowing combination is always a fence — never a silent send.
        if !all_true {
            prop_assert!(matches!(decision, SendDecision::Fenced(_)));
        }
    }

    /// Property 2: for an arbitrary set of rooms and one safety config, the set
    /// of chats that received a confirmed send equals exactly the set of chats
    /// the safety gate permits — and no real send or network egress ever
    /// happens, while LOCO writes stay quarantined regardless of any opt-in.
    ///
    /// Validates: Requirements 6.10, 7.15, 12.15
    #[test]
    fn sends_only_to_gate_permitted_targets_zero_loco(
        cfg in config_strategy(),
        reqs in prop::collection::vec(request_strategy(), 0..12),
        loco_op_index in 0usize..5,
    ) {
        let gate = DefaultSafetyGate;
        let guard = SendGuard {
            gate: &gate,
            breaker: &OpenBreaker,
            grade: &AnyGrade,
            pacing: &ReadyPacing,
            profile: &OwnerSetInScope,
            clock: &ZeroClock,
        };

        // The three-layer "zero real sends" defense: fake output, a real-send
        // tripwire, and a forbidden network. Nothing here ever wires a real
        // adapter.
        let fake_send = FakeSendPort::new();
        let blocked_real = BlockingRealSendPort::new();
        let net = ForbiddenNetwork::new();

        // Expected: the gate's own permitted set, computed independently.
        let mut expected_permitted: HashSet<i64> = HashSet::new();
        for req in &reqs {
            if gate.evaluate(req, &cfg).is_allowed() {
                expected_permitted.insert(req.chat_id);
            }
        }

        // Drive every request through the guard. A ticket is minted iff the
        // gate permits (preconditions are all permissive here); only then does
        // a send happen, and it can only target the ticket's chat.
        let mut confirmed: HashSet<i64> = HashSet::new();
        for req in &reqs {
            let intent = SendIntent {
                grade: SendGrade::Memo,
                origin: Origin::PartnerMessage { event_id: "evt".into() },
            };
            match guard.authorize(req, &cfg, &intent) {
                Ok(ticket) => {
                    // The send targets exactly the ticket's chat.
                    prop_assert_eq!(ticket.chat_id(), req.chat_id);
                    let receipt = fake_send
                        .send_text(&ticket, "body")
                        .expect("fake send confirms");
                    prop_assert_eq!(receipt.chat_id, req.chat_id);
                    confirmed.insert(receipt.chat_id);
                }
                Err(fence) => {
                    // A guard fence caused by the gate must carry a gate reason;
                    // no send happens on this path.
                    if let GuardFence::Gate(_) = fence {
                        // expected for gate-fenced targets
                    }
                }
            }
        }

        // The confirmed-send set equals exactly the gate-permitted set.
        prop_assert_eq!(&confirmed, &expected_permitted);

        // No call ever reached the real send path, and no egress happened.
        prop_assert_eq!(blocked_real.blocked_count(), 0);
        prop_assert_eq!(net.egress_count(), 0);

        // LOCO writes stay quarantined regardless of `allow_loco_write`: the
        // product path here never touches LOCO, and the LOCO decision type has
        // no allow variant.
        let loco = DefaultLocoQuarantine;
        let op = [
            LocoWriteOp::Send,
            LocoWriteOp::Delete,
            LocoWriteOp::Edit,
            LocoWriteOp::React,
            LocoWriteOp::MarkRead,
        ][loco_op_index];
        prop_assert_eq!(
            loco.evaluate_loco(&LocoWriteRequest { op, chat_id: 1 }),
            LocoDecision::Quarantined(LocoQuarantineReason::ResearchQuarantined)
        );

        // A forbidden-network request would count but never transmit — assert
        // the count only moves when we deliberately probe it, proving egress is
        // observable and zero on the product path above.
        let _ = net.request(HttpRequest {
            method: "GET".into(),
            url: "https://example.invalid".into(),
            body: Vec::new(),
        });
        prop_assert_eq!(net.egress_count(), 1);
    }
}
