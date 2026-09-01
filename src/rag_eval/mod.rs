//! Offline RAG-quality metrics — a Rust port of `scripts/rag_eval.py` (Task 13,
//! R11.1/R11.10).
//!
//! `rag_eval.py` reads each room's `reply-evidence.jsonl` ledger and computes
//! citation/recall-style metrics per model cohort so retrieval behaviour can be
//! compared across models **without re-running any model**. It is a read-only,
//! offline analysis tool.
//!
//! This module ports the *pure* metric computation — the part that turns a list
//! of ledger records into metrics — so its allow/deny/**ordering** results are
//! identical to the Python original for the same inputs. The parity is proven by
//! [`tests/rag_eval_parity.rs`], which feeds both this code and the real Python
//! the same fixture records and asserts the normalized outputs match exactly.
//!
//! Behaviour reproduced bit-for-bit from the Python original:
//! * `citation_metrics` returns the *unrounded* `(recall, utilization)` pair.
//! * every aggregate mean/median is rounded to four decimals with
//!   round-half-to-even, matching Python's `round(..., 4)`.
//! * ordered maps (Python `Counter.most_common()` / `sorted`) keep the same
//!   order: counts descending with first-seen ties, and age distributions
//!   ascending by key.
//!
//! Records are handled as [`serde_json::Value`] so the same field-presence and
//! type rules the dynamically-typed Python code applies are mirrored exactly.

use serde_json::{json, Map, Value};
use std::collections::BTreeMap;

/// The recall@k / precision@k window sizes, matching `ks` in the Python
/// `citation_position_analysis`.
const KS: [i64; 5] = [3, 5, 7, 10, 13];

/// Round to four decimals with round-half-to-even, matching Python's
/// `round(x, 4)`.
fn round4(value: f64) -> f64 {
    (value * 10_000.0).round_ties_even() / 10_000.0
}

/// `statistics.fmean` then `round(_, 4)`; `None` for an empty input, matching
/// the Python `_mean` helper.
fn mean(values: &[f64]) -> Option<f64> {
    if values.is_empty() {
        return None;
    }
    let sum: f64 = values.iter().sum();
    Some(round4(sum / values.len() as f64))
}

/// `statistics.median` then `round(_, 4)`; `None` for an empty input, matching
/// the Python `_median` helper. For an even count this averages the two middle
/// values, exactly like `statistics.median`.
fn median(values: &[f64]) -> Option<f64> {
    if values.is_empty() {
        return None;
    }
    let mut sorted = values.to_vec();
    sorted.sort_by(|a, b| a.partial_cmp(b).expect("metric values are never NaN"));
    let n = sorted.len();
    let mid = n / 2;
    let median = if n % 2 == 1 {
        sorted[mid]
    } else {
        (sorted[mid - 1] + sorted[mid]) / 2.0
    };
    Some(round4(median))
}

/// A number that could not be produced (`None`) serializes to JSON `null`,
/// matching Python emitting `None`.
fn opt_number(value: Option<f64>) -> Value {
    match value {
        Some(v) => json!(v),
        None => Value::Null,
    }
}

/// An insertion-ordered counter that reproduces `collections.Counter`:
/// [`Self::most_common`] returns counts descending with equal counts kept in
/// first-seen order (a stable sort), exactly like `Counter.most_common()`.
#[derive(Debug, Default)]
struct OrderedCounter {
    order: Vec<String>,
    counts: BTreeMap<String, u64>,
}

impl OrderedCounter {
    fn add(&mut self, key: &str) {
        if !self.counts.contains_key(key) {
            self.order.push(key.to_string());
        }
        *self.counts.entry(key.to_string()).or_insert(0) += 1;
    }

    /// Counts descending, ties in first-seen order.
    fn most_common(&self) -> Vec<(String, u64)> {
        let mut items: Vec<(String, u64)> = self
            .order
            .iter()
            .map(|key| (key.clone(), self.counts[key]))
            .collect();
        // `sort_by` is stable, so equal counts stay in first-seen order.
        items.sort_by(|a, b| b.1.cmp(&a.1));
        items
    }
}

/// The set of stringified ids from a JSON array, matching Python `_ids`: a
/// non-array yields an empty set; array items are stringified.
fn ids(value: Option<&Value>) -> Vec<String> {
    let mut seen = Vec::new();
    if let Some(Value::Array(items)) = value {
        for item in items {
            let s = json_scalar_to_string(item);
            if !seen.contains(&s) {
                seen.push(s);
            }
        }
    }
    seen
}

/// Mirror Python `str()` for the scalar id values that appear in ledgers.
/// Strings are used verbatim (the common case); other scalars are stringified
/// the way `str()` would render them.
fn json_scalar_to_string(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Bool(b) => {
            if *b {
                "True".to_string()
            } else {
                "False".to_string()
            }
        }
        Value::Null => "None".to_string(),
        other => other.to_string(),
    }
}

/// A JSON number, excluding booleans (`v.as_f64()` already rejects `Bool`).
fn number(value: Option<&Value>) -> Option<f64> {
    value.and_then(Value::as_f64)
}

/// `(citation_recall, pool_utilization)` for one record, **unrounded**,
/// matching `rag_eval.citation_metrics`.
pub fn citation_metrics(record: &Value) -> (f64, f64) {
    let used = ids(record.get("evidence_ids"));
    let supplied: Vec<String> = match record.get("recent_conversation") {
        Some(Value::Array(items)) => {
            let mut set = Vec::new();
            for item in items {
                if let Some(eid) = item.get("evidence_id") {
                    if is_truthy(eid) {
                        let s = json_scalar_to_string(eid);
                        if !set.contains(&s) {
                            set.push(s);
                        }
                    }
                }
            }
            set
        }
        _ => Vec::new(),
    };
    if used.is_empty() {
        return (0.0, 0.0);
    }
    let hit = used.iter().filter(|id| supplied.contains(id)).count();
    let recall = hit as f64 / used.len() as f64;
    let utilization = if supplied.is_empty() {
        0.0
    } else {
        hit as f64 / supplied.len() as f64
    };
    (recall, utilization)
}

/// Python truthiness for the scalar values used as `evidence_id`: a non-empty
/// string / non-zero number / `true` is truthy.
fn is_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::String(s) => !s.is_empty(),
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// Count of cited evidence by kind (the token before the first `:`), ordered
/// most-common-first, matching `rag_eval.evidence_composition` +
/// `Counter.most_common`.
pub fn evidence_composition(records: &[Value]) -> Vec<(String, u64)> {
    let mut kinds = OrderedCounter::default();
    for record in records {
        if let Some(Value::Array(items)) = record.get("evidence_ids") {
            for value in items {
                let s = json_scalar_to_string(value);
                let kind = s.split(':').next().unwrap_or("").to_string();
                kinds.add(&kind);
            }
        }
    }
    kinds.most_common()
}

/// The model cohort name for a record: the segment after the first `/` of
/// `str(record.get("model") or "none")`, matching the Python grouping key.
fn model_name(record: &Value) -> String {
    let raw = record
        .get("model")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .unwrap_or("none");
    // `split("/", 1)[-1]` == everything after the first slash (or the whole).
    raw.splitn(2, '/').last().unwrap_or(raw).to_string()
}

/// Ordered pairs as a JSON array of `[key, value]`, preserving order.
fn pairs_to_value<T: Into<Value> + Clone>(pairs: &[(String, T)]) -> Value {
    Value::Array(
        pairs
            .iter()
            .map(|(k, v)| json!([k.clone(), v.clone().into()]))
            .collect(),
    )
}

/// Cohort metrics for a set of records, normalized to the same shape the parity
/// fixture captures (ordered maps as `[key, value]` pair-lists). Mirrors
/// `rag_eval.cohort_metrics`.
pub fn cohort_metrics(records: &[Value], status_by_event: &BTreeMap<String, String>) -> Value {
    let mut recalls = Vec::new();
    let mut utils = Vec::new();
    let mut ctx_matches = Vec::new();
    let mut style_matches = Vec::new();
    let mut best_ctx = Vec::new();
    let mut best_style = Vec::new();
    let mut prior_sim = Vec::new();
    let mut reply_lens = Vec::new();
    let mut draft_counts = Vec::new();
    let mut models = OrderedCounter::default();
    let mut terminals = OrderedCounter::default();

    for record in records {
        models.add(&model_name(record));
        let (recall, utilization) = citation_metrics(record);
        recalls.push(recall);
        utils.push(utilization);
        if let Some(v) = number(record.get("context_match_count")) {
            ctx_matches.push(v);
        }
        if let Some(v) = number(record.get("style_match_count")) {
            style_matches.push(v);
        }
        if let Some(v) = number(record.get("best_context_score")) {
            best_ctx.push(v);
        }
        if let Some(v) = number(record.get("best_style_score")) {
            best_style.push(v);
        }
        if let Some(v) = number(record.get("prior_similarity")) {
            prior_sim.push(v);
        }
        if let Some(reply) = record.get("reply").and_then(Value::as_str) {
            if !reply.is_empty() {
                // Python `len(str)` counts code points.
                reply_lens.push(reply.chars().count() as f64);
            }
        }
        if let Some(Value::Array(drafts)) = record.get("drafts") {
            draft_counts.push(drafts.len() as f64);
        }
        let event_id = record
            .get("event_id")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .unwrap_or("");
        if !event_id.is_empty() {
            if let Some(terminal) = status_by_event.get(event_id) {
                terminals.add(terminal);
            }
        }
    }

    json!({
        "n": records.len() as u64,
        "models": pairs_to_value(&models.most_common()),
        "citation_recall_mean": opt_number(mean(&recalls)),
        "citation_recall_median": opt_number(median(&recalls)),
        "pool_utilization_mean": opt_number(mean(&utils)),
        "context_match_mean": opt_number(mean(&ctx_matches)),
        "style_match_mean": opt_number(mean(&style_matches)),
        "best_context_score_mean": opt_number(mean(&best_ctx)),
        "best_style_score_mean": opt_number(mean(&best_style)),
        "prior_similarity_mean": opt_number(mean(&prior_sim)),
        "reply_len_p50": opt_number(median(&reply_lens)),
        "drafts_mean": opt_number(mean(&draft_counts)),
        "terminal_status": pairs_to_value(&terminals.most_common()),
    })
}

/// Per-model cohort metrics, grouped by model name and ordered by name
/// (`sorted(groups.items())`), matching `rag_eval.per_model`.
pub fn per_model(
    records: &[Value],
    status_by_event: &BTreeMap<String, String>,
) -> Vec<(String, Value)> {
    // BTreeMap keeps groups sorted by name, like Python `sorted(groups.items())`.
    let mut groups: BTreeMap<String, Vec<Value>> = BTreeMap::new();
    for record in records {
        groups
            .entry(model_name(record))
            .or_default()
            .push(record.clone());
    }
    groups
        .into_iter()
        .map(|(name, items)| (name, cohort_metrics(&items, status_by_event)))
        .collect()
}

/// `recall@k` / `precision@k` and citation-age analysis, normalized to the
/// fixture shape. Mirrors `rag_eval.citation_position_analysis`, including the
/// two different return shapes (no `recall_at_k`/`precision_at_k`/
/// `age_distribution` keys when there are zero citations).
pub fn citation_position_analysis(records: &[Value]) -> Value {
    let mut positions: Vec<i64> = Vec::new();
    let mut window_lens: Vec<f64> = Vec::new();
    let mut precision_hits: BTreeMap<i64, Vec<f64>> = KS.iter().map(|k| (*k, Vec::new())).collect();
    let mut rerank_empty: u64 = 0;
    let mut rerank_scored: u64 = 0;
    let mut rerank_fallbacks = OrderedCounter::default();

    for record in records {
        // Rerank counters are tallied for *every* record, before the
        // recent-conversation guard below (matching the Python order).
        match record.get("rerank_scores") {
            Some(Value::Array(scores)) if !scores.is_empty() => rerank_scored += 1,
            _ => rerank_empty += 1,
        }
        let fallback = record
            .get("fallback")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .unwrap_or("none");
        rerank_fallbacks.add(fallback);

        let rc = match record.get("recent_conversation") {
            Some(Value::Array(items)) if !items.is_empty() => items,
            _ => continue,
        };
        window_lens.push(rc.len() as f64);

        // `newest_ids`: ids from newest (reversed) to oldest.
        let newest_ids: Vec<String> = rc
            .iter()
            .rev()
            .filter_map(|item| {
                item.get("evidence_id").and_then(|eid| {
                    if is_truthy(eid) {
                        Some(json_scalar_to_string(eid))
                    } else {
                        None
                    }
                })
            })
            .collect();
        // `id_pos = {eid: idx ...}`: later (older) duplicates overwrite, so the
        // final position for a duplicated id is its oldest index.
        let mut id_pos: BTreeMap<String, i64> = BTreeMap::new();
        for (idx, eid) in newest_ids.iter().enumerate() {
            id_pos.insert(eid.clone(), idx as i64);
        }
        let used_recent: Vec<String> = ids(record.get("evidence_ids"))
            .into_iter()
            .filter(|eid| eid.starts_with("recent:"))
            .collect();
        for eid in &used_recent {
            if let Some(pos) = id_pos.get(eid) {
                positions.push(*pos);
            }
        }
        for k in KS {
            let window_k = &newest_ids[..newest_ids.len().min(k as usize)];
            if window_k.is_empty() {
                continue;
            }
            let hit = window_k
                .iter()
                .filter(|eid| used_recent.contains(eid))
                .count();
            precision_hits
                .get_mut(&k)
                .expect("k present")
                .push(hit as f64 / window_k.len() as f64);
        }
    }

    let total = positions.len();
    let window_len_mean = opt_number(mean(&window_lens));
    let fallbacks = pairs_to_value(&rerank_fallbacks.most_common());

    if total == 0 {
        return json!({
            "citations": 0,
            "window_len_mean": window_len_mean,
            "rerank_empty": rerank_empty,
            "rerank_scored": rerank_scored,
            "rerank_fallbacks": fallbacks,
        });
    }

    // Age distribution ascending by position key, emitted with string keys like
    // Python `{str(k): v ...}`.
    let mut dist: BTreeMap<i64, u64> = BTreeMap::new();
    for pos in &positions {
        *dist.entry(*pos).or_insert(0) += 1;
    }
    let age_distribution = Value::Array(
        dist.iter()
            .map(|(k, v)| json!([k.to_string(), *v]))
            .collect(),
    );

    let recall_at_k: Vec<Value> = KS
        .iter()
        .map(|k| {
            let kept = positions.iter().filter(|p| **p < *k).count();
            json!([*k, round4(kept as f64 / total as f64)])
        })
        .collect();
    let precision_at_k: Vec<Value> = KS
        .iter()
        .map(|k| json!([*k, opt_number(mean(&precision_hits[k]))]))
        .collect();

    json!({
        "citations": total as u64,
        "window_len_mean": window_len_mean,
        "age_distribution": age_distribution,
        "recall_at_k": Value::Array(recall_at_k),
        "precision_at_k": Value::Array(precision_at_k),
        "rerank_empty": rerank_empty,
        "rerank_scored": rerank_scored,
        "rerank_fallbacks": fallbacks,
    })
}

/// Parse a `status_by_event` JSON object into a map, ignoring non-string values.
pub fn status_map_from_value(value: &Value) -> BTreeMap<String, String> {
    let mut map = BTreeMap::new();
    if let Value::Object(obj) = value {
        for (k, v) in obj {
            if let Some(s) = v.as_str() {
                map.insert(k.clone(), s.to_string());
            }
        }
    }
    map
}

/// Convenience for callers that hold a `serde_json::Map`.
pub fn status_map_from_map(obj: &Map<String, Value>) -> BTreeMap<String, String> {
    status_map_from_value(&Value::Object(obj.clone()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn round4_uses_round_half_to_even() {
        assert_eq!(round4(0.12345), 0.1234);
        assert_eq!(round4(0.12355), 0.1236);
        assert_eq!(round4(1.0), 1.0);
    }

    #[test]
    fn citation_metrics_matches_simple_case() {
        let record = json!({
            "evidence_ids": ["recent:a", "recent:b", "timing:1"],
            "recent_conversation": [
                {"evidence_id": "recent:c"},
                {"evidence_id": "recent:b"},
                {"evidence_id": "recent:a"},
            ],
        });
        let (recall, util) = citation_metrics(&record);
        assert_eq!(recall, 2.0 / 3.0);
        assert_eq!(util, 2.0 / 3.0);
    }

    #[test]
    fn no_citations_returns_zero() {
        let record = json!({ "evidence_ids": [] });
        assert_eq!(citation_metrics(&record), (0.0, 0.0));
    }

    #[test]
    fn ordered_counter_breaks_ties_by_first_seen() {
        let mut c = OrderedCounter::default();
        for key in ["b", "a", "a", "b", "c"] {
            c.add(key);
        }
        // a and b both 2; a first-seen after b, so b precedes a.
        assert_eq!(
            c.most_common(),
            vec![
                ("b".to_string(), 2),
                ("a".to_string(), 2),
                ("c".to_string(), 1)
            ]
        );
    }
}
