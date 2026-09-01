//! Unified model configuration + atomic snapshot swap (R5, Component 5).
//!
//! The model-settings window (R5) manages the reply model and the image model
//! from a single place. When the operator changes a provider/model, the change
//! must apply **immediately and stably**:
//!
//! * A reply generation that is already in flight keeps running on the settings
//!   it started with, all the way to completion (R5.6).
//! * A reply generation that starts *after* the swap sees the new settings
//!   (R5.5).
//!
//! This module provides the runtime primitive that makes that possible: an
//! atomically versioned [`ModelConfig`] snapshot behind [`ModelConfigStore`].
//!
//! ## How the snapshot pin works
//!
//! The runtime **pins** a snapshot at the start of processing a message by
//! calling [`ModelConfigStore::current`], which returns an *owned*
//! [`ModelConfig`]. Because the returned value is owned, a later
//! [`ModelConfigStore::apply`] can never mutate a snapshot that was already
//! handed out — the in-flight processing is value-isolated from the swap. Each
//! successful `apply` publishes a new snapshot with a strictly greater
//! [`ModelConfig::version`], so any processing that pins *after* the swap
//! observes the new version.
//!
//! ## Validation (R5.7)
//!
//! [`ModelConfigStore::apply`] validates the incoming selection before it swaps
//! anything. An empty provider/model or a value that is not in the supported
//! catalog is rejected with a plain-language [`ConfigError`], and the stored
//! configuration is left **exactly** as it was.

use std::collections::BTreeSet;
use std::fmt;
use std::sync::{Arc, RwLock};

use thiserror::Error;

/// Which of the two managed models a value belongs to. Used for
/// plain-language error messages (R5.7).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ModelSlot {
    /// The reply (answer-generation) model.
    Reply,
    /// The image model.
    Image,
}

impl fmt::Display for ModelSlot {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ModelSlot::Reply => f.write_str("답변 모델"),
            ModelSlot::Image => f.write_str("이미지 모델"),
        }
    }
}

/// A provider + model pair for one slot (reply or image).
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub struct ModelSelection {
    /// The provider id (e.g. `openai-codex`, `gjc`).
    pub provider: String,
    /// The model id served by that provider.
    pub model: String,
}

impl ModelSelection {
    /// Build a selection from raw parts. Values are stored verbatim; validation
    /// happens in [`ModelConfigStore::apply`].
    pub fn new(provider: impl Into<String>, model: impl Into<String>) -> Self {
        Self {
            provider: provider.into(),
            model: model.into(),
        }
    }

    /// The `provider/model` pair, trimmed, as owned strings. Used for
    /// supported-catalog membership checks.
    fn trimmed_pair(&self) -> (String, String) {
        (self.provider.trim().to_string(), self.model.trim().to_string())
    }
}

/// The unified model configuration: reply model + image model + a monotonic
/// version stamped by the store (R5.1, R5.4).
///
/// The `version` is authoritative and assigned by [`ModelConfigStore`]; it
/// strictly increases on every successful [`ModelConfigStore::apply`]. A value
/// constructed via [`ModelConfig::new`] carries version `0` until the store
/// stamps it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ModelConfig {
    /// The reply-model selection.
    pub reply: ModelSelection,
    /// The image-model selection.
    pub image: ModelSelection,
    /// Monotonic snapshot version assigned by the store.
    pub version: u64,
}

impl ModelConfig {
    /// Build an unversioned configuration (version `0`). The store assigns the
    /// authoritative version when this is applied.
    pub fn new(reply: ModelSelection, image: ModelSelection) -> Self {
        Self {
            reply,
            image,
            version: 0,
        }
    }
}

/// The supported catalog a selection is validated against (R5.7). A selection
/// is supported only when its trimmed `(provider, model)` pair is present in
/// the matching slot's set.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct SupportedModels {
    reply: BTreeSet<(String, String)>,
    image: BTreeSet<(String, String)>,
}

impl SupportedModels {
    /// Build a catalog from the reply and image `(provider, model)` pairs.
    pub fn new(
        reply: impl IntoIterator<Item = (String, String)>,
        image: impl IntoIterator<Item = (String, String)>,
    ) -> Self {
        Self {
            reply: reply.into_iter().collect(),
            image: image.into_iter().collect(),
        }
    }

    /// True when the reply selection's trimmed pair is in the catalog.
    pub fn allows_reply(&self, selection: &ModelSelection) -> bool {
        self.reply.contains(&selection.trimmed_pair())
    }

    /// True when the image selection's trimmed pair is in the catalog.
    pub fn allows_image(&self, selection: &ModelSelection) -> bool {
        self.image.contains(&selection.trimmed_pair())
    }
}

/// Why a model-configuration save was rejected (R5.7). Each variant carries a
/// plain-language Korean message and leaves the stored configuration unchanged.
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum ConfigError {
    /// The provider value for a slot is empty (or only whitespace).
    #[error("{0}의 공급자 값이 비어 있어요. 공급자를 입력한 뒤 다시 저장해 주세요.")]
    EmptyProvider(ModelSlot),
    /// The model value for a slot is empty (or only whitespace).
    #[error("{0}의 모델 값이 비어 있어요. 모델을 입력한 뒤 다시 저장해 주세요.")]
    EmptyModel(ModelSlot),
    /// The provider/model pair for a slot is not supported.
    #[error(
        "{slot}에 지원하지 않는 값이 있어요: 공급자 \"{provider}\", 모델 \"{model}\". 목록에 있는 값을 골라 다시 저장해 주세요."
    )]
    UnsupportedSelection {
        /// Which slot the unsupported value is in.
        slot: ModelSlot,
        /// The offending provider value.
        provider: String,
        /// The offending model value.
        model: String,
    },
}

/// Validate a single slot's selection against the supported catalog. Empty
/// values are reported first, then catalog membership.
fn validate_selection(
    slot: ModelSlot,
    selection: &ModelSelection,
    supported: bool,
) -> Result<(), ConfigError> {
    if selection.provider.trim().is_empty() {
        return Err(ConfigError::EmptyProvider(slot));
    }
    if selection.model.trim().is_empty() {
        return Err(ConfigError::EmptyModel(slot));
    }
    if !supported {
        return Err(ConfigError::UnsupportedSelection {
            slot,
            provider: selection.provider.clone(),
            model: selection.model.clone(),
        });
    }
    Ok(())
}

/// Validate a full configuration (both slots) against the catalog. The reply
/// slot is checked first so its error surfaces before the image slot's.
fn validate_config(config: &ModelConfig, supported: &SupportedModels) -> Result<(), ConfigError> {
    validate_selection(
        ModelSlot::Reply,
        &config.reply,
        supported.allows_reply(&config.reply),
    )?;
    validate_selection(
        ModelSlot::Image,
        &config.image,
        supported.allows_image(&config.image),
    )?;
    Ok(())
}

/// The model-configuration store (R5.4, R5.5, R5.6).
///
/// [`ModelConfigStore::current`] pins and returns an owned snapshot;
/// [`ModelConfigStore::apply`] validates and atomically swaps in a new snapshot
/// with a strictly greater version.
pub trait ModelConfigStore {
    /// Pin and return the current configuration snapshot. The returned value is
    /// owned, so it stays fixed for the caller even if another thread applies a
    /// swap afterwards (R5.6).
    fn current(&self) -> ModelConfig;

    /// Validate `new` and, on success, atomically publish it as the current
    /// snapshot with the next version (R5.4, R5.5). On validation failure the
    /// stored configuration is left unchanged and a plain-language
    /// [`ConfigError`] is returned (R5.7). The `version` field of `new` is
    /// ignored; the store assigns the authoritative version.
    fn apply(&self, new: ModelConfig) -> Result<(), ConfigError>;
}

/// An in-memory [`ModelConfigStore`]. The current snapshot lives behind an
/// `RwLock<Arc<ModelConfig>>`: readers clone the `Arc`-shared value cheaply and
/// a swap replaces the pointer under the write lock, so a swap is atomic with
/// respect to any concurrent [`ModelConfigStore::current`] call.
#[derive(Debug)]
pub struct InMemoryModelConfigStore {
    inner: RwLock<Arc<ModelConfig>>,
    supported: SupportedModels,
}

impl InMemoryModelConfigStore {
    /// Build a store from a validated initial configuration. The initial
    /// snapshot is stamped with version `1`. Returns a [`ConfigError`] (without
    /// building the store) if the initial configuration is invalid.
    pub fn new(initial: ModelConfig, supported: SupportedModels) -> Result<Self, ConfigError> {
        validate_config(&initial, &supported)?;
        let seeded = ModelConfig {
            reply: initial.reply,
            image: initial.image,
            version: 1,
        };
        Ok(Self {
            inner: RwLock::new(Arc::new(seeded)),
            supported,
        })
    }

    /// The supported catalog this store validates against.
    pub fn supported(&self) -> &SupportedModels {
        &self.supported
    }
}

impl ModelConfigStore for InMemoryModelConfigStore {
    fn current(&self) -> ModelConfig {
        // Clone the pointed-to value into an owned snapshot. The read lock is
        // held only for the pointer clone; the snapshot is independent of any
        // later swap.
        let guard = self
            .inner
            .read()
            .expect("model-config lock poisoned on read");
        (**guard).clone()
    }

    fn apply(&self, new: ModelConfig) -> Result<(), ConfigError> {
        // Validate before touching the stored snapshot so a rejected save keeps
        // the existing configuration exactly as it was (R5.7).
        validate_config(&new, &self.supported)?;
        let mut guard = self
            .inner
            .write()
            .expect("model-config lock poisoned on write");
        let next_version = guard.version.saturating_add(1);
        *guard = Arc::new(ModelConfig {
            reply: new.reply,
            image: new.image,
            version: next_version,
        });
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Barrier;
    use std::thread;

    /// A small supported catalog reused across tests. Providers/models mirror
    /// values the config layer already attests as valid.
    fn catalog() -> SupportedModels {
        SupportedModels::new(
            [
                (
                    "google-antigravity".to_string(),
                    "google-antigravity/gemini-3.7-flash-tiered".to_string(),
                ),
                ("openai-codex".to_string(), "gpt-5.6-luna".to_string()),
            ],
            [
                ("openai-image".to_string(), "img-model-a".to_string()),
                ("openai-image".to_string(), "img-model-b".to_string()),
            ],
        )
    }

    fn reply_a() -> ModelSelection {
        ModelSelection::new("openai-codex", "gpt-5.6-luna")
    }

    fn reply_b() -> ModelSelection {
        ModelSelection::new(
            "google-antigravity",
            "google-antigravity/gemini-3.7-flash-tiered",
        )
    }

    fn image_a() -> ModelSelection {
        ModelSelection::new("openai-image", "img-model-a")
    }

    fn image_b() -> ModelSelection {
        ModelSelection::new("openai-image", "img-model-b")
    }

    fn store() -> InMemoryModelConfigStore {
        InMemoryModelConfigStore::new(ModelConfig::new(reply_a(), image_a()), catalog())
            .expect("valid initial config")
    }

    #[test]
    fn initial_snapshot_is_version_one() {
        let store = store();
        let snapshot = store.current();
        assert_eq!(snapshot.version, 1);
        assert_eq!(snapshot.reply, reply_a());
        assert_eq!(snapshot.image, image_a());
    }

    #[test]
    fn apply_bumps_version_and_swaps_selection() {
        let store = store();
        store
            .apply(ModelConfig::new(reply_b(), image_b()))
            .expect("valid swap");
        let snapshot = store.current();
        assert_eq!(snapshot.version, 2);
        assert_eq!(snapshot.reply, reply_b());
        assert_eq!(snapshot.image, image_b());
    }

    #[test]
    fn apply_ignores_incoming_version() {
        let store = store();
        let mut incoming = ModelConfig::new(reply_b(), image_b());
        incoming.version = 999;
        store.apply(incoming).expect("valid swap");
        // The store assigns 2, not the caller-supplied 999.
        assert_eq!(store.current().version, 2);
    }

    #[test]
    fn pinned_snapshot_is_isolated_from_later_swap() {
        let store = store();
        // Pin at the start of "processing".
        let pinned = store.current();
        assert_eq!(pinned.version, 1);
        // A swap happens while the pinned snapshot is in use.
        store
            .apply(ModelConfig::new(reply_b(), image_b()))
            .expect("valid swap");
        // The in-flight snapshot is unchanged (R5.6).
        assert_eq!(pinned.version, 1);
        assert_eq!(pinned.reply, reply_a());
        // Processing that starts after the swap sees the new version (R5.5).
        assert_eq!(store.current().version, 2);
        assert_eq!(store.current().reply, reply_b());
    }

    #[test]
    fn empty_reply_provider_is_rejected_and_config_unchanged() {
        let store = store();
        let err = store
            .apply(ModelConfig::new(
                ModelSelection::new("   ", "gpt-5.6-luna"),
                image_a(),
            ))
            .expect_err("empty provider must be rejected");
        assert_eq!(err, ConfigError::EmptyProvider(ModelSlot::Reply));
        // Existing config is preserved (R5.7).
        assert_eq!(store.current().version, 1);
        assert_eq!(store.current().reply, reply_a());
    }

    #[test]
    fn empty_image_model_is_rejected() {
        let store = store();
        let err = store
            .apply(ModelConfig::new(reply_a(), ModelSelection::new("openai-image", "")))
            .expect_err("empty model must be rejected");
        assert_eq!(err, ConfigError::EmptyModel(ModelSlot::Image));
        assert_eq!(store.current().version, 1);
    }

    #[test]
    fn unsupported_reply_selection_is_rejected() {
        let store = store();
        let err = store
            .apply(ModelConfig::new(
                ModelSelection::new("unknown-provider", "made-up-model"),
                image_a(),
            ))
            .expect_err("unsupported selection must be rejected");
        assert_eq!(
            err,
            ConfigError::UnsupportedSelection {
                slot: ModelSlot::Reply,
                provider: "unknown-provider".to_string(),
                model: "made-up-model".to_string(),
            }
        );
        assert_eq!(store.current().version, 1);
    }

    #[test]
    fn invalid_initial_config_is_rejected() {
        let err = InMemoryModelConfigStore::new(
            ModelConfig::new(ModelSelection::new("", ""), image_a()),
            catalog(),
        )
        .expect_err("invalid initial config must fail");
        assert_eq!(err, ConfigError::EmptyProvider(ModelSlot::Reply));
    }

    #[test]
    fn error_messages_are_plain_korean() {
        let msg = ConfigError::EmptyProvider(ModelSlot::Reply).to_string();
        assert!(msg.contains("답변 모델"));
        assert!(msg.contains("비어 있어요"));
    }

    /// In-flight processing completes on the OLD version even when a swap lands
    /// mid-processing, while processing that starts after the swap sees the NEW
    /// version. Ordering is made deterministic with barriers so the swap is
    /// guaranteed to happen while the worker is still holding its pinned
    /// snapshot (R5.5, R5.6).
    #[test]
    fn in_flight_processing_completes_on_old_version_under_concurrent_swap() {
        let store = Arc::new(store());
        let pinned_barrier = Arc::new(Barrier::new(2));
        let swapped_barrier = Arc::new(Barrier::new(2));

        let worker_store = Arc::clone(&store);
        let worker_pinned = Arc::clone(&pinned_barrier);
        let worker_swapped = Arc::clone(&swapped_barrier);
        let worker = thread::spawn(move || {
            // Start of processing: pin the snapshot.
            let snapshot = worker_store.current();
            // Signal that the snapshot is pinned, then wait until the main
            // thread has applied the swap.
            worker_pinned.wait();
            worker_swapped.wait();
            // Finish processing using the originally pinned snapshot.
            (snapshot.version, snapshot.reply.clone())
        });

        // Wait until the worker has pinned its snapshot, then swap.
        pinned_barrier.wait();
        store
            .apply(ModelConfig::new(reply_b(), image_b()))
            .expect("valid swap");
        // Release the worker to finish on its old snapshot.
        swapped_barrier.wait();

        let (finished_version, finished_reply) = worker.join().expect("worker thread");
        // In-flight processing finished on the OLD version (R5.6).
        assert_eq!(finished_version, 1);
        assert_eq!(finished_reply, reply_a());
        // A processing that starts now sees the NEW version (R5.5).
        assert_eq!(store.current().version, 2);
        assert_eq!(store.current().reply, reply_b());
    }
}
