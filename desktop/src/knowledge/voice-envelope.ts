import * as THREE from "three";

const VERTICES = 128;
const TAU_SECONDS = 0.12;
const EPSILON = 0.0001;

/** A measured input/playback amplitude envelope, not an audio spectrum. */
export class VoiceEnvelope {
  readonly line: THREE.LineLoop<THREE.BufferGeometry, THREE.LineBasicMaterial>;
  private readonly positions = new Float32Array(VERTICES * 3);
  private readonly angles = new Float32Array(VERTICES * 3);
  private target = 0;
  private level = 0;

  constructor() {
    for (let i = 0; i < VERTICES; i++) {
      const angle = i * Math.PI * 2 / VERTICES;
      this.angles[i * 3] = Math.cos(angle);
      this.angles[i * 3 + 1] = Math.sin(angle);
      this.angles[i * 3 + 2] = Math.sin(angle * 6);
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(this.positions, 3).setUsage(THREE.DynamicDrawUsage));
    this.line = new THREE.LineLoop(geometry, new THREE.LineBasicMaterial({
      color: "#a2b9d0", transparent: true, opacity: 0, depthWrite: false,
    }));
    this.line.name = "voice-amplitude-envelope";
    this.line.rotation.x = 0.35;
    this.line.rotation.z = -0.2;
    this.line.position.z = -0.5;
    // Its changing, bounded radius never participates in graph hit tests.
    geometry.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 2.2);
    this.line.visible = false;
  }

  setRms(rms: number): boolean {
    const bounded = Number.isFinite(rms) ? Math.max(0, Math.min(1, rms)) : 0;
    if (bounded === this.target) return false;
    this.target = bounded;
    return true;
  }

  get targetRms(): number { return this.target; }
  get displayedRms(): number { return this.level; }
  get needsFrame(): boolean { return Math.abs(this.target - this.level) > EPSILON; }

  advance(dtSeconds: number): void {
    const dt = Number.isFinite(dtSeconds) ? Math.max(0, dtSeconds) : 0;
    this.level += (1 - Math.exp(-dt / TAU_SECONDS)) * (this.target - this.level);
    if (!this.needsFrame) this.level = this.target;
    const strength = Math.sqrt(this.level);
    this.line.visible = this.level > EPSILON;
    this.line.material.opacity = strength * 0.36;
    for (let i = 0; i < VERTICES; i++) {
      const j = i * 3;
      const radius = 1.95 + strength * 0.12 * this.angles[j + 2];
      this.positions[j] = this.angles[j] * radius;
      this.positions[j + 1] = this.angles[j + 1] * radius;
      this.positions[j + 2] = 0;
    }
    this.line.geometry.getAttribute("position").needsUpdate = true;
  }

  clear(): void {
    this.target = this.level = 0;
    this.line.visible = false;
    this.line.material.opacity = 0;
  }
}
