import { describe, expect, it } from "vitest";
import { AnimationLoop, type FrameScheduler } from "../core/animation-loop";
import { RenderLifecycle } from "../core/lifecycle";
import { parseJobEvent, parseRuntimeSnapshot, parseRuntimeSnapshotJson, serializeJobEvent } from "../contracts";
import {
  KnowledgeDrilldown,
  ON_SCREEN_NODE_CAP,
  type KnowledgeGraph,
} from "../knowledge/graph-model";
import {
  cancelModelSwap,
  prepareSwapModel,
  runBrowserTool,
  setResidentModel,
  swapToLargeModel,
  type SettingsInvoke,
} from "../runtime";
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
    expect(markup).toContain('id="model-owner-state" class="muted"');
    expect(markup).toContain("모델 소유권을 확인 중입니다.");
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

  it("sends the fixed explicit swap token contract and sanitizes success", async () => {
    const calls: Array<{ command: string; args?: Record<string, unknown> }> = [];
    const token = { id: "123e4567-e89b-42d3-a456-426614174000", cancelled: false };
    const invokeSwap: SettingsInvoke = async <T>(command: string, args?: Record<string, unknown>) => {
      calls.push({ command, args });
      return {
        ok: true,
        action: "model-swap",
        model: SWAP_MODEL_ID,
        stage: "ready",
        reason: "ready",
        stages: ["drain", "unload", "memory_check", "load", "probe", "ready"],
        stored: true,
        prepared: true,
        body: "drop-me",
        secret: "drop-me",
      } as T;
    };

    expect(await swapToLargeModel(token, invokeSwap)).toEqual({
      ok: true,
      action: "model-swap",
      model: SWAP_MODEL_ID,
      stage: "ready",
      reason: "ready",
      stages: ["drain", "unload", "memory_check", "load", "probe", "ready"],
      stored: true,
      prepared: true,
    });
    expect(calls).toEqual([{
      command: "fetch_settings_action",
      args: {
        action: "model-swap",
        model: SWAP_MODEL_ID,
        explicitOptIn: true,
        tokenId: token.id,
      },
    }]);
  });

  it.each([
    ["model_owner_unknown", "aborted"],
    ["model_owner_unmanaged", "aborted"],
    ["insufficient_free_memory", "aborted"],
    ["probe_failed_rollback_failed", "failed"],
  ])("preserves safe swap failure %s", async (reason, stage) => {
    const token = { id: "123e4567-e89b-42d3-a456-426614174000", cancelled: false };
    const failed: SettingsInvoke = async <T>() => ({
      ok: false,
      action: "model-swap",
      model: SWAP_MODEL_ID,
      stage,
      reason,
      stages: ["drain", "rollback"],
      stored: false,
      prepared: false,
    } as T);
    const result = await swapToLargeModel(token, failed);
    expect(result.ok).toBe(false);
    expect(result.reason).toBe(reason);
    expect(result.stage).toBe(stage);
  });

  it("does not invoke swap for an already-cancelled token", async () => {
    let invoked = false;
    const invokeSwap: SettingsInvoke = async <T>() => {
      invoked = true;
      return {} as T;
    };
    const result = await swapToLargeModel(
      { id: "123e4567-e89b-42d3-a456-426614174000", cancelled: true },
      invokeSwap,
    );
    expect(invoked).toBe(false);
    expect(result.reason).toBe("cancelled");
  });

  it("uses the dedicated cooperative model-swap cancellation command", async () => {
    const calls: Array<{ command: string; args?: Record<string, unknown> }> = [];
    const token = { id: "123e4567-e89b-42d3-a456-426614174000", cancelled: false };
    const cancelInvoke: SettingsInvoke = async <T>(command: string, args?: Record<string, unknown>) => {
      calls.push({ command, args });
      return true as T;
    };
    await cancelModelSwap(token, cancelInvoke);
    await cancelModelSwap(token, cancelInvoke);
    expect(token.cancelled).toBe(true);
    expect(calls).toEqual([{
      command: "cancel_model_swap",
      args: { tokenId: token.id },
    }]);
  });

  it.each(["cancelled_rollback_failed", "memory_budget_unavailable_rollback_failed", "ready"])(
    "preserves the backend outcome after cancellation: %s", async (reason) => {
      const token = { id: "123e4567-e89b-42d3-a456-426614174000", cancelled: false };
      const backend: SettingsInvoke = async <T>() => {
        token.cancelled = true;
        return { ok: reason === "ready", action: "model-swap", model: SWAP_MODEL_ID,
          stage: reason === "ready" ? "ready" : "failed", reason,
          stored: reason === "ready", prepared: reason === "ready", stages: [] } as T;
      };
      const result = await swapToLargeModel(token, backend);
      expect(result.reason).toBe(reason);
      expect(result.ok).toBe(reason === "ready");
    },
  );

  it("does not turn a transport failure into confirmed cancellation", async () => {
    const token = { id: "123e4567-e89b-42d3-a456-426614174000", cancelled: false };
    const failed: SettingsInvoke = async () => { token.cancelled = true; throw new Error("python_timed_out"); };
    expect((await swapToLargeModel(token, failed)).reason).toBe("model_residency_uncertain");
  });

  it("keeps an unacknowledged cancellation retryable", async () => {
    const token = { id: "123e4567-e89b-42d3-a456-426614174000", cancelled: false };
    await cancelModelSwap(token, async <T>() => false as T);
    expect(token.cancelled).toBe(false);
    await cancelModelSwap(token, async <T>() => true as T);
    expect(token.cancelled).toBe(true);
  });

  it("rejects ready envelopes carrying a failure reason", async () => {
    const token = { id: "123e4567-e89b-42d3-a456-426614174000", cancelled: false };
    const invalid: SettingsInvoke = async <T>() => ({ ok: true, action: "model-swap", model: SWAP_MODEL_ID,
      stage: "ready", reason: "cancelled_rollback_failed", stored: true, prepared: true } as T);
    expect((await swapToLargeModel(token, invalid)).ok).toBe(false);
  });
});

describe("owned browser tool bridge", () => {
  it("invokes only the bounded browser command without model or profile arguments", async () => {
    const calls: Array<{ command: string; args?: Record<string, unknown> }> = [];
    const token = { id: "123e4567-e89b-42d3-a456-426614174000", cancelled: false };
    const fakeInvoke: SettingsInvoke = async <T>(command: string, args?: Record<string, unknown>) => {
      calls.push({ command, args });
      return { ok: true, status: "completed", errorCode: "", result: "owned result" } as T;
    };

    expect(await runBrowserTool({ jobId: "browser-1", task: "inspect page" }, token, fakeInvoke)).toEqual({
      ok: true,
      status: "completed",
      errorCode: "",
      result: "owned result",
    });
    expect(calls).toEqual([{
      command: "run_browser_tool",
      args: { jobId: "browser-1", task: "inspect page", tokenId: token.id },
    }]);
    expect(JSON.stringify(calls)).not.toContain("profile");
    expect(JSON.stringify(calls)).not.toContain("model");
  });

  it("rejects invalid input before invoke and bounds untrusted results", async () => {
    const token = { id: "123e4567-e89b-42d3-a456-426614174000", cancelled: false };
    let invoked = false;
    const invokeFn: SettingsInvoke = async <T>() => {
      invoked = true;
      return {} as T;
    };
    expect((await runBrowserTool({ jobId: "../bad", task: "task" }, token, invokeFn)).errorCode)
      .toBe("browser_input_invalid");
    expect(invoked).toBe(false);

    const oversized: SettingsInvoke = async <T>() => ({
      ok: true,
      status: "completed",
      errorCode: "",
      result: "x".repeat(64 * 1024 + 1),
    } as T);
    expect((await runBrowserTool({ jobId: "browser-2", task: "task" }, token, oversized)).errorCode)
      .toBe("browser_result_invalid");

    const leaked: SettingsInvoke = async <T>() => ({
      ok: false,
      status: "failed",
      errorCode: "browser_job_failed",
      result: "",
      secret: "must not cross",
    } as T);
    expect((await runBrowserTool({ jobId: "browser-3", task: "task" }, token, leaked)).errorCode)
      .toBe("browser_bridge_unavailable");
  });

  it("preserves global abort and fails closed on timeout", async () => {
    const token = { id: "123e4567-e89b-42d3-a456-426614174000", cancelled: false };
    const aborted: SettingsInvoke = async <T>() => ({
      ok: false,
      status: "aborted",
      errorCode: "global_abort",
      result: "",
    } as T);
    expect(await runBrowserTool({ jobId: "browser-4", task: "task" }, token, aborted)).toEqual({
      ok: false,
      status: "aborted",
      errorCode: "global_abort",
      result: "",
    });

    const timedOut: SettingsInvoke = <T>() => Promise.reject(new Error("python_timed_out")) as Promise<T>;
    expect((await runBrowserTool({ jobId: "browser-5", task: "task" }, token, timedOut)).errorCode)
      .toBe("browser_bridge_unavailable");
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
