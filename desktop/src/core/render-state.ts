import {
  writeLatticePulse,
  writeRingTargetVelocities,
  type LatticePulse,
  type SourceLoads,
} from "./load-mapping";

// Preserve 15fps frame time, while bounding unusually long foreground stalls.
const MAX_FRAME_SECONDS = 0.25;
const MAX_SPRING_STEP_SECONDS = 0.05;
const SIGNAL_LAMBDA = 4.2;
const VOICE_LAMBDA = 7.0;
const RING_INERTIA_BASE = 1.8;
const RING_INERTIA_STEP = 0.55;
const RING_TILT_BASE = 0.22;
const RING_TILT_STEP = 0.06;
const NUCLEUS_STIFFNESS = 18;
const NUCLEUS_DAMPING = 7.5;
const LOAD_SCALE_GAIN = 0.2;
const VOICE_SCALE_GAIN = 0.12;
const LATTICE_ACOUSTIC_BASE = 0.07;
const LATTICE_ACOUSTIC_WOBBLE = 0.025;
const TAU = Math.PI * 2;
const ACOUSTIC_WOBBLE_HZ = 1.9;

export function damp(current: number, target: number, lambda: number, dt: number): number {
  return current + (target - current) * (1 - Math.exp(-lambda * dt));
}

/** Ring i tilts its x axis by this factor of its own angular velocity. */
export function ringTilt(index: number): number {
  return RING_TILT_BASE + index * RING_TILT_STEP;
}

/**
 * The per-frame signal math for the Jarvis core.
 *
 * The state object is also the frame result: `step` returns `this`, so the
 * 15-30fps loop reads fields that the math already wrote instead of copying
 * them into a second object. Every container it reads or writes is a stable
 * field, so a frame allocates nothing (2026-09-22).
 */
export class CoreRenderState {
  /** Clamped seconds of the last step; 0 for a refused step. */
  dt = 0;
  smoothLoad = 0;
  smoothVoiceRms = 0;
  nucleusScale = 1;
  latticeScale = 1;
  latticeOpacity = 0.06;
  readonly pulse: LatticePulse = { frequency: 1, amplitude: 0.05, opacity: 0.06, density: 0 };
  /** Smoothed per-source loads; also the input of the ring targets. */
  readonly smoothSources: SourceLoads = { reply: 0, geeknews: 0, dbSync: 0, total: 0, voice: 0 };
  readonly ringVelocities: number[] = [0, 0, 0];
  readonly ringTargets: number[] = [0, 0, 0];
  private readonly targetSources: SourceLoads = { reply: 0, geeknews: 0, dbSync: 0, total: 0, voice: 0 };
  private targetLoad = 0;
  private targetVoiceRms = 0;
  private nucleusVelocity = 0;
  private pulsePhase = 0;
  private acousticPhase = 0;

  /** The clamped job load the frame rate is derived from. */
  get jobLoad(): number {
    return Math.max(this.targetLoad, this.targetSources.total, this.targetVoiceRms);
  }

  /** Clamp and store the incoming load signals. Never throws on bad input. */
  setSignals(jobLoad: number, voiceRms: number, sources?: SourceLoads): void {
    const safe = (value: number): number => Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 0;
    this.targetLoad = safe(jobLoad);
    this.targetVoiceRms = safe(voiceRms);
    const target = this.targetSources;
    target.voice = this.targetVoiceRms;
    if (sources) {
      target.reply = safe(sources.reply);
      target.geeknews = safe(sources.geeknews);
      target.dbSync = safe(sources.dbSync);
      target.total = Math.max(safe(sources.total), this.targetLoad, target.voice);
      return;
    }
    target.reply = this.targetLoad;
    target.geeknews = this.targetLoad;
    target.dbSync = this.targetLoad;
    target.total = Math.max(this.targetLoad, target.voice);
  }

  /** Advance the smoothing and spring state by one frame; returns `this`. */
  step(
    dtSeconds: number,
    _nowMs: number,
    base: readonly number[],
    gain: readonly number[],
  ): CoreRenderState {
    if (base.length !== this.ringVelocities.length) this.resizeRings(base.length);
    const dt = Number.isFinite(dtSeconds) && dtSeconds > 0
      ? Math.min(dtSeconds, MAX_FRAME_SECONDS)
      : 0;
    this.smoothLoad = damp(this.smoothLoad, this.targetLoad, SIGNAL_LAMBDA, dt);
    this.smoothVoiceRms = damp(this.smoothVoiceRms, this.targetVoiceRms, VOICE_LAMBDA, dt);

    const smooth = this.smoothSources;
    const target = this.targetSources;
    const reply = damp(smooth.reply, target.reply, SIGNAL_LAMBDA, dt);
    const geeknews = damp(smooth.geeknews, target.geeknews, SIGNAL_LAMBDA, dt);
    const dbSync = damp(smooth.dbSync, target.dbSync, SIGNAL_LAMBDA, dt);
    const total = damp(smooth.total, target.total, SIGNAL_LAMBDA, dt);
    const voice = damp(smooth.voice ?? 0, target.voice ?? 0, VOICE_LAMBDA, dt);
    smooth.reply = reply;
    smooth.geeknews = geeknews;
    smooth.dbSync = dbSync;
    smooth.total = total;
    smooth.voice = voice;

    // No guard on the two values below: `setSignals` clamps every input, the
    // step is clamped, and `writeRingTargetVelocities` already replaces a
    // non-finite base or gain with 0, so these stay finite without a per-ring
    // `Number.isFinite` check.
    writeRingTargetVelocities(this.ringTargets, base, smooth, gain);
    const velocities = this.ringVelocities;
    const targets = this.ringTargets;
    for (let index = 0; index < velocities.length; index += 1) {
      velocities[index] = damp(
        velocities[index],
        targets[index],
        RING_INERTIA_BASE + index * RING_INERTIA_STEP,
        dt,
      );
    }

    const desiredScale = 1 + this.smoothLoad * LOAD_SCALE_GAIN + this.smoothVoiceRms * VOICE_SCALE_GAIN;
    let springRemaining = dt;
    while (springRemaining > 0) {
      const springDt = Math.min(springRemaining, MAX_SPRING_STEP_SECONDS);
      const acceleration = (desiredScale - this.nucleusScale) * NUCLEUS_STIFFNESS
        - this.nucleusVelocity * NUCLEUS_DAMPING;
      this.nucleusVelocity += acceleration * springDt;
      this.nucleusScale += this.nucleusVelocity * springDt;
      springRemaining -= springDt;
    }

    const globalLoad = Math.max(total, this.smoothLoad, this.smoothVoiceRms);
    const pulse = writeLatticePulse(this.pulse, globalLoad, 0);
    this.pulsePhase = (this.pulsePhase + TAU * pulse.frequency * dt) % TAU;
    this.acousticPhase = (this.acousticPhase + TAU * ACOUSTIC_WOBBLE_HZ * dt) % TAU;
    const backgroundPulse = 1 + pulse.amplitude * Math.sin(this.pulsePhase);
    const acousticPulse = 1
      + this.smoothVoiceRms
        * (LATTICE_ACOUSTIC_BASE + LATTICE_ACOUSTIC_WOBBLE * Math.sin(this.acousticPhase));

    this.dt = dt;
    this.latticeScale = backgroundPulse * acousticPulse;
    this.latticeOpacity = pulse.opacity;
    return this;
  }

  private resizeRings(count: number): void {
    for (const values of [this.ringVelocities, this.ringTargets]) {
      values.length = count;
      for (let index = 0; index < count; index += 1) {
        if (!Number.isFinite(values[index])) values[index] = 0;
      }
    }
  }
}
