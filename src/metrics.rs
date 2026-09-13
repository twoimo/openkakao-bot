//! Opt-in, privacy-safe performance metrics for AutoReply services.
//!
//! Mirrors `scripts/auto_reply_metrics.py`. Metrics are disabled unless
//! `OPENKAKAO_PERF_METRICS=1` and never carry application data.

use std::fs::OpenOptions;
use std::io::Write;
use std::time::Instant;
use serde::{Deserialize, Serialize};

pub const SCHEMA: &str = "auto_reply_perf_v1";
const ENABLE_ENV: &str = "OPENKAKAO_PERF_METRICS";
const FILE_ENV: &str = "OPENKAKAO_PERF_METRICS_FILE";

pub fn enabled() -> bool {
    std::env::var(ENABLE_ENV).map(|v| v == "1").unwrap_or(false)
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Measurement {
    pub schema: &'static str,
    pub stage: String,
    pub duration_ms: f64,
    pub outcome: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rows: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub count: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bytes_total: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub queue_depth: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub lateness_ms: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error_class: Option<String>,
}

impl Measurement {
    pub fn new(stage: impl Into<String>) -> Self {
        Self {
            schema: SCHEMA,
            stage: stage.into(),
            duration_ms: 0.0,
            outcome: "ok".into(),
            rows: None,
            count: None,
            bytes_total: None,
            queue_depth: None,
            lateness_ms: None,
            error_class: None,
        }
    }
}

pub struct ScopeTimer {
    pub sample: Measurement,
    started: Instant,
}

impl ScopeTimer {
    pub fn start(stage: impl Into<String>) -> Self {
        let stage_str = stage.into();
        Self {
            sample: Measurement::new(stage_str),
            started: Instant::now(),
        }
    }

    pub fn finish(mut self, outcome: &str) -> bool {
        self.sample.duration_ms = self.started.elapsed().as_secs_f64() * 1000.0;
        self.sample.outcome = outcome.into();
        record_measurement(&self.sample)
    }
}

pub fn record_measurement(m: &Measurement) -> bool {
    if !enabled() {
        return false;
    }
    let Ok(line) = serde_json::to_string(m) else {
        return false;
    };
    if let Ok(file_path) = std::env::var(FILE_ENV) {
        if !file_path.trim().is_empty() {
            if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(file_path.trim()) {
                let _ = writeln!(file, "{}", line);
                return true;
            }
        }
    }
    eprintln!("{}", line);
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn measurement_serializes_with_schema() {
        let m = Measurement::new("test_stage");
        let s = serde_json::to_string(&m).unwrap();
        assert!(s.contains("auto_reply_perf_v1"));
        assert!(s.contains("test_stage"));
        assert_eq!(m.outcome, "ok");
    }

    #[test]
    fn scope_timer_records_duration() {
        let timer = ScopeTimer::start("timer_test");
        std::thread::sleep(std::time::Duration::from_millis(5));
        assert!(timer.started.elapsed().as_millis() >= 4);
    }
}

