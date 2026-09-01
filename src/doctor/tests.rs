//! Unit tests for the self-check doctor (R7). Every probe here is a fake with
//! interior mutability that induces a fault state and (for repairable probes)
//! recovers after a configured number of attempts. No real Kakao/network call
//! is ever made.

use super::*;
use std::cell::Cell;

/// A fake repairable probe that starts failing and becomes healthy after
/// `recover_after` repair attempts. If `recover_after` is larger than
/// [`MAX_REPAIR_ATTEMPTS`], it never recovers within the bound.
struct FlakyProbe {
    name: String,
    kind: CheckKind,
    recover_after: u32,
    attempts: Cell<u32>,
}

impl FlakyProbe {
    fn new(name: &str, kind: CheckKind, recover_after: u32) -> Self {
        Self {
            name: name.to_string(),
            kind,
            recover_after,
            attempts: Cell::new(0),
        }
    }
}

impl CheckProbe for FlakyProbe {
    fn name(&self) -> String {
        self.name.clone()
    }
    fn kind(&self) -> CheckKind {
        self.kind
    }
    fn check(&self) -> CheckStatus {
        if self.attempts.get() >= self.recover_after {
            CheckStatus::Ok
        } else {
            CheckStatus::Fail("아직 정상으로 돌아오지 않았어요.".to_string())
        }
    }
    fn is_repairable(&self) -> bool {
        true
    }
    fn attempt_repair(&self) {
        self.attempts.set(self.attempts.get() + 1);
    }
    fn guidance(&self) -> String {
        "여러 번 고쳐 봤지만 아직 문제가 남아 있어요. 카카오톡을 다시 켠 뒤 시도해 주세요.".to_string()
    }
}

/// A fake safety-gate probe. It is intentionally *unrepairable*: the doctor
/// must never call [`CheckProbe::attempt_repair`] on it (R7.8).
struct SafetyProbe {
    consistent: bool,
}

impl SafetyProbe {
    fn new(consistent: bool) -> Self {
        Self { consistent }
    }
}

impl CheckProbe for SafetyProbe {
    fn name(&self) -> String {
        "안전 규칙 정합성".to_string()
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
        false
    }
    fn attempt_repair(&self) {
        // Should never be called by the doctor for a safety-gate probe (R7.8).
        panic!("안전 규칙 복구가 호출되면 안 됩니다");
    }
    fn guidance(&self) -> String {
        "안전 규칙은 자동으로 바꾸지 않아요. 설정에서 값을 직접 확인해 주세요.".to_string()
    }
}

#[test]
fn diagnose_is_read_only_and_reports_every_item() {
    let doctor = Doctor::new(vec![
        Box::new(FlakyProbe::new("runtime", CheckKind::Runtime, 1)),
        Box::new(SafetyProbe::new(true)),
    ]);
    let items = doctor.diagnose();
    assert_eq!(items.len(), 2);
    assert_eq!(items[0].kind, CheckKind::Runtime);
    assert!(items[0].status.is_fail());
    // diagnose never repairs (read-only): the flaky probe is still failing.
    assert!(!items[0].repaired);
    assert!(items[1].status.is_ok());
}

#[test]
fn repair_recovers_within_bound() {
    // Recovers on the 2nd attempt.
    let probe = FlakyProbe::new("db", CheckKind::LocalDb, 2);
    let doctor = Doctor::new(vec![Box::new(probe)]);
    let item = doctor.diagnose().into_iter().next().unwrap();
    let outcome = doctor.repair(&item);
    assert!(outcome.repaired);
    assert_eq!(outcome.attempts, 2);
    assert_eq!(outcome.final_status, CheckStatus::Ok);
    assert!(outcome.guidance.is_none());
}

#[test]
fn repair_is_bounded_to_three_attempts_then_gives_guidance() {
    // Never recovers within the bound (needs 99 attempts).
    let probe = FlakyProbe::new("db", CheckKind::LocalDb, 99);
    let doctor = Doctor::new(vec![Box::new(probe)]);
    let item = doctor.diagnose().into_iter().next().unwrap();
    let outcome = doctor.repair(&item);
    assert!(!outcome.repaired);
    assert_eq!(outcome.attempts, MAX_REPAIR_ATTEMPTS);
    assert!(outcome.final_status.is_fail());
    assert!(outcome.guidance.is_some());
}

#[test]
fn safety_gate_is_never_repaired_and_value_is_invariant() {
    let doctor = Doctor::new(vec![Box::new(SafetyProbe::new(false))]);
    let report = doctor.run();
    // The safety gate is failing and unrepairable: it is reported with guidance
    // and left unchanged (R7.8).
    assert_eq!(report.items.len(), 1);
    assert!(report.items[0].status.is_fail());
    assert!(!report.items[0].repaired);
    assert_eq!(report.repairs.len(), 1);
    let record = &report.repairs[0];
    assert_eq!(record.kind, CheckKind::SafetyGate);
    assert_eq!(record.outcome.attempts, 0);
    assert!(!record.outcome.repaired);
    assert!(record.outcome.guidance.is_some());
}

#[test]
fn safety_probe_attempt_repair_is_not_invoked_by_doctor() {
    // Observe the invariant with shared Cells owned outside the probe: if the
    // doctor ever called attempt_repair, the counter would tick and the gate
    // value would flip to 0.
    struct CountingSafety {
        repair_calls: std::rc::Rc<Cell<u32>>,
        gate_value: std::rc::Rc<Cell<u64>>,
    }
    impl CheckProbe for CountingSafety {
        fn name(&self) -> String {
            "안전 규칙".to_string()
        }
        fn kind(&self) -> CheckKind {
            CheckKind::SafetyGate
        }
        fn check(&self) -> CheckStatus {
            CheckStatus::Fail("불일치".to_string())
        }
        fn is_repairable(&self) -> bool {
            false
        }
        fn attempt_repair(&self) {
            self.repair_calls.set(self.repair_calls.get() + 1);
            self.gate_value.set(0);
        }
        fn guidance(&self) -> String {
            "안전 규칙은 자동으로 바꾸지 않아요.".to_string()
        }
    }

    let repair_calls = std::rc::Rc::new(Cell::new(0u32));
    let gate_value = std::rc::Rc::new(Cell::new(0xFEEDu64));
    let doctor = Doctor::new(vec![Box::new(CountingSafety {
        repair_calls: repair_calls.clone(),
        gate_value: gate_value.clone(),
    })]);
    let _ = doctor.run();
    // attempt_repair was never called and the gate value is invariant (R7.8).
    assert_eq!(repair_calls.get(), 0);
    assert_eq!(gate_value.get(), 0xFEED);
}

#[test]
fn run_repairs_only_failing_items_and_reflects_post_repair_status() {
    let doctor = Doctor::new(vec![
        Box::new(FlakyProbe::new("runtime", CheckKind::Runtime, 1)), // recovers on 1st attempt
        Box::new(FlakyProbe::new("db", CheckKind::LocalDb, 99)),     // never recovers
        Box::new(SafetyProbe::new(true)),                            // healthy, untouched
    ]);
    let report = doctor.run();
    assert_eq!(report.items.len(), 3);

    // Runtime: repaired, now OK (R7.3).
    assert_eq!(report.items[0].kind, CheckKind::Runtime);
    assert!(report.items[0].repaired);
    assert!(report.items[0].status.is_ok());

    // DB: still failing after bounded attempts, guidance produced (R7.6).
    assert_eq!(report.items[1].kind, CheckKind::LocalDb);
    assert!(!report.items[1].repaired);
    assert!(report.items[1].status.is_fail());

    // Safety: healthy, never appears in repair records.
    assert!(report.items[2].status.is_ok());

    // Repairs recorded only for the two failing items.
    assert_eq!(report.repairs.len(), 2);
    let db_repair = report
        .repairs
        .iter()
        .find(|r| r.kind == CheckKind::LocalDb)
        .unwrap();
    assert_eq!(db_repair.outcome.attempts, MAX_REPAIR_ATTEMPTS);
    assert!(db_repair.outcome.guidance.is_some());

    assert!(!report.all_ok());
    assert_eq!(report.unresolved().count(), 1);
}

#[test]
fn already_healthy_item_repair_is_a_no_op() {
    let doctor = Doctor::new(vec![Box::new(FlakyProbe::new(
        "runtime",
        CheckKind::Runtime,
        0, // already healthy
    ))]);
    let item = doctor.diagnose().into_iter().next().unwrap();
    assert!(item.status.is_ok());
    let outcome = doctor.repair(&item);
    assert_eq!(outcome.attempts, 0);
    assert!(outcome.repaired);
}
