import { describe, expect, it } from 'vitest';
import { overviewCameraDistance } from '../knowledge/camera-fit';

describe('graph overview camera', () => {
  it('keeps every enclosing corner inside a narrow viewport and its label margin', () => {
    const points = [{ x: -1.84, y: 0.78, z: 0.44 }, { x: 1.84, y: -0.78, z: 0.44 }];
    const width = 439, height = 470;
    const distance = overviewCameraDistance(points, width, height);
    const tanY = Math.tan(38 * Math.PI / 360), tanX = tanY * width / height;
    for (const point of points) for (const x of [-0.32, 0.32]) for (const y of [-0.32, 0.32]) for (const z of [-0.32, 0.32]) {
      const depth = distance - point.z - z;
      expect(Math.abs((point.x + x) / (depth * tanX))).toBeLessThanOrEqual(1 - 96 / width + 1e-12);
      expect(Math.abs((point.y + y) / (depth * tanY))).toBeLessThanOrEqual(1 - 40 / height + 1e-12);
    }
    expect(distance).toBeGreaterThan(5.2);
    expect(distance).toBeLessThan(12);
  });
  it('preserves the normal camera distance when the graph already fits', () => {
    expect(overviewCameraDistance([{ x: 0, y: 0, z: 0 }], 1200, 632)).toBe(3.6);
    expect(overviewCameraDistance([], 640, 470)).toBe(3.6);
  });
});
