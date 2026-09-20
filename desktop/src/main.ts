import { invoke } from "@tauri-apps/api/core";
import "./styles.css";
import { createCancellationToken, type CancellationToken, type RuntimeSnapshot } from "./contracts";
import { JarvisCore } from "./core/jarvis-core";
import { RenderLifecycle } from "./core/lifecycle";
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

async function bootSettings(): Promise<void> {
  app.innerHTML = settingsMarkup();
  const token = createCancellationToken();
  const [snapshot, models, dream, knowledge] = await Promise.all([
    fetchRuntimeSnapshot(token),
    fetchSettingsAction("models"),
    fetchSettingsAction("dream-rsi-status"),
    fetchSettingsAction("knowledge-graph-status"),
  ]);
  renderRooms(snapshot);
  renderModels(models, snapshot);

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
}

async function bootPanel(): Promise<void> {
  app.innerHTML = mainPanelMarkup();
  const canvas = document.querySelector<HTMLCanvasElement>(".jarvis-core");
  const gear = document.querySelector<HTMLButtonElement>("#gear");
  if (!canvas || !gear) throw new Error("panel_contract_missing");

  const core = new JarvisCore(canvas);
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
      core.setSignals(snapshot.jobLoad, 0);
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
