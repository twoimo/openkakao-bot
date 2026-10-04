use crate::resource_layout::{
    self, validate_path, DevOverrides, Kind, ResourceError, ResourceLayout,
};
use serde::Serialize;
use serde_json::{json, Value};
use std::collections::{HashMap, HashSet};
use std::ffi::{OsStr, OsString};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::ffi::{OsStrExt, OsStringExt};
use std::os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use thiserror::Error;

const DEFAULT_TIMEOUT: Duration = Duration::from_secs(8);
const SNAPSHOT_TIMEOUT: Duration = Duration::from_secs(25);
const BROWSER_TOOL_TIMEOUT: Duration = Duration::from_secs(90);
const BROWSER_TOOL_ABORT_GRACE: Duration = Duration::from_secs(2);
const MODEL_SWAP_TIMEOUT: Duration = Duration::from_secs(120);
const MODEL_SWAP_RECOVERY_TIMEOUT: Duration = Duration::from_secs(120);
const MLX_LAUNCH_TIMEOUT: Duration = Duration::from_secs(150);
const OUTPUT_LIMIT_BYTES: usize = 4 * 1024 * 1024;
const BROWSER_TOOL_OUTPUT_LIMIT_BYTES: usize = 96 * 1024;
const BROWSER_TOOL_RESULT_LIMIT_BYTES: usize = 64 * 1024;
const BROWSER_TOOL_TASK_LIMIT_BYTES: usize = 16 * 1024;
const BROWSER_TOOL_JOB_ID_LIMIT: usize = 64;
const JOB_EVENT_CAP: usize = 8;
const JOB_EVENT_MAX_AGE_SECS: f64 = 300.0;
const ABORT_STATE_NAME: &str = "alden-abort.json";
const VOICE_STATUS_NAME: &str = "alden-voice-status.json";
const VOICE_STATUS_MAX_AGE_SECS: u64 = 5 * 60;
const STATE_FILE_LIMIT_BYTES: u64 = 4096;
const ABORT_EPOCH_MAX: u64 = 9_007_199_254_740_991;
const ABORT_LOCK_NAME: &str = "alden-abort.lock";
const ABORT_LOCK_TIMEOUT: Duration = Duration::from_millis(250);
const WAKE_PHRASE: &str = "올든";
const WAKE_THRESHOLD: f64 = 0.65;
const RESIDENT_MODEL_ID: &str = "ddalcu/Qwen3.8-Flash-Next-MLX-Serve-iQ-MLX-3.3bpw";
const LEGACY_RESIDENT_MODEL_ID: &str = "ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit";
const SWAP_MODEL_ID: &str = "ddalcu/Qwen3.8-27B-MLX-Serve-4bit";
// The on-disk directory names the app-owned server publishes in /v1/models.
const RESIDENT_MODEL_NAME: &str = "Qwen3.8-Flash-Next-MLX-Serve-iQ-MLX-3.3bpw";
const LEGACY_RESIDENT_MODEL_NAME: &str = "Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit";
const SWAP_MODEL_NAME: &str = "Qwen3.8-27B-MLX-Serve-4bit";
const MLX_SERVER_STATUS_ACTION: &str = "mlx-server-status";
const MLX_SERVER_LAUNCH_ACTION: &str = "mlx-server-launch";
const MLX_SERVER_STOP_ACTION: &str = "mlx-server-stop";
const MODEL_SWAP_OPT_IN: &str = "qwen38-27b-explicit-v1";
const MODEL_SWAP_CANCEL_DIR: &str = "model-swap-cancel";

fn is_resident_model_id(candidate: &str) -> bool {
    candidate == RESIDENT_MODEL_ID || candidate == LEGACY_RESIDENT_MODEL_ID
}

fn is_local_model_id(candidate: &str) -> bool {
    is_resident_model_id(candidate) || candidate == SWAP_MODEL_ID
}

fn is_model_set_id(candidate: &str) -> bool {
    candidate == RESIDENT_MODEL_ID || candidate == SWAP_MODEL_ID
}

fn is_resident_model_name(candidate: &str) -> bool {
    candidate == RESIDENT_MODEL_NAME || candidate == LEGACY_RESIDENT_MODEL_NAME
}

fn is_local_model_name(candidate: &str) -> bool {
    is_resident_model_name(candidate) || candidate == SWAP_MODEL_NAME
}

fn is_snapshot_model_id(candidate: &str) -> bool {
    is_local_model_id(candidate.strip_prefix("mlx/").unwrap_or(candidate))
}

const VOICE_TTS_OUT_NAME: &str = "alden-voice-out.wav";
static VOICE_SESSION_START_LOCK: Mutex<()> = Mutex::new(());

#[derive(Debug, Error)]
pub enum BridgeError {
    #[error("python_spawn_failed")]
    Spawn,
    #[error("python_wait_failed")]
    Wait,
    #[error("python_timed_out")]
    Timeout,
    #[error("python_cancelled")]
    Cancelled,
    #[error("python_output_too_large")]
    OutputTooLarge,
    #[error("python_failed")]
    Exit,
    #[error("python_output_empty")]
    Empty,
    #[error("python_json_invalid")]
    Json,
    #[error("action_not_allowed")]
    ActionNotAllowed,
    #[error("state_io_failed")]
    StateIo,
    #[error("resource_missing")]
    ResourceMissing,
    #[error("resource_unsafe")]
    ResourceUnsafe,
    #[error("development_resources_disabled")]
    DevelopmentDisabled,
    #[error("python_environment_missing_or_unsafe")]
    PythonEnv,
    #[error("browser_environment_missing_or_unsafe")]
    BrowserEnv,
    #[cfg_attr(not(test), allow(dead_code))]
    #[error("voice_environment_missing")]
    VoiceEnv,
    #[cfg_attr(not(test), allow(dead_code))]
    #[error("voice_script_missing")]
    VoiceScript,
    #[error("voice_session_already_running")]
    VoiceSessionAlreadyRunning,
    #[error("voice_session_process_check_failed")]
    VoiceProcessCheck,
    #[error("alden_wake_model_unavailable")]
    WakeModelUnavailable,
}

#[derive(Clone)]
pub struct PythonBridge {
    config: Arc<BridgeConfig>,
    cancellations: Arc<Mutex<HashMap<String, CancellationHandle>>>,
    jobs: Arc<Mutex<HashMap<String, SafeJobEvent>>>,
}

#[derive(Clone)]
struct CancellationHandle {
    flag: Arc<AtomicBool>,
    cooperative_marker: Option<PathBuf>,
    global_abort_flag: Option<Arc<AtomicBool>>,
}

struct ProcessControl<'a> {
    stdin_payload: Option<&'a [u8]>,
    hard_cancel_flag: Arc<AtomicBool>,
    global_abort: Option<(Arc<AtomicBool>, Duration)>,
    cooperative_marker: Option<&'a Path>,
    recovery_timeout: Duration,
}

/// One python invocation. Callers name only the fields they differ on and take
/// the rest from PythonRun::default, so the common path no longer passes a
/// positional run of constants.
struct PythonRun<'a> {
    extra: &'a [String],
    timeout: Duration,
    token_id: Option<&'a str>,
    cooperative_cancel: bool,
    output_limit: usize,
    stdin_payload: Option<&'a [u8]>,
    global_abort_grace: Option<Duration>,
    browser_runtime: bool,
}

impl Default for PythonRun<'_> {
    fn default() -> Self {
        PythonRun {
            extra: &[],
            timeout: DEFAULT_TIMEOUT,
            token_id: None,
            cooperative_cancel: false,
            output_limit: OUTPUT_LIMIT_BYTES,
            stdin_payload: None,
            global_abort_grace: None,
            browser_runtime: false,
        }
    }
}

#[derive(Debug)]
struct BridgeConfig {
    python: PathBuf,
    browser_python: PathBuf,
    browser_browsers_path: PathBuf,
    resources: Result<ResourceLayout, ResourceError>,
    state_root: PathBuf,
    logs_dir: PathBuf,
}

#[derive(Debug)]
struct VoiceSessionPlan {
    python: PathBuf,
    script: PathBuf,
    tts_out: PathBuf,
}

#[derive(Debug, Serialize)]
pub struct SafeRoom {
    chat_id: i64,
    title: String,
    live: bool,
    auto_reply: bool,
    open_jobs: u64,
    #[serde(rename = "replyReadiness")]
    reply_readiness: String,
}

#[derive(Debug, Serialize, PartialEq, Eq, Clone)]
pub struct SafeAvailableChat {
    chat_id: i64,
    title: String,
    catalog: bool,
    live: bool,
}

#[derive(Clone, Debug, Serialize)]
pub struct SafeJobEvent {
    #[serde(rename = "jobId")]
    job_id: String,
    kind: String,
    stage: String,
    load: f64,
    time: f64,
    #[serde(rename = "errorCode")]
    error_code: Option<String>,
}

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct SafeEmergencyState {
    schema_version: u64,
    epoch: u64,
    latched: bool,
    reason: String,
}

#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct DiskEmergencyState {
    schema_version: u64,
    epoch: u64,
    latched: bool,
    reason: String,
}

impl Default for SafeEmergencyState {
    fn default() -> Self {
        Self {
            schema_version: 1,
            epoch: 0,
            latched: false,
            reason: String::new(),
        }
    }
}

#[derive(Debug, Default, Serialize)]
pub struct TerminalCounts {
    sent: u64,
    skipped: u64,
    delivery_unknown: u64,
    burst_superseded: u64,
}

#[derive(Debug, Serialize)]
pub struct ContextSync {
    mode: &'static str,
    waited: bool,
}

#[derive(Debug, Serialize, Clone)]
pub struct SafeVoiceStatus {
    manual_running: bool,
    available: bool,
    state: String,
    rms: f64,
    output_rms: f64,
    error_code: Option<String>,
    wake_source: String,
    updated_at: u64,
    wake_phrase: String,
    threshold: f64,
    custom_model_selected: bool,
}

impl Default for SafeVoiceStatus {
    fn default() -> Self {
        Self {
            manual_running: false,
            available: false,
            state: "unavailable".to_string(),
            rms: 0.0,
            output_rms: 0.0,
            error_code: None,
            wake_source: "none".to_string(),
            updated_at: 0,
            wake_phrase: WAKE_PHRASE.to_string(),
            threshold: WAKE_THRESHOLD,
            custom_model_selected: false,
        }
    }
}

#[derive(Debug, Serialize)]
pub struct SafeRuntimeSnapshot {
    available: bool,
    rooms: Vec<SafeRoom>,
    #[serde(rename = "availableChats")]
    available_chats: Vec<SafeAvailableChat>,
    jobs: Vec<SafeJobEvent>,
    recent_receipts: Vec<SafeRecentReceipt>,
    job_load: f64,
    background: SafeBackground,
    #[serde(rename = "onDevice")]
    on_device: SafeOnDevice,
    pipeline: SafePipeline,
    terminal_counts: TerminalCounts,
    context_sync: ContextSync,
    reply_model_id: Option<String>,
    voice: SafeVoiceStatus,
    error_code: Option<String>,
}

#[derive(Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
struct SafePipeline {
    active: bool,
    stage: String,
    stage_index: u32,
    stage_total: u32,
    outcome: String,
}

impl Default for SafePipeline {
    fn default() -> Self {
        Self {
            active: false,
            stage: "none".to_string(),
            stage_index: 0,
            stage_total: 0,
            outcome: "unknown".to_string(),
        }
    }
}

#[derive(Debug, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
struct SafeOnDevice {
    available: bool,
    chip: String,
    cores: u32,
    memory_gb: f64,
    apple_silicon: bool,
    engine: String,
    recommended_model: String,
    quant: String,
    verified: bool,
    status_label: String,
    status_detail: String,
}

impl Default for SafeOnDevice {
    fn default() -> Self {
        Self {
            available: false,
            chip: String::new(),
            cores: 0,
            memory_gb: 0.0,
            apple_silicon: false,
            engine: String::new(),
            recommended_model: String::new(),
            quant: String::new(),
            verified: false,
            status_label: String::new(),
            status_detail: String::new(),
        }
    }
}

#[derive(Debug, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
struct SafeBackground {
    activity: f64,
    caption: String,
    reply_load: f64,
    geeknews: SafeBackgroundSource,
    db_sync: SafeBackgroundSource,
}

#[derive(Debug, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
struct SafeBackgroundSource {
    state: String,
    activity: f64,
    caption: String,
}

impl SafeBackgroundSource {
    fn unknown() -> Self {
        Self {
            state: "unknown".to_string(),
            activity: 0.0,
            caption: String::new(),
        }
    }
}

impl SafeBackground {
    fn fallback(activity: f64, reply_load: f64) -> Self {
        Self {
            activity: round_activity(activity),
            caption: String::new(),
            reply_load: round_activity(reply_load),
            geeknews: SafeBackgroundSource::unknown(),
            db_sync: SafeBackgroundSource::unknown(),
        }
    }
}

#[derive(Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
struct SafeRecentReceipt {
    chat_id: i64,
    title: String,
    display_time: String,
    clock: String,
    outcome: String,
    outcome_text: String,
    reason_code: String,
    reason_text: String,
    retrieval_state: String,
}

#[derive(Debug, Serialize, PartialEq)]
pub struct SafeBrowserToolResult {
    ok: bool,
    status: String,
    #[serde(rename = "errorCode")]
    error_code: String,
    result: String,
}

impl SafeBrowserToolResult {
    fn failed(error_code: &str) -> Self {
        Self {
            ok: false,
            status: "failed".to_string(),
            error_code: error_code.to_string(),
            result: String::new(),
        }
    }
}

impl PythonBridge {
    pub fn new() -> Self {
        Self {
            config: Arc::new(BridgeConfig::discover()),
            cancellations: Arc::new(Mutex::new(HashMap::new())),
            jobs: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    /// Fixed app-owned checkpoint metadata, with no graph read or subprocess.
    pub fn knowledge_revision(&self) -> Option<String> {
        let path = self.config.state_root.join("knowledge/osk/sync.json");
        validate_path(&path, Kind::Data).ok()?;
        let file = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_NOFOLLOW)
            .open(path)
            .ok()?;
        let meta = file.metadata().ok()?;
        if !meta.is_file() {
            return None;
        }
        Some(format!(
            "{}:{}:{}:{}:{}:{}:{}",
            meta.dev(),
            meta.ino(),
            meta.mtime(),
            meta.mtime_nsec(),
            meta.ctime(),
            meta.ctime_nsec(),
            meta.len()
        ))
    }

    /// App-owned, local, bounded background tick. It shares the emergency latch
    /// and never registers hooks, creates a service or adopts another worker.
    pub fn synchronize_knowledge(&self) -> Result<(), BridgeError> {
        if global_abort_is_latched(&self.config.state_root)? {
            return Ok(());
        }
        let resources = self.config.resources()?;
        resources.validate()?;
        let runtime = select_python_runtime(
            &self.config.python,
            &self.config.browser_python,
            &self.config.browser_browsers_path,
            resources.installed,
            false,
        )?;
        let script = resources.root.join("scripts/alden_osk.py");
        let hard = Arc::new(AtomicBool::new(false));
        let abort = Arc::new(AtomicBool::new(false));
        let key = "alden-osk-sync";
        {
            let mut map = self
                .cancellations
                .lock()
                .map_err(|_| BridgeError::StateIo)?;
            if map.contains_key(key) {
                return Ok(());
            }
            map.insert(
                key.into(),
                CancellationHandle {
                    flag: hard.clone(),
                    cooperative_marker: None,
                    global_abort_flag: Some(abort.clone()),
                },
            );
        }
        let args = vec![
            "-E".into(),
            "-B".into(),
            "-s".into(),
            script.to_str().ok_or(BridgeError::ResourceUnsafe)?.into(),
            "--state-root".into(),
            self.config.state_root.to_string_lossy().into_owned(),
            "--sync".into(),
            "--bin".into(),
            resources
                .root
                .join("bin/openkakao-cli")
                .to_string_lossy()
                .into_owned(),
        ];
        let result = run_process_with_recovery_env(
            &runtime.executable,
            &args,
            Duration::from_secs(120),
            OUTPUT_LIMIT_BYTES,
            ProcessControl {
                stdin_payload: None,
                hard_cancel_flag: hard,
                global_abort: Some((abort, Duration::from_secs(2))),
                cooperative_marker: None,
                recovery_timeout: Duration::ZERO,
            },
            &runtime.env,
        );
        if let Ok(mut map) = self.cancellations.lock() {
            map.remove(key);
        }
        let payload = parse_json_output(&result?)?;
        if payload.get("ok").and_then(Value::as_bool) != Some(true) {
            return Err(BridgeError::StateIo);
        }
        Ok(())
    }

    /// Native settings audit only: bypass the general dispatcher and read the
    /// persisted graph with mode=ro/query_only, no seeds, indexing or inference.
    #[cfg(target_os = "macos")]
    pub fn fetch_persisted_graph(&self) -> Result<Value, BridgeError> {
        let resources = self.config.resources()?;
        resources.validate()?;
        let runtime = select_python_runtime(
            &self.config.python,
            &self.config.browser_python,
            &self.config.browser_browsers_path,
            resources.installed,
            false,
        )?;
        let script = resources.root.join("scripts/auto_reply_knowledge_graph.py");
        let args = vec![
            "-E".into(),
            "-B".into(),
            "-s".into(),
            script.to_str().ok_or(BridgeError::ResourceUnsafe)?.into(),
            "--read-persisted-view".into(),
            self.config
                .state_root
                .to_str()
                .ok_or(BridgeError::StateIo)?
                .into(),
        ];
        let bytes = run_process_with_recovery_env(
            &runtime.executable,
            &args,
            DEFAULT_TIMEOUT,
            OUTPUT_LIMIT_BYTES,
            ProcessControl {
                stdin_payload: None,
                hard_cancel_flag: Arc::new(AtomicBool::new(false)),
                global_abort: None,
                cooperative_marker: None,
                recovery_timeout: Duration::ZERO,
            },
            &[],
        )?;
        Ok(sanitize_knowledge_graph(&parse_json_output(&bytes)?))
    }

    pub fn fetch_snapshot(
        &self,
        token_id: Option<&str>,
    ) -> Result<SafeRuntimeSnapshot, BridgeError> {
        let bytes = self.run_python(&[], SNAPSHOT_TIMEOUT, token_id, false)?;
        let value = parse_json_output(&bytes)?;
        let mut snapshot = sanitize_snapshot(&value);
        snapshot.voice = self.voice_status();
        self.merge_jobs(&mut snapshot);
        Ok(snapshot)
    }

    /// Read only the app's bounded status/latch files; no Python or models.
    pub fn voice_status(&self) -> SafeVoiceStatus {
        let mut status = read_voice_status(&self.config.state_root);
        status.manual_running = self
            .cancellations
            .lock()
            .is_ok_and(|map| map.contains_key("voice-session"));
        if status.manual_running && !status.available {
            status.available = true;
            status.state = "idle".to_string();
        }
        match global_abort_is_latched(&self.config.state_root) {
            Ok(false) => {}
            latch => {
                status.rms = 0.0;
                status.output_rms = 0.0;
                status.state = "aborted".to_string();
                status.error_code = Some(if matches!(latch, Ok(true)) {
                    "global_abort".to_string()
                } else {
                    "voice_status_latch_invalid".to_string()
                });
            }
        }
        status
    }

    // Every argument is a settings-action payload field the frontend sends, so
    // the length is the invoke contract rather than an accidental signature.
    #[allow(clippy::too_many_arguments)]
    pub fn fetch_settings_action(
        &self,
        action: &str,
        query: Option<&str>,
        node_id: Option<&str>,
        chat_id: Option<&str>,
        model: Option<&str>,
        explicit_opt_in: Option<bool>,
        token_id: Option<&str>,
    ) -> Result<Value, BridgeError> {
        let args = settings_action_args(
            action,
            query,
            node_id,
            chat_id,
            model,
            explicit_opt_in,
            token_id,
        )?;
        let is_swap = action == "model-swap";
        let is_mlx_launch = action == MLX_SERVER_LAUNCH_ACTION;
        let is_history = matches!(action, "history-rooms" | "history-messages");
        let swap_job_id = token_id.unwrap_or("model-swap");
        if is_swap {
            self.begin_job(swap_job_id, "model_swap", "swap", 0.9);
        }
        let result: Result<Value, BridgeError> = (|| {
            let bytes = self.run_python(
                &args,
                if is_swap {
                    MODEL_SWAP_TIMEOUT
                } else if is_mlx_launch {
                    MLX_LAUNCH_TIMEOUT
                } else if is_history {
                    SNAPSHOT_TIMEOUT
                } else {
                    DEFAULT_TIMEOUT
                },
                if is_swap { token_id } else { None },
                is_swap,
            )?;
            let value = parse_json_output(&bytes)?;
            Ok(match action {
                "models" => sanitize_models(&value),
                "dream-rsi-status" => sanitize_dream_rsi(&value),
                "knowledge-graph-status" => sanitize_knowledge_status(&value),
                "knowledge-graph" => sanitize_knowledge_graph(&value),
                "knowledge-graph-focus" => sanitize_knowledge_focus(&value, node_id.unwrap_or("")),
                "model-owner-status" => sanitize_model_owner_status(&value),
                MLX_SERVER_STATUS_ACTION => sanitize_mlx_server_status(&value),
                MLX_SERVER_LAUNCH_ACTION => sanitize_mlx_lifecycle(&value, true),
                MLX_SERVER_STOP_ACTION => sanitize_mlx_lifecycle(&value, false),
                "model-set" => sanitize_model_action(
                    &value,
                    action,
                    model.ok_or(BridgeError::ActionNotAllowed)?,
                ),
                "model-prepare" => sanitize_model_action(&value, action, SWAP_MODEL_ID),
                "model-swap" => sanitize_model_swap_action(&value),
                "room-upsert" => sanitize_room_upsert(&value),
                "history-rooms"
                | "history-messages"
                | "voice-history-sessions"
                | "voice-history-messages"
                | "db-sync-history"
                | "reply-history"
                | "geeknews-history"
                | "room-catalog"
                | "room-delete" => value,
                _ => return Err(BridgeError::ActionNotAllowed),
            })
        })();
        if is_swap {
            self.finish_job(swap_job_id);
        }
        result
    }

    pub fn run_browser_tool(
        &self,
        job_id: &str,
        task: &str,
        token_id: Option<&str>,
    ) -> Result<SafeBrowserToolResult, BridgeError> {
        if token_id.is_some_and(|token| !valid_browser_tool_job_id(token)) {
            return Err(BridgeError::ActionNotAllowed);
        }
        let args = browser_tool_args(job_id, task)?;
        let internal_token = format!("browser-tool-{job_id}");
        let cancellation_token = token_id.unwrap_or(&internal_token);
        self.begin_job(job_id, "browser", "running", 0.7);
        let result: Result<SafeBrowserToolResult, BridgeError> = (|| {
            let bytes = self.run_python_with_output_limit(PythonRun {
                extra: &args,
                timeout: BROWSER_TOOL_TIMEOUT,
                token_id: Some(cancellation_token),
                output_limit: BROWSER_TOOL_OUTPUT_LIMIT_BYTES,
                stdin_payload: Some(task.as_bytes()),
                global_abort_grace: Some(BROWSER_TOOL_ABORT_GRACE),
                browser_runtime: true,
                ..PythonRun::default()
            })?;
            let value = parse_json_output(&bytes)?;
            Ok(sanitize_browser_tool_result(&value))
        })();
        match &result {
            Ok(value) if !value.error_code.is_empty() => {
                self.set_job_error(job_id, Some(&value.error_code));
            }
            Err(error) => {
                let error_code = error.to_string();
                self.set_job_error(job_id, Some(&error_code));
            }
            _ => self.set_job_error(job_id, None),
        }
        self.finish_job(job_id);
        result
    }

    fn begin_job(&self, job_id: &str, kind: &str, stage: &str, load: f64) {
        let key = bounded_job_id(job_id);
        if key.is_empty() {
            return;
        }
        let event = SafeJobEvent {
            job_id: key.clone(),
            kind: kind.chars().take(32).collect(),
            stage: stage.chars().take(32).collect(),
            load: clamp01(load),
            time: epoch_seconds(),
            error_code: None,
        };
        let mut jobs = self
            .jobs
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        jobs.insert(key, event);
        if jobs.len() > JOB_EVENT_CAP {
            let mut newest = jobs
                .iter()
                .map(|(job_id, event)| (job_id.clone(), event.time))
                .collect::<Vec<_>>();
            newest.sort_by(|left, right| {
                right
                    .1
                    .total_cmp(&left.1)
                    .then_with(|| left.0.cmp(&right.0))
            });
            for (job_id, _) in newest.into_iter().skip(JOB_EVENT_CAP) {
                jobs.remove(&job_id);
            }
        }
    }

    fn set_job_error(&self, job_id: &str, error_code: Option<&str>) {
        let key = bounded_job_id(job_id);
        if key.is_empty() {
            return;
        }
        let mut jobs = self
            .jobs
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if let Some(event) = jobs.get_mut(&key) {
            event.error_code = error_code.map(|value| value.chars().take(64).collect());
        }
    }

    fn finish_job(&self, job_id: &str) {
        let key = bounded_job_id(job_id);
        if key.is_empty() {
            return;
        }
        self.jobs
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .remove(&key);
    }

    fn jobs_snapshot(&self) -> Vec<SafeJobEvent> {
        let now = epoch_seconds();
        let mut jobs = self
            .jobs
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        jobs.retain(|_, event| {
            event.time.is_finite()
                && (now == 0.0 || event.time > now || now - event.time <= JOB_EVENT_MAX_AGE_SECS)
        });
        let mut snapshot = jobs.values().cloned().collect::<Vec<_>>();
        snapshot.sort_by(|left, right| {
            right
                .time
                .total_cmp(&left.time)
                .then_with(|| left.job_id.cmp(&right.job_id))
        });
        snapshot.truncate(JOB_EVENT_CAP);
        snapshot
    }

    fn merge_jobs(&self, snapshot: &mut SafeRuntimeSnapshot) {
        snapshot.jobs = self.jobs_snapshot();
    }

    pub fn cancel(&self, token_id: &str) -> bool {
        let Ok(map) = self.cancellations.lock() else {
            return false;
        };
        let Some(handle) = map.get(token_id) else {
            return false;
        };
        if handle.cooperative_marker.is_some() {
            return false;
        }
        handle.flag.store(true, Ordering::SeqCst);
        true
    }

    pub fn cancel_model_swap(&self, token_id: &str) -> bool {
        if !valid_model_swap_token(token_id) {
            return false;
        }
        let Ok(map) = self.cancellations.lock() else {
            return false;
        };
        if let Some(handle) = map.get(token_id) {
            return handle
                .cooperative_marker
                .as_deref()
                .is_some_and(|marker| write_model_swap_cancel_marker(marker).is_ok());
        }
        // The UI can cancel before spawn_blocking has registered the child.
        // Keep a token-scoped tombstone that startup must not erase.
        write_model_swap_cancel_marker(
            &self
                .config
                .state_root
                .join(MODEL_SWAP_CANCEL_DIR)
                .join(format!("{token_id}.json")),
        )
        .is_ok()
    }

    pub fn global_abort(&self) -> Result<(), BridgeError> {
        let map = self
            .cancellations
            .lock()
            .map_err(|_| BridgeError::StateIo)?;
        // Publish the latch before starting a browser grace deadline so the
        // Python token can observe it and run Playwright cleanup. Holding the
        // registry lock also prevents a child from registering in between.
        let latch_result = write_global_abort(&self.config.state_root);
        let mut marker_failed = false;
        for handle in map.values() {
            if let Some(marker) = handle.cooperative_marker.as_deref() {
                if write_model_swap_cancel_marker(marker).is_err() {
                    marker_failed = true;
                    handle.flag.store(true, Ordering::SeqCst);
                }
            } else if latch_result.is_ok() {
                if let Some(global_abort_flag) = handle.global_abort_flag.as_ref() {
                    global_abort_flag.store(true, Ordering::SeqCst);
                } else {
                    handle.flag.store(true, Ordering::SeqCst);
                }
            } else {
                // Without a durable latch the Python token cannot safely
                // observe the abort, so terminate owned children immediately.
                handle.flag.store(true, Ordering::SeqCst);
            }
        }
        drop(map);
        latch_result?;
        if marker_failed {
            return Err(BridgeError::StateIo);
        }
        Ok(())
    }

    pub fn emergency_state(&self) -> Result<SafeEmergencyState, BridgeError> {
        read_global_abort_state(&self.config.state_root)
    }

    pub fn operator_resume(
        &self,
        explicit_opt_in: bool,
    ) -> Result<SafeEmergencyState, BridgeError> {
        if !explicit_opt_in {
            return Err(BridgeError::ActionNotAllowed);
        }
        let _map = self
            .cancellations
            .lock()
            .map_err(|_| BridgeError::StateIo)?;
        let _state_lock = lock_global_abort(&self.config.state_root)?;
        let current = read_global_abort_state(&self.config.state_root)?;
        if !current.latched {
            return Err(BridgeError::ActionNotAllowed);
        }
        let next = SafeEmergencyState {
            schema_version: 1,
            epoch: current
                .epoch
                .checked_add(1)
                .filter(|epoch| *epoch <= ABORT_EPOCH_MAX)
                .ok_or(BridgeError::StateIo)?,
            latched: false,
            reason: "human_resume".to_string(),
        };
        write_global_abort_state(&self.config.state_root, &next)?;
        if read_global_abort_state(&self.config.state_root)? != next {
            return Err(BridgeError::StateIo);
        }
        Ok(next)
    }

    pub fn start_voice_session(&self) -> Result<(), BridgeError> {
        start_voice_session_if_absent(&self.config.state_root, voice_session_is_running, || {
            Err(BridgeError::WakeModelUnavailable)
        })
    }

    pub fn start_manual_voice_session(
        &self,
        conversation_id: Option<&str>,
    ) -> Result<(), BridgeError> {
        start_voice_session_if_absent(&self.config.state_root, voice_session_is_running, || {
            if global_abort_is_latched(&self.config.state_root)? {
                return Err(BridgeError::Cancelled);
            }
            let resources = self.config.resources()?;
            resources.validate()?;
            let support = dirs::home_dir()
                .ok_or(BridgeError::VoiceEnv)?
                .join("Library/Application Support/openkakao");
            let plan = plan_voice_session(
                &resources.root,
                &self.config.state_root,
                &support.join("runtimes/voice/bin/python3.11"),
            )?;
            validate_path(
                &resources.root.join(resource_layout::VOICE_AUDIO_LIBRARY),
                Kind::Data,
            )
            .map_err(|_| BridgeError::VoiceScript)?;
            let mut command = voice_session_command(&plan, &self.config.state_root)?;
            command.args([
                "--manual-listen",
                "--parent-pid",
                &std::process::id().to_string(),
            ]);
            if let Some(id) = conversation_id {
                if id.len() != 32
                    || !id
                        .bytes()
                        .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
                {
                    return Err(BridgeError::ActionNotAllowed);
                }
                command.args(["--conversation-id", id]);
            }
            let mut map = self
                .cancellations
                .lock()
                .map_err(|_| BridgeError::StateIo)?;
            if map.contains_key("voice-session") {
                return Err(BridgeError::VoiceSessionAlreadyRunning);
            }
            let child = command.spawn().map_err(|_| BridgeError::Spawn)?;
            let hard = Arc::new(AtomicBool::new(false));
            map.insert(
                "voice-session".into(),
                CancellationHandle {
                    flag: hard.clone(),
                    cooperative_marker: None,
                    global_abort_flag: None,
                },
            );
            let bridge = self.clone();
            thread::spawn(move || {
                supervise_manual_voice(child, &hard, Duration::from_secs(300), || {
                    global_abort_is_latched(&bridge.config.state_root).unwrap_or(true)
                });
                if let Ok(mut map) = bridge.cancellations.lock() {
                    map.remove("voice-session");
                }
            });
            Ok(())
        })
    }

    pub fn stop_manual_voice_session(&self) -> bool {
        let Ok(_guard) = VOICE_SESSION_START_LOCK.lock() else {
            return false;
        };
        if !self.cancel("voice-session") {
            return false;
        }
        let deadline = Instant::now() + Duration::from_secs(3);
        while Instant::now() < deadline {
            if self
                .cancellations
                .lock()
                .is_ok_and(|map| !map.contains_key("voice-session"))
            {
                return true;
            }
            thread::sleep(Duration::from_millis(20));
        }
        false
    }

    fn run_python(
        &self,
        extra: &[String],
        timeout: Duration,
        token_id: Option<&str>,
        cooperative_cancel: bool,
    ) -> Result<Vec<u8>, BridgeError> {
        self.run_python_with_output_limit(PythonRun {
            extra,
            timeout,
            token_id,
            cooperative_cancel,
            ..PythonRun::default()
        })
    }

    fn run_python_with_output_limit(&self, run: PythonRun<'_>) -> Result<Vec<u8>, BridgeError> {
        let PythonRun {
            extra,
            timeout,
            token_id,
            cooperative_cancel,
            output_limit,
            stdin_payload,
            global_abort_grace,
            browser_runtime,
        } = run;
        let resources = self.config.resources()?;
        resources.validate().map_err(BridgeError::from)?;
        let runtime = select_python_runtime(
            &self.config.python,
            &self.config.browser_python,
            &self.config.browser_browsers_path,
            resources.installed,
            browser_runtime,
        )?;
        let script = resources
            .script
            .to_str()
            .ok_or(BridgeError::ResourceUnsafe)?;
        let bin = resources.bin.to_str().ok_or(BridgeError::ResourceUnsafe)?;
        let cancel_flag = Arc::new(AtomicBool::new(false));
        let global_abort_flag = global_abort_grace.map(|_| Arc::new(AtomicBool::new(false)));
        let cooperative_marker = if cooperative_cancel {
            let token = token_id.filter(|value| valid_model_swap_token(value));
            let Some(token) = token else {
                return Err(BridgeError::ActionNotAllowed);
            };
            let directory = self.config.state_root.join(MODEL_SWAP_CANCEL_DIR);
            fs::create_dir_all(&directory).map_err(|_| BridgeError::StateIo)?;
            let marker = directory.join(format!("{token}.json"));
            if marker.is_symlink() {
                return Err(BridgeError::StateIo);
            }
            Some(marker)
        } else {
            None
        };
        if let Some(token) = token_id {
            {
                let mut map = self
                    .cancellations
                    .lock()
                    .map_err(|_| BridgeError::StateIo)?;
                if map.contains_key(token) {
                    return Err(BridgeError::ActionNotAllowed);
                }
                if let Some(flag) = global_abort_flag.as_ref() {
                    if global_abort_is_latched(&self.config.state_root)? {
                        // The child still starts so Python can return its fixed
                        // aborted envelope; Rust enforces the hard deadline.
                        flag.store(true, Ordering::SeqCst);
                    }
                }
                map.insert(
                    token.to_string(),
                    CancellationHandle {
                        flag: cancel_flag.clone(),
                        cooperative_marker: cooperative_marker.clone(),
                        global_abort_flag: global_abort_flag.clone(),
                    },
                );
            }
        }

        let mut args = vec![
            "-E".to_string(),
            "-B".to_string(),
            "-s".to_string(),
            script.to_string(),
            "--state-root".to_string(),
            self.config.state_root.to_string_lossy().into_owned(),
            "--logs-dir".to_string(),
            self.config.logs_dir.to_string_lossy().into_owned(),
        ];
        args.push("--bin".to_string());
        args.push(bin.to_string());
        args.extend(extra.iter().cloned());

        let result = run_process_with_recovery_env(
            &runtime.executable,
            &args,
            timeout,
            output_limit,
            ProcessControl {
                stdin_payload,
                hard_cancel_flag: cancel_flag,
                global_abort: global_abort_flag.zip(global_abort_grace),
                cooperative_marker: cooperative_marker.as_deref(),
                recovery_timeout: MODEL_SWAP_RECOVERY_TIMEOUT,
            },
            &runtime.env,
        );
        if let Some(token) = token_id {
            if let Ok(mut map) = self.cancellations.lock() {
                map.remove(token);
            }
        }
        if let Some(marker) = cooperative_marker {
            let _ = fs::remove_file(marker);
        }
        result
    }
}

impl From<ResourceError> for BridgeError {
    fn from(error: ResourceError) -> Self {
        match error {
            ResourceError::Missing => Self::ResourceMissing,
            ResourceError::Unsafe => Self::ResourceUnsafe,
            ResourceError::DevelopmentDisabled => Self::DevelopmentDisabled,
        }
    }
}

impl BridgeConfig {
    fn resources(&self) -> Result<&ResourceLayout, BridgeError> {
        self.resources
            .as_ref()
            .map_err(|error| BridgeError::from(*error))
    }

    fn discover() -> Self {
        let checkout = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .parent()
            .unwrap();
        let executable = std::env::current_exe();
        // Environment reads for code selection are debug-checkout-only. In
        // particular a debug .app must use exactly the installed contract.
        let dev = cfg!(debug_assertions)
            && executable.as_ref().is_ok_and(|path| {
                !path
                    .ancestors()
                    .any(|p| p.extension().is_some_and(|e| e == "app"))
            });
        let get_override = |key| {
            if dev {
                std::env::var_os(key).map(PathBuf::from)
            } else {
                None
            }
        };
        let resources = executable
            .map_err(|_| ResourceError::Missing)
            .and_then(|path| {
                ResourceLayout::discover(
                    &path,
                    checkout,
                    dev,
                    DevOverrides {
                        root: get_override("OPENKAKAO_RESOURCE_ROOT"),
                        script: get_override("OPENKAKAO_MENUBAR_SCRIPT"),
                        bin: get_override("OPENKAKAO_BIN"),
                    },
                )
            });
        let home = dirs::home_dir().unwrap_or_else(|| PathBuf::from("/nonexistent"));
        let support = home.join("Library/Application Support/openkakao");
        let modern = support.join("auto-reply");
        let legacy = support.join("bujamentor");
        let state_root = get_override("OPENKAKAO_STATE_ROOT").unwrap_or_else(|| {
            if modern.join("enrollment.json").is_file() || !legacy.join("enrollment.json").is_file()
            {
                modern
            } else {
                legacy
            }
        });
        let logs_dir = get_override("OPENKAKAO_LOGS_DIR")
            .unwrap_or_else(|| home.join("Library/Logs/AutoReplyMenu"));
        let python = if dev && resources.as_ref().is_ok_and(|layout| !layout.installed) {
            get_override("OPENKAKAO_PYTHON").unwrap_or_else(|| {
                let uv = home
                    .join(".local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11");
                if uv.is_file() {
                    return uv;
                }
                let homebrew = PathBuf::from("/opt/homebrew/opt/python@3.11/bin/python3.11");
                if homebrew.is_file() {
                    homebrew
                } else {
                    PathBuf::from("python3")
                }
            })
        } else {
            // Provisioned separately; never copy or inspect a checkout .venv.
            support.join("runtimes/menubar/bin/python3.11")
        };
        let browser_python = support.join("runtimes/browser/bin/python3.11");
        let browser_browsers_path = support.join("runtimes/browser/ms-playwright");
        Self {
            python,
            browser_python,
            browser_browsers_path,
            resources,
            state_root,
            logs_dir,
        }
    }
}

struct PythonRuntimeSelection {
    executable: String,
    env: Vec<(OsString, OsString)>,
}

fn select_python_runtime(
    menubar_python: &Path,
    browser_python: &Path,
    browser_browsers_path: &Path,
    installed_resources: bool,
    browser_runtime: bool,
) -> Result<PythonRuntimeSelection, BridgeError> {
    if browser_runtime {
        validate_path(browser_python, Kind::Executable).map_err(|_| BridgeError::BrowserEnv)?;
        validate_path(browser_browsers_path, Kind::Directory)
            .map_err(|_| BridgeError::BrowserEnv)?;
        let executable = browser_python
            .to_str()
            .ok_or(BridgeError::BrowserEnv)?
            .to_string();
        return Ok(PythonRuntimeSelection {
            executable,
            env: vec![
                (
                    OsString::from("PLAYWRIGHT_BROWSERS_PATH"),
                    browser_browsers_path.as_os_str().to_owned(),
                ),
                (
                    OsString::from("ANONYMIZED_TELEMETRY"),
                    OsString::from("false"),
                ),
            ],
        });
    }

    if !installed_resources && menubar_python == Path::new("python3") {
        return Ok(PythonRuntimeSelection {
            executable: "python3".to_string(),
            env: Vec::new(),
        });
    }
    validate_path(menubar_python, Kind::Executable).map_err(|_| BridgeError::PythonEnv)?;
    Ok(PythonRuntimeSelection {
        executable: menubar_python
            .to_str()
            .ok_or(BridgeError::PythonEnv)?
            .to_string(),
        env: Vec::new(),
    })
}

#[cfg(test)]
fn run_process(
    executable: &str,
    args: &[String],
    timeout: Duration,
    output_limit: usize,
    cancel_flag: Arc<AtomicBool>,
) -> Result<Vec<u8>, BridgeError> {
    run_process_with_recovery(
        executable,
        args,
        timeout,
        output_limit,
        ProcessControl {
            stdin_payload: None,
            hard_cancel_flag: cancel_flag,
            global_abort: None,
            cooperative_marker: None,
            recovery_timeout: Duration::ZERO,
        },
    )
}

#[cfg(test)]
fn run_process_with_recovery(
    executable: &str,
    args: &[String],
    timeout: Duration,
    output_limit: usize,
    control: ProcessControl<'_>,
) -> Result<Vec<u8>, BridgeError> {
    run_process_with_recovery_env(executable, args, timeout, output_limit, control, &[])
}

fn run_process_with_recovery_env(
    executable: &str,
    args: &[String],
    timeout: Duration,
    output_limit: usize,
    control: ProcessControl<'_>,
    env_overrides: &[(OsString, OsString)],
) -> Result<Vec<u8>, BridgeError> {
    let ProcessControl {
        stdin_payload,
        hard_cancel_flag,
        global_abort,
        cooperative_marker,
        recovery_timeout,
    } = control;
    let mut command = Command::new(executable);
    command
        .args(args)
        .envs(env_overrides.iter().cloned())
        .stdin(if stdin_payload.is_some() {
            Stdio::piped()
        } else {
            Stdio::null()
        })
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    let mut child = command.spawn().map_err(|_| BridgeError::Spawn)?;
    let mut stdin_writer = if let Some(payload) = stdin_payload {
        let mut stdin = child.stdin.take().ok_or(BridgeError::Spawn)?;
        let payload = payload.to_vec();
        Some(thread::spawn(move || {
            stdin.write_all(&payload).map_err(|_| BridgeError::Wait)?;
            stdin.flush().map_err(|_| BridgeError::Wait)
        }))
    } else {
        None
    };
    let mut stdout = child.stdout.take().ok_or(BridgeError::Spawn)?;

    // Drain while the process runs. Waiting first can deadlock when JSON exceeds
    // the platform pipe buffer. Continue draining after the limit is exceeded;
    // only storage stops growing.
    let reader = thread::spawn(move || {
        let mut stored = Vec::with_capacity(output_limit.min(64 * 1024));
        let mut buffer = [0_u8; 16 * 1024];
        let mut total = 0_usize;
        loop {
            let read = stdout.read(&mut buffer).map_err(|_| BridgeError::Wait)?;
            if read == 0 {
                break;
            }
            total = total.saturating_add(read);
            if stored.len() < output_limit.saturating_add(1) {
                let remaining = output_limit.saturating_add(1).saturating_sub(stored.len());
                stored.extend_from_slice(&buffer[..read.min(remaining)]);
            }
        }
        Ok::<_, BridgeError>((stored, total > output_limit))
    });

    let started = Instant::now();
    let mut recovery_started = None;
    let mut global_abort_started = None;
    let status = loop {
        if hard_cancel_flag.load(Ordering::SeqCst) {
            let _ = child.kill();
            let _ = child.wait();
            if let Some(writer) = stdin_writer.take() {
                let _ = writer.join();
            }
            let _ = reader.join();
            return Err(BridgeError::Cancelled);
        }
        if global_abort
            .as_ref()
            .is_some_and(|(flag, _)| flag.load(Ordering::SeqCst))
            && global_abort_started.is_none()
        {
            global_abort_started = Some(Instant::now());
        }
        if started.elapsed() >= timeout {
            if let Some(marker) = cooperative_marker {
                if recovery_started.is_none() {
                    let _ = write_model_swap_cancel_marker(marker);
                    recovery_started = Some(Instant::now());
                }
            }
            if recovery_started.is_none_or(|since| since.elapsed() >= recovery_timeout) {
                let _ = child.kill();
                let _ = child.wait();
                if let Some(writer) = stdin_writer.take() {
                    let _ = writer.join();
                }
                let _ = reader.join();
                return Err(BridgeError::Timeout);
            }
        }
        match child.try_wait().map_err(|_| BridgeError::Wait)? {
            Some(status) => break status,
            None => {
                if global_abort_started.is_some_and(|since| {
                    global_abort
                        .as_ref()
                        .is_some_and(|(_, grace)| since.elapsed() >= *grace)
                }) {
                    let _ = child.kill();
                    let _ = child.wait();
                    if let Some(writer) = stdin_writer.take() {
                        let _ = writer.join();
                    }
                    let _ = reader.join();
                    return Err(BridgeError::Cancelled);
                }
                thread::sleep(Duration::from_millis(10));
            }
        }
    };

    if let Some(writer) = stdin_writer.take() {
        writer.join().map_err(|_| BridgeError::Wait)??;
    }
    let (output, oversized) = reader.join().map_err(|_| BridgeError::Wait)??;
    if oversized {
        return Err(BridgeError::OutputTooLarge);
    }
    if !status.success() {
        return Err(BridgeError::Exit);
    }
    if output.is_empty() {
        return Err(BridgeError::Empty);
    }
    Ok(output)
}

fn parse_json_output(bytes: &[u8]) -> Result<Value, BridgeError> {
    serde_json::from_slice(bytes).map_err(|_| BridgeError::Json)
}

fn as_u64(value: Option<&Value>) -> u64 {
    value.and_then(Value::as_u64).unwrap_or(0)
}

fn clamp01(value: f64) -> f64 {
    value.clamp(0.0, 1.0)
}

fn bounded_job_id(value: &str) -> String {
    value.chars().take(BROWSER_TOOL_JOB_ID_LIMIT).collect()
}

fn epoch_seconds() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs_f64())
        .unwrap_or(0.0)
}

fn round_activity(value: f64) -> f64 {
    (clamp01(value) * 1000.0).round() / 1000.0
}

fn sanitize_background_source(value: Option<&Value>, allowed: &[&str]) -> SafeBackgroundSource {
    let Some(source) = value.and_then(Value::as_object) else {
        return SafeBackgroundSource::unknown();
    };
    let state = source
        .get("state")
        .and_then(Value::as_str)
        .filter(|state| allowed.contains(state))
        .unwrap_or("unknown")
        .to_string();
    SafeBackgroundSource {
        state,
        activity: round_activity(
            source
                .get("activity")
                .and_then(Value::as_f64)
                .unwrap_or(0.0),
        ),
        caption: bounded_json_string(source.get("caption"), 120),
    }
}

fn sanitize_background(value: Option<&Value>, job_load: f64, reply_load: f64) -> SafeBackground {
    const GEEKNEWS_STATES: &[&str] = &["sending", "confirmed", "idle", "unknown"];
    const DB_SYNC_STATES: &[&str] = &[
        "retrying", "stalled", "syncing", "behind", "ready", "unknown",
    ];
    let Some(background) = value.and_then(Value::as_object) else {
        return SafeBackground::fallback(job_load, reply_load);
    };
    SafeBackground {
        activity: round_activity(
            background
                .get("activity")
                .and_then(Value::as_f64)
                .unwrap_or(0.0),
        ),
        caption: bounded_json_string(background.get("caption"), 120),
        reply_load: round_activity(reply_load),
        geeknews: sanitize_background_source(background.get("geeknews"), GEEKNEWS_STATES),
        db_sync: sanitize_background_source(background.get("db_sync"), DB_SYNC_STATES),
    }
}

fn sanitize_ondevice(value: Option<&Value>) -> SafeOnDevice {
    let Some(root) = value.and_then(Value::as_object) else {
        return SafeOnDevice::default();
    };
    let (Some(hardware), Some(recommendation), Some(verification)) = (
        root.get("hardware").and_then(Value::as_object),
        root.get("recommendation").and_then(Value::as_object),
        root.get("verification").and_then(Value::as_object),
    ) else {
        return SafeOnDevice::default();
    };
    let memory_gb = hardware
        .get("memory_gb")
        .and_then(Value::as_f64)
        .filter(|value| value.is_finite())
        .unwrap_or(0.0)
        .clamp(0.0, 4096.0);
    let cores = hardware
        .get("cores")
        .and_then(Value::as_u64)
        .unwrap_or(0)
        .min(1024) as u32;

    SafeOnDevice {
        available: true,
        chip: bounded_json_string(hardware.get("chip"), 64),
        cores,
        memory_gb: (memory_gb * 10.0).round() / 10.0,
        apple_silicon: hardware
            .get("is_apple_silicon")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        engine: bounded_json_string(recommendation.get("primary_engine"), 32),
        recommended_model: bounded_json_string(recommendation.get("recommended_model"), 128),
        quant: bounded_json_string(recommendation.get("recommended_quant"), 32),
        verified: verification
            .get("ok")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        status_label: bounded_json_string(root.get("status_label"), 240),
        status_detail: bounded_json_string(root.get("status_detail"), 240),
    }
}

fn sanitize_pipeline(value: Option<&Value>) -> SafePipeline {
    const STAGE_IDS: &[&str] = &[
        "detect",
        "authorize",
        "queue",
        "context",
        "model",
        "delay",
        "send",
        "confirm",
    ];
    const OUTCOMES: &[&str] = &[
        "none",
        "sent",
        "skipped",
        "deferred",
        "scheduled",
        "failed",
        "aborted",
    ];

    let Some(pipeline) = value.and_then(Value::as_object) else {
        return SafePipeline::default();
    };
    let Some(stages) = pipeline.get("stages").and_then(Value::as_array) else {
        return SafePipeline::default();
    };

    let active_index = pipeline
        .get("active_index")
        .and_then(Value::as_i64)
        .filter(|index| *index >= 0)
        .and_then(|index| usize::try_from(index).ok())
        .filter(|index| *index < stages.len())
        .or_else(|| {
            stages.iter().position(|stage| {
                stage
                    .as_object()
                    .and_then(|item| item.get("state"))
                    .and_then(Value::as_str)
                    == Some("active")
            })
        });

    let (active, stage, stage_index) = if let Some(index) = active_index {
        let stage = stages[index]
            .as_object()
            .and_then(|item| item.get("id"))
            .and_then(Value::as_str)
            .filter(|id| STAGE_IDS.contains(id))
            .unwrap_or("unknown")
            .to_string();
        (true, stage, index.min(16) as u32)
    } else {
        (false, "none".to_string(), 0)
    };

    SafePipeline {
        active,
        stage,
        stage_index,
        stage_total: stages.len().min(16) as u32,
        outcome: pipeline
            .get("outcome")
            .and_then(Value::as_str)
            .filter(|outcome| OUTCOMES.contains(outcome))
            .unwrap_or("unknown")
            .to_string(),
    }
}

fn bounded_arg(value: Option<&str>, max_chars: usize) -> Option<String> {
    let value = value?.trim();
    if value.is_empty() {
        return None;
    }
    Some(value.chars().take(max_chars).collect())
}

fn valid_browser_tool_job_id(value: &str) -> bool {
    let bytes = value.as_bytes();
    if bytes.is_empty()
        || bytes.len() > BROWSER_TOOL_JOB_ID_LIMIT
        || !bytes[0].is_ascii_alphanumeric()
    {
        return false;
    }
    bytes[1..]
        .iter()
        .all(|byte| byte.is_ascii_alphanumeric() || matches!(*byte, b'.' | b'_' | b'-'))
}

fn browser_tool_args(job_id: &str, task: &str) -> Result<Vec<String>, BridgeError> {
    if !valid_browser_tool_job_id(job_id)
        || task.trim().is_empty()
        || task.contains('\0')
        || task.len() > BROWSER_TOOL_TASK_LIMIT_BYTES
    {
        return Err(BridgeError::ActionNotAllowed);
    }
    Ok(vec![
        "--action".to_string(),
        "tool-browser".to_string(),
        "--job-id".to_string(),
        job_id.to_string(),
    ])
}

fn settings_action_args(
    action: &str,
    query: Option<&str>,
    node_id: Option<&str>,
    chat_id: Option<&str>,
    model: Option<&str>,
    explicit_opt_in: Option<bool>,
    token_id: Option<&str>,
) -> Result<Vec<String>, BridgeError> {
    let mut args = vec!["--action".to_string(), action.to_string()];
    let opt_in_allowed = matches!(
        action,
        "model-swap" | MLX_SERVER_LAUNCH_ACTION | MLX_SERVER_STOP_ACTION
    );
    if !opt_in_allowed && (explicit_opt_in.is_some() || token_id.is_some()) {
        return Err(BridgeError::ActionNotAllowed);
    }
    match action {
        "models"
        | "dream-rsi-status"
        | "knowledge-graph-status"
        | "knowledge-graph"
        | "model-owner-status"
        | MLX_SERVER_STATUS_ACTION => {}
        "room-catalog" => {}
        "history-rooms"
        | "history-messages"
        | "voice-history-sessions"
        | "voice-history-messages"
        | "db-sync-history"
        | "reply-history"
        | "geeknews-history" => {
            // Read-only history requests; no worker or send action is exposed.
            if let Some(value) = bounded_arg(query, 4096) {
                let parsed: Value =
                    serde_json::from_str(&value).map_err(|_| BridgeError::ActionNotAllowed)?;
                if !parsed.is_object() {
                    return Err(BridgeError::ActionNotAllowed);
                }
                args.extend(["--history-query".into(), value.to_owned()]);
            }
            if let Some(value) = bounded_arg(chat_id, 128) {
                args.extend(["--history-chat".into(), value.to_owned()]);
            }
        }
        "room-delete" => {
            let id = chat_id
                .and_then(|s| s.parse::<i64>().ok())
                .filter(|v| *v > 0)
                .ok_or(BridgeError::ActionNotAllowed)?;
            args.extend(["--catalog-delete".into(), id.to_string()]);
        }
        MLX_SERVER_LAUNCH_ACTION => {
            // Starting a resident model needs a deliberate opt-in and one of
            // the fixed local models; the app supplies the binary, models
            // directory, and log path itself so no path crosses this boundary.
            if token_id.is_some() {
                return Err(BridgeError::ActionNotAllowed);
            }
            if explicit_opt_in != Some(true) {
                return Err(BridgeError::ActionNotAllowed);
            }
            let candidate = model
                .filter(|value| is_local_model_id(value))
                .ok_or(BridgeError::ActionNotAllowed)?;
            args.push("--model".to_string());
            args.push(candidate.to_string());
            args.push("--explicit-opt-in".to_string());
        }
        MLX_SERVER_STOP_ACTION => {
            // Stopping needs no model selector: it only ever signals the pid
            // this app started, so an extra selector is a caller error.
            if token_id.is_some() || model.is_some() {
                return Err(BridgeError::ActionNotAllowed);
            }
            if explicit_opt_in != Some(true) {
                return Err(BridgeError::ActionNotAllowed);
            }
            args.push("--explicit-opt-in".to_string());
        }
        "knowledge-graph-focus" => {
            if let Some(value) = bounded_arg(query, 256) {
                args.push("--knowledge-query".to_string());
                args.push(value);
            }
            if let Some(value) = bounded_arg(node_id, 192) {
                args.push("--knowledge-node-id".to_string());
                args.push(value);
            }
            if let Some(value) = bounded_arg(chat_id, 128) {
                args.push("--knowledge-chat".to_string());
                args.push(value);
            }
        }
        "model-set" => {
            let candidate = model
                .filter(|value| is_model_set_id(value))
                .ok_or(BridgeError::ActionNotAllowed)?;
            args.extend([
                "--model".to_string(),
                candidate.to_string(),
                "--no-wait".to_string(),
            ]);
        }
        "model-prepare" if model == Some(SWAP_MODEL_ID) => {
            args.extend(["--model".to_string(), SWAP_MODEL_ID.to_string()]);
        }
        "model-swap"
            if model == Some(SWAP_MODEL_ID)
                && explicit_opt_in == Some(true)
                && token_id.is_some_and(valid_model_swap_token) =>
        {
            args.extend([
                "--model".to_string(),
                SWAP_MODEL_ID.to_string(),
                "--explicit-opt-in".to_string(),
                MODEL_SWAP_OPT_IN.to_string(),
                "--request-token".to_string(),
                token_id.unwrap().to_string(),
            ]);
        }
        "room-upsert" => {
            let id = chat_id
                .and_then(|v| v.parse::<i64>().ok())
                .filter(|id| *id > 0)
                .ok_or(BridgeError::ActionNotAllowed)?;
            let title = bounded_arg(query, 4096).unwrap_or_default();
            let mut payload = serde_json::json!({
                "chat_id": id,
                "title": title,
                "auto_reply": true,
                "geeknews": false,
            });
            if title.starts_with('{') {
                let edited: Value =
                    serde_json::from_str(&title).map_err(|_| BridgeError::ActionNotAllowed)?;
                for key in ["auto_reply", "geeknews"] {
                    let flag = edited
                        .get(key)
                        .and_then(Value::as_bool)
                        .ok_or(BridgeError::ActionNotAllowed)?;
                    payload[key] = Value::Bool(flag);
                }
                payload["title"] = Value::String(bounded_json_string(edited.get("title"), 128));
            }
            args.extend(["--catalog-upsert".to_string(), payload.to_string()]);
        }
        _ => return Err(BridgeError::ActionNotAllowed),
    }
    Ok(args)
}

fn valid_model_swap_token(value: &str) -> bool {
    let bytes = value.as_bytes();
    if bytes.len() != 36 {
        return false;
    }
    bytes.iter().enumerate().all(|(index, byte)| match index {
        8 | 13 | 18 | 23 => *byte == b'-',
        14 => matches!(*byte, b'1'..=b'5'),
        19 => matches!(*byte, b'8' | b'9' | b'a' | b'b'),
        _ => byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase(),
    })
}

fn start_voice_session_if_absent(
    state_root: &Path,
    probe: impl FnOnce(&Path) -> Result<bool, BridgeError>,
    spawn: impl FnOnce() -> Result<(), BridgeError>,
) -> Result<(), BridgeError> {
    // Serialize starts from all windows/bridge clones through spawn. A heartbeat
    // can be stale during model loading and never grants permission to duplicate.
    let _guard = VOICE_SESSION_START_LOCK
        .lock()
        .map_err(|_| BridgeError::VoiceProcessCheck)?;
    if probe(state_root)? {
        return Err(BridgeError::VoiceSessionAlreadyRunning);
    }
    spawn()
}

fn supervise_manual_voice(
    mut child: Child,
    hard: &AtomicBool,
    timeout: Duration,
    global_stop: impl Fn() -> bool,
) {
    let deadline = Instant::now() + timeout;
    loop {
        if hard.load(Ordering::SeqCst) || Instant::now() >= deadline || global_stop() {
            // This unreaped Child is the only permitted signal target.
            let _ = Command::new("/bin/kill")
                .args(["-TERM", &child.id().to_string()])
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
            let grace = Instant::now() + Duration::from_secs(2);
            while Instant::now() < grace {
                if child.try_wait().is_ok_and(|status| status.is_some()) {
                    return;
                }
                thread::sleep(Duration::from_millis(20));
            }
            let _ = child.kill();
            let _ = child.wait();
            return;
        }
        match child.try_wait() {
            Ok(Some(_)) => return,
            Ok(None) => thread::sleep(Duration::from_millis(20)),
            Err(_) => {
                let _ = child.kill();
                let _ = child.wait();
                return;
            }
        }
    }
}

type VoiceProcessArgs = (PathBuf, Vec<OsString>);

fn is_python_executable(path: &Path) -> bool {
    let Some(name) = path.file_name().and_then(OsStr::to_str) else {
        return false;
    };
    name == "Python"
        || name == "python"
        || name.strip_prefix("python").is_some_and(|version| {
            version
                .split('.')
                .all(|part| !part.is_empty() && part.bytes().all(|byte| byte.is_ascii_digit()))
        })
}

fn voice_process_matches((executable, argv): &VoiceProcessArgs, state_root: &Path) -> bool {
    if !is_python_executable(executable) {
        return false;
    }
    let mut args = argv.iter().skip(1);
    let script = loop {
        let Some(arg) = args.next() else {
            return false;
        };
        match arg.to_str() {
            Some("--") => break args.next(),
            Some("-W" | "-X" | "--check-hash-based-pycs") => {
                if args.next().is_none() {
                    return false;
                }
            }
            Some(value) if value.starts_with("-W") || value.starts_with("-X") => {}
            Some(value) if value.starts_with("--check-hash-based-pycs=") => {}
            Some(value) if value.starts_with('-') => {
                // Only interpreter flags without operands may precede the script.
                // In particular, -c/-m (including combined flags) are not scripts.
                if value.len() == 1
                    || !value[1..]
                        .chars()
                        .all(|flag| "bBdEiIOPqRsStuUvVx".contains(flag))
                {
                    return false;
                }
            }
            _ => break Some(arg),
        }
    };
    // Match the complete script path components in either a checkout or bundle,
    // never a substring in another script's arguments or a shell command.
    if !script.is_some_and(|path| Path::new(path).ends_with(resource_layout::VOICE_SCRIPT)) {
        return false;
    }
    let mut root = None;
    while let Some(arg) = args.next() {
        if arg == "--" {
            break;
        }
        if arg == "--state-root" {
            root = args.next().map(|value| value.as_bytes());
        } else if let Some(value) = arg.as_bytes().strip_prefix(b"--state-root=") {
            root = Some(value);
        }
    }
    // argparse uses the final occurrence. Keep spaces and argument boundaries;
    // a sibling directory or a longer state-root prefix is not this listener.
    root == Some(state_root.as_os_str().as_bytes())
}

fn voice_session_is_running(state_root: &Path) -> Result<bool, BridgeError> {
    // SAFETY: geteuid only reads this process's effective user ID.
    let current_uid = unsafe { libc::geteuid() };
    voice_session_from_process_table(
        state_root,
        current_uid,
        Command::new("/bin/ps")
            .args(["-ww", "-axo", "uid=,pid=,stat=,comm="])
            .stdin(Stdio::null())
            .output(),
        read_voice_process_args,
    )
}

fn voice_session_from_process_table(
    state_root: &Path,
    current_uid: libc::uid_t,
    output: std::io::Result<std::process::Output>,
    mut read_args: impl FnMut(i32) -> Result<Option<VoiceProcessArgs>, BridgeError>,
) -> Result<bool, BridgeError> {
    let output = output.map_err(|_| BridgeError::VoiceProcessCheck)?;
    if !output.status.success() || output.stdout.is_empty() {
        return Err(BridgeError::VoiceProcessCheck);
    }
    let table = std::str::from_utf8(&output.stdout).map_err(|_| BridgeError::VoiceProcessCheck)?;
    let mut matched = false;
    for line in table.lines() {
        let (uid, rest) = line
            .trim()
            .split_once(char::is_whitespace)
            .ok_or(BridgeError::VoiceProcessCheck)?;
        let uid = uid
            .parse::<libc::uid_t>()
            .map_err(|_| BridgeError::VoiceProcessCheck)?;
        // Never read argv for another user's Python processes: macOS may deny
        // KERN_PROCARGS2 even though ps can list them. Keep all same-user Python
        // runtimes eligible so an installed listener also blocks a debug start.
        if uid != current_uid {
            continue;
        }
        let (pid, rest) = rest
            .trim_start()
            .split_once(char::is_whitespace)
            .ok_or(BridgeError::VoiceProcessCheck)?;
        let pid = pid
            .parse::<i32>()
            .ok()
            .filter(|pid| *pid > 0)
            .ok_or(BridgeError::VoiceProcessCheck)?;
        let (status, executable) = rest
            .trim_start()
            .split_once(char::is_whitespace)
            .ok_or(BridgeError::VoiceProcessCheck)?;
        let executable = executable.trim_start();
        if executable.is_empty() {
            return Err(BridgeError::VoiceProcessCheck);
        }
        if !status.contains('Z') && is_python_executable(Path::new(executable)) {
            if let Some(args) = read_args(pid)? {
                matched |= voice_process_matches(&args, state_root);
            }
        }
    }
    Ok(matched)
}

#[cfg(target_os = "macos")]
fn read_voice_process_args(pid: i32) -> Result<Option<VoiceProcessArgs>, BridgeError> {
    let mut limit = 0i32;
    let mut size = std::mem::size_of_val(&limit);
    let mut limit_mib = [libc::CTL_KERN, libc::KERN_ARGMAX];
    // SAFETY: each writable buffer and length describes allocated storage; the
    // MIBs contain only read-only kernel queries. No signals are sent to any PID.
    let result = unsafe {
        libc::sysctl(
            limit_mib.as_mut_ptr(),
            2,
            (&mut limit as *mut i32).cast(),
            &mut size,
            std::ptr::null_mut(),
            0,
        )
    };
    if result != 0 || limit <= 0 || limit as usize > OUTPUT_LIMIT_BYTES {
        return Err(BridgeError::VoiceProcessCheck);
    }
    let mut bytes = vec![0u8; limit as usize];
    size = bytes.len();
    let mut mib = [libc::CTL_KERN, libc::KERN_PROCARGS2, pid];
    // SAFETY: bytes owns size writable bytes and mib has exactly three entries.
    let result = unsafe {
        libc::sysctl(
            mib.as_mut_ptr(),
            3,
            bytes.as_mut_ptr().cast(),
            &mut size,
            std::ptr::null_mut(),
            0,
        )
    };
    if result != 0 {
        return if std::io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH) {
            Ok(None) // Exited since ps; permission/parse failures still block spawn.
        } else {
            Err(BridgeError::VoiceProcessCheck)
        };
    }
    bytes.truncate(size);
    parse_voice_process_args(&bytes).map(Some)
}

#[cfg(not(target_os = "macos"))]
fn read_voice_process_args(_pid: i32) -> Result<Option<VoiceProcessArgs>, BridgeError> {
    Err(BridgeError::VoiceProcessCheck)
}

#[cfg(any(target_os = "macos", test))]
fn parse_voice_process_args(bytes: &[u8]) -> Result<VoiceProcessArgs, BridgeError> {
    let count = bytes.get(..4).ok_or(BridgeError::VoiceProcessCheck)?;
    let count = i32::from_ne_bytes(
        count
            .try_into()
            .map_err(|_| BridgeError::VoiceProcessCheck)?,
    );
    if count <= 0 || count as usize > bytes.len() {
        return Err(BridgeError::VoiceProcessCheck);
    }
    let mut remaining = &bytes[4..];
    let end = remaining
        .iter()
        .position(|byte| *byte == 0)
        .filter(|end| *end > 0)
        .ok_or(BridgeError::VoiceProcessCheck)?;
    let executable = PathBuf::from(OsString::from_vec(remaining[..end].to_vec()));
    remaining = &remaining[end..];
    let start = remaining
        .iter()
        .position(|byte| *byte != 0)
        .ok_or(BridgeError::VoiceProcessCheck)?;
    remaining = &remaining[start..];
    let mut argv = Vec::with_capacity(count as usize);
    for _ in 0..count {
        let end = remaining
            .iter()
            .position(|byte| *byte == 0)
            .ok_or(BridgeError::VoiceProcessCheck)?;
        argv.push(OsString::from_vec(remaining[..end].to_vec()));
        remaining = &remaining[end + 1..];
    }
    // KERN_PROCARGS2 also includes the environment; never inspect or return it.
    Ok((executable, argv))
}

fn plan_voice_session(
    resource_root: &Path,
    state_root: &Path,
    python: &Path,
) -> Result<VoiceSessionPlan, BridgeError> {
    validate_path(python, Kind::Executable).map_err(|_| BridgeError::VoiceEnv)?;
    let script = resource_root.join(resource_layout::VOICE_SCRIPT);
    validate_path(&script, Kind::Data).map_err(|_| BridgeError::VoiceScript)?;
    Ok(VoiceSessionPlan {
        python: python.to_path_buf(),
        script,
        tts_out: state_root.join(VOICE_TTS_OUT_NAME),
    })
}

fn voice_session_command(
    plan: &VoiceSessionPlan,
    state_root: &Path,
) -> Result<Command, BridgeError> {
    let mut command = Command::new(&plan.python);
    command
        .env("OPENKAKAO_VOICE_ENV", "1")
        .env("OPENKAKAO_VOICE_TTS_OUT", &plan.tts_out)
        .args([
            "-E",
            "-B",
            "-s",
            plan.script.to_str().ok_or(BridgeError::VoiceScript)?,
            "--state-root",
            state_root.to_str().ok_or(BridgeError::StateIo)?,
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    Ok(command)
}

fn bounded_json_string(value: Option<&Value>, max_chars: usize) -> String {
    value
        .and_then(Value::as_str)
        .unwrap_or("")
        .chars()
        .take(max_chars)
        .collect()
}

fn bounded_status_string(value: Option<&Value>, max_chars: usize, fallback: &str) -> String {
    value
        .and_then(Value::as_str)
        .unwrap_or(fallback)
        .chars()
        .filter(|character| !character.is_control())
        .take(max_chars)
        .collect()
}

fn sanitize_browser_tool_result(value: &Value) -> SafeBrowserToolResult {
    const STATUSES: &[&str] = &["completed", "aborted", "rejected", "failed"];
    const ERROR_CODES: &[&str] = &[
        "global_abort",
        "state_root_invalid",
        "job_id_invalid",
        "browser_task_invalid",
        "browser_task_too_large",
        "browser_runtime_unavailable",
        "browser_job_failed",
        "browser_result_invalid",
        "browser_result_too_large",
    ];
    let status = value.get("status").and_then(Value::as_str).unwrap_or("");
    let error_code = value.get("errorCode").and_then(Value::as_str).unwrap_or("");
    let Some(result) = value.get("result").and_then(Value::as_str) else {
        return SafeBrowserToolResult::failed("browser_result_invalid");
    };
    if result.len() > BROWSER_TOOL_RESULT_LIMIT_BYTES {
        return SafeBrowserToolResult::failed("browser_result_too_large");
    }
    if value.get("ok").and_then(Value::as_bool) == Some(true)
        && status == "completed"
        && error_code.is_empty()
    {
        return SafeBrowserToolResult {
            ok: true,
            status: status.to_string(),
            error_code: String::new(),
            result: result.to_string(),
        };
    }
    if !STATUSES.contains(&status) || !ERROR_CODES.contains(&error_code) {
        return SafeBrowserToolResult::failed("browser_job_failed");
    }
    SafeBrowserToolResult {
        ok: false,
        status: status.to_string(),
        error_code: error_code.to_string(),
        result: String::new(),
    }
}

fn safe_small_json(path: &Path) -> Option<Value> {
    let metadata = fs::symlink_metadata(path).ok()?;
    if metadata.file_type().is_symlink()
        || !metadata.is_file()
        || metadata.len() > STATE_FILE_LIMIT_BYTES
    {
        return None;
    }
    let bytes = fs::read(path).ok()?;
    serde_json::from_slice(&bytes).ok()
}

fn read_global_abort_state(state_root: &Path) -> Result<SafeEmergencyState, BridgeError> {
    match fs::symlink_metadata(state_root) {
        Ok(metadata) if !private_abort_root(&metadata) => return Err(BridgeError::StateIo),
        Ok(_) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok(SafeEmergencyState::default())
        }
        Err(_) => return Err(BridgeError::StateIo),
    }
    let path = state_root.join(ABORT_STATE_NAME);
    let file = match OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
        .open(&path)
    {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok(SafeEmergencyState::default())
        }
        Err(_) => return Err(BridgeError::StateIo),
    };
    let metadata = file.metadata().map_err(|_| BridgeError::StateIo)?;
    if !metadata.is_file()
        || metadata.len() > STATE_FILE_LIMIT_BYTES
        || metadata.uid() != unsafe { libc::geteuid() }
        || metadata.mode() & 0o777 != 0o600
        || metadata.nlink() != 1
    {
        return Err(BridgeError::StateIo);
    }
    let mut bytes = Vec::with_capacity(metadata.len() as usize);
    file.take(STATE_FILE_LIMIT_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| BridgeError::StateIo)?;
    if bytes.len() as u64 > STATE_FILE_LIMIT_BYTES {
        return Err(BridgeError::StateIo);
    }
    // Struct deserialization rejects duplicate, missing and extra fields.
    let value: DiskEmergencyState =
        serde_json::from_slice(&bytes).map_err(|_| BridgeError::StateIo)?;
    if value.schema_version != 1 {
        return Err(BridgeError::StateIo);
    }
    let epoch = value.epoch;
    if epoch > ABORT_EPOCH_MAX {
        return Err(BridgeError::StateIo);
    }
    let latched = value.latched;
    let reason = value.reason;
    if reason.chars().count() > 96
        || reason.chars().any(char::is_control)
        || (latched && reason.is_empty())
        || (!latched && reason != "human_resume" && !(epoch == 0 && reason.is_empty()))
    {
        return Err(BridgeError::StateIo);
    }
    Ok(SafeEmergencyState {
        schema_version: 1,
        epoch,
        latched,
        reason,
    })
}

fn global_abort_is_latched(state_root: &Path) -> Result<bool, BridgeError> {
    read_global_abort_state(state_root).map(|state| state.latched)
}

fn default_voice_status() -> SafeVoiceStatus {
    SafeVoiceStatus {
        custom_model_selected: false,
        ..SafeVoiceStatus::default()
    }
}

fn read_voice_status(state_root: &Path) -> SafeVoiceStatus {
    let Some(value) = safe_small_json(&state_root.join(VOICE_STATUS_NAME)) else {
        return default_voice_status();
    };
    let Some(root) = value.as_object() else {
        return default_voice_status();
    };
    if root.get("schema_version").and_then(Value::as_u64) != Some(1) {
        return default_voice_status();
    }
    let state = root
        .get("state")
        .and_then(Value::as_str)
        .unwrap_or("unknown")
        .chars()
        .take(32)
        .collect::<String>();
    let error_code = root
        .get("error_code")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(|value| value.chars().take(96).collect::<String>());
    let wake_source = match root.get("wake_source").and_then(Value::as_str) {
        Some("stock") => "stock",
        Some("custom") => "custom",
        _ => "none",
    };
    let updated_at = as_u64(root.get("updated_at"));
    let now = epoch_seconds() as u64;
    if updated_at == 0
        || updated_at > now.saturating_add(5)
        || now.saturating_sub(updated_at) > VOICE_STATUS_MAX_AGE_SECS
    {
        return default_voice_status();
    }
    SafeVoiceStatus {
        manual_running: false,
        available: true,
        state,
        rms: clamp01(root.get("rms").and_then(Value::as_f64).unwrap_or(0.0)),
        output_rms: clamp01(
            root.get("output_rms")
                .and_then(Value::as_f64)
                .unwrap_or(0.0),
        ),
        error_code,
        wake_source: wake_source.to_string(),
        updated_at,
        wake_phrase: root
            .get("wake_phrase")
            .and_then(Value::as_str)
            .unwrap_or(WAKE_PHRASE)
            .chars()
            .take(64)
            .collect::<String>(),
        threshold: root
            .get("threshold")
            .and_then(Value::as_f64)
            .unwrap_or(WAKE_THRESHOLD)
            .clamp(WAKE_THRESHOLD, 0.95),
        // Persisted status from older builds must not re-enable an unverified
        // wake model in the current desktop UI.
        custom_model_selected: false,
    }
}

fn write_global_abort(state_root: &Path) -> Result<(), BridgeError> {
    let _state_lock = lock_global_abort(state_root)?;
    let current = read_global_abort_state(state_root)?;
    let next = SafeEmergencyState {
        schema_version: 1,
        epoch: current
            .epoch
            .checked_add(1)
            .filter(|epoch| *epoch <= ABORT_EPOCH_MAX)
            .ok_or(BridgeError::StateIo)?,
        latched: true,
        reason: "global_abort".to_string(),
    };
    write_global_abort_state(state_root, &next)?;
    if read_global_abort_state(state_root)? != next {
        return Err(BridgeError::StateIo);
    }
    Ok(())
}

fn private_abort_root(metadata: &fs::Metadata) -> bool {
    metadata.is_dir()
        && !metadata.file_type().is_symlink()
        && metadata.uid() == unsafe { libc::geteuid() }
        && metadata.mode() & 0o777 == 0o700
}

fn lock_global_abort(state_root: &Path) -> Result<File, BridgeError> {
    fs::DirBuilder::new()
        .recursive(true)
        .mode(0o700)
        .create(state_root)
        .map_err(|_| BridgeError::StateIo)?;
    if !private_abort_root(&fs::symlink_metadata(state_root).map_err(|_| BridgeError::StateIo)?) {
        return Err(BridgeError::StateIo);
    }
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
        .open(state_root.join(ABORT_LOCK_NAME))
        .map_err(|_| BridgeError::StateIo)?;
    let metadata = file.metadata().map_err(|_| BridgeError::StateIo)?;
    if !metadata.is_file()
        || metadata.uid() != unsafe { libc::geteuid() }
        || metadata.mode() & 0o777 != 0o600
        || metadata.nlink() != 1
    {
        return Err(BridgeError::StateIo);
    }
    let deadline = Instant::now() + ABORT_LOCK_TIMEOUT;
    loop {
        if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } == 0 {
            return Ok(file); // Closing this descriptor releases the flock.
        }
        let error = std::io::Error::last_os_error();
        if !matches!(
            error.kind(),
            std::io::ErrorKind::WouldBlock | std::io::ErrorKind::Interrupted
        ) || Instant::now() >= deadline
        {
            return Err(BridgeError::StateIo);
        }
        thread::sleep(Duration::from_millis(5));
    }
}

fn write_global_abort_state(
    state_root: &Path,
    state: &SafeEmergencyState,
) -> Result<(), BridgeError> {
    fs::create_dir_all(state_root).map_err(|_| BridgeError::StateIo)?;
    let root_metadata = fs::symlink_metadata(state_root).map_err(|_| BridgeError::StateIo)?;
    if !private_abort_root(&root_metadata) {
        return Err(BridgeError::StateIo);
    }
    let path = state_root.join(ABORT_STATE_NAME);
    match fs::symlink_metadata(&path) {
        Ok(_) => {
            read_global_abort_state(state_root)?;
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(_) => return Err(BridgeError::StateIo),
    }
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| BridgeError::StateIo)?
        .as_nanos();
    let temp = state_root.join(format!(
        ".{ABORT_STATE_NAME}.{}.{}.tmp",
        std::process::id(),
        unique
    ));
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(&temp)
        .map_err(|_| BridgeError::StateIo)?;
    let result = (|| {
        let payload = json!({
            "schema_version": 1,
            "epoch": state.epoch,
            "latched": state.latched,
            "reason": state.reason,
        });
        serde_json::to_writer(&mut file, &payload).map_err(|_| BridgeError::StateIo)?;
        file.flush().map_err(|_| BridgeError::StateIo)?;
        file.sync_all().map_err(|_| BridgeError::StateIo)?;
        let metadata = file.metadata().map_err(|_| BridgeError::StateIo)?;
        if metadata.uid() != unsafe { libc::geteuid() } || metadata.mode() & 0o777 != 0o600 {
            return Err(BridgeError::StateIo);
        }
        drop(file);
        fs::rename(&temp, &path).map_err(|_| BridgeError::StateIo)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temp);
    }
    result
}

fn write_model_swap_cancel_marker(path: &Path) -> Result<(), BridgeError> {
    let parent = path.parent().ok_or(BridgeError::StateIo)?;
    fs::create_dir_all(parent).map_err(|_| BridgeError::StateIo)?;
    if path.is_symlink() {
        return Err(BridgeError::StateIo);
    }
    let file_name = path
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or(BridgeError::StateIo)?;
    let temp = parent.join(format!(".{file_name}.{}.tmp", std::process::id()));
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&temp)
        .map_err(|_| BridgeError::StateIo)?;
    let result = (|| {
        serde_json::to_writer(&mut file, &json!({"schema_version": 1, "cancelled": true}))
            .map_err(|_| BridgeError::StateIo)?;
        file.flush().map_err(|_| BridgeError::StateIo)?;
        file.sync_all().map_err(|_| BridgeError::StateIo)?;
        fs::rename(&temp, path).map_err(|_| BridgeError::StateIo)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temp);
    }
    result
}

fn receipt_chat_id(value: Option<&Value>) -> Option<i64> {
    match value? {
        Value::Number(number) => number.as_i64().filter(|id| *id > 0),
        Value::String(text) => text.parse::<i64>().ok().filter(|id| *id > 0),
        _ => None,
    }
}

fn allowed_receipt_outcome(value: &str) -> bool {
    matches!(value, "sent" | "deferred" | "scheduled" | "skipped")
}

fn receipt_reason_slug(value: &str) -> bool {
    let bytes = value.as_bytes();
    (1..=64).contains(&bytes.len())
        && bytes[0].is_ascii_lowercase()
        && bytes[1..]
            .iter()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || *byte == b'_')
}

fn safe_receipt_reason(value: Option<&Value>) -> String {
    value
        .and_then(Value::as_str)
        .filter(|reason| receipt_reason_slug(reason))
        .unwrap_or("unspecified")
        .to_string()
}

fn safe_retrieval_state(value: Option<&Value>) -> String {
    match value.and_then(Value::as_str) {
        Some(state @ ("ok" | "empty" | "skipped" | "error" | "index_not_ready" | "unrecorded")) => {
            state.to_string()
        }
        _ => "unrecorded".to_string(),
    }
}

fn sanitize_recent_receipts(
    root: &serde_json::Map<String, Value>,
    titles: &HashMap<i64, String>,
) -> Vec<SafeRecentReceipt> {
    let Some(rooms) = root
        .get("reply_receipts")
        .and_then(Value::as_object)
        .and_then(|obj| obj.get("rooms"))
        .and_then(Value::as_array)
    else {
        return Vec::new();
    };

    let mut candidates = Vec::<(String, String, SafeRecentReceipt)>::new();
    for room in rooms {
        let Some(room_obj) = room.as_object() else {
            continue;
        };
        let Some(chat_id) = receipt_chat_id(room_obj.get("chat_id")) else {
            continue;
        };
        let Some(receipts) = room_obj.get("receipts").and_then(Value::as_array) else {
            continue;
        };
        let title = titles
            .get(&chat_id)
            .cloned()
            .unwrap_or_else(|| format!("id:{chat_id}"));
        for receipt in receipts {
            let Some(obj) = receipt.as_object() else {
                continue;
            };
            let (
                Some(event_id),
                Some(recorded_at),
                Some(display_time),
                Some(clock),
                Some(outcome),
                Some(reason_text),
                retrieval_state,
            ) = (
                obj.get("event_id").and_then(Value::as_str),
                obj.get("recorded_at").and_then(Value::as_str),
                obj.get("display_time").and_then(Value::as_str),
                obj.get("clock").and_then(Value::as_str),
                obj.get("outcome").and_then(Value::as_str),
                obj.get("reason_text").and_then(Value::as_str),
                safe_retrieval_state(obj.get("retrieval_state")),
            )
            else {
                continue;
            };
            if event_id.is_empty() || recorded_at.is_empty() || !allowed_receipt_outcome(outcome) {
                continue;
            }
            let reason_code = safe_receipt_reason(obj.get("reason_code"));
            let outcome_text = obj
                .get("outcome_text")
                .and_then(Value::as_str)
                .unwrap_or("");
            candidates.push((
                recorded_at.to_string(),
                event_id.to_string(),
                SafeRecentReceipt {
                    chat_id,
                    title: title.chars().take(120).collect(),
                    display_time: display_time.chars().take(32).collect(),
                    clock: clock.chars().take(8).collect(),
                    outcome: outcome.to_string(),
                    outcome_text: outcome_text.chars().take(32).collect(),
                    reason_code,
                    reason_text: reason_text.chars().take(64).collect(),
                    retrieval_state,
                },
            ));
        }
    }

    candidates.sort_by(|left, right| right.0.cmp(&left.0));
    let mut seen = HashSet::<String>::new();
    candidates
        .into_iter()
        .filter_map(|(_, event_id, receipt)| seen.insert(event_id).then_some(receipt))
        .take(12)
        .collect()
}

fn exact_room_reply_readiness(value: &Value) -> Option<&'static str> {
    match value.as_str()? {
        "ready" => Some("ready"),
        "blocked" => Some("blocked"),
        "unknown" => Some("unknown"),
        _ => None,
    }
}

fn sanitize_room_reply_readiness(room: &serde_json::Map<String, Value>) -> &'static str {
    let snake = room.get("reply_readiness");
    let camel = room.get("replyReadiness");
    match (snake, camel) {
        (Some(left), Some(right)) => match (
            exact_room_reply_readiness(left),
            exact_room_reply_readiness(right),
        ) {
            (Some(left), Some(right)) if left == right => left,
            _ => "unknown",
        },
        (Some(value), None) | (None, Some(value)) => {
            exact_room_reply_readiness(value).unwrap_or("unknown")
        }
        (None, None) => "unknown",
    }
}

fn sanitize_snapshot(value: &Value) -> SafeRuntimeSnapshot {
    let Some(root) = value.as_object() else {
        return SafeRuntimeSnapshot {
            available: false,
            rooms: Vec::new(),
            available_chats: Vec::new(),
            jobs: Vec::new(),
            recent_receipts: Vec::new(),
            job_load: 0.0,
            background: SafeBackground::fallback(0.0, 0.0),
            on_device: SafeOnDevice::default(),
            pipeline: SafePipeline::default(),
            terminal_counts: TerminalCounts::default(),
            context_sync: ContextSync {
                mode: "async",
                waited: false,
            },
            reply_model_id: None,
            voice: SafeVoiceStatus::default(),
            error_code: Some("snapshot_invalid".to_string()),
        };
    };

    let mut titles = HashMap::<i64, String>::new();
    let mut available_chats = Vec::new();
    if let Some(chats) = root.get("available_chats").and_then(Value::as_array) {
        for chat in chats {
            let Some(obj) = chat.as_object() else {
                continue;
            };
            let (Some(id), Some(title)) = (
                obj.get("chat_id").and_then(Value::as_i64),
                obj.get("title").and_then(Value::as_str),
            ) else {
                continue;
            };
            if id <= 0 {
                continue;
            }
            let title_clean: String = title.chars().take(120).collect();
            titles.insert(id, title_clean.clone());
            let catalog = obj.get("catalog").and_then(Value::as_bool).unwrap_or(false);
            let live = obj.get("live").and_then(Value::as_bool).unwrap_or(false);
            available_chats.push(SafeAvailableChat {
                chat_id: id,
                title: title_clean,
                catalog,
                live,
            });
        }
    }

    let rooms = root
        .get("rooms")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(|item| {
                    let obj = item.as_object()?;
                    let chat_id = obj.get("chat_id")?.as_i64()?;
                    if chat_id <= 0 {
                        return None;
                    }
                    let fallback = obj.get("selector").and_then(Value::as_str).unwrap_or("");
                    let title = titles.get(&chat_id).cloned().unwrap_or_else(|| {
                        if fallback.is_empty() {
                            format!("id:{chat_id}")
                        } else {
                            fallback.chars().take(120).collect()
                        }
                    });
                    Some(SafeRoom {
                        chat_id,
                        title,
                        live: obj.get("live").and_then(Value::as_bool).unwrap_or(false),
                        auto_reply: obj
                            .get("auto_reply")
                            .and_then(Value::as_bool)
                            .unwrap_or(false),
                        open_jobs: as_u64(obj.get("open_jobs")),
                        reply_readiness: sanitize_room_reply_readiness(obj).to_string(),
                    })
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    let recent_receipts = sanitize_recent_receipts(root, &titles);

    let open_jobs = as_u64(root.get("open_jobs"));
    let background_activity = root
        .get("background")
        .and_then(Value::as_object)
        .and_then(|obj| obj.get("activity"))
        .and_then(Value::as_f64)
        .unwrap_or(0.0);
    let reply_load = open_jobs as f64 / 4.0;
    let job_load = clamp01(reply_load.max(background_activity));
    let background = sanitize_background(root.get("background"), job_load, reply_load);
    let on_device = sanitize_ondevice(root.get("ondevice_hardware"));
    let pipeline = sanitize_pipeline(root.get("pipeline"));

    let mut burst_superseded = 0_u64;
    if let Some(receipt_rooms) = root
        .get("reply_receipts")
        .and_then(Value::as_object)
        .and_then(|obj| obj.get("rooms"))
        .and_then(Value::as_array)
    {
        for room in receipt_rooms {
            let Some(receipts) = room
                .as_object()
                .and_then(|obj| obj.get("receipts"))
                .and_then(Value::as_array)
            else {
                continue;
            };
            for receipt in receipts {
                let Some(obj) = receipt.as_object() else {
                    continue;
                };
                let reason = obj.get("reason_code").and_then(Value::as_str).unwrap_or("");
                let outcome = obj.get("outcome").and_then(Value::as_str).unwrap_or("");
                if reason == "burst_superseded" || outcome == "burst_superseded" {
                    burst_superseded = burst_superseded.saturating_add(1);
                }
            }
        }
    }

    let reply_model_id = root
        .get("reply_model")
        .and_then(Value::as_object)
        .and_then(|obj| obj.get("id"))
        .and_then(Value::as_str)
        .filter(|id| is_snapshot_model_id(id))
        .map(str::to_string);

    SafeRuntimeSnapshot {
        available: true,
        rooms,
        available_chats,
        jobs: Vec::new(),
        recent_receipts,
        job_load,
        background,
        on_device,
        pipeline,
        terminal_counts: TerminalCounts {
            sent: as_u64(root.get("sent")),
            skipped: as_u64(root.get("skipped")),
            delivery_unknown: as_u64(root.get("delivery_unknown")),
            burst_superseded,
        },
        // Unit-1 owns this contract; the shell exposes only the fail-closed
        // contract shape and never forwards evidence or prompt content.
        context_sync: ContextSync {
            mode: "async",
            waited: false,
        },
        reply_model_id,
        voice: SafeVoiceStatus::default(),
        error_code: None,
    }
}

fn sanitize_room_upsert(value: &Value) -> Value {
    let ok = value.get("ok").and_then(Value::as_bool).unwrap_or(true);
    let reason = value.get("reason").and_then(Value::as_str).unwrap_or("");
    serde_json::json!({
        "ok": ok,
        "reason": reason,
    })
}

fn sanitize_models(value: &Value) -> Value {
    let model = value.get("model").and_then(Value::as_str);
    let providers = value
        .get("providers")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(|provider| {
                    let obj = provider.as_object()?;
                    let id = obj.get("id")?.as_str()?;
                    let label = obj.get("label").and_then(Value::as_str).unwrap_or(id);
                    let models = obj
                        .get("models")
                        .and_then(Value::as_array)
                        .map(|models| {
                            models
                                .iter()
                                .filter_map(|model| {
                                    let model_obj = model.as_object()?;
                                    let model_id = model_obj.get("id")?.as_str()?;
                                    let model_label = model_obj
                                        .get("label")
                                        .and_then(Value::as_str)
                                        .unwrap_or(model_id);
                                    Some(json!({"id": model_id, "label": model_label}))
                                })
                                .collect::<Vec<_>>()
                        })
                        .unwrap_or_default();
                    Some(json!({"id": id, "label": label, "models": models}))
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    json!({"ok": value.get("ok").and_then(Value::as_bool).unwrap_or(true), "model": model, "providers": providers})
}

fn sanitize_model_owner_status(value: &Value) -> Value {
    const OWNER_STATES: &[&str] = &[
        "app_owned",
        "model_owner_unknown",
        "model_owner_unmanaged",
        "model_owner_state_invalid",
        "model_owner_state_stale",
        "model_gateway_unavailable",
        "model_residency_mismatch",
        "model_drain_unverified",
    ];
    let reported = value
        .get("owner_state")
        .and_then(Value::as_str)
        .filter(|candidate| OWNER_STATES.contains(candidate))
        .unwrap_or("model_owner_unknown");
    let owner_verified = value
        .get("owner_verified")
        .and_then(Value::as_bool)
        .unwrap_or(false)
        && reported == "app_owned";
    let current = value
        .get("current_model")
        .and_then(Value::as_str)
        .filter(|candidate| is_local_model_id(candidate));
    json!({
        "ok": value.get("ok").and_then(Value::as_bool).unwrap_or(false),
        "action": "model-owner-status",
        "owner_state": if owner_verified { "app_owned" } else { reported },
        "owner_verified": owner_verified,
        "drain_verified": value
            .get("drain_verified")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        "current_model": current,
    })
}

/// Reduce the owner report to fixed state codes, one flag, and a known name.
fn sanitize_mlx_server_status(value: &Value) -> Value {
    const OWNER_STATES: &[&str] = &[
        "app_owned",
        "stopped",
        "foreign_listener",
        "state_invalid",
        "state_stale",
    ];
    let reported = value
        .get("owner_state")
        .and_then(Value::as_str)
        .filter(|candidate| OWNER_STATES.contains(candidate))
        .unwrap_or("state_invalid");
    let app_owned = value
        .get("app_owned")
        .and_then(Value::as_bool)
        .unwrap_or(false)
        && reported == "app_owned";
    let model = value
        .get("model")
        .and_then(Value::as_str)
        .filter(|candidate| is_local_model_name(candidate));
    json!({
        "ok": value.get("ok").and_then(Value::as_bool).unwrap_or(false),
        "action": MLX_SERVER_STATUS_ACTION,
        "owner_state": if app_owned { "app_owned" } else { reported },
        "app_owned": app_owned,
        "model": model,
    })
}

/// Reduce a launch/stop result to allowlisted codes; paths and pids are dropped.
fn sanitize_mlx_lifecycle(value: &Value, launch: bool) -> Value {
    const LAUNCH_REASONS: &[&str] = &[
        "launch_ready",
        "launch_already_running",
        "launch_invalid_spec",
        "launch_executable_unsafe",
        "launch_executable_unverified",
        "launch_model_dir_missing",
        "launch_models_dir_missing",
        "launch_port_in_use",
        "launch_memory_insufficient",
        "launch_memory_unavailable",
        "launch_state_unsafe",
        "launch_spawn_failed",
        "launch_spawn_exited",
        "launch_startup_timeout",
        "launch_attest_failed",
        "launch_state_write_failed",
        "explicit_opt_in_required",
        "mlx_model_not_supported",
    ];
    const STOP_REASONS: &[&str] = &[
        "stop_stopped",
        "stop_no_owned_server",
        "stop_owner_mismatch",
        "stop_signal_failed",
        "explicit_opt_in_required",
    ];
    const STAGES: &[&str] = &[
        "validate",
        "executable",
        "model_dir",
        "models_dir",
        "port_check",
        "memory_check",
        "state_check",
        "spawn",
        "startup",
        "attest",
        "already_running",
        "ready",
        "failed",
    ];
    let (fallback, allowlist) = if launch {
        ("launch_invalid_spec", LAUNCH_REASONS)
    } else {
        ("stop_no_owned_server", STOP_REASONS)
    };
    let reason = value
        .get("reason")
        .and_then(Value::as_str)
        .filter(|candidate| allowlist.contains(candidate))
        .unwrap_or(fallback);
    let ok = value.get("ok").and_then(Value::as_bool).unwrap_or(false);
    if !launch {
        return json!({
            "ok": ok,
            "action": MLX_SERVER_STOP_ACTION,
            "reason": reason,
        });
    }
    let model = value
        .get("model")
        .and_then(Value::as_str)
        .filter(|candidate| is_local_model_name(candidate));
    let stage = value
        .get("stage")
        .and_then(Value::as_str)
        .filter(|candidate| STAGES.contains(candidate));
    let stages: Vec<Value> = value
        .get("stages")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .filter(|candidate| STAGES.contains(candidate))
                .map(|candidate| json!(candidate))
                .collect()
        })
        .unwrap_or_default();
    json!({
        "ok": ok,
        "action": MLX_SERVER_LAUNCH_ACTION,
        "stage": stage,
        "reason": reason,
        "model": model,
        "stages": stages,
    })
}

fn sanitize_model_action(value: &Value, action: &str, model: &str) -> Value {
    let contract_matches = value.get("ok").and_then(Value::as_bool).unwrap_or(false)
        && value.get("action").and_then(Value::as_str) == Some(action)
        && value.get("model").and_then(Value::as_str) == Some(model);
    let stored = value
        .get("stored")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let prepared = value
        .get("prepared")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let completed = match action {
        "model-set" => stored,
        "model-prepare" => prepared,
        _ => false,
    };
    json!({
        "ok": contract_matches && completed,
        "action": action,
        "model": model,
        "stored": stored,
        "prepared": prepared,
        "needs_prepare": value.get("needs_prepare").and_then(Value::as_bool).unwrap_or(false),
    })
}

fn sanitize_model_swap_action(value: &Value) -> Value {
    const STAGES: &[&str] = &[
        "idle",
        "drain",
        "unload",
        "memory_check",
        "load",
        "probe",
        "rollback",
        "ready",
        "aborted",
        "failed",
    ];
    const REASONS: &[&str] = &[
        "ready",
        "already_resident",
        "cancelled",
        "drain_timeout",
        "explicit_opt_in_required",
        "model_not_allowed",
        "model_owner_unknown",
        "model_owner_unmanaged",
        "model_owner_state_invalid",
        "model_owner_state_stale",
        "model_gateway_unavailable",
        "model_residency_mismatch",
        "model_drain_unverified",
        "memory_budget_unavailable",
        "insufficient_free_memory",
        "unload_failed",
        "load_failed",
        "probe_failed",
        "load_failed_rollback_failed",
        "probe_failed_rollback_failed",
        "cancelled_rollback_failed",
        "model_state_write_failed",
        "model_state_write_failed_rollback_failed",
        "model_override_write_failed",
        "model_override_write_failed_rollback_failed",
        "model_swap_busy",
        "model_residency_uncertain",
        "memory_budget_unavailable_rollback_failed",
        "insufficient_free_memory_rollback_failed",
    ];
    let stage = value
        .get("stage")
        .and_then(Value::as_str)
        .filter(|candidate| STAGES.contains(candidate))
        .unwrap_or("failed");
    let reason = value
        .get("reason")
        .and_then(Value::as_str)
        .filter(|candidate| REASONS.contains(candidate))
        .unwrap_or("model_owner_unknown");
    let stages = value
        .get("stages")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .filter(|candidate| STAGES.contains(candidate))
                .take(12)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    let stored = value
        .get("stored")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let prepared = value
        .get("prepared")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let contract_matches = value.get("ok").and_then(Value::as_bool).unwrap_or(false)
        && value.get("action").and_then(Value::as_str) == Some("model-swap")
        && value.get("model").and_then(Value::as_str) == Some(SWAP_MODEL_ID)
        && stage == "ready"
        && matches!(reason, "ready" | "already_resident")
        && stored
        && prepared;
    json!({
        "ok": contract_matches,
        "action": "model-swap",
        "model": SWAP_MODEL_ID,
        "stage": stage,
        "reason": reason,
        "stages": stages,
        "stored": stored,
        "prepared": prepared,
    })
}

fn sanitize_dream_rsi(value: &Value) -> Value {
    json!({
        "ok": value.get("ok").and_then(Value::as_bool).unwrap_or(false),
        "status": value.get("status").and_then(Value::as_str).unwrap_or("unknown"),
        "selected_policy": value.get("selected_policy").and_then(Value::as_str),
        "gold_rows": as_u64(value.get("gold_rows")),
        "excluded_model_gold": as_u64(value.get("excluded_model_gold")),
        "gold_source_policy": value.get("gold_source_policy").and_then(Value::as_str).unwrap_or("unknown")
    })
}

fn sanitize_knowledge_status(value: &Value) -> Value {
    json!({
        "ok": value.get("ok").and_then(Value::as_bool).unwrap_or(false),
        "node_count": as_u64(value.get("node_count")),
        "edge_count": as_u64(value.get("edge_count")),
        "grounded_nodes": as_u64(value.get("grounded_nodes")),
        "indexed_at": as_u64(value.get("indexed_at")),
        "indexed_count": as_u64(value.get("indexed_count")),
        "dense_status": bounded_status_string(value.get("dense_status"), 400, "unknown"),
        "dense_indexed_at": as_u64(value.get("dense_indexed_at")),
        "stale": value.get("stale").and_then(Value::as_bool).unwrap_or(true),
        "snapshot_status": value.get("snapshot_status").and_then(Value::as_str).unwrap_or("unknown"),
        "indexing_mode": value.get("indexing_mode").and_then(Value::as_str).unwrap_or("unknown")
    })
}

fn sanitize_knowledge_evidence(value: Option<&Value>) -> Value {
    let source_event_ids = value
        .and_then(|item| item.get("source_event_ids"))
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .take(16)
                .map(|item| item.chars().take(128).collect::<String>())
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    let kind = match value
        .and_then(|item| item.get("kind"))
        .and_then(Value::as_str)
    {
        Some("ledger") => "ledger",
        Some("snapshot") => "snapshot",
        _ => "seed",
    };
    json!({
        "kind": kind,
        "source_event_ids": source_event_ids,
        "chat_id": bounded_json_string(value.and_then(|item| item.get("chat_id")), 128),
        "confirmed_at": value
            .and_then(|item| item.get("confirmed_at"))
            .and_then(Value::as_str)
            .map(|item| item.chars().take(64).collect::<String>()),
        "retracted": value
            .and_then(|item| item.get("retracted"))
            .and_then(Value::as_bool)
            .unwrap_or(false),
    })
}

fn sanitize_knowledge_graph(value: &Value) -> Value {
    let mut node_ids = HashSet::new();
    let nodes = value
        .get("nodes")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(|node| {
                    let id = bounded_json_string(node.get("id"), 192);
                    if id.is_empty() || id.starts_with("message:") || id.starts_with("msg:") {
                        return None;
                    }
                    node_ids.insert(id.clone());
                    Some(json!({
                        "id": id,
                        "label": bounded_json_string(node.get("label"), 160),
                        "category": bounded_json_string(node.get("category"), 96),
                        "importance": node.get("importance").and_then(Value::as_i64).unwrap_or(0).clamp(0, 100),
                        "updated_at": as_u64(node.get("updated_at")),
                        "evidence": sanitize_knowledge_evidence(node.get("evidence")),
                        "description": bounded_json_string(node.get("description"), 2400),
                        "facts": bounded_string_list(node.get("facts"), 6, 600),
                    }))
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();

    let edges = value
        .get("edges")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(|edge| {
                    let source = bounded_json_string(edge.get("source"), 192);
                    let target = bounded_json_string(edge.get("target"), 192);
                    if !node_ids.contains(&source) || !node_ids.contains(&target) {
                        return None;
                    }
                    Some(json!({
                        "source": source,
                        "relation": bounded_json_string(edge.get("relation"), 128),
                        "target": target,
                        "context": bounded_json_string(edge.get("context"), 320),
                        "weight": edge.get("weight").and_then(Value::as_i64).unwrap_or(0).clamp(0, 1000),
                        "room_id": bounded_json_string(edge.get("room_id"), 128),
                        "valid_from": bounded_json_string(edge.get("valid_from"), 64),
                        "valid_to": bounded_json_string(edge.get("valid_to"), 64),
                        "evidence_message_id": bounded_json_string(edge.get("evidence_message_id"), 128),
                        "evidence": sanitize_knowledge_evidence(edge.get("evidence")),
                    }))
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();

    json!({
        "ok": value.get("ok").and_then(Value::as_bool).unwrap_or(false),
        "nodes": nodes,
        "edges": edges,
        "node_count": node_ids.len(),
        "edge_count": edges.len(),
        "grounded_nodes": as_u64(value.get("grounded_nodes")),
        "indexed_at": as_u64(value.get("indexed_at")),
        "indexed_count": as_u64(value.get("indexed_count")),
        "stale": value.get("stale").and_then(Value::as_bool).unwrap_or(true),
        "osk": {
            "state": bounded_json_string(value.get("osk").and_then(|v| v.get("state")), 32),
            "engine": bounded_json_string(value.get("osk").and_then(|v| v.get("engine")), 32),
            "synced_at": as_u64(value.get("osk").and_then(|v| v.get("synced_at"))),
            "pending": as_u64(value.get("osk").and_then(|v| v.get("pending"))),
            "conflicts": as_u64(value.get("osk").and_then(|v| v.get("conflicts"))),
        },
    })
}

fn bounded_string_list(value: Option<&Value>, count: usize, chars: usize) -> Vec<String> {
    value
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .take(count)
                .map(|item| item.chars().take(chars).collect())
                .collect()
        })
        .unwrap_or_default()
}

fn source_number(value: Option<&Value>) -> String {
    match value {
        Some(Value::String(text))
            if text.bytes().all(|byte| byte.is_ascii_digit())
                && text.parse::<u64>().is_ok_and(|id| id < i64::MAX as u64) =>
        {
            text.clone()
        }
        Some(Value::Number(number)) => number
            .as_u64()
            .filter(|id| *id < i64::MAX as u64)
            .map(|id| id.to_string())
            .unwrap_or_default(),
        _ => String::new(),
    }
}

fn sanitize_knowledge_focus(value: &Value, expected_node: &str) -> Value {
    let facts = value
        .get("facts")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .take(12)
                .map(|item| item.chars().take(512).collect::<String>())
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    let details = value.get("details").filter(|details| {
        !expected_node.is_empty()
            && details.get("node_id").and_then(Value::as_str) == Some(expected_node)
    });
    let scope = source_number(details.and_then(|details| details.get("scope_room_id")));
    let identity: Vec<_> = expected_node.split(':').collect();
    let mut sources = Vec::new();
    let mut seen = HashSet::new();
    let mut quote_budget = 4800;
    if details.is_some() && value.get("ok").and_then(Value::as_bool) == Some(true) {
        for source in value
            .get("sources")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .take(12)
        {
            let id = bounded_json_string(source.get("source_id"), 192);
            let parts: Vec<_> = id.split(':').collect();
            let actor = source_number(source.get("author_id"));
            if parts.len() != 6
                || parts[0] != "kakao"
                || parts[1].len() != 64
                || !parts[1].bytes().all(|byte| byte.is_ascii_hexdigit())
                || parts[2] != "room"
                || parts[4] != "log"
                || !parts[3].bytes().all(|byte| byte.is_ascii_digit())
                || !parts[5].bytes().all(|byte| byte.is_ascii_digit())
                || parts[3].parse::<i64>().unwrap_or(0) <= 0
                || parts[5].parse::<i64>().unwrap_or(0) <= 0
                || actor.is_empty()
                || source.get("source_kind").and_then(Value::as_str) != Some("local_db_snapshot")
                || (!scope.is_empty() && scope != parts[3])
                || (identity.len() == 5
                    && matches!(identity[0], "person" | "chat")
                    && (identity[2] != parts[1]
                        || (identity[0] == "person" && identity[4] != actor)
                        || (identity[0] == "chat" && identity[4] != parts[3])))
                || seen.contains(&id)
            {
                continue;
            }
            let role = source
                .get("source_role")
                .and_then(Value::as_str)
                .unwrap_or("");
            if !matches!(
                role,
                "peer_history" | "system_history" | "outgoing_unclassified"
            ) {
                continue;
            }
            let content = bounded_json_string(source.get("content"), quote_budget.min(1000));
            if content.is_empty() {
                continue;
            }
            quote_budget -= content.chars().count();
            seen.insert(id.clone());
            sources.push(json!({"source_id":id,"source_kind":"local_db_snapshot","source_role":role,
                "chat_id":parts[3],"author_id":actor,"content":content,
                "sender":bounded_json_string(source.get("sender"),128),
                "room_title":bounded_json_string(source.get("room_title"),128),
                "date":bounded_json_string(source.get("date"),64),
                "truncated":source.get("truncated").and_then(Value::as_bool).unwrap_or(false)
                    || content.chars().count() < source.get("content").and_then(Value::as_str).map(|value|value.chars().count()).unwrap_or(0)}));
            if sources.len() == 6 || quote_budget == 0 {
                break;
            }
        }
    }
    let safe_details = details.map(|details|json!({
        "node_id":expected_node,"summary":bounded_json_string(details.get("summary"),1200),
        "kind":bounded_json_string(details.get("kind"),96),"basis":bounded_json_string(details.get("basis"),32),
        "key_facts":bounded_string_list(details.get("key_facts"),6,600),"scope_room_id":scope,
        "source_updated_at":as_u64(details.get("source_updated_at")),"note_updated_at":as_u64(details.get("note_updated_at")),
        "source_unavailable":details.get("source_unavailable").and_then(Value::as_bool).unwrap_or(false)
    }));
    json!({
        "ok": value.get("ok").and_then(Value::as_bool).unwrap_or(false),
        "facts": facts,
        "fact_count": as_u64(value.get("fact_count")),
        "focus_node_id": bounded_json_string(value.get("focus_node_id"), 192),
        "focus_k": as_u64(value.get("focus_k")).clamp(0, 3),
        "focus_node_count": as_u64(value.get("focus_node_count")),
        "focus_edge_count": as_u64(value.get("focus_edge_count")),
        "search_mode": bounded_json_string(value.get("search_mode"), 64),
        "index_version": bounded_json_string(value.get("index_version"), 64),
        "watermark": bounded_json_string(value.get("watermark"), 96),
        "details": safe_details,
        "sources": sources,
    })
}

#[cfg(test)]
mod tests {
    #[test]
    fn manual_voice_stop_signals_only_its_child_and_preserves_cleanup() {
        let root = std::env::temp_dir().canonicalize().unwrap().join(format!(
            "alden-owned-voice-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&root).unwrap();
        let ready = root.join("ready");
        let ended = root.join("ended");
        let script = format!("import signal,time,sys\nfrom pathlib import Path\ndef end(*args):\n Path({:?}).write_text('closed')\n sys.exit(0)\nsignal.signal(signal.SIGTERM,end)\nPath({:?}).write_text('ready')\ntime.sleep(10)", ended.to_str().unwrap(), ready.to_str().unwrap());
        let mut other = Command::new("python3")
            .args(["-E", "-B", "-s", "-c", "import time; time.sleep(10)"])
            .spawn()
            .unwrap();
        let child = Command::new("python3")
            .args(["-E", "-B", "-s", "-c", &script])
            .spawn()
            .unwrap();
        let deadline = Instant::now() + Duration::from_secs(2);
        while !ready.is_file() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
        assert!(ready.is_file());
        let hard = AtomicBool::new(true);
        supervise_manual_voice(child, &hard, Duration::from_secs(5), || false);
        assert_eq!(fs::read_to_string(ended).unwrap(), "closed");
        assert!(other.try_wait().unwrap().is_none());
        other.kill().unwrap();
        other.wait().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn manual_voice_timeout_bounds_an_uncooperative_owned_child() {
        use std::io::BufRead;
        let mut child = Command::new("python3")
            .args([
                "-E",
                "-B",
                "-s",
                "-c",
                "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(10)",
            ])
            .stdout(Stdio::piped()).spawn()
            .unwrap();
        let mut ready = String::new();
        std::io::BufReader::new(child.stdout.take().unwrap())
            .read_line(&mut ready)
            .unwrap();
        assert_eq!(ready.trim(), "ready");
        let started = Instant::now();
        supervise_manual_voice(
            child,
            &AtomicBool::new(false),
            Duration::from_millis(150),
            || false,
        );
        assert!(started.elapsed() < Duration::from_secs(3));
    }

    use super::*;
    use std::os::unix::fs::{symlink, PermissionsExt};

    fn receipt(event_id: &str, recorded_at: &str, outcome: &str, reason_code: &str) -> Value {
        let outcome_text = match outcome {
            "sent" => "전송 완료",
            "deferred" => "보류",
            "scheduled" => "대기",
            "skipped" => "건너뜀",
            _ => "상태 기록 없음",
        };
        json!({
            "event_id": event_id,
            "recorded_at": recorded_at,
            "display_time": "09-21 16:00",
            "clock": "16:00",
            "outcome": outcome,
            "outcome_text": outcome_text,
            "reason_code": reason_code,
            "reason_text": "안전한 사유",
            "retrieval_state": "ok",
            "message": "drop body",
            "prompt": "drop prompt",
            "token": "drop token",
            "preview": "drop preview",
            "preview_text": "drop preview text",
            "summary": "drop summary",
            "detail": "drop detail",
            "chat": "drop chat",
            "model": "drop model",
            "model_attempts": [{"model": "drop detail"}],
            "log_id": 123,
            "candidates": 9,
            "retrieved": 8,
            "included": 7,
            "evidence_ids": ["drop evidence"],
            "ledger": "drop ledger"
        })
    }

    #[test]
    fn rejects_bad_json() {
        assert!(matches!(parse_json_output(b"{bad"), Err(BridgeError::Json)));
    }

    #[test]
    fn browser_tool_action_is_exactly_allowlisted_and_bounded() {
        let task = "inspect the owned page";
        let args = browser_tool_args("browser-1", task).unwrap();
        assert_eq!(
            args,
            vec!["--action", "tool-browser", "--job-id", "browser-1",]
        );
        assert!(!args.iter().any(|value| value.contains(task)));
        assert!(!args.iter().any(|value| {
            value == "--model" || value == "--profile" || value == "--browser-profile"
        }));

        for (job_id, task) in [
            ("../bad", "task"),
            ("", "task"),
            ("browser-2", "   "),
            ("browser-3", "bad\0task"),
        ] {
            assert!(matches!(
                browser_tool_args(job_id, task),
                Err(BridgeError::ActionNotAllowed)
            ));
        }
        let oversized = "가".repeat(BROWSER_TOOL_TASK_LIMIT_BYTES / 3 + 1);
        assert!(matches!(
            browser_tool_args("browser-4", &oversized),
            Err(BridgeError::ActionNotAllowed)
        ));
        assert!(valid_browser_tool_job_id(
            "123e4567-e89b-42d3-a456-426614174000"
        ));
        assert!(!valid_browser_tool_job_id(
            &"a".repeat(BROWSER_TOOL_JOB_ID_LIMIT + 1)
        ));
    }

    #[test]
    fn browser_runtime_selector_is_fixed_and_action_specific() {
        let root = std::env::temp_dir().canonicalize().unwrap().join(format!(
            "openkakao-browser-runtime-selector-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        let menubar_python = root.join("menubar/bin/python3.11");
        let browser_python = root.join("browser/bin/python3.11");
        let browsers = root.join("browser/ms-playwright");
        fs::create_dir_all(menubar_python.parent().unwrap()).unwrap();
        fs::create_dir_all(browser_python.parent().unwrap()).unwrap();
        fs::create_dir_all(&browsers).unwrap();
        for python in [&menubar_python, &browser_python] {
            fs::write(python, b"fixture interpreter").unwrap();
            fs::set_permissions(python, fs::Permissions::from_mode(0o755)).unwrap();
        }

        let normal =
            select_python_runtime(&menubar_python, &browser_python, &browsers, true, false)
                .unwrap();
        assert_eq!(normal.executable, menubar_python.to_string_lossy());
        assert!(normal.env.is_empty());

        let browser =
            select_python_runtime(&menubar_python, &browser_python, &browsers, true, true).unwrap();
        assert_eq!(browser.executable, browser_python.to_string_lossy());
        let env = browser.env.into_iter().collect::<HashMap<_, _>>();
        assert_eq!(
            env.get(OsStr::new("PLAYWRIGHT_BROWSERS_PATH")),
            Some(&browsers.clone().into_os_string())
        );
        assert_eq!(
            env.get(OsStr::new("ANONYMIZED_TELEMETRY")),
            Some(&OsString::from("false"))
        );
        assert_eq!(env.len(), 2);

        let linked = root.join("browser-link");
        symlink(root.join("browser"), &linked).unwrap();
        assert!(matches!(
            select_python_runtime(
                &menubar_python,
                &linked.join("bin/python3.11"),
                &linked.join("ms-playwright"),
                true,
                true,
            ),
            Err(BridgeError::BrowserEnv)
        ));
        assert!(matches!(
            select_python_runtime(
                &menubar_python,
                &root.join("missing/python3.11"),
                &browsers,
                true,
                true,
            ),
            Err(BridgeError::BrowserEnv)
        ));
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn bridge_job_registry_tracks_and_finishes_jobs() {
        let bridge = PythonBridge::new();
        bridge.begin_job(&"j".repeat(80), "browser", "running", 1.4);
        let jobs = bridge.jobs_snapshot();
        assert_eq!(jobs.len(), 1);
        assert_eq!(jobs[0].job_id.chars().count(), BROWSER_TOOL_JOB_ID_LIMIT);
        assert_eq!(jobs[0].kind, "browser");
        assert_eq!(jobs[0].stage, "running");
        assert_eq!(jobs[0].load, 1.0);
        assert!(jobs[0].error_code.is_none());

        let bounded_id = "j".repeat(BROWSER_TOOL_JOB_ID_LIMIT);
        bridge.set_job_error(&bounded_id, Some("browser_job_failed"));
        assert_eq!(
            bridge.jobs_snapshot()[0].error_code.as_deref(),
            Some("browser_job_failed")
        );
        bridge.finish_job(&bounded_id);
        assert!(bridge.jobs_snapshot().is_empty());
    }

    #[test]
    fn bridge_job_registry_is_newest_first_capped_and_drops_stale_entries() {
        let bridge = PythonBridge::new();
        let now = epoch_seconds();
        {
            let mut jobs = bridge.jobs.lock().unwrap();
            for index in 0..10 {
                let job_id = format!("job-{index}");
                jobs.insert(
                    job_id.clone(),
                    SafeJobEvent {
                        job_id,
                        kind: "browser".to_string(),
                        stage: "running".to_string(),
                        load: 0.7,
                        time: now - index as f64,
                        error_code: None,
                    },
                );
            }
            jobs.insert(
                "stale".to_string(),
                SafeJobEvent {
                    job_id: "stale".to_string(),
                    kind: "browser".to_string(),
                    stage: "running".to_string(),
                    load: 0.7,
                    time: now - JOB_EVENT_MAX_AGE_SECS - 1.0,
                    error_code: None,
                },
            );
        }

        let snapshot = bridge.jobs_snapshot();
        assert_eq!(snapshot.len(), JOB_EVENT_CAP);
        assert_eq!(snapshot[0].job_id, "job-0");
        assert_eq!(snapshot[JOB_EVENT_CAP - 1].job_id, "job-7");
        assert!(!snapshot.iter().any(|event| event.job_id == "stale"));
        assert!(!bridge.jobs.lock().unwrap().contains_key("stale"));
    }

    #[test]
    fn bridge_job_registry_merges_into_safe_snapshot_and_serializes_only_safe_keys() {
        let bridge = PythonBridge::new();
        bridge.begin_job("browser-1", "browser", "running", 0.7);
        let mut snapshot = sanitize_snapshot(&json!({"open_jobs": 0}));
        bridge.merge_jobs(&mut snapshot);
        assert_eq!(snapshot.jobs.len(), 1);

        let serialized = serde_json::to_value(&snapshot.jobs[0]).unwrap();
        let event = serialized.as_object().unwrap();
        assert_eq!(
            event.keys().map(String::as_str).collect::<HashSet<_>>(),
            HashSet::from(["jobId", "kind", "stage", "load", "time", "errorCode"])
        );
        let text = serde_json::to_string(event).unwrap();
        for forbidden in ["task", "prompt", "token", "message", "conversation"] {
            assert!(!text.contains(forbidden));
        }
    }

    #[test]
    fn bridge_job_registry_recovers_from_poisoned_lock_without_panicking() {
        let bridge = PythonBridge::new();
        let jobs = bridge.jobs.clone();
        let _ = std::panic::catch_unwind(move || {
            let _guard = jobs.lock().unwrap();
            panic!("poison job registry for recovery test");
        });

        bridge.begin_job("after-poison", "browser", "running", 0.7);
        let snapshot = bridge.jobs_snapshot();
        assert_eq!(snapshot.len(), 1);
        assert_eq!(snapshot[0].job_id, "after-poison");
        bridge.finish_job("after-poison");
        assert!(bridge.jobs_snapshot().is_empty());
    }

    #[test]
    fn browser_tool_result_is_bounded_and_redacted() {
        let completed = sanitize_browser_tool_result(&json!({
            "ok": true,
            "status": "completed",
            "errorCode": "",
            "result": "safe result",
            "secret": "drop-me",
        }));
        assert_eq!(
            completed,
            SafeBrowserToolResult {
                ok: true,
                status: "completed".to_string(),
                error_code: String::new(),
                result: "safe result".to_string(),
            }
        );

        let failed = sanitize_browser_tool_result(&json!({
            "ok": false,
            "status": "failed",
            "errorCode": "browser_job_failed",
            "result": "private body must be dropped",
        }));
        assert_eq!(failed.result, "");
        assert_eq!(failed.error_code, "browser_job_failed");

        let oversized = sanitize_browser_tool_result(&json!({
            "ok": true,
            "status": "completed",
            "errorCode": "",
            "result": "x".repeat(BROWSER_TOOL_RESULT_LIMIT_BYTES + 1),
        }));
        assert_eq!(oversized.error_code, "browser_result_too_large");
        assert_eq!(oversized.result, "");

        let unknown = sanitize_browser_tool_result(&json!({
            "ok": false,
            "status": "failed",
            "errorCode": "credential=private",
            "result": "",
        }));
        assert_eq!(unknown.error_code, "browser_job_failed");
    }

    #[test]
    fn local_model_actions_are_exactly_allowlisted() {
        assert_eq!(
            RESIDENT_MODEL_ID,
            "ddalcu/Qwen3.8-Flash-Next-MLX-Serve-iQ-MLX-3.3bpw"
        );
        assert_eq!(
            LEGACY_RESIDENT_MODEL_ID,
            "ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit"
        );
        for model in [RESIDENT_MODEL_ID, SWAP_MODEL_ID] {
            let set_args =
                settings_action_args("model-set", None, None, None, Some(model), None, None)
                    .unwrap();
            assert_eq!(
                set_args,
                vec!["--action", "model-set", "--model", model, "--no-wait",]
            );
        }

        let prepare_args = settings_action_args(
            "model-prepare",
            None,
            None,
            None,
            Some(SWAP_MODEL_ID),
            None,
            None,
        )
        .unwrap();
        assert_eq!(
            prepare_args,
            vec!["--action", "model-prepare", "--model", SWAP_MODEL_ID]
        );

        for (action, model) in [
            ("model-set", LEGACY_RESIDENT_MODEL_ID),
            ("model-prepare", RESIDENT_MODEL_ID),
            ("model-set", "../../tmp/model"),
            ("model-prepare", "remote/arbitrary --flag"),
        ] {
            assert!(matches!(
                settings_action_args(action, None, None, None, Some(model), None, None),
                Err(BridgeError::ActionNotAllowed)
            ));
        }
        assert!(matches!(
            settings_action_args("model-set", None, None, None, None, None, None),
            Err(BridgeError::ActionNotAllowed)
        ));

        let token = "123e4567-e89b-42d3-a456-426614174000";
        let swap_args = settings_action_args(
            "model-swap",
            None,
            None,
            None,
            Some(SWAP_MODEL_ID),
            Some(true),
            Some(token),
        )
        .unwrap();
        assert_eq!(
            swap_args,
            vec![
                "--action",
                "model-swap",
                "--model",
                SWAP_MODEL_ID,
                "--explicit-opt-in",
                MODEL_SWAP_OPT_IN,
                "--request-token",
                token,
            ]
        );
        for (opt_in, token) in [
            (None, Some(token)),
            (Some(false), Some(token)),
            (Some(true), None),
            (Some(true), Some("../../bad")),
        ] {
            assert!(matches!(
                settings_action_args(
                    "model-swap",
                    None,
                    None,
                    None,
                    Some(SWAP_MODEL_ID),
                    opt_in,
                    token,
                ),
                Err(BridgeError::ActionNotAllowed)
            ));
        }
        for token in [
            "123e4567-e89b-02d3-a456-426614174000",
            "123e4567-e89b-42d3-c456-426614174000",
            "123E4567-e89b-42d3-a456-426614174000",
        ] {
            assert!(!valid_model_swap_token(token));
        }
        assert!(matches!(
            settings_action_args(
                "models",
                None,
                None,
                None,
                None,
                Some(true),
                Some("123e4567-e89b-42d3-a456-426614174000"),
            ),
            Err(BridgeError::ActionNotAllowed)
        ));
    }

    #[test]
    fn model_owner_status_is_code_only_and_read_only() {
        assert_eq!(
            settings_action_args("model-owner-status", None, None, None, None, None, None,)
                .unwrap(),
            vec!["--action", "model-owner-status"]
        );
        // The owner probe never accepts model, opt-in, or token overrides.
        assert!(matches!(
            settings_action_args(
                "model-owner-status",
                None,
                None,
                None,
                Some(SWAP_MODEL_ID),
                Some(true),
                Some("123e4567-e89b-42d3-a456-426614174000"),
            ),
            Err(BridgeError::ActionNotAllowed)
        ));

        let safe = sanitize_model_owner_status(&json!({
            "ok": true,
            "action": "model-owner-status",
            "owner_state": "model_owner_unmanaged",
            "owner_verified": false,
            "drain_verified": false,
            "current_model": RESIDENT_MODEL_ID,
            "argv": ["--serve", "credential=private"],
            "path": "/Users/private/models",
        }));
        assert_eq!(safe["owner_state"], "model_owner_unmanaged");
        assert_eq!(safe["owner_verified"], false);
        assert_eq!(safe["current_model"], RESIDENT_MODEL_ID);
        assert!(safe.get("argv").is_none());
        assert!(safe.get("path").is_none());

        let legacy = sanitize_model_owner_status(&json!({
            "ok": true,
            "action": "model-owner-status",
            "owner_state": "app_owned",
            "owner_verified": true,
            "drain_verified": true,
            "current_model": LEGACY_RESIDENT_MODEL_ID,
        }));
        assert_eq!(legacy["owner_verified"], true);
        assert_eq!(legacy["current_model"], LEGACY_RESIDENT_MODEL_ID);

        // A claim of verified ownership is only honoured for the app-owned code.
        let coerced = sanitize_model_owner_status(&json!({
            "ok": true,
            "owner_state": "model_owner_unknown",
            "owner_verified": true,
            "current_model": "remote/arbitrary",
        }));
        assert_eq!(coerced["owner_state"], "model_owner_unknown");
        assert_eq!(coerced["owner_verified"], false);
        assert!(coerced["current_model"].is_null());

        let unknown = sanitize_model_owner_status(&json!({
            "owner_state": "../../etc/passwd",
            "owner_verified": true,
        }));
        assert_eq!(unknown["owner_state"], "model_owner_unknown");
        assert_eq!(unknown["ok"], false);
    }

    #[test]
    fn fixed_model_names_match_served_ids() {
        assert!(RESIDENT_MODEL_ID.ends_with(RESIDENT_MODEL_NAME));
        assert!(LEGACY_RESIDENT_MODEL_ID.ends_with(LEGACY_RESIDENT_MODEL_NAME));
        assert!(SWAP_MODEL_ID.ends_with(SWAP_MODEL_NAME));
    }

    #[test]
    fn mlx_server_status_is_code_only_and_read_only() {
        assert_eq!(
            settings_action_args(MLX_SERVER_STATUS_ACTION, None, None, None, None, None, None)
                .unwrap(),
            vec!["--action", "mlx-server-status"]
        );
        // The read-only probe never accepts model, opt-in, or token overrides.
        assert!(matches!(
            settings_action_args(
                MLX_SERVER_STATUS_ACTION,
                None,
                None,
                None,
                Some(SWAP_MODEL_ID),
                Some(true),
                Some("123e4567-e89b-42d3-a456-426614174000"),
            ),
            Err(BridgeError::ActionNotAllowed)
        ));

        let safe = sanitize_mlx_server_status(&json!({
            "ok": true,
            "action": "mlx-server-status",
            "owner_state": "foreign_listener",
            "app_owned": false,
            "model": null,
            "executable": "/Applications/MLX Core.app/Contents/MacOS/mlx-serve",
            "pid": 38868,
        }));
        assert_eq!(safe["owner_state"], "foreign_listener");
        assert_eq!(safe["app_owned"], false);
        assert!(safe["model"].is_null());
        assert!(safe.get("executable").is_none());
        assert!(safe.get("pid").is_none());

        for known in [
            RESIDENT_MODEL_NAME,
            LEGACY_RESIDENT_MODEL_NAME,
            SWAP_MODEL_NAME,
        ] {
            let known_status = sanitize_mlx_server_status(&json!({
                "ok": true,
                "action": "mlx-server-status",
                "owner_state": "app_owned",
                "app_owned": true,
                "model": known,
            }));
            assert_eq!(known_status["model"], known);
        }

        // A verified claim is only honoured when the code says app_owned.
        let coerced = sanitize_mlx_server_status(&json!({
            "ok": true,
            "owner_state": "foreign_listener",
            "app_owned": true,
        }));
        assert_eq!(coerced["owner_state"], "foreign_listener");
        assert_eq!(coerced["app_owned"], false);

        let unknown = sanitize_mlx_server_status(&json!({
            "owner_state": "../../etc/passwd",
            "model": "/Users/private/models",
        }));
        assert_eq!(unknown["owner_state"], "state_invalid");
        assert_eq!(unknown["ok"], false);
        assert!(unknown["model"].is_null());
    }

    #[test]
    fn mlx_server_launch_requires_opt_in_and_a_fixed_model() {
        for model in [RESIDENT_MODEL_ID, LEGACY_RESIDENT_MODEL_ID, SWAP_MODEL_ID] {
            assert_eq!(
                settings_action_args(
                    MLX_SERVER_LAUNCH_ACTION,
                    None,
                    None,
                    None,
                    Some(model),
                    Some(true),
                    None,
                )
                .unwrap(),
                vec![
                    "--action",
                    "mlx-server-launch",
                    "--model",
                    model,
                    "--explicit-opt-in"
                ]
            );
        }
        for (model, opt_in, token) in [
            (Some(SWAP_MODEL_ID), None, None),
            (Some(SWAP_MODEL_ID), Some(false), None),
            (Some("remote/arbitrary"), Some(true), None),
            (None, Some(true), None),
            (
                Some(SWAP_MODEL_ID),
                Some(true),
                Some("123e4567-e89b-42d3-a456-426614174000"),
            ),
        ] {
            assert!(
                matches!(
                    settings_action_args(
                        MLX_SERVER_LAUNCH_ACTION,
                        None,
                        None,
                        None,
                        model,
                        opt_in,
                        token,
                    ),
                    Err(BridgeError::ActionNotAllowed)
                ),
                "launch accepted model={model:?} opt_in={opt_in:?} token={token:?}"
            );
        }
    }

    #[test]
    fn mlx_server_stop_requires_opt_in() {
        assert_eq!(
            settings_action_args(
                MLX_SERVER_STOP_ACTION,
                None,
                None,
                None,
                None,
                Some(true),
                None,
            )
            .unwrap(),
            vec!["--action", "mlx-server-stop", "--explicit-opt-in"]
        );
        assert!(matches!(
            settings_action_args(
                MLX_SERVER_STOP_ACTION,
                None,
                None,
                None,
                Some(SWAP_MODEL_ID),
                Some(true),
                None,
            ),
            Err(BridgeError::ActionNotAllowed)
        ));
        assert!(matches!(
            settings_action_args(MLX_SERVER_STOP_ACTION, None, None, None, None, None, None),
            Err(BridgeError::ActionNotAllowed)
        ));
    }

    #[test]
    fn mlx_lifecycle_sanitizers_drop_paths_pids_and_unknown_codes() {
        let launch = sanitize_mlx_lifecycle(
            &json!({
                "ok": false,
                "action": "mlx-server-launch",
                "stage": "failed",
                "reason": "launch_port_in_use",
                "model": null,
                "stages": ["validate", "not-a-stage", "port_check"],
                "pid": 4242,
                "log_path": "/Users/private/mlx.log",
            }),
            true,
        );
        assert_eq!(launch["ok"], false);
        assert_eq!(launch["action"], "mlx-server-launch");
        assert_eq!(launch["reason"], "launch_port_in_use");
        assert_eq!(launch["stage"], "failed");
        assert_eq!(launch["stages"], json!(["validate", "port_check"]));
        assert!(launch.get("pid").is_none());
        assert!(launch.get("log_path").is_none());

        let unknown = sanitize_mlx_lifecycle(
            &json!({"ok": false, "reason": "not-a-real-code", "stage": "bogus"}),
            true,
        );
        assert_eq!(unknown["reason"], "launch_invalid_spec");
        assert!(unknown["stage"].is_null());
        assert_eq!(unknown["stages"], json!([]));

        let model = sanitize_mlx_lifecycle(
            &json!({
                "ok": true,
                "reason": "launch_ready",
                "model": "/Users/private/models/Qwen3.8-27B-MLX-Serve-4bit",
            }),
            true,
        );
        assert!(model["model"].is_null());

        let named = sanitize_mlx_lifecycle(
            &json!({"ok": true, "reason": "launch_ready", "model": SWAP_MODEL_NAME}),
            true,
        );
        assert_eq!(named["model"], SWAP_MODEL_NAME);

        let legacy_named = sanitize_mlx_lifecycle(
            &json!({"ok": true, "reason": "launch_ready", "model": LEGACY_RESIDENT_MODEL_NAME}),
            true,
        );
        assert_eq!(legacy_named["model"], LEGACY_RESIDENT_MODEL_NAME);

        let stop = sanitize_mlx_lifecycle(
            &json!({"ok": true, "reason": "stop_stopped", "pid": 4242}),
            false,
        );
        assert_eq!(stop["action"], "mlx-server-stop");
        assert_eq!(stop["reason"], "stop_stopped");
        assert!(stop.get("pid").is_none());
        assert!(stop.get("stage").is_none());

        let stop_unknown =
            sanitize_mlx_lifecycle(&json!({"ok": false, "reason": "launch_ready"}), false);
        assert_eq!(stop_unknown["reason"], "stop_no_owned_server");
    }

    #[test]
    fn model_swap_reason_allowlist_accepts_external_owner_code() {
        let external = sanitize_model_swap_action(&json!({
            "ok": false,
            "action": "model-swap",
            "model": SWAP_MODEL_ID,
            "stage": "aborted",
            "reason": "model_owner_unmanaged",
            "stages": ["drain"],
            "stored": false,
            "prepared": false,
        }));
        assert_eq!(external["ok"], false);
        assert_eq!(external["stage"], "aborted");
        assert_eq!(external["reason"], "model_owner_unmanaged");

        let unknown = sanitize_model_swap_action(&json!({
            "ok": false,
            "action": "model-swap",
            "model": SWAP_MODEL_ID,
            "stage": "aborted",
            "reason": "not-an-allowlisted-code",
        }));
        assert_eq!(unknown["reason"], "model_owner_unknown");
    }

    #[test]
    fn model_swap_cancel_uses_marker_without_hard_cancelling() {
        let temp = std::env::temp_dir().join(format!(
            "openkakao-model-swap-cancel-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&temp);
        let marker = temp.join("123e4567-e89b-42d3-a456-426614174000.json");
        let cooperative_flag = Arc::new(AtomicBool::new(false));
        let normal_flag = Arc::new(AtomicBool::new(false));
        let bridge = PythonBridge::new();
        {
            let mut map = bridge.cancellations.lock().unwrap();
            map.insert(
                "123e4567-e89b-42d3-a456-426614174000".to_string(),
                CancellationHandle {
                    flag: cooperative_flag.clone(),
                    cooperative_marker: Some(marker.clone()),
                    global_abort_flag: None,
                },
            );
            map.insert(
                "snapshot".to_string(),
                CancellationHandle {
                    flag: normal_flag.clone(),
                    cooperative_marker: None,
                    global_abort_flag: None,
                },
            );
        }

        assert!(!bridge.cancel("123e4567-e89b-42d3-a456-426614174000"));
        assert!(!cooperative_flag.load(Ordering::SeqCst));
        assert!(bridge.cancel_model_swap("123e4567-e89b-42d3-a456-426614174000"));
        assert_eq!(
            safe_small_json(&marker)
                .and_then(|value| value.get("cancelled").and_then(Value::as_bool)),
            Some(true)
        );

        assert!(bridge.cancel("snapshot"));
        assert!(normal_flag.load(Ordering::SeqCst));
        assert!(!bridge.cancel_model_swap("snapshot"));
        let _ = fs::remove_dir_all(&temp);
    }

    #[test]
    fn global_abort_graces_browser_but_keeps_existing_cancel_semantics() {
        let temp = std::env::temp_dir().join(format!(
            "openkakao-global-abort-semantics-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&temp);
        let mut bridge = PythonBridge::new();
        Arc::get_mut(&mut bridge.config).unwrap().state_root = temp.clone();
        let browser_hard = Arc::new(AtomicBool::new(false));
        let browser_global = Arc::new(AtomicBool::new(false));
        let snapshot_hard = Arc::new(AtomicBool::new(false));
        let model_hard = Arc::new(AtomicBool::new(false));
        let model_marker = temp.join("model-cancel.json");
        {
            let mut map = bridge.cancellations.lock().unwrap();
            map.insert(
                "browser".to_string(),
                CancellationHandle {
                    flag: browser_hard.clone(),
                    cooperative_marker: None,
                    global_abort_flag: Some(browser_global.clone()),
                },
            );
            map.insert(
                "snapshot".to_string(),
                CancellationHandle {
                    flag: snapshot_hard.clone(),
                    cooperative_marker: None,
                    global_abort_flag: None,
                },
            );
            map.insert(
                "model".to_string(),
                CancellationHandle {
                    flag: model_hard.clone(),
                    cooperative_marker: Some(model_marker.clone()),
                    global_abort_flag: None,
                },
            );
        }

        bridge.global_abort().unwrap();
        assert!(browser_global.load(Ordering::SeqCst));
        assert!(!browser_hard.load(Ordering::SeqCst));
        assert!(snapshot_hard.load(Ordering::SeqCst));
        assert!(!model_hard.load(Ordering::SeqCst));
        assert_eq!(safe_small_json(&model_marker).unwrap()["cancelled"], true);
        assert_eq!(
            safe_small_json(&temp.join(ABORT_STATE_NAME)).unwrap()["latched"],
            true
        );
        fs::remove_dir_all(temp).unwrap();
    }

    #[test]
    fn global_abort_state_failure_hard_cancels_browser() {
        let temp = std::env::temp_dir().join(format!(
            "openkakao-global-abort-fail-closed-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&temp);
        fs::create_dir_all(&temp).unwrap();
        std::os::unix::fs::symlink("missing-target", temp.join(ABORT_STATE_NAME)).unwrap();
        let mut bridge = PythonBridge::new();
        Arc::get_mut(&mut bridge.config).unwrap().state_root = temp.clone();
        let browser_hard = Arc::new(AtomicBool::new(false));
        let browser_global = Arc::new(AtomicBool::new(false));
        bridge.cancellations.lock().unwrap().insert(
            "browser".to_string(),
            CancellationHandle {
                flag: browser_hard.clone(),
                cooperative_marker: None,
                global_abort_flag: Some(browser_global.clone()),
            },
        );

        assert!(matches!(bridge.global_abort(), Err(BridgeError::StateIo)));
        assert!(browser_hard.load(Ordering::SeqCst));
        assert!(!browser_global.load(Ordering::SeqCst));
        fs::remove_dir_all(temp).unwrap();
    }

    #[test]
    fn cancellation_before_child_registration_is_preserved() {
        let temp =
            std::env::temp_dir().join(format!("openkakao-early-cancel-{}", std::process::id()));
        let mut bridge = PythonBridge::new();
        Arc::get_mut(&mut bridge.config).unwrap().state_root = temp.clone();
        let token = "123e4567-e89b-42d3-a456-426614174001";
        assert!(!bridge.cancel_model_swap("../invalid"));
        assert!(bridge.cancel_model_swap(token));
        let marker = temp
            .join(MODEL_SWAP_CANCEL_DIR)
            .join(format!("{token}.json"));
        assert_eq!(safe_small_json(&marker).unwrap()["cancelled"], true);
        assert!(bridge.cancel_model_swap(token));
        fs::remove_dir_all(temp).unwrap();
    }

    #[test]
    fn swap_timeout_allows_cooperative_recovery_result() {
        let temp =
            std::env::temp_dir().join(format!("openkakao-timeout-recovery-{}", std::process::id()));
        let marker = temp.join("cancel.json");
        let args = vec!["-c".to_string(),
            "import pathlib,sys,time; p=pathlib.Path(sys.argv[1]);\nwhile not p.exists(): time.sleep(.01)\nprint('{\"reason\":\"cancelled_rollback_failed\"}')".to_string(),
            marker.to_string_lossy().into_owned()];
        let result = run_process_with_recovery(
            "python3",
            &args,
            Duration::from_millis(30),
            1024,
            ProcessControl {
                stdin_payload: None,
                hard_cancel_flag: Arc::new(AtomicBool::new(false)),
                global_abort: None,
                cooperative_marker: Some(&marker),
                recovery_timeout: Duration::from_secs(2),
            },
        )
        .unwrap();
        assert_eq!(
            parse_json_output(&result).unwrap()["reason"],
            "cancelled_rollback_failed"
        );
        fs::remove_dir_all(temp).unwrap();
    }

    #[test]
    fn unresponsive_swap_has_a_bounded_recovery_deadline() {
        let temp = std::env::temp_dir().join(format!(
            "openkakao-recovery-deadline-{}",
            std::process::id()
        ));
        let marker = temp.join("cancel.json");
        let result = run_process_with_recovery(
            "python3",
            &["-c".into(), "import time; time.sleep(5)".into()],
            Duration::from_millis(20),
            1024,
            ProcessControl {
                stdin_payload: None,
                hard_cancel_flag: Arc::new(AtomicBool::new(false)),
                global_abort: None,
                cooperative_marker: Some(&marker),
                recovery_timeout: Duration::from_millis(30),
            },
        );
        assert!(matches!(result, Err(BridgeError::Timeout)));
        assert!(marker.exists());
        fs::remove_dir_all(temp).unwrap();
    }

    #[test]
    fn swap_success_cannot_carry_a_failure_reason() {
        for reason in [
            "cancelled_rollback_failed",
            "insufficient_free_memory_rollback_failed",
            "private-error",
        ] {
            let value = sanitize_model_swap_action(&json!({
                "ok": true, "action": "model-swap", "model": SWAP_MODEL_ID,
                "stage": "ready", "reason": reason, "stored": true, "prepared": true,
            }));
            assert_eq!(value["ok"], false);
        }
    }

    #[test]
    fn model_action_response_is_sanitized_and_fail_closed() {
        for model in [RESIDENT_MODEL_ID, SWAP_MODEL_ID] {
            let safe = sanitize_model_action(
                &json!({
                    "ok": true,
                    "action": "model-set",
                    "model": model,
                    "stored": true,
                    "prepared": true,
                    "needs_prepare": false,
                    "prompt": "private",
                    "body": "private",
                    "secret": "private",
                    "warnings": ["untrusted"],
                }),
                "model-set",
                model,
            );
            assert_eq!(safe["ok"], true);
            assert_eq!(safe["model"], model);
            assert!(safe.get("prompt").is_none());
            assert!(safe.get("body").is_none());
            assert!(safe.get("secret").is_none());
            assert!(safe.get("warnings").is_none());
        }

        let mismatched = sanitize_model_action(
            &json!({
                "ok": true,
                "action": "model-prepare",
                "model": "remote/arbitrary",
                "prepared": true,
            }),
            "model-prepare",
            SWAP_MODEL_ID,
        );
        assert_eq!(mismatched["ok"], false);

        let cancelled = sanitize_model_swap_action(&json!({
            "ok": false,
            "action": "model-swap",
            "model": SWAP_MODEL_ID,
            "stage": "aborted",
            "reason": "cancelled",
            "stages": ["drain", "private-stage"],
            "stored": false,
            "prepared": false,
            "secret": "drop-me"
        }));
        assert_eq!(cancelled["reason"], "cancelled");
        assert_eq!(cancelled["stages"], json!(["drain"]));
        assert!(cancelled.get("secret").is_none());

        let rollback_failed = sanitize_model_swap_action(&json!({
            "ok": false,
            "action": "model-swap",
            "model": SWAP_MODEL_ID,
            "stage": "failed",
            "reason": "probe_failed_rollback_failed",
            "stages": ["drain", "unload", "load", "probe", "rollback"],
            "stored": false,
            "prepared": false
        }));
        assert_eq!(rollback_failed["reason"], "probe_failed_rollback_failed");
    }

    #[test]
    fn snapshot_defends_empty_lists_and_missing_model_id() {
        let safe = sanitize_snapshot(&json!({
            "rooms": [],
            "available_chats": [],
            "open_jobs": 0,
            "sent": 0,
            "skipped": 0,
            "delivery_unknown": 1
        }));
        assert!(safe.available);
        assert!(safe.rooms.is_empty());
        assert!(safe.available_chats.is_empty());
        assert!(safe.jobs.is_empty());
        assert!(safe.recent_receipts.is_empty());
        assert!(safe.reply_model_id.is_none());
        assert_eq!(safe.terminal_counts.delivery_unknown, 1);
        assert_eq!(safe.context_sync.mode, "async");
        assert!(!safe.context_sync.waited);
    }

    #[test]
    fn snapshot_preserves_only_known_local_model_ids_including_legacy_resident() {
        for model in [RESIDENT_MODEL_ID, LEGACY_RESIDENT_MODEL_ID, SWAP_MODEL_ID] {
            let bare = sanitize_snapshot(&json!({"reply_model": {"id": model}}));
            assert_eq!(bare.reply_model_id.as_deref(), Some(model));

            let prefixed = format!("mlx/{model}");
            let with_prefix = sanitize_snapshot(&json!({"reply_model": {"id": prefixed}}));
            assert_eq!(
                with_prefix.reply_model_id.as_deref(),
                Some(prefixed.as_str())
            );
        }

        for model in [
            "remote/arbitrary",
            "ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit/extra",
        ] {
            let safe = sanitize_snapshot(&json!({"reply_model": {"id": model}}));
            assert!(safe.reply_model_id.is_none());
        }
    }

    #[test]
    fn snapshot_parses_and_bounds_available_chats() {
        let safe = sanitize_snapshot(&json!({
            "available_chats": [
                {"chat_id": 417780809780519_i64, "title": "부자멘토멘티", "catalog": true, "live": true},
                {"chat_id": 999_i64, "title": "새로운 방", "catalog": false, "live": false},
                {"chat_id": -1, "title": "invalid"},
            ]
        }));
        assert_eq!(safe.available_chats.len(), 2);
        assert_eq!(safe.available_chats[0].chat_id, 417780809780519);
        assert_eq!(safe.available_chats[0].title, "부자멘토멘티");
        assert!(safe.available_chats[0].catalog);
        assert!(safe.available_chats[0].live);
        assert_eq!(safe.available_chats[1].chat_id, 999);
        assert_eq!(safe.available_chats[1].title, "새로운 방");
        assert!(!safe.available_chats[1].catalog);
    }

    #[test]
    fn snapshot_room_reply_readiness_accepts_aliases_and_fails_closed() {
        fn sanitized_readiness(room: Value) -> String {
            let safe = sanitize_snapshot(&json!({"rooms": [room]}));
            assert_eq!(safe.rooms.len(), 1);
            safe.rooms[0].reply_readiness.clone()
        }

        let cases = [
            (json!({"chat_id": 1, "reply_readiness": "ready"}), "ready"),
            (
                json!({"chat_id": 1, "replyReadiness": "blocked"}),
                "blocked",
            ),
            (
                json!({
                    "chat_id": 1,
                    "reply_readiness": "unknown",
                    "replyReadiness": "unknown"
                }),
                "unknown",
            ),
            (
                json!({
                    "chat_id": 1,
                    "reply_readiness": "ready",
                    "replyReadiness": "blocked"
                }),
                "unknown",
            ),
            (
                json!({
                    "chat_id": 1,
                    "reply_readiness": "ready",
                    "replyReadiness": 7
                }),
                "unknown",
            ),
            (json!({"chat_id": 1}), "unknown"),
            (json!({"chat_id": 1, "reply_readiness": "READY"}), "unknown"),
            (json!({"chat_id": 1, "replyReadiness": true}), "unknown"),
            (json!({"chat_id": 1, "replyReadiness": "paused"}), "unknown"),
        ];

        for (room, expected) in cases {
            assert_eq!(sanitized_readiness(room), expected);
        }
    }

    #[test]
    fn snapshot_room_serializes_only_the_safe_readiness_enum() {
        let safe = sanitize_snapshot(&json!({
            "rooms": [{
                "chat_id": 42,
                "selector": "id:42",
                "live": true,
                "auto_reply": true,
                "open_jobs": 3,
                "reply_readiness": "ready",
                "replyReadiness": "ready",
                "raw_state": {"delivery_enabled": true},
                "private_message": "must-not-escape",
                "error": "private-error"
            }]
        }));
        let serialized = serde_json::to_value(&safe.rooms[0]).unwrap();
        let room = serialized.as_object().unwrap();
        assert_eq!(
            room.keys().map(String::as_str).collect::<HashSet<_>>(),
            HashSet::from([
                "chat_id",
                "title",
                "live",
                "auto_reply",
                "open_jobs",
                "replyReadiness",
            ])
        );
        assert_eq!(room["replyReadiness"], "ready");
        let text = serde_json::to_string(room).unwrap();
        for forbidden in [
            "reply_readiness",
            "raw_state",
            "delivery_enabled",
            "private_message",
            "must-not-escape",
            "private-error",
        ] {
            assert!(!text.contains(forbidden));
        }
    }

    #[test]
    fn room_upsert_action_args_and_sanitization() {
        let args = settings_action_args(
            "room-upsert",
            Some("테스트 방"),
            None,
            Some("123456"),
            None,
            None,
            None,
        )
        .expect("valid room-upsert");
        assert_eq!(args[0], "--action");
        assert_eq!(args[1], "room-upsert");
        assert_eq!(args[2], "--catalog-upsert");
        let parsed: Value = serde_json::from_str(&args[3]).unwrap();
        assert_eq!(parsed["chat_id"], 123456);
        assert_eq!(parsed["title"], "테스트 방");
        assert_eq!(parsed["auto_reply"], true);
        assert_eq!(parsed["geeknews"], false);

        let sanitized = sanitize_room_upsert(&json!({"ok": true, "action": "room-upsert"}));
        assert_eq!(sanitized["ok"], true);
    }

    #[test]
    fn snapshot_background_allowlists_clamps_rounds_and_truncates() {
        let safe = sanitize_snapshot(&json!({
            "open_jobs": 2,
            "background": {
                "activity": 0.1236,
                "caption": "가".repeat(140),
                "geeknews": {"state": "sending", "activity": 2.0, "caption": "긱".repeat(130)},
                "db_sync": {"state": "behind", "activity": -0.4, "caption": "DB 정상"}
            }
        }));
        assert_eq!(safe.job_load, 0.5);
        assert_eq!(safe.background.activity, 0.124);
        assert_eq!(safe.background.reply_load, 0.5);
        assert_eq!(safe.background.caption.chars().count(), 120);
        assert_eq!(safe.background.geeknews.state, "sending");
        assert_eq!(safe.background.geeknews.activity, 1.0);
        assert_eq!(safe.background.geeknews.caption.chars().count(), 120);
        assert_eq!(safe.background.db_sync.state, "behind");
        assert_eq!(safe.background.db_sync.activity, 0.0);
    }

    #[test]
    fn snapshot_background_unknown_states_fail_closed() {
        let safe = sanitize_snapshot(&json!({
            "background": {
                "activity": 0.2,
                "geeknews": {"state": "unexpected", "activity": 0.4},
                "db_sync": {"state": "unexpected", "activity": 0.6}
            }
        }));
        assert_eq!(safe.background.geeknews.state, "unknown");
        assert_eq!(safe.background.db_sync.state, "unknown");
    }

    #[test]
    fn snapshot_background_missing_uses_job_load_safe_default() {
        let safe = sanitize_snapshot(&json!({"open_jobs": 3}));
        assert_eq!(safe.job_load, 0.75);
        assert_eq!(safe.background.activity, 0.75);
        assert_eq!(safe.background.reply_load, 0.75);
        assert_eq!(safe.background.geeknews, SafeBackgroundSource::unknown());
        assert_eq!(safe.background.db_sync, SafeBackgroundSource::unknown());
    }

    #[test]
    fn snapshot_background_omits_unapproved_source_fields() {
        let safe = sanitize_snapshot(&json!({
            "background": {
                "activity": 0.8,
                "caption": "safe",
                "rooms": [{"chat": "body-value"}],
                "geeknews": {
                    "state": "confirmed", "activity": 0.4, "caption": "done",
                    "age_seconds": 7, "posted_slots": ["morning"]
                },
                "db_sync": {
                    "state": "ready", "activity": 0.3, "caption": "ready",
                    "age_seconds": 9, "capability_state": "cap-value", "fence_reason": "fence-value"
                }
            }
        }));
        let serialized = serde_json::to_value(&safe).unwrap();
        let background = serialized["background"].as_object().unwrap();
        assert_eq!(
            background
                .keys()
                .map(String::as_str)
                .collect::<HashSet<_>>(),
            HashSet::from(["activity", "caption", "replyLoad", "geeknews", "dbSync"])
        );
        let text = serde_json::to_string(background).unwrap();
        for forbidden in [
            "rooms",
            "age_seconds",
            "posted_slots",
            "capability_state",
            "fence_reason",
            "body-value",
            "cap-value",
            "fence-value",
        ] {
            assert!(!text.contains(forbidden));
        }
    }

    #[test]
    fn snapshot_ondevice_bounds_and_truncates_safe_fields() {
        let safe = sanitize_snapshot(&json!({
            "ondevice_hardware": {
                "hardware": {
                    "chip": "M".repeat(80),
                    "cores": 5000,
                    "memory_bytes": 999999999999_u64,
                    "memory_gb": 127.96,
                    "is_apple_silicon": true
                },
                "recommendation": {
                    "primary_engine": "e".repeat(40),
                    "recommended_model": "m".repeat(150),
                    "recommended_quant": "q".repeat(40)
                },
                "verification": {"ok": true},
                "status_label": "상".repeat(260),
                "status_detail": "세".repeat(260)
            }
        }));
        assert!(safe.on_device.available);
        assert_eq!(safe.on_device.chip.chars().count(), 64);
        assert_eq!(safe.on_device.cores, 1024);
        assert_eq!(safe.on_device.memory_gb, 128.0);
        assert!(safe.on_device.apple_silicon);
        assert_eq!(safe.on_device.engine.chars().count(), 32);
        assert_eq!(safe.on_device.recommended_model.chars().count(), 128);
        assert_eq!(safe.on_device.quant.chars().count(), 32);
        assert!(safe.on_device.verified);
        assert_eq!(safe.on_device.status_label.chars().count(), 240);
        assert_eq!(safe.on_device.status_detail.chars().count(), 240);

        let clamped = sanitize_snapshot(&json!({
            "ondevice_hardware": {
                "hardware": {"memory_gb": 9999.0},
                "recommendation": {},
                "verification": {}
            }
        }));
        assert_eq!(clamped.on_device.memory_gb, 4096.0);
    }

    #[test]
    fn snapshot_ondevice_missing_or_corrupt_fails_closed() {
        let missing = sanitize_snapshot(&json!({}));
        assert_eq!(missing.on_device, SafeOnDevice::default());
        let corrupt = sanitize_snapshot(&json!({"ondevice_hardware": "invalid"}));
        assert_eq!(corrupt.on_device, SafeOnDevice::default());
        let empty = sanitize_snapshot(&json!({"ondevice_hardware": {}}));
        assert_eq!(empty.on_device, SafeOnDevice::default());
        let partial = sanitize_snapshot(&json!({
            "ondevice_hardware": {"hardware": {}, "recommendation": {}}
        }));
        assert_eq!(partial.on_device, SafeOnDevice::default());
    }

    #[test]
    fn snapshot_ondevice_serializes_only_allowlisted_fields() {
        let safe = sanitize_snapshot(&json!({
            "ondevice_hardware": {
                "hardware": {
                    "chip": "Apple M5 Max",
                    "cores": 16,
                    "memory_bytes": 137438953472_u64,
                    "memory_gb": 128.0,
                    "is_apple_silicon": true
                },
                "recommendation": {
                    "primary_engine": "mlx-serve",
                    "recommended_model": "safe-model",
                    "recommended_quant": "4bit",
                    "reason": "FORBIDDEN_REASON_VALUE",
                    "engine_paths": {"secret": "FORBIDDEN_ENGINE_PATH"},
                    "fallback_models": ["FORBIDDEN_FALLBACK"],
                    "worker_model_id": "FORBIDDEN_WORKER_MODEL"
                },
                "verification": {"ok": true},
                "last_probe": {"model": "FORBIDDEN_LAST_PROBE"},
                "status_label": "safe label",
                "status_detail": "safe detail"
            }
        }));
        let serialized = serde_json::to_value(&safe).unwrap();
        let on_device = serialized["onDevice"].as_object().unwrap();
        assert_eq!(
            on_device.keys().map(String::as_str).collect::<HashSet<_>>(),
            HashSet::from([
                "available",
                "chip",
                "cores",
                "memoryGb",
                "appleSilicon",
                "engine",
                "recommendedModel",
                "quant",
                "verified",
                "statusLabel",
                "statusDetail",
            ])
        );
        let text = serde_json::to_string(on_device).unwrap();
        for forbidden in [
            "memory_bytes",
            "reason",
            "engine_paths",
            "fallback_models",
            "worker_model_id",
            "last_probe",
            "FORBIDDEN_REASON_VALUE",
            "FORBIDDEN_ENGINE_PATH",
            "FORBIDDEN_FALLBACK",
            "FORBIDDEN_WORKER_MODEL",
            "FORBIDDEN_LAST_PROBE",
        ] {
            assert!(!text.contains(forbidden));
        }
    }

    #[test]
    fn snapshot_pipeline_selects_idle_fallback_and_indexed_stages() {
        let idle = sanitize_snapshot(&json!({
            "pipeline": {
                "active_index": null,
                "event_id": "none",
                "outcome": "none",
                "stages": [
                    {"id": "detect", "state": "idle"},
                    {"id": "authorize", "state": "idle"},
                    {"id": "queue", "state": "idle"},
                    {"id": "context", "state": "idle"},
                    {"id": "model", "state": "idle"},
                    {"id": "delay", "state": "idle"},
                    {"id": "send", "state": "idle"},
                    {"id": "confirm", "state": "idle"}
                ]
            }
        }));
        assert!(!idle.pipeline.active);
        assert_eq!(idle.pipeline.stage, "none");
        assert_eq!(idle.pipeline.stage_index, 0);
        assert_eq!(idle.pipeline.stage_total, 8);
        assert_eq!(idle.pipeline.outcome, "none");

        let fallback = sanitize_snapshot(&json!({
            "pipeline": {
                "active_index": null,
                "outcome": "scheduled",
                "stages": [
                    {"id": "detect", "state": "done"},
                    {"id": "delay", "state": "active"}
                ]
            }
        }));
        assert!(fallback.pipeline.active);
        assert_eq!(fallback.pipeline.stage, "delay");
        assert_eq!(fallback.pipeline.stage_index, 1);
        assert_eq!(fallback.pipeline.stage_total, 2);
        assert_eq!(fallback.pipeline.outcome, "scheduled");

        let indexed = sanitize_snapshot(&json!({
            "pipeline": {
                "active_index": 4,
                "outcome": "deferred",
                "stages": [
                    {"id": "detect", "state": "done"},
                    {"id": "authorize", "state": "done"},
                    {"id": "queue", "state": "done"},
                    {"id": "context", "state": "done"},
                    {"id": "model", "state": "idle"}
                ]
            }
        }));
        assert!(indexed.pipeline.active);
        assert_eq!(indexed.pipeline.stage, "model");
        assert_eq!(indexed.pipeline.stage_index, 4);
        assert_eq!(indexed.pipeline.stage_total, 5);
        assert_eq!(indexed.pipeline.outcome, "deferred");
    }

    #[test]
    fn snapshot_pipeline_unknown_and_missing_values_fail_closed() {
        let unknown = sanitize_snapshot(&json!({
            "pipeline": {
                "active_index": 0,
                "outcome": "private-outcome",
                "stages": [{"id": "private-stage", "state": "active"}]
            }
        }));
        assert!(unknown.pipeline.active);
        assert_eq!(unknown.pipeline.stage, "unknown");
        assert_eq!(unknown.pipeline.outcome, "unknown");

        assert_eq!(
            sanitize_snapshot(&json!({})).pipeline,
            SafePipeline::default()
        );
        assert_eq!(
            sanitize_snapshot(&json!({"pipeline": "invalid"})).pipeline,
            SafePipeline::default()
        );
        assert_eq!(
            sanitize_snapshot(&json!({"pipeline": {"outcome": "sent"}})).pipeline,
            SafePipeline::default()
        );
    }

    #[test]
    fn snapshot_pipeline_serializes_only_allowlisted_summary() {
        let safe = sanitize_snapshot(&json!({
            "pipeline": {
                "active_index": 1,
                "event_id": "FORBIDDEN_EVENT_ID",
                "outcome": "sent",
                "stages": [
                    {"id": "detect", "state": "blocked"},
                    {"id": "send", "state": "active"}
                ]
            }
        }));
        let serialized = serde_json::to_value(&safe).unwrap();
        let pipeline = serialized["pipeline"].as_object().unwrap();
        assert_eq!(
            pipeline.keys().map(String::as_str).collect::<HashSet<_>>(),
            HashSet::from(["active", "stage", "stageIndex", "stageTotal", "outcome"])
        );
        let text = serde_json::to_string(pipeline).unwrap();
        for forbidden in ["event_id", "FORBIDDEN_EVENT_ID", "stages", "blocked"] {
            assert!(!text.contains(forbidden));
        }
        assert!(!text.contains(":\"active\""));
    }

    #[test]
    fn snapshot_receipts_drop_unknown_outcome_and_preserve_slug_reasons() {
        let safe = sanitize_snapshot(&json!({
            "available_chats": [{"chat_id": 7, "title": "방"}],
            "reply_receipts": {"rooms": [{
                "chat_id": "7",
                "receipts": [
                    receipt("bad-outcome", "2026-09-21T07:03:00Z", "unknown", "self_author"),
                    receipt("already", "2026-09-21T07:02:00Z", "skipped", "already_commented"),
                    receipt("low", "2026-09-21T07:01:00Z", "skipped", "low_information"),
                    receipt("uncertain", "2026-09-21T07:00:00Z", "skipped", "uncertain")
                ]
            }]}
        }));
        assert_eq!(safe.recent_receipts.len(), 3);
        assert_eq!(safe.recent_receipts[0].chat_id, 7);
        assert_eq!(safe.recent_receipts[0].reason_code, "already_commented");
        assert_eq!(safe.recent_receipts[1].reason_code, "low_information");
        assert_eq!(safe.recent_receipts[2].reason_code, "uncertain");
    }

    #[test]
    fn snapshot_receipts_normalize_freeform_reason_codes_without_leaking_them() {
        let korean = "수신된 이미지는 MoruLiveExam 프로젝트의 터미널 작업 화면 캡처이며, 구체적인 질문이나 답변을 요구하는 요청 사항이 포함되어 있지 않아";
        let spaced = "direct_question about prior conversation topic";
        let too_long = format!("a{}", "b".repeat(64));
        let safe = sanitize_snapshot(&json!({
            "reply_receipts": {"rooms": [{
                "chat_id": 11,
                "receipts": [
                    receipt("korean", "2026-09-21T07:04:00Z", "skipped", korean),
                    receipt("spaced", "2026-09-21T07:03:00Z", "skipped", spaced),
                    receipt("long", "2026-09-21T07:02:00Z", "skipped", &too_long),
                    receipt("non-ascii", "2026-09-21T07:01:00Z", "skipped", "social_reply_한글")
                ]
            }]}
        }));

        assert_eq!(safe.recent_receipts.len(), 4);
        assert!(safe
            .recent_receipts
            .iter()
            .all(|item| item.reason_code == "unspecified"));
        let serialized = serde_json::to_string(&safe).unwrap();
        for forbidden in [
            korean,
            spaced,
            too_long.as_str(),
            "social_reply_한글",
            "수신된 이미지는",
        ] {
            assert!(!serialized.contains(forbidden));
        }
    }

    #[test]
    fn snapshot_receipts_sort_dedupe_limit_and_truncate() {
        let mut receipts = Vec::new();
        for index in 0..14 {
            let mut item = receipt(
                &format!("evt-{index}"),
                &format!("2026-09-21T07:{index:02}:00Z"),
                "sent",
                "direct_question",
            );
            item["display_time"] = json!("x".repeat(40));
            item["clock"] = json!("y".repeat(12));
            item["outcome_text"] = json!("결".repeat(40));
            item["reason_text"] = json!("가".repeat(80));
            receipts.push(item);
        }
        receipts.push(receipt(
            "evt-13",
            "2026-09-21T06:59:00Z",
            "sent",
            "direct_question",
        ));
        let safe = sanitize_snapshot(&json!({
            "available_chats": [{"chat_id": 9, "title": "방".repeat(130)}],
            "reply_receipts": {"rooms": [{"chat_id": 9, "receipts": receipts}]}
        }));

        assert_eq!(safe.recent_receipts.len(), 12);
        assert_eq!(safe.recent_receipts[0].display_time.chars().count(), 32);
        assert_eq!(safe.recent_receipts[0].clock.chars().count(), 8);
        assert_eq!(safe.recent_receipts[0].outcome_text.chars().count(), 32);
        assert_eq!(safe.recent_receipts[0].reason_text.chars().count(), 64);
        assert_eq!(safe.recent_receipts[0].title.chars().count(), 120);
        assert_eq!(safe.recent_receipts[0].chat_id, 9);
        let ids = safe
            .recent_receipts
            .iter()
            .map(|item| (&item.display_time, item.chat_id))
            .collect::<Vec<_>>();
        assert_eq!(ids.len(), 12);
    }

    #[test]
    fn snapshot_receipts_sort_and_dedupe_keep_newest_event() {
        let mut newer = receipt("same", "2026-09-21T07:09:00Z", "sent", "direct_question");
        newer["reason_text"] = json!("newest");
        let mut older = receipt("same", "2026-09-21T07:01:00Z", "sent", "direct_question");
        older["reason_text"] = json!("older");
        let safe = sanitize_snapshot(&json!({
            "reply_receipts": {"rooms": [{
                "chat_id": "22",
                "receipts": [older, receipt("other", "2026-09-21T07:05:00Z", "skipped", "stale_backlog"), newer]
            }]}
        }));
        assert_eq!(safe.recent_receipts.len(), 2);
        assert_eq!(safe.recent_receipts[0].reason_text, "newest");
        assert_eq!(safe.recent_receipts[0].title, "id:22");
        assert_eq!(safe.recent_receipts[1].reason_code, "stale_backlog");
    }

    #[test]
    fn snapshot_receipts_serialize_only_allowlisted_fields() {
        let safe = sanitize_snapshot(&json!({
            "reply_receipts": {"rooms": [{"chat_id": "31", "receipts": [
                receipt("evt", "2026-09-21T07:00:00Z", "sent", "direct_question")
            ]}]}
        }));
        let serialized = serde_json::to_value(&safe).unwrap();
        let item = serialized["recent_receipts"][0].as_object().unwrap();
        let keys = item.keys().map(String::as_str).collect::<HashSet<_>>();
        assert_eq!(
            keys,
            HashSet::from([
                "chatId",
                "title",
                "displayTime",
                "clock",
                "outcome",
                "outcomeText",
                "reasonCode",
                "reasonText",
                "retrievalState",
            ])
        );
        let text = serde_json::to_string(item).unwrap();
        for forbidden in [
            "drop body",
            "drop prompt",
            "drop token",
            "drop preview",
            "drop preview text",
            "drop summary",
            "drop detail",
            "drop chat",
            "drop model",
            "drop evidence",
            "drop ledger",
        ] {
            assert!(!text.contains(forbidden));
        }
    }

    #[test]
    fn timeout_kills_owned_child() {
        let args = vec!["-c".to_string(), "import time; time.sleep(1)".to_string()];
        let result = run_process(
            "python3",
            &args,
            Duration::from_millis(30),
            1024,
            Arc::new(AtomicBool::new(false)),
        );
        assert!(matches!(result, Err(BridgeError::Timeout)));
    }

    #[test]
    fn browser_task_uses_stdin_and_never_argv() {
        let task = "private browser task 가";
        let browser_args = browser_tool_args("browser-stdin", task).unwrap();
        assert!(!browser_args.iter().any(|value| value.contains(task)));
        let result = run_process_with_recovery(
            "python3",
            &[
                "-c".to_string(),
                "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())".to_string(),
            ],
            Duration::from_secs(2),
            1024,
            ProcessControl {
                stdin_payload: Some(task.as_bytes()),
                hard_cancel_flag: Arc::new(AtomicBool::new(false)),
                global_abort: None,
                cooperative_marker: None,
                recovery_timeout: Duration::ZERO,
            },
        )
        .unwrap();
        assert_eq!(result, task.as_bytes());
    }

    #[test]
    fn global_abort_preserves_graceful_child_envelope() {
        let global_abort = Arc::new(AtomicBool::new(false));
        let trigger = global_abort.clone();
        let signal = thread::spawn(move || {
            thread::sleep(Duration::from_millis(20));
            trigger.store(true, Ordering::SeqCst);
        });
        let result = run_process_with_recovery(
            "python3",
            &[
                "-c".to_string(),
                "import time; time.sleep(.06); print('{\"ok\":false,\"status\":\"aborted\",\"errorCode\":\"global_abort\",\"result\":\"\"}')".to_string(),
            ],
            Duration::from_secs(2),
            1024,
            ProcessControl {
                stdin_payload: None,
                hard_cancel_flag: Arc::new(AtomicBool::new(false)),
                global_abort: Some((global_abort, Duration::from_millis(500))),
                cooperative_marker: None,
                recovery_timeout: Duration::ZERO,
            },
        )
        .unwrap();
        signal.join().unwrap();
        let value = parse_json_output(&result).unwrap();
        assert_eq!(value["status"], "aborted");
        assert_eq!(value["errorCode"], "global_abort");
    }

    #[test]
    fn global_abort_grace_has_a_hard_deadline() {
        let started = Instant::now();
        let result = run_process_with_recovery(
            "python3",
            &["-c".to_string(), "import time; time.sleep(5)".to_string()],
            Duration::from_secs(2),
            1024,
            ProcessControl {
                stdin_payload: None,
                hard_cancel_flag: Arc::new(AtomicBool::new(false)),
                global_abort: Some((Arc::new(AtomicBool::new(true)), Duration::from_millis(40))),
                cooperative_marker: None,
                recovery_timeout: Duration::ZERO,
            },
        );
        assert!(matches!(result, Err(BridgeError::Cancelled)));
        assert!(started.elapsed() < Duration::from_secs(1));
    }

    #[test]
    fn cancellation_kills_owned_child() {
        let started = Instant::now();
        let result = run_process(
            "python3",
            &["-c".to_string(), "import time; time.sleep(1)".to_string()],
            Duration::from_secs(2),
            1024,
            Arc::new(AtomicBool::new(true)),
        );
        assert!(matches!(result, Err(BridgeError::Cancelled)));
        assert!(started.elapsed() < Duration::from_millis(500));
    }

    #[test]
    fn oversized_stdout_is_rejected_after_drain() {
        let args = vec!["-c".to_string(), "print('x' * 8192)".to_string()];
        let result = run_process(
            "python3",
            &args,
            Duration::from_secs(2),
            128,
            Arc::new(AtomicBool::new(false)),
        );
        assert!(matches!(result, Err(BridgeError::OutputTooLarge)));
    }

    #[test]
    fn missing_voice_status_never_selects_an_unverified_wake_model() {
        let temp = std::env::temp_dir().canonicalize().unwrap().join(format!(
            "openkakao-voice-status-valid-bundle-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&temp);
        let state_root = temp.join("state");
        let repo_root = temp.join("repo");
        let bundled_model = repo_root.join("voice/models/alden_ko_ridge.onnx");
        fs::create_dir_all(bundled_model.parent().unwrap()).unwrap();
        fs::write(&bundled_model, b"onnx").unwrap();

        let status = read_voice_status(&state_root);
        assert!(!status.available);
        assert_eq!(status.wake_phrase, WAKE_PHRASE);
        assert_eq!(status.threshold, WAKE_THRESHOLD);
        assert!(!status.custom_model_selected);

        let _ = fs::remove_dir_all(&temp);
    }

    #[test]
    fn missing_or_invalid_legacy_wake_model_stays_disabled() {
        let temp = std::env::temp_dir().canonicalize().unwrap().join(format!(
            "openkakao-voice-status-invalid-bundle-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&temp);
        let state_root = temp.join("state");
        let repo_root = temp.join("repo");

        let missing = read_voice_status(&state_root);
        assert!(!missing.custom_model_selected);

        let bundled_model = repo_root.join("voice/models/alden_ko_ridge.onnx");
        fs::create_dir_all(bundled_model.parent().unwrap()).unwrap();
        fs::write(&bundled_model, b"").unwrap();
        let invalid = read_voice_status(&state_root);
        assert!(!invalid.custom_model_selected);

        let _ = fs::remove_dir_all(&temp);
    }

    #[test]
    fn voice_status_is_bounded_and_clamped() {
        let temp = std::env::temp_dir()
            .canonicalize()
            .unwrap()
            .join(format!("openkakao-voice-status-{}", std::process::id()));
        let _ = fs::remove_dir_all(&temp);
        fs::create_dir_all(&temp).unwrap();
        fs::write(
            temp.join(VOICE_STATUS_NAME),
            json!({
                "schema_version": 1,
                "state": "speaking",
                "rms": 2.0,
                "error_code": "",
                "wake_source": "stock",
                "custom_model_selected": true,
                "updated_at": epoch_seconds() as u64
            })
            .to_string(),
        )
        .unwrap();
        let status = read_voice_status(&temp);
        assert!(status.available);
        assert_eq!(status.state, "speaking");
        assert_eq!(status.rms, 1.0);
        assert_eq!(status.wake_source, "stock");
        assert!(status.updated_at > 0);
        assert_eq!(status.wake_phrase, WAKE_PHRASE);
        assert_eq!(status.threshold, WAKE_THRESHOLD);
        assert!(!status.custom_model_selected);
        let _ = fs::remove_dir_all(&temp);
    }

    #[test]
    fn stale_voice_status_is_not_reported_as_a_live_listener() {
        let temp = std::env::temp_dir().canonicalize().unwrap().join(format!(
            "openkakao-stale-voice-status-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&temp);
        fs::create_dir_all(&temp).unwrap();
        fs::write(
            temp.join(VOICE_STATUS_NAME),
            json!({
                "schema_version": 1,
                "state": "wake_listen",
                "rms": 0.0,
                "error_code": "",
                "wake_source": "none",
                "updated_at": (epoch_seconds() as u64).saturating_sub(VOICE_STATUS_MAX_AGE_SECS + 1)
            })
            .to_string(),
        )
        .unwrap();

        let status = read_voice_status(&temp);
        assert!(!status.available);
        assert_eq!(status.state, "unavailable");
        assert_eq!(status.updated_at, 0);

        let _ = fs::remove_dir_all(&temp);
    }

    #[test]
    fn clicked_node_details_preserve_quotes_and_reject_foreign_evidence() {
        let account = "1".repeat(64);
        let node = format!("person:kakao:{account}:actor:7");
        let valid = json!({"source_id":format!("kakao:{account}:room:42:log:1"),"source_kind":"local_db_snapshot",
            "source_role":"outgoing_unclassified","author_id":"7","sender":"same name","room_title":"meeting",
            "date":"2026-10-01T15:00:00+09:00","content":"Friday at 3","private_path":"must drop"});
        let mut wrong_room = valid.clone();
        wrong_room["source_id"] = json!(format!("kakao:{account}:room:84:log:2"));
        let mut wrong_actor = valid.clone();
        wrong_actor["author_id"] = json!("8");
        let mut wrong_account = valid.clone();
        wrong_account["source_id"] = json!(format!("kakao:{}:room:42:log:3", "2".repeat(64)));
        let safe = sanitize_knowledge_focus(
            &json!({"ok":true,"details":{"node_id":node,"scope_room_id":"42",
            "summary":"a recorded participant","token":"drop"},"sources":[valid.clone(), valid, wrong_room, wrong_actor, wrong_account]}),
            &node,
        );
        assert_eq!(safe["sources"].as_array().unwrap().len(), 1);
        assert_eq!(safe["sources"][0]["source_role"], "outgoing_unclassified");
        assert_eq!(safe["sources"][0]["content"], "Friday at 3");
        assert!(safe["sources"][0].get("private_path").is_none());
        assert!(safe["details"].get("token").is_none());
        let rejected = sanitize_knowledge_focus(&safe, "another-node");
        assert!(rejected["details"].is_null());
        assert!(rejected["sources"].as_array().unwrap().is_empty());
    }

    #[test]
    fn knowledge_graph_keeps_ere_metadata_and_rejects_message_nodes() {
        let safe = sanitize_knowledge_graph(&json!({
            "ok": true,
            "nodes": [
                {"id":"ent:a","label":"A","importance":80,"evidence":{"kind":"ledger","chat_id":"room-1","source_event_ids":["db:1"]}},
                {"id":"ent:b","label":"B","importance":40,"evidence":{"kind":"seed"}},
                {"id":"message:raw:1","label":"raw message","importance":100}
            ],
            "edges": [
                {"source":"ent:a","relation":"USES","target":"ent:b","weight":9,"room_id":"room-1","valid_from":"2026-09-20T10:00:00+09:00","valid_to":"","evidence_message_id":"db:1","evidence":{"kind":"ledger","chat_id":"room-1","source_event_ids":["db:1"]}},
                {"source":"message:raw:1","relation":"MENTIONS","target":"ent:a","weight":99}
            ]
        }));
        assert_eq!(safe["nodes"].as_array().unwrap().len(), 2);
        assert_eq!(safe["edges"].as_array().unwrap().len(), 1);
        assert_eq!(safe["edges"][0]["source"], "ent:a");
        assert_eq!(safe["edges"][0]["relation"], "USES");
        assert_eq!(safe["edges"][0]["target"], "ent:b");
        assert_eq!(safe["edges"][0]["room_id"], "room-1");
        assert_eq!(safe["edges"][0]["evidence_message_id"], "db:1");
    }

    #[test]
    fn knowledge_status_sanitizes_dense_metadata() {
        let dense_status = format!("prefix\n\t\u{0007}{}tail", "x".repeat(450));
        let safe = sanitize_knowledge_status(&json!({
            "dense_status": dense_status,
            "dense_indexed_at": 12345
        }));
        let status = safe["dense_status"].as_str().unwrap();
        assert_eq!(status.chars().count(), 400);
        assert!(status.chars().all(|character| !character.is_control()));
        assert_eq!(safe["dense_indexed_at"], 12345);

        let missing = sanitize_knowledge_status(&json!({}));
        assert_eq!(missing["dense_status"], "unknown");
        assert_eq!(missing["dense_indexed_at"], 0);

        for invalid in [json!(-1), json!(1.5), json!("12345")] {
            let invalid = sanitize_knowledge_status(&json!({ "dense_indexed_at": invalid }));
            assert_eq!(invalid["dense_indexed_at"], 0);
        }
    }

    #[test]
    fn global_abort_is_latched_and_increments_epoch() {
        let temp = std::env::temp_dir()
            .canonicalize()
            .unwrap()
            .join(format!("openkakao-abort-state-{}", std::process::id()));
        let _ = fs::remove_dir_all(&temp);
        write_global_abort(&temp).unwrap();
        write_global_abort(&temp).unwrap();
        let value = safe_small_json(&temp.join(ABORT_STATE_NAME)).unwrap();
        assert_eq!(value.get("epoch").and_then(Value::as_u64), Some(2));
        assert_eq!(value.get("latched").and_then(Value::as_bool), Some(true));
        assert_eq!(
            value.get("reason").and_then(Value::as_str),
            Some("global_abort")
        );
        let metadata = fs::metadata(temp.join(ABORT_STATE_NAME)).unwrap();
        assert_eq!(metadata.permissions().mode() & 0o777, 0o600);
        let _ = fs::remove_dir_all(&temp);
    }

    fn bridge_with_state_root(state_root: PathBuf) -> PythonBridge {
        let mut config = BridgeConfig::discover();
        config.state_root = state_root;
        PythonBridge {
            config: Arc::new(config),
            cancellations: Arc::new(Mutex::new(HashMap::new())),
            jobs: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    #[test]
    fn knowledge_revision_tracks_atomic_replacements_and_rejects_links() {
        let root = std::env::temp_dir()
            .canonicalize()
            .unwrap()
            .join(format!("alden-revision-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        let bridge = bridge_with_state_root(root.clone());
        assert_eq!(bridge.knowledge_revision(), None);
        let home = root.join("knowledge/osk");
        fs::create_dir_all(&home).unwrap();
        let checkpoint = home.join("sync.json");
        fs::write(&checkpoint, b"{}").unwrap();
        let first = bridge.knowledge_revision().unwrap();
        assert_eq!(bridge.knowledge_revision().as_deref(), Some(first.as_str()));
        let replacement = home.join("replacement.json");
        fs::write(&replacement, b"{}").unwrap();
        fs::rename(&replacement, &checkpoint).unwrap();
        assert_ne!(bridge.knowledge_revision().unwrap(), first);
        fs::remove_file(&checkpoint).unwrap();
        std::os::unix::fs::symlink("replacement.json", &checkpoint).unwrap();
        fs::write(&replacement, b"{}").unwrap();
        assert_eq!(bridge.knowledge_revision(), None);
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn lightweight_voice_status_reads_pcm_without_python_and_masks_the_emergency_latch() {
        let root = std::env::temp_dir()
            .canonicalize()
            .unwrap()
            .join(format!("alden-voice-level-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir(&root).unwrap();
        fs::set_permissions(&root, fs::Permissions::from_mode(0o700)).unwrap();
        let mut bridge = bridge_with_state_root(root.clone());
        Arc::get_mut(&mut bridge.config).unwrap().python = root.join("python-must-not-run");
        fs::write(
            root.join(VOICE_STATUS_NAME),
            serde_json::to_vec(&serde_json::json!({
                "schema_version": 1, "state": "speaking", "rms": 0.9, "output_rms": 0.25,
                "updated_at": epoch_seconds() as u64, "custom_model_selected": true
            }))
            .unwrap(),
        )
        .unwrap();
        let status = bridge.voice_status();
        assert!(status.available);
        assert_eq!(status.output_rms, 0.25);
        assert!(!status.custom_model_selected);
        assert!(bridge.jobs.lock().unwrap().is_empty());
        assert!(bridge.cancellations.lock().unwrap().is_empty());
        write_global_abort_state(
            &root,
            &SafeEmergencyState {
                schema_version: 1,
                epoch: 1,
                latched: true,
                reason: "operator_pause".into(),
            },
        )
        .unwrap();
        let paused = bridge.voice_status();
        assert_eq!(paused.state, "aborted");
        assert_eq!(paused.rms, 0.0);
        assert_eq!(paused.output_rms, 0.0);
        assert_eq!(paused.custom_model_selected, status.custom_model_selected);
        fs::write(root.join(ABORT_STATE_NAME), b"invalid").unwrap();
        assert_eq!(bridge.voice_status().output_rms, 0.0);
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn operator_resume_requires_opt_in_increments_epoch_and_keeps_old_token_cancelled() {
        let temp = std::env::temp_dir()
            .canonicalize()
            .unwrap()
            .join(format!("openkakao-resume-state-{}", std::process::id()));
        let _ = fs::remove_dir_all(&temp);
        let bridge = bridge_with_state_root(temp.clone());
        let old_cancel = Arc::new(AtomicBool::new(false));
        bridge.cancellations.lock().unwrap().insert(
            "old-job".to_string(),
            CancellationHandle {
                flag: old_cancel.clone(),
                cooperative_marker: None,
                global_abort_flag: None,
            },
        );

        bridge.global_abort().unwrap();
        assert!(old_cancel.load(Ordering::SeqCst));
        let latched = bridge.emergency_state().unwrap();
        assert_eq!(latched.epoch, 1);
        assert!(latched.latched);
        assert!(matches!(
            bridge.operator_resume(false),
            Err(BridgeError::ActionNotAllowed)
        ));

        let resumed = bridge.operator_resume(true).unwrap();
        assert_eq!(resumed.epoch, 2);
        assert!(!resumed.latched);
        assert_eq!(resumed.reason, "human_resume");
        assert!(old_cancel.load(Ordering::SeqCst));
        assert!(!global_abort_is_latched(&temp).unwrap());
        let _ = fs::remove_dir_all(&temp);
    }

    #[test]
    fn corrupt_abort_state_is_rejected_and_not_overwritten_by_resume() {
        let temp = std::env::temp_dir()
            .canonicalize()
            .unwrap()
            .join(format!("openkakao-corrupt-resume-{}", std::process::id()));
        let _ = fs::remove_dir_all(&temp);
        fs::create_dir_all(&temp).unwrap();
        fs::set_permissions(&temp, fs::Permissions::from_mode(0o700)).unwrap();
        let path = temp.join(ABORT_STATE_NAME);
        fs::write(&path, b"{bad").unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).unwrap();
        let bridge = bridge_with_state_root(temp.clone());

        assert!(matches!(
            bridge.emergency_state(),
            Err(BridgeError::StateIo)
        ));
        assert!(matches!(
            bridge.operator_resume(true),
            Err(BridgeError::StateIo)
        ));
        assert_eq!(fs::read(&path).unwrap(), b"{bad");
        let _ = fs::remove_dir_all(&temp);
    }

    #[test]
    fn abort_reader_checks_duplicate_fields_custom_reason_hardlinks_and_fifo() {
        let temp = std::env::temp_dir()
            .canonicalize()
            .unwrap()
            .join(format!("openkakao-strict-abort-{}", std::process::id()));
        let _ = fs::remove_dir_all(&temp);
        write_global_abort(&temp).unwrap();
        let path = temp.join(ABORT_STATE_NAME);
        fs::write(
            &path,
            br#"{"schema_version":1,"epoch":1,"latched":true,"reason":"operator stop"}"#,
        )
        .unwrap();
        assert_eq!(
            read_global_abort_state(&temp).unwrap().reason,
            "operator stop"
        );
        let bridge = bridge_with_state_root(temp.clone());
        assert_eq!(bridge.operator_resume(true).unwrap().epoch, 2);
        fs::write(
            &path,
            br#"{"schema_version":1,"epoch":1,"epoch":2,"latched":true,"reason":"stop"}"#,
        )
        .unwrap();
        assert!(read_global_abort_state(&temp).is_err());
        write_private_test_json(
            &path,
            &json!({"schema_version":1,"epoch":2,"latched":true,"reason":"stop"}),
        );
        fs::hard_link(&path, temp.join("linked")).unwrap();
        assert!(read_global_abort_state(&temp).is_err());
        fs::remove_file(&path).unwrap();
        let cpath = std::ffi::CString::new(path.as_os_str().as_bytes()).unwrap();
        assert_eq!(unsafe { libc::mkfifo(cpath.as_ptr(), 0o600) }, 0);
        let started = Instant::now();
        assert!(read_global_abort_state(&temp).is_err());
        assert!(started.elapsed() < Duration::from_secs(1));
        let _ = fs::remove_dir_all(&temp);
    }

    fn write_private_test_json(path: &Path, value: &Value) {
        fs::write(path, serde_json::to_vec(value).unwrap()).unwrap();
        fs::set_permissions(path, fs::Permissions::from_mode(0o600)).unwrap();
    }

    #[test]
    fn abort_writers_share_python_flock_and_refuse_busy_or_unsafe_lock() {
        let temp = std::env::temp_dir().canonicalize().unwrap().join(format!(
            "openkakao-python-abort-lock-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&temp);
        write_global_abort(&temp).unwrap();
        let state_before = fs::read(temp.join(ABORT_STATE_NAME)).unwrap();
        let held = lock_global_abort(&temp).unwrap();
        assert!(write_global_abort(&temp).is_err());
        let scripts = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .parent()
            .unwrap()
            .join("scripts");
        let output = Command::new("python3").args(["-E", "-B", "-s", "-c"])
            .arg("import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); from alden_abort import AbortController,AbortStateError\ntry: AbortController(Path(sys.argv[2])).abort('python stop')\nexcept AbortStateError as e: assert 'lock_timeout' in str(e); print('locked')\nelse: raise SystemExit('lock ignored')")
            .arg(&scripts).arg(&temp).output().unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        assert_eq!(String::from_utf8_lossy(&output.stdout).trim(), "locked");
        assert_eq!(fs::read(temp.join(ABORT_STATE_NAME)).unwrap(), state_before);
        drop(held);
        let output = Command::new("python3").args(["-E", "-B", "-s", "-c"])
            .arg("import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); from alden_abort import AbortController; print(AbortController(Path(sys.argv[2])).abort('python stop').epoch)")
            .arg(&scripts).arg(&temp).output().unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        assert_eq!(read_global_abort_state(&temp).unwrap().epoch, 2);
        let bridge = bridge_with_state_root(temp.clone());
        assert_eq!(bridge.operator_resume(true).unwrap().epoch, 3);
        let lock_path = temp.join(ABORT_LOCK_NAME);
        fs::remove_file(&lock_path).unwrap();
        let cpath = std::ffi::CString::new(lock_path.as_os_str().as_bytes()).unwrap();
        assert_eq!(unsafe { libc::mkfifo(cpath.as_ptr(), 0o600) }, 0);
        assert!(write_global_abort(&temp).is_err());
        let _ = fs::remove_dir_all(&temp);
    }

    fn voice_process_fixture(argv: &[&str]) -> VoiceProcessArgs {
        (
            PathBuf::from("/usr/bin/python3"),
            argv.iter().map(|arg| OsString::from(*arg)).collect(),
        )
    }

    fn voice_ps_output(success: bool, stdout: &[u8]) -> std::io::Result<std::process::Output> {
        use std::os::unix::process::ExitStatusExt;
        Ok(std::process::Output {
            status: std::process::ExitStatus::from_raw(if success { 0 } else { 256 }),
            stdout: stdout.to_vec(),
            stderr: Vec::new(),
        })
    }

    #[test]
    fn voice_process_matching_preserves_script_and_state_root_argument_boundaries() {
        let root = "/Users/listener/Library/Application Support/openkakao/auto-reply";
        let script = "/Applications/Alden.app/Contents/Resources/scripts/alden_voice.py";
        for argv in [
            vec!["python3", script, "--state-root", root],
            vec!["python3", "-E", "-B", "-s", script, "--state-root", root],
            vec![
                "python3",
                "-Iu",
                "--",
                "scripts/alden_voice.py",
                "--state-root",
                root,
            ],
            vec![
                "python3",
                "-W",
                "ignore",
                "-X",
                "utf8",
                script,
                "--state-root",
                root,
            ],
        ] {
            assert!(
                voice_process_matches(&voice_process_fixture(&argv), Path::new(root)),
                "{argv:?}"
            );
        }
        let equals = format!("--state-root={root}");
        assert!(voice_process_matches(
            &voice_process_fixture(&["python3", script, &equals]),
            Path::new(root)
        ));

        let sibling = format!("{root}-other");
        let child = format!("{root}/child");
        let script_suffix = format!("{script}.backup");
        for argv in [
            vec!["python3", script, "--state-root", &sibling],
            vec!["python3", script, "--state-root", &child],
            vec!["python3", &script_suffix, "--state-root", root],
            vec![
                "python3",
                "/repo/not-scripts/alden_voice.py",
                "--state-root",
                root,
            ],
            vec!["python3", "/repo/other.py", script, "--state-root", root],
            vec!["python3", "-c", script, "--state-root", root],
            vec!["python3", "-m", script, "--state-root", root],
            vec!["python3", "-Ic", script, "--state-root", root],
            vec!["python3", "-", script, "--state-root", root],
            vec!["python3", script, "--state-root"],
            vec!["python3", script, "--state-root-prefix", root],
            vec!["python3", script, "--", "--state-root", root],
            vec![
                "python3",
                script,
                "--state-root",
                root,
                "--state-root",
                &sibling,
            ],
        ] {
            assert!(
                !voice_process_matches(&voice_process_fixture(&argv), Path::new(root)),
                "{argv:?}"
            );
        }
        let (_, argv) = voice_process_fixture(&["python3", script, "--state-root", root]);
        for executable in [
            "/bin/sh",
            "/bin/echo",
            "/bin/not-python3",
            "/bin/python3-helper",
        ] {
            assert!(!voice_process_matches(
                &(PathBuf::from(executable), argv.clone()),
                Path::new(root)
            ));
        }
        assert!(voice_process_matches(
            &(
                PathBuf::from("/Frameworks/Python.app/Contents/MacOS/Python"),
                argv
            ),
            Path::new(root)
        ));
    }

    #[test]
    fn voice_process_table_ignores_unrelated_zombie_and_exited_processes() {
        let mut inspected = Vec::new();
        let result = voice_session_from_process_table(
            Path::new("/state"),
            501,
            voice_ps_output(
                true,
                concat!(
                    " 501 1 Ss /sbin/launchd\n",
                    " 501 2 S /bin/echo\n",
                    " 501 3 Z /usr/bin/python3\n",
                    " 501 4 S /usr/bin/python3\n",
                )
                .as_bytes(),
            ),
            |pid| {
                inspected.push(pid);
                Ok(None)
            },
        );
        assert!(!result.unwrap());
        assert_eq!(inspected, vec![4]);
    }

    #[test]
    fn voice_process_table_scopes_argv_reads_to_current_uid() {
        let root = "/Users/listener/Library/Application Support/openkakao/bujamentor";
        let python =
            "/Users/listener/Library/Application Support/openkakao/runtimes/voice/bin/python3.11";
        let args: VoiceProcessArgs = (
            PathBuf::from(python),
            [
                python,
                "-E",
                "-B",
                "-s",
                "/Applications/Alden.app/Contents/Resources/scripts/alden_voice.py",
                "--state-root",
                root,
            ]
            .into_iter()
            .map(OsString::from)
            .collect(),
        );
        // These protected foreign-user processes must never reach read_args,
        // even when they use the same interpreter as a matching voice worker.
        let foreign = format!("    0 40 Ss   /usr/bin/python3\n  502 41 S    {python}\n");
        let mut inspected = Vec::new();
        let absent = voice_session_from_process_table(
            Path::new(root),
            501,
            voice_ps_output(true, foreign.as_bytes()),
            |_| panic!("foreign-user argv must not be queried"),
        );
        assert!(!absent.unwrap());
        let table = format!("{foreign}  501 25865 S    {python}\n");
        let present = voice_session_from_process_table(
            Path::new(root),
            501,
            voice_ps_output(true, table.as_bytes()),
            |pid| {
                assert_eq!(pid, 25865, "foreign-user argv must not be queried");
                inspected.push(pid);
                Ok(Some(args.clone()))
            },
        );
        assert!(present.unwrap());
        assert_eq!(inspected, vec![25865]);
    }

    #[test]
    fn voice_session_enumeration_failures_never_spawn() {
        let spawned = std::cell::Cell::new(0);
        for output in [
            Err(std::io::Error::new(
                std::io::ErrorKind::PermissionDenied,
                "ps denied",
            )),
            voice_ps_output(false, b"501 42 S /usr/bin/python3\n"),
            voice_ps_output(true, b""),
            voice_ps_output(true, b"\xff"),
            voice_ps_output(true, b"bad 42 S /usr/bin/python3\n"),
            voice_ps_output(true, b"501 bad S /usr/bin/python3\n"),
            voice_ps_output(true, b"501 42 S\n"),
            voice_ps_output(true, b"501 42 S /usr/bin/python3\n"),
        ] {
            let result = start_voice_session_if_absent(
                Path::new("/state"),
                |root| {
                    voice_session_from_process_table(root, 501, output, |_| {
                        Err(BridgeError::VoiceProcessCheck)
                    })
                },
                || {
                    spawned.set(spawned.get() + 1);
                    Ok(())
                },
            );
            assert!(matches!(result, Err(BridgeError::VoiceProcessCheck)));
        }
        assert_eq!(spawned.get(), 0);
        assert_eq!(
            BridgeError::VoiceProcessCheck.to_string(),
            "voice_session_process_check_failed"
        );
    }

    #[test]
    fn voice_session_candidate_argument_parse_failure_never_spawns() {
        let table = concat!(
            "0 40 S /usr/bin/python3\n",
            "501 41 S /usr/bin/python3\n",
            "501 42 S /usr/bin/python3\n",
        );
        let valid = voice_process_fixture(&[
            "python3",
            "scripts/alden_voice.py",
            "--state-root",
            "/state",
        ]);
        let result = start_voice_session_if_absent(
            Path::new("/state"),
            |root| {
                voice_session_from_process_table(
                    root,
                    501,
                    voice_ps_output(true, table.as_bytes()),
                    |pid| match pid {
                        41 => Ok(Some(valid.clone())),
                        42 => parse_voice_process_args(b"truncated").map(Some),
                        _ => panic!("foreign-user argv must not be queried"),
                    },
                )
            },
            || panic!("an unparseable candidate must block spawn"),
        );
        assert!(matches!(result, Err(BridgeError::VoiceProcessCheck)));
    }

    #[test]
    fn voice_session_live_process_blocks_spawn_with_missing_stale_or_fresh_heartbeat() {
        let root =
            std::env::temp_dir().join(format!("openkakao-voice-duplicate-{}", std::process::id()));
        fs::create_dir_all(&root).unwrap();
        let heartbeat = root.join(VOICE_STATUS_NAME);
        let spawned = std::cell::Cell::new(0);
        let args = voice_process_fixture(&[
            "python3",
            "/another checkout/scripts/alden_voice.py",
            "--state-root",
            root.to_str().unwrap(),
        ]);
        for updated_at in [None, Some(1), Some(epoch_seconds() as u64)] {
            let _ = fs::remove_file(&heartbeat);
            if let Some(updated_at) = updated_at {
                fs::write(
                    &heartbeat,
                    json!({
                        "schema_version": 1, "state": "wake_listen", "updated_at": updated_at,
                    })
                    .to_string(),
                )
                .unwrap();
            }
            let before = fs::read(&heartbeat).ok();
            let result = start_voice_session_if_absent(
                &root,
                |root| {
                    voice_session_from_process_table(
                        root,
                        501,
                        voice_ps_output(true, b"501 42 S /usr/bin/python3\n"),
                        |_| Ok(Some(args.clone())),
                    )
                },
                || {
                    spawned.set(spawned.get() + 1);
                    Ok(())
                },
            );
            assert!(matches!(
                result,
                Err(BridgeError::VoiceSessionAlreadyRunning)
            ));
            assert_eq!(
                result.unwrap_err().to_string(),
                "voice_session_already_running"
            );
            assert_eq!(fs::read(&heartbeat).ok(), before);
        }
        assert_eq!(spawned.get(), 0);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn voice_session_absent_listener_allows_one_spawn_and_preserves_spawn_failure() {
        let spawned = std::cell::Cell::new(0);
        let result = start_voice_session_if_absent(
            Path::new("/state"),
            |root| {
                voice_session_from_process_table(
                    root,
                    501,
                    voice_ps_output(true, b"0 1 Ss /sbin/launchd\n"),
                    |_| unreachable!(),
                )
            },
            || {
                spawned.set(spawned.get() + 1);
                Err(BridgeError::Spawn)
            },
        );
        assert!(matches!(result, Err(BridgeError::Spawn)));
        assert_eq!(spawned.get(), 1);
    }

    #[test]
    fn voice_session_concurrent_starts_probe_again_after_first_spawn() {
        let ready = Arc::new(std::sync::Barrier::new(2));
        let running = Arc::new(AtomicBool::new(false));
        let workers = (0..2)
            .map(|_| {
                let ready = ready.clone();
                let running = running.clone();
                thread::spawn(move || {
                    ready.wait();
                    start_voice_session_if_absent(
                        Path::new("/state"),
                        |_| Ok(running.load(Ordering::SeqCst)),
                        || {
                            running.store(true, Ordering::SeqCst);
                            Ok(())
                        },
                    )
                })
            })
            .collect::<Vec<_>>();
        let results = workers
            .into_iter()
            .map(|worker| worker.join().unwrap())
            .collect::<Vec<_>>();
        assert_eq!(results.iter().filter(|result| result.is_ok()).count(), 1);
        assert_eq!(
            results
                .iter()
                .filter(|result| matches!(result, Err(BridgeError::VoiceSessionAlreadyRunning)))
                .count(),
            1
        );
    }

    #[test]
    fn voice_process_native_arguments_preserve_spaces_empty_args_and_omit_environment() {
        let argv = [
            "python3",
            "/bundle with spaces/scripts/alden_voice.py",
            "--state-root",
            "/state with spaces",
            "",
        ];
        let mut bytes = (argv.len() as i32).to_ne_bytes().to_vec();
        bytes.extend_from_slice(b"/usr/bin/python3\0\0\0");
        for arg in argv {
            bytes.extend_from_slice(arg.as_bytes());
            bytes.push(0);
        }
        let parsed = parse_voice_process_args(&bytes).unwrap();
        assert_eq!(parsed, voice_process_fixture(&argv));
        let truncated = bytes[..bytes.len() - 1].to_vec();
        bytes.extend_from_slice(b"PRIVATE_ENV=not-an-argument\0");
        assert_eq!(parse_voice_process_args(&bytes).unwrap(), parsed);
        for invalid in [
            vec![],
            0i32.to_ne_bytes().to_vec(),
            (-1i32).to_ne_bytes().to_vec(),
            truncated,
        ] {
            assert!(matches!(
                parse_voice_process_args(&invalid),
                Err(BridgeError::VoiceProcessCheck)
            ));
        }
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn voice_process_native_probe_matches_synthetic_worker_without_signaling_it() {
        use std::io::BufRead;
        let root =
            std::env::temp_dir().join(format!("openkakao native voice {}", std::process::id()));
        let script = root.join("scripts/alden_voice.py");
        fs::create_dir_all(script.parent().unwrap()).unwrap();
        fs::write(
            &script,
            b"import sys\nprint('ready', flush=True)\nsys.stdin.buffer.read(1)\n",
        )
        .unwrap();
        let mut child = Command::new("python3")
            .args(["-E", "-B", "-s"])
            .arg(&script)
            .arg("--state-root")
            .arg(&root)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .unwrap();
        let mut ready = String::new();
        let read = std::io::BufReader::new(child.stdout.take().unwrap()).read_line(&mut ready);
        // Exercise the real full macOS table shape. Read native argv only for
        // our synthetic worker; all other processes use the fake adapter.
        // SAFETY: geteuid only reads this process's effective user ID.
        let current_uid = unsafe { libc::geteuid() };
        let result = voice_session_from_process_table(
            &root,
            current_uid,
            Command::new("/bin/ps")
                .args(["-ww", "-axo", "uid=,pid=,stat=,comm="])
                .output(),
            |pid| {
                if pid == child.id() as i32 {
                    read_voice_process_args(pid)
                } else {
                    Ok(None)
                }
            },
        );
        let still_alive = child.try_wait();
        // The owned fixture exits normally on EOF; it never imports voice code
        // or opens a microphone, and neither inspection nor cleanup sends signals.
        drop(child.stdin.take());
        let exit = child.wait();
        fs::remove_dir_all(root).unwrap();
        read.unwrap();
        assert_eq!(ready, "ready\n");
        assert!(result.unwrap());
        assert!(still_alive.unwrap().is_none());
        assert!(exit.unwrap().success());
    }

    #[test]
    fn plan_voice_session_requires_isolated_interpreter() {
        let temp = std::env::temp_dir()
            .canonicalize()
            .unwrap()
            .join(format!("openkakao-voice-session-{}", std::process::id()));
        let _ = fs::remove_dir_all(&temp);
        fs::create_dir_all(&temp).unwrap();
        let state_root = temp.join("state");
        let python = temp.join(".venv-voice/bin/python");
        assert!(matches!(
            plan_voice_session(&temp, &state_root, &python),
            Err(BridgeError::VoiceEnv)
        ));

        let python = temp.join(".venv-voice/bin/python");
        fs::create_dir_all(python.parent().unwrap()).unwrap();
        fs::write(&python, b"fake interpreter; never executed").unwrap();
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&python, fs::Permissions::from_mode(0o755)).unwrap();
        assert!(matches!(
            plan_voice_session(&temp, &state_root, &python),
            Err(BridgeError::VoiceScript)
        ));

        let script = temp.join("scripts/alden_voice.py");
        fs::create_dir_all(script.parent().unwrap()).unwrap();
        fs::write(&script, b"# fake voice script").unwrap();
        let plan = plan_voice_session(&temp, &state_root, &python).unwrap();
        assert_eq!(plan.python, python.canonicalize().unwrap());
        assert_eq!(plan.script, script);
        assert_eq!(plan.tts_out, state_root.join(VOICE_TTS_OUT_NAME));

        // The interpreter is never executed. Symlinks and non-executables
        // are rejected before starting a microphone/model session.
        let link = temp.join("python-link");
        std::os::unix::fs::symlink(&python, &link).unwrap();
        assert!(matches!(
            plan_voice_session(&temp, &state_root, &link),
            Err(BridgeError::VoiceEnv)
        ));
        fs::set_permissions(&python, fs::Permissions::from_mode(0o644)).unwrap();
        assert!(matches!(
            plan_voice_session(&temp, &state_root, &python),
            Err(BridgeError::VoiceEnv)
        ));

        let command = voice_session_command(&plan, &state_root).unwrap();
        let envs = command
            .get_envs()
            .map(|(key, value)| (key.to_owned(), value.map(|value| value.to_owned())))
            .collect::<HashMap<_, _>>();
        assert_eq!(
            envs.get(std::ffi::OsStr::new("OPENKAKAO_VOICE_ENV")),
            Some(&Some(std::ffi::OsString::from("1")))
        );
        assert_eq!(
            envs.get(std::ffi::OsStr::new("OPENKAKAO_VOICE_TTS_OUT")),
            Some(&Some(plan.tts_out.clone().into_os_string()))
        );
        let _ = fs::remove_dir_all(&temp);
    }
}
