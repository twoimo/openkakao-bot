//! Property-based tests for the feature-coverage verifier (task 6.3).
//!
//! Feature: kakao-agent-live-ops
//! Property 16: 기능검증 판정의 전체성·우선순위·개수 합
//!   (verdict totality, priority, and count sums)
//! Property 7: 검증 결정성 — 기능 검증 관점
//!   (verification determinism, feature-verification perspective)
//!
//! Property 16 drives [`CoverageVerifier::verify`] over arbitrary room sets
//! (0..=16 rooms, unique chat ids, titles that include blanks and duplicates)
//! and an arbitrary per-cell [`CellProbe`] decision, and pins:
//!   * the total combination count is `방 수 × 6` (R5.1),
//!   * every combination is judged exactly once — the matrix carries one
//!     [`Verdict`] per `(RoomKey, Feature)` cell so `cells.len() == total`
//!     (R5.2),
//!   * the four counts sum to the total and per-room / per-feature subtotals
//!     agree with the overall tally (R5.5),
//!   * the overall pass is exactly `sums_match_total ∧ fail == 0 ∧ total > 0`
//!     (R5.9), and
//!   * the set of room labels has the same size as the number of rooms — every
//!     room stays distinguishable even with blank or duplicated titles (R5.11).
//!
//! A companion pure-function property pins the priority rule of [`classify`]:
//! when more than one candidate holds, the verdict is selected in the order
//! `unsupported` → `blocked` → `fail` → `pass`, and the function is total —
//! a cell that neither failed nor reached a result is a `fail` (R5.2).
//!
//! Property 7 pins determinism from the feature-verification side: for the same
//! automation list and the same fake per-cell input, [`CoverageVerifier::verify`]
//! produces an identical matrix — identical per-combination verdicts and
//! identical counts — regardless of how many workers evaluate the cells and how
//! they interleave (R5.14). The verifier keys every result by `(RoomKey,
//! Feature)` into a sorted map, so worker scheduling cannot change the outcome;
//! the test demonstrates this by comparing runs at 1, 3, and 8 workers plus a
//! re-run, and by driving a per-combination timeout budget that some cells
//! exceed (R5.12).
//!
//! No real KakaoTalk send or network egress is possible here: the verifier only
//! ever calls the in-memory [`CellProbe`], which fabricates verdict signals and
//! never touches a send port or the network (R5.7, R12.2).
//!
//! Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 5.9, 5.11, 5.12, 5.14, 12.2

use std::collections::{BTreeMap, BTreeSet};

use openkakao_cli::coverage::{
    classify, CellProbe, CoverageBudget, CoverageVerifier, FailDetail, Feature, ProbeSignal,
    UnsupportedReason, Verdict, VerdictCandidates,
};
use openkakao_cli::logging::Stage;
use openkakao_cli::room_catalog::CatalogRoom;
use proptest::prelude::*;

// ===========================================================================
// Shared fixtures
// ===========================================================================

/// The mismatched safety-setting item names a `blocked` verdict can carry
/// (R5.3). Any one of these is representative for the counting invariants.
const FENCE_ITEMS: [&str; 5] = [
    "allow_ax_send",
    "allow_auto_reply",
    "allowed_send_chats",
    "authority_identity",
    "authority_state",
];

/// One scripted decision for a single (room, feature) combination. The verifier
/// turns each into a verdict; together they exercise every branch of the count
/// tally without any real processing.
#[derive(Debug, Clone)]
enum ProbeDecision {
    /// Reaches a result cleanly, inside the per-combination budget → `pass`.
    Pass(u64),
    /// A fenced send feature, inside the budget → `blocked`.
    Blocked(u64),
    /// Fails at a stage, inside the budget → `fail`.
    Fail(u64),
    /// Reaches a result but overruns the per-combination budget → timeout
    /// `fail` (R5.12).
    Timeout,
    /// Ends without reaching a result → `fail` with a next step (R5.2).
    NoResult,
}

impl ProbeDecision {
    fn to_signal(&self) -> ProbeSignal {
        match self {
            ProbeDecision::Pass(ms) => ProbeSignal::passed(*ms),
            ProbeDecision::Blocked(ms) => ProbeSignal::blocked(vec![FENCE_ITEMS[0]], *ms),
            ProbeDecision::Fail(ms) => {
                ProbeSignal::failed(FailDetail::new(Stage::Model, "boom", "고쳐 주세요."), *ms)
            }
            // Over the default 10 s per-combination budget.
            ProbeDecision::Timeout => ProbeSignal {
                fenced: None,
                failed: None,
                reached_result: true,
                reached_stage: Stage::Model,
                elapsed_ms: 20_000,
            },
            ProbeDecision::NoResult => ProbeSignal {
                fenced: None,
                failed: None,
                reached_result: false,
                reached_stage: Stage::Detect,
                elapsed_ms: 5,
            },
        }
    }
}

/// A deterministic probe: the verdict signal for a combination is looked up by
/// `(chat_id, Feature)`, so the same input always yields the same signal — the
/// precondition the determinism property leans on.
struct MapProbe {
    decisions: BTreeMap<(i64, Feature), ProbeSignal>,
}

impl CellProbe for MapProbe {
    fn probe(&self, room: &CatalogRoom, feature: Feature) -> ProbeSignal {
        self.decisions
            .get(&(room.chat_id, feature))
            .cloned()
            .unwrap_or_else(|| ProbeSignal::passed(5))
    }
}

/// A generated room together with a scripted decision for each of its six
/// features (in [`Feature::ALL`] order).
#[derive(Debug, Clone)]
struct RoomSpec {
    title: String,
    enabled: bool,
    auto_reply: bool,
    geeknews: bool,
    link_forward: bool,
    telegram_relay: bool,
    decisions: Vec<ProbeDecision>,
}

/// Titles chosen from a small pool so blanks and duplicates arise often,
/// exercising the label-distinctness rule (R5.11).
fn title_strategy() -> impl Strategy<Value = String> {
    prop::sample::select(vec!["", "스터디", "스터디", "모임", "study"]).prop_map(str::to_string)
}

/// A decision inside the budget uses an elapsed time strictly under the default
/// 10 s bound so it is not reclassified as a timeout.
fn decision_strategy() -> impl Strategy<Value = ProbeDecision> {
    prop_oneof![
        (0u64..9_000).prop_map(ProbeDecision::Pass),
        (0u64..9_000).prop_map(ProbeDecision::Blocked),
        (0u64..9_000).prop_map(ProbeDecision::Fail),
        Just(ProbeDecision::Timeout),
        Just(ProbeDecision::NoResult),
    ]
}

fn room_spec_strategy() -> impl Strategy<Value = RoomSpec> {
    (
        title_strategy(),
        any::<bool>(),
        any::<bool>(),
        any::<bool>(),
        any::<bool>(),
        any::<bool>(),
        prop::collection::vec(decision_strategy(), Feature::ALL.len()),
    )
        .prop_map(
            |(title, enabled, auto_reply, geeknews, link_forward, telegram_relay, decisions)| {
                RoomSpec {
                    title,
                    enabled,
                    auto_reply,
                    geeknews,
                    link_forward,
                    telegram_relay,
                    decisions,
                }
            },
        )
}

/// Build the catalog rooms (unique chat ids from the index) and the matching
/// per-cell probe from a list of specs.
fn build(specs: &[RoomSpec]) -> (Vec<CatalogRoom>, MapProbe) {
    let rooms: Vec<CatalogRoom> = specs
        .iter()
        .enumerate()
        .map(|(i, s)| CatalogRoom {
            chat_id: (i as i64) + 1,
            title: s.title.clone(),
            enabled: s.enabled,
            auto_reply: s.auto_reply,
            geeknews: s.geeknews,
            link_forward: s.link_forward,
            telegram_relay: s.telegram_relay,
        })
        .collect();

    let mut decisions = BTreeMap::new();
    for (i, s) in specs.iter().enumerate() {
        let chat_id = (i as i64) + 1;
        for (fi, &feature) in Feature::ALL.iter().enumerate() {
            decisions.insert((chat_id, feature), s.decisions[fi].to_signal());
        }
    }
    (rooms, MapProbe { decisions })
}

// ===========================================================================
// Property 16: totality, priority, and count sums.
// ===========================================================================

// Strategies for the pure classify-priority sub-property.

fn unsupported_reason_strategy() -> impl Strategy<Value = UnsupportedReason> {
    prop_oneof![
        Just(UnsupportedReason::RoomDisabled),
        Just(UnsupportedReason::FeatureDisabled),
    ]
}

fn blocked_items_strategy() -> impl Strategy<Value = Vec<&'static str>> {
    prop::collection::vec(prop::sample::select(FENCE_ITEMS.to_vec()), 1..=3)
}

fn stage_strategy() -> impl Strategy<Value = Stage> {
    prop_oneof![
        Just(Stage::Detect),
        Just(Stage::Authorize),
        Just(Stage::Retrieve),
        Just(Stage::Model),
        Just(Stage::Schedule),
        Just(Stage::PreSend),
        Just(Stage::Commit),
    ]
}

fn fail_detail_strategy() -> impl Strategy<Value = FailDetail> {
    stage_strategy().prop_map(|stage| FailDetail::new(stage, "scripted", "확인해 주세요."))
}

fn candidates_strategy() -> impl Strategy<Value = VerdictCandidates> {
    (
        prop::option::of(unsupported_reason_strategy()),
        prop::option::of(blocked_items_strategy()),
        prop::option::of(fail_detail_strategy()),
        any::<bool>(),
    )
        .prop_map(
            |(unsupported, blocked, failed, reached_result)| VerdictCandidates {
                unsupported,
                blocked,
                failed,
                reached_result,
            },
        )
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// Feature: kakao-agent-live-ops, Property 16 (matrix totality and counts)
    /// Validates: Requirements 5.1, 5.2, 5.5, 5.9, 5.11, 5.12, 12.2
    #[test]
    fn matrix_totality_and_count_sums(
        specs in prop::collection::vec(room_spec_strategy(), 0..=16)
    ) {
        let (rooms, probe) = build(&specs);
        let n = rooms.len();

        let matrix = CoverageVerifier::new(rooms, &probe).verify();

        // R5.1: total = 방 수 × 6.
        prop_assert_eq!(matrix.total, n * Feature::ALL.len());

        // R5.2: every combination is judged exactly once — one cell per combo.
        prop_assert_eq!(matrix.cells.len(), n * Feature::ALL.len());

        // R5.5: the four counts sum to the total.
        prop_assert_eq!(matrix.totals.sum(), matrix.total);
        prop_assert!(matrix.sums_match_total);

        // R5.5: one subtotal per room and per feature.
        prop_assert_eq!(matrix.by_room.len(), n);
        prop_assert_eq!(matrix.by_feature.len(), Feature::ALL.len());

        // R5.5: per-room subtotals each cover the six features and, added up,
        // equal the overall tally.
        let mut room_pass = 0usize;
        let mut room_fail = 0usize;
        let mut room_uns = 0usize;
        let mut room_blk = 0usize;
        for counts in matrix.by_room.values() {
            prop_assert_eq!(counts.sum(), Feature::ALL.len());
            room_pass += counts.pass;
            room_fail += counts.fail;
            room_uns += counts.unsupported;
            room_blk += counts.blocked;
        }
        prop_assert_eq!(room_pass, matrix.totals.pass);
        prop_assert_eq!(room_fail, matrix.totals.fail);
        prop_assert_eq!(room_uns, matrix.totals.unsupported);
        prop_assert_eq!(room_blk, matrix.totals.blocked);

        // R5.5: per-feature subtotals each cover every room and, added up,
        // equal the overall tally.
        let mut feat_pass = 0usize;
        let mut feat_fail = 0usize;
        let mut feat_uns = 0usize;
        let mut feat_blk = 0usize;
        for counts in matrix.by_feature.values() {
            prop_assert_eq!(counts.sum(), n);
            feat_pass += counts.pass;
            feat_fail += counts.fail;
            feat_uns += counts.unsupported;
            feat_blk += counts.blocked;
        }
        prop_assert_eq!(feat_pass, matrix.totals.pass);
        prop_assert_eq!(feat_fail, matrix.totals.fail);
        prop_assert_eq!(feat_uns, matrix.totals.unsupported);
        prop_assert_eq!(feat_blk, matrix.totals.blocked);

        // R5.9: overall pass ⇔ sums match ∧ no fail ∧ at least one combination.
        let expected_overall =
            matrix.total > 0 && matrix.sums_match_total && matrix.totals.fail == 0;
        prop_assert_eq!(matrix.overall_pass(), expected_overall);

        // R5.11: every room label is distinct — the label set size equals the
        // number of rooms, even with blank or duplicated titles.
        let labels: BTreeSet<String> =
            matrix.by_room.keys().map(|key| key.label()).collect();
        prop_assert_eq!(labels.len(), n);
    }

    /// Feature: kakao-agent-live-ops, Property 16 (classify priority)
    /// Validates: Requirements 5.2, 5.3, 5.4
    #[test]
    fn classify_selects_one_verdict_by_priority(c in candidates_strategy()) {
        let verdict = classify(c.clone());

        // The fixed priority order: unsupported → blocked → fail → pass.
        if let Some(reason) = c.unsupported {
            prop_assert_eq!(verdict, Verdict::Unsupported(reason));
        } else if let Some(items) = c.blocked {
            prop_assert_eq!(verdict, Verdict::Blocked(items));
        } else if let Some(detail) = c.failed {
            prop_assert_eq!(verdict, Verdict::Fail(detail));
        } else if c.reached_result {
            prop_assert_eq!(verdict, Verdict::Pass);
        } else {
            // Total function: neither failed nor reached a result ⇒ a fail that
            // still carries a plain-language next step.
            match verdict {
                Verdict::Fail(detail) => {
                    prop_assert!(!detail.next_step.is_empty());
                }
                other => prop_assert!(false, "expected a fail, got {:?}", other),
            }
        }
    }
}

// ===========================================================================
// Property 7: verification determinism (feature-verification perspective).
// ===========================================================================

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// Feature: kakao-agent-live-ops, Property 7 (feature-verification determinism)
    /// Validates: Requirements 5.14, 12.2
    #[test]
    fn verify_is_identical_regardless_of_worker_scheduling(
        specs in prop::collection::vec(room_spec_strategy(), 0..=16)
    ) {
        let (rooms, probe) = build(&specs);

        // A tight per-combination budget so some scripted timeouts land as
        // fails; the whole-run bound is generous. Both are held constant across
        // runs (R5.12).
        let budget = CoverageBudget {
            per_combination_ms: 10_000,
            total_ms: 60_000,
        };

        let one = CoverageVerifier::new(rooms.clone(), &probe)
            .with_budget(budget)
            .with_max_workers(1)
            .verify();
        let three = CoverageVerifier::new(rooms.clone(), &probe)
            .with_budget(budget)
            .with_max_workers(3)
            .verify();
        let eight = CoverageVerifier::new(rooms.clone(), &probe)
            .with_budget(budget)
            .with_max_workers(8)
            .verify();
        let rerun = CoverageVerifier::new(rooms, &probe)
            .with_budget(budget)
            .with_max_workers(8)
            .verify();

        // R5.14: same automation list + same fake input ⇒ identical matrix
        // (per-combination verdicts and every count) regardless of worker
        // count or interleaving.
        prop_assert_eq!(&one, &three);
        prop_assert_eq!(&one, &eight);
        prop_assert_eq!(&one, &rerun);
    }
}
