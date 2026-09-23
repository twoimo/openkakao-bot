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

  button.addEventListener("click", () => {
    if (requestPending) return;

    requestPending = true;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    if (status) status.textContent = "음성 듣기를 준비하고 있습니다…";

    void (async () => {
      try {
        await invokeFn("start_voice_session");
        if (status) status.textContent = "음성 듣기 시작 요청을 보냈습니다.";
      } catch (error) {
        const code = error instanceof Error ? error.message : error;
        if (code === "voice_session_already_running") {
          if (status) status.textContent = "기존 음성 실행이 남아 있어 새로 시작하지 않았습니다.";
        } else {
          if (status) status.textContent = "음성 듣기를 시작하지 못했습니다. 잠시 후 다시 시도해 주세요.";
        }
      } finally {
        requestPending = false;
        button.disabled = false;
        button.removeAttribute("aria-busy");
      }
    })();
  });
}
