//! Property-based tests for the durability harness (task 6.2).
//!
//! Feature: kakao-agent-live-ops
//! Property 3: 가짜 경로만·실제 전송 0·확정 전송 계수 == commit/success 기록 수
//!   (fake paths only, zero real sends, confirmed-send count == commit/success
//!   journal records)
//! Property 6: 집계 정확성·합격 판정 동치·불합격 조건 문구
//!   (aggregation accuracy, pass-verdict equivalence, failed-condition wording)
//! Property 7: 검증 결정성 — 시간 제외 동일
//!   (verification determinism — identical non-time aggregates)
//! Property 8: 시험 격리 — 실사용 상태 불변·샌드박스 하위 쓰기
//!   (test isolation — production state unchanged, writes stay under the sandbox)
//!
//! Every test asserts the three-layer "zero real sends" defense holds: the fake
//! output is the only send path, the [`BlockingRealSendPort`] tripwire counter
//! stays at zero, and the [`ForbiddenNetwork`] egress count stays at zero.
//!
//! The harness itself ([`crate::durability`]) drives the assembly production
//! uses, so these properties observe real behavior rather than a fake gate:
//! [`FakeMessageSource`] → [`SendGuard::authorize`] → [`FakeProvider`] →
//! [`FakeSendPort`] → [`HistoryStore::append`] for automatic replies, and
//! [`run_geeknews_post`] with fake feed/sender/cursor-store for GeekNews.
//!
//! Validates: Requirements 1.1, 1.2, 1.3, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 1.11,
//! 1.13, 4.6, 12.2, 12.16, 12.18

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use openkakao_cli::durability::{
    judge, BatchTally, CancelToken, DurabilityHarness, HarnessPlan, HarnessReport, HarnessVerdict,
};
use openkakao_cli::fakes::{FakePorts, ScenarioShape};
use openkakao_cli::logging::{HistoryStore, SqliteHistoryStore, Stage, StageStatus};
use openkakao_cli::ports::NetworkPort;
use proptest::prelude::*;

// ===========================================================================
// Shared strategies and helpers.
// ===========================================================================

/// A bounded scenario shape. Rooms/messages/images are kept small so a 200-case
/// suite stays fast; the properties do not depend on the size. The safety-combo
/// dial spans its full range so the provider count varies.
fn shape_strategy() -> impl Strategy<Value = ScenarioShape> {
    (
        1usize..=8,
        1usize..=20,
        0usize..=3,
        0usize..=ScenarioShape::MAX_SAFETY_COMBO,
    )
        .prop_map(|(rooms, msgs, imgs, combo)| ScenarioShape::new(rooms, msgs, imgs, combo))
}

/// Build a small plan (fast per-case batches, per task 6.2 guidance).
fn plan_for(seed: u64, sandbox: PathBuf, auto: usize, geek: usize) -> HarnessPlan {
    HarnessPlan {
        auto_reply_runs: auto,
        geeknews_runs: geek,
        seed,
        sandbox,
    }
}

/// Run one full batch against a fresh fake world and return the owned report.
fn run_report(seed: u64, shape: ScenarioShape, auto: usize, geek: usize) -> HarnessReport {
    let ports = FakePorts::from_seed(seed, shape);
    let sandbox = ports.sandbox.path().to_path_buf();
    let journal = SqliteHistoryStore::open_in_memory().expect("in-memory journal");
    let mut harness = DurabilityHarness::new(ports, &journal);
    harness.run(&plan_for(seed, sandbox, auto, geek), &CancelToken::new())
}

/// Zero out the wall-time fields so only the deterministic aggregates remain
/// (R1.8 excludes elapsed / P50 / P95 from the reproducibility guarantee).
fn without_time(tally: &BatchTally) -> BatchTally {
    let mut clone = tally.clone();
    clone.p50_ms = 0;
    clone.p95_ms = 0;
    clone.elapsed_ms = 0;
    clone
}

/// A flat snapshot of a directory: every file mapped to its bytes.
fn snapshot_dir(root: &Path) -> BTreeMap<PathBuf, Vec<u8>> {
    let mut out = BTreeMap::new();
    if let Ok(entries) = fs::read_dir(root) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_file() {
                out.insert(path.clone(), fs::read(&path).unwrap_or_default());
            }
        }
    }
    out
}

/// Whether `path` sits under the system temp directory (R1.9).
fn under_temp(path: &Path) -> bool {
    match (path.canonicalize(), std::env::temp_dir().canonicalize()) {
        (Ok(p), Ok(tmp)) => p.starts_with(&tmp),
        _ => false,
    }
}

// ===========================================================================
// Property 3 — fake paths only, zero real sends, confirmed == commit.
// ===========================================================================

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// Property 3: with every outward path a fake, a full batch confirms zero
    /// real sends and zero network egress, and the confirmed-send count equals
    /// the number of `commit`/`success` journal records — verified against both
    /// the report's own count and an independent recount off the journal
    /// (R1.2, R12.2, R12.16).
    ///
    /// Validates: Requirements 1.1, 1.2, 1.3, 1.11, 4.6, 12.2, 12.16
    #[test]
    fn fake_only_zero_real_sends_and_confirmed_equals_commit(
        seed in any::<u64>(),
        shape in shape_strategy(),
        auto in 20usize..=32,
        geek in 5usize..=8,
    ) {
        let ports = FakePorts::from_seed(seed, shape);
        let sandbox = ports.sandbox.path().to_path_buf();
        let journal = SqliteHistoryStore::open_in_memory().expect("in-memory journal");
        let mut harness = DurabilityHarness::new(ports, &journal);

        // Fake paths only: preflight passes exactly because every outward path
        // is a fake and nothing has leaked before start (R1.11).
        prop_assert!(harness.preflight().is_ok());

        let plan = plan_for(seed, sandbox, auto, geek);
        let report = harness.run(&plan, &CancelToken::new());

        // Exactly the configured attempts were made (R1.1).
        prop_assert_eq!(report.auto_reply.attempted, auto);
        prop_assert_eq!(report.geeknews.attempted, geek);

        // Zero real sends and zero egress: report values and the observation
        // layer (tripwire counters) agree.
        prop_assert_eq!(report.real_sends, 0);
        prop_assert_eq!(report.network_egress, 0);
        prop_assert_eq!(harness.ports().blocked_real_send.blocked_count(), 0);
        prop_assert_eq!(harness.ports().net.egress_count(), 0);

        // Confirmed sends == commit/success journal records (R12.16).
        prop_assert_eq!(report.confirmed_sends, report.commit_success_records);

        // An independent recount straight from the journal agrees with the
        // report's tally — the definition of a confirmed send.
        let commit_success = journal
            .recent(usize::MAX)
            .expect("recent")
            .iter()
            .filter(|e| e.stage == Stage::Commit && e.status == StageStatus::Success)
            .count();
        prop_assert_eq!(report.commit_success_records, commit_success);

        // The world stayed isolated across the whole run.
        prop_assert!(harness.ports().assert_isolated().is_ok());
    }
}

// ===========================================================================
// Property 6 — aggregation accuracy, pass-verdict equivalence, wording.
// ===========================================================================

/// A judge input: an arbitrary report whose seven pass conditions each vary
/// independently, plus the plan whose attempt counts it is judged against.
fn judge_input_strategy() -> impl Strategy<Value = (HarnessReport, HarnessPlan)> {
    (
        20usize..=32,   // plan.auto_reply_runs
        20usize..=32,   // report.auto_reply.attempted
        5usize..=8,     // plan.geeknews_runs
        5usize..=8,     // report.geeknews.attempted
        0usize..=3,     // real_sends
        0usize..=3,     // network_egress
        0usize..=2,     // auto panics
        0usize..=2,     // geek panics
        0usize..=6,     // confirmed_sends
        0usize..=6,     // commit_success_records
        0usize..=3,     // gate_mismatch
        any::<u64>(),   // seed
    )
        .prop_map(
            |(
                auto_runs,
                auto_attempted,
                geek_runs,
                geek_attempted,
                real_sends,
                egress,
                auto_panics,
                geek_panics,
                confirmed,
                commit,
                gate_mismatch,
                seed,
            )| {
                let plan = HarnessPlan {
                    auto_reply_runs: auto_runs,
                    geeknews_runs: geek_runs,
                    seed,
                    sandbox: std::env::temp_dir(),
                };
                let report = HarnessReport {
                    auto_reply: BatchTally {
                        attempted: auto_attempted,
                        unhandled_panics: auto_panics,
                        ..Default::default()
                    },
                    geeknews: BatchTally {
                        attempted: geek_attempted,
                        unhandled_panics: geek_panics,
                        ..Default::default()
                    },
                    seed,
                    real_sends,
                    network_egress: egress,
                    confirmed_sends: confirmed,
                    commit_success_records: commit,
                    gate_mismatch,
                    verdict: HarnessVerdict::Pass, // placeholder; judge ignores it
                };
                (report, plan)
            },
        )
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// Property 6 (aggregation accuracy): after a real batch, the per-batch
    /// tallies are internally consistent. Every automatic-reply attempt is
    /// either allowed, fenced, or an absorbed panic; the fenced breakdown sums
    /// to the fenced total; and every GeekNews attempt is a post, a no-send, or
    /// an absorbed panic (R1.1, R1.5, R1.13).
    ///
    /// Validates: Requirements 1.1, 1.5, 1.13, 12.2
    #[test]
    fn batch_tallies_are_internally_consistent(
        seed in any::<u64>(),
        shape in shape_strategy(),
        auto in 20usize..=32,
        geek in 5usize..=8,
    ) {
        let ports = FakePorts::from_seed(seed, shape);
        let sandbox = ports.sandbox.path().to_path_buf();
        let journal = SqliteHistoryStore::open_in_memory().expect("in-memory journal");
        let mut harness = DurabilityHarness::new(ports, &journal);
        let plan = plan_for(seed, sandbox, auto, geek);
        let report = harness.run(&plan, &CancelToken::new());

        // Attempts match the plan exactly (R1.1).
        prop_assert_eq!(report.auto_reply.attempted, auto);
        prop_assert_eq!(report.geeknews.attempted, geek);

        // Auto-reply: each attempt is allowed | fenced | panic.
        prop_assert_eq!(
            report.auto_reply.allowed
                + report.auto_reply.fenced
                + report.auto_reply.unhandled_panics,
            report.auto_reply.attempted
        );
        // The fenced breakdown accounts for every fence.
        let fenced_sum: usize = report.auto_reply.fenced_by_reason.values().sum();
        prop_assert_eq!(fenced_sum, report.auto_reply.fenced);

        // GeekNews: each attempt is a post | no-send (a stage failure) | panic.
        let geek_no_send: usize = report.geeknews.stage_failures.values().sum();
        prop_assert_eq!(
            report.geeknews.allowed + geek_no_send + report.geeknews.unhandled_panics,
            report.geeknews.attempted
        );

        // Zero real sends / egress throughout.
        prop_assert_eq!(harness.ports().blocked_real_send.blocked_count(), 0);
        prop_assert_eq!(harness.ports().net.egress_count(), 0);
    }

    /// Property 6 (pass-verdict equivalence + wording): the pure [`judge`]
    /// returns `Pass` exactly when all seven pass conditions hold, and returns
    /// `Fail` with at least one condition otherwise. Every reported
    /// [`FailedCondition`] carries a name, an observed value, and a non-empty
    /// Plain_Language next step (R1.6, R1.7). `judge` never aborts.
    ///
    /// Validates: Requirements 1.6, 1.7
    #[test]
    fn judge_passes_iff_all_conditions_hold_with_next_steps(
        (report, plan) in judge_input_strategy(),
    ) {
        let all_hold = report.real_sends == 0
            && report.network_egress == 0
            && (report.auto_reply.unhandled_panics + report.geeknews.unhandled_panics) == 0
            && report.auto_reply.attempted == plan.auto_reply_runs
            && report.geeknews.attempted == plan.geeknews_runs
            && report.confirmed_sends == report.commit_success_records
            && report.gate_mismatch == 0;

        match judge(&report, &plan) {
            HarnessVerdict::Pass => {
                prop_assert!(all_hold, "Pass returned but a condition failed");
            }
            HarnessVerdict::Fail(conditions) => {
                prop_assert!(!all_hold, "Fail returned but every condition held");
                prop_assert!(!conditions.is_empty());
                for c in &conditions {
                    prop_assert!(!c.name.is_empty(), "condition name is empty");
                    prop_assert!(!c.observed.is_empty(), "observed value is empty");
                    prop_assert!(
                        !c.next_step.is_empty(),
                        "next_step empty for condition {}",
                        c.name
                    );
                }
            }
            HarnessVerdict::Aborted { .. } => {
                prop_assert!(false, "judge must never return Aborted");
            }
        }
    }
}

// ===========================================================================
// Property 7 — verification determinism (non-time aggregates).
// ===========================================================================

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// Property 7: the same seed and the same fake input produce identical
    /// non-time aggregates across two independent runs. Only the wall-time
    /// fields (elapsed, P50, P95) are allowed to differ (R1.8).
    ///
    /// Validates: Requirements 1.8, 12.2
    #[test]
    fn same_seed_yields_identical_non_time_aggregates(
        seed in any::<u64>(),
        shape in shape_strategy(),
        auto in 20usize..=32,
        geek in 5usize..=8,
    ) {
        let a = run_report(seed, shape, auto, geek);
        let b = run_report(seed, shape, auto, geek);

        // Per-batch aggregates match once wall-time fields are stripped.
        prop_assert_eq!(without_time(&a.auto_reply), without_time(&b.auto_reply));
        prop_assert_eq!(without_time(&a.geeknews), without_time(&b.geeknews));

        // Top-level aggregates match.
        prop_assert_eq!(a.confirmed_sends, b.confirmed_sends);
        prop_assert_eq!(a.commit_success_records, b.commit_success_records);
        prop_assert_eq!(a.gate_mismatch, b.gate_mismatch);
        prop_assert_eq!(a.real_sends, b.real_sends);
        prop_assert_eq!(a.network_egress, b.network_egress);
        prop_assert_eq!(a.seed, b.seed);
        prop_assert_eq!(a.verdict, b.verdict);

        // The seed is echoed for reproducibility (R1.8).
        prop_assert_eq!(a.seed, seed);

        // Determinism never comes at the cost of a real send or egress.
        prop_assert_eq!(a.real_sends, 0);
        prop_assert_eq!(a.network_egress, 0);
        prop_assert_eq!(b.real_sends, 0);
        prop_assert_eq!(b.network_egress, 0);
    }
}

// ===========================================================================
// Property 8 — test isolation (sandbox writes, production state unchanged).
// ===========================================================================

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// Property 8: a full batch keeps the sandbox under the system temp
    /// directory and leaves production state outside the sandbox byte-for-byte
    /// unchanged. A canary "production" directory populated before the run is
    /// identical afterwards (R1.9). Real sends and egress stay at zero.
    ///
    /// Validates: Requirements 1.9, 1.10, 12.2, 12.18
    #[test]
    fn writes_stay_under_sandbox_and_production_state_unchanged(
        seed in any::<u64>(),
        shape in shape_strategy(),
        auto in 20usize..=32,
        geek in 5usize..=8,
    ) {
        // A stand-in for real user state that must not be touched.
        let production = tempfile::TempDir::new().expect("production dir");
        let canary = production.path().join("real_state.txt");
        fs::write(&canary, b"production-state-v1").expect("write canary");
        let before = snapshot_dir(production.path());

        let ports = FakePorts::from_seed(seed, shape);
        let sandbox = ports.sandbox.path().to_path_buf();

        // The sandbox sits under the system temp directory (R1.9).
        prop_assert!(under_temp(&sandbox), "sandbox {sandbox:?} not under temp dir");

        let journal = SqliteHistoryStore::open_in_memory().expect("in-memory journal");
        let mut harness = DurabilityHarness::new(ports, &journal);
        let plan = plan_for(seed, sandbox.clone(), auto, geek);
        let report = harness.run(&plan, &CancelToken::new());

        // Tripwires stay at zero and the world remains isolated.
        prop_assert_eq!(report.real_sends, 0);
        prop_assert_eq!(report.network_egress, 0);
        prop_assert_eq!(harness.ports().blocked_real_send.blocked_count(), 0);
        prop_assert_eq!(harness.ports().net.egress_count(), 0);
        prop_assert!(harness.ports().assert_isolated().is_ok());

        // The sandbox is still under temp after the run.
        prop_assert!(under_temp(harness.ports().sandbox.path()));

        // Production state outside the sandbox is unchanged.
        let after = snapshot_dir(production.path());
        prop_assert_eq!(before, after);
        prop_assert_eq!(
            fs::read(&canary).expect("read canary"),
            b"production-state-v1".to_vec()
        );
    }
}
