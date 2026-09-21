import { LAYOUT, RESIDENT_MODEL_ID, SWAP_MODEL_ID } from "./tokens";
import type { RuntimeSnapshot } from "./contracts";

export const MAIN_PANEL_CONTROLS = Object.freeze(["gear"] as const);
export const SETTINGS_IDS = Object.freeze([
  "settings-room-popup",
  "model-owner-state",
  "mlx-server-state",
  "settings-hardware-status",
  "settings-sync-source",
  "settings-activity-source",
  "settings-sync-copy",
  "settings-sync-mode",
  "settings-sync-index",
  "settings-sync-card",
  "settings-dream-rsi-status",
  "settings-dream-rsi-gold",
  "settings-dream-rsi-card",
  "settings-knowledge-card",
  "knowledge-graph-canvas",
  "knowledge-expand-hop",
  "knowledge-focus-title",
  "knowledge-focus-meta",
  "knowledge-relations",
  "knowledge-retrieve",
  "settings-slot-morning",
  "settings-slot-lunch",
  "settings-slot-evening",
] as const);

export function mainPanelMarkup(): string {
  return `<main class="jarvis-panel" aria-label="Jarvis">
    <canvas class="jarvis-core" width="${LAYOUT.coreSize}" height="${LAYOUT.coreSize}" aria-label="Jarvis core"></canvas>
    <button id="gear" class="gear" type="button" aria-label="설정 열기" title="설정">⚙︎</button>
  </main>`;
}

export function settingsMarkup(): string {
  return `<main class="settings-shell">
    <header class="settings-header">
      <p class="eyebrow">OPENKAKAO · LOCAL</p>
      <h1>Jarvis 설정</h1>
      <p>하나의 창에서 대상 방과 로컬 AI, 지식 상태를 확인합니다.</p>
    </header>

    <section class="settings-card" aria-labelledby="rooms-title">
      <div class="section-heading"><h2 id="rooms-title">대상 채팅방</h2><span class="status-dot" aria-hidden="true"></span></div>
      <select id="settings-room-popup" aria-label="대상 채팅방"><option value="">확인 중</option></select>
      <p id="room-summary" class="muted">snapshot에서 안전한 방 상태만 불러옵니다.</p>
    </section>

    <section class="settings-card" aria-labelledby="model-title">
      <div class="section-heading"><h2 id="model-title">AI 모델</h2><span class="tag">온디바이스</span></div>
      <button class="model-row selection" type="button" data-model-id="${RESIDENT_MODEL_ID}" aria-pressed="true" disabled>
        <span class="model-copy"><strong>Flash-Next</strong><span class="model-id">${RESIDENT_MODEL_ID}</span></span><span class="tag">기본 상주</span>
      </button>
      <button class="model-row" type="button" data-model-id="${SWAP_MODEL_ID}" aria-pressed="false" disabled>
        <span class="model-copy"><strong>Qwen3.8 27B</strong><span class="model-id">${SWAP_MODEL_ID}</span></span><span class="tag muted-tag">온디맨드 스왑 · 미로딩</span>
      </button>
      <p id="model-status" class="muted" role="status" aria-live="polite">모델 목록을 확인 중입니다. 27B는 사용자가 선택하고 안전 게이트를 통과할 때만 전환합니다.</p>
      <p id="model-owner-state" class="muted">모델 소유권을 확인 중입니다.</p>
      <p id="mlx-server-state" class="muted">앱 소유 MLX 서버 상태를 확인 중입니다.</p>
      <p id="settings-hardware-status" class="muted" role="status" aria-live="polite">온디바이스 하드웨어를 확인 중입니다.</p>
    </section>

    <section class="settings-card" aria-labelledby="voice-title">
      <div class="section-heading"><h2 id="voice-title">Voice</h2><span class="tag muted-tag">로컬 전용</span></div>
      <p id="voice-status">음성 런타임 상태를 확인 중입니다.</p>
      <p id="voice-phrase">호출어: 헤이 자비스</p>
      <p id="voice-threshold">임계값: 0.65 고정</p>
      <p id="voice-custom">한국어 커스텀 헤드: bundled ONNX 선택됨 (TTS 보정, 사람 음성 일반화 아님)</p>
      <p class="muted">RMS는 acoustic pulse lattice 진폭에만 반영됩니다. 영어 스톡 모델은 한국어 호출을 놓칩니다.</p>
      <button id="voice-start" type="button">마이크 세션 시작</button>
    </section>

    <section id="settings-sync-card" class="settings-card knowledge-accent" aria-labelledby="sync-title">
      <div class="section-heading"><h2 id="sync-title">카카오 DB 동기화 · 색인</h2><span class="tag">GraphRAG 준비</span></div>
      <p id="settings-sync-source">동기화: 확인 중</p>
      <p id="settings-activity-source" class="muted">백그라운드 활동을 확인 중입니다.</p>
      <p id="settings-sync-copy">격리 복제: 확인 중</p>
      <p id="settings-sync-mode">색인 모드: 확인 중</p>
      <p id="settings-sync-index">마지막 색인: 확인 중</p>
    </section>

    <section id="settings-dream-rsi-card" class="settings-card" aria-labelledby="dream-title">
      <div class="section-heading"><h2 id="dream-title">DREAM-RSI</h2><span class="tag muted-tag">체크포인트 provenance</span></div>
      <p id="settings-dream-rsi-status">status: 확인 중 · selected_policy: 확인 중</p>
      <p id="settings-dream-rsi-gold">gold_rows: 확인 중 · gold_source_policy: 확인 중</p>
    </section>

    <section id="settings-knowledge-card" class="settings-card knowledge-accent" aria-labelledby="knowledge-title">
      <div class="section-heading"><h2 id="knowledge-title">Knowledge</h2><span id="knowledge-mode" class="tag">GraphRAG</span></div>
      <p id="knowledge-summary">E-R-E 그래프를 읽는 중입니다. 메시지는 노드로 만들지 않습니다.</p>
      <div class="knowledge-hologram-shell">
        <canvas id="knowledge-graph-canvas" width="1280" height="640" aria-label="Knowledge E-R-E hologram graph"></canvas>
        <div class="knowledge-hologram-toolbar">
          <span id="knowledge-hop-label">overview · 최대 24 nodes</span>
          <button id="knowledge-expand-hop" type="button" disabled>+1 hop</button>
        </div>
      </div>
      <div class="knowledge-focus-card" aria-live="polite">
        <strong id="knowledge-focus-title">노드를 선택하면 2-hop으로 집중합니다.</strong>
        <p id="knowledge-focus-meta">subject · relation · object / room · time · evidence</p>
        <div id="knowledge-relations" class="knowledge-relations"></div>
        <p id="knowledge-retrieve">선택 시 기존 read-only GraphRAG retrieve를 사용합니다.</p>
      </div>
      <div class="slot-grid" aria-label="GeekNews 슬롯">
        <span>아침 <b id="settings-slot-morning">대기</b></span>
        <span>점심 <b id="settings-slot-lunch">대기</b></span>
        <span>저녁 <b id="settings-slot-evening">대기</b></span>
      </div>
    </section>

    <section class="settings-card" aria-labelledby="history-title">
      <div class="section-heading"><h2 id="history-title">History</h2><span class="tag muted-tag">안전 요약</span></div>
      <p id="history-summary" class="muted" role="status" aria-live="polite">최근 기록을 확인 중입니다.</p>
      <div id="history-list" class="knowledge-relations" role="list" aria-label="최근 답변 기록"></div>
    </section>
  </main>`;
}

export function renderHistory(snapshot: RuntimeSnapshot, root: Document = document): void {
  const summary = root.getElementById("history-summary");
  const list = root.getElementById("history-list");
  if (!summary || !list) return;
  list.replaceChildren();

  if (!snapshot.available) {
    summary.textContent = "기록을 확인할 수 없습니다.";
    return;
  }
  if (snapshot.recentReceipts.length === 0) {
    summary.textContent = "최근 기록이 없습니다.";
    return;
  }

  summary.textContent = `최근 ${snapshot.recentReceipts.length}건 · 본문·프롬프트 제외`;
  snapshot.recentReceipts.forEach((receipt) => {
    const row = root.createElement("div");
    row.className = "knowledge-relation-row";
    row.setAttribute("role", "listitem");

    const heading = root.createElement("strong");
    heading.textContent = `${receipt.displayTime || receipt.clock || "시간 미기록"} · ${receipt.title}`;

    const detail = root.createElement("span");
    detail.textContent = `${receipt.outcomeText || receipt.outcome} · ${receipt.reasonText || receipt.reasonCode} · 검색 ${receipt.retrievalState}`;

    row.append(heading, detail);
    list.append(row);
  });
}

export function renderBackground(snapshot: RuntimeSnapshot, root: Document = document): void {
  const target = root.getElementById("settings-activity-source");
  if (!target) return;
  if (!snapshot.available) {
    target.textContent = "백그라운드 상태를 확인할 수 없습니다.";
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
  const caption = background.caption ? ` · ${background.caption.slice(0, 120)}` : "";
  const pipeline = snapshot.pipeline.active ? ` · 파이프라인 ${snapshot.pipeline.stage}` : "";
  const jobs = snapshot.jobs.length > 0 ? ` · 진행 중 작업 ${snapshot.jobs.length}` : "";
  target.textContent = `백그라운드 · 답변 대기 ${pendingReplies} · 긱뉴스 ${background.geeknews.state} · DB 동기화 ${background.dbSync.state}${pipeline}${jobs}${caption}`;
}

export function renderHardware(snapshot: RuntimeSnapshot, root: Document = document): void {
  const target = root.getElementById("settings-hardware-status");
  if (!target) return;
  if (!snapshot.onDevice.available) {
    target.textContent = "하드웨어 정보를 확인할 수 없습니다.";
    return;
  }

  const statusLabel = snapshot.onDevice.statusLabel.slice(0, 240);
  if (statusLabel) {
    target.textContent = `온디바이스: ${statusLabel}`;
    return;
  }
  const chip = snapshot.onDevice.chip || "칩 미확인";
  target.textContent = `온디바이스: ${chip} · ${snapshot.onDevice.memoryGb.toFixed(1)}GB`;
}
