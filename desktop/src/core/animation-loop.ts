export type LifecycleState = "visible" | "hidden" | "closed" | "locked";

export interface FrameScheduler {
  request(callback: FrameRequestCallback): number;
  cancel(id: number): void;
  now(): number;
}

const browserScheduler: FrameScheduler = {
  request: (callback) => requestAnimationFrame(callback),
  cancel: (id) => cancelAnimationFrame(id),
  now: () => performance.now(),
};

export class AnimationLoop {
  private rafId: number | null = null;
  private running = false;
  private lastTickMs = 0;
  private lastRenderMs = 0;
  private busyLoad = 0;
  private readonly maxDtSeconds = 0.05;
  renderCount = 0;

  constructor(
    private readonly renderFrame: (dtSeconds: number, nowMs: number) => void,
    private readonly scheduler: FrameScheduler = browserScheduler,
  ) {}

  setLoad(load: number): void {
    this.busyLoad = Math.min(1, Math.max(0, load));
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    const now = this.scheduler.now();
    this.lastTickMs = now;
    this.lastRenderMs = now - this.frameIntervalMs();
    this.rafId = this.scheduler.request(this.tick);
  }

  stop(): void {
    this.running = false;
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

  private frameIntervalMs(): number {
    return this.busyLoad > 0.08 ? 1000 / 30 : 1000 / 15;
  }

  private readonly tick = (nowMs: number): void => {
    if (!this.running) return;
    const elapsedRender = nowMs - this.lastRenderMs;
    if (elapsedRender >= this.frameIntervalMs()) {
      const rawDt = Math.max(0, nowMs - this.lastTickMs) / 1000;
      const dt = Math.min(this.maxDtSeconds, rawDt);
      this.lastTickMs = nowMs;
      this.lastRenderMs = nowMs;
      this.renderFrame(dt, nowMs);
      this.renderCount += 1;
    }
    this.rafId = this.scheduler.request(this.tick);
  };
}
