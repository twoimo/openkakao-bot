//! Send tickets and the [`SendGuard`] precondition layer (task 2).
//!
//! `DefaultSafetyGate::evaluate` decides the six-input, 64-combination product
//! of R12.1 and must never change. The live-ops features add several *new*
//! preconditions — an emergency breaker, an owner-name gate, per-account scope,
//! send-grade limits, and a minimum send interval. Folding those into
//! `evaluate` would break the "exactly one of 64 combinations allows" property,
//! so they live *outside* the gate in this thin composing layer.
//!
//! The single output of a successful authorization is a [`SendTicket`]. Its
//! fields and its [`SendTicket::mint`] constructor are private to the `safety`
//! module, so nothing elsewhere can forge one. Because every [`SendPort`] method
//! requires a `&SendTicket`, a code path that tries to send without going
//! through [`SendGuard::authorize`] simply fails to compile — gate bypass is a
//! type error, not a runtime check.
//!
//! [`SendPort`]: crate::ports::SendPort

use crate::ports::Clock;

use super::{FenceReason, SafetyConfig, SafetyGate, SendDecision, SendRequest};

/// The grade of a learning-sample send (R2.4).
///
/// Defined here rather than in `live_sample` because both [`SendTicket`] and
/// [`SendIntent`] need it and this layer is built before the sample collector;
/// `live_sample` reuses this type.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum SendGrade {
    /// Fake-adapter bulk send — never a real KakaoTalk send (R2.5).
    Fake,
    /// The owner's own memo chat (나와의 채팅) (R2.6).
    Memo,
    /// A tiny, user-started smoke run against a real counterpart (R2.7).
    Smoke,
}

/// Why the owner display name is unset (R10.5).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum UnsetReason {
    /// No owner name is configured.
    Missing,
    /// The trimmed name is shorter than one character.
    TooShort,
    /// The trimmed name is longer than 64 characters.
    TooLong,
}

/// The owner display-name status the guard consults (R10.5).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OwnerNameStatus {
    /// A valid owner name is set.
    Set,
    /// No usable owner name is set, with the reason.
    Unset(UnsetReason),
}

/// Why the emergency breaker tripped (mirrors `breaker::BreakReason`, task 4.1).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BreakReason {
    /// A bot-to-bot reply loop (R3.1).
    BotLoop,
    /// A burst of confirmed sends to one room (R3.2).
    SendBurst,
    /// A spike in failed processings (R3.3).
    ErrorSpike,
}

/// The scope a breaker trip covers (mirrors `breaker::BreakScope`, task 4.1).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BreakScope {
    /// One room, by chat id.
    Room(i64),
    /// Every room.
    All,
}

/// The breaker's view for one chat, queried on every authorization (R3.10).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BreakerGate {
    /// No trip covers this chat; sends may proceed.
    Open,
    /// A trip covers this chat; sends are held until user release.
    Tripped {
        /// Why the breaker tripped.
        reason: BreakReason,
        /// The scope the trip covers.
        scope: BreakScope,
        /// Logical time the trip fired.
        at: i64,
    },
    /// The breaker state could not be read; treated as blocked (fail-closed).
    Unknown(&'static str),
}

/// A send-grade limit that blocks a ticket (R2.6, R2.7).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GradeLimit {
    /// A `memo`-grade send targeted something other than the memo chat (R2.6).
    MemoOnly,
    /// A `smoke`-grade run already reached its confirmed-send cap (R2.7).
    SmokeCapReached,
}

/// Why [`SendGuard::authorize`] refused to issue a ticket.
///
/// This is deliberately a *separate* enum from [`FenceReason`]: extending
/// `FenceReason` would enlarge the gate's decision space and break the
/// 64-combination property. New preconditions surface here instead, with the
/// underlying gate reason wrapped in [`GuardFence::Gate`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum GuardFence {
    /// The underlying safety gate fenced the send (unchanged behavior).
    Gate(FenceReason),
    /// The emergency breaker is tripped for this scope (R3.1–R3.4).
    Breaker {
        /// Why the breaker tripped.
        reason: BreakReason,
        /// The scope the trip covers.
        scope: BreakScope,
    },
    /// The breaker state could not be read; blocked fail-closed (R3.10).
    BreakerUnknown(&'static str),
    /// The owner display name is unset (R10.5).
    OwnerNameUnset(UnsetReason),
    /// The target is outside the running account's scope (R10.8).
    ProfileScopeMismatch,
    /// A send-grade limit blocked the send (R2.6, R2.7).
    GradeLimit(GradeLimit),
    /// The minimum send interval has not elapsed; a rejection, not a delay
    /// (R2.8).
    TooSoon {
        /// The earliest logical time the next send may happen.
        next_allowed_at: i64,
    },
}

/// What a send is and why it is happening.
///
/// [`Origin`] deliberately has no "sample filling", "early achievement", or
/// "demo" variant: an artificial send has no value to construct, so it cannot be
/// expressed (R2.11).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SendIntent {
    /// The grade of this send.
    pub grade: SendGrade,
    /// Where this send originated.
    pub origin: Origin,
}

/// The origin of a send. Every variant traces back to a real trigger.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Origin {
    /// A reply prompted by an incoming partner message.
    PartnerMessage {
        /// The originating event id.
        event_id: String,
    },
    /// A GeekNews post triggered by a slot coming due.
    GeekNewsSlot {
        /// The slot marker.
        marker: String,
    },
    /// A forward the user ran directly from the composer.
    UserComposer,
    /// A relay of a source Telegram message.
    RelaySource {
        /// The source message pid.
        message_pid: String,
    },
}

/// A ticket authorizing exactly one send. Cannot be constructed outside the
/// `safety` module — its fields and [`SendTicket::mint`] are private — so it is
/// unforgeable, and because [`SendPort`](crate::ports::SendPort) requires one,
/// gate bypass fails to compile.
#[derive(Debug, PartialEq, Eq)]
pub struct SendTicket {
    chat_id: i64,
    grade: SendGrade,
    issued_at: i64,
}

impl SendTicket {
    /// The chat this ticket authorizes.
    pub fn chat_id(&self) -> i64 {
        self.chat_id
    }

    /// The grade this ticket carries.
    pub fn grade(&self) -> SendGrade {
        self.grade
    }

    /// The logical time this ticket was issued.
    pub fn issued_at(&self) -> i64 {
        self.issued_at
    }

    /// The one and only way to build a ticket. Private to `safety`; only
    /// [`SendGuard::authorize`] calls it.
    fn mint(chat_id: i64, grade: SendGrade, issued_at: i64) -> Self {
        SendTicket {
            chat_id,
            grade,
            issued_at,
        }
    }

    /// Mint a ticket for in-crate tests without wiring a full guard.
    #[cfg(test)]
    pub(crate) fn for_test(chat_id: i64, grade: SendGrade) -> Self {
        SendTicket::mint(chat_id, grade, 0)
    }
}

/// The breaker's per-chat gate source (implemented by `breaker`, task 4.1).
pub trait BreakerGateSource {
    /// The breaker gate for `chat_id`, queried on every authorization.
    fn breaker_gate(&self, chat_id: i64) -> BreakerGate;
}

/// The minimum-interval pacing source (implemented by `live_sample`, task 4).
pub trait PacingSource {
    /// The earliest logical time (ms) the next confirmed send may happen. The
    /// guard rejects — never delays — a request that arrives before this.
    fn next_allowed_at(&self) -> i64;
}

/// The owner-profile view the guard consults (implemented by `profile`,
/// task 3).
pub trait ProfileView {
    /// The owner display-name status (R10.5).
    fn owner_name_status(&self) -> OwnerNameStatus;

    /// Whether the target is within the running account's scope (R10.8).
    fn in_scope(&self) -> bool;
}

/// The send-grade policy (implemented by `live_sample`, task 4).
///
/// This realizes the "send grade" step of [`SendGuard::authorize`]'s ordering.
/// It is a collaborator because a grade limit needs state the guard does not
/// otherwise hold: whether the target is the memo chat (R2.6) and how many
/// smoke sends a run already made (R2.7).
pub trait GradePolicy {
    /// Whether `intent`'s grade permits a send to `chat_id`.
    fn check(&self, intent: &SendIntent, chat_id: i64) -> Result<(), GradeLimit>;
}

/// The composing precondition layer above the unchanged [`SafetyGate`].
pub struct SendGuard<'a> {
    /// The unchanged safety gate (six-input, 64-combination decision).
    pub gate: &'a dyn SafetyGate,
    /// The emergency-breaker gate source.
    pub breaker: &'a dyn BreakerGateSource,
    /// The send-grade policy.
    pub grade: &'a dyn GradePolicy,
    /// The minimum-interval pacing source.
    pub pacing: &'a dyn PacingSource,
    /// The owner-profile view.
    pub profile: &'a dyn ProfileView,
    /// The logical clock.
    pub clock: &'a dyn Clock,
}

impl SendGuard<'_> {
    /// The only path that issues a [`SendTicket`].
    ///
    /// Preconditions run in a fixed order, each fail-closed:
    /// owner name → account scope → breaker → send grade → minimum interval →
    /// the unchanged [`SafetyGate::evaluate`]. Only when all pass is a ticket
    /// minted.
    pub fn authorize(
        &self,
        req: &SendRequest,
        cfg: &SafetyConfig,
        intent: &SendIntent,
    ) -> Result<SendTicket, GuardFence> {
        // 1. Owner display name must be set (R10.5).
        if let OwnerNameStatus::Unset(reason) = self.profile.owner_name_status() {
            return Err(GuardFence::OwnerNameUnset(reason));
        }

        // 2. Target must be within the running account's scope (R10.8).
        if !self.profile.in_scope() {
            return Err(GuardFence::ProfileScopeMismatch);
        }

        // 3. Emergency breaker (fail-closed on unknown) (R3.1–R3.4, R3.10).
        match self.breaker.breaker_gate(req.chat_id) {
            BreakerGate::Open => {}
            BreakerGate::Tripped { reason, scope, .. } => {
                return Err(GuardFence::Breaker { reason, scope });
            }
            BreakerGate::Unknown(why) => return Err(GuardFence::BreakerUnknown(why)),
        }

        // 4. Send-grade limit (R2.6, R2.7).
        if let Err(limit) = self.grade.check(intent, req.chat_id) {
            return Err(GuardFence::GradeLimit(limit));
        }

        // 5. Minimum interval — a rejection, not a delay (R2.8).
        let next_allowed_at = self.pacing.next_allowed_at();
        if self.clock.now_ms() < next_allowed_at {
            return Err(GuardFence::TooSoon { next_allowed_at });
        }

        // 6. The unchanged safety gate (R12.1).
        match self.gate.evaluate(req, cfg) {
            SendDecision::Allow => {}
            SendDecision::Fenced(reason) => return Err(GuardFence::Gate(reason)),
        }

        // 7. Every precondition passed — mint the ticket.
        Ok(SendTicket::mint(req.chat_id, intent.grade, self.clock.now_ms()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::safety::DefaultSafetyGate;
    use std::cell::Cell;

    struct OpenBreaker;
    impl BreakerGateSource for OpenBreaker {
        fn breaker_gate(&self, _chat_id: i64) -> BreakerGate {
            BreakerGate::Open
        }
    }

    struct TrippedBreaker;
    impl BreakerGateSource for TrippedBreaker {
        fn breaker_gate(&self, chat_id: i64) -> BreakerGate {
            BreakerGate::Tripped {
                reason: BreakReason::SendBurst,
                scope: BreakScope::Room(chat_id),
                at: 0,
            }
        }
    }

    struct UnknownBreaker;
    impl BreakerGateSource for UnknownBreaker {
        fn breaker_gate(&self, _chat_id: i64) -> BreakerGate {
            BreakerGate::Unknown("no state")
        }
    }

    struct AnyGrade;
    impl GradePolicy for AnyGrade {
        fn check(&self, _intent: &SendIntent, _chat_id: i64) -> Result<(), GradeLimit> {
            Ok(())
        }
    }

    struct MemoOnlyGrade;
    impl GradePolicy for MemoOnlyGrade {
        fn check(&self, _intent: &SendIntent, _chat_id: i64) -> Result<(), GradeLimit> {
            Err(GradeLimit::MemoOnly)
        }
    }

    struct FixedPacing(i64);
    impl PacingSource for FixedPacing {
        fn next_allowed_at(&self) -> i64 {
            self.0
        }
    }

    struct FixedProfile {
        name: OwnerNameStatus,
        scope: bool,
    }
    impl ProfileView for FixedProfile {
        fn owner_name_status(&self) -> OwnerNameStatus {
            self.name
        }
        fn in_scope(&self) -> bool {
            self.scope
        }
    }

    struct FixedClock(Cell<i64>);
    impl Clock for FixedClock {
        fn now_ms(&self) -> i64 {
            self.0.get()
        }
    }

    fn intent() -> SendIntent {
        SendIntent {
            grade: SendGrade::Memo,
            origin: Origin::PartnerMessage {
                event_id: "evt".into(),
            },
        }
    }

    fn cfg() -> SafetyConfig {
        SafetyConfig::new(true, true, [42], "fp-owner")
    }

    fn req() -> SendRequest {
        SendRequest {
            chat_id: 42,
            account_fp: "fp-owner".into(),
            state_ok: true,
        }
    }

    #[test]
    fn issues_ticket_when_all_preconditions_and_gate_pass() {
        let gate = DefaultSafetyGate;
        let guard = SendGuard {
            gate: &gate,
            breaker: &OpenBreaker,
            grade: &AnyGrade,
            pacing: &FixedPacing(0),
            profile: &FixedProfile {
                name: OwnerNameStatus::Set,
                scope: true,
            },
            clock: &FixedClock(Cell::new(1_000)),
        };
        let ticket = guard.authorize(&req(), &cfg(), &intent()).expect("ticket");
        assert_eq!(ticket.chat_id(), 42);
        assert_eq!(ticket.grade(), SendGrade::Memo);
        assert_eq!(ticket.issued_at(), 1_000);
    }

    #[test]
    fn owner_name_checked_first() {
        let gate = DefaultSafetyGate;
        let guard = SendGuard {
            gate: &gate,
            breaker: &TrippedBreaker, // would also fail, but owner name comes first
            grade: &AnyGrade,
            pacing: &FixedPacing(0),
            profile: &FixedProfile {
                name: OwnerNameStatus::Unset(UnsetReason::Missing),
                scope: false,
            },
            clock: &FixedClock(Cell::new(0)),
        };
        assert_eq!(
            guard.authorize(&req(), &cfg(), &intent()),
            Err(GuardFence::OwnerNameUnset(UnsetReason::Missing))
        );
    }

    #[test]
    fn scope_checked_before_breaker() {
        let gate = DefaultSafetyGate;
        let guard = SendGuard {
            gate: &gate,
            breaker: &TrippedBreaker,
            grade: &AnyGrade,
            pacing: &FixedPacing(0),
            profile: &FixedProfile {
                name: OwnerNameStatus::Set,
                scope: false,
            },
            clock: &FixedClock(Cell::new(0)),
        };
        assert_eq!(
            guard.authorize(&req(), &cfg(), &intent()),
            Err(GuardFence::ProfileScopeMismatch)
        );
    }

    #[test]
    fn breaker_unknown_is_fail_closed() {
        let gate = DefaultSafetyGate;
        let guard = SendGuard {
            gate: &gate,
            breaker: &UnknownBreaker,
            grade: &AnyGrade,
            pacing: &FixedPacing(0),
            profile: &FixedProfile {
                name: OwnerNameStatus::Set,
                scope: true,
            },
            clock: &FixedClock(Cell::new(0)),
        };
        assert_eq!(
            guard.authorize(&req(), &cfg(), &intent()),
            Err(GuardFence::BreakerUnknown("no state"))
        );
    }

    #[test]
    fn grade_limit_blocks_before_pacing_and_gate() {
        let gate = DefaultSafetyGate;
        let guard = SendGuard {
            gate: &gate,
            breaker: &OpenBreaker,
            grade: &MemoOnlyGrade,
            pacing: &FixedPacing(i64::MAX), // would also fail, but grade comes first
            profile: &FixedProfile {
                name: OwnerNameStatus::Set,
                scope: true,
            },
            clock: &FixedClock(Cell::new(0)),
        };
        assert_eq!(
            guard.authorize(&req(), &cfg(), &intent()),
            Err(GuardFence::GradeLimit(GradeLimit::MemoOnly))
        );
    }

    #[test]
    fn too_soon_is_rejection_not_delay() {
        let gate = DefaultSafetyGate;
        let guard = SendGuard {
            gate: &gate,
            breaker: &OpenBreaker,
            grade: &AnyGrade,
            pacing: &FixedPacing(5_000),
            profile: &FixedProfile {
                name: OwnerNameStatus::Set,
                scope: true,
            },
            clock: &FixedClock(Cell::new(4_999)),
        };
        assert_eq!(
            guard.authorize(&req(), &cfg(), &intent()),
            Err(GuardFence::TooSoon {
                next_allowed_at: 5_000
            })
        );
    }

    #[test]
    fn gate_fence_is_wrapped() {
        let gate = DefaultSafetyGate;
        let mut bad = cfg();
        bad.allow_ax_send = false;
        let guard = SendGuard {
            gate: &gate,
            breaker: &OpenBreaker,
            grade: &AnyGrade,
            pacing: &FixedPacing(0),
            profile: &FixedProfile {
                name: OwnerNameStatus::Set,
                scope: true,
            },
            clock: &FixedClock(Cell::new(0)),
        };
        assert_eq!(
            guard.authorize(&req(), &bad, &intent()),
            Err(GuardFence::Gate(FenceReason::AxSendDisabled))
        );
    }
}
