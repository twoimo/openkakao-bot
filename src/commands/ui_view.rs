//! Live-ops menu-bar window data source (`ui-view`).
//!
//! The SwiftUI/AppKit menu-bar shell (`macos/AutoReplyMenu/main.swift`) draws
//! four live-ops windows — 대량 검증(durability), 기능 점검(coverage),
//! 자기개선(improvement), 권한 설정(onboarding). Each one shells out to
//! `openkakao-cli --action <window>-view` (through the Python bridge) and
//! decodes a fixed JSON shape into a `*ViewModel`. This command is the Rust
//! side that produces that JSON: it runs the same core decision logic used
//! everywhere else ([`openkakao_cli::ui_shell`]) over real (or fully-fake, for
//! durability) inputs and prints exactly one JSON line whose field names match
//! the Swift `Decodable` structs verbatim.
//!
//! All work is read-only and local: durability runs against a fully fake world
//! (zero real sends, zero network egress), coverage reads the on-disk room
//! catalog, improvement reads the local self-improvement history database, and
//! onboarding reads the macOS permission probes. Nothing here contacts a
//! KakaoTalk server.

use std::path::{Path, PathBuf};

use anyhow::Result;
use serde_json::{json, Value};

use openkakao_cli::coverage::{CellProbe, CoverageVerifier, Feature, ProbeSignal};
use openkakao_cli::durability::{CancelToken, DurabilityHarness, HarnessPlan};
use openkakao_cli::fakes::{FakePorts, ScenarioShape};
use openkakao_cli::improve::{ImproveStore, SqliteImproveStore};
use openkakao_cli::logging::SqliteHistoryStore;
use openkakao_cli::packaging::{capabilities, Permission, PermissionState};
use openkakao_cli::room_catalog::CatalogRoom;
use openkakao_cli::ui_shell::{
    coverage_view, durability_view, improvement_view, onboarding_view, ImprovementView,
};

/// Run one live-ops window's data source and print a single JSON line to
/// stdout (R1, R5, R8, R9). Field names match the Swift `*ViewModel` structs.
pub fn cmd_ui_view(
    window: &str,
    state_root: Option<PathBuf>,
    granted: Option<String>,
) -> Result<()> {
    let root = state_root.unwrap_or_else(default_state_root);
    let value = match window {
        "durability" => durability_json()?,
        "coverage" => coverage_json(&root),
        "improvement" => improvement_json(&root),
        "onboarding" => onboarding_json(granted.as_deref()),
        other => anyhow::bail!("unknown ui-view window: {other}"),
    };
    println!("{}", serde_json::to_string(&value)?);
    Ok(())
}

// ---------------------------------------------------------------------------
// Default state root
// ---------------------------------------------------------------------------

/// The default menu-bar state root: `~/Library/Application Support/openkakao`.
/// Built component-by-component so no embedded separators leak in.
fn default_state_root() -> PathBuf {
    dirs::home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("Library")
        .join("Application Support")
        .join("openkakao")
}

// ---------------------------------------------------------------------------
// Durability (대량 검증)
// ---------------------------------------------------------------------------

/// Build the durability window JSON by running a bounded durability batch over
/// a fully fake world (R1.5–R1.8). The fake world guarantees zero real sends
/// and zero network egress, so a fresh run always shows the tripwires at zero.
fn durability_json() -> Result<Value> {
    let ports = FakePorts::from_seed(1, ScenarioShape::new(8, 40, 2, 16));
    let journal = SqliteHistoryStore::open_in_memory()?;
    // Capture the sandbox path before the ports are moved into the harness.
    let sandbox = ports.sandbox.path().to_path_buf();
    let mut harness = DurabilityHarness::new(ports, &journal);
    let plan = HarnessPlan {
        auto_reply_runs: 100,
        geeknews_runs: 20,
        seed: 1,
        sandbox,
    };
    let report = harness.run(&plan, &CancelToken::new());
    let view = durability_view(&report);
    Ok(json!({
        "auto_reply": view.auto_reply,
        "geeknews": view.geeknews,
        "verdict": view.verdict,
        "seed": view.seed,
    }))
}

// ---------------------------------------------------------------------------
// Coverage (기능 점검)
// ---------------------------------------------------------------------------

/// A probe that never performs a real send: send-performing features are
/// reported as `blocked` on the accessibility permission when it is not
/// granted, and everything else passes. This mirrors what a real coverage run
/// would observe on a machine whose AX permission decides send eligibility,
/// without ever touching KakaoTalk.
struct NoSendProbe {
    accessibility: bool,
}

impl CellProbe for NoSendProbe {
    fn probe(&self, _room: &CatalogRoom, feature: Feature) -> ProbeSignal {
        if feature.performs_send() && !self.accessibility {
            ProbeSignal::blocked(vec!["accessibility"], 1)
        } else {
            ProbeSignal::passed(1)
        }
    }
}

/// Read the menu-bar room catalog tolerantly: a missing, unreadable, or
/// malformed file yields an empty room list rather than an error, so the
/// coverage window degrades to a "no rooms" notice instead of failing.
fn load_catalog_rooms_tolerant(state_root: &Path) -> Vec<CatalogRoom> {
    #[derive(serde::Deserialize)]
    struct CatalogFileView {
        #[serde(default)]
        rooms: Vec<CatalogRoom>,
    }

    let path = state_root.join("menubar-room-catalog.json");
    let raw = match std::fs::read(&path) {
        Ok(raw) => raw,
        Err(_) => return Vec::new(),
    };
    match serde_json::from_slice::<CatalogFileView>(&raw) {
        Ok(file) => file.rooms,
        Err(_) => Vec::new(),
    }
}

/// Build the coverage window JSON by verifying every `room × feature`
/// combination over the on-disk catalog (R5.5). The verdicts are turned into
/// plain-language lines by [`coverage_view`].
fn coverage_json(state_root: &Path) -> Value {
    let rooms = load_catalog_rooms_tolerant(state_root);
    let probe = NoSendProbe {
        accessibility: probe_accessibility(),
    };
    let matrix = CoverageVerifier::new(rooms, &probe).verify();
    let view = coverage_view(&matrix);
    json!({
        "summary": view.summary,
        "status": view.status,
        "rows": view.rows,
    })
}

// ---------------------------------------------------------------------------
// Improvement (자기개선)
// ---------------------------------------------------------------------------

/// Build the self-improvement window JSON from the local history database
/// (R8.16). The database is looked up first directly under the state root,
/// then under the `auto-reply` subdirectory; when neither exists (or a read
/// fails) the window shows the plain-language empty notice.
fn improvement_json(state_root: &Path) -> Value {
    let candidates = [
        state_root.join("improve.sqlite3"),
        state_root.join("auto-reply").join("improve.sqlite3"),
    ];
    let entries = candidates
        .iter()
        .find(|path| path.exists())
        .and_then(|path| SqliteImproveStore::open(path).ok())
        .and_then(|store| store.history(50).ok())
        .unwrap_or_default();

    match improvement_view(&entries) {
        ImprovementView::Rows(rows) => json!({ "rows": rows, "empty": Value::Null }),
        ImprovementView::Empty(message) => json!({ "rows": [], "empty": message }),
    }
}

// ---------------------------------------------------------------------------
// Onboarding (권한 설정)
// ---------------------------------------------------------------------------

/// Parse a comma-separated permission list into the recognized [`Permission`]s.
/// Tokens are trimmed and lowercased; a small set of aliases is accepted for
/// each permission, and unknown tokens are ignored.
fn parse_granted(list: &str) -> Vec<Permission> {
    let mut out = Vec::new();
    for token in list.split(',') {
        let normalized = token.trim().to_lowercase();
        let permission = match normalized.as_str() {
            "accessibility" | "acc" | "ax" => Some(Permission::Accessibility),
            "full-disk" | "full_disk" | "fda" | "disk" => Some(Permission::FullDiskAccess),
            "screen-recording" | "screen_recording" | "scr" | "screen" => {
                Some(Permission::ScreenRecording)
            }
            _ => None,
        };
        if let Some(permission) = permission {
            if !out.contains(&permission) {
                out.push(permission);
            }
        }
    }
    out
}

/// Build the onboarding window JSON from the permission → capability partition
/// (R9.11). When `granted` is supplied it is used directly (the caller has
/// already decided the permission state); otherwise the live macOS probes are
/// consulted.
fn onboarding_json(granted: Option<&str>) -> Value {
    let state = match granted {
        Some(list) => PermissionState::from_granted(parse_granted(list)),
        None => probe_permission_state(),
    };
    let map = capabilities(&state);
    let view = onboarding_view(&map);
    json!({
        "available": view.available,
        "blocked": view.blocked,
    })
}

// ---------------------------------------------------------------------------
// Permission probes
// ---------------------------------------------------------------------------

/// Whether this process is trusted for macOS Accessibility (AX) automation.
#[cfg(target_os = "macos")]
fn probe_accessibility() -> bool {
    extern "C" {
        fn AXIsProcessTrusted() -> bool;
    }
    unsafe { AXIsProcessTrusted() }
}

#[cfg(not(target_os = "macos"))]
fn probe_accessibility() -> bool {
    false
}

/// Whether this process may capture the screen on macOS.
#[cfg(target_os = "macos")]
fn probe_screen_recording() -> bool {
    extern "C" {
        fn CGPreflightScreenCaptureAccess() -> bool;
    }
    unsafe { CGPreflightScreenCaptureAccess() }
}

#[cfg(not(target_os = "macos"))]
fn probe_screen_recording() -> bool {
    false
}

/// Whether this process has Full Disk Access, inferred by whether the
/// TCC database — readable only with that permission — can be opened.
fn probe_full_disk_access() -> bool {
    let tcc = dirs::home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("Library")
        .join("Application Support")
        .join("com.apple.TCC")
        .join("TCC.db");
    std::fs::File::open(tcc).is_ok()
}

/// Assemble the live permission state from the three probes.
fn probe_permission_state() -> PermissionState {
    let mut granted = Vec::new();
    if probe_accessibility() {
        granted.push(Permission::Accessibility);
    }
    if probe_full_disk_access() {
        granted.push(Permission::FullDiskAccess);
    }
    if probe_screen_recording() {
        granted.push(Permission::ScreenRecording);
    }
    PermissionState::from_granted(granted)
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn durability_json_has_all_fields_and_zero_real_sends_verdict() {
        let value = durability_json().expect("durability json");
        assert!(value.get("auto_reply").is_some());
        assert!(value.get("geeknews").is_some());
        assert!(value.get("verdict").is_some());
        assert!(value.get("seed").is_some());

        let verdict = value["verdict"].as_array().expect("verdict is array");
        assert!(
            verdict
                .iter()
                .any(|line| line.as_str().is_some_and(|s| s.contains("실제 전송 0건"))),
            "verdict must report zero real sends: {verdict:?}"
        );
    }

    #[test]
    fn coverage_json_empty_tempdir_shows_no_rooms() {
        let dir = tempdir().expect("tempdir");
        let value = coverage_json(dir.path());
        assert_eq!(
            value["summary"], "전체 0개 · 정상 0 · 실패 0 · 미지원 0 · 차단 0"
        );
        assert!(value["status"]
            .as_str()
            .expect("status")
            .contains("점검할 방이 없어요"));
        assert!(value["rows"].as_array().expect("rows").is_empty());
    }

    #[test]
    fn coverage_json_one_room_has_six_rows() {
        let dir = tempdir().expect("tempdir");
        std::fs::write(
            dir.path().join("menubar-room-catalog.json"),
            r#"{"rooms":[{"chat_id":1,"title":"스터디","enabled":true,"auto_reply":true,"geeknews":true,"link_forward":true,"telegram_relay":true}]}"#,
        )
        .expect("write catalog");
        let value = coverage_json(dir.path());
        assert!(value["summary"]
            .as_str()
            .expect("summary")
            .contains("전체 6개"));
        assert_eq!(value["rows"].as_array().expect("rows").len(), 6);
    }

    #[test]
    fn improvement_json_empty_tempdir_shows_empty_notice() {
        let dir = tempdir().expect("tempdir");
        let value = improvement_json(dir.path());
        assert!(value["rows"].as_array().expect("rows").is_empty());
        assert!(value["empty"]
            .as_str()
            .expect("empty message")
            .contains("아직 자기개선 기록이 없어요"));
    }

    #[test]
    fn onboarding_json_no_grants_blocks_send() {
        // With nothing granted, the send capability (AX) is blocked. The three
        // permission-free capabilities remain available by design (they never
        // require a permission), so `available` is not empty — but it must not
        // contain the send label, and `blocked` must be non-empty.
        let value = onboarding_json(Some(""));
        let available = value["available"].as_array().expect("available");
        assert!(
            available
                .iter()
                .all(|line| line.as_str() != Some("메시지 보내기")),
            "message-send must not be available with no permissions: {available:?}"
        );
        assert!(!value["blocked"].as_array().expect("blocked").is_empty());
    }

    #[test]
    fn onboarding_json_all_grants_unblocks_send() {
        let value = onboarding_json(Some("accessibility,full-disk,screen-recording"));
        let available = value["available"].as_array().expect("available");
        assert!(available
            .iter()
            .any(|line| line.as_str() == Some("메시지 보내기")));
        assert!(value["blocked"].as_array().expect("blocked").is_empty());
    }

    #[test]
    fn parse_granted_accepts_aliases_and_whitespace() {
        let parsed = parse_granted("acc, full-disk , scr");
        assert!(parsed.contains(&Permission::Accessibility));
        assert!(parsed.contains(&Permission::FullDiskAccess));
        assert!(parsed.contains(&Permission::ScreenRecording));
        assert_eq!(parsed.len(), 3);
    }
}
