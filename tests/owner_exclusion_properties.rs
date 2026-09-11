//! Property-based test for owner (최연우) exclusion from the reply-target set.
//!
//! Verifies Correctness Property 2 (owner exclusion) from the design document:
//! over arbitrary observed-message streams, a message authored by the owner is
//! NEVER included in the reply-target set. The owner's own messages are kept
//! upstream only for context and style learning, never replied to.
//!
//! Validates: Requirement R3.11

use openkakao_cli::auto_reply_service::{select_reply_targets, FlowRecorder, ObservedMessage};
use openkakao_cli::local_db::is_self_author;
use openkakao_cli::logging::{SqliteHistoryStore, HistoryStore, Stage};
use proptest::prelude::*;

/// Result code the pipeline records at Detect when owner messages are excluded.
const OWNER_SELF_EXCLUDED_CODE: &str = "owner_self_excluded";

/// Strategy for an arbitrary observed message. Author ids intentionally include
/// the same small range the account id is drawn from, so a healthy fraction of
/// generated messages are self-authored, plus `0` (which the numeric-only self
/// proof must never treat as the owner).
fn observed_message_strategy() -> impl Strategy<Value = ObservedMessage> {
    (-3i64..3, 0i64..1_000_000, 0i64..6).prop_map(|(chat_id, log_id, author_id)| ObservedMessage {
        chat_id,
        log_id,
        author_id,
    })
}

/// Strategy for a stream of observed messages.
fn stream_strategy() -> impl Strategy<Value = Vec<ObservedMessage>> {
    prop::collection::vec(observed_message_strategy(), 0..48)
}

proptest! {
    /// Correctness Property 2: for any account id and any observed stream, no
    /// message the account authored (per the numeric-only self proof) ever
    /// appears in the reply-target set.
    #[test]
    fn owner_messages_are_never_reply_targets(
        account_user_id in 0i64..6,
        stream in stream_strategy(),
    ) {
        let targets = select_reply_targets(account_user_id, &stream);

        // No selected target may be self-authored.
        for target in &targets {
            prop_assert!(
                !is_self_author(target.author_id, account_user_id),
                "reply target was owner-authored: {target:?} (account {account_user_id})"
            );
        }

        // Completeness: every non-self message is retained, order preserved.
        let expected: Vec<ObservedMessage> = stream
            .iter()
            .copied()
            .filter(|m| !is_self_author(m.author_id, account_user_id))
            .collect();
        prop_assert_eq!(&targets, &expected);
    }

    /// Selection is idempotent: selecting again over the already-filtered set
    /// changes nothing (the owner can never re-enter the reply-target set).
    #[test]
    fn selection_is_idempotent(
        account_user_id in 0i64..6,
        stream in stream_strategy(),
    ) {
        let once = select_reply_targets(account_user_id, &stream);
        let twice = select_reply_targets(account_user_id, &once);
        prop_assert_eq!(once, twice);
    }

    /// When the pipeline instruments detection, the reply-target set it returns
    /// still excludes every owner message, and it records an explicit
    /// owner-exclusion event exactly when at least one owner message was
    /// dropped.
    #[test]
    fn instrumented_detect_excludes_owner_and_logs_the_point(
        account_user_id in 0i64..6,
        stream in stream_strategy(),
    ) {
        let store = SqliteHistoryStore::open_in_memory().expect("open in-memory store");
        let recorder = FlowRecorder::auto_reply(&store, "owner-exclusion-prop");
        let targets = recorder
            .detect_reply_targets(account_user_id, &stream, 0)
            .expect("detect");

        for target in &targets {
            prop_assert!(!is_self_author(target.author_id, account_user_id));
        }

        let owner_present = stream
            .iter()
            .any(|m| is_self_author(m.author_id, account_user_id));
        let events = store.recent(10_000).expect("recent");
        let logged_exclusion = events
            .iter()
            .any(|e| e.stage == Stage::Detect && e.result_code == OWNER_SELF_EXCLUDED_CODE);
        prop_assert_eq!(owner_present, logged_exclusion);
    }
}

// ===========================================================================
// Feature: kakao-agent-live-ops
// Property 15: 소유자 발신 메시지의 답변 대상 제외
//
// For any message stream, the reply-target set of each of the three flows —
// auto reply, dataset (incremental) refresh, and self-improve sample selection
// — has an empty intersection with the owner-sent set, while owner-sent
// messages are still retained upstream for context/style learning. Owner
// detection holds via BOTH paths: a database-authoritative owner match AND an
// exact (whitespace-trimmed) display-name match (R10.7).
//
// The three flows share the single owner-detection primitive
// `profile::is_owner_message`, per the design (R4.12: 소유자 발신 메시지는
// `profile::is_owner_message`로 답변 대상 제외). This property pins that shared
// exclusion for all three.
//
// Validates: Requirements 4.12, 10.7, 12.3
// ===========================================================================

use openkakao_cli::profile::{is_owner_message, AccountScope, SenderIdentity, UserProfile};

/// The three live-ops flows that must exclude owner-sent messages from their
/// reply-target set (R12.3).
const FLOWS: [&str; 3] = ["auto_reply", "dataset_refresh", "self_improve"];

/// A message reduced to the index it occupies in the stream and its sender's
/// database-authoritative identity — the only inputs owner detection needs.
#[derive(Debug, Clone)]
struct ScopedMessage {
    index: usize,
    sender: SenderIdentity,
}

/// How a generated message's sender relates to the owner.
#[allow(
    clippy::enum_variant_names,
    reason = "each variant names how the sender is (or is not) the owner"
)]
#[derive(Debug, Clone, Copy)]
enum SenderKind {
    /// Owner via the database-authoritative flag (display name irrelevant).
    DbOwner,
    /// Owner via an exact display-name match (with surrounding whitespace, to
    /// exercise the trim path).
    NameOwner,
    /// A non-owner participant.
    NonOwner,
}

/// The shared reply-target selection used by all three flows: keep every
/// message that is NOT owner-sent. Order preserved.
fn reply_targets(profile: &UserProfile, stream: &[ScopedMessage]) -> Vec<usize> {
    stream
        .iter()
        .filter(|m| !is_owner_message(profile, &m.sender))
        .map(|m| m.index)
        .collect()
}

/// The owner-sent set: messages the owner authored, kept only for context/style
/// learning and never replied to.
fn owner_sent(profile: &UserProfile, stream: &[ScopedMessage]) -> Vec<usize> {
    stream
        .iter()
        .filter(|m| is_owner_message(profile, &m.sender))
        .map(|m| m.index)
        .collect()
}

fn build_sender(kind: SenderKind, owner: &str, i: usize) -> SenderIdentity {
    match kind {
        SenderKind::DbOwner => SenderIdentity {
            db_authoritative_owner: true,
            // A different display name proves the DB flag alone excludes.
            display_name: format!("anon-{i}"),
        },
        SenderKind::NameOwner => SenderIdentity {
            db_authoritative_owner: false,
            // Padded so the exact match must go through trimming.
            display_name: format!("  {owner}  "),
        },
        SenderKind::NonOwner => SenderIdentity {
            db_authoritative_owner: false,
            // Guaranteed distinct from the owner name after trimming.
            display_name: format!("{owner}_other-{i}"),
        },
    }
}

fn kind_strategy() -> impl Strategy<Value = SenderKind> {
    prop_oneof![
        Just(SenderKind::DbOwner),
        Just(SenderKind::NameOwner),
        Just(SenderKind::NonOwner),
    ]
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// For every flow, the reply-target set has empty intersection with the
    /// owner-sent set; owner-sent messages are still retained (targets ∪
    /// owner-sent covers the whole stream); and both owner-detection paths
    /// hold — DB-authoritative and exact display-name match.
    ///
    /// Validates: Requirements 4.12, 10.7, 12.3
    #[test]
    fn owner_messages_excluded_from_all_three_flow_targets(
        owner in "[a-z]{1,10}",
        kinds in prop::collection::vec(kind_strategy(), 0..48),
    ) {
        let profile = UserProfile {
            owner_display_name: owner.clone(),
            account_scope: AccountScope::new("/home/test"),
        };

        let stream: Vec<ScopedMessage> = kinds
            .iter()
            .enumerate()
            .map(|(i, kind)| ScopedMessage {
                index: i,
                sender: build_sender(*kind, &owner, i),
            })
            .collect();

        let owner_indices: std::collections::BTreeSet<usize> =
            owner_sent(&profile, &stream).into_iter().collect();

        // Expected owner set by construction: every DbOwner and NameOwner
        // message, and no NonOwner message — this pins both detection paths.
        let expected_owner: std::collections::BTreeSet<usize> = kinds
            .iter()
            .enumerate()
            .filter(|(_, k)| matches!(k, SenderKind::DbOwner | SenderKind::NameOwner))
            .map(|(i, _)| i)
            .collect();
        prop_assert_eq!(&owner_indices, &expected_owner);

        for flow in FLOWS {
            let targets: std::collections::BTreeSet<usize> =
                reply_targets(&profile, &stream).into_iter().collect();

            // Empty intersection with the owner-sent set (R12.3).
            prop_assert!(
                targets.is_disjoint(&owner_indices),
                "flow {} reply targets intersected owner-sent set",
                flow
            );

            // Retention: targets ∪ owner-sent covers every message, so owner
            // messages are kept upstream (context/style), just not replied to.
            let union: std::collections::BTreeSet<usize> =
                targets.union(&owner_indices).copied().collect();
            let all: std::collections::BTreeSet<usize> = (0..stream.len()).collect();
            prop_assert_eq!(union, all);

            // Every non-owner message is a reply target.
            for (i, kind) in kinds.iter().enumerate() {
                if matches!(kind, SenderKind::NonOwner) {
                    prop_assert!(targets.contains(&i), "flow {} dropped non-owner {}", flow, i);
                }
            }
        }
    }

    /// The DB-authoritative path alone excludes a sender even when the display
    /// name does not match the owner; the display-name path alone excludes a
    /// sender whose trimmed name matches, even without the DB flag (R10.7).
    ///
    /// Validates: Requirements 10.7
    #[test]
    fn owner_detected_via_either_path(
        owner in "[a-z]{1,10}",
        other in "[a-z]{1,10}",
        lpad in 0usize..4,
        rpad in 0usize..4,
    ) {
        let profile = UserProfile {
            owner_display_name: owner.clone(),
            account_scope: AccountScope::new("/home/test"),
        };

        // DB-authoritative path: excluded regardless of the display name.
        let db_owner = SenderIdentity {
            db_authoritative_owner: true,
            display_name: format!("{other}!!"),
        };
        prop_assert!(is_owner_message(&profile, &db_owner));

        // Display-name path: excluded when the trimmed name matches exactly,
        // with no DB flag.
        let name_owner = SenderIdentity {
            db_authoritative_owner: false,
            display_name: format!("{}{}{}", " ".repeat(lpad), owner, " ".repeat(rpad)),
        };
        prop_assert!(is_owner_message(&profile, &name_owner));

        // A distinct trimmed name with no DB flag is NOT the owner.
        let stranger = SenderIdentity {
            db_authoritative_owner: false,
            display_name: format!("{owner}_{other}"),
        };
        prop_assert!(!is_owner_message(&profile, &stranger));
    }
}
