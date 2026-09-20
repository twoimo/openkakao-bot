import { LAYOUT, RESIDENT_MODEL_ID, SWAP_MODEL_ID } from "./tokens";

export const MAIN_PANEL_CONTROLS = Object.freeze(["gear"] as const);
export const SETTINGS_IDS = Object.freeze([
  "settings-room-popup",
  "settings-sync-source",
  "settings-sync-copy",
  "settings-sync-mode",
  "settings-sync-index",
  "settings-sync-card",
  "settings-dream-rsi-status",
  "settings-dream-rsi-gold",
  "settings-dream-rsi-card",
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
      <div class="model-row selection" data-model-id="${RESIDENT_MODEL_ID}">
        <div><strong>Flash-Next</strong><p>${RESIDENT_MODEL_ID}</p></div><span class="tag">기본 상주</span>
      </div>
      <div class="model-row" data-model-id="${SWAP_MODEL_ID}">
        <div><strong>Qwen3.8 27B</strong><p>${SWAP_MODEL_ID}</p></div><span class="tag muted-tag">온디맨드 스왑 · 미로딩</span>
      </div>
      <p id="model-status" class="muted">모델 목록을 확인 중입니다. 이 화면은 27B를 준비하거나 로드하지 않습니다.</p>
    </section>

    <section class="settings-card" aria-labelledby="voice-title">
      <div class="section-heading"><h2 id="voice-title">Voice</h2><span class="tag muted-tag">로컬 전용</span></div>
      <p id="voice-status">음성 런타임 상태를 확인 중입니다.</p>
      <p class="muted">“헤이 자비스” · RMS는 acoustic pulse lattice 진폭에만 반영됩니다.</p>
    </section>

    <section id="settings-sync-card" class="settings-card knowledge-accent" aria-labelledby="sync-title">
      <div class="section-heading"><h2 id="sync-title">카카오 DB 동기화 · 색인</h2><span class="tag">GraphRAG 준비</span></div>
      <p id="settings-sync-source">동기화: 확인 중</p>
      <p id="settings-sync-copy">격리 복제: 확인 중</p>
      <p id="settings-sync-mode">색인 모드: 확인 중</p>
      <p id="settings-sync-index">마지막 색인: 확인 중</p>
    </section>

    <section id="settings-dream-rsi-card" class="settings-card" aria-labelledby="dream-title">
      <div class="section-heading"><h2 id="dream-title">DREAM-RSI</h2><span class="tag muted-tag">체크포인트 provenance</span></div>
      <p id="settings-dream-rsi-status">status: 확인 중 · selected_policy: 확인 중</p>
      <p id="settings-dream-rsi-gold">gold_rows: 확인 중 · gold_source_policy: 확인 중</p>
    </section>

    <section class="settings-card" aria-labelledby="knowledge-title">
      <div class="section-heading"><h2 id="knowledge-title">Knowledge</h2><span class="tag muted-tag">drilldown shell</span></div>
      <p>근거는 신뢰되지 않은 evidence로 취급합니다. GraphRAG drilldown을 위한 자리만 마련하며 true BM25+Dense+RRF는 이 단위에서 구현하지 않습니다.</p>
      <div class="slot-grid" aria-label="GeekNews 슬롯">
        <span>아침 <b id="settings-slot-morning">대기</b></span>
        <span>점심 <b id="settings-slot-lunch">대기</b></span>
        <span>저녁 <b id="settings-slot-evening">대기</b></span>
      </div>
    </section>

    <section class="settings-card" aria-labelledby="history-title">
      <div class="section-heading"><h2 id="history-title">History</h2><span class="tag muted-tag">안전 요약 shell</span></div>
      <p>채팅 본문·프롬프트·토큰을 공유 이벤트에 싣지 않습니다. 기록 UI 연결은 후속 단위에서 안전 필드만 사용합니다.</p>
    </section>
  </main>`;
}
