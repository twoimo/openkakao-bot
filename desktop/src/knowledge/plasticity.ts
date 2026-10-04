import { ON_SCREEN_NODE_CAP, type KnowledgeEdge, type KnowledgeView } from './graph-model';

export const SYNAPSE_CAP = 144;
export interface Point3 { x: number; y: number; z: number }
export interface Synapse { key: string; source: string; target: string; strength: number }

/** Visual bundles only: source triples and their evidence remain unchanged. */
export function selectSynapses(edges: readonly KnowledgeEdge[], activeIds?: ReadonlySet<string>, nowMs = Date.now()): Synapse[] {
  const pairs = new Map<string, Synapse>();
  for (const edge of edges) {
    if (edge.source === edge.target || edge.evidence.retracted) continue;
    if (activeIds && (!activeIds.has(edge.source) || !activeIds.has(edge.target))) continue;
    const until = Date.parse(edge.validTo);
    if (Number.isFinite(until) && until <= nowMs) continue;
    const source = edge.source < edge.target ? edge.source : edge.target;
    const target = edge.source < edge.target ? edge.target : edge.source;
    const key = JSON.stringify([source, target]);
    const weight = Number.isFinite(edge.weight) ? Math.max(0, edge.weight) : 0;
    const strength = weight / (1 + weight);
    const old = pairs.get(key);
    if (!old || strength > old.strength) pairs.set(key, { key, source, target, strength });
  }
  return [...pairs.values()].sort((a, b) => b.strength - a.strength || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0)).slice(0, SYNAPSE_CAP);
}

/** Bounded damped mechanics in display units; never trains or rewrites memory. */
export class PlasticityLayout {
  readonly coordinates = new Float64Array(ON_SCREEN_NODE_CAP * 3);
  private readonly velocity = new Float64Array(ON_SCREEN_NODE_CAP * 3);
  private readonly forces = new Float64Array(ON_SCREEN_NODE_CAP * 3);
  private readonly anchors = new Float64Array(ON_SCREEN_NODE_CAP * 3);
  private readonly inverseMass = new Float64Array(ON_SCREEN_NODE_CAP);
  private readonly degree = new Uint16Array(ON_SCREEN_NODE_CAP);
  private readonly edgeA = new Uint8Array(SYNAPSE_CAP);
  private readonly edgeB = new Uint8Array(SYNAPSE_CAP);
  private readonly edgeStrength = new Float64Array(SYNAPSE_CAP);
  private readonly restLength = new Float64Array(SYNAPSE_CAP);
  private ids: string[] = [];
  private edgeCount = 0;
  private targetSignature = '';
  private quietSteps = 0;
  moving = false;
  private maxSpeed = 0;
  private maxForce = 0;
  private simulatedSeconds = 0;

  setGraph(view: KnowledgeView, anchors: ReadonlyMap<string, Point3>, initial: ReadonlyMap<string, Point3>): void {
    const nodes = view.nodes.filter(node => !node.evidence.retracted).slice(0, ON_SCREEN_NODE_CAP);
    const targets = nodes.map(node => boundedPoint(anchors.get(node.id)));
    const synapses = selectSynapses(view.edges, new Set(nodes.map(node => node.id)));
    const signature = JSON.stringify([nodes.map((node, i) => [node.id, node.importance, node.category === 'collection' && node.label === '카카오톡', targets[i]]), synapses]);
    if (signature === this.targetSignature) return;
    this.targetSignature = signature;
    const old = new Map(this.ids.map((id, i) => [id, {
      x: this.coordinates[i * 3], y: this.coordinates[i * 3 + 1], z: this.coordinates[i * 3 + 2],
      vx: this.velocity[i * 3], vy: this.velocity[i * 3 + 1], vz: this.velocity[i * 3 + 2],
    }]));
    this.ids = nodes.map(node => node.id);
    const index = new Map(this.ids.map((id, i) => [id, i]));
    this.degree.fill(0);
    for (let i = 0; i < nodes.length; i++) {
      const node = nodes[i], anchor = targets[i];
      const previous = old.get(node.id), start = boundedPoint(previous ?? initial.get(node.id), anchor);
      this.anchors[i * 3] = anchor.x; this.anchors[i * 3 + 1] = anchor.y; this.anchors[i * 3 + 2] = anchor.z;
      this.coordinates[i * 3] = start.x; this.coordinates[i * 3 + 1] = start.y; this.coordinates[i * 3 + 2] = start.z;
      this.velocity[i * 3] = previous?.vx ?? 0; this.velocity[i * 3 + 1] = previous?.vy ?? 0; this.velocity[i * 3 + 2] = previous?.vz ?? 0;
      const importance = Number.isFinite(node.importance) ? Math.min(100, Math.max(0, node.importance)) : 0;
      this.inverseMass[i] = node.category === 'collection' && node.label === '카카오톡' ? 0 : 1 / (1 + importance / 100);
    }
    this.edgeCount = 0;
    for (const edge of synapses) {
      const a = index.get(edge.source), b = index.get(edge.target);
      if (a === undefined || b === undefined) continue;
      const i = this.edgeCount++;
      this.edgeA[i] = a; this.edgeB[i] = b; this.edgeStrength[i] = edge.strength;
      this.degree[a]++; this.degree[b]++;
      const dx = this.anchors[a * 3] - this.anchors[b * 3], dy = this.anchors[a * 3 + 1] - this.anchors[b * 3 + 1], dz = this.anchors[a * 3 + 2] - this.anchors[b * 3 + 2];
      this.restLength[i] = Math.max(0.24, Math.hypot(dx, dy, dz)) * (0.90 - 0.22 * edge.strength);
    }
    this.quietSteps = 0; this.maxSpeed = this.maxForce = this.simulatedSeconds = 0; this.moving = this.ids.length > 0;
  }

  forEachPoint(apply: (id: string, x: number, y: number, z: number) => void): void {
    for (let i = 0; i < this.ids.length; i++) apply(this.ids[i], this.coordinates[i * 3], this.coordinates[i * 3 + 1], this.coordinates[i * 3 + 2]);
  }

  advance(dt: number): boolean {
    if (!this.moving || !Number.isFinite(dt) || dt <= 0) return false;
    const elapsed = Math.min(.25, dt), steps = Math.min(8, Math.max(1, Math.ceil(elapsed * 120 - 1e-12)));
    for (let i = 0; i < steps && this.moving; i++) { this.step(elapsed / steps); this.simulatedSeconds += elapsed / steps; }
    return true;
  }

  settle(): void {
    for (let i = 0; i < 960 && this.moving; i++) this.step(1 / 120);
    this.velocity.fill(0); this.moving = false;
  }

  diagnostics(): { nodes: number; synapses: number; moving: boolean; maxSpeed: number; maxForce: number; simulatedSeconds: number; bufferBytes: number } {
    return { nodes: this.ids.length, synapses: this.edgeCount, moving: this.moving, maxSpeed: this.maxSpeed, maxForce: this.maxForce,
      simulatedSeconds: this.simulatedSeconds, bufferBytes: this.coordinates.byteLength + this.velocity.byteLength + this.forces.byteLength + this.anchors.byteLength + this.inverseMass.byteLength + this.degree.byteLength + this.edgeA.byteLength + this.edgeB.byteLength + this.edgeStrength.byteLength + this.restLength.byteLength };
  }

  private step(h: number): void {
    const count = this.ids.length, positions = this.coordinates;
    this.forces.fill(0);
    for (let i = 0; i < count * 3; i++) this.forces[i] = 18 * (this.anchors[i] - positions[i]);
    for (let i = 0; i < this.edgeCount; i++) {
      const a = this.edgeA[i], b = this.edgeB[i], ai = a * 3, bi = b * 3;
      const dx = positions[bi] - positions[ai], dy = positions[bi + 1] - positions[ai + 1], dz = positions[bi + 2] - positions[ai + 2];
      const length = Math.max(0.001, Math.hypot(dx, dy, dz));
      const stiffness = 14 * (0.25 + 0.75 * this.edgeStrength[i]) / Math.sqrt(Math.max(1, this.degree[a] * this.degree[b]));
      const gain = stiffness * (length - this.restLength[i]) / length;
      this.forces[ai] += gain * dx; this.forces[ai + 1] += gain * dy; this.forces[ai + 2] += gain * dz;
      this.forces[bi] -= gain * dx; this.forces[bi + 1] -= gain * dy; this.forces[bi + 2] -= gain * dz;
    }
    for (let a = 0; a < count; a++) for (let b = a + 1; b < count; b++) {
      const ai = a * 3, bi = b * 3;
      let dx = positions[bi] - positions[ai], dy = positions[bi + 1] - positions[ai + 1], dz = positions[bi + 2] - positions[ai + 2];
      if (dx * dx + dy * dy + dz * dz < 1e-10) { dx = ((a + b) % 2 ? 1 : -1) * 0.001; dy = 0.001; }
      const squared = dx * dx + dy * dy + dz * dz + 0.0324;
      const distance = Math.max(0.001, Math.hypot(dx, dy, dz));
      const gain = 0.045 / (squared * Math.sqrt(squared)) + 36 * Math.max(0, 0.18 - distance) / distance;
      this.forces[ai] -= gain * dx; this.forces[ai + 1] -= gain * dy; this.forces[ai + 2] -= gain * dz;
      this.forces[bi] += gain * dx; this.forces[bi + 1] += gain * dy; this.forces[bi + 2] += gain * dz;
    }
    this.maxSpeed = 0; this.maxForce = 0;
    const damping = Math.exp(-11 * h);
    for (let i = 0; i < count; i++) {
      const at = i * 3;
      if (this.inverseMass[i] === 0) {
        for (let axis = 0; axis < 3; axis++) { positions[at + axis] = this.anchors[at + axis]; this.velocity[at + axis] = 0; }
        continue;
      }
      let fx = this.forces[at], fy = this.forces[at + 1], fz = this.forces[at + 2];
      const force = Math.hypot(fx, fy, fz);
      const scale = force > 24 ? 24 / force : 1;
      for (let axis = 0; axis < 3; axis++) this.velocity[at + axis] = damping * (this.velocity[at + axis] + h * this.forces[at + axis] * scale * this.inverseMass[i]);
      const speed = Math.hypot(this.velocity[at], this.velocity[at + 1], this.velocity[at + 2]);
      const speedScale = speed > 1.4 ? 1.4 / speed : 1;
      for (let axis = 0; axis < 3; axis++) { this.velocity[at + axis] *= speedScale; positions[at + axis] += h * this.velocity[at + axis]; }
      const envelope = Math.sqrt((positions[at] / 2.3) ** 2 + (positions[at + 1] / 1.55) ** 2 + (positions[at + 2] / 1.1) ** 2);
      if (envelope > 1) for (let axis = 0; axis < 3; axis++) positions[at + axis] /= envelope;
      if (envelope >= 1 - 1e-9) {
        let nx = positions[at] / 5.29, ny = positions[at + 1] / 2.4025, nz = positions[at + 2] / 1.21;
        const length = Math.hypot(nx, ny, nz); nx /= length; ny /= length; nz /= length;
        const normalForce = Math.max(0, fx * nx + fy * ny + fz * nz);
        fx -= normalForce * nx; fy -= normalForce * ny; fz -= normalForce * nz;
        const normalVelocity = Math.max(0, this.velocity[at] * nx + this.velocity[at + 1] * ny + this.velocity[at + 2] * nz);
        this.velocity[at] -= normalVelocity * nx; this.velocity[at + 1] -= normalVelocity * ny; this.velocity[at + 2] -= normalVelocity * nz;
      }
      this.maxSpeed = Math.max(this.maxSpeed, Math.hypot(this.velocity[at], this.velocity[at + 1], this.velocity[at + 2])); this.maxForce = Math.max(this.maxForce, Math.hypot(fx, fy, fz));
    }
    if (this.maxSpeed < 0.0015 && this.maxForce < 0.025) this.quietSteps++; else this.quietSteps = 0;
    if (this.quietSteps >= 12) { this.velocity.fill(0); this.moving = false; }
  }
}

function boundedPoint(point?: Point3, fallback: Point3 = { x: 0, y: 0, z: 0 }): Point3 {
  if (!point || ![point.x, point.y, point.z].every(Number.isFinite)) return { ...fallback };
  const scale = Math.max(1, Math.hypot(point.x / 2.3, point.y / 1.55, point.z / 1.1));
  return { x: point.x / scale, y: point.y / scale, z: point.z / scale };
}
