import * as THREE from "three";
import { describe, expect, it, vi } from "vitest";
import { AldenCore } from "../core/alden-core";
import { AnimationLoop, type FrameScheduler } from "../core/animation-loop";
import { RenderLifecycle } from "../core/lifecycle";

class FakeScheduler implements FrameScheduler {
  nowMs = 0;
  nextId = 1;
  callbacks = new Map<number, FrameRequestCallback>();

  request(callback: FrameRequestCallback): number {
    const id = this.nextId++;
    this.callbacks.set(id, callback);
    return id;
  }

  cancel(id: number): void {
    this.callbacks.delete(id);
  }

  now(): number {
    return this.nowMs;
  }

  step(nowMs: number): void {
    this.nowMs = nowMs;
    const queued = [...this.callbacks.values()];
    this.callbacks.clear();
    queued.forEach((callback) => callback(nowMs));
  }
}

describe("render loop lifecycle races", () => {
  it('keeps the 30 fps cadence under display timestamp rounding without a hidden backlog', () => {
    const scheduler=new FakeScheduler(),loop=new AnimationLoop(()=>undefined,scheduler);
    loop.setInteractive(true);loop.start();
    for(let frame=1;frame<=300;frame++)scheduler.step(frame*1000/60 + (frame%3-1)*.15);
    expect(loop.renderCount).toBeGreaterThanOrEqual(149);
    expect(loop.renderCount).toBeLessThanOrEqual(151);
    loop.stop();scheduler.step(10000);
    expect(scheduler.callbacks.size).toBe(0);
  });
  it("does not requeue a RAF when hidden during the current frame", () => {
    const scheduler = new FakeScheduler();
    let lifecycle: RenderLifecycle;
    const loop = new AnimationLoop(() => lifecycle.transition("hidden"), scheduler);
    lifecycle = new RenderLifecycle(loop, () => undefined, () => undefined, () => scheduler.now());

    lifecycle.transition("visible");
    scheduler.step(100);

    expect(loop.renderCount).toBe(1);
    expect(loop.isRunning()).toBe(false);
    expect(scheduler.callbacks.size).toBe(0);
  });

  it("keeps one RAF when hide and restore both land inside an old frame", () => {
    const scheduler = new FakeScheduler();
    let lifecycle: RenderLifecycle;
    let interrupt = true;
    const loop = new AnimationLoop(() => {
      if (!interrupt) return;
      interrupt = false;
      lifecycle.transition("hidden");
      lifecycle.transition("visible");
    }, scheduler);
    lifecycle = new RenderLifecycle(loop, () => undefined, () => undefined, () => scheduler.now());

    lifecycle.transition("visible");
    scheduler.step(100);
    expect(scheduler.callbacks.size).toBe(1);

    scheduler.step(200);
    expect(loop.renderCount).toBe(2);
    expect(scheduler.callbacks.size).toBe(1);
  });

  it("records hidden-event to last-frame evidence without counting later renders", () => {
    const scheduler = new FakeScheduler();
    const loop = new AnimationLoop(() => undefined, scheduler);
    const lifecycle = new RenderLifecycle(loop, () => undefined, () => undefined, () => scheduler.now());

    lifecycle.transition("visible");
    scheduler.step(100);
    scheduler.nowMs = 125;
    lifecycle.transition("hidden");
    scheduler.step(1_125);

    expect(lifecycle.lastPauseMeasurement()).toEqual({
      state: "hidden",
      eventAtMs: 125,
      observedAtMs: 1_125,
      renderCountAtEvent: 1,
      renderCountAtObservation: 1,
      rendersAfterEvent: 0,
      lastRenderAtMs: 100,
      lastFrameOffsetFromEventMs: -25,
      eventToLastFrameMs: 0,
      running: false,
      pendingFrame: false,
    });
  });

  it("does not multiply timer ownership across repeated visibility signals", () => {
    const scheduler = new FakeScheduler();
    const loop = new AnimationLoop(() => undefined, scheduler);
    let starts = 0;
    let stops = 0;
    const lifecycle = new RenderLifecycle(
      loop,
      () => { stops += 1; },
      () => { starts += 1; },
      () => scheduler.now(),
    );

    lifecycle.transition("visible");
    for (let index = 0; index < 50; index += 1) lifecycle.transition("visible");
    lifecycle.transition("hidden");
    const firstPauseAt = lifecycle.lastPauseMeasurement()?.eventAtMs;
    scheduler.nowMs = 500;
    for (let index = 0; index < 50; index += 1) lifecycle.transition("hidden");

    expect({ starts, stops, rafs: scheduler.callbacks.size }).toEqual({ starts: 1, stops: 1, rafs: 0 });
    expect(lifecycle.lastPauseMeasurement()?.eventAtMs).toBe(firstPauseAt);
  });
});

describe("GPU lifecycle ownership", () => {
  it("disposes each retained GPU resource once and cannot restart afterward", () => {
    const geometry = new THREE.BufferGeometry();
    const material = new THREE.MeshBasicMaterial();
    const mesh = new THREE.Mesh(geometry, material);
    const geometryDispose = vi.spyOn(geometry, "dispose");
    const materialDispose = vi.spyOn(material, "dispose");
    const rendererDispose = vi.fn();
    const loop = { start: vi.fn(), stop: vi.fn() };
    const core = Object.create(AldenCore.prototype) as AldenCore;

    Object.defineProperties(core, {
      disposed: { value: false, writable: true },
      loop: { value: loop },
      scene: { value: { traverse: (visit: (object: THREE.Object3D) => void) => visit(mesh) } },
      renderer: { value: { dispose: rendererDispose } },
    });

    core.dispose();
    core.dispose();
    core.start();

    expect(loop.stop).toHaveBeenCalledTimes(1);
    expect(loop.start).not.toHaveBeenCalled();
    expect(geometryDispose).toHaveBeenCalledTimes(1);
    expect(materialDispose).toHaveBeenCalledTimes(1);
    expect(rendererDispose).toHaveBeenCalledTimes(1);
  });
});
