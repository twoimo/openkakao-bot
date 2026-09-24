// @vitest-environment happy-dom
import { describe, expect, it } from "vitest";
import {
  latticePulse,
  ringTargetVelocities,
  writeLatticePulse,
  writeRingTargetVelocities,
  type LatticePulse,
  type SourceLoads,
} from "../core/load-mapping";
import { CoreRenderState, ringTilt } from "../core/render-state";
import { buildPulseLattice, pulseLatticeDrawCount } from "../core/pulse-lattice";

const BASE = [0.17, -0.12, 0.09] as const;
const GAIN = [1.4, 1.65, 1.9] as const;

function loads(reply: number, geeknews: number, dbSync: number, total: number): SourceLoads {
  return { reply, geeknews, dbSync, total };
}

const SIGNAL_CASES: SourceLoads[] = [
  loads(0, 0, 0, 0),
  loads(1, 1, 1, 1),
  loads(0.5, 0, 1, 0.25),
  loads(Number.NaN, 2, -1, 3),
  loads(Number.POSITIVE_INFINITY, -0.5, 0.5, Number.NaN),
];

describe("reused render scratch space", () => {
  it("writes exactly the values the allocating path returns", () => {
    for (const signals of SIGNAL_CASES) {
      const out = [0, 0, 0];
      expect(writeRingTargetVelocities(out, BASE, signals, GAIN)).toBe(out);
      expect(out).toEqual(ringTargetVelocities(BASE, signals, GAIN));
    }
  });

  it("keeps the old zero fallback past the last known source and for holes", () => {
    const out = [9, 9, 9, 9];
    writeRingTargetVelocities(out, [0.17, -0.12, 0.09, 0.2], loads(1, 1, 1, 0), [1, 1, 1, 1]);
    // Ring index 3 has no source of its own, so it keeps base * 1 (no boost).
    expect(out[3]).toBeCloseTo(0.2, 12);
    expect(out[0]).toBeCloseTo(0.34, 12);
    const holey: number[] = [0.17, -0.12, undefined as unknown as number];
    expect(ringTargetVelocities(holey, loads(1, 1, 1, 0), GAIN)[2]).toBe(0);
  });

  it("writes the lattice pulse into the caller's object", () => {
    const out: LatticePulse = { frequency: 0, amplitude: 0, opacity: 0, density: 0 };
    for (const total of [-4, 0, 0.5, 1, 5, Number.NaN]) {
      expect(writeLatticePulse(out, total, 0)).toBe(out);
      expect(out).toEqual(latticePulse(total, 0));
    }
  });

  it("tilts rings by their own velocity in the same proportions as before", () => {
    expect(ringTilt(0)).toBeCloseTo(0.22, 12);
    expect(ringTilt(1)).toBeCloseTo(0.28, 12);
    expect(ringTilt(2)).toBeCloseTo(0.34, 12);
  });
});

describe("core render state", () => {
  it("matches the damped integration the inline renderer used", () => {
    const state = new CoreRenderState();
    state.setSignals(0.8, 0.4, loads(0.3, 0.6, 0.9, 0.7));
    const dt = 0.02;
    const damp = (current: number, target: number, lambda: number): number =>
      current + (target - current) * (1 - Math.exp(-lambda * dt));
    let smoothLoad = 0;
    let smoothVoice = 0;
    let scale = 1;
    let velocity = 0;
    const smoothSources = { reply: 0, geeknews: 0, dbSync: 0, total: 0, voice: 0 };
    const ringVelocity = [0, 0, 0];
    let frame: CoreRenderState | undefined;
    for (let index = 0; index < 150; index += 1) {
      smoothLoad = damp(smoothLoad, 0.8, 4.2);
      smoothVoice = damp(smoothVoice, 0.4, 7.0);
      smoothSources.reply = damp(smoothSources.reply, 0.3, 4.2);
      smoothSources.geeknews = damp(smoothSources.geeknews, 0.6, 4.2);
      smoothSources.dbSync = damp(smoothSources.dbSync, 0.9, 4.2);
      smoothSources.total = damp(smoothSources.total, 0.8, 4.2);
      smoothSources.voice = smoothVoice;
      const desiredScale = 1 + smoothLoad * 0.2 + smoothVoice * 0.12;
      const acceleration = (desiredScale - scale) * 18 - velocity * 7.5;
      velocity += acceleration * dt;
      scale += velocity * dt;
      const targets = ringTargetVelocities(BASE, smoothSources, GAIN);
      for (let ring = 0; ring < 3; ring += 1) {
        ringVelocity[ring] = damp(ringVelocity[ring], targets[ring], 1.8 + ring * 0.55);
      }

      frame = state.step(dt, index * 20, BASE, GAIN);
      expect(frame.smoothLoad).toBeCloseTo(smoothLoad, 12);
      expect(frame.smoothVoiceRms).toBeCloseTo(smoothVoice, 12);
      expect(frame.nucleusScale).toBeCloseTo(scale, 12);
      expect([...frame.ringTargets]).toEqual(targets);
      expect([...frame.ringVelocities][0]).toBeCloseTo(ringVelocity[0], 12);
      expect([...frame.ringVelocities][2]).toBeCloseTo(ringVelocity[2], 12);
    }
    expect(frame).toBeDefined();
  });

  it("returns one reused frame object and never regrows its arrays", () => {
    const state = new CoreRenderState();
    state.setSignals(0.5, 0.2, loads(0.4, 0.4, 0.4, 0.5));
    const first = state.step(0.016, 0, BASE, GAIN);
    const velocities = first.ringVelocities;
    const targets = first.ringTargets;
    for (let index = 1; index <= 200; index += 1) {
      const frame = state.step(0.016, index * 16, BASE, GAIN);
      expect(frame).toBe(first);
      expect(frame.ringVelocities).toBe(velocities);
      expect(frame.ringTargets).toBe(targets);
    }
    expect(first.ringVelocities.length).toBe(BASE.length);
    expect(first.ringTargets.length).toBe(BASE.length);
  });

  it("allocates no array in the per-frame path", () => {
    const state = new CoreRenderState();
    state.setSignals(0.6, 0.2, loads(0.5, 0.5, 0.5, 0.6));
    state.step(0.016, 0, BASE, GAIN);
    const realMap = Array.prototype.map;
    const realSlice = Array.prototype.slice;
    const realConcat = Array.prototype.concat;
    const counts = { map: 0, slice: 0, concat: 0 };
    const prototype = Array.prototype as unknown as Record<string, unknown>;
    prototype.map = function patchedMap(this: number[], ...args: unknown[]): unknown {
      counts.map += 1;
      return (realMap as (...call: unknown[]) => unknown).apply(this, args);
    };
    prototype.slice = function patchedSlice(this: unknown[], ...args: unknown[]): unknown {
      counts.slice += 1;
      return (realSlice as (...call: unknown[]) => unknown).apply(this, args);
    };
    prototype.concat = function patchedConcat(this: unknown[], ...args: unknown[]): unknown {
      counts.concat += 1;
      return (realConcat as (...call: unknown[]) => unknown).apply(this, args);
    };
    try {
      for (let index = 0; index < 300; index += 1) {
        state.step(0.016, index * 16, BASE, GAIN);
      }
    } finally {
      prototype.map = realMap;
      prototype.slice = realSlice;
      prototype.concat = realConcat;
    }
    expect(counts).toEqual({ map: 0, slice: 0, concat: 0 });
  });

  it("clamps a long resume gap and survives non-finite input", () => {
    const state = new CoreRenderState();
    state.setSignals(1, 1, loads(1, 1, 1, 1));
    const resumed = state.step(10, 1_000_000, BASE, GAIN);
    expect(resumed.dt).toBe(0.25);
    expect(resumed.smoothLoad).toBeLessThan(1);
    expect(Number.isFinite(resumed.nucleusScale)).toBe(true);

    const broken = state.step(Number.NaN, Number.NaN, BASE, GAIN);
    expect(broken.dt).toBe(0);
    for (const value of [
      broken.smoothLoad,
      broken.smoothVoiceRms,
      broken.nucleusScale,
      broken.latticeScale,
      broken.latticeOpacity,
      broken.pulse.frequency,
    ]) {
      expect(Number.isFinite(value)).toBe(true);
    }
  });

  it("fails closed on non-finite signals instead of poisoning the loop", () => {
    const state = new CoreRenderState();
    state.setSignals(Number.NaN, Number.POSITIVE_INFINITY, {
      reply: Number.NaN,
      geeknews: -3,
      dbSync: 4,
      total: Number.POSITIVE_INFINITY,
    });
    expect(state.jobLoad).toBe(0);
    const frame = state.step(0.05, 0, BASE, GAIN);
    expect(frame.latticeScale).toBeGreaterThan(0);
    expect(Number.isFinite(frame.nucleusScale)).toBe(true);
    expect([...frame.ringTargets].every((value) => Number.isFinite(value))).toBe(true);
  });

  it("resizes its ring arrays when the ring count changes", () => {
    const state = new CoreRenderState();
    const frame = state.step(0.016, 0, [0.1, 0.2, 0.3, 0.4], [1, 1, 1, 1]);
    expect(frame.ringVelocities.length).toBe(4);
    expect(frame.ringTargets.length).toBe(4);
    expect([...frame.ringTargets].every((value) => Number.isFinite(value))).toBe(true);
  });

  it("preserves 15fps elapsed time and lets voice activity drive global motion", () => {
    const state = new CoreRenderState();
    state.setSignals(0, 0.8, loads(0, 0, 0, 0));
    const frame = state.step(1 / 15, 0, BASE, GAIN);
    expect(frame.dt).toBeCloseTo(1 / 15, 12);
    expect(frame.jobLoad).toBe(0.8);
    expect(frame.pulse.density).toBeGreaterThan(0);
    expect(frame.ringTargets[0]).toBeGreaterThan(BASE[0]);
    expect(frame.ringTargets[1]).toBeLessThan(BASE[1]);
  });

  it("accumulates pulse phase continuously as load changes", () => {
    const state = new CoreRenderState();
    state.setSignals(0, 0, loads(0, 0, 0, 0));
    const first = state.step(0.05, 0, BASE, GAIN).latticeScale;
    state.setSignals(1, 0, loads(0, 0, 0, 1));
    const second = state.step(0.05, 50, BASE, GAIN).latticeScale;
    expect(first).toBeGreaterThan(1);
    expect(second).toBeGreaterThan(1);
    expect(Math.abs(second - first)).toBeLessThan(0.1);
  });

  it.each([15, 30])("smoothly reveals and retracts prebuilt segments at %i fps", (fps) => {
    const lattice = buildPulseLattice(1.08);
    const state = new CoreRenderState();
    let previous = lattice.drawCounts[0];
    for (const target of [1, 0]) {
      // Background-only activity must reach the lattice even with no job load.
      state.setSignals(0, 0, loads(0, 0, 0, target));
      const counts = new Set<number>();
      for (let frame = 0; frame < fps * 3; frame += 1) {
        const density = state.step(1 / fps, frame * 1000 / fps, BASE, GAIN).pulse.density;
        const count = pulseLatticeDrawCount(density, lattice.drawCounts);
        expect(target === 1 ? count >= previous : count <= previous).toBe(true);
        // Even the first frame of a full step is smaller than the old tier jump.
        expect(Math.abs(count - previous)).toBeLessThan(448);
        counts.add(count);
        previous = count;
      }
      expect(counts.size).toBeGreaterThan(3);
      expect(previous).toBe(target === 1 ? lattice.drawCounts[2] : lattice.drawCounts[0]);
    }
  });
});
