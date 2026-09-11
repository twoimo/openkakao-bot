//! Model-config atomic snapshot swap property test (Task 7.1, Correctness
//! Property 5).
//!
//! Verifies R5.5 and R5.6: during a configuration swap, a processing that has
//! already pinned a snapshot completes on the **old** version, while a
//! processing that starts **after** the swap observes the **new** version.
//!
//! The store is exercised through in-memory fakes only — no real Kakao or
//! network call is ever made. Two shapes of the property are checked:
//!
//! 1. A deterministic value-semantics property over an arbitrary sequence of
//!    valid swaps (a pinned snapshot never changes; each swap strictly bumps
//!    the version and publishes the new selection).
//! 2. A concurrent property where the swap lands while a worker thread is still
//!    holding its pinned snapshot, made deterministic with barriers.
//!
//! Validates: Requirements R5.5, R5.6

use std::sync::{Arc, Barrier};
use std::thread;

use openkakao_cli::model_config::{
    InMemoryModelConfigStore, ModelConfig, ModelConfigStore, ModelSelection, SupportedModels,
};
use proptest::prelude::*;

/// A fixed catalog of supported reply/image selections. Every selection the
/// strategies produce is drawn from this catalog, so applies always succeed and
/// the swap-ordering invariants are what is under test.
fn catalog() -> SupportedModels {
    SupportedModels::new(reply_catalog(), image_catalog())
}

fn reply_catalog() -> Vec<(String, String)> {
    vec![
        (
            "google-antigravity".to_string(),
            "google-antigravity/gemini-3.7-flash-tiered".to_string(),
        ),
        (
            "google-antigravity".to_string(),
            "google-antigravity/gemini-3.6-flash-tiered".to_string(),
        ),
        ("openai-codex".to_string(), "gpt-5.6-luna".to_string()),
    ]
}

fn image_catalog() -> Vec<(String, String)> {
    vec![
        ("openai-image".to_string(), "img-model-a".to_string()),
        ("openai-image".to_string(), "img-model-b".to_string()),
        ("local-image".to_string(), "img-local".to_string()),
    ]
}

/// Strategy that picks a supported reply selection by index.
fn reply_selection_strategy() -> impl Strategy<Value = ModelSelection> {
    let catalog = reply_catalog();
    (0..catalog.len()).prop_map(move |i| {
        let (provider, model) = catalog[i].clone();
        ModelSelection::new(provider, model)
    })
}

/// Strategy that picks a supported image selection by index.
fn image_selection_strategy() -> impl Strategy<Value = ModelSelection> {
    let catalog = image_catalog();
    (0..catalog.len()).prop_map(move |i| {
        let (provider, model) = catalog[i].clone();
        ModelSelection::new(provider, model)
    })
}

/// Strategy for a full (reply, image) configuration.
fn config_strategy() -> impl Strategy<Value = ModelConfig> {
    (reply_selection_strategy(), image_selection_strategy())
        .prop_map(|(reply, image)| ModelConfig::new(reply, image))
}

proptest! {
    /// Correctness Property 5 (value semantics): a pinned snapshot never changes
    /// under later swaps, and every successful swap strictly increases the
    /// version and publishes exactly the applied selection.
    #[test]
    fn pinned_snapshot_is_stable_and_swaps_are_versioned(
        initial in config_strategy(),
        swaps in prop::collection::vec(config_strategy(), 1..8),
    ) {
        let store = InMemoryModelConfigStore::new(initial.clone(), catalog())
            .expect("valid initial config");

        // Pin at the very start of processing. This is the "in-flight" snapshot.
        let pinned_at_start = store.current();
        prop_assert_eq!(pinned_at_start.version, 1);
        prop_assert_eq!(&pinned_at_start.reply, &initial.reply);
        prop_assert_eq!(&pinned_at_start.image, &initial.image);

        for (offset, swap) in swaps.iter().enumerate() {
            // A processing that starts just before this swap.
            let before = store.current();
            let before_version = before.version;

            store.apply(swap.clone()).expect("valid swap applies");

            // The store starts at version 1 and publishes one new version per
            // applied swap, so the loop offset determines the expected version.
            let expected_version = offset as u64 + 2;

            // A processing that starts just after this swap sees the new version
            // and the new selection (R5.5).
            let after = store.current();
            prop_assert_eq!(after.version, expected_version);
            prop_assert!(after.version > before_version);
            prop_assert_eq!(&after.reply, &swap.reply);
            prop_assert_eq!(&after.image, &swap.image);

            // The snapshot pinned before the swap is unchanged (R5.6): it still
            // reports its own version and selection.
            prop_assert_eq!(before.version, before_version);

            // The snapshot pinned at the very start is still the initial config,
            // no matter how many swaps have landed (R5.6).
            prop_assert_eq!(pinned_at_start.version, 1);
            prop_assert_eq!(&pinned_at_start.reply, &initial.reply);
            prop_assert_eq!(&pinned_at_start.image, &initial.image);
        }
    }

    /// Correctness Property 5 (concurrency): when a swap lands while a worker is
    /// still holding its pinned snapshot, the worker finishes on the OLD version
    /// and a fresh pin sees the NEW version. Barriers make the interleaving
    /// deterministic so the swap is guaranteed to happen mid-processing.
    #[test]
    fn in_flight_worker_finishes_on_old_version(
        initial in config_strategy(),
        swap in config_strategy(),
    ) {
        let store = Arc::new(
            InMemoryModelConfigStore::new(initial.clone(), catalog())
                .expect("valid initial config"),
        );
        let pinned = Arc::new(Barrier::new(2));
        let swapped = Arc::new(Barrier::new(2));

        let worker_store = Arc::clone(&store);
        let worker_pinned = Arc::clone(&pinned);
        let worker_swapped = Arc::clone(&swapped);
        let worker = thread::spawn(move || {
            // Start of processing: pin the snapshot on the OLD version.
            let snapshot = worker_store.current();
            worker_pinned.wait();
            // Block until the main thread has applied the swap.
            worker_swapped.wait();
            // Finish on the originally pinned snapshot.
            (snapshot.version, snapshot.reply.clone(), snapshot.image.clone())
        });

        // Wait until the worker pinned, then swap while it is in flight.
        pinned.wait();
        store.apply(swap.clone()).expect("valid swap applies");
        swapped.wait();

        let (version, reply, image) = worker.join().expect("worker thread joins");
        // In-flight processing completed on the OLD version (R5.6).
        prop_assert_eq!(version, 1);
        prop_assert_eq!(&reply, &initial.reply);
        prop_assert_eq!(&image, &initial.image);

        // A processing starting after the swap sees the NEW version (R5.5).
        let after = store.current();
        prop_assert_eq!(after.version, 2);
        prop_assert_eq!(&after.reply, &swap.reply);
        prop_assert_eq!(&after.image, &swap.image);
    }
}
