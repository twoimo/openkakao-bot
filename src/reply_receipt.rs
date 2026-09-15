//! Turn receipts for the auto-reply evidence ledger.
//!
//! Every room keeps `reply-evidence.jsonl`: one JSON object per recorded step.
//! A single turn can leave **two** records under the same `event_id` — the
//! generation half (`scheduled`/`deferred`/`skipped`, written by
//! `persist_reply_evidence_ledger`) and the delivery half (`sent`, written by
//! `record_delivery_ledger`). The delivery half deliberately carries no
//! `retrieval` block, so reading only the last line for an event loses the
//! evidence that the earlier line captured. This module joins the two halves
//! first and only then describes the turn.
//!
//! The module is pure: it turns already-parsed records into the Korean summary
//! and detail lines the menu bar window shows. Reading files, calling the
//! model, and drawing stay outside.
//!
//! Field contracts that the display layer must not re-derive on its own:
//!
//! * `retrieval.attempted` is `Option<bool>`. `false` means "the search stage
//!   was not entered", a missing `retrieval` block means "this record does not
//!   say", and neither of those may be rewritten as `정상 0건`.
//! * `retrieval.evidence_ids`, `retrieved_evidence_ids` and
//!   `prompt_evidence_ids` are **counts of ids**, not id arrays, and they count
//!   three different sets: what retrieval collected, what prompt assembly had
//!   before the byte-budget fit, and what survived the fit. A drop between the
//!   last two is the only budget-induced reduction this module reports.
//! * `fallback` on a ledger line is `analysis.rerank_fallback`, the *rerank*
//!   stage's degradation, not the generation model chain. Model fallback is
//!   reported from a separate `model_attempts` array and is never inferred
//!   from `fallback`.
//! * `context_sync.totals.indexed_messages` counts messages indexed *in that
//!   sync run*, so it is `0` on an unchanged, fully indexed room. It cannot
//!   prove that the index is missing.

use serde_json::{json, Value};
use std::collections::BTreeMap;

/// How many turns a single window refresh asks for by default.
pub const DEFAULT_RECEIPT_LIMIT: usize = 40;

/// Search-stage outcome. `Unrecorded` is deliberately outside the five
/// operational states: it says the ledger has no information, not that the
/// search produced nothing.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RetrievalState {
    Ok,
    Empty,
    Skipped,
    Error,
    IndexNotReady,
    Unrecorded,
}

impl RetrievalState {
    pub fn code(self) -> &'static str {
        match self {
            RetrievalState::Ok => "ok",
            RetrievalState::Empty => "empty",
            RetrievalState::Skipped => "skipped",
            RetrievalState::Error => "error",
            RetrievalState::IndexNotReady => "index_not_ready",
            RetrievalState::Unrecorded => "unrecorded",
        }
    }

    pub fn text(self) -> &'static str {
        match self {
            RetrievalState::Ok => "검색 성공",
            RetrievalState::Empty => "정상 0건",
            RetrievalState::Skipped => "검색 미실행",
            RetrievalState::Error => "검색 오류",
            RetrievalState::IndexNotReady => "색인 미준비",
            RetrievalState::Unrecorded => "검색 기록 없음",
        }
    }
}

/// One line of the generation chain, as the worker actually walked it.
#[derive(Debug, Clone)]
pub struct ModelAttempt {
    pub order: usize,
    pub model: String,
    pub result: String,
    pub reason_code: String,
    pub included: Option<i64>,
}

impl ModelAttempt {
    fn result_text(&self) -> &'static str {
        match self.result.as_str() {
            "generated" => "생성 성공 · 최종 답변",
            "failed" => "생성 실패",
            "skipped_precall" => "실행 전 건너뜀",
            "skipped" => "건너뜀",
            _ => "결과 기록 없음",
        }
    }

    fn reason_text(&self) -> String {
        reason_text(&self.reason_code)
    }
}

/// A room's answer to "what happened this turn", already resolved to display
/// text. The menu bar renders these fields and must not re-interpret raw
/// ledger strings.
#[derive(Debug, Clone)]
pub struct TurnReceipt {
    pub event_id: String,
    pub recorded_at: String,
    pub clock: String,
    pub chat: String,
    pub log_id: Option<i64>,
    /// `sent` | `deferred` | `scheduled` | `skipped`
    pub outcome: String,
    pub outcome_text: &'static str,
    pub decision: String,
    pub reason_code: String,
    pub reason_text: String,
    pub retrieval_state: RetrievalState,
    pub retrieval_error: Option<String>,
    pub candidates: i64,
    pub retrieved: i64,
    pub included: i64,
    pub model: String,
    pub attempts: Vec<ModelAttempt>,
    pub fallback_code: String,
    pub preview: String,
    pub needs_attention: bool,
}

impl TurnReceipt {
    /// `검색 성공 · 후보 10 · 입력 8 · 포함 3`. The three numbers come from
    /// three different sets and are labelled so they cannot be read as one.
    pub fn retrieval_text(&self) -> String {
        match self.retrieval_state {
            RetrievalState::Unrecorded => RetrievalState::Unrecorded.text().to_string(),
            RetrievalState::Skipped => RetrievalState::Skipped.text().to_string(),
            RetrievalState::IndexNotReady => RetrievalState::IndexNotReady.text().to_string(),
            RetrievalState::Error => {
                let detail = self
                    .retrieval_error
                    .as_deref()
                    .map(retrieval_error_text)
                    .unwrap_or_else(|| "사유 기록 없음".to_string());
                format!("{} · {}", RetrievalState::Error.text(), detail)
            }
            _ => format!(
                "{} · 후보 {} · 입력 {} · 포함 {}",
                self.retrieval_state.text(),
                self.candidates,
                self.retrieved,
                self.included
            ),
        }
    }

    /// True when the prompt received less evidence than retrieval collected.
    /// This is the shape that produced the missed-context reply: the ledger has
    /// candidates but the model never saw them.
    pub fn evidence_dropped(&self) -> bool {
        self.retrieval_state == RetrievalState::Ok && self.candidates > 0 && self.included == 0
    }

    pub fn budget_trimmed(&self) -> bool {
        self.retrieved > self.included
    }

    /// The one-line list entry:
    /// `18:42 · 부자멘토멘티 | 전송 완료 · 질문 응답 | 검색 성공 · 후보 10 … | 답변…`
    pub fn summary(&self) -> String {
        let mut parts = vec![
            format!("{} · {}", self.clock, self.chat),
            format!("{} · {}", self.outcome_text, self.reason_text),
            self.retrieval_text(),
        ];
        if let Some(attempt) = self.fallback_attempt() {
            parts.push(format!("대체 모델 {}", attempt.model));
        }
        parts.push(self.preview_text());
        parts.join(" | ")
    }

    fn preview_text(&self) -> String {
        let trimmed = self.preview.trim();
        if trimmed.is_empty() {
            return if self.outcome == "sent" {
                "답변 기록 없음".to_string()
            } else {
                "답변 미전송".to_string()
            };
        }
        let folded: String = trimmed.split_whitespace().collect::<Vec<_>>().join(" ");
        let mut chars = folded.chars();
        let short: String = chars.by_ref().take(48).collect();
        if chars.next().is_some() {
            format!("{short}…")
        } else {
            short
        }
    }

    /// The first attempt that was not the head of the configured chain.
    pub fn fallback_attempt(&self) -> Option<&ModelAttempt> {
        self.attempts
            .iter()
            .skip(1)
            .find(|attempt| attempt.result == "generated")
    }

    /// Detail pane lines, in the order 6 Pro fixed:
    /// outcome → generation chain → retrieval and evidence → technical.
    pub fn detail_lines(&self) -> Vec<String> {
        let mut lines = vec![
            format!("① 결과 · {}", self.outcome_text),
            format!("   사유 · {}", self.reason_text),
        ];
        if !self.decision.is_empty() {
            lines.push(format!("   판단 · {}", decision_text(&self.decision)));
        }
        if self.outcome == "sent" {
            lines.push("   전송은 실제 발송 결과를 확인한 기록입니다.".to_string());
        } else if self.outcome == "deferred" {
            lines.push("   보내지 않았습니다. 모델 실패가 아니라 보류 결정입니다.".to_string());
        }

        lines.push(String::new());
        lines.push("② 검색과 포함 근거".to_string());
        lines.push(format!("   {}", self.retrieval_text()));
        if self.evidence_dropped() {
            lines.push(
                "   검색은 후보를 찾았지만 최종 입력에 하나도 들어가지 않았습니다.".to_string(),
            );
        } else if self.budget_trimmed() {
            lines.push(format!(
                "   프롬프트 예산 때문에 입력 {}건 중 {}건만 남았습니다.",
                self.retrieved, self.included
            ));
        }
        if self.retrieval_state == RetrievalState::Unrecorded {
            lines.push(
                "   이 기록에는 검색 여부가 남아 있지 않습니다. 미실행이나 0건으로 단정하지 마세요."
                    .to_string(),
            );
        }
        if !self.fallback_code.is_empty() && self.fallback_code != "none" {
            lines.push(format!(
                "   답변 후보 재정렬 · {}",
                reason_text(&self.fallback_code)
            ));
        }

        lines.push(String::new());
        lines.push("③ 실제 모델 시도".to_string());
        if self.attempts.is_empty() {
            lines.push(format!("   설정된 답변 모델 · {}", model_text(&self.model)));
            lines.push("   시도별 기록이 없습니다. 구버전 기록일 수 있습니다.".to_string());
        } else {
            for attempt in &self.attempts {
                let tail = if attempt.result == "generated" {
                    format!(" · 입력 {}건", attempt.included.unwrap_or(0))
                } else if attempt.result == "skipped_precall" {
                    format!(" · {}", attempt.reason_text())
                } else {
                    format!(" · {}", attempt.reason_text())
                };
                lines.push(format!(
                    "   {}. {} — {}{}",
                    attempt.order,
                    model_text(&attempt.model),
                    attempt.result_text(),
                    tail
                ));
            }
        }
        lines
    }

    pub fn to_json(&self) -> Value {
        json!({
            "event_id": self.event_id,
            "recorded_at": self.recorded_at,
            "clock": self.clock,
            "chat": self.chat,
            "log_id": self.log_id,
            "outcome": self.outcome,
            "outcome_text": self.outcome_text,
            "decision": self.decision,
            "reason_code": self.reason_code,
            "reason_text": self.reason_text,
            "retrieval_state": self.retrieval_state.code(),
            "retrieval_text": self.retrieval_text(),
            "retrieval_error": self.retrieval_error,
            "candidates": self.candidates,
            "retrieved": self.retrieved,
            "included": self.included,
            "evidence_dropped": self.evidence_dropped(),
            "budget_trimmed": self.budget_trimmed(),
            "model": self.model,
            "model_attempts": self.attempts.iter().map(|attempt| json!({
                "order": attempt.order,
                "model": attempt.model,
                "result": attempt.result,
                "result_text": attempt.result_text(),
                "reason_code": attempt.reason_code,
                "reason_text": attempt.reason_text(),
                "included": attempt.included,
            })).collect::<Vec<_>>(),
            "fallback_used": self.fallback_attempt().is_some(),
            "fallback_code": self.fallback_code,
            "preview": self.preview,
            "needs_attention": self.needs_attention,
            "summary": self.summary(),
            "detail": self.detail_lines(),
        })
    }
}

/// Human words for a ledger `reason` code. Unknown codes keep their raw form so
/// a new decision reason shows up instead of silently reading as a failure.
pub fn reason_text(code: &str) -> String {
    match code {
        "" => "사유 기록 없음".to_string(),
        "direct_question" => "질문 응답".to_string(),
        "social_reply" => "대화 참여".to_string(),
        "reaction_reply" => "반응 답장".to_string(),
        "useful_information" => "유용한 정보 공유".to_string(),
        "geeknews_rss" => "긱뉴스 소식".to_string(),
        "already_commented" => "이미 답변함".to_string(),
        "stale_backlog" => "오래된 메시지".to_string(),
        "model_temporarily_unavailable" => "모델 일시 사용 불가".to_string(),
        "model_authentication_unavailable" => "모델 인증 실패".to_string(),
        "model_usage_limited" => "모델 사용 한도 초과".to_string(),
        "media_unavailable_clarification" => "사진 확인 불가".to_string(),
        "warming" => "재정렬 준비 중".to_string(),
        "missing_model" => "재정렬 모델 없음".to_string(),
        "question_unknown" => "질문 판단 불가".to_string(),
        "photo_passthrough" => "사진 그대로 전달".to_string(),
        "lenient_policy" => "완화된 기준 적용".to_string(),
        "all_policy_rejected" => "모든 후보가 기준 미달".to_string(),
        "same_ending_hold" => "같은 말투라 보류".to_string(),
        "sidecar_crash" => "재정렬 도구 중단".to_string(),
        "timeout" => "재정렬 시간 초과".to_string(),
        "delivery_unknown" => "전송 결과 미확인".to_string(),
        other => format!("기록된 사유({other})"),
    }
}

fn decision_text(code: &str) -> String {
    match code {
        "reply" => "답변하기로 선택됨".to_string(),
        "skip" => "답변하지 않기로 선택됨".to_string(),
        other => format!("기록된 판단({other})"),
    }
}

fn outcome_text(status: &str) -> &'static str {
    match status {
        "sent" => "전송 완료",
        "deferred" => "보류",
        "scheduled" => "대기",
        "skipped" => "건너뜀",
        _ => "상태 기록 없음",
    }
}

fn retrieval_error_text(code: &str) -> String {
    match code {
        "retrieval_command_failed" => "검색 명령 실행 실패".to_string(),
        other => format!("기록된 오류({other})"),
    }
}

fn model_text(model: &str) -> String {
    match model {
        "" => "모델 기록 없음".to_string(),
        other => other.to_string(),
    }
}

fn as_i64(value: Option<&Value>) -> i64 {
    match value {
        Some(Value::Number(number)) => number.as_i64().unwrap_or_else(|| {
            number
                .as_f64()
                .map(|float| float as i64)
                .unwrap_or_default()
        }),
        Some(Value::Bool(true)) => 1,
        _ => 0,
    }
}

fn as_string(value: Option<&Value>) -> String {
    match value {
        Some(Value::String(text)) => text.clone(),
        Some(Value::Number(number)) => number.to_string(),
        Some(Value::Bool(flag)) => flag.to_string(),
        _ => String::new(),
    }
}

/// `HH:MM` from an ISO-8601 UTC stamp, so a room's turns can be compared
/// without dragging a date parser into the display layer.
pub fn clock_of(recorded_at: &str) -> String {
    let time = match recorded_at.split_once('T') {
        Some((_, rest)) => rest,
        None => return recorded_at.to_string(),
    };
    let head: String = time.chars().take(5).collect();
    if head.len() == 5 && head.chars().nth(2) == Some(':') {
        head
    } else {
        recorded_at.to_string()
    }
}

fn parse_attempts(value: Option<&Value>) -> Vec<ModelAttempt> {
    let mut attempts = Vec::new();
    let Some(Value::Array(items)) = value else {
        return attempts;
    };
    for (index, item) in items.iter().enumerate() {
        let Value::Object(fields) = item else { continue };
        let order = fields
            .get("order")
            .map(|raw| as_i64(Some(raw)))
            .filter(|order| *order > 0)
            .unwrap_or(index as i64 + 1);
        attempts.push(ModelAttempt {
            order: order as usize,
            model: as_string(fields.get("model")),
            result: as_string(fields.get("result")),
            reason_code: as_string(fields.get("reason")),
            included: fields.get("included").and_then(|raw| {
                if raw.is_null() {
                    None
                } else {
                    Some(as_i64(Some(raw)))
                }
            }),
        });
    }
    attempts.sort_by_key(|attempt| attempt.order);
    attempts
}

/// Which half of a turn a record is.
fn record_kind(record: &Value) -> &'static str {
    match record.get("status").and_then(Value::as_str) {
        Some("sent") => "delivery",
        _ => "generation",
    }
}

fn record_event_id(record: &Value) -> String {
    as_string(record.get("event_id"))
}

fn merge_group(records: &[&Value]) -> Option<TurnReceipt> {
    let generation = records
        .iter()
        .rev()
        .find(|record| record_kind(record) == "generation")
        .copied();
    let delivery = records
        .iter()
        .rev()
        .find(|record| record_kind(record) == "delivery")
        .copied();
    let latest = delivery.or(generation)?;

    let event_id = record_event_id(latest);
    // The delivery half wins for outcome fields, because it is the later fact.
    // Everything the delivery half does not carry falls back to generation.
    let outcome = as_string(latest.get("status"));
    let retrieval_block = generation
        .and_then(|record| record.get("retrieval"))
        .filter(|value| value.is_object());

    let attempted = retrieval_block
        .and_then(|block| block.get("attempted"))
        .and_then(Value::as_bool);
    let retrieval_error = retrieval_block
        .and_then(|block| block.get("error"))
        .and_then(Value::as_str)
        .filter(|text| !text.is_empty())
        .map(str::to_string);
    let index_ready = generation
        .and_then(|record| record.get("index_readiness"))
        .and_then(|value| value.get("ready"))
        .and_then(Value::as_bool);

    let candidates = retrieval_block
        .map(|block| as_i64(block.get("evidence_ids")))
        .unwrap_or_else(|| {
            delivery
                .and_then(|record| record.get("retrieval"))
                .map(|block| as_i64(block.get("evidence_ids")))
                .unwrap_or_default()
        });
    let retrieved = as_i64(latest.get("retrieved_evidence_ids")).max(
        generation
            .map(|record| as_i64(record.get("retrieved_evidence_ids")))
            .unwrap_or_default(),
    );
    let included = as_i64(latest.get("prompt_evidence_ids")).max(
        generation
            .map(|record| as_i64(record.get("prompt_evidence_ids")))
            .unwrap_or_default(),
    );

    let retrieval_state = if index_ready == Some(false) {
        RetrievalState::IndexNotReady
    } else {
        match (attempted, retrieval_error.is_some()) {
            (Some(_), true) => RetrievalState::Error,
            (Some(true), false) => {
                if candidates > 0 {
                    RetrievalState::Ok
                } else {
                    RetrievalState::Empty
                }
            }
            (Some(false), false) => RetrievalState::Skipped,
            (None, _) => RetrievalState::Unrecorded,
        }
    };

    let reason_code = as_string(latest.get("reason"));
    let attempts = parse_attempts(latest.get("model_attempts"))
        .into_iter()
        .chain(parse_attempts(
            generation.and_then(|record| record.get("model_attempts")),
        ))
        .collect::<Vec<_>>();
    let mut deduped: BTreeMap<usize, ModelAttempt> = BTreeMap::new();
    for attempt in attempts {
        deduped.entry(attempt.order).or_insert(attempt);
    }
    let attempts: Vec<ModelAttempt> = deduped.into_values().collect();

    let recorded_at = as_string(latest.get("recorded_at"));
    let needs_attention = retrieval_state == RetrievalState::Error
        || (retrieval_state == RetrievalState::IndexNotReady)
        || (reason_code.starts_with("model_") && outcome != "sent");

    Some(TurnReceipt {
        event_id,
        clock: clock_of(&recorded_at),
        recorded_at,
        chat: as_string(latest.get("chat")),
        log_id: latest.get("log_id").and_then(Value::as_i64),
        outcome_text: outcome_text(&outcome),
        outcome,
        decision: as_string(latest.get("decision")),
        reason_text: reason_text(&reason_code),
        reason_code,
        retrieval_state,
        retrieval_error,
        candidates,
        retrieved,
        included,
        model: as_string(latest.get("model")),
        attempts,
        fallback_code: as_string(latest.get("fallback")),
        preview: as_string(latest.get("reply")),
        needs_attention,
    })
}

/// Join each event's generation and delivery records, then describe the newest
/// `limit` turns oldest-last so the window can append in reading order.
pub fn build_receipts(records: &[Value], limit: usize) -> Vec<TurnReceipt> {
    // Insertion order is preserved so recency ties keep ledger order.
    let mut order: Vec<String> = Vec::new();
    let mut groups: BTreeMap<String, Vec<&Value>> = BTreeMap::new();
    for record in records {
        if !record.is_object() {
            continue;
        }
        let event_id = record_event_id(record);
        if event_id.is_empty() {
            continue;
        }
        if !groups.contains_key(&event_id) {
            order.push(event_id.clone());
        }
        groups.entry(event_id).or_default().push(record);
    }
    let mut receipts: Vec<TurnReceipt> = order
        .iter()
        .filter_map(|event_id| groups.get(event_id))
        .filter_map(|group| merge_group(group))
        .collect();
    if limit > 0 && receipts.len() > limit {
        receipts.drain(..receipts.len() - limit);
    }
    receipts
}

/// Parse a ledger file body, skipping lines that are not JSON objects.
pub fn parse_ledger(text: &str) -> Vec<Value> {
    text.lines()
        .filter_map(|line| {
            let trimmed = line.trim();
            if trimmed.is_empty() {
                return None;
            }
            serde_json::from_str::<Value>(trimmed)
                .ok()
                .filter(Value::is_object)
        })
        .collect()
}

/// The whole window payload for one room.
pub fn render_room(chat: &str, records: &[Value], limit: usize) -> Value {
    let receipts = build_receipts(records, limit);
    let lines = receipts
        .iter()
        .map(|receipt| receipt.summary())
        .collect::<Vec<_>>();
    let attention = receipts
        .iter()
        .filter(|receipt| receipt.needs_attention)
        .count();
    json!({
        "chat": chat,
        "count": receipts.len(),
        "needs_attention": attention,
        "lines": lines,
        "receipts": receipts.iter().map(TurnReceipt::to_json).collect::<Vec<_>>(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn generation(event_id: &str, extra: Value) -> Value {
        let mut base = json!({
            "recorded_at": "2026-09-15T09:42:11Z",
            "event_id": event_id,
            "chat": "부자멘토멘티",
            "log_id": 39100,
            "status": "scheduled",
            "decision": "reply",
            "reason": "direct_question",
            "reply": "네, 맞아요.",
        });
        if let (Some(target), Some(source)) = (base.as_object_mut(), extra.as_object()) {
            for (key, value) in source {
                target.insert(key.clone(), value.clone());
            }
        }
        base
    }

    #[test]
    fn joins_scheduled_and_sent_into_one_turn() {
        let scheduled = generation(
            "evt-1",
            json!({
                "retrieval": {"attempted": true, "error": null, "evidence_ids": 16},
                "retrieved_evidence_ids": 16,
                "prompt_evidence_ids": 12,
            }),
        );
        let sent = json!({
            "recorded_at": "2026-09-15T09:42:31Z",
            "event_id": "evt-1",
            "chat": "부자멘토멘티",
            "log_id": 39100,
            "status": "sent",
            "decision": "reply",
            "reply": "네, 맞아요.",
            "outgoing_log_id": 39101,
        });
        let receipts = build_receipts(&[scheduled, sent], 10);
        assert_eq!(receipts.len(), 1, "one turn, not two rows");
        let receipt = &receipts[0];
        assert_eq!(receipt.outcome, "sent");
        assert_eq!(receipt.outcome_text, "전송 완료");
        // The delivery half carries no retrieval block; the generation half
        // must still supply the numbers.
        assert_eq!(receipt.candidates, 16);
        assert_eq!(receipt.included, 12);
        assert_eq!(receipt.retrieval_state, RetrievalState::Ok);
        assert!(receipt.budget_trimmed());
    }

    #[test]
    fn missing_retrieval_block_is_unrecorded_not_empty() {
        let receipts = build_receipts(&[generation("evt-2", json!({}))], 10);
        assert_eq!(receipts[0].retrieval_state, RetrievalState::Unrecorded);
        assert_eq!(receipts[0].retrieval_text(), "검색 기록 없음");
    }

    #[test]
    fn attempted_false_is_skipped_not_empty() {
        let receipts = build_receipts(
            &[generation(
                "evt-3",
                json!({"retrieval": {"attempted": false, "error": null, "evidence_ids": 0}}),
            )],
            10,
        );
        assert_eq!(receipts[0].retrieval_state, RetrievalState::Skipped);
        assert_eq!(receipts[0].retrieval_text(), "검색 미실행");
    }

    #[test]
    fn attempted_true_without_candidates_is_empty() {
        let receipts = build_receipts(
            &[generation(
                "evt-4",
                json!({"retrieval": {"attempted": true, "error": null, "evidence_ids": 0}}),
            )],
            10,
        );
        assert_eq!(receipts[0].retrieval_state, RetrievalState::Empty);
        assert_eq!(
            receipts[0].retrieval_text(),
            "정상 0건 · 후보 0 · 입력 0 · 포함 0"
        );
    }

    #[test]
    fn retrieval_error_carries_korean_reason() {
        let receipts = build_receipts(
            &[generation(
                "evt-5",
                json!({"retrieval": {
                    "attempted": true,
                    "error": "retrieval_command_failed",
                    "evidence_ids": 0
                }}),
            )],
            10,
        );
        assert_eq!(receipts[0].retrieval_state, RetrievalState::Error);
        assert_eq!(receipts[0].retrieval_text(), "검색 오류 · 검색 명령 실행 실패");
        assert!(receipts[0].needs_attention);
    }

    #[test]
    fn fresh_index_with_zero_new_messages_is_not_index_not_ready() {
        // context_sync.indexed_messages counts this run's new rows only.
        let receipts = build_receipts(
            &[generation(
                "evt-6",
                json!({
                    "retrieval": {"attempted": true, "error": null, "evidence_ids": 3},
                    "context_sync": {"totals": {"indexed_messages": 0}},
                }),
            )],
            10,
        );
        assert_eq!(receipts[0].retrieval_state, RetrievalState::Ok);
    }

    #[test]
    fn explicit_index_readiness_marks_not_ready() {
        let receipts = build_receipts(
            &[generation(
                "evt-7",
                json!({
                    "retrieval": {"attempted": true, "error": null, "evidence_ids": 0},
                    "index_readiness": {"ready": false},
                }),
            )],
            10,
        );
        assert_eq!(receipts[0].retrieval_state, RetrievalState::IndexNotReady);
    }

    #[test]
    fn candidates_without_a_single_included_id_needs_attention() {
        let receipts = build_receipts(
            &[generation(
                "evt-8",
                json!({
                    "retrieval": {"attempted": true, "error": null, "evidence_ids": 10},
                    "retrieved_evidence_ids": 0,
                    "prompt_evidence_ids": 0,
                }),
            )],
            10,
        );
        assert!(receipts[0].evidence_dropped());
        assert!(receipts[0]
            .detail_lines()
            .iter()
            .any(|line| line.contains("하나도 들어가지 않았습니다")));
    }

    #[test]
    fn rerank_fallback_is_not_reported_as_model_fallback() {
        let receipts = build_receipts(
            &[generation(
                "evt-9",
                json!({
                    "retrieval": {"attempted": true, "error": null, "evidence_ids": 2},
                    "fallback": "missing_model",
                }),
            )],
            10,
        );
        assert!(receipts[0].fallback_attempt().is_none());
        assert!(!receipts[0].summary().contains("대체 모델"));
        assert!(receipts[0]
            .detail_lines()
            .iter()
            .any(|line| line.contains("재정렬 모델 없음")));
    }

    #[test]
    fn model_attempts_render_skipped_and_generated_rows() {
        let receipts = build_receipts(
            &[generation(
                "evt-10",
                json!({
                    "retrieval": {"attempted": true, "error": null, "evidence_ids": 3},
                    "model": "provider/head",
                    "model_attempts": [
                        {"order": 1, "model": "provider/head", "result": "skipped_precall", "reason": "missing_model", "included": null},
                        {"order": 2, "model": "provider/alt", "result": "generated", "reason": "", "included": 3}
                    ],
                }),
            )],
            10,
        );
        let receipt = &receipts[0];
        assert_eq!(receipt.attempts.len(), 2);
        assert!(receipt.summary().contains("대체 모델 provider/alt"));
        let detail = receipt.detail_lines().join("\n");
        assert!(detail.contains("실행 전 건너뜀"));
        assert!(detail.contains("생성 성공 · 최종 답변"));
    }

    #[test]
    fn summary_labels_the_three_evidence_numbers_separately() {
        let receipts = build_receipts(
            &[generation(
                "evt-11",
                json!({
                    "retrieval": {"attempted": true, "error": null, "evidence_ids": 16},
                    "retrieved_evidence_ids": 16,
                    "prompt_evidence_ids": 12,
                }),
            )],
            10,
        );
        let text = receipts[0].retrieval_text();
        assert_eq!(text, "검색 성공 · 후보 16 · 입력 16 · 포함 12");
    }

    #[test]
    fn limit_keeps_the_newest_turns() {
        let records = vec![
            generation("evt-a", json!({"recorded_at": "2026-09-15T09:00:00Z"})),
            generation("evt-b", json!({"recorded_at": "2026-09-15T10:00:00Z"})),
            generation("evt-c", json!({"recorded_at": "2026-09-15T11:00:00Z"})),
        ];
        let receipts = build_receipts(&records, 2);
        assert_eq!(receipts.len(), 2);
        assert_eq!(receipts[0].event_id, "evt-b");
        assert_eq!(receipts[1].event_id, "evt-c");
    }

    #[test]
    fn clock_uses_the_time_part_of_an_iso_stamp() {
        assert_eq!(clock_of("2026-09-15T09:42:11Z"), "09:42");
        assert_eq!(clock_of("not-a-stamp"), "not-a-stamp");
    }

    #[test]
    fn parse_ledger_skips_broken_lines() {
        let text = "{\"event_id\": \"a\"}\nnot json\n\n{\"event_id\": \"b\"}\n";
        let records = parse_ledger(text);
        assert_eq!(records.len(), 2);
    }
}
