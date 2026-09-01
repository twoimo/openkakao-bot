//! Safety-gate layer — the single source of truth for whether an automatic
//! reply may perform a real product send.
//!
//! Every product-send path (auto reply, GeekNews) must funnel through
//! [`SafetyGate::evaluate`]. There is intentionally no bypass: the UI, the
//! experiment runner, and the dataset builder never construct a
//! [`SendDecision::Allow`] on their own.
//!
//! Two safety invariants are enforced structurally in this module:
//!
//! 1. **Opt-in + database-authoritative match (R11.3, R11.5, R11.6).** A send is
//!    only ever allowed when *all* of the following hold: AX sending is enabled,
//!    automatic replies are enabled, the target chat is on the allowlist, and the
//!    database-authoritative identity/target/state checks all agree. Any single
//!    failure keeps the send [`SendDecision::Fenced`] with a machine-readable
//!    reason and performs no send.
//!
//! 2. **LOCO stays quarantined (R11.4).** LOCO write operations are represented
//!    by a *separate* decision type ([`LocoDecision`]) that has **no** allow
//!    variant. Because there is no value of `LocoDecision` that authorizes a
//!    product send — and no conversion from `LocoDecision` into
//!    `SendDecision::Allow` — it is impossible at the type level for a LOCO path
//!    to reach a product-send decision.

use std::collections::BTreeSet;

mod guard;
pub use guard::{
    BreakReason, BreakScope, BreakerGate, BreakerGateSource, GradeLimit, GradePolicy, GuardFence,
    Origin, OwnerNameStatus, PacingSource, ProfileView, SendGrade, SendGuard, SendIntent,
    SendTicket, UnsetReason,
};

/// A request to perform a product send through the AX `local-send` path.
///
/// The request carries the values that must be reconciled against the
/// database-authoritative configuration before a send is allowed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SendRequest {
    /// Target chat id the send is destined for (database-authoritative target).
    pub chat_id: i64,
    /// Account fingerprint presented for this send (database-authoritative
    /// identity). Compared for exact equality against
    /// [`SafetyConfig::authoritative_account_fp`].
    pub account_fp: String,
    /// Whether the database-authoritative state check passed for this send.
    pub state_ok: bool,
}

/// Safety-gate configuration — the database-authoritative source of truth the
/// gate reconciles each [`SendRequest`] against.
///
/// This is deliberately distinct from [`crate::config::SafetyConfig`], which is
/// the on-disk TOML representation. This type is the resolved, in-memory view
/// the gate reasons about, so the gate never depends on serialization details.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SafetyConfig {
    /// AX-automation send opt-in. Must be true for any product send.
    pub allow_ax_send: bool,
    /// Automatic-reply opt-in, independent from `allow_ax_send`.
    pub allow_auto_reply: bool,
    /// Chat ids the gate is permitted to target. Empty means nothing may send.
    pub allowed_send_chats: BTreeSet<i64>,
    /// Database-authoritative account identity. A send is only allowed when the
    /// request's fingerprint matches this exactly and is non-empty.
    pub authoritative_account_fp: String,
}

impl SafetyConfig {
    /// Build a gate config from raw parts. Convenience for call sites that hold
    /// the allowlist as a slice/iterator.
    pub fn new(
        allow_ax_send: bool,
        allow_auto_reply: bool,
        allowed_send_chats: impl IntoIterator<Item = i64>,
        authoritative_account_fp: impl Into<String>,
    ) -> Self {
        Self {
            allow_ax_send,
            allow_auto_reply,
            allowed_send_chats: allowed_send_chats.into_iter().collect(),
            authoritative_account_fp: authoritative_account_fp.into(),
        }
    }
}

/// Why a send was fenced. Every non-`Allow` outcome carries exactly one reason,
/// so callers can surface a plain-language explanation without guessing.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FenceReason {
    /// `allow_ax_send` opt-in is off.
    AxSendDisabled,
    /// `allow_auto_reply` opt-in is off.
    AutoReplyDisabled,
    /// Target chat id is not on the allowlist (database-authoritative target
    /// mismatch).
    ChatNotAllowlisted,
    /// Presented account fingerprint does not match the authoritative identity
    /// (or is empty).
    IdentityMismatch,
    /// Database-authoritative state check failed.
    StateMismatch,
}

/// The only decision type that can authorize a product send.
///
/// `Allow` is produced exclusively by [`SafetyGate::evaluate`] after every
/// opt-in and database-authoritative check passes.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SendDecision {
    /// The send may proceed.
    Allow,
    /// The send is held back; no send is performed.
    Fenced(FenceReason),
}

impl SendDecision {
    /// True only for [`SendDecision::Allow`].
    pub fn is_allowed(&self) -> bool {
        matches!(self, SendDecision::Allow)
    }
}

/// The safety gate. All product-send authorization flows through this trait.
pub trait SafetyGate {
    /// Reconcile `req` against `cfg` and decide whether a product send may
    /// proceed. Pure and side-effect free: fencing performs no send.
    fn evaluate(&self, req: &SendRequest, cfg: &SafetyConfig) -> SendDecision;
}

/// The default, fail-closed safety gate.
#[derive(Debug, Clone, Copy, Default)]
pub struct DefaultSafetyGate;

impl SafetyGate for DefaultSafetyGate {
    fn evaluate(&self, req: &SendRequest, cfg: &SafetyConfig) -> SendDecision {
        // Opt-in gates first (R11.3): both AX send and auto reply must be on.
        if !cfg.allow_ax_send {
            return SendDecision::Fenced(FenceReason::AxSendDisabled);
        }
        if !cfg.allow_auto_reply {
            return SendDecision::Fenced(FenceReason::AutoReplyDisabled);
        }
        // Database-authoritative target: the chat must be on the allowlist.
        if !cfg.allowed_send_chats.contains(&req.chat_id) {
            return SendDecision::Fenced(FenceReason::ChatNotAllowlisted);
        }
        // Database-authoritative identity: exact, non-empty fingerprint match
        // (R11.6). An empty authoritative fingerprint can never match.
        if cfg.authoritative_account_fp.is_empty()
            || req.account_fp != cfg.authoritative_account_fp
        {
            return SendDecision::Fenced(FenceReason::IdentityMismatch);
        }
        // Database-authoritative state (R11.5/R11.6).
        if !req.state_ok {
            return SendDecision::Fenced(FenceReason::StateMismatch);
        }
        SendDecision::Allow
    }
}

// ---------------------------------------------------------------------------
// LOCO quarantine — separate, allow-less decision type (R11.4).
// ---------------------------------------------------------------------------

/// A request to perform a LOCO write. Kept structurally distinct from
/// [`SendRequest`] so a LOCO write can never be mistaken for a product send.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocoWriteRequest {
    /// The LOCO write operation being requested.
    pub op: LocoWriteOp,
    /// Target chat id (informational only; never authorizes a product send).
    pub chat_id: i64,
}

/// LOCO write operations. All remain research-quarantined.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LocoWriteOp {
    Send,
    Delete,
    Edit,
    React,
    MarkRead,
}

/// Why a LOCO write was quarantined.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LocoQuarantineReason {
    /// LOCO writes are research-quarantined and never used as a product path.
    ResearchQuarantined,
}

/// The outcome of evaluating a LOCO write.
///
/// This enum has **no** allow variant on purpose. Because the only reachable
/// value is [`LocoDecision::Quarantined`], and there is no conversion from
/// `LocoDecision` into [`SendDecision::Allow`], the type system guarantees a
/// LOCO path can never reach a product-send decision (R11.4).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LocoDecision {
    /// The LOCO write is held in quarantine; it is never a product send.
    Quarantined(LocoQuarantineReason),
}

/// The LOCO quarantine gate. Kept separate from [`SafetyGate`] so the two
/// decision types never mix.
pub trait LocoQuarantine {
    /// Evaluate a LOCO write. Always quarantined — never a product send.
    fn evaluate_loco(&self, req: &LocoWriteRequest) -> LocoDecision;
}

/// The default LOCO quarantine: every LOCO write is quarantined.
#[derive(Debug, Clone, Copy, Default)]
pub struct DefaultLocoQuarantine;

impl LocoQuarantine for DefaultLocoQuarantine {
    fn evaluate_loco(&self, _req: &LocoWriteRequest) -> LocoDecision {
        LocoDecision::Quarantined(LocoQuarantineReason::ResearchQuarantined)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cfg_allowing(chat_id: i64) -> SafetyConfig {
        SafetyConfig::new(true, true, [chat_id], "fp-owner")
    }

    fn good_request(chat_id: i64) -> SendRequest {
        SendRequest {
            chat_id,
            account_fp: "fp-owner".into(),
            state_ok: true,
        }
    }

    #[test]
    fn allows_only_when_every_condition_holds() {
        let gate = DefaultSafetyGate;
        let decision = gate.evaluate(&good_request(42), &cfg_allowing(42));
        assert_eq!(decision, SendDecision::Allow);
    }

    #[test]
    fn fences_when_ax_send_disabled() {
        let gate = DefaultSafetyGate;
        let mut cfg = cfg_allowing(42);
        cfg.allow_ax_send = false;
        assert_eq!(
            gate.evaluate(&good_request(42), &cfg),
            SendDecision::Fenced(FenceReason::AxSendDisabled)
        );
    }

    #[test]
    fn fences_when_auto_reply_disabled() {
        let gate = DefaultSafetyGate;
        let mut cfg = cfg_allowing(42);
        cfg.allow_auto_reply = false;
        assert_eq!(
            gate.evaluate(&good_request(42), &cfg),
            SendDecision::Fenced(FenceReason::AutoReplyDisabled)
        );
    }

    #[test]
    fn fences_when_chat_not_allowlisted() {
        let gate = DefaultSafetyGate;
        let cfg = cfg_allowing(42);
        assert_eq!(
            gate.evaluate(&good_request(7), &cfg),
            SendDecision::Fenced(FenceReason::ChatNotAllowlisted)
        );
    }

    #[test]
    fn fences_when_identity_mismatch() {
        let gate = DefaultSafetyGate;
        let cfg = cfg_allowing(42);
        let mut req = good_request(42);
        req.account_fp = "someone-else".into();
        assert_eq!(
            gate.evaluate(&req, &cfg),
            SendDecision::Fenced(FenceReason::IdentityMismatch)
        );
    }

    #[test]
    fn fences_when_authoritative_identity_empty() {
        let gate = DefaultSafetyGate;
        let mut cfg = cfg_allowing(42);
        cfg.authoritative_account_fp = String::new();
        let mut req = good_request(42);
        req.account_fp = String::new();
        assert_eq!(
            gate.evaluate(&req, &cfg),
            SendDecision::Fenced(FenceReason::IdentityMismatch)
        );
    }

    #[test]
    fn fences_when_state_check_failed() {
        let gate = DefaultSafetyGate;
        let cfg = cfg_allowing(42);
        let mut req = good_request(42);
        req.state_ok = false;
        assert_eq!(
            gate.evaluate(&req, &cfg),
            SendDecision::Fenced(FenceReason::StateMismatch)
        );
    }

    #[test]
    fn loco_write_is_always_quarantined() {
        let gate = DefaultLocoQuarantine;
        for op in [
            LocoWriteOp::Send,
            LocoWriteOp::Delete,
            LocoWriteOp::Edit,
            LocoWriteOp::React,
            LocoWriteOp::MarkRead,
        ] {
            let decision = gate.evaluate_loco(&LocoWriteRequest { op, chat_id: 42 });
            assert_eq!(
                decision,
                LocoDecision::Quarantined(LocoQuarantineReason::ResearchQuarantined)
            );
        }
    }
}
