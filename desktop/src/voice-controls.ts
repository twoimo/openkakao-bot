export type VoiceSessionInvoke = (command: string) => Promise<unknown>;

const wiredVoiceButtons = new WeakSet<HTMLButtonElement>();

export function wireVoiceStart(
  root: ParentNode,
  invokeFn: VoiceSessionInvoke,
): void {
  const button = root.querySelector<HTMLButtonElement>("#voice-start");
  const status = root.querySelector<HTMLElement>("#voice-status");
  if (!button || wiredVoiceButtons.has(button)) return;

  wiredVoiceButtons.add(button);
  let requestPending = false;
  let sessionStarted = false;

  button.addEventListener("click", () => {
    if (requestPending || sessionStarted) return;

    requestPending = true;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    if (status) status.textContent = "상태 시작 중…";

    void (async () => {
      try {
        await invokeFn("start_voice_session");
        sessionStarted = true;
        if (status) status.textContent = "상태 시작됨 · voice_session_started";
      } catch {
        if (status) status.textContent = "상태 실패 · voice_session_start_failed";
      } finally {
        requestPending = false;
        button.disabled = false;
        button.removeAttribute("aria-busy");
      }
    })();
  });
}
