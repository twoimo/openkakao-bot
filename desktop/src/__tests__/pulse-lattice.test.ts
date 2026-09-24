import { describe, expect, it } from "vitest";
import { buildPulseLattice, pulseLatticeDrawCount } from "../core/pulse-lattice";

describe("progressive pulse-lattice density", () => {
  const lattice = buildPulseLattice(1.08);

  it("preserves the prebuilt sphere and the idle/full geometry bounds", () => {
    expect(lattice.drawCounts).toEqual([448, 896, 1344]);
    expect(lattice.positions.length).toBe(1344 * 3);
    for (let vertex = 0; vertex < lattice.positions.length; vertex += 3) {
      expect(Math.hypot(...lattice.positions.subarray(vertex, vertex + 3))).toBeCloseTo(1.08, 6);
    }
  });

  it.each([
    [0, 448], [0.25, 672], [0.5, 896], [0.75, 1120], [1, 1344],
  ])("maps load %f to %i vertices", (load, expected) => {
    expect(pulseLatticeDrawCount(load, lattice.drawCounts)).toBe(expected);
  });

  it("is monotonic and resolves every complete segment without exceeding the buffer", () => {
    let previous = lattice.drawCounts[0];
    const counts = new Set<number>();
    for (let step = 0; step <= 4096; step += 1) {
      const count = pulseLatticeDrawCount(step / 4096, lattice.drawCounts);
      expect(Number.isInteger(count)).toBe(true);
      expect(count % 2).toBe(0);
      expect(count).toBeGreaterThanOrEqual(previous);
      expect(count - previous).toBeLessThanOrEqual(2);
      expect(count).toBeLessThanOrEqual(lattice.positions.length / 3);
      previous = count;
      counts.add(count);
    }
    expect(counts.size).toBe(449);
  });

  it.each([0.22, 0.62])("does not jump a tier around the former %f threshold", (threshold) => {
    const below = pulseLatticeDrawCount(threshold - 0.000001, lattice.drawCounts);
    const above = pulseLatticeDrawCount(threshold + 0.000001, lattice.drawCounts);
    expect(above - below).toBeGreaterThanOrEqual(0);
    expect(above - below).toBeLessThanOrEqual(2);
  });

  it("keeps non-finite loads at idle and clamps finite loads to the original bounds", () => {
    for (const load of [Number.NaN, Number.NEGATIVE_INFINITY, Number.POSITIVE_INFINITY, -2]) {
      expect(pulseLatticeDrawCount(load, lattice.drawCounts)).toBe(448);
    }
    expect(pulseLatticeDrawCount(2, lattice.drawCounts)).toBe(1344);
  });

  it("does not mutate or replace the prebuilt positions or count anchors", () => {
    const positions = lattice.positions;
    const savedPositions = positions.slice();
    const anchors = lattice.drawCounts;
    for (const load of [0, 0.21, 0.23, 0.61, 0.63, 1, 0]) {
      pulseLatticeDrawCount(load, anchors);
    }
    expect(lattice.positions).toBe(positions);
    expect(positions).toEqual(savedPositions);
    expect(lattice.drawCounts).toBe(anchors);
    expect(anchors).toEqual([448, 896, 1344]);
  });
});
