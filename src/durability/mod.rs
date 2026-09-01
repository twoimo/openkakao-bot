//! The durability harness (내구성검증기) — bulk fake-adapter verification (R1).
//!
//! A maintainer must be able to run automatic-reply processing 1000 times and
//! GeekNews posting 100 times at once, aggregate the result, and confirm the
//! system survives bulk processing — all with **zero** real KakaoTalk sends and
//! **zero** outbound network egress (R1). The harness wires a fully fake world
//! (see [`crate::fakes::FakePorts`]) and runs the same assembly production uses,
//! so nothing about the safety judgement is faked:
//!
//! * One automatic-reply run flows through exactly the production seam —
//!   [`FakeMessageSource`](crate::fakes::FakeMessageSource) →
//!   [`SendGuard::authorize`](crate::safety::SendGuard::authorize) →
//!   [`FakeProvider`](crate::fakes::FakeProvider) →
//!   [`FakeSendPort`](crate::fakes::FakeSendPort) →
//!   [`HistoryStore::append`](crate::logging::HistoryStore::append) (R1.3).
//! * The GeekNews batch calls [`crate::geeknews::run_geeknews_post`] with a fake
//!   [`FeedSource`](crate::geeknews::FeedSource) /
//!   [`Sender`](crate::geeknews::Sender) /
//!   [`CursorStore`](crate::geeknews::CursorStore) (R12.5).
//!
//! Three structural guarantees hold here:
//!
//! * **Zero real sends, zero egress (R1.2, R1.12, R12.2).** The report records
//!   the [`BlockingRealSendPort`](crate::fakes::BlockingRealSendPort) count as
//!   `real_sends` and the [`ForbiddenNetwork`](crate::fakes::ForbiddenNetwork)
//!   egress as `network_egress`; both must be 0. The harness never routes to
//!   either, and [`judge`] fails the run if either is non-zero.
//! * **Confirmed sends == commit/success journal records (R12.16).** A
//!   confirmed send is written to the journal as a `commit`/`success` record at
//!   exactly one place, so the two counts agree by construction, and [`judge`]
//!   verifies it.
//! * **A panic is absorbed as a single failure (R1.13).** Each processing runs
//!   inside [`std::panic::catch_unwind`]; a panic is counted as one unhandled
//!   panic and the remaining processings continue. Cancellation is cooperative:
//!   a [`CancelToken`] is checked at every processing boundary (R1.10).

use std::collections::BTreeMap;
use std::panic::{self, AssertUnwindSafe};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Instant;

use crate::experiment::{Prompt, Provider};
use crate::fakes::{FakePorts, ScenarioShape};
use crate::geeknews::{self, CursorStore, FeedSource, GeekNewsCursor, PostOutcome, Sender};
use crate::live_sample::{resolve_target, LiveSampleCollector, SqliteSampleStore};
use crate::logging::{FlowKind, HistoryStore, PipelineEvent, SqliteHistoryStore, Stage, StageStatus};
use crate::ports::{AxReadPort, Clock, MessageSource, NetworkPort, SendPort};
use crate::safety::{
    DefaultSafetyGate, FenceReason, GuardFence, Origin, OwnerNameStatus, ProfileView, SafetyConfig,
    SendGrade, SendGuard, SendIntent, SendRequest,
};

use crate::breaker::{EmergencyBreaker, SqliteBreakerStore};

use std::cell::Cell;

/// The default number of automatic-reply runs in a full batch (R1.1).
pub const DEFAULT_AUTO_REPLY_RUNS: usize = 1_000;
/// The default number of GeekNews runs in a full batch (R1.1).
pub const DEFAULT_GEEKNEWS_RUNS: usize = 100;
/// The floor on the number of randomized scenarios run per invariant criterion
/// (R12.17).
pub const MIN_SCENARIOS_PER_CRITERION: usize = 200;

// ---------------------------------------------------------------------------
// CancelToken
// ---------------------------------------------------------------------------

/// A cooperative cancellation flag checked at every processing boundary (R1.10).
///
/// Cloning shares the same underlying flag, so a caller can hold one clone and
/// hand another to the harness.
#[derive(Debug, Clone, Default)]
pub struct CancelToken(Arc<AtomicBool>);

impl CancelToken {
    /// A fresh, not-yet-cancelled token.
    pub fn new() -> Self {
        Self(Arc::new(AtomicBool::new(false)))
    }

    /// Request cancellation. The next boundary check stops the harness.
    pub fn cancel(&self) {
        self.0.store(true, Ordering::SeqCst);
    }

    /// Whether cancellation has been requested.
    pub fn is_cancelled(&self) -> bool {
        self.0.load(Ordering::SeqCst)
    }
}

// ---------------------------------------------------------------------------
// Plan and tallies
// ---------------------------------------------------------------------------

/// What one durability run should do (R1.1).
#[derive(Debug, Clone)]
pub struct HarnessPlan {
    /// How many automatic-reply runs to attempt (default 1000).
    pub auto_reply_runs: usize,
    /// How many GeekNews runs to attempt (default 100).
    pub geeknews_runs: usize,
    /// The deterministic seed driving the fake world and jitter (R1.8).
    pub seed: u64,
    /// The sandbox root the harness is allowed to write under (R1.9). Every
    /// filesystem write stays under here.
    pub sandbox: PathBuf,
}

impl HarnessPlan {
    /// A full-size plan (1000 / 100) with the given seed and sandbox.
    pub fn new(seed: u64, sandbox: impl Into<PathBuf>) -> Self {
        Self {
            auto_reply_runs: DEFAULT_AUTO_REPLY_RUNS,
            geeknews_runs: DEFAULT_GEEKNEWS_RUNS,
            seed,
            sandbox: sandbox.into(),
        }
    }
}

impl Default for HarnessPlan {
    fn default() -> Self {
        Self::new(0, std::env::temp_dir())
    }
}

/// The aggregate for one batch (automatic-reply or GeekNews), reported
/// separately (R1.5).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct BatchTally {
    /// Total processings attempted (counts success and failure alike, R1.1).
    pub attempted: usize,
    /// How many processings the guard authorized (a ticket was issued).
    pub allowed: usize,
    /// How many processings were fenced by the guard.
    pub fenced: usize,
    /// Fenced count grouped by machine-readable reason code.
    pub fenced_by_reason: BTreeMap<&'static str, usize>,
    /// Stage-failure count grouped by stage name.
    pub stage_failures: BTreeMap<&'static str, usize>,
    /// How many calls reached the blocked real-send path (must be 0).
    pub blocked_real_send_attempts: usize,
    /// How many processings ended in an unhandled panic (R1.13).
    pub unhandled_panics: usize,
    /// Median per-processing wall time in milliseconds (R1.5).
    pub p50_ms: u64,
    /// 95th-percentile per-processing wall time in milliseconds (R1.5).
    pub p95_ms: u64,
    /// Total batch wall time in milliseconds.
    pub elapsed_ms: u64,
}

/// The full durability report (R1.5, R1.6). `real_sends` and `network_egress`
/// must both be 0.
#[derive(Debug, Clone)]
pub struct HarnessReport {
    /// The automatic-reply batch aggregate.
    pub auto_reply: BatchTally,
    /// The GeekNews batch aggregate.
    pub geeknews: BatchTally,
    /// The seed used, echoed for reproducibility (R1.8).
    pub seed: u64,
    /// Real KakaoTalk sends — must be 0 (R1.2, R12.2).
    pub real_sends: usize,
    /// Outbound network egress — must be 0 (R1.12, R12.9).
    pub network_egress: usize,
    /// Total confirmed sends across both batches (R12.16).
    pub confirmed_sends: usize,
    /// Total `commit`/`success` journal records — must equal `confirmed_sends`.
    pub commit_success_records: usize,
    /// Cases where the guard's authorize decision disagreed with a fully-valid
    /// safety configuration — must be 0 (R1.6).
    pub gate_mismatch: usize,
    /// The verdict.
    pub verdict: HarnessVerdict,
}

/// The overall verdict of a durability run (R1.6, R1.7, R1.10, R1.11).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum HarnessVerdict {
    /// Every pass condition held.
    Pass,
    /// One or more conditions failed; each carries a plain-language explanation.
    Fail(Vec<FailedCondition>),
    /// The batch did not run to completion (preflight refused, or cancelled).
    Aborted {
        /// Processings completed before the abort.
        completed: usize,
        /// Processings that were not run.
        not_run: usize,
        /// Plain-language reasons for the abort.
        reasons: Vec<String>,
    },
}

/// One failed pass condition: its name, the measured value, and what to do next
/// (R1.7).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FailedCondition {
    /// A stable condition name.
    pub name: &'static str,
    /// The measured value that failed the condition.
    pub observed: String,
    /// One plain-language next action for the user.
    pub next_step: String,
}

// ---------------------------------------------------------------------------
// Invariant report (R12.17, R12.18)
// ---------------------------------------------------------------------------

/// The result of running randomized scenarios against the harness invariants
/// (R12.17).
#[derive(Debug, Clone, Default)]
pub struct InvariantReport {
    /// How many randomized scenarios were run in total.
    pub scenarios_run: usize,
    /// How many scenarios were run per criterion.
    pub per_criterion: usize,
    /// Every observed violation (empty means all criteria held).
    pub violations: Vec<InvariantViolation>,
}

impl InvariantReport {
    /// Whether every criterion held across every scenario.
    pub fn passed(&self) -> bool {
        self.violations.is_empty()
    }
}

/// One invariant violation, with the seed that reproduces it (R12.18).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InvariantViolation {
    /// The criterion that was violated.
    pub criterion: &'static str,
    /// The seed that reproduces the violation.
    pub seed: u64,
    /// A plain-language description of the observed value.
    pub detail: String,
}

// ---------------------------------------------------------------------------
// Harness-internal fakes (owner profile view, GeekNews sender/cursor store)
// ---------------------------------------------------------------------------

/// The owner-profile view the harness presents to the send guard. The harness
/// runs a fully fake world, so the owner name is set and the account scope
/// matches (the guard's profile preconditions are exercised elsewhere; here
/// they must pass so the gate itself is what decides).
struct HarnessProfile;

impl ProfileView for HarnessProfile {
    fn owner_name_status(&self) -> OwnerNameStatus {
        OwnerNameStatus::Set
    }

    fn in_scope(&self) -> bool {
        true
    }
}

/// A GeekNews sender that confirms every send and counts them. It never
/// transmits — the durability harness only ever confirms in memory.
struct HarnessSender {
    confirmed: Cell<usize>,
}

impl HarnessSender {
    fn new() -> Self {
        Self {
            confirmed: Cell::new(0),
        }
    }

    fn confirmed(&self) -> usize {
        self.confirmed.get()
    }
}

impl Sender for HarnessSender {
    fn send(&self, _chat_id: i64, _message: &str) -> Result<(), geeknews::SendError> {
        self.confirmed.set(self.confirmed.get() + 1);
        Ok(())
    }
}

/// An in-memory GeekNews cursor store. Persists `seen`/`slots` only after a
/// confirmed send, exactly as [`run_geeknews_post`](crate::geeknews::run_geeknews_post)
/// dictates.
struct HarnessCursorStore {
    cursor: std::cell::RefCell<GeekNewsCursor>,
}

impl HarnessCursorStore {
    fn new() -> Self {
        Self {
            cursor: std::cell::RefCell::new(GeekNewsCursor::default()),
        }
    }
}

impl CursorStore for HarnessCursorStore {
    fn load(&self) -> Result<GeekNewsCursor, geeknews::StoreError> {
        Ok(self.cursor.borrow().clone())
    }

    fn store(&self, cursor: &GeekNewsCursor) -> Result<(), geeknews::StoreError> {
        *self.cursor.borrow_mut() = cursor.clone();
        Ok(())
    }
}

/// A GeekNews feed built from a run index: five fresh, unique topic ids so each
/// run has fresh items to post.
struct IndexedFeed {
    xml: String,
}

impl IndexedFeed {
    fn for_run(idx: usize) -> Self {
        let base = (idx as u32) * 10 + 1;
        let mut xml = String::new();
        for i in 0..geeknews::MAX_ITEMS as u32 {
            let id = base + i;
            xml.push_str(&format!(
                "<entry><title>topic {id}</title>\
                 <link href=\"https://news.hada.io/topic?id={id}\"/>\
                 <content>summary {id}</content></entry>"
            ));
        }
        Self { xml }
    }
}

impl FeedSource for IndexedFeed {
    fn fetch_xml(&self) -> Result<String, geeknews::FeedError> {
        Ok(self.xml.clone())
    }
}

// ---------------------------------------------------------------------------
// The harness
// ---------------------------------------------------------------------------

/// The durability harness. Owns a fully fake world and writes redacted records
/// to a journal.
pub struct DurabilityHarness<'a> {
    ports: FakePorts,
    journal: &'a dyn HistoryStore,
}

impl<'a> DurabilityHarness<'a> {
    /// Build a harness over a fake world and a journal.
    pub fn new(ports: FakePorts, journal: &'a dyn HistoryStore) -> Self {
        Self { ports, journal }
    }

    /// The fake world this harness runs against (read-only access for callers
    /// that want to inspect the tripwires).
    pub fn ports(&self) -> &FakePorts {
        &self.ports
    }

    /// Verify the world is safe to start: every outward path is a fake, no real
    /// send or egress has happened, and the sandbox sits under the system temp
    /// directory. Returns the list of breaches, or `Ok(())` when clear (R1.11).
    pub fn preflight(&self) -> Result<(), Vec<&'static str>> {
        let mut problems: Vec<&'static str> = Vec::new();

        // The four bulk-verification paths (R1.11): local DB, AX send, LLM
        // provider, GeekNews feed. Providers and the feed are fakes by type
        // (`FakeProvider` / `FakeFeedSource`); the two dynamic ports are
        // checked explicitly, plus the Telegram read boundary.
        if !self.ports.messages.is_fake() {
            problems.push("카카오톡 로컬 DB 경로가 가짜가 아니에요");
        }
        if !self.ports.send.is_fake() {
            problems.push("AX 전송 경로가 가짜가 아니에요");
        }
        if !self.ports.ax_read.is_fake() {
            problems.push("텔레그램 읽기 경로가 가짜가 아니에요");
        }

        // Nothing may have leaked before we start.
        if self.ports.blocked_real_send.blocked_count() != 0 {
            problems.push("실제 전송 경로로 향한 호출이 이미 있었어요");
        }
        if self.ports.net.egress_count() != 0 {
            problems.push("외부 네트워크 송신이 이미 있었어요");
        }

        // The sandbox boundary must hold (R1.9).
        if !sandbox_under_temp(self.ports.sandbox.path()) {
            problems.push("시험용 저장 공간이 임시 폴더 밖에 있어요");
        }

        if problems.is_empty() {
            Ok(())
        } else {
            Err(problems)
        }
    }

    /// Run the plan. Performs exactly the configured number of attempts, absorbs
    /// an individual panic as a single failure and continues (R1.1, R1.13), and
    /// checks `cancel` at every processing boundary for cooperative cancellation
    /// (R1.10). If preflight refuses, the batch does not start and the verdict
    /// is [`HarnessVerdict::Aborted`] (R1.11).
    pub fn run(&mut self, plan: &HarnessPlan, cancel: &CancelToken) -> HarnessReport {
        if let Err(problems) = self.preflight() {
            let total = plan.auto_reply_runs + plan.geeknews_runs;
            return HarnessReport {
                auto_reply: BatchTally::default(),
                geeknews: BatchTally::default(),
                seed: plan.seed,
                real_sends: self.ports.blocked_real_send.blocked_count(),
                network_egress: self.ports.net.egress_count(),
                confirmed_sends: 0,
                commit_success_records: 0,
                gate_mismatch: 0,
                verdict: HarnessVerdict::Aborted {
                    completed: 0,
                    not_run: total,
                    reasons: problems.into_iter().map(String::from).collect(),
                },
            };
        }
        execute(&self.ports, self.journal, plan, cancel)
    }

    /// Run at least [`MIN_SCENARIOS_PER_CRITERION`] randomized scenarios per
    /// criterion and check the harness invariants (R12.17). A single seed drives
    /// the whole sweep so a failure is reproducible (R12.18).
    ///
    /// Each scenario builds a fresh fake world and a fresh in-memory journal, so
    /// this does not touch `self.ports` or the shared journal.
    pub fn check_invariants(&mut self, seed: u64, per_criterion: usize) -> InvariantReport {
        let n = per_criterion.max(MIN_SCENARIOS_PER_CRITERION);
        let mut report = InvariantReport {
            scenarios_run: 0,
            per_criterion: n,
            violations: Vec::new(),
        };

        for i in 0..n {
            let scenario_seed = seed.wrapping_add(i as u64);
            let shape = shape_for(scenario_seed);
            let ports = FakePorts::from_seed(scenario_seed, shape);
            let journal = match SqliteHistoryStore::open_in_memory() {
                Ok(j) => j,
                Err(_) => continue,
            };
            // Small per-scenario batch so 200+ scenarios stay fast; the
            // invariants do not depend on the batch size.
            let plan = HarnessPlan {
                auto_reply_runs: 20,
                geeknews_runs: 5,
                seed: scenario_seed,
                sandbox: ports.sandbox.path().to_path_buf(),
            };
            let cancel = CancelToken::new();
            let result = execute(&ports, &journal, &plan, &cancel);
            report.scenarios_run += 1;
            collect_violations(&result, &plan, scenario_seed, &mut report.violations);
        }

        report
    }
}

/// Judge a completed report against the six pass conditions (R1.6, R1.7). Pure:
/// it reads the report and produces a verdict without side effects.
pub fn judge(report: &HarnessReport, plan: &HarnessPlan) -> HarnessVerdict {
    let mut fails: Vec<FailedCondition> = Vec::new();

    if report.real_sends != 0 {
        fails.push(FailedCondition {
            name: "real_sends_zero",
            observed: report.real_sends.to_string(),
            next_step: "시험용 전송 경로 설정을 확인해 주세요.".to_string(),
        });
    }
    if report.network_egress != 0 {
        fails.push(FailedCondition {
            name: "network_egress_zero",
            observed: report.network_egress.to_string(),
            next_step: "외부 네트워크를 막은 설정을 확인해 주세요.".to_string(),
        });
    }
    let panics = report.auto_reply.unhandled_panics + report.geeknews.unhandled_panics;
    if panics != 0 {
        fails.push(FailedCondition {
            name: "no_unhandled_panics",
            observed: panics.to_string(),
            next_step: "예외로 멈춘 처리의 사유를 확인해 주세요.".to_string(),
        });
    }
    if report.auto_reply.attempted != plan.auto_reply_runs {
        fails.push(FailedCondition {
            name: "auto_reply_attempts_match",
            observed: format!("{} / {}", report.auto_reply.attempted, plan.auto_reply_runs),
            next_step: "자동 답변 시도 수가 계획과 달라요. 다시 실행해 주세요.".to_string(),
        });
    }
    if report.geeknews.attempted != plan.geeknews_runs {
        fails.push(FailedCondition {
            name: "geeknews_attempts_match",
            observed: format!("{} / {}", report.geeknews.attempted, plan.geeknews_runs),
            next_step: "긱뉴스 시도 수가 계획과 달라요. 다시 실행해 주세요.".to_string(),
        });
    }
    if report.confirmed_sends != report.commit_success_records {
        fails.push(FailedCondition {
            name: "confirmed_equals_commit",
            observed: format!(
                "확정 전송 {} / 커밋 기록 {}",
                report.confirmed_sends, report.commit_success_records
            ),
            next_step: "전송 기록이 어긋났어요. 저널을 확인해 주세요.".to_string(),
        });
    }
    if report.gate_mismatch != 0 {
        fails.push(FailedCondition {
            name: "gate_decision_consistent",
            observed: report.gate_mismatch.to_string(),
            next_step: "안전 판정이 설정과 어긋났어요. 안전게이트를 확인해 주세요.".to_string(),
        });
    }

    if fails.is_empty() {
        HarnessVerdict::Pass
    } else {
        HarnessVerdict::Fail(fails)
    }
}

// ---------------------------------------------------------------------------
// Core execution
// ---------------------------------------------------------------------------

/// The outcome of one automatic-reply processing.
enum AutoOutcome {
    /// Authorized and the send was confirmed.
    Committed,
    /// Authorized but a later stage failed (carries the stage name).
    StageFailed(&'static str),
    /// The guard fenced the send (carries the reason code).
    Fenced(&'static str),
}

impl AutoOutcome {
    fn authorized(&self) -> bool {
        matches!(self, AutoOutcome::Committed | AutoOutcome::StageFailed(_))
    }
}

/// Run the full plan against an assembled world. Shared by
/// [`DurabilityHarness::run`] and [`DurabilityHarness::check_invariants`].
fn execute(
    ports: &FakePorts,
    journal: &dyn HistoryStore,
    plan: &HarnessPlan,
    cancel: &CancelToken,
) -> HarnessReport {
    // Assemble the production send-guard collaborators once. All are real
    // components; only the outward ports are fake.
    let gate = DefaultSafetyGate;
    let sample_store = SqliteSampleStore::open_in_memory().expect("in-memory sample store");
    let breaker_store = SqliteBreakerStore::open_in_memory().expect("in-memory breaker store");
    let breaker = EmergencyBreaker::new(&breaker_store, &ports.clock);
    let collector = LiveSampleCollector::new(
        &sample_store,
        &ports.clock,
        None,
        resolve_target(None),
        None,
        plan.seed,
    );
    let profile = HarnessProfile;
    let guard = SendGuard {
        gate: &gate,
        breaker: &breaker,
        grade: &collector,
        pacing: &collector,
        profile: &profile,
        clock: &ports.clock,
    };

    let rooms = ports.messages.rooms().unwrap_or_default();
    let messages = ports.messages.messages_after(None, 1_000_000).unwrap_or_default();

    // Suppress the default panic hook while we run so absorbed panics do not
    // spam the console; the harness reports them as counts instead (R1.13).
    let prev_hook = panic::take_hook();
    panic::set_hook(Box::new(|_| {}));

    let mut auto = BatchTally::default();
    let mut gate_mismatch = 0usize;
    let mut auto_durations: Vec<u64> = Vec::with_capacity(plan.auto_reply_runs);
    let mut completed = 0usize;
    let mut cancelled = false;

    let auto_start = Instant::now();
    for idx in 0..plan.auto_reply_runs {
        // Cooperative cancellation boundary (R1.10).
        if cancel.is_cancelled() {
            cancelled = true;
            break;
        }

        // Choose a target chat, preferring a scripted message (the detect seam).
        let chat_id = if !messages.is_empty() {
            messages[idx % messages.len()].chat_id
        } else if !rooms.is_empty() {
            rooms[idx % rooms.len()].chat_id
        } else {
            1
        };
        let owner_fp = match ports.messages.authority(chat_id) {
            Ok(a) => format!("fp-{}", a.owner_display_name.unwrap_or_else(|| "owner".into())),
            Err(_) => "fp-owner".to_string(),
        };

        let perturb = pick_perturbation(plan.seed, idx);
        let (cfg, req) = build_case(perturb, chat_id, &owner_fp);
        let should_allow = perturb == Perturbation::None;
        let intent = SendIntent {
            grade: SendGrade::Fake,
            origin: Origin::PartnerMessage {
                event_id: format!("db:{}:{}", chat_id.max(1), idx + 1),
            },
        };
        let trace_id = format!("durability:auto:{idx}");
        let provider = if ports.providers.is_empty() {
            None
        } else {
            Some(&ports.providers[idx % ports.providers.len()])
        };

        let start = Instant::now();
        let result = panic::catch_unwind(AssertUnwindSafe(|| {
            process_auto_reply_run(
                &guard, journal, provider, &cfg, &req, &intent, &trace_id, idx, &ports.clock,
                &ports.send,
            )
        }));
        auto_durations.push(start.elapsed().as_millis() as u64);
        auto.attempted += 1;
        completed += 1;

        match result {
            Ok(outcome) => {
                if outcome.authorized() != should_allow {
                    gate_mismatch += 1;
                }
                match outcome {
                    AutoOutcome::Committed => auto.allowed += 1,
                    AutoOutcome::StageFailed(stage) => {
                        auto.allowed += 1;
                        *auto.stage_failures.entry(stage).or_insert(0) += 1;
                    }
                    AutoOutcome::Fenced(reason) => {
                        auto.fenced += 1;
                        *auto.fenced_by_reason.entry(reason).or_insert(0) += 1;
                    }
                }
            }
            Err(_) => auto.unhandled_panics += 1,
        }
    }
    auto.elapsed_ms = auto_start.elapsed().as_millis() as u64;
    auto.blocked_real_send_attempts = ports.blocked_real_send.blocked_count();
    let (p50, p95) = percentiles(&mut auto_durations);
    auto.p50_ms = p50;
    auto.p95_ms = p95;

    // GeekNews batch: fake feed/sender/cursor-store through run_geeknews_post.
    let geek_sender = HarnessSender::new();
    let geek_cursor = HarnessCursorStore::new();
    let mut geek = BatchTally::default();
    let mut geek_durations: Vec<u64> = Vec::with_capacity(plan.geeknews_runs);
    let geek_start = Instant::now();
    if !cancelled {
        for idx in 0..plan.geeknews_runs {
            if cancel.is_cancelled() {
                cancelled = true;
                break;
            }
            let start = Instant::now();
            let result = panic::catch_unwind(AssertUnwindSafe(|| {
                process_geeknews_run(idx, journal, &geek_sender, &geek_cursor, &ports.clock)
            }));
            geek_durations.push(start.elapsed().as_millis() as u64);
            geek.attempted += 1;
            completed += 1;

            match result {
                Ok(GeekRunOutcome::Posted) => geek.allowed += 1,
                Ok(GeekRunOutcome::NoSend(code)) => {
                    *geek.stage_failures.entry(code).or_insert(0) += 1;
                }
                Err(_) => geek.unhandled_panics += 1,
            }
        }
    }
    geek.elapsed_ms = geek_start.elapsed().as_millis() as u64;
    geek.blocked_real_send_attempts = ports.blocked_real_send.blocked_count();
    let (gp50, gp95) = percentiles(&mut geek_durations);
    geek.p50_ms = gp50;
    geek.p95_ms = gp95;

    // Restore the default panic hook.
    panic::set_hook(prev_hook);

    let confirmed_sends = ports.send.confirmed_count() + geek_sender.confirmed();
    let commit_success_records = count_commit_success(journal);

    let mut report = HarnessReport {
        auto_reply: auto,
        geeknews: geek,
        seed: plan.seed,
        real_sends: ports.blocked_real_send.blocked_count(),
        network_egress: ports.net.egress_count(),
        confirmed_sends,
        commit_success_records,
        gate_mismatch,
        verdict: HarnessVerdict::Pass, // placeholder, set below
    };

    report.verdict = if cancelled {
        let total = plan.auto_reply_runs + plan.geeknews_runs;
        HarnessVerdict::Aborted {
            completed,
            not_run: total.saturating_sub(completed),
            reasons: vec!["사용자 요청으로 검증을 멈췄어요.".to_string()],
        }
    } else {
        judge(&report, plan)
    };

    report
}

/// Process one automatic-reply run through the production seam. Journaling
/// happens inside so a panicking journal is captured by the caller's
/// `catch_unwind`.
#[allow(clippy::too_many_arguments)]
fn process_auto_reply_run(
    guard: &SendGuard<'_>,
    journal: &dyn HistoryStore,
    provider: Option<&crate::fakes::FakeProvider>,
    cfg: &SafetyConfig,
    req: &SendRequest,
    intent: &SendIntent,
    trace_id: &str,
    idx: usize,
    clock: &dyn Clock,
    send: &dyn SendPort,
) -> AutoOutcome {
    let now = clock.now_ms();

    // detect (in_progress) — the message was picked up.
    let _ = journal.append(PipelineEvent {
        trace_id: trace_id.to_string(),
        flow: FlowKind::AutoReply,
        stage: Stage::Detect,
        status: StageStatus::InProgress,
        result_code: "detect_start".to_string(),
        duration_ms: 0,
        at: now,
    });

    match guard.authorize(req, cfg, intent) {
        Ok(ticket) => {
            // model — generate a reply through the fake provider.
            let answer = provider.and_then(|p| {
                let model = p.list_models().ok()?.into_iter().next()?;
                let prompt = Prompt {
                    version: "v1".to_string(),
                    question_id: format!("q{idx}"),
                    text: format!("durability prompt {idx}"),
                };
                p.generate(&model, &prompt).ok()
            });
            let Some(answer) = answer else {
                let _ = journal.append(PipelineEvent {
                    trace_id: trace_id.to_string(),
                    flow: FlowKind::AutoReply,
                    stage: Stage::Model,
                    status: StageStatus::Failed,
                    result_code: "model_failed".to_string(),
                    duration_ms: 0,
                    at: clock.now_ms(),
                });
                return AutoOutcome::StageFailed("model");
            };

            // commit — the confirmed send. This is the single place a confirmed
            // send is recorded, so confirmed == commit/success (R12.16).
            match send.send_text(&ticket, &answer.text) {
                Ok(_receipt) => {
                    let _ = journal.append(PipelineEvent {
                        trace_id: trace_id.to_string(),
                        flow: FlowKind::AutoReply,
                        stage: Stage::Commit,
                        status: StageStatus::Success,
                        result_code: "committed".to_string(),
                        duration_ms: answer.latency_ms,
                        at: clock.now_ms(),
                    });
                    AutoOutcome::Committed
                }
                Err(_) => {
                    let _ = journal.append(PipelineEvent {
                        trace_id: trace_id.to_string(),
                        flow: FlowKind::AutoReply,
                        stage: Stage::PreSend,
                        status: StageStatus::Failed,
                        result_code: "pre_send_failed".to_string(),
                        duration_ms: 0,
                        at: clock.now_ms(),
                    });
                    AutoOutcome::StageFailed("pre_send")
                }
            }
        }
        Err(fence) => {
            let reason = fence_code(&fence);
            let _ = journal.append(PipelineEvent {
                trace_id: trace_id.to_string(),
                flow: FlowKind::AutoReply,
                stage: Stage::Authorize,
                status: StageStatus::Failed,
                result_code: reason.to_string(),
                duration_ms: 0,
                at: clock.now_ms(),
            });
            AutoOutcome::Fenced(reason)
        }
    }
}

/// The outcome of one GeekNews run.
enum GeekRunOutcome {
    /// A post was sent and the cursor advanced.
    Posted,
    /// The attempt did not send (carries a machine-readable reason).
    NoSend(&'static str),
}

/// Process one GeekNews run through [`run_geeknews_post`](crate::geeknews::run_geeknews_post)
/// with the fake feed/sender/cursor-store.
fn process_geeknews_run(
    idx: usize,
    journal: &dyn HistoryStore,
    sender: &HarnessSender,
    cursor: &HarnessCursorStore,
    clock: &dyn Clock,
) -> GeekRunOutcome {
    let feed = IndexedFeed::for_run(idx);
    let now_secs = in_slot_now(idx);
    let trace_id = format!("durability:geeknews:{idx}");

    match geeknews::run_geeknews_post(1, now_secs, &feed, sender, cursor) {
        Ok(PostOutcome::Posted { .. }) => {
            let _ = journal.append(PipelineEvent {
                trace_id,
                flow: FlowKind::GeekNews,
                stage: Stage::Commit,
                status: StageStatus::Success,
                result_code: "geeknews_posted".to_string(),
                duration_ms: 0,
                at: clock.now_ms(),
            });
            GeekRunOutcome::Posted
        }
        Ok(other) => {
            let code = match other {
                PostOutcome::NoOpenSlot => "geeknews_no_slot",
                PostOutcome::EmptyFeed => "geeknews_empty_feed",
                PostOutcome::NoFreshItems => "geeknews_no_fresh",
                PostOutcome::Posted { .. } => unreachable!(),
            };
            let _ = journal.append(PipelineEvent {
                trace_id,
                flow: FlowKind::GeekNews,
                stage: Stage::Detect,
                status: StageStatus::Success,
                result_code: code.to_string(),
                duration_ms: 0,
                at: clock.now_ms(),
            });
            GeekRunOutcome::NoSend(code)
        }
        Err(_) => {
            let _ = journal.append(PipelineEvent {
                trace_id,
                flow: FlowKind::GeekNews,
                stage: Stage::Authorize,
                status: StageStatus::Failed,
                result_code: "geeknews_failed".to_string(),
                duration_ms: 0,
                at: clock.now_ms(),
            });
            GeekRunOutcome::NoSend("geeknews_failed")
        }
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// How a run's safety configuration is perturbed to exercise the tally.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Perturbation {
    /// A fully-valid configuration (the guard allows).
    None,
    /// `allow_ax_send` off.
    AxOff,
    /// `allow_auto_reply` off.
    AutoReplyOff,
    /// The target chat is not on the allowlist.
    NotAllowlisted,
    /// The presented account fingerprint does not match.
    IdentityMismatch,
    /// The database-authoritative state check failed.
    StateBad,
}

/// Deterministically pick a perturbation for `idx` from `seed`. About 60% of
/// runs are fully valid so the batch yields many confirmed sends.
fn pick_perturbation(seed: u64, idx: usize) -> Perturbation {
    // A small, deterministic hash of (seed, idx).
    let mut h = seed
        .wrapping_mul(0x9E37_79B9_7F4A_7C15)
        .wrapping_add(idx as u64)
        .wrapping_mul(0xBF58_476D_1CE4_E5B9);
    h ^= h >> 31;
    match h % 100 {
        0..=59 => Perturbation::None,
        60..=67 => Perturbation::AxOff,
        68..=75 => Perturbation::AutoReplyOff,
        76..=83 => Perturbation::NotAllowlisted,
        84..=91 => Perturbation::IdentityMismatch,
        _ => Perturbation::StateBad,
    }
}

/// Build a `(SafetyConfig, SendRequest)` for a perturbation.
fn build_case(perturb: Perturbation, chat_id: i64, owner_fp: &str) -> (SafetyConfig, SendRequest) {
    let good_cfg = || SafetyConfig::new(true, true, [chat_id], owner_fp);
    let good_req = || SendRequest {
        chat_id,
        account_fp: owner_fp.to_string(),
        state_ok: true,
    };
    match perturb {
        Perturbation::None => (good_cfg(), good_req()),
        Perturbation::AxOff => {
            let mut cfg = good_cfg();
            cfg.allow_ax_send = false;
            (cfg, good_req())
        }
        Perturbation::AutoReplyOff => {
            let mut cfg = good_cfg();
            cfg.allow_auto_reply = false;
            (cfg, good_req())
        }
        Perturbation::NotAllowlisted => {
            // Allowlist a different chat so the target is excluded.
            let cfg = SafetyConfig::new(true, true, [chat_id + 1], owner_fp);
            (cfg, good_req())
        }
        Perturbation::IdentityMismatch => {
            let mut req = good_req();
            req.account_fp = "intruder".to_string();
            (good_cfg(), req)
        }
        Perturbation::StateBad => {
            let mut req = good_req();
            req.state_ok = false;
            (good_cfg(), req)
        }
    }
}

/// A short, stable machine code for a guard fence.
fn fence_code(fence: &GuardFence) -> &'static str {
    match fence {
        GuardFence::Gate(reason) => match reason {
            FenceReason::AxSendDisabled => "gate_ax_send_disabled",
            FenceReason::AutoReplyDisabled => "gate_auto_reply_disabled",
            FenceReason::ChatNotAllowlisted => "gate_chat_not_allowlisted",
            FenceReason::IdentityMismatch => "gate_identity_mismatch",
            FenceReason::StateMismatch => "gate_state_mismatch",
        },
        GuardFence::Breaker { .. } => "breaker_tripped",
        GuardFence::BreakerUnknown(_) => "breaker_unknown",
        GuardFence::OwnerNameUnset(_) => "owner_name_unset",
        GuardFence::ProfileScopeMismatch => "profile_scope_mismatch",
        GuardFence::GradeLimit(_) => "grade_limit",
        GuardFence::TooSoon { .. } => "too_soon",
    }
}

/// Pick a unix-second instant inside a fresh GeekNews slot for run `idx`, one
/// day apart per run so each run lands in a not-yet-posted slot.
fn in_slot_now(idx: usize) -> i64 {
    // A base instant well into the future; shift one day per run.
    const BASE: i64 = 1_800_000_000;
    let approx = BASE + idx as i64 * 86_400;
    match geeknews::day_slot_windows(approx).first() {
        Some(w) => w.start + 60, // 60s inside the 30-minute window
        None => approx,
    }
}

/// Count `commit`/`success` journal records — the definition of a confirmed
/// send (R12.16).
fn count_commit_success(journal: &dyn HistoryStore) -> usize {
    journal
        .recent(usize::MAX)
        .map(|events| {
            events
                .iter()
                .filter(|e| e.stage == Stage::Commit && e.status == StageStatus::Success)
                .count()
        })
        .unwrap_or(0)
}

/// Nearest-rank P50/P95 over per-processing durations (sorts in place).
fn percentiles(durations: &mut [u64]) -> (u64, u64) {
    if durations.is_empty() {
        return (0, 0);
    }
    durations.sort_unstable();
    (percentile(durations, 50.0), percentile(durations, 95.0))
}

fn percentile(sorted: &[u64], p: f64) -> u64 {
    if sorted.is_empty() {
        return 0;
    }
    let rank = (p / 100.0 * (sorted.len() as f64 - 1.0)).round() as usize;
    sorted[rank.min(sorted.len() - 1)]
}

/// Whether `path` sits under the system temp directory (R1.9). Canonicalizes
/// both sides so `/var` vs `/private/var` symlinks on macOS do not trip it.
fn sandbox_under_temp(path: &std::path::Path) -> bool {
    let Ok(sandbox) = path.canonicalize() else {
        return false;
    };
    let Ok(tmp) = std::env::temp_dir().canonicalize() else {
        return false;
    };
    sandbox.starts_with(&tmp)
}

/// A bounded scenario shape derived from a seed (for `check_invariants`).
fn shape_for(seed: u64) -> ScenarioShape {
    let rooms = 1 + (seed % ScenarioShape::MAX_ROOMS as u64) as usize;
    let msgs = 1 + (seed % 40) as usize;
    let imgs = (seed % 4) as usize;
    let combo = (seed % ScenarioShape::MAX_SAFETY_COMBO as u64) as usize;
    ScenarioShape::new(rooms, msgs, imgs, combo)
}

/// Append any harness-invariant violations observed in `report` to `out`.
fn collect_violations(
    report: &HarnessReport,
    plan: &HarnessPlan,
    seed: u64,
    out: &mut Vec<InvariantViolation>,
) {
    if report.real_sends != 0 {
        out.push(InvariantViolation {
            criterion: "real_sends_zero",
            seed,
            detail: format!("real_sends = {}", report.real_sends),
        });
    }
    if report.network_egress != 0 {
        out.push(InvariantViolation {
            criterion: "network_egress_zero",
            seed,
            detail: format!("network_egress = {}", report.network_egress),
        });
    }
    if report.confirmed_sends != report.commit_success_records {
        out.push(InvariantViolation {
            criterion: "confirmed_equals_commit",
            seed,
            detail: format!(
                "confirmed {} != commit {}",
                report.confirmed_sends, report.commit_success_records
            ),
        });
    }
    if report.auto_reply.attempted != plan.auto_reply_runs
        || report.geeknews.attempted != plan.geeknews_runs
    {
        out.push(InvariantViolation {
            criterion: "attempts_exact",
            seed,
            detail: format!(
                "auto {}/{}, geeknews {}/{}",
                report.auto_reply.attempted,
                plan.auto_reply_runs,
                report.geeknews.attempted,
                plan.geeknews_runs
            ),
        });
    }
    let panics = report.auto_reply.unhandled_panics + report.geeknews.unhandled_panics;
    if panics != 0 {
        out.push(InvariantViolation {
            criterion: "no_unhandled_panics",
            seed,
            detail: format!("panics = {panics}"),
        });
    }
}

#[cfg(test)]
mod tests;
