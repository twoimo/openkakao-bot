use std::path::PathBuf;

use anyhow::Result;
use owo_colors::OwoColorize;

use crate::auth::get_credential_candidates;
use crate::config::OpenKakaoConfig;
use crate::credentials::load_credentials;
use crate::model::KakaoCredentials;
use crate::rest::KakaoRestClient;
use crate::state::{recovery_snapshot, safety_snapshot};
use crate::util::{color_enabled, VERSION};

struct Check {
    name: String,
    status: CheckStatus,
    detail: String,
}

enum CheckStatus {
    Ok,
    Warn,
    Fail,
}

fn format_remaining(remaining_secs: Option<u64>, until: Option<&str>) -> String {
    match (remaining_secs, until) {
        (Some(secs), Some(until)) => format!("{}s (until {})", secs, until),
        (Some(secs), None) => format!("{}s", secs),
        _ => "none".to_string(),
    }
}

pub fn cmd_doctor(json: bool, test_loco: bool, config: &OpenKakaoConfig) -> Result<()> {
    let mut checks: Vec<Check> = Vec::new();
    let mut installed_version: Option<String> = None;
    let mut saved_app_version: Option<String> = None;
    let recovery = recovery_snapshot()?;
    let safety = safety_snapshot(
        config
            .safety
            .min_unattended_send_interval_secs
            .unwrap_or(10),
        config.safety.min_hook_interval_secs.unwrap_or(2),
        config.safety.min_webhook_interval_secs.unwrap_or(2),
    )?;

    checks.push(Check {
        name: "State file".into(),
        status: CheckStatus::Ok,
        detail: recovery.path.clone(),
    });
    checks.push(Check {
        name: "Auth recovery state".into(),
        status: if recovery.auth_cooldown_remaining_secs.is_some()
            || recovery.consecutive_failures > 0
        {
            CheckStatus::Warn
        } else {
            CheckStatus::Ok
        },
        detail: format!(
            "failures={}, last_failure={}, auth_cooldown={}, last_success={} via {}",
            recovery.consecutive_failures,
            recovery.last_failure_kind.as_deref().unwrap_or("none"),
            format_remaining(
                recovery.auth_cooldown_remaining_secs,
                recovery.cooldown_until.as_deref()
            ),
            recovery
                .last_success_transport
                .as_deref()
                .unwrap_or("never"),
            recovery.last_recovery_source.as_deref().unwrap_or("none")
        ),
    });
    checks.push(Check {
        name: "Safety guards".into(),
        status: if safety.last_guard_reason.is_some() {
            CheckStatus::Warn
        } else {
            CheckStatus::Ok
        },
        detail: format!(
            "send={}s, hook={}s, webhook={}s, hook_timeout={}s, webhook_timeout={}s, insecure_webhooks={}, last_guard={}",
            config.safety.min_unattended_send_interval_secs.unwrap_or(10),
            config.safety.min_hook_interval_secs.unwrap_or(2),
            config.safety.min_webhook_interval_secs.unwrap_or(2),
            config.safety.hook_timeout_secs.unwrap_or(20),
            config.safety.webhook_timeout_secs.unwrap_or(10),
            if config.safety.allow_insecure_webhooks { "allowed" } else { "blocked" },
            safety.last_guard_reason.as_deref().unwrap_or("none")
        ),
    });

    // 1. KakaoTalk.app installed version
    let app_plist = PathBuf::from("/Applications/KakaoTalk.app/Contents/Info.plist");
    if app_plist.exists() {
        match plist::from_file::<_, plist::Dictionary>(&app_plist) {
            Ok(dict) => {
                let version = dict
                    .get("CFBundleShortVersionString")
                    .and_then(|v| v.as_string())
                    .unwrap_or("unknown");
                installed_version = Some(version.to_string());
                let bundle_id = dict
                    .get("CFBundleIdentifier")
                    .and_then(|v| v.as_string())
                    .unwrap_or("unknown");
                checks.push(Check {
                    name: "KakaoTalk.app".into(),
                    status: CheckStatus::Ok,
                    detail: format!("v{} ({})", version, bundle_id),
                });
            }
            Err(e) => {
                checks.push(Check {
                    name: "KakaoTalk.app".into(),
                    status: CheckStatus::Warn,
                    detail: format!("Installed but cannot read Info.plist: {}", e),
                });
            }
        }
    } else {
        checks.push(Check {
            name: "KakaoTalk.app".into(),
            status: CheckStatus::Fail,
            detail: "Not found in /Applications".into(),
        });
    }

    // 2. KakaoTalk process running
    let pgrep_output = std::process::Command::new("pgrep")
        .args(["-x", "KakaoTalk"])
        .output();
    match pgrep_output {
        Ok(output) if output.status.success() => {
            let pids = String::from_utf8_lossy(&output.stdout).trim().to_string();
            checks.push(Check {
                name: "KakaoTalk process".into(),
                status: CheckStatus::Ok,
                detail: format!("Running (PID: {})", pids.replace('\n', ", ")),
            });
        }
        _ => {
            checks.push(Check {
                name: "KakaoTalk process".into(),
                status: CheckStatus::Warn,
                detail: "Not running. Start KakaoTalk to refresh tokens.".into(),
            });
        }
    }

    // 3. Cache.db existence and freshness
    let home = dirs::home_dir().unwrap_or_default();
    let cache_db =
        home.join("Library/Containers/com.kakao.KakaoTalkMac/Data/Library/Caches/Cache.db");
    if cache_db.exists() {
        match std::fs::metadata(&cache_db) {
            Ok(meta) => {
                let modified = meta
                    .modified()
                    .ok()
                    .and_then(|t| t.elapsed().ok())
                    .map(|d| {
                        if d.as_secs() < 60 {
                            format!("{}s ago", d.as_secs())
                        } else if d.as_secs() < 3600 {
                            format!("{}m ago", d.as_secs() / 60)
                        } else if d.as_secs() < 86400 {
                            format!("{}h ago", d.as_secs() / 3600)
                        } else {
                            format!("{}d ago", d.as_secs() / 86400)
                        }
                    })
                    .unwrap_or_else(|| "unknown".into());
                let size_kb = meta.len() / 1024;
                let status = if meta
                    .modified()
                    .ok()
                    .and_then(|t| t.elapsed().ok())
                    .is_some_and(|d| d.as_secs() > 86400)
                {
                    CheckStatus::Warn
                } else {
                    CheckStatus::Ok
                };
                checks.push(Check {
                    name: "Cache.db".into(),
                    status,
                    detail: format!("{}KB, modified {}", size_kb, modified),
                });
            }
            Err(e) => {
                checks.push(Check {
                    name: "Cache.db".into(),
                    status: CheckStatus::Warn,
                    detail: format!("Exists but unreadable: {}", e),
                });
            }
        }
    } else {
        checks.push(Check {
            name: "Cache.db".into(),
            status: CheckStatus::Fail,
            detail: "Not found. Has KakaoTalk been used on this Mac?".into(),
        });
    }

    // 4. Saved credentials file
    match crate::credentials::credentials_path() {
        Ok(path) => {
            if path.exists() {
                match load_credentials() {
                    Ok(Some(creds)) => {
                        saved_app_version = Some(creds.app_version.clone());
                        checks.push(Check {
                            name: "Saved credentials".into(),
                            status: CheckStatus::Ok,
                            detail: format!(
                                "user_id={}, version={}, token={}...",
                                creds.user_id,
                                creds.app_version,
                                creds.oauth_token.chars().take(8).collect::<String>()
                            ),
                        });
                    }
                    Ok(None) => {
                        checks.push(Check {
                            name: "Saved credentials".into(),
                            status: CheckStatus::Warn,
                            detail: "File exists but empty/invalid".into(),
                        });
                    }
                    Err(e) => {
                        checks.push(Check {
                            name: "Saved credentials".into(),
                            status: CheckStatus::Warn,
                            detail: format!("Parse error: {}", e),
                        });
                    }
                }
            } else {
                checks.push(Check {
                    name: "Saved credentials".into(),
                    status: CheckStatus::Warn,
                    detail: format!(
                        "Not found. Run 'openkakao-cli login --save'. ({})",
                        path.display()
                    ),
                });
            }
        }
        Err(e) => {
            checks.push(Check {
                name: "Saved credentials".into(),
                status: CheckStatus::Fail,
                detail: format!("Cannot determine path: {}", e),
            });
        }
    }

    // 4b. Version drift
    if let (Some(installed), Some(saved)) = (&installed_version, &saved_app_version) {
        if installed == saved {
            checks.push(Check {
                name: "Version match".into(),
                status: CheckStatus::Ok,
                detail: format!("Installed and saved both v{}", installed),
            });
        } else {
            checks.push(Check {
                name: "Version drift".into(),
                status: CheckStatus::Warn,
                detail: format!(
                    "Installed v{} != saved v{}. Run `relogin --fresh-xvc` to re-authenticate.",
                    installed, saved
                ),
            });
        }
    }

    // 5. Token validity via REST API
    let creds_result: Result<KakaoCredentials> = {
        if let Ok(Some(saved)) = load_credentials() {
            Ok(saved)
        } else {
            let candidates = get_credential_candidates(4).unwrap_or_default();
            candidates
                .into_iter()
                .next()
                .ok_or_else(|| anyhow::anyhow!("No credentials found"))
        }
    };
    match &creds_result {
        Ok(creds) => match KakaoRestClient::new(creds.clone()) {
            Ok(client) => match client.verify_token() {
                Ok(true) => {
                    checks.push(Check {
                        name: "REST API token".into(),
                        status: CheckStatus::Ok,
                        detail: format!("Valid (user_id={})", creds.user_id),
                    });
                }
                Ok(false) => {
                    checks.push(Check {
                        name: "REST API token".into(),
                        status: CheckStatus::Fail,
                        detail: "Token rejected. Open KakaoTalk, browse chats, then re-login."
                            .into(),
                    });
                }
                Err(e) => {
                    checks.push(Check {
                        name: "REST API token".into(),
                        status: CheckStatus::Fail,
                        detail: format!("Request failed: {}", e),
                    });
                }
            },
            Err(e) => {
                checks.push(Check {
                    name: "REST API token".into(),
                    status: CheckStatus::Fail,
                    detail: format!("Client init failed: {}", e),
                });
            }
        },
        Err(e) => {
            checks.push(Check {
                name: "REST API token".into(),
                status: CheckStatus::Fail,
                detail: format!("No credentials: {}", e),
            });
        }
    }

    // 6. LOCO booking connectivity (optional)
    if test_loco {
        if let Ok(creds) = &creds_result {
            let rt = tokio::runtime::Runtime::new()?;
            let loco_creds = creds.clone();
            match rt.block_on(async {
                let client = crate::loco::client::LocoClient::new(loco_creds);
                client.booking().await
            }) {
                Ok(config) => {
                    let hosts = config
                        .get_document("ticket")
                        .ok()
                        .and_then(|t| t.get_array("lsl").ok())
                        .map(|arr| {
                            arr.iter()
                                .filter_map(|v| v.as_str())
                                .collect::<Vec<_>>()
                                .join(", ")
                        })
                        .unwrap_or_else(|| "none".into());
                    let ports = config
                        .get_document("wifi")
                        .ok()
                        .and_then(|w| w.get_array("ports").ok())
                        .map(|arr| {
                            arr.iter()
                                .filter_map(|v| v.as_i32())
                                .map(|p| p.to_string())
                                .collect::<Vec<_>>()
                                .join(", ")
                        })
                        .unwrap_or_else(|| "none".into());
                    checks.push(Check {
                        name: "LOCO booking (GETCONF)".into(),
                        status: CheckStatus::Ok,
                        detail: format!("hosts=[{}], ports=[{}]", hosts, ports),
                    });
                }
                Err(e) => {
                    checks.push(Check {
                        name: "LOCO booking (GETCONF)".into(),
                        status: CheckStatus::Fail,
                        detail: format!("Connection failed: {}", e),
                    });
                }
            }
        } else {
            checks.push(Check {
                name: "LOCO booking (GETCONF)".into(),
                status: CheckStatus::Fail,
                detail: "Skipped (no credentials)".into(),
            });
        }
    }

    // 7. Local database (SQLCipher) access
    match crate::local_db::LocalDbReader::check_access() {
        Ok(status) => {
            let (db_status, detail) = if status.decryptable {
                (
                    CheckStatus::Ok,
                    format!(
                        "Decryptable. Path: {}",
                        status.db_path.as_deref().unwrap_or("unknown")
                    ),
                )
            } else if !status.container_exists {
                (
                    CheckStatus::Fail,
                    "KakaoTalk container directory not found".into(),
                )
            } else if !status.uuid_available {
                (
                    CheckStatus::Fail,
                    "IOPlatformUUID not available (ioreg failed)".into(),
                )
            } else if !status.user_id_available {
                (
                    CheckStatus::Fail,
                    "User ID not found in KakaoTalk preferences".into(),
                )
            } else if !status.db_file_found {
                (
                    CheckStatus::Warn,
                    "Database file not found (key derivation may differ)".into(),
                )
            } else {
                (
                    CheckStatus::Warn,
                    format!(
                        "File found but decryption failed. Path: {}",
                        status.db_path.as_deref().unwrap_or("unknown")
                    ),
                )
            };
            checks.push(Check {
                name: "Local DB (SQLCipher)".into(),
                status: db_status,
                detail,
            });
        }
        Err(e) => {
            checks.push(Check {
                name: "Local DB (SQLCipher)".into(),
                status: CheckStatus::Warn,
                detail: format!("Check failed: {}", e),
            });
        }
    }

    // 7b. LOCO write safety
    checks.push(Check {
        name: "LOCO write operations".into(),
        status: if config.safety.allow_loco_write {
            CheckStatus::Warn
        } else {
            CheckStatus::Ok
        },
        detail: if config.safety.allow_loco_write {
            "ENABLED — send/delete/edit/react allowed (account ban risk)".into()
        } else {
            "Disabled (safe). Enable with safety.allow_loco_write = true".into()
        },
    });

    // 8. Protocol constants
    checks.push(Check {
        name: "Protocol constants".into(),
        status: CheckStatus::Ok,
        detail: format!(
            "handshake_key_type=16, encrypt_type=3 (AES-128-GCM), RSA=2048-bit e=3, booking={}:{}",
            "booking-loco.kakao.com", 443
        ),
    });

    // Output
    if json {
        let items: Vec<serde_json::Value> = checks
            .iter()
            .map(|c| {
                serde_json::json!({
                    "check": c.name,
                    "status": match c.status {
                        CheckStatus::Ok => "ok",
                        CheckStatus::Warn => "warn",
                        CheckStatus::Fail => "fail",
                    },
                    "detail": c.detail,
                })
            })
            .collect();
        let out = serde_json::json!({
            "checks": items,
            "recovery_state": recovery,
            "safety_state": safety,
        });
        println!("{}", serde_json::to_string_pretty(&out)?);
    } else {
        println!("openkakao-cli doctor (v{})", VERSION);
        println!();
        for c in &checks {
            let (icon, color_fn): (&str, fn(&str) -> String) = match c.status {
                CheckStatus::Ok => {
                    if color_enabled() {
                        ("OK", |s: &str| format!("{}", s.green()))
                    } else {
                        ("OK", |s: &str| s.to_string())
                    }
                }
                CheckStatus::Warn => {
                    if color_enabled() {
                        ("WARN", |s: &str| format!("{}", s.yellow()))
                    } else {
                        ("WARN", |s: &str| s.to_string())
                    }
                }
                CheckStatus::Fail => {
                    if color_enabled() {
                        ("FAIL", |s: &str| format!("{}", s.red()))
                    } else {
                        ("FAIL", |s: &str| s.to_string())
                    }
                }
            };
            println!("  [{}] {}: {}", color_fn(icon), c.name, c.detail);
        }

        if !test_loco {
            println!();
            println!("  Tip: run with --loco to also test LOCO booking connectivity.");
        }
        println!(
            "  Tip: run 'openkakao-cli auth-status --json' for the raw persisted recovery state."
        );
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// Self-check (닥터) — pure-local diagnosis and bounded repair (R7).
//
// `okc doctor --self-check [--json]` runs the [`openkakao_cli::doctor::Doctor`]
// with the *real* local probes below. Every probe reads only local state
// (state.json, the loaded config, and the local SQLCipher database check) and
// performs NO external/network contact (R7.4). The safety-gate probe is
// diagnostic only and its values are never modified by any repair (R7.8).
// ---------------------------------------------------------------------------

use std::cell::RefCell;

use openkakao_cli::doctor as selfcheck;
use selfcheck::{CheckKind, CheckProbe, Doctor};

/// Runtime health probe (R7.1). Reads the persisted recovery snapshot to tell
/// whether the internal processing is in a healthy state. This is diagnostic
/// only in the one-shot CLI: restarting a *live* supervisor is the menubar
/// app's job (the bounded-restart capability itself is proven by the fake
/// process adapter in the self-check tests).
struct RuntimeProbe {
    snapshot: std::result::Result<crate::state::RecoverySnapshot, String>,
}

impl RuntimeProbe {
    fn new() -> Self {
        Self {
            snapshot: recovery_snapshot().map_err(|error| error.to_string()),
        }
    }
}

impl CheckProbe for RuntimeProbe {
    fn name(&self) -> String {
        "자동 답변 동작 상태".to_string()
    }
    fn kind(&self) -> CheckKind {
        CheckKind::Runtime
    }
    fn check(&self) -> selfcheck::CheckStatus {
        match &self.snapshot {
            Err(error) => selfcheck::CheckStatus::Fail(format!(
                "동작 상태를 읽지 못했어요: {error}"
            )),
            Ok(snapshot) => {
                if let Some(secs) = snapshot.auth_cooldown_remaining_secs {
                    selfcheck::CheckStatus::Warn(format!(
                        "로그인 잠깐 쉬는 중이에요(약 {secs}초 뒤 다시 시도)."
                    ))
                } else if snapshot.consecutive_failures > 0 {
                    selfcheck::CheckStatus::Warn(format!(
                        "최근에 {}번 실패했어요. 잠시 뒤 자동으로 다시 시도해요.",
                        snapshot.consecutive_failures
                    ))
                } else {
                    selfcheck::CheckStatus::Ok
                }
            }
        }
    }
    fn guidance(&self) -> String {
        "자동 답변이 멈춰 있으면 메뉴에서 다시 시작하거나 카카오톡을 켠 뒤 다시 눌러 주세요."
            .to_string()
    }
}

/// The operational (non-safety-gate) config values the config probe validates
/// and can restore to defaults. These are timing/privacy values only; the
/// safety-gate opt-ins, allowlist, and database-authoritative rules are NOT
/// part of this snapshot and are never touched (R7.8).
#[derive(Clone)]
struct ConfigValidity {
    send_interval: Option<u64>,
    hook_interval: Option<u64>,
    webhook_interval: Option<u64>,
    hook_timeout: Option<u64>,
    webhook_timeout: Option<u64>,
    privacy_mode: Option<String>,
}

impl ConfigValidity {
    /// Default operational values (mirror `SafetyConfig::default`).
    fn defaults() -> Self {
        Self {
            send_interval: Some(10),
            hook_interval: Some(2),
            webhook_interval: Some(2),
            hook_timeout: Some(20),
            webhook_timeout: Some(10),
            privacy_mode: None,
        }
    }

    /// First plain-language problem found, if any.
    fn problem(&self) -> Option<String> {
        for (label, value) in [
            ("전송 간격", self.send_interval),
            ("훅 간격", self.hook_interval),
            ("웹훅 간격", self.webhook_interval),
            ("훅 제한 시간", self.hook_timeout),
            ("웹훅 제한 시간", self.webhook_timeout),
        ] {
            if value == Some(0) {
                return Some(format!("{label} 값이 0이라 사용할 수 없어요."));
            }
        }
        if let Some(mode) = self.privacy_mode.as_deref() {
            if mode != "local" && mode != "remote_explicit" {
                return Some(format!("개인정보 모드 값 \"{mode}\"은(는) 알 수 없는 값이에요."));
            }
        }
        None
    }
}

/// Config-validity probe (R7.1). A `Fail` (an out-of-range operational value) is
/// repaired by restoring the offending values to their defaults **in memory**
/// (R7.2); the user's config file is never rewritten and no safety-gate value
/// is touched (R7.8).
struct ConfigProbe {
    state: RefCell<ConfigValidity>,
}

impl ConfigProbe {
    fn new(config: &OpenKakaoConfig) -> Self {
        Self {
            state: RefCell::new(ConfigValidity {
                send_interval: config.safety.min_unattended_send_interval_secs,
                hook_interval: config.safety.min_hook_interval_secs,
                webhook_interval: config.safety.min_webhook_interval_secs,
                hook_timeout: config.safety.hook_timeout_secs,
                webhook_timeout: config.safety.webhook_timeout_secs,
                privacy_mode: config.model.privacy_mode.clone(),
            }),
        }
    }
}

impl CheckProbe for ConfigProbe {
    fn name(&self) -> String {
        "설정 값 유효성".to_string()
    }
    fn kind(&self) -> CheckKind {
        CheckKind::Config
    }
    fn check(&self) -> selfcheck::CheckStatus {
        match self.state.borrow().problem() {
            Some(reason) => selfcheck::CheckStatus::Fail(reason),
            None => selfcheck::CheckStatus::Ok,
        }
    }
    fn is_repairable(&self) -> bool {
        true
    }
    fn attempt_repair(&self) {
        // Restore only the offending operational value(s) to defaults. Safety
        // opt-ins / allowlist / DB-authoritative rules are not part of this
        // snapshot and are never modified (R7.8).
        let defaults = ConfigValidity::defaults();
        let mut state = self.state.borrow_mut();
        if state.send_interval == Some(0) {
            state.send_interval = defaults.send_interval;
        }
        if state.hook_interval == Some(0) {
            state.hook_interval = defaults.hook_interval;
        }
        if state.webhook_interval == Some(0) {
            state.webhook_interval = defaults.webhook_interval;
        }
        if state.hook_timeout == Some(0) {
            state.hook_timeout = defaults.hook_timeout;
        }
        if state.webhook_timeout == Some(0) {
            state.webhook_timeout = defaults.webhook_timeout;
        }
        if let Some(mode) = state.privacy_mode.clone() {
            if mode != "local" && mode != "remote_explicit" {
                state.privacy_mode = defaults.privacy_mode;
            }
        }
    }
    fn guidance(&self) -> String {
        "설정 값을 확인해 주세요. 설정 파일에서 잘못된 값을 지우면 기본값으로 돌아가요.".to_string()
    }
}

/// Local-database access probe (R7.1). A `Fail` (database not reachable) is
/// repaired by re-running the local access check — i.e. a reconnect attempt
/// (R7.2). The check is fully local (SQLCipher on disk), never a network call.
struct LocalDbProbe;

impl CheckProbe for LocalDbProbe {
    fn name(&self) -> String {
        "로컬 데이터베이스 접근".to_string()
    }
    fn kind(&self) -> CheckKind {
        CheckKind::LocalDb
    }
    fn check(&self) -> selfcheck::CheckStatus {
        match openkakao_cli::local_db::LocalDbReader::check_access() {
            Ok(status) if status.decryptable => selfcheck::CheckStatus::Ok,
            Ok(_) => selfcheck::CheckStatus::Fail(
                "로컬 데이터베이스를 열 수 없어요.".to_string(),
            ),
            Err(error) => {
                selfcheck::CheckStatus::Fail(format!("연결 확인에 실패했어요: {error}"))
            }
        }
    }
    fn is_repairable(&self) -> bool {
        true
    }
    fn attempt_repair(&self) {
        // Reconnect attempt: re-open the local database. `check()` re-runs the
        // same access check afterwards, so no state needs to be held here.
        let _ = openkakao_cli::local_db::LocalDbReader::check_access();
    }
    fn guidance(&self) -> String {
        "카카오톡이 켜져 있고 로그인되어 있는지 확인한 뒤 다시 눌러 주세요.".to_string()
    }
}

/// Safety-gate consistency probe (R7.1). This is **diagnostic only**: it never
/// modifies the whitelist, the opt-in flags, or the database-authoritative
/// rules (R7.8). An inconsistency is reported with plain-language guidance and
/// the state is left unchanged (R7.6).
struct SafetyGateProbe {
    allow_ax_send: bool,
    allow_auto_reply: bool,
    allow_loco_write: bool,
    allowed_send_chats: usize,
}

impl SafetyGateProbe {
    fn new(config: &OpenKakaoConfig) -> Self {
        Self {
            allow_ax_send: config.safety.allow_ax_send,
            allow_auto_reply: config.safety.allow_auto_reply,
            allow_loco_write: config.safety.allow_loco_write,
            allowed_send_chats: config.safety.allowed_send_chats.len(),
        }
    }
}

impl CheckProbe for SafetyGateProbe {
    fn name(&self) -> String {
        "안전 규칙 정합성".to_string()
    }
    fn kind(&self) -> CheckKind {
        CheckKind::SafetyGate
    }
    fn check(&self) -> selfcheck::CheckStatus {
        // AX send enabled but no room is allowed to receive: sending can never
        // happen, which is an inconsistent (but safe) state worth flagging.
        if self.allow_ax_send && self.allowed_send_chats == 0 {
            return selfcheck::CheckStatus::Fail(
                "전송이 켜져 있는데 허용된 대화방이 없어요.".to_string(),
            );
        }
        // Auto reply requires AX send to be on to ever produce a real send.
        if self.allow_auto_reply && !self.allow_ax_send {
            return selfcheck::CheckStatus::Fail(
                "자동 답변이 켜져 있는데 전송이 꺼져 있어요.".to_string(),
            );
        }
        // LOCO writes must stay quarantined; enabled is risky but user-chosen.
        if self.allow_loco_write {
            return selfcheck::CheckStatus::Warn(
                "위험한 LOCO 쓰기가 켜져 있어요(권장하지 않아요).".to_string(),
            );
        }
        selfcheck::CheckStatus::Ok
    }
    fn is_repairable(&self) -> bool {
        // Never repairable: safety-gate values are never modified (R7.8).
        false
    }
    fn guidance(&self) -> String {
        "안전 규칙은 자동으로 바꾸지 않아요. 설정 파일의 [safety] 항목을 직접 확인해 주세요.".to_string()
    }
}

/// Build the real self-check probes from the loaded config and local state.
fn build_self_check_probes(config: &OpenKakaoConfig) -> Vec<Box<dyn CheckProbe>> {
    vec![
        Box::new(RuntimeProbe::new()),
        Box::new(ConfigProbe::new(config)),
        Box::new(LocalDbProbe),
        Box::new(SafetyGateProbe::new(config)),
    ]
}

/// `okc doctor --self-check [--json]`: run the pure-local self-check. Diagnose
/// runtime health, config validity, local DB access, and safety-gate
/// consistency; attempt bounded repair of repairable faults; re-check; and
/// report in plain language. Makes NO external/network contact (R7.4).
pub fn cmd_doctor_self_check(json: bool, config: &OpenKakaoConfig) -> Result<()> {
    let doctor = Doctor::new(build_self_check_probes(config));
    let report = doctor.run();

    if json {
        let checks: Vec<serde_json::Value> = report
            .items
            .iter()
            .map(|item| {
                serde_json::json!({
                    "check": item.name,
                    "kind": item.kind.as_str(),
                    "status": item.status.as_str(),
                    "detail": item.status.detail(),
                    "repaired": item.repaired,
                })
            })
            .collect();
        let repairs: Vec<serde_json::Value> = report
            .repairs
            .iter()
            .map(|record| {
                serde_json::json!({
                    "check": record.name,
                    "kind": record.kind.as_str(),
                    "attempts": record.outcome.attempts,
                    "repaired": record.outcome.repaired,
                    "guidance": record.outcome.guidance,
                })
            })
            .collect();
        let out = serde_json::json!({
            "ok": true,
            "action": "doctor_self_check",
            "external_contact": false,
            "all_ok": report.all_ok(),
            "checks": checks,
            "repairs": repairs,
        });
        println!("{}", serde_json::to_string_pretty(&out)?);
    } else {
        println!("openkakao-cli 자가 점검 (v{})", VERSION);
        println!();
        for item in &report.items {
            let icon = match item.status {
                selfcheck::CheckStatus::Ok => "정상",
                selfcheck::CheckStatus::Warn(_) => "주의",
                selfcheck::CheckStatus::Fail(_) => "문제",
            };
            let repaired = if item.repaired { " (고쳤어요)" } else { "" };
            match item.status.detail() {
                Some(detail) => println!("  [{icon}] {}: {detail}{repaired}", item.name),
                None => println!("  [{icon}] {}{repaired}", item.name),
            }
        }
        // Plain-language guidance for anything still unresolved (R7.6).
        let unresolved: Vec<&selfcheck::RepairRecord> = report
            .repairs
            .iter()
            .filter(|record| record.outcome.guidance.is_some() && !record.outcome.repaired)
            .collect();
        if !unresolved.is_empty() {
            println!();
            println!("아직 남은 문제와 다음에 할 일:");
            for record in unresolved {
                if let Some(guidance) = &record.outcome.guidance {
                    println!("  - {}: {guidance}", record.name);
                }
            }
        }
    }

    Ok(())
}
