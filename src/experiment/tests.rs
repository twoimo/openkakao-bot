//! Unit tests for the experiment runner, provider abstraction, and local
//! quality scorer. Every test uses a fake provider — no real network/LLM call
//! ever happens.

use super::*;
use std::cell::RefCell;
use std::collections::HashMap;

/// How a fake provider should behave for one (model, question) pair.
#[derive(Debug, Clone)]
enum FakeBehavior {
    /// Return an answer with the given text and reported latency.
    Answer { text: String, latency_ms: u64 },
    /// Fail generation with a reason.
    Fail(String),
    /// Report a timeout.
    Timeout,
}

/// A fully in-memory provider. `list` controls discovery; `behaviors` controls
/// per-(model, question) generation. Never touches the network.
struct FakeProvider {
    id: String,
    list: Result<Vec<ModelId>, ProviderError>,
    behaviors: HashMap<(String, String), FakeBehavior>,
    default_behavior: FakeBehavior,
    calls: RefCell<usize>,
}

impl FakeProvider {
    fn new(id: &str, models: &[&str]) -> Self {
        Self {
            id: id.to_string(),
            list: Ok(models.iter().map(|m| ModelId::new(*m)).collect()),
            behaviors: HashMap::new(),
            default_behavior: FakeBehavior::Answer {
                text: "네 좋아요".to_string(),
                latency_ms: 100,
            },
            calls: RefCell::new(0),
        }
    }

    fn failing_list(id: &str, reason: &str) -> Self {
        let mut p = Self::new(id, &[]);
        p.list = Err(ProviderError::ListFailed(reason.to_string()));
        p
    }

    fn set(&mut self, model: &str, question_id: &str, behavior: FakeBehavior) {
        self.behaviors
            .insert((model.to_string(), question_id.to_string()), behavior);
    }
}

impl Provider for FakeProvider {
    fn id(&self) -> &str {
        &self.id
    }

    fn list_models(&self) -> Result<Vec<ModelId>, ProviderError> {
        self.list.clone()
    }

    fn generate(&self, model: &ModelId, prompt: &Prompt) -> Result<Answer, ProviderError> {
        *self.calls.borrow_mut() += 1;
        let behavior = self
            .behaviors
            .get(&(model.as_str().to_string(), prompt.question_id.clone()))
            .cloned()
            .unwrap_or_else(|| self.default_behavior.clone());
        match behavior {
            FakeBehavior::Answer { text, latency_ms } => Ok(Answer { text, latency_ms }),
            FakeBehavior::Fail(reason) => Err(ProviderError::GenerateFailed(reason)),
            FakeBehavior::Timeout => Err(ProviderError::Timeout),
        }
    }
}

// ---- discovery (Task 6, R4.1) ---------------------------------------------

#[test]
fn discovery_includes_only_successfully_listed_models() {
    let providers: Vec<Box<dyn Provider>> = vec![
        Box::new(FakeProvider::new("alpha", &["m1", "m2"])),
        Box::new(FakeProvider::failing_list("beta", "no auth")),
        Box::new(FakeProvider::new("gamma", &["m3"])),
    ];
    let targets = discover_targets(&providers);
    // beta contributed nothing because its listing failed.
    assert_eq!(targets.len(), 3);
    assert!(targets.iter().all(|t| t.provider != "beta"));
    let labels: Vec<String> = targets.iter().map(Target::label).collect();
    assert!(labels.contains(&"alpha/m1".to_string()));
    assert!(labels.contains(&"alpha/m2".to_string()));
    assert!(labels.contains(&"gamma/m3".to_string()));
}

// ---- question set (R4.3) ---------------------------------------------------

#[test]
fn default_question_set_has_at_least_ten_unique_questions() {
    let questions = default_question_set();
    assert!(questions.len() >= 10, "R4.3 requires >= 10 questions");
    let mut ids: Vec<&str> = questions.iter().map(|q| q.id.as_str()).collect();
    ids.sort();
    ids.dedup();
    assert_eq!(ids.len(), questions.len(), "question ids must be unique");
}

// ---- local quality scorer (R4.5) ------------------------------------------

#[test]
fn scorer_rewards_matching_honorific_tone() {
    let target = StyleTarget {
        honorific: Honorific::Honorific,
        formality: Formality::Informal,
        target_len: 8,
    };
    let matching = score_reply(&target, "네 곧 갈게요");
    let mismatching = score_reply(&target, "어 그래 이따 봐");
    assert!(
        matching > mismatching,
        "honorific reply ({matching}) should outscore casual reply ({mismatching})"
    );
}

#[test]
fn scorer_returns_zero_for_empty_reply() {
    assert_eq!(score_reply(&StyleTarget::default(), "   "), 0);
}

#[test]
fn scorer_is_bounded_0_to_100() {
    let target = StyleTarget::default();
    for reply in ["네", "네 좋아요", "그래 이따 봐", &"요".repeat(500)] {
        let score = score_reply(&target, reply);
        assert!(score <= 100);
    }
}

#[test]
fn style_target_from_profile_uses_derivation() {
    let profile = StyleProfile {
        chat: "c".into(),
        source: "s".into(),
        user: "u".into(),
        sample_count: 10,
        average_character_length: 12.0,
        median_character_length: 10.0,
        p90_character_length: 20.0,
        casual_ending_count: 0,
        casual_ending_counts_json: "{}".into(),
        question_count: 0,
        emoji_count: 0,
        punctuation_count: 0,
        common_endings_json: "{\"요\":10}".into(),
        common_tokens_json: "{}".into(),
        policy_version: "v1".into(),
    };
    let target = StyleTarget::from_style_profile(&profile);
    assert_eq!(target.honorific, Honorific::Honorific);
    assert_eq!(target.target_len, 12);
}

// ---- run (Task 6.1, R4.2/R4.3/R4.4/R4.5) ----------------------------------

#[test]
fn run_with_no_targets_does_not_start() {
    let providers: Vec<Box<dyn Provider>> =
        vec![Box::new(FakeProvider::failing_list("alpha", "down"))];
    let runner = StyleExperimentRunner::with_defaults(StyleTarget::default());
    let err = runner.run("v1", &providers).unwrap_err();
    assert!(matches!(err, ExperimentError::NoTargets));
}

#[test]
fn run_measures_every_target_and_question() {
    let providers: Vec<Box<dyn Provider>> = vec![
        Box::new(FakeProvider::new("alpha", &["m1"])),
        Box::new(FakeProvider::new("beta", &["m2", "m3"])),
    ];
    let questions = default_question_set();
    let runner = StyleExperimentRunner::new(questions.clone(), StyleTarget::default());
    let results = runner.run("v1", &providers).unwrap();
    // 3 targets x N questions.
    assert_eq!(results.len(), 3 * questions.len());
    assert!(results.iter().all(|r| r.prompt_version == "v1"));
    assert!(results.iter().all(|r| r.outcome.is_ok()));
    assert!(results.iter().all(|r| r.latency_ms == 100));
}

#[test]
fn run_reclassifies_over_budget_latency_as_timeout() {
    let mut provider = FakeProvider::new("alpha", &["m1"]);
    let qid = default_question_set()[0].id.clone();
    provider.set(
        "m1",
        &qid,
        FakeBehavior::Answer {
            text: "느린 답".into(),
            latency_ms: TIMEOUT_MS + 1,
        },
    );
    let providers: Vec<Box<dyn Provider>> = vec![Box::new(provider)];
    let runner = StyleExperimentRunner::with_defaults(StyleTarget::default());
    let results = runner.run("v1", &providers).unwrap();
    let slow = results.iter().find(|r| r.question_id == qid).unwrap();
    assert_eq!(slow.outcome, Outcome::Timeout);
    assert_eq!(slow.quality_score, 0);
}

// ---- failure isolation (Task 6.2, R4.8) -----------------------------------

#[test]
fn run_continues_past_failures_and_keeps_successes() {
    let mut provider = FakeProvider::new("alpha", &["m1"]);
    let questions = default_question_set();
    // Fail the second question, time out the third; the rest succeed.
    provider.set("m1", &questions[1].id, FakeBehavior::Fail("boom".into()));
    provider.set("m1", &questions[2].id, FakeBehavior::Timeout);
    let providers: Vec<Box<dyn Provider>> = vec![Box::new(provider)];
    let runner = StyleExperimentRunner::new(questions.clone(), StyleTarget::default());
    let results = runner.run("v1", &providers).unwrap();

    assert_eq!(results.len(), questions.len());
    let failed = results.iter().find(|r| r.question_id == questions[1].id).unwrap();
    assert!(matches!(failed.outcome, Outcome::Failed(_)));
    let timed = results.iter().find(|r| r.question_id == questions[2].id).unwrap();
    assert_eq!(timed.outcome, Outcome::Timeout);
    // Everything else stayed successful.
    let ok_count = results.iter().filter(|r| r.outcome.is_ok()).count();
    assert_eq!(ok_count, questions.len() - 2);
}

// ---- compare (Task 6.2, R4.6/R4.7) ----------------------------------------

#[test]
fn compare_single_run_lines_up_answers_per_question() {
    let providers: Vec<Box<dyn Provider>> = vec![
        Box::new(FakeProvider::new("alpha", &["m1"])),
        Box::new(FakeProvider::new("beta", &["m2"])),
    ];
    let questions = default_question_set();
    let runner = StyleExperimentRunner::new(questions.clone(), StyleTarget::default());
    let run = runner.run("v1", &providers).unwrap();
    let table = runner.compare(std::slice::from_ref(&run));

    // One row per (question, target); 2 targets => 2 rows per question.
    assert_eq!(table.rows.len(), questions.len() * 2);
    assert!(table.rows.iter().all(|row| row.cells.len() == 1));
    assert!(table
        .rows
        .iter()
        .all(|row| row.cells[0].answer.is_some()));
}

#[test]
fn compare_two_runs_aligns_versions_side_by_side() {
    let providers: Vec<Box<dyn Provider>> = vec![Box::new(FakeProvider::new("alpha", &["m1"]))];
    let questions = default_question_set();
    let runner = StyleExperimentRunner::new(questions.clone(), StyleTarget::default());
    let run_v1 = runner.run("v1", &providers).unwrap();
    let run_v2 = runner.run("v2", &providers).unwrap();

    let table = runner.compare(&[run_v1, run_v2]);
    assert_eq!(table.rows.len(), questions.len());
    for row in &table.rows {
        assert_eq!(row.cells.len(), 2);
        assert_eq!(row.cells[0].prompt_version, "v1");
        assert_eq!(row.cells[1].prompt_version, "v2");
    }
}

// ---- persistence (R4.3/R4.5) ----------------------------------------------

#[test]
fn store_records_and_reloads_by_prompt_version() {
    let providers: Vec<Box<dyn Provider>> = vec![Box::new(FakeProvider::new("alpha", &["m1"]))];
    let questions = default_question_set();
    let runner = StyleExperimentRunner::new(questions.clone(), StyleTarget::default());
    let run = runner.run("v1", &providers).unwrap();

    let mut store = SqliteExperimentStore::open_in_memory().unwrap();
    store.record(&run).unwrap();

    let loaded = store.load_by_prompt_version("v1").unwrap();
    assert_eq!(loaded.len(), questions.len());
    // The answer text is never persisted (redacted-logging spirit).
    assert!(loaded.iter().all(|r| r.answer.is_none()));
    assert!(loaded.iter().all(|r| r.prompt_version == "v1"));
    // A different version returns nothing.
    assert!(store.load_by_prompt_version("v2").unwrap().is_empty());
}

#[test]
fn store_persists_failure_and_timeout_outcomes() {
    let mut provider = FakeProvider::new("alpha", &["m1"]);
    let questions = default_question_set();
    provider.set("m1", &questions[0].id, FakeBehavior::Fail("bad thing".into()));
    provider.set("m1", &questions[1].id, FakeBehavior::Timeout);
    let providers: Vec<Box<dyn Provider>> = vec![Box::new(provider)];
    let runner = StyleExperimentRunner::new(questions.clone(), StyleTarget::default());
    let run = runner.run("v3", &providers).unwrap();

    let mut store = SqliteExperimentStore::open_in_memory().unwrap();
    store.record(&run).unwrap();
    let loaded = store.load_by_prompt_version("v3").unwrap();

    let failed = loaded.iter().find(|r| r.question_id == questions[0].id).unwrap();
    assert!(matches!(failed.outcome, Outcome::Failed(_)));
    let timed = loaded.iter().find(|r| r.question_id == questions[1].id).unwrap();
    assert_eq!(timed.outcome, Outcome::Timeout);
}

#[test]
fn outcome_db_string_roundtrips() {
    for outcome in [
        Outcome::Ok,
        Outcome::Timeout,
        Outcome::Failed("reason".into()),
    ] {
        assert_eq!(Outcome::parse(&outcome.as_db_string()), outcome);
    }
}
