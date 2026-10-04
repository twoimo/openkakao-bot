import type { VoiceStatus } from "./contracts";
import { fetchVoiceStatus } from "./runtime";
import { browserPollScheduler, type PollTimerScheduler } from "./runtime-poller";
import { freshVoiceAmplitude, type VoiceAmplitudeSource } from "./voice-amplitude";

export type VoiceStatusLoader = () => Promise<VoiceStatus>;

/** One visible owner reads tiny native status, without a Python invocation. */
export class VoiceAmplitudePoller {
  private active = false;
  private generation = 0;
  private timer: number | null = null;

  constructor(
    private readonly apply: (rms: number, source: VoiceAmplitudeSource) => void,
    private readonly load: VoiceStatusLoader = fetchVoiceStatus,
    private readonly scheduler: PollTimerScheduler = browserPollScheduler,
    private readonly observeVoice: (voice: VoiceStatus | null) => void = () => undefined,
  ) {}

  start(): void {
    if (this.active) return;
    this.active = true;
    void this.poll(++this.generation);
  }

  stop(): void {
    this.active = false;
    this.generation++;
    if (this.timer !== null) {
      try { this.scheduler.clearTimeout(this.timer); } finally { this.timer = null; }
    }
  }

  private async poll(generation: number): Promise<void> {
    let voice: VoiceStatus | null = null;
    try { voice = await this.load(); } catch { /* Failed reads have zero amplitude. */ }
    if (!this.active || generation !== this.generation) return;
    const signal = freshVoiceAmplitude(voice);
    try { this.observeVoice(voice); this.apply(signal.rms, signal.source); } catch { /* Rendering cannot rearm stale work. */ }
    if (!this.active || generation !== this.generation) return;
    try {
      this.timer = this.scheduler.setTimeout(() => {
        this.timer = null;
        void this.poll(generation);
      }, voice?.available ? 100 : 750);
    } catch { /* A closing surface cannot create a replacement timer. */ }
  }
}
