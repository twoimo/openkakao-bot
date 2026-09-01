//! `okc experiment run --prompt <v>` and `okc experiment compare <v1> <v2>` —
//! run the LLM quality/speed experiment and compare prompt versions (R4).
//!
//! Real runs wrap the configured `gjc` runner in a [`GjcProvider`]; scoring is
//! done locally against 최연우's style profile with no external judge LLM. When
//! no provider is configured the run reports "no targets" in plain language
//! (R4.2) rather than contacting anything.

use anyhow::Result;
use serde_json::json;

use openkakao_cli::experiment::{
    build_comparison, ComparisonTable, ExperimentError, ExperimentResult, ExperimentRunner,
    GjcProvider, Outcome, Provider, SqliteExperimentStore, StyleExperimentRunner, StyleTarget,
};

use crate::config::OpenKakaoConfig;

/// Build the set of providers from configuration. Only the configured `gjc`
/// runner is wrapped; if none is configured, discovery finds no targets and the
/// run reports "no targets" (R4.2). This never makes a network call by itself.
fn build_providers(config: &OpenKakaoConfig) -> Vec<Box<dyn Provider>> {
    let mut providers: Vec<Box<dyn Provider>> = Vec::new();
    if let Some(runner) = config.auto_reply.reply_runner.as_deref() {
        let kind = config
            .auto_reply
            .reply_runner_kind
            .as_deref()
            .unwrap_or("gjc");
        if kind == "gjc" {
            providers.push(Box::new(GjcProvider::new("gjc", runner)));
        }
    }
    providers
}

/// `okc experiment run --prompt <v>`: run the fixed question set against every
/// discovered target, record metrics, and print a side-by-side comparison of
/// each LLM's answer to the same question (R4.3, R4.4, R4.5, R4.6, R4.8).
pub fn cmd_experiment_run(prompt: String, json: bool, config: &OpenKakaoConfig) -> Result<()> {
    let providers = build_providers(config);
    // Scoring target: derive from the learned style profile when available,
    // otherwise fall back to 최연우's default 해요체 tone.
    let runner = StyleExperimentRunner::with_defaults(StyleTarget::default());

    match runner.run(&prompt, &providers) {
        Ok(results) => {
            persist(&results, json);
            let table = build_comparison(std::slice::from_ref(&results));
            if json {
                print_run_json(&prompt, &results);
            } else {
                print_run_human(&prompt, &results, &table);
            }
            Ok(())
        }
        Err(error) => {
            report_error(json, "experiment_run", &error);
            Err(anyhow::anyhow!(error.to_string()))
        }
    }
}

/// `okc experiment compare <v1> <v2>`: load stored results for two prompt
/// versions and compare their quality and speed side by side (R4.7).
pub fn cmd_experiment_compare(
    version_a: String,
    version_b: String,
    json: bool,
) -> Result<()> {
    let db_path = openkakao_cli::context::default_db_path();
    let store = match SqliteExperimentStore::open(&db_path) {
        Ok(store) => store,
        Err(error) => {
            report_error(json, "experiment_compare", &error);
            return Err(anyhow::anyhow!(error.to_string()));
        }
    };

    let run_a = store.load_by_prompt_version(&version_a)?;
    let run_b = store.load_by_prompt_version(&version_b)?;
    let table = build_comparison(&[run_a.clone(), run_b.clone()]);

    if json {
        print_compare_json(&version_a, &version_b, &run_a, &run_b);
    } else {
        print_compare_human(&version_a, &version_b, &table);
    }
    Ok(())
}

/// Persist results to the local experiment store. A store failure is reported
/// but does not discard the in-memory results already computed.
fn persist(results: &[ExperimentResult], json: bool) {
    let db_path = openkakao_cli::context::default_db_path();
    match SqliteExperimentStore::open(&db_path).and_then(|mut store| store.record(results)) {
        Ok(()) => {}
        Err(error) => report_error(json, "experiment_record", &error),
    }
}

/// Average quality of the successful results in a run.
fn average_quality(results: &[ExperimentResult]) -> Option<f64> {
    let scores: Vec<u8> = results
        .iter()
        .filter(|r| r.outcome.is_ok())
        .map(|r| r.quality_score)
        .collect();
    if scores.is_empty() {
        None
    } else {
        Some(scores.iter().map(|s| *s as f64).sum::<f64>() / scores.len() as f64)
    }
}

/// Average latency of the successful results in a run.
fn average_latency(results: &[ExperimentResult]) -> Option<f64> {
    let latencies: Vec<u64> = results
        .iter()
        .filter(|r| r.outcome.is_ok())
        .map(|r| r.latency_ms)
        .collect();
    if latencies.is_empty() {
        None
    } else {
        Some(latencies.iter().map(|l| *l as f64).sum::<f64>() / latencies.len() as f64)
    }
}

fn outcome_label(outcome: &Outcome) -> String {
    match outcome {
        Outcome::Ok => "성공".to_string(),
        Outcome::Timeout => "시간 초과".to_string(),
        Outcome::Failed(reason) => format!("실패({reason})"),
    }
}

fn print_run_human(prompt: &str, results: &[ExperimentResult], table: &ComparisonTable) {
    let ok = results.iter().filter(|r| r.outcome.is_ok()).count();
    let failed = results.iter().filter(|r| matches!(r.outcome, Outcome::Failed(_))).count();
    let timed = results.iter().filter(|r| matches!(r.outcome, Outcome::Timeout)).count();
    println!("프롬프트 '{prompt}' 실험을 마쳤어요.");
    println!("  측정: {}건 (성공 {ok}, 실패 {failed}, 시간 초과 {timed})", results.len());
    if let Some(avg) = average_quality(results) {
        println!("  평균 말투 점수: {:.1}/100", avg);
    }
    if let Some(avg) = average_latency(results) {
        println!("  평균 응답 속도: {:.0}ms", avg);
    }
    println!("\n같은 질문에 대한 LLM별 답변:");
    for row in &table.rows {
        let cell = &row.cells[0];
        let answer = cell.answer.as_deref().unwrap_or("-");
        match cell.quality_score {
            Some(score) => println!(
                "  [{}] {} · {}점 · {}ms · {}",
                row.question_id,
                row.target,
                score,
                cell.latency_ms.unwrap_or(0),
                answer
            ),
            None => println!(
                "  [{}] {} · {}",
                row.question_id,
                row.target,
                outcome_label(&cell.outcome)
            ),
        }
    }
}

fn print_run_json(prompt: &str, results: &[ExperimentResult]) {
    let rows: Vec<_> = results
        .iter()
        .map(|r| {
            json!({
                "provider": r.provider,
                "model": r.model,
                "question_id": r.question_id,
                "latency_ms": r.latency_ms,
                "quality_score": r.quality_score,
                "outcome": r.outcome.as_db_string(),
            })
        })
        .collect();
    let payload = json!({
        "ok": true,
        "action": "experiment_run",
        "prompt_version": prompt,
        "count": results.len(),
        "average_quality": average_quality(results),
        "average_latency_ms": average_latency(results),
        "results": rows,
    });
    if let Ok(text) = serde_json::to_string(&payload) {
        println!("{text}");
    }
}

fn print_compare_human(version_a: &str, version_b: &str, table: &ComparisonTable) {
    println!("프롬프트 '{version_a}' vs '{version_b}' 비교 (품질 점수 · 응답 속도):");
    if table.rows.is_empty() {
        println!("  비교할 실험 결과가 없어요. 먼저 각 프롬프트로 실험을 실행해 주세요.");
        return;
    }
    for row in &table.rows {
        let a = &row.cells[0];
        let b = &row.cells[1];
        println!(
            "  [{}] {}: {} → {}",
            row.question_id,
            row.target,
            cell_summary(a),
            cell_summary(b)
        );
    }
}

fn cell_summary(cell: &openkakao_cli::experiment::ComparisonCell) -> String {
    match cell.quality_score {
        Some(score) => format!("{}점/{}ms", score, cell.latency_ms.unwrap_or(0)),
        None => outcome_label(&cell.outcome),
    }
}

fn print_compare_json(
    version_a: &str,
    version_b: &str,
    run_a: &[ExperimentResult],
    run_b: &[ExperimentResult],
) {
    let payload = json!({
        "ok": true,
        "action": "experiment_compare",
        "version_a": version_a,
        "version_b": version_b,
        "average_quality_a": average_quality(run_a),
        "average_quality_b": average_quality(run_b),
        "average_latency_ms_a": average_latency(run_a),
        "average_latency_ms_b": average_latency(run_b),
        "count_a": run_a.len(),
        "count_b": run_b.len(),
    });
    if let Ok(text) = serde_json::to_string(&payload) {
        println!("{text}");
    }
}

/// Report an experiment error: plain-language to stderr for a human, structured
/// JSON to stdout for machine callers.
fn report_error(json: bool, action: &str, error: &ExperimentError) {
    if json {
        let payload = json!({
            "ok": false,
            "action": action,
            "error": error.to_string(),
        });
        if let Ok(text) = serde_json::to_string(&payload) {
            println!("{text}");
        }
    }
    eprintln!("{error}");
}
