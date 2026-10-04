# Local model adapter readback — 2026-09-30

The exact `alden_voice.LocalMlxLlm.generate` adapter successfully invoked resident `ddalcu/Qwen3.8-27B-MLX-Serve-4bit` at `127.0.0.1:11234` for a synthetic arithmetic prompt. It returned **`4` in 1.484731 s**, including admission, selected-model readiness and HTTP response handling. The adapter checked the returned model identity and did not speak the reasoning channel. This is an actual inference result, beyond a configured label or health response.

The request used the existing 0.3 temperature and 128-token cap, the shared model-request lease and a captured operator abort token. No KakaoTalk message or voice session was created. Model inference used only the validated loopback endpoint; no cloud inference request was made.

Before the request, the existing macOS `vm_stat` estimator reported **58,816,692,224 bytes (54.777 GiB)** from free, inactive and speculative pages. Flash-Next's unchanged **64,424,509,440-byte (60 GiB)** admission requirement was therefore not met. The iQ endpoint at `11235` was unavailable and its main catalog row was unloaded. No admission threshold was reduced and no unrelated app was stopped to force a load. The estimate is not a guarantee that every counted page can be allocated without paging.

This single warmed arithmetic example is not a measurement of normal conversation latency, contextual reply quality, self-talk frequency, or voice end-to-end success. It must not be compared as a speedup against earlier Flash-Next generation or offline DPO scoring, which used different models, inputs and procedures. Microphone, wake detection, Whisper and TTS remain separate requirements.

See the [structured receipt](alden-local-model-readback-20260930.json).
