//! Unit tests for the GeekNews posting rule enforcement (R11.7).

use std::cell::RefCell;

use super::*;

/// A fake feed that returns canned XML with no network.
struct FakeFeed {
    xml: String,
}

impl FeedSource for FakeFeed {
    fn fetch_xml(&self) -> Result<String, FeedError> {
        Ok(self.xml.clone())
    }
}

/// A fake sender that records what it was asked to send and can be forced to
/// fail, so we never touch a real Kakao send.
struct FakeSender {
    fail: bool,
    sent: RefCell<Vec<(i64, String)>>,
}

impl FakeSender {
    fn confirming() -> Self {
        Self {
            fail: false,
            sent: RefCell::new(Vec::new()),
        }
    }

    fn failing() -> Self {
        Self {
            fail: true,
            sent: RefCell::new(Vec::new()),
        }
    }
}

impl Sender for FakeSender {
    fn send(&self, chat_id: i64, message: &str) -> Result<(), SendError> {
        if self.fail {
            return Err(SendError("send refused by fake".to_string()));
        }
        self.sent.borrow_mut().push((chat_id, message.to_string()));
        Ok(())
    }
}

/// An in-memory cursor store that counts writes so a test can assert the cursor
/// was (or was not) persisted.
struct FakeStore {
    cursor: RefCell<GeekNewsCursor>,
    writes: RefCell<usize>,
}

impl FakeStore {
    fn new(cursor: GeekNewsCursor) -> Self {
        Self {
            cursor: RefCell::new(cursor),
            writes: RefCell::new(0),
        }
    }

    fn snapshot(&self) -> GeekNewsCursor {
        self.cursor.borrow().clone()
    }

    fn write_count(&self) -> usize {
        *self.writes.borrow()
    }
}

impl CursorStore for FakeStore {
    fn load(&self) -> Result<GeekNewsCursor, StoreError> {
        Ok(self.cursor.borrow().clone())
    }

    fn store(&self, cursor: &GeekNewsCursor) -> Result<(), StoreError> {
        *self.cursor.borrow_mut() = cursor.clone();
        *self.writes.borrow_mut() += 1;
        Ok(())
    }
}

fn entry(id: u32, title: &str) -> String {
    format!(
        "<entry><title>{title}</title><link rel='alternate' href='https://news.hada.io/topic?id={id}'/><content>summary for {id}</content></entry>"
    )
}

fn feed_xml(ids_titles: &[(u32, &str)]) -> String {
    let entries: String = ids_titles
        .iter()
        .map(|(id, title)| entry(*id, title))
        .collect();
    format!("<feed>{entries}</feed>")
}

/// A unix timestamp guaranteed to sit inside the first slot of the KST day that
/// contains `base`.
fn instant_in_open_slot(base: i64) -> i64 {
    let windows = day_slot_windows(base);
    assert!(!windows.is_empty(), "expected slot windows for the KST day");
    windows[0].start
}

// A fixed instant: 2026-08-20 around midday UTC — a stable reference day.
const BASE: i64 = 1_787_000_000;

#[test]
fn format_preserves_top5_layout() {
    // A blank line after the header and a blank line between each numbered item.
    let items = vec![
        FeedItem {
            id: 1,
            title: "First".to_string(),
            url: "https://news.hada.io/topic?id=1".to_string(),
            summary: String::new(),
        },
        FeedItem {
            id: 2,
            title: "Second".to_string(),
            url: "https://news.hada.io/topic?id=2".to_string(),
            summary: String::new(),
        },
    ];
    let now = instant_in_open_slot(BASE);
    let message = format_top5(&items, now);

    assert!(message.starts_with("GeekNews TOP5 · "));
    let (header, body) = message.split_once("\n\n").expect("blank line after header");
    assert!(header.starts_with("GeekNews TOP5 · "));
    // Items are separated by a blank line and numbered from 1.
    assert_eq!(
        body,
        "1. First https://news.hada.io/topic?id=1\n\n2. Second https://news.hada.io/topic?id=2"
    );
}

#[test]
fn format_caps_at_five_items() {
    let items: Vec<FeedItem> = (1..=8)
        .map(|id| FeedItem {
            id,
            title: format!("T{id}"),
            url: format!("https://news.hada.io/topic?id={id}"),
            summary: String::new(),
        })
        .collect();
    let message = format_top5(&items, instant_in_open_slot(BASE));
    // Exactly five numbered items -> five "N. " prefixes, no "6.".
    assert!(message.contains("5. T5"));
    assert!(!message.contains("6. T6"));
}

#[test]
fn parse_keeps_only_canonical_topic_entries() {
    let xml = format!(
        "<feed>{}{}{}</feed>",
        entry(101, "Valid"),
        // Non-topic link is dropped.
        "<entry><title>Ad</title><link href='https://example.com/x'/></entry>",
        // Empty title is dropped.
        "<entry><title></title><link href='https://news.hada.io/topic?id=102'/></entry>",
    );
    let items = parse_geeknews_entries(&xml);
    assert_eq!(items.len(), 1);
    assert_eq!(items[0].id, 101);
    assert_eq!(items[0].title, "Valid");
    assert_eq!(items[0].url, "https://news.hada.io/topic?id=101");
}

#[test]
fn slot_at_is_none_outside_windows() {
    // Pick an instant far from any slot: 3 minutes before the first window opens.
    let windows = day_slot_windows(BASE);
    let before_first = windows[0].start - 3 * 60;
    assert!(slot_at(before_first).is_none());
    // And inside the first window it resolves.
    assert!(slot_at(windows[0].start).is_some());
}

#[test]
fn post_outside_slot_does_not_send_or_persist() {
    let windows = day_slot_windows(BASE);
    let outside = windows[0].start - 5 * 60;
    let feed = FakeFeed {
        xml: feed_xml(&[(1, "a"), (2, "b")]),
    };
    let sender = FakeSender::confirming();
    let store = FakeStore::new(GeekNewsCursor::default());

    let outcome = run_geeknews_post(42, outside, &feed, &sender, &store).expect("ok");
    assert_eq!(outcome, PostOutcome::NoOpenSlot);
    assert!(sender.sent.borrow().is_empty());
    assert_eq!(store.write_count(), 0);
}

#[test]
fn confirmed_send_persists_seen_and_slot() {
    let now = instant_in_open_slot(BASE);
    let feed = FakeFeed {
        xml: feed_xml(&[(10, "a"), (11, "b"), (12, "c")]),
    };
    let sender = FakeSender::confirming();
    let store = FakeStore::new(GeekNewsCursor::default());

    let outcome = run_geeknews_post(42, now, &feed, &sender, &store).expect("ok");

    let (slot, ids) = match outcome {
        PostOutcome::Posted { slot, ids } => (slot, ids),
        other => panic!("expected Posted, got {other:?}"),
    };
    assert_eq!(ids, vec![10, 11, 12]);
    assert_eq!(store.write_count(), 1);

    let after = store.snapshot();
    assert!(after.has_seen(10) && after.has_seen(11) && after.has_seen(12));
    assert!(after.has_posted_slot(&slot));
    assert_eq!(after.newest_id, 12);
    assert_eq!(after.updated_at, now);
    // Exactly one send happened.
    assert_eq!(sender.sent.borrow().len(), 1);
}

#[test]
fn failed_send_leaves_cursor_unchanged() {
    let now = instant_in_open_slot(BASE);
    let feed = FakeFeed {
        xml: feed_xml(&[(10, "a"), (11, "b")]),
    };
    let sender = FakeSender::failing();
    let before = GeekNewsCursor::default();
    let store = FakeStore::new(before.clone());

    let err = run_geeknews_post(42, now, &feed, &sender, &store).expect_err("send fails");
    assert!(matches!(err, GeekNewsError::Send(_)));

    // R11.7 / Correctness Property 6: nothing persisted, cursor untouched.
    assert_eq!(store.write_count(), 0);
    assert_eq!(store.snapshot(), before);
}

#[test]
fn already_posted_slot_is_not_reposted() {
    let now = instant_in_open_slot(BASE);
    let (day, name) = slot_at(now).expect("open slot");
    let marker = format!("{day}:{name}");
    let mut cursor = GeekNewsCursor::default();
    cursor.posted_slots.push(marker);

    let feed = FakeFeed {
        xml: feed_xml(&[(10, "a")]),
    };
    let sender = FakeSender::confirming();
    let store = FakeStore::new(cursor);

    let outcome = run_geeknews_post(42, now, &feed, &sender, &store).expect("ok");
    assert_eq!(outcome, PostOutcome::NoOpenSlot);
    assert!(sender.sent.borrow().is_empty());
    assert_eq!(store.write_count(), 0);
}

#[test]
fn only_fresh_items_are_posted() {
    let now = instant_in_open_slot(BASE);
    let cursor = GeekNewsCursor {
        seen_ids: vec![10, 11], // already seen
        ..Default::default()
    };
    let feed = FakeFeed {
        xml: feed_xml(&[(10, "old"), (11, "old"), (12, "new")]),
    };
    let sender = FakeSender::confirming();
    let store = FakeStore::new(cursor);

    let outcome = run_geeknews_post(42, now, &feed, &sender, &store).expect("ok");
    match outcome {
        PostOutcome::Posted { ids, .. } => assert_eq!(ids, vec![12]),
        other => panic!("expected Posted, got {other:?}"),
    }
}

#[test]
fn no_fresh_items_does_not_send() {
    let now = instant_in_open_slot(BASE);
    let cursor = GeekNewsCursor {
        seen_ids: vec![10, 11],
        ..Default::default()
    };
    let feed = FakeFeed {
        xml: feed_xml(&[(10, "a"), (11, "b")]),
    };
    let sender = FakeSender::confirming();
    let store = FakeStore::new(cursor);

    let outcome = run_geeknews_post(42, now, &feed, &sender, &store).expect("ok");
    assert_eq!(outcome, PostOutcome::NoFreshItems);
    assert!(sender.sent.borrow().is_empty());
    assert_eq!(store.write_count(), 0);
}

#[test]
fn empty_feed_does_not_send() {
    let now = instant_in_open_slot(BASE);
    let feed = FakeFeed {
        xml: "<feed></feed>".to_string(),
    };
    let sender = FakeSender::confirming();
    let store = FakeStore::new(GeekNewsCursor::default());

    let outcome = run_geeknews_post(42, now, &feed, &sender, &store).expect("ok");
    assert_eq!(outcome, PostOutcome::EmptyFeed);
    assert!(sender.sent.borrow().is_empty());
    assert_eq!(store.write_count(), 0);
}

#[test]
fn cursor_json_roundtrips_existing_shape() {
    // Parity with the deployed geeknews-rss-cursor.json shape.
    let raw = r#"{"feed":"https://news.hada.io/rss/news","newest_id":100,"seen_ids":[98,99,100],"posted_slots":["2026-08-20:evening"],"updated_at":1787223724}"#;
    let cursor: GeekNewsCursor = serde_json::from_str(raw).expect("parse");
    assert_eq!(cursor.newest_id, 100);
    assert!(cursor.has_seen(99));
    assert!(cursor.has_posted_slot("2026-08-20:evening"));
    let back = serde_json::to_string(&cursor).expect("serialize");
    let reparsed: GeekNewsCursor = serde_json::from_str(&back).expect("reparse");
    assert_eq!(cursor, reparsed);
}
