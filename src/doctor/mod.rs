//! Self-check (닥터) — pure-local diagnosis and bounded repair (R7).
//!
//! The self-check system diagnoses four things and, where possible, repairs
//! them without contacting any external provider or LLM (R7.4):
//!
//! 1. **Runtime health** — is the internal auto-reply processing alive? A
//!    stopped processing can be restarted (R7.2).
//! 2. **Config validity** — are configuration values in range? An invalid
//!    value can be restored to its default (R7.2). **Safety-gate values are
//!    never touched** (R7.8).
//! 3. **Local DB access** — can the local database be opened? A dropped
//!    connection can be reconnected (R7.2).
//! 4. **Safety-gate consistency** — are the account-protection rules coherent?
//!    This check is **diagnostic only**: its values are never modified by any
//!    repair (R7.8). An inconsistency is reported with plain-language guidance
//!    and the state is left unchanged (R7.6, R7.7).
//!
//! ## Bounded repair (R7.2, R7.3)
//!
//! A repairable problem is retried at most [`MAX_REPAIR_ATTEMPTS`] times *per
//! problem*. After each attempt the item is re-checked; the loop stops as soon
//! as the check passes. If the problem still remains after the last attempt,
//! plain-language guidance is produced and the item is reported as still
//! failing (R7.6).
//!
//! ## Structural safety invariant (R7.8)
//!
//! A [`CheckProbe`] declares whether its problem is auto-repairable via
//! [`CheckProbe::is_repairable`]. The [`Doctor`] only ever calls
//! [`CheckProbe::attempt_repair`] on probes that return `true`, and the
//! safety-gate probe returns `false`. Because the doctor never invokes a repair
//! on the safety-gate probe, and no other probe is given a handle to the
//! safety-gate values, it is not possible for a repair to modify a safety gate.
//!
//! ## Testability
//!
//! The doctor is generic over a set of injected [`CheckProbe`]s. Tests inject
//! **fake** process/DB/AX probes that induce fault states, so a self-check can
//! be verified end to end without a live Kakao/network call. Real probes (which
//! read local state, config, and the local DB) live on the binary side.

use std::fmt;

/// Maximum number of repair attempts made for a single problem before the
/// doctor gives up and produces guidance (R7.2).
pub const MAX_REPAIR_ATTEMPTS: u32 = 3;

/// Which subsystem a check covers (R7.1).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum CheckKind {
    /// The internal auto-reply processing is alive (R7.1).
    Runtime,
    /// Configuration values are valid / in range (R7.1).
    Config,
    /// The local database can be opened (R7.1).
    LocalDb,
    /// The safety-gate rules are coherent. Never repaired (R7.8).
    SafetyGate,
}

impl CheckKind {
    /// A short, stable machine label (used in JSON output).
    pub fn as_str(self) -> &'static str {
        match self {
            CheckKind::Runtime => "runtime",
            CheckKind::Config => "config",
            CheckKind::LocalDb => "local_db",
            CheckKind::SafetyGate => "safety_gate",
        }
    }
}

impl fmt::Display for CheckKind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        // Plain-language Korean labels (R7.5).
        let label = match self {
            CheckKind::Runtime => "자동 답변 동작 상태",
            CheckKind::Config => "설정 값",
            CheckKind::LocalDb => "로컬 데이터베이스 연결",
            CheckKind::SafetyGate => "안전 규칙",
        };
        f.write_str(label)
    }
}

/// The result of a single check (R7.1). `Warn`/`Fail` carry a plain-language
/// reason (R7.5).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CheckStatus {
    /// The item is healthy.
    Ok,
    /// The item is usable but worth noting; not treated as a repair trigger.
    Warn(String),
    /// The item is faulty. A repairable `Fail` triggers bounded repair.
    Fail(String),
}

impl CheckStatus {
    /// True only for [`CheckStatus::Ok`].
    pub fn is_ok(&self) -> bool {
        matches!(self, CheckStatus::Ok)
    }

    /// True when the item is a fault (a repair trigger).
    pub fn is_fail(&self) -> bool {
        matches!(self, CheckStatus::Fail(_))
    }

    /// A short, stable machine label (used in JSON output).
    pub fn as_str(&self) -> &'static str {
        match self {
            CheckStatus::Ok => "ok",
            CheckStatus::Warn(_) => "warn",
            CheckStatus::Fail(_) => "fail",
        }
    }

    /// The plain-language reason, if any.
    pub fn detail(&self) -> Option<&str> {
        match self {
            CheckStatus::Ok => None,
            CheckStatus::Warn(reason) | CheckStatus::Fail(reason) => Some(reason),
        }
    }
}

/// One diagnosed check item (R7.1). `repaired` is set when a repair brought the
/// item back to [`CheckStatus::Ok`] (R7.3).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CheckItem {
    /// A stable, human-readable name for the check.
    pub name: String,
    /// The subsystem the check covers.
    pub kind: CheckKind,
    /// The current status of the check.
    pub status: CheckStatus,
    /// Whether a repair restored this item during the self-check (R7.3).
    pub repaired: bool,
}

/// The outcome of attempting to repair one problem (R7.2, R7.3, R7.6).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RepairOutcome {
    /// How many repair attempts were made. Always `<= MAX_REPAIR_ATTEMPTS`, and
    /// `0` when the problem was unrepairable or already healthy.
    pub attempts: u32,
    /// Whether the problem is healthy after the attempts (R7.3).
    pub repaired: bool,
    /// The status after the repair attempts / re-check (R7.3).
    pub final_status: CheckStatus,
    /// Plain-language guidance shown when the problem is unrepairable or still
    /// remains after the maximum attempts (R7.6). `None` when the problem was
    /// repaired.
    pub guidance: Option<String>,
}

/// A single diagnostic probe.
///
/// [`CheckProbe::check`] is **read-only** and free of side effects.
/// [`CheckProbe::attempt_repair`] performs exactly one repair attempt and is
/// only ever called by the [`Doctor`] on probes that report
/// [`CheckProbe::is_repairable`] as `true`. A repair MUST NOT modify any
/// safety-gate value (R7.8).
pub trait CheckProbe {
    /// A stable, human-readable name for the check.
    fn name(&self) -> String;

    /// The subsystem this probe covers.
    fn kind(&self) -> CheckKind;

    /// Run the check. Read-only: it must not change any state (R7.7).
    fn check(&self) -> CheckStatus;

    /// Whether a failing check can be auto-repaired (R7.2). The safety-gate
    /// probe returns `false` so its values are never modified (R7.8).
    fn is_repairable(&self) -> bool {
        false
    }

    /// Perform exactly one repair attempt. The default is a no-op. This is only
    /// called on repairable probes and must never touch safety-gate values
    /// (R7.8).
    fn attempt_repair(&self) {}

    /// Plain-language guidance shown when the problem is unrepairable or still
    /// remains after the maximum attempts (R7.5, R7.6).
    fn guidance(&self) -> String;
}

/// A full self-check report (R7.1–R7.3).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DoctorReport {
    /// Every diagnosed item, with the post-repair status reflected (R7.3).
    pub items: Vec<CheckItem>,
    /// The repair record for every item that started as a fault.
    pub repairs: Vec<RepairRecord>,
}

impl DoctorReport {
    /// True when every item is healthy after the self-check.
    pub fn all_ok(&self) -> bool {
        self.items.iter().all(|item| item.status.is_ok())
    }

    /// Items that are still failing after the self-check (R7.6).
    pub fn unresolved(&self) -> impl Iterator<Item = &CheckItem> {
        self.items.iter().filter(|item| item.status.is_fail())
    }
}

/// The repair record for one item that started as a fault.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RepairRecord {
    /// The name of the item that was repaired (or attempted).
    pub name: String,
    /// The subsystem the item covers.
    pub kind: CheckKind,
    /// The bounded-repair outcome.
    pub outcome: RepairOutcome,
}

/// The self-check system (닥터). Holds the set of probes to run (R7.1).
pub struct Doctor {
    probes: Vec<Box<dyn CheckProbe>>,
}

impl Doctor {
    /// Build a doctor from a set of probes. The probes are run in order.
    pub fn new(probes: Vec<Box<dyn CheckProbe>>) -> Self {
        Self { probes }
    }

    /// Diagnose every check item (R7.1). This is entirely read-only: no repair
    /// is attempted and no state is changed (R7.7).
    pub fn diagnose(&self) -> Vec<CheckItem> {
        self.probes
            .iter()
            .map(|probe| CheckItem {
                name: probe.name(),
                kind: probe.kind(),
                status: probe.check(),
                repaired: false,
            })
            .collect()
    }

    /// Attempt to repair one problem (R7.2, R7.3, R7.6, R7.8).
    ///
    /// * An already-healthy item is a no-op (`attempts = 0`, `repaired = true`).
    /// * An unrepairable problem (for example the safety gate) is never
    ///   modified: `attempts = 0`, guidance is produced, and the status is left
    ///   unchanged (R7.8).
    /// * A repairable problem is retried at most [`MAX_REPAIR_ATTEMPTS`] times,
    ///   re-checking after each attempt (R7.2, R7.3). If it still fails after
    ///   the last attempt, guidance is produced (R7.6).
    pub fn repair(&self, item: &CheckItem) -> RepairOutcome {
        let Some(probe) = self
            .probes
            .iter()
            .find(|probe| probe.name() == item.name && probe.kind() == item.kind)
        else {
            return RepairOutcome {
                attempts: 0,
                repaired: false,
                final_status: item.status.clone(),
                guidance: Some("점검 항목을 찾을 수 없어요. 자가 점검을 다시 실행해 주세요.".to_string()),
            };
        };
        self.repair_probe(probe.as_ref())
    }

    /// Run the full self-check: diagnose, repair repairable faults (bounded),
    /// re-check, and report (R7.1–R7.3, R7.6).
    ///
    /// Healthy items and unrepairable faults are never repaired; the safety
    /// gate is therefore never modified (R7.8). Warnings are reported as-is and
    /// do not trigger a repair.
    pub fn run(&self) -> DoctorReport {
        let mut items = Vec::with_capacity(self.probes.len());
        let mut repairs = Vec::new();
        for probe in &self.probes {
            let initial = probe.check();
            let mut item = CheckItem {
                name: probe.name(),
                kind: probe.kind(),
                status: initial.clone(),
                repaired: false,
            };
            if initial.is_fail() {
                let outcome = self.repair_probe(probe.as_ref());
                item.status = outcome.final_status.clone();
                item.repaired = outcome.repaired;
                repairs.push(RepairRecord {
                    name: probe.name(),
                    kind: probe.kind(),
                    outcome,
                });
            }
            items.push(item);
        }
        DoctorReport { items, repairs }
    }

    /// Shared bounded-repair loop used by both [`Doctor::repair`] and
    /// [`Doctor::run`].
    fn repair_probe(&self, probe: &dyn CheckProbe) -> RepairOutcome {
        // Re-check first so a stale item that is already healthy is a no-op.
        let current = probe.check();
        if current.is_ok() {
            return RepairOutcome {
                attempts: 0,
                repaired: true,
                final_status: CheckStatus::Ok,
                guidance: None,
            };
        }
        // Unrepairable problem: never modified, guidance only (R7.6, R7.8).
        if !probe.is_repairable() {
            return RepairOutcome {
                attempts: 0,
                repaired: false,
                final_status: current,
                guidance: Some(probe.guidance()),
            };
        }
        // Bounded repair: at most MAX_REPAIR_ATTEMPTS, rechecking each time
        // (R7.2, R7.3).
        let mut attempts = 0;
        loop {
            attempts += 1;
            probe.attempt_repair();
            let status = probe.check();
            if status.is_ok() {
                return RepairOutcome {
                    attempts,
                    repaired: true,
                    final_status: status,
                    guidance: None,
                };
            }
            if attempts >= MAX_REPAIR_ATTEMPTS {
                return RepairOutcome {
                    attempts,
                    repaired: false,
                    final_status: status,
                    guidance: Some(probe.guidance()),
                };
            }
        }
    }
}

#[cfg(test)]
mod tests;
