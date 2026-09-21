use crate::resource_layout::{
    self, validate_path, DevOverrides, Kind, ResourceError, ResourceLayout,
};
use serde::Serialize;
use serde_json::{json, Value};
use std::collections::{HashMap, HashSet};
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use thiserror::Error;

const DEFAULT_TIMEOUT: Duration = Duration::from_secs(8);
const SNAPSHOT_TIMEOUT: Duration = Duration::from_secs(25);
const BROWSER_TOOL_TIMEOUT: Duration = Duration::from_secs(90);
const BROWSER_TOOL_ABORT_GRACE: Duration = Duration::from_secs(2);
const MODEL_SWAP_TIMEOUT: Duration = Duration::from_secs(120);
const MODEL_SWAP_RECOVERY_TIMEOUT: Duration = Duration::from_secs(120);
const OUTPUT_LIMIT_BYTES: usize = 4 * 1024 * 1024;
const BROWSER_TOOL_OUTPUT_LIMIT_BYTES: usize = 96 * 1024;
const BROWSER_TOOL_RESULT_LIMIT_BYTES: usize = 64 * 1024;
const BROWSER_TOOL_TASK_LIMIT_BYTES: usize = 16 * 1024;
const BROWSER_TOOL_JOB_ID_LIMIT: usize = 64;
const ABORT_STATE_NAME: &str = "jarvis-abort.json";
const VOICE_STATUS_NAME: &str = "jarvis-voice-status.json";
const STATE_FILE_LIMIT_BYTES: u64 = 4096;
const WAKE_PHRASE: &str = "헤이 자비스";
const WAKE_THRESHOLD: f64 = 0.65;
const CUSTOM_WAKE_MODEL_MAX_BYTES: u64 = 64 * 1024 * 1024;
const BUNDLED_CUSTOM_WAKE_MODEL: &str = resource_layout::WAKE_MODEL;
const RESIDENT_MODEL_ID: &str = "ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit";
const SWAP_MODEL_ID: &str = "ddalcu/Qwen3.8-27B-MLX-Serve-4bit";
const MODEL_SWAP_OPT_IN: &str = "qwen38-27b-explicit-v1";
const MODEL_SWAP_CANCEL_DIR: &str = "model-swap-cancel";
const VOICE_TTS_OUT_NAME: &str = "jarvis-voice-out.wav";

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
    #[error("voice_environment_missing")]
    VoiceEnv,
    #[error("voice_script_missing")]
    VoiceScript,
}

#[derive(Clone)]
pub struct PythonBridge {
    config: Arc<BridgeConfig>,
    cancellations: Arc<Mutex<HashMap<String, CancellationHandle>>>,
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

#[derive(Debug)]
struct BridgeConfig {
    python: PathBuf,
    voice_python: PathBuf,
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
}

#[derive(Debug, Serialize)]
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
    available: bool,
    state: String,
    rms: f64,
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
            available: false,
            state: "unavailable".to_string(),
            rms: 0.0,
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
    jobs: Vec<SafeJobEvent>,
    job_load: f64,
    terminal_counts: TerminalCounts,
    context_sync: ContextSync,
    reply_model_id: Option<String>,
    voice: SafeVoiceStatus,
    error_code: Option<String>,
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
        }
    }

    pub fn fetch_snapshot(
        &self,
        token_id: Option<&str>,
    ) -> Result<SafeRuntimeSnapshot, BridgeError> {
        let bytes = self.run_python(&[], SNAPSHOT_TIMEOUT, token_id, false)?;
        let value = parse_json_output(&bytes)?;
        let mut snapshot = sanitize_snapshot(&value);
        snapshot.voice = read_voice_status(&self.config.state_root, &self.config.resources()?.root);
        Ok(snapshot)
    }

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
        let bytes = self.run_python(
            &args,
            if is_swap {
                MODEL_SWAP_TIMEOUT
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
            "knowledge-graph-focus" => sanitize_knowledge_focus(&value),
            "model-set" => sanitize_model_action(&value, action, RESIDENT_MODEL_ID),
            "model-prepare" => sanitize_model_action(&value, action, SWAP_MODEL_ID),
            "model-swap" => sanitize_model_swap_action(&value),
            _ => return Err(BridgeError::ActionNotAllowed),
        })
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
        let bytes = self.run_python_with_output_limit(
            &args,
            BROWSER_TOOL_TIMEOUT,
            Some(cancellation_token),
            false,
            BROWSER_TOOL_OUTPUT_LIMIT_BYTES,
            Some(task.as_bytes()),
            Some(BROWSER_TOOL_ABORT_GRACE),
        )?;
        let value = parse_json_output(&bytes)?;
        Ok(sanitize_browser_tool_result(&value))
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

    pub fn start_voice_session(&self) -> Result<(), BridgeError> {
        let resources = self.config.resources()?;
        resources.validate().map_err(BridgeError::from)?;
        let plan = plan_voice_session(
            &resources.root,
            &self.config.state_root,
            &self.config.voice_python,
        )?;
        voice_session_command(&plan, &self.config.state_root)?
            .spawn()
            .map(|_| ())
            .map_err(|_| BridgeError::Spawn)
    }

    fn run_python(
        &self,
        extra: &[String],
        timeout: Duration,
        token_id: Option<&str>,
        cooperative_cancel: bool,
    ) -> Result<Vec<u8>, BridgeError> {
        self.run_python_with_output_limit(
            extra,
            timeout,
            token_id,
            cooperative_cancel,
            OUTPUT_LIMIT_BYTES,
            None,
            None,
        )
    }

    fn run_python_with_output_limit(
        &self,
        extra: &[String],
        timeout: Duration,
        token_id: Option<&str>,
        cooperative_cancel: bool,
        output_limit: usize,
        stdin_payload: Option<&[u8]>,
        global_abort_grace: Option<Duration>,
    ) -> Result<Vec<u8>, BridgeError> {
        let resources = self.config.resources()?;
        resources.validate().map_err(BridgeError::from)?;
        let python = if !resources.installed && self.config.python == Path::new("python3") {
            "python3"
        } else {
            validate_path(&self.config.python, Kind::Executable)
                .map_err(|_| BridgeError::PythonEnv)?;
            self.config.python.to_str().ok_or(BridgeError::PythonEnv)?
        };
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
                    if global_abort_is_latched(&self.config.state_root) {
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

        let result = run_process_with_recovery(
            python,
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
        let (python, voice_python) = if dev
            && resources.as_ref().is_ok_and(|layout| !layout.installed)
        {
            let root = resources
                .as_ref()
                .map(|r| r.root.as_path())
                .unwrap_or(checkout);
            (
                get_override("OPENKAKAO_PYTHON").unwrap_or_else(|| {
                    let uv = home.join(
                        ".local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11",
                    );
                    if uv.is_file() {
                        return uv;
                    }
                    let homebrew = PathBuf::from("/opt/homebrew/opt/python@3.11/bin/python3.11");
                    if homebrew.is_file() {
                        homebrew
                    } else {
                        PathBuf::from("python3")
                    }
                }),
                get_override("OPENKAKAO_VOICE_PYTHON")
                    .unwrap_or_else(|| root.join(".venv-voice/bin/python")),
            )
        } else {
            // Provisioned separately; never copy or inspect a checkout .venv.
            (
                support.join("runtimes/menubar/bin/python3.11"),
                support.join("runtimes/voice/bin/python3.11"),
            )
        };
        Self {
            python,
            voice_python,
            resources,
            state_root,
            logs_dir,
        }
    }
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

fn run_process_with_recovery(
    executable: &str,
    args: &[String],
    timeout: Duration,
    output_limit: usize,
    control: ProcessControl<'_>,
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
    if action != "model-swap" && (explicit_opt_in.is_some() || token_id.is_some()) {
        return Err(BridgeError::ActionNotAllowed);
    }
    match action {
        "models" | "dream-rsi-status" | "knowledge-graph-status" | "knowledge-graph" => {}
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
        "model-set" if model == Some(RESIDENT_MODEL_ID) => {
            args.extend([
                "--model".to_string(),
                RESIDENT_MODEL_ID.to_string(),
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

fn global_abort_is_latched(state_root: &Path) -> bool {
    safe_small_json(&state_root.join(ABORT_STATE_NAME))
        .and_then(|value| value.get("latched").and_then(Value::as_bool))
        .unwrap_or(false)
}

fn bundled_custom_wake_model_selected(repo_root: &Path) -> bool {
    let path = repo_root.join(BUNDLED_CUSTOM_WAKE_MODEL);
    if validate_path(&path, Kind::Data).is_err() {
        return false;
    }
    let Ok(metadata) = fs::symlink_metadata(&path) else {
        return false;
    };
    let extension_valid = path
        .extension()
        .and_then(|value| value.to_str())
        .map(|value| value.eq_ignore_ascii_case("onnx") || value.eq_ignore_ascii_case("tflite"))
        .unwrap_or(false);
    metadata.is_file()
        && !metadata.file_type().is_symlink()
        && extension_valid
        && metadata.len() > 0
        && metadata.len() <= CUSTOM_WAKE_MODEL_MAX_BYTES
}

fn default_voice_status(repo_root: &Path) -> SafeVoiceStatus {
    SafeVoiceStatus {
        custom_model_selected: bundled_custom_wake_model_selected(repo_root),
        ..SafeVoiceStatus::default()
    }
}

fn read_voice_status(state_root: &Path, repo_root: &Path) -> SafeVoiceStatus {
    let Some(value) = safe_small_json(&state_root.join(VOICE_STATUS_NAME)) else {
        return default_voice_status(repo_root);
    };
    let Some(root) = value.as_object() else {
        return default_voice_status(repo_root);
    };
    if root.get("schema_version").and_then(Value::as_u64) != Some(1) {
        return default_voice_status(repo_root);
    }
    let bundled_model_selected = bundled_custom_wake_model_selected(repo_root);
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
    SafeVoiceStatus {
        available: true,
        state,
        rms: clamp01(root.get("rms").and_then(Value::as_f64).unwrap_or(0.0)),
        error_code,
        wake_source: wake_source.to_string(),
        updated_at: as_u64(root.get("updated_at")),
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
        custom_model_selected: root
            .get("custom_model_selected")
            .and_then(Value::as_bool)
            .unwrap_or(bundled_model_selected),
    }
}

fn write_global_abort(state_root: &Path) -> Result<(), BridgeError> {
    fs::create_dir_all(state_root).map_err(|_| BridgeError::StateIo)?;
    let path = state_root.join(ABORT_STATE_NAME);
    if fs::symlink_metadata(&path)
        .map(|metadata| metadata.file_type().is_symlink())
        .unwrap_or(false)
    {
        return Err(BridgeError::StateIo);
    }
    let epoch = safe_small_json(&path)
        .and_then(|value| value.get("epoch").and_then(Value::as_u64))
        .unwrap_or(0)
        .saturating_add(1);
    let payload = json!({
        "schema_version": 1,
        "epoch": epoch,
        "latched": true,
        "reason": "global_abort"
    });
    let temp = state_root.join(format!(".{ABORT_STATE_NAME}.{}.tmp", std::process::id()));
    let mut file = OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .open(&temp)
        .map_err(|_| BridgeError::StateIo)?;
    serde_json::to_writer(&mut file, &payload).map_err(|_| BridgeError::StateIo)?;
    file.flush().map_err(|_| BridgeError::StateIo)?;
    file.sync_all().map_err(|_| BridgeError::StateIo)?;
    fs::rename(&temp, &path).map_err(|_| BridgeError::StateIo)?;
    Ok(())
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

fn sanitize_snapshot(value: &Value) -> SafeRuntimeSnapshot {
    let Some(root) = value.as_object() else {
        return SafeRuntimeSnapshot {
            available: false,
            rooms: Vec::new(),
            jobs: Vec::new(),
            job_load: 0.0,
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
            titles.insert(id, title.chars().take(120).collect());
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
                    })
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();

    let open_jobs = as_u64(root.get("open_jobs"));
    let background = root
        .get("background")
        .and_then(Value::as_object)
        .and_then(|obj| obj.get("activity"))
        .and_then(Value::as_f64)
        .unwrap_or(0.0);
    let job_load = clamp01((open_jobs as f64 / 4.0).max(background));

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
        .map(|id| id.chars().take(256).collect::<String>());

    SafeRuntimeSnapshot {
        available: true,
        rooms,
        jobs: Vec::new(),
        job_load,
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
    })
}

fn sanitize_knowledge_focus(value: &Value) -> Value {
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
    })
}

#[cfg(test)]
mod tests {
    use super::*;

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
        let set_args = settings_action_args(
            "model-set",
            None,
            None,
            None,
            Some(RESIDENT_MODEL_ID),
            None,
            None,
        )
        .unwrap();
        assert_eq!(
            set_args,
            vec![
                "--action",
                "model-set",
                "--model",
                RESIDENT_MODEL_ID,
                "--no-wait",
            ]
        );

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
            ("model-set", SWAP_MODEL_ID),
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
        let safe = sanitize_model_action(
            &json!({
                "ok": true,
                "action": "model-set",
                "model": RESIDENT_MODEL_ID,
                "stored": true,
                "prepared": true,
                "needs_prepare": false,
                "prompt": "private",
                "body": "private",
                "secret": "private",
                "warnings": ["untrusted"],
            }),
            "model-set",
            RESIDENT_MODEL_ID,
        );
        assert_eq!(safe["ok"], true);
        assert_eq!(safe["model"], RESIDENT_MODEL_ID);
        assert!(safe.get("prompt").is_none());
        assert!(safe.get("body").is_none());
        assert!(safe.get("secret").is_none());
        assert!(safe.get("warnings").is_none());

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
        assert!(safe.jobs.is_empty());
        assert!(safe.reply_model_id.is_none());
        assert_eq!(safe.terminal_counts.delivery_unknown, 1);
        assert_eq!(safe.context_sync.mode, "async");
        assert!(!safe.context_sync.waited);
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
    fn missing_voice_status_selects_valid_bundled_wake_model() {
        let temp = std::env::temp_dir().canonicalize().unwrap().join(format!(
            "openkakao-voice-status-valid-bundle-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&temp);
        let state_root = temp.join("state");
        let repo_root = temp.join("repo");
        let bundled_model = repo_root.join(BUNDLED_CUSTOM_WAKE_MODEL);
        fs::create_dir_all(bundled_model.parent().unwrap()).unwrap();
        fs::write(&bundled_model, b"onnx").unwrap();

        let status = read_voice_status(&state_root, &repo_root);
        assert!(!status.available);
        assert_eq!(status.wake_phrase, WAKE_PHRASE);
        assert_eq!(status.threshold, WAKE_THRESHOLD);
        assert!(status.custom_model_selected);

        let _ = fs::remove_dir_all(&temp);
    }

    #[test]
    fn missing_voice_status_rejects_missing_or_invalid_bundled_wake_model() {
        let temp = std::env::temp_dir().canonicalize().unwrap().join(format!(
            "openkakao-voice-status-invalid-bundle-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&temp);
        let state_root = temp.join("state");
        let repo_root = temp.join("repo");

        let missing = read_voice_status(&state_root, &repo_root);
        assert!(!missing.custom_model_selected);

        let bundled_model = repo_root.join(BUNDLED_CUSTOM_WAKE_MODEL);
        fs::create_dir_all(bundled_model.parent().unwrap()).unwrap();
        fs::write(&bundled_model, b"").unwrap();
        let invalid = read_voice_status(&state_root, &repo_root);
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
            br#"{"schema_version":1,"state":"speaking","rms":2.0,"error_code":"","wake_source":"stock","updated_at":7}"#,
        )
        .unwrap();
        let status = read_voice_status(&temp, &temp.join("repo-without-bundle"));
        assert!(status.available);
        assert_eq!(status.state, "speaking");
        assert_eq!(status.rms, 1.0);
        assert_eq!(status.wake_source, "stock");
        assert_eq!(status.updated_at, 7);
        assert_eq!(status.wake_phrase, WAKE_PHRASE);
        assert_eq!(status.threshold, WAKE_THRESHOLD);
        assert!(!status.custom_model_selected);
        let _ = fs::remove_dir_all(&temp);
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
        let _ = fs::remove_dir_all(&temp);
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

        let script = temp.join("scripts/jarvis_voice.py");
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
