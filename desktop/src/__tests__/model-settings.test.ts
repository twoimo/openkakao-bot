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

describe("model ownership settings", () => {
  it("explains an unmanaged MLX owner separately and gives the next action", () => {
    expect(modelSwapFailureText("model_owner_unmanaged")).toBe(
      "27B 전환 차단 · 외부 MLX Core가 게이트웨이를 소유 중이라 앱이 안전하게 27B로 전환할 수 없습니다. 외부 MLX Core를 종료한 뒤 다시 시도하세요.",
    );
    expect(modelSwapFailureText("model_owner_unknown")).toBe(
      "27B 전환 중단 · 상주 모델의 소유권을 증명할 수 없습니다.",
    );
  });

  it.each([
    ["app_owned", "앱 소유 확인됨"],
    ["model_owner_unmanaged", "외부 소유 · 27B 전환 차단"],
    ["model_owner_state_stale", "소유권 미확인 · 27B 전환 차단"],
  ])("loads and renders owner state %s", async (ownerState, expected) => {
    const loadAction = vi.fn(async (action: string): Promise<Record<string, unknown> | null> => {
      if (action === "model-owner-status") {
        return {
          ok: true,
          action,
          owner_state: ownerState,
          owner_verified: ownerState === "app_owned",
          drain_verified: false,
          current_model: RESIDENT_MODEL_ID,
        };
      }
      return null;
    });

    await bootSettings({
      loadSnapshot: async () => snapshot,
      loadAction,
      wireVoice: () => undefined,
      invokeCommand: async <T>() => undefined as T,
    });

    expect(loadAction.mock.calls.map(([action]) => action)).toEqual([
      "models",
      "model-owner-status",
      "dream-rsi-status",
      "knowledge-graph-status",
      "knowledge-graph",
    ]);
    expect(document.querySelector("#model-owner-state")?.textContent).toBe(expected);
  });
});
