//! Menu-bar window shell contract + auto-refresh state machine (Task 12).
//!
//! The SwiftUI/AppKit menu-bar shell (`macos/AutoReplyMenu/main.swift`) is a
//! thin view layer: it draws native windows but must hold **no** behavior that
//! decides *whether* a window is created, focused, or refreshed. The design
//! (Component 1) places that contract in the core as a `WindowShell` trait and
//! an `AppWindow` enum. This module is the pure, UI-toolkit-agnostic model of
//! that contract, so the behavior can be unit-tested without a running AppKit
//! process (the Swift shell itself is compiled by `swiftc`, but is not
//! `cargo`-testable).
//!
//! What this module pins down:
//!
//! * **Windows, not dropdowns.** Every primary feature — 기록, 모델 설정,
//!   채팅방, 자가 점검, 대화 기억 — is a [`AppWindow`] that opens on click. The
//!   reply-model and image-model menus collapse into the single
//!   [`AppWindow::ModelSettings`] entry (R5.1, R5.3).
//! * **No refresh menu (R9.1).** [`build_menu`] never emits
//!   [`MenuItemKind::Refresh`]; the variant exists only so a test can prove its
//!   absence.
//! * **Focus, don't recreate (R1.4).** [`WindowShell::open_with`] focuses an
//!   already-open window instead of building a second one.
//! * **Fail without losing state (R1.5, R10.4).** A failed open leaves the
//!   previous window state exactly as it was and yields a plain-language
//!   message.
//! * **Auto-refresh (R9.2, R9.3, R9.4).** [`AutoRefresh`] models the
//!   change-event-driven refresh (within [`EVENT_REFRESH_DEADLINE_MS`]) with a
//!   [`FALLBACK_REFRESH_INTERVAL_MS`] poll fallback, and — critically — keeps
//!   the last shown screen and raises a failure flag when a refresh fails
//!   instead of blanking the view.
//! * **Display connectors (Task 12.2).** [`history_view`] and [`memory_view`]
//!   turn the redacted [`crate::logging`] journal and the [`crate::memory`]
//!   store into plain-language, beginner-friendly screen models, including the
//!   empty-list and query-failure notices and the confirm-before-delete step.

use std::collections::BTreeSet;

use crate::coverage::{CoverageMatrix, Counts, Feature, Verdict};
use crate::durability::{BatchTally, HarnessReport, HarnessVerdict};
use crate::improve::HistoryEntry;
use crate::live_sample::Progress;
use crate::logging::{FlowKind, HistoryStore, PipelineEvent, Stage, StageStatus};
use crate::memory::{MemoryItem, MemoryKind, MemoryStore, RagComparisonRow};
use crate::packaging::{Capability, CapabilityMap, Permission};
use crate::safety::SendGrade;

/// A primary feature window opened from the menu bar (Component 1, Component 14).
///
/// There is exactly one entry per feature. The former separate reply-model and
/// image-model menus are unified under [`AppWindow::ModelSettings`] (R5.1). The
/// live-ops spec grows this set from five to nine: the four new windows
/// [`AppWindow::Durability`] (대량 검증, R1), [`AppWindow::Coverage`] (기능 점검,
/// R5), [`AppWindow::Improvement`] (자기개선, R8), and [`AppWindow::Onboarding`]
/// (권한 설정, R9) reuse the same window contract and auto-refresh cadence.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum AppWindow {
    /// 기록 — the pipeline/history window (R1, R2).
    History,
    /// 모델 설정 — the unified reply + image model settings window (R5).
    ModelSettings,
    /// 채팅방 — the chat-room management window (R6).
    ChatRooms,
    /// 자가 점검 — the self-check (doctor) window (R7).
    SelfCheck,
    /// 대화 기억 — the conversation-memory window (R8).
    Memory,
    /// 대량 검증 — the durability-harness aggregate window (R1).
    Durability,
    /// 기능 점검 — the coverage-matrix window (R5).
    Coverage,
    /// 자기개선 — the self-improvement history window (R8).
    Improvement,
    /// 권한 설정 — the onboarding / permission window (R9.6, R9.11).
    Onboarding,
}

impl AppWindow {
    /// Every window, in the fixed menu order (R10.2 — consistent open method).
    pub fn all() -> [AppWindow; 9] {
        [
            AppWindow::History,
            AppWindow::ModelSettings,
            AppWindow::ChatRooms,
            AppWindow::SelfCheck,
            AppWindow::Memory,
            AppWindow::Durability,
            AppWindow::Coverage,
            AppWindow::Improvement,
            AppWindow::Onboarding,
        ]
    }

    /// The plain-language Korean menu title shown to a beginner (R10.1).
    pub fn menu_title(self) -> &'static str {
        match self {
            AppWindow::History => "기록",
            AppWindow::ModelSettings => "모델 설정",
            AppWindow::ChatRooms => "채팅방",
            AppWindow::SelfCheck => "자가 점검",
            AppWindow::Memory => "대화 기억",
            AppWindow::Durability => "대량 검증",
            AppWindow::Coverage => "기능 점검",
            AppWindow::Improvement => "자기개선",
            AppWindow::Onboarding => "권한 설정",
        }
    }

    /// A stable, non-localized key (used by the shell for frame autosave, etc.).
    pub fn key(self) -> &'static str {
        match self {
            AppWindow::History => "history",
            AppWindow::ModelSettings => "model_settings",
            AppWindow::ChatRooms => "chat_rooms",
            AppWindow::SelfCheck => "self_check",
            AppWindow::Memory => "memory",
            AppWindow::Durability => "durability",
            AppWindow::Coverage => "coverage",
            AppWindow::Improvement => "improvement",
            AppWindow::Onboarding => "onboarding",
        }
    }
}

/// One entry in the menu-bar menu.
///
/// [`MenuItemKind::Refresh`] is intentionally never produced by [`build_menu`]
/// (R9.1). It exists so a test can assert the built menu does not contain it —
/// removing the variant would make that guarantee un-checkable.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MenuItemKind {
    /// A click-to-open window entry.
    OpenWindow(AppWindow),
    /// The quit item.
    Quit,
    /// The removed manual-refresh item — never emitted (R9.1).
    Refresh,
}

/// Build the menu-bar menu model: one open-window entry per feature followed by
/// quit. No refresh entry is ever included (R9.1), and every feature entry
/// opens a window rather than a hover dropdown (R1.2, R5.2, R6.1, R8.1).
pub fn build_menu() -> Vec<MenuItemKind> {
    let mut items: Vec<MenuItemKind> = AppWindow::all()
        .into_iter()
        .map(MenuItemKind::OpenWindow)
        .collect();
    items.push(MenuItemKind::Quit);
    items
}

/// The result of opening a window (R1.1, R1.4).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OpenOutcome {
    /// A new window was created and brought to front.
    Opened,
    /// The window was already open, so it was brought to front (not recreated).
    Focused,
}

/// A plain-language UI error suitable for showing a beginner (R10.3, R10.4).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UiError {
    /// The plain-language message, including at least one next action.
    pub message: String,
}

impl UiError {
    /// Wrap a message.
    pub fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }
}

/// The plain-language message shown when a window cannot be opened (R1.5,
/// R10.4). It names the window and gives a concrete next action.
pub fn window_open_failure_message(w: AppWindow) -> String {
    format!(
        "{} 창을 열지 못했어요. 잠시 뒤 메뉴에서 다시 눌러 주세요.",
        w.menu_title()
    )
}

/// Tracks which feature windows are open and which is frontmost, enforcing the
/// focus-don't-recreate and fail-without-losing-state contract (R1.4, R1.5,
/// R10.4). This is the toolkit-agnostic mirror of the AppKit shell's per-window
/// lifecycle.
#[derive(Debug, Default)]
pub struct WindowShell {
    open: BTreeSet<AppWindow>,
    front: Option<AppWindow>,
    last_error: Option<String>,
}

impl WindowShell {
    /// A shell with no windows open.
    pub fn new() -> Self {
        Self::default()
    }

    /// Whether `w` is currently open.
    pub fn is_open(&self, w: AppWindow) -> bool {
        self.open.contains(&w)
    }

    /// How many windows are currently open.
    pub fn open_count(&self) -> usize {
        self.open.len()
    }

    /// The frontmost window, if any.
    pub fn front(&self) -> Option<AppWindow> {
        self.front
    }

    /// The most recent open-failure message, cleared on any successful open.
    pub fn last_error(&self) -> Option<&str> {
        self.last_error.as_deref()
    }

    /// Open `w`, or focus it if it is already open (R1.1, R1.4).
    ///
    /// `create` is the injected side effect that actually builds the native
    /// window; it is called **only** when the window is not already open, so an
    /// already-open window is brought to front and never rebuilt. When `create`
    /// fails the previous state is left untouched (the open set and frontmost
    /// window are unchanged) and a plain-language error is recorded and returned
    /// (R1.5, R10.4).
    pub fn open_with<F>(&mut self, w: AppWindow, create: F) -> Result<OpenOutcome, UiError>
    where
        F: FnOnce() -> Result<(), UiError>,
    {
        if self.open.contains(&w) {
            // Already open: bring to front, do not recreate (R1.4).
            self.front = Some(w);
            self.last_error = None;
            return Ok(OpenOutcome::Focused);
        }
        match create() {
            Ok(()) => {
                self.open.insert(w);
                self.front = Some(w);
                self.last_error = None;
                Ok(OpenOutcome::Opened)
            }
            Err(err) => {
                // Fail-closed: previous state preserved, plain-language message
                // surfaced (R1.5, R10.4). Neither `open` nor `front` changes.
                self.last_error = Some(err.message.clone());
                Err(err)
            }
        }
    }

    /// Mark a window closed (e.g. the user closed it). No-op if not open.
    pub fn close(&mut self, w: AppWindow) {
        self.open.remove(&w);
        if self.front == Some(w) {
            self.front = self.open.iter().next_back().copied();
        }
    }
}

/// The deadline, in milliseconds, by which a refresh must run after a change
/// event is observed (R9.2 — "within 3 seconds").
pub const EVENT_REFRESH_DEADLINE_MS: u64 = 3_000;

/// The maximum interval, in milliseconds, between automatic refreshes when no
/// change event arrives (R9.3 — "at least every 60 seconds").
pub const FALLBACK_REFRESH_INTERVAL_MS: u64 = 60_000;

/// Auto-refresh state machine over an arbitrary screen snapshot `T` (R9.2,
/// R9.3, R9.4).
///
/// The shell feeds it two kinds of input: [`AutoRefresh::note_change`] when the
/// core signals that displayed data changed, and [`AutoRefresh::tick`] on a
/// timer for the fallback poll. [`AutoRefresh::should_refresh`] decides when a
/// reload is due, and the shell reports the outcome back with
/// [`AutoRefresh::apply_success`] or [`AutoRefresh::apply_failure`].
///
/// The safety-relevant behavior is the failure path: a failed refresh keeps the
/// last shown screen and raises [`AutoRefresh::showing_failure`] rather than
/// replacing the view with empty/placeholder data (R9.4).
#[derive(Debug, Clone)]
pub struct AutoRefresh<T> {
    screen: T,
    last_refresh_at: u64,
    pending_since: Option<u64>,
    showing_failure: bool,
}

impl<T: Clone> AutoRefresh<T> {
    /// Start with an initial screen shown at time `now` (ms).
    pub fn new(now: u64, initial: T) -> Self {
        Self {
            screen: initial,
            last_refresh_at: now,
            pending_since: None,
            showing_failure: false,
        }
    }

    /// The screen currently shown to the user.
    pub fn screen(&self) -> &T {
        &self.screen
    }

    /// True while the last refresh attempt failed and the view is showing the
    /// preserved previous screen plus a failure indicator (R9.4).
    pub fn showing_failure(&self) -> bool {
        self.showing_failure
    }

    /// Record that the core signaled a data change at `now` (R9.2). The refresh
    /// becomes due immediately and must complete within
    /// [`EVENT_REFRESH_DEADLINE_MS`].
    pub fn note_change(&mut self, now: u64) {
        if self.pending_since.is_none() {
            self.pending_since = Some(now);
        }
    }

    /// The wall-clock time by which a change-driven refresh must run, if one is
    /// pending (R9.2).
    pub fn must_refresh_by(&self) -> Option<u64> {
        self.pending_since
            .map(|since| since + EVENT_REFRESH_DEADLINE_MS)
    }

    /// The next fallback refresh time even if no change event arrives (R9.3).
    pub fn next_fallback_at(&self) -> u64 {
        self.last_refresh_at + FALLBACK_REFRESH_INTERVAL_MS
    }

    /// A fallback timer tick. Returns whether a refresh is due at `now`.
    pub fn tick(&self, now: u64) -> bool {
        self.should_refresh(now)
    }

    /// Whether a refresh should run at `now`: either a change is pending, or the
    /// fallback interval has elapsed since the last refresh (R9.2, R9.3).
    pub fn should_refresh(&self, now: u64) -> bool {
        self.pending_since.is_some() || now >= self.next_fallback_at()
    }

    /// Apply a successful refresh: adopt the new screen, clear the pending and
    /// failure flags, and reset the fallback clock (R9.2).
    pub fn apply_success(&mut self, now: u64, screen: T) {
        self.screen = screen;
        self.last_refresh_at = now;
        self.pending_since = None;
        self.showing_failure = false;
    }

    /// Apply a failed refresh: keep the last shown screen, raise the failure
    /// indicator, and reset the fallback clock so the next attempt is scheduled
    /// (R9.4). The pending flag is cleared — this attempt consumed it — so a new
    /// change event is needed to force another immediate try.
    pub fn apply_failure(&mut self, now: u64) {
        self.last_refresh_at = now;
        self.pending_since = None;
        self.showing_failure = true;
    }
}

// ---------------------------------------------------------------------------
// History window display connector (Task 12.2, R2.3–R2.7)
// ---------------------------------------------------------------------------

/// The history window's screen model (R2.3–R2.7).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum HistoryView {
    /// One plain-language line per event, newest first (R2.3, R2.7).
    Rows(Vec<String>),
    /// No records yet — a plain-language empty notice (R2.5).
    Empty(String),
    /// The lookup failed — a plain-language notice (R2.6). The caller keeps the
    /// previously shown rows and does not delete stored records.
    Failed(String),
}

/// The largest number of history rows the window shows at once.
pub const HISTORY_WINDOW_LIMIT: usize = 200;

/// The deadline, in ms, for showing the initial history list when the window
/// opens (R2.3 — "within 3 seconds").
pub const HISTORY_INITIAL_DEADLINE_MS: u64 = 3_000;

/// The deadline, in ms, for appending a newly occurred stage to the list (R2.4
/// — "within 5 seconds").
pub const HISTORY_APPEND_DEADLINE_MS: u64 = 5_000;

/// A beginner-friendly Korean label for a flow (R2.7).
fn flow_label(flow: FlowKind) -> &'static str {
    match flow {
        FlowKind::AutoReply => "자동 답변",
        FlowKind::GeekNews => "긱뉴스",
        FlowKind::LinkForward => "링크 전달",
        FlowKind::TelegramRelay => "텔레그램 중계",
        FlowKind::DatasetRefresh => "대화 반영",
        FlowKind::SelfImprove => "자기개선",
        FlowKind::Coverage => "기능 점검",
        FlowKind::Durability => "대량 검증",
    }
}

/// A beginner-friendly Korean label for a stage — no jargon (R2.7).
fn stage_label(stage: Stage) -> &'static str {
    match stage {
        Stage::Detect => "메시지 확인",
        Stage::Authorize => "보낼 수 있는지 확인",
        Stage::Retrieve => "지난 대화 찾기",
        Stage::Model => "답변 만들기",
        Stage::Schedule => "보낼 시간 정하기",
        Stage::PreSend => "보내기 전 마지막 점검",
        Stage::Commit => "보내기 완료",
    }
}

/// A beginner-friendly Korean label for a stage status (R2.1, R2.7).
fn status_label(status: StageStatus) -> &'static str {
    match status {
        StageStatus::InProgress => "진행 중",
        StageStatus::Success => "성공",
        StageStatus::Failed => "실패",
    }
}

/// Render one event as a plain-language line, including how long it took (R2.9).
pub fn history_line(ev: &PipelineEvent) -> String {
    format!(
        "{} · {} — {} ({}ms)",
        flow_label(ev.flow),
        stage_label(ev.stage),
        status_label(ev.status),
        ev.duration_ms
    )
}

/// Build the history window's screen model from the redacted journal (R2.3–R2.7).
///
/// The store already returns events newest-first, so the order is preserved.
/// An empty list yields a plain-language empty notice; a lookup error yields a
/// plain-language failure notice and the caller keeps the previous rows (the
/// store never deletes on a read error, satisfying R2.6).
pub fn history_view(store: &dyn HistoryStore, limit: usize) -> HistoryView {
    match store.recent(limit) {
        Ok(events) if events.is_empty() => {
            HistoryView::Empty("아직 보여 줄 기록이 없어요. 새 처리가 생기면 여기에 표시돼요.".to_string())
        }
        Ok(events) => HistoryView::Rows(events.iter().map(history_line).collect()),
        Err(_) => HistoryView::Failed(
            "기록을 불러오지 못했어요. 이전에 본 기록은 그대로 두었어요. 잠시 뒤 다시 시도해 주세요."
                .to_string(),
        ),
    }
}

// ---------------------------------------------------------------------------
// Memory window display connector (Task 12.2, R8.1–R8.3, R8.5, R8.8)
// ---------------------------------------------------------------------------

/// The memory window's screen model (R8.2, R8.3). Lists conversation-memory
/// notes, read-only reference packs, and the RAG comparison results side by
/// side.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MemoryView {
    /// Editable free-text notes, newest-first (R8.2).
    pub notes: Vec<MemoryItem>,
    /// Read-only what/how/why reference packs (R8.2).
    pub references: Vec<MemoryItem>,
    /// RAG performance comparison results (R8.3).
    pub comparisons: Vec<RagComparisonRow>,
}

/// The deadline, in ms, for showing the memory list and the RAG comparison list
/// after the window opens (R8.1, R8.2, R8.3 — "within 3 seconds").
pub const MEMORY_OPEN_DEADLINE_MS: u64 = 3_000;

/// Build the memory window's screen model (R8.2, R8.3). Returns a
/// plain-language failure message on a database error so the caller can keep
/// the previous screen and show a beginner-friendly notice (R8.7, R8.8).
pub fn memory_view(store: &dyn MemoryStore) -> Result<MemoryView, String> {
    let notes = store.list(MemoryKind::Note).map_err(|_| {
        "대화 기억을 불러오지 못했어요. 잠시 뒤 다시 열어 주세요.".to_string()
    })?;
    let references = store.list(MemoryKind::ReferencePack).map_err(|_| {
        "설명 자료를 불러오지 못했어요. 잠시 뒤 다시 열어 주세요.".to_string()
    })?;
    let comparisons = store.rag_comparisons().map_err(|_| {
        "성능 비교 결과를 불러오지 못했어요. 잠시 뒤 다시 열어 주세요.".to_string()
    })?;
    Ok(MemoryView {
        notes,
        references,
        comparisons,
    })
}

/// A pending deletion that must be confirmed before it runs (R8.5).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PendingDelete {
    /// The note id the user asked to delete.
    pub id: i64,
}

impl PendingDelete {
    /// Stage a deletion request for a note.
    pub fn new(id: i64) -> Self {
        Self { id }
    }
}

/// The plain-language confirmation shown before a note is deleted (R8.5, R8.8).
pub fn delete_confirmation_message(item: &MemoryItem) -> String {
    let preview: String = item.text.chars().take(20).collect();
    format!("이 기억을 지울까요? \u{201c}{}\u{2026}\u{201d} 지우면 되돌릴 수 없어요.", preview)
}

/// Run a staged deletion only if the user confirmed (R8.5).
///
/// * `confirmed == false`: nothing is deleted and `Ok(false)` is returned —
///   the store is untouched.
/// * `confirmed == true`: the note is deleted; a database failure yields a
///   plain-language message and the row is preserved (R8.7).
pub fn perform_delete(
    store: &dyn MemoryStore,
    pending: PendingDelete,
    confirmed: bool,
) -> Result<bool, String> {
    if !confirmed {
        return Ok(false);
    }
    store
        .delete(pending.id)
        .map(|()| true)
        .map_err(|_| "기억을 지우지 못했어요. 변경 내용은 저장되지 않았어요.".to_string())
}

// ---------------------------------------------------------------------------
// Sample-progress display connector (Task 11, R2.2, R2.9)
// ---------------------------------------------------------------------------

/// A beginner-friendly Korean label for a send grade (R2.4).
fn grade_label(grade: SendGrade) -> &'static str {
    match grade {
        SendGrade::Fake => "가짜(대량)",
        SendGrade::Memo => "나와의 채팅",
        SendGrade::Smoke => "스모크",
    }
}

/// Render the live-sample progress as plain-language lines (R2.2, R2.9).
///
/// The first line is the `누적/목표` ratio produced by
/// [`Progress::as_ratio`]; the second lists the per-grade counts. All display
/// logic lives here in the core so the Swift shell only draws the strings.
pub fn sample_progress_view(progress: &Progress) -> Vec<String> {
    let mut rows = Vec::new();
    rows.push(format!("학습 표본: {}", progress.as_ratio()));

    let grades: Vec<String> = progress
        .by_grade
        .iter()
        .map(|(grade, count)| format!("{} {}", grade_label(*grade), count))
        .collect();
    if !grades.is_empty() {
        rows.push(format!("등급별: {}", grades.join(" · ")));
    }
    rows
}

// ---------------------------------------------------------------------------
// Durability window display connector (Task 11, R1.5, R1.6, R1.7)
// ---------------------------------------------------------------------------

/// The durability (대량 검증) window's screen model (R1.5–R1.7).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DurabilityView {
    /// Plain-language lines for the automatic-reply batch.
    pub auto_reply: Vec<String>,
    /// Plain-language lines for the GeekNews batch.
    pub geeknews: Vec<String>,
    /// Plain-language lines describing the verdict (R1.6, R1.7).
    pub verdict: Vec<String>,
    /// The seed line, echoed for reproducibility (R1.8).
    pub seed: String,
}

/// Render one batch tally as plain-language lines (R1.5).
fn batch_lines(label: &str, tally: &BatchTally) -> Vec<String> {
    vec![
        format!(
            "{}: 시도 {} · 보낼 수 있음 {} · 막음 {} · 예외 {}",
            label, tally.attempted, tally.allowed, tally.fenced, tally.unhandled_panics
        ),
        format!(
            "{} 처리 시간: 중앙값 {}ms · 상위 5% {}ms",
            label, tally.p50_ms, tally.p95_ms
        ),
    ]
}

/// Build the durability window's screen model from a [`HarnessReport`] (R1.5–R1.7).
///
/// The verdict lines turn [`HarnessVerdict`] into plain language: a pass is a
/// single reassurance line, a fail lists each failed condition with its
/// measured value and next action (R1.7), and an abort reports the completed
/// and not-run counts (R1.10).
pub fn durability_view(report: &HarnessReport) -> DurabilityView {
    let mut verdict = Vec::new();
    match &report.verdict {
        HarnessVerdict::Pass => {
            verdict.push("합격했어요. 실제 전송과 외부 송신이 모두 0건이에요.".to_string());
        }
        HarnessVerdict::Fail(conditions) => {
            verdict.push("불합격이에요. 아래 항목을 확인해 주세요.".to_string());
            for c in conditions {
                verdict.push(format!("{} (측정값 {}) — {}", c.name, c.observed, c.next_step));
            }
        }
        HarnessVerdict::Aborted {
            completed,
            not_run,
            reasons,
        } => {
            verdict.push(format!(
                "검증을 끝까지 못 했어요. 끝낸 처리 {}건, 하지 못한 처리 {}건이에요.",
                completed, not_run
            ));
            for reason in reasons {
                verdict.push(reason.clone());
            }
        }
    }
    // Both tripwires must read zero; surface them so the window can show it.
    verdict.push(format!(
        "실제 전송 {}건 · 외부 송신 {}건",
        report.real_sends, report.network_egress
    ));

    DurabilityView {
        auto_reply: batch_lines("자동 답변", &report.auto_reply),
        geeknews: batch_lines("긱뉴스", &report.geeknews),
        verdict,
        seed: format!("무작위 시드: {}", report.seed),
    }
}

// ---------------------------------------------------------------------------
// Coverage window display connector (Task 11, R5.5, R5.9)
// ---------------------------------------------------------------------------

/// The coverage (기능 점검) window's screen model (R5.5).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CoverageView {
    /// The overall count summary line (R5.5).
    pub summary: String,
    /// Whether the four counts sum to the total, and the overall pass state
    /// (R5.5, R5.9), rendered as a plain-language line.
    pub status: String,
    /// One plain-language line per `(room, feature)` combination, in the matrix
    /// order.
    pub rows: Vec<String>,
}

/// A beginner-friendly Korean label for a feature (R5.1).
fn feature_label(feature: Feature) -> &'static str {
    match feature {
        Feature::AutoReply => "자동 답변",
        Feature::GeekNews => "긱뉴스 게시",
        Feature::LinkForward => "링크 전달",
        Feature::TelegramRelay => "텔레그램 중계",
        Feature::ContextSearch => "맥락 검색",
        Feature::StyleApply => "말투 적용",
    }
}

/// A beginner-friendly Korean label for a verdict (R5.2).
fn verdict_label(verdict: &Verdict) -> &'static str {
    match verdict {
        Verdict::Pass => "정상",
        Verdict::Fail(_) => "실패",
        Verdict::Unsupported(_) => "미지원",
        Verdict::Blocked(_) => "차단",
    }
}

/// Render the four-count summary line (R5.5).
fn counts_summary(prefix: &str, total: usize, counts: &Counts) -> String {
    format!(
        "{} {}개 · 정상 {} · 실패 {} · 미지원 {} · 차단 {}",
        prefix, total, counts.pass, counts.fail, counts.unsupported, counts.blocked
    )
}

/// Build the coverage window's screen model from a [`CoverageMatrix`] (R5.5, R5.9).
///
/// The matrix stores cells in a `BTreeMap`, so iterating it yields a stable,
/// deterministic order regardless of which worker computed each cell (R5.14).
pub fn coverage_view(matrix: &CoverageMatrix) -> CoverageView {
    let summary = counts_summary("전체", matrix.total, &matrix.totals);

    let status = if matrix.total == 0 {
        "점검할 방이 없어요. 채팅방 창에서 자동화 대상 방을 추가해 주세요.".to_string()
    } else if matrix.overall_pass() {
        "전체 합격이에요. 실패한 조합이 없어요.".to_string()
    } else if !matrix.sums_match_total {
        "판정하지 못한 조합이 있어요. 잠시 뒤 다시 점검해 주세요.".to_string()
    } else {
        format!("실패한 조합이 {}개 있어요.", matrix.totals.fail)
    };

    let rows = matrix
        .cells
        .iter()
        .map(|((room, feature), verdict)| {
            format!(
                "{} · {} — {}",
                room.label(),
                feature_label(*feature),
                verdict_label(verdict)
            )
        })
        .collect();

    CoverageView {
        summary,
        status,
        rows,
    }
}

// ---------------------------------------------------------------------------
// Self-improvement window display connector (Task 11, R8.16)
// ---------------------------------------------------------------------------

/// The self-improvement (자기개선) window's screen model (R8.16).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ImprovementView {
    /// One plain-language line per history entry, newest first.
    Rows(Vec<String>),
    /// No history yet — a plain-language empty notice.
    Empty(String),
}

/// A beginner-friendly Korean label for a problem-signal kind (R8.2).
fn problem_kind_label(kind: &str) -> &'static str {
    match kind {
        "low_quality" => "품질 낮음",
        "reask" => "되물음",
        "repeat_question" => "질문 반복",
        "owner_correction" => "직접 정정",
        "topic_drift" => "주제 벗어남",
        _ => "문제 신호",
    }
}

/// Render a stored verdict code as plain language (R8.16).
fn improve_verdict_label(verdict: &str) -> String {
    if verdict == "promoted" {
        "새 설정으로 바꿨어요".to_string()
    } else if verdict == "rolled_back" {
        "이전 설정으로 되돌렸어요".to_string()
    } else if let Some(reason) = verdict.strip_prefix("rejected:") {
        format!("바꾸지 않았어요 ({})", reason)
    } else if verdict.is_empty() {
        "구성 적용".to_string()
    } else {
        verdict.to_string()
    }
}

/// Render one self-improvement history entry as a plain-language line (R8.16).
pub fn improvement_line(entry: &HistoryEntry) -> String {
    let mut line = String::new();
    if !entry.problem_kind.is_empty() {
        line.push_str(problem_kind_label(&entry.problem_kind));
        line.push_str(" · ");
    }
    line.push_str(&improve_verdict_label(&entry.verdict));
    if !entry.changed_keys.is_empty() {
        line.push_str(&format!(" (바뀐 항목: {})", entry.changed_keys));
    }
    line
}

/// Build the self-improvement window's screen model from the redacted history
/// (R8.16). Entries are expected newest-first; an empty history yields a
/// plain-language empty notice.
pub fn improvement_view(entries: &[HistoryEntry]) -> ImprovementView {
    if entries.is_empty() {
        return ImprovementView::Empty(
            "아직 자기개선 기록이 없어요. 대화가 쌓이면 여기에 표시돼요.".to_string(),
        );
    }
    ImprovementView::Rows(entries.iter().map(improvement_line).collect())
}

// ---------------------------------------------------------------------------
// Onboarding window display connector (Task 11, R9.6, R9.11)
// ---------------------------------------------------------------------------

/// The onboarding (권한 설정) window's screen model (R9.6, R9.11).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OnboardingView {
    /// Plain-language lines for currently available features (R9.11).
    pub available: Vec<String>,
    /// Plain-language lines for blocked features, each naming the required
    /// permission and how to grant it (R9.6, R9.11).
    pub blocked: Vec<String>,
}

/// A beginner-friendly Korean label for a capability (R9.11).
fn capability_label(capability: Capability) -> &'static str {
    match capability {
        Capability::AxSend => "메시지 보내기",
        Capability::TelegramRead => "텔레그램 읽기",
        Capability::LocalRead => "카카오톡 대화 읽기",
        Capability::ContextSearch => "맥락 검색",
        Capability::DatasetRefresh => "대화 반영(데이터셋 갱신)",
        Capability::CoverageVerify => "기능 점검",
        Capability::ScreenCaptureImages => "화면 캡처로 이미지 확보",
        Capability::GeekNewsPost => "긱뉴스 게시",
        Capability::LinkForward => "링크 전달",
    }
}

/// A beginner-friendly Korean label for a permission (R9.7).
fn permission_label(permission: Permission) -> &'static str {
    match permission {
        Permission::Accessibility => "손쉬운 사용",
        Permission::FullDiskAccess => "전체 디스크 접근",
        Permission::ScreenRecording => "화면 기록",
    }
}

/// Build the onboarding window's screen model from a [`CapabilityMap`] (R9.6,
/// R9.11).
///
/// The map itself is produced by [`crate::packaging::capabilities`], the single
/// pure function that decides which features each permission unlocks; this
/// connector only turns that decision into beginner-friendly Korean lines so
/// the Swift shell holds no branching (R9.11).
pub fn onboarding_view(map: &CapabilityMap) -> OnboardingView {
    let available = map
        .available
        .iter()
        .map(|c| capability_label(*c).to_string())
        .collect();
    let blocked = map
        .blocked
        .iter()
        .map(|(capability, permission, how)| {
            format!(
                "{} — 필요 권한: {}. {}",
                capability_label(*capability),
                permission_label(*permission),
                how
            )
        })
        .collect();
    OnboardingView { available, blocked }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::logging::SqliteHistoryStore;
    use crate::memory::{MemoryDraft, SqliteMemoryStore};

    // ----- Menu model (R9.1, R5.1) -----

    #[test]
    fn menu_has_no_refresh_entry() {
        let menu = build_menu();
        assert!(
            !menu.contains(&MenuItemKind::Refresh),
            "the refresh menu item must be gone (R9.1)"
        );
    }

    #[test]
    fn menu_opens_all_nine_feature_windows_and_quit() {
        let menu = build_menu();
        // All nine feature windows appear, including the four live-ops windows.
        assert_eq!(AppWindow::all().len(), 9);
        for w in AppWindow::all() {
            assert!(
                menu.contains(&MenuItemKind::OpenWindow(w)),
                "menu must have a window entry for {:?}",
                w
            );
        }
        assert!(menu.contains(&MenuItemKind::Quit));
        // Reply + image models collapse into one ModelSettings entry (R5.1).
        let model_entries = menu
            .iter()
            .filter(|k| matches!(k, MenuItemKind::OpenWindow(AppWindow::ModelSettings)))
            .count();
        assert_eq!(model_entries, 1);
        // The four new live-ops windows are present with distinct keys.
        for w in [
            AppWindow::Durability,
            AppWindow::Coverage,
            AppWindow::Improvement,
            AppWindow::Onboarding,
        ] {
            assert!(menu.contains(&MenuItemKind::OpenWindow(w)));
        }
    }

    // ----- Window contract (R1.4, R1.5, R10.4) -----

    #[test]
    fn reopening_focuses_instead_of_recreating() {
        let mut shell = WindowShell::new();
        let mut creates = 0;

        let first = shell
            .open_with(AppWindow::History, || {
                creates += 1;
                Ok(())
            })
            .expect("first open");
        assert_eq!(first, OpenOutcome::Opened);

        let second = shell
            .open_with(AppWindow::History, || {
                creates += 1;
                Ok(())
            })
            .expect("second open");
        assert_eq!(second, OpenOutcome::Focused);
        assert_eq!(creates, 1, "an open window must not be rebuilt (R1.4)");
        assert_eq!(shell.open_count(), 1);
        assert_eq!(shell.front(), Some(AppWindow::History));
    }

    #[test]
    fn failed_open_preserves_previous_state() {
        let mut shell = WindowShell::new();
        shell
            .open_with(AppWindow::Memory, || Ok(()))
            .expect("memory opens");
        assert_eq!(shell.front(), Some(AppWindow::Memory));

        let err = shell
            .open_with(AppWindow::History, || {
                Err(UiError::new(window_open_failure_message(AppWindow::History)))
            })
            .expect_err("history open fails");

        // Previous state untouched (R1.5, R10.4).
        assert!(!shell.is_open(AppWindow::History));
        assert!(shell.is_open(AppWindow::Memory));
        assert_eq!(shell.front(), Some(AppWindow::Memory));
        assert!(err.message.contains("기록"));
        assert!(err.message.contains("다시"));
        assert_eq!(shell.last_error(), Some(err.message.as_str()));
    }

    // ----- Auto-refresh state machine (R9.2, R9.3, R9.4) -----

    #[test]
    fn cadence_bounds_meet_requirements() {
        assert!(EVENT_REFRESH_DEADLINE_MS <= 3_000, "R9.2 within 3s");
        assert!(FALLBACK_REFRESH_INTERVAL_MS <= 60_000, "R9.3 at least every 60s");
    }

    #[test]
    fn change_event_makes_refresh_due_within_three_seconds() {
        let mut ar = AutoRefresh::new(0, "A".to_string());
        assert!(!ar.should_refresh(10), "no refresh due right after start");
        ar.note_change(1_000);
        assert!(ar.should_refresh(1_000));
        assert_eq!(ar.must_refresh_by(), Some(1_000 + EVENT_REFRESH_DEADLINE_MS));
    }

    #[test]
    fn fallback_fires_within_sixty_seconds_without_events() {
        let ar = AutoRefresh::new(0, "A".to_string());
        assert!(!ar.tick(59_000));
        assert!(ar.tick(FALLBACK_REFRESH_INTERVAL_MS));
    }

    #[test]
    fn failed_refresh_keeps_last_screen_and_flags_failure() {
        let mut ar = AutoRefresh::new(0, "A".to_string());
        ar.note_change(1_000);
        ar.apply_failure(1_200);
        assert_eq!(ar.screen(), "A", "last screen preserved on failure (R9.4)");
        assert!(ar.showing_failure());

        // A later successful refresh clears the failure and swaps the screen.
        ar.note_change(2_000);
        ar.apply_success(2_100, "B".to_string());
        assert_eq!(ar.screen(), "B");
        assert!(!ar.showing_failure());
    }

    // ----- History view (R2.3–R2.7) -----

    fn ev(flow: FlowKind, stage: Stage, status: StageStatus, code: &str, at: i64) -> PipelineEvent {
        PipelineEvent {
            trace_id: "trace".to_string(),
            flow,
            stage,
            status,
            result_code: code.to_string(),
            duration_ms: 7,
            at,
        }
    }

    #[test]
    fn history_view_is_empty_notice_when_no_records() {
        let store = SqliteHistoryStore::open_in_memory().expect("store");
        match history_view(&store, HISTORY_WINDOW_LIMIT) {
            HistoryView::Empty(msg) => assert!(msg.contains("기록이 없")),
            other => panic!("expected empty notice, got {other:?}"),
        }
    }

    #[test]
    fn history_view_lists_newest_first_in_plain_language() {
        let store = SqliteHistoryStore::open_in_memory().expect("store");
        store
            .append(ev(FlowKind::AutoReply, Stage::Detect, StageStatus::Success, "ok", 1_000))
            .expect("append older");
        store
            .append(ev(FlowKind::AutoReply, Stage::Model, StageStatus::InProgress, "run", 2_000))
            .expect("append newer");
        match history_view(&store, HISTORY_WINDOW_LIMIT) {
            HistoryView::Rows(rows) => {
                assert_eq!(rows.len(), 2);
                assert!(rows[0].contains("답변 만들기"), "newest first: {rows:?}");
                assert!(rows[0].contains("진행 중"));
                assert!(rows[1].contains("메시지 확인"));
                // No jargon leaks through the labels.
                assert!(!rows[0].contains("Model"));
            }
            other => panic!("expected rows, got {other:?}"),
        }
    }

    // ----- Memory view + delete confirmation (R8.2, R8.3, R8.5) -----

    #[test]
    fn memory_view_lists_notes_and_empty_comparisons() {
        let store = SqliteMemoryStore::open_in_memory().expect("store");
        store
            .upsert(MemoryDraft::new_note("첫 번째 기억"))
            .expect("note added");
        let view = memory_view(&store).expect("memory view");
        assert_eq!(view.notes.len(), 1);
        assert_eq!(view.notes[0].text, "첫 번째 기억");
        assert!(view.references.is_empty());
        assert!(view.comparisons.is_empty());
    }

    #[test]
    fn delete_only_runs_when_confirmed() {
        let store = SqliteMemoryStore::open_in_memory().expect("store");
        let item = store
            .upsert(MemoryDraft::new_note("지울 기억"))
            .expect("note added");
        let pending = PendingDelete::new(item.id);

        // Unconfirmed: nothing deleted.
        let deleted = perform_delete(&store, pending, false).expect("no-op");
        assert!(!deleted);
        assert_eq!(store.list(MemoryKind::Note).expect("list").len(), 1);

        // The confirmation message is plain language and references the note.
        let msg = delete_confirmation_message(&item);
        assert!(msg.contains("지울까요"));

        // Confirmed: deleted.
        let deleted = perform_delete(&store, pending, true).expect("delete");
        assert!(deleted);
        assert!(store.list(MemoryKind::Note).expect("list").is_empty());
    }

    // ----- New live-ops windows: keys and titles (Task 11) -----

    #[test]
    fn nine_windows_have_distinct_titles_and_keys() {
        let windows = AppWindow::all();
        assert_eq!(windows.len(), 9);
        let titles: BTreeSet<&str> = windows.iter().map(|w| w.menu_title()).collect();
        let keys: BTreeSet<&str> = windows.iter().map(|w| w.key()).collect();
        assert_eq!(titles.len(), 9, "every window title is distinct");
        assert_eq!(keys.len(), 9, "every window key is distinct");
        // Failure message names the new windows too (R1.5).
        assert!(window_open_failure_message(AppWindow::Durability).contains("대량 검증"));
        assert!(window_open_failure_message(AppWindow::Onboarding).contains("권한 설정"));
    }

    // ----- Sample progress view (R2.2, R2.9) -----

    #[test]
    fn sample_progress_view_renders_ratio_and_grades() {
        let mut by_grade = std::collections::BTreeMap::new();
        by_grade.insert(SendGrade::Fake, 10);
        by_grade.insert(SendGrade::Memo, 300);
        by_grade.insert(SendGrade::Smoke, 42);
        let progress = Progress {
            accumulated: 342,
            target: 1000,
            by_grade,
            last_commit_at: Some(1_000),
            next_allowed_at: Some(11_000),
        };
        let rows = sample_progress_view(&progress);
        assert!(rows[0].contains("342/1000"));
        assert!(rows[1].contains("나와의 채팅 300"));
    }

    // ----- Durability view (R1.6, R1.7) -----

    fn empty_report(verdict: HarnessVerdict) -> HarnessReport {
        HarnessReport {
            auto_reply: BatchTally {
                attempted: 1000,
                allowed: 600,
                fenced: 400,
                p50_ms: 3,
                p95_ms: 8,
                ..BatchTally::default()
            },
            geeknews: BatchTally {
                attempted: 100,
                allowed: 100,
                ..BatchTally::default()
            },
            seed: 7,
            real_sends: 0,
            network_egress: 0,
            confirmed_sends: 700,
            commit_success_records: 700,
            gate_mismatch: 0,
            verdict,
        }
    }

    #[test]
    fn durability_view_pass_and_fail() {
        let pass = durability_view(&empty_report(HarnessVerdict::Pass));
        assert!(pass.auto_reply[0].contains("시도 1000"));
        assert!(pass.verdict.iter().any(|l| l.contains("합격")));
        assert!(pass.seed.contains("7"));

        let fail = durability_view(&empty_report(HarnessVerdict::Fail(vec![
            crate::durability::FailedCondition {
                name: "real_sends_zero",
                observed: "3".to_string(),
                next_step: "확인해 주세요.".to_string(),
            },
        ])));
        assert!(fail.verdict.iter().any(|l| l.contains("불합격")));
        assert!(fail.verdict.iter().any(|l| l.contains("real_sends_zero")));
    }

    // ----- Coverage view (R5.5, R5.9) -----

    #[test]
    fn coverage_view_summary_and_rows() {
        let room = crate::coverage::RoomKey {
            chat_id: 1,
            title: "스터디".to_string(),
            needs_id_suffix: false,
        };
        let mut cells = std::collections::BTreeMap::new();
        cells.insert((room.clone(), Feature::AutoReply), Verdict::Pass);
        cells.insert(
            (room.clone(), Feature::ContextSearch),
            Verdict::Unsupported(crate::coverage::UnsupportedReason::FeatureDisabled),
        );
        let matrix = CoverageMatrix {
            total: 2,
            totals: Counts {
                pass: 1,
                fail: 0,
                unsupported: 1,
                blocked: 0,
            },
            cells,
            by_room: std::collections::BTreeMap::new(),
            by_feature: std::collections::BTreeMap::new(),
            sums_match_total: true,
        };
        let view = coverage_view(&matrix);
        assert!(view.summary.contains("전체 2개"));
        assert!(view.status.contains("전체 합격"));
        assert!(view.rows.iter().any(|r| r.contains("스터디") && r.contains("자동 답변") && r.contains("정상")));
        assert!(view.rows.iter().any(|r| r.contains("미지원")));
    }

    #[test]
    fn coverage_view_zero_rooms_notice() {
        let matrix = CoverageMatrix {
            total: 0,
            totals: Counts::default(),
            cells: std::collections::BTreeMap::new(),
            by_room: std::collections::BTreeMap::new(),
            by_feature: std::collections::BTreeMap::new(),
            sums_match_total: true,
        };
        let view = coverage_view(&matrix);
        assert!(view.status.contains("점검할 방이 없어요"));
    }

    // ----- Improvement view (R8.16) -----

    #[test]
    fn improvement_view_empty_and_rows() {
        assert!(matches!(improvement_view(&[]), ImprovementView::Empty(_)));

        let entries = vec![
            HistoryEntry {
                at: 2_000,
                problem_kind: "low_quality".to_string(),
                changed_keys: "prompt_version".to_string(),
                verdict: "promoted".to_string(),
                generation: Some(2),
            },
            HistoryEntry {
                at: 1_000,
                problem_kind: "reask".to_string(),
                changed_keys: String::new(),
                verdict: "rejected:gain_too_small".to_string(),
                generation: None,
            },
        ];
        match improvement_view(&entries) {
            ImprovementView::Rows(rows) => {
                assert!(rows[0].contains("품질 낮음"));
                assert!(rows[0].contains("새 설정으로 바꿨어요"));
                assert!(rows[0].contains("prompt_version"));
                assert!(rows[1].contains("바꾸지 않았어요"));
            }
            other => panic!("expected rows, got {other:?}"),
        }
    }

    // ----- Onboarding view (R9.6, R9.11) -----

    #[test]
    fn onboarding_view_partitions_available_and_blocked() {
        // Only full-disk-access granted: AX + telegram + screen-capture blocked.
        let state = crate::packaging::PermissionState::from_granted([
            crate::packaging::Permission::FullDiskAccess,
        ]);
        let map = crate::packaging::capabilities(&state);
        let view = onboarding_view(&map);
        assert_eq!(
            view.available.len() + view.blocked.len(),
            crate::packaging::Capability::ALL.len()
        );
        // Blocked lines name the required permission and how to grant it.
        assert!(view
            .blocked
            .iter()
            .any(|l| l.contains("메시지 보내기") && l.contains("손쉬운 사용") && l.contains("시스템 설정")));
        // Local read is available since full-disk-access is granted.
        assert!(view.available.iter().any(|l| l.contains("카카오톡 대화 읽기")));
    }
}
