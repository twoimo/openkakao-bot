//! Dataset local-only property test (Task 5.4, Correctness Property 7).
//!
//! Verifies that building the RAG dataset performs **zero outbound network
//! transmission** across arbitrary conversation inputs (R3.12).
//!
//! The harness follows the design's "network adapter in forbidden mode": every
//! injected adapter (source, embedder, sink) holds a [`ForbiddenNetwork`] guard.
//! A networked implementation would route through `guard.outbound(...)`, which
//! panics and increments a counter. The real local-only path never calls it, so
//! after any build the recorded attempt count must be exactly zero. If a future
//! change smuggled a network call into the build path, the guard would panic and
//! fail this test.

use std::sync::atomic::{AtomicUsize, Ordering};

use openkakao_cli::context::{Embedder, LocalHashEmbedder};
use openkakao_cli::dataset::{
    BuiltDataset, ConversationSource, DatasetBuilder, DatasetError, DatasetMessage, DatasetSink,
    MessageKind, SqliteDatasetSink,
};
use proptest::prelude::*;

/// The owner display name for these tests. The production `OWNER_NAME` constant
/// was removed in favor of an injected `UserProfile::owner_display_name`
/// (task 3.1); the builder learns the owner from message `is_owner` flags, so a
/// fixed local value is sufficient here.
const OWNER_NAME: &str = "최연우";

/// A network adapter pinned to "forbidden mode": any outbound attempt panics and
/// is counted, so a violation is impossible to miss.
struct ForbiddenNetwork {
    attempts: AtomicUsize,
}

impl ForbiddenNetwork {
    fn new() -> Self {
        Self {
            attempts: AtomicUsize::new(0),
        }
    }

    /// Called only by a hypothetical networked adapter. Records the attempt and
    /// panics — outbound transmission is forbidden during a build.
    #[allow(dead_code)]
    fn outbound(&self, endpoint: &str) -> ! {
        self.attempts.fetch_add(1, Ordering::SeqCst);
        panic!("forbidden outbound network transmission during dataset build: {endpoint}");
    }

    fn attempts(&self) -> usize {
        self.attempts.load(Ordering::SeqCst)
    }
}

/// Local-only embedder guarded by the forbidden-network harness. A remote
/// embedder would call `net.outbound(...)` inside `embed`; the local one never
/// does.
struct GuardedEmbedder<'a> {
    inner: LocalHashEmbedder,
    #[allow(dead_code)]
    net: &'a ForbiddenNetwork,
}

impl Embedder for GuardedEmbedder<'_> {
    fn dim(&self) -> usize {
        self.inner.dim()
    }
    fn embed(&self, text: &str) -> Vec<f32> {
        // Deterministic local hashing — no network.
        self.inner.embed(text)
    }
    fn is_local_only(&self) -> bool {
        true
    }
}

/// Conversation source guarded by the harness. A networked source would call
/// `net.outbound(...)`; the local SQLCipher read never does.
struct GuardedSource<'a> {
    messages: Vec<DatasetMessage>,
    #[allow(dead_code)]
    net: &'a ForbiddenNetwork,
}

impl ConversationSource for GuardedSource<'_> {
    fn load(&self) -> Result<Vec<DatasetMessage>, DatasetError> {
        Ok(self.messages.clone())
    }
}

/// Sink guarded by the harness, wrapping a real in-memory SQLite store. A
/// networked vector store would call `net.outbound(...)`; the local file store
/// never does.
struct GuardedSink<'a> {
    inner: SqliteDatasetSink,
    #[allow(dead_code)]
    net: &'a ForbiddenNetwork,
}

impl DatasetSink for GuardedSink<'_> {
    fn store(&mut self, dataset: &BuiltDataset) -> Result<(), DatasetError> {
        self.inner.store(dataset)
    }
}

/// Strategy for one arbitrary conversation message.
fn message_strategy() -> impl Strategy<Value = DatasetMessage> {
    (
        1i64..5,             // chat_id
        0i64..1_000,         // at
        any::<bool>(),       // is_owner
        0usize..3,           // partner name index
        0usize..3,           // kind index
        0usize..4,           // text index
        0u32..1_000_000,     // provenance salt
    )
        .prop_map(
            |(chat_id, at, is_owner, name_idx, kind_idx, text_idx, salt)| {
                let sender = if is_owner {
                    OWNER_NAME.to_string()
                } else {
                    ["민수", "지현", "상현"][name_idx].to_string()
                };
                let kind = [MessageKind::Text, MessageKind::Image, MessageKind::Emoticon][kind_idx];
                let text = [
                    "안녕 오늘 회의 몇 시야",
                    "이거 확인해줘 https://github.com/foo/bar",
                    "뉴스 봤어? https://news.naver.com/x 그리고 https://youtu.be/z",
                    "",
                ][text_idx]
                    .to_string();
                DatasetMessage {
                    chat_id,
                    at,
                    sender,
                    is_owner,
                    kind,
                    text,
                    provenance: format!("local-db:{chat_id}:{at}:{salt}"),
                }
            },
        )
}

proptest! {
    /// Correctness Property 7: across arbitrary conversation streams, a build
    /// never triggers the forbidden-network guard — zero outbound transmission.
    #[test]
    fn build_never_transmits_outbound(messages in prop::collection::vec(message_strategy(), 0..40)) {
        let net = ForbiddenNetwork::new();
        let source = GuardedSource { messages: messages.clone(), net: &net };
        let embedder = GuardedEmbedder { inner: LocalHashEmbedder::new(), net: &net };
        let mut sink = GuardedSink {
            inner: SqliteDatasetSink::open_in_memory().expect("in-memory store"),
            net: &net,
        };

        let result = {
            let mut builder = DatasetBuilder::new(&source, &mut sink);
            builder.build(&embedder)
        };

        // Empty input aborts with NoConversations; any non-empty input builds.
        if messages.is_empty() {
            prop_assert!(matches!(result, Err(DatasetError::NoConversations)));
        } else {
            prop_assert!(result.is_ok(), "local-only build should succeed: {result:?}");
        }

        // The core invariant: no outbound network transmission occurred.
        prop_assert_eq!(net.attempts(), 0, "a build attempted outbound network I/O");
    }
}

/// A non-local embedder is rejected before any build work, so no adapter — and
/// therefore no network path — is ever exercised (defense in depth for R3.12).
#[test]
fn non_local_embedder_is_rejected_before_any_work() {
    struct RemoteEmbedder;
    impl Embedder for RemoteEmbedder {
        fn dim(&self) -> usize {
            8
        }
        fn embed(&self, _text: &str) -> Vec<f32> {
            vec![0.0; 8]
        }
        fn is_local_only(&self) -> bool {
            false
        }
    }

    let net = ForbiddenNetwork::new();
    let source = GuardedSource {
        messages: vec![DatasetMessage {
            chat_id: 1,
            at: 1,
            sender: "민수".into(),
            is_owner: false,
            kind: MessageKind::Text,
            text: "hi".into(),
            provenance: "local-db:1:1".into(),
        }],
        net: &net,
    };
    let mut sink = GuardedSink {
        inner: SqliteDatasetSink::open_in_memory().unwrap(),
        net: &net,
    };
    let mut builder = DatasetBuilder::new(&source, &mut sink);
    let err = builder.build(&RemoteEmbedder).unwrap_err();
    assert!(matches!(err, DatasetError::NonLocalEmbedder));
    assert_eq!(net.attempts(), 0);
}

// ===========================================================================
// Correctness Property 4: local-only flows perform zero external transmission.
//
// Feature: kakao-agent-live-ops
//
// Property 4 (design) states that local-only flows transmit nothing externally,
// and that an embedder with `is_local_only == false` does not start and leaves
// state unchanged. Task 5.1 extends this perspective to the **dataset refresh**
// flow.
//
// Deferral note: the design's Property 4 also covers self-improve and
// bulk/feature verification. Those modules (`src/improve`, `src/coverage`,
// `src/durability`) do not exist yet — tasks 9, 6.1, and 6 are not implemented —
// so this addition is scoped to the dataset-refresh perspective. The
// self-improve and bulk/feature-verification perspectives are deferred to their
// own tasks (9.1 asserts `ForbiddenNetwork::egress_count() == 0` for
// self-improve; 6.2 for the durability harness).
//
// Validates: Requirements 1.12, 4.11, 4.14, 8.15, 12.9
// ===========================================================================

use openkakao_cli::dataset::{DatasetRefresher, RefreshOutcome, SkipReason};
use openkakao_cli::fakes::{ForbiddenNetwork as FakeForbiddenNetwork, VirtualClock};
use openkakao_cli::ports::{
    MessageCursor, MessageSource, NetworkPort, OwnerCandidate, PortError, RoomRef, SendAuthority,
};
use openkakao_cli::profile::{AccountScope, UserProfile};
use rusqlite::Connection;

/// A minimal fixed-room in-memory [`MessageSource`] for the refresh flow. Reads
/// happen entirely on-device; no network path exists.
struct RefreshSource {
    messages: Vec<DatasetMessage>,
}

impl MessageSource for RefreshSource {
    fn rooms(&self) -> Result<Vec<RoomRef>, PortError> {
        Ok(vec![RoomRef {
            chat_id: 1,
            title: "room-1".into(),
        }])
    }

    fn messages_after(
        &self,
        after: Option<&MessageCursor>,
        limit: usize,
    ) -> Result<Vec<DatasetMessage>, PortError> {
        let mut all = self.messages.clone();
        all.sort_by(|a, b| a.at.cmp(&b.at).then(a.chat_id.cmp(&b.chat_id)));
        Ok(all
            .into_iter()
            .filter(|m| match after {
                Some(c) => m.at > c.at,
                None => true,
            })
            .take(limit)
            .collect())
    }

    fn authority(&self, chat_id: i64) -> Result<SendAuthority, PortError> {
        Ok(SendAuthority {
            chat_id,
            chat_type: 1,
            is_memo: false,
            owner_display_name: Some(OWNER_NAME.into()),
        })
    }

    fn owner_candidates(&self, _limit: usize) -> Result<Vec<OwnerCandidate>, PortError> {
        Ok(vec![])
    }
}

/// A remote embedder for the refresh flow (`is_local_only == false`). If a
/// refresh ever ran with it, the build would be non-local; the refresher must
/// refuse to start instead.
struct RemoteRefreshEmbedder;
impl Embedder for RemoteRefreshEmbedder {
    fn dim(&self) -> usize {
        8
    }
    fn embed(&self, _text: &str) -> Vec<f32> {
        vec![0.0; 8]
    }
    fn is_local_only(&self) -> bool {
        false
    }
}

fn refresh_profile() -> UserProfile {
    UserProfile {
        owner_display_name: OWNER_NAME.to_string(),
        account_scope: AccountScope::new("/tmp/openkakao-refresh-local-only"),
    }
}

fn qa_rows(conn: &Connection) -> i64 {
    conn.query_row("SELECT COUNT(*) FROM qa_pair", [], |row| row.get(0))
        .unwrap_or(0)
}

fn cursor_present(conn: &Connection) -> bool {
    conn.query_row("SELECT 1 FROM refresh_cursor WHERE id = 1", [], |_| Ok(()))
        .is_ok()
}

proptest! {
    /// Property 4 (dataset refresh): across arbitrary conversation streams, a
    /// local-only refresh performs zero outbound network transmission — the
    /// forbidden-network egress count stays at zero from read to commit.
    ///
    /// Validates: Requirements 1.12, 4.11, 12.9
    #[test]
    fn refresh_never_transmits_outbound(
        messages in prop::collection::vec(message_strategy(), 0..40),
    ) {
        let source = RefreshSource { messages };
        let embedder = LocalHashEmbedder::new();
        let clock = VirtualClock::new(1_000);
        let net = FakeForbiddenNetwork::new();
        let profile = refresh_profile();
        let mut refresher = DatasetRefresher::new(&source, &embedder, &clock, &net, &profile);
        let mut conn = Connection::open_in_memory().expect("in-memory conn");

        let outcome = refresher.refresh_now(&mut conn);
        // A local-only refresh always commits (skips/failures only arise from
        // failure injection, which this strategy never triggers).
        prop_assert!(matches!(outcome, RefreshOutcome::Committed(_)));

        // The core invariant: no outbound network transmission occurred.
        prop_assert_eq!(refresher.net_egress(), 0);
        prop_assert_eq!(net.egress_count(), 0);
    }

    /// Property 4 (dataset refresh): an embedder with `is_local_only == false`
    /// does not start a refresh and leaves the cursor and dataset unchanged,
    /// with zero outbound transmission.
    ///
    /// Validates: Requirements 4.14, 8.15, 1.12
    #[test]
    fn non_local_embedder_refresh_does_not_start_and_preserves_state(
        messages in prop::collection::vec(message_strategy(), 0..40),
    ) {
        let source = RefreshSource { messages };
        let embedder = RemoteRefreshEmbedder;
        let clock = VirtualClock::new(1_000);
        let net = FakeForbiddenNetwork::new();
        let profile = refresh_profile();
        let mut refresher = DatasetRefresher::new(&source, &embedder, &clock, &net, &profile);
        let mut conn = Connection::open_in_memory().expect("in-memory conn");

        prop_assert_eq!(
            refresher.refresh_now(&mut conn),
            RefreshOutcome::Skipped(SkipReason::NoLocalEmbedder)
        );

        // Nothing was built, no cursor was written, and no egress happened.
        prop_assert_eq!(qa_rows(&conn), 0);
        prop_assert!(!cursor_present(&conn));
        prop_assert_eq!(net.egress_count(), 0);
    }
}
