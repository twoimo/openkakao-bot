import * as THREE from 'three';
import { orbFrames, orbPose, type OrbFrames, type PackedOrbFrame, type OrbState } from './frames';
import type { OrbActivity } from './activity';

const vertex = `
attribute vec3 ink;
uniform float diameter;
uniform float viewportHeight;
varying vec2 shade;
void main() {
  vec4 center = modelViewMatrix * vec4(0.0, 0.0, 0.0, 1.0);
  center.xy += position.xy * diameter;
  gl_Position = projectionMatrix * center;
  gl_PointSize = max(1.0, ink.x * diameter * viewportHeight * projectionMatrix[1][1] / max(0.1, -center.z));
  shade = ink.yz;
}`;
const fragment = `
uniform vec3 tint;
varying vec2 shade;
void main() {
  float radius = length(gl_PointCoord * 2.0 - 1.0);
  if (radius > 1.0) discard;
  gl_FragColor = vec4(tint * shade.x, shade.y * (1.0 - smoothstep(0.78, 1.0, radius)));
  #include <colorspace_fragment>
}`;

/** An upstream projected orb anchored to an actual knowledge node. No picking IDs. */
export class ThinkingOrbMesh extends THREE.Group {
  private readonly pointsGeometry = new THREE.BufferGeometry();
  private readonly lineGeometry = new THREE.BufferGeometry();
  private readonly positions = new Float32Array(1024 * 3);
  private readonly inks = new Float32Array(1024 * 3);
  private readonly linePositions = new Float32Array(2048 * 6);
  private readonly lineColors = new Float32Array(2048 * 8);
  private readonly pointsMaterial: THREE.ShaderMaterial;
  private readonly linesMaterial: THREE.ShaderMaterial;
  private state: OrbState;
  private bank: OrbFrames | null = null;
  private elapsed = 0;
  private frame: PackedOrbFrame | null = null;
  active = false;

  constructor(state: OrbState, diameter: number, tint: THREE.Color) {
    super();
    this.state = state;
    this.pointsGeometry.setAttribute('position', new THREE.BufferAttribute(this.positions, 3).setUsage(THREE.DynamicDrawUsage));
    this.pointsGeometry.setAttribute('ink', new THREE.BufferAttribute(this.inks, 3).setUsage(THREE.DynamicDrawUsage));
    this.lineGeometry.setAttribute('position', new THREE.BufferAttribute(this.linePositions, 3).setUsage(THREE.DynamicDrawUsage));
    this.lineGeometry.setAttribute('ink', new THREE.BufferAttribute(this.lineColors, 4).setUsage(THREE.DynamicDrawUsage));
    this.pointsMaterial = new THREE.ShaderMaterial({
      uniforms: { diameter: { value: diameter }, viewportHeight: { value: 1 }, tint: { value: tint } },
      vertexShader: vertex, fragmentShader: fragment, transparent: true, depthWrite: false, depthTest: false,
    });
    this.linesMaterial = new THREE.ShaderMaterial({
      uniforms: { diameter: { value: diameter }, tint: { value: tint } },
      vertexShader: `attribute vec4 ink; uniform float diameter; varying vec4 shade;
        void main() { vec4 center = modelViewMatrix * vec4(0.0,0.0,0.0,1.0); center.xy += position.xy * diameter; gl_Position = projectionMatrix * center; shade = ink; }`,
      fragmentShader: `uniform vec3 tint; varying vec4 shade;
        void main() {
          gl_FragColor = vec4(tint * shade.rgb, shade.a);
          #include <colorspace_fragment>
        }`,
      transparent: true, depthWrite: false, depthTest: false,
    });
    const points = new THREE.Points(this.pointsGeometry, this.pointsMaterial);
    const lines = new THREE.LineSegments(this.lineGeometry, this.linesMaterial);
    points.frustumCulled = false; lines.frustumCulled = false;
    this.add(lines, points);
    this.write(orbPose(state, 64));
  }

  setViewportHeight(height: number): void { this.pointsMaterial.uniforms.viewportHeight.value = height; }

  setActivity(activity: OrbActivity, reduced: boolean): boolean {
    const active = activity.active && !reduced;
    if (this.state === activity.state && this.active === active) return false;
    this.state = activity.state; this.active = active; this.elapsed = 0;
    this.bank = active ? orbFrames(this.state, 64) : null;
    this.write(orbPose(this.state, 64));
    return true;
  }

  advance(dt: number): void {
    if (!this.active || !this.bank) return;
    this.elapsed += Math.max(0, Math.min(0.25, dt));
    this.write(this.bank.at(this.elapsed));
  }

  private write(frame: PackedOrbFrame): void {
    if (frame === this.frame) return;
    this.frame = frame;
    const dots = frame.dots, lines = frame.lines;
    for (let i = 0, j = 0; i < dots.length; i += 5, j += 3) {
      this.positions[j] = dots[i] / 64 - 0.5;
      this.positions[j + 1] = 0.5 - dots[i + 1] / 64;
      this.inks[j] = dots[i + 2] / 64;
      this.inks[j + 1] = dots[i + 3]; this.inks[j + 2] = dots[i + 4];
    }
    for (let i = 0, p = 0, c = 0; i < lines.length; i += 7, p += 6, c += 8) {
      this.linePositions[p] = lines[i] / 64 - 0.5; this.linePositions[p + 1] = 0.5 - lines[i + 1] / 64;
      this.linePositions[p + 3] = lines[i + 2] / 64 - 0.5; this.linePositions[p + 4] = 0.5 - lines[i + 3] / 64;
      for (let v = 0; v < 2; v++) {
        for (let k = 0; k < 3; k++) this.lineColors[c + v * 4 + k] = lines[i + 4];
        this.lineColors[c + v * 4 + 3] = lines[i + 5];
      }
    }
    this.pointsGeometry.setDrawRange(0, dots.length / 5);
    this.lineGeometry.setDrawRange(0, lines.length / 7 * 2);
    this.pointsGeometry.attributes.position.needsUpdate = true; this.pointsGeometry.attributes.ink.needsUpdate = true;
    this.lineGeometry.attributes.position.needsUpdate = true; this.lineGeometry.attributes.ink.needsUpdate = true;
  }
}
