//! Experiment failure-isolation property test (Task 6.3, Correctness Property 8).
//!
//! Verifies R4.8: when an arbitrary subset of a provider's per-question
//! generations fail or time out, the batch still **completes** (the runner
//! returns `Ok`), every failure/timeout is recorded, and the successful results
//! remain intact (correct outcome, preserved answer, and the locally-computed
//! quality score).
//!
//! The provider is a fake driven by a proptest-generated behavior matrix, so no
//! real network/LLM call ever happens.

use std::collections::HashMap;

use openkakao_cli::experiment::{
    default_question_set, score_reply, Answer, ExperimentRunner, ModelId, Outcome, Prompt,
    Provider, ProviderError, StyleExperimentRunner, StyleTarget,
};
use proptest::prelude::*;

/// A per-(model, question) behavior chosen by the property strategy.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Behavior {
    Ok,
    Fail,
    Timeout,
}

/// The fixed successful answer text, so a success has a deterministic score.
const OK_TEXT: &str = "네 알겠어요";
/// A small in-budget latency for successful answers.
const OK_LATENCY_MS: u64 = 50;

/// A fake provider driven entirely by an in-memory behavior matrix. Discovery
/// always succeeds (so every model becomes a target); generation follows the
/// matrix. No network is ever touched.
struct MatrixProvider {
    id: String,
    models: Vec<ModelId>,
    matrix: HashMap<(String, String), Behavior>,
}

impl Provider for MatrixProvider {
    fn id(&self) -> &str {
        &self.id
    }

    fn list_models(&self) -> Result<Vec<ModelId>, ProviderError> {
        Ok(self.models.clone())
    }

    fn generate(&self, model: &ModelId, prompt: &Prompt) -> Result<Answer, ProviderError> {
        let behavior = self
            .matrix
            .get(&(model.as_str().to_string(), prompt.question_id.clone()))
            .copied()
            .unwrap_or(Behavior::Ok);
        match behavior {
            Behavior::Ok => Ok(Answer {
                text: OK_TEXT.to_string(),
                latency_ms: OK_LATENCY_MS,
            }),
            Behavior::Fail => Err(ProviderError::GenerateFailed("simulated".to_string())),
            Behavior::Timeout => Err(ProviderError::Timeout),
        }
    }
}

/// Strategy for one behavior.
fn behavior_strategy() -> impl Strategy<Value = Behavior> {
    prop_oneof![
        Just(Behavior::Ok),
        Just(Behavior::Fail),
        Just(Behavior::Timeout),
    ]
}

proptest! {
    /// Correctness Property 8: for an arbitrary behavior matrix over
    /// `num_models` models and the fixed question set, the batch always
    /// completes, every cell is recorded, and successes stay intact.
    #[test]
    fn batch_completes_and_isolates_failures(
        num_models in 1usize..4,
        // One behavior per (model, question).
        flat in prop::collection::vec(behavior_strategy(), 1usize..(4 * 12 + 1)),
    ) {
        let questions = default_question_set();
        let models: Vec<ModelId> =
            (0..num_models).map(|i| ModelId::new(format!("m{i}"))).collect();

        // Fill the behavior matrix deterministically from the flat vector.
        let mut matrix = HashMap::new();
        let mut expected: HashMap<(String, String), Behavior> = HashMap::new();
        let mut idx = 0usize;
        for model in &models {
            for question in &questions {
                let behavior = flat[idx % flat.len()];
                idx += 1;
                matrix.insert((model.as_str().to_string(), question.id.clone()), behavior);
                expected.insert((model.as_str().to_string(), question.id.clone()), behavior);
            }
        }

        let provider = MatrixProvider {
            id: "fake".to_string(),
            models: models.clone(),
            matrix,
        };
        let providers: Vec<Box<dyn Provider>> = vec![Box::new(provider)];

        let target = StyleTarget::default();
        let runner = StyleExperimentRunner::new(questions.clone(), target);

        // The batch must complete regardless of how many cells fail.
        let results = runner.run("v1", &providers).expect("batch should complete");

        // Every (model, question) cell is present exactly once.
        prop_assert_eq!(results.len(), models.len() * questions.len());

        for result in &results {
            let key = (result.model.clone(), result.question_id.clone());
            let behavior = expected[&key];
            match behavior {
                Behavior::Ok => {
                    // Success stays intact: right outcome, preserved answer, and
                    // the locally-computed score.
                    prop_assert_eq!(&result.outcome, &Outcome::Ok);
                    prop_assert_eq!(result.answer.as_deref(), Some(OK_TEXT));
                    prop_assert_eq!(result.latency_ms, OK_LATENCY_MS);
                    prop_assert_eq!(result.quality_score, score_reply(&target, OK_TEXT));
                }
                Behavior::Fail => {
                    prop_assert!(matches!(result.outcome, Outcome::Failed(_)));
                    prop_assert_eq!(result.quality_score, 0);
                    prop_assert!(result.answer.is_none());
                }
                Behavior::Timeout => {
                    prop_assert_eq!(&result.outcome, &Outcome::Timeout);
                    prop_assert_eq!(result.quality_score, 0);
                    prop_assert!(result.answer.is_none());
                }
            }
        }

        // At least every successful cell keeps its full score, independent of
        // how many neighboring cells failed.
        let ok_results: Vec<_> = results.iter().filter(|r| r.outcome.is_ok()).collect();
        for ok in ok_results {
            prop_assert_eq!(ok.quality_score, score_reply(&target, OK_TEXT));
        }
    }
}
