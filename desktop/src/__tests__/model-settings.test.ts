// @vitest-environment happy-dom

import { beforeAll, describe, expect, it, vi } from "vitest";
import { parseRuntimeSnapshot } from "../contracts";
import { RESIDENT_MODEL_ID } from "../tokens";

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

describe("simple model settings", () => {
  it("loads only the conversation data needed for the settings screen", async () => {
    const loadAction = vi.fn(async (): Promise<Record<string, unknown> | null> => null);

    await bootSettings({
      loadSnapshot: async () => snapshot,
      loadAction,
      wireVoice: () => undefined,
      invokeCommand: async <T>() => undefined as T,
      subscribeVisibility: null,
      readVisibility: null,
    });

    expect(loadAction.mock.calls).toEqual([
      ["knowledge-graph-status"],
      ["knowledge-graph"],
    ]);
    expect(document.querySelector('[data-model-choice="fast"]')?.getAttribute("aria-pressed")).toBe("true");
    expect(document.querySelector("#model-status")?.textContent).toBe("빠른 대화가 선택되어 있습니다.");
    expect(document.querySelector("#model-owner-state")).toBeNull();
    expect(document.querySelector("#mlx-server-state")).toBeNull();
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
