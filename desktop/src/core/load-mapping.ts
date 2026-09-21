import type { BackgroundStatus } from "../contracts";

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

export function sourceLoads(background: BackgroundStatus): SourceLoads {
  return {
    reply: clamp01(background.replyLoad),
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
