import { describe, expect, it } from "vitest";
import { AnimationLoop, type FrameScheduler } from "../core/animation-loop";
import { RenderLifecycle } from "../core/lifecycle";
import { parseJobEvent, parseRuntimeSnapshot, parseRuntimeSnapshotJson, serializeJobEvent } from "../contracts";
import {
  KnowledgeDrilldown,
  ON_SCREEN_NODE_CAP,
  type KnowledgeGraph,
} from "../knowledge/graph-model";
import { prepareSwapModel, setResidentModel, type SettingsInvoke } from "../runtime";
import { LAYOUT, RESIDENT_MODEL_ID, SWAP_MODEL_ID } from "../tokens";
import { MAIN_PANEL_CONTROLS, mainPanelMarkup, settingsMarkup } from "../ui";

class FakeScheduler implements FrameScheduler {
  nowMs = 0;
  nextId = 1;
  callbacks = new Map<number, FrameRequestCallback>();
  request(callback: FrameRequestCallback): number {
    const id = this.nextId++;
    this.callbacks.set(id, callback);
    return id;
  }
  cancel(id: number): void { this.callbacks.delete(id); }
  now(): number { return this.nowMs; }
  step(ms: number): void {
    this.nowMs = ms;
    const queued = [...this.callbacks.values()];
    this.callbacks.clear();
    queued.forEach((callback) => callback(ms));
  }
}

describe("render lifecycle", () => {
  it.each(["hidden", "closed", "locked"] as const)("%s stops renders immediately and reopen does not duplicate RAF", (state) => {
    const scheduler = new FakeScheduler();
    const loop = new AnimationLoop(() => undefined, scheduler);
    let stoppedTimers = 0;
    const lifecycle = new RenderLifecycle(loop, () => { stoppedTimers += 1; }, () => undefined);
    lifecycle.transition("visible");
    lifecycle.transition("visible");
    expect(scheduler.callbacks.size).toBe(1);
    scheduler.step(100);
    expect(loop.renderCount).toBe(1);
    lifecycle.transition(state);
    expect(scheduler.callbacks.size).toBe(0);
    const frozen = loop.renderCount;
    scheduler.step(1000);
    expect(loop.renderCount - frozen).toBe(0);
    expect(stoppedTimers).toBe(1);
    lifecycle.transition("visible");
    lifecycle.transition("visible");
    expect(scheduler.callbacks.size).toBe(1);
  });

  it("clamps dt after a long resume gap", () => {
    const scheduler = new FakeScheduler();
    const dts: number[] = [];
    const loop = new AnimationLoop((dt) => dts.push(dt), scheduler);
    loop.start();
    scheduler.step(10_000);
    expect(dts).toHaveLength(1);
    expect(dts[0]).toBeLessThanOrEqual(0.05);
  });
});

describe("safe shared contracts", () => {
  it("JobEvent accepts only the six safe fields and never serializes bodies or secrets", () => {
    const safe = { jobId: "j1", kind: "reply", stage: "model", load: 0.4, time: 7, errorCode: null };
    expect(parseJobEvent(safe)).toEqual(safe);
    expect(parseJobEvent({ ...safe, chatBody: "private" })).toBeNull();
    expect(parseJobEvent({ ...safe, prompt: "ignore previous instructions" })).toBeNull();
    expect(parseJobEvent({ ...safe, token: "secret" })).toBeNull();
    expect(serializeJobEvent(safe)).toBe('{"jobId":"j1","kind":"reply","stage":"model","load":0.4,"time":7,"errorCode":null}');
  });

  it("maps terminal outcomes and async context sync", () => {
    const snapshot = parseRuntimeSnapshot({
      available: true,
      rooms: [],
      jobs: [],
      job_load: 0.7,
      terminal_counts: { sent: 3, skipped: 2, delivery_unknown: 1, burst_superseded: 4 },
      context_sync: { mode: "async", waited: false },
      reply_model_id: "model-a",
      voice: { available: true, state: "speaking", rms: 0.42, error_code: null, wake_source: "stock", updated_at: 10 },
    });
    expect(snapshot.terminal).toEqual({ sent: 3, skipped: 2, deliveryUnknown: 1, burstSuperseded: 4 });
    expect(snapshot.contextSync).toEqual({ mode: "async", waited: false });
    expect(snapshot.jobLoad).toBe(0.7);
    expect(snapshot.voice).toEqual({ available: true, state: "speaking", rms: 0.42, errorCode: null, wakeSource: "stock", updatedAt: 10, wakePhrase: "", threshold: 0.65, customModelSelected: false });
  });

  it("fails closed on bad JSON, empty lists, and a missing model id", () => {
    expect(parseRuntimeSnapshotJson("{bad").available).toBe(false);
    const empty = parseRuntimeSnapshot({ available: true, rooms: [], jobs: [], context_sync: { mode: "async", waited: false } });
    expect(empty.rooms).toEqual([]);
    expect(empty.jobs).toEqual([]);
    expect(empty.replyModelId).toBeNull();
  });
});

describe("layout and settings contract", () => {
  it("keeps live Extra geometry", () => {
    expect(LAYOUT).toMatchObject({ panelWidth: 276, panelHeight: 260, panelInset: 12, coreSize: 236, gearSize: 28 });
  });

  it("gear is the only main-panel control", () => {
    expect(MAIN_PANEL_CONTROLS).toEqual(["gear"]);
    expect((mainPanelMarkup().match(/<button\b/g) ?? []).length).toBe(1);
    expect(mainPanelMarkup()).toContain('id="gear"');
  });

  it("keeps sync card before DREAM-RSI and preserves AX ids", () => {
    const markup = settingsMarkup();
    expect(markup.indexOf('id="settings-sync-card"')).toBeLessThan(markup.indexOf('id="settings-dream-rsi-card"'));
    for (const id of [
      "settings-room-popup", "settings-sync-source", "settings-sync-copy", "settings-sync-mode", "settings-sync-index",
      "settings-sync-card", "settings-dream-rsi-status", "settings-dream-rsi-gold", "settings-dream-rsi-card",
      "settings-slot-morning", "settings-slot-lunch", "settings-slot-evening",
    ]) expect(markup).toContain(`id="${id}"`);
    expect(markup).toContain('id="voice-status"');
    expect(markup).toContain('id="voice-phrase"');
    expect(markup).toContain('id="voice-threshold"');
    expect(markup).toContain('id="voice-custom"');
    expect(markup).toContain('id="voice-start"');
    expect(mainPanelMarkup()).not.toContain('id="voice-start"');
    expect(markup).toContain('id="knowledge-graph-canvas"');
    expect(markup).toContain('id="knowledge-expand-hop"');
  });

  it("renders both local models as keyboard-native buttons with explicit selection state", () => {
    const markup = settingsMarkup();
    expect(markup).toContain(`data-model-id="${RESIDENT_MODEL_ID}"`);
    expect(markup).toContain(`data-model-id="${SWAP_MODEL_ID}"`);
    expect(markup).toContain('class="model-row selection" type="button"');
    expect(markup).toContain('aria-pressed="true"');
    expect(markup).toContain('class="model-row" type="button"');
    expect(markup).toContain('aria-pressed="false"');
    expect(markup).toContain('id="model-status" class="muted" role="status" aria-live="polite"');
  });
});

describe("local model settings bridge", () => {
  it("invokes only the fixed resident save and swap prepare contracts", async () => {
    const calls: Array<{ command: string; args?: Record<string, unknown> }> = [];
    const fakeInvoke: SettingsInvoke = async <T>(command: string, args?: Record<string, unknown>) => {
      calls.push({ command, args });
      if (args?.action === "model-set") {
        return { ok: true, action: "model-set", model: RESIDENT_MODEL_ID, stored: true, prepared: true, needs_prepare: false, prompt: "drop-me" } as T;
      }
      return { ok: true, action: "model-prepare", model: SWAP_MODEL_ID, stored: false, prepared: true, needs_prepare: false, secret: "drop-me" } as T;
    };

    expect(await setResidentModel(fakeInvoke)).toEqual({
      ok: true, action: "model-set", model: RESIDENT_MODEL_ID, stored: true, prepared: true, needsPrepare: false,
    });
    expect(await prepareSwapModel(fakeInvoke)).toEqual({
      ok: true, action: "model-prepare", model: SWAP_MODEL_ID, stored: false, prepared: true, needsPrepare: false,
    });
    expect(calls).toEqual([
      { command: "fetch_settings_action", args: { action: "model-set", model: RESIDENT_MODEL_ID } },
      { command: "fetch_settings_action", args: { action: "model-prepare", model: SWAP_MODEL_ID } },
    ]);
  });

  it("fails closed on invoke failure or a mismatched model response", async () => {
    const throwing: SettingsInvoke = <T>() => Promise.reject(new Error("python_timed_out")) as Promise<T>;
    const mismatched: SettingsInvoke = async <T>() => ({
      ok: true, action: "model-prepare", model: "remote/arbitrary", prepared: true, body: "private",
    } as T);
    expect((await setResidentModel(throwing)).ok).toBe(false);
    expect((await prepareSwapModel(mismatched)).ok).toBe(false);
  });

  it("keeps a failed 27B readiness check prepare-only without model promotion", async () => {
    const calls: Array<{ command: string; args?: Record<string, unknown> }> = [];
    const unavailable: SettingsInvoke = async <T>(command: string, args?: Record<string, unknown>) => {
      calls.push({ command, args });
      return {
        ok: false,
        action: "model-prepare",
        model: SWAP_MODEL_ID,
        stored: false,
        prepared: false,
        needs_prepare: true,
      } as T;
    };

    expect(await prepareSwapModel(unavailable)).toEqual({
      ok: false,
      action: "model-prepare",
      model: SWAP_MODEL_ID,
      stored: false,
      prepared: false,
      needsPrepare: false,
    });
    expect(calls).toEqual([
      { command: "fetch_settings_action", args: { action: "model-prepare", model: SWAP_MODEL_ID } },
    ]);
  });
});

function drilldownGraph(): KnowledgeGraph {
  const ids = ["root"];
  for (const prefix of ["a", "b", "c"]) {
    for (let index = 0; index < 10; index += 1) ids.push(`${prefix}${index}`);
  }
  const nodes = ids.map((id, index) => ({
    id,
    label: id,
    category: "entity",
    importance: 100 - index,
    updatedAt: 1,
    evidence: { kind: "seed" as const, sourceEventIds: [], chatId: "", confirmedAt: null, retracted: false },
  }));
  const edge = (source: string, target: string, weight: number) => ({
    source,
    relation: "RELATED_TO",
    target,
    context: "",
    weight,
    roomId: "room-1",
    validFrom: "2026-09-20T10:00:00+09:00",
    validTo: "",
    evidenceMessageId: "db:1",
    evidence: { kind: "ledger" as const, sourceEventIds: ["db:1"], chatId: "room-1", confirmedAt: null, retracted: false },
  });
  const edges = [];
  for (let index = 0; index < 10; index += 1) {
    edges.push(edge("root", `a${index}`, 100 - index));
    edges.push(edge(`a${index}`, `b${index}`, 90 - index));
    edges.push(edge(`b${index}`, `c${index}`, 80 - index));
  }
  return { nodes, edges };
}

describe("knowledge hologram drilldown", () => {
  it("click focuses the node at exactly 2 hops; another click does not add a hop", () => {
    const drilldown = new KnowledgeDrilldown(drilldownGraph());
    const focused = drilldown.clickNode("root");
    expect(focused.focusId).toBe("root");
    expect(focused.hops).toBe(2);
    expect(focused.nodes.some((node) => node.id === "b0")).toBe(true);
    expect(focused.nodes.some((node) => node.id === "c0")).toBe(false);
    expect(drilldown.clickNode("root").hops).toBe(2);
  });

  it("caps visible nodes and only reaches hop 3 through explicit expansion", () => {
    const drilldown = new KnowledgeDrilldown(drilldownGraph());
    expect(drilldown.clickNode("root").nodes).toHaveLength(21);
    const expanded = drilldown.expandOneHop();
    expect(expanded.hops).toBe(3);
    expect(expanded.nodes).toHaveLength(ON_SCREEN_NODE_CAP);
    expect(expanded.nodes.length).toBeLessThanOrEqual(ON_SCREEN_NODE_CAP);
  });

  it("has zero queued RAF callbacks while the settings graph is hidden", () => {
    const scheduler = new FakeScheduler();
    const loop = new AnimationLoop(() => undefined, scheduler);
    const lifecycle = new RenderLifecycle(loop, () => undefined, () => undefined);
    lifecycle.transition("visible");
    expect(scheduler.callbacks.size).toBe(1);
    scheduler.step(100);
    lifecycle.transition("hidden");
    expect(scheduler.callbacks.size).toBe(0);
    const frozen = loop.renderCount;
    scheduler.step(1000);
    expect(loop.renderCount).toBe(frozen);
  });
});
