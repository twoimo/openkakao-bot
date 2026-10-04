export type VoiceSessionInvoke = (command: string, args?: Record<string, unknown>) => Promise<unknown>;

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
    const stopping = button.dataset.voiceActive === "true";
    if (status) status.textContent = stopping ? "마이크를 끄고 있습니다…" : "음성 듣기를 준비하고 있습니다…";

    void (async () => {
      try {
        const conversationId = button.dataset.conversationId;
        const result = await (stopping ? invokeFn("stop_manual_voice_session")
          : conversationId ? invokeFn("start_manual_voice_session", {conversationId})
          : invokeFn("start_manual_voice_session"));
        if (stopping && result === false) {
          if (status) status.textContent = "마이크 중지를 확인하지 못했습니다. 다시 눌러주세요.";
        } else {
          button.dataset.voiceActive = String(!stopping);
          button.textContent = stopping ? "마이크 켜기" : "마이크 끄기";
          if (status) status.textContent = stopping ? "마이크를 껐습니다." : "음성 듣기 시작 요청을 보냈습니다.";
        }
      } catch (error) {
        const code = error instanceof Error ? error.message : error;
        if (code === "voice_preview_only") {
          if (status) {
            status.dataset.previewOnly = "true";
            status.textContent = "음성 입력은 설치된 올든 앱에서 사용하세요.";
          }
        } else if (code === "voice_session_already_running") {
          if (status) status.textContent = "기존 음성 실행이 남아 있어 새로 시작하지 않았습니다.";
        } else if (code === "python_cancelled") {
          if (status) status.textContent = "일시 중지 상태입니다. 재개한 뒤 마이크를 켜주세요.";
        } else if (code === "alden_wake_model_unavailable") {
          if (status) status.textContent = "‘올든’을 알아듣는 기능이 준비되지 않아 음성 입력을 시작하지 않았습니다.";
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
