// @vitest-environment happy-dom

import { afterEach, describe, expect, it, vi } from "vitest";
import { settingsMarkup } from "../ui";
import { wireVoiceStart } from "../voice-controls";

function voiceElements(): { button: HTMLButtonElement; status: HTMLElement } {
  document.body.innerHTML = settingsMarkup();
  const button = document.querySelector<HTMLButtonElement>("#voice-start");
  const status = document.querySelector<HTMLElement>("#voice-status");
  if (!button || !status) throw new Error("voice_test_dom_missing");
  return { button, status };
}

afterEach(() => {
  document.body.replaceChildren();
});

describe("settings voice start control", () => {
  it("wires once, invokes once, and blocks races and duplicate sessions", async () => {
    const { button, status } = voiceElements();
    let resolveInvoke: (() => void) | undefined;
    const pendingInvoke = new Promise<void>((resolve) => {
      resolveInvoke = resolve;
    });
    const invoke = vi.fn((_command: string) => pendingInvoke);

    wireVoiceStart(document, invoke);
    wireVoiceStart(document, invoke);
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));

    expect(invoke).toHaveBeenCalledTimes(1);
    expect(invoke).toHaveBeenCalledWith("start_voice_session");
    expect(button.disabled).toBe(true);
    expect(button.getAttribute("aria-busy")).toBe("true");
    expect(status.textContent).toBe("상태 시작 중…");

    resolveInvoke?.();
    await vi.waitFor(() => expect(button.disabled).toBe(false));
    expect(button.hasAttribute("aria-busy")).toBe(false);
    expect(status.textContent).toBe("상태 시작됨 · voice_session_started");

    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await Promise.resolve();
    expect(invoke).toHaveBeenCalledTimes(1);
  });

  it("fails closed with a safe code and restores the button after rejection", async () => {
    const { button, status } = voiceElements();
    const privateFailure = "CoreAudio failed at /Users/private/device";
    const invoke = vi.fn((_command: string) => Promise.reject(new Error(privateFailure)));

    wireVoiceStart(document, invoke);
    button.click();

    expect(button.disabled).toBe(true);
    expect(button.getAttribute("aria-busy")).toBe("true");
    await vi.waitFor(() => expect(button.disabled).toBe(false));
    expect(button.hasAttribute("aria-busy")).toBe(false);
    expect(status.textContent).toBe("상태 실패 · voice_session_start_failed");
    expect(status.textContent).not.toContain(privateFailure);
  });
});
