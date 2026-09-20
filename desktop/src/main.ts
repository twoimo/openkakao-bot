import { invoke } from "@tauri-apps/api/core";
import "./styles.css";
import { createCancellationToken, type CancellationToken, type RuntimeSnapshot } from "./contracts";
import { JarvisCore } from "./core/jarvis-core";
import { RenderLifecycle } from "./core/lifecycle";
import { KnowledgeHologram } from "./knowledge/hologram";
import {
  MAX_FOCUS_HOPS,
  ON_SCREEN_NODE_CAP,
  parseKnowledgeGraph,
  type KnowledgeEdge,
  type KnowledgeGraph,
  type KnowledgeNode,
  type KnowledgeView,
} from "./knowledge/graph-model";
import { cancelRuntimeRequest, fetchRuntimeSnapshot, fetchSettingsAction } from "./runtime";
import { mainPanelMarkup, settingsMarkup } from "./ui";
import { RESIDENT_MODEL_ID, SWAP_MODEL_ID } from "./tokens";

const app = document.querySelector<HTMLDivElement>("#app")!;
if (!app) throw new Error("app_root_missing");

const isSettings = new URLSearchParams(window.location.search).get("view") === "settings";
document.body.classList.add(isSettings ? "settings-view" : "panel-view");

function setText(id: string, value: string): void {
  const element = document.getElementById(id);
  if (element) element.textContent = value;
}

function renderRooms(snapshot: RuntimeSnapshot): void {
  const popup = document.querySelector<HTMLSelectElement>("#settings-room-popup");
  if (!popup) return;
  popup.replaceChildren();
  if (snapshot.rooms.length === 0) {
    const option = document.createElement("option");
    option.textContent = snapshot.available ? "등록된 방 없음" : "snapshot 확인 불가";
    option.value = "";
    popup.append(option);
    setText("room-summary", "방 목록이 비어 있거나 snapshot을 읽지 못했습니다.");
    return;
  }
  snapshot.rooms.forEach((room) => {
    const option = document.createElement("option");
    option.value = String(room.chatId);
    option.textContent = room.title;
    popup.append(option);
  });
  const live = snapshot.rooms.filter((room) => room.live).length;
  setText("room-summary", `등록 ${snapshot.rooms.length} · live ${live} · 본문 미전달`);
}

function renderModels(payload: Record<string, unknown> | null, snapshot: RuntimeSnapshot): void {
  const current = typeof payload?.model === "string" ? payload.model : snapshot.replyModelId;
  const normalized = current?.replace(/^mlx\//, "") ?? null;
  const residentRow = document.querySelector<HTMLElement>(`[data-model-id="${RESIDENT_MODEL_ID}"]`);
  const swapRow = document.querySelector<HTMLElement>(`[data-model-id="${SWAP_MODEL_ID}"]`);
  residentRow?.classList.toggle("selection", normalized === RESIDENT_MODEL_ID || normalized === null);
  swapRow?.classList.toggle("selection", normalized === SWAP_MODEL_ID);
  setText("model-status", normalized
    ? `현재 선택: ${normalized}`
    : "현재 모델 ID를 확인할 수 없습니다. 27B는 이 화면에서 로드하지 않습니다.");
}

function renderVoice(snapshot: RuntimeSnapshot): void {
  const voice = snapshot.voice;
  if (!voice.available) {
    setText("voice-status", "음성 런타임 상태를 아직 받지 못했습니다.");
    setText("voice-phrase", "호출어: 헤이 자비스");
    setText("voice-threshold", "임계값: 0.65 고정");
    setText("voice-custom", "한국어 커스텀 헤드: 상태 없음 · 영어 스톡만이면 한국어 호출은 실패합니다.");
    return;
  }
  const suffix = voice.errorCode ? ` · ${voice.errorCode}` : "";
  setText("voice-status", `상태 ${voice.state} · wake ${voice.wakeSource} · RMS ${voice.rms.toFixed(3)}${suffix}`);
  setText("voice-phrase", `호출어: ${voice.wakePhrase || "헤이 자비스"}`);
  setText("voice-threshold", `임계값: ${voice.threshold.toFixed(2)} 고정`);
  setText("voice-custom", voice.customModelSelected
    ? "한국어 커스텀 헤드: bundled ONNX 선택됨 (TTS 보정, 사람 음성 일반화 아님)"
    : "한국어 커스텀 헤드: 없음 · 영어 스톡만 사용 중, 한국어 호출은 실패합니다.");
}

function relationMeta(edge: KnowledgeEdge): string {
  const room = edge.roomId || edge.evidence.chatId || "room ?";
  const time = edge.validFrom
    ? `${edge.validFrom}${edge.validTo ? ` → ${edge.validTo}` : ""}`
    : "time ?";
  const evidence = edge.evidenceMessageId
    || edge.evidence.sourceEventIds[0]
    || edge.evidence.kind;
  return `${room} · ${time} · evidence ${evidence}`;
}

function renderKnowledgeRelations(graph: KnowledgeGraph, node: KnowledgeNode, view: KnowledgeView): void {
  const container = document.getElementById("knowledge-relations");
  if (!container) return;
  container.replaceChildren();
  const labels = new Map(graph.nodes.map((candidate) => [candidate.id, candidate.label]));
  const related = view.edges
    .filter((edge) => edge.source === node.id || edge.target === node.id)
    .sort((left, right) => right.weight - left.weight)
    .slice(0, 6);

  if (related.length === 0) {
    const empty = document.createElement("p");
    empty.className = "knowledge-empty";
    empty.textContent = "이 범위에 연결된 E-R-E 관계가 없습니다.";
    container.append(empty);
    return;
  }

  related.forEach((edge) => {
    const row = document.createElement("div");
    row.className = "knowledge-relation-row";
    const triple = document.createElement("strong");
    triple.textContent = `${labels.get(edge.source) ?? edge.source} —${edge.relation || "RELATED"}→ ${labels.get(edge.target) ?? edge.target}`;
    const meta = document.createElement("span");
    meta.textContent = relationMeta(edge);
    row.append(triple, meta);
    container.append(row);
  });
}

function setupKnowledgeGraph(
  payload: Record<string, unknown> | null,
  snapshot: RuntimeSnapshot,
): KnowledgeHologram | null {
  const graph = parseKnowledgeGraph(payload);
  const canvas = document.querySelector<HTMLCanvasElement>("#knowledge-graph-canvas");
  const expand = document.querySelector<HTMLButtonElement>("#knowledge-expand-hop");
  if (!canvas || !expand || graph.nodes.length === 0) {
    setText("knowledge-summary", "지식 그래프 payload를 읽지 못했거나 노드가 없습니다.");
    setText("knowledge-mode", "unavailable");
    return null;
  }

  setText(
    "knowledge-summary",
    `E-R-E ${graph.nodes.length} nodes · ${graph.edges.length} relations · 화면 최대 ${ON_SCREEN_NODE_CAP} nodes`,
  );
  setText("knowledge-mode", payload?.stale === true ? "GraphRAG · stale" : "GraphRAG · ready");

  let activeNodeId = "";
  const hologram = new KnowledgeHologram(canvas, graph, ({ node, view }) => {
    activeNodeId = node.id;
    setText("knowledge-focus-title", node.label);
    setText("knowledge-focus-meta", `${view.hops}-hop · ${view.nodes.length} nodes · ${view.edges.length} relations`);
    setText("knowledge-hop-label", `${view.hops}-hop · ${view.nodes.length}/${ON_SCREEN_NODE_CAP} nodes`);
    expand.disabled = view.hops >= MAX_FOCUS_HOPS;
    renderKnowledgeRelations(graph, node, view);
    setText("knowledge-retrieve", "GraphRAG retrieve 확인 중…");

    const localRoom = view.edges.find((edge) => edge.source === node.id || edge.target === node.id)?.roomId
      || node.evidence.chatId
      || String(snapshot.rooms[0]?.chatId ?? "");
    void fetchSettingsAction("knowledge-graph-focus", {
      query: node.label,
      nodeId: node.id,
      chatId: localRoom,
    }).then((focus) => {
      if (activeNodeId !== node.id) return;
      if (!focus || focus.ok !== true) {
        setText("knowledge-retrieve", "GraphRAG retrieve: 확인 불가");
        return;
      }
      const facts = Array.isArray(focus.facts) ? focus.facts.filter((item): item is string => typeof item === "string") : [];
      const mode = typeof focus.search_mode === "string" && focus.search_mode ? focus.search_mode : "unknown";
      setText("knowledge-retrieve", `retrieve ${mode} · facts ${facts.length}${facts[0] ? ` · ${facts[0]}` : ""}`);
    });
  });

  expand.addEventListener("click", () => {
    const view = hologram.expandOneHop();
    if (!view.focusId) return;
    const node = graph.nodes.find((candidate) => candidate.id === view.focusId);
    if (!node) return;
    setText("knowledge-focus-meta", `${view.hops}-hop · ${view.nodes.length} nodes · ${view.edges.length} relations`);
    setText("knowledge-hop-label", `${view.hops}-hop · ${view.nodes.length}/${ON_SCREEN_NODE_CAP} nodes`);
    expand.disabled = view.hops >= MAX_FOCUS_HOPS;
    renderKnowledgeRelations(graph, node, view);
  });
  return hologram;
}

async function bootSettings(): Promise<void> {
  app.innerHTML = settingsMarkup();
  const token = createCancellationToken();
  const [snapshot, models, dream, knowledge, graphPayload] = await Promise.all([
    fetchRuntimeSnapshot(token),
    fetchSettingsAction("models"),
    fetchSettingsAction("dream-rsi-status"),
    fetchSettingsAction("knowledge-graph-status"),
    fetchSettingsAction("knowledge-graph"),
  ]);
  renderRooms(snapshot);
  renderModels(models, snapshot);
  renderVoice(snapshot);

  if (dream) {
    setText("settings-dream-rsi-status", `status: ${String(dream.status ?? "unknown")} · selected_policy: ${String(dream.selected_policy ?? "none")}`);
    setText("settings-dream-rsi-gold", `gold_rows: ${String(dream.gold_rows ?? "unknown")} · gold_source_policy: ${String(dream.gold_source_policy ?? "unknown")}`);
  } else {
    setText("settings-dream-rsi-status", "status: 확인 불가 · selected_policy: 확인 불가");
    setText("settings-dream-rsi-gold", "gold_rows: 확인 불가 · gold_source_policy: 확인 불가");
  }

  if (knowledge) {
    const stale = knowledge.stale === true ? "stale" : "ready";
    setText("settings-sync-source", `동기화: ${stale}`);
    setText("settings-sync-copy", `격리 복제: ${String(knowledge.snapshot_status ?? "unknown")}`);
    setText("settings-sync-mode", `색인 모드: ${String(knowledge.indexing_mode ?? "unknown")}`);
    setText("settings-sync-index", `마지막 색인: ${String(knowledge.indexed_at ?? "unknown")} · indexed ${String(knowledge.indexed_count ?? 0)}`);
  } else {
    setText("settings-sync-source", "동기화: 확인 불가");
    setText("settings-sync-copy", "격리 복제: 확인 불가");
    setText("settings-sync-mode", "색인 모드: 확인 불가");
    setText("settings-sync-index", "마지막 색인: 확인 불가");
  }

  const room = snapshot.rooms[0];
  const slots = room ? ["대기", "대기", "대기"] : ["미확인", "미확인", "미확인"];
  setText("settings-slot-morning", slots[0]);
  setText("settings-slot-lunch", slots[1]);
  setText("settings-slot-evening", slots[2]);

  const hologram = setupKnowledgeGraph(graphPayload, snapshot);
  if (hologram) {
    Object.defineProperty(window, "__knowledgeRenderCount", { configurable: true, get: () => hologram.renderCount });
    const lifecycle = new RenderLifecycle(hologram, () => undefined, () => undefined);
    const deactivate = (): void => lifecycle.transition("hidden");
    window.addEventListener("blur", deactivate);
    window.addEventListener("pagehide", () => {
      lifecycle.transition("closed");
      hologram.dispose();
    });
    document.addEventListener("visibilitychange", () => {
      lifecycle.transition(document.visibilityState === "visible" ? "visible" : "hidden");
    });
    window.addEventListener("focus", () => {
      if (document.visibilityState === "visible") lifecycle.transition("visible");
    });
    lifecycle.transition(document.visibilityState === "visible" ? "visible" : "hidden");
  }
}

async function bootPanel(): Promise<void> {
  app.innerHTML = mainPanelMarkup();
  const canvas = document.querySelector<HTMLCanvasElement>(".jarvis-core");
  const gear = document.querySelector<HTMLButtonElement>("#gear");
  if (!canvas || !gear) throw new Error("panel_contract_missing");

  const core = new JarvisCore(canvas);
  Object.defineProperty(window, "__jarvisRenderCount", { configurable: true, get: () => core.renderCount });
  let pollTimer: number | null = null;
  let requestToken: CancellationToken | null = null;

  const stopTimers = (): void => {
    if (pollTimer !== null) {
      window.clearTimeout(pollTimer);
      pollTimer = null;
    }
    if (requestToken) {
      void cancelRuntimeRequest(requestToken);
      requestToken = null;
    }
  };

  const poll = async (): Promise<void> => {
    const token = createCancellationToken();
    requestToken = token;
    const snapshot = await fetchRuntimeSnapshot(token);
    if (requestToken === token && !token.cancelled) {
      requestToken = null;
      core.setSignals(snapshot.jobLoad, snapshot.voice.rms);
      pollTimer = window.setTimeout(() => void poll(), 2500);
    }
  };

  const lifecycle = new RenderLifecycle(
    { start: () => core.start(), stop: () => core.stop() },
    stopTimers,
    () => void poll(),
  );

  gear.addEventListener("click", () => {
    void invoke("open_settings");
  });

  const deactivate = (): void => lifecycle.transition("hidden");
  window.addEventListener("blur", deactivate);
  window.addEventListener("pagehide", () => lifecycle.transition("closed"));
  document.addEventListener("visibilitychange", () => {
    lifecycle.transition(document.visibilityState === "visible" ? "visible" : "hidden");
  });
  window.addEventListener("focus", () => {
    if (document.visibilityState === "visible") lifecycle.transition("visible");
  });
  lifecycle.transition(document.visibilityState === "visible" ? "visible" : "hidden");
}

if (isSettings) void bootSettings();
else void bootPanel();
