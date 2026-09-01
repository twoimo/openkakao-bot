//! Experiment runner + provider abstraction (R4).
//!
//! This module drives the quality/speed experiment across every available LLM.
//! It is split into three cooperating parts:
//!
//! 1. **Provider abstraction (Task 6, R4.1).** A [`Provider`] can list its
//!    models and generate an answer. [`GjcProvider`] wraps the existing
//!    `gjc --list-models` / `gjc setup provider` subprocess path so real runs
//!    use the same runner the auto-reply worker uses. [`discover_targets`]
//!    queries every provider and includes **only** the models it could
//!    successfully list — a provider whose listing fails contributes no targets.
//!
//! 2. **Experiment runner (Task 6.1/6.2, R4.2–R4.8).** [`StyleExperimentRunner`]
//!    runs a fixed question set (>= 10 questions) against every discovered
//!    target, records `latency_ms`, a local `quality_score` (0..100) and the
//!    `prompt_version`, compares runs side by side, and continues past
//!    per-target failures/timeouts (>60s) instead of aborting the batch.
//!
//! 3. **Local quality scorer (R4.5).** [`score_reply`] scores a generated reply
//!    against a [`StyleTarget`] derived from 최연우's style profile — reusing the
//!    honorific/formality derivation in [`crate::context`]. There is **no**
//!    external judge LLM; scoring is deterministic and offline.
//!
//! **Safety / testability.** No type in this module performs a product send.
//! The runner is generic over injected [`Provider`]s, so tests inject fake
//! providers and never make a real network/LLM call. Persisted results carry
//! only redacted metrics (no answer text, no prompts) in the `experiment_result`
//! table.

use std::collections::BTreeMap;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use rusqlite::{params, Connection};
use thiserror::Error;

use crate::context::{
    self, derive_honorific_formality, is_contraction_token, Formality, Honorific, StyleProfile,
    StyleSignals,
};

/// Any single answer generation that takes longer than this is recorded as a
/// timeout and excluded from success metrics (R4.8).
pub const TIMEOUT_MS: u64 = 60_000;

// ---------------------------------------------------------------------------
// Provider abstraction (Task 6, R4.1)
// ---------------------------------------------------------------------------

/// A model identifier as reported by a provider (for example
/// `google-antigravity/gemini-3.7-flash-tiered`).
#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct ModelId(pub String);

impl ModelId {
    /// Wrap a raw model string.
    pub fn new(id: impl Into<String>) -> Self {
        ModelId(id.into())
    }

    /// The raw model string.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// A fully-rendered prompt handed to a provider. `version` is the prompt version
/// being experimented with (R4.3, R4.7); `text` is the concrete text sent.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Prompt {
    /// The prompt version label (e.g. `v1`, `v2`).
    pub version: String,
    /// The question this prompt answers.
    pub question_id: String,
    /// The concrete prompt text.
    pub text: String,
}

/// A generated answer plus the measured round-trip latency (R4.4).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Answer {
    /// The generated reply text (kept in memory for the comparison table; never
    /// persisted to the results table).
    pub text: String,
    /// Milliseconds from request to response completion.
    pub latency_ms: u64,
}

/// Why a provider call failed. Each carries a short, plain-language reason.
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum ProviderError {
    /// The provider's model listing failed (provider excluded from targets).
    #[error("모델 목록을 불러오지 못했어요: {0}")]
    ListFailed(String),
    /// A single answer generation failed.
    #[error("답변을 만들지 못했어요: {0}")]
    GenerateFailed(String),
    /// A single answer generation exceeded the allowed time (R4.8).
    #[error("답변을 만드는 데 시간이 너무 오래 걸렸어요")]
    Timeout,
}

/// A source of models and generated answers. Real code wraps a CLI runner;
/// tests inject a fake so no live network/LLM call happens.
pub trait Provider {
    /// Stable provider id (used to group results, e.g. `gjc`).
    fn id(&self) -> &str;

    /// List the models this provider can serve. On success every returned model
    /// becomes an experiment target; on failure the provider contributes none
    /// (R4.1).
    fn list_models(&self) -> Result<Vec<ModelId>, ProviderError>;

    /// Generate one 최연우-style answer for `prompt` from `model`.
    fn generate(&self, model: &ModelId, prompt: &Prompt) -> Result<Answer, ProviderError>;
}

/// One discovered experiment target: a specific model on a specific provider.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub struct Target {
    /// The provider id that owns the model.
    pub provider: String,
    /// The model to exercise.
    pub model: ModelId,
}

impl Target {
    /// A stable `provider/model` label used in comparison tables.
    pub fn label(&self) -> String {
        format!("{}/{}", self.provider, self.model.as_str())
    }
}

/// Query every provider and collect the models that were **successfully**
/// listed (R4.1). A provider whose [`Provider::list_models`] returns an error is
/// skipped entirely, so only reachable LLMs become targets.
pub fn discover_targets(providers: &[Box<dyn Provider>]) -> Vec<Target> {
    let mut targets = Vec::new();
    for provider in providers {
        match provider.list_models() {
            Ok(models) => {
                for model in models {
                    targets.push(Target {
                        provider: provider.id().to_string(),
                        model,
                    });
                }
            }
            // Listing failed → this provider contributes no targets (R4.1).
            Err(_) => continue,
        }
    }
    targets
}

// ---------------------------------------------------------------------------
// gjc-backed provider (real path; never exercised by tests)
// ---------------------------------------------------------------------------

/// A [`Provider`] that shells out to the existing `gjc` runner, reusing the
/// `gjc --list-models` and `gjc setup provider` code paths. This is the real
/// production provider; tests never construct it (they inject fakes) so no live
/// LLM call happens under test.
pub struct GjcProvider {
    id: String,
    runner_path: std::path::PathBuf,
}

impl GjcProvider {
    /// Wrap a `gjc` runner executable at `runner_path`.
    pub fn new(id: impl Into<String>, runner_path: impl Into<std::path::PathBuf>) -> Self {
        Self {
            id: id.into(),
            runner_path: runner_path.into(),
        }
    }
}

impl Provider for GjcProvider {
    fn id(&self) -> &str {
        &self.id
    }

    fn list_models(&self) -> Result<Vec<ModelId>, ProviderError> {
        use std::process::{Command, Stdio};
        // Reuse the `gjc --list-models` path. `gjc setup provider` is the
        // provider-configuration path; listing is what discovery needs.
        let output = Command::new(&self.runner_path)
            .arg("--list-models")
            .stdin(Stdio::null())
            .output()
            .map_err(|e| ProviderError::ListFailed(e.to_string()))?;
        if !output.status.success() {
            return Err(ProviderError::ListFailed(
                String::from_utf8_lossy(&output.stderr).trim().to_string(),
            ));
        }
        let stdout = String::from_utf8_lossy(&output.stdout);
        let models: Vec<ModelId> = stdout
            .lines()
            .map(str::trim)
            .filter(|line| !line.is_empty())
            .map(ModelId::new)
            .collect();
        if models.is_empty() {
            return Err(ProviderError::ListFailed("no models listed".to_string()));
        }
        Ok(models)
    }

    fn generate(&self, model: &ModelId, prompt: &Prompt) -> Result<Answer, ProviderError> {
        use std::process::{Command, Stdio};
        let start = Instant::now();
        let output = Command::new(&self.runner_path)
            .args([
                "-p",
                "--no-session",
                "--no-rules",
                "--no-lsp",
                "--no-title",
                "--no-tools",
                "--mode",
                "text",
                "--model",
                model.as_str(),
                &prompt.text,
            ])
            .stdin(Stdio::null())
            .output()
            .map_err(|e| ProviderError::GenerateFailed(e.to_string()))?;
        let latency_ms = start.elapsed().as_millis() as u64;
        if !output.status.success() {
            return Err(ProviderError::GenerateFailed(
                String::from_utf8_lossy(&output.stderr).trim().to_string(),
            ));
        }
        Ok(Answer {
            text: String::from_utf8_lossy(&output.stdout).trim().to_string(),
            latency_ms,
        })
    }
}

// ---------------------------------------------------------------------------
// Fixed question set (R4.3)
// ---------------------------------------------------------------------------

/// One fixed test question presented to every target LLM.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Question {
    /// Stable id used to group answers across targets and runs.
    pub id: String,
    /// The partner message 최연우 would reply to.
    pub text: String,
}

/// The fixed question set. Requirement R4.3 mandates at least 10 questions; this
/// returns 12 stable, everyday Korean prompts so every run and comparison uses
/// exactly the same inputs.
pub fn default_question_set() -> Vec<Question> {
    const QUESTIONS: [&str; 12] = [
        "오늘 회의 몇 시에 시작해요?",
        "점심 같이 먹을래?",
        "이 코드 리뷰 좀 부탁해도 될까요?",
        "주말에 시간 괜찮아?",
        "그 뉴스 봤어요?",
        "내일 일정 어떻게 돼?",
        "이번 프로젝트 마감이 언제예요?",
        "커피 한잔 어때?",
        "저번에 말한 링크 다시 보내줄 수 있어요?",
        "지금 통화 가능해?",
        "발표 자료 준비 다 됐어요?",
        "오늘 저녁에 뭐 해?",
    ];
    QUESTIONS
        .iter()
        .enumerate()
        .map(|(index, text)| Question {
            id: format!("q{:02}", index + 1),
            text: (*text).to_string(),
        })
        .collect()
}

// ---------------------------------------------------------------------------
// Local quality scorer (R4.5) — no external judge LLM
// ---------------------------------------------------------------------------

/// The target style a generated reply is scored against. Derived from 최연우's
/// style profile so scoring measures how well a reply matches the learned tone.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct StyleTarget {
    /// Target honorific (존댓말/반말).
    pub honorific: Honorific,
    /// Target formality (격식/비격식).
    pub formality: Formality,
    /// Target reply length in characters (used for the conciseness component).
    pub target_len: usize,
}

impl Default for StyleTarget {
    /// A sensible default for 최연우: 해요체 — polite but informal, medium length.
    fn default() -> Self {
        StyleTarget {
            honorific: Honorific::Honorific,
            formality: Formality::Informal,
            target_len: 30,
        }
    }
}

impl StyleTarget {
    /// Derive a scoring target from a recipient/global style profile (R4.5),
    /// reusing the same honorific/formality derivation the dataset builder uses.
    pub fn from_style_profile(profile: &StyleProfile) -> Self {
        let (honorific, formality) =
            context::derive_style_profile_honorific_formality(profile);
        let target_len = profile.average_character_length.round().max(1.0) as usize;
        StyleTarget {
            honorific,
            formality,
            target_len,
        }
    }
}

/// True when `ch` sits in a common emoji block. Used only for a coarse emoji
/// count in the local scorer.
fn is_emoji_char(ch: char) -> bool {
    let cp = ch as u32;
    (0x1F300..=0x1FAFF).contains(&cp)
        || (0x2600..=0x27BF).contains(&cp)
        || (0x1F000..=0x1F0FF).contains(&cp)
        || cp == 0x2764
}

/// Classify the trailing ending of a single sentence segment into a style
/// signal. Mirrors [`context::classify_style_ending_class`] but works on a raw
/// reply token (which carries the whole ending inline) via suffix matching.
fn accumulate_segment_ending(token: &str, signals: &mut StyleSignals) {
    let trimmed = token.trim_end_matches(|c: char| {
        c.is_whitespace() || matches!(c, '.' | '!' | '?' | '~' | '…' | '。' | '！' | '？')
    });
    if trimmed.is_empty() {
        return;
    }
    if is_contraction_token(trimmed) {
        signals.contraction_count += 1;
        return;
    }
    if trimmed.ends_with("습니다") || trimmed.ends_with("합니다") || trimmed.ends_with("됩니다") {
        signals.deferential_ending_count += 1;
    } else if trimmed.ends_with('요') || trimmed.ends_with('죠') {
        signals.honorific_ending_count += 1;
    } else {
        signals.casual_ending_count += 1;
    }
}

/// Build rule-based style signals from a generated reply, so the same
/// [`derive_honorific_formality`] logic used for the style profile can classify
/// the reply's tone.
fn reply_signals(reply: &str) -> StyleSignals {
    let mut signals = StyleSignals::default();

    let segments: Vec<&str> = reply
        .split(|c: char| matches!(c, '.' | '!' | '?' | '\n' | '。' | '！' | '？' | '…'))
        .map(str::trim)
        .filter(|segment| !segment.is_empty())
        .collect();
    signals.sample_count = segments.len().max(1);

    for segment in &segments {
        if let Some(last) = segment.split_whitespace().last() {
            accumulate_segment_ending(last, &mut signals);
        }
    }

    signals.emoji_count = reply.chars().filter(|c| is_emoji_char(*c)).count();
    signals.punctuation_count = reply
        .chars()
        .filter(|c| matches!(c, '!' | '?' | '~' | '…'))
        .count();
    for token in reply.split_whitespace() {
        if is_contraction_token(token) {
            signals.contraction_count += 1;
        }
    }
    signals
}

/// Score a generated reply against a [`StyleTarget`] on a 0..100 scale (R4.5).
///
/// The score combines four local, deterministic components:
/// * honorific match (40): reply's derived 존댓말/반말 matches the target,
/// * formality match (20): reply's derived 격식/비격식 matches the target,
/// * conciseness (25): how close the reply length is to the target length,
/// * format compliance (15): single-line, non-empty, not excessively long.
///
/// An empty reply scores 0. No external LLM is consulted.
pub fn score_reply(target: &StyleTarget, reply: &str) -> u8 {
    let trimmed = reply.trim();
    if trimmed.is_empty() {
        return 0;
    }

    let signals = reply_signals(reply);
    let (honorific, formality) = derive_honorific_formality(&signals);

    let mut score: u32 = 0;
    if honorific == target.honorific {
        score += 40;
    }
    if formality == target.formality {
        score += 20;
    }

    // Conciseness: full marks when the length matches; linearly down to zero as
    // the reply drifts one full target-length away.
    let len = reply.chars().count() as f64;
    let target_len = target.target_len.max(1) as f64;
    let drift = ((len - target_len).abs() / target_len).min(1.0);
    score += (25.0 * (1.0 - drift)).round() as u32;

    // Format compliance: a single, reasonably short line is ideal.
    let mut format = 15u32;
    if reply.contains('\n') {
        format = format.saturating_sub(8);
    }
    if len > 200.0 {
        format = format.saturating_sub(7);
    }
    score += format;

    score.min(100) as u8
}

// ---------------------------------------------------------------------------
// Experiment results + runner (Task 6.1/6.2)
// ---------------------------------------------------------------------------

/// The outcome of a single (target, question) generation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Outcome {
    /// The answer was generated within budget and scored.
    Ok,
    /// Generation failed; the string is a short reason (R4.8).
    Failed(String),
    /// Generation exceeded [`TIMEOUT_MS`] (R4.8).
    Timeout,
}

impl Outcome {
    /// The stable string persisted in the `outcome` column
    /// (`ok` | `failed:<reason>` | `timeout`).
    pub fn as_db_string(&self) -> String {
        match self {
            Outcome::Ok => "ok".to_string(),
            Outcome::Failed(reason) => format!("failed:{reason}"),
            Outcome::Timeout => "timeout".to_string(),
        }
    }

    /// Parse an `outcome` column value back into an [`Outcome`].
    pub fn parse(value: &str) -> Outcome {
        if value == "ok" {
            Outcome::Ok
        } else if value == "timeout" {
            Outcome::Timeout
        } else if let Some(reason) = value.strip_prefix("failed:") {
            Outcome::Failed(reason.to_string())
        } else {
            Outcome::Failed(value.to_string())
        }
    }

    /// True only for [`Outcome::Ok`].
    pub fn is_ok(&self) -> bool {
        matches!(self, Outcome::Ok)
    }
}

/// One recorded experiment measurement (R4.3, R4.4, R4.5). Stored rows carry
/// only these redacted metrics; `answer` is kept in memory for the side-by-side
/// comparison table and is never persisted.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExperimentResult {
    /// Groups every measurement of one `run` invocation.
    pub run_id: String,
    /// Provider id.
    pub provider: String,
    /// Model id string.
    pub model: String,
    /// Question id.
    pub question_id: String,
    /// Measured round-trip latency in ms (R4.4).
    pub latency_ms: u64,
    /// Local quality score 0..100 (R4.5). Zero for failures/timeouts.
    pub quality_score: u8,
    /// The prompt version this measurement used (R4.3, R4.7).
    pub prompt_version: String,
    /// The generation outcome (R4.8).
    pub outcome: Outcome,
    /// The generated answer text (in memory only; never persisted).
    pub answer: Option<String>,
}

impl ExperimentResult {
    /// A stable `provider/model` label.
    pub fn target_label(&self) -> String {
        format!("{}/{}", self.provider, self.model)
    }
}

/// Errors the experiment runner can return, each with a plain-language message.
#[derive(Debug, Error)]
pub enum ExperimentError {
    /// No target LLM could be discovered, so the experiment does not start
    /// (R4.2).
    #[error(
        "실험할 수 있는 LLM이 하나도 없어요. 공급자와 모델 설정을 확인한 뒤 다시 시도해 주세요."
    )]
    NoTargets,
    /// Persisting the results failed.
    #[error("실험 결과를 저장하지 못했어요: {0}")]
    StoreFailure(String),
}

/// One cell of a comparison table: a target's measurement for a question in one
/// run.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ComparisonCell {
    /// The prompt version of the run this cell came from.
    pub prompt_version: String,
    /// Quality score, if the generation succeeded.
    pub quality_score: Option<u8>,
    /// Latency in ms, if measured.
    pub latency_ms: Option<u64>,
    /// The answer text, if available (only present for in-memory runs).
    pub answer: Option<String>,
    /// The outcome for this cell.
    pub outcome: Outcome,
}

/// One row of a comparison table: a (question, target) pair with one cell per
/// run being compared.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ComparisonRow {
    /// The question id.
    pub question_id: String,
    /// The `provider/model` target label.
    pub target: String,
    /// One cell per run passed to [`ExperimentRunner::compare`], in order.
    pub cells: Vec<ComparisonCell>,
}

/// A side-by-side comparison table. With one run it lines up every target's
/// answer to the same question (R4.6); with two runs it lines up quality and
/// speed across prompt versions (R4.7).
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct ComparisonTable {
    /// Rows ordered by question id then target label.
    pub rows: Vec<ComparisonRow>,
}

/// The experiment runner interface (R4.2–R4.8).
pub trait ExperimentRunner {
    /// Run the fixed question set against every discovered target using
    /// `prompt_version`, recording latency, quality, and outcome. Returns
    /// [`ExperimentError::NoTargets`] without starting if discovery is empty
    /// (R4.2). A per-target failure/timeout is recorded and the batch continues
    /// (R4.8).
    fn run(
        &self,
        prompt_version: &str,
        providers: &[Box<dyn Provider>],
    ) -> Result<Vec<ExperimentResult>, ExperimentError>;

    /// Build a comparison table from one or more runs (R4.6, R4.7).
    fn compare(&self, runs: &[Vec<ExperimentResult>]) -> ComparisonTable;
}

/// The default runner: a fixed question set scored against a [`StyleTarget`].
pub struct StyleExperimentRunner {
    questions: Vec<Question>,
    style: StyleTarget,
}

impl StyleExperimentRunner {
    /// Build a runner with an explicit question set and scoring target.
    pub fn new(questions: Vec<Question>, style: StyleTarget) -> Self {
        Self { questions, style }
    }

    /// Build a runner with the fixed [`default_question_set`] and a scoring
    /// target.
    pub fn with_defaults(style: StyleTarget) -> Self {
        Self::new(default_question_set(), style)
    }

    /// The question set this runner uses.
    pub fn questions(&self) -> &[Question] {
        &self.questions
    }

    /// Render the prompt for a question at a given version. The version tag is
    /// embedded so a re-run with an improved prompt produces a materially
    /// different prompt while the question stays fixed (R4.7).
    fn build_prompt(&self, version: &str, question: &Question) -> Prompt {
        let honorific = match self.style.honorific {
            Honorific::Honorific => "존댓말",
            Honorific::Casual => "반말",
        };
        let formality = match self.style.formality {
            Formality::Formal => "격식체",
            Formality::Informal => "비격식체",
        };
        let text = format!(
            "[{version}] 최연우의 말투({honorific}, {formality})로 짧게 한 문장으로 답해줘.\n질문: {}",
            question.text
        );
        Prompt {
            version: version.to_string(),
            question_id: question.id.clone(),
            text,
        }
    }

    /// Classify one provider generation into a recorded [`ExperimentResult`].
    fn measure(
        &self,
        run_id: &str,
        prompt_version: &str,
        target: &Target,
        question: &Question,
        result: Result<Answer, ProviderError>,
    ) -> ExperimentResult {
        let (latency_ms, quality_score, outcome, answer) = match result {
            Ok(answer) if answer.latency_ms > TIMEOUT_MS => {
                // Reported latency over budget → recorded as a timeout (R4.8).
                (answer.latency_ms, 0, Outcome::Timeout, None)
            }
            Ok(answer) => {
                let score = score_reply(&self.style, &answer.text);
                (answer.latency_ms, score, Outcome::Ok, Some(answer.text))
            }
            Err(ProviderError::Timeout) => (TIMEOUT_MS, 0, Outcome::Timeout, None),
            Err(err) => (0, 0, Outcome::Failed(short_reason(&err)), None),
        };
        ExperimentResult {
            run_id: run_id.to_string(),
            provider: target.provider.clone(),
            model: target.model.as_str().to_string(),
            question_id: question.id.clone(),
            latency_ms,
            quality_score,
            prompt_version: prompt_version.to_string(),
            outcome,
            answer,
        }
    }
}

/// A short, single-token-ish reason for a provider error, suitable for the
/// `failed:<reason>` outcome string.
fn short_reason(err: &ProviderError) -> String {
    match err {
        ProviderError::ListFailed(_) => "list".to_string(),
        ProviderError::GenerateFailed(reason) => {
            let cleaned: String = reason
                .chars()
                .map(|c| if c.is_whitespace() { '_' } else { c })
                .take(40)
                .collect();
            if cleaned.is_empty() {
                "generate".to_string()
            } else {
                cleaned
            }
        }
        ProviderError::Timeout => "timeout".to_string(),
    }
}

/// A run id derived from the prompt version and the current time. Only used to
/// group a batch; nothing asserts on its exact value.
fn new_run_id(prompt_version: &str) -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    format!("{prompt_version}-{nanos}")
}

impl ExperimentRunner for StyleExperimentRunner {
    fn run(
        &self,
        prompt_version: &str,
        providers: &[Box<dyn Provider>],
    ) -> Result<Vec<ExperimentResult>, ExperimentError> {
        let targets = discover_targets(providers);
        if targets.is_empty() {
            // Do not start with zero targets (R4.2).
            return Err(ExperimentError::NoTargets);
        }

        let run_id = new_run_id(prompt_version);
        let mut results = Vec::with_capacity(targets.len() * self.questions.len());

        for target in &targets {
            // Every discovered target has a provider that listed it.
            let provider = providers
                .iter()
                .find(|p| p.id() == target.provider)
                .expect("discovered target must map to a provider");
            for question in &self.questions {
                let prompt = self.build_prompt(prompt_version, question);
                // A single failure/timeout is recorded; the batch continues
                // (R4.8).
                let generated = provider.generate(&target.model, &prompt);
                results.push(self.measure(
                    &run_id,
                    prompt_version,
                    target,
                    question,
                    generated,
                ));
            }
        }

        Ok(results)
    }

    fn compare(&self, runs: &[Vec<ExperimentResult>]) -> ComparisonTable {
        build_comparison(runs)
    }
}

/// Build a comparison table from one or more runs. Grouped by (question, target)
/// with one cell per run in the order given.
pub fn build_comparison(runs: &[Vec<ExperimentResult>]) -> ComparisonTable {
    // key: (question_id, target_label) -> per-run cells (indexed by run index).
    let mut grouped: BTreeMap<(String, String), Vec<Option<ComparisonCell>>> = BTreeMap::new();

    for (run_index, run) in runs.iter().enumerate() {
        for result in run {
            let key = (result.question_id.clone(), result.target_label());
            let cells = grouped
                .entry(key)
                .or_insert_with(|| vec![None; runs.len()]);
            let (quality, latency) = if result.outcome.is_ok() {
                (Some(result.quality_score), Some(result.latency_ms))
            } else {
                (None, Some(result.latency_ms))
            };
            cells[run_index] = Some(ComparisonCell {
                prompt_version: result.prompt_version.clone(),
                quality_score: quality,
                latency_ms: latency,
                answer: result.answer.clone(),
                outcome: result.outcome.clone(),
            });
        }
    }

    let rows = grouped
        .into_iter()
        .map(|((question_id, target), cells)| {
            let cells = cells
                .into_iter()
                .enumerate()
                .map(|(run_index, cell)| {
                    cell.unwrap_or(ComparisonCell {
                        prompt_version: runs
                            .get(run_index)
                            .and_then(|run| run.first())
                            .map(|r| r.prompt_version.clone())
                            .unwrap_or_default(),
                        quality_score: None,
                        latency_ms: None,
                        answer: None,
                        outcome: Outcome::Failed("missing".to_string()),
                    })
                })
                .collect();
            ComparisonRow {
                question_id,
                target,
                cells,
            }
        })
        .collect();

    ComparisonTable { rows }
}

// ---------------------------------------------------------------------------
// Persistence: experiment_result table
// ---------------------------------------------------------------------------

/// SQLite-backed store for the `experiment_result` table (R4.3, R4.5). Persists
/// only redacted metrics — never the generated answer or prompt text.
pub struct SqliteExperimentStore {
    conn: Connection,
}

impl SqliteExperimentStore {
    /// Wrap an existing connection, ensuring the schema is present.
    pub fn new(conn: Connection) -> Result<Self, ExperimentError> {
        ensure_schema(&conn).map_err(|e| ExperimentError::StoreFailure(e.to_string()))?;
        Ok(Self { conn })
    }

    /// Open (or create) a store at `path`.
    pub fn open(path: &std::path::Path) -> Result<Self, ExperimentError> {
        let conn =
            Connection::open(path).map_err(|e| ExperimentError::StoreFailure(e.to_string()))?;
        Self::new(conn)
    }

    /// Open an in-memory store. Primarily for tests.
    pub fn open_in_memory() -> Result<Self, ExperimentError> {
        let conn = Connection::open_in_memory()
            .map_err(|e| ExperimentError::StoreFailure(e.to_string()))?;
        Self::new(conn)
    }

    /// Persist a batch of results in a single transaction. A failure rolls back
    /// so partial rows are never left behind.
    pub fn record(&mut self, results: &[ExperimentResult]) -> Result<(), ExperimentError> {
        let tx = self
            .conn
            .transaction()
            .map_err(|e| ExperimentError::StoreFailure(e.to_string()))?;
        let outcome = (|| -> rusqlite::Result<()> {
            for result in results {
                tx.execute(
                    "INSERT INTO experiment_result(
                        run_id, provider, model, question_id,
                        latency_ms, quality_score, prompt_version, outcome
                     ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
                    params![
                        result.run_id,
                        result.provider,
                        result.model,
                        result.question_id,
                        result.latency_ms as i64,
                        result.quality_score as i64,
                        result.prompt_version,
                        result.outcome.as_db_string(),
                    ],
                )?;
            }
            Ok(())
        })();
        match outcome {
            Ok(()) => tx
                .commit()
                .map_err(|e| ExperimentError::StoreFailure(e.to_string())),
            Err(e) => {
                drop(tx);
                Err(ExperimentError::StoreFailure(e.to_string()))
            }
        }
    }

    /// Load every stored result for a given prompt version, ordered for stable
    /// comparison (R4.7). Loaded rows carry no answer text (never persisted).
    pub fn load_by_prompt_version(
        &self,
        prompt_version: &str,
    ) -> Result<Vec<ExperimentResult>, ExperimentError> {
        let mut stmt = self
            .conn
            .prepare(
                "SELECT run_id, provider, model, question_id,
                        latency_ms, quality_score, prompt_version, outcome
                 FROM experiment_result
                 WHERE prompt_version = ?1
                 ORDER BY question_id ASC, provider ASC, model ASC",
            )
            .map_err(|e| ExperimentError::StoreFailure(e.to_string()))?;
        let rows = stmt
            .query_map(params![prompt_version], |row| {
                Ok(ExperimentResult {
                    run_id: row.get::<_, String>(0)?,
                    provider: row.get::<_, String>(1)?,
                    model: row.get::<_, String>(2)?,
                    question_id: row.get::<_, String>(3)?,
                    latency_ms: row.get::<_, i64>(4)?.max(0) as u64,
                    quality_score: row.get::<_, i64>(5)?.clamp(0, 100) as u8,
                    prompt_version: row.get::<_, String>(6)?,
                    outcome: Outcome::parse(&row.get::<_, String>(7)?),
                    answer: None,
                })
            })
            .map_err(|e| ExperimentError::StoreFailure(e.to_string()))?;

        let mut results = Vec::new();
        for row in rows {
            results.push(row.map_err(|e| ExperimentError::StoreFailure(e.to_string()))?);
        }
        Ok(results)
    }
}

/// Create the `experiment_result` table if it does not exist.
fn ensure_schema(conn: &Connection) -> rusqlite::Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS experiment_result(
            id INTEGER PRIMARY KEY,
            run_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            question_id TEXT NOT NULL,
            latency_ms INTEGER NOT NULL CHECK(latency_ms >= 0),
            quality_score INTEGER NOT NULL CHECK(quality_score >= 0 AND quality_score <= 100),
            prompt_version TEXT NOT NULL,
            outcome TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_experiment_result_version
            ON experiment_result(prompt_version, question_id);",
    )
}

#[cfg(test)]
mod tests;
