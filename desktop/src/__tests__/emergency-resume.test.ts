// @vitest-environment happy-dom
import { beforeAll, describe, expect, it, vi } from "vitest";
import { parseEmergencyState } from "../contracts";
import { settingsMarkup } from "../ui";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(async () => null) }));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn(async () => () => undefined) }));

let wireEmergencyResume: typeof import("../main").wireEmergencyResume;
beforeAll(async () => {
  document.body.innerHTML = '<div id="app"></div>';
  window.history.replaceState({}, "", "/?view=settings");
  const main = await import("../main");
  wireEmergencyResume = main.wireEmergencyResume;
  await vi.waitFor(() => expect(document.querySelector<HTMLElement>("#app")?.dataset.state).not.toBe("loading"));
});

const paused = { schemaVersion: 1 as const, epoch: 1, latched: true, reason: "operator stop" };
const resumed = { schemaVersion: 1 as const, epoch: 2, latched: false, reason: "human_resume" };

function fixture(action: Parameters<typeof wireEmergencyResume>[0]) {
  document.body.innerHTML = settingsMarkup();
  const controls = wireEmergencyResume(action);
  const button = document.querySelector<HTMLButtonElement>("#emergency-resume")!;
  return { controls, button, area: document.querySelector<HTMLElement>("#emergency-controls")! };
}

describe("explicit emergency resume", () => {
  it("accepts private schema states and bounded custom stop reasons", () => {
    expect(parseEmergencyState(paused)).toEqual(paused);
    expect(parseEmergencyState(resumed)).toEqual(resumed);
    expect(parseEmergencyState({ schemaVersion: 1, epoch: 0, latched: false, reason: "" })).not.toBeNull();
    for (const change of [
      { epoch: Number.MAX_SAFE_INTEGER + 1 }, { epoch: true }, { latched: 1 },
      { reason: "" }, { reason: "x".repeat(97) }, { reason: "stop\nnow" },
      { schemaVersion: 2 }, { extra: true },
    ]) expect(parseEmergencyState({ ...paused, ...change })).toBeNull();
    expect(parseEmergencyState({ ...resumed, reason: "automatic_resume" })).toBeNull();
  });

  it("never resumes on boot and keeps one operation control visible", () => {
    const action = vi.fn(async () => resumed);
    const { controls, area, button } = fixture(action);
    expect(area.hidden).toBe(false);
    controls.update(paused);
    expect(area.hidden).toBe(false);
    expect(button.getAttribute("aria-label")).toBe("올든 운영 재개");
    expect(document.querySelector("#emergency-status")?.textContent).toBe("다시 시작");
    expect(action).not.toHaveBeenCalled();
    controls.update(resumed);
    expect(area.hidden).toBe(false);
    expect(button.disabled).toBe(false);
  });

  it("sends explicit opt-in once and requires resumed epoch readback", async () => {
    let resolve!: (state: typeof resumed) => void;
    const action = vi.fn(() => new Promise<typeof resumed>((accept) => { resolve = accept; }));
    const { controls, button } = fixture(action);
    controls.update(paused);
    button.click(); button.click();
    expect(action.mock.calls).toEqual([[true]]);
    expect(button.disabled).toBe(true);
    resolve(resumed);
    await vi.waitFor(() => expect(button.getAttribute("aria-label")).toBe("올든 일시 중지"));
  });

  it("keeps the stop state when resume fails or returns an old epoch", async () => {
    for (const output of [null, { ...resumed, epoch: 1 }, paused]) {
      const { controls, area, button } = fixture(async () => output);
      controls.update(paused);
      button.click();
      await vi.waitFor(() => expect(button.disabled).toBe(false));
      expect(area.hidden).toBe(false);
    }
  });

  it("preserves a newer stop that arrives while resume is pending", async () => {
    let resolve!: (state: typeof resumed) => void;
    const { controls, area, button } = fixture(() => new Promise<typeof resumed>((accept) => { resolve = accept; }));
    controls.update(paused);
    button.click();
    controls.update({ ...paused, epoch: 3 });
    resolve(resumed);
    await vi.waitFor(() => expect(button.disabled).toBe(false));
    expect(area.hidden).toBe(false);
    controls.update(resumed);
    expect(area.hidden).toBe(false);
  });
});
