//! Property-based tests for the two-path link collector and the collection /
//! forward deadlines (task 7.2).
//!
//! Feature: kakao-agent-live-ops
//! Property 24: 수집 경로 라우팅 규칙 (collection-path routing rules)
//! Property 25: 수집·전달 마감 준수와 임시 원문 데이터 삭제
//!   (collection/forward deadline compliance and temporary raw-data deletion)
//!
//! Property 24 drives [`RoutingCollector::collect`] over arbitrary
//! Aside-installed / response-delay / mid-collection-failure combinations and
//! pins:
//!   * the connection check is issued exactly once, and Aside is used only when
//!     that single check answers healthy within three seconds (R11.1, R11.2),
//!   * a switch to the fallback path happens at most once, and when it does the
//!     switch reason is carried in the result (R11.3, R11.11),
//!   * a private/deleted post is terminal — the other path is never called
//!     (zero calls) (R11.10), and
//!   * when a secret cannot be read the remaining path is tried exactly once and
//!     the guidance carries only the secret's *name*, never its value (R11.7).
//!
//! Property 25 pins that one link's collection finishes within the twenty-second
//! bound (connection check + switch included) and one link's forward finishes
//! within the thirty-second bound, that a deadline overrun performs zero sends,
//! and that on every termination — success, failure, or abandonment — the
//! temporary raw data is deleted and the deletion completion is reported
//! (R6.8, R6.12, R6.15, R11.4, R11.8, R11.12).
//!
//! Because the collector's own test doubles live in its `#[cfg(test)]` module
//! (invisible to an integration test), this file defines its own small fakes:
//! a scripted [`PageCollector`], a counting [`AsideTransport`], secret stores, a
//! no-op [`NetworkPort`], a scripted link collector / room sender for the
//! forwarder, and a minimal [`RoomCatalog`].
//!
//! Validates: Requirements 6.8, 6.12, 6.15, 11.1, 11.2, 11.3, 11.4, 11.7,
//! 11.8, 11.10, 11.11, 11.12

use std::cell::Cell;

use openkakao_cli::collector::{
    AsideCollector, Collected, CollectError, CollectFailure, CollectorPath, LoopbackEndpoint,
    PageCollector, RoutingCollector, SwitchReason, TempScope, ASIDE_COLLECT_TIMEOUT_SECS,
    COLLECT_DEADLINE_SECS, DELETION_DEADLINE_MS, HEALTH_TIMEOUT_SECS,
};
use openkakao_cli::fakes::VirtualClock;
use openkakao_cli::ports::{Clock, Secret, SecretError, SecretKey, SecretStore};
use proptest::prelude::*;

// ===========================================================================
// Local fakes (integration tests cannot see the module's #[cfg(test)] items).
// ===========================================================================

/// A scripted [`PageCollector`] with configurable health and collect results
/// plus per-method call counters, so a test can assert a path was (or was not)
/// called.
struct ScriptedPage {
    path: CollectorPath,
    health: Result<(), CollectError>,
    collect: Result<Collected, CollectError>,
    health_calls: Cell<usize>,
    collect_calls: Cell<usize>,
}

impl ScriptedPage {
    fn new(
        path: CollectorPath,
        health: Result<(), CollectError>,
        collect: Result<Collected, CollectError>,
    ) -> Self {
        Self {
            path,
            health,
            collect,
            health_calls: Cell::new(0),
            collect_calls: Cell::new(0),
        }
    }

    fn health_calls(&self) -> usize {
        self.health_calls.get()
    }

    fn collect_calls(&self) -> usize {
        self.collect_calls.get()
    }
}

impl PageCollector for ScriptedPage {
    fn path(&self) -> CollectorPath {
        self.path
    }

    fn health_check(&self) -> Result<(), CollectError> {
        self.health_calls.set(self.health_calls.get() + 1);
        self.health.clone()
    }

    fn collect(&self, _url: &str, _scope: &mut TempScope) -> Result<Collected, CollectError> {
        self.collect_calls.set(self.collect_calls.get() + 1);
        self.collect.clone()
    }
}

/// A clock whose reads advance on their own by a fixed step, so a routed
/// collection blows past its bound between two reads without any real sleep.
struct AdvancingClock {
    now: Cell<i64>,
    step_ms: i64,
}

impl AdvancingClock {
    fn new(step_ms: i64) -> Self {
        Self {
            now: Cell::new(0),
            step_ms,
        }
    }
}

impl Clock for AdvancingClock {
    fn now_ms(&self) -> i64 {
        let v = self.now.get();
        self.now.set(v + self.step_ms);
        v
    }
}

/// A counting [`AsideTransport`] whose health call advances the shared clock by
/// a scripted delay, so the three-second health bound is exercised on a virtual
/// timeline. It records how many health calls happened.
struct CountingTransport {
    clock: VirtualClock,
    health_delay_ms: i64,
    health_result: Result<(), CollectError>,
    fetch_result: Result<Collected, CollectError>,
    health_calls: Cell<usize>,
}

impl openkakao_cli::collector::AsideTransport for CountingTransport {
    fn health(&self, _endpoint: &LoopbackEndpoint) -> Result<(), CollectError> {
        self.health_calls.set(self.health_calls.get() + 1);
        self.clock.advance_ms(self.health_delay_ms);
        self.health_result.clone()
    }

    fn fetch(
        &self,
        _endpoint: &LoopbackEndpoint,
        _url: &str,
        _session: Option<&Secret>,
        _scope: &mut TempScope,
    ) -> Result<Collected, CollectError> {
        self.fetch_result.clone()
    }
}

/// A secret store that always yields a session token.
struct OkSecrets;
impl SecretStore for OkSecrets {
    fn get(&self, _key: SecretKey) -> Result<Secret, SecretError> {
        Ok(Secret::new("session-token"))
    }
}

/// A secret store that never has the requested secret (R11.7).
struct MissingSecrets;
impl SecretStore for MissingSecrets {
    fn get(&self, _key: SecretKey) -> Result<Secret, SecretError> {
        Err(SecretError::NotFound)
    }
}

/// A collected result on `path` with no images and a fixed body.
fn collected(path: CollectorPath) -> Collected {
    Collected {
        title: Some("title".into()),
        body: "body".into(),
        images: Vec::new(),
        author: None,
        path,
        switched: None,
    }
}

// ===========================================================================
// Property 24 — routing rules.
// ===========================================================================

/// How the scripted Aside path behaves for one generated scenario.
#[derive(Debug, Clone, Copy)]
enum AsideBehavior {
    HealthOk,
    HealthNotInstalled,
    HealthTimeout,
    CollectPrivate,
    CollectDeleted,
    CollectUnauthorized,
    CollectTimeout,
    CollectTransport,
}

fn aside_behavior_strategy() -> impl Strategy<Value = AsideBehavior> {
    prop_oneof![
        Just(AsideBehavior::HealthOk),
        Just(AsideBehavior::HealthNotInstalled),
        Just(AsideBehavior::HealthTimeout),
        Just(AsideBehavior::CollectPrivate),
        Just(AsideBehavior::CollectDeleted),
        Just(AsideBehavior::CollectUnauthorized),
        Just(AsideBehavior::CollectTimeout),
        Just(AsideBehavior::CollectTransport),
    ]
}

/// Build a scripted Aside page for a behavior.
fn aside_for(behavior: AsideBehavior) -> ScriptedPage {
    match behavior {
        AsideBehavior::HealthOk => ScriptedPage::new(
            CollectorPath::Aside,
            Ok(()),
            Ok(collected(CollectorPath::Aside)),
        ),
        AsideBehavior::HealthNotInstalled => ScriptedPage::new(
            CollectorPath::Aside,
            Err(CollectError::NotInstalled),
            Ok(collected(CollectorPath::Aside)),
        ),
        AsideBehavior::HealthTimeout => ScriptedPage::new(
            CollectorPath::Aside,
            Err(CollectError::HealthTimeout),
            Ok(collected(CollectorPath::Aside)),
        ),
        AsideBehavior::CollectPrivate => {
            ScriptedPage::new(CollectorPath::Aside, Ok(()), Err(CollectError::Private))
        }
        AsideBehavior::CollectDeleted => {
            ScriptedPage::new(CollectorPath::Aside, Ok(()), Err(CollectError::Deleted))
        }
        AsideBehavior::CollectUnauthorized => ScriptedPage::new(
            CollectorPath::Aside,
            Ok(()),
            Err(CollectError::Unauthorized {
                item: "브라우저 연결 정보",
            }),
        ),
        AsideBehavior::CollectTimeout => {
            ScriptedPage::new(CollectorPath::Aside, Ok(()), Err(CollectError::Timeout))
        }
        AsideBehavior::CollectTransport => ScriptedPage::new(
            CollectorPath::Aside,
            Ok(()),
            Err(CollectError::Transport("aside down".into())),
        ),
    }
}

/// Whether a behavior is terminal (private/deleted): the other path must never
/// be tried (R11.10).
fn is_terminal(behavior: AsideBehavior) -> bool {
    matches!(
        behavior,
        AsideBehavior::CollectPrivate | AsideBehavior::CollectDeleted
    )
}

/// Whether a behavior means Aside served the result itself (health ok + collect
/// ok), so no switch happens.
fn aside_serves(behavior: AsideBehavior) -> bool {
    matches!(behavior, AsideBehavior::HealthOk)
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// Aside is preferred only when its single connection check passes; a switch
    /// to the fallback happens at most once (carrying its reason); a
    /// private/deleted result is terminal with zero calls to the other path;
    /// and a missing secret tries the remaining path exactly once. The
    /// connection check is issued exactly once regardless of outcome.
    ///
    /// Validates: Requirements 11.1, 11.2, 11.3, 11.7, 11.10, 11.11
    #[test]
    fn routing_rules_hold(
        behavior in aside_behavior_strategy(),
        fallback_ok in any::<bool>(),
    ) {
        // A non-advancing clock: only the routing rules (not the deadline) are
        // under test here.
        let clock = VirtualClock::new(0);
        let aside = aside_for(behavior);
        let fallback = ScriptedPage::new(
            CollectorPath::Fallback,
            Ok(()),
            if fallback_ok {
                Ok(collected(CollectorPath::Fallback))
            } else {
                Err(CollectError::Transport("net down".into()))
            },
        );
        let router = RoutingCollector {
            aside: &aside,
            fallback: &fallback,
            clock: &clock,
        };

        let result = router.collect("https://example.com/post");

        // The connection check is issued exactly once (R11.2).
        prop_assert_eq!(aside.health_calls(), 1);

        if aside_serves(behavior) {
            // Aside served: no switch, fallback never collected (R11.1).
            let collected = result.expect("aside should serve");
            prop_assert_eq!(collected.path, CollectorPath::Aside);
            prop_assert_eq!(collected.switched, None);
            prop_assert_eq!(fallback.collect_calls(), 0);
        } else if is_terminal(behavior) {
            // Private/deleted is terminal: the other path is never called
            // (R11.10).
            prop_assert!(matches!(result, Err(CollectFailure::Terminal(_))));
            prop_assert_eq!(fallback.collect_calls(), 0);
        } else {
            // Every remaining case switches to the fallback exactly once
            // (R11.11).
            prop_assert_eq!(fallback.collect_calls(), 1);
            match result {
                Ok(collected) => {
                    prop_assert!(fallback_ok);
                    prop_assert_eq!(collected.path, CollectorPath::Fallback);
                    // A switch carries its reason (R11.3, R11.11).
                    prop_assert!(collected.switched.is_some());
                    // A missing secret switches for the SecretUnavailable reason
                    // (R11.7).
                    if matches!(behavior, AsideBehavior::CollectUnauthorized) {
                        prop_assert_eq!(collected.switched, Some(SwitchReason::SecretUnavailable));
                    }
                }
                Err(CollectFailure::BothFailed { .. }) => {
                    prop_assert!(!fallback_ok);
                }
                other => prop_assert!(false, "unexpected routed result: {other:?}"),
            }
        }
        // Whatever happened, the fallback was tried at most once — a second
        // switch is unrepresentable.
        prop_assert!(fallback.collect_calls() <= 1);
    }

    /// A missing/denied secret surfaces guidance that carries only the secret's
    /// *name*, never any secret value (R11.7). The error variant holds a
    /// `&'static str` name, so no generated secret string can appear in the
    /// message.
    ///
    /// Validates: Requirements 11.7
    #[test]
    fn missing_secret_guidance_carries_only_the_name(secret in "[a-zA-Z0-9]{8,40}") {
        let clock = VirtualClock::new(0);
        let transport = CountingTransport {
            clock: clock.clone(),
            health_delay_ms: 10,
            health_result: Ok(()),
            // Even if fetch would succeed, the missing secret short-circuits it.
            fetch_result: Ok(collected(CollectorPath::Aside)),
            health_calls: Cell::new(0),
        };
        let secrets = MissingSecrets;
        let aside =
            AsideCollector::new(LoopbackEndpoint::new(9000), &transport, &secrets, &clock);
        let mut scope = TempScope::new().unwrap();

        let err = aside
            .collect("https://example.com/post", &mut scope)
            .expect_err("missing secret must be Unauthorized");
        match err {
            CollectError::Unauthorized { item } => {
                // The guidance names the item and never contains the secret.
                prop_assert!(!item.is_empty());
                prop_assert!(!item.contains(secret.as_str()));
                prop_assert!(!err.to_string().contains(secret.as_str()));
            }
            other => prop_assert!(false, "expected Unauthorized, got {other:?}"),
        }
        let _ = scope.finish();
    }

    /// The Aside connection check answers healthy within three seconds exactly
    /// when its delay is within the bound; a slower check is a health timeout.
    /// The single check is issued exactly once (R11.1, R11.2).
    ///
    /// Validates: Requirements 11.1, 11.2
    #[test]
    fn aside_health_check_is_single_and_bounded(delay_ms in 0i64..8_000) {
        let clock = VirtualClock::new(0);
        let transport = CountingTransport {
            clock: clock.clone(),
            health_delay_ms: delay_ms,
            health_result: Ok(()),
            fetch_result: Ok(collected(CollectorPath::Aside)),
            health_calls: Cell::new(0),
        };
        let secrets = OkSecrets;
        let aside =
            AsideCollector::new(LoopbackEndpoint::new(9000), &transport, &secrets, &clock);

        let health = aside.health_check();
        // Exactly one connection-check request was issued.
        prop_assert_eq!(transport.health_calls.get(), 1);

        let bound_ms = (HEALTH_TIMEOUT_SECS as i64) * 1000;
        if delay_ms > bound_ms {
            prop_assert_eq!(health, Err(CollectError::HealthTimeout));
        } else {
            prop_assert!(health.is_ok());
        }
    }
}

// ===========================================================================
// Property 25 — collection/forward deadlines and temp-data deletion.
// ===========================================================================

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// A collection that would exceed the twenty-second bound (connection check
    /// and switch time included) ends as a deadline failure, and the fallback
    /// send never happens past the bound (R11.12). The clock advances past the
    /// bound between the start read and the pre-send deadline check.
    ///
    /// Validates: Requirements 11.12, 6.15
    #[test]
    fn collection_deadline_stops_before_any_fallback(
        behavior in aside_behavior_strategy(),
    ) {
        // Each clock read jumps 21 seconds: the start read is 0, and the very
        // next read — the deadline check — is already past the 20-second bound.
        let step = (COLLECT_DEADLINE_SECS as i64 + 1) * 1_000;
        let clock = AdvancingClock::new(step);
        let aside = aside_for(behavior);
        let fallback = ScriptedPage::new(
            CollectorPath::Fallback,
            Ok(()),
            Ok(collected(CollectorPath::Fallback)),
        );
        let router = RoutingCollector {
            aside: &aside,
            fallback: &fallback,
            clock: &clock,
        };

        let result = router.collect("https://example.com/post");

        if is_terminal(behavior) {
            // A terminal result short-circuits before the deadline check.
            prop_assert!(matches!(result, Err(CollectFailure::Terminal(_))));
        } else {
            // Otherwise the twenty-second bound is enforced before any send.
            prop_assert_eq!(result, Err(CollectFailure::DeadlineExceeded));
        }
        // No fallback collection ever happened past the bound.
        prop_assert_eq!(fallback.collect_calls(), 0);
    }

    /// A collection well within the bound succeeds and reports the Aside path
    /// with no switch — the deadline does not fire when time does not advance.
    ///
    /// Validates: Requirements 11.12
    #[test]
    fn collection_within_bound_succeeds(_seed in 0u8..1) {
        let clock = VirtualClock::new(0);
        let aside = aside_for(AsideBehavior::HealthOk);
        let fallback = ScriptedPage::new(
            CollectorPath::Fallback,
            Ok(()),
            Ok(collected(CollectorPath::Fallback)),
        );
        let router = RoutingCollector {
            aside: &aside,
            fallback: &fallback,
            clock: &clock,
        };
        let collected = router.collect("https://example.com/post").expect("within bound");
        prop_assert_eq!(collected.path, CollectorPath::Aside);
    }

    /// On every termination — a normal finish or an abandonment (drop without
    /// `finish`) — the temporary raw data is deleted and, when finished, the
    /// deletion completion is reported within the five-second bound (R11.8).
    ///
    /// Validates: Requirements 6.8, 11.8
    #[test]
    fn temp_data_deleted_and_reported_on_every_termination(
        contents in prop::collection::vec(any::<u8>(), 0..256),
        finish in any::<bool>(),
    ) {
        // Path captured before the scope ends, so we can assert it is gone.
        let leaked_path;
        {
            let scope = TempScope::new().unwrap();
            let dir = scope.path().expect("fresh scope has a path").to_path_buf();
            // Write some raw working data under the scope.
            let file = dir.join("raw.bin");
            std::fs::write(&file, &contents).unwrap();
            prop_assert!(file.exists());
            leaked_path = dir;

            if finish {
                // A normal termination reports the deletion (R11.8).
                let report = scope.finish();
                prop_assert!(report.deleted);
                prop_assert!(report.within_deadline);
                prop_assert!(report.elapsed_ms <= DELETION_DEADLINE_MS);
            }
            // else: the scope is dropped here (abandonment) — the Drop safety
            // net must still delete the raw data.
        }
        // In both terminations the temporary raw data no longer exists.
        prop_assert!(!leaked_path.exists());
    }
}

// ===========================================================================
// Property 25 (forward angle) — the link-forward thirty-second deadline.
// ===========================================================================

use openkakao_cli::forward::{
    ForwardOutcome, ForwardRequest, ImageLadder, LinkCollector, LinkForwarder, LinkOutcome,
    RoomSendOutcome, RoomSender, FORWARD_DEADLINE_SECS,
};
use openkakao_cli::logging::SqliteHistoryStore;
use openkakao_cli::ports::{AcquireError, ImageAcquirer, ImageAcquisition, ImageBlob, ImageRef};
use openkakao_cli::room_catalog::{CatalogRoom, RoomAutomation, RoomCatalog, RoomError, Toggle};

/// A scripted link collector for the forwarder: always returns a fixed
/// collected result without touching the clock.
struct OkLinkCollector;
impl LinkCollector for OkLinkCollector {
    fn collect(&self, _url: &str) -> Result<Collected, CollectFailure> {
        Ok(collected(CollectorPath::Aside))
    }
}

/// A room sender that records how many sends it was asked to perform, so a
/// deadline test can assert zero sends.
struct CountingSender {
    sends: Cell<usize>,
}
impl RoomSender for CountingSender {
    fn send_forward(&self, _chat_id: i64, _text: &str, _images: &[ImageBlob]) -> RoomSendOutcome {
        self.sends.set(self.sends.get() + 1);
        RoomSendOutcome::Sent {
            images_delivered: 0,
        }
    }
}

/// A delivery ledger that records nothing and reports nothing delivered.
struct NeverDeliveredLedger;
impl openkakao_cli::forward::DeliveryLedger for NeverDeliveredLedger {
    fn is_delivered(&self, _chat_id: i64, _link_key: &str) -> bool {
        false
    }
    fn mark(
        &self,
        _chat_id: i64,
        _link_key: &str,
        _at: i64,
    ) -> Result<(), openkakao_cli::forward::LedgerError> {
        Ok(())
    }
}

/// A minimal [`RoomCatalog`] fake: only [`RoomCatalog::titles`] is exercised by
/// the forwarder's mention resolution; the rest are stubs.
struct FakeCatalog {
    rooms: Vec<(i64, String)>,
}
impl RoomCatalog for FakeCatalog {
    fn list(&self) -> Vec<CatalogRoom> {
        self.rooms
            .iter()
            .map(|(chat_id, title)| CatalogRoom {
                chat_id: *chat_id,
                title: title.clone(),
                enabled: true,
                auto_reply: false,
                geeknews: false,
                link_forward: true,
                telegram_relay: false,
            })
            .collect()
    }
    fn add(&self, _chat_id: i64) -> Result<(), RoomError> {
        Ok(())
    }
    fn remove(&self, _chat_id: i64) -> Result<(), RoomError> {
        Ok(())
    }
    fn set_toggle(&self, _chat_id: i64, _kind: Toggle, _on: bool) -> Result<(), RoomError> {
        Ok(())
    }
    fn title_of(&self, chat_id: i64) -> Option<String> {
        self.rooms
            .iter()
            .find(|(id, _)| *id == chat_id)
            .map(|(_, t)| t.clone())
    }
    fn titles(&self) -> Vec<(i64, String)> {
        self.rooms.clone()
    }
    fn pin(&self, chat_id: i64) -> RoomAutomation {
        RoomAutomation {
            chat_id,
            present: true,
            enabled: true,
            auto_reply: false,
            geeknews: false,
            version: 0,
        }
    }
}

/// An image acquirer that never yields an image (unused in the deadline path,
/// which stops before image acquisition).
struct NoImage(ImageAcquisition);
impl ImageAcquirer for NoImage {
    fn kind(&self) -> ImageAcquisition {
        self.0
    }
    fn acquire(&self, _r: &ImageRef) -> Result<ImageBlob, AcquireError> {
        Err(AcquireError::NotAvailable)
    }
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// A forward whose per-link processing exceeds the thirty-second bound ends
    /// as a deadline overrun with zero sends to any room (R6.12, R6.15). The
    /// clock advances past the bound between the start read and the pre-send
    /// deadline check.
    ///
    /// Validates: Requirements 6.12, 6.15
    #[test]
    fn forward_deadline_sends_zero(room_count in 1usize..4) {
        // Each clock read jumps 31 seconds: start read is 0, and the pre-send
        // deadline check is already past the 30-second bound.
        let step = (FORWARD_DEADLINE_SECS as i64 + 1) * 1_000;
        let clock = AdvancingClock::new(step);

        let rooms: Vec<(i64, String)> = (0..room_count)
            .map(|i| (i as i64 + 1, format!("room-{i}")))
            .collect();
        let catalog = FakeCatalog {
            rooms: rooms.clone(),
        };
        let mentions: Vec<String> = rooms.iter().map(|(_, t)| t.clone()).collect();

        let collector = OkLinkCollector;
        let sender = CountingSender { sends: Cell::new(0) };
        let ledger = NeverDeliveredLedger;
        let journal = SqliteHistoryStore::open_in_memory().expect("journal");

        let s0 = NoImage(ImageAcquisition::OriginalSave);
        let s1 = NoImage(ImageAcquisition::CacheFolder);
        let s2 = NoImage(ImageAcquisition::ScreenCapture);
        let ladder = ImageLadder {
            steps: [&s0, &s1, &s2],
            clock: &clock,
        };

        let forwarder = LinkForwarder {
            collector: &collector,
            sender: &sender,
            ledger: &ledger,
            catalog: &catalog,
            ladder: &ladder,
            journal: &journal,
            clock: &clock,
            screen_recording_granted: true,
        };

        let req = ForwardRequest {
            links: vec!["https://example.com/post".into()],
            room_mentions: mentions,
        };

        match forwarder.forward(&req) {
            ForwardOutcome::Ran { links } => {
                prop_assert_eq!(links.len(), 1);
                prop_assert!(matches!(links[0].outcome, LinkOutcome::DeadlineExceeded));
            }
            other => prop_assert!(false, "expected Ran with a deadline, got {other:?}"),
        }
        // The deadline was enforced before any send (R6.15).
        prop_assert_eq!(sender.sends.get(), 0);
    }
}

/// Keep the imported timeout constant referenced so a future refactor that
/// drops it fails loudly here too.
#[allow(dead_code)]
const _ASIDE_COLLECT_TIMEOUT_REFERENCED: u64 = ASIDE_COLLECT_TIMEOUT_SECS;
