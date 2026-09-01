//! Unit tests for the durability harness (task 6).

use std::cell::Cell;

use super::*;
use crate::fakes::{FakePorts, ScenarioShape};
use crate::logging::{HistoryStore, PipelineEvent, SqliteHistoryStore, StoreError};
use crate::safety::{SendGrade, SendTicket};

/// A small, deterministic plan for fast unit runs.
fn small_plan(seed: u64, sandbox: std::path::PathBuf) -> HarnessPlan {
    HarnessPlan {
        auto_reply_runs: 40,
        geeknews_runs: 10,
        seed,
        sandbox,
    }
}

/// A journal wrapper that panics on the Nth `append` (1-based) and otherwise
/// delegates. Used to prove a per-run panic is absorbed as a single failure.
struct PanicOnceStore {
    inner: SqliteHistoryStore,
    panic_on: usize,
    count: Cell<usize>,
}

impl PanicOnceStore {
    fn new(panic_on: usize) -> Self {
        Self {
            inner: SqliteHistoryStore::open_in_memory().unwrap(),
            panic_on,
            count: Cell::new(0),
        }
    }
}

impl HistoryStore for PanicOnceStore {
    fn append(&self, ev: PipelineEvent) -> Result<(), StoreError> {
        let n = self.count.get() + 1;
        self.count.set(n);
        if n == self.panic_on {
            panic!("scripted journal panic on append {n}");
        }
        self.inner.append(ev)
    }

    fn recent(&self, limit: usize) -> Result<Vec<PipelineEvent>, StoreError> {
        self.inner.recent(limit)
    }
}

/// A journal wrapper that cancels a token on the Nth `append`, to exercise
/// cooperative mid-run cancellation.
struct CancelAfterStore {
    inner: SqliteHistoryStore,
    token: CancelToken,
    cancel_after: usize,
    count: Cell<usize>,
}

impl CancelAfterStore {
    fn new(token: CancelToken, cancel_after: usize) -> Self {
        Self {
            inner: SqliteHistoryStore::open_in_memory().unwrap(),
            token,
            cancel_after,
            count: Cell::new(0),
        }
    }
}

impl HistoryStore for CancelAfterStore {
    fn append(&self, ev: PipelineEvent) -> Result<(), StoreError> {
        let n = self.count.get() + 1;
        self.count.set(n);
        if n == self.cancel_after {
            self.token.cancel();
        }
        self.inner.append(ev)
    }

    fn recent(&self, limit: usize) -> Result<Vec<PipelineEvent>, StoreError> {
        self.inner.recent(limit)
    }
}

#[test]
fn preflight_passes_for_a_fresh_fake_world() {
    let ports = FakePorts::from_seed(1, ScenarioShape::new(3, 10, 1, 8));
    let journal = SqliteHistoryStore::open_in_memory().unwrap();
    let harness = DurabilityHarness::new(ports, &journal);
    assert!(harness.preflight().is_ok());
}

#[test]
fn preflight_refuses_when_real_send_path_was_hit() {
    let ports = FakePorts::from_seed(1, ScenarioShape::new(2, 5, 0, 4));
    // Simulate a call that reached the real send path before starting.
    let ticket = SendTicket::for_test(1, SendGrade::Fake);
    let _ = ports.blocked_real_send.send_text(&ticket, "leak");

    let journal = SqliteHistoryStore::open_in_memory().unwrap();
    let mut harness = DurabilityHarness::new(ports, &journal);
    assert!(harness.preflight().is_err());

    // run must not start; it aborts without any attempts.
    let plan = small_plan(1, harness.ports().sandbox.path().to_path_buf());
    let report = harness.run(&plan, &CancelToken::new());
    match report.verdict {
        HarnessVerdict::Aborted {
            completed, not_run, ..
        } => {
            assert_eq!(completed, 0);
            assert_eq!(not_run, plan.auto_reply_runs + plan.geeknews_runs);
        }
        other => panic!("expected Aborted, got {other:?}"),
    }
    assert_eq!(report.auto_reply.attempted, 0);
}

#[test]
fn run_makes_exactly_the_configured_attempts_and_passes() {
    let ports = FakePorts::from_seed(7, ScenarioShape::new(4, 12, 1, 8));
    let journal = SqliteHistoryStore::open_in_memory().unwrap();
    let sandbox = ports.sandbox.path().to_path_buf();
    let mut harness = DurabilityHarness::new(ports, &journal);
    let plan = small_plan(7, sandbox);

    let report = harness.run(&plan, &CancelToken::new());

    assert_eq!(report.auto_reply.attempted, plan.auto_reply_runs);
    assert_eq!(report.geeknews.attempted, plan.geeknews_runs);
    // Real sends and egress stay at zero.
    assert_eq!(report.real_sends, 0);
    assert_eq!(report.network_egress, 0);
    // Confirmed sends match commit/success journal records exactly.
    assert_eq!(report.confirmed_sends, report.commit_success_records);
    // Some sends were confirmed (the fake world has valid rooms).
    assert!(report.confirmed_sends > 0);
    // The gate decision always agreed with the configuration.
    assert_eq!(report.gate_mismatch, 0);
    assert_eq!(report.verdict, HarnessVerdict::Pass);
    // The seed is echoed for reproducibility.
    assert_eq!(report.seed, 7);
}

#[test]
fn geeknews_batch_confirms_one_send_per_run() {
    let ports = FakePorts::from_seed(3, ScenarioShape::new(2, 8, 0, 4));
    let journal = SqliteHistoryStore::open_in_memory().unwrap();
    let sandbox = ports.sandbox.path().to_path_buf();
    let mut harness = DurabilityHarness::new(ports, &journal);
    let plan = small_plan(3, sandbox);

    let report = harness.run(&plan, &CancelToken::new());
    // Every GeekNews run lands in a fresh slot with fresh items, so all post.
    assert_eq!(report.geeknews.allowed, plan.geeknews_runs);
}

#[test]
fn determinism_same_seed_same_aggregates() {
    let sandbox_a;
    let report_a = {
        let ports = FakePorts::from_seed(42, ScenarioShape::new(3, 10, 1, 8));
        sandbox_a = ports.sandbox.path().to_path_buf();
        let journal = SqliteHistoryStore::open_in_memory().unwrap();
        let mut harness = DurabilityHarness::new(ports, &journal);
        harness.run(&small_plan(42, sandbox_a.clone()), &CancelToken::new())
    };
    let report_b = {
        let ports = FakePorts::from_seed(42, ScenarioShape::new(3, 10, 1, 8));
        let sandbox_b = ports.sandbox.path().to_path_buf();
        let journal = SqliteHistoryStore::open_in_memory().unwrap();
        let mut harness = DurabilityHarness::new(ports, &journal);
        harness.run(&small_plan(42, sandbox_b), &CancelToken::new())
    };

    // Non-time aggregates are identical across runs (R1.8).
    assert_eq!(report_a.auto_reply.allowed, report_b.auto_reply.allowed);
    assert_eq!(report_a.auto_reply.fenced, report_b.auto_reply.fenced);
    assert_eq!(
        report_a.auto_reply.fenced_by_reason,
        report_b.auto_reply.fenced_by_reason
    );
    assert_eq!(report_a.confirmed_sends, report_b.confirmed_sends);
    assert_eq!(report_a.geeknews.allowed, report_b.geeknews.allowed);
}

#[test]
fn a_single_panic_is_absorbed_and_the_batch_continues() {
    let ports = FakePorts::from_seed(5, ScenarioShape::new(3, 10, 1, 8));
    let sandbox = ports.sandbox.path().to_path_buf();
    // Panic on the very first append (run 0's detect, before any send), so the
    // confirmed==commit invariant is unaffected.
    let journal = PanicOnceStore::new(1);
    let mut harness = DurabilityHarness::new(ports, &journal);
    let plan = small_plan(5, sandbox);

    let report = harness.run(&plan, &CancelToken::new());

    // The batch still made every attempt (R1.1) and counted the panic (R1.13).
    assert_eq!(report.auto_reply.attempted, plan.auto_reply_runs);
    assert_eq!(report.auto_reply.unhandled_panics, 1);
    // The confirmed/commit invariant still holds for the surviving runs.
    assert_eq!(report.confirmed_sends, report.commit_success_records);
    // A panic means the run is not a pass.
    match report.verdict {
        HarnessVerdict::Fail(conditions) => {
            assert!(conditions.iter().any(|c| c.name == "no_unhandled_panics"));
        }
        other => panic!("expected Fail, got {other:?}"),
    }
}

#[test]
fn cancel_before_start_aborts_with_zero_completed() {
    let ports = FakePorts::from_seed(9, ScenarioShape::new(2, 8, 0, 4));
    let journal = SqliteHistoryStore::open_in_memory().unwrap();
    let sandbox = ports.sandbox.path().to_path_buf();
    let mut harness = DurabilityHarness::new(ports, &journal);
    let plan = small_plan(9, sandbox);

    let cancel = CancelToken::new();
    cancel.cancel();
    let report = harness.run(&plan, &cancel);

    match report.verdict {
        HarnessVerdict::Aborted { completed, .. } => assert_eq!(completed, 0),
        other => panic!("expected Aborted, got {other:?}"),
    }
    assert_eq!(report.auto_reply.attempted, 0);
}

#[test]
fn cooperative_cancellation_stops_partway() {
    let ports = FakePorts::from_seed(11, ScenarioShape::new(4, 12, 1, 8));
    let sandbox = ports.sandbox.path().to_path_buf();
    let token = CancelToken::new();
    // Cancel after a handful of journal appends (mid auto-reply batch).
    let journal = CancelAfterStore::new(token.clone(), 5);
    let mut harness = DurabilityHarness::new(ports, &journal);
    let plan = small_plan(11, sandbox);

    let report = harness.run(&plan, &token);

    match report.verdict {
        HarnessVerdict::Aborted {
            completed, not_run, ..
        } => {
            assert!(completed > 0, "some runs should have completed");
            assert!(
                completed < plan.auto_reply_runs + plan.geeknews_runs,
                "not every run should have completed"
            );
            assert_eq!(
                not_run,
                plan.auto_reply_runs + plan.geeknews_runs - completed
            );
        }
        other => panic!("expected Aborted, got {other:?}"),
    }
    // Even when cancelled, no real sends or egress happened.
    assert_eq!(report.real_sends, 0);
    assert_eq!(report.network_egress, 0);
}

#[test]
fn judge_pass_when_all_conditions_hold() {
    let report = HarnessReport {
        auto_reply: BatchTally {
            attempted: 1000,
            ..Default::default()
        },
        geeknews: BatchTally {
            attempted: 100,
            ..Default::default()
        },
        seed: 0,
        real_sends: 0,
        network_egress: 0,
        confirmed_sends: 600,
        commit_success_records: 600,
        gate_mismatch: 0,
        verdict: HarnessVerdict::Pass,
    };
    let plan = HarnessPlan::new(0, std::env::temp_dir());
    assert_eq!(judge(&report, &plan), HarnessVerdict::Pass);
}

#[test]
fn judge_fails_and_names_each_violated_condition() {
    let report = HarnessReport {
        auto_reply: BatchTally {
            attempted: 999, // wrong count
            unhandled_panics: 2,
            ..Default::default()
        },
        geeknews: BatchTally {
            attempted: 100,
            ..Default::default()
        },
        seed: 0,
        real_sends: 1,     // must be 0
        network_egress: 3, // must be 0
        confirmed_sends: 5,
        commit_success_records: 4, // mismatch
        gate_mismatch: 1,
        verdict: HarnessVerdict::Pass,
    };
    let plan = HarnessPlan::new(0, std::env::temp_dir());
    match judge(&report, &plan) {
        HarnessVerdict::Fail(conditions) => {
            let names: Vec<&str> = conditions.iter().map(|c| c.name).collect();
            assert!(names.contains(&"real_sends_zero"));
            assert!(names.contains(&"network_egress_zero"));
            assert!(names.contains(&"no_unhandled_panics"));
            assert!(names.contains(&"auto_reply_attempts_match"));
            assert!(names.contains(&"confirmed_equals_commit"));
            assert!(names.contains(&"gate_decision_consistent"));
            // Every failed condition carries a plain-language next step.
            assert!(conditions.iter().all(|c| !c.next_step.is_empty()));
        }
        other => panic!("expected Fail, got {other:?}"),
    }
}

#[test]
fn check_invariants_runs_at_least_two_hundred_scenarios_and_passes() {
    let ports = FakePorts::from_seed(0, ScenarioShape::new(2, 6, 0, 4));
    let journal = SqliteHistoryStore::open_in_memory().unwrap();
    let mut harness = DurabilityHarness::new(ports, &journal);

    // Ask for fewer than the floor; it must still run at least 200 (R12.17).
    let report = harness.check_invariants(1234, 10);
    assert_eq!(report.per_criterion, MIN_SCENARIOS_PER_CRITERION);
    assert_eq!(report.scenarios_run, MIN_SCENARIOS_PER_CRITERION);
    assert!(report.passed(), "violations: {:?}", report.violations);
}

#[test]
fn cancel_token_shares_state_across_clones() {
    let token = CancelToken::new();
    let clone = token.clone();
    assert!(!token.is_cancelled());
    clone.cancel();
    assert!(token.is_cancelled());
}
