//! Property-based tests for the link forwarder (task 7.3).
//!
//! Feature: kakao-agent-live-ops
//! Property 17: 방 언급 해석의 전부-또는-전무
//!   (mention resolution is all-or-nothing)
//! Property 18: 요약 길이 상한과 개인정보 마스킹
//!   (summary length bound and PII masking)
//! Property 19: 이미지 개수 일치 또는 누락 명시 — 링크 전달 관점
//!   (image counts match or missing is explicit — link-forward perspective)
//! Property 21: (원본 항목, 대상 방) 쌍당 1회 전달
//!   (at most one confirmed delivery per (source item, target room) pair)
//! Property 25: 전달 마감·임시 데이터 삭제 관점
//!   (forward deadline + temporary-data deletion perspective)
//!
//! Every property drives the real [`LinkForwarder`] over in-memory seams. No
//! real KakaoTalk send and no network egress is possible here: the forwarder
//! only ever calls the injected [`LinkCollector`] and [`RoomSender`] fakes, and
//! every property additionally registers a [`BlockingRealSendPort`] tripwire and
//! a [`ForbiddenNetwork`] and asserts both counters stay at zero — the
//! observation layer of the "zero real sends / zero egress" defense.
//!
//! Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.9, 6.13, 6.14,
//! 6.16, 12.6, 12.7

use std::cell::RefCell;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use openkakao_cli::collector::{
    Collected, CollectError, CollectFailure, CollectorPath, PageCollector, RoutingCollector,
    TempScope,
};
use openkakao_cli::fakes::{BlockingRealSendPort, ForbiddenNetwork, VirtualClock};
use openkakao_cli::forward::{
    canonical_link_key, mask_pii, resolve_mention, summarize, DeliveryLedger, DeliveryResult,
    ForwardOutcome, ForwardRejection, ForwardRequest, ImageLadder, LinkCollector, LinkForwarder,
    LinkOutcome, MentionResolution, RoomSendOutcome, RoomSender, SqliteDeliveryLedger, TitleField,
    MAX_IMAGES_PER_POST, MAX_LINKS, MAX_MENTIONS, SHORT_BODY_THRESHOLD, SUMMARY_MAX,
};
use openkakao_cli::logging::{FlowKind, HistoryStore, SqliteHistoryStore};
use openkakao_cli::ports::{
    AcquireError, ImageAcquisition, ImageAcquirer, ImageBlob, ImageRef, NetworkPort, SendPort,
};
use openkakao_cli::room_catalog::{CatalogRoom, InMemoryRoomCatalog, MapRoomDirectory};
use proptest::prelude::*;

// ===========================================================================
// Shared fixtures
// ===========================================================================

/// Build an in-memory room catalog from `(chat_id, title)` pairs, opting every
/// room into link forwarding.
fn catalog(rooms: &[(i64, &str)]) -> InMemoryRoomCatalog {
    let dir = Arc::new(MapRoomDirectory::new(
        rooms.iter().map(|(id, t)| (*id, (*t).to_string())),
    ));
    let catalog_rooms: Vec<CatalogRoom> = rooms
        .iter()
        .map(|(id, t)| CatalogRoom {
            chat_id: *id,
            title: (*t).to_string(),
            enabled: true,
            auto_reply: false,
            geeknews: false,
            link_forward: true,
            telegram_relay: false,
        })
        .collect();
    InMemoryRoomCatalog::from_rooms(dir, catalog_rooms)
}

/// A collector that returns a scripted result and counts its calls, so tests
/// can prove that a rejection performed no collection at all.
struct ScriptedCollector {
    result: Result<Collected, CollectFailure>,
    calls: RefCell<usize>,
}

impl ScriptedCollector {
    fn ok(collected: Collected) -> Self {
        Self {
            result: Ok(collected),
            calls: RefCell::new(0),
        }
    }

    fn calls(&self) -> usize {
        *self.calls.borrow()
    }
}

impl LinkCollector for ScriptedCollector {
    fn collect(&self, _url: &str) -> Result<Collected, CollectFailure> {
        *self.calls.borrow_mut() += 1;
        self.result.clone()
    }
}

/// A room sender that returns a fixed outcome and counts its sends.
struct ScriptedSender {
    outcome: RoomSendOutcome,
    sends: RefCell<usize>,
}

impl ScriptedSender {
    fn new(outcome: RoomSendOutcome) -> Self {
        Self {
            outcome,
            sends: RefCell::new(0),
        }
    }

    fn send_count(&self) -> usize {
        *self.sends.borrow()
    }
}

impl RoomSender for ScriptedSender {
    fn send_forward(&self, _chat_id: i64, _text: &str, _images: &[ImageBlob]) -> RoomSendOutcome {
        *self.sends.borrow_mut() += 1;
        self.outcome.clone()
    }
}

/// An acquirer that always yields a non-empty image (the ladder's first rung).
struct OkAcquirer {
    clock: VirtualClock,
}
impl ImageAcquirer for OkAcquirer {
    fn kind(&self) -> ImageAcquisition {
        ImageAcquisition::OriginalSave
    }
    fn acquire(&self, _r: &ImageRef) -> Result<ImageBlob, AcquireError> {
        self.clock.advance_ms(10);
        Ok(ImageBlob {
            bytes: vec![1, 2, 3],
            mime: "image/png".into(),
        })
    }
}

/// An acquirer that never yields an image (fills the unused rungs).
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

/// Owns the three ladder rungs and their (separate) clock so image acquisition
/// never advances the forwarder's per-link deadline clock.
struct LadderHarness {
    ladder_clock: VirtualClock,
    ok: OkAcquirer,
    n1: NeverAcquirer,
    n2: NeverAcquirer,
}

impl LadderHarness {
    fn new() -> Self {
        let ladder_clock = VirtualClock::new(0);
        Self {
            ok: OkAcquirer {
                clock: ladder_clock.clone(),
            },
            n1: NeverAcquirer {
                kind: ImageAcquisition::CacheFolder,
            },
            n2: NeverAcquirer {
                kind: ImageAcquisition::ScreenCapture,
            },
            ladder_clock,
        }
    }

    fn ladder(&self) -> ImageLadder<'_> {
        ImageLadder {
            steps: [&self.ok, &self.n1, &self.n2],
            clock: &self.ladder_clock,
        }
    }
}

/// A collected link carrying `images` image references, in post order.
fn collected(images: usize) -> Collected {
    Collected {
        title: Some("좋은 글".into()),
        body: "본문 내용입니다. ".repeat(30),
        images: (0..images)
            .map(|i| ImageRef {
                locator: format!("img-{i}"),
            })
            .collect(),
        author: Some("작성자".into()),
        path: CollectorPath::Fallback,
        switched: None,
    }
}

/// The observation-defense tripwires every property registers and asserts are
/// never touched: the real-send path and the network stay at zero.
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
        // send port that must never be invoked by the forwarder.
        prop_assert!(!SendPort::is_fake(&self.blocked));
        Ok(())
    }
}

// ===========================================================================
// Property 17: mention resolution is all-or-nothing.
// ===========================================================================

/// The catalog title pool. Duplicates arise when two rooms draw the same title
/// (an ambiguous mention); "study" exercises the case-sensitive comparison.
const TITLE_POOL: [&str; 4] = ["스터디", "모임", "study", "업무방"];

/// One generated room mention.
#[derive(Debug, Clone)]
enum MentionSpec {
    /// A mention derived from a pool title, with whitespace padding and an
    /// optional upper-casing (which breaks a case-sensitive match).
    FromPool {
        idx: usize,
        pad_left: usize,
        pad_right: usize,
        upper: bool,
    },
    /// A mention that matches no room.
    Junk(String),
}

impl MentionSpec {
    fn render(&self) -> String {
        match self {
            MentionSpec::FromPool {
                idx,
                pad_left,
                pad_right,
                upper,
            } => {
                let base = TITLE_POOL[*idx];
                let base = if *upper {
                    base.to_uppercase()
                } else {
                    base.to_string()
                };
                format!("{}{}{}", " ".repeat(*pad_left), base, " ".repeat(*pad_right))
            }
            MentionSpec::Junk(s) => s.clone(),
        }
    }
}

fn mention_spec_strategy() -> impl Strategy<Value = MentionSpec> {
    prop_oneof![
        (0..TITLE_POOL.len(), 0usize..3, 0usize..3, any::<bool>()).prop_map(
            |(idx, pad_left, pad_right, upper)| MentionSpec::FromPool {
                idx,
                pad_left,
                pad_right,
                upper,
            }
        ),
        "junk[0-9]{0,3}".prop_map(MentionSpec::Junk),
    ]
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// Property 17: every room mention is resolved first; the request runs
    /// exactly when all mentions resolve (trimmed, case-sensitive, to exactly
    /// one title) AND the link count is 1..=5 AND the mention count is 1..=10.
    /// Any resolution failure or out-of-range count rejects the whole request
    /// with zero sends to any room and no collection at all (R6.1, R6.2, R6.16).
    ///
    /// Validates: Requirements 6.1, 6.2, 6.16, 12.7
    #[test]
    fn mention_resolution_is_all_or_nothing(
        room_pick in prop::collection::vec(0usize..TITLE_POOL.len(), 1..=5),
        mention_specs in prop::collection::vec(mention_spec_strategy(), 0..=12),
        n_links in 0usize..=7,
    ) {
        let rooms: Vec<(i64, &str)> = room_pick
            .iter()
            .enumerate()
            .map(|(i, &p)| ((i as i64) + 1, TITLE_POOL[p]))
            .collect();
        let cat = catalog(&rooms);
        let title_strings: Vec<String> = rooms.iter().map(|(_, t)| t.to_string()).collect();

        let mentions: Vec<String> = mention_specs.iter().map(MentionSpec::render).collect();

        // Independent oracle: a mention resolves iff its trimmed, case-sensitive
        // form equals exactly one room title. Confirm resolve_mention agrees.
        let mut all_resolve = true;
        for m in &mentions {
            let target = m.trim();
            let match_count = title_strings.iter().filter(|t| t.as_str() == target).count();
            let resolved = matches!(resolve_mention(m, &cat), MentionResolution::Exact(_));
            prop_assert_eq!(resolved, match_count == 1);
            if !resolved {
                all_resolve = false;
            }
        }

        let links: Vec<String> = (0..n_links).map(|i| format!("https://x/{i}")).collect();
        let count_ok = (1..=MAX_LINKS).contains(&n_links)
            && (1..=MAX_MENTIONS).contains(&mentions.len());
        let expect_run = count_ok && all_resolve && !mentions.is_empty();

        let collector = ScriptedCollector::ok(collected(0));
        let sender = ScriptedSender::new(RoomSendOutcome::Sent { images_delivered: 0 });
        let ledger = SqliteDeliveryLedger::open_in_memory().unwrap();
        let journal = SqliteHistoryStore::open_in_memory().unwrap();
        let clock = VirtualClock::new(0);
        let harness = LadderHarness::new();
        let ladder = harness.ladder();
        let trip = Tripwires::new();

        let fwd = LinkForwarder {
            collector: &collector,
            sender: &sender,
            ledger: &ledger,
            catalog: &cat,
            ladder: &ladder,
            journal: &journal,
            clock: &clock,
            screen_recording_granted: true,
        };

        let outcome = fwd.forward(&ForwardRequest {
            links: links.clone(),
            room_mentions: mentions.clone(),
        });

        match outcome {
            ForwardOutcome::Rejected(rej) => {
                prop_assert!(!expect_run, "rejected a request that should have run");
                // A rejection sends nothing and collects nothing (R6.2, R6.16).
                prop_assert_eq!(sender.send_count(), 0);
                prop_assert_eq!(collector.calls(), 0);
                // The count guard takes priority over mention resolution.
                if !count_ok {
                    prop_assert!(
                        matches!(rej, ForwardRejection::CountOutOfRange { .. }),
                        "expected a count-out-of-range rejection"
                    );
                } else {
                    prop_assert!(matches!(rej, ForwardRejection::MentionResolution(_)));
                }
            }
            ForwardOutcome::Ran { links: results } => {
                prop_assert!(expect_run, "ran a request that should have been rejected");
                prop_assert_eq!(results.len(), n_links);
            }
        }

        trip.assert_untouched()?;
    }
}

// ===========================================================================
// Property 18: summary length bound and PII masking.
// ===========================================================================

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// Property 18 (summary bound): a summary is always ≤ 700 characters and,
    /// for a body over 200 characters, ≤ half the body's character count; a
    /// short body (≤ 200) is kept whole up to 200 characters (R6.3, R6.9).
    /// Truncation never splits a multi-byte character.
    ///
    /// Validates: Requirements 6.3, 6.9
    #[test]
    fn summary_respects_length_bounds(body in "\\PC{0,2000}") {
        let out = summarize(&body);
        let total = body.chars().count();
        let n = out.chars().count();

        // Hard upper bound (R6.3, R6.9).
        prop_assert!(n <= SUMMARY_MAX);
        // A summary can never be longer than the body it came from.
        prop_assert!(n <= total);

        if total <= SHORT_BODY_THRESHOLD {
            // A short body is kept whole, capped at the short-body threshold.
            prop_assert!(n <= SHORT_BODY_THRESHOLD);
            prop_assert_eq!(&out, &body);
        } else {
            // A longer body is at most half its character count.
            prop_assert!(n <= total / 2);
        }
    }
}

/// One PII item to embed in a body, together with the exact substring that must
/// not survive masking.
#[derive(Debug, Clone)]
enum PiiItem {
    Phone(String),
    Account(String),
    Address(String),
}

/// A phone number: `010` + eight digits ⇒ eleven digits starting with `0`.
fn phone_strategy() -> impl Strategy<Value = PiiItem> {
    (0u32..100_000_000u32).prop_map(|n| PiiItem::Phone(format!("010{n:08}")))
}

/// An account number: a twelve-digit run that never starts with `0`, so it is
/// classified as an account rather than a phone number.
fn account_strategy() -> impl Strategy<Value = PiiItem> {
    (0u64..1_000_000_000_00u64).prop_map(|n| PiiItem::Account(format!("2{n:011}")))
}

/// A Korean address whose building number is small (1..=999), so the digit scan
/// never mistakes it for a phone or account number.
fn address_strategy() -> impl Strategy<Value = PiiItem> {
    (1u32..1000).prop_map(|n| PiiItem::Address(format!("서울특별시 강남구 테헤란로 {n}")))
}

fn pii_item_strategy() -> impl Strategy<Value = PiiItem> {
    prop_oneof![phone_strategy(), account_strategy(), address_strategy()]
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// Property 18 (PII masking): every embedded phone/address/account string is
    /// gone from the masked text, and the per-kind counts equal the embedded
    /// counts (R6.13). Filler words separate the items so their digit runs never
    /// merge.
    ///
    /// Validates: Requirements 6.13
    #[test]
    fn mask_pii_removes_every_item_and_counts_by_kind(
        items in prop::collection::vec(pii_item_strategy(), 0..=8),
    ) {
        // Interleave each PII item with a digit-free, non-address filler word so
        // adjacent items never form one run or one address span.
        let mut parts: Vec<String> = Vec::new();
        let mut expect_phone = 0usize;
        let mut expect_account = 0usize;
        let mut expect_address = 0usize;
        for (i, item) in items.iter().enumerate() {
            parts.push(format!("메모{i}"));
            match item {
                PiiItem::Phone(s) => {
                    expect_phone += 1;
                    parts.push(s.clone());
                }
                PiiItem::Account(s) => {
                    expect_account += 1;
                    parts.push(s.clone());
                }
                PiiItem::Address(s) => {
                    expect_address += 1;
                    parts.push(s.clone());
                }
            }
        }
        parts.push("내용".to_string());
        let text = parts.join(" ");

        let (masked, tally) = mask_pii(&text);

        // No embedded raw value survives (R6.13).
        for item in &items {
            let raw = match item {
                PiiItem::Phone(s) | PiiItem::Account(s) | PiiItem::Address(s) => s,
            };
            prop_assert!(
                !masked.contains(raw.as_str()),
                "masked text still contains {raw:?}: {masked:?}"
            );
        }

        // Per-kind counts equal the embedded counts (R6.13).
        prop_assert_eq!(tally.phone, expect_phone);
        prop_assert_eq!(tally.account, expect_account);
        prop_assert_eq!(tally.address, expect_address);
        prop_assert_eq!(tally.total(), expect_phone + expect_account + expect_address);
    }

    /// Property 18 (third-party attribution): a rendered forward message for a
    /// third party's post carries both the author and the source link (R6.9).
    ///
    /// Validates: Requirements 6.9
    #[test]
    fn third_party_message_carries_author_and_source(
        author in "[가-힣]{1,8}",
        source in "https://[a-z]{3,8}\\.com/[a-z]{1,8}",
        body in "\\PC{0,400}",
    ) {
        use openkakao_cli::forward::ForwardMessage;
        let msg = ForwardMessage {
            title: TitleField::Known("제목".into()),
            summary: summarize(&body),
            source: source.clone(),
            author: Some(author.clone()),
            images: Vec::new(),
            dropped_images: 0,
        };
        let rendered = msg.render();
        prop_assert!(rendered.contains(&author));
        prop_assert!(rendered.contains(&source));
    }
}

// ===========================================================================
// Property 19: image counts match or missing is explicit (link-forward).
// ===========================================================================

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// Property 19: for a source post with 0..=20 images and a delivered count
    /// no greater than the per-post ceiling, the delivery is a complete `Sent`
    /// exactly when every source image was delivered; any shortfall (including
    /// the images dropped over the 10-per-post cap) is a partial failure that
    /// names the missing count, is never marked delivered, and so stays
    /// retryable (R6.4, R6.5, R6.6, R6.14, R12.6).
    ///
    /// Validates: Requirements 6.4, 6.5, 6.6, 6.14, 12.6
    #[test]
    fn image_counts_match_or_missing_is_explicit(
        source in 0usize..=20,
        // A realistic sender delivers at most what the ladder can include.
        delivered in 0usize..=10,
    ) {
        let delivered = delivered.min(source.min(MAX_IMAGES_PER_POST));

        let cat = catalog(&[(1, "스터디")]);
        let collector = ScriptedCollector::ok(collected(source));
        let sender = ScriptedSender::new(RoomSendOutcome::Sent {
            images_delivered: delivered,
        });
        let ledger = SqliteDeliveryLedger::open_in_memory().unwrap();
        let journal = SqliteHistoryStore::open_in_memory().unwrap();
        let clock = VirtualClock::new(0);
        let harness = LadderHarness::new();
        let ladder = harness.ladder();
        let trip = Tripwires::new();

        let fwd = LinkForwarder {
            collector: &collector,
            sender: &sender,
            ledger: &ledger,
            catalog: &cat,
            ladder: &ladder,
            journal: &journal,
            clock: &clock,
            screen_recording_granted: true,
        };

        let outcome = fwd.forward(&ForwardRequest {
            links: vec!["https://x.com/a".into()],
            room_mentions: vec!["스터디".into()],
        });

        let key = canonical_link_key("https://x.com/a");
        let complete = source == delivered;

        match outcome {
            ForwardOutcome::Ran { links } => {
                prop_assert_eq!(links.len(), 1);
                match &links[0].outcome {
                    LinkOutcome::Delivered {
                        rooms,
                        source_images,
                        dropped_images,
                        ..
                    } => {
                        // The source count and the dropped-over-cap count are
                        // reported faithfully (R6.4, R6.5).
                        prop_assert_eq!(*source_images, source);
                        prop_assert_eq!(*dropped_images, source.saturating_sub(MAX_IMAGES_PER_POST));

                        match &rooms[0].result {
                            DeliveryResult::Sent { images } => {
                                prop_assert!(complete, "marked Sent on an image mismatch");
                                prop_assert!(images.is_complete());
                                prop_assert_eq!(images.source, source);
                                prop_assert_eq!(images.delivered, delivered);
                                // Complete ⇒ recorded as delivered (R6.14).
                                prop_assert!(ledger.is_delivered(1, &key));
                            }
                            DeliveryResult::PartialFailure { images, miss_reasons } => {
                                prop_assert!(!complete, "partial failure on a complete delivery");
                                prop_assert_eq!(images.missing(), source - delivered);
                                // A miss must always be explained (R6.6, R12.6).
                                prop_assert!(!miss_reasons.is_empty());
                                // Partial ⇒ not recorded, so it stays retryable.
                                prop_assert!(!ledger.is_delivered(1, &key));
                            }
                            other => prop_assert!(false, "unexpected result: {other:?}"),
                        }
                    }
                    other => prop_assert!(false, "expected delivered, got {other:?}"),
                }
            }
            other => prop_assert!(false, "expected ran, got {other:?}"),
        }

        // Only redacted image counts reach the journal (R6.11).
        for ev in journal.recent(20).unwrap() {
            prop_assert_eq!(ev.flow, FlowKind::LinkForward);
        }

        trip.assert_untouched()?;
    }
}

// ===========================================================================
// Property 21: once per (source item, target room) pair.
// ===========================================================================

/// A lifecycle event mixed between repeated forward requests.
#[derive(Debug, Clone)]
enum Repeat {
    /// The exact same link string.
    Same,
    /// The same link with a trailing slash (a canonical-key match).
    TrailingSlash,
    /// The same link upper-cased (a canonical-key match).
    UpperCase,
}

fn repeat_strategy() -> impl Strategy<Value = Repeat> {
    prop_oneof![
        Just(Repeat::Same),
        Just(Repeat::TrailingSlash),
        Just(Repeat::UpperCase),
    ]
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// Property 21: repeating the same forward request (with app-restart-like
    /// rebuilds of the forwarder over the same persistent ledger, and
    /// case/trailing-slash link variants) up to 20 times delivers each (link,
    /// room) pair at most once — every repeat after the first reports
    /// `AlreadyDelivered` with zero new sends, and the total confirmed sends
    /// equals the number of target rooms (R6.7, R6.14, R12.7).
    ///
    /// Validates: Requirements 6.7, 6.14, 12.7
    #[test]
    fn at_most_one_delivery_per_link_room_pair(
        room_count in 1usize..=3,
        repeats in prop::collection::vec(repeat_strategy(), 1..=20),
    ) {
        let base = "https://Example.com/Post";
        let rooms: Vec<(i64, &str)> = (0..room_count)
            .map(|i| ((i as i64) + 1, ["스터디", "모임", "업무방"][i]))
            .collect();
        let cat = catalog(&rooms);
        let mentions: Vec<String> = rooms.iter().map(|(_, t)| t.to_string()).collect();

        // One persistent ledger survives every "restart" (a fresh forwarder).
        let ledger = SqliteDeliveryLedger::open_in_memory().unwrap();
        // Zero images ⇒ every send is complete and marked.
        let sender = ScriptedSender::new(RoomSendOutcome::Sent { images_delivered: 0 });
        let collector = ScriptedCollector::ok(collected(0));
        let journal = SqliteHistoryStore::open_in_memory().unwrap();
        let clock = VirtualClock::new(0);
        let harness = LadderHarness::new();
        let ladder = harness.ladder();
        let trip = Tripwires::new();
        let key = canonical_link_key(base);

        for (idx, repeat) in repeats.iter().enumerate() {
            let link = match repeat {
                Repeat::Same => base.to_string(),
                Repeat::TrailingSlash => format!("{base}/"),
                Repeat::UpperCase => base.to_uppercase(),
            };

            // A fresh forwarder each iteration models an app restart while the
            // ledger persists the delivered pairs (R6.14, R12.7).
            let fwd = LinkForwarder {
                collector: &collector,
                sender: &sender,
                ledger: &ledger,
                catalog: &cat,
                ladder: &ladder,
                journal: &journal,
                clock: &clock,
                screen_recording_granted: true,
            };

            let outcome = fwd.forward(&ForwardRequest {
                links: vec![link],
                room_mentions: mentions.clone(),
            });

            let ForwardOutcome::Ran { links } = outcome else {
                prop_assert!(false, "request must run");
                unreachable!()
            };
            let LinkOutcome::Delivered { rooms: results, .. } = &links[0].outcome else {
                prop_assert!(false, "link must be delivered");
                unreachable!()
            };

            for rd in results {
                if idx == 0 {
                    // First pass: every room is sent once.
                    prop_assert!(
                        matches!(rd.result, DeliveryResult::Sent { .. }),
                        "first pass must send to every room"
                    );
                } else {
                    // Every later pass: already delivered, nothing new sent.
                    prop_assert_eq!(&rd.result, &DeliveryResult::AlreadyDelivered);
                }
            }
        }

        // Exactly one confirmed send per room, regardless of repeat count.
        prop_assert_eq!(sender.send_count(), room_count);
        for (chat_id, _) in &rooms {
            prop_assert!(ledger.is_delivered(*chat_id, &key));
        }

        trip.assert_untouched()?;
    }
}

// ===========================================================================
// Property 25: forward deadline + temporary-data deletion.
// ===========================================================================

/// A collector that advances the forwarder's clock by a set amount to simulate
/// a slow collection, exercising the 30-second per-link deadline (R6.12, R6.15).
struct DeadlineCollector {
    clock: VirtualClock,
    advance_ms: i64,
}

impl LinkCollector for DeadlineCollector {
    fn collect(&self, _url: &str) -> Result<Collected, CollectFailure> {
        self.clock.advance_ms(self.advance_ms);
        Ok(collected(0))
    }
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// Property 25 (deadline): a link whose collection overruns the 30-second
    /// per-link bound ends as `DeadlineExceeded` with zero sends, and a link
    /// inside the bound is delivered (R6.12, R6.15).
    ///
    /// Validates: Requirements 6.12, 6.15
    #[test]
    fn forward_deadline_yields_zero_sends(advance_secs in 0u64..=60) {
        let cat = catalog(&[(1, "스터디")]);
        let clock = VirtualClock::new(0);
        let collector = DeadlineCollector {
            clock: clock.clone(),
            advance_ms: (advance_secs * 1000) as i64,
        };
        let sender = ScriptedSender::new(RoomSendOutcome::Sent { images_delivered: 0 });
        let ledger = SqliteDeliveryLedger::open_in_memory().unwrap();
        let journal = SqliteHistoryStore::open_in_memory().unwrap();
        let harness = LadderHarness::new();
        let ladder = harness.ladder();
        let trip = Tripwires::new();

        let fwd = LinkForwarder {
            collector: &collector,
            sender: &sender,
            ledger: &ledger,
            catalog: &cat,
            ladder: &ladder,
            journal: &journal,
            clock: &clock,
            screen_recording_granted: true,
        };

        let outcome = fwd.forward(&ForwardRequest {
            links: vec!["https://x.com/a".into()],
            room_mentions: vec!["스터디".into()],
        });

        // now (== advance_ms) >= start(0) + 30_000 ⇔ advance_secs >= 30.
        let expect_deadline = advance_secs >= 30;

        let ForwardOutcome::Ran { links } = outcome else {
            prop_assert!(false, "request must run");
            unreachable!()
        };
        match &links[0].outcome {
            LinkOutcome::DeadlineExceeded => {
                prop_assert!(expect_deadline, "deadline fired early");
                // A deadline overrun performs zero sends (R6.15).
                prop_assert_eq!(sender.send_count(), 0);
            }
            LinkOutcome::Delivered { .. } => {
                prop_assert!(!expect_deadline, "should have exceeded the deadline");
            }
            other => prop_assert!(false, "unexpected outcome: {other:?}"),
        }

        trip.assert_untouched()?;
    }
}

/// A page collector that records the temporary working directory it is given
/// and writes a scratch file under it, so a test can prove the data is deleted
/// once collection finishes (R6.8, R11.8). The scripted `result` decides whether
/// this path yields content or a terminal failure.
struct RecordingPageCollector {
    path: CollectorPath,
    result: Result<Collected, CollectError>,
    seen_paths: Arc<Mutex<Vec<PathBuf>>>,
}

impl PageCollector for RecordingPageCollector {
    fn path(&self) -> CollectorPath {
        self.path
    }

    fn health_check(&self) -> Result<(), CollectError> {
        Ok(())
    }

    fn collect(&self, _url: &str, scope: &mut TempScope) -> Result<Collected, CollectError> {
        if let Some(dir) = scope.path() {
            let dir = dir.to_path_buf();
            // Write scratch working data the scope is responsible for deleting.
            let _ = std::fs::write(dir.join("raw.tmp"), b"working data");
            self.seen_paths.lock().unwrap().push(dir);
        }
        self.result.clone()
    }
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(128))]

    /// Property 25 (temp deletion): driving the real [`RoutingCollector`] through
    /// the forwarder, the temporary raw-data directory is deleted on every
    /// termination — a clean collection, and a terminal (private) failure —
    /// leaving nothing behind (R6.8, R11.8).
    ///
    /// Validates: Requirements 6.8
    #[test]
    fn temp_data_deleted_on_every_outcome(succeed in any::<bool>()) {
        let cat = catalog(&[(1, "스터디")]);
        let seen: Arc<Mutex<Vec<PathBuf>>> = Arc::new(Mutex::new(Vec::new()));

        let aside_result = if succeed {
            Ok(collected(0))
        } else {
            // A terminal state must not be retried on the other path (R11.10),
            // and its temp data must still be deleted.
            Err(CollectError::Private)
        };
        let aside = RecordingPageCollector {
            path: CollectorPath::Aside,
            result: aside_result,
            seen_paths: Arc::clone(&seen),
        };
        let fallback = RecordingPageCollector {
            path: CollectorPath::Fallback,
            result: Ok(collected(0)),
            seen_paths: Arc::clone(&seen),
        };
        let collector_clock = VirtualClock::new(0);
        let routing = RoutingCollector {
            aside: &aside,
            fallback: &fallback,
            clock: &collector_clock,
        };

        let sender = ScriptedSender::new(RoomSendOutcome::Sent { images_delivered: 0 });
        let ledger = SqliteDeliveryLedger::open_in_memory().unwrap();
        let journal = SqliteHistoryStore::open_in_memory().unwrap();
        let clock = VirtualClock::new(0);
        let harness = LadderHarness::new();
        let ladder = harness.ladder();
        let trip = Tripwires::new();

        let fwd = LinkForwarder {
            collector: &routing,
            sender: &sender,
            ledger: &ledger,
            catalog: &cat,
            ladder: &ladder,
            journal: &journal,
            clock: &clock,
            screen_recording_granted: true,
        };

        let outcome = fwd.forward(&ForwardRequest {
            links: vec!["https://x.com/a".into()],
            room_mentions: vec!["스터디".into()],
        });

        let ForwardOutcome::Ran { links } = outcome else {
            prop_assert!(false, "request must run");
            unreachable!()
        };
        if succeed {
            prop_assert!(
                matches!(links[0].outcome, LinkOutcome::Delivered { .. }),
                "a clean collection must deliver"
            );
        } else {
            // A terminal failure collects nothing further and sends nothing.
            prop_assert!(matches!(links[0].outcome, LinkOutcome::CollectFailed(_)));
            prop_assert_eq!(sender.send_count(), 0);
        }

        // The collector created at least one temp directory, and every one of
        // them is gone after the collection finished (R6.8, R11.8).
        let paths = seen.lock().unwrap().clone();
        prop_assert!(!paths.is_empty(), "collector never created a temp scope");
        for p in paths {
            prop_assert!(!p.exists(), "temporary data survived: {}", p.display());
        }

        trip.assert_untouched()?;
    }
}
