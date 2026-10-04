import * as THREE from 'three';
import { SYNAPSE_CAP, type Point3, type Synapse } from './plasticity';

interface Bridge extends Synapse { goal: number; growth: number; active: boolean; color: THREE.Color; ax: number; ay: number; az: number; bx: number; by: number; bz: number }
const CAPACITY = SYNAPSE_CAP * 2;

/** One persistent GPU ribbon batch; withdrawn bridges retract without becoming pick targets. */
export class SynapticBridges {
  readonly mesh: THREE.Mesh;
  private readonly geometry = new THREE.InstancedBufferGeometry();
  private readonly starts = new Float32Array(CAPACITY * 3);
  private readonly ends = new Float32Array(CAPACITY * 3);
  private readonly strengths = new Float32Array(CAPACITY);
  private readonly growths = new Float32Array(CAPACITY);
  private readonly opacities = new Float32Array(CAPACITY);
  private readonly colors = new Float32Array(CAPACITY * 3);
  private readonly attributes: THREE.InstancedBufferAttribute[];
  private readonly records: Bridge[] = [];
  private readonly index = new Map<string, Bridge>();
  private readonly neutral = new THREE.Color('#90a9bc');
  private readonly selected = new THREE.Color('#dacda8');
  moving = false;
  private dirty = true;
  private disposed = false;

  constructor() {
    const positions: number[] = [], indices: number[] = [];
    for (let i = 0; i <= 16; i++) positions.push(-1, i / 16, 0, 1, i / 16, 0);
    for (let i = 0; i < 16; i++) { const at = i * 2; indices.push(at, at + 1, at + 2, at + 1, at + 3, at + 2); }
    this.geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    this.geometry.setIndex(indices);
    this.attributes = [new THREE.InstancedBufferAttribute(this.starts, 3), new THREE.InstancedBufferAttribute(this.ends, 3),
      new THREE.InstancedBufferAttribute(this.strengths, 1), new THREE.InstancedBufferAttribute(this.growths, 1),
      new THREE.InstancedBufferAttribute(this.opacities, 1), new THREE.InstancedBufferAttribute(this.colors, 3)];
    ['aStart', 'aEnd', 'aStrength', 'aGrowth', 'aOpacity', 'aColor'].forEach((name, i) => {
      this.attributes[i].setUsage(THREE.DynamicDrawUsage); this.geometry.setAttribute(name, this.attributes[i]);
    });
    this.geometry.instanceCount = 0;
    this.mesh = new THREE.Mesh(this.geometry, new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, side: THREE.DoubleSide, forceSinglePass:true,
      uniforms:{uVisibleTime:{value:0},uAmbientFlow:{value:0}},
      vertexShader: `
        attribute vec3 aStart, aEnd, aColor;
        attribute float aStrength, aGrowth, aOpacity;
        varying float vAcross, vAlong, vGrowth, vOpacity, vPhase;
        varying vec3 vColor;
        void main() {
          float t = position.y;
          vec3 delta = aEnd - aStart;
          float distance = max(.0001, length(delta));
          vec3 direction = delta / distance;
          vec3 bend = cross(direction, vec3(0.,0.,1.));
          if(length(bend)<.01) bend = cross(direction,vec3(0.,1.,0.));
          bend = normalize(bend) * min(.20,.045+.06*distance);
          vec3 center = mix(aStart,aEnd,t) + bend*sin(3.14159265*t);
          vec3 tangent = delta + bend*3.14159265*cos(3.14159265*t);
          vec4 eye = modelViewMatrix*vec4(center,1.);
          vec2 projected = (modelViewMatrix*vec4(tangent,0.)).xy;
          vec2 normal = length(projected)>.0001 ? normalize(vec2(-projected.y,projected.x)) : vec2(1.,0.);
          float terminal = exp(-900.*pow(t-.47,2.))+exp(-900.*pow(t-.53,2.));
          float radius = (.004+.007*aStrength)*(1.+.60*terminal);
          eye.xy += normal*position.x*radius;
          gl_Position = projectionMatrix*eye;
          vAcross=position.x; vAlong=t; vGrowth=aGrowth; vOpacity=aOpacity; vColor=aColor; vPhase=dot(aStart,vec3(2.1,3.7,1.9));
        }`,
      fragmentShader: `
        uniform float uVisibleTime,uAmbientFlow;
        varying float vAcross, vAlong, vGrowth, vOpacity, vPhase;
        varying vec3 vColor;
        void main() {
          if(vAlong>vGrowth*.5 && vAlong<1.-vGrowth*.5) discard;
          if(abs(vAlong-.5)<.003) discard;
          float rounded=sqrt(max(0.,1.-vAcross*vAcross));
          float flow=exp(-100.*pow(fract(vAlong-uVisibleTime*.32+vPhase)-.5,2.))*uAmbientFlow;
          float alpha=vOpacity*smoothstep(0.,.25,rounded)*(1.+flow*.24);
          gl_FragColor=vec4(vColor*(.58+.42*rounded)*(1.+flow*.35),alpha);
          #include <tonemapping_fragment>
          #include <colorspace_fragment>
        }`,
    }));
    this.mesh.frustumCulled = false;
    this.mesh.name = 'source-synaptic-bridges';
    this.mesh.visible = false;
  }

  set(synapses: readonly Synapse[], focus: string | null, reducedMotion: boolean): void {
    if (this.disposed) return;
    const incoming = new Set(synapses.slice(0, SYNAPSE_CAP).map(edge => edge.key));
    this.records.forEach(record => { record.active = incoming.has(record.key); });
    for (const edge of synapses.slice(0, SYNAPSE_CAP)) {
      let record = this.index.get(edge.key);
      if (!record) {
        if (this.records.length >= CAPACITY) this.remove(this.records.findIndex(candidate => !candidate.active));
        record = { ...edge, goal: edge.strength, growth: reducedMotion ? 1 : 0, active: true, color: this.neutral.clone(), ax:0,ay:0,az:0,bx:0,by:0,bz:0 };
        this.records.push(record); this.index.set(edge.key, record);
      }
      record.goal = edge.strength; record.active = true;
      record.color.copy(edge.source === focus || edge.target === focus ? this.selected : this.neutral);
      if (reducedMotion) { record.strength = record.goal; record.growth = 1; }
    }
    if (reducedMotion) for (let i = this.records.length - 1; i >= 0; i--) if (!this.records[i].active) this.remove(i);
    this.dirty = true; this.moving = !reducedMotion && this.records.some(record => !record.active || record.growth < 1 || Math.abs(record.strength - record.goal) > .001);
    this.geometry.instanceCount = this.records.length; this.mesh.visible = this.records.length > 0;
  }

  update(dt: number, points: ReadonlyMap<string, Point3>, reducedMotion: boolean): boolean {
    if (this.disposed) return false;
    let changed = this.dirty; this.moving = false;
    const elapsed = Number.isFinite(dt) ? Math.min(.25, Math.max(0, dt)) : 0;
    const alpha = 1 - Math.exp(-elapsed / .16);
    for (let i = this.records.length - 1; i >= 0; i--) {
      const record = this.records[i], target = record.active ? 1 : 0;
      if (reducedMotion) record.growth = target;
      else record.growth += (target - record.growth) * alpha;
      if (Math.abs(record.growth - target) < .003) record.growth = target;
      if (!record.active && record.growth === 0) { this.remove(i); changed = true; continue; }
      record.strength += (record.goal - record.strength) * (reducedMotion ? 1 : alpha);
      if (Math.abs(record.goal - record.strength) < .001) record.strength = record.goal;
      if (record.growth !== target || record.strength !== record.goal) this.moving = true;
    }
    for (let i = 0; i < this.records.length; i++) {
      const record = this.records[i];
      const a = points.get(record.source), b = points.get(record.target), at = i * 3;
      if (record.active && a && b) {
        record.ax=a.x; record.ay=a.y; record.az=a.z; record.bx=b.x; record.by=b.y; record.bz=b.z;
      }
      if (this.starts[at] !== Math.fround(record.ax) || this.starts[at+1] !== Math.fround(record.ay) || this.starts[at+2] !== Math.fround(record.az)
        || this.ends[at] !== Math.fround(record.bx) || this.ends[at+1] !== Math.fround(record.by) || this.ends[at+2] !== Math.fround(record.bz)) changed=true;
      this.starts[at]=record.ax; this.starts[at+1]=record.ay; this.starts[at+2]=record.az;
      this.ends[at]=record.bx; this.ends[at+1]=record.by; this.ends[at+2]=record.bz;
      if (this.growths[i] !== Math.fround(record.growth) || this.strengths[i] !== Math.fround(record.strength)) changed = true;
      this.strengths[i] = record.strength; this.growths[i] = record.growth;
      this.opacities[i] = (record.active ? .22 + .16 * record.strength : .20) * record.growth;
      this.colors[at] = record.color.r; this.colors[at + 1] = record.color.g; this.colors[at + 2] = record.color.b;
    }
    if (changed) for (let i=0;i<this.attributes.length;i++) this.attributes[i].needsUpdate = true;
    this.geometry.instanceCount = this.records.length; this.mesh.visible = this.records.length > 0;
    this.dirty = false; return changed;
  }

  diagnostics(): { active: number; retiring: number; capacity: number; bufferBytes: number; moving: boolean } {
    const active = this.records.filter(record => record.active).length;
    return { active, retiring: this.records.length - active, capacity: CAPACITY, moving: this.moving,
      bufferBytes: this.starts.byteLength + this.ends.byteLength + this.strengths.byteLength + this.growths.byteLength + this.opacities.byteLength + this.colors.byteLength };
  }

  setVisualMotion(visibleTime:number,enabled:boolean):void {
    const material=this.mesh.material as THREE.ShaderMaterial;
    material.uniforms.uVisibleTime.value=Number.isFinite(visibleTime)?visibleTime:0;
    material.uniforms.uAmbientFlow.value=enabled?1:0;
  }

  private remove(at: number): void {
    if (at < 0) return;
    this.index.delete(this.records[at].key);
    const last = this.records.pop()!;
    if (at < this.records.length) this.records[at] = last;
    this.dirty = true;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true; this.geometry.dispose(); (this.mesh.material as THREE.Material).dispose();
    this.records.length = 0; this.index.clear();
  }
}
