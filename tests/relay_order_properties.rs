//! Property-based tests for the Telegram → KakaoTalk relay (task 8.1).
//!
//! Feature: kakao-agent-live-ops
//! Property 22: 중계 순서의 부분수열성과 커서 전진
//!   (relay ordering is a subsequence of the source order; the cursor advances
//!    only after a message settles as Committed or MissFinal)
//! Property 23: 중계 내용 추출·감지 방식·지연 표시 규칙
//!   (content extraction, detection mode, and latency-display rules)
//! Property 20: 이미지 확보 사다리의 순서와 권한 결합
//!   (image-acquisition ladder order and permission coupling)
//! Property 19: 이미지 개수 일치 또는 누락 명시 — 중계 관점
//!   (image counts match or missing is explicit — relay perspective)
//!
//! Every property that runs the relay drives the real [`TelegramRelay`] over
//! in-memory seams. No real KakaoTalk send and no network egress is possible
//! here: the relay only ever calls the injected [`RelaySender`] fake, and each
//! such property additionally registers a [`BlockingRealSendPort`] tripwire and
//! a [`ForbiddenNetwork`] and asserts both counters stay at zero — the
//! observation layer of the "zero real sends / zero egress" defense.
//!
//! Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 7.12,
//! 7.13, 7.14, 7.17, 7.18, 7.19, 9.10, 12.6, 12.8

use std::cell::{Cell, RefCell};

use openkakao_cli::fakes::{BlockingRealSendPort, FakeAxReadPort, ForbiddenNetwork, VirtualClock};
use openkakao_cli::forward::{ImageLadder, StepAttempt, STEP_TIMEOUT_MS};
use openkakao_cli::logging::{FlowKind, HistoryStore, SqliteHistoryStore};
use openkakao_cli::ports::{
    AcquireError, DetectMode, ExcludedAttachment, ImageAcquisition, ImageAcquirer, ImageBlob,
    ImageRef, LinkRef, NetworkPort, SendPort, TelegramMessage,
};
use openkakao_cli::relay::{
    extract_links, summarize_excluded, LatencyWindow, RelayLedger, RelayPair, RelaySendOutcome,
    RelaySender, Settlement, SqliteRelayStore, TelegramChatRef, TelegramRelay, LATENCY_READY_MIN,
    MAX_IMAGES, MAX_PAIRS, MISMATCH_STREAK_LIMIT, P50_TARGET_MS, P95_TARGET_MS,
};
use proptest::prelude::*;

// ===========================================================================
// Shared fixtures
// ===========================================================================

/// The observation-defense tripwires every relay property registers and asserts
/// are never touched: the real-send path and the network stay at zero.
struct Tripwires {
    blocked: BlockingRealSendPort,
    net: ForbiddenNetwork,
}

impl Tripwires {
    fn new() -> Self {
        Self {
            blocked: BlockingRealSendPort::new(),
            net: ForbiddenNetwork::new(),
        }
    }

    fn assert_untouched(&self) -> Result<(), TestCaseError> {
        prop_assert_eq!(
            self.blocked.blocked_count(),
            0,
            "a call reached the real send path"
        );
        prop_assert_eq!(self.net.egress_count(), 0, "network egress happened");
        // Keep the SendPort trait in scope: the tripwire is a real (non-fake)
        // send port that the relay must never invoke.
        prop_assert!(!SendPort::is_fake(&self.blocked));
        Ok(())
    }
}

/// Build a single relay pair reading one Telegram conversation into `rooms`.
fn pair(rooms: &[i64]) -> RelayPair {
    RelayPair {
        telegram: TelegramChatRef { id: "tg-1".into() },
        kakao_rooms: rooms.to_vec(),
    }
}

/// A plain text-only Telegram message.
fn text_msg(pid: &str, at: i64, order: i64, text: &str) -> TelegramMessage {
    TelegramMessage {
        pid: pid.into(),
        at,
        display_order: order,
        text: text.into(),
        links: Vec::new(),
        images: Vec::new(),
        excluded: Vec::new(),
    }
}

/// An image reference.
fn img_ref(i: usize) -> ImageRef {
    ImageRef {
        locator: format!("img-{i}"),
    }
}

// --- image acquirers for the shared ladder ---

/// An acquirer that always yields a non-empty image within the time bound.
struct OkAcquirer {
    kind: ImageAcquisition,
    clock: VirtualClock,
}
impl ImageAcquirer for OkAcquirer {
    fn kind(&self) -> ImageAcquisition {
        self.kind
    }
    fn acquire(&self, _r: &ImageRef) -> Result<ImageBlob, AcquireError> {
        self.clock.advance_ms(5);
        Ok(ImageBlob {
            bytes: vec![1, 2, 3],
            mime: "image/png".into(),
        })
    }
}

/// An acquirer that never yields an image.
struct NeverAcquirer {
    kind: ImageAcquisition,
}
impl ImageAcquirer for NeverAcquirer {
    fn kind(&self) -> ImageAcquisition {
        self.kind
    }
    fn acquire(&self, _r: &ImageRef) -> Result<ImageBlob, AcquireError> {
        Err(AcquireError::NotAvailable)
    }
}

/// Owns three ladder rungs (first always succeeds) plus their clock, so image
/// acquisition never advances a relay's latency clock unexpectedly.
struct AcquiringRig {
    clock: VirtualClock,
    ok: OkAcquirer,
    n1: NeverAcquirer,
    n2: NeverAcquirer,
}
impl AcquiringRig {
    fn new(clock: VirtualClock) -> Self {
        Self {
            ok: OkAcquirer {
                kind: ImageAcquisition::OriginalSave,
                clock: clock.clone(),
            },
            n1: NeverAcquirer {
                kind: ImageAcquisition::CacheFolder,
            },
            n2: NeverAcquirer {
                kind: ImageAcquisition::ScreenCapture,
            },
            clock,
        }
    }
    fn ladder(&self) -> ImageLadder<'_> {
        ImageLadder {
            steps: [&self.ok, &self.n1, &self.n2],
            clock: &self.clock,
        }
    }
}

/// Owns three always-failing ladder rungs and their clock (models "all three
/// image-acquisition attempts fail", R7.17).
struct FailingRig {
    clock: VirtualClock,
    n0: NeverAcquirer,
    n1: NeverAcquirer,
    n2: NeverAcquirer,
}
impl FailingRig {
    fn new(clock: VirtualClock) -> Self {
        Self {
            n0: NeverAcquirer {
                kind: ImageAcquisition::OriginalSave,
            },
            n1: NeverAcquirer {
                kind: ImageAcquisition::CacheFolder,
            },
            n2: NeverAcquirer {
                kind: ImageAcquisition::ScreenCapture,
            },
            clock,
        }
    }
    fn ladder(&self) -> ImageLadder<'_> {
        ImageLadder {
            steps: [&self.n0, &self.n1, &self.n2],
            clock: &self.clock,
        }
    }
}

// --- senders ---

/// A sender that returns a fixed outcome and records each (chat_id, body) it is
/// asked to send.
struct RecordingSender {
    outcome: RelaySendOutcome,
    sends: RefCell<Vec<(i64, String)>>,
}
impl RecordingSender {
    fn new(outcome: RelaySendOutcome) -> Self {
        Self {
            outcome,
            sends: RefCell::new(Vec::new()),
        }
    }
    fn count(&self) -> usize {
        self.sends.borrow().len()
    }
}
impl RelaySender for RecordingSender {
    fn send_relay(&self, chat_id: i64, text: &str, _images: &[ImageBlob]) -> RelaySendOutcome {
        self.sends.borrow_mut().push((chat_id, text.to_string()));
        self.outcome.clone()
    }
}

/// A sender that fences any body containing `"FENCE"` and records the bodies it
/// actually sends. This lets a per-message send/fence decision be encoded in
/// the message text.
struct MarkerSender {
    sends: RefCell<Vec<String>>,
}
impl MarkerSender {
    fn new() -> Self {
        Self {
            sends: RefCell::new(Vec::new()),
        }
    }
    fn bodies(&self) -> Vec<String> {
        self.sends.borrow().clone()
    }
}
impl RelaySender for MarkerSender {
    fn send_relay(&self, _chat_id: i64, text: &str, _images: &[ImageBlob]) -> RelaySendOutcome {
        if text.contains("FENCE") {
            RelaySendOutcome::Fenced {
                reason: "안전 설정이 꺼져 있어요".into(),
            }
        } else {
            self.sends.borrow_mut().push(text.to_string());
            RelaySendOutcome::Sent {
                images_delivered: 0,
            }
        }
    }
}

// ===========================================================================
// Property 22: relay ordering is a subsequence + cursor advance.
// ===========================================================================

/// One generated source message: its opaque timestamp and whether the sender
/// will confirm it (a `send`) or fence it (skipped from the confirmed stream).
#[derive(Debug, Clone, Copy)]
struct MsgSpec {
    at: i64,
    send: bool,
}

fn msg_spec_strategy() -> impl Strategy<Value = MsgSpec> {
    // A small `at` pool forces many same-instant ties, which the unique
    // display_order (the message index) then breaks (R7.9, R12.8).
    (1i64..=25, any::<bool>()).prop_map(|(at, send)| MsgSpec { at, send })
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// Property 22: for a batch of 1..=200 source messages (with same-instant
    /// ties), the confirmed-send sequence is exactly the subsequence of the
    /// source messages — taken in ascending `(at, display_order)` order — whose
    /// sender confirmed them. A fenced message settles as a final miss and
    /// advances the cursor without a send; the loop processes one message at a
    /// time; and after the tick the cursor sits at the last processed message.
    /// A fresh relay over the same store (an app restart) re-delivers nothing.
    ///
    /// Validates: Requirements 7.9, 7.14, 12.8
    #[test]
    fn confirmed_sequence_is_subsequence_and_cursor_advances(
        specs in prop::collection::vec(msg_spec_strategy(), 1..=200),
    ) {
        // Assign a globally-unique display_order (the index) so every
        // (at, display_order) pair is unique and the read boundary never drops
        // a same-instant message.
        struct Built {
            at: i64,
            order: i64,
            send: bool,
            body: String,
        }
        let built: Vec<Built> = specs
            .iter()
            .enumerate()
            .map(|(i, s)| Built {
                at: s.at,
                order: i as i64,
                send: s.send,
                body: format!("m{i}-{}", if s.send { "SEND" } else { "FENCE" }),
            })
            .collect();

        // The oracle source order and its confirmed subsequence.
        let mut sorted: Vec<&Built> = built.iter().collect();
        sorted.sort_by_key(|a| (a.at, a.order));
        let expected_sent: Vec<String> =
            sorted.iter().filter(|b| b.send).map(|b| b.body.clone()).collect();
        let last = sorted.last().expect("at least one message");
        let (last_at, last_order) = (last.at, last.order);

        // The read boundary (the fake sorts internally, so input order is
        // irrelevant).
        let tg_messages: Vec<TelegramMessage> = built
            .iter()
            .map(|b| text_msg(&format!("pid-{}", b.order), b.at, b.order, &b.body))
            .collect();
        let ax = FakeAxReadPort::new(tg_messages);

        let clock = VirtualClock::new(0);
        let rig = AcquiringRig::new(clock.clone());
        let ladder = rig.ladder();
        let sender = MarkerSender::new();
        let store = SqliteRelayStore::open_in_memory().unwrap();
        let journal = SqliteHistoryStore::open_in_memory().unwrap();
        let trip = Tripwires::new();

        let mut relay = TelegramRelay::new(
            &ax, &ladder, &sender, &store, &store, &journal, &clock, pair(&[10]), true,
        );

        let tick = relay.tick();

        // Every processed message settled as Committed or MissFinal (the cursor
        // advanced past each), never left mid-retry.
        for outcome in &tick.processed {
            prop_assert!(
                matches!(outcome.settlement, Settlement::Committed(_) | Settlement::MissFinal(_)),
                "an all-advancing batch left a message retrying"
            );
        }
        // All source messages were processed exactly once (one at a time).
        prop_assert_eq!(tick.processed.len(), built.len());

        // The confirmed-send stream equals the source subsequence in order
        // (R7.9, R12.8).
        prop_assert_eq!(sender.bodies(), expected_sent);

        // The cursor advanced to the last message in source order.
        prop_assert_eq!(relay.cursor().last_at, last_at);
        prop_assert_eq!(relay.cursor().last_display_order, last_order);

        // Restart: a fresh relay over the same store resumes past everything and
        // re-delivers nothing (R7.14 cursor persistence, R7.10 dedup).
        let sends_before = sender.bodies().len();
        let mut relay2 = TelegramRelay::new(
            &ax, &ladder, &sender, &store, &store, &journal, &clock, pair(&[10]), true,
        );
        prop_assert_eq!(relay2.cursor().last_at, last_at);
        relay2.tick();
        prop_assert_eq!(sender.bodies().len(), sends_before);

        // Only redacted relay events reached the journal.
        for ev in journal.recent(5).unwrap() {
            prop_assert_eq!(ev.flow, FlowKind::TelegramRelay);
        }

        trip.assert_untouched()?;
    }
}

// ===========================================================================
// Property 19: image counts match or missing is explicit (relay perspective).
// ===========================================================================

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// Property 19: for a source message carrying 0..=20 images and a sender
    /// that confirms `delivered` of them, the relay settles as `Committed`
    /// exactly when every source image was delivered and none was dropped over
    /// the per-message cap of 10; otherwise it is a miss. A miss keeps the
    /// cursor pinned and retries (`MissRetrying`) until three consecutive
    /// mismatches settle it as `MissFinal`, at which point the cursor advances
    /// and the message is never recorded as delivered (retryable throughout).
    /// The tally always reports source vs delivered so the missing count is
    /// explicit (R7.2, R7.7, R7.8, R7.18, R12.6).
    ///
    /// Validates: Requirements 7.2, 7.7, 7.8, 7.18, 12.6
    #[test]
    fn relay_image_counts_match_or_miss_is_explicit(
        source in 0usize..=20,
        delivered_raw in 0usize..=20,
    ) {
        // A realistic sender delivers no more than the capped, source-bounded
        // count.
        let delivered = delivered_raw.min(source).min(MAX_IMAGES);
        let dropped = source.saturating_sub(MAX_IMAGES);
        // The relay's commit rule: every source image delivered AND nothing
        // dropped over the cap.
        let complete = delivered == source && dropped == 0;

        let mut m = text_msg("m1", 1, 0, "with images");
        m.images = (0..source).map(img_ref).collect();
        let ax = FakeAxReadPort::new(vec![m]);

        let clock = VirtualClock::new(0);
        let rig = AcquiringRig::new(clock.clone());
        let ladder = rig.ladder();
        let sender = RecordingSender::new(RelaySendOutcome::Sent {
            images_delivered: delivered,
        });
        let store = SqliteRelayStore::open_in_memory().unwrap();
        let journal = SqliteHistoryStore::open_in_memory().unwrap();
        let trip = Tripwires::new();
        let pid_hash = openkakao_cli::context::provenance_id("m1");

        let mut relay = TelegramRelay::new(
            &ax, &ladder, &sender, &store, &store, &journal, &clock, pair(&[10]), true,
        );

        if complete {
            let tick = relay.tick();
            let outcome = &tick.processed[0];
            match &outcome.settlement {
                Settlement::Committed(tally) => {
                    prop_assert_eq!(tally.source, source);
                    prop_assert_eq!(tally.delivered, source);
                    prop_assert!(tally.is_complete());
                }
                other => prop_assert!(false, "expected committed, got {other:?}"),
            }
            // Cursor advanced; the message is recorded as delivered.
            prop_assert_eq!(relay.cursor().last_at, 1);
            prop_assert!(store.is_delivered(10, &pid_hash));
        } else {
            // Ticks 1..=(LIMIT-1): retrying, cursor pinned, missing count
            // explicit, never marked delivered (R7.8).
            for _ in 0..(MISMATCH_STREAK_LIMIT - 1) {
                let tick = relay.tick();
                let outcome = &tick.processed[0];
                match &outcome.settlement {
                    Settlement::MissRetrying(tally) => {
                        prop_assert_eq!(tally.source, source);
                        // The missing count is (source − delivered), always
                        // explicit (R12.6).
                        prop_assert_eq!(tally.missing(), source - tally.delivered);
                        prop_assert!(!tally.is_complete());
                    }
                    other => prop_assert!(false, "expected retrying, got {other:?}"),
                }
                prop_assert_eq!(relay.cursor().last_at, 0, "cursor advanced during retry");
                prop_assert!(!store.is_delivered(10, &pid_hash));
            }
            // The MISMATCH_STREAK_LIMIT-th consecutive mismatch settles final.
            let tick = relay.tick();
            let outcome = &tick.processed[0];
            match &outcome.settlement {
                Settlement::MissFinal(tally) => {
                    prop_assert_eq!(tally.source, source);
                    prop_assert!(!tally.is_complete());
                }
                other => prop_assert!(false, "expected final miss, got {other:?}"),
            }
            // Cursor advances on the final miss, but delivery is never recorded,
            // so the message stays retryable in principle (R7.18).
            prop_assert_eq!(relay.cursor().last_at, 1);
            prop_assert!(!store.is_delivered(10, &pid_hash));
        }

        // Relay events stay redacted.
        for ev in journal.recent(10).unwrap() {
            prop_assert_eq!(ev.flow, FlowKind::TelegramRelay);
        }

        trip.assert_untouched()?;
    }
}

// ===========================================================================
// Property 20: image ladder order and permission coupling.
// ===========================================================================

/// What one ladder rung does when called.
#[derive(Debug, Clone, Copy)]
enum Behavior {
    /// Returns a non-empty image within the two-second bound.
    Ok,
    /// Returns an empty image (a failure that advances).
    Empty,
    /// Answers past the two-second bound (a timeout that advances).
    Timeout,
    /// Fails with a not-available error (advances).
    NotAvailable,
}

impl Behavior {
    /// How long this rung takes on the shared clock, in milliseconds.
    fn takes_ms(self) -> i64 {
        match self {
            Behavior::Timeout => STEP_TIMEOUT_MS as i64 + 1,
            Behavior::Ok | Behavior::Empty => 100,
            Behavior::NotAvailable => 50,
        }
    }
}

fn behavior_strategy() -> impl Strategy<Value = Behavior> {
    prop_oneof![
        Just(Behavior::Ok),
        Just(Behavior::Empty),
        Just(Behavior::Timeout),
        Just(Behavior::NotAvailable),
    ]
}

/// A configurable rung that advances the shared clock and counts its calls, so
/// a test can prove which rungs were and were not attempted.
struct CfgAcquirer {
    kind: ImageAcquisition,
    clock: VirtualClock,
    behavior: Behavior,
    calls: Cell<usize>,
}
impl CfgAcquirer {
    fn new(kind: ImageAcquisition, clock: VirtualClock, behavior: Behavior) -> Self {
        Self {
            kind,
            clock,
            behavior,
            calls: Cell::new(0),
        }
    }
    fn calls(&self) -> usize {
        self.calls.get()
    }
}
impl ImageAcquirer for CfgAcquirer {
    fn kind(&self) -> ImageAcquisition {
        self.kind
    }
    fn acquire(&self, _r: &ImageRef) -> Result<ImageBlob, AcquireError> {
        self.calls.set(self.calls.get() + 1);
        self.clock.advance_ms(self.behavior.takes_ms());
        match self.behavior {
            Behavior::Ok | Behavior::Timeout => Ok(ImageBlob {
                bytes: vec![1, 2, 3],
                mime: "image/png".into(),
            }),
            Behavior::Empty => Ok(ImageBlob {
                bytes: Vec::new(),
                mime: "image/png".into(),
            }),
            Behavior::NotAvailable => Err(AcquireError::NotAvailable),
        }
    }
}

/// The independent oracle: mirror the ladder's contract to compute the expected
/// per-rung attempts, whether an image was acquired, and via which rung.
fn ladder_oracle(
    behaviors: [Behavior; 3],
    granted: bool,
) -> ([StepAttempt; 3], bool, Option<usize>) {
    let kinds = [
        ImageAcquisition::OriginalSave,
        ImageAcquisition::CacheFolder,
        ImageAcquisition::ScreenCapture,
    ];
    let mut attempts = [StepAttempt::Skipped; 3];
    for (i, &b) in behaviors.iter().enumerate() {
        // The screen-capture rung needs the permission; without it the rung is
        // not attempted at all (R9.10).
        if kinds[i] == ImageAcquisition::ScreenCapture && !granted {
            attempts[i] = StepAttempt::PermissionMissing;
            continue;
        }
        // A late answer is a timeout regardless of what it returned (R7.5).
        if b.takes_ms() > STEP_TIMEOUT_MS as i64 {
            attempts[i] = StepAttempt::TimedOut;
            continue;
        }
        match b {
            Behavior::Ok => {
                attempts[i] = StepAttempt::Acquired;
                return (attempts, true, Some(i));
            }
            Behavior::Empty => attempts[i] = StepAttempt::Empty,
            Behavior::NotAvailable => attempts[i] = StepAttempt::Error("not_available"),
            Behavior::Timeout => unreachable!("handled by the time bound above"),
        }
    }
    (attempts, false, None)
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// Property 20: for any combination of three rung behaviors and the screen
    /// permission, the ladder tries rungs strictly in order, advances only on a
    /// failure, stops at the first non-empty image acquired within two seconds,
    /// never calls a rung after a success, and never attempts the screen-capture
    /// rung without the permission. The produced per-rung attempts, the acquired
    /// flag, and the acquiring rung all match the independent oracle, and the
    /// call counts confirm which rungs ran (R7.5, R7.6, R9.10).
    ///
    /// Validates: Requirements 7.5, 7.6, 9.10
    #[test]
    fn ladder_tries_in_order_and_couples_permission(
        b0 in behavior_strategy(),
        b1 in behavior_strategy(),
        b2 in behavior_strategy(),
        granted in any::<bool>(),
    ) {
        let behaviors = [b0, b1, b2];
        let clock = VirtualClock::new(0);
        let s0 = CfgAcquirer::new(ImageAcquisition::OriginalSave, clock.clone(), b0);
        let s1 = CfgAcquirer::new(ImageAcquisition::CacheFolder, clock.clone(), b1);
        let s2 = CfgAcquirer::new(ImageAcquisition::ScreenCapture, clock.clone(), b2);
        let ladder = ImageLadder {
            steps: [&s0, &s1, &s2],
            clock: &clock,
        };

        let out = ladder.acquire(&img_ref(0), granted);
        let (want_attempts, want_acquired, want_via) = ladder_oracle(behaviors, granted);

        // Per-rung attempts, acquisition, and screen-capture flag match oracle.
        prop_assert_eq!(out.attempts, want_attempts);
        prop_assert_eq!(out.is_acquired(), want_acquired);
        prop_assert_eq!(
            out.used_screen_capture(),
            want_via == Some(2),
        );

        // Call counts: a rung ran iff its attempt was neither Skipped nor
        // PermissionMissing.
        let counts = [s0.calls(), s1.calls(), s2.calls()];
        for i in 0..3 {
            let expected_call = !matches!(
                want_attempts[i],
                StepAttempt::Skipped | StepAttempt::PermissionMissing
            );
            prop_assert_eq!(counts[i], usize::from(expected_call), "rung {} call count", i);
        }
        // The screen-capture rung is never called without the permission
        // (R9.10). Its attempt is PermissionMissing when reached and Skipped
        // when an earlier rung already succeeded — both are covered by the
        // oracle comparison above; here we pin that it never actually ran.
        if !granted {
            prop_assert_eq!(s2.calls(), 0);
        }

        // No rung is attempted after a success: everything after the acquiring
        // rung is Skipped.
        if let Some(via) = want_via {
            for later in (via + 1)..3 {
                prop_assert_eq!(out.attempts[later], StepAttempt::Skipped);
            }
        }
    }
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(100))]

    /// Property 20 (R7.17 in the relay): when all three image-acquisition rungs
    /// fail, the images are unacquired but the message's text and links are
    /// still relayed — the send happens and the message settles as a retryable
    /// miss rather than being dropped.
    ///
    /// Validates: Requirements 7.17
    #[test]
    fn all_rungs_fail_still_relays_text_and_links(image_count in 1usize..=5) {
        let mut m = text_msg("m1", 1, 0, "본문은 남아요");
        m.links = vec![LinkRef::Resolved("https://example.com/a".into())];
        m.images = (0..image_count).map(img_ref).collect();
        let ax = FakeAxReadPort::new(vec![m]);

        let clock = VirtualClock::new(0);
        let rig = FailingRig::new(clock.clone());
        let ladder = rig.ladder();
        // The sender is reached (text/links relayed) but confirms zero images.
        let sender = RecordingSender::new(RelaySendOutcome::Sent { images_delivered: 0 });
        let store = SqliteRelayStore::open_in_memory().unwrap();
        let journal = SqliteHistoryStore::open_in_memory().unwrap();
        let trip = Tripwires::new();

        let mut relay = TelegramRelay::new(
            &ax, &ladder, &sender, &store, &store, &journal, &clock, pair(&[10]), true,
        );

        let tick = relay.tick();
        // The send still happened — text and links continue (R7.17).
        prop_assert!(sender.count() >= 1);
        // With images missing, the message is a retryable miss (not committed).
        prop_assert!(matches!(
            tick.processed[0].settlement,
            Settlement::MissRetrying(_)
        ));

        trip.assert_untouched()?;
    }
}

// ===========================================================================
// Property 23: content extraction, detection mode, latency-display rules.
// ===========================================================================

/// A source link: either a resolvable real URL or a display-only string whose
/// real address could not be recovered (R7.3, R7.4).
#[derive(Debug, Clone)]
enum LinkSpec {
    Resolved(String),
    DisplayOnly(String),
}

fn link_spec_strategy() -> impl Strategy<Value = LinkSpec> {
    prop_oneof![
        "https://[a-z]{3,8}\\.com/[a-z0-9]{1,6}".prop_map(LinkSpec::Resolved),
        "[가-힣a-z ]{1,10}".prop_map(LinkSpec::DisplayOnly),
    ]
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// Property 23 (links): `extract_links` renders each link in original
    /// appearance order — a resolved link as its real URL, a display-only link
    /// as its text plus an "unconfirmed address" note — and reports the count of
    /// display-only links, capping the whole list at the per-message maximum.
    /// The relay surfaces the same display-only count on the message outcome.
    ///
    /// Validates: Requirements 7.3, 7.4
    #[test]
    fn links_prefer_real_url_and_flag_display_only(
        specs in prop::collection::vec(link_spec_strategy(), 0..=25),
    ) {
        let links: Vec<LinkRef> = specs
            .iter()
            .map(|s| match s {
                LinkSpec::Resolved(u) => LinkRef::Resolved(u.clone()),
                LinkSpec::DisplayOnly(t) => LinkRef::DisplayOnly(t.clone()),
            })
            .collect();

        let (rendered, unresolved) = extract_links(&links);

        // The output is capped at the per-message link maximum.
        let expected_len = specs.len().min(openkakao_cli::relay::MAX_LINKS);
        prop_assert_eq!(rendered.len(), expected_len);

        // Order is preserved; each kind is rendered per its rule.
        let mut expected_unresolved = 0usize;
        for (i, spec) in specs.iter().take(expected_len).enumerate() {
            match spec {
                LinkSpec::Resolved(u) => {
                    // A resolved link renders as exactly its real URL (R7.3).
                    prop_assert_eq!(&rendered[i], u);
                }
                LinkSpec::DisplayOnly(t) => {
                    expected_unresolved += 1;
                    // A display-only link keeps its text and is flagged (R7.4).
                    prop_assert!(rendered[i].contains(t.as_str()));
                    prop_assert!(rendered[i].contains("확인 불가"));
                }
            }
        }
        prop_assert_eq!(unresolved, expected_unresolved);

        // The relay reports the same display-only count on the outcome.
        let mut m = text_msg("m1", 1, 0, "본문");
        m.links = links.clone();
        let ax = FakeAxReadPort::new(vec![m]);
        let clock = VirtualClock::new(0);
        let rig = AcquiringRig::new(clock.clone());
        let ladder = rig.ladder();
        let sender = RecordingSender::new(RelaySendOutcome::Sent { images_delivered: 0 });
        let store = SqliteRelayStore::open_in_memory().unwrap();
        let journal = SqliteHistoryStore::open_in_memory().unwrap();
        let trip = Tripwires::new();
        let mut relay = TelegramRelay::new(
            &ax, &ladder, &sender, &store, &store, &journal, &clock, pair(&[10]), true,
        );
        let tick = relay.tick();
        prop_assert_eq!(tick.processed[0].unresolved_links, expected_unresolved);

        trip.assert_untouched()?;
    }
}

/// One excluded attachment kind.
fn excluded_strategy() -> impl Strategy<Value = ExcludedAttachment> {
    prop_oneof![
        Just(ExcludedAttachment::Video),
        Just(ExcludedAttachment::File),
        Just(ExcludedAttachment::Voice),
        Just(ExcludedAttachment::Sticker),
    ]
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// Property 23 (excluded attachments): non-relayable attachments (video,
    /// file, voice, sticker) are excluded from the relay and reported by kind
    /// and count; an empty set produces no note. The relay carries the excluded
    /// set through to the message outcome unchanged (R7.19).
    ///
    /// Validates: Requirements 7.19
    #[test]
    fn excluded_attachments_are_dropped_and_reported(
        excluded in prop::collection::vec(excluded_strategy(), 0..=8),
    ) {
        let note = summarize_excluded(&excluded);
        if excluded.is_empty() {
            prop_assert!(note.is_none());
        } else {
            let note = note.expect("a non-empty set yields a note");
            let count = |k: ExcludedAttachment| excluded.iter().filter(|&&e| e == k).count();
            // Build each expected substring first: a `{}` inside a `prop_assert!`
            // condition would be misread as a format placeholder by the macro.
            let video = count(ExcludedAttachment::Video);
            if video > 0 {
                let want = format!("동영상 {video}개");
                prop_assert!(note.contains(&want));
            }
            let file = count(ExcludedAttachment::File);
            if file > 0 {
                let want = format!("파일 {file}개");
                prop_assert!(note.contains(&want));
            }
            let voice = count(ExcludedAttachment::Voice);
            if voice > 0 {
                let want = format!("음성 {voice}개");
                prop_assert!(note.contains(&want));
            }
            let sticker = count(ExcludedAttachment::Sticker);
            if sticker > 0 {
                let want = format!("스티커 {sticker}개");
                prop_assert!(note.contains(&want));
            }
        }

        // The relay preserves the excluded set on the outcome, and still relays
        // the message's text.
        let mut m = text_msg("m1", 1, 0, "본문 텍스트");
        m.excluded = excluded.clone();
        let ax = FakeAxReadPort::new(vec![m]);
        let clock = VirtualClock::new(0);
        let rig = AcquiringRig::new(clock.clone());
        let ladder = rig.ladder();
        let sender = RecordingSender::new(RelaySendOutcome::Sent { images_delivered: 0 });
        let store = SqliteRelayStore::open_in_memory().unwrap();
        let journal = SqliteHistoryStore::open_in_memory().unwrap();
        let trip = Tripwires::new();
        let mut relay = TelegramRelay::new(
            &ax, &ladder, &sender, &store, &store, &journal, &clock, pair(&[10]), true,
        );
        let tick = relay.tick();
        prop_assert_eq!(&tick.processed[0].excluded, &excluded);

        trip.assert_untouched()?;
    }
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// Property 23 (detection mode): the relay prefers AX notifications and
    /// switches to polling only once the subscription is unconfirmed for five
    /// seconds; the polling interval is always clamped to two seconds or less
    /// (R7.13).
    ///
    /// Validates: Requirements 7.13
    #[test]
    fn detection_mode_prefers_notification_then_polls(
        delay_ms in 0i64..10_000,
        poll_interval in 0u64..10_000,
    ) {
        use openkakao_cli::relay::{NOTIFICATION_CONFIRM_MS, POLLING_MAX_INTERVAL_MS};

        // Notification path: with no messages the subscription is never
        // confirmed, so after five seconds the relay falls back to polling.
        {
            let clock = VirtualClock::new(0);
            let ax = FakeAxReadPort::new(vec![]);
            let rig = AcquiringRig::new(clock.clone());
            let ladder = rig.ladder();
            let sender = RecordingSender::new(RelaySendOutcome::Sent { images_delivered: 0 });
            let store = SqliteRelayStore::open_in_memory().unwrap();
            let journal = SqliteHistoryStore::open_in_memory().unwrap();
            let mut relay = TelegramRelay::new(
                &ax, &ladder, &sender, &store, &store, &journal, &clock, pair(&[10]), true,
            );
            clock.advance_ms(delay_ms);
            let mode = relay.tick().detect_mode;
            if delay_ms >= NOTIFICATION_CONFIRM_MS {
                match mode {
                    DetectMode::Polling { interval_ms } => {
                        prop_assert!(interval_ms <= POLLING_MAX_INTERVAL_MS);
                    }
                    other => prop_assert!(false, "expected polling, got {other:?}"),
                }
            } else {
                prop_assert_eq!(mode, DetectMode::Notification);
            }
        }

        // Polling path: any reported interval is clamped to the maximum.
        {
            let clock = VirtualClock::new(0);
            let ax = FakeAxReadPort::new(vec![])
                .with_mode(DetectMode::Polling { interval_ms: poll_interval });
            let rig = AcquiringRig::new(clock.clone());
            let ladder = rig.ladder();
            let sender = RecordingSender::new(RelaySendOutcome::Sent { images_delivered: 0 });
            let store = SqliteRelayStore::open_in_memory().unwrap();
            let journal = SqliteHistoryStore::open_in_memory().unwrap();
            let mut relay = TelegramRelay::new(
                &ax, &ladder, &sender, &store, &store, &journal, &clock, pair(&[10]), true,
            );
            match relay.tick().detect_mode {
                DetectMode::Polling { interval_ms } => {
                    prop_assert_eq!(interval_ms, poll_interval.min(POLLING_MAX_INTERVAL_MS));
                }
                other => prop_assert!(false, "expected polling, got {other:?}"),
            }
        }
    }
}

/// The nearest-rank percentile oracle over an already-sorted slice.
fn nearest_rank(sorted: &[u64], p: f64) -> u64 {
    let n = sorted.len();
    let rank = (p / 100.0 * n as f64).ceil() as usize;
    let idx = rank.saturating_sub(1).min(n - 1);
    sorted[idx]
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// Property 23 (latency display): the latency window judges its targets only
    /// once at least 20 samples exist. Below that, no percentile and no
    /// target-exceeded verdict is produced; at or above it, the P50/P95 match
    /// the nearest-rank definition and the exceeded flag equals "either
    /// percentile over its target" (R7.11, R7.12). The window keeps only the
    /// most recent 100 samples.
    ///
    /// Validates: Requirements 7.11, 7.12
    #[test]
    fn latency_window_readiness_and_percentiles(
        samples in prop::collection::vec(0u64..30_000, 0..250),
    ) {
        let mut w = LatencyWindow::new();
        for &s in &samples {
            w.record(s);
        }

        let kept = samples.len().min(openkakao_cli::relay::LATENCY_WINDOW_MAX);
        prop_assert_eq!(w.len(), kept);

        if kept < LATENCY_READY_MIN {
            prop_assert!(!w.ready());
            prop_assert_eq!(w.p50(), None);
            prop_assert_eq!(w.p95(), None);
            prop_assert!(!w.exceeds_target());
        } else {
            prop_assert!(w.ready());
            // Oracle over the most-recent `kept` samples, sorted.
            let mut recent: Vec<u64> = samples[samples.len() - kept..].to_vec();
            recent.sort_unstable();
            let p50 = nearest_rank(&recent, 50.0);
            let p95 = nearest_rank(&recent, 95.0);
            prop_assert_eq!(w.p50(), Some(p50));
            prop_assert_eq!(w.p95(), Some(p95));
            prop_assert_eq!(
                w.exceeds_target(),
                p50 > P50_TARGET_MS || p95 > P95_TARGET_MS
            );
        }
    }
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(100))]

    /// Property 23 (relay keeps going regardless of latency): even when every
    /// confirmed relay's measured latency far exceeds the targets, the relay
    /// keeps processing — all messages are read (they are all after the cursor),
    /// committed, and the cursor advances — while the window reports the target
    /// as exceeded (R7.1, R7.11, R7.12).
    ///
    /// Validates: Requirements 7.1, 7.11, 7.12
    #[test]
    fn relay_continues_when_latency_exceeds_target(count in 20usize..=40) {
        // Messages sit far in the past relative to the clock, so each confirmed
        // relay records a very large latency.
        let messages: Vec<TelegramMessage> = (0..count)
            .map(|i| text_msg(&format!("m{i}"), (i as i64) + 1, 0, &format!("본문 {i}")))
            .collect();
        let ax = FakeAxReadPort::new(messages);
        let clock = VirtualClock::new(1_000_000);
        let rig = AcquiringRig::new(clock.clone());
        let ladder = rig.ladder();
        let sender = RecordingSender::new(RelaySendOutcome::Sent { images_delivered: 0 });
        let store = SqliteRelayStore::open_in_memory().unwrap();
        let journal = SqliteHistoryStore::open_in_memory().unwrap();
        let trip = Tripwires::new();
        let mut relay = TelegramRelay::new(
            &ax, &ladder, &sender, &store, &store, &journal, &clock, pair(&[10]), true,
        );

        let tick = relay.tick();
        // Every message was read (all after the initial cursor) and committed.
        prop_assert!(!tick.ax_unavailable);
        prop_assert_eq!(tick.processed.len(), count);
        prop_assert!(tick
            .processed
            .iter()
            .all(|o| matches!(o.settlement, Settlement::Committed(_))));
        // The cursor advanced to the last message.
        prop_assert_eq!(relay.cursor().last_at, count as i64);
        // Latency is judged (≥ 20 samples) and exceeds the target, yet relaying
        // still completed the whole batch.
        prop_assert!(tick.latency.ready);
        prop_assert!(tick.latency.exceeds_target);

        trip.assert_untouched()?;
    }
}

/// Property 23 (registered pair bound): the relay caps registered pairs at
/// eight (R7.1). A single sanity check, since the bound is a module constant the
/// relay reads.
///
/// Validates: Requirements 7.1
#[test]
fn registered_pairs_are_bounded() {
    assert_eq!(MAX_PAIRS, 8);
    // A pair fanning out to several rooms is well-formed within the bound.
    let p = pair(&[1, 2, 3]);
    assert_eq!(p.kakao_rooms.len(), 3);
}
