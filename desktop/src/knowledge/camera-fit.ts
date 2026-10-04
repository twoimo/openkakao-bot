/** Fit a front-facing perspective camera around the nodes' enclosing boxes. */
export function overviewCameraDistance(
  points: ReadonlyArray<{ x: number; y: number; z: number }>,
  width: number,
  height: number,
  verticalFov = 38,
): number {
  const tanY = Math.tan(verticalFov * Math.PI / 360);
  const tanX = tanY * Math.max(1, width) / Math.max(1, height);
  const safeX = tanX * Math.max(0.3, 1 - 96 / Math.max(1, width));
  const safeY = tanY * Math.max(0.3, 1 - 40 / Math.max(1, height));
  const radius = 0.32; // Largest overview orb; also leaves room for small labels.
  let distance = 3.6;
  for (const point of points) {
    distance = Math.max(distance, point.z + radius + Math.max(
      (Math.abs(point.x) + radius) / safeX,
      (Math.abs(point.y) + radius) / safeY,
    ));
  }
  return distance;
}
