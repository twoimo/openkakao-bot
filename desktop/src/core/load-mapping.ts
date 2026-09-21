import type { BackgroundStatus, PipelineStatus } from "../contracts";

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
}

function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

export function pipelineLoad(status: PipelineStatus): number {
  if (!status.active) return 0;
  // Active reply phases map to bounded load: detect .10, authorize .15,
  // queue .20, context .35, model .85, delay .05, send .60, confirm .25.
  const weights: Record<(typeof PIPELINE_STAGE_IDS)[number], number> = {
    detect: 0.10,
    authorize: 0.15,
    queue: 0.20,
    context: 0.35,
    model: 0.85,
    delay: 0.05,
    send: 0.60,
    confirm: 0.25,
  };
  return clamp01(weights[status.stage as (typeof PIPELINE_STAGE_IDS)[number]] ?? 0);
}

export function sourceLoads(background: BackgroundStatus, pipeline: PipelineStatus): SourceLoads {
  return {
    reply: clamp01(Math.max(background.replyLoad, pipelineLoad(pipeline))),
    geeknews: clamp01(background.geeknews.activity),
    dbSync: clamp01(background.dbSync.activity),
    total: clamp01(background.activity),
  };
}

export function ringTargetVelocities(
  base: readonly number[],
  loads: SourceLoads,
  gain: readonly number[],
): number[] {
  const perSource = [loads.reply, loads.geeknews, loads.dbSync] as const;
  // Ring i follows v_i = base_i * (1 + gain_i * clamp01(source_i)).
  return base.map((velocity, index) => {
    const safeBase = Number.isFinite(velocity) ? velocity : 0;
    const safeGain = Number.isFinite(gain[index]) ? gain[index] : 0;
    const load = clamp01(perSource[index] ?? 0);
    return safeBase * (1 + safeGain * load);
  });
}

export function latticePulse(total: number, _seconds: number): { frequency: number; amplitude: number; opacity: number } {
  const load = clamp01(total);
  const baseFrequency = 1.0;
  // F = F0 * (1 + 0.8L), A = 0.05 + 0.05L, O = 0.06 + 0.12L.
  return {
    frequency: baseFrequency * (1 + 0.8 * load),
    amplitude: 0.05 + 0.05 * load,
    opacity: 0.06 + 0.12 * load,
  };
}
