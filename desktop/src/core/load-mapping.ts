import type { BackgroundStatus, JobEvent, PipelineStatus } from "../contracts";

const TOTAL_BOOST = 0.35;

export const PIPELINE_STAGE_IDS = [
  "detect",
  "authorize",
  "queue",
  "context",
  "model",
  "delay",
  "send",
  "confirm",
] as const;

export interface SourceLoads {
  reply: number;
  geeknews: number;
  dbSync: number;
  total: number;
  /** Normalized microphone activity; it boosts motion without owning a ring. */
  voice?: number;
}

export interface LatticePulse {
  frequency: number;
  amplitude: number;
  opacity: number;
  /** Normalized activity used to reveal prebuilt lattice segments. */
  density: number;
}

// Active reply phases map to bounded load: detect .10, authorize .15,
// queue .20, context .35, model .85, delay .05, send .60, confirm .25.
// The table lives at module scope: the poll path rebuilt it on every call
// before this (2026-09-22).
const PIPELINE_STAGE_WEIGHTS: Readonly<Record<string, number>> = {
  detect: 0.10,
  authorize: 0.15,
  queue: 0.20,
  context: 0.35,
  model: 0.85,
  delay: 0.05,
  send: 0.60,
  confirm: 0.25,
};

function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

// Branch instead of building `[reply, geeknews, dbSync]` per frame. The order
// and the "0 for anything past the third ring" fallback match the array the
// allocating path used.
function sourceAt(loads: SourceLoads, index: number): number {
  if (index === 0) return loads.reply;
  if (index === 1) return loads.geeknews;
  if (index === 2) return loads.dbSync;
  return 0;
}

export function pipelineLoad(status: PipelineStatus): number {
  if (!status.active) return 0;
  return clamp01(PIPELINE_STAGE_WEIGHTS[status.stage] ?? 0);
}

export function sourceLoads(background: BackgroundStatus, pipeline: PipelineStatus): SourceLoads {
  return {
    // Clamp each side before max: Math.max(NaN, valid) would otherwise erase
    // the valid signal when the final clamp converts NaN to zero.
    reply: Math.max(clamp01(background.replyLoad), pipelineLoad(pipeline)),
    geeknews: clamp01(background.geeknews.activity),
    dbSync: clamp01(background.dbSync.activity),
    total: clamp01(background.activity),
  };
}

export function totalFor(background: BackgroundStatus, jobs: readonly JobEvent[]): number {
  let jobLoad = 0;
  for (const job of jobs) jobLoad = Math.max(jobLoad, clamp01(job.load));
  return Math.max(clamp01(background.activity), jobLoad);
}

export function ringTargetVelocities(
  base: readonly number[],
  loads: SourceLoads,
  gain: readonly number[],
): number[] {
  return writeRingTargetVelocities(new Array<number>(base.length), base, loads, gain);
}

/**
 * Ring i follows v_i = base_i * (1 + gain_i * clamp01(source_i))
 * * (1 + 0.35 * max(clamp01(total), clamp01(voice))). The last factor is
 * exactly 1 when both global signals are zero.
 *
 * The render loop owns one scratch array and writes into it, so a 15-30fps
 * loop stops allocating a fresh array per frame (2026-09-22).
 */
export function writeRingTargetVelocities(
  out: number[],
  base: readonly number[],
  loads: SourceLoads,
  gain: readonly number[],
): number[] {
  const globalLoad = Math.max(clamp01(loads.total), clamp01(loads.voice ?? 0));
  const globalBoost = 1 + TOTAL_BOOST * globalLoad;
  for (let index = 0; index < out.length; index += 1) {
    const velocity = base[index];
    const safeBase = Number.isFinite(velocity) ? velocity : 0;
    const safeGain = Number.isFinite(gain[index]) ? gain[index] : 0;
    out[index] = safeBase * (1 + safeGain * clamp01(sourceAt(loads, index))) * globalBoost;
  }
  return out;
}

export function latticePulse(total: number, seconds: number): LatticePulse {
  return writeLatticePulse({ frequency: 0, amplitude: 0, opacity: 0, density: 0 }, total, seconds);
}

/** Write the lattice pulse into a reused object. F = F0*(1+0.8L), A = 0.05+0.05L, O = 0.06+0.12L. */
export function writeLatticePulse(out: LatticePulse, total: number, _seconds: number): LatticePulse {
  const load = clamp01(total);
  out.frequency = 1.0 * (1 + 0.8 * load);
  out.amplitude = 0.05 + 0.05 * load;
  out.opacity = 0.06 + 0.12 * load;
  out.density = load;
  return out;
}
