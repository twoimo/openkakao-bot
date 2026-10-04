import * as THREE from "three";

/** Decorative, static sky. These points never enter graph data or hit tests. */
export function createCosmosBackdrop(): THREE.Group {
  const sky = new THREE.Group();
  sky.name = "cosmos-backdrop";
  const count = 1536;
  const positions = new Float32Array(count * 3);
  const colors = new Float32Array(count * 3);
  const cold = new THREE.Color("#cad8e9");
  const warm = new THREE.Color("#e6cfac");
  let seed = 0x41d3c0de;
  const random = () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return seed / 0x100000000;
  };
  for (let i = 0; i < count; i++) {
    const z = random() * 2 - 1;
    const angle = random() * Math.PI * 2;
    const radius = 15 + random() * 8;
    const ring = Math.sqrt(1 - z * z);
    positions.set([Math.cos(angle) * ring * radius, z * radius, Math.sin(angle) * ring * radius], i * 3);
    const color = i % 13 === 0 ? warm : cold;
    const brightness = 0.55 + random() * 0.45;
    colors.set([color.r * brightness, color.g * brightness, color.b * brightness], i * 3);
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  sky.add(new THREE.Points(geometry, new THREE.PointsMaterial({
    vertexColors: true, size: 0.035, sizeAttenuation: true,
    transparent: true, opacity: 0.65, depthWrite: false,
  })));
  for (const [radius, tilt, opacity] of [[1.85, 0.38, 0.11], [2.15, -0.35, 0.055]]) {
    const points: THREE.Vector3[] = [];
    for (let i = 0; i < 128; i++) {
      const angle = i * Math.PI * 2 / 128;
      points.push(new THREE.Vector3(Math.cos(angle) * radius, Math.sin(angle) * radius * 0.52, 0));
    }
    const orbit = new THREE.LineLoop(
      new THREE.BufferGeometry().setFromPoints(points),
      new THREE.LineBasicMaterial({ color: "#6883a3", transparent: true, opacity, depthWrite: false }),
    );
    orbit.rotation.x = tilt;
    orbit.rotation.z = -0.2;
    orbit.position.z = -0.45;
    sky.add(orbit);
  }
  return sky;
}
