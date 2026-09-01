//! Self-check (닥터) fake-adapter test (Task 9.1, R7.2, R7.3, R7.8).
//!
//! Fake process/DB/AX adapters induce fault states so the whole self-check can
//! be exercised without any real Kakao or network call. The test verifies two
//! things:
//!
//! * **Bounded repair (R7.2, R7.3).** A repairable fault is retried at most
//!   [`MAX_REPAIR_ATTEMPTS`] (= 3) times per problem, re-checking after each
//!   attempt; a fault that recovers within the bound stops early.
//! * **Safety-gate invariance (R7.8).** The AX/safety-gate adapter is never
//!   repaired: its value is byte-identical before and after the self-check, and
//!   its repair hook is never invoked.
//!
//! Validates: Requirements R7.2, R7.3, R7.8

use std::cell::Cell;
use std::rc::Rc;

use openkakao_cli::doctor::{
    CheckKind, CheckProbe, CheckStatus, Doctor, RepairRecord, MAX_REPAIR_ATTEMPTS,
};
use proptest::prelude::*;

/// Fake process adapter: the internal processing is stopped and a restart
/// succeeds once `restart_success_after` restart attempts have been made. Every
/// restart call is counted so the test can assert the attempt bound.
struct FakeProcessAdapter {
    restart_calls: Rc<Cell<u32>>,
    restart_success_after: u32,
}

impl FakeProcessAdapter {
    fn new(restart_success_after: u32) -> (Self, Rc<Cell<u32>>) {
        let restart_calls = Rc::new(Cell::new(0));
        (
            Self {
                restart_calls: restart_calls.clone(),
                restart_success_after,
            },
            restart_calls,
        )
    }
}

impl CheckProbe for FakeProcessAdapter {
    fn name(&self) -> String {
        "가짜 처리 프로세스".to_string()
    }
    fn kind(&self) -> CheckKind {
        CheckKind::Runtime
    }
    fn check(&self) -> CheckStatus {
        if self.restart_calls.get() >= self.restart_success_after {
            CheckStatus::Ok
        } else {
            CheckStatus::Fail("처리 프로세스가 멈춰 있어요.".to_string())
        }
    }
    fn is_repairable(&self) -> bool {
        true
    }
    fn attempt_repair(&self) {
        self.restart_calls.set(self.restart_calls.get() + 1);
    }
    fn guidance(&self) -> String {
        "처리 프로세스를 다시 시작하지 못했어요. 카카오톡을 다시 켠 뒤 시도해 주세요.".to_string()
    }
}

/// Fake DB adapter: the connection is dropped and a reconnect succeeds once
/// `reconnect_success_after` attempts have been made.
struct FakeDbAdapter {
    reconnect_calls: Rc<Cell<u32>>,
    reconnect_success_after: u32,
}

impl FakeDbAdapter {
    fn new(reconnect_success_after: u32) -> (Self, Rc<Cell<u32>>) {
        let reconnect_calls = Rc::new(Cell::new(0));
        (
            Self {
                reconnect_calls: reconnect_calls.clone(),
                reconnect_success_after,
            },
            reconnect_calls,
        )
    }
}

impl CheckProbe for FakeDbAdapter {
    fn name(&self) -> String {
        "가짜 로컬 데이터베이스".to_string()
    }
    fn kind(&self) -> CheckKind {
        CheckKind::LocalDb
    }
    fn check(&self) -> CheckStatus {
        if self.reconnect_calls.get() >= self.reconnect_success_after {
            CheckStatus::Ok
        } else {
            CheckStatus::Fail("데이터베이스 연결이 끊겼어요.".to_string())
        }
    }
    fn is_repairable(&self) -> bool {
        true
    }
    fn attempt_repair(&self) {
        self.reconnect_calls.set(self.reconnect_calls.get() + 1);
    }
    fn guidance(&self) -> String {
        "데이터베이스에 다시 연결하지 못했어요. 카카오톡이 켜져 있는지 확인해 주세요.".to_string()
    }
}

/// Fake AX / safety-gate adapter. It owns a shared "gate value" that stands in
/// for the whitelist + opt-in flags + database-authoritative rules. It is
/// unrepairable (R7.8): if the doctor ever calls `attempt_repair`, the repair
/// counter ticks and the gate value is corrupted so the invariant assertion
/// fails loudly.
struct FakeAxSafetyAdapter {
    consistent: bool,
    gate_value: Rc<Cell<u64>>,
    repair_calls: Rc<Cell<u32>>,
}

impl FakeAxSafetyAdapter {
    /// Build the adapter plus shared handles to observe the gate value and the
    /// repair-call counter after a run.
    fn new(consistent: bool, gate_value: u64) -> (Self, Rc<Cell<u64>>, Rc<Cell<u32>>) {
        let gate = Rc::new(Cell::new(gate_value));
        let repair_calls = Rc::new(Cell::new(0));
        (
            Self {
                consistent,
                gate_value: gate.clone(),
                repair_calls: repair_calls.clone(),
            },
            gate,
            repair_calls,
        )
    }
}

impl CheckProbe for FakeAxSafetyAdapter {
    fn name(&self) -> String {
        "가짜 안전 규칙(AX 전송)".to_string()
    }
    fn kind(&self) -> CheckKind {
        CheckKind::SafetyGate
    }
    fn check(&self) -> CheckStatus {
        if self.consistent {
            CheckStatus::Ok
        } else {
            CheckStatus::Fail("안전 규칙이 서로 맞지 않아요.".to_string())
        }
    }
    fn is_repairable(&self) -> bool {
        // Safety-gate values are never modified by repair (R7.8).
        false
    }
    fn attempt_repair(&self) {
        // The doctor must never reach here for a safety-gate probe. If it does,
        // corrupt the gate so the invariance assertion fails.
        self.repair_calls.set(self.repair_calls.get() + 1);
        self.gate_value.set(self.gate_value.get().wrapping_add(1));
    }
    fn guidance(&self) -> String {
        "안전 규칙은 자동으로 바꾸지 않아요. 설정을 직접 확인해 주세요.".to_string()
    }
}

/// Find the repair record for a given check kind.
fn repair_for(records: &[RepairRecord], kind: CheckKind) -> &RepairRecord {
    records
        .iter()
        .find(|record| record.kind == kind)
        .expect("a repair record for the kind")
}

#[test]
fn never_recovering_faults_are_bounded_to_three_attempts() {
    // Process and DB never recover (success threshold far beyond the bound).
    let (process, restart_calls) = FakeProcessAdapter::new(99);
    let (db, reconnect_calls) = FakeDbAdapter::new(99);
    let (ax, gate, ax_repair_calls) = FakeAxSafetyAdapter::new(true, 0xA11_C0DE);

    let doctor = Doctor::new(vec![Box::new(process), Box::new(db), Box::new(ax)]);
    let report = doctor.run();

    // Each repairable problem is retried exactly MAX_REPAIR_ATTEMPTS times and
    // then left failing with plain-language guidance (R7.2, R7.6).
    let runtime = repair_for(&report.repairs, CheckKind::Runtime);
    assert_eq!(runtime.outcome.attempts, MAX_REPAIR_ATTEMPTS);
    assert!(!runtime.outcome.repaired);
    assert!(runtime.outcome.guidance.is_some());

    let localdb = repair_for(&report.repairs, CheckKind::LocalDb);
    assert_eq!(localdb.outcome.attempts, MAX_REPAIR_ATTEMPTS);
    assert!(!localdb.outcome.repaired);

    // The actual number of repair actions performed is bounded (<= 3) (R7.2).
    assert_eq!(restart_calls.get(), MAX_REPAIR_ATTEMPTS);
    assert_eq!(reconnect_calls.get(), MAX_REPAIR_ATTEMPTS);

    // Safety gate is invariant: never repaired, value unchanged (R7.8).
    assert_eq!(ax_repair_calls.get(), 0);
    assert_eq!(gate.get(), 0xA11_C0DE);
}

#[test]
fn recovering_faults_repair_within_bound() {
    // Process recovers on the 1st restart, DB on the 2nd reconnect.
    let (process, restart_calls) = FakeProcessAdapter::new(1);
    let (db, reconnect_calls) = FakeDbAdapter::new(2);
    let (ax, gate, ax_repair_calls) = FakeAxSafetyAdapter::new(true, 42);

    let doctor = Doctor::new(vec![Box::new(process), Box::new(db), Box::new(ax)]);
    let report = doctor.run();

    let runtime = repair_for(&report.repairs, CheckKind::Runtime);
    assert_eq!(runtime.outcome.attempts, 1);
    assert!(runtime.outcome.repaired);
    assert!(runtime.outcome.guidance.is_none());

    let localdb = repair_for(&report.repairs, CheckKind::LocalDb);
    assert_eq!(localdb.outcome.attempts, 2);
    assert!(localdb.outcome.repaired);

    assert_eq!(restart_calls.get(), 1);
    assert_eq!(reconnect_calls.get(), 2);

    // After repair the items are re-checked and reported healthy (R7.3).
    assert!(report.items.iter().all(|item| item.status.is_ok()));
    assert!(report.all_ok());

    // Safety gate untouched throughout (R7.8).
    assert_eq!(ax_repair_calls.get(), 0);
    assert_eq!(gate.get(), 42);
}

#[test]
fn inconsistent_safety_gate_is_reported_but_never_modified() {
    // An inconsistent safety gate must be surfaced with guidance and left
    // exactly as it was — no repair attempt, no value change (R7.6, R7.8).
    let (ax, gate, ax_repair_calls) = FakeAxSafetyAdapter::new(false, 7);
    let doctor = Doctor::new(vec![Box::new(ax)]);
    let report = doctor.run();

    assert_eq!(report.items.len(), 1);
    assert!(report.items[0].status.is_fail());
    assert!(!report.items[0].repaired);

    let record = repair_for(&report.repairs, CheckKind::SafetyGate);
    assert_eq!(record.outcome.attempts, 0);
    assert!(!record.outcome.repaired);
    assert!(record.outcome.guidance.is_some());

    // Never repaired, value invariant (R7.8).
    assert_eq!(ax_repair_calls.get(), 0);
    assert_eq!(gate.get(), 7);
}

proptest! {
    /// For any recovery threshold, a repairable fault performs at most
    /// MAX_REPAIR_ATTEMPTS repair actions, and the recorded attempt count is
    /// exactly `min(threshold, MAX_REPAIR_ATTEMPTS)` — while the safety gate is
    /// never touched (R7.2, R7.3, R7.8).
    #[test]
    fn repair_attempts_are_always_bounded(recover_after in 1u32..=8) {
        let (process, restart_calls) = FakeProcessAdapter::new(recover_after);
        let (ax, gate, ax_repair_calls) = FakeAxSafetyAdapter::new(true, 0xBEEF);

        let doctor = Doctor::new(vec![Box::new(process), Box::new(ax)]);
        let report = doctor.run();

        let runtime = repair_for(&report.repairs, CheckKind::Runtime);
        let expected = recover_after.min(MAX_REPAIR_ATTEMPTS);

        // Attempts recorded and repair actions performed are both bounded.
        prop_assert_eq!(runtime.outcome.attempts, expected);
        prop_assert!(runtime.outcome.attempts <= MAX_REPAIR_ATTEMPTS);
        prop_assert_eq!(restart_calls.get(), expected);
        prop_assert!(restart_calls.get() <= MAX_REPAIR_ATTEMPTS);

        // Repaired only when it could recover within the bound (R7.3).
        prop_assert_eq!(runtime.outcome.repaired, recover_after <= MAX_REPAIR_ATTEMPTS);

        // Safety gate invariant regardless of the runtime fault (R7.8).
        prop_assert_eq!(ax_repair_calls.get(), 0);
        prop_assert_eq!(gate.get(), 0xBEEF);
    }
}
