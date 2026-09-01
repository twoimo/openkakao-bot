//! Property-based tests for the emergency breaker (task 4.3).
//!
//! Feature: kakao-agent-live-ops
//! Property 11: 브레이커 발동 동치와 판단 입력 제한
//!   (trip equivalence and judgment-input restriction)
//! Property 12: 브레이커 지속성과 사용자 해제 전 전송 0
//!   (persistence and zero confirmed sends before user release)
//!
//! Property 11 pins that [`evaluate`] trips exactly when Bot_Loop ∨ Send_Burst
//! ∨ Error_Spike holds per the module constants, that the scope follows the
//! reason (Bot_Loop / Send_Burst → the room, Error_Spike → all rooms), and that
//! the verdict depends only on the three judgment inputs — the room identifier,
//! the last-five-minutes confirmed-send timestamps, and the last-twenty result
//! codes / alternation observation. Because [`BreakerObservation`] holds nothing
//! else, "judge on only these" is a property of the input type; the tests
//! demonstrate the corollaries (order/room-id/out-of-window invariance).
//!
//! Property 12 drives real send attempts through [`SendGuard::authorize`] with
//! an [`EmergencyBreaker`] as the [`BreakerGateSource`]. Under any interleaving
//! of time passage, restart (a fresh breaker over the same
//! [`SqliteBreakerStore`]), configuration change, and automatic retry, zero
//! confirmed sends happen before an explicit user release; an unreadable
//! judgment input blocks fail-closed ([`BreakerGate::Unknown`]); and the three
//! observation values are reset immediately after release. Every send path
//! asserts the `BlockingRealSendPort` counter and `ForbiddenNetwork` egress
//! stay at zero.
//!
//! Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.6, 3.7, 3.9, 3.10, 3.12, 3.13,
//! 12.13, 12.14

use openkakao_cli::breaker::{
    evaluate, Alternation, BreakReason, BreakScope, BreakerError, BreakerObservation, BreakerStore,
    EmergencyBreaker, SqliteBreakerStore, TripState, BOT_LOOP_ALTERNATIONS, BOT_LOOP_MAX_GAP_SECS,
    BURST_MAX_COMMITS, SPIKE_RATIO, SPIKE_WINDOW,
};
use openkakao_cli::fakes::{BlockingRealSendPort, FakeSendPort, ForbiddenNetwork, VirtualClock};
use openkakao_cli::logging::StageStatus;
use openkakao_cli::ports::{Clock, NetworkPort, SendPort};
use openkakao_cli::safety::{
    BreakerGate, BreakerGateSource, DefaultSafetyGate, GradeLimit, GradePolicy, GuardFence, Origin,
    OwnerNameStatus, PacingSource, ProfileView, SafetyConfig, SendGrade, SendGuard, SendIntent,
    SendRequest,
};
use proptest::prelude::*;

// ===========================================================================
// Shared strategies for the pure-evaluation property (Property 11).
// ===========================================================================

/// An arbitrary pipeline-stage result code.
fn status_strategy() -> impl Strategy<Value = StageStatus> {
    prop_oneof![
        Just(StageStatus::InProgress),
        Just(StageStatus::Success),
        Just(StageStatus::Failed),
    ]
}

/// An arbitrary breaker observation covering both tripping and quiet inputs:
///   * commit counts that straddle the burst cap,
///   * outcome runs that straddle the spike window and ratio,
///   * alternation runs and round-trip gaps that straddle the bot-loop
///     thresholds (including negative and over-limit gaps).
fn observation_strategy() -> impl Strategy<Value = BreakerObservation> {
    (
        1i64..1_000,
        prop::collection::vec(0i64..1_000_000, 0..16),
        prop::collection::vec(status_strategy(), 0..26),
        0u8..6,
        prop::collection::vec(-5i64..120, 0..6),
    )
        .prop_map(|(room_id, commits, outcomes, run, gaps)| BreakerObservation {
            room_id,
            commits_last_5min: commits,
            recent_outcomes: outcomes,
            alternation: Alternation {
                run,
                gaps_secs: gaps,
            },
        })
}

// --- Independent oracles for the three trip conditions, recomputed from the
// --- module constants so the equivalence is anchored to the conditions rather
// --- than to the implementation's control flow. ---

/// Error_Spike: over the last [`SPIKE_WINDOW`] outcomes, the failed ratio
/// exceeds [`SPIKE_RATIO`] (R3.3).
fn cond_error_spike(outcomes: &[StageStatus]) -> bool {
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
        .filter(|s| matches!(s, StageStatus::Failed))
        .count();
    (failed as f32 / window.len() as f32) > SPIKE_RATIO
}

/// Send_Burst: more than [`BURST_MAX_COMMITS`] confirmed sends in the window
/// (R3.2).
fn cond_send_burst(commits: &[i64]) -> bool {
    commits.len() > BURST_MAX_COMMITS
}

/// Bot_Loop: a run of at least [`BOT_LOOP_ALTERNATIONS`] human-free
/// alternations, each round-trip gap within [`BOT_LOOP_MAX_GAP_SECS`] (R3.1).
fn cond_bot_loop(a: &Alternation) -> bool {
    a.run >= BOT_LOOP_ALTERNATIONS
        && a.gaps_secs.len() >= BOT_LOOP_ALTERNATIONS as usize
        && a.gaps_secs
            .iter()
            .all(|g| *g >= 0 && *g <= BOT_LOOP_MAX_GAP_SECS)
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// Property 11 (core): `evaluate` trips iff one of the three conditions
    /// holds, the reason follows the broadest-first priority
    /// (Error_Spike → Send_Burst → Bot_Loop), and the scope follows the reason
    /// (Error_Spike → all rooms, otherwise the observed room).
    ///
    /// Validates: Requirements 3.1, 3.2, 3.3, 3.7, 3.9, 12.14
    #[test]
    fn trip_iff_one_of_three_conditions_with_scope_and_priority(
        obs in observation_strategy(),
    ) {
        let result = evaluate(&obs);
        let spike = cond_error_spike(&obs.recent_outcomes);
        let burst = cond_send_burst(&obs.commits_last_5min);
        let bot_loop = cond_bot_loop(&obs.alternation);

        // Trip equivalence: a trip happens exactly when a condition holds.
        prop_assert_eq!(result.is_some(), spike || burst || bot_loop);

        match result {
            Some((reason, scope)) => {
                // Broadest-first priority when more than one condition holds.
                if spike {
                    prop_assert_eq!(reason, BreakReason::ErrorSpike);
                } else if burst {
                    prop_assert_eq!(reason, BreakReason::SendBurst);
                } else {
                    prop_assert_eq!(reason, BreakReason::BotLoop);
                }
                // Scope rule: Error_Spike is all rooms; the others are the room.
                match reason {
                    BreakReason::ErrorSpike => prop_assert_eq!(scope, BreakScope::All),
                    BreakReason::SendBurst | BreakReason::BotLoop => {
                        prop_assert_eq!(scope, BreakScope::Room(obs.room_id));
                    }
                }
            }
            None => {
                prop_assert!(!spike && !burst && !bot_loop);
            }
        }
    }

    /// Property 11 (input restriction): the verdict is invariant to the order of
    /// the confirmed-send timestamps — only their count feeds the burst check.
    ///
    /// Validates: Requirements 3.9
    #[test]
    fn verdict_invariant_to_commit_order(obs in observation_strategy()) {
        let mut reordered = obs.clone();
        reordered.commits_last_5min.reverse();
        prop_assert_eq!(evaluate(&obs), evaluate(&reordered));
    }

    /// Property 11 (input restriction): changing the room identifier never
    /// changes whether or why the breaker trips; it only relabels the room in a
    /// room-scoped verdict, and never touches an all-rooms verdict.
    ///
    /// Validates: Requirements 3.7, 3.9
    #[test]
    fn reason_invariant_to_room_id(obs in observation_strategy()) {
        let mut other = obs.clone();
        other.room_id = obs.room_id.wrapping_add(500);
        prop_assume!(other.room_id != obs.room_id);

        match (evaluate(&obs), evaluate(&other)) {
            (None, None) => {}
            (Some((ra, sa)), Some((rb, sb))) => {
                // The reason is unaffected by the room identifier.
                prop_assert_eq!(ra, rb);
                match ra {
                    BreakReason::ErrorSpike => {
                        prop_assert_eq!(sa, BreakScope::All);
                        prop_assert_eq!(sb, BreakScope::All);
                    }
                    BreakReason::SendBurst | BreakReason::BotLoop => {
                        prop_assert_eq!(sa, BreakScope::Room(obs.room_id));
                        prop_assert_eq!(sb, BreakScope::Room(other.room_id));
                    }
                }
            }
            _ => prop_assert!(false, "room id alone changed the trip decision"),
        }
    }

    /// Property 11 (input restriction): result codes older than the last
    /// [`SPIKE_WINDOW`] do not feed the verdict, so prepending arbitrary older
    /// outcomes leaves the decision unchanged.
    ///
    /// Validates: Requirements 3.3, 3.9
    #[test]
    fn verdict_invariant_to_outcomes_before_window(
        mut obs in observation_strategy(),
        tail in prop::collection::vec(status_strategy(), SPIKE_WINDOW..(SPIKE_WINDOW + 20)),
        prefix in prop::collection::vec(status_strategy(), 0..12),
    ) {
        // A tail of at least a full window, so prepending only touches outcomes
        // outside the considered window.
        obs.recent_outcomes = tail;
        let baseline = evaluate(&obs);

        let mut extended = obs.clone();
        let mut with_prefix = prefix;
        with_prefix.extend(extended.recent_outcomes.clone());
        extended.recent_outcomes = with_prefix;

        prop_assert_eq!(baseline, evaluate(&extended));
    }

    /// Property 11 (purity): `evaluate` is a pure function — evaluating the same
    /// observation twice yields the same verdict, with no hidden state.
    ///
    /// Validates: Requirements 3.9
    #[test]
    fn evaluate_is_deterministic(obs in observation_strategy()) {
        prop_assert_eq!(evaluate(&obs), evaluate(&obs));
    }
}

// ===========================================================================
// Property 12 — persistence, zero sends before release, fail-closed, reset.
// ===========================================================================

// --- Permissive guard collaborators: every precondition other than the
// --- breaker passes, so the breaker gate is the sole decider of a fence. ---

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

/// A store whose reads always fail — models "the judgment inputs cannot be
/// read" so the gate must block fail-closed (R3.10).
struct FailingStore;
impl BreakerStore for FailingStore {
    fn load(&self) -> Result<Vec<TripState>, BreakerError> {
        Err(BreakerError::Backend("unreadable".into()))
    }
    fn trip(&self, _s: &TripState) -> Result<(), BreakerError> {
        Ok(())
    }
    fn clear_and_reset(&self, _scope: BreakScope) -> Result<(), BreakerError> {
        Ok(())
    }
}

/// One product intent that routes through the single send guard.
fn intent() -> SendIntent {
    SendIntent {
        grade: SendGrade::Memo,
        origin: Origin::PartnerMessage {
            event_id: "evt".into(),
        },
    }
}

/// A config/request pair the *unchanged* safety gate always allows, so only a
/// breaker fence can hold a send back.
fn allowing_gate_io(chat_id: i64) -> (SafetyConfig, SendRequest) {
    let cfg = SafetyConfig::new(true, true, [chat_id], "fp-owner");
    let req = SendRequest {
        chat_id,
        account_fp: "fp-owner".into(),
        state_ok: true,
    };
    (cfg, req)
}

/// An interleaved lifecycle event applied while the breaker is tripped.
#[derive(Debug, Clone)]
enum Event {
    /// Logical time passes.
    TimePass(i64),
    /// The app restarts (modeled as a fresh breaker over the same store).
    Restart,
    /// Some configuration changes (must not release the trip).
    ConfigChange,
    /// An automatic retry occurs (must not release the trip).
    AutoRetry,
    /// A send is attempted; while tripped it must be fenced.
    SendAttempt,
}

fn event_strategy() -> impl Strategy<Value = Event> {
    prop_oneof![
        (1i64..600_000).prop_map(Event::TimePass),
        Just(Event::Restart),
        Just(Event::ConfigChange),
        Just(Event::AutoRetry),
        Just(Event::SendAttempt),
    ]
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// Property 12 (persistence + zero sends before release): once tripped, no
    /// interleaving of time passage, restart, config change, or auto retry
    /// releases the trip, so every send attempt within the trip scope is fenced
    /// with zero confirmed sends. Only an explicit user release reopens the
    /// gate, and the send that follows succeeds. Throughout, no call reaches the
    /// real send path and no network egress occurs.
    ///
    /// Validates: Requirements 3.4, 3.6, 3.12, 12.13, 12.14
    #[test]
    fn no_confirmed_sends_before_explicit_release(
        reason_idx in 0usize..3,
        target in 1i64..50,
        events in prop::collection::vec(event_strategy(), 0..20),
    ) {
        let store = SqliteBreakerStore::open_in_memory().expect("store");
        let clock = VirtualClock::new(1_000);
        let gate = DefaultSafetyGate;
        let (cfg, req) = allowing_gate_io(target);

        let fake_send = FakeSendPort::new();
        let blocked = BlockingRealSendPort::new();
        let net = ForbiddenNetwork::new();

        // Trip the breaker for a scope covering the target chat. Error_Spike is
        // all-rooms; the others are the room itself.
        let reason = [
            BreakReason::BotLoop,
            BreakReason::SendBurst,
            BreakReason::ErrorSpike,
        ][reason_idx];
        let scope = match reason {
            BreakReason::ErrorSpike => BreakScope::All,
            _ => BreakScope::Room(target),
        };
        store
            .trip(&TripState {
                reason,
                scope,
                at: clock.now_ms(),
            })
            .expect("trip");
        // Seed an alternation observation so its reset is observable later.
        store
            .upsert_observation(target, 3, Some(10), Some(20))
            .expect("observation");

        // Apply the interleaved events. A fresh breaker is built for every send
        // attempt, which models a restart and also the "read the store on every
        // authorization, never cache" contract (R3.12).
        for event in &events {
            match event {
                Event::TimePass(ms) => clock.advance_ms(*ms),
                // Restart / config change / auto retry must never release a
                // trip; they are represented here as steps that touch neither
                // the store's trip row nor the release path.
                Event::Restart | Event::ConfigChange | Event::AutoRetry => {}
                Event::SendAttempt => {
                    let breaker = EmergencyBreaker::new(&store, &clock);
                    let guard = SendGuard {
                        gate: &gate,
                        breaker: &breaker,
                        grade: &AnyGrade,
                        pacing: &ReadyPacing,
                        profile: &OwnerSetInScope,
                        clock: &clock,
                    };
                    match guard.authorize(&req, &cfg, &intent()) {
                        Ok(_) => prop_assert!(false, "a tripped breaker must fence the send"),
                        Err(GuardFence::Breaker { reason: r, .. }) => {
                            prop_assert_eq!(r, mirror_reason(reason));
                        }
                        Err(other) => {
                            prop_assert!(false, "unexpected fence while tripped: {:?}", other)
                        }
                    }
                }
            }
        }

        // A final restart-then-attempt guarantees persistence is exercised even
        // when the generated stream contained no send attempt.
        {
            let breaker = EmergencyBreaker::new(&store, &clock);
            let guard = SendGuard {
                gate: &gate,
                breaker: &breaker,
                grade: &AnyGrade,
                pacing: &ReadyPacing,
                profile: &OwnerSetInScope,
                clock: &clock,
            };
            let still_fenced =
                matches!(guard.authorize(&req, &cfg, &intent()), Err(GuardFence::Breaker { .. }));
            prop_assert!(still_fenced, "trip must persist across the event stream");
        }

        // Nothing was sent before release.
        prop_assert_eq!(fake_send.confirmed_count(), 0);
        prop_assert_eq!(blocked.blocked_count(), 0);
        prop_assert_eq!(net.egress_count(), 0);

        // Explicit user release reopens the gate for the scope.
        {
            let breaker = EmergencyBreaker::new(&store, &clock);
            breaker.release(scope).expect("release");
        }

        // Immediately after release the send succeeds.
        {
            let breaker = EmergencyBreaker::new(&store, &clock);
            let guard = SendGuard {
                gate: &gate,
                breaker: &breaker,
                grade: &AnyGrade,
                pacing: &ReadyPacing,
                profile: &OwnerSetInScope,
                clock: &clock,
            };
            let ticket = guard
                .authorize(&req, &cfg, &intent())
                .expect("open after user release");
            let receipt = fake_send.send_text(&ticket, "body").expect("fake send");
            prop_assert_eq!(receipt.chat_id, target);
        }

        // Exactly one confirmed send, and only after release.
        prop_assert_eq!(fake_send.confirmed_count(), 1);
        prop_assert_eq!(blocked.blocked_count(), 0);
        prop_assert_eq!(net.egress_count(), 0);
    }

    /// Property 12 (fail-closed): when the breaker cannot read its judgment
    /// inputs, the gate blocks with [`GuardFence::BreakerUnknown`] and no send
    /// happens (R3.10).
    ///
    /// Validates: Requirements 3.10
    #[test]
    fn unreadable_inputs_block_fail_closed(target in 1i64..50) {
        let clock = VirtualClock::new(0);
        let store = FailingStore;
        let breaker = EmergencyBreaker::new(&store, &clock);
        let gate = DefaultSafetyGate;
        let (cfg, req) = allowing_gate_io(target);

        let fake_send = FakeSendPort::new();
        let blocked = BlockingRealSendPort::new();
        let net = ForbiddenNetwork::new();

        let guard = SendGuard {
            gate: &gate,
            breaker: &breaker,
            grade: &AnyGrade,
            pacing: &ReadyPacing,
            profile: &OwnerSetInScope,
            clock: &clock,
        };
        prop_assert!(matches!(
            guard.authorize(&req, &cfg, &intent()),
            Err(GuardFence::BreakerUnknown(_))
        ));
        prop_assert_eq!(fake_send.confirmed_count(), 0);
        prop_assert_eq!(blocked.blocked_count(), 0);
        prop_assert_eq!(net.egress_count(), 0);
    }

    /// Property 12 (reset on release): immediately after an explicit release the
    /// persisted alternation observation is reset to its initial value and the
    /// gate reads open, so the burst / spike observations recompute from
    /// scratch (R3.13).
    ///
    /// Validates: Requirements 3.13
    #[test]
    fn observation_values_reset_after_release(
        reason_idx in 0usize..3,
        target in 1i64..50,
        run in 1u8..6,
    ) {
        let store = SqliteBreakerStore::open_in_memory().expect("store");
        let clock = VirtualClock::new(0);

        let reason = [
            BreakReason::BotLoop,
            BreakReason::SendBurst,
            BreakReason::ErrorSpike,
        ][reason_idx];
        let scope = match reason {
            BreakReason::ErrorSpike => BreakScope::All,
            _ => BreakScope::Room(target),
        };
        store
            .trip(&TripState { reason, scope, at: 0 })
            .expect("trip");
        store
            .upsert_observation(target, run, Some(1), Some(2))
            .expect("observation");
        prop_assert_eq!(store.observation_run(target).expect("run"), run);

        let breaker = EmergencyBreaker::new(&store, &clock);
        let is_tripped = matches!(breaker.breaker_gate(target), BreakerGate::Tripped { .. });
        prop_assert!(is_tripped, "breaker must be tripped before release");

        breaker.release(scope).expect("release");

        // The alternation observation is reset and the gate is open again.
        prop_assert_eq!(store.observation_run(target).expect("run"), 0);
        prop_assert_eq!(breaker.breaker_gate(target), BreakerGate::Open);
    }
}

/// Map the authoritative breaker reason to the send guard's mirror reason so the
/// fence assertion can compare against the reason the trip was created with.
fn mirror_reason(reason: BreakReason) -> openkakao_cli::safety::BreakReason {
    match reason {
        BreakReason::BotLoop => openkakao_cli::safety::BreakReason::BotLoop,
        BreakReason::SendBurst => openkakao_cli::safety::BreakReason::SendBurst,
        BreakReason::ErrorSpike => openkakao_cli::safety::BreakReason::ErrorSpike,
    }
}
