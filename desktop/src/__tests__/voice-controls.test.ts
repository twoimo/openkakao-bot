// @vitest-environment happy-dom

import { afterEach, describe, expect, it, vi } from "vitest";
import { settingsMarkup, voiceErrorMessage } from "../ui";
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
  it("wires once and blocks clicks while a start request is pending", async () => {
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
    expect(status.textContent).toBe("음성 듣기를 준비하고 있습니다…");

    resolveInvoke?.();
    await vi.waitFor(() => expect(button.disabled).toBe(false));
    expect(button.hasAttribute("aria-busy")).toBe(false);
    expect(status.textContent).toBe("음성 듣기 시작 요청을 보냈습니다.");

    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await Promise.resolve();
    expect(invoke).toHaveBeenCalledTimes(2);
  });

  it.each([
    "voice_session_already_running",
    new Error("voice_session_already_running"),
  ])("reports the existing listener without spawning again (%s)", async (outcome) => {
    const { button, status } = voiceElements();
    const invoke = vi.fn((_command: string) => Promise.reject(outcome));

    wireVoiceStart(document, invoke);
    button.click();

    await vi.waitFor(() => expect(button.disabled).toBe(false));
    expect(button.hasAttribute("aria-busy")).toBe(false);
    expect(status.textContent).toBe("기존 음성 실행이 남아 있어 새로 시작하지 않았습니다.");
    button.click();
    await Promise.resolve();
    expect(invoke).toHaveBeenCalledTimes(2);
  });

  it.each([
    "voice_session_process_check_failed",
    "unexpected: voice_session_already_running",
  ])("keeps unconfirmed failures retryable and does not infer an existing listener (%s)", async (outcome) => {
    const { button, status } = voiceElements();
    const invoke = vi.fn().mockRejectedValueOnce(outcome).mockResolvedValue(undefined);
    wireVoiceStart(document, invoke);
    button.click();
    await vi.waitFor(() => expect(button.disabled).toBe(false));
    expect(status.textContent).toBe("음성 듣기를 시작하지 못했습니다. 잠시 후 다시 시도해 주세요.");
    button.click();
    await vi.waitFor(() => expect(status.textContent).toBe("음성 듣기 시작 요청을 보냈습니다."));
    expect(invoke).toHaveBeenCalledTimes(2);
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
    expect(status.textContent).toBe("음성 듣기를 시작하지 못했습니다. 잠시 후 다시 시도해 주세요.");
    expect(status.textContent).not.toContain(privateFailure);
  });

  it("explains local voice memory blocks in plain Korean without exposing codes", () => {
    expect(voiceErrorMessage("voice_memory_budget_low")).toBe("기기 메모리 여유가 부족해 음성 처리를 멈췄습니다.");
    expect(voiceErrorMessage("voice_memory_budget_unavailable")).toBe("기기 메모리 상태를 확인할 수 없어 음성 처리를 시작하지 않았습니다.");
    expect(voiceErrorMessage("mic_unavailable")).toBe("마이크를 열 수 없습니다. 연결 상태를 확인해 주세요.");
    expect(voiceErrorMessage("mic_disconnected")).toBe("마이크를 사용할 수 없습니다. 연결을 확인해 주세요.");
    expect(voiceErrorMessage("voice_session_process_check_failed")).toBe("음성 기능을 시작하지 못했습니다.");
    expect(voiceErrorMessage(null)).toBeNull();
  });
});
