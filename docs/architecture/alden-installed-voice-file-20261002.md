# Installed voice file pipeline — 2026-10-02

Installed source:40769fc4801b35c3c18eecf3bd5dbd69bd4e66cf (Alden0.3.1). Three isolated Python subprocesses each processed the same two hashed synthetic, presegmented WAVs through the real installed Whisper/local27B/product Qwen-TTS adapters. All six turns ended without an error and all three subprocesses exited0. The math follow-up retained context:4 then12.

Scope excludes wake/VAD, human microphone, speaker playback, physical interruption and primary-process UI. No remote inference or model replacement occurred. Audit hooks permitted only socket connections to127.0.0.1:11234. TTS parameters were actually MPS0/BF16. Each explicit close left Whisper ModelHolder empty and MPS allocated memory8,388,608bytes. Peak process RSS was2,972,450,816–2,973,466,624bytes; RSS is not an additional pool to add to GPU allocations in unified memory.

The first turn in each process had STT+LLM+WAV-file stage totals6.246–11.036s (median9.375s,n3). The second had1.818–4.205s (median2.022s,n3). These are file-stage timings, not speech-end-to-first-playback latency, independent p95 samples, or a measured speedup. The earlier installed0.1.8 exit-6 recursive_mutex error was not reproduced in this sample; no root-cause cure is inferred from three clean exits.

A separate source0.3.2 synthetic two-room knowledge sequence, using actual local E5 and27B, returned Friday15:00 from the selected meeting room and the same answer to its follow-up; the other room's Saturday event was absent. An unrelated arithmetic turn skipped retrieval and returned4. Retrieval was RRF with four scoped quotes in59.8ms and31.4ms,n1 each. These are controlled sequence observations, not recall/quality/latency percentile results.
