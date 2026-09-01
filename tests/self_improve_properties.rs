//! Property-based tests for the self-improver (task 9.1).
//!
//! Feature: kakao-agent-live-ops
//! Property 27: 문제 신호 감지 경계와 개선 착수 규칙
//!   (problem-signal detection boundaries + improvement-start rule)
//! Property 28: 홀드아웃 분리
//!   (holdout separation)
//! Property 29: 승격 게이트 동치
//!   (promotion-gate equivalence)
//! Property 30: 롤백 왕복 복원
//!   (rollback round-trip restoration)
//! Property 31: 개선 후보의 안전설정 불가침
//!   (improvement candidate safety-setting immutability)
//!
//! The whole self-improvement path is local-only: detection, diagnosis,
//! evaluation, promotion, and rollback never transmit. Every property that
//! drives a [`SelfImprover`] injects a [`ForbiddenNetwork`] and asserts
//! `egress_count() == 0` throughout — the observation layer of the local-only
//! guarantee (R8.15, R12.9). Quality scores come from the local
//! [`experiment::score_reply`] seam, never an external judge.
//!
//! Validates: Requirements 8.1, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9, 8.10, 8.12,
//! 8.13, 8.14, 8.17, 8.19, 8.20, 12.9, 12.10, 12.11, 12.12

use std::collections::BTreeSet;

use openkakao_cli::experiment::StyleTarget;
use openkakao_cli::fakes::{ForbiddenNetwork, VirtualClock};
use openkakao_cli::improve::{
    detect_signals, promotion_gate, resolve_drift_threshold, EvalSummary, FewShotSelection,
    GeneratedReply, HoldoutSet, ImproveStore, InMemoryKnobStore, KnobKey, KnobPatch, KnobStore,
    Knobs, KnobsDigest, PromotionOutcome, QaSample, RejectReason, ReplyModel, ReplyObservation,
    RetrievalWeights, SelfImprover, SignalKind, SignalRecord, SqliteImproveStore, StyleParams,
    ATTEMPT_CAP, LOW_QUALITY_BELOW, MAX_HOLDOUT, MAX_LATENCY_RATIO, MIN_HOLDOUT_FOR_EVAL,
    MIN_QUALITY_GAIN, OWNER_CORRECTION_WITHIN_SECS, REASK_WITHIN_SECS, REGRESSION_BASELINE_MIN,
    REGRESSION_DROP_OVER, REPEAT_COUNT_MIN, ROLLBACK_DROP, WATCH_WINDOW,
};
use openkakao_cli::ports::{Clock, NetworkPort};
use proptest::prelude::*;

// ===========================================================================
// Shared fixtures
// ===========================================================================

/// The five signal kinds in a fixed order, so per-kind plans line up.
const KINDS: [SignalKind; 5] = [
    SignalKind::LowQuality,
    SignalKind::Reask,
    SignalKind::RepeatQuestion,
    SignalKind::OwnerCorrection,
    SignalKind::TopicDrift,
];

/// The four knob keys — the only things an improvement can ever change.
const ALL_KNOB_KEYS: [KnobKey; 4] = [
    KnobKey::PromptVersion,
    KnobKey::StyleParams,
    KnobKey::RetrievalWeights,
    KnobKey::FewShot,
];

/// Safety-adjacent / database-authority keys that a candidate patch must never
/// be able to carry into a [`Knobs`] (R8.7, R12.10).
const SAFETY_KEYS: [&str; 6] = [
    "allow_ax_send",
    "allow_auto_reply",
    "allowed_send_chats",
    "allow_loco_write",
    "db_authority",
    "allow_non_interactive_send",
];

/// A deterministic, distinct knob configuration for index `i`. Every field is
/// valid ([`validate_knobs`] passes) and the prompt version makes each variant
/// unique.
fn knob_variant(i: usize) -> Knobs {
    Knobs {
        prompt_version: format!("v{i}"),
        style_params: StyleParams {
            honorific_bias: (i as f32) * 0.1,
            formality_bias: 0.5,
            target_len: (i as u32) + 1,
        },
        retrieval_weights: RetrievalWeights {
            recency: 0.3,
            similarity: 0.5,
            recipient: 0.2,
        },
        few_shot: FewShotSelection {
            example_pids: vec![format!("pid-{i}")],
            count: 1,
        },
    }
}

/// A trivial local reply model — enough to construct a [`SelfImprover`]. The
/// promotion properties feed pre-built [`EvalSummary`] values, so this only
/// needs to be a valid, network-free seam.
struct FixedReplyModel;

impl ReplyModel for FixedReplyModel {
    fn generate(&self, _knobs: &Knobs, _sample: &QaSample) -> GeneratedReply {
        GeneratedReply {
            text: "네 알겠습니다".to_string(),
            latency_ms: 10,
        }
    }
}

/// `n` distinct QA samples.
fn samples(n: usize) -> Vec<QaSample> {
    (0..n)
        .map(|i| QaSample {
            qa_pair_id: i as i64,
            sample_pid: format!("s{i}"),
            question: format!("q{i}"),
        })
        .collect()
}

/// Build an [`EvalSummary`] from a mean, a P95, and per-pid scores.
fn eval(mean: f32, p95: u64, scores: &[(String, u8)]) -> EvalSummary {
    EvalSummary {
        mean_quality: mean,
        p95_latency_ms: p95,
        evaluated: scores.len().max(1),
        scores: scores.iter().cloned().collect(),
    }
}

/// The mean of a `u8` slice (mirrors the improver's own `mean_u8`).
fn mean_u8(scores: &[u8]) -> f32 {
    if scores.is_empty() {
        return 0.0;
    }
    let sum: u64 = scores.iter().map(|&s| s as u64).sum();
    sum as f32 / scores.len() as f32
}

// ===========================================================================
// Property 27: detection boundaries + improvement-start rule.
// ===========================================================================

/// An arbitrary reply observation that straddles every documented boundary:
/// quality around 60, re-ask around 120s (including out-of-window and negative),
/// repeat count around 3, owner correction around 300s, and topic similarity
/// across the whole 0..=1 range.
fn observation_strategy() -> impl Strategy<Value = ReplyObservation> {
    (
        0u8..=100,
        prop::option::of(-30i64..200),
        0u32..6,
        prop::option::of(-30i64..400),
        0.0f32..=1.0,
        1i64..1_000_000,
    )
        .prop_map(|(quality, reask, repeat, owner, sim, at)| ReplyObservation {
            room_pid: "room-1".to_string(),
            at,
            quality_score: quality,
            reask_secs: reask,
            repeat_count: repeat,
            owner_correction_secs: owner,
            topic_similarity: sim,
        })
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// Property 27 (detection boundaries): the detected signal set equals the
    /// five threshold predicates exactly — quality < 60, re-ask within 120s,
    /// repeat ≥ 3, owner correction within 300s, topic similarity < the
    /// resolved drift threshold — recomputed here from the module constants
    /// rather than the implementation's control flow (R8.1).
    ///
    /// Validates: Requirements 8.1
    #[test]
    fn detected_signals_match_thresholds_exactly(
        obs in observation_strategy(),
        raw_drift in prop::option::of(0.0f32..=1.0),
    ) {
        let drift = resolve_drift_threshold(raw_drift);
        // The resolved threshold is always in the documented band or the default.
        prop_assert!((0.30..=0.90).contains(&drift) || drift == 0.50);

        let detected: BTreeSet<SignalKind> =
            detect_signals(&obs, drift).into_iter().map(|(k, _)| k).collect();

        // Independent oracle from the constants (R8.1).
        let mut expected = BTreeSet::new();
        if obs.quality_score < LOW_QUALITY_BELOW {
            expected.insert(SignalKind::LowQuality);
        }
        if let Some(s) = obs.reask_secs {
            if (0..=REASK_WITHIN_SECS).contains(&s) {
                expected.insert(SignalKind::Reask);
            }
        }
        if obs.repeat_count >= REPEAT_COUNT_MIN {
            expected.insert(SignalKind::RepeatQuestion);
        }
        if let Some(s) = obs.owner_correction_secs {
            if (0..=OWNER_CORRECTION_WITHIN_SECS).contains(&s) {
                expected.insert(SignalKind::OwnerCorrection);
            }
        }
        if obs.topic_similarity < drift {
            expected.insert(SignalKind::TopicDrift);
        }

        prop_assert_eq!(detected, expected);

        // Every threshold code is non-empty and carries no raw content (R8.2).
        for (_, code) in detect_signals(&obs, drift) {
            prop_assert!(!code.is_empty());
            prop_assert!(!code.contains("http"));
        }
    }
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// Property 27 (start rule): over a fresh 24h window, `should_start` is true
    /// iff there are ≥2 signals of one kind or ≥2 distinct kinds, after
    /// excluding any kind marked as a false positive ≥3 times. Before any signal
    /// is recorded the count is zero (no start). When it starts, the diagnosis
    /// is a non-empty subset of the four knob keys (R8.3, R8.4, R8.5). No
    /// network egress occurs.
    ///
    /// Validates: Requirements 8.3, 8.4, 8.5, 12.9
    #[test]
    fn should_start_matches_accumulation_rule(
        // Per kind, in KINDS order: (dismissed-to-cap, fresh active count 0..=3).
        plan in (
            (any::<bool>(), 0u32..=3),
            (any::<bool>(), 0u32..=3),
            (any::<bool>(), 0u32..=3),
            (any::<bool>(), 0u32..=3),
            (any::<bool>(), 0u32..=3),
        ),
    ) {
        let store = SqliteImproveStore::open_in_memory().unwrap();
        let knob_store = InMemoryKnobStore::new(knob_variant(1)).unwrap();
        let model = FixedReplyModel;
        let net = ForbiddenNetwork::new();
        let clock = VirtualClock::new(1_000_000);
        let improver = SelfImprover::new(
            &store, &knob_store, &model, &net, &clock, StyleTarget::default(), None,
        );

        // Before any signal, there is no reason to start (R8.3).
        prop_assert!(!improver.should_start("room-1").unwrap());

        let plan = [plan.0, plan.1, plan.2, plan.3, plan.4];
        let now = clock.now_ms();

        for (kind, (dismiss_to_cap, count)) in KINDS.iter().zip(plan.iter()) {
            // Dismiss to the cap *before* recording, so the fresh signals below
            // stay active yet the kind is excluded by the false-positive cap.
            if *dismiss_to_cap {
                for _ in 0..3 {
                    store.dismiss("room-1", *kind).unwrap();
                }
            }
            for _ in 0..*count {
                store
                    .record_signal(&SignalRecord {
                        kind: *kind,
                        room_pid: "room-1".to_string(),
                        at: now,
                        threshold_code: "x".to_string(),
                        dismissed: false,
                    })
                    .unwrap();
            }
        }

        // Oracle: effective per-kind counts exclude capped kinds.
        let mut effective: Vec<u32> = Vec::new();
        for (dismiss_to_cap, count) in plan.iter() {
            if !*dismiss_to_cap && *count > 0 {
                effective.push(*count);
            }
        }
        let same_kind_two = effective.iter().any(|&c| c >= 2);
        let distinct_two = effective.len() >= 2;
        let expected = same_kind_two || distinct_two;

        prop_assert_eq!(improver.should_start("room-1").unwrap(), expected);

        // Whenever a start is warranted, the diagnosis is a non-empty subset of
        // the four knob keys with no duplicates (R8.5).
        if expected {
            let active = store.active_signals_since("room-1", 0).unwrap();
            let diag = improver.diagnose(&active);
            prop_assert!(!diag.causes.is_empty());
            let unique: BTreeSet<KnobKey> = diag.causes.iter().copied().collect();
            prop_assert_eq!(unique.len(), diag.causes.len());
            for key in &diag.causes {
                prop_assert!(ALL_KNOB_KEYS.contains(key));
            }
        }

        // Local-only: nothing left the machine (R8.15, R12.9).
        prop_assert_eq!(improver.net_egress(), 0);
        prop_assert_eq!(net.egress_count(), 0);
    }
}

// ===========================================================================
// Property 28: holdout separation.
// ===========================================================================

proptest! {
    #![proptest_config(ProptestConfig::with_cases(128))]

    /// Property 28: for any dataset size 0..=5000, the holdout is 20% capped at
    /// 500, the training and holdout sets are value-disjoint and together
    /// partition the whole dataset, and the split is deterministic for a given
    /// seed (R8.8).
    ///
    /// Validates: Requirements 8.8
    #[test]
    fn holdout_split_is_partition_capped_and_deterministic(
        size in 0usize..=5000,
        seed in any::<u64>(),
    ) {
        let expected_holdout = (size / 5).min(MAX_HOLDOUT);
        prop_assert_eq!(HoldoutSet::holdout_len_for(size), expected_holdout);

        let (train, holdout) = HoldoutSet::split(samples(size), seed);

        // Size: 20% capped at 500, remainder is training; together the whole set.
        prop_assert_eq!(holdout.len(), expected_holdout);
        prop_assert_eq!(train.len(), size - expected_holdout);
        prop_assert_eq!(train.len() + holdout.len(), size);

        // Zero intersection at the value level, and the union is exactly 0..size.
        let train_ids: BTreeSet<i64> = train.iter().map(|s| s.qa_pair_id).collect();
        let holdout_ids: BTreeSet<i64> =
            holdout.items.iter().map(|s| s.qa_pair_id).collect();
        prop_assert!(train_ids.is_disjoint(&holdout_ids));
        prop_assert_eq!(train_ids.len(), train.len()); // no dupes within training
        prop_assert_eq!(holdout_ids.len(), holdout.len()); // no dupes within holdout
        let union: BTreeSet<i64> = train_ids.union(&holdout_ids).copied().collect();
        let all_ids: BTreeSet<i64> = (0..size as i64).collect();
        prop_assert_eq!(union, all_ids);

        // Deterministic for the same seed.
        let (train2, holdout2) = HoldoutSet::split(samples(size), seed);
        prop_assert_eq!(train, train2);
        prop_assert_eq!(holdout.items, holdout2.items);
    }
}

// ===========================================================================
// Property 29: promotion-gate equivalence.
// ===========================================================================

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// Property 29: `promotion_gate` returns promote iff ALL six conditions hold
    /// (holdout ≥ 50 ∧ mean gain ≥ 5 ∧ P95 ≤ 120% ∧ zero regressions ∧ not in
    /// the rollback history ∧ attempts < 3). When it rejects, the reason is the
    /// first failing condition in the gate's precedence, and a regression is
    /// exactly "a ≥60 case dropping more than 10 points" (R8.9, R8.10, R8.14,
    /// R8.18, R8.19, R8.20, R12.11).
    ///
    /// Validates: Requirements 8.9, 8.10, 8.14, 8.17, 8.19, 8.20, 12.11
    #[test]
    fn promotion_gate_is_conjunction_with_reason_precedence(
        cur_mean in 0.0f32..=100.0,
        cand_mean in 0.0f32..=100.0,
        cur_p95 in 0u64..=400,
        cand_p95 in 0u64..=400,
        pairs in prop::collection::vec((0u8..=100, 0u8..=100), 0..=8),
        holdout_len in 0usize..=100,
        attempts in 0u8..=5,
        rolled in any::<bool>(),
    ) {
        // Distinct pids so both eval maps cover the same samples.
        let cur_scores: Vec<(String, u8)> = pairs
            .iter()
            .enumerate()
            .map(|(i, (b, _))| (format!("s{i}"), *b))
            .collect();
        let cand_scores: Vec<(String, u8)> = pairs
            .iter()
            .enumerate()
            .map(|(i, (_, a))| (format!("s{i}"), *a))
            .collect();

        let current = eval(cur_mean, cur_p95, &cur_scores);
        let candidate = eval(cand_mean, cand_p95, &cand_scores);

        let digest = KnobsDigest("d".to_string());
        let rolled_back: BTreeSet<KnobsDigest> = if rolled {
            [digest.clone()].into_iter().collect()
        } else {
            BTreeSet::new()
        };

        let result = promotion_gate(
            &current, &candidate, holdout_len, &rolled_back, &digest, attempts,
        );

        // --- Independent oracles for each condition ---
        let not_rolled = !rolled;
        let attempts_ok = attempts < ATTEMPT_CAP;
        let holdout_ok = holdout_len >= MIN_HOLDOUT_FOR_EVAL;
        let has_regression = pairs.iter().any(|(b, a)| {
            *b >= REGRESSION_BASELINE_MIN && (*b as i32 - *a as i32) > REGRESSION_DROP_OVER
        });
        let gain_ok = (cand_mean - cur_mean) >= MIN_QUALITY_GAIN;
        // Mirror the gate's latency ratio exactly (including the zero handling).
        let ratio = if cur_p95 == 0 {
            if cand_p95 == 0 { 1.0 } else { f32::INFINITY }
        } else {
            cand_p95 as f32 / cur_p95 as f32
        };
        let latency_ok = ratio <= MAX_LATENCY_RATIO;

        let all_ok = not_rolled
            && attempts_ok
            && holdout_ok
            && !has_regression
            && gain_ok
            && latency_ok;

        // Equivalence: promote iff every condition holds (R12.11).
        prop_assert_eq!(result.is_ok(), all_ok);

        // The reject reason follows the gate's precedence:
        // rolled-back → attempts → holdout → regressions → gain → latency.
        let expected_code: Option<&str> = if !not_rolled {
            Some("previously_rolled_back")
        } else if !attempts_ok {
            Some("attempt_cap_reached")
        } else if !holdout_ok {
            Some("insufficient_holdout")
        } else if has_regression {
            Some("regressions")
        } else if !gain_ok {
            Some("gain_too_small")
        } else if !latency_ok {
            Some("latency_regression")
        } else {
            None
        };

        match result {
            Ok(()) => prop_assert!(expected_code.is_none()),
            Err(reason) => prop_assert_eq!(Some(reason.code()), expected_code),
        }
    }
}

// ===========================================================================
// Property 30: rollback round-trip restoration.
// ===========================================================================

proptest! {
    #![proptest_config(ProptestConfig::with_cases(128))]

    /// Property 30 (manual round-trip): after applying a chain of distinct
    /// configurations, `rollback_to(g)` restores generation `g` exactly — all
    /// four knob values and the digest equal the target's. The prior
    /// generations stay queryable (R8.13, R12.12). No network egress occurs.
    ///
    /// Validates: Requirements 8.13, 12.12, 12.9
    #[test]
    fn rollback_to_restores_prior_generation_exactly(
        n in 2usize..=8,
        target_offset in 0usize..8,
    ) {
        let store = SqliteImproveStore::open_in_memory().unwrap();
        let knob_store = InMemoryKnobStore::new(knob_variant(0)).unwrap();
        let model = FixedReplyModel;
        let net = ForbiddenNetwork::new();
        let clock = VirtualClock::new(1_000);
        let mut improver = SelfImprover::new(
            &store, &knob_store, &model, &net, &clock, StyleTarget::default(), None,
        );

        // Generation g (1..=n) holds knob_variant(g - 1).
        for i in 1..n {
            knob_store.apply(knob_variant(i)).unwrap();
        }

        let target_gen = (target_offset % n) + 1; // 1..=n
        let restored = improver.rollback_to(target_gen as u64).unwrap();
        prop_assert!(restored >= n as u64); // rollback creates a fresh generation

        // The active configuration equals the target generation exactly.
        let expected = knob_variant(target_gen - 1);
        let (_, current) = knob_store.current();
        prop_assert_eq!(&current, &expected);
        prop_assert_eq!(current.digest(), expected.digest());

        // The prior generations remain queryable (bounded retention window).
        let gens = knob_store.generations(100).unwrap();
        prop_assert!(gens.iter().any(|g| g.generation == target_gen as u64));

        prop_assert_eq!(improver.net_egress(), 0);
        prop_assert_eq!(net.egress_count(), 0);
    }
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// Property 30 (auto rollback + re-promotion bar): after a clean promotion,
    /// an automatic rollback fires exactly when the recent-20 mean falls ≥5
    /// points below the pre-promotion baseline. When it does, the prior
    /// configuration is restored exactly and the rolled-back candidate is barred
    /// from ever being promoted again (`PreviouslyRolledBack`) (R8.12, R8.19,
    /// R8.20). No network egress occurs.
    ///
    /// Validates: Requirements 8.12, 8.19, 8.20, 12.9, 12.12
    #[test]
    fn auto_rollback_matches_drop_rule_and_bars_repromotion(
        baseline in prop::array::uniform20(0u8..=100),
        recent in prop::collection::vec(0u8..=100, 0..=25),
    ) {
        let store = SqliteImproveStore::open_in_memory().unwrap();
        let prior = knob_variant(1);
        let knob_store = InMemoryKnobStore::new(prior.clone()).unwrap();
        let model = FixedReplyModel;
        let net = ForbiddenNetwork::new();
        let clock = VirtualClock::new(1_000);
        let mut improver = SelfImprover::new(
            &store, &knob_store, &model, &net, &clock, StyleTarget::default(), None,
        );

        let cand = knob_variant(2);
        // A candidate that clears the gate: +10 mean, equal latency, holdout 50,
        // no regressions (empty score maps).
        let outcome = improver
            .promote(
                ("room-1".to_string(), SignalKind::LowQuality),
                &cand,
                &eval(70.0, 100, &[]),
                &eval(80.0, 100, &[]),
                50,
                &baseline,
            )
            .unwrap();
        let promoted = matches!(outcome, PromotionOutcome::Promoted { .. });
        prop_assert!(promoted);
        prop_assert_eq!(&knob_store.current().1, &cand);

        // Oracle: auto rollback iff a full recent window sits ≥5 below baseline.
        let baseline_mean = mean_u8(&baseline);
        let expect_rollback = recent.len() >= WATCH_WINDOW && {
            let window = &recent[recent.len() - WATCH_WINDOW..];
            mean_u8(window) < baseline_mean - ROLLBACK_DROP
        };

        let rolled = improver.watch_after_promotion(&recent).unwrap();
        prop_assert_eq!(rolled.is_some(), expect_rollback);

        if expect_rollback {
            // The prior configuration is restored exactly (round-trip, R12.12).
            prop_assert_eq!(&knob_store.current().1, &prior);
            prop_assert_eq!(knob_store.current().1.digest(), prior.digest());
            // The rolled-back candidate is recorded and barred from re-promotion.
            prop_assert!(store.rolled_back_digests().unwrap().contains(&cand.digest()));
            let repromote = improver
                .promote(
                    ("room-1".to_string(), SignalKind::LowQuality),
                    &cand,
                    &eval(70.0, 100, &[]),
                    &eval(95.0, 100, &[]),
                    50,
                    &baseline,
                )
                .unwrap();
            prop_assert_eq!(
                repromote,
                PromotionOutcome::Rejected(RejectReason::PreviouslyRolledBack)
            );
        } else {
            // Quality held: the promoted candidate stays active.
            prop_assert_eq!(&knob_store.current().1, &cand);
        }

        prop_assert_eq!(improver.net_egress(), 0);
        prop_assert_eq!(net.egress_count(), 0);
    }
}

// ===========================================================================
// Property 31: improvement candidate safety-setting immutability.
// ===========================================================================

/// Where an unknown/safety key is injected into an otherwise-valid patch.
#[derive(Debug, Clone, Copy)]
enum InjectAt {
    TopLevel,
    StyleParams,
    RetrievalWeights,
    FewShot,
}

fn inject_at_strategy() -> impl Strategy<Value = InjectAt> {
    prop_oneof![
        Just(InjectAt::TopLevel),
        Just(InjectAt::StyleParams),
        Just(InjectAt::RetrievalWeights),
        Just(InjectAt::FewShot),
    ]
}

/// A key guaranteed not to collide with any allowed knob field: either a
/// safety-adjacent name or a `zz_`-prefixed junk key.
fn unknown_key_strategy() -> impl Strategy<Value = String> {
    prop_oneof![
        (0usize..SAFETY_KEYS.len()).prop_map(|i| SAFETY_KEYS[i].to_string()),
        "zz_[a-z]{1,6}".prop_map(|s| s),
    ]
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// Property 31: any patch that adds an unknown/safety key — at the top level
    /// or nested inside a sub-object — is rejected with
    /// `RejectReason::SafetyChangeRequested` and never yields a [`Knobs`]. A
    /// clean patch parses back to the exact configuration, whose serialization
    /// carries no safety-key names (there is no safety field to carry), and the
    /// changed-key set between any two configurations is always a subset of the
    /// four knob keys (R8.6, R8.7, R12.10, R12.12).
    ///
    /// Validates: Requirements 8.6, 8.7, 12.10, 12.12
    #[test]
    fn safety_and_unknown_keys_are_rejected_never_building_knobs(
        idx in 0usize..8,
        other_idx in 0usize..8,
        at in inject_at_strategy(),
        key in unknown_key_strategy(),
    ) {
        let base = knob_variant(idx);
        let base_value = serde_json::to_value(&base).unwrap();

        // Control: a clean, untouched patch parses to the exact configuration.
        let parsed = KnobPatch::parse(&base_value).unwrap();
        prop_assert_eq!(&parsed, &base);

        // The configuration cannot even express a safety setting: none of the
        // safety-key names appear in its serialization (R8.6, R12.10).
        let json = base.to_json();
        for k in SAFETY_KEYS {
            prop_assert!(!json.contains(k), "knobs json leaked a safety key: {k}");
        }

        // Inject the unknown/safety key and re-parse.
        let mut tainted = base_value.clone();
        match at {
            InjectAt::TopLevel => {
                tainted
                    .as_object_mut()
                    .unwrap()
                    .insert(key.clone(), serde_json::json!(true));
            }
            InjectAt::StyleParams => {
                tainted["style_params"]
                    .as_object_mut()
                    .unwrap()
                    .insert(key.clone(), serde_json::json!(1));
            }
            InjectAt::RetrievalWeights => {
                tainted["retrieval_weights"]
                    .as_object_mut()
                    .unwrap()
                    .insert(key.clone(), serde_json::json!(1));
            }
            InjectAt::FewShot => {
                tainted["few_shot"]
                    .as_object_mut()
                    .unwrap()
                    .insert(key.clone(), serde_json::json!("x"));
            }
        }

        // The only entry point rejects it, and no Knobs is produced (R8.7).
        prop_assert_eq!(
            KnobPatch::parse(&tainted),
            Err(RejectReason::SafetyChangeRequested)
        );

        // The changed-key set between any two configurations is always a subset
        // of the four knob keys, with no duplicates (R12.12).
        let other = knob_variant(other_idx);
        let changed = base.changed_keys(&other);
        let unique: BTreeSet<KnobKey> = changed.iter().copied().collect();
        prop_assert_eq!(unique.len(), changed.len());
        for key in &changed {
            prop_assert!(ALL_KNOB_KEYS.contains(key));
        }
        // Distinct variants differ in at least the prompt version.
        if idx != other_idx {
            prop_assert!(changed.contains(&KnobKey::PromptVersion));
        }
    }
}
