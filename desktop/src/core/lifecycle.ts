import type { AnimationLoopDiagnostics, LifecycleState } from "./animation-loop";

export interface RenderLoopControl {
  start(): void;
  stop(): void;
  diagnostics?(): AnimationLoopDiagnostics;
}

type PauseState = Exclude<LifecycleState, "visible">;

export interface RenderPauseMeasurement {
  state: PauseState;
  eventAtMs: number;
  observedAtMs: number;
  renderCountAtEvent: number;
  renderCountAtObservation: number;
  rendersAfterEvent: number;
  lastRenderAtMs: number | null;
  /** Signed offset: negative means the last frame preceded the pause event. */
  lastFrameOffsetFromEventMs: number | null;
  /** Non-negative late-frame latency; zero means no frame completed afterward. */
  eventToLastFrameMs: number;
  running: boolean;
  pendingFrame: boolean;
}

interface PauseSeed {
  state: PauseState;
  eventAtMs: number;
  renderCountAtEvent: number;
}

export class RenderLifecycle {
  private active = false;
  private surfaceVisible = true;
  private requestedState: LifecycleState | null = null;
  private state: LifecycleState | null = null;
  private pauseSeed: PauseSeed | null = null;
  private completedPause: RenderPauseMeasurement | null = null;

  constructor(
    private readonly loop: RenderLoopControl,
    private readonly stopTimers: () => void,
    private readonly startTimers: () => void,
    private readonly now: () => number = () => performance.now(),
  ) {}

  setSurfaceVisible(visible: boolean): void {
    if (this.surfaceVisible === visible) return;
    this.surfaceVisible = visible;
    if (this.requestedState !== null) this.apply(this.effectiveState(this.requestedState));
  }

  transition(state: LifecycleState): void {
    this.requestedState = state;
    this.apply(this.effectiveState(state));
  }

  private effectiveState(state: LifecycleState): LifecycleState {
    return state === "visible" && !this.surfaceVisible ? "hidden" : state;
  }

  private apply(state: LifecycleState): void {
    if (state === "visible") {
      if (this.active) return;
      this.completedPause = this.measurePause();
      this.pauseSeed = null;
      // `active` only follows a start that actually happened. Claiming it first
      // meant a throwing `start()` left the panel inactive-but-marked-active,
      // so every later visible signal returned early and nothing rendered until
      // the next hide/show cycle (2026-09-22).
      try {
        this.loop.start();
      } catch {
        return;
      }
      this.active = true;
      this.state = "visible";
      this.safely(this.startTimers);
      return;
    }
    if (!this.active && this.state === state) return;
    const eventAtMs = this.safeNow();
    const diagnostics = this.loop.diagnostics?.();
    this.pauseSeed = {
      state,
      eventAtMs,
      renderCountAtEvent: diagnostics?.renderCount ?? 0,
    };
    this.active = false;
    this.state = state;
    // A throwing timer cleanup used to skip the loop stop and leave a render
    // loop running behind a hidden window, so each half is guarded on its own
    // (2026-09-22).
    this.safely(this.stopTimers);
    this.safely(() => this.loop.stop());
  }

  lastPauseMeasurement(): RenderPauseMeasurement | null {
    return this.measurePause() ?? this.completedPause;
  }

  private measurePause(): RenderPauseMeasurement | null {
    const seed = this.pauseSeed;
    const diagnostics = this.loop.diagnostics?.();
    if (!seed || !diagnostics) return null;
    const rendersAfterEvent = Math.max(0, diagnostics.renderCount - seed.renderCountAtEvent);
    const lastFrameOffsetFromEventMs = diagnostics.lastRenderAtMs === null
      ? null
      : diagnostics.lastRenderAtMs - seed.eventAtMs;
    const eventToLastFrameMs = rendersAfterEvent > 0 && lastFrameOffsetFromEventMs !== null
      ? Math.max(0, lastFrameOffsetFromEventMs)
      : 0;
    return {
      state: seed.state,
      eventAtMs: seed.eventAtMs,
      observedAtMs: this.safeNow(),
      renderCountAtEvent: seed.renderCountAtEvent,
      renderCountAtObservation: diagnostics.renderCount,
      rendersAfterEvent,
      lastRenderAtMs: diagnostics.lastRenderAtMs,
      lastFrameOffsetFromEventMs,
      eventToLastFrameMs,
      running: diagnostics.running,
      pendingFrame: diagnostics.pendingFrame,
    };
  }

  private safeNow(): number {
    try {
      const value = this.now();
      return Number.isFinite(value) ? value : 0;
    } catch {
      return 0;
    }
  }

  private safely(run: () => void): void {
    try {
      run();
    } catch {
      // Render or timer failures must not take the panel process down.
    }
  }
}
