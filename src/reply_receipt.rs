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
//!
//! What the live room ledger actually contains (checked on 2026-09-15 against
//! 971 rows): a `sent` row carries `status`, `decision`, `model`, `reply` and
//! the send clock, and carries **no** `reason`, **no** `fallback`, **no**
//! `retrieval` block and **no** prompt counts. Judgement fields therefore come
//! from the generation half and only the outcome and its clock come from the
//! delivery half, no matter which row is newer.
//!
//! Prompt counts are `null` on 834 of those rows, including every `sent` row:
//! a turn that ends before prompt assembly (a stale-backlog skip, a model that
//! never answered) records no count at all. `null` is preserved as `None` and
//! rendered as "프롬프트 미생성"; only a recorded `0` is a drop to zero.
//!
//! Ledger stamps are UTC (`2026-09-11T18:27:52.233172+00:00`). Windows show
//! KST, so the conversion happens here rather than in the drawing layer.

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
    /// The newest `recorded_at` across both halves; the sort key for a turn.
    pub recorded_at: String,
    /// `HH:MM` in KST.
    pub clock: String,
    /// `MM-DD HH:MM` in KST, for lists that span more than one day.
    pub display_time: String,
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
    /// Evidence the prompt had before the byte budget. `None` is "not
    /// recorded", which is not the same as zero.
    pub retrieved: Option<i64>,
    /// Evidence that survived into the prompt. `None` is "no prompt was
    /// assembled", which is not the same as zero.
    pub included: Option<i64>,
    pub model: String,
    pub attempts: Vec<ModelAttempt>,
    pub fallback_code: String,
    pub preview: String,
    pub needs_attention: bool,
}

impl TurnReceipt {
    /// `검색 성공 · 후보 10 · 수집 12 · 포함 8`. The three numbers come from
    /// three different sets and are labelled so they cannot be read as one.
    /// A count that was never recorded is spelled out instead of drawn as 0.
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
            RetrievalState::Empty => {
                format!(
                    "{} · 후보 {}",
                    RetrievalState::Empty.text(),
                    self.candidates
                )
            }
            RetrievalState::Ok => {
                let mut parts = vec![
                    RetrievalState::Ok.text().to_string(),
                    format!("후보 {}", self.candidates),
                ];
                match (self.retrieved, self.included) {
                    (Some(retrieved), Some(included)) => {
                        parts.push(format!("수집 {retrieved}"));
                        parts.push(format!("포함 {included}"));
                    }
                    (Some(retrieved), Option::None) => {
                        parts.push(format!("수집 {retrieved}"));
                        parts.push("프롬프트 미생성".to_string());
                    }
                    (None, Some(included)) => parts.push(format!("포함 {included}")),
                    (Option::None, Option::None) => parts.push("프롬프트 미생성".to_string()),
                }
                parts.join(" · ")
            }
        }
    }

    /// True when the prompt received less evidence than retrieval collected.
    /// This is the shape that produced the missed-context reply: the ledger has
    /// candidates but the model never saw them. A missing count is not a zero
    /// count, so an unrecorded prompt stage never sets this.
    pub fn evidence_dropped(&self) -> bool {
        self.retrieval_state == RetrievalState::Ok
            && self.candidates > 0
            && self.included == Some(0)
    }

    pub fn budget_trimmed(&self) -> bool {
        matches!(
            (self.retrieved, self.included),
            (Some(retrieved), Some(included)) if retrieved > included
        )
    }

    /// The one-line list entry:
    /// `18:42 · 부자멘토멘티 | 전송 완료 · 질문 응답 | 검색 성공 · 후보 10 … | 답변…`
    pub fn summary(&self) -> String {
        let mut parts = vec![
            format!("{} · {}", self.display_time, self.chat),
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
        lines.push(format!("   기록 시각 · {} (KST)", self.display_time));
        if self.outcome == "sent" {
            lines.push("   전송은 실제 발송 결과를 확인한 기록입니다.".to_string());
        } else if self.outcome == "deferred" {
            // Deferred is not only a policy decision: an exhausted model chain
            // defers too, and the old wording hid that failure.
            if self.reason_code.starts_with("model_") {
                lines.push("   모델 호출이 끝내 성공하지 못해 이 턴을 미뤘습니다.".to_string());
            } else if self.reason_code.is_empty() {
                lines.push("   사유를 남기지 않고 끝난 턴입니다.".to_string());
            } else {
                lines.push("   이번 턴은 답변을 보내지 않은 채 미뤘습니다.".to_string());
            }
        }

        lines.push(String::new());
        lines.push("② 검색과 포함 근거".to_string());
        lines.push(format!("   {}", self.retrieval_text()));
        if self.evidence_dropped() {
            if self.decision == "reply" {
                lines.push(
                    "   검색은 후보를 찾았지만 최종 입력에 하나도 들어가지 않았습니다.".to_string(),
                );
            } else {
                lines.push("   답변하지 않은 턴이라 입력이 0건으로 기록되었습니다.".to_string());
            }
        } else if self.budget_trimmed() {
            lines.push(format!(
                "   프롬프트 예산 때문에 수집 {}건 중 {}건만 남았습니다.",
                self.retrieved.unwrap_or_default(),
                self.included.unwrap_or_default()
            ));
        } else if self.retrieval_state == RetrievalState::Ok && self.included.is_none() {
            lines.push("   근거는 찾았지만 프롬프트를 만들지 않은 채 턴이 끝났습니다.".to_string());
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
                let tail = match (attempt.result.as_str(), attempt.included) {
                    ("generated", Some(included)) => format!(" · 입력 {included}건"),
                    ("generated", None) => " · 입력 기록 없음".to_string(),
                    _ => format!(" · {}", attempt.reason_text()),
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
            "display_time": self.display_time,
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
            "preview_text": self.preview_text(),
            "needs_attention": self.needs_attention,
            "summary": self.summary(),
            "detail": self.detail_lines(),
        })
    }
}

/// Human words for a ledger `reason` code. Unknown codes keep their raw form so
/// a new decision reason shows up instead of silently reading as a failure.
pub fn reason_text(code: &str) -> String {
    if let Some((head, detail)) = split_explained_reason(code) {
        if let Some(text) = known_reason(head) {
            if detail.is_empty() {
                return text.to_string();
            }
            return format!("{} (설명 기록 있음)", text);
        }
    }
    match known_reason(code) {
        Some(text) => text.to_string(),
        None => format!("기록된 사유({code})"),
    }
}

/// Writers append a free-text explanation after the code, separated by either a
/// colon or a semicolon: social_reply: 티보 주문 소식에 대한 확인. Only the head
/// is a code, so the tail is trimmed off before the lookup.
fn split_explained_reason(code: &str) -> Option<(&str, &str)> {
    let index = code.find(|ch: char| ch == ':' || ch == ';')?;
    let (head, tail) = code.split_at(index);
    Some((head.trim(), tail.get(1..).unwrap_or_default().trim()))
}

/// The mapped Korean phrase for a known reason code, or none when this build
/// has never heard of the code.
fn known_reason(code: &str) -> Option<&'static str> {
    match code {
        "" => Some("사유 기록 없음"),
        "direct_question" => Some("질문 응답"),
        "social_reply" => Some("대화 참여"),
        "reaction_reply" => Some("반응 답장"),
        "useful_information" => Some("유용한 정보 공유"),
        "geeknews_rss" => Some("긱뉴스 소식"),
        "already_commented" => Some("이미 답변함"),
        "stale_backlog" => Some("오래된 메시지"),
        "model_temporarily_unavailable" => Some("모델 일시 사용 불가"),
        "model_authentication_unavailable" => Some("모델 인증 실패"),
        "model_usage_limited" => Some("모델 사용 한도 초과"),
        "media_unavailable_clarification" => Some("사진 확인 불가"),
        "warming" => Some("재정렬 준비 중"),
        "missing_model" => Some("재정렬 모델 없음"),
        "question_unknown" => Some("질문 판단 불가"),
        "photo_passthrough" => Some("사진 그대로 전달"),
        "lenient_policy" => Some("완화된 기준 적용"),
        "all_policy_rejected" => Some("모든 후보가 기준 미달"),
        "same_ending_hold" => Some("같은 말투라 보류"),
        "sidecar_crash" => Some("재정렬 도구 중단"),
        "timeout" => Some("재정렬 시간 초과"),
        "delivery_unknown" => Some("전송 결과 미확인"),
        _ => Option::None,
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

const KST_OFFSET_MINUTES: i64 = 9 * 60;

/// A ledger timestamp taken apart far enough to reorder it and to shift it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct Stamp {
    year: i64,
    month: i64,
    day: i64,
    hour: i64,
    minute: i64,
    offset_minutes: i64,
}

fn parse_stamp(text: &str) -> Option<Stamp> {
    let text = text.trim();
    let (date, rest) = text.split_once('T')?;
    let mut fields = date.split('-');
    let year: i64 = fields.next()?.parse().ok()?;
    let month: i64 = fields.next()?.parse().ok()?;
    let day: i64 = fields.next()?.parse().ok()?;
    if fields.next().is_some() || !(1..=12).contains(&month) || !(1..=31).contains(&day) {
        return None;
    }
    if rest.as_bytes().get(2) != Some(&b':') {
        return None;
    }
    let hour: i64 = rest.get(0..2)?.parse().ok()?;
    let minute: i64 = rest.get(3..5)?.parse().ok()?;
    if hour > 23 || minute > 59 {
        return None;
    }
    Some(Stamp {
        year,
        month,
        day,
        hour,
        minute,
        offset_minutes: offset_minutes(rest)?,
    })
}

fn offset_minutes(rest: &str) -> Option<i64> {
    if rest.ends_with('Z') {
        return Some(0);
    }
    let tail = rest.get(rest.len().checked_sub(6)?..)?;
    let sign = match tail.as_bytes().first()? {
        b'+' => 1,
        b'-' => -1,
        _ => return None,
    };
    if tail.as_bytes().get(3) != Some(&b':') {
        return None;
    }
    let hours: i64 = tail.get(1..3)?.parse().ok()?;
    let minutes: i64 = tail.get(4..6)?.parse().ok()?;
    Some(sign * (hours * 60 + minutes))
}

/// Minutes since 1970-01-01T00:00Z, so turns can be ordered without a date
/// library and without relying on the writer's textual format.
fn epoch_minutes(stamp: Stamp) -> i64 {
    days_from_civil(stamp.year, stamp.month, stamp.day) * 1440 + stamp.hour * 60 + stamp.minute
        - stamp.offset_minutes
}

/// Howard Hinnant's civil-date algorithm, valid for the proleptic Gregorian
/// calendar the ledger's ISO stamps use.
fn days_from_civil(year: i64, month: i64, day: i64) -> i64 {
    let year = if month <= 2 { year - 1 } else { year };
    let era = if year >= 0 { year } else { year - 399 } / 400;
    let year_of_era = year - era * 400;
    let month_shifted = (month + 9) % 12;
    let day_of_year = (153 * month_shifted + 2) / 5 + day - 1;
    let day_of_era = year_of_era * 365 + year_of_era / 4 - year_of_era / 100 + day_of_year;
    era * 146097 + day_of_era - 719468
}

fn civil_from_days(days: i64) -> (i64, i64, i64) {
    let days = days + 719468;
    let era = if days >= 0 { days } else { days - 146096 } / 146097;
    let day_of_era = days - era * 146097;
    let year_of_era =
        (day_of_era - day_of_era / 1460 + day_of_era / 36524 - day_of_era / 146096) / 365;
    let year = year_of_era + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_shifted = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * month_shifted + 2) / 5 + 1;
    let month = if month_shifted < 10 {
        month_shifted + 3
    } else {
        month_shifted - 9
    };
    (if month <= 2 { year + 1 } else { year }, month, day)
}

/// The ledger's UTC stamp as KST, which is the clock every window shows.
/// Returns `(MM-DD HH:MM, HH:MM)`, or the raw text twice when it cannot be read.
pub fn kst_display(recorded_at: &str) -> (String, String) {
    let Some(stamp) = parse_stamp(recorded_at) else {
        return (recorded_at.to_string(), recorded_at.to_string());
    };
    let total = epoch_minutes(stamp) + KST_OFFSET_MINUTES;
    let minutes = total.rem_euclid(1440);
    let clock = format!("{:02}:{:02}", minutes / 60, minutes % 60);
    let (_, month, day) = civil_from_days(total.div_euclid(1440));
    (format!("{month:02}-{day:02} {clock}"), clock)
}

/// Sort key for a turn. An unreadable stamp sorts oldest instead of splitting
/// the list, and ties keep ledger order because the sort is stable.
fn observed_key(recorded_at: &str) -> i64 {
    parse_stamp(recorded_at)
        .map(epoch_minutes)
        .unwrap_or(i64::MIN)
}

fn parse_attempts(value: Option<&Value>) -> Vec<ModelAttempt> {
    let mut attempts = Vec::new();
    let Some(Value::Array(items)) = value else {
        return attempts;
    };
    for (index, item) in items.iter().enumerate() {
        let Value::Object(fields) = item else {
            continue;
        };
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

/// A count that was recorded, or `None` when the field is absent or `null`.
/// An array is not a count: the top-level `evidence_ids` is a list of ids, so
/// reading it as a number would invent a zero.
fn number_of(value: Option<&Value>) -> Option<i64> {
    match value {
        Some(Value::Number(number)) => Some(
            number
                .as_i64()
                .or_else(|| number.as_f64().map(|float| float as i64))
                .unwrap_or_default(),
        ),
        Some(Value::Bool(true)) => Some(1),
        Some(Value::Bool(false)) => Some(0),
        _ => None,
    }
}

/// The retrieval block is the only place that holds all three counts, so it is
/// read first; the top-level copies are consulted only when it is absent.
fn count_of(block: Option<&Value>, records: &[Option<&Value>], key: &str) -> Option<i64> {
    if let Some(block) = block {
        if let Some(count) = number_of(block.get(key)) {
            return Some(count);
        }
    }
    records
        .iter()
        .flatten()
        .find_map(|record| number_of(record.get(key)))
}

fn merge_group(records: &[&Value]) -> Option<TurnReceipt> {
    // Judgement lives in the generation half, outcome in the delivery half.
    // A deferred row means the turn had not decided yet, so it is only used
    // when the event never reached a decision.
    let generation = records
        .iter()
        .rev()
        .copied()
        .filter(|record| record_kind(record) == "generation")
        .find(|record| as_string(record.get("status")) != "deferred")
        .or_else(|| {
            records
                .iter()
                .rev()
                .copied()
                .find(|record| record_kind(record) == "generation")
        });
    let delivery = records
        .iter()
        .rev()
        .copied()
        .find(|record| record_kind(record) == "delivery");

    let outcome_source = delivery.or(generation)?;
    let decision_source = generation.or(delivery)?;
    let halves = [generation, delivery];

    let event_id = record_event_id(outcome_source);
    let outcome = as_string(outcome_source.get("status"));
    let recorded_at = halves
        .iter()
        .flatten()
        .map(|record| as_string(record.get("recorded_at")))
        .max_by_key(|text| observed_key(text))
        .unwrap_or_default();
    let (display_time, clock) = kst_display(&recorded_at);
    let retrieval_block = halves
        .iter()
        .flatten()
        .find_map(|record| record.get("retrieval").filter(|value| value.is_object()));

    let attempted = retrieval_block
        .and_then(|block| block.get("attempted"))
        .and_then(Value::as_bool);
    let retrieval_error = retrieval_block
        .and_then(|block| block.get("error"))
        .and_then(Value::as_str)
        .filter(|text| !text.is_empty())
        .map(str::to_string);
    let index_ready = halves
        .iter()
        .flatten()
        .find_map(|record| record.get("index_readiness"))
        .and_then(|value| value.get("ready"))
        .and_then(Value::as_bool);

    let candidates = retrieval_block
        .and_then(|block| number_of(block.get("evidence_ids")))
        .unwrap_or_default();
    let retrieved = count_of(retrieval_block, &halves, "retrieved_evidence_ids");
    let included = count_of(retrieval_block, &halves, "prompt_evidence_ids");

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

    let reason_code = as_string(decision_source.get("reason"));
    let attempts = parse_attempts(decision_source.get("model_attempts"))
        .into_iter()
        .chain(parse_attempts(
            Some(outcome_source).and_then(|record| record.get("model_attempts")),
        ))
        .collect::<Vec<_>>();
    let mut deduped: BTreeMap<usize, ModelAttempt> = BTreeMap::new();
    for attempt in attempts {
        deduped.entry(attempt.order).or_insert(attempt);
    }
    let attempts: Vec<ModelAttempt> = deduped.into_values().collect();

    let decision = as_string(decision_source.get("decision"));
    // The model that answered this turn: a sent row records the model that
    // produced the delivered reply, so it wins when it names one.
    let model = {
        let delivered = as_string(outcome_source.get("model"));
        if delivered.trim().is_empty() {
            as_string(decision_source.get("model"))
        } else {
            delivered
        }
    };
    let reply = {
        let decided = as_string(decision_source.get("reply"));
        if decided.trim().is_empty() {
            as_string(outcome_source.get("reply"))
        } else {
            decided
        }
    };
    let dropped = retrieval_state == RetrievalState::Ok && candidates > 0 && included == Some(0);
    let needs_attention = retrieval_state == RetrievalState::Error
        || (retrieval_state == RetrievalState::IndexNotReady)
        || (dropped && decision == "reply")
        || (reason_code.starts_with("model_") && outcome != "sent");

    Some(TurnReceipt {
        event_id,
        clock,
        display_time,
        recorded_at,
        chat: as_string(outcome_source.get("chat")),
        log_id: outcome_source
            .get("log_id")
            .and_then(Value::as_i64)
            .or_else(|| decision_source.get("log_id").and_then(Value::as_i64)),
        outcome_text: outcome_text(&outcome),
        outcome,
        decision,
        reason_text: reason_text(&reason_code),
        reason_code,
        retrieval_state,
        retrieval_error,
        candidates,
        retrieved,
        included,
        model,
        attempts,
        fallback_code: as_string(decision_source.get("fallback")),
        preview: reply,
        needs_attention,
    })
}

/// Join each event's generation and delivery records, then describe the newest
/// `limit` turns oldest-last so the window can append in reading order.
///
/// Turns are ordered by the newest stamp either half carries, not by where the
/// event first appears: a turn that was deferred, retried and then sent is
/// observed last even though its first record sits far earlier in the file.
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
    receipts.sort_by_key(|receipt| observed_key(&receipt.recorded_at));
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
        assert_eq!(receipt.included, Option::Some(12));
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
        assert_eq!(receipts[0].retrieval_text(), "정상 0건 · 후보 0");
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
        assert_eq!(
            receipts[0].retrieval_text(),
            "검색 오류 · 검색 명령 실행 실패"
        );
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
        assert_eq!(text, "검색 성공 · 후보 16 · 수집 16 · 포함 12");
    }

    #[test]
    fn the_window_shows_the_same_short_reply_the_summary_uses() {
        let long = "x".repeat(60);
        let receipts = build_receipts(&[generation("evt-p", json!({"reply": long}))], 10);
        let receipt = &receipts[0];
        let payload = receipt.to_json();
        assert_eq!(payload["preview"].as_str().unwrap().chars().count(), 60);
        let shown = payload["preview_text"].as_str().unwrap().to_string();
        assert_eq!(shown.chars().count(), 49, "48 characters and one ellipsis");
        assert!(receipt.summary().ends_with(&shown), "one shortening rule");
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
    fn a_ledger_stamp_is_shown_in_kst() {
        // 09:42 UTC is 18:42 the same day in KST.
        assert_eq!(
            kst_display("2026-09-15T09:42:11Z"),
            ("09-15 18:42".to_string(), "18:42".to_string())
        );
        // The live ledger writes microsecond precision with an explicit offset.
        assert_eq!(
            kst_display("2026-09-11T18:27:52.233172+00:00"),
            ("09-12 03:27".to_string(), "03:27".to_string())
        );
        // An unreadable stamp is shown as it was written, not as midnight.
        assert_eq!(
            kst_display("not-a-stamp"),
            ("not-a-stamp".to_string(), "not-a-stamp".to_string())
        );
    }

    #[test]
    fn parse_ledger_skips_broken_lines() {
        let text = "{\"event_id\": \"a\"}\nnot json\n\n{\"event_id\": \"b\"}\n";
        let records = parse_ledger(text);
        assert_eq!(records.len(), 2);
    }

    #[test]
    fn sent_row_inherits_the_judgement_of_its_generation_row() {
        // Every live sent row looks like this: no reason, no model, no counts.
        let sent = json!({
            "recorded_at": "2026-09-15T09:42:31Z",
            "event_id": "evt-12",
            "chat": "부자멘토멘티",
            "log_id": 39100,
            "status": "sent",
            "decision": "reply",
            "model": "provider/head",
            "reply": "네, 맞아요.",
        });
        let receipts = build_receipts(&[generation("evt-12", json!({})), sent], 10);
        let receipt = &receipts[0];
        assert_eq!(receipt.outcome, "sent");
        assert_eq!(receipt.reason_code, "direct_question");
        assert_eq!(receipt.reason_text, "질문 응답");
        assert_eq!(receipt.decision, "reply");
        assert_eq!(receipt.model, "provider/head");
        assert_eq!(receipt.display_time, "09-15 18:42");
        assert_eq!(receipt.clock, "18:42");
        assert!(receipt
            .detail_lines()
            .iter()
            .any(|line| line.contains("기록 시각 · 09-15 18:42 (KST)")));
    }

    #[test]
    fn null_prompt_count_reads_as_not_generated() {
        let receipts = build_receipts(
            &[generation(
                "evt-13",
                json!({
                    "retrieval": {
                        "attempted": true,
                        "error": null,
                        "evidence_ids": 12,
                        "retrieved_evidence_ids": 19,
                        "prompt_evidence_ids": null
                    }
                }),
            )],
            10,
        );
        let receipt = &receipts[0];
        assert_eq!(receipt.included, Option::None);
        assert_eq!(receipt.retrieved, Option::Some(19));
        assert_eq!(
            receipt.retrieval_text(),
            "검색 성공 · 후보 12 · 수집 19 · 프롬프트 미생성"
        );
        assert!(!receipt.evidence_dropped());
        assert!(!receipt.needs_attention);
        let detail = receipt.detail_lines().join("\n");
        assert!(detail.contains("프롬프트를 만들지 않은 채"));
        assert!(!detail.contains("예산 때문에"));
    }

    #[test]
    fn the_retrieval_block_wins_over_null_top_level_copies() {
        let receipts = build_receipts(
            &[generation(
                "evt-19",
                json!({
                    "retrieved_evidence_ids": null,
                    "prompt_evidence_ids": null,
                    "retrieval": {
                        "attempted": true,
                        "error": null,
                        "evidence_ids": 12,
                        "retrieved_evidence_ids": 19,
                        "prompt_evidence_ids": 18
                    }
                }),
            )],
            10,
        );
        let receipt = &receipts[0];
        assert_eq!(
            receipt.retrieval_text(),
            "검색 성공 · 후보 12 · 수집 19 · 포함 18"
        );
        assert!(receipt.budget_trimmed());
    }

    #[test]
    fn a_top_level_id_array_is_not_a_candidate_count() {
        // The top-level evidence_ids is a list of ids, never a number.
        let receipts = build_receipts(
            &[generation("evt-18", json!({"evidence_ids": [11, 12, 13]}))],
            10,
        );
        let receipt = &receipts[0];
        assert_eq!(receipt.retrieval_state, RetrievalState::Unrecorded);
        assert_eq!(receipt.candidates, 0);
        assert_eq!(receipt.retrieval_text(), "검색 기록 없음");
        assert!(!receipt.evidence_dropped());
    }

    #[test]
    fn a_skipped_turn_records_zero_input_without_raising_attention() {
        let receipts = build_receipts(
            &[generation(
                "evt-14",
                json!({
                    "status": "skipped",
                    "decision": "skip",
                    "reason": "already_commented",
                    "reply": "",
                    "retrieval": {"attempted": true, "error": null, "evidence_ids": 3},
                    "retrieved_evidence_ids": 3,
                    "prompt_evidence_ids": 0
                }),
            )],
            10,
        );
        let receipt = &receipts[0];
        assert_eq!(receipt.outcome, "skipped");
        assert!(receipt.evidence_dropped());
        assert!(!receipt.needs_attention);
        assert!(receipt
            .detail_lines()
            .iter()
            .any(|line| line.contains("답변하지 않은 턴이라 입력이 0건")));
    }

    #[test]
    fn limit_keeps_the_turn_that_was_observed_last() {
        let records = vec![
            generation(
                "evt-a",
                json!({
                    "recorded_at": "2026-09-15T09:00:00Z",
                    "status": "deferred",
                    "reason": "model_temporarily_unavailable",
                    "reply": ""
                }),
            ),
            generation(
                "evt-b",
                json!({"recorded_at": "2026-09-15T09:30:00Z", "reply": "B 답변"}),
            ),
            generation(
                "evt-a",
                json!({
                    "recorded_at": "2026-09-15T10:00:00Z",
                    "reply": "A 답변"
                }),
            ),
            json!({
                "recorded_at": "2026-09-15T10:00:30Z",
                "event_id": "evt-a",
                "chat": "부자멘토멘티",
                "log_id": 39100,
                "status": "sent",
                "decision": "reply",
                "reply": "A 답변"
            }),
        ];
        let receipts = build_receipts(&records, 1);
        assert_eq!(receipts.len(), 1);
        let receipt = &receipts[0];
        assert_eq!(receipt.event_id, "evt-a");
        assert_eq!(receipt.outcome, "sent");
        assert_eq!(receipt.reason_text, "질문 응답");
        assert_eq!(receipt.preview, "A 답변");
    }

    #[test]
    fn deferred_after_a_model_failure_says_so() {
        let receipts = build_receipts(
            &[generation(
                "evt-15",
                json!({
                    "status": "deferred",
                    "reason": "model_temporarily_unavailable",
                    "reply": ""
                }),
            )],
            10,
        );
        let receipt = &receipts[0];
        assert!(receipt.needs_attention);
        let detail = receipt.detail_lines().join("\n");
        assert!(detail.contains("모델 호출이 끝내 성공하지 못해"));
        assert!(!detail.contains("보내지 않은 채 미뤘습니다"));
    }

    #[test]
    fn deferred_without_a_reason_is_not_given_one() {
        let receipts = build_receipts(
            &[generation(
                "evt-16",
                json!({"status": "deferred", "reason": "", "reply": ""}),
            )],
            10,
        );
        let receipt = &receipts[0];
        assert_eq!(receipt.reason_text, "사유 기록 없음");
        assert!(!receipt.needs_attention);
        assert!(receipt
            .detail_lines()
            .iter()
            .any(|line| line.contains("사유를 남기지 않고 끝난 턴")));
    }

    #[test]
    fn a_reason_with_a_trailing_explanation_keeps_its_code_label() {
        let receipts = build_receipts(
            &[generation(
                "evt-17",
                json!({"reason": "social_reply: 티보 주문 소식에 대한 짧은 확인"}),
            )],
            10,
        );
        let receipt = &receipts[0];
        assert_eq!(receipt.reason_text, "대화 참여 (설명 기록 있음)");
        assert_eq!(
            receipt.reason_code,
            "social_reply: 티보 주문 소식에 대한 짧은 확인"
        );
    }

    #[test]
    fn unknown_reason_codes_stay_visible() {
        assert_eq!(
            reason_text("brand_new_reason"),
            "기록된 사유(brand_new_reason)"
        );
        assert_eq!(
            reason_text("brand_new_reason: 왜 그런지"),
            "기록된 사유(brand_new_reason: 왜 그런지)"
        );
        assert_eq!(reason_text("social_reply:"), "대화 참여");
    }
}
