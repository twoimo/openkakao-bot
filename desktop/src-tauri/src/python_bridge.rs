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
const OUTPUT_LIMIT_BYTES: usize = 4 * 1024 * 1024;
const ABORT_STATE_NAME: &str = "jarvis-abort.json";
const VOICE_STATUS_NAME: &str = "jarvis-voice-status.json";
const STATE_FILE_LIMIT_BYTES: u64 = 4096;
const WAKE_PHRASE: &str = "헤이 자비스";
const WAKE_THRESHOLD: f64 = 0.65;
const CUSTOM_WAKE_MODEL_MAX_BYTES: u64 = 64 * 1024 * 1024;
const BUNDLED_CUSTOM_WAKE_MODEL: &str = "voice/models/hey_jarvis_ko_ridge.onnx";
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
    #[error("voice_environment_missing")]
    VoiceEnv,
    #[error("voice_script_missing")]
    VoiceScript,
}

#[derive(Clone)]
pub struct PythonBridge {
    config: Arc<BridgeConfig>,
    cancellations: Arc<Mutex<HashMap<String, Arc<AtomicBool>>>>,
}

#[derive(Debug)]
struct BridgeConfig {
    python: String,
    script: PathBuf,
    repo_root: PathBuf,
    state_root: PathBuf,
    logs_dir: PathBuf,
    bin: Option<PathBuf>,
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
        let bytes = self.run_python(&[], SNAPSHOT_TIMEOUT, token_id)?;
        let value = parse_json_output(&bytes)?;
        let mut snapshot = sanitize_snapshot(&value);
        snapshot.voice = read_voice_status(&self.config.state_root, &self.config.repo_root);
        Ok(snapshot)
    }

    pub fn fetch_settings_action(
        &self,
        action: &str,
        query: Option<&str>,
        node_id: Option<&str>,
        chat_id: Option<&str>,
    ) -> Result<Value, BridgeError> {
        let allowed = matches!(
            action,
            "models"
                | "dream-rsi-status"
                | "knowledge-graph-status"
                | "knowledge-graph"
                | "knowledge-graph-focus"
        );
        if !allowed {
            return Err(BridgeError::ActionNotAllowed);
        }
        let mut args = vec!["--action".to_string(), action.to_string()];
        if action == "knowledge-graph-focus" {
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
        let bytes = self.run_python(&args, DEFAULT_TIMEOUT, None)?;
        let value = parse_json_output(&bytes)?;
        Ok(match action {
            "models" => sanitize_models(&value),
            "dream-rsi-status" => sanitize_dream_rsi(&value),
            "knowledge-graph-status" => sanitize_knowledge_status(&value),
            "knowledge-graph" => sanitize_knowledge_graph(&value),
            "knowledge-graph-focus" => sanitize_knowledge_focus(&value),
            _ => return Err(BridgeError::ActionNotAllowed),
        })
    }

    pub fn cancel(&self, token_id: &str) -> bool {
        let Ok(map) = self.cancellations.lock() else {
            return false;
        };
        let Some(flag) = map.get(token_id) else {
            return false;
        };
        flag.store(true, Ordering::SeqCst);
        true
    }

    pub fn global_abort(&self) -> Result<(), BridgeError> {
        if let Ok(map) = self.cancellations.lock() {
            for flag in map.values() {
                flag.store(true, Ordering::SeqCst);
            }
        }
        write_global_abort(&self.config.state_root)
    }

    pub fn start_voice_session(&self) -> Result<(), BridgeError> {
        let plan = plan_voice_session(&self.config.repo_root, &self.config.state_root)?;
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
    ) -> Result<Vec<u8>, BridgeError> {
        let cancel_flag = Arc::new(AtomicBool::new(false));
        if let Some(token) = token_id {
            if let Ok(mut map) = self.cancellations.lock() {
                map.insert(token.to_string(), cancel_flag.clone());
            }
        }

        let mut args = vec![
            "-E".to_string(),
            "-B".to_string(),
            self.config.script.to_string_lossy().into_owned(),
            "--state-root".to_string(),
            self.config.state_root.to_string_lossy().into_owned(),
            "--logs-dir".to_string(),
            self.config.logs_dir.to_string_lossy().into_owned(),
        ];
        if let Some(bin) = &self.config.bin {
            args.push("--bin".to_string());
            args.push(bin.to_string_lossy().into_owned());
        }
        args.extend(extra.iter().cloned());

        let result = run_process(
            &self.config.python,
            &args,
            timeout,
            OUTPUT_LIMIT_BYTES,
            cancel_flag,
        );
        if let Some(token) = token_id {
            if let Ok(mut map) = self.cancellations.lock() {
                map.remove(token);
            }
        }
        result
    }
}

impl BridgeConfig {
    fn discover() -> Self {
        let repo_root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .and_then(Path::parent)
            .map(Path::to_path_buf)
            .unwrap_or_else(|| PathBuf::from("."));
        let home = dirs::home_dir().unwrap_or_else(|| PathBuf::from("/tmp"));
        let support = home.join("Library/Application Support/openkakao");
        let modern = support.join("auto-reply");
        let legacy = support.join("bujamentor");
        let state_root = std::env::var_os("OPENKAKAO_STATE_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                if modern.join("enrollment.json").is_file()
                    || !legacy.join("enrollment.json").is_file()
                {
                    modern
                } else {
                    legacy
                }
            });
        let script = std::env::var_os("OPENKAKAO_MENUBAR_SCRIPT")
            .map(PathBuf::from)
            .unwrap_or_else(|| repo_root.join("scripts/auto-reply-menubar.py"));
        let logs_dir = std::env::var_os("OPENKAKAO_LOGS_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|| home.join("Library/Logs/AutoReplyMenu"));
        let bin = std::env::var_os("OPENKAKAO_BIN")
            .map(PathBuf::from)
            .or_else(|| {
                let candidate = repo_root.join("target/debug/openkakao-cli");
                candidate.is_file().then_some(candidate)
            });
        let python = std::env::var("OPENKAKAO_PYTHON").unwrap_or_else(|_| {
            let uv =
                home.join(".local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11");
            if uv.is_file() {
                return uv.to_string_lossy().into_owned();
            }
            let homebrew = Path::new("/opt/homebrew/opt/python@3.11/bin/python3.11");
            if homebrew.is_file() {
                homebrew.to_string_lossy().into_owned()
            } else {
                "python3".to_string()
            }
        });
        Self {
            python,
            script,
            repo_root,
            state_root,
            logs_dir,
            bin,
        }
    }
}

fn run_process(
    executable: &str,
    args: &[String],
    timeout: Duration,
    output_limit: usize,
    cancel_flag: Arc<AtomicBool>,
) -> Result<Vec<u8>, BridgeError> {
    let mut child = Command::new(executable)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|_| BridgeError::Spawn)?;
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
    let status = loop {
        if cancel_flag.load(Ordering::SeqCst) {
            let _ = child.kill();
            let _ = child.wait();
            let _ = reader.join();
            return Err(BridgeError::Cancelled);
        }
        if started.elapsed() >= timeout {
            let _ = child.kill();
            let _ = child.wait();
            let _ = reader.join();
            return Err(BridgeError::Timeout);
        }
        match child.try_wait().map_err(|_| BridgeError::Wait)? {
            Some(status) => break status,
            None => thread::sleep(Duration::from_millis(10)),
        }
    };

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

fn plan_voice_session(repo_root: &Path, state_root: &Path) -> Result<VoiceSessionPlan, BridgeError> {
    let python = repo_root.join(".venv-voice/bin/python")
        .canonicalize()
        .map_err(|_| BridgeError::VoiceEnv)?;
    let script = repo_root.join("scripts/jarvis_voice.py");
    if !python.is_file() {
        return Err(BridgeError::VoiceEnv);
    }
    if !script.is_file() || script.symlink_metadata().map(|m| m.file_type().is_symlink()).unwrap_or(true) {
        return Err(BridgeError::VoiceScript);
    }
    Ok(VoiceSessionPlan {
        python,
        script,
        tts_out: state_root.join(VOICE_TTS_OUT_NAME),
    })
}

fn voice_session_command(plan: &VoiceSessionPlan, state_root: &Path) -> Result<Command, BridgeError> {
    let mut command = Command::new(&plan.python);
    command
        .env("OPENKAKAO_VOICE_ENV", "1")
        .env("OPENKAKAO_VOICE_TTS_OUT", &plan.tts_out)
        .args([
            "-E",
            "-B",
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

fn bundled_custom_wake_model_selected(repo_root: &Path) -> bool {
    let path = repo_root.join(BUNDLED_CUSTOM_WAKE_MODEL);
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
        threshold: {
            let value = root
                .get("threshold")
                .and_then(Value::as_f64)
                .unwrap_or(WAKE_THRESHOLD);
            if value < WAKE_THRESHOLD {
                WAKE_THRESHOLD
            } else if value > 0.95 {
                0.95
            } else {
                value
            }
        },
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
        let temp = std::env::temp_dir().join(format!(
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
        let temp = std::env::temp_dir().join(format!(
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
        let temp =
            std::env::temp_dir().join(format!("openkakao-voice-status-{}", std::process::id()));
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
        let temp =
            std::env::temp_dir().join(format!("openkakao-abort-state-{}", std::process::id()));
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
        let temp = std::env::temp_dir().join(format!(
            "openkakao-voice-session-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&temp);
        fs::create_dir_all(&temp).unwrap();
        let state_root = temp.join("state");
        assert!(matches!(
            plan_voice_session(&temp, &state_root),
            Err(BridgeError::VoiceEnv)
        ));

        let python = temp.join(".venv-voice/bin/python");
        fs::create_dir_all(python.parent().unwrap()).unwrap();
        fs::write(&python, b"").unwrap();
        assert!(matches!(
            plan_voice_session(&temp, &state_root),
            Err(BridgeError::VoiceScript)
        ));

        let script = temp.join("scripts/jarvis_voice.py");
        fs::create_dir_all(script.parent().unwrap()).unwrap();
        fs::write(&script, b"").unwrap();
        let plan = plan_voice_session(&temp, &state_root).unwrap();
        assert_eq!(plan.python, python.canonicalize().unwrap());
        assert_eq!(plan.script, script);
        assert_eq!(plan.tts_out, state_root.join(VOICE_TTS_OUT_NAME));

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
