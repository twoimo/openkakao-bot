//! Golden parity test pinning `DefaultSafetyGate::evaluate`'s 64-combination
//! decision table.
//!
//! Task 2 added the `SendGuard` precondition layer *around* the gate without
//! touching `evaluate`. This test freezes the gate's full six-input decision
//! table as a golden fixture: if any future change alters the gate's behavior
//! for even one of the 64 combinations, this test fails. It proves the
//! `SendGuard` addition did not modify the gate (R12.1).
//!
//! The six inputs are laid out as bits, matching the 64-combination property in
//! `tests/safety_gate_properties.rs`:
//!
//! ```text
//! bit 0: allow_ax_send
//! bit 1: allow_auto_reply
//! bit 2: on the allowlist
//! bit 3: db-authoritative identity match
//! bit 4: db-authoritative target match
//! bit 5: db-authoritative state match
//! ```
//!
//! `evaluate` has a single allowlist/target lever, so chat membership is set
//! only when *both* the allowlist bit and the db-target bit are true. The gate
//! therefore allows exactly one of the 64 rows — the all-true row — and every
//! other row is a fence with a specific reason.

use std::collections::BTreeSet;

use openkakao_cli::safety::{
    DefaultSafetyGate, FenceReason, SafetyConfig, SafetyGate, SendDecision, SendRequest,
};

/// The frozen decision table: the fence reason (or `"Allow"`) for each of the
/// 64 input combinations, indexed by the bit pattern described above. Generated
/// once and pinned; do not edit without an intentional gate change.
const GOLDEN: [&str; 64] = [
    "AxSendDisabled", "AutoReplyDisabled", "AxSendDisabled", "ChatNotAllowlisted",
    "AxSendDisabled", "AutoReplyDisabled", "AxSendDisabled", "ChatNotAllowlisted",
    "AxSendDisabled", "AutoReplyDisabled", "AxSendDisabled", "ChatNotAllowlisted",
    "AxSendDisabled", "AutoReplyDisabled", "AxSendDisabled", "ChatNotAllowlisted",
    "AxSendDisabled", "AutoReplyDisabled", "AxSendDisabled", "ChatNotAllowlisted",
    "AxSendDisabled", "AutoReplyDisabled", "AxSendDisabled", "IdentityMismatch",
    "AxSendDisabled", "AutoReplyDisabled", "AxSendDisabled", "ChatNotAllowlisted",
    "AxSendDisabled", "AutoReplyDisabled", "AxSendDisabled", "StateMismatch",
    "AxSendDisabled", "AutoReplyDisabled", "AxSendDisabled", "ChatNotAllowlisted",
    "AxSendDisabled", "AutoReplyDisabled", "AxSendDisabled", "ChatNotAllowlisted",
    "AxSendDisabled", "AutoReplyDisabled", "AxSendDisabled", "ChatNotAllowlisted",
    "AxSendDisabled", "AutoReplyDisabled", "AxSendDisabled", "ChatNotAllowlisted",
    "AxSendDisabled", "AutoReplyDisabled", "AxSendDisabled", "ChatNotAllowlisted",
    "AxSendDisabled", "AutoReplyDisabled", "AxSendDisabled", "IdentityMismatch",
    "AxSendDisabled", "AutoReplyDisabled", "AxSendDisabled", "ChatNotAllowlisted",
    "AxSendDisabled", "AutoReplyDisabled", "AxSendDisabled", "Allow",
];

/// Render a gate decision as the short code used in [`GOLDEN`].
fn decision_code(decision: &SendDecision) -> &'static str {
    match decision {
        SendDecision::Allow => "Allow",
        SendDecision::Fenced(FenceReason::AxSendDisabled) => "AxSendDisabled",
        SendDecision::Fenced(FenceReason::AutoReplyDisabled) => "AutoReplyDisabled",
        SendDecision::Fenced(FenceReason::ChatNotAllowlisted) => "ChatNotAllowlisted",
        SendDecision::Fenced(FenceReason::IdentityMismatch) => "IdentityMismatch",
        SendDecision::Fenced(FenceReason::StateMismatch) => "StateMismatch",
    }
}

/// Build the gate inputs for one bit pattern (same mapping as the property
/// test).
fn gate_io(bits: u8) -> (SendRequest, SafetyConfig) {
    let chat_id = 42;
    let ax = bits & 0b000001 != 0;
    let auto = bits & 0b000010 != 0;
    let allowlisted = bits & 0b000100 != 0;
    let db_identity = bits & 0b001000 != 0;
    let db_target = bits & 0b010000 != 0;
    let db_state = bits & 0b100000 != 0;

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

#[test]
fn gate_64_combination_table_matches_golden() {
    let gate = DefaultSafetyGate;
    for bits in 0u8..64 {
        let (req, cfg) = gate_io(bits);
        let decision = gate.evaluate(&req, &cfg);
        assert_eq!(
            decision_code(&decision),
            GOLDEN[bits as usize],
            "gate decision changed for input bits {bits:06b}"
        );
    }
}

#[test]
fn gate_allows_exactly_one_of_64_combinations() {
    let gate = DefaultSafetyGate;
    let allow_count = (0u8..64)
        .filter(|&bits| {
            let (req, cfg) = gate_io(bits);
            gate.evaluate(&req, &cfg).is_allowed()
        })
        .count();
    assert_eq!(allow_count, 1, "exactly one of 64 combinations must Allow");
    // The single allowing row is the all-true row.
    let (req, cfg) = gate_io(0b111111);
    assert!(gate.evaluate(&req, &cfg).is_allowed());
}
