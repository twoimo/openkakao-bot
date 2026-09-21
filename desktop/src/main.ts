import { invoke } from "@tauri-apps/api/core";
import "./styles.css";
import {
  createCancellationToken,
  unavailableSnapshot,
  type CancellationToken,
  type RuntimeSnapshot,
} from "./contracts";
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
import {
  cancelRuntimeRequest,
  cancelModelSwap,
  fetchRuntimeSnapshot,
  fetchSettingsAction,
  setResidentModel,
  swapToLargeModel,
} from "./runtime";
import {
  RuntimeSnapshotPoller,
  browserPollScheduler,
  type PollTimerScheduler,
  type SnapshotCanceller,
  type SnapshotLoader,
} from "./runtime-poller";
import { mainPanelMarkup, settingsMarkup } from "./ui";
import { RESIDENT_MODEL_ID, SWAP_MODEL_ID } from "./tokens";
import { wireVoiceStart } from "./voice-controls";

export { RuntimeSnapshotPoller, type PollTimerScheduler } from "./runtime-poller";

const app = document.querySelector<HTMLDivElement>("#app")!;
if (!app) throw new Error("app_root_missing");

const isSettings = new URLSearchParams(window.location.search).get("view") === "settings";
document.body.classList.add(isSettings ? "settings-view" : "panel-view");

interface JarvisCoreControl {
  readonly renderCount: number;
  start(): void;
  stop(): void;
  setSignals(jobLoad: number, voiceRms: number): void;
  dispose(): void;
}

function setText(id: string, value: string): void {
  const element = document.getElementById(id);
  if (element) element.textContent = value;
}

function showPanelUnavailable(message: string, disableGear: boolean): void {
  app.dataset.state = "unavailable";
  const gear = document.querySelector<HTMLButtonElement>("#gear");
  if (gear) {
    gear.disabled = disableGear;
    gear.dataset.state = "unavailable";
    gear.setAttribute("aria-label", "설정 확인 불가");
    gear.title = message;
  }
  const panel = document.querySelector<HTMLElement>(".jarvis-panel");
  if (!panel) return;
  let status = document.getElementById("panel-status");
  if (!status) {
    status = document.createElement("p");
    status.id = "panel-status";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    status.style.cssText = "position:absolute;left:12px;right:12px;bottom:9px;margin:0;text-align:center;font-size:10px;line-height:1.35;color:var(--muted);pointer-events:none";
    panel.append(status);
  }
  status.textContent = message;
}

function clearPanelUnavailable(): void {
  app.dataset.state = "ready";
  document.getElementById("panel-status")?.remove();
  const gear = document.querySelector<HTMLButtonElement>("#gear");
  if (!gear) return;
  gear.disabled = false;
  delete gear.dataset.state;
  gear.setAttribute("aria-label", "설정 열기");
  gear.title = "설정";
}

function renderPanelUnavailable(): void {
  app.innerHTML = mainPanelMarkup();
  document.querySelector<HTMLCanvasElement>(".jarvis-core")
    ?.setAttribute("aria-label", "Jarvis core 확인 불가");
  showPanelUnavailable("Jarvis 상태를 확인할 수 없습니다.", true);
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

function modelButton(modelId: string): HTMLButtonElement | null {
  return document.querySelector<HTMLButtonElement>(`button[data-model-id="${modelId}"]`);
}

function setModelSelection(modelId: string | null): void {
  for (const candidate of [RESIDENT_MODEL_ID, SWAP_MODEL_ID]) {
    const button = modelButton(candidate);
    const selected = modelId === candidate;
    button?.classList.toggle("selection", selected);
    button?.setAttribute("aria-pressed", String(selected));
  }
}

function setModelBusy(busy: boolean): void {
  for (const candidate of [RESIDENT_MODEL_ID, SWAP_MODEL_ID]) {
    const button = modelButton(candidate);
    if (button) button.disabled = busy;
  }
}

function clearModelFailures(): void {
  modelButton(RESIDENT_MODEL_ID)?.classList.remove("model-failed");
  modelButton(SWAP_MODEL_ID)?.classList.remove("model-failed");
}

function renderModels(payload: Record<string, unknown> | null, snapshot: RuntimeSnapshot): void {
  const current = typeof payload?.model === "string" ? payload.model : snapshot.replyModelId;
  const normalized = current?.replace(/^mlx\//, "") ?? null;
  const selected = normalized === RESIDENT_MODEL_ID || normalized === SWAP_MODEL_ID
    ? normalized
    : normalized === null ? RESIDENT_MODEL_ID : null;
  setModelSelection(selected);
  setText("model-status", normalized
    ? `현재 선택: ${normalized}`
    : "현재 모델 ID를 확인할 수 없습니다. 27B는 명시적 선택과 안전 게이트가 필요합니다.");
}

function renderModelOwnerState(payload: Record<string, unknown> | null): void {
  const ownerState = typeof payload?.owner_state === "string" ? payload.owner_state : "";
  if (ownerState === "app_owned") {
    setText("model-owner-state", "앱 소유 확인됨");
    return;
  }
  if (ownerState === "model_owner_unmanaged") {
    setText("model-owner-state", "외부 소유 · 27B 전환 차단");
    return;
  }
  setText("model-owner-state", "소유권 미확인 · 27B 전환 차단");
}

/**
 * Show who owns the MLX gateway port.
 *
 * A foreign listener is reported, never adopted: the app only ever starts or
 * stops a server it can prove it started, so this line must not suggest that
 * an external MLX Core was taken over.
 */
function renderMlxServerState(payload: Record<string, unknown> | null): void {
  const ownerState = typeof payload?.owner_state === "string" ? payload.owner_state : "";
  const model = typeof payload?.model === "string" && payload.model ? ` · ${payload.model}` : "";
  if (ownerState === "app_owned") {
    setText("mlx-server-state", `앱 소유 서버 실행 중${model}`);
    return;
  }
  if (ownerState === "foreign_listener") {
    setText("mlx-server-state", "외부 런타임이 11234 포트를 점유 중 · 앱은 시작/중지하지 않습니다");
    return;
  }
  if (ownerState === "state_stale") {
    setText("mlx-server-state", "소유 기록이 프로세스와 불일치 · 27B 전환 차단");
    return;
  }
  if (ownerState === "state_invalid") {
    setText("mlx-server-state", "소유 기록을 신뢰할 수 없음 · 27B 전환 차단");
    return;
  }
  setText("mlx-server-state", "앱 소유 서버 없음");
}

export function modelSwapFailureText(reason: string): string {
  if (reason === "model_residency_uncertain") {
    return "27B 전환 결과를 확인하지 못했습니다. 재시도 전에 상주 모델과 진행 중인 요청을 점검해 주세요.";
  }
  if (reason === "model_swap_busy") return "다른 모델 전환이 진행 중입니다. 결과를 기다려 주세요.";
  if (reason === "cancelled") return "27B 전환을 취소했습니다. 기존 모델 상태를 유지합니다.";
  if (reason === "insufficient_free_memory" || reason === "memory_budget_unavailable") {
    return "27B 전환 중단 · 안전한 메모리 여유를 확인하지 못했습니다.";
  }
  if (reason === "model_owner_unmanaged") {
    return "27B 전환 차단 · 외부 MLX Core가 게이트웨이를 소유 중이라 앱이 안전하게 27B로 전환할 수 없습니다. 외부 MLX Core를 종료한 뒤 다시 시도하세요.";
  }
  if (reason === "model_owner_unknown" || reason === "model_owner_state_invalid" || reason === "model_owner_state_stale") {
    return "27B 전환 중단 · 상주 모델의 소유권을 증명할 수 없습니다.";
  }
  if (reason.includes("rollback_failed")) {
    return "27B 전환 실패 · 복구도 확인되지 않았습니다. 모델 상태를 점검해 주세요.";
  }
  if (reason === "load_failed" || reason === "probe_failed" || reason === "unload_failed") {
    return "27B 전환 실패 · 상주 모델 상태를 확인해 주세요.";
  }
  return "27B 전환 실패 · 기존 선택을 유지합니다.";
}

function wireModelSelection(): void {
  const resident = modelButton(RESIDENT_MODEL_ID);
  const swap = modelButton(SWAP_MODEL_ID);
  if (!resident || !swap) return;
  let activeSwap: CancellationToken | null = null;
  window.addEventListener("pagehide", () => {
    const token = activeSwap;
    activeSwap = null;
    if (token) void cancelModelSwap(token);
  }, { once: true });
  setModelBusy(false);

  resident.addEventListener("click", () => {
    void (async () => {
      clearModelFailures();
      setModelBusy(true);
      resident.setAttribute("aria-busy", "true");
      setText("model-status", "Flash-Next 기본 모델을 저장 중입니다…");
      const result = await setResidentModel();
      resident.removeAttribute("aria-busy");
      setModelBusy(false);
      if (!result.ok) {
        resident.classList.add("model-failed");
        setText("model-status", "모델 전환 실패 · 기존 선택 상태를 유지합니다.");
        return;
      }
      setModelSelection(RESIDENT_MODEL_ID);
      setText("model-status", `현재 선택: ${RESIDENT_MODEL_ID} · 저장 완료`);
    })();
  });

  swap.addEventListener("click", () => {
    void (async () => {
      clearModelFailures();
      setModelBusy(true);
      swap.setAttribute("aria-busy", "true");
      setText("model-status", "사용자 요청으로 Qwen3.8 27B 안전 전환을 확인 중입니다…");
      const token = createCancellationToken();
      activeSwap = token;
      const result = await swapToLargeModel(token);
      if (activeSwap === token) activeSwap = null;
      swap.removeAttribute("aria-busy");
      setModelBusy(false);
      if (!result.ok) {
        swap.classList.add("model-failed");
        setText("model-status", modelSwapFailureText(result.reason));
        return;
      }
      setModelSelection(SWAP_MODEL_ID);
      setText("model-status", "Qwen3.8 27B 전환 완료 · 로컬 probe와 저장을 확인했습니다.");
    })();
  });
}

function renderVoice(snapshot: RuntimeSnapshot): void {
  const voice = snapshot.voice;
  const customWakeText = voice.customModelSelected
    ? "한국어 커스텀 헤드: bundled ONNX 선택됨 (TTS 보정, 사람 음성 일반화 아님)"
    : "한국어 커스텀 헤드: 없음 · 영어 스톡만 사용 중, 한국어 호출은 실패합니다.";
  if (!voice.available) {
    setText("voice-status", "음성 런타임 상태를 아직 받지 못했습니다.");
    setText("voice-phrase", `호출어: ${voice.wakePhrase || "헤이 자비스"}`);
    setText("voice-threshold", `임계값: ${voice.threshold.toFixed(2)} 고정`);
    return;
  }
  const suffix = voice.errorCode ? ` · ${voice.errorCode}` : "";
  setText("voice-status", `상태 ${voice.state} · wake ${voice.wakeSource} · RMS ${voice.rms.toFixed(3)}${suffix}`);
  setText("voice-phrase", `호출어: ${voice.wakePhrase || "헤이 자비스"}`);
  setText("voice-threshold", `임계값: ${voice.threshold.toFixed(2)} 고정`);
  setText("voice-custom", customWakeText);
}

function renderSettingsUnavailable(): void {
  app.innerHTML = settingsMarkup();
  app.dataset.state = "unavailable";
  const snapshot = unavailableSnapshot("desktop_boot_failed");
  renderRooms(snapshot);
  renderModels(null, snapshot);
  renderModelOwnerState(null);
  renderMlxServerState(null);
  renderVoice(snapshot);
  setText("model-status", "모델 상태를 확인할 수 없습니다. 기존 선택은 변경하지 않습니다.");
  setText("settings-dream-rsi-status", "status: 확인 불가 · selected_policy: 확인 불가");
  setText("settings-dream-rsi-gold", "gold_rows: 확인 불가 · gold_source_policy: 확인 불가");
  setText("settings-sync-source", "동기화: 확인 불가");
  setText("settings-sync-copy", "격리 복제: 확인 불가");
  setText("settings-sync-mode", "색인 모드: 확인 불가");
  setText("settings-sync-index", "마지막 색인: 확인 불가");
  setText("knowledge-summary", "지식 그래프 상태를 확인할 수 없습니다.");
  setText("knowledge-mode", "unavailable");
  setText("settings-slot-morning", "미확인");
  setText("settings-slot-lunch", "미확인");
  setText("settings-slot-evening", "미확인");
  document.querySelectorAll<HTMLButtonElement>("button").forEach((button) => {
    button.disabled = true;
  });
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

interface SettingsBootDependencies {
  loadSnapshot: SnapshotLoader;
  loadAction: typeof fetchSettingsAction;
  wireVoice: typeof wireVoiceStart;
  invokeCommand: typeof invoke;
}

export async function bootSettings(
  overrides: Partial<SettingsBootDependencies> = {},
): Promise<void> {
  const dependencies: SettingsBootDependencies = {
    loadSnapshot: fetchRuntimeSnapshot,
    loadAction: fetchSettingsAction,
    wireVoice: wireVoiceStart,
    invokeCommand: invoke,
    ...overrides,
  };
  app.innerHTML = settingsMarkup();
  app.dataset.state = "loading";
  let hologram: KnowledgeHologram | null = null;
  try {
    const token = createCancellationToken();
    const results = await Promise.allSettled([
      dependencies.loadSnapshot(token),
      dependencies.loadAction("models"),
      dependencies.loadAction("model-owner-status"),
      dependencies.loadAction("mlx-server-status"),
      dependencies.loadAction("dream-rsi-status"),
      dependencies.loadAction("knowledge-graph-status"),
      dependencies.loadAction("knowledge-graph"),
    ] as const);
    const [
      snapshotResult,
      modelsResult,
      ownerResult,
      mlxServerResult,
      dreamResult,
      knowledgeResult,
      graphResult,
    ] = results;
    const snapshot = snapshotResult.status === "fulfilled"
      ? snapshotResult.value
      : unavailableSnapshot("settings_snapshot_unavailable");
    const models = modelsResult.status === "fulfilled" ? modelsResult.value : null;
    const owner = ownerResult.status === "fulfilled" ? ownerResult.value : null;
    const mlxServer = mlxServerResult.status === "fulfilled" ? mlxServerResult.value : null;
    const dream = dreamResult.status === "fulfilled" ? dreamResult.value : null;
    const knowledge = knowledgeResult.status === "fulfilled" ? knowledgeResult.value : null;
    const graphPayload = graphResult.status === "fulfilled" ? graphResult.value : null;
    const degraded = results.some((result) => result.status === "rejected") || !snapshot.available;

    renderRooms(snapshot);
    renderModels(models, snapshot);
    renderModelOwnerState(owner);
    renderMlxServerState(mlxServer);
    wireModelSelection();
    renderVoice(snapshot);
    dependencies.wireVoice(document, dependencies.invokeCommand);

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

    hologram = setupKnowledgeGraph(graphPayload, snapshot);
    app.dataset.state = degraded ? "unavailable" : "ready";
    if (hologram) {
      const graph = hologram;
      Object.defineProperty(window, "__knowledgeRenderCount", { configurable: true, get: () => graph.renderCount });
      const lifecycle = new RenderLifecycle(graph, () => undefined, () => undefined);
      let disposed = false;
      const deactivate = (): void => {
        if (!disposed) lifecycle.transition("hidden");
      };
      const visibilityChanged = (): void => {
        if (!disposed) lifecycle.transition(document.visibilityState === "visible" ? "visible" : "hidden");
      };
      const activate = (): void => {
        if (!disposed && document.visibilityState === "visible") lifecycle.transition("visible");
      };
      const close = (): void => {
        if (disposed) return;
        disposed = true;
        window.removeEventListener("blur", deactivate);
        window.removeEventListener("focus", activate);
        document.removeEventListener("visibilitychange", visibilityChanged);
        lifecycle.transition("closed");
        try {
          graph.dispose();
        } catch {
          // The page is closing; disposal remains fail-closed and idempotent here.
        }
      };
      window.addEventListener("blur", deactivate);
      window.addEventListener("pagehide", close, { once: true });
      document.addEventListener("visibilitychange", visibilityChanged);
      window.addEventListener("focus", activate);
      lifecycle.transition(document.visibilityState === "visible" ? "visible" : "hidden");
    }
  } catch {
    try {
      hologram?.dispose();
    } catch {
      // Rendering the fixed unavailable state takes precedence over cleanup errors.
    }
    renderSettingsUnavailable();
  }
}

type CommandInvoker = (command: string) => Promise<unknown>;

interface PanelBootDependencies {
  createCore: (canvas: HTMLCanvasElement) => JarvisCoreControl;
  loadSnapshot: SnapshotLoader;
  cancelSnapshot: SnapshotCanceller;
  makeToken: () => CancellationToken;
  pollScheduler: PollTimerScheduler;
  invokeCommand: CommandInvoker;
}

export async function bootPanel(
  overrides: Partial<PanelBootDependencies> = {},
): Promise<void> {
  const dependencies: PanelBootDependencies = {
    createCore: (canvas) => new JarvisCore(canvas),
    loadSnapshot: fetchRuntimeSnapshot,
    cancelSnapshot: cancelRuntimeRequest,
    makeToken: createCancellationToken,
    pollScheduler: browserPollScheduler,
    invokeCommand: invoke,
    ...overrides,
  };
  app.innerHTML = mainPanelMarkup();
  app.dataset.state = "loading";
  const canvas = document.querySelector<HTMLCanvasElement>(".jarvis-core");
  const gear = document.querySelector<HTMLButtonElement>("#gear");
  if (!canvas || !gear) {
    renderPanelUnavailable();
    return;
  }

  let core: JarvisCoreControl | null = null;
  let closePanel: (() => void) | null = null;
  try {
    core = dependencies.createCore(canvas);
    const activeCore = core;
    Object.defineProperty(window, "__jarvisRenderCount", { configurable: true, get: () => activeCore.renderCount });
    const polling = new RuntimeSnapshotPoller(
      activeCore,
      dependencies.loadSnapshot,
      dependencies.cancelSnapshot,
      dependencies.makeToken,
      dependencies.pollScheduler,
    );
    const lifecycle = new RenderLifecycle(activeCore, () => polling.stop(), () => polling.start());
    let disposed = false;
    let settingsPending = false;

    const deactivate = (): void => {
      if (!disposed) lifecycle.transition("hidden");
    };
    const visibilityChanged = (): void => {
      if (!disposed) lifecycle.transition(document.visibilityState === "visible" ? "visible" : "hidden");
    };
    const activate = (): void => {
      if (!disposed && document.visibilityState === "visible") lifecycle.transition("visible");
    };
    const openSettings = (): void => {
      if (disposed || settingsPending) return;
      settingsPending = true;
      clearPanelUnavailable();
      gear.disabled = true;
      gear.setAttribute("aria-busy", "true");
      void (async () => {
        let failed = false;
        try {
          await dependencies.invokeCommand("open_settings");
        } catch {
          failed = true;
          if (!disposed) showPanelUnavailable("설정을 확인할 수 없습니다.", false);
        } finally {
          settingsPending = false;
          if (!disposed) {
            gear.disabled = false;
            gear.removeAttribute("aria-busy");
            if (!failed) clearPanelUnavailable();
          }
        }
      })();
    };
    const close = (): void => {
      if (disposed) return;
      disposed = true;
      window.removeEventListener("blur", deactivate);
      window.removeEventListener("focus", activate);
      window.removeEventListener("pagehide", close);
      document.removeEventListener("visibilitychange", visibilityChanged);
      gear.removeEventListener("click", openSettings);
      try {
        lifecycle.transition("closed");
      } catch {
        polling.stop();
      }
      try {
        activeCore.dispose();
      } catch {
        // WebGL teardown must never surface an exception during page shutdown.
      }
    };
    closePanel = close;

    gear.addEventListener("click", openSettings);
    window.addEventListener("blur", deactivate);
    window.addEventListener("pagehide", close, { once: true });
    document.addEventListener("visibilitychange", visibilityChanged);
    window.addEventListener("focus", activate);
    lifecycle.transition(document.visibilityState === "visible" ? "visible" : "hidden");
    app.dataset.state = "ready";
  } catch {
    if (closePanel) closePanel();
    else {
      try {
        core?.dispose();
      } catch {
        // A partially constructed renderer is best-effort cleanup only.
      }
    }
    renderPanelUnavailable();
  }
}

interface DesktopBootDependencies {
  panel: () => Promise<void>;
  settings: () => Promise<void>;
}

export async function startDesktopApp(
  settingsView = isSettings,
  booters: DesktopBootDependencies = { panel: bootPanel, settings: bootSettings },
): Promise<void> {
  try {
    if (settingsView) await booters.settings();
    else await booters.panel();
  } catch {
    if (settingsView) renderSettingsUnavailable();
    else renderPanelUnavailable();
  }
}

void startDesktopApp().catch(() => undefined);
