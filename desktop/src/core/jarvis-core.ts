import * as THREE from "three";
import { AnimationLoop } from "./animation-loop";

const NEURON_COUNT = 96;
const SYNAPSES_PER_NEURON = 3;
const PARTICLE_COUNT = 30;

function damp(current: number, target: number, lambda: number, dt: number): number {
  return THREE.MathUtils.lerp(current, target, 1 - Math.exp(-lambda * dt));
}

function spherePoint(index: number, count: number, radius: number): THREE.Vector3 {
  const offset = 2 / count;
  const y = (index * offset - 1) + offset / 2;
  const r = Math.sqrt(Math.max(0, 1 - y * y));
  const phi = index * Math.PI * (3 - Math.sqrt(5));
  return new THREE.Vector3(Math.cos(phi) * r * radius, y * radius, Math.sin(phi) * r * radius);
}

export class JarvisCore {
  private readonly renderer: THREE.WebGLRenderer;
  private readonly scene = new THREE.Scene();
  private readonly camera = new THREE.PerspectiveCamera(34, 1, 0.1, 20);
  private readonly root = new THREE.Group();
  private readonly rings: THREE.Mesh[] = [];
  private readonly ringVelocity = [0, 0, 0];
  private readonly ringBaseVelocity = [0.17, -0.12, 0.09];
  private readonly neuronsMaterial: THREE.PointsMaterial;
  private readonly synapsesMaterial: THREE.LineBasicMaterial;
  private readonly particlesMaterial: THREE.PointsMaterial;
  private readonly lattice: THREE.LineSegments;
  private readonly nucleus: THREE.Mesh;
  private readonly loop: AnimationLoop;
  private targetLoad = 0;
  private smoothLoad = 0;
  private targetVoiceRms = 0;
  private smoothVoiceRms = 0;
  private nucleusScale = 1;
  private nucleusVelocity = 0;

  constructor(canvas: HTMLCanvasElement) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: "low-power" });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setSize(236, 236, false);
    this.renderer.setClearColor(0x000000, 0);
    this.camera.position.set(0, 0, 4.25);
    this.scene.add(this.root);

    const accent = new THREE.Color(getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#B88A45");
    const muted = new THREE.Color(getComputedStyle(document.documentElement).getPropertyValue("--muted").trim() || "#6D655B");

    const ringGeometry = new THREE.TorusGeometry(1.02, 0.012, 8, 96);
    for (let index = 0; index < 3; index += 1) {
      const material = new THREE.MeshBasicMaterial({ color: accent, transparent: true, opacity: 0.32 + index * 0.09 });
      const ring = new THREE.Mesh(ringGeometry, material);
      ring.rotation.set(index === 0 ? 0.18 : 0.9 + index * 0.24, index * 0.62, index * 0.35);
      ring.scale.setScalar(1 - index * 0.055);
      this.rings.push(ring);
      this.root.add(ring);
    }

    const neuronPositions = new Float32Array(NEURON_COUNT * 3);
    for (let i = 0; i < NEURON_COUNT; i += 1) {
      const point = spherePoint(i, NEURON_COUNT, 0.82 + (i % 7) * 0.015);
      neuronPositions.set([point.x, point.y, point.z], i * 3);
    }
    const neuronGeometry = new THREE.BufferGeometry();
    neuronGeometry.setAttribute("position", new THREE.BufferAttribute(neuronPositions, 3));
    this.neuronsMaterial = new THREE.PointsMaterial({ color: accent, size: 0.038, transparent: true, opacity: 0.78, sizeAttenuation: true });
    this.root.add(new THREE.Points(neuronGeometry, this.neuronsMaterial));

    const synapsePositions = new Float32Array(NEURON_COUNT * SYNAPSES_PER_NEURON * 2 * 3);
    const offsets = [13, 37, 59];
    let cursor = 0;
    for (let source = 0; source < NEURON_COUNT; source += 1) {
      const start = spherePoint(source, NEURON_COUNT, 0.83);
      for (const offset of offsets) {
        const end = spherePoint((source + offset) % NEURON_COUNT, NEURON_COUNT, 0.83);
        synapsePositions.set([start.x, start.y, start.z, end.x, end.y, end.z], cursor);
        cursor += 6;
      }
    }
    const synapseGeometry = new THREE.BufferGeometry();
    synapseGeometry.setAttribute("position", new THREE.BufferAttribute(synapsePositions, 3));
    this.synapsesMaterial = new THREE.LineBasicMaterial({ color: muted, transparent: true, opacity: 0.12 });
    this.root.add(new THREE.LineSegments(synapseGeometry, this.synapsesMaterial));

    const particlePositions = new Float32Array(PARTICLE_COUNT * 3);
    for (let i = 0; i < PARTICLE_COUNT; i += 1) {
      const point = spherePoint((i * 17) % PARTICLE_COUNT, PARTICLE_COUNT, 1.18 + (i % 5) * 0.045);
      particlePositions.set([point.x, point.y, point.z], i * 3);
    }
    const particleGeometry = new THREE.BufferGeometry();
    particleGeometry.setAttribute("position", new THREE.BufferAttribute(particlePositions, 3));
    this.particlesMaterial = new THREE.PointsMaterial({ color: accent, size: 0.02, transparent: true, opacity: 0.3 });
    this.root.add(new THREE.Points(particleGeometry, this.particlesMaterial));

    const latticeGeometry = new THREE.WireframeGeometry(new THREE.SphereGeometry(1.08, 18, 10));
    this.lattice = new THREE.LineSegments(latticeGeometry, new THREE.LineBasicMaterial({ color: accent, transparent: true, opacity: 0.09 }));
    this.root.add(this.lattice);

    this.nucleus = new THREE.Mesh(
      new THREE.SphereGeometry(0.27, 28, 18),
      new THREE.MeshStandardMaterial({ color: accent, roughness: 0.7, metalness: 0.08 }),
    );
    this.root.add(this.nucleus);
    this.scene.add(new THREE.AmbientLight(0xffffff, 1.35));
    const key = new THREE.DirectionalLight(0xffffff, 1.8);
    key.position.set(2, 3, 4);
    this.scene.add(key);

    this.loop = new AnimationLoop((dt, nowMs) => this.render(dt, nowMs));
  }

  start(): void {
    this.loop.start();
  }

  stop(): void {
    this.loop.stop();
  }

  setSignals(jobLoad: number, voiceRms: number): void {
    this.targetLoad = Math.min(1, Math.max(0, jobLoad));
    this.targetVoiceRms = Math.min(1, Math.max(0, voiceRms));
    this.loop.setLoad(this.targetLoad);
  }

  get renderCount(): number {
    return this.loop.renderCount;
  }

  dispose(): void {
    this.stop();
    this.scene.traverse((object) => {
      if (object instanceof THREE.Mesh || object instanceof THREE.Points || object instanceof THREE.LineSegments) {
        object.geometry.dispose();
        const materials = Array.isArray(object.material) ? object.material : [object.material];
        materials.forEach((material) => material.dispose());
      }
    });
    this.renderer.dispose();
  }

  private render(dt: number, nowMs: number): void {
    this.smoothLoad = damp(this.smoothLoad, this.targetLoad, 4.2, dt);
    this.smoothVoiceRms = damp(this.smoothVoiceRms, this.targetVoiceRms, 7.0, dt);

    this.rings.forEach((ring, index) => {
      const targetVelocity = this.ringBaseVelocity[index] * (1 + this.smoothLoad * (1.4 + index * 0.25));
      this.ringVelocity[index] = damp(this.ringVelocity[index], targetVelocity, 1.8 + index * 0.55, dt);
      ring.rotation.z += this.ringVelocity[index] * dt;
      ring.rotation.x += this.ringVelocity[index] * dt * (0.22 + index * 0.06);
    });

    const desiredScale = 1 + this.smoothLoad * 0.2 + this.smoothVoiceRms * 0.12;
    const acceleration = (desiredScale - this.nucleusScale) * 18 - this.nucleusVelocity * 7.5;
    this.nucleusVelocity += acceleration * dt;
    this.nucleusScale += this.nucleusVelocity * dt;
    this.nucleus.scale.setScalar(this.nucleusScale);

    const acousticPulse = 1 + this.smoothVoiceRms * (0.07 + 0.025 * Math.sin(nowMs * 0.012));
    this.lattice.scale.setScalar(acousticPulse);
    this.neuronsMaterial.size = 0.036 + this.smoothLoad * 0.012;
    this.synapsesMaterial.opacity = 0.09 + this.smoothLoad * 0.16;
    this.particlesMaterial.opacity = 0.2 + this.smoothLoad * 0.45;
    this.root.rotation.y += dt * (0.04 + this.smoothLoad * 0.11);
    this.renderer.render(this.scene, this.camera);
  }
}
