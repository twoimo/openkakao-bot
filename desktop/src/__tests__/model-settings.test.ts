// @vitest-environment happy-dom

import { beforeAll, describe, expect, it, vi } from "vitest";
import { parseRuntimeSnapshot } from "../contracts";
import { LEGACY_RESIDENT_MODEL_ID, RESIDENT_MODEL_ID, SWAP_MODEL_ID } from "../tokens";

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(async () => null),
}));

let bootSettings: typeof import("../main").bootSettings;
let modelSwapFailureText: typeof import("../main").modelSwapFailureText;

beforeAll(async () => {
  document.body.innerHTML = '<div id="app"></div>';
  window.history.replaceState({}, "", "/?view=settings");
  const main = await import("../main");
  bootSettings = main.bootSettings;
  modelSwapFailureText = main.modelSwapFailureText;
  await vi.waitFor(() => expect(document.querySelector<HTMLElement>("#app")?.dataset.state).not.toBe("loading"));
});

const snapshot = parseRuntimeSnapshot({
  available: true,
  rooms: [],
  jobs: [],
  context_sync: { mode: "async", waited: false },
  reply_model_id: `mlx/${RESIDENT_MODEL_ID}`,
});

const legacySnapshot = parseRuntimeSnapshot({
  available: true,
  rooms: [],
  jobs: [],
  context_sync: { mode: "async", waited: false },
  reply_model_id: `mlx/${LEGACY_RESIDENT_MODEL_ID}`,
});

const deepSnapshot = parseRuntimeSnapshot({
  available: true,
  rooms: [],
  jobs: [],
  context_sync: { mode: "async", waited: false },
  reply_model_id: `mlx/${SWAP_MODEL_ID}`,
});

describe("simple model settings", () => {
  it("loads only the conversation data needed for the settings screen", async () => {
    const loadAction = vi.fn(async (): Promise<Record<string, unknown> | null> => null);
    const invokeMock = vi.fn(async () => undefined);
    const invokeCommand = invokeMock as unknown as typeof import("@tauri-apps/api/core").invoke;

    await bootSettings({
      loadSnapshot: async () => snapshot,
      loadAction,
      wireVoice: () => undefined,
      invokeCommand,
      subscribeVisibility: null,
      readVisibility: null,
    });

    expect(loadAction.mock.calls).toEqual([
      ["knowledge-graph-status"],
      ["knowledge-graph"],
    ]);
    expect(invokeMock).not.toHaveBeenCalled();
    expect(document.querySelector('[data-model-choice="fast"]')?.getAttribute("aria-pressed")).toBe("true");
    expect(document.querySelector("#model-status")?.textContent).toBe("빠른 대화가 선택되어 있습니다.");
    expect(document.querySelector("#model-owner-state")).toBeNull();
    expect(document.querySelector("#mlx-server-state")).toBeNull();
  });

  it("keeps a legacy Flash-Next saved state on the fast conversation choice", async () => {
    await bootSettings({
      loadSnapshot: async () => legacySnapshot,
      loadAction: async (): Promise<Record<string, unknown> | null> => null,
      wireVoice: () => undefined,
      invokeCommand: async <T>() => undefined as T,
      subscribeVisibility: null,
      readVisibility: null,
    });

    expect(document.querySelector('[data-model-choice="fast"]')?.getAttribute("aria-pressed")).toBe("true");
    expect(document.querySelector("#model-status")?.textContent).toBe("빠른 대화가 선택되어 있습니다.");
  });

  it("keeps the current selection busy and unchanged when resident launch fails", async () => {
    let resolveLaunch!: (value: unknown) => void;
    const launchPending = new Promise<unknown>((resolve) => { resolveLaunch = resolve; });
    const invokeMock = vi.fn(async (_command: string, args?: Record<string, unknown>) => {
      if (args?.action === "model-set") {
        return {
          ok: false,
          action: "model-set",
          model: RESIDENT_MODEL_ID,
          stored: false,
          prepared: false,
          needs_prepare: true,
        };
      }
      if (args?.action === "mlx-server-launch") return await launchPending;
      return undefined;
    });
    const invokeCommand = invokeMock as unknown as typeof import("@tauri-apps/api/core").invoke;

    await bootSettings({
      loadSnapshot: async () => deepSnapshot,
      loadAction: async (): Promise<Record<string, unknown> | null> => null,
      wireVoice: () => undefined,
      invokeCommand,
      subscribeVisibility: null,
      readVisibility: null,
    });

    const fast = document.querySelector<HTMLButtonElement>('[data-model-choice="fast"]')!;
    const deep = document.querySelector<HTMLButtonElement>('[data-model-choice="deep"]')!;
    fast.click();
    await vi.waitFor(() => expect(invokeMock).toHaveBeenCalledTimes(2));
    expect(fast.disabled).toBe(true);
    expect(deep.disabled).toBe(true);
    expect(fast.getAttribute("aria-busy")).toBe("true");
    expect(document.querySelector("#model-status")?.textContent).toBe("빠른 대화를 준비하고 있습니다…");

    resolveLaunch({
      ok: false,
      action: "mlx-server-launch",
      model: null,
      reason: "launch_memory_insufficient",
    });
    await vi.waitFor(() => expect(fast.disabled).toBe(false));
    expect(fast.getAttribute("aria-pressed")).toBe("false");
    expect(deep.getAttribute("aria-pressed")).toBe("true");
    expect(document.querySelector("#model-status")?.textContent).toBe(
      "빠른 대화를 준비하지 못했습니다. 기존 설정을 유지했습니다.",
    );
  });

  it("selects an already-ready deep model directly without model-swap", async () => {
    const invokeMock = vi.fn(async (_command: string, args?: Record<string, unknown>) => {
      if (args?.action === "model-set" && args?.model === SWAP_MODEL_ID) {
        return {
          ok: true,
          action: "model-set",
          model: SWAP_MODEL_ID,
          stored: true,
          prepared: true,
          needs_prepare: false,
        };
      }
      throw new Error("unexpected_action");
    });
    const invokeCommand = invokeMock as unknown as typeof import("@tauri-apps/api/core").invoke;

    await bootSettings({
      loadSnapshot: async () => snapshot,
      loadAction: async (): Promise<Record<string, unknown> | null> => null,
      wireVoice: () => undefined,
      invokeCommand,
      subscribeVisibility: null,
      readVisibility: null,
    });

    const fast = document.querySelector<HTMLButtonElement>('[data-model-choice="fast"]')!;
    const deep = document.querySelector<HTMLButtonElement>('[data-model-choice="deep"]')!;
    deep.click();
    await vi.waitFor(() => expect(deep.getAttribute("aria-pressed")).toBe("true"));

    expect(invokeMock.mock.calls).toEqual([
      ["fetch_settings_action", { action: "model-set", model: SWAP_MODEL_ID }],
    ]);
    expect(fast.getAttribute("aria-pressed")).toBe("false");
    expect(deep.disabled).toBe(false);
    expect(document.querySelector("#model-status")?.textContent).toBe("깊은 분석을 사용할 준비가 되었습니다.");
  });

  it("falls back to one cancellable model-swap only after exact needs_prepare", async () => {
    let resolveSwap!: (value: unknown) => void;
    const swapPending = new Promise<unknown>((resolve) => { resolveSwap = resolve; });
    const invokeMock = vi.fn(async (_command: string, args?: Record<string, unknown>) => {
      if (args?.action === "model-set" && args?.model === SWAP_MODEL_ID) {
        return {
          ok: false,
          action: "model-set",
          model: SWAP_MODEL_ID,
          stored: false,
          prepared: false,
          needs_prepare: true,
        };
      }
      if (args?.action === "model-swap" && args?.model === SWAP_MODEL_ID) return await swapPending;
      throw new Error("unexpected_action");
    });
    const invokeCommand = invokeMock as unknown as typeof import("@tauri-apps/api/core").invoke;

    await bootSettings({
      loadSnapshot: async () => snapshot,
      loadAction: async (): Promise<Record<string, unknown> | null> => null,
      wireVoice: () => undefined,
      invokeCommand,
      subscribeVisibility: null,
      readVisibility: null,
    });

    const fast = document.querySelector<HTMLButtonElement>('[data-model-choice="fast"]')!;
    const deep = document.querySelector<HTMLButtonElement>('[data-model-choice="deep"]')!;
    deep.click();
    await vi.waitFor(() => expect(invokeMock).toHaveBeenCalledTimes(2));

    const swapCall = invokeMock.mock.calls[1];
    expect(swapCall[0]).toBe("fetch_settings_action");
    expect(swapCall[1]).toMatchObject({ action: "model-swap", model: SWAP_MODEL_ID, explicitOptIn: true });
    expect(typeof swapCall[1]?.tokenId).toBe("string");
    expect(fast.disabled).toBe(true);
    expect(deep.disabled).toBe(true);
    expect(deep.getAttribute("aria-busy")).toBe("true");
    expect(document.querySelector("#model-status")?.textContent).toBe("깊은 분석을 준비하고 있습니다…");

    resolveSwap({
      ok: true,
      action: "model-swap",
      model: SWAP_MODEL_ID,
      stage: "ready",
      reason: "ready",
      stages: ["drain", "load", "probe", "ready"],
      stored: true,
      prepared: true,
    });
    await vi.waitFor(() => expect(deep.getAttribute("aria-pressed")).toBe("true"));
    expect(fast.getAttribute("aria-pressed")).toBe("false");
    expect(deep.disabled).toBe(false);
    expect(invokeMock).toHaveBeenCalledTimes(2);
  });

  it("does not fall through to model-swap for malformed or arbitrary deep model-set failures", async () => {
    for (const response of [
      {
        ok: false,
        action: "model-set",
        model: SWAP_MODEL_ID,
        stored: false,
        prepared: false,
        needs_prepare: "true",
      },
      {
        ok: false,
        action: "model-set",
        model: "remote/arbitrary",
        stored: false,
        prepared: false,
        needs_prepare: true,
      },
    ]) {
      const invokeMock = vi.fn(async () => response);
      const invokeCommand = invokeMock as unknown as typeof import("@tauri-apps/api/core").invoke;
      await bootSettings({
        loadSnapshot: async () => snapshot,
        loadAction: async (): Promise<Record<string, unknown> | null> => null,
        wireVoice: () => undefined,
        invokeCommand,
        subscribeVisibility: null,
        readVisibility: null,
      });

      const fast = document.querySelector<HTMLButtonElement>('[data-model-choice="fast"]')!;
      const deep = document.querySelector<HTMLButtonElement>('[data-model-choice="deep"]')!;
      deep.click();
      await vi.waitFor(() => expect(deep.disabled).toBe(false));
      expect(invokeMock).toHaveBeenCalledTimes(1);
      expect(invokeMock.mock.calls[0]).toEqual([
        "fetch_settings_action",
        { action: "model-set", model: SWAP_MODEL_ID },
      ]);
      expect(fast.getAttribute("aria-pressed")).toBe("true");
      expect(deep.getAttribute("aria-pressed")).toBe("false");
      expect(document.querySelector("#model-status")?.textContent).toBe(
        "깊은 분석을 선택하지 못했습니다. 기존 설정을 유지했습니다.",
      );
    }
  });

  it.each([
    ["model_owner_unmanaged", "현재 사용 중인 AI와 안전하게 바꿀 수 없어 기존 설정을 유지했습니다."],
    ["model_owner_unknown", "AI 실행 상태를 확인하지 못해 기존 설정을 유지했습니다."],
    ["insufficient_free_memory", "안전하게 사용할 수 있는 메모리가 부족해 설정을 바꾸지 않았습니다."],
  ])("explains why a requested change was not made (%s) without backend jargon", (reason, message) => {
    const result = modelSwapFailureText(reason);
    expect(result).toBe(message);
    expect(result).not.toMatch(/MLX|Qwen|27B|gateway|model_owner/i);
  });
});
