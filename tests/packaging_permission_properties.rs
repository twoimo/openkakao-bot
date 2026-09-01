//! Property-based tests for the app packager and permission→capability map
//! (task 10.1).
//!
//! Feature: kakao-agent-live-ops
//! Property 35: 권한 → 기능 매핑의 분할 성질
//!   (permission → capability partition)
//! Property 36: 서명·공증·스테이플 모두 성공 ⇔ 산출물 생성
//!   (signing / notarize / staple all-success ⇔ artifact)
//!
//! Property 35 drives [`capabilities`] over the eight exhaustive
//! [`Permission`] grant combinations and pins:
//!   * `available` and `blocked` form a *partition* of the full capability set
//!     — every capability appears exactly once, and the union is
//!     `Capability::ALL` (disjoint, union = all) (R9.11),
//!   * each `blocked` entry names the required [`Permission`] and a non-empty
//!     unblock method (R9.8, R9.9, R9.10, R9.11),
//!   * each of the three permissions blocks *exactly* its documented scope —
//!     Accessibility → AX send + Telegram read (R9.8), FullDiskAccess →
//!     the local-DB features (R9.9), ScreenRecording → the screen-capture
//!     path (R9.10),
//!   * the permissions that can ever be requested / displayed are a subset of
//!     the three required permissions (R9.7), and
//!   * the app keeps running in every combination — at least one capability is
//!     always available (R9.13).
//!
//! Property 36 drives [`package`] with a fake [`SigningTools`] (no real
//! certificate) over arbitrary per-stage success/failure combinations and
//! pins:
//!   * an artifact is produced iff all three stages (codesign → notarize →
//!     staple) succeed (R9.3), and a successful report carries exactly one
//!     artifact with zero extra installer files and zero setup commands (R9.1),
//!   * if any stage fails, zero artifacts are produced — the DMG builder is
//!     never called — and the failure carries the failing stage, its cause,
//!     and a next action in plain language (R9.5).
//!
//! No real KakaoTalk send or network egress is possible here: `capabilities`
//! is a pure function over a permission set, and `package` only ever calls the
//! injected in-memory [`SigningTools`], which fabricates verdicts and never
//! touches a signing certificate, a send port, or the network (R9.5).
//!
//! Validates: Requirements 9.1, 9.3, 9.5, 9.7, 9.8, 9.9, 9.10, 9.11, 9.13

use std::cell::Cell;
use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use openkakao_cli::packaging::{
    capabilities, package, BundlePlan, Capability, PackageError, Permission, PermissionState,
    SignStage, SigningTools, SigningVerdict,
};
use proptest::prelude::*;

// ===========================================================================
// Shared fixtures
// ===========================================================================

/// The three required permissions in a fixed order, so bit-indexed combos line
/// up (R9.7).
const PERMISSIONS: [Permission; 3] = [
    Permission::Accessibility,
    Permission::FullDiskAccess,
    Permission::ScreenRecording,
];

/// The three signing stages in the order [`package`] runs them.
const STAGES: [SignStage; 3] = [SignStage::Codesign, SignStage::Notarize, SignStage::Staple];

/// The documented block scope of each permission (R9.8/9.9/9.10). Denying the
/// permission must block *exactly* this set of capabilities and nothing else.
fn documented_scope(permission: Permission) -> BTreeSet<Capability> {
    match permission {
        // 손쉬운 사용 → AX 전송·텔레그램 읽기만 (R9.8).
        Permission::Accessibility => {
            BTreeSet::from([Capability::AxSend, Capability::TelegramRead])
        }
        // 전체 디스크 접근 → 로컬 DB 의존 기능만 (R9.9).
        Permission::FullDiskAccess => BTreeSet::from([
            Capability::LocalRead,
            Capability::ContextSearch,
            Capability::DatasetRefresh,
        ]),
        // 화면 기록 → 화면 캡처 경로만 (R9.10).
        Permission::ScreenRecording => BTreeSet::from([Capability::ScreenCaptureImages]),
    }
}

/// Build a [`PermissionState`] from a 3-bit combo index (bit 0 = Accessibility,
/// bit 1 = FullDiskAccess, bit 2 = ScreenRecording).
fn state_from_bits(bits: u8) -> PermissionState {
    let mut granted = Vec::new();
    for (i, &permission) in PERMISSIONS.iter().enumerate() {
        if bits & (1 << i) != 0 {
            granted.push(permission);
        }
    }
    PermissionState::from_granted(granted)
}

/// Every capability that `capabilities` reports as blocked, as a set.
fn blocked_set(map: &openkakao_cli::packaging::CapabilityMap) -> BTreeSet<Capability> {
    map.blocked.iter().map(|(c, _, _)| *c).collect()
}

/// Assert the core partition + scope invariants for one permission state
/// (R9.8/9.9/9.10/9.11/9.13). Shared by the exhaustive and randomized cases.
fn assert_partition_invariants(state: &PermissionState) {
    let map = capabilities(state);

    // available ∪ blocked covers exactly Capability::ALL, with no capability in
    // both halves (disjoint, union = all) (R9.11).
    let mut seen: BTreeSet<Capability> = BTreeSet::new();
    for &c in &map.available {
        assert!(seen.insert(c), "capability {c:?} appeared twice in available");
    }
    for (c, _perm, _how) in &map.blocked {
        assert!(seen.insert(*c), "capability {c:?} appeared in both halves");
    }
    let expected: BTreeSet<Capability> = Capability::ALL.into_iter().collect();
    assert_eq!(seen, expected, "partition must cover exactly all features");

    // Each blocked entry names the required permission and a non-empty unblock
    // method, and that permission is genuinely denied (R9.11).
    for (cap, perm, how) in &map.blocked {
        assert_eq!(
            cap.required_permission(),
            Some(*perm),
            "blocked capability {cap:?} must name its own required permission",
        );
        assert!(!state.is_granted(*perm), "blocked permission must be denied");
        assert!(!how.is_empty(), "unblock method must be plain language, not empty");
    }

    // Each denied permission blocks exactly its documented scope; each granted
    // permission blocks none of its scope (R9.8/9.9/9.10).
    let blocked = blocked_set(&map);
    for &permission in &PERMISSIONS {
        let scope = documented_scope(permission);
        if state.is_granted(permission) {
            assert!(
                blocked.is_disjoint(&scope),
                "granted {permission:?} must not block any of its scope",
            );
        } else {
            for cap in &scope {
                assert!(
                    blocked.contains(cap),
                    "denied {permission:?} must block {cap:?}",
                );
            }
        }
    }

    // The permissions that can be requested / displayed (those surfaced on
    // blocked entries) are a subset of the three required permissions (R9.7).
    let all_permissions: BTreeSet<Permission> = Permission::ALL.into_iter().collect();
    for (_cap, perm, _how) in &map.blocked {
        assert!(all_permissions.contains(perm), "displayed permission {perm:?} is off-list");
    }
    for p in state.granted() {
        assert!(all_permissions.contains(&p), "granted permission {p:?} is off-list");
    }

    // The app keeps running in every combination (R9.13).
    assert!(map.keeps_running(), "at least one capability must stay available");
}

// ---------------------------------------------------------------------------
// Fake signing tools (no real certificate, R9.5)
// ---------------------------------------------------------------------------

/// Returns the injected per-stage verdicts and counts how many times the DMG
/// builder runs, so a failure can be shown to produce zero artifacts.
struct FakeSigningTools {
    codesign: SigningVerdict,
    notarize: SigningVerdict,
    staple: SigningVerdict,
    dmg_calls: Cell<usize>,
}

impl FakeSigningTools {
    /// Build from three success/failure flags (`true` = the stage succeeds).
    fn from_flags(ok: [bool; 3]) -> Self {
        let verdict = |i: usize| {
            if ok[i] {
                SigningVerdict::ok("TEAM123")
            } else {
                SigningVerdict::failed(STAGES[i], format!("가짜 실패: {}", STAGES[i].as_str()))
            }
        };
        Self {
            codesign: verdict(0),
            notarize: verdict(1),
            staple: verdict(2),
            dmg_calls: Cell::new(0),
        }
    }
}

impl SigningTools for FakeSigningTools {
    fn codesign(&self, _bundle: &Path) -> SigningVerdict {
        self.codesign.clone()
    }

    fn notarize(&self, _bundle: &Path) -> SigningVerdict {
        self.notarize.clone()
    }

    fn staple(&self, _bundle: &Path) -> SigningVerdict {
        self.staple.clone()
    }

    fn make_dmg(&self, _bundle: &Path, out: &Path) -> Result<PathBuf, PackageError> {
        self.dmg_calls.set(self.dmg_calls.get() + 1);
        Ok(out.to_path_buf())
    }
}

fn plan() -> BundlePlan {
    BundlePlan::new("AutoReplyMenu", "/tmp/core", "/tmp/shell", "/tmp/out")
}

/// The first stage index that fails, if any (matching `package`'s ordering).
fn first_failed(ok: [bool; 3]) -> Option<usize> {
    (0..3).find(|&i| !ok[i])
}

/// Assert the packaging outcome invariants for one success/failure flag set
/// (R9.1, R9.3, R9.5). Shared by the exhaustive and randomized cases.
fn assert_packaging_invariants(ok: [bool; 3]) {
    let tools = FakeSigningTools::from_flags(ok);
    let result = package(&plan(), &tools);

    match first_failed(ok) {
        None => {
            // All three stages succeeded → exactly one artifact (R9.3).
            let report = result.expect("all-ok must package");
            assert!(report.codesign.is_ok());
            assert!(report.notarize.is_ok());
            assert!(report.staple.is_ok());
            assert_eq!(report.artifacts, 1, "success produces exactly one artifact");
            // A successful artifact has zero extra installers and setup
            // commands (R9.1).
            assert_eq!(report.extra_installers, 0);
            assert_eq!(report.setup_commands, 0);
            assert_eq!(report.dmg, plan().dmg_path());
            assert_eq!(tools.dmg_calls.get(), 1, "DMG built exactly once on success");
        }
        Some(idx) => {
            // Any stage failing → zero artifacts + failure info (R9.5).
            let err = result.expect_err("any stage failure must fail packaging");
            match err {
                PackageError::SigningFailed {
                    stage,
                    reason,
                    next_step,
                } => {
                    // The failure names the *first* failing stage in order.
                    assert_eq!(stage, STAGES[idx], "must report the first failing stage");
                    assert!(!reason.is_empty(), "failure must carry a cause");
                    assert!(!next_step.is_empty(), "failure must carry a next action");
                }
                other => panic!("expected signing failure, got {other:?}"),
            }
            // Zero artifacts: the DMG builder is never called (R9.5).
            assert_eq!(tools.dmg_calls.get(), 0, "no DMG on any stage failure");
        }
    }
}

// ===========================================================================
// Property 35 — permission → capability partition (exhaustive, R9.7..R9.13)
// ===========================================================================

/// Feature: kakao-agent-live-ops, Property 35 (permission → capability partition)
/// Validates: Requirements 9.7, 9.8, 9.9, 9.10, 9.11, 9.13
///
/// Exhaustive over the eight permission grant combinations.
#[test]
fn capabilities_partition_over_all_eight_combos() {
    for bits in 0u8..8 {
        assert_partition_invariants(&state_from_bits(bits));
    }
}

/// Feature: kakao-agent-live-ops, Property 35 (documented block scope)
/// Validates: Requirements 9.8, 9.9, 9.10
///
/// Denying a single permission (with the other two granted) blocks exactly its
/// documented scope and nothing else.
#[test]
fn single_permission_denial_blocks_exactly_its_scope() {
    for &denied in &PERMISSIONS {
        let granted: Vec<Permission> =
            PERMISSIONS.iter().copied().filter(|p| *p != denied).collect();
        let state = PermissionState::from_granted(granted);
        let map = capabilities(&state);
        assert_eq!(
            blocked_set(&map),
            documented_scope(denied),
            "denying {denied:?} must block exactly its documented scope",
        );
    }
}

// ===========================================================================
// Property 35 — permission → capability partition (randomized, R9.7..R9.13)
// ===========================================================================

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// Feature: kakao-agent-live-ops, Property 35 (partition, randomized)
    /// Validates: Requirements 9.7, 9.8, 9.9, 9.10, 9.11, 9.13
    ///
    /// Randomizing the three grant bits reaches every combination and pins the
    /// partition, scope, subset, and keeps-running invariants on each draw.
    #[test]
    fn capabilities_partition_holds_for_random_grants(
        a in any::<bool>(),
        f in any::<bool>(),
        s in any::<bool>(),
    ) {
        let bits = (a as u8) | ((f as u8) << 1) | ((s as u8) << 2);
        assert_partition_invariants(&state_from_bits(bits));
    }
}

// ===========================================================================
// Property 36 — signing all-success ⇔ artifact (exhaustive, R9.1/9.3/9.5)
// ===========================================================================

/// Feature: kakao-agent-live-ops, Property 36 (all-success ⇔ artifact)
/// Validates: Requirements 9.1, 9.3, 9.5
///
/// Exhaustive over the eight per-stage success/failure combinations.
#[test]
fn package_artifact_iff_all_stages_succeed_over_all_combos() {
    for bits in 0u8..8 {
        let ok = [
            bits & 0b001 != 0,
            bits & 0b010 != 0,
            bits & 0b100 != 0,
        ];
        assert_packaging_invariants(ok);
    }
}

// ===========================================================================
// Property 36 — signing all-success ⇔ artifact (randomized, R9.1/9.3/9.5)
// ===========================================================================

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// Feature: kakao-agent-live-ops, Property 36 (all-success ⇔ artifact, random)
    /// Validates: Requirements 9.1, 9.3, 9.5
    ///
    /// Random per-stage success/failure flags exercise the iff: an artifact
    /// appears exactly when all three stages pass, and any failure yields zero
    /// artifacts plus failure info naming the first failing stage. A fake
    /// [`SigningTools`] injects verdicts, so no real certificate is involved.
    #[test]
    fn package_artifact_iff_all_stages_succeed_random(
        c in any::<bool>(),
        n in any::<bool>(),
        s in any::<bool>(),
    ) {
        assert_packaging_invariants([c, n, s]);
    }
}
