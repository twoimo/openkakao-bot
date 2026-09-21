// @vitest-environment happy-dom
import { describe, expect, it, vi } from "vitest";
import { AnimationLoop, type FrameScheduler } from "../core/animation-loop";
import { JarvisCore } from "../core/jarvis-core";
import { RenderLifecycle } from "../core/lifecycle";
import { parseBackground, parseJobEvent, parseOnDevice, parsePipeline, parseRuntimeSnapshot, parseRuntimeSnapshotJson, serializeJobEvent } from "../contracts";
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
import { RuntimeSnapshotPoller } from "../runtime-poller";
import { LAYOUT, RESIDENT_MODEL_ID, SWAP_MODEL_ID } from "../tokens";
import { MAIN_PANEL_CONTROLS, mainPanelMarkup, renderBackground, renderHardware, renderHistory, settingsMarkup } from "../ui";

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

  it("JarvisCore reports zero renders while stopped", () => {
    const scheduler = new FakeScheduler();
    const loop = new AnimationLoop(() => undefined, scheduler);
    const core = Object.create(JarvisCore.prototype) as JarvisCore;
    Object.defineProperty(core, "loop", { value: loop });
    core.stop();
    scheduler.step(1000);
    expect(core.renderCount).toBe(0);
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

  it("normalizes per-source background state with bounded captions", () => {
    const parsed = parseBackground({
      activity: 1.4,
      caption: "총".repeat(130),
      reply_load: -0.5,
      geeknews: { state: "sending", activity: 0.3336, caption: "긱".repeat(130) },
      db_sync: { state: "unexpected", activity: Number.POSITIVE_INFINITY, caption: "DB" },
    });
    expect(parsed.activity).toBe(1);
    expect(parsed.caption).toHaveLength(120);
    expect(parsed.replyLoad).toBe(0);
    expect(parsed.geeknews).toEqual({ state: "sending", activity: 0.334, caption: "긱".repeat(120) });
    expect(parsed.dbSync).toEqual({ state: "unknown", activity: 0, caption: "DB" });
    expect(parseBackground(undefined)).toEqual({
      activity: 0,
      caption: "",
      replyLoad: 0,
      geeknews: { state: "unknown", activity: 0, caption: "" },
      dbSync: { state: "unknown", activity: 0, caption: "" },
    });
  });

  it("reads background from runtime snapshots without changing jobLoad", () => {
    const snapshot = parseRuntimeSnapshot({
      available: true,
      rooms: [],
      jobs: [],
      job_load: 0.8,
      context_sync: { mode: "async", waited: false },
      background: {
        activity: 0.6,
        replyLoad: 0.25,
        geeknews: { state: "confirmed", activity: 0.4, caption: "posted" },
        dbSync: { state: "ready", activity: 0.2, caption: "fresh" },
      },
    });
    expect(snapshot.jobLoad).toBe(0.8);
    expect(snapshot.background.replyLoad).toBe(0.25);
    expect(snapshot.background.geeknews.state).toBe("confirmed");
    expect(snapshot.background.dbSync.state).toBe("ready");
  });

  it("parses pipeline stage selection, allowlists, and safe defaults", () => {
    const idle = parsePipeline({
      active_index: null,
      event_id: "none",
      outcome: "none",
      stages: [
        { id: "detect", state: "idle" },
        { id: "authorize", state: "idle" },
        { id: "queue", state: "idle" },
        { id: "context", state: "idle" },
        { id: "model", state: "idle" },
        { id: "delay", state: "idle" },
        { id: "send", state: "idle" },
        { id: "confirm", state: "idle" },
      ],
    });
    expect(idle).toEqual({ active: false, stage: "none", stageIndex: 0, stageTotal: 8, outcome: "none" });

    const fallbackActive = parsePipeline({
      active_index: null,
      outcome: "scheduled",
      stages: [
        { id: "detect", state: "done" },
        { id: "delay", state: "active" },
      ],
    });
    expect(fallbackActive).toEqual({ active: true, stage: "delay", stageIndex: 1, stageTotal: 2, outcome: "scheduled" });

    const indexed = parsePipeline({
      activeIndex: 4,
      outcome: "deferred",
      stages: [
        { id: "detect", state: "done" },
        { id: "authorize", state: "done" },
        { id: "queue", state: "done" },
        { id: "context", state: "done" },
        { id: "model", state: "idle" },
      ],
    });
    expect(indexed).toEqual({ active: true, stage: "model", stageIndex: 4, stageTotal: 5, outcome: "deferred" });

    expect(parsePipeline({ active_index: 0, outcome: "private", stages: [{ id: "private-stage", state: "active" }] }))
      .toEqual({ active: true, stage: "unknown", stageIndex: 0, stageTotal: 1, outcome: "unknown" });
    expect(parsePipeline({ active: true, stage: "send", stage_index: 6, stage_total: 8, outcome: "sent" }))
      .toEqual({ active: true, stage: "send", stageIndex: 6, stageTotal: 8, outcome: "sent" });
    expect(parsePipeline(undefined))
      .toEqual({ active: false, stage: "none", stageIndex: 0, stageTotal: 0, outcome: "unknown" });
  });

  it("parses bounded on-device summaries from raw and bridged shapes", () => {
    const raw = parseOnDevice({
      hardware: {
        chip: "M".repeat(80),
        cores: 5000,
        memory_gb: 127.96,
        is_apple_silicon: true,
        memory_bytes: 999999,
      },
      recommendation: {
        primary_engine: "mlx-serve",
        recommended_model: "m".repeat(140),
        recommended_quant: "4bit",
        reason: "raw reason",
      },
      verification: { ok: true },
      status_label: "상".repeat(250),
      status_detail: "세".repeat(250),
      last_probe: { model: "secret" },
    });
    expect(raw.available).toBe(true);
    expect(raw.chip).toHaveLength(64);
    expect(raw.cores).toBe(1024);
    expect(raw.memoryGb).toBe(128);
    expect(raw.appleSilicon).toBe(true);
    expect(raw.engine).toBe("mlx-serve");
    expect(raw.recommendedModel).toHaveLength(128);
    expect(raw.quant).toBe("4bit");
    expect(raw.verified).toBe(true);
    expect(raw.statusLabel).toHaveLength(240);
    expect(raw.statusDetail).toHaveLength(240);

    const bridged = parseOnDevice({
      available: true,
      chip: "Apple M5 Max",
      cores: 16,
      memory_gb: 5000,
      apple_silicon: true,
      engine: "mlx-serve",
      recommended_model: "model-a",
      quant: "4bit",
      verified: true,
      status_label: "ready",
      status_detail: "detail",
    });
    expect(bridged.memoryGb).toBe(4096);
    expect(bridged.appleSilicon).toBe(true);
    expect(bridged.recommendedModel).toBe("model-a");
    expect(bridged.statusLabel).toBe("ready");
  });

  it("fails closed for missing, corrupt, and non-finite on-device data", () => {
    expect(parseOnDevice(undefined).available).toBe(false);
    expect(parseOnDevice({}).available).toBe(false);
    expect(parseOnDevice({ hardware: {} }).available).toBe(false);
    expect(parseOnDevice("bad").available).toBe(false);
    expect(parseOnDevice({ available: true, memoryGb: Number.POSITIVE_INFINITY, cores: -3 })).toMatchObject({
      available: true,
      memoryGb: 0,
      cores: 0,
    });
    const snapshot = parseRuntimeSnapshot({
      available: true,
      rooms: [],
      jobs: [],
      context_sync: { mode: "async", waited: false },
      onDevice: { available: true, chip: "Apple", memoryGb: 64 },
    });
    expect(snapshot.onDevice).toMatchObject({ available: true, chip: "Apple", memoryGb: 64 });
  });

  it("fails closed on bad JSON, empty lists, and a missing model id", () => {
    expect(parseRuntimeSnapshotJson("{bad").available).toBe(false);
    const empty = parseRuntimeSnapshot({ available: true, rooms: [], jobs: [], context_sync: { mode: "async", waited: false } });
    expect(empty.rooms).toEqual([]);
    expect(empty.jobs).toEqual([]);
    expect(empty.recentReceipts).toEqual([]);
    expect(empty.replyModelId).toBeNull();
  });

  it("parses only safe recent receipt fields and drops malformed entries", () => {
    const snapshot = parseRuntimeSnapshot({
      available: true,
      rooms: [],
      jobs: [],
      context_sync: { mode: "async", waited: false },
      recent_receipts: [
        {
          chatId: 7,
          title: "방",
          displayTime: "09-21 16:00",
          clock: "16:00",
          outcome: "sent",
          outcomeText: "전".repeat(40),
          reasonCode: "direct_question",
          reasonText: "질문 응답",
          retrievalState: "ok",
          message: "private body",
          prompt: "private prompt",
        },
        { chatId: "bad", title: "drop", outcome: "sent" },
        { chatId: 8, title: "drop", displayTime: "", clock: "", outcome: "unknown", reasonCode: "direct_question", reasonText: "x", retrievalState: "ok" },
      ],
    });
    expect(snapshot.recentReceipts).toEqual([{
      chatId: 7,
      title: "방",
      displayTime: "09-21 16:00",
      clock: "16:00",
      outcome: "sent",
      outcomeText: "전".repeat(32),
      reasonCode: "direct_question",
      reasonText: "질문 응답",
      retrievalState: "ok",
    }]);
  });

  it("preserves safe slug reasons and normalizes free-form reason codes", () => {
    const freeForm = "수신된 이미지는 구체적인 질문 없이 공유된 화면입니다";
    const tooLong = `a${"b".repeat(64)}`;
    const snapshot = parseRuntimeSnapshot({
      available: true,
      rooms: [],
      jobs: [],
      context_sync: { mode: "async", waited: false },
      recent_receipts: [
        { chatId: 1, title: "a", displayTime: "t", clock: "c", outcome: "skipped", outcomeText: "건너뜀", reasonCode: "already_commented", reasonText: "이미 답변함", retrievalState: "ok" },
        { chatId: 2, title: "b", displayTime: "t", clock: "c", outcome: "skipped", outcomeText: "건너뜀", reasonCode: "low_information", reasonText: "알맹이 없음", retrievalState: "ok" },
        { chatId: 3, title: "c", displayTime: "t", clock: "c", outcome: "skipped", outcomeText: "건너뜀", reasonCode: "uncertain", reasonText: "판단 보류", retrievalState: "ok" },
        { chatId: 4, title: "d", displayTime: "t", clock: "c", outcome: "skipped", outcomeText: "건너뜀", reasonCode: freeForm, reasonText: "기록된 사유", retrievalState: "ok" },
        { chatId: 5, title: "e", displayTime: "t", clock: "c", outcome: "skipped", outcomeText: "건너뜀", reasonCode: tooLong, reasonText: "기록된 사유", retrievalState: "ok" },
        { chatId: 6, title: "f", displayTime: "t", clock: "c", outcome: "skipped", outcomeText: "건너뜀", reasonCode: "social reply", reasonText: "기록된 사유", retrievalState: "bad-state" },
      ],
    });
    expect(snapshot.recentReceipts.map((item) => item.reasonCode)).toEqual([
      "already_commented",
      "low_information",
      "uncertain",
      "unspecified",
      "unspecified",
      "unspecified",
    ]);
    expect(snapshot.recentReceipts[5]?.retrievalState).toBe("unrecorded");
    expect(JSON.stringify(snapshot.recentReceipts)).not.toContain(freeForm);
    expect(JSON.stringify(snapshot.recentReceipts)).not.toContain(tooLong);
  });
});

describe("history settings card", () => {
  it("renders present receipts without forbidden content", () => {
    document.body.innerHTML = settingsMarkup();
    const snapshot = parseRuntimeSnapshot({
      available: true,
      rooms: [],
      jobs: [],
      context_sync: { mode: "async", waited: false },
      recent_receipts: [{
        chatId: 7,
        title: "부자멘토멘티",
        displayTime: "09-21 16:00",
        clock: "16:00",
        outcome: "sent",
        outcomeText: "전송 완료",
        reasonCode: "direct_question about prior conversation topic",
        reasonText: "기록된 사유",
        retrievalState: "ok",
        message: "SHOULD_NOT_RENDER",
        prompt: "SECRET_PROMPT",
        token: "SECRET_TOKEN",
        model_attempts: [{ model: "SECRET_MODEL" }],
      }],
    });
    renderHistory(snapshot);
    expect(document.getElementById("history-summary")?.textContent).toContain("최근 1건");
    expect(document.getElementById("history-list")?.textContent).toContain("부자멘토멘티");
    expect(document.getElementById("history-list")?.textContent).toContain("전송 완료");
    expect(document.getElementById("history-list")?.textContent).toContain("기록된 사유");
    expect(document.body.textContent).not.toContain("direct_question about prior conversation topic");
    expect(document.body.textContent).not.toContain("SHOULD_NOT_RENDER");
    expect(document.body.textContent).not.toContain("SECRET_PROMPT");
    expect(document.body.textContent).not.toContain("SECRET_TOKEN");
    expect(document.body.textContent).not.toContain("SECRET_MODEL");
  });

  it("renders the empty state", () => {
    document.body.innerHTML = settingsMarkup();
    const snapshot = parseRuntimeSnapshot({ available: true, rooms: [], jobs: [], context_sync: { mode: "async", waited: false } });
    renderHistory(snapshot);
    expect(document.getElementById("history-summary")?.textContent).toBe("최근 기록이 없습니다.");
    expect(document.querySelectorAll("#history-list [role='listitem']")).toHaveLength(0);
  });

  it("falls back to the outcome code when outcomeText is absent", () => {
    document.body.innerHTML = settingsMarkup();
    const snapshot = parseRuntimeSnapshot({
      available: true,
      rooms: [],
      jobs: [],
      context_sync: { mode: "async", waited: false },
      recent_receipts: [{
        chatId: 9,
        title: "방",
        displayTime: "09-21 16:00",
        clock: "16:00",
        outcome: "scheduled",
        reasonCode: "social_reply",
        reasonText: "대화 참여",
        retrievalState: "skipped",
      }],
    });
    renderHistory(snapshot);
    expect(document.getElementById("history-list")?.textContent).toContain("scheduled");
  });

  it("renders the unavailable state", () => {
    document.body.innerHTML = settingsMarkup();
    renderHistory(parseRuntimeSnapshot({ available: false, rooms: [], jobs: [], context_sync: { mode: "async", waited: false } }));
    expect(document.getElementById("history-summary")?.textContent).toBe("기록을 확인할 수 없습니다.");
  });
});

describe("background settings activity", () => {
  it("renders valid, empty, and unavailable states", () => {
    document.body.innerHTML = settingsMarkup();
    const active = parseRuntimeSnapshot({
      available: true,
      rooms: [],
      jobs: [],
      context_sync: { mode: "async", waited: false },
      background: {
        activity: 0.75,
        caption: "예약 작업 처리 중",
        reply_load: 0.5,
        geeknews: { state: "sending", activity: 0.7, caption: "" },
        db_sync: { state: "behind", activity: 0.3, caption: "" },
      },
    });
    renderBackground(active);
    expect(document.getElementById("settings-activity-source")?.textContent)
      .toBe("백그라운드 · 답변 대기 2 · 긱뉴스 sending · DB 동기화 behind · 예약 작업 처리 중");

    renderBackground(parseRuntimeSnapshot({ available: true, rooms: [], jobs: [], context_sync: { mode: "async", waited: false } }));
    expect(document.getElementById("settings-activity-source")?.textContent).toBe("백그라운드 활동이 없습니다.");

    renderBackground(parseRuntimeSnapshot({ available: false, rooms: [], jobs: [], context_sync: { mode: "async", waited: false } }));
    expect(document.getElementById("settings-activity-source")?.textContent).toBe("백그라운드 상태를 확인할 수 없습니다.");
  });

  it("shows the safe pipeline stage only while the pipeline is active", () => {
    document.body.innerHTML = settingsMarkup();
    const active = parseRuntimeSnapshot({
      available: true,
      rooms: [],
      jobs: [],
      context_sync: { mode: "async", waited: false },
      pipeline: {
        active_index: 4,
        outcome: "scheduled",
        stages: [
          { id: "detect", state: "done" },
          { id: "authorize", state: "done" },
          { id: "queue", state: "done" },
          { id: "context", state: "done" },
          { id: "model", state: "active" },
        ],
      },
    });
    renderBackground(active);
    expect(document.getElementById("settings-activity-source")?.textContent).toContain("파이프라인 model");

    const inactive = parseRuntimeSnapshot({
      available: true,
      rooms: [],
      jobs: [],
      context_sync: { mode: "async", waited: false },
      background: { activity: 0.2 },
      pipeline: { active_index: null, outcome: "none", stages: [{ id: "detect", state: "idle" }] },
    });
    renderBackground(inactive);
    expect(document.getElementById("settings-activity-source")?.textContent).not.toContain("파이프라인");
  });

  it("shows the count of in-flight bridge jobs", () => {
    document.body.innerHTML = settingsMarkup();
    const snapshot = parseRuntimeSnapshot({
      available: true,
      rooms: [],
      jobs: [
        { jobId: "browser-1", kind: "browser", stage: "running", load: 0.7, time: 1, errorCode: null },
        { jobId: "swap-1", kind: "model_swap", stage: "swap", load: 0.9, time: 2, errorCode: null },
      ],
      context_sync: { mode: "async", waited: false },
    });
    renderBackground(snapshot);
    expect(document.getElementById("settings-activity-source")?.textContent).toContain("진행 중 작업 2");
  });
});

describe("on-device hardware settings", () => {
  it("renders the bounded status label without raw fields", () => {
    document.body.innerHTML = settingsMarkup();
    const snapshot = parseRuntimeSnapshot({
      available: true,
      rooms: [],
      jobs: [],
      context_sync: { mode: "async", waited: false },
      ondevice_hardware: {
        hardware: { chip: "Apple M5 Max", cores: 16, memory_gb: 128, memory_bytes: 123456 },
        recommendation: {
          primary_engine: "mlx-serve",
          recommended_model: "safe-model",
          recommended_quant: "4bit",
          reason: "RAW_REASON_SHOULD_NOT_RENDER",
          engine_paths: ["RAW_ENGINE_PATH"],
          fallback_models: ["RAW_FALLBACK"],
          worker_model_id: "RAW_WORKER_ID",
        },
        verification: { ok: true },
        last_probe: { model: "RAW_PROBE_MODEL" },
        status_label: "온디바이스 감지: Apple M5 Max (128GB RAM) · MLX Core/Serve",
        status_detail: "RAW_DETAIL_SHOULD_NOT_RENDER",
      },
    });
    renderHardware(snapshot);
    expect(document.getElementById("settings-hardware-status")?.textContent)
      .toBe("온디바이스: 온디바이스 감지: Apple M5 Max (128GB RAM) · MLX Core/Serve");
    for (const forbidden of [
      "RAW_REASON_SHOULD_NOT_RENDER",
      "RAW_ENGINE_PATH",
      "RAW_FALLBACK",
      "RAW_WORKER_ID",
      "RAW_PROBE_MODEL",
      "RAW_DETAIL_SHOULD_NOT_RENDER",
      "123456",
    ]) {
      expect(document.body.textContent).not.toContain(forbidden);
    }
  });

  it("renders chip and memory fallback when the safe label is empty", () => {
    document.body.innerHTML = settingsMarkup();
    const snapshot = parseRuntimeSnapshot({
      available: true,
      rooms: [],
      jobs: [],
      context_sync: { mode: "async", waited: false },
      onDevice: { available: true, chip: "Apple M5 Max", memoryGb: 128 },
    });
    renderHardware(snapshot);
    expect(document.getElementById("settings-hardware-status")?.textContent)
      .toBe("온디바이스: Apple M5 Max · 128.0GB");
  });

  it("renders unavailable state", () => {
    document.body.innerHTML = settingsMarkup();
    const snapshot = parseRuntimeSnapshot({ available: false, rooms: [], jobs: [], context_sync: { mode: "async", waited: false } });
    renderHardware(snapshot);
    expect(document.getElementById("settings-hardware-status")?.textContent)
      .toBe("하드웨어 정보를 확인할 수 없습니다.");
  });
});

describe("background signal polling", () => {
  it("forwards differentiated source loads to the signal sink", async () => {
    const snapshot = parseRuntimeSnapshot({
      available: true,
      rooms: [],
      jobs: [],
      job_load: 0.9,
      context_sync: { mode: "async", waited: false },
      voice: { available: true, rms: 0.2 },
      background: {
        activity: 0.8,
        replyLoad: 0.25,
        geeknews: { state: "sending", activity: 0.6, caption: "" },
        dbSync: { state: "syncing", activity: 0.3, caption: "" },
      },
      pipeline: {
        active_index: 4,
        outcome: "scheduled",
        stages: [
          { id: "detect", state: "done" },
          { id: "authorize", state: "done" },
          { id: "queue", state: "done" },
          { id: "context", state: "done" },
          { id: "model", state: "active" },
        ],
      },
    });
    const sink = { setSignals: vi.fn() };
    const scheduler = { setTimeout: vi.fn(() => 1), clearTimeout: vi.fn() };
    const poller = new RuntimeSnapshotPoller(
      sink,
      async () => snapshot,
      async () => undefined,
      () => ({ id: "background-test", cancelled: false }),
      scheduler,
    );
    poller.start();
    await Promise.resolve();
    await Promise.resolve();
    expect(sink.setSignals).toHaveBeenCalledWith(0.9, 0.2, {
      reply: 0.85,
      geeknews: 0.6,
      dbSync: 0.3,
      total: 0.8,
    });
    poller.stop();
  });

  it("forwards total load when bridge jobs are the only active status", async () => {
    const snapshot = parseRuntimeSnapshot({
      available: true,
      rooms: [],
      jobs: [
        { jobId: "browser-1", kind: "browser", stage: "running", load: 0.7, time: 1, errorCode: null },
      ],
      job_load: 0,
      context_sync: { mode: "async", waited: false },
      voice: { available: false, rms: 0 },
    });
    const sink = { setSignals: vi.fn() };
    const scheduler = { setTimeout: vi.fn(() => 1), clearTimeout: vi.fn() };
    const poller = new RuntimeSnapshotPoller(
      sink,
      async () => snapshot,
      async () => undefined,
      () => ({ id: "jobs-only-test", cancelled: false }),
      scheduler,
    );
    poller.start();
    await Promise.resolve();
    await Promise.resolve();
    expect(sink.setSignals).toHaveBeenCalledWith(0, 0, {
      reply: 0,
      geeknews: 0,
      dbSync: 0,
      total: 0.7,
    });
    poller.stop();
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
      "settings-activity-source",
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
