const LATITUDE_RINGS = 12;
const LONGITUDE_RINGS = 24;
const LATITUDE_SEGMENTS = 24;
const LONGITUDE_SEGMENTS = 16;
const DENSITY_THRESHOLDS = [0.22, 0.62] as const;
const TAU = Math.PI * 2;

export interface PulseLatticeData {
  /** Packed line-segment vertices, grouped into three cumulative density tiers. */
  readonly positions: Float32Array;
  /** THREE.BufferGeometry draw counts, measured in vertices. */
  readonly drawCounts: readonly [number, number, number];
}

/**
 * Build a bounded wire sphere once. Idle uses one third of the lines; activity
 * reveals evenly distributed additions without allocating geometry per frame.
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

/** Select a precomputed density band without introducing frame-time geometry work. */
export function pulseLatticeTier(load: number): 0 | 1 | 2 {
  const safeLoad = Number.isFinite(load) ? Math.min(1, Math.max(0, load)) : 0;
  if (safeLoad < DENSITY_THRESHOLDS[0]) return 0;
  if (safeLoad < DENSITY_THRESHOLDS[1]) return 1;
  return 2;
}
