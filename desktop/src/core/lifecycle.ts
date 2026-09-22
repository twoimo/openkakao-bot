import type { LifecycleState } from "./animation-loop";

export interface RenderLoopControl {
  start(): void;
  stop(): void;
}

export class RenderLifecycle {
  private active = false;

  constructor(
    private readonly loop: RenderLoopControl,
    private readonly stopTimers: () => void,
    private readonly startTimers: () => void,
  ) {}

  transition(state: LifecycleState): void {
    if (state === "visible") {
      if (this.active) return;
      this.active = true;
      this.safely(() => this.loop.start());
      this.safely(this.startTimers);
      return;
    }
    this.active = false;
    // A throwing timer cleanup used to skip the loop stop and leave a render
    // loop running behind a hidden window, so each half is guarded on its own
    // (2026-09-22).
    this.safely(this.stopTimers);
    this.safely(() => this.loop.stop());
  }

  private safely(run: () => void): void {
    try {
      run();
    } catch {
      // Render or timer failures must not take the panel process down.
    }
  }
}
