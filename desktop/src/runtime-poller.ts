import { createCancellationToken, type CancellationToken, type RuntimeSnapshot } from "./contracts";
import { sourceLoads, type SourceLoads } from "./core/load-mapping";
import { cancelRuntimeRequest, fetchRuntimeSnapshot } from "./runtime";

export interface RuntimeSignalSink {
  setSignals(jobLoad: number, voiceRms: number, sources?: SourceLoads): void;
}

export interface PollTimerScheduler {
  setTimeout(callback: () => void, delayMs: number): number;
  clearTimeout(timerId: number): void;
}

export type SnapshotLoader = (token: CancellationToken) => Promise<RuntimeSnapshot>;
export type SnapshotCanceller = (token: CancellationToken) => Promise<void>;

export const browserPollScheduler: PollTimerScheduler = {
  setTimeout: (callback, delayMs) => window.setTimeout(callback, delayMs),
  clearTimeout: (timerId) => window.clearTimeout(timerId),
};

function snapshotSources(snapshot: RuntimeSnapshot | null): SourceLoads | undefined {
  if (!snapshot) return undefined;
  const background = snapshot.background;
  const hasStatus = background.activity > 0
    || background.replyLoad > 0
    || background.geeknews.activity > 0
    || background.dbSync.activity > 0
    || background.caption.length > 0
    || background.geeknews.caption.length > 0
    || background.dbSync.caption.length > 0
    || background.geeknews.state !== "unknown"
    || background.dbSync.state !== "unknown"
    || snapshot.pipeline.active;
  return hasStatus ? sourceLoads(background, snapshot.pipeline) : undefined;
}

export class RuntimeSnapshotPoller {
  private active = false;
  private generation = 0;
  private pollTimer: number | null = null;
  private requestToken: CancellationToken | null = null;

  constructor(
    private readonly signalSink: RuntimeSignalSink,
    private readonly loadSnapshot: SnapshotLoader = fetchRuntimeSnapshot,
    private readonly cancelSnapshot: SnapshotCanceller = cancelRuntimeRequest,
    private readonly makeToken: () => CancellationToken = createCancellationToken,
    private readonly scheduler: PollTimerScheduler = browserPollScheduler,
    private readonly intervalMs = 2500,
  ) {}

  start(): void {
    if (this.active) return;
    this.active = true;
    const generation = ++this.generation;
    this.beginPoll(generation);
  }

  stop(): void {
    if (!this.active && this.pollTimer === null && this.requestToken === null) return;
    this.active = false;
    this.generation += 1;
    if (this.pollTimer !== null) {
      try {
        this.scheduler.clearTimeout(this.pollTimer);
      } catch {
        // Timer cleanup is best-effort; generation invalidation still blocks stale work.
      }
      this.pollTimer = null;
    }
    const token = this.requestToken;
    this.requestToken = null;
    if (token) this.cancelToken(token);
  }

  private beginPoll(generation: number): void {
    if (!this.active || generation !== this.generation || this.requestToken !== null) return;
    let token: CancellationToken;
    try {
      token = this.makeToken();
    } catch {
      this.schedule(generation);
      return;
    }
    this.requestToken = token;
    void this.poll(generation, token);
  }

  private async poll(generation: number, token: CancellationToken): Promise<void> {
    let snapshot: RuntimeSnapshot | null = null;
    try {
      snapshot = await this.loadSnapshot(token);
    } catch {
      snapshot = null;
    }

    const isCurrent = this.active
      && generation === this.generation
      && this.requestToken === token
      && !token.cancelled;
    if (this.requestToken === token) this.requestToken = null;
    if (!isCurrent) return;

    try {
      const sources = snapshotSources(snapshot);
      if (sources) {
        this.signalSink.setSignals(snapshot?.jobLoad ?? 0, snapshot?.voice.rms ?? 0, sources);
      } else {
        this.signalSink.setSignals(snapshot?.jobLoad ?? 0, snapshot?.voice.rms ?? 0);
      }
    } catch {
      // Rendering state must not break polling cleanup or create an unhandled rejection.
    }
    this.schedule(generation);
  }

  private schedule(generation: number): void {
    if (!this.active || generation !== this.generation || this.pollTimer !== null) return;
    try {
      this.pollTimer = this.scheduler.setTimeout(() => {
        this.pollTimer = null;
        this.beginPoll(generation);
      }, this.intervalMs);
    } catch {
      this.pollTimer = null;
    }
  }

  private cancelToken(token: CancellationToken): void {
    try {
      const cancellation = this.cancelSnapshot(token);
      token.cancelled = true;
      void cancellation.catch(() => undefined);
    } catch {
      token.cancelled = true;
    }
  }
}
