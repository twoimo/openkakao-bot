// @vitest-environment happy-dom
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AnimationLoop, type FrameScheduler } from "../core/animation-loop";
import { RenderLifecycle } from "../core/lifecycle";
import {
  VISIBILITY_EVENT,
  wireRenderLifecycle,
  type VisibilityHandler,
  type VisibilitySubscriber,
} from "../core/lifecycle-wiring";

// Locate the shell source by walking up from the working directory, so the
// check does not depend on how this runner exposes `import.meta.url`.
function readShellSource(): string {
  const relative = join("src-tauri", "src", "main.rs");
  let directory = process.cwd();
  for (let depth = 0; depth < 5; depth += 1) {
    const candidate = join(directory, relative);
    if (existsSync(candidate)) return readFileSync(candidate, "utf8");
    directory = dirname(directory);
  }
  throw new Error(`rust shell source not found above ${process.cwd()}`);
}

const RUST_MAIN = readShellSource();

const settle = (): Promise<void> => new Promise((resolve) => { setTimeout(resolve, 0); });

class FakeScheduler implements FrameScheduler {
  nowMs = 0;
  nextId = 1;
  callbacks = new Map<number, FrameRequestCallback>();
  request(callback: FrameRequestCallback): number {
    const id = this.nextId++;
    this.callbacks.set(id, callback);
    return id;
  }
  cancel(id: number): void { this.callbacks.delete(id); }
  now(): number { return this.nowMs; }
  step(ms: number): void {
    this.nowMs = ms;
    const queued = [...this.callbacks.values()];
    this.callbacks.clear();
    queued.forEach((callback) => callback(ms));
  }
}

interface Harness {
  scheduler: FakeScheduler;
  loop: AnimationLoop;
  detach: () => void;
  signal: (visible: boolean) => void;
  subscribeError: () => unknown;
  counts: () => { stoppedTimers: number; startedTimers: number; closed: number };
}

function harness(subscribeVisibility: VisibilitySubscriber | null = null): Harness {
  const scheduler = new FakeScheduler();
  const loop = new AnimationLoop(() => undefined, scheduler);
  let stoppedTimers = 0;
  let startedTimers = 0;
  let closed = 0;
  let handler: VisibilityHandler | null = null;
  const subscriber: VisibilitySubscriber | null = subscribeVisibility === null
    ? null
    : (next) => {
        handler = next;
        return subscribeVisibility(next);
      };
  const lifecycle = new RenderLifecycle(
    loop,
    () => { stoppedTimers += 1; },
    () => { startedTimers += 1; },
  );
  const detach = wireRenderLifecycle(lifecycle, {
    subscribeVisibility: subscriber,
    onClosed: () => { closed += 1; },
  });
  return {
    scheduler,
    loop,
    detach,
    signal: (visible) => handler?.(visible),
    subscribeError: () => handler,
    counts: () => ({ stoppedTimers, startedTimers, closed }),
  };
}

function withVisibility(state: DocumentVisibilityState, run: () => void): void {
  const own = Object.getOwnPropertyDescriptor(document, "visibilityState");
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
  try {
    run();
  } finally {
    if (own) Object.defineProperty(document, "visibilityState", own);
    else delete (document as unknown as Record<string, unknown>).visibilityState;
  }
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("render lifecycle wiring", () => {
  it("starts on a visible document and stops the loop and poller on blur", () => {
    withVisibility("visible", () => {
      const { scheduler, loop, counts } = harness();
      expect(scheduler.callbacks.size).toBe(1);
      window.dispatchEvent(new Event("blur"));
      expect(scheduler.callbacks.size).toBe(0);
      const frozen = loop.renderCount;
      scheduler.step(1000);
      expect(loop.renderCount - frozen).toBe(0);
      expect(counts()).toEqual({ stoppedTimers: 1, startedTimers: 1, closed: 0 });
    });
  });

  it("resumes on focus with exactly one RAF", () => {
    withVisibility("visible", () => {
      const { scheduler, counts } = harness();
      window.dispatchEvent(new Event("blur"));
      window.dispatchEvent(new Event("focus"));
      window.dispatchEvent(new Event("focus"));
      expect(scheduler.callbacks.size).toBe(1);
      expect(counts().startedTimers).toBe(2);
    });
  });

  it("stops on an explicit hidden signal while the document still says visible", async () => {
    await withVisibilityAsync("visible", async () => {
      const { scheduler, loop, signal } = harness(async () => () => undefined);
      await settle();
      expect(scheduler.callbacks.size).toBe(1);
      signal(false);
      expect(scheduler.callbacks.size).toBe(0);
      const frozen = loop.renderCount;
      scheduler.step(5000);
      expect(loop.renderCount - frozen).toBe(0);
    });
  });

  it("lets an explicit visible signal override a document that reports hidden", async () => {
    await withVisibilityAsync("hidden", async () => {
      const { scheduler, signal } = harness(async () => () => undefined);
      await settle();
      expect(scheduler.callbacks.size).toBe(0);
      // Focus alone is still gated on the document's own visibility state.
      window.dispatchEvent(new Event("focus"));
      expect(scheduler.callbacks.size).toBe(0);
      signal(true);
      expect(scheduler.callbacks.size).toBe(1);
    });
  });

  it("follows document visibility changes without an explicit signal", () => {
    withVisibility("visible", () => {
      const { scheduler } = harness();
      Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" });
      document.dispatchEvent(new Event("visibilitychange"));
      expect(scheduler.callbacks.size).toBe(0);
      Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
      document.dispatchEvent(new Event("visibilitychange"));
      expect(scheduler.callbacks.size).toBe(1);
    });
  });

  it("closes once on pagehide, stops rendering, and detaches idempotently", () => {
    withVisibility("visible", () => {
      const { scheduler, detach, counts } = harness();
      window.dispatchEvent(new Event("pagehide"));
      window.dispatchEvent(new Event("pagehide"));
      expect(counts()).toEqual({ stoppedTimers: 1, startedTimers: 1, closed: 1 });
      expect(scheduler.callbacks.size).toBe(0);
      detach();
      detach();
      window.dispatchEvent(new Event("focus"));
      expect(scheduler.callbacks.size).toBe(0);
      expect(counts().closed).toBe(1);
    });
  });

  it("stays usable when the Tauri bridge is missing or fails", async () => {
    const rejected = vi.fn(async () => { throw new Error("no tauri internals"); });
    await withVisibilityAsync("visible", async () => {
      const { scheduler, loop, detach } = harness(rejected as unknown as VisibilitySubscriber);
      await settle();
      window.dispatchEvent(new Event("blur"));
      expect(scheduler.callbacks.size).toBe(0);
      const frozen = loop.renderCount;
      scheduler.step(500);
      expect(loop.renderCount - frozen).toBe(0);
      detach();
    });
    expect(rejected).toHaveBeenCalledTimes(1);
  });

  it("releases the bridge subscription when the page closes", async () => {
    await withVisibilityAsync("visible", async () => {
      let released = 0;
      const { detach } = harness(async () => () => { released += 1; });
      await settle();
      window.dispatchEvent(new Event("pagehide"));
      expect(released).toBe(1);
      detach();
      expect(released).toBe(1);
    });
  });

  it("does not let a throwing transition escape the event handler", () => {
    withVisibility("visible", () => {
      const thrown = new Error("render state exploded");
      wireRenderLifecycle({ transition: () => { throw thrown; } });
      expect(() => window.dispatchEvent(new Event("blur"))).not.toThrow();
      expect(() => window.dispatchEvent(new Event("visibilitychange"))).not.toThrow();
    });
  });

  it("pins the event name the Rust shell announces", () => {
    const rust = RUST_MAIN;
    expect(VISIBILITY_EVENT).toBe("jarvis://visibility");
    expect(rust).toMatch(/const\s+VISIBILITY_EVENT\s*:\s*&str\s*=\s*"jarvis:\/\/visibility";/);
    // Both hide paths in the shell must announce the hidden state.
    const hidden = rust.match(/announce_visibility\(window\.app_handle\(\), window\.label\(\), false\)/g) ?? [];
    expect(hidden.length).toBeGreaterThanOrEqual(2);
    expect(rust).toContain("announce_visibility(&app, window.label(), true)");
  });
});

async function withVisibilityAsync(state: DocumentVisibilityState, run: () => Promise<void>): Promise<void> {
  const own = Object.getOwnPropertyDescriptor(document, "visibilityState");
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
  try {
    await run();
  } finally {
    if (own) Object.defineProperty(document, "visibilityState", own);
    else delete (document as unknown as Record<string, unknown>).visibilityState;
  }
}
