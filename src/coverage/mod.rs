//! The feature-coverage verifier (기능검증기) — room × feature verification and
//! counting (R5).
//!
//! An owner needs to confirm, in one run, that every feature works across every
//! automated room, and to receive the result as counts so it is obvious which
//! feature in which room has a problem (R5). This module checks all
//! `점검 대상 방 수 × 6` combinations, gives each combination exactly one
//! [`Verdict`], and rolls the verdicts up into a [`CoverageMatrix`].
//!
//! Three structural guarantees hold here:
//!
//! * **Every combination gets exactly one verdict, chosen by priority (R5.2).**
//!   [`classify`] is a pure function: when more than one condition holds it
//!   selects a single verdict in the order `unsupported` → `blocked` → `fail` →
//!   `pass`. The input type [`VerdictCandidates`] carries at most one of each
//!   candidate, so the priority rule is applied consistently everywhere.
//! * **The run is deterministic even though cells run in parallel (R5.14).**
//!   Cells are evaluated in a worker pool capped at [`MAX_WORKERS`], but each
//!   result is keyed by `(RoomKey, Feature)` into a [`BTreeMap`], so the final
//!   ordering — and therefore the whole matrix — does not depend on which
//!   worker finished first.
//! * **Zero rooms is never an overall pass (R5.13).**
//!   [`CoverageMatrix::overall_pass`] requires `total > 0` in addition to the
//!   four counts summing to the total and `fail == 0`.
//!
//! The per-combination and whole-run time bounds (R5.6, R5.12) are represented
//! as a configurable [`CoverageBudget`]. A combination whose reported work time
//! exceeds the per-combination budget is judged `fail` with a `timeout` cause
//! code (R5.12); the verifier never sleeps to enforce the bound, so tests stay
//! fast.
//!
//! The actual per-cell processing (assembling a fake world, running the send
//! guard, generating a reply, and so on) lives behind the [`CellProbe`] seam,
//! so this module can be exercised with a deterministic in-memory probe and no
//! real KakaoTalk send or network egress ever happens (R5.7).

use std::collections::BTreeMap;
use std::sync::atomic::{AtomicUsize, Ordering};

use crate::logging::Stage;
use crate::room_catalog::CatalogRoom;
use crate::safety::FenceReason;

/// The most workers a coverage run may use at once. Cells are independent, so
/// bounding concurrency here keeps the 32-room × 6-feature (192-combination)
/// run inside its 60-second budget (R5.6) without unbounded thread creation.
pub const MAX_WORKERS: usize = 8;

// ---------------------------------------------------------------------------
// Feature
// ---------------------------------------------------------------------------

/// One of the six features every room is checked against (R5.1).
///
/// The `PartialOrd`/`Ord` derives order features by declaration, which is the
/// stable order the report and the [`CoverageMatrix`] use.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Feature {
    /// Automatic replies (자동 답변).
    AutoReply,
    /// GeekNews posting (긱뉴스 게시).
    GeekNews,
    /// Link forwarding (링크 전달).
    LinkForward,
    /// Telegram relaying (텔레그램 중계).
    TelegramRelay,
    /// Context search (맥락 검색).
    ContextSearch,
    /// Style application (말투 적용).
    StyleApply,
}

impl Feature {
    /// Every feature, in the stable report order. Its length (6) is the
    /// per-room multiplier used to compute the total combination count (R5.1).
    pub const ALL: [Feature; 6] = [
        Feature::AutoReply,
        Feature::GeekNews,
        Feature::LinkForward,
        Feature::TelegramRelay,
        Feature::ContextSearch,
        Feature::StyleApply,
    ];

    /// A short, stable string for reports.
    pub fn as_str(self) -> &'static str {
        match self {
            Feature::AutoReply => "auto_reply",
            Feature::GeekNews => "geeknews",
            Feature::LinkForward => "link_forward",
            Feature::TelegramRelay => "telegram_relay",
            Feature::ContextSearch => "context_search",
            Feature::StyleApply => "style_apply",
        }
    }

    /// Whether this feature performs a KakaoTalk send. Only send-performing
    /// features can be judged `blocked` by a fenced safety gate (R5.3); the
    /// read-only features (context search, style application) never are.
    pub fn performs_send(self) -> bool {
        matches!(
            self,
            Feature::AutoReply | Feature::GeekNews | Feature::LinkForward | Feature::TelegramRelay
        )
    }
}

// ---------------------------------------------------------------------------
// Verdict and its ingredients
// ---------------------------------------------------------------------------

/// Why a combination is `unsupported` (R5.4). Reported without any processing
/// attempt.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum UnsupportedReason {
    /// The room's master switch ("동작") is off, so no feature runs there.
    RoomDisabled,
    /// The room's per-feature toggle is off — it has not opted in to this
    /// feature.
    FeatureDisabled,
}

impl UnsupportedReason {
    /// A short, stable code for reports.
    pub fn as_str(self) -> &'static str {
        match self {
            UnsupportedReason::RoomDisabled => "room_disabled",
            UnsupportedReason::FeatureDisabled => "feature_disabled",
        }
    }
}

/// Detail for a `fail` verdict: the stage that failed, a machine-readable cause
/// code, and one plain-language next step (R5.8). The struct holds no chat
/// body, generated reply, prompt, URL, absolute path, or account identifier.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FailDetail {
    /// The processing stage that failed.
    pub stage: Stage,
    /// A short, stable cause code (e.g. `timeout`).
    pub code: &'static str,
    /// One plain-language next action for the user.
    pub next_step: String,
}

impl FailDetail {
    /// Build a fail detail.
    pub fn new(stage: Stage, code: &'static str, next_step: impl Into<String>) -> Self {
        Self {
            stage,
            code,
            next_step: next_step.into(),
        }
    }

    /// The fail detail for a combination that exceeded its per-combination time
    /// budget (R5.12).
    pub fn timeout(stage: Stage) -> Self {
        Self::new(
            stage,
            "timeout",
            "점검이 정해진 시간 안에 끝나지 않았어요. 잠시 뒤 다시 점검해 주세요.",
        )
    }

    /// The fail detail for a combination that ended without reaching a result.
    fn no_result() -> Self {
        Self::new(
            Stage::Detect,
            "no_result",
            "점검이 결과를 만들지 못했어요. 자가 점검을 한 번 실행해 주세요.",
        )
    }
}

/// The verdict for one (room, feature) combination (R5.2).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Verdict {
    /// Processing reached a result or a send-ready state without error (R5.2).
    Pass,
    /// Processing should have worked but failed (R5.8).
    Fail(FailDetail),
    /// The feature is off for this room, or the room is not a target (R5.4).
    Unsupported(UnsupportedReason),
    /// A send feature was intentionally stopped by the safety gate; carries the
    /// mismatched safety-setting item names (R5.3).
    Blocked(Vec<&'static str>),
}

/// The candidate signals for one combination, before priority selection.
///
/// At most one of each candidate is carried; [`classify`] resolves them into a
/// single [`Verdict`] using the fixed priority order (R5.2).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct VerdictCandidates {
    /// Set when the feature is unsupported in this room (R5.4).
    pub unsupported: Option<UnsupportedReason>,
    /// Set when a send feature's gate decision was `Fenced`, carrying the
    /// mismatched safety-setting item names (R5.3).
    pub blocked: Option<Vec<&'static str>>,
    /// Set when processing failed (R5.8).
    pub failed: Option<FailDetail>,
    /// Whether processing reached a result / send-ready state (R5.2).
    pub reached_result: bool,
}

/// Resolve candidate signals into exactly one verdict (R5.2).
///
/// When two or more conditions hold, the priority is `unsupported` → `blocked`
/// → `fail` → `pass`. A combination that neither failed nor reached a result is
/// treated as a failure so the function is total (every input yields a verdict).
pub fn classify(c: VerdictCandidates) -> Verdict {
    if let Some(reason) = c.unsupported {
        return Verdict::Unsupported(reason);
    }
    if let Some(items) = c.blocked {
        return Verdict::Blocked(items);
    }
    if let Some(detail) = c.failed {
        return Verdict::Fail(detail);
    }
    if c.reached_result {
        Verdict::Pass
    } else {
        Verdict::Fail(FailDetail::no_result())
    }
}

/// Map a fenced safety-gate reason to the mismatched safety-setting item names
/// a `blocked` verdict reports (R5.3).
pub fn fenced_items(reason: &FenceReason) -> Vec<&'static str> {
    let item = match reason {
        FenceReason::AxSendDisabled => "allow_ax_send",
        FenceReason::AutoReplyDisabled => "allow_auto_reply",
        FenceReason::ChatNotAllowlisted => "allowed_send_chats",
        FenceReason::IdentityMismatch => "authority_identity",
        FenceReason::StateMismatch => "authority_state",
    };
    vec![item]
}

// ---------------------------------------------------------------------------
// RoomKey
// ---------------------------------------------------------------------------

/// How a room is displayed in the report. Each room is shown by its real title,
/// but when a title is empty or shared by two or more rooms the identifier is
/// carried alongside so the rooms can still be told apart (R5.11).
///
/// `chat_id` is the first field so the `Ord` derive orders rooms by their
/// unique identifier, which keeps the matrix deterministic and every label
/// distinct.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub struct RoomKey {
    /// The database-authoritative chat id.
    pub chat_id: i64,
    /// The real room title (may be empty).
    pub title: String,
    /// Whether the label must carry the identifier to stay distinct (R5.11).
    pub needs_id_suffix: bool,
}

impl RoomKey {
    /// Build one key per room, marking a key as needing the identifier suffix
    /// when its trimmed title is empty or duplicated (R5.11).
    pub fn for_rooms(rooms: &[CatalogRoom]) -> Vec<RoomKey> {
        let mut title_counts: BTreeMap<&str, usize> = BTreeMap::new();
        for room in rooms {
            *title_counts.entry(room.title.trim()).or_insert(0) += 1;
        }
        rooms
            .iter()
            .map(|room| {
                let trimmed = room.title.trim();
                let duplicated = title_counts.get(trimmed).copied().unwrap_or(0) > 1;
                RoomKey {
                    chat_id: room.chat_id,
                    title: room.title.clone(),
                    needs_id_suffix: trimmed.is_empty() || duplicated,
                }
            })
            .collect()
    }

    /// The display label for this room: the real title verbatim, or the title
    /// with its identifier when a bare title would be ambiguous (R5.11).
    pub fn label(&self) -> String {
        let trimmed = self.title.trim();
        if !self.needs_id_suffix {
            return self.title.clone();
        }
        if trimmed.is_empty() {
            format!("#{}", self.chat_id)
        } else {
            format!("{} (#{})", trimmed, self.chat_id)
        }
    }
}

// ---------------------------------------------------------------------------
// Counts and matrix
// ---------------------------------------------------------------------------

/// The four verdict counts (R5.5).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Counts {
    /// `pass` count.
    pub pass: usize,
    /// `fail` count.
    pub fail: usize,
    /// `unsupported` count.
    pub unsupported: usize,
    /// `blocked` count.
    pub blocked: usize,
}

impl Counts {
    /// Add one verdict to the tally.
    fn tally(&mut self, verdict: &Verdict) {
        match verdict {
            Verdict::Pass => self.pass += 1,
            Verdict::Fail(_) => self.fail += 1,
            Verdict::Unsupported(_) => self.unsupported += 1,
            Verdict::Blocked(_) => self.blocked += 1,
        }
    }

    /// The sum of the four counts.
    pub fn sum(&self) -> usize {
        self.pass + self.fail + self.unsupported + self.blocked
    }
}

/// The matrix-shaped coverage report (R5.5).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CoverageMatrix {
    /// Total combinations = number of rooms × 6 (R5.1).
    pub total: usize,
    /// The overall four-count tally.
    pub totals: Counts,
    /// Every combination's verdict, keyed by `(RoomKey, Feature)` so the
    /// ordering is deterministic regardless of worker scheduling (R5.14).
    pub cells: BTreeMap<(RoomKey, Feature), Verdict>,
    /// Per-room subtotals (R5.5).
    pub by_room: BTreeMap<RoomKey, Counts>,
    /// Per-feature subtotals (R5.5).
    pub by_feature: BTreeMap<Feature, Counts>,
    /// Whether the four counts sum to `total` (R5.5).
    pub sums_match_total: bool,
}

impl CoverageMatrix {
    /// Whether the whole run passed: the four counts sum to the total, no
    /// combination failed, and at least one combination exists (R5.9, R5.13).
    pub fn overall_pass(&self) -> bool {
        self.total > 0 && self.sums_match_total && self.totals.fail == 0
    }
}

// ---------------------------------------------------------------------------
// Budget and probe
// ---------------------------------------------------------------------------

/// The configurable time bounds a coverage run respects (R5.6, R5.12).
///
/// The verifier never sleeps to enforce these; it compares a combination's
/// reported work time against `per_combination_ms` and judges an over-budget
/// combination `timeout`/`fail`, so tests can drive the bound with fabricated
/// durations.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CoverageBudget {
    /// The per-combination bound in milliseconds (R5.12, default 10 s).
    pub per_combination_ms: u64,
    /// The whole-run bound in milliseconds for 192 combinations (R5.6,
    /// default 60 s).
    pub total_ms: u64,
}

impl Default for CoverageBudget {
    fn default() -> Self {
        Self {
            per_combination_ms: 10_000,
            total_ms: 60_000,
        }
    }
}

/// What one processing attempt of a (room, feature) combination observed.
///
/// A probe must never perform a real send or any network egress; it only
/// reports the raw signals the verifier turns into a [`Verdict`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProbeSignal {
    /// Set when a send feature's gate decision was `Fenced`, carrying the
    /// mismatched safety-setting item names (R5.3).
    pub fenced: Option<Vec<&'static str>>,
    /// Set when processing failed at a stage (R5.8).
    pub failed: Option<FailDetail>,
    /// Whether processing reached a result / send-ready state (R5.2).
    pub reached_result: bool,
    /// The furthest stage the attempt reached (attributed to a timeout fail).
    pub reached_stage: Stage,
    /// How long the attempt took, in milliseconds (checked against the
    /// per-combination budget, R5.12).
    pub elapsed_ms: u64,
}

impl ProbeSignal {
    /// A combination that reached a result cleanly.
    pub fn passed(elapsed_ms: u64) -> Self {
        Self {
            fenced: None,
            failed: None,
            reached_result: true,
            reached_stage: Stage::Commit,
            elapsed_ms,
        }
    }

    /// A combination the safety gate fenced (send features only, R5.3).
    pub fn blocked(items: Vec<&'static str>, elapsed_ms: u64) -> Self {
        Self {
            fenced: Some(items),
            failed: None,
            reached_result: false,
            reached_stage: Stage::Authorize,
            elapsed_ms,
        }
    }

    /// A combination that failed at a stage (R5.8).
    pub fn failed(detail: FailDetail, elapsed_ms: u64) -> Self {
        let stage = detail.stage;
        Self {
            fenced: None,
            failed: Some(detail),
            reached_result: false,
            reached_stage: stage,
            elapsed_ms,
        }
    }
}

/// The seam that runs one (room, feature) combination through the same path
/// production uses, over a fully fake world (R5.7). Implementations must be
/// [`Sync`] so cells can be evaluated in the worker pool.
pub trait CellProbe: Sync {
    /// Attempt processing of one combination and report its raw signals. This
    /// is only called for a supported combination — an `unsupported` verdict is
    /// decided without any attempt (R5.4).
    fn probe(&self, room: &CatalogRoom, feature: Feature) -> ProbeSignal;
}

// ---------------------------------------------------------------------------
// Verifier
// ---------------------------------------------------------------------------

/// Decide whether `feature` is supported in `room`, or why it is not (R5.4).
///
/// The two live-ops toggles [`CatalogRoom::link_forward`] and
/// [`CatalogRoom::telegram_relay`] gate their respective features here, and the
/// read-only features (context search, style application) are supported in any
/// enabled room.
fn feature_support(room: &CatalogRoom, feature: Feature) -> Option<UnsupportedReason> {
    if !room.enabled {
        return Some(UnsupportedReason::RoomDisabled);
    }
    let enabled = match feature {
        Feature::AutoReply => room.auto_reply,
        Feature::GeekNews => room.geeknews,
        Feature::LinkForward => room.link_forward,
        Feature::TelegramRelay => room.telegram_relay,
        Feature::ContextSearch | Feature::StyleApply => true,
    };
    if enabled {
        None
    } else {
        Some(UnsupportedReason::FeatureDisabled)
    }
}

/// Evaluate one cell: decide support without attempting (R5.4), otherwise probe
/// and apply the per-combination timeout bound (R5.12), then classify (R5.2).
fn evaluate_cell(
    room: &CatalogRoom,
    feature: Feature,
    probe: &(dyn CellProbe),
    budget: &CoverageBudget,
) -> Verdict {
    if let Some(reason) = feature_support(room, feature) {
        // R5.4: an unsupported combination is judged with no processing attempt.
        return classify(VerdictCandidates {
            unsupported: Some(reason),
            ..VerdictCandidates::default()
        });
    }

    let signal = probe.probe(room, feature);
    let mut failed = signal.failed;
    if failed.is_none() && signal.elapsed_ms > budget.per_combination_ms {
        // R5.12: over the per-combination bound is a timeout failure.
        failed = Some(FailDetail::timeout(signal.reached_stage));
    }
    classify(VerdictCandidates {
        unsupported: None,
        blocked: signal.fenced,
        failed,
        reached_result: signal.reached_result,
    })
}

/// The feature-coverage verifier (R5).
///
/// Holds the rooms to check, the [`CellProbe`] that runs each combination over
/// a fake world, and the time [`CoverageBudget`]. [`Self::verify`] evaluates
/// every combination in a bounded worker pool and rolls the verdicts up into a
/// [`CoverageMatrix`].
pub struct CoverageVerifier<'a> {
    rooms: Vec<CatalogRoom>,
    probe: &'a (dyn CellProbe + 'a),
    budget: CoverageBudget,
    max_workers: usize,
}

impl<'a> CoverageVerifier<'a> {
    /// Build a verifier over `rooms` with the default budget and worker cap.
    pub fn new(rooms: Vec<CatalogRoom>, probe: &'a (dyn CellProbe + 'a)) -> Self {
        Self {
            rooms,
            probe,
            budget: CoverageBudget::default(),
            max_workers: MAX_WORKERS,
        }
    }

    /// Override the time budget (R5.6, R5.12).
    pub fn with_budget(mut self, budget: CoverageBudget) -> Self {
        self.budget = budget;
        self
    }

    /// Override the worker cap (bounded by [`MAX_WORKERS`]).
    pub fn with_max_workers(mut self, max_workers: usize) -> Self {
        self.max_workers = max_workers.clamp(1, MAX_WORKERS);
        self
    }

    /// Check every `방 × 6` combination and roll the verdicts up into a matrix
    /// (R5.1, R5.5).
    ///
    /// Cells are evaluated in a worker pool capped at [`Self::max_workers`], but
    /// each verdict is keyed by `(RoomKey, Feature)` into a [`BTreeMap`], so the
    /// matrix is identical regardless of which worker computed which cell
    /// (R5.14). Zero rooms yields `total == 0`, which never counts as an overall
    /// pass (R5.13).
    pub fn verify(&self) -> CoverageMatrix {
        let room_keys = RoomKey::for_rooms(&self.rooms);
        let total = self.rooms.len() * Feature::ALL.len();

        // Flatten every combination into an indexed task list.
        let tasks: Vec<(usize, Feature)> = (0..self.rooms.len())
            .flat_map(|room_idx| Feature::ALL.iter().map(move |&feature| (room_idx, feature)))
            .collect();

        let results: Vec<(usize, Feature, Verdict)> = if tasks.is_empty() {
            Vec::new()
        } else {
            let cursor = AtomicUsize::new(0);
            let worker_count = self.max_workers.clamp(1, MAX_WORKERS).min(tasks.len());
            let rooms = &self.rooms;
            let probe = self.probe;
            let budget = self.budget;
            let tasks_ref = &tasks;
            let cursor_ref = &cursor;

            std::thread::scope(|scope| {
                let handles: Vec<_> = (0..worker_count)
                    .map(|_| {
                        scope.spawn(move || {
                            // Each worker pulls the next task off the shared
                            // cursor until the list is drained.
                            let mut local: Vec<(usize, Feature, Verdict)> = Vec::new();
                            loop {
                                let i = cursor_ref.fetch_add(1, Ordering::Relaxed);
                                if i >= tasks_ref.len() {
                                    break;
                                }
                                let (room_idx, feature) = tasks_ref[i];
                                let verdict =
                                    evaluate_cell(&rooms[room_idx], feature, probe, &budget);
                                local.push((room_idx, feature, verdict));
                            }
                            local
                        })
                    })
                    .collect();

                handles
                    .into_iter()
                    .flat_map(|h| h.join().unwrap_or_default())
                    .collect()
            })
        };

        // Insert every verdict into sorted maps. BTreeMap keying makes the
        // ordering — and the whole matrix — independent of worker scheduling.
        let mut cells: BTreeMap<(RoomKey, Feature), Verdict> = BTreeMap::new();
        let mut by_room: BTreeMap<RoomKey, Counts> = BTreeMap::new();
        let mut by_feature: BTreeMap<Feature, Counts> = BTreeMap::new();
        let mut totals = Counts::default();

        // Seed every room and feature so subtotals are present even for an
        // all-unsupported room (they still get tallied below).
        for key in &room_keys {
            by_room.entry(key.clone()).or_default();
        }
        for feature in Feature::ALL {
            by_feature.entry(feature).or_default();
        }

        for (room_idx, feature, verdict) in results {
            let key = room_keys[room_idx].clone();
            totals.tally(&verdict);
            by_room.entry(key.clone()).or_default().tally(&verdict);
            by_feature.entry(feature).or_default().tally(&verdict);
            cells.insert((key, feature), verdict);
        }

        let sums_match_total = totals.sum() == total;

        CoverageMatrix {
            total,
            totals,
            cells,
            by_room,
            by_feature,
            sums_match_total,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn room(chat_id: i64, title: &str) -> CatalogRoom {
        CatalogRoom {
            chat_id,
            title: title.to_string(),
            enabled: true,
            auto_reply: false,
            geeknews: false,
            link_forward: false,
            telegram_relay: false,
        }
    }

    /// A deterministic probe driven by a fixed decision per feature so the
    /// verifier can be exercised without a real world.
    struct ScriptedProbe {
        signal: fn(Feature) -> ProbeSignal,
    }

    impl CellProbe for ScriptedProbe {
        fn probe(&self, _room: &CatalogRoom, feature: Feature) -> ProbeSignal {
            (self.signal)(feature)
        }
    }

    // ---- classify: priority ----

    #[test]
    fn classify_unsupported_wins_over_everything() {
        let verdict = classify(VerdictCandidates {
            unsupported: Some(UnsupportedReason::FeatureDisabled),
            blocked: Some(vec!["allow_ax_send"]),
            failed: Some(FailDetail::timeout(Stage::Model)),
            reached_result: true,
        });
        assert_eq!(
            verdict,
            Verdict::Unsupported(UnsupportedReason::FeatureDisabled)
        );
    }

    #[test]
    fn classify_blocked_wins_over_fail_and_pass() {
        let verdict = classify(VerdictCandidates {
            unsupported: None,
            blocked: Some(vec!["allow_auto_reply"]),
            failed: Some(FailDetail::timeout(Stage::Model)),
            reached_result: true,
        });
        assert_eq!(verdict, Verdict::Blocked(vec!["allow_auto_reply"]));
    }

    #[test]
    fn classify_fail_wins_over_pass() {
        let verdict = classify(VerdictCandidates {
            unsupported: None,
            blocked: None,
            failed: Some(FailDetail::new(Stage::Retrieve, "boom", "확인해 주세요.")),
            reached_result: true,
        });
        assert!(matches!(verdict, Verdict::Fail(d) if d.code == "boom"));
    }

    #[test]
    fn classify_pass_when_only_reached_result() {
        let verdict = classify(VerdictCandidates {
            reached_result: true,
            ..VerdictCandidates::default()
        });
        assert_eq!(verdict, Verdict::Pass);
    }

    #[test]
    fn classify_no_result_is_a_fail_with_next_step() {
        let verdict = classify(VerdictCandidates::default());
        match verdict {
            Verdict::Fail(detail) => {
                assert_eq!(detail.code, "no_result");
                assert!(!detail.next_step.is_empty());
            }
            other => panic!("expected fail, got {other:?}"),
        }
    }

    // ---- feature_support: toggles gate unsupported ----

    #[test]
    fn support_uses_the_two_new_toggles() {
        let mut r = room(1, "room");
        // Every send feature is off ⇒ unsupported; read features always on.
        assert_eq!(
            feature_support(&r, Feature::LinkForward),
            Some(UnsupportedReason::FeatureDisabled)
        );
        assert_eq!(
            feature_support(&r, Feature::TelegramRelay),
            Some(UnsupportedReason::FeatureDisabled)
        );
        assert_eq!(feature_support(&r, Feature::ContextSearch), None);
        assert_eq!(feature_support(&r, Feature::StyleApply), None);

        r.link_forward = true;
        r.telegram_relay = true;
        assert_eq!(feature_support(&r, Feature::LinkForward), None);
        assert_eq!(feature_support(&r, Feature::TelegramRelay), None);
    }

    #[test]
    fn disabled_room_makes_every_feature_unsupported() {
        let mut r = room(1, "room");
        r.enabled = false;
        r.auto_reply = true;
        r.link_forward = true;
        for feature in Feature::ALL {
            assert_eq!(
                feature_support(&r, feature),
                Some(UnsupportedReason::RoomDisabled)
            );
        }
    }

    // ---- RoomKey: labels ----

    #[test]
    fn room_labels_carry_id_when_empty_or_duplicated() {
        let rooms = vec![room(1, "스터디"), room(2, "스터디"), room(3, ""), room(4, "혼자")];
        let keys = RoomKey::for_rooms(&rooms);
        // Duplicated title ⇒ suffixed.
        assert_eq!(keys[0].label(), "스터디 (#1)");
        assert_eq!(keys[1].label(), "스터디 (#2)");
        // Empty title ⇒ identifier only.
        assert_eq!(keys[2].label(), "#3");
        // Unique title ⇒ verbatim.
        assert_eq!(keys[3].label(), "혼자");

        // Labels are all distinct (R5.11, Property 16 label-count rule).
        let labels: std::collections::BTreeSet<_> =
            keys.iter().map(|k| k.label()).collect();
        assert_eq!(labels.len(), keys.len());
    }

    // ---- verify: counting, sums, overall pass ----

    fn all_pass(_f: Feature) -> ProbeSignal {
        ProbeSignal::passed(5)
    }

    #[test]
    fn verify_counts_sum_to_total_and_pass_when_no_fail() {
        // Two rooms fully opted in ⇒ every send feature supported.
        let mut a = room(1, "a");
        a.auto_reply = true;
        a.geeknews = true;
        a.link_forward = true;
        a.telegram_relay = true;
        let mut b = room(2, "b");
        b.auto_reply = true;
        b.geeknews = true;
        b.link_forward = true;
        b.telegram_relay = true;

        let probe = ScriptedProbe { signal: all_pass };
        let matrix = CoverageVerifier::new(vec![a, b], &probe).verify();

        assert_eq!(matrix.total, 12);
        assert_eq!(matrix.totals.sum(), 12);
        assert!(matrix.sums_match_total);
        assert_eq!(matrix.totals.pass, 12);
        assert_eq!(matrix.totals.fail, 0);
        assert!(matrix.overall_pass());
        // Per-room and per-feature subtotals also sum to their slice.
        assert_eq!(matrix.by_room.len(), 2);
        assert_eq!(matrix.by_feature.len(), 6);
        for counts in matrix.by_room.values() {
            assert_eq!(counts.sum(), 6);
        }
    }

    #[test]
    fn verify_unsupported_features_counted_without_probe() {
        // A room with no toggles on: the four send features are unsupported,
        // the two read features pass.
        let probe = ScriptedProbe { signal: all_pass };
        let matrix = CoverageVerifier::new(vec![room(1, "a")], &probe).verify();

        assert_eq!(matrix.total, 6);
        assert_eq!(matrix.totals.unsupported, 4);
        assert_eq!(matrix.totals.pass, 2);
        assert!(matrix.sums_match_total);
        // No fails ⇒ overall pass even with unsupported cells (R5.9).
        assert!(matrix.overall_pass());
    }

    #[test]
    fn verify_blocked_send_feature_is_counted_blocked_not_fail() {
        fn signal(f: Feature) -> ProbeSignal {
            if f.performs_send() {
                ProbeSignal::blocked(vec!["allow_ax_send"], 3)
            } else {
                ProbeSignal::passed(3)
            }
        }
        let mut r = room(1, "a");
        r.auto_reply = true;
        r.geeknews = true;
        r.link_forward = true;
        r.telegram_relay = true;
        let probe = ScriptedProbe { signal };
        let matrix = CoverageVerifier::new(vec![r], &probe).verify();

        assert_eq!(matrix.totals.blocked, 4);
        assert_eq!(matrix.totals.fail, 0);
        assert_eq!(matrix.totals.pass, 2);
        // Blocked but no fail ⇒ still an overall pass (R5.3/R5.9).
        assert!(matrix.overall_pass());
    }

    #[test]
    fn verify_timeout_over_budget_is_a_fail() {
        fn slow(f: Feature) -> ProbeSignal {
            // AutoReply reaches a result but takes too long; the always-supported
            // read features finish inside the budget.
            if f == Feature::AutoReply {
                ProbeSignal {
                    fenced: None,
                    failed: None,
                    reached_result: true,
                    reached_stage: Stage::Model,
                    elapsed_ms: 20_000,
                }
            } else {
                ProbeSignal::passed(5)
            }
        }
        let mut r = room(1, "a");
        r.auto_reply = true;
        let probe = ScriptedProbe { signal: slow };
        let matrix = CoverageVerifier::new(vec![r], &probe)
            .with_budget(CoverageBudget {
                per_combination_ms: 10_000,
                total_ms: 60_000,
            })
            .verify();

        // AutoReply timed out ⇒ fail; the run is not an overall pass (R5.12).
        let key = RoomKey::for_rooms(&[room(1, "a")])[0].clone();
        assert_eq!(
            matrix.cells.get(&(key, Feature::AutoReply)),
            Some(&Verdict::Fail(FailDetail::timeout(Stage::Model)))
        );
        assert_eq!(matrix.totals.fail, 1);
        assert!(!matrix.overall_pass());
    }

    // ---- verify: zero rooms and determinism ----

    #[test]
    fn zero_rooms_is_total_zero_and_never_overall_pass() {
        let probe = ScriptedProbe { signal: all_pass };
        let matrix = CoverageVerifier::new(Vec::new(), &probe).verify();
        assert_eq!(matrix.total, 0);
        assert!(matrix.sums_match_total); // 0 == 0
        assert!(!matrix.overall_pass()); // but zero rooms never passes (R5.13)
    }

    #[test]
    fn verify_is_deterministic_across_runs_and_worker_counts() {
        // 32 rooms with mixed toggles exercises the worker pool.
        let rooms: Vec<CatalogRoom> = (1..=32)
            .map(|i| {
                let mut r = room(i, if i % 5 == 0 { "" } else { "room" });
                r.auto_reply = i % 2 == 0;
                r.geeknews = i % 3 == 0;
                r.link_forward = i % 4 == 0;
                r.telegram_relay = i % 6 == 0;
                r
            })
            .collect();

        fn mixed(f: Feature) -> ProbeSignal {
            match f {
                Feature::GeekNews => ProbeSignal::blocked(vec!["allowed_send_chats"], 1),
                Feature::TelegramRelay => {
                    ProbeSignal::failed(FailDetail::new(Stage::Model, "x", "고쳐 주세요."), 1)
                }
                _ => ProbeSignal::passed(1),
            }
        }

        let probe = ScriptedProbe { signal: mixed };
        let first = CoverageVerifier::new(rooms.clone(), &probe)
            .with_max_workers(8)
            .verify();
        let second = CoverageVerifier::new(rooms.clone(), &probe)
            .with_max_workers(1)
            .verify();

        // Same rooms + same probe ⇒ identical matrix regardless of worker count
        // (R5.14).
        assert_eq!(first, second);
        assert_eq!(first.total, 32 * 6);
        assert!(first.sums_match_total);
        assert_eq!(first.by_room.len(), 32);
    }
}
