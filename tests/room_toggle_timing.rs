//! Room-toggle timing property test (Task 8.1, Correctness Property 5, latter
//! half).
//!
//! Verifies R6.6: a per-room toggle change is reflected **only** from message
//! processing that *starts after* the change. A message that has already pinned
//! its automation snapshot keeps the toggle values it started with; a message
//! whose processing starts after the toggle observes the new values.
//!
//! Everything runs through the in-memory catalog and a fake room directory — no
//! real Kakao or network call is ever made. Two shapes of the property are
//! checked:
//!
//! 1. A deterministic value-semantics property over an arbitrary sequence of
//!    toggles: a snapshot pinned before a toggle never changes, while a fresh
//!    pin after the toggle reflects it, and every mutation strictly bumps the
//!    catalog version.
//! 2. A concurrent property where a toggle lands while a worker thread is still
//!    holding its pinned snapshot, made deterministic with barriers so the
//!    toggle is guaranteed to happen mid-processing.
//!
//! Validates: Requirement R6.6

use std::sync::{Arc, Barrier};
use std::thread;

use openkakao_cli::room_catalog::{
    InMemoryRoomCatalog, MapRoomDirectory, RoomCatalog, Toggle,
};
use proptest::prelude::*;

const CHAT_ID: i64 = 42;

/// A catalog holding exactly one accessible room (chat id 42), with every
/// toggle starting off. The room is added through the public API, so the fake
/// directory is the only source of truth for accessibility.
fn catalog() -> InMemoryRoomCatalog {
    let directory = Arc::new(MapRoomDirectory::new([(CHAT_ID, "부자멘토멘티".to_string())]));
    let catalog = InMemoryRoomCatalog::new(directory);
    catalog.add(CHAT_ID).expect("accessible room adds");
    catalog
}

/// The three per-room toggles, used to drive arbitrary toggle sequences.
fn toggle_strategy() -> impl Strategy<Value = Toggle> {
    prop_oneof![
        Just(Toggle::Enabled),
        Just(Toggle::AutoReply),
        Just(Toggle::GeekNews),
    ]
}

/// One change: a (toggle, on/off) pair.
fn change_strategy() -> impl Strategy<Value = (Toggle, bool)> {
    (toggle_strategy(), any::<bool>())
}

proptest! {
    /// Correctness Property 5 (value semantics, latter half): an automation
    /// snapshot pinned at the start of processing never changes under later
    /// toggles, and a snapshot pinned *after* a toggle reflects exactly the
    /// applied change (R6.6).
    #[test]
    fn pinned_snapshot_is_stable_and_toggle_applies_to_next_pin(
        changes in prop::collection::vec(change_strategy(), 1..12),
    ) {
        let catalog = catalog();

        // A message begins processing before any change: pin the snapshot.
        let pinned_at_start = catalog.pin(CHAT_ID);
        prop_assert!(pinned_at_start.present);
        prop_assert!(pinned_at_start.enabled);
        prop_assert!(!pinned_at_start.auto_reply);
        prop_assert!(!pinned_at_start.geeknews);
        let start_version = pinned_at_start.version;

        for (kind, on) in changes {
            // A message that pins just before this change.
            let before = catalog.pin(CHAT_ID);
            let before_version = before.version;

            catalog.set_toggle(CHAT_ID, kind, on).expect("toggle applies");

            // A message that starts after the change sees the new value on the
            // toggled field (R6.6), while the other fields are unchanged.
            let after = catalog.pin(CHAT_ID);
            prop_assert!(after.version > before_version);
            match kind {
                Toggle::Enabled => {
                    prop_assert_eq!(after.enabled, on);
                    prop_assert_eq!(after.auto_reply, before.auto_reply);
                    prop_assert_eq!(after.geeknews, before.geeknews);
                }
                Toggle::AutoReply => {
                    prop_assert_eq!(after.auto_reply, on);
                    prop_assert_eq!(after.enabled, before.enabled);
                    prop_assert_eq!(after.geeknews, before.geeknews);
                }
                Toggle::GeekNews => {
                    prop_assert_eq!(after.geeknews, on);
                    prop_assert_eq!(after.enabled, before.enabled);
                    prop_assert_eq!(after.auto_reply, before.auto_reply);
                }
                // `toggle_strategy` only yields the three timing toggles above.
                Toggle::LinkForward | Toggle::TelegramRelay => unreachable!(),
            }

            // The in-flight snapshot pinned before the change is untouched: it
            // still reports its own version (R6.6).
            prop_assert_eq!(before.version, before_version);

            // The snapshot pinned at the very start is still the initial state,
            // no matter how many toggles have landed (R6.6).
            prop_assert_eq!(pinned_at_start.version, start_version);
            prop_assert!(pinned_at_start.enabled);
            prop_assert!(!pinned_at_start.auto_reply);
            prop_assert!(!pinned_at_start.geeknews);
        }
    }

    /// Correctness Property 5 (concurrency, latter half): when a toggle lands
    /// while a worker is still holding its pinned snapshot, the worker finishes
    /// on the OLD value and a fresh pin sees the NEW value. Barriers make the
    /// interleaving deterministic so the toggle is guaranteed to happen
    /// mid-processing (R6.6).
    #[test]
    fn in_flight_worker_finishes_on_old_toggle_value(
        kind in toggle_strategy(),
    ) {
        let catalog = Arc::new(catalog());
        // The initial state is enabled=on, auto_reply=off, geeknews=off. Flip
        // the targeted toggle to the opposite of its initial value so the
        // change is genuine for all three kinds.
        let target = !matches!(kind, Toggle::Enabled);
        let pinned = Arc::new(Barrier::new(2));
        let toggled = Arc::new(Barrier::new(2));

        let worker_catalog = Arc::clone(&catalog);
        let worker_pinned = Arc::clone(&pinned);
        let worker_toggled = Arc::clone(&toggled);
        let worker = thread::spawn(move || {
            // Start of processing: pin the snapshot on the OLD (off) value.
            let snapshot = worker_catalog.pin(CHAT_ID);
            worker_pinned.wait();
            // Block until the main thread has applied the toggle.
            worker_toggled.wait();
            // Finish on the originally pinned snapshot.
            snapshot
        });

        // Wait until the worker pinned, then flip the toggle while it is in
        // flight.
        pinned.wait();
        catalog.set_toggle(CHAT_ID, kind, target).expect("toggle applies");
        toggled.wait();

        let snapshot = worker.join().expect("worker thread joins");
        // In-flight processing completed on the OLD value: the field the toggle
        // targeted still holds its initial value for this snapshot (R6.6).
        match kind {
            Toggle::Enabled => prop_assert!(snapshot.enabled),
            Toggle::AutoReply => prop_assert!(!snapshot.auto_reply),
            Toggle::GeekNews => prop_assert!(!snapshot.geeknews),
            Toggle::LinkForward | Toggle::TelegramRelay => unreachable!(),
        }

        // A message starting after the toggle sees the NEW value.
        let after = catalog.pin(CHAT_ID);
        match kind {
            Toggle::Enabled => prop_assert_eq!(after.enabled, target),
            Toggle::AutoReply => prop_assert_eq!(after.auto_reply, target),
            Toggle::GeekNews => prop_assert_eq!(after.geeknews, target),
            Toggle::LinkForward | Toggle::TelegramRelay => unreachable!(),
        }
        prop_assert!(after.version > snapshot.version);
    }
}
