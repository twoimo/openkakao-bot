//! Unit tests for the self-improver (task 9).

use super::*;
use crate::experiment::StyleTarget;
use crate::fakes::{ForbiddenNetwork, VirtualClock};

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

fn knobs(version: &str) -> Knobs {
    Knobs {
        prompt_version: version.to_string(),
        style_params: StyleParams {
            honorific_bias: 0.5,
            formality_bias: 0.5,
            target_len: 30,
        },
        retrieval_weights: RetrievalWeights {
            recency: 0.3,
            similarity: 0.5,
            recipient: 0.2,
        },
        few_shot: FewShotSelection {
            example_pids: vec!["pid-a".to_string(), "pid-b".to_string()],
            count: 2,
        },
    }
}

/// A trivial reply model returning a fixed, style-shaped reply. Only used to
/// exercise `evaluate`'s plumbing; gate tests build `EvalSummary` directly.
struct FakeReplyModel {
    reply: String,
    latency_ms: u64,
}

impl ReplyModel for FakeReplyModel {
    fn generate(&self, _knobs: &Knobs, _sample: &QaSample) -> GeneratedReply {
        GeneratedReply {
            text: self.reply.clone(),
            latency_ms: self.latency_ms,
        }
    }
}

fn eval(mean: f32, p95: u64, scores: &[(&str, u8)]) -> EvalSummary {
    EvalSummary {
        mean_quality: mean,
        p95_latency_ms: p95,
        evaluated: scores.len().max(1),
        scores: scores
            .iter()
            .map(|(pid, s)| (pid.to_string(), *s))
            .collect(),
    }
}

// ---------------------------------------------------------------------------
// resolve_drift_threshold / detect_signals
// ---------------------------------------------------------------------------

#[test]
fn drift_threshold_clamps_and_defaults() {
    assert_eq!(resolve_drift_threshold(None), DRIFT_DEFAULT_THRESHOLD);
    assert_eq!(resolve_drift_threshold(Some(0.10)), DRIFT_DEFAULT_THRESHOLD);
    assert_eq!(resolve_drift_threshold(Some(0.95)), DRIFT_DEFAULT_THRESHOLD);
    assert_eq!(resolve_drift_threshold(Some(0.30)), 0.30);
    assert_eq!(resolve_drift_threshold(Some(0.70)), 0.70);
    assert_eq!(resolve_drift_threshold(Some(0.90)), 0.90);
}

fn base_obs() -> ReplyObservation {
    ReplyObservation {
        room_pid: "room-1".to_string(),
        at: 1_000,
        quality_score: 80,
        reask_secs: None,
        repeat_count: 0,
        owner_correction_secs: None,
        topic_similarity: 0.9,
    }
}

#[test]
fn detect_low_quality_boundary() {
    let mut obs = base_obs();
    obs.quality_score = 59;
    let sigs: Vec<_> = detect_signals(&obs, 0.5).into_iter().map(|(k, _)| k).collect();
    assert!(sigs.contains(&SignalKind::LowQuality));
    obs.quality_score = 60;
    let sigs: Vec<_> = detect_signals(&obs, 0.5).into_iter().map(|(k, _)| k).collect();
    assert!(!sigs.contains(&SignalKind::LowQuality));
}

#[test]
fn detect_reask_within_window() {
    let mut obs = base_obs();
    obs.reask_secs = Some(120);
    let sigs: Vec<_> = detect_signals(&obs, 0.5).into_iter().map(|(k, _)| k).collect();
    assert!(sigs.contains(&SignalKind::Reask));
    obs.reask_secs = Some(121);
    let sigs: Vec<_> = detect_signals(&obs, 0.5).into_iter().map(|(k, _)| k).collect();
    assert!(!sigs.contains(&SignalKind::Reask));
}

#[test]
fn detect_repeat_question_threshold() {
    let mut obs = base_obs();
    obs.repeat_count = 2;
    assert!(!detect_signals(&obs, 0.5)
        .iter()
        .any(|(k, _)| *k == SignalKind::RepeatQuestion));
    obs.repeat_count = 3;
    assert!(detect_signals(&obs, 0.5)
        .iter()
        .any(|(k, _)| *k == SignalKind::RepeatQuestion));
}

#[test]
fn detect_owner_correction_window() {
    let mut obs = base_obs();
    obs.owner_correction_secs = Some(300);
    assert!(detect_signals(&obs, 0.5)
        .iter()
        .any(|(k, _)| *k == SignalKind::OwnerCorrection));
    obs.owner_correction_secs = Some(301);
    assert!(!detect_signals(&obs, 0.5)
        .iter()
        .any(|(k, _)| *k == SignalKind::OwnerCorrection));
}

#[test]
fn detect_topic_drift_below_threshold() {
    let mut obs = base_obs();
    obs.topic_similarity = 0.49;
    assert!(detect_signals(&obs, 0.5)
        .iter()
        .any(|(k, _)| *k == SignalKind::TopicDrift));
    obs.topic_similarity = 0.50;
    assert!(!detect_signals(&obs, 0.5)
        .iter()
        .any(|(k, _)| *k == SignalKind::TopicDrift));
}

#[test]
fn signal_kind_roundtrip() {
    for kind in [
        SignalKind::LowQuality,
        SignalKind::Reask,
        SignalKind::RepeatQuestion,
        SignalKind::OwnerCorrection,
        SignalKind::TopicDrift,
    ] {
        assert_eq!(SignalKind::parse(kind.as_str()), Some(kind));
    }
    assert_eq!(SignalKind::parse("nope"), None);
}

#[test]
fn threshold_code_carries_no_raw_content() {
    let mut obs = base_obs();
    obs.quality_score = 10;
    obs.reask_secs = Some(5);
    obs.repeat_count = 4;
    obs.owner_correction_secs = Some(10);
    obs.topic_similarity = 0.1;
    for (_, code) in detect_signals(&obs, 0.5) {
        assert!(!code.contains("http"));
        assert!(!code.contains('/') || code.starts_with("owner") || code.contains('@'));
        assert!(!code.is_empty());
    }
}

// ---------------------------------------------------------------------------
// Knobs / KnobPatch
// ---------------------------------------------------------------------------

#[test]
fn knobs_digest_is_stable_and_distinct() {
    assert_eq!(knobs("v1").digest(), knobs("v1").digest());
    assert_ne!(knobs("v1").digest(), knobs("v2").digest());
}

#[test]
fn knobs_changed_keys_detects_each_field() {
    let base = knobs("v1");
    let mut other = base.clone();
    assert!(base.changed_keys(&other).is_empty());
    other.prompt_version = "v2".to_string();
    assert_eq!(base.changed_keys(&other), vec![KnobKey::PromptVersion]);
    let mut other = base.clone();
    other.few_shot.count = 1;
    other.few_shot.example_pids = vec!["pid-a".to_string()];
    assert_eq!(base.changed_keys(&other), vec![KnobKey::FewShot]);
}

#[test]
fn knobs_json_roundtrip() {
    let k = knobs("v3");
    let json = k.to_json();
    assert_eq!(Knobs::from_json(&json).unwrap(), k);
}

#[test]
fn patch_parse_accepts_valid_knobs() {
    let value = serde_json::to_value(knobs("v1")).unwrap();
    assert_eq!(KnobPatch::parse(&value).unwrap(), knobs("v1"));
}

#[test]
fn patch_parse_rejects_unknown_safety_key() {
    let mut value = serde_json::to_value(knobs("v1")).unwrap();
    value
        .as_object_mut()
        .unwrap()
        .insert("allow_ax_send".to_string(), serde_json::json!(true));
    assert_eq!(
        KnobPatch::parse(&value),
        Err(RejectReason::SafetyChangeRequested)
    );
}

#[test]
fn patch_parse_rejects_nested_unknown_key() {
    let mut value = serde_json::to_value(knobs("v1")).unwrap();
    value["style_params"]
        .as_object_mut()
        .unwrap()
        .insert("allow_loco_write".to_string(), serde_json::json!(true));
    assert_eq!(
        KnobPatch::parse(&value),
        Err(RejectReason::SafetyChangeRequested)
    );
}

#[test]
fn patch_parse_rejects_non_object() {
    let value = serde_json::json!(["nope"]);
    assert!(matches!(
        KnobPatch::parse(&value),
        Err(RejectReason::MalformedPatch(_))
    ));
}

// ---------------------------------------------------------------------------
// KnobStore (atomic snapshot swap)
// ---------------------------------------------------------------------------

#[test]
fn knob_store_initial_is_generation_one() {
    let store = InMemoryKnobStore::new(knobs("v1")).unwrap();
    let (gen, k) = store.current();
    assert_eq!(gen, 1);
    assert_eq!(k, knobs("v1"));
}

#[test]
fn knob_store_apply_bumps_generation() {
    let store = InMemoryKnobStore::new(knobs("v1")).unwrap();
    let gen = store.apply(knobs("v2")).unwrap();
    assert_eq!(gen, 2);
    assert_eq!(store.current().1, knobs("v2"));
}

#[test]
fn knob_store_pinned_snapshot_is_isolated() {
    let store = InMemoryKnobStore::new(knobs("v1")).unwrap();
    let (pinned_gen, pinned) = store.current();
    store.apply(knobs("v2")).unwrap();
    // The pinned owned snapshot is unaffected by the later swap (R8.12).
    assert_eq!(pinned_gen, 1);
    assert_eq!(pinned, knobs("v1"));
    assert_eq!(store.current().0, 2);
}

#[test]
fn knob_store_invalid_knobs_rejected() {
    let mut bad = knobs("v1");
    bad.prompt_version = "  ".to_string();
    assert!(matches!(
        InMemoryKnobStore::new(bad),
        Err(ImproveError::InvalidKnobs(_))
    ));
}

#[test]
fn knob_store_generations_keeps_recent_only() {
    let store = InMemoryKnobStore::new(knobs("v0")).unwrap();
    for i in 1..=15 {
        store.apply(knobs(&format!("v{i}"))).unwrap();
    }
    let gens = store.generations(100).unwrap();
    assert_eq!(gens.len(), KEEP_GENERATIONS);
    // Newest first.
    assert_eq!(gens[0].knobs.prompt_version, "v15");
}

// ---------------------------------------------------------------------------
// Holdout
// ---------------------------------------------------------------------------

fn samples(n: usize) -> Vec<QaSample> {
    (0..n)
        .map(|i| QaSample {
            qa_pair_id: i as i64,
            sample_pid: format!("s{i}"),
            question: format!("q{i}"),
        })
        .collect()
}

#[test]
fn holdout_size_is_twenty_percent_capped_at_500() {
    assert_eq!(HoldoutSet::holdout_len_for(100), 20);
    assert_eq!(HoldoutSet::holdout_len_for(0), 0);
    assert_eq!(HoldoutSet::holdout_len_for(10_000), MAX_HOLDOUT);
}

#[test]
fn holdout_split_is_disjoint_and_deterministic() {
    let (train, holdout) = HoldoutSet::split(samples(100), 7);
    assert_eq!(holdout.len(), 20);
    assert_eq!(train.len(), 80);
    let train_ids: BTreeSet<i64> = train.iter().map(|s| s.qa_pair_id).collect();
    let holdout_ids: BTreeSet<i64> = holdout.items.iter().map(|s| s.qa_pair_id).collect();
    assert!(train_ids.is_disjoint(&holdout_ids));
    // Deterministic for the same seed.
    let (_, holdout2) = HoldoutSet::split(samples(100), 7);
    assert_eq!(holdout.items, holdout2.items);
}

// ---------------------------------------------------------------------------
// promotion_gate / regressions
// ---------------------------------------------------------------------------

fn no_rollback() -> BTreeSet<KnobsDigest> {
    BTreeSet::new()
}

#[test]
fn gate_passes_when_all_conditions_hold() {
    let cur = eval(70.0, 100, &[]);
    let cand = eval(76.0, 110, &[]);
    let digest = KnobsDigest("d".to_string());
    assert!(promotion_gate(&cur, &cand, 50, &no_rollback(), &digest, 0).is_ok());
}

#[test]
fn gate_rejects_small_gain() {
    let cur = eval(70.0, 100, &[]);
    let cand = eval(73.0, 100, &[]);
    let digest = KnobsDigest("d".to_string());
    assert!(matches!(
        promotion_gate(&cur, &cand, 50, &no_rollback(), &digest, 0),
        Err(RejectReason::GainTooSmall { .. })
    ));
}

#[test]
fn gate_rejects_latency_regression() {
    let cur = eval(70.0, 100, &[]);
    let cand = eval(80.0, 130, &[]); // 130% > 120%
    let digest = KnobsDigest("d".to_string());
    assert!(matches!(
        promotion_gate(&cur, &cand, 50, &no_rollback(), &digest, 0),
        Err(RejectReason::LatencyRegression { .. })
    ));
}

#[test]
fn gate_rejects_regressions() {
    let cur = eval(70.0, 100, &[("s1", 80)]);
    let cand = eval(90.0, 100, &[("s1", 60)]); // dropped 20 (>10) from a >=60 case
    let digest = KnobsDigest("d".to_string());
    assert!(matches!(
        promotion_gate(&cur, &cand, 50, &no_rollback(), &digest, 0),
        Err(RejectReason::Regressions(_))
    ));
}

#[test]
fn gate_rejects_previously_rolled_back() {
    let cur = eval(70.0, 100, &[]);
    let cand = eval(90.0, 100, &[]);
    let digest = KnobsDigest("d".to_string());
    let mut rolled = BTreeSet::new();
    rolled.insert(digest.clone());
    assert_eq!(
        promotion_gate(&cur, &cand, 50, &rolled, &digest, 0),
        Err(RejectReason::PreviouslyRolledBack)
    );
}

#[test]
fn gate_rejects_attempt_cap() {
    let cur = eval(70.0, 100, &[]);
    let cand = eval(90.0, 100, &[]);
    let digest = KnobsDigest("d".to_string());
    assert!(matches!(
        promotion_gate(&cur, &cand, 50, &no_rollback(), &digest, 3),
        Err(RejectReason::AttemptCapReached { cap: 3 })
    ));
}

#[test]
fn gate_rejects_insufficient_holdout() {
    let cur = eval(70.0, 100, &[]);
    let cand = eval(90.0, 100, &[]);
    let digest = KnobsDigest("d".to_string());
    assert!(matches!(
        promotion_gate(&cur, &cand, 49, &no_rollback(), &digest, 0),
        Err(RejectReason::InsufficientHoldout { have: 49, need: 50 })
    ));
}

#[test]
fn regression_ignores_below_baseline_cases() {
    // Before < 60 is never a regression even if it drops a lot.
    let cur = eval(50.0, 100, &[("s1", 55)]);
    let cand = eval(50.0, 100, &[("s1", 10)]);
    assert!(compute_regressions(&cur, &cand).is_empty());
}

// ---------------------------------------------------------------------------
// SqliteImproveStore
// ---------------------------------------------------------------------------

fn sig(kind: SignalKind, at: i64) -> SignalRecord {
    SignalRecord {
        kind,
        room_pid: "room-1".to_string(),
        at,
        threshold_code: "x".to_string(),
        dismissed: false,
    }
}

#[test]
fn store_records_and_reads_active_signals() {
    let store = SqliteImproveStore::open_in_memory().unwrap();
    store.record_signal(&sig(SignalKind::LowQuality, 100)).unwrap();
    store.record_signal(&sig(SignalKind::Reask, 200)).unwrap();
    let active = store.active_signals_since("room-1", 0).unwrap();
    assert_eq!(active.len(), 2);
    // Signals before the window are excluded.
    assert_eq!(store.active_signals_since("room-1", 150).unwrap().len(), 1);
}

#[test]
fn store_dismiss_increments_false_positive_and_hides_signal() {
    let store = SqliteImproveStore::open_in_memory().unwrap();
    store.record_signal(&sig(SignalKind::LowQuality, 100)).unwrap();
    assert_eq!(store.false_positive_count("room-1", SignalKind::LowQuality).unwrap(), 0);
    assert_eq!(store.dismiss("room-1", SignalKind::LowQuality).unwrap(), 1);
    assert_eq!(store.dismiss("room-1", SignalKind::LowQuality).unwrap(), 2);
    // Dismissed signals no longer count as active.
    assert!(store.active_signals_since("room-1", 0).unwrap().is_empty());
}

#[test]
fn store_attempts_bump_clamps_at_three() {
    let store = SqliteImproveStore::open_in_memory().unwrap();
    assert_eq!(store.attempts("room-1", SignalKind::Reask).unwrap(), 0);
    for expected in [1, 2, 3, 3, 3] {
        assert_eq!(store.bump_attempt("room-1", SignalKind::Reask).unwrap(), expected);
    }
}

#[test]
fn store_generation_roundtrip_and_keep_recent() {
    let store = SqliteImproveStore::open_in_memory().unwrap();
    for gen in 1..=12u64 {
        let k = knobs(&format!("v{gen}"));
        store
            .record_generation(&GenerationRecord {
                generation: gen,
                knobs: k.clone(),
                digest: k.digest(),
                promoted_at: gen as i64,
                holdout_mean: 70,
            })
            .unwrap();
    }
    // Oldest two dropped, keeping the last 10.
    assert!(store.generation(1).unwrap().is_none());
    assert!(store.generation(2).unwrap().is_none());
    let rec = store.generation(12).unwrap().unwrap();
    assert_eq!(rec.knobs.prompt_version, "v12");
    assert_eq!(rec.holdout_mean, 70);
}

#[test]
fn store_rolled_back_and_history_and_holdout() {
    let store = SqliteImproveStore::open_in_memory().unwrap();
    let d = KnobsDigest("digest-1".to_string());
    store.record_rolled_back(&d, 10).unwrap();
    assert!(store.rolled_back_digests().unwrap().contains(&d));

    store
        .record_history(&HistoryEntry {
            at: 5,
            problem_kind: "low_quality".to_string(),
            changed_keys: "prompt_version".to_string(),
            verdict: "promoted".to_string(),
            generation: Some(2),
        })
        .unwrap();
    assert_eq!(store.history(10).unwrap().len(), 1);

    store.set_holdout(&[1, 2, 3]).unwrap();
    assert_eq!(store.holdout_ids().unwrap(), vec![1, 2, 3]);
    store.set_holdout(&[4, 5]).unwrap();
    assert_eq!(store.holdout_ids().unwrap(), vec![4, 5]);
}

// ---------------------------------------------------------------------------
// SelfImprover
// ---------------------------------------------------------------------------

fn improver_env() -> (SqliteImproveStore, InMemoryKnobStore, FakeReplyModel, ForbiddenNetwork, VirtualClock) {
    (
        SqliteImproveStore::open_in_memory().unwrap(),
        InMemoryKnobStore::new(knobs("v1")).unwrap(),
        FakeReplyModel {
            reply: "네 곧 갈게요".to_string(),
            latency_ms: 50,
        },
        ForbiddenNetwork::new(),
        VirtualClock::new(1_000_000),
    )
}

#[test]
fn improver_observe_records_detected_signals() {
    let (store, knob_store, model, net, clock) = improver_env();
    let improver = SelfImprover::new(&store, &knob_store, &model, &net, &clock, StyleTarget::default(), None);
    let obs = ReplyObservation {
        room_pid: "room-1".to_string(),
        at: 1_000_000,
        quality_score: 40,
        reask_secs: Some(30),
        repeat_count: 0,
        owner_correction_secs: None,
        topic_similarity: 0.9,
    };
    let recorded = improver.observe(&obs).unwrap();
    assert_eq!(recorded.len(), 2); // low quality + reask
    assert_eq!(store.active_signals_since("room-1", 0).unwrap().len(), 2);
    assert_eq!(improver.net_egress(), 0);
}

#[test]
fn improver_should_start_on_two_same_kind() {
    let (store, knob_store, model, net, clock) = improver_env();
    let improver = SelfImprover::new(&store, &knob_store, &model, &net, &clock, StyleTarget::default(), None);
    let now = clock.now_ms();
    store.record_signal(&sig(SignalKind::LowQuality, now)).unwrap();
    assert!(!improver.should_start("room-1").unwrap());
    store.record_signal(&sig(SignalKind::LowQuality, now)).unwrap();
    assert!(improver.should_start("room-1").unwrap());
}

#[test]
fn improver_should_start_on_two_distinct_kinds() {
    let (store, knob_store, model, net, clock) = improver_env();
    let improver = SelfImprover::new(&store, &knob_store, &model, &net, &clock, StyleTarget::default(), None);
    let now = clock.now_ms();
    store.record_signal(&sig(SignalKind::LowQuality, now)).unwrap();
    store.record_signal(&sig(SignalKind::Reask, now)).unwrap();
    assert!(improver.should_start("room-1").unwrap());
}

#[test]
fn improver_should_start_excludes_dismissed_kind() {
    let (store, knob_store, model, net, clock) = improver_env();
    let improver = SelfImprover::new(&store, &knob_store, &model, &net, &clock, StyleTarget::default(), None);
    let now = clock.now_ms();
    // Two low-quality signals, but the kind is dismissed 3 times → excluded.
    store.record_signal(&sig(SignalKind::LowQuality, now)).unwrap();
    store.record_signal(&sig(SignalKind::LowQuality, now)).unwrap();
    for _ in 0..3 {
        store.dismiss("room-1", SignalKind::LowQuality).unwrap();
    }
    assert!(!improver.should_start("room-1").unwrap());
}

#[test]
fn improver_diagnose_is_nonempty_subset_of_four() {
    let (store, knob_store, model, net, clock) = improver_env();
    let improver = SelfImprover::new(&store, &knob_store, &model, &net, &clock, StyleTarget::default(), None);
    let diag = improver.diagnose(&[sig(SignalKind::TopicDrift, 1)]);
    assert!(!diag.causes.is_empty());
    assert_eq!(diag.causes, vec![KnobKey::RetrievalWeights]);
    // Empty signals still yield a non-empty subset.
    assert!(!improver.diagnose(&[]).causes.is_empty());
}

#[test]
fn improver_evaluate_runs_locally_with_zero_egress() {
    let (store, knob_store, model, net, clock) = improver_env();
    let improver = SelfImprover::new(&store, &knob_store, &model, &net, &clock, StyleTarget::default(), None);
    let (_, holdout) = HoldoutSet::split(samples(60), 3);
    let summary = improver.evaluate(&knobs("v2"), &holdout);
    assert_eq!(summary.evaluated, holdout.len());
    assert!(summary.mean_quality <= 100.0);
    assert_eq!(summary.p95_latency_ms, 50);
    assert_eq!(improver.net_egress(), 0);
}

#[test]
fn improver_promote_insufficient_holdout() {
    let (store, knob_store, model, net, clock) = improver_env();
    let mut improver = SelfImprover::new(&store, &knob_store, &model, &net, &clock, StyleTarget::default(), None);
    let out = improver
        .promote(
            ("room-1".to_string(), SignalKind::LowQuality),
            &knobs("v2"),
            &eval(70.0, 100, &[]),
            &eval(90.0, 100, &[]),
            49,
            &[],
        )
        .unwrap();
    assert_eq!(out, PromotionOutcome::Insufficient { have: 49, need: 50 });
    // Config unchanged, no attempt counted.
    assert_eq!(knob_store.current().1, knobs("v1"));
    assert_eq!(store.attempts("room-1", SignalKind::LowQuality).unwrap(), 0);
}

#[test]
fn improver_promote_success_swaps_and_records() {
    let (store, knob_store, model, net, clock) = improver_env();
    let mut improver = SelfImprover::new(&store, &knob_store, &model, &net, &clock, StyleTarget::default(), None);
    let out = improver
        .promote(
            ("room-1".to_string(), SignalKind::LowQuality),
            &knobs("v2"),
            &eval(70.0, 100, &[]),
            &eval(78.0, 110, &[]),
            50,
            &[80; 20],
        )
        .unwrap();
    match out {
        PromotionOutcome::Promoted { generation, changed_keys, delta, .. } => {
            assert_eq!(generation, 2);
            assert_eq!(changed_keys, vec![KnobKey::PromptVersion]);
            assert!((delta - 8.0).abs() < 1e-3);
        }
        other => panic!("expected Promoted, got {other:?}"),
    }
    assert_eq!(knob_store.current().1, knobs("v2"));
    assert!(store.generation(2).unwrap().is_some());
    assert_eq!(improver.net_egress(), 0);
}

#[test]
fn improver_promote_rejected_counts_attempt() {
    let (store, knob_store, model, net, clock) = improver_env();
    let mut improver = SelfImprover::new(&store, &knob_store, &model, &net, &clock, StyleTarget::default(), None);
    let out = improver
        .promote(
            ("room-1".to_string(), SignalKind::LowQuality),
            &knobs("v2"),
            &eval(70.0, 100, &[]),
            &eval(72.0, 100, &[]), // gain < 5
            50,
            &[80; 20],
        )
        .unwrap();
    assert!(matches!(out, PromotionOutcome::Rejected(RejectReason::GainTooSmall { .. })));
    // Config unchanged, attempt counted (R8.14).
    assert_eq!(knob_store.current().1, knobs("v1"));
    assert_eq!(store.attempts("room-1", SignalKind::LowQuality).unwrap(), 1);
}

#[test]
fn improver_auto_rollback_restores_and_blocks_repromote() {
    let (store, knob_store, model, net, clock) = improver_env();
    let mut improver = SelfImprover::new(&store, &knob_store, &model, &net, &clock, StyleTarget::default(), None);
    let cand = knobs("v2");
    // Promote with a high baseline.
    improver
        .promote(
            ("room-1".to_string(), SignalKind::LowQuality),
            &cand,
            &eval(70.0, 100, &[]),
            &eval(80.0, 100, &[]),
            50,
            &[85; 20],
        )
        .unwrap();
    assert_eq!(knob_store.current().1, cand);

    // Live quality drops well below baseline → auto rollback (R8.12).
    let rollback = improver.watch_after_promotion(&[70; 20]).unwrap();
    let outcome = rollback.expect("should roll back");
    assert!((outcome.before_mean - 85.0).abs() < 1e-3);
    assert!((outcome.after_mean - 70.0).abs() < 1e-3);
    // Reverted to the prior config (round-trip restore, R12.12).
    assert_eq!(knob_store.current().1, knobs("v1"));
    // The rolled-back candidate cannot be promoted again (R8.19).
    assert!(store.rolled_back_digests().unwrap().contains(&cand.digest()));

    // Re-promoting the same candidate is rejected as previously rolled back.
    let out = improver
        .promote(
            ("room-1".to_string(), SignalKind::LowQuality),
            &cand,
            &eval(70.0, 100, &[]),
            &eval(90.0, 100, &[]),
            50,
            &[85; 20],
        )
        .unwrap();
    assert_eq!(out, PromotionOutcome::Rejected(RejectReason::PreviouslyRolledBack));
}

#[test]
fn improver_watch_no_rollback_when_quality_holds() {
    let (store, knob_store, model, net, clock) = improver_env();
    let mut improver = SelfImprover::new(&store, &knob_store, &model, &net, &clock, StyleTarget::default(), None);
    improver
        .promote(
            ("room-1".to_string(), SignalKind::LowQuality),
            &knobs("v2"),
            &eval(70.0, 100, &[]),
            &eval(80.0, 100, &[]),
            50,
            &[80; 20],
        )
        .unwrap();
    // Recent quality stays close to baseline → no rollback.
    assert!(improver.watch_after_promotion(&[78; 20]).unwrap().is_none());
    assert_eq!(knob_store.current().1, knobs("v2"));
}

#[test]
fn improver_manual_rollback_round_trips() {
    let (store, knob_store, model, net, clock) = improver_env();
    let mut improver = SelfImprover::new(&store, &knob_store, &model, &net, &clock, StyleTarget::default(), None);
    // gen 1 = v1, gen 2 = v2.
    knob_store.apply(knobs("v2")).unwrap();
    assert_eq!(knob_store.current().1, knobs("v2"));
    // Roll back to generation 1.
    improver.rollback_to(1).unwrap();
    assert_eq!(knob_store.current().1, knobs("v1"));
}

#[test]
fn knob_generation_json_has_no_leaky_content() {
    // The persisted knobs_json carries only version labels + provenance pids.
    let k = knobs("v2");
    let json = k.to_json();
    assert!(!json.contains("http://"));
    assert!(!json.contains("https://"));
    assert!(!json.contains("/Users/"));
    assert!(json.contains("pid-a"));
}
