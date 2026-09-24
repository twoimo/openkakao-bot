const LATITUDE_RINGS = 12;
const LONGITUDE_RINGS = 24;
const LATITUDE_SEGMENTS = 24;
const LONGITUDE_SEGMENTS = 16;
const TAU = Math.PI * 2;

export interface PulseLatticeData {
  /** Packed line-segment vertices in the original three cumulative groups. */
  readonly positions: Float32Array;
  /** Cumulative vertex-count anchors; the first and last bound the draw range. */
  readonly drawCounts: readonly [number, number, number];
}

/**
 * Build a bounded wire sphere once. Idle uses one third of the lines; activity
 * reveals more prebuilt segments without allocating geometry per frame.
 */
export function buildPulseLattice(radius: number): PulseLatticeData {
  const segmentCount = LATITUDE_RINGS * LATITUDE_SEGMENTS
    + LONGITUDE_RINGS * LONGITUDE_SEGMENTS;
  const positions = new Float32Array(segmentCount * 2 * 3);
  const drawCounts: [number, number, number] = [0, 0, 0];
  let cursor = 0;

  for (let tier = 0; tier < 3; tier += 1) {
    for (let ring = 0; ring < LATITUDE_RINGS; ring += 1) {
      if (ring % 3 !== tier) continue;
      const latitude = ((ring + 1) / (LATITUDE_RINGS + 1) - 0.5) * Math.PI;
      const y = Math.sin(latitude) * radius;
      const circleRadius = Math.cos(latitude) * radius;
      for (let segment = 0; segment < LATITUDE_SEGMENTS; segment += 1) {
        const a = segment * TAU / LATITUDE_SEGMENTS;
        const b = (segment + 1) * TAU / LATITUDE_SEGMENTS;
        positions[cursor++] = Math.sin(a) * circleRadius;
        positions[cursor++] = y;
        positions[cursor++] = Math.cos(a) * circleRadius;
        positions[cursor++] = Math.sin(b) * circleRadius;
        positions[cursor++] = y;
        positions[cursor++] = Math.cos(b) * circleRadius;
      }
    }

    for (let ring = 0; ring < LONGITUDE_RINGS; ring += 1) {
      if (ring % 3 !== tier) continue;
      const longitude = ring * TAU / LONGITUDE_RINGS;
      for (let segment = 0; segment < LONGITUDE_SEGMENTS; segment += 1) {
        const a = -Math.PI / 2 + segment * Math.PI / LONGITUDE_SEGMENTS;
        const b = -Math.PI / 2 + (segment + 1) * Math.PI / LONGITUDE_SEGMENTS;
        positions[cursor++] = Math.cos(a) * Math.sin(longitude) * radius;
        positions[cursor++] = Math.sin(a) * radius;
        positions[cursor++] = Math.cos(a) * Math.cos(longitude) * radius;
        positions[cursor++] = Math.cos(b) * Math.sin(longitude) * radius;
        positions[cursor++] = Math.sin(b) * radius;
        positions[cursor++] = Math.cos(b) * Math.cos(longitude) * radius;
      }
    }
    drawCounts[tier] = cursor / 3;
  }

  return { positions, drawCounts };
}

/**
 * Reveal complete segments in proportion to the already-smoothed load:
 * count = idle + 2 * round((full - idle) * clamp01(load) / 2).
 * The existing geometry spans 448..1344 vertices (224..672 segments).
 * Boundaries add/remove one segment instead of 224; rounding error is at most
 * half a segment relative to the ideal linear density.
 * Only the draw range changes; no geometry or frame-time containers are built.
 */
export function pulseLatticeDrawCount(load: number, drawCounts: PulseLatticeData["drawCounts"]): number {
  const safeLoad = Number.isFinite(load) ? Math.min(1, Math.max(0, load)) : 0;
  const idle = drawCounts[0];
  return idle + 2 * Math.round((drawCounts[2] - idle) * safeLoad / 2);
}
