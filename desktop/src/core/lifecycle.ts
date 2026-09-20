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
      this.loop.start();
      this.startTimers();
      return;
    }
    this.active = false;
    this.stopTimers();
    this.loop.stop();
  }
}
