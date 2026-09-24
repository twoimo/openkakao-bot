// @vitest-environment happy-dom
import * as THREE from "three";
import { afterEach, describe, expect, it, vi } from "vitest";
import { JarvisCore } from "../core/jarvis-core";
import { RenderLifecycle } from "../core/lifecycle";

// Exercise the real scene, geometry and animation loop, but make no GPU claim.
const renderer = vi.hoisted(() => ({ render: vi.fn(), dispose: vi.fn() }));
vi.mock("three", async (importOriginal) => {
  const actual = await importOriginal<typeof import("three")>();
  return {
    ...actual,
    WebGLRenderer: class {
      setPixelRatio(): void {}
      setSize(): void {}
      setClearColor(): void {}
      render = renderer.render;
      dispose = renderer.dispose;
    },
  };
});

function harness() {
  let nowMs = 0;
  let nextId = 0;
  const callbacks = new Map<number, FrameRequestCallback>();
  vi.spyOn(performance, "now").mockImplementation(() => nowMs);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback): number => {
    callbacks.set(++nextId, callback);
    return nextId;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number): void => { callbacks.delete(id); });
  const core = new JarvisCore(document.createElement("canvas"));
  const lifecycle = new RenderLifecycle(core, () => undefined, () => undefined);
  const advance = (ms = 1000 / 30 + 1): void => {
    nowMs += ms;
    const queued = [...callbacks.values()];
    callbacks.clear();
    queued.forEach((callback) => callback(nowMs));
  };
  return { core, lifecycle, advance, callbacks };
}

function renderedObjects() {
  const scene = renderer.render.mock.lastCall?.[0] as THREE.Scene;
  const objects: (THREE.Mesh | THREE.Points | THREE.LineSegments)[] = [];
  scene.traverse((object) => {
    if (object instanceof THREE.Mesh || object instanceof THREE.Points || object instanceof THREE.LineSegments) {
      objects.push(object);
    }
  });
  return objects;
}

function renderedLattice(objects: ReturnType<typeof renderedObjects>): THREE.LineSegments {
  const lattice = objects.find((object) => object instanceof THREE.LineSegments
    && object.geometry.getAttribute("position").count === 1344);
  if (!(lattice instanceof THREE.LineSegments)) throw new Error("Missing prebuilt pulse lattice");
  return lattice;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("Jarvis core lattice integration", () => {
  it("changes only the draw range while retaining one lattice drawable and all buffers", () => {
    const { core, lifecycle, advance } = harness();
    try {
      lifecycle.transition("visible");
      advance(100);
      const objects = renderedObjects();
      expect(objects).toHaveLength(8);
      const lattice = renderedLattice(objects);
      const geometry = lattice.geometry;
      const material = lattice.material;
      const position = geometry.getAttribute("position") as THREE.BufferAttribute;
      const positions = position.array;
      const originalPositions = positions.slice();
      const version = position.version;
      const setDrawRange = vi.spyOn(geometry, "setDrawRange");
      const counts = new Set<number>([geometry.drawRange.count]);

      for (const load of [0, 0.21, 0.23, 0.61, 0.63, 1, 0]) {
        core.setSignals(0, 0, { reply: 0.2, geeknews: 0.4, dbSync: 0.6, total: load });
        for (let frame = 0; frame < 90; frame += 1) {
          advance();
          const current = renderedObjects();
          expect(current).toHaveLength(objects.length);
          current.forEach((object, index) => expect(object).toBe(objects[index]));
          expect(lattice.geometry).toBe(geometry);
          expect(lattice.material).toBe(material);
          expect(geometry.getAttribute("position")).toBe(position);
          expect(position.array).toBe(positions);
          expect(position.version).toBe(version);
          expect(geometry.groups).toHaveLength(0);
          expect(geometry.drawRange.start).toBe(0);
          expect(geometry.drawRange.count % 2).toBe(0);
          expect(geometry.drawRange.count).toBeGreaterThanOrEqual(448);
          expect(geometry.drawRange.count).toBeLessThanOrEqual(position.count);
          counts.add(geometry.drawRange.count);
        }
      }
      expect(counts.size).toBeGreaterThan(3);
      expect(counts.has(1344)).toBe(true);
      expect(positions).toEqual(originalPositions);
      const updates = setDrawRange.mock.calls.length;
      for (let frame = 0; frame < 30; frame += 1) advance();
      expect(setDrawRange).toHaveBeenCalledTimes(updates);
    } finally {
      core.dispose();
    }
  });

  it("freezes density and rendering while hidden, locked or closed and resumes one loop", () => {
    const { core, lifecycle, advance, callbacks } = harness();
    try {
      lifecycle.transition("visible");
      advance(100);
      const lattice = renderedLattice(renderedObjects());
      for (const hidden of ["hidden", "locked", "closed"] as const) {
        lifecycle.transition(hidden);
        const renderCount = core.renderCount;
        const drawCount = lattice.geometry.drawRange.count;
        core.setSignals(1, 1, { reply: 1, geeknews: 1, dbSync: 1, total: 1 });
        advance(10_000);
        expect(callbacks.size).toBe(0);
        expect(core.renderCount).toBe(renderCount);
        expect(renderer.render).toHaveBeenCalledTimes(renderCount);
        expect(lattice.geometry.drawRange.count).toBe(drawCount);
        lifecycle.transition("visible");
        lifecycle.transition("visible");
        expect(callbacks.size).toBe(1);
        advance();
        expect(core.renderCount).toBe(renderCount + 1);
        expect(callbacks.size).toBe(1);
      }
    } finally {
      core.dispose();
    }
  });
});
