//! 배포패키저와 권한→기능 매핑 (task 10, R9).
//!
//! 이 모듈은 두 가지 순수한 결정 로직을 코어에 둔다. Swift 셸은 이 결과를
//! *그리기만* 하므로, 배포 산출물 규칙과 권한별 차단 범위가 인증서 없이 `cargo
//! test`로 검증된다.
//!
//! * **서명·공증을 통과한 산출물만 (R9.1, R9.3, R9.4, R9.5).** [`package`]는
//!   서명(codesign) → 공증(notarize) → 스테이플(staple) 세 단계를 순서대로
//!   실행하고, **세 단계가 모두 성공한 경우에만** 배포 이미지(DMG)를 만든다.
//!   한 단계라도 실패하면 산출물을 만들지 않고([`SigningTools::make_dmg`]를
//!   호출하지 않고) 실패 단계·원인·다음에 할 일을 담은
//!   [`PackageError::SigningFailed`]를 낸다. 성공한 산출물의 추가 설치 파일
//!   수([`PackageReport::extra_installers`])와 실행해야 하는 설치 명령
//!   수([`PackageReport::setup_commands`])는 항상 0이다.
//! * **권한 → 기능 매핑의 분할 성질 (R9.7, R9.8, R9.9, R9.10, R9.11, R9.13).**
//!   [`capabilities`]는 세 [`Permission`] 허용 상태를 받아, 전체 기능을 사용
//!   가능([`CapabilityMap::available`])과 차단([`CapabilityMap::blocked`])으로
//!   *분할*한다(합집합이 전체, 교집합이 공집합). 손쉬운 사용 거부는 AX 전송·
//!   텔레그램 읽기만, 전체 디스크 접근 거부는 로컬 DB 의존 기능만, 화면 기록
//!   거부는 화면 캡처 경로만 차단한다. 어떤 조합에서도 최소 한 기능은 사용
//!   가능하므로 앱은 항상 실행을 유지한다.
//!
//! 서명 도구는 [`SigningTools`] 트레이트 뒤에 두어, 테스트가 가짜 구현을 주입해
//! **실제 인증서 없이** "실패 시 산출물 0건"과 "세 단계 성공 시에만 산출물 생성"을
//! 검증할 수 있다.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use thiserror::Error;

// ---------------------------------------------------------------------------
// Bundle plan
// ---------------------------------------------------------------------------

/// 배포 계획: Swift 셸과 Rust 코어를 하나의 `.app` 번들로 묶기 위한 입력 (R9.1).
///
/// `.app` 조립 자체는 `scripts/package-app.sh`가 수행하고, 이 계획은 조립된
/// 번들과 산출물의 위치를 [`package`]에 알려 준다.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BundlePlan {
    /// 번들 이름(확장자 제외). 예: `AutoReplyMenu`.
    pub app_name: String,
    /// `.app` 안에 넣을 Rust 코어 실행 파일 경로.
    pub core_binary: PathBuf,
    /// `.app` 안에 넣을 Swift 셸 실행 파일 경로.
    pub shell_binary: PathBuf,
    /// `.app`과 `.dmg`를 놓을 출력 디렉터리.
    pub out_dir: PathBuf,
}

impl BundlePlan {
    /// 계획을 만든다.
    pub fn new(
        app_name: impl Into<String>,
        core_binary: impl Into<PathBuf>,
        shell_binary: impl Into<PathBuf>,
        out_dir: impl Into<PathBuf>,
    ) -> Self {
        Self {
            app_name: app_name.into(),
            core_binary: core_binary.into(),
            shell_binary: shell_binary.into(),
            out_dir: out_dir.into(),
        }
    }

    /// 조립된 `.app` 번들의 경로.
    pub fn bundle_path(&self) -> PathBuf {
        self.out_dir.join(format!("{}.app", self.app_name))
    }

    /// 만들 배포 이미지(DMG)의 경로 (R9.4).
    pub fn dmg_path(&self) -> PathBuf {
        self.out_dir.join(format!("{}.dmg", self.app_name))
    }
}

// ---------------------------------------------------------------------------
// Signing stages and verdicts
// ---------------------------------------------------------------------------

/// 배포 산출물이 통과해야 하는 세 서명 단계 (R9.3, R9.5).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum SignStage {
    /// 코드 서명.
    Codesign,
    /// 공증(notarization).
    Notarize,
    /// 공증 스테이플.
    Staple,
}

impl SignStage {
    /// 짧고 안정적인 코드.
    pub fn as_str(self) -> &'static str {
        match self {
            SignStage::Codesign => "codesign",
            SignStage::Notarize => "notarize",
            SignStage::Staple => "staple",
        }
    }

    /// 이 단계가 실패했을 때 사용자가 다음에 할 일 (쉬운말, R9.5).
    fn next_step(self) -> &'static str {
        match self {
            SignStage::Codesign => "서명 인증서를 확인한 뒤 다시 실행해 주세요.",
            SignStage::Notarize => {
                "공증(notarization) 설정과 Apple 계정 자격 증명을 확인한 뒤 다시 실행해 주세요."
            }
            SignStage::Staple => "공증 스테이플 단계를 확인한 뒤 다시 실행해 주세요.",
        }
    }
}

/// 한 서명 단계의 판정 결과.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SigningVerdict {
    /// 성공. 서명에 사용한 팀 식별자를 담는다.
    Ok {
        /// 서명 팀 식별자.
        team_id: String,
    },
    /// 실패. 실패한 단계와 원인을 담는다 (R9.5).
    Failed {
        /// 실패한 단계.
        stage: SignStage,
        /// 실패 원인(쉬운말). 절대 경로·계정 식별정보를 담지 않는다.
        reason: String,
    },
}

impl SigningVerdict {
    /// 성공 판정을 만든다.
    pub fn ok(team_id: impl Into<String>) -> Self {
        SigningVerdict::Ok {
            team_id: team_id.into(),
        }
    }

    /// 실패 판정을 만든다.
    pub fn failed(stage: SignStage, reason: impl Into<String>) -> Self {
        SigningVerdict::Failed {
            stage,
            reason: reason.into(),
        }
    }

    /// 성공 여부.
    pub fn is_ok(&self) -> bool {
        matches!(self, SigningVerdict::Ok { .. })
    }
}

// ---------------------------------------------------------------------------
// Report and error
// ---------------------------------------------------------------------------

/// 배포 산출물 생성 보고. 세 단계가 모두 성공했을 때만 만들어진다 (R9.3, R9.4).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PackageReport {
    /// 서명·공증을 통과한 `.app` 번들 경로.
    pub bundle: PathBuf,
    /// 번들 1개와 설치 위치 1개를 담은 배포 이미지(DMG) 경로 (R9.4).
    pub dmg: PathBuf,
    /// 서명 단계 판정 (항상 성공).
    pub codesign: SigningVerdict,
    /// 공증 단계 판정 (항상 성공).
    pub notarize: SigningVerdict,
    /// 스테이플 단계 판정 (항상 성공).
    pub staple: SigningVerdict,
    /// 번들 외에 따로 설치해야 하는 실행 파일 수 — 반드시 0 (R9.1).
    pub extra_installers: usize,
    /// 실행해야 하는 설치 명령 수 — 반드시 0 (R9.1).
    pub setup_commands: usize,
    /// 만들어진 배포 산출물 수. 성공 시 정확히 1, 실패 경로에서는 만들어지지
    /// 않는다 (R9.3, R9.5).
    pub artifacts: usize,
}

/// 배포 산출물 생성 실패 (R9.5).
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum PackageError {
    /// 서명·공증·스테이플 중 한 단계가 실패해 산출물을 만들지 않았다. 산출물
    /// 생성 건수는 0으로 유지된다 (R9.5).
    #[error("배포 서명 단계({})에서 멈췄어요: {reason}", stage.as_str())]
    SigningFailed {
        /// 실패한 단계.
        stage: SignStage,
        /// 실패 원인(쉬운말).
        reason: String,
        /// 사용자가 다음에 할 일(쉬운말).
        next_step: String,
    },
    /// 배포 이미지(DMG) 생성이 실패했다.
    #[error("배포 이미지를 만들지 못했어요: {0}")]
    Dmg(String),
}

// ---------------------------------------------------------------------------
// Signing tools seam
// ---------------------------------------------------------------------------

/// `codesign` / `notarytool` / `stapler` / `hdiutil` 경계.
///
/// 테스트는 가짜 구현을 주입해 인증서 없이 "실패 시 산출물 0건"과 "세 단계 성공
/// 시에만 산출물 생성"을 검증한다 (R9.5).
pub trait SigningTools {
    /// 번들에 코드 서명을 적용하고 판정을 낸다.
    fn codesign(&self, bundle: &Path) -> SigningVerdict;
    /// 번들을 공증하고 판정을 낸다.
    fn notarize(&self, bundle: &Path) -> SigningVerdict;
    /// 공증 결과를 번들에 스테이플하고 판정을 낸다.
    fn staple(&self, bundle: &Path) -> SigningVerdict;
    /// 번들을 담은 배포 이미지(DMG)를 만든다. 세 서명 단계가 모두 성공한 뒤에만
    /// 호출된다 (R9.3).
    fn make_dmg(&self, bundle: &Path, out: &Path) -> Result<PathBuf, PackageError>;
}

/// 배포 산출물을 만든다 (R9.3, R9.5).
///
/// 서명 → 공증 → 스테이플 세 단계를 순서대로 실행하고, **세 단계가 모두 성공한
/// 경우에만** 배포 이미지(DMG)를 만든다. 한 단계라도 실패하면
/// [`SigningTools::make_dmg`]를 호출하지 않아 산출물을 0건으로 유지하고, 실패
/// 단계·원인·다음에 할 일을 담은 [`PackageError::SigningFailed`]를 낸다.
///
/// 성공 시 [`PackageReport::extra_installers`]와
/// [`PackageReport::setup_commands`]는 항상 0이다 (R9.1).
pub fn package(plan: &BundlePlan, tools: &dyn SigningTools) -> Result<PackageReport, PackageError> {
    let bundle = plan.bundle_path();

    // 1단계: 코드 서명.
    let codesign = tools.codesign(&bundle);
    if let SigningVerdict::Failed { stage, reason } = &codesign {
        return Err(signing_failure(*stage, reason));
    }

    // 2단계: 공증. 앞 단계가 성공한 뒤에만 진행한다.
    let notarize = tools.notarize(&bundle);
    if let SigningVerdict::Failed { stage, reason } = &notarize {
        return Err(signing_failure(*stage, reason));
    }

    // 3단계: 스테이플. 앞 두 단계가 성공한 뒤에만 진행한다.
    let staple = tools.staple(&bundle);
    if let SigningVerdict::Failed { stage, reason } = &staple {
        return Err(signing_failure(*stage, reason));
    }

    // 세 단계가 모두 성공한 경우에만 배포 이미지를 만든다 (R9.3).
    let dmg = tools.make_dmg(&bundle, &plan.dmg_path())?;

    Ok(PackageReport {
        bundle,
        dmg,
        codesign,
        notarize,
        staple,
        extra_installers: 0,
        setup_commands: 0,
        artifacts: 1,
    })
}

/// 실패 판정에서 사용자 안내가 담긴 오류를 만든다.
fn signing_failure(stage: SignStage, reason: &str) -> PackageError {
    PackageError::SigningFailed {
        stage,
        reason: reason.to_string(),
        next_step: stage.next_step().to_string(),
    }
}

// ---------------------------------------------------------------------------
// Permissions and capabilities
// ---------------------------------------------------------------------------

/// 앱이 쓰는 필요 권한. 이 셋으로만 한정한다 (R9.7).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Permission {
    /// 손쉬운 사용(AX 자동화). AX 전송·텔레그램 읽기에 필요.
    Accessibility,
    /// 전체 디스크 접근. 로컬 카카오톡 DB 읽기에 필요.
    FullDiskAccess,
    /// 화면 기록. 화면 캡처로 이미지를 확보할 때만 필요.
    ScreenRecording,
}

impl Permission {
    /// 세 필요 권한 전부 (R9.7).
    pub const ALL: [Permission; 3] = [
        Permission::Accessibility,
        Permission::FullDiskAccess,
        Permission::ScreenRecording,
    ];

    /// 짧고 안정적인 코드.
    pub fn as_str(self) -> &'static str {
        match self {
            Permission::Accessibility => "accessibility",
            Permission::FullDiskAccess => "full_disk_access",
            Permission::ScreenRecording => "screen_recording",
        }
    }

    /// 이 권한을 허용하는 방법 (쉬운말, R9.8/9.9/9.10/9.11).
    pub fn how_to_grant(self) -> &'static str {
        match self {
            Permission::Accessibility => {
                "시스템 설정 > 개인정보 보호 및 보안 > 손쉬운 사용에서 이 앱을 켜 주세요."
            }
            Permission::FullDiskAccess => {
                "시스템 설정 > 개인정보 보호 및 보안 > 전체 디스크 접근에서 이 앱을 켜 주세요."
            }
            Permission::ScreenRecording => {
                "시스템 설정 > 개인정보 보호 및 보안 > 화면 기록에서 이 앱을 켜 주세요."
            }
        }
    }
}

/// 현재 허용된 권한 집합. 세 필요 권한의 부분집합만 담는다 (R9.7).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct PermissionState {
    granted: BTreeSet<Permission>,
}

impl PermissionState {
    /// 아무 권한도 허용되지 않은 상태.
    pub fn none() -> Self {
        Self {
            granted: BTreeSet::new(),
        }
    }

    /// 세 권한이 모두 허용된 상태.
    pub fn all() -> Self {
        Self {
            granted: Permission::ALL.into_iter().collect(),
        }
    }

    /// 허용된 권한 목록에서 상태를 만든다. 세 필요 권한 밖의 값은 담기지 않는다.
    pub fn from_granted(granted: impl IntoIterator<Item = Permission>) -> Self {
        Self {
            granted: granted.into_iter().collect(),
        }
    }

    /// 권한을 허용 상태로 바꾼다.
    pub fn grant(&mut self, permission: Permission) {
        self.granted.insert(permission);
    }

    /// 권한 허용을 거둔다.
    pub fn revoke(&mut self, permission: Permission) {
        self.granted.remove(&permission);
    }

    /// 이 권한이 허용되어 있는지.
    pub fn is_granted(&self, permission: Permission) -> bool {
        self.granted.contains(&permission)
    }

    /// 현재 허용된 권한 목록(정렬됨). 세 필요 권한의 부분집합이다 (R9.7, R9.11).
    pub fn granted(&self) -> Vec<Permission> {
        self.granted.iter().copied().collect()
    }
}

/// 앱이 제공하는 기능. 권한 상태에 따라 사용 가능/차단으로 갈린다.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Capability {
    /// AX 전송 (손쉬운 사용 필요, R9.8).
    AxSend,
    /// 텔레그램 읽기 (손쉬운 사용 필요, R9.8).
    TelegramRead,
    /// 로컬 카카오톡 대화 읽기 (전체 디스크 접근 필요, R9.9).
    LocalRead,
    /// 맥락 검색 (로컬 DB 의존, 전체 디스크 접근 필요, R9.9).
    ContextSearch,
    /// 데이터셋 증분 갱신 (로컬 DB 의존, 전체 디스크 접근 필요, R9.9).
    DatasetRefresh,
    /// 기능 검증 (가짜 어댑터로 동작, 권한 불필요, R9.8).
    CoverageVerify,
    /// 화면 캡처로 이미지 확보 (화면 기록 필요, R9.10).
    ScreenCaptureImages,
    /// 긱뉴스 게시.
    GeekNewsPost,
    /// 링크 전달.
    LinkForward,
}

impl Capability {
    /// 전체 기능 목록.
    pub const ALL: [Capability; 9] = [
        Capability::AxSend,
        Capability::TelegramRead,
        Capability::LocalRead,
        Capability::ContextSearch,
        Capability::DatasetRefresh,
        Capability::CoverageVerify,
        Capability::ScreenCaptureImages,
        Capability::GeekNewsPost,
        Capability::LinkForward,
    ];

    /// 짧고 안정적인 코드.
    pub fn as_str(self) -> &'static str {
        match self {
            Capability::AxSend => "ax_send",
            Capability::TelegramRead => "telegram_read",
            Capability::LocalRead => "local_read",
            Capability::ContextSearch => "context_search",
            Capability::DatasetRefresh => "dataset_refresh",
            Capability::CoverageVerify => "coverage_verify",
            Capability::ScreenCaptureImages => "screen_capture_images",
            Capability::GeekNewsPost => "geeknews_post",
            Capability::LinkForward => "link_forward",
        }
    }

    /// 이 기능에 필요한 권한. `None`이면 어떤 권한도 필요하지 않아 항상 사용
    /// 가능하다.
    ///
    /// 각 권한 거부의 차단 범위를 정확히 만든다 (R9.8/9.9/9.10):
    /// * 손쉬운 사용 → AX 전송·텔레그램 읽기만
    /// * 전체 디스크 접근 → 로컬 DB 의존 기능만(로컬 읽기·맥락 검색·데이터셋 갱신)
    /// * 화면 기록 → 화면 캡처 경로만
    pub fn required_permission(self) -> Option<Permission> {
        match self {
            Capability::AxSend | Capability::TelegramRead => Some(Permission::Accessibility),
            Capability::LocalRead | Capability::ContextSearch | Capability::DatasetRefresh => {
                Some(Permission::FullDiskAccess)
            }
            Capability::ScreenCaptureImages => Some(Permission::ScreenRecording),
            // 권한이 필요 없는 기능은 어떤 권한 조합에서도 계속 제공된다.
            Capability::CoverageVerify | Capability::GeekNewsPost | Capability::LinkForward => None,
        }
    }
}

/// 권한 상태에 따른 기능 분할 (R9.11).
///
/// [`Self::available`]과 [`Self::blocked`]는 전체 기능 집합의 분할을 이룬다:
/// 합집합이 전체이고 교집합이 공집합이다. 각 차단 항목은 필요 권한과 허용
/// 방법을 함께 담는다.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CapabilityMap {
    /// 현재 사용할 수 있는 기능.
    pub available: Vec<Capability>,
    /// 차단된 기능과 그 필요 권한·허용 방법.
    pub blocked: Vec<(Capability, Permission, String)>,
}

impl CapabilityMap {
    /// 앱이 실행을 유지하는지: 사용 가능한 기능이 하나라도 있으면 참 (R9.13).
    ///
    /// 권한이 필요 없는 기능(기능 검증·긱뉴스 게시·링크 전달)이 항상 존재하므로
    /// 이 값은 어떤 권한 조합에서도 참이다.
    pub fn keeps_running(&self) -> bool {
        !self.available.is_empty()
    }
}

/// 권한 상태를 기능 분할로 매핑하는 순수 함수 (R9.8/9.9/9.10/9.11/9.13).
///
/// Swift 온보딩 화면은 이 결과를 그리기만 한다. 각 기능은 필요 권한이 허용되어
/// 있으면 사용 가능으로, 아니면 필요 권한·허용 방법과 함께 차단으로 분류된다.
pub fn capabilities(state: &PermissionState) -> CapabilityMap {
    let mut available = Vec::new();
    let mut blocked = Vec::new();

    for capability in Capability::ALL {
        match capability.required_permission() {
            Some(permission) if !state.is_granted(permission) => {
                blocked.push((capability, permission, permission.how_to_grant().to_string()));
            }
            _ => available.push(capability),
        }
    }

    CapabilityMap { available, blocked }
}

#[cfg(test)]
mod tests {
    use super::*;

    // -----------------------------------------------------------------------
    // Fake signing tools (no real certificate, R9.5)
    // -----------------------------------------------------------------------

    /// 주입된 판정을 그대로 돌려주는 가짜 서명 도구. `make_dmg` 호출 횟수를 세어
    /// "실패 시 산출물 0건"을 검증한다.
    struct FakeSigningTools {
        codesign: SigningVerdict,
        notarize: SigningVerdict,
        staple: SigningVerdict,
        dmg_calls: std::cell::Cell<usize>,
    }

    impl FakeSigningTools {
        fn all_ok() -> Self {
            Self {
                codesign: SigningVerdict::ok("TEAM123"),
                notarize: SigningVerdict::ok("TEAM123"),
                staple: SigningVerdict::ok("TEAM123"),
                dmg_calls: std::cell::Cell::new(0),
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
        BundlePlan::new(
            "AutoReplyMenu",
            "/tmp/core",
            "/tmp/shell",
            "/tmp/out",
        )
    }

    // -----------------------------------------------------------------------
    // package: signing outcomes
    // -----------------------------------------------------------------------

    #[test]
    fn package_succeeds_only_when_all_three_stages_pass() {
        let tools = FakeSigningTools::all_ok();
        let report = package(&plan(), &tools).expect("all-ok should package");

        assert!(report.codesign.is_ok());
        assert!(report.notarize.is_ok());
        assert!(report.staple.is_ok());
        // 성공 산출물의 추가 설치 파일·설치 명령은 0 (R9.1).
        assert_eq!(report.extra_installers, 0);
        assert_eq!(report.setup_commands, 0);
        assert_eq!(report.artifacts, 1);
        assert_eq!(report.dmg, plan().dmg_path());
        // DMG가 정확히 한 번 만들어졌다.
        assert_eq!(tools.dmg_calls.get(), 1);
    }

    #[test]
    fn package_codesign_failure_produces_zero_artifacts() {
        let tools = FakeSigningTools {
            codesign: SigningVerdict::failed(SignStage::Codesign, "인증서 없음"),
            ..FakeSigningTools::all_ok()
        };
        let err = package(&plan(), &tools).expect_err("codesign failure fails packaging");
        match err {
            PackageError::SigningFailed {
                stage, next_step, ..
            } => {
                assert_eq!(stage, SignStage::Codesign);
                assert!(!next_step.is_empty());
            }
            other => panic!("expected signing failure, got {other:?}"),
        }
        // 실패 시 DMG를 만들지 않는다 (R9.5).
        assert_eq!(tools.dmg_calls.get(), 0);
    }

    #[test]
    fn package_notarize_failure_stops_before_staple_and_dmg() {
        let tools = FakeSigningTools {
            notarize: SigningVerdict::failed(SignStage::Notarize, "공증 거부"),
            ..FakeSigningTools::all_ok()
        };
        let err = package(&plan(), &tools).expect_err("notarize failure fails packaging");
        assert!(matches!(
            err,
            PackageError::SigningFailed {
                stage: SignStage::Notarize,
                ..
            }
        ));
        assert_eq!(tools.dmg_calls.get(), 0);
    }

    #[test]
    fn package_staple_failure_produces_zero_artifacts() {
        let tools = FakeSigningTools {
            staple: SigningVerdict::failed(SignStage::Staple, "스테이플 실패"),
            ..FakeSigningTools::all_ok()
        };
        let err = package(&plan(), &tools).expect_err("staple failure fails packaging");
        assert!(matches!(
            err,
            PackageError::SigningFailed {
                stage: SignStage::Staple,
                ..
            }
        ));
        assert_eq!(tools.dmg_calls.get(), 0);
    }

    // -----------------------------------------------------------------------
    // capabilities: exhaustive permission combinations (R9.8/9.9/9.10/9.13)
    // -----------------------------------------------------------------------

    /// 세 권한의 전수 8조합.
    fn all_permission_combos() -> Vec<PermissionState> {
        let mut combos = Vec::new();
        for bits in 0u8..8 {
            let mut granted = Vec::new();
            if bits & 0b001 != 0 {
                granted.push(Permission::Accessibility);
            }
            if bits & 0b010 != 0 {
                granted.push(Permission::FullDiskAccess);
            }
            if bits & 0b100 != 0 {
                granted.push(Permission::ScreenRecording);
            }
            combos.push(PermissionState::from_granted(granted));
        }
        combos
    }

    #[test]
    fn capabilities_partition_the_full_feature_set() {
        for state in all_permission_combos() {
            let map = capabilities(&state);

            // available ∪ blocked == 전체 기능, 교집합 == 공집합 (R9.11).
            let mut seen: BTreeSet<Capability> = BTreeSet::new();
            for &c in &map.available {
                assert!(seen.insert(c), "capability {c:?} appeared twice");
            }
            for (c, _perm, _how) in &map.blocked {
                assert!(seen.insert(*c), "capability {c:?} appeared in both halves");
            }
            let expected: BTreeSet<Capability> = Capability::ALL.into_iter().collect();
            assert_eq!(seen, expected, "partition must cover exactly all features");

            // 어떤 조합에서도 앱은 실행을 유지한다 (R9.13).
            assert!(map.keeps_running());

            // 각 차단 항목은 필요 권한과 비어 있지 않은 허용 방법을 담는다.
            for (cap, perm, how) in &map.blocked {
                assert_eq!(cap.required_permission(), Some(*perm));
                assert!(!state.is_granted(*perm));
                assert!(!how.is_empty());
            }
        }
    }

    #[test]
    fn accessibility_denied_blocks_only_ax_send_and_telegram_read() {
        // 손쉬운 사용만 거부, 나머지 둘은 허용.
        let state = PermissionState::from_granted([
            Permission::FullDiskAccess,
            Permission::ScreenRecording,
        ]);
        let map = capabilities(&state);
        let blocked: BTreeSet<Capability> = map.blocked.iter().map(|(c, _, _)| *c).collect();
        assert_eq!(
            blocked,
            BTreeSet::from([Capability::AxSend, Capability::TelegramRead])
        );
        // 로컬 읽기·맥락 검색·데이터셋 갱신·기능 검증은 계속 제공 (R9.8).
        assert!(map.available.contains(&Capability::LocalRead));
        assert!(map.available.contains(&Capability::ContextSearch));
        assert!(map.available.contains(&Capability::DatasetRefresh));
        assert!(map.available.contains(&Capability::CoverageVerify));
    }

    #[test]
    fn full_disk_denied_blocks_only_local_db_features() {
        let state = PermissionState::from_granted([
            Permission::Accessibility,
            Permission::ScreenRecording,
        ]);
        let map = capabilities(&state);
        let blocked: BTreeSet<Capability> = map.blocked.iter().map(|(c, _, _)| *c).collect();
        assert_eq!(
            blocked,
            BTreeSet::from([
                Capability::LocalRead,
                Capability::ContextSearch,
                Capability::DatasetRefresh,
            ])
        );
        // DB 읽기가 필요 없는 기능은 계속 제공 (R9.9).
        assert!(map.available.contains(&Capability::AxSend));
        assert!(map.available.contains(&Capability::CoverageVerify));
        assert!(map.available.contains(&Capability::ScreenCaptureImages));
    }

    #[test]
    fn screen_recording_denied_blocks_only_screen_capture() {
        let state = PermissionState::from_granted([
            Permission::Accessibility,
            Permission::FullDiskAccess,
        ]);
        let map = capabilities(&state);
        let blocked: BTreeSet<Capability> = map.blocked.iter().map(|(c, _, _)| *c).collect();
        assert_eq!(blocked, BTreeSet::from([Capability::ScreenCaptureImages]));
    }

    #[test]
    fn all_permissions_granted_blocks_nothing() {
        let map = capabilities(&PermissionState::all());
        assert!(map.blocked.is_empty());
        assert_eq!(map.available.len(), Capability::ALL.len());
    }

    #[test]
    fn no_permissions_still_keeps_running() {
        let map = capabilities(&PermissionState::none());
        // 권한이 필요 없는 세 기능은 여전히 사용 가능 (R9.13).
        assert!(map.available.contains(&Capability::CoverageVerify));
        assert!(map.available.contains(&Capability::GeekNewsPost));
        assert!(map.available.contains(&Capability::LinkForward));
        assert!(map.keeps_running());
    }

    #[test]
    fn granted_permissions_are_a_subset_of_the_three() {
        // from_granted는 세 필요 권한만 담을 수 있는 타입이므로, 어떤 상태의
        // granted()도 세 값의 부분집합이다 (R9.7).
        let state = PermissionState::all();
        let all: BTreeSet<Permission> = Permission::ALL.into_iter().collect();
        for p in state.granted() {
            assert!(all.contains(&p));
        }
    }
}
