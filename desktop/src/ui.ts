import { LAYOUT } from "./tokens";
import type { EmergencyState, RuntimeSnapshot } from "./contracts";
declare const __ALDEN_VERSION__: string;

export const MAIN_PANEL_CONTROLS = Object.freeze([] as const);

export function voiceErrorMessage(errorCode: string | null): string | null {
  switch (errorCode) {
    case "voice_utterance_too_long":
      return "말씀을 짧게 나눠서 다시 들려주세요.";
    case "voice_conversation_unavailable":
      return "이전 음성 대화를 불러오지 못했습니다. 대화를 다시 선택해 주세요.";
    case "voice_memory_budget_low":
      return "기기 메모리 여유가 부족해 음성 처리를 멈췄습니다.";
    case "voice_memory_budget_unavailable":
      return "기기 메모리 상태를 확인할 수 없어 음성 처리를 시작하지 않았습니다.";
    case "alden_wake_model_unavailable":
      return "‘올든’을 알아듣는 기능이 준비되지 않아 음성 입력을 시작하지 않았습니다.";
    case "mic_disconnected":
      return "마이크를 사용할 수 없습니다. 연결을 확인해 주세요.";
    case "mic_unavailable":
      return "마이크를 열 수 없습니다. 연결 상태를 확인해 주세요.";
    case "mic_access_required":
      return "음성 입력을 사용하려면 마이크 접근을 허용해 주세요.";
    case "mic_audio_gap":
      return "음성 입력의 일부를 놓쳐 중단했습니다. 다시 시작해 주세요.";
    case "voice_audio_processing_unavailable":
      return "음성 입력과 재생을 연결하지 못해 중단했습니다. 다시 시작해 주세요.";
    case "stt_empty":
      return "말씀을 알아듣지 못했습니다. 다시 말씀해 주세요.";
    case "generation_error":
      return "답변을 준비하지 못했습니다. 다시 말씀해 주세요.";
    case "tts_error":
      return "답변을 소리로 들려주지 못했습니다.";
    case "global_abort":
      return "음성 요청을 중단했습니다.";
    case null:
      return null;
    default:
      return "음성 기능을 시작하지 못했습니다.";
  }
}

export const SETTINGS_IDS = Object.freeze([
  "settings-room-popup",
  "settings-sync-source",
  "settings-activity-source",
  "settings-sync-card",
  "settings-knowledge-card",
  "knowledge-graph-canvas",
  "knowledge-accessible-nodes",
  "knowledge-expand-hop",
  "knowledge-back",
  "knowledge-overview",
  "knowledge-focus-title",
  "knowledge-relations",
  "knowledge-retrieve",
] as const);

export function mainPanelMarkup(): string {
  return `<main class="alden-panel" aria-label="올든">
    <header class="mini-graph-heading"><span class="mini-graph-brand"><canvas data-orb-role="brand" data-orb-size="28" aria-hidden="true"></canvas><strong>Alden</strong></span><span id="orb-activity" role="status" aria-live="polite">대화가 남기는 연결</span></header><div class="mini-graph-space"><canvas class="alden-core" width="${LAYOUT.coreSize}" height="${LAYOUT.coreSize}" aria-label="올든 3D 지식 그래프"></canvas><div class="knowledge-node-labels" aria-hidden="true"></div><div id="knowledge-accessible-nodes" class="sr-only" role="region" aria-label="지식 항목"></div></div><p id="panel-knowledge-title" class="mini-graph-title">카카오톡</p>
  </main>`;
}

function automationHistoryMarkup(page: 'reply' | 'geeknews', title: string): string {
  return `<section id="settings-page-${page}" class="settings-page conversation-workspace automation-history" role="tabpanel" aria-labelledby="settings-tab-${page}" hidden>
    <aside class="history-rail"><header><h1>${title}</h1><input id="${page}-search" type="search" placeholder="채팅방·내용 검색" aria-label="${title} 검색"><select id="${page}-filter" aria-label="${title} 결과"><option value="all">모든 결과</option><option value="confirmed" selected>전송 확인</option><option value="waiting">처리 중·대기</option><option value="unknown">확인 필요</option><option value="failed">실패</option><option value="cancelled">취소</option><option value="skipped">건너뜀</option></select></header><div id="${page}-list" class="history-room-list" role="list" aria-label="${title} 기록"></div><button id="${page}-older" type="button" class="history-older" hidden>이전 기록 더 보기</button></aside>
    <div class="history-reader"><header class="history-reader-heading automation-history-heading"><div><h2 id="${page}-title">${title}</h2><p id="${page}-status" role="status">기록을 불러옵니다.</p></div><button id="${page}-refresh" class="history-refresh" type="button">새로 고침</button></header><div id="${page}-detail" class="automation-history-detail"><p class="history-empty">기록을 선택하세요.</p></div></div>
  </section>`;
}

export function settingsMarkup(): string {
  return `<main class="settings-shell" aria-label="올든" data-settings-page="memory">
    <aside class="settings-sidebar">
      <div class="settings-brand"><canvas data-orb-role="brand" data-orb-size="48" aria-hidden="true"></canvas><strong>Alden</strong></div>
      <nav class="settings-nav" role="tablist" aria-label="대화 영역" aria-orientation="vertical">
<button id="settings-tab-memory" class="settings-nav-item" type="button" role="tab" data-settings-view="memory" aria-controls="settings-page-memory" aria-selected="true" tabindex="0"><canvas data-orb-role="graph" data-orb-size="20" aria-hidden="true"></canvas><span>지식 그래프</span></button>
<button id="settings-tab-conversation" class="settings-nav-item" type="button" role="tab" data-settings-view="conversation" aria-controls="settings-page-conversation" aria-selected="false" tabindex="-1"><i data-lucide="MessagesSquare" aria-hidden="true"></i><span>카카오톡 대화</span></button>
<button id="settings-tab-reply" class="settings-nav-item" type="button" role="tab" data-settings-view="reply" aria-controls="settings-page-reply" aria-selected="false" tabindex="-1"><i data-lucide="MessageCircle" aria-hidden="true"></i><span>카카오톡 답변</span></button>
<button id="settings-tab-geeknews" class="settings-nav-item" type="button" role="tab" data-settings-view="geeknews" aria-controls="settings-page-geeknews" aria-selected="false" tabindex="-1"><i data-lucide="Rss" aria-hidden="true"></i><span>긱뉴스 전송</span></button>
<button id="settings-tab-voice" class="settings-nav-item" type="button" role="tab" data-settings-view="voice" aria-controls="settings-page-voice" aria-selected="false" tabindex="-1"><canvas data-orb-role="voice" data-orb-size="20" aria-hidden="true"></canvas><span>음성 대화</span></button>
<button id="settings-tab-history" class="settings-nav-item" type="button" role="tab" data-settings-view="history" aria-controls="settings-page-history" aria-selected="false" tabindex="-1"><canvas data-orb-role="database" data-orb-size="20" aria-hidden="true"></canvas><span>기억 정리</span></button>
<button id="settings-tab-settings" class="settings-nav-item" type="button" role="tab" data-settings-view="settings" aria-controls="settings-page-settings" aria-selected="false" tabindex="-1"><i data-lucide="Settings" aria-hidden="true"></i><span>설정</span></button>
</nav>
      <div id="emergency-controls" class="settings-sidebar-footer"><button id="emergency-resume" class="operation-button" type="button" disabled aria-label="운영 상태 확인 중"><canvas data-orb-role="operation" data-orb-size="20" aria-hidden="true"></canvas><span id="emergency-status">일시 중지</span><span class="operation-action" aria-hidden="true">일시 중지</span></button></div><span id="app-version" class="settings-version">v${__ALDEN_VERSION__}</span>
    </aside>
    <div class="settings-main"><header class="settings-topbar"><p>Alden <span aria-hidden="true">/</span><strong id="settings-current-view">지식 그래프</strong></p></header>
      <div class="settings-content">
<section id="settings-page-conversation" class="settings-page conversation-workspace" role="tabpanel" aria-labelledby="settings-tab-conversation" hidden>
 <aside class="history-rail"><header><h1>카카오톡 대화</h1><input id="conversation-search" type="search" placeholder="채팅방 찾기" aria-label="카카오톡 대화 찾기"></header><div id="chat-room-list" role="list" class="history-room-list"></div></aside>
 <div class="history-reader"><header class="history-reader-heading"><h2 id="conversation-history-title">채팅방을 선택하세요</h2><p id="conversation-history-status" role="status">이 기기에 수집된 대화 기록입니다.</p></header><div id="conversation-placeholder" class="history-placeholder" role="status"><strong>대화를 불러옵니다</strong><span>잠시만 기다려 주세요.</span><button id="conversation-retry" type="button" hidden>다시 불러오기</button></div><div id="chat-message-list" class="message-scroll" role="log" aria-label="카카오톡 대화 기록"><div class="history-empty">채팅방별 대화 기록을 불러옵니다.</div></div><button id="conversation-history-older" class="history-older" type="button" hidden>이전 대화 더 보기</button></div>
 </section>
<section id="settings-page-voice" class="settings-page conversation-workspace" role="tabpanel" aria-labelledby="settings-tab-voice" hidden>
 <aside class="history-rail"><header><h1>음성 대화</h1><input id="voice-search" type="search" placeholder="대화 찾기" aria-label="음성 대화 찾기"></header><div id="voice-session-list" role="list" class="history-room-list"></div></aside>
 <div class="history-reader"><header class="history-reader-heading"><h2 id="voice-history-title">대화를 선택하세요</h2><p id="voice-history-status" role="status">확인된 말씀과 올든의 답변을 읽습니다.</p></header><div class="voice-reader-controls"><p id="voice-status" role="status">마이크가 꺼져 있습니다.</p><button id="voice-start" type="button">마이크 켜기</button></div><div id="voice-placeholder" class="history-placeholder" role="status"><strong>지난 음성 대화를 불러옵니다</strong><span></span><button id="voice-retry" type="button" hidden>다시 불러오기</button></div><div id="voice-message-list" class="message-scroll" role="log" aria-label="음성 대화 기록"><div class="history-empty">음성으로 나눈 대화가 여기에 남습니다.</div></div><button id="voice-history-older" class="history-older" type="button" hidden>이전 대화 더 보기</button></div>
 </section>
${automationHistoryMarkup('reply', '카카오톡 답변')}
${automationHistoryMarkup('geeknews', '긱뉴스 전송')}
<section id="settings-page-memory" class="settings-page" role="tabpanel" aria-labelledby="settings-tab-memory">
          <section id="settings-knowledge-card" class="knowledge-section" aria-labelledby="knowledge-title">
            <div class="knowledge-workspace"><div class="knowledge-hologram-shell"><canvas id="knowledge-graph-canvas" width="1600" height="1200" aria-label="대화 속 이름과 주제의 연결 그림"></canvas><div class="knowledge-node-labels" aria-hidden="true"></div><div id="knowledge-accessible-nodes" class="sr-only" role="region" aria-label="대화 검색 항목 목록"></div></div></div>
            <header class="knowledge-heading"><h1 id="knowledge-title">지식 그래프</h1><p id="knowledge-summary" role="status" aria-live="polite">불러오는 중</p><span id="knowledge-mode" class="tag" hidden>확인 중</span><p id="knowledge-sync" class="knowledge-sync" role="status" aria-live="polite">갱신 확인 중</p></header>
            <div class="knowledge-hologram-toolbar"><button id="knowledge-back" type="button" disabled>이전</button><button id="knowledge-overview" type="button" disabled>전체 보기</button><button id="knowledge-expand-hop" type="button" disabled>더 보기</button></div>
            <aside class="knowledge-focus-card" aria-label="선택한 지식" aria-live="polite" hidden><header class="knowledge-detail-heading"><div><span id="knowledge-node-kind">저장된 기억</span><strong id="knowledge-focus-title">선택한 지식</strong></div><button id="knowledge-focus-close" type="button" aria-label="선택한 지식 닫기"><i data-lucide="X" aria-hidden="true"></i></button></header><p id="knowledge-node-summary" class="knowledge-node-summary"></p><p id="knowledge-node-basis" class="knowledge-node-basis"></p><ul id="knowledge-node-facts" class="knowledge-node-facts" hidden></ul><div id="knowledge-relations" class="knowledge-relations"></div><h2 id="knowledge-evidence-heading" hidden>원문 근거</h2><div id="knowledge-node-evidence"></div><p id="knowledge-retrieve">항목을 선택하면 관련 대화를 찾아 보여드립니다.</p></aside>
          </section>
        </section>
        <section id="settings-page-history" class="settings-page db-history-page" role="tabpanel" aria-labelledby="settings-tab-history" hidden><header class="settings-page-heading"><h1>기억 정리</h1><p>수집된 대화를 검색할 수 있게 정리합니다.</p></header><section class="db-current" aria-live="polite"><span id="db-current-dot" class="operation-dot" aria-hidden="true"></span><div><strong id="db-current-title">갱신 상태 확인 중</strong><p id="db-current-detail"></p></div></section><div id="db-cycle-list" class="db-cycle-list" role="list" aria-label="기억 정리 기록"></div><button id="db-history-older" type="button" class="history-older" hidden>이전 갱신 더 보기</button></section>
        <section id="settings-page-settings" class="settings-page preferences-page" role="tabpanel" aria-labelledby="settings-tab-settings" hidden>
          <header class="preferences-heading"><div><h1>설정</h1><p>답변 방식과 채팅방 자동화.</p></div><span class="preferences-local"><i data-lucide="Laptop" aria-hidden="true"></i>이 기기에서 실행</span></header>
          <section class="automation-board" aria-labelledby="automation-title">
            <header class="preferences-section-heading"><div><h2 id="automation-title">채팅방 자동화</h2><p>등록한 방에서만 동작합니다.</p></div><button id="automation-new" type="button" class="preferences-add"><i data-lucide="Plus" aria-hidden="true"></i>새 등록</button></header>
            <div class="automation-workspace"><div class="automation-registry"><div class="automation-list-heading"><span>등록된 채팅방</span><span id="automation-count">확인 중</span></div><div id="automation-list"></div><p class="automation-footnote">재시작 후 적용됩니다.</p></div>
              <form id="automation-form" class="automation-editor"><h3 id="automation-editor-title">새 자동화 등록</h3><label class="preference-field">채팅방<div class="room-picker"><input id="automation-room-search" type="search" role="combobox" aria-label="채팅방 검색" aria-autocomplete="list" aria-controls="automation-room-options" aria-expanded="false" autocomplete="off" placeholder="채팅방 검색" required><select id="automation-room" hidden aria-hidden="true" tabindex="-1"></select><div id="automation-room-options" class="room-picker-options" role="listbox" aria-label="채팅방 검색 결과" hidden></div></div></label><label class="preference-field">표시 이름<input id="automation-title-input" maxlength="128" placeholder="채팅방 이름"></label>
                <label class="automation-toggle"><span><strong>자동 답변</strong><small>채팅방의 질문에 올든이 답합니다.</small></span><input id="automation-reply" type="checkbox" role="switch"><span class="preference-switch" aria-hidden="true"></span></label>
                <label class="automation-toggle"><span><strong>긱뉴스 자동 전송</strong><small>정해진 시간에 새 소식을 전합니다.</small></span><input id="automation-geeknews" type="checkbox" role="switch"><span class="preference-switch" aria-hidden="true"></span></label>
                <div class="automation-editor-footer"><button id="automation-cancel" type="button" class="preferences-cancel">취소</button><button type="submit" class="preferences-save">변경 저장</button></div>
              </form>
            </div><p id="automation-status" role="status" aria-live="polite" hidden></p>
          </section>
          <div class="preferences-options">
            <section class="preferences-option" aria-labelledby="model-title"><header class="preferences-section-heading"><div><h2 id="model-title">대화 모델</h2><p>이 기기의 로컬 LLM을 선택하세요.</p></div><i data-lucide="Sparkles" aria-hidden="true"></i></header>
              <div class="model-options"><button class="model-row selection" type="button" data-model-choice="fast" aria-pressed="true" disabled><span class="model-symbol"><i data-lucide="Zap" aria-hidden="true"></i></span><span class="model-copy"><strong>Qwen3.8 Flash Next</strong><span class="model-desc">빠른 대화</span></span><span class="model-selection-mark" aria-hidden="true"></span></button><button class="model-row" type="button" data-model-choice="deep" aria-pressed="false" disabled><span class="model-symbol"><i data-lucide="Waypoints" aria-hidden="true"></i></span><span class="model-copy"><strong>Qwen3.8 27B</strong><span class="model-desc">깊은 분석</span></span><span class="model-selection-mark" aria-hidden="true"></span></button></div>
              <p id="model-status" class="model-status-highlight" role="status" aria-live="polite">AI 답변 상태를 확인하고 있습니다.</p>
            </section>
            <section class="preferences-option preferences-voice" aria-labelledby="voice-title"><header class="preferences-section-heading"><div><h2 id="voice-title">음성 대화</h2><p>마이크를 켜고 말씀하세요.</p></div><i data-lucide="AudioLines" aria-hidden="true"></i></header><div class="voice-wake-line"><button id="open-voice-page" type="button">음성 대화 열기</button></div></section>
          </div>
          <details class="preferences-runtime"><summary><i data-lucide="ShieldCheck" aria-hidden="true"></i><span>현재 운영 상태</span><i data-lucide="ChevronDown" aria-hidden="true"></i></summary><div class="preferences-runtime-content"><div class="setting-control"><label for="settings-room-popup">현재 운영 대상</label><select id="settings-room-popup" aria-label="대상 채팅방"><option value="">확인 중</option></select><p id="room-summary" class="settings-field-note" role="status" aria-live="polite">운영 중인 채팅방을 불러오는 중입니다.</p></div><div id="settings-sync-card"><p id="settings-sync-source" role="status" aria-live="polite">대화 준비 상태를 확인하고 있습니다.</p><p id="settings-activity-source" class="settings-field-note">앱의 작업 상태를 확인하고 있습니다.</p></div></div></details>
        </section>
      </div></div>
  </main>`;
}

export function renderEmergencyState(state: EmergencyState, root: Document = document): void {
  const controls = root.querySelector<HTMLElement>("#emergency-controls");
  const status = root.querySelector<HTMLElement>("#emergency-status");
  const button = root.querySelector<HTMLButtonElement>("#emergency-resume");
  if (!controls || !status || !button) return;
  if (!state.latched) {
    controls.hidden = false;
    status.textContent = "일시 중지";
    button.dataset.paused="false";
    button.setAttribute('aria-label','올든 일시 중지');
    const action=button.querySelector('.operation-action');if(action)action.textContent='일시 중지';
    button.disabled = false;
    return;
  }
  status.textContent = "다시 시작";
  button.dataset.paused="true";
  button.setAttribute('aria-label','올든 운영 재개');
  const action=button.querySelector('.operation-action');if(action)action.textContent='다시 시작';
  button.disabled = false;
  controls.hidden = false;
}

function activityStateLabel(value: unknown): string {
  const state = safeDisplayString(value, "").toLowerCase();
  if (["active", "running", "in_progress", "processing"].includes(state)) return "진행 중";
  if (["ready", "complete", "completed", "done", "ok", "success"].includes(state)) return "완료";
  if (["queued", "pending", "waiting"].includes(state)) return "대기 중";
  if (["sending", "send", "publishing"].includes(state)) return "전송 중";
  if (["behind", "stale"].includes(state)) return "새로 확인 필요";
  if (["error", "failed", "unavailable", "blocked"].includes(state)) return "확인 필요";
  return "확인 중";
}

export function renderBackground(snapshot: RuntimeSnapshot, root: Document = document): void {
  const target = root.getElementById("settings-activity-source");
  if (!target) return;
  if (!snapshot.available) {
    target.textContent = "앱의 작업 상태를 불러오지 못했습니다.";
    return;
  }

  const background = snapshot.background;
  const empty = background.activity === 0
    && background.replyLoad === 0
    && background.geeknews.activity === 0
    && background.dbSync.activity === 0
    && background.geeknews.state === "unknown"
    && background.dbSync.state === "unknown"
    && background.caption.length === 0
    && !snapshot.pipeline.active
    && snapshot.jobs.length === 0;
  if (empty) {
    target.textContent = "백그라운드 활동이 없습니다.";
    return;
  }

  const pendingReplies = Math.round(Math.min(1, Math.max(0, background.replyLoad)) * 4);
  const pipeline = snapshot.pipeline.active ? " · 답변 준비 중" : "";
  const jobs = snapshot.jobs.length > 0 ? ` · 다른 작업 ${snapshot.jobs.length}건 진행 중` : "";
  target.textContent = `작업 현황 · 답변 대기 ${pendingReplies}건 · 긱뉴스 ${activityStateLabel(background.geeknews.state)} · 대화 준비 ${activityStateLabel(background.dbSync.state)}${pipeline}${jobs}`;
}

function safeDisplayString(value: unknown, fallback: string): string {
  if (value === undefined || value === null) return fallback;
  try {
    return String(value);
  } catch {
    return fallback;
  }
}

function syncRoomOptions(
  select: HTMLSelectElement,
  items: ReadonlyArray<{ value: string; label: string }>,
): void {
  // Stable snapshots must not rebuild a native menu the user is interacting with.
  if (select.options.length === items.length && items.every((item, index) => {
    const option = select.options[index];
    return option.value === item.value && option.textContent === item.label;
  })) return;

  const selected = select.value;
  const options = items.map((item) => {
    const option = select.ownerDocument.createElement("option");
    option.value = item.value;
    option.textContent = item.label;
    return option;
  });
  select.replaceChildren(...options);
  if (items.some((item) => item.value === selected)) select.value = selected;
}

export function renderRooms(snapshot: RuntimeSnapshot, root: Document = document): void {
  const rooms = snapshot.available ? snapshot.rooms : [];
  const popup = root.querySelector<HTMLSelectElement>("#settings-room-popup");
  if (popup) {
    syncRoomOptions(popup, rooms.length > 0
      ? rooms.map((room) => ({ value: String(room.chatId), label: room.title }))
      : [{ value: "", label: snapshot.available
        ? "등록된 채팅방이 없습니다." : "채팅방 목록을 불러오지 못했습니다." }]);
  }

  const summary = root.getElementById("room-summary");
  if (summary) {
    let message: string;
    if (!snapshot.available) {
      message = "채팅방 목록을 불러오지 못했습니다. 다시 확인해 주세요.";
    } else if (rooms.length === 0) {
      message = "등록된 채팅방이 없습니다.";
    } else {
      const enabled = rooms.filter((room) => room.autoReply);
      const ready = enabled.filter((room) => room.live && room.replyReadiness === "ready").length;
      const attention = enabled.length - ready;
      const off = rooms.length - enabled.length;
      const parts = [`등록된 채팅방 ${rooms.length}개`, `답변 가능 ${ready}개`];
      if (attention > 0) parts.push(`확인 필요 ${attention}개`);
      if (off > 0) parts.push(`자동 답변 꺼짐 ${off}개`);
      message = parts.join(" · ");
    }
    if (summary.textContent !== message) summary.textContent = message;
  }

}
