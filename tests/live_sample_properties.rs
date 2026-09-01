//! Property-based tests for the live learning-sample collector.
//!
//! Feature: kakao-agent-live-ops
//! Property 9: 표본 누적 규칙 — 확정 전송 기준·멱등·유래 검사
//! Property 10: 전송 속도와 등급 한도
//!
//! Property 9 pins how confirmed automatic-reply results become learning
//! samples: only `commit`/`success` receipts accumulate, re-applying the same
//! `trace_pid` never double-counts (the `UNIQUE` column + `INSERT OR IGNORE`),
//! artificial sends that do not trace back to an incoming partner message are
//! rejected, and the `fake` grade is excluded from the `accumulated/target`
//! count.
//!
//! Property 10 pins send speed and grade limits: the base interval is at least
//! ten seconds, the jitter lies in `[0, base]`, the total interval between two
//! consecutive confirmed sends lies in `[base, 2·base]`, pacing is invariant to
//! the remaining target and any deadline (the [`plan_pacing`] signature accepts
//! neither), a request that arrives before the interval elapses is *rejected*
//! (not delayed), the `memo` grade may only target the memo chat (나와의 채팅),
//! and a smoke run is capped at three confirmed sends.
//!
//! Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.11, 2.12,
//! 2.13, 3.8

use std::collections::BTreeMap;

use openkakao_cli::fakes::VirtualClock;
use openkakao_cli::live_sample::{
    plan_pacing, AdmitRejection, CommitReceipt, LiveSampleCollector, SampleError, SampleStore,
    SqliteSampleStore, MIN_INTERVAL_SECS, SMOKE_CAP,
};
use openkakao_cli::logging::{Stage, StageStatus};
use openkakao_cli::ports::SendAuthority;
use openkakao_cli::safety::{
    GradeLimit, GradePolicy, Origin, PacingSource, SendGrade, SendIntent,
};
use proptest::prelude::*;
use rand::rngs::StdRng;
use rand::SeedableRng;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// A `commit`/`success` receipt — the only kind that accumulates (R2.1).
fn commit_receipt(trace: &str, grade: SendGrade, at: i64) -> CommitReceipt {
    CommitReceipt {
        trace_pid: trace.to_string(),
        room_pid: "room-pid".to_string(),
        grade,
        quality_score: 50,
        latency_ms: 10,
        at,
        stage: Stage::Commit,
        status: StageStatus::Success,
    }
}

/// A database-authoritative authority row for one chat.
fn authority(chat_id: i64, is_memo: bool) -> SendAuthority {
    SendAuthority {
        chat_id,
        chat_type: if is_memo { 0 } else { 1 },
        is_memo,
        owner_display_name: Some("owner".into()),
    }
}

/// An intent that traces back to an incoming partner message with a valid
/// event id (the only origin `admit` does not reject as synthetic, R2.11).
fn partner_intent(grade: SendGrade) -> SendIntent {
    SendIntent {
        grade,
        origin: Origin::PartnerMessage {
            event_id: "db:1:2".into(),
        },
    }
}

/// A strategy over the three send grades.
fn grade_strategy() -> impl Strategy<Value = SendGrade> {
    prop_oneof![
        Just(SendGrade::Fake),
        Just(SendGrade::Memo),
        Just(SendGrade::Smoke),
    ]
}

/// A sequence of `(trace-pool-index, grade)` steps. Drawing the trace id from a
/// small pool forces duplicates so idempotency is actually exercised.
fn steps_strategy() -> impl Strategy<Value = Vec<(usize, SendGrade)>> {
    prop::collection::vec((0usize..8, grade_strategy()), 0..30)
}

// ===========================================================================
// Property 9: 표본 누적 규칙 — 확정 전송 기준·멱등·유래 검사
// ===========================================================================

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// The accumulated count equals the number of distinct `commit`/`success`
    /// processings whose (first-seen) grade is `memo` or `smoke`; the `fake`
    /// grade is excluded from `accumulated` but still counted per-grade. Every
    /// `trace_pid` accumulates at most once, and re-applying the whole sequence
    /// changes nothing.
    ///
    /// Validates: Requirements 2.1, 2.2, 2.4, 2.5
    #[test]
    fn confirmed_sends_accumulate_once_fake_excluded(steps in steps_strategy()) {
        let store = SqliteSampleStore::open_in_memory().unwrap();
        let clock = VirtualClock::new(0);
        let collector =
            LiveSampleCollector::new(&store, &clock, Some(10), 1000, Some(1), 7);

        // The grade that "wins" for each trace pid is its first occurrence,
        // because `trace_pid` is UNIQUE and later inserts are ignored.
        let mut first_grade: BTreeMap<String, SendGrade> = BTreeMap::new();
        for (idx, grade) in &steps {
            let trace = format!("trace-{idx}");
            let r = commit_receipt(&trace, *grade, 1);
            collector.on_commit(&r).expect("on_commit records");
            first_grade.entry(trace).or_insert(*grade);
        }

        let distinct_total = first_grade.len() as u32;
        let expected_accumulated = first_grade
            .values()
            .filter(|g| **g != SendGrade::Fake)
            .count() as u32;
        let expected_fake = first_grade
            .values()
            .filter(|g| **g == SendGrade::Fake)
            .count() as u32;

        let progress = store.progress(1000).unwrap();
        // fake grade is excluded from the accumulated count (R2.5).
        prop_assert_eq!(progress.accumulated, expected_accumulated);
        prop_assert_eq!(progress.by_grade[&SendGrade::Fake], expected_fake);
        // accumulated + fake == every distinct confirmed processing.
        prop_assert_eq!(progress.accumulated + expected_fake, distinct_total);
        // The ratio string mirrors the accumulated count (R2.2).
        prop_assert_eq!(
            progress.as_ratio(),
            format!("{}/{}", expected_accumulated, 1000)
        );

        // Re-applying every receipt is idempotent: the count does not move.
        for (idx, grade) in &steps {
            let trace = format!("trace-{idx}");
            collector
                .on_commit(&commit_receipt(&trace, *grade, 1))
                .expect("on_commit records");
        }
        prop_assert_eq!(store.progress(1000).unwrap().accumulated, expected_accumulated);
    }

    /// A receipt that is not `commit`/`success` is rejected and never counted:
    /// the accumulated count stays at its pre-request value (R2.1, R2.13).
    ///
    /// Validates: Requirements 2.1, 2.13
    #[test]
    fn non_commit_receipt_rejected_without_counting(
        which in 0u8..2,
        grade in grade_strategy(),
    ) {
        let store = SqliteSampleStore::open_in_memory().unwrap();
        let clock = VirtualClock::new(0);
        let collector =
            LiveSampleCollector::new(&store, &clock, Some(10), 1000, Some(1), 7);

        let mut r = commit_receipt("t", grade, 1);
        if which == 0 {
            r.status = StageStatus::Failed;
        } else {
            r.stage = Stage::Detect;
        }

        prop_assert!(matches!(collector.on_commit(&r), Err(SampleError::NotACommit)));
        prop_assert_eq!(store.progress(1000).unwrap().accumulated, 0);
        prop_assert_eq!(store.progress(1000).unwrap().by_grade[&SendGrade::Fake], 0);
    }

    /// Only a send that traces back to an incoming partner message (with a
    /// valid event id) escapes the synthetic-origin refusal; every other origin
    /// — and a partner message with an invalid event id — is rejected as
    /// `Synthetic` regardless of grade, and nothing is accumulated (R2.11).
    ///
    /// Validates: Requirements 2.11, 2.12
    #[test]
    fn admit_rejects_synthetic_and_invalid_origins(
        kind in 0usize..5,
        grade in grade_strategy(),
    ) {
        let store = SqliteSampleStore::open_in_memory().unwrap();
        let clock = VirtualClock::new(0);
        let collector =
            LiveSampleCollector::new(&store, &clock, Some(10), 1000, Some(1), 7);

        let intent = match kind {
            // A valid partner-message origin.
            0 => SendIntent {
                grade,
                origin: Origin::PartnerMessage {
                    event_id: "db:1:2".into(),
                },
            },
            // A partner-message origin with a malformed event id.
            1 => SendIntent {
                grade,
                origin: Origin::PartnerMessage {
                    event_id: "not-a-valid-id".into(),
                },
            },
            2 => SendIntent {
                grade,
                origin: Origin::GeekNewsSlot { marker: "m".into() },
            },
            3 => SendIntent {
                grade,
                origin: Origin::UserComposer,
            },
            _ => SendIntent {
                grade,
                origin: Origin::RelaySource {
                    message_pid: "p".into(),
                },
            },
        };

        let res = collector.admit(&intent, &authority(1, true));
        if kind == 0 {
            // A valid partner origin is never rejected as synthetic (it may
            // still pass; everything else here is permissive).
            prop_assert!(!matches!(res, Err(AdmitRejection::Synthetic)));
        } else {
            prop_assert_eq!(res, Err(AdmitRejection::Synthetic));
        }
        // No admission decision ever accumulates a sample.
        prop_assert_eq!(store.progress(1000).unwrap().accumulated, 0);
    }
}

// ===========================================================================
// Property 10: 전송 속도와 등급 한도
// ===========================================================================

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// The base interval is at least ten seconds, the jitter lies in
    /// `[0, base]`, and the total interval `base + jitter` lies in
    /// `[base, 2·base]` (R2.3).
    ///
    /// Validates: Requirements 2.3
    #[test]
    fn plan_pacing_interval_bounds(
        base_cfg in prop::option::of(0u64..300),
        last in 0i64..1_000_000,
        seed in any::<u64>(),
    ) {
        let mut rng = StdRng::seed_from_u64(seed);
        let p = plan_pacing(base_cfg, Some(last), &mut rng);

        prop_assert!(p.base_secs >= MIN_INTERVAL_SECS);
        prop_assert!(p.jitter_secs <= p.base_secs);

        let base_ms = p.base_secs as i64 * 1_000;
        let total = p.next_allowed_at - last;
        prop_assert!(total >= base_ms, "total {} < base_ms {}", total, base_ms);
        prop_assert!(total <= 2 * base_ms, "total {} > 2·base_ms {}", total, base_ms);
    }

    /// Pacing depends only on the configured base interval and the last commit
    /// time — never on the remaining target or a deadline. Two collectors with
    /// the *same* seed and base but wildly different targets plan the identical
    /// next-allowed time from the same commit (R2.12).
    ///
    /// Validates: Requirements 2.12
    #[test]
    fn pacing_invariant_to_target(
        base_cfg in prop::option::of(0u64..300),
        last in 1i64..1_000_000,
        seed in any::<u64>(),
        target_a in 1u32..100_000,
        target_b in 1u32..100_000,
    ) {
        let store_a = SqliteSampleStore::open_in_memory().unwrap();
        let store_b = SqliteSampleStore::open_in_memory().unwrap();
        let clock = VirtualClock::new(0);
        let ca = LiveSampleCollector::new(&store_a, &clock, base_cfg, target_a, Some(1), seed);
        let cb = LiveSampleCollector::new(&store_b, &clock, base_cfg, target_b, Some(1), seed);

        let r = commit_receipt("t0", SendGrade::Memo, last);
        ca.on_commit(&r).unwrap();
        cb.on_commit(&r).unwrap();

        // Different targets, identical pacing: the target cannot influence it.
        prop_assert_eq!(ca.next_allowed_at(), cb.next_allowed_at());
    }

    /// A request that arrives before the minimum interval elapses is a
    /// rejection, not a delay: `admit` returns `TooSoon` and the counts are
    /// left at their pre-request values (R2.8).
    ///
    /// Validates: Requirements 2.8
    #[test]
    fn interval_not_elapsed_is_rejected(
        base in 10u64..120,
        seed in any::<u64>(),
    ) {
        let store = SqliteSampleStore::open_in_memory().unwrap();
        let clock = VirtualClock::new(1_000);
        let collector =
            LiveSampleCollector::new(&store, &clock, Some(base), 1000, Some(1), seed);

        // A memo commit at t=1000 paces the next send forward by >= base secs.
        collector
            .on_commit(&commit_receipt("t0", SendGrade::Memo, 1_000))
            .unwrap();
        let accumulated_before = store.progress(1000).unwrap().accumulated;

        // The clock has not advanced, so the next send is not yet allowed.
        match collector.admit(&partner_intent(SendGrade::Memo), &authority(1, true)) {
            Err(AdmitRejection::TooSoon { next_allowed_at }) => {
                prop_assert!(next_allowed_at >= 1_000 + base as i64 * 1_000);
            }
            other => prop_assert!(false, "expected TooSoon, got {:?}", other),
        }

        // The rejected request left the counts unchanged.
        prop_assert_eq!(store.progress(1000).unwrap().accumulated, accumulated_before);
    }

    /// The `memo` grade may target only the memo chat (나와의 채팅). Both the
    /// `admit` pre-screen and the `GradePolicy` seam the guard consults agree:
    /// a memo-grade send to any other chat is refused (R2.6).
    ///
    /// Validates: Requirements 2.6
    #[test]
    fn memo_grade_only_targets_memo_chat(
        is_memo in any::<bool>(),
        memo_chat in 1i64..50,
        other_chat in 51i64..100,
    ) {
        let store = SqliteSampleStore::open_in_memory().unwrap();
        let clock = VirtualClock::new(0);
        let collector =
            LiveSampleCollector::new(&store, &clock, Some(10), 1000, Some(memo_chat), 7);

        // The admit pre-screen keys off the database-authoritative `is_memo`.
        let res = collector.admit(&partner_intent(SendGrade::Memo), &authority(memo_chat, is_memo));
        if is_memo {
            prop_assert!(!matches!(res, Err(AdmitRejection::MemoOnlyTarget)));
        } else {
            prop_assert_eq!(res, Err(AdmitRejection::MemoOnlyTarget));
        }

        // The GradePolicy seam keys off the configured memo chat id.
        prop_assert!(collector.check(&partner_intent(SendGrade::Memo), memo_chat).is_ok());
        prop_assert_eq!(
            collector.check(&partner_intent(SendGrade::Memo), other_chat),
            Err(GradeLimit::MemoOnly)
        );
    }

    /// A smoke run is capped at three confirmed sends: after three the fourth
    /// request is refused, through both the `admit` pre-screen and the
    /// `GradePolicy` seam (R2.7).
    ///
    /// Validates: Requirements 2.7
    #[test]
    fn smoke_run_capped_at_three(seed in any::<u64>()) {
        let store = SqliteSampleStore::open_in_memory().unwrap();
        let clock = VirtualClock::new(0);
        let collector =
            LiveSampleCollector::new(&store, &clock, Some(10), 1000, None, seed);

        // Three confirmed smoke sends bring the run to its cap.
        for i in 0..SMOKE_CAP {
            collector
                .on_commit(&commit_receipt(&format!("s{i}"), SendGrade::Smoke, 0))
                .unwrap();
        }
        prop_assert_eq!(collector.smoke_confirmed(), SMOKE_CAP);

        // The fourth request is refused by both the admit pre-screen (grade
        // check precedes the interval check) and the GradePolicy seam.
        prop_assert_eq!(
            collector.admit(&partner_intent(SendGrade::Smoke), &authority(9, false)),
            Err(AdmitRejection::SmokeCapReached { cap: SMOKE_CAP })
        );
        prop_assert_eq!(
            collector.check(&partner_intent(SendGrade::Smoke), 9),
            Err(GradeLimit::SmokeCapReached)
        );
    }

    /// Two consecutive confirmed sends are separated by a total interval in
    /// `[base, 2·base]`: the second commit's planned next-allowed time is at
    /// least `base` and at most `2·base` seconds past the second commit (R2.3).
    ///
    /// Validates: Requirements 2.3
    #[test]
    fn consecutive_interval_within_base_and_double_base(
        base in 10u64..120,
        seed in any::<u64>(),
        first_at in 1i64..500_000,
        gap_ms in 0i64..5_000_000,
    ) {
        let store = SqliteSampleStore::open_in_memory().unwrap();
        let clock = VirtualClock::new(0);
        let collector =
            LiveSampleCollector::new(&store, &clock, Some(base), 1000, Some(1), seed);

        collector
            .on_commit(&commit_receipt("t0", SendGrade::Memo, first_at))
            .unwrap();
        let second_at = first_at + gap_ms;
        collector
            .on_commit(&commit_receipt("t1", SendGrade::Memo, second_at))
            .unwrap();

        let base_ms = base as i64 * 1_000;
        let interval = collector.next_allowed_at() - second_at;
        prop_assert!(interval >= base_ms, "interval {} < base_ms {}", interval, base_ms);
        prop_assert!(interval <= 2 * base_ms, "interval {} > 2·base_ms {}", interval, base_ms);
    }
}
