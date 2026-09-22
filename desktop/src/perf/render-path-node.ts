// Per-frame signal math benchmark. Run with:
//
//   cd desktop && npm run bench:render
//
// esbuild bundles this file and plain node runs it, so the numbers are real V8
// behaviour instead of the test runner's module interop. The `legacy:` cases
// are literal copies of the pre-2026-09-22 render body: the old loop damped
// through `THREE.MathUtils.lerp`, built `[reply, geeknews, dbSync]`, allocated a
// fresh array via `base.map`, allocated a fresh pulse object and iterated the
// rings with a `forEach` closure. They are never imported by the app.
import * as THREE from "three";
import {
  latticePulse,
  ringTargetVelocities,
  writeLatticePulse,
  writeRingTargetVelocities,
  type LatticePulse,
  type SourceLoads,
} from "../core/load-mapping";
import { CoreRenderState } from "../core/render-state";

const BASE = [0.17, -0.12, 0.09];
const GAIN = [1.4, 1.65, 1.9];
const LOADS: SourceLoads = { reply: 0.6, geeknews: 0.3, dbSync: 0.45, total: 0.7 };
const DT = 0.033;
const ROUNDS = 5;
const ITERATIONS = 2_000_000;

function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

const legacyDamp = (current: number, target: number, lambda: number, dt: number): number =>
  THREE.MathUtils.lerp(current, target, 1 - Math.exp(-lambda * dt));

function legacyRingTargetVelocities(
  base: readonly number[],
  loads: SourceLoads,
  gain: readonly number[],
): number[] {
  const perSource = [loads.reply, loads.geeknews, loads.dbSync] as const;
  const globalBoost = 1 + 0.35 * clamp01(loads.total);
  return base.map((velocity, index) => {
    const safeBase = Number.isFinite(velocity) ? velocity : 0;
    const safeGain = Number.isFinite(gain[index]) ? gain[index] : 0;
    const load = clamp01(perSource[index] ?? 0);
    return safeBase * (1 + safeGain * load) * globalBoost;
  });
}

class LegacyFrame {
  // Mutable instance targets, exactly like the old class fields: writing LOADS
  // straight into the body would let V8 constant-fold the targets and flatter
  // the "before" side.
  readonly targetSources: SourceLoads = { reply: 0, geeknews: 0, dbSync: 0, total: 0 };
  targetLoad = 0;
  targetVoiceRms = 0;
  smoothLoad = 0;
  smoothVoiceRms = 0;
  nucleusScale = 1;
  nucleusVelocity = 0;
  readonly smoothSources: SourceLoads = { reply: 0, geeknews: 0, dbSync: 0, total: 0 };
  readonly ringVelocity = [0, 0, 0];
  readonly out = {
    dt: 0,
    smoothLoad: 0,
    smoothVoiceRms: 0,
    nucleusScale: 1,
    latticeScale: 1,
    latticeOpacity: 0.06,
  };

  step(dt: number, nowMs: number, base: readonly number[], gain: readonly number[]): typeof this.out {
    const target = this.targetSources;
    this.smoothLoad = legacyDamp(this.smoothLoad, this.targetLoad, 4.2, dt);
    this.smoothVoiceRms = legacyDamp(this.smoothVoiceRms, this.targetVoiceRms, 7.0, dt);
    this.smoothSources.reply = legacyDamp(this.smoothSources.reply, target.reply, 4.2, dt);
    this.smoothSources.geeknews = legacyDamp(this.smoothSources.geeknews, target.geeknews, 4.2, dt);
    this.smoothSources.dbSync = legacyDamp(this.smoothSources.dbSync, target.dbSync, 4.2, dt);
    this.smoothSources.total = legacyDamp(this.smoothSources.total, target.total, 4.2, dt);

    const targetVelocities = legacyRingTargetVelocities(base, this.smoothSources, gain);
    this.ringVelocity.forEach((_unused, index) => {
      this.ringVelocity[index] = legacyDamp(
        this.ringVelocity[index],
        targetVelocities[index],
        1.8 + index * 0.55,
        dt,
      );
    });

    const desiredScale = 1 + this.smoothLoad * 0.2 + this.smoothVoiceRms * 0.12;
    const acceleration = (desiredScale - this.nucleusScale) * 18 - this.nucleusVelocity * 7.5;
    this.nucleusVelocity += acceleration * dt;
    this.nucleusScale += this.nucleusVelocity * dt;

    const seconds = nowMs / 1000;
    const pulse = latticePulse(this.smoothSources.total, seconds);
    const backgroundPulse = 1 + pulse.amplitude * Math.sin(seconds * pulse.frequency * Math.PI * 2);
    // The old body did compute the acoustic term as well; leaving it out here
    // would drop one Math.sin from the "before" side and flatter legacy.
    const acousticPulse = 1 + this.smoothVoiceRms * (0.07 + 0.025 * Math.sin(nowMs * 0.012));
    this.out.dt = dt;
    this.out.smoothLoad = this.smoothLoad;
    this.out.smoothVoiceRms = this.smoothVoiceRms;
    this.out.nucleusScale = this.nucleusScale;
    this.out.latticeScale = backgroundPulse * acousticPulse;
    this.out.latticeOpacity = pulse.opacity;
    return this.out;
  }
}

function measure(run: () => void): number {
  const rates: number[] = [];
  for (let round = 0; round < ROUNDS; round += 1) {
    const started = process.hrtime.bigint();
    run();
    const elapsedNs = Number(process.hrtime.bigint() - started);
    rates.push((ITERATIONS / elapsedNs) * 1e9);
  }
  rates.sort((left, right) => left - right);
  return rates[Math.floor(rates.length / 2)];
}

function report(name: string, run: () => void): void {
  run();
  const hz = measure(run);
  console.log(`  ${name.padEnd(38)} ${hz.toFixed(0).padStart(12)} ops/s  ${((1 / hz) * 1e9).toFixed(1).padStart(7)} ns/op`);
}

const scratch = [0, 0, 0];
const pulseOut: LatticePulse = { frequency: 0, amplitude: 0, opacity: 0 };
const legacyFrame = new LegacyFrame();
legacyFrame.targetSources.reply = LOADS.reply;
legacyFrame.targetSources.geeknews = LOADS.geeknews;
legacyFrame.targetSources.dbSync = LOADS.dbSync;
legacyFrame.targetSources.total = LOADS.total;
legacyFrame.targetLoad = LOADS.total;
legacyFrame.targetVoiceRms = 0.25;
const newFrame = new CoreRenderState();
newFrame.setSignals(LOADS.total, 0.25, LOADS);
let tick = 0;
// The pulse results must be consumed, or V8 is free to delete the whole call.
let sink = 0;

console.log(`render path benchmark · ${ITERATIONS} iterations x ${ROUNDS} rounds, median ops/s`);
console.log("ring target velocities (per frame)");
report("legacy: base.map with a closure", () => {
  for (let index = 0; index < ITERATIONS; index += 1) legacyRingTargetVelocities(BASE, LOADS, GAIN);
});
report("legacy: allocating helper", () => {
  for (let index = 0; index < ITERATIONS; index += 1) ringTargetVelocities(BASE, LOADS, GAIN);
});
report("now: reused scratch array", () => {
  for (let index = 0; index < ITERATIONS; index += 1) writeRingTargetVelocities(scratch, BASE, LOADS, GAIN);
});
console.log("lattice pulse (per frame)");
report("legacy: object literal", () => {
  for (let index = 0; index < ITERATIONS; index += 1) {
    const pulse = latticePulse(LOADS.total, 12.5);
    sink += pulse.frequency + pulse.amplitude + pulse.opacity;
  }
});
report("now: reused object", () => {
  for (let index = 0; index < ITERATIONS; index += 1) {
    const pulse = writeLatticePulse(pulseOut, LOADS.total, 12.5);
    sink += pulse.frequency + pulse.amplitude + pulse.opacity;
  }
});
console.log("whole frame signal math");
report("legacy: inline render body", () => {
  for (let index = 0; index < ITERATIONS; index += 1) {
    tick += 1;
    legacyFrame.step(DT, tick * 33, BASE, GAIN);
  }
});
report("now: CoreRenderState.step", () => {
  for (let index = 0; index < ITERATIONS; index += 1) {
    tick += 1;
    newFrame.step(DT, tick * 33, BASE, GAIN);
  }
});

// Keep the accumulated pulse work observable.
console.log(`checksum ${sink.toFixed(3)}`);
