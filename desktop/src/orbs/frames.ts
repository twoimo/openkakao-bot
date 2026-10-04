import { MODE_FRAMES, resolvePreset, type OrbState, type OrbSize } from 'thinking-orbs/engine';

export type { OrbState };
export const ORB_FPS = 30;
const FRAME_COUNT = 96;
const banks = new Map<string, OrbFrames>();
const poses = new Map<string, PackedOrbFrame>();

/** Official geometry is evaluated at preparation time, never in a render tick. */
export interface PackedOrbFrame {
  /** x, y, radius, ink, opacity; coordinates are in canonical CSS pixels. */
  readonly dots: Float32Array;
  /** x1, y1, x2, y2, ink, opacity, width. */
  readonly lines: Float32Array;
}

function prepare(state: OrbState, size: OrbSize, seconds: number): PackedOrbFrame {
  const preset = resolvePreset(state, size);
  const frame = MODE_FRAMES[preset.mode](size, seconds * preset.speed, preset.opts);
  if (frame.dots.length > 1024 || frame.lines.length > 2048) throw new Error('orb_geometry_budget');
  const dots = new Float32Array(frame.dots.length * 5);
  const lines = new Float32Array(frame.lines.length * 7);
  frame.dots.forEach((dot, i) => dots.set([dot.x, dot.y, dot.r, 1 - dot.white, dot.a ?? 1], i * 5));
  frame.lines.forEach((line, i) => lines.set([line.x1, line.y1, line.x2, line.y2, 1 - line.white, line.a ?? 1, line.w], i * 7));
  return { dots, lines };
}

export function orbPose(state: OrbState, size: OrbSize): PackedOrbFrame {
  const key = `${state}:${size}`;
  let pose = poses.get(key);
  if (!pose) { pose = prepare(state, size, 0.6); poses.set(key, pose); }
  return pose;
}

export class OrbFrames {
  private readonly frames: PackedOrbFrame[];
  readonly bytes: number;
  constructor(state: OrbState, size: OrbSize) {
    this.frames = Array.from({ length: FRAME_COUNT }, (_, i) => prepare(state, size, 0.6 + i / ORB_FPS));
    this.bytes = this.frames.reduce((sum, frame) => sum + frame.dots.byteLength + frame.lines.byteLength, 0);
  }
  /** Reverse the sampled sequence at its endpoints to avoid a loop seam. */
  at(seconds: number): PackedOrbFrame {
    const last = FRAME_COUNT - 1;
    const period = 2 * last;
    const phase = Math.floor(Math.max(0, seconds) * ORB_FPS) % period;
    return this.frames[phase <= last ? phase : period - phase];
  }
}

export function orbFrames(state: OrbState, size: OrbSize): OrbFrames {
  const key = `${state}:${size}`;
  let bank = banks.get(key);
  if (!bank) { bank = new OrbFrames(state, size); banks.set(key, bank); }
  return bank;
}

export function orbCacheBytes(): number {
  let bytes = 0;
  for (const bank of banks.values()) bytes += bank.bytes;
  for (const pose of poses.values()) bytes += pose.dots.byteLength + pose.lines.byteLength;
  return bytes;
}
