import { describe, expect, it } from "vitest";
import { AnimationLoop, type FrameScheduler } from "../core/animation-loop";
import { RenderLifecycle } from "../core/lifecycle";
import { parseJobEvent, parseRuntimeSnapshot, parseRuntimeSnapshotJson, serializeJobEvent } from "../contracts";
import { LAYOUT } from "../tokens";
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
    });
    expect(snapshot.terminal).toEqual({ sent: 3, skipped: 2, deliveryUnknown: 1, burstSuperseded: 4 });
    expect(snapshot.contextSync).toEqual({ mode: "async", waited: false });
    expect(snapshot.jobLoad).toBe(0.7);
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
  });
});
