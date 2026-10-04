export type LifecycleState = "visible" | "hidden" | "closed" | "locked";

export interface FrameScheduler {
  request(callback: FrameRequestCallback): number;
  cancel(id: number): void;
  now(): number;
}

export interface AnimationLoopDiagnostics {
  running: boolean;
  pendingFrame: boolean;
  renderCount: number;
  lastRenderAtMs: number | null;
}

const browserScheduler: FrameScheduler = {
  request: (callback) => requestAnimationFrame(callback),
  cancel: (id) => cancelAnimationFrame(id),
  now: () => performance.now(),
};

export class AnimationLoop {
  private rafId: number | null = null;
  private activeTick: FrameRequestCallback | null = null;
  private running = false;
  private lastTickMs = 0;
  private lastRenderMs = 0;
  private lastRenderAtMs: number | null = null;
  private busyLoad = 0;
  private voiceActive = false;
  private interactive = false;
  private readonly maxDtSeconds = 0.25;
  renderCount = 0;

  constructor(
    private readonly renderFrame: (dtSeconds: number, nowMs: number) => void,
    private readonly scheduler: FrameScheduler = browserScheduler,
  ) {}

  setLoad(load: number): void {
    this.busyLoad = Number.isFinite(load) ? Math.min(1, Math.max(0, load)) : 0;
  }

  setVoiceActive(active: boolean): void { this.voiceActive = active; }
  setInteractive(active: boolean): void { this.interactive = active; }

  start(): void {
    if (this.running) return;
    this.running = true;
    const now = this.scheduler.now();
    this.lastTickMs = now;
    this.lastRenderMs = now - this.frameIntervalMs();
    // One stable callback belongs to one visible generation. A callback from a
    // prior generation can still be delivered after cancelAnimationFrame when
    // hide lands inside that callback. Identity keeps it from rearming itself
    // after a newer generation has started.
    const owner: FrameRequestCallback = (nowMs) => this.tick(nowMs, owner);
    this.activeTick = owner;
    this.rafId = this.scheduler.request(owner);
  }

  stop(): void {
    this.running = false;
    this.activeTick = null;
    if (this.rafId !== null) {
      this.scheduler.cancel(this.rafId);
      this.rafId = null;
    }
  }

  handleLifecycle(state: LifecycleState): void {
    if (state === "visible") this.start();
    else this.stop();
  }

  isRunning(): boolean {
    return this.running;
  }

  diagnostics(): AnimationLoopDiagnostics {
    return {
      running: this.running,
      pendingFrame: this.rafId !== null,
      renderCount: this.renderCount,
      lastRenderAtMs: this.lastRenderAtMs,
    };
  }

  private frameIntervalMs(): number {
    return this.interactive || this.voiceActive || this.busyLoad > 0.08 ? 1000 / 30 : 1000 / 15;
  }

  private tick(nowMs: number, owner: FrameRequestCallback): void {
    if (!this.running || this.activeTick !== owner) return;
    // The callback is executing, so there is no cancellable future frame until
    // this method explicitly schedules one below.
    this.rafId = null;
    const elapsedRender = nowMs - this.lastRenderMs;
    const interval = this.frameIntervalMs();
    if (elapsedRender + 0.5 >= interval) {
      const rawDt = Number.isFinite(nowMs) ? Math.max(0, nowMs - this.lastTickMs) / 1000 : 0;
      const dt = Math.min(this.maxDtSeconds, rawDt);
      this.lastTickMs = nowMs;
      // Keep the cadence phase: rounding around 33.33 ms must not repeatedly
      // skip to a third 60 Hz display refresh and turn a 30 fps cap into 23 fps.
      this.lastRenderMs += Math.max(1, Math.floor((elapsedRender + 0.5) / interval)) * interval;
      this.renderFrame(dt, nowMs);
      this.renderCount += 1;
      this.lastRenderAtMs = Number.isFinite(nowMs) ? nowMs : this.scheduler.now();
    }
    if (!this.running || this.activeTick !== owner) return;
    this.rafId = this.scheduler.request(owner);
  }
}
