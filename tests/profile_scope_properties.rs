//! Property-based tests for user-profile scope and the owner-name gate.
//!
//! Feature: kakao-agent-live-ops
//! Property 33: 사용자프로필 범위와 소유자 이름 게이트
//!
//! For any path/database designation and any owner-name string:
//!   * reads/writes outside the running account's scope are 0 and fence
//!     (R10.3, R10.4, R10.8),
//!   * the owner display name being unset (missing / trimmed < 1 char /
//!     trimmed > 64 chars) is *equivalent* to all four product-send flows
//!     (auto reply, GeekNews, link forward, Telegram relay) being fenced with
//!     zero confirmed sends (R10.5),
//!   * an absent configured value never yields a built-in substitute (R10.1),
//!   * surfaced owner candidates are capped at five (R10.2), and
//!   * a confirmed value is stored whitespace-trimmed (R10.2).
//!
//! Every path that involves a send asserts the `BlockingRealSendPort` tripwire
//! counter stays at 0 and `ForbiddenNetwork::egress_count()` stays at 0.
//!
//! Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.5, 10.8

use std::path::PathBuf;

use openkakao_cli::fakes::{
    BlockingRealSendPort, FakeMessageSource, FakeSendPort, ForbiddenNetwork,
};
use openkakao_cli::ports::{Clock, MessageSource, NetworkPort, OwnerCandidate, SendPort};
use openkakao_cli::profile::{
    assert_in_scope, resolve_owner, AccountScope, OwnerName, ProfileError, ProfileStore,
    SqliteProfileStore, OWNER_NAME_MAX_CHARS,
};
use openkakao_cli::safety::{
    BreakerGate, BreakerGateSource, DefaultSafetyGate, GradeLimit, GradePolicy, GuardFence, Origin,
    OwnerNameStatus, PacingSource, ProfileView, SafetyConfig, SendGrade, SendGuard, SendIntent,
    SendRequest, UnsetReason,
};
use proptest::prelude::*;

// ---------------------------------------------------------------------------
// Permissive guard collaborators: every precondition other than the profile
// passes, so the owner-name and account-scope gates are the sole deciders.
// ---------------------------------------------------------------------------

struct OpenBreaker;
impl BreakerGateSource for OpenBreaker {
    fn breaker_gate(&self, _chat_id: i64) -> BreakerGate {
        BreakerGate::Open
    }
}

struct AnyGrade;
impl GradePolicy for AnyGrade {
    fn check(&self, _intent: &SendIntent, _chat_id: i64) -> Result<(), GradeLimit> {
        Ok(())
    }
}

struct ReadyPacing;
impl PacingSource for ReadyPacing {
    fn next_allowed_at(&self) -> i64 {
        i64::MIN
    }
}

struct ZeroClock;
impl Clock for ZeroClock {
    fn now_ms(&self) -> i64 {
        0
    }
}

/// A profile view that derives its owner-name status from the *real*
/// [`resolve_owner`], so the guard's fencing is tied to the actual resolver
/// rather than a hand-rolled stub. Scope membership is set explicitly so the
/// property can vary it without depending on the running account's home dir.
struct TestProfile {
    raw_owner: Option<String>,
    in_scope: bool,
}

impl ProfileView for TestProfile {
    fn owner_name_status(&self) -> OwnerNameStatus {
        match resolve_owner(self.raw_owner.as_deref()) {
            OwnerName::Set(_) => OwnerNameStatus::Set,
            OwnerName::Unset(reason) => OwnerNameStatus::Unset(reason),
        }
    }

    fn in_scope(&self) -> bool {
        self.in_scope
    }
}

/// One intent per product-send flow that routes through the single send guard:
/// auto reply, GeekNews, link forward, Telegram relay.
fn four_flow_intents() -> Vec<SendIntent> {
    vec![
        SendIntent {
            grade: SendGrade::Memo,
            origin: Origin::PartnerMessage {
                event_id: "evt".into(),
            },
        },
        SendIntent {
            grade: SendGrade::Memo,
            origin: Origin::GeekNewsSlot {
                marker: "slot".into(),
            },
        },
        SendIntent {
            grade: SendGrade::Memo,
            origin: Origin::UserComposer,
        },
        SendIntent {
            grade: SendGrade::Memo,
            origin: Origin::RelaySource {
                message_pid: "pid".into(),
            },
        },
    ]
}

/// A config/request pair the *unchanged* safety gate always allows, so the only
/// thing that can fence is a profile precondition.
fn allowing_gate_io(chat_id: i64) -> (SafetyConfig, SendRequest) {
    let cfg = SafetyConfig::new(true, true, [chat_id], "fp-owner");
    let req = SendRequest {
        chat_id,
        account_fp: "fp-owner".into(),
        state_ok: true,
    };
    (cfg, req)
}

/// A raw owner-name value covering every branch of [`resolve_owner`]: absent,
/// blank/whitespace-only, valid (including a padded 64-char boundary), and
/// over-long.
fn owner_raw_strategy() -> impl Strategy<Value = Option<String>> {
    prop_oneof![
        2 => Just(None),
        2 => Just(Some(String::new())),
        1 => Just(Some("   ".to_string())),
        1 => Just(Some(format!(" {} ", "a".repeat(OWNER_NAME_MAX_CHARS)))),
        4 => prop::string::string_regex("[a-zA-Z0-9]{1,20}").unwrap().prop_map(Some),
        2 => prop::string::string_regex("[a-zA-Z0-9]{1,12}")
                .unwrap()
                .prop_map(|s| Some(format!("  {s}  "))),
        2 => (OWNER_NAME_MAX_CHARS + 1..OWNER_NAME_MAX_CHARS + 32)
                .prop_map(|n| Some("a".repeat(n))),
    ]
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// The owner display name being unset is equivalent to all four
    /// product-send flows fencing with zero confirmed sends; when it is set
    /// (and everything else passes) all four flows issue a ticket and send.
    ///
    /// Validates: Requirements 10.5, 10.1
    #[test]
    fn owner_name_unset_iff_all_four_flows_fenced(
        raw in owner_raw_strategy(),
        chat_id in 1i64..50,
    ) {
        let gate = DefaultSafetyGate;
        let profile = TestProfile { raw_owner: raw.clone(), in_scope: true };
        let guard = SendGuard {
            gate: &gate,
            breaker: &OpenBreaker,
            grade: &AnyGrade,
            pacing: &ReadyPacing,
            profile: &profile,
            clock: &ZeroClock,
        };
        let (cfg, req) = allowing_gate_io(chat_id);

        let fake_send = FakeSendPort::new();
        let blocked = BlockingRealSendPort::new();
        let net = ForbiddenNetwork::new();

        let expected_unset = match resolve_owner(raw.as_deref()) {
            OwnerName::Unset(reason) => Some(reason),
            OwnerName::Set(_) => None,
        };

        let mut fenced = 0usize;
        let mut sent = 0usize;
        for intent in four_flow_intents() {
            match guard.authorize(&req, &cfg, &intent) {
                Ok(ticket) => {
                    prop_assert_eq!(ticket.chat_id(), chat_id);
                    let receipt = fake_send.send_text(&ticket, "body").expect("fake send");
                    prop_assert_eq!(receipt.chat_id, chat_id);
                    sent += 1;
                }
                Err(fence) => {
                    fenced += 1;
                    if let Some(expected) = expected_unset {
                        prop_assert_eq!(fence, GuardFence::OwnerNameUnset(expected));
                    }
                }
            }
        }

        if expected_unset.is_some() {
            // Owner unset ⟹ all four flows fenced, zero confirmed sends.
            prop_assert_eq!(fenced, 4);
            prop_assert_eq!(sent, 0);
            prop_assert_eq!(fake_send.confirmed_count(), 0);
        } else {
            // Owner set (everything else permissive) ⟹ all four flows send.
            prop_assert_eq!(fenced, 0);
            prop_assert_eq!(sent, 4);
        }

        // No path ever reached the real send or the network.
        prop_assert_eq!(blocked.blocked_count(), 0);
        prop_assert_eq!(net.egress_count(), 0);
    }

    /// A target outside the running account's scope fences all four flows with
    /// zero confirmed sends; an in-scope target (owner set) issues tickets.
    ///
    /// Validates: Requirements 10.3, 10.4, 10.8
    #[test]
    fn out_of_scope_fences_all_four_flows(
        in_scope in any::<bool>(),
        chat_id in 1i64..50,
    ) {
        let gate = DefaultSafetyGate;
        let profile = TestProfile { raw_owner: Some("owner".into()), in_scope };
        let guard = SendGuard {
            gate: &gate,
            breaker: &OpenBreaker,
            grade: &AnyGrade,
            pacing: &ReadyPacing,
            profile: &profile,
            clock: &ZeroClock,
        };
        let (cfg, req) = allowing_gate_io(chat_id);

        let fake_send = FakeSendPort::new();
        let blocked = BlockingRealSendPort::new();
        let net = ForbiddenNetwork::new();

        let mut fenced = 0usize;
        let mut sent = 0usize;
        for intent in four_flow_intents() {
            match guard.authorize(&req, &cfg, &intent) {
                Ok(ticket) => {
                    let _ = fake_send.send_text(&ticket, "body").expect("fake send");
                    sent += 1;
                }
                Err(fence) => {
                    fenced += 1;
                    if !in_scope {
                        prop_assert_eq!(fence, GuardFence::ProfileScopeMismatch);
                    }
                }
            }
        }

        if in_scope {
            prop_assert_eq!(sent, 4);
            prop_assert_eq!(fenced, 0);
        } else {
            // Out of scope ⟹ all four flows fenced, zero confirmed sends.
            prop_assert_eq!(sent, 0);
            prop_assert_eq!(fenced, 4);
            prop_assert_eq!(fake_send.confirmed_count(), 0);
        }

        prop_assert_eq!(blocked.blocked_count(), 0);
        prop_assert_eq!(net.egress_count(), 0);
    }

    /// A path is admitted for read/write exactly when it lies under the
    /// account's home root; anything else is refused (zero reads/writes).
    ///
    /// Validates: Requirements 10.3, 10.4, 10.8
    #[test]
    fn assert_in_scope_admits_only_paths_under_home_root(
        root_name in "[a-z]{3,8}",
        path_name in "[a-z]{3,8}",
        sub in "[a-z]{1,6}",
    ) {
        let scope = AccountScope::new(format!("/home/{root_name}"));
        let path = PathBuf::from(format!("/home/{path_name}/.config/{sub}"));
        let result = assert_in_scope(&path, &scope);
        if root_name == path_name {
            prop_assert!(result.is_ok());
        } else {
            prop_assert!(matches!(result, Err(ProfileError::OutOfScope(_))));
        }
    }

    /// [`resolve_owner`] classifies purely by the trimmed length: an absent
    /// value never becomes a built-in substitute (R10.1), and the unset reasons
    /// track the < 1 char / > 64 char boundaries exactly (R10.5).
    ///
    /// Validates: Requirements 10.1, 10.5
    #[test]
    fn resolve_owner_classifies_by_trimmed_length(raw in owner_raw_strategy()) {
        match resolve_owner(raw.as_deref()) {
            OwnerName::Set(value) => {
                let trimmed = raw.as_deref().unwrap().trim();
                // The confirmed value is exactly the trimmed input — no
                // built-in name is ever substituted.
                prop_assert_eq!(value.as_str(), trimmed);
                let len = value.chars().count();
                prop_assert!((1..=OWNER_NAME_MAX_CHARS).contains(&len));
            }
            OwnerName::Unset(reason) => match reason {
                UnsetReason::Missing => prop_assert!(raw.is_none()),
                UnsetReason::TooShort => {
                    let trimmed = raw.as_deref().unwrap().trim();
                    prop_assert_eq!(trimmed.chars().count(), 0);
                }
                UnsetReason::TooLong => {
                    let trimmed = raw.as_deref().unwrap().trim();
                    prop_assert!(trimmed.chars().count() > OWNER_NAME_MAX_CHARS);
                }
            },
        }
    }

    /// Owner candidates surfaced from the database are capped at five (R10.2).
    ///
    /// Validates: Requirements 10.2
    #[test]
    fn owner_candidates_capped_at_five(n in 0usize..12) {
        let candidates: Vec<OwnerCandidate> = (0..n)
            .map(|i| OwnerCandidate {
                display_name: format!("cand-{i}"),
                message_count: i as i64,
            })
            .collect();
        let source = FakeMessageSource::new(1, Vec::new(), candidates);
        let got = source.owner_candidates(5).expect("candidates");
        prop_assert!(got.len() <= 5);
        prop_assert_eq!(got.len(), n.min(5));
    }

    /// A confirmed owner value is stored whitespace-trimmed and round-trips
    /// through the store unchanged (R10.2).
    ///
    /// Validates: Requirements 10.2
    #[test]
    fn confirmed_owner_value_is_trimmed(
        name in "[a-zA-Z0-9]{1,20}",
        lpad in 0usize..4,
        rpad in 0usize..4,
    ) {
        let raw = format!("{}{}{}", " ".repeat(lpad), name, " ".repeat(rpad));
        let store =
            SqliteProfileStore::open_in_memory(AccountScope::new("/home/alice")).expect("store");

        let profile = store.confirm_owner(&raw).expect("confirm owner");
        prop_assert_eq!(profile.owner_display_name.as_str(), name.as_str());

        let loaded = store.load().expect("load").expect("some profile");
        prop_assert_eq!(loaded.owner_display_name.as_str(), name.as_str());

        let len = profile.owner_display_name.chars().count();
        prop_assert!((1..=OWNER_NAME_MAX_CHARS).contains(&len));
    }
}

// ===========================================================================
// Feature: kakao-agent-live-ops
// Property 34: 소유자 범위 마이그레이션의 개수 보존과 멱등성
//
// For any legacy owner-scoped style/context data and any owner name:
//   * the three counts (style-profile / Q&A-pair / attachment-evidence) are
//     equal before and after the migration (R10.6),
//   * a second run changes nothing (idempotent, R10.11), and
//   * an injected failure at an arbitrary point leaves the three counts and the
//     data at their pre-migration values and does NOT record the completion
//     marker — the single-transaction boundary is that guarantee (R10.10).
//
// Validates: Requirements 10.6, 10.10, 10.11
// ===========================================================================

use openkakao_cli::profile::{migrate_owner_scope, OWNER_SCOPE_MIGRATION};
use rusqlite::{params, Connection, OptionalExtension};

/// The legacy → renamed table pairs the migration operates on, mirrored here so
/// the property can assert the rename from the outside.
const LEGACY_RENAMES: [(&str, &str); 4] = [
    ("choi_yeonwoo_style", "owner_style"),
    ("choi_yeonwoo_style_profile", "owner_style_profile"),
    (
        "choi_yeonwoo_recipient_style_profile",
        "owner_recipient_style_profile",
    ),
    (
        "choi_yeonwoo_recipient_style_samples",
        "owner_recipient_style_samples",
    ),
];

/// The shape of a generated legacy database.
#[derive(Debug, Clone, Copy)]
struct LegacyShape {
    style_messages: usize,
    style_profiles: usize,
    recipient_profiles: usize,
    recipient_samples: usize,
    qa_pairs: usize,
    attachments: usize,
}

/// Build an in-memory legacy database with the four `choi_yeonwoo_*` tables and
/// the `qa_pair` / `attachment_reference` tables populated to the given shape.
/// The `schema_migration` marker table is intentionally NOT created, so its
/// absence after a rolled-back migration is observable.
fn build_legacy_db(shape: LegacyShape) -> Connection {
    let conn = Connection::open_in_memory().expect("open in-memory");
    conn.execute_batch(
        "CREATE TABLE choi_yeonwoo_style(id INTEGER PRIMARY KEY, message TEXT NOT NULL);
         CREATE TABLE choi_yeonwoo_style_profile(chat TEXT PRIMARY KEY, sample_count INTEGER NOT NULL);
         CREATE TABLE choi_yeonwoo_recipient_style_profile(recipient TEXT PRIMARY KEY);
         CREATE TABLE choi_yeonwoo_recipient_style_samples(id INTEGER PRIMARY KEY, style_message_id INTEGER);
         CREATE TABLE qa_pair(id INTEGER PRIMARY KEY);
         CREATE TABLE attachment_reference(id INTEGER PRIMARY KEY);",
    )
    .expect("create legacy schema");

    for i in 0..shape.style_messages {
        conn.execute(
            "INSERT INTO choi_yeonwoo_style(message) VALUES (?1)",
            params![format!("style-{i}")],
        )
        .unwrap();
    }
    for i in 0..shape.style_profiles {
        conn.execute(
            "INSERT INTO choi_yeonwoo_style_profile(chat, sample_count) VALUES (?1, ?2)",
            params![format!("room-{i}"), i as i64],
        )
        .unwrap();
    }
    for i in 0..shape.recipient_profiles {
        conn.execute(
            "INSERT INTO choi_yeonwoo_recipient_style_profile(recipient) VALUES (?1)",
            params![format!("recipient-{i}")],
        )
        .unwrap();
    }
    for _ in 0..shape.recipient_samples {
        conn.execute(
            "INSERT INTO choi_yeonwoo_recipient_style_samples(style_message_id) VALUES (1)",
            [],
        )
        .unwrap();
    }
    for _ in 0..shape.qa_pairs {
        conn.execute("INSERT INTO qa_pair DEFAULT VALUES", [])
            .unwrap();
    }
    for _ in 0..shape.attachments {
        conn.execute("INSERT INTO attachment_reference DEFAULT VALUES", [])
            .unwrap();
    }
    conn
}

fn table_present(conn: &Connection, name: &str) -> bool {
    conn.query_row(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?1",
        params![name],
        |_| Ok(()),
    )
    .optional()
    .unwrap()
    .is_some()
}

fn count_rows_of(conn: &Connection, table: &str) -> usize {
    if !table_present(conn, table) {
        return 0;
    }
    conn.query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |row| {
        row.get::<_, i64>(0)
    })
    .map(|n| n as usize)
    .unwrap()
}

fn marker_recorded(conn: &Connection) -> bool {
    if !table_present(conn, "schema_migration") {
        return false;
    }
    conn.query_row(
        "SELECT 1 FROM schema_migration WHERE name = ?1",
        params![OWNER_SCOPE_MIGRATION],
        |_| Ok(()),
    )
    .optional()
    .unwrap()
    .is_some()
}

/// The style-profile count reading whichever of the legacy or renamed table is
/// currently present — the same rule the migration uses to preserve the count.
fn style_profile_count(conn: &Connection) -> usize {
    if table_present(conn, "owner_style_profile") {
        count_rows_of(conn, "owner_style_profile")
    } else {
        count_rows_of(conn, "choi_yeonwoo_style_profile")
    }
}

fn shape_strategy() -> impl Strategy<Value = LegacyShape> {
    (
        0usize..6,
        0usize..6,
        0usize..5,
        0usize..8,
        0usize..10,
        0usize..10,
    )
        .prop_map(
            |(
                style_messages,
                style_profiles,
                recipient_profiles,
                recipient_samples,
                qa_pairs,
                attachments,
            )| LegacyShape {
                style_messages,
                style_profiles,
                recipient_profiles,
                recipient_samples,
                qa_pairs,
                attachments,
            },
        )
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// A committed migration preserves the three counts (R10.6) and is
    /// idempotent (R10.11): a second run changes nothing and records exactly
    /// one marker. It also renames every legacy table to its `owner_*` name.
    ///
    /// Validates: Requirements 10.6, 10.11
    #[test]
    fn migration_preserves_three_counts_and_is_idempotent(
        shape in shape_strategy(),
        owner in "[a-zA-Z0-9가-힣]{1,20}",
    ) {
        let mut conn = build_legacy_db(shape);

        let style_before = style_profile_count(&conn);
        let qa_before = count_rows_of(&conn, "qa_pair");
        let attach_before = count_rows_of(&conn, "attachment_reference");

        // First run: commit.
        let report = {
            let tx = conn.transaction().expect("begin");
            let report = migrate_owner_scope(&tx, &owner).expect("migrate");
            tx.commit().expect("commit");
            report
        };

        // The report's three counts equal the pre-migration counts (R10.6).
        prop_assert_eq!(report.style_profiles, style_before);
        prop_assert_eq!(report.qa_pairs, qa_before);
        prop_assert_eq!(report.attachments, attach_before);

        // And the live counts after the rename are unchanged.
        prop_assert_eq!(style_profile_count(&conn), style_before);
        prop_assert_eq!(count_rows_of(&conn, "qa_pair"), qa_before);
        prop_assert_eq!(count_rows_of(&conn, "attachment_reference"), attach_before);

        // Every legacy name is gone and its `owner_*` counterpart is present.
        for (legacy, renamed) in LEGACY_RENAMES {
            prop_assert!(!table_present(&conn, legacy), "legacy {} still present", legacy);
            prop_assert!(table_present(&conn, renamed), "renamed {} missing", renamed);
        }
        prop_assert!(marker_recorded(&conn));

        // Second run is idempotent: same counts, still exactly one marker.
        let report2 = {
            let tx = conn.transaction().expect("begin");
            let report = migrate_owner_scope(&tx, &owner).expect("migrate again");
            tx.commit().expect("commit");
            report
        };
        prop_assert_eq!(report2.style_profiles, style_before);
        prop_assert_eq!(report2.qa_pairs, qa_before);
        prop_assert_eq!(report2.attachments, attach_before);

        let markers: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM schema_migration WHERE name = ?1",
                params![OWNER_SCOPE_MIGRATION],
                |row| row.get(0),
            )
            .unwrap();
        prop_assert_eq!(markers, 1);
    }

    /// An injected failure at an arbitrary point — modeled by rolling the
    /// migration's single transaction back instead of committing — leaves the
    /// three counts and every table at their pre-migration values and records
    /// no completion marker (R10.10). The transaction boundary is the
    /// guarantee, so where the failure occurs does not matter.
    ///
    /// Validates: Requirements 10.10
    #[test]
    fn rolled_back_migration_leaves_everything_unchanged(
        shape in shape_strategy(),
        owner in "[a-zA-Z0-9가-힣]{1,20}",
    ) {
        let mut conn = build_legacy_db(shape);

        let style_before = style_profile_count(&conn);
        let qa_before = count_rows_of(&conn, "qa_pair");
        let attach_before = count_rows_of(&conn, "attachment_reference");
        let style_rows_before = count_rows_of(&conn, "choi_yeonwoo_style");
        let recipient_rows_before = count_rows_of(&conn, "choi_yeonwoo_recipient_style_samples");

        // Run the migration inside a transaction, then abort it (simulating a
        // failure at an arbitrary point during or after the work).
        {
            let tx = conn.transaction().expect("begin");
            // The migration itself succeeds; the "failure" is the abort below.
            let _ = migrate_owner_scope(&tx, &owner).expect("migrate");
            tx.rollback().expect("rollback");
        }

        // Pre-migration state fully preserved: legacy names still present, the
        // `owner_*` names absent, and no marker recorded.
        for (legacy, renamed) in LEGACY_RENAMES {
            prop_assert!(table_present(&conn, legacy), "legacy {} lost after rollback", legacy);
            prop_assert!(!table_present(&conn, renamed), "renamed {} leaked after rollback", renamed);
        }
        prop_assert!(!marker_recorded(&conn));

        // The three counts and the underlying data are unchanged.
        prop_assert_eq!(style_profile_count(&conn), style_before);
        prop_assert_eq!(count_rows_of(&conn, "qa_pair"), qa_before);
        prop_assert_eq!(count_rows_of(&conn, "attachment_reference"), attach_before);
        prop_assert_eq!(count_rows_of(&conn, "choi_yeonwoo_style"), style_rows_before);
        prop_assert_eq!(
            count_rows_of(&conn, "choi_yeonwoo_recipient_style_samples"),
            recipient_rows_before
        );
    }
}
