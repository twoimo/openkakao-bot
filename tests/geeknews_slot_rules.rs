//! Property-based test for the GeekNews posting rule (Correctness Property 6).
//!
//! Verifies R11.7: when a GeekNews send is **not confirmed**, the `seen`/`slots`
//! dedup state is left completely unchanged. Across arbitrary feeds, arbitrary
//! pre-existing cursors, and arbitrary in-window post times, a failing sender
//! never writes the cursor store — so the same items stay eligible for the next
//! attempt and no slot is marked posted.
//!
//! Everything uses fake feed/sender/store adapters: there is no real network
//! and no real Kakao send.
//!
//! Validates: Requirement R11.7

use std::cell::RefCell;
use std::panic::{self, AssertUnwindSafe};
use std::rc::Rc;

use openkakao_cli::geeknews::{
    day_slot_windows, run_geeknews_post, CursorStore, FeedSource, GeekNewsCursor, GeekNewsError,
    PostOutcome, SendError, Sender, StoreError,
};
use proptest::prelude::*;

/// A fake Atom feed returning canned XML — no network.
struct FakeFeed {
    xml: String,
}

impl FeedSource for FakeFeed {
    fn fetch_xml(&self) -> Result<String, openkakao_cli::geeknews::FeedError> {
        Ok(self.xml.clone())
    }
}

/// A sender that always refuses, standing in for an unconfirmed send. It records
/// nothing because it never performs a real send.
struct FailingSender;

impl Sender for FailingSender {
    fn send(&self, _chat_id: i64, _message: &str) -> Result<(), SendError> {
        Err(SendError("simulated unconfirmed send".to_string()))
    }
}

/// An in-memory cursor store that counts writes so the test can prove the store
/// was never touched on a failed send.
struct CountingStore {
    cursor: RefCell<GeekNewsCursor>,
    writes: RefCell<usize>,
}

impl CountingStore {
    fn new(cursor: GeekNewsCursor) -> Self {
        Self {
            cursor: RefCell::new(cursor),
            writes: RefCell::new(0),
        }
    }
}

impl CursorStore for CountingStore {
    fn load(&self) -> Result<GeekNewsCursor, StoreError> {
        Ok(self.cursor.borrow().clone())
    }

    fn store(&self, cursor: &GeekNewsCursor) -> Result<(), StoreError> {
        *self.cursor.borrow_mut() = cursor.clone();
        *self.writes.borrow_mut() += 1;
        Ok(())
    }
}

/// Build a valid Atom feed from a set of `(id, title)` entries.
fn feed_from(ids: &[u32]) -> String {
    let entries: String = ids
        .iter()
        .map(|id| {
            format!(
                "<entry><title>Item {id}</title><link href='https://news.hada.io/topic?id={id}'/><content>c{id}</content></entry>"
            )
        })
        .collect();
    format!("<feed>{entries}</feed>")
}

/// A base timestamp roughly spanning a range of days so different KST days (and
/// therefore different jittered slot windows) are exercised.
fn base_timestamp_strategy() -> impl Strategy<Value = i64> {
    // ~2025 through ~2027 in unix seconds.
    1_735_000_000i64..1_820_000_000i64
}

/// Distinct feed topic ids (1..=999) with 1..=8 entries.
fn feed_ids_strategy() -> impl Strategy<Value = Vec<u32>> {
    prop::collection::hash_set(1u32..1000, 1..8).prop_map(|set| {
        let mut v: Vec<u32> = set.into_iter().collect();
        v.sort_unstable();
        v
    })
}

/// A pre-existing cursor whose seen ids and posted slots are arbitrary but do
/// not pre-mark the slot under test (that is handled by the test body).
fn cursor_strategy() -> impl Strategy<Value = GeekNewsCursor> {
    (
        prop::collection::hash_set(1u32..1000, 0..6),
        0u32..1000,
    )
        .prop_map(|(seen, newest)| {
            let mut seen_ids: Vec<u32> = seen.into_iter().collect();
            seen_ids.sort_unstable();
            GeekNewsCursor {
                feed: "https://news.hada.io/rss/news".to_string(),
                newest_id: newest,
                seen_ids,
                posted_slots: Vec::new(),
                updated_at: 0,
            }
        })
}

proptest! {
    /// Correctness Property 6: a failed (unconfirmed) send never mutates the
    /// dedup cursor. We drive the attempt at an instant inside an open slot and
    /// assert the store was never written and its contents are byte-for-byte the
    /// pre-attempt cursor.
    #[test]
    fn failed_send_never_mutates_seen_or_slots(
        base in base_timestamp_strategy(),
        ids in feed_ids_strategy(),
        chat_id in 1i64..1_000_000,
        cursor in cursor_strategy(),
        slot_index in 0usize..3,
    ) {
        // Choose an instant that is guaranteed to be inside an open slot.
        let windows = day_slot_windows(base);
        prop_assume!(!windows.is_empty());
        let window = &windows[slot_index % windows.len()];
        let now = window.start; // window start is inside [start, end)

        // Ensure the attempt actually reaches the send step: the chosen slot is
        // not already posted, and at least one feed item is fresh (unseen). This
        // makes the "failed send" the reason nothing is persisted — not an early
        // slot/no-fresh short-circuit.
        let mut cursor = cursor;
        cursor.posted_slots.retain(|m| m != &window.marker);
        cursor.seen_ids.retain(|id| !ids.contains(id));
        let before = cursor.clone();

        let feed = FakeFeed { xml: feed_from(&ids) };
        let sender = FailingSender;
        let store = CountingStore::new(cursor);

        let result = run_geeknews_post(chat_id, now, &feed, &sender, &store);

        // The send failed, so the call must return a send error...
        prop_assert!(matches!(result, Err(GeekNewsError::Send(_))));
        // ...the store was never written...
        prop_assert_eq!(*store.writes.borrow(), 0);
        // ...and the cursor is exactly what it was before.
        prop_assert_eq!(&*store.cursor.borrow(), &before);
    }

    /// Sanity companion: the slot-window instant we pick is genuinely inside a
    /// slot, so the "failed send" case above is actually reaching the send step
    /// (not short-circuiting at the slot gate).
    #[test]
    fn chosen_instant_is_inside_a_slot(
        base in base_timestamp_strategy(),
        slot_index in 0usize..3,
    ) {
        let windows = day_slot_windows(base);
        prop_assume!(!windows.is_empty());
        let window = &windows[slot_index % windows.len()];
        prop_assert!(window.start >= 0);
        prop_assert!(window.end > window.start);
        prop_assert!(openkakao_cli::geeknews::slot_at(window.start).is_some());
    }
}

// ---------------------------------------------------------------------------
// Property 32: 긱뉴스 커서는 전송 확정 후에만 지속 (R12.5)
//
// Across *every* posting attempt — send confirmed, send failed, or forced
// termination mid-processing — the number of persisted `seen`/`slots` entries
// stays ≤ the number of confirmed-send entries; an unconfirmed attempt (failed
// send, or termination before the store commit) leaves both states equal to
// their pre-attempt values; and the persisted state survives a restart
// unchanged (a fresh `CursorStore` loaded from the same persisted state reads
// back exactly what was committed).
//
// All three adapters are fakes: no real network, no real Kakao send. Forced
// termination is modeled as an attempt whose store commit is never reached —
// the cursor store aborts (panics) at the persist boundary and the abort is
// caught, so the persisted state is left untouched. Restart is modeled as a
// fresh `SharedStore` reading the same persisted backing state.
// ---------------------------------------------------------------------------

/// A sender that always confirms, counting how many confirmed sends occurred.
struct SucceedingSender {
    sends: Rc<RefCell<usize>>,
}

impl Sender for SucceedingSender {
    fn send(&self, _chat_id: i64, _message: &str) -> Result<(), SendError> {
        *self.sends.borrow_mut() += 1;
        Ok(())
    }
}

/// A cursor store over a *shared* persisted state so that a fresh store built
/// from the same backing `Rc` models an app restart. When `panic_on_store` is
/// set the store aborts at the persist boundary *before* mutating anything,
/// modeling a forced termination that never reaches the store commit.
struct SharedStore {
    persisted: Rc<RefCell<GeekNewsCursor>>,
    panic_on_store: bool,
}

impl SharedStore {
    fn new(persisted: Rc<RefCell<GeekNewsCursor>>, panic_on_store: bool) -> Self {
        Self {
            persisted,
            panic_on_store,
        }
    }
}

impl CursorStore for SharedStore {
    fn load(&self) -> Result<GeekNewsCursor, StoreError> {
        Ok(self.persisted.borrow().clone())
    }

    fn store(&self, cursor: &GeekNewsCursor) -> Result<(), StoreError> {
        if self.panic_on_store {
            // Forced termination: die before the cursor commit lands. Nothing is
            // mutated, so the persisted state is left exactly as it was.
            panic!("simulated forced termination before cursor commit");
        }
        *self.persisted.borrow_mut() = cursor.clone();
        Ok(())
    }
}

/// How a single posting attempt ends.
#[derive(Clone, Debug)]
enum AttemptKind {
    /// The send is confirmed and the cursor is allowed to persist.
    Confirmed,
    /// The send is not confirmed (a `SendError`); the store is never touched.
    Failed,
    /// The process is force-terminated before the store commit is reached.
    ForcedTermination,
}

/// One generated posting attempt.
#[derive(Clone, Debug)]
struct Attempt {
    base: i64,
    ids: Vec<u32>,
    slot_index: usize,
    kind: AttemptKind,
}

fn attempt_kind_strategy() -> impl Strategy<Value = AttemptKind> {
    prop_oneof![
        Just(AttemptKind::Confirmed),
        Just(AttemptKind::Failed),
        Just(AttemptKind::ForcedTermination),
    ]
}

fn attempt_strategy() -> impl Strategy<Value = Attempt> {
    (
        base_timestamp_strategy(),
        feed_ids_strategy(),
        0usize..3,
        attempt_kind_strategy(),
    )
        .prop_map(|(base, ids, slot_index, kind)| Attempt {
            base,
            ids,
            slot_index,
            kind,
        })
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// Correctness Property 32 (R12.5): persisted `seen`/`slots` entries never
    /// exceed the confirmed-send entries, unconfirmed attempts leave both
    /// states unchanged, and the state survives a restart.
    #[test]
    fn cursor_persists_only_after_confirmed_send(
        attempts in prop::collection::vec(attempt_strategy(), 1..12),
        chat_id in 1i64..1_000_000,
    ) {
        // Start from an empty cursor so the persisted-entry counts can be
        // compared directly against the confirmed-send counts (no pre-existing
        // entries to offset the bound).
        let persisted = Rc::new(RefCell::new(GeekNewsCursor::default()));
        let confirmed_sends = Rc::new(RefCell::new(0usize));

        // Confirmed-send accounting: distinct ids and distinct slot markers that
        // reached a persisted `Posted` outcome.
        let mut confirmed_item_ids: std::collections::BTreeSet<u32> =
            std::collections::BTreeSet::new();
        let mut confirmed_slot_markers: std::collections::BTreeSet<String> =
            std::collections::BTreeSet::new();

        for attempt in &attempts {
            let windows = day_slot_windows(attempt.base);
            if windows.is_empty() {
                continue;
            }
            let window = &windows[attempt.slot_index % windows.len()];
            let now = window.start; // guaranteed inside [start, end)
            let feed = FakeFeed {
                xml: feed_from(&attempt.ids),
            };
            let before = persisted.borrow().clone();

            match attempt.kind {
                AttemptKind::Confirmed => {
                    let sender = SucceedingSender {
                        sends: confirmed_sends.clone(),
                    };
                    let store = SharedStore::new(persisted.clone(), false);
                    let result = run_geeknews_post(chat_id, now, &feed, &sender, &store);
                    match result {
                        Ok(PostOutcome::Posted { slot, ids }) => {
                            // A confirmed send that persisted: record what was
                            // confirmed. State is allowed to change here.
                            for id in &ids {
                                confirmed_item_ids.insert(*id);
                            }
                            confirmed_slot_markers.insert(slot);
                        }
                        Ok(_) => {
                            // No send happened (slot already posted, empty feed,
                            // or nothing fresh): the cursor must be unchanged.
                            prop_assert_eq!(&*persisted.borrow(), &before);
                        }
                        Err(err) => {
                            // A succeeding sender + committing store cannot error.
                            prop_assert!(false, "unexpected error on confirmed send: {err:?}");
                        }
                    }
                }
                AttemptKind::Failed => {
                    let sender = FailingSender;
                    let store = SharedStore::new(persisted.clone(), false);
                    let _ = run_geeknews_post(chat_id, now, &feed, &sender, &store);
                    // Unconfirmed: the persisted state is unchanged, whether the
                    // attempt short-circuited before the send or failed at it.
                    prop_assert_eq!(&*persisted.borrow(), &before);
                }
                AttemptKind::ForcedTermination => {
                    let sender = SucceedingSender {
                        sends: confirmed_sends.clone(),
                    };
                    let store = SharedStore::new(persisted.clone(), true);
                    // Silence the abort's panic output, then catch the forced
                    // termination so it does not fail the test process.
                    let prev_hook = panic::take_hook();
                    panic::set_hook(Box::new(|_| {}));
                    let _ = panic::catch_unwind(AssertUnwindSafe(|| {
                        let _ = run_geeknews_post(chat_id, now, &feed, &sender, &store);
                    }));
                    panic::set_hook(prev_hook);
                    // The commit was never reached: the persisted state is
                    // unchanged from before the attempt.
                    prop_assert_eq!(&*persisted.borrow(), &before);
                }
            }
        }

        // Restart: a fresh store over the same backing state reads back exactly
        // what was committed.
        let snapshot = persisted.borrow().clone();
        let restart_store = SharedStore::new(persisted.clone(), false);
        let reloaded = restart_store.load().expect("reload after restart");
        prop_assert_eq!(&reloaded, &snapshot);

        // Persisted entry counts never exceed the confirmed-send entry counts,
        // and this holds on the restarted view too.
        prop_assert!(reloaded.seen_ids.len() <= confirmed_item_ids.len());
        prop_assert!(reloaded.posted_slots.len() <= confirmed_slot_markers.len());
    }
}
