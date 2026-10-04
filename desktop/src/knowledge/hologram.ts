import * as THREE from "three";
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { AnimationLoop } from "../core/animation-loop";
import type { AnimationLoopDiagnostics } from "../core/animation-loop";
import { createCorticalBackdrop } from './cortex';
import { pickKnowledgeSphere } from "./picking";
import { overviewCameraDistance } from './camera-fit';
import { PlasticityLayout, selectSynapses } from './plasticity';
import { SynapticBridges } from './synapses';
import { VoiceEnvelope } from "./voice-envelope";
import { VoiceAmplitudePoller, type VoiceStatusLoader } from "../voice-amplitude-poller";
import type { VoiceAmplitudeSource } from "../voice-amplitude";
import { fetchVoiceStatus } from "../runtime";
import type { EmergencyState, RuntimeSnapshot, VoiceStatus } from '../contracts';
import { ThinkingOrbMesh } from '../orbs/mesh';
import { runtimeOrb, QUIET_ORB, PAUSED_ORB, type OrbActivity } from '../orbs/activity';
import { orbCacheBytes, type OrbState } from '../orbs/frames';
import {
  KnowledgeDrilldown,
  type KnowledgeGraph,
  type KnowledgeNode,
  type KnowledgeView,
} from "./graph-model";

export interface KnowledgeFocusEvent {
  node: KnowledgeNode | null;
  view: KnowledgeView;
}

type FocusHandler = (event: KnowledgeFocusEvent) => void;

function cssColor(surface: Element, name: string, fallback: string): THREE.Color {
  const value = getComputedStyle(surface).getPropertyValue(name).trim();
  return new THREE.Color(value || fallback);
}

function stablePoint(id: string, radius: number): THREE.Vector3 {
  let seed=2166136261;
  for(let i=0;i<id.length;i++)seed=Math.imul(seed^id.charCodeAt(i),16777619);
  const y = ((seed>>>0)+.5)/4294967296*2-1;
  const ring = Math.sqrt(Math.max(0, 1 - y * y));
  const phi = (Math.imul(seed^0x9e3779b9,1664525)>>>0)/4294967296*Math.PI*2;
  return new THREE.Vector3(Math.cos(phi) * ring * radius, y * radius, Math.sin(phi) * ring * radius);
}

function disposeObject(object: THREE.Object3D): void {
  const geometries=new Set<THREE.BufferGeometry>(), materials=new Set<THREE.Material>();
  object.traverse((child) => {
    if (child instanceof THREE.Mesh || child instanceof THREE.Points || child instanceof THREE.LineSegments || child instanceof THREE.Line) {
      if(!geometries.has(child.geometry)){geometries.add(child.geometry);child.geometry.dispose();}
      const list = Array.isArray(child.material) ? child.material : [child.material];
      for(const material of list)if(!materials.has(material)){materials.add(material);material.dispose();}
    }
  });
}

export class KnowledgeHologram {
  private readonly renderer: THREE.WebGLRenderer;
  private readonly scene = new THREE.Scene();
  private readonly camera = new THREE.PerspectiveCamera(38, 2, 0.1, 60);
  private readonly graphRoot = new THREE.Group();
  private readonly voiceEnvelope = new VoiceEnvelope();
  private readonly voicePoller: VoiceAmplitudePoller | null;
  private voiceSource: VoiceAmplitudeSource = "none";
  private readonly raycaster = new THREE.Raycaster();
  private readonly pointer = new THREE.Vector2();
  private readonly loop: AnimationLoop;
  private readonly orbit: OrbitControls;
  private orbitActive=false;
  private readonly press=new THREE.Vector2();
  private drilldown: KnowledgeDrilldown;
  private readonly positions = new Map<string, THREE.Vector3>();
  private readonly displayedPositions = new Map<string, THREE.Vector3>();
  private readonly phases = new Map<string, number>();
  private visibleTime = 0;
  private ambientMotion = false;
  private readonly cortex = createCorticalBackdrop();
  private readonly anchors = new Map<string, THREE.Vector3>();
  private readonly plasticity = new PlasticityLayout();
  private readonly synapses = new SynapticBridges();
  private readonly nodeMeshes = new Map<string, THREE.Mesh>();
  private readonly orbMeshes = new Map<string, ThinkingOrbMesh>();
  private readonly motion = window.matchMedia('(prefers-reduced-motion: reduce)');
  private snapshot: RuntimeSnapshot | null = null;
  private latestVoice: VoiceStatus | null = null;
  private paused = false;
  private activity: OrbActivity = QUIET_ORB;
  private rootNodeId = '';
  private readonly labels = new Map<string, HTMLSpanElement>();
  private readonly labelBounds: Array<{ width: number; left: number; top: number; visible: boolean }> = [];
  private readonly labelOverlays: HTMLElement[] = [];
  private readonly excludedLabelBounds: Array<{ left:number; top:number; right:number; bottom:number }> = [];
  private readonly projected = new THREE.Vector3();
  private readonly desiredCamera = new THREE.Vector3(0, 0, 5.2);
  private readonly desiredLookAt = new THREE.Vector3();
  private readonly lookAt = new THREE.Vector3();
  private readonly resizeObserver: ResizeObserver | null;
  private view: KnowledgeView;
  private disposed = false;
  private requestedAnimation = false;
  private viewport = { x: 0, y: 0, width: 1, height: 1 };

  constructor(
    private readonly canvas: HTMLCanvasElement,
    private graph: KnowledgeGraph,
    private readonly onFocus: FocusHandler,
    private readonly onDispose: () => void = () => undefined,
    private readonly layoutMode: 'workspace'|'popover' = 'workspace',
    voiceLoader: VoiceStatusLoader | null = fetchVoiceStatus,
    private readonly onVoice: (voice: VoiceStatus | null) => void = () => undefined,
  ) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: "low-power" });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.camera.position.copy(this.desiredCamera);
    this.orbit=new OrbitControls(this.camera,canvas);
    this.orbit.enableDamping=false;this.orbit.minDistance=2.2;this.orbit.maxDistance=12;this.orbit.rotateSpeed=.65;this.orbit.zoomSpeed=.7;
    this.orbit.addEventListener('start',()=>{this.orbitActive=true;});
    this.orbit.addEventListener('end',()=>{this.orbitActive=false;});
    this.orbit.addEventListener('change',()=>{if(this.orbitActive){this.desiredCamera.copy(this.camera.position);this.desiredLookAt.copy(this.orbit.target);this.lookAt.copy(this.orbit.target);}this.invalidateFrame();});
    this.scene.add(this.graphRoot);
    this.graphRoot.add(this.synapses.mesh);

    const ambient = new THREE.AmbientLight(0xd7e1f3, 1.75);
    const key = new THREE.DirectionalLight(0xffedcc, 2.1);
    key.position.set(2.5, 3.5, 5);
    this.scene.add(ambient, key);
    this.scene.add(this.cortex);
    this.scene.add(this.voiceEnvelope.line);

    this.layoutPositions();

    this.drilldown = new KnowledgeDrilldown(graph);
    this.view = this.drilldown.current();
    this.rebuildGraph();
    this.loop = new AnimationLoop((dt) => this.render(dt));
    this.voicePoller = voiceLoader ? new VoiceAmplitudePoller((rms, source) => {
      if (!this.requestedAnimation || this.disposed) return;
      this.voiceSource = source;
      this.applyVoiceRms(this.paused ? 0 : rms);
    }, voiceLoader, undefined, voice => {
      if (!this.requestedAnimation || this.disposed) return;
      this.latestVoice = voice; this.updateOrbActivity(); this.onVoice(voice);
    }) : null;
    this.canvas.addEventListener("pointerdown", this.rememberPress);
    this.canvas.addEventListener("pointerup", this.handlePointerDown);
    this.motion.addEventListener('change', this.handleMotionChange);

    this.resizeObserver = typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(() => this.resize());
    this.resizeObserver?.observe(canvas);
    const workspace = canvas.closest('.knowledge-section, .alden-panel');
    for (const selector of ['.knowledge-heading, .mini-graph-heading', '.knowledge-focus-card', '.knowledge-hologram-toolbar, .mini-graph-title']) {
      const overlay = workspace?.querySelector<HTMLElement>(selector);
      if (overlay) { this.labelOverlays.push(overlay); this.excludedLabelBounds.push({left:0,top:0,right:0,bottom:0}); this.resizeObserver?.observe(overlay); }
    }
    this.resize();
  }

  start(): void {
    if (this.disposed) return;
    this.requestedAnimation = true;
    this.updateOrbActivity();
    if (this.view.nodes.length > 0) { this.loop.start(); this.voicePoller?.start(); }
  }

  stop(): void {
    this.requestedAnimation = false;
    this.ambientMotion = false;
    this.synapses.setVisualMotion(this.visibleTime, false);
    try { this.voicePoller?.stop(); } finally {
      try { this.loop.stop(); } finally {
        this.loop.setVoiceActive(false);
        this.voiceEnvelope.clear();
        this.voiceSource = "none";
        this.latestVoice = null;
      }
    }
  }

  get renderCount(): number {
    return this.loop.renderCount;
  }

  setSignals(load:number,voiceRms:number):void {
    if (this.disposed) return;
    const bounded=Number.isFinite(load)?Math.max(0,Math.min(1,load)):0;
    this.loop.setLoad(bounded);
    const scale=1+bounded*.006;
    if(Math.abs(this.graphRoot.scale.x-scale)>0.0001){this.graphRoot.scale.setScalar(scale);this.invalidateFrame();}
    // The narrow poller owns current input/output; the slow snapshot supplies load.
    if (!this.voicePoller) this.applyVoiceRms(voiceRms);
  }

  private applyVoiceRms(rms: number): void {
    this.loop.setVoiceActive(rms > 0 || this.voiceEnvelope.displayedRms > 0 || this.orbMeshes.get(this.rootNodeId)?.active === true);
    if (this.voiceEnvelope.setRms(rms)) this.invalidateFrame();
  }

  setActivity(snapshot: RuntimeSnapshot | null): void { this.snapshot = snapshot; this.updateOrbActivity(); }
  setEmergency(state: EmergencyState): void {
    if (this.disposed) return;
    this.paused = state.latched;
    if (this.paused) { this.voiceEnvelope.clear(); this.voiceSource = 'none'; }
    this.updateOrbActivity();
    this.invalidateFrame();
  }
  private readonly updateOrbActivity = (): void => {
    if (this.disposed) return;
    this.activity = this.paused ? PAUSED_ORB : runtimeOrb(this.snapshot, this.latestVoice ?? this.snapshot?.voice ?? null);
    const root = this.orbMeshes.get(this.rootNodeId);
    if (root?.setActivity(this.activity, this.motion.matches)) this.invalidateFrame();
  };
  private readonly handleMotionChange = (): void => { this.updateOrbActivity(); this.invalidateFrame(); };

  private invalidateFrame(): void {
    if (!this.disposed && this.requestedAnimation && this.view.nodes.length > 0) this.loop.start();
  }

  get currentView(): KnowledgeView {
    return this.view;
  }

  get canGoBack(): boolean { return this.drilldown.canGoBack; }

  replaceGraph(graph: KnowledgeGraph): void {
    if (this.disposed) return;
    const focus = this.view.focusId;
    this.graph = graph;
    const sorted = [...graph.nodes].sort((a, b) => a.id.localeCompare(b.id));
    this.layoutPositions();
    const ids = new Set(sorted.map(node => node.id));
    for (const id of this.positions.keys()) if (!ids.has(id)) this.positions.delete(id);
    for (const id of this.displayedPositions.keys()) if (!ids.has(id)) this.displayedPositions.delete(id);
    for (const id of this.phases.keys()) if (!ids.has(id)) this.phases.delete(id);
    this.view = this.drilldown.replaceGraph(graph);
    if (!focus || !ids.has(focus)) this.resetOverviewCamera();
    this.rebuildGraph();
    if (focus && ids.has(focus)) this.focusCamera(focus);
    this.canvas.hidden = graph.nodes.length === 0;
    this.resize();
    if (this.requestedAnimation && graph.nodes.length > 0) { this.loop.start(); this.voicePoller?.start(); }
    else { this.loop.stop(); this.voicePoller?.stop(); this.voiceEnvelope.clear(); this.voiceSource = "none"; }
    this.notifyView();
  }

  diagnostics(): AnimationLoopDiagnostics & { ambientMotion:boolean; visibleTime:number; voiceRms: number; voiceSource: VoiceAmplitudeSource; displayedRms: number; audioVertices: number; orbCount: number; orbState: OrbState; orbActive: boolean; orbCacheBytes: number; physics: ReturnType<PlasticityLayout['diagnostics']>; synapses: ReturnType<SynapticBridges['diagnostics']>; drawCalls:number; geometries:number } {
    return { ...this.loop.diagnostics(), voiceRms: this.voiceEnvelope.targetRms, voiceSource: this.voiceSource,
      displayedRms: this.voiceEnvelope.displayedRms, audioVertices: this.voiceEnvelope.line.geometry.getAttribute("position").count,
      orbCount: this.orbMeshes.size, orbState: this.activity.state, orbActive: this.orbMeshes.get(this.rootNodeId)?.active === true, orbCacheBytes: orbCacheBytes(), physics:this.plasticity.diagnostics(), synapses:this.synapses.diagnostics(), drawCalls:this.renderer.info.render.calls, geometries:this.renderer.info.memory.geometries, ambientMotion:this.ambientMotion, visibleTime:this.visibleTime };
  }
  get navigationTargets(): { camera: number[]; lookAt: number[] } {
    return { camera: this.desiredCamera.toArray(), lookAt: this.desiredLookAt.toArray() };
  }

  clickNode(nodeId: string): KnowledgeView {
    if (this.disposed) return this.view;
    const node = this.graph.nodes.find((candidate) => candidate.id === nodeId);
    if (!node) return this.view;
    this.view = this.drilldown.clickNode(nodeId);
    this.rebuildGraph();
    this.focusCamera(nodeId);
    this.onFocus({ node, view: this.view });
    return this.view;
  }

  expandOneHop(): KnowledgeView {
    if (this.disposed) return this.view;
    this.view = this.drilldown.expandOneHop();
    this.rebuildGraph();
    if (this.view.focusId) this.focusCamera(this.view.focusId);
    this.notifyView();
    return this.view;
  }

  reset(): KnowledgeView {
    if (this.disposed) return this.view;
    this.view = this.drilldown.reset();
    this.rebuildGraph();
    this.resetOverviewCamera();
    this.invalidateFrame();
    this.notifyView();
    return this.view;
  }

  back(): KnowledgeView {
    if (this.disposed) return this.view;
    this.view = this.drilldown.back();
    this.rebuildGraph();
    if (this.view.focusId) this.focusCamera(this.view.focusId);
    else {
      this.resetOverviewCamera();
      this.invalidateFrame();
    }
    this.notifyView();
    return this.view;
  }

  private notifyView(): void {
    const node = this.graph.nodes.find((candidate) => candidate.id === this.view.focusId) ?? null;
    this.onFocus({ node, view: this.view });
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.onDispose();
    this.stop();
    this.canvas.removeEventListener("pointerdown", this.rememberPress);
    this.canvas.removeEventListener("pointerup", this.handlePointerDown);
    this.motion?.removeEventListener('change', this.handleMotionChange);
    this.orbit.dispose();
    this.resizeObserver?.disconnect();
    this.synapses.mesh.removeFromParent();
    this.synapses.dispose();
    disposeObject(this.graphRoot);
    this.scene.children
      .filter((child) => child !== this.graphRoot)
      .forEach((child) => disposeObject(child));
    this.renderer.dispose();
    this.labels.clear();
    this.labelBounds.length = 0;
    this.canvas.parentElement?.querySelector(".knowledge-node-labels")?.replaceChildren();
    this.canvas.parentElement?.querySelector("#knowledge-accessible-nodes")?.replaceChildren();
  }

  private layoutPositions():void {
    const sorted=[...this.graph.nodes].sort((a,b)=>a.id.localeCompare(b.id));
    this.anchors.clear();
    const place=(id:string,point:THREE.Vector3):void=>{this.anchors.set(id,point);if(!this.positions.has(id))this.positions.set(id,point.clone());};
    const groups=sorted.filter(n=>n.category==='collection');
    if(!groups.length){sorted.forEach(n=>{const p=stablePoint(n.id,1.3);p.y*=.85;p.z*=.60;place(n.id,p);});return;}
    const root=groups.find(n=>n.label==='카카오톡')??groups[0];place(root.id,new THREE.Vector3(0,.1,0));
    for(const node of groups.filter(n=>n!==root)){
      const point=/인물|사람/.test(node.label)?new THREE.Vector3(-.82,.22,-.12):/대화방/.test(node.label)?new THREE.Vector3(.85,.15,.02):/주제/.test(node.label)?new THREE.Vector3(0,-.66,.12):stablePoint(node.id,1.05);
      place(node.id,point);
    }
    const parents=new Map(this.graph.edges.filter(e=>e.relation==='contains').map(e=>[e.target,e.source]));
    const members=new Map<string,KnowledgeNode[]>();
    for(const node of sorted){if(node.category==='collection')continue;const parent=parents.get(node.id)??root.id;const bucket=members.get(parent)??[];bucket.push(node);members.set(parent,bucket);}
    for(const [parent,nodes] of members){const center=this.anchors.get(parent)??new THREE.Vector3();nodes.forEach(node=>{
      const point=stablePoint(node.id,.65);point.y*=.8;point.z*=.6;point.add(center);place(node.id,point);
    });}
  }

  private rebuildGraph(): void {
    for (const child of [...this.graphRoot.children]) {
      if (child === this.synapses.mesh) continue;
      this.graphRoot.remove(child);
      disposeObject(child);
    }
    this.nodeMeshes.clear();
    this.orbMeshes.clear();
    this.labels.clear();
    this.labelBounds.length = 0;
    const labelLayer = this.canvas.parentElement?.querySelector(".knowledge-node-labels");
    labelLayer?.replaceChildren();
    const accessible = this.canvas.parentElement?.querySelector("#knowledge-accessible-nodes");
    accessible?.replaceChildren();
    const focusId = this.view.focusId;
    this.rootNodeId = this.graph.nodes.find(node => node.category === 'collection' && node.label === '카카오톡')?.id
      ?? this.graph.nodes.find(node => node.category === 'collection')?.id ?? '';

    this.plasticity.setGraph(this.view, this.anchors, this.positions);
    if (this.motion.matches) this.plasticity.settle();
    this.plasticity.forEachPoint(this.syncPoint);
    this.synapses.set(selectSynapses(this.view.edges,new Set(this.view.nodes.map(node=>node.id))),focusId,this.motion.matches);
    this.synapses.update(0,this.positions,this.motion.matches);

    for (const node of this.view.nodes) {
      const position = this.positions.get(node.id);
      if (!position) continue;
      if(!this.displayedPositions.has(node.id))this.displayedPositions.set(node.id,position.clone());
      if(!this.phases.has(node.id)){let hash=0;for(let i=0;i<node.id.length;i++)hash=Math.imul(hash,31)+node.id.charCodeAt(i);this.phases.set(node.id,(hash>>>0)/4294967296*Math.PI*2);}
      const focused = node.id === focusId;
      const kind=node.category==='collection'?node.label:node.category;
      const token=/인물|사람|대화 상대|화자/.test(kind)?'--cosmos-person':/대화방/.test(kind)?'--cosmos-room':/주제/.test(kind)?'--cosmos-topic':'--accent';
      const color=cssColor(this.canvas,token,"#dde7ef");
      const isOrb = node.category === 'collection' || focused;
      const diameter = node.id === this.rootNodeId ? 0.64 : node.category === 'collection' ? 0.4 : 0.3;
      const radius = isOrb ? diameter * 0.44 : .04 + (node.importance / 100) * 0.025;
      const geometry = new THREE.SphereGeometry(radius, 18, 12);
      const material = new THREE.MeshStandardMaterial({
        color,
        emissive: color,
        emissiveIntensity: focused ? 0.28 : 0.12,
        roughness: 0.55,
        metalness: 0.12,
      });
      const mesh = new THREE.Mesh(geometry, material);
      if (isOrb) {
        material.visible = false;
        const state: OrbState = /인물|사람|대화 상대|화자/.test(kind) ? 'listening' : /대화방/.test(kind) ? 'connecting' : /주제/.test(kind) ? 'composing' : 'solving';
        const orb = new ThinkingOrbMesh(state, diameter, color);
        orb.position.copy(position);
        if (node.id === this.rootNodeId) orb.setActivity(this.activity, this.motion.matches);
        orb.setViewportHeight(this.viewport.height * this.renderer.getPixelRatio());
        this.orbMeshes.set(node.id, orb);
        this.graphRoot.add(orb);
      }
      mesh.position.copy(position);
      mesh.userData.nodeId = node.id;
      this.nodeMeshes.set(node.id, mesh);
      this.graphRoot.add(mesh);
      if (accessible) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "knowledge-a11y-node";
        button.dataset.nodeId = node.id;
        button.textContent = node.label;
        button.setAttribute("aria-label", `${node.label} 선택`);
        button.onclick = () => this.clickNode(node.id);
        accessible.append(button);
      }
      if (labelLayer) {
        const label = document.createElement("span");
        label.className = "knowledge-node-label";
        label.textContent = node.label;
        label.title = node.label;
        label.dataset.focused = String(focused);
        labelLayer.append(label);
        this.labels.set(node.id, label);
        this.labelBounds.push({ width: 0, left: 0, top: 0, visible: false });
      }
    }
  }

  private focusCamera(nodeId: string): void {
    const position = this.anchors.get(nodeId) ?? this.positions.get(nodeId);
    if (!position) return;
    this.desiredLookAt.copy(position);
    this.desiredCamera.set(position.x * 0.58, position.y * 0.58, position.z + 2.55);
    this.invalidateFrame();
  }

  private readonly rememberPress=(event:PointerEvent):void=>{this.press.set(event.clientX,event.clientY);};
  private readonly handlePointerDown = (event: PointerEvent): void => {
    if(Math.hypot(event.clientX-this.press.x,event.clientY-this.press.y)>5)return;
    const rect = this.canvas.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    const x = event.clientX - rect.left - this.viewport.x;
    const y = event.clientY - rect.top - this.viewport.y;
    if (x < 0 || y < 0 || x > this.viewport.width || y > this.viewport.height) return;
    this.pointer.x = (x / this.viewport.width) * 2 - 1;
    this.pointer.y = -(y / this.viewport.height) * 2 + 1;
    // An idle scene may have changed since its last draw. Picking must use
    // the current transforms even before the invalidated frame is delivered.
    this.scene.updateMatrixWorld(true);
    this.camera.updateMatrixWorld(true);
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const hit = pickKnowledgeSphere(this.raycaster, this.nodeMeshes.values());
    const nodeId = typeof hit?.userData.nodeId === "string" ? hit.userData.nodeId : "";
    if (nodeId) this.clickNode(nodeId);
    else this.reset();
  };

  private resetOverviewCamera(): void {
    const points = this.view.nodes.flatMap(node => {
      const point = this.anchors.get(node.id) ?? this.positions.get(node.id);
      return point ? [point] : [];
    });
    this.desiredCamera.set(0, 0, overviewCameraDistance(points, this.viewport.width, this.viewport.height, this.camera.fov));
    this.desiredLookAt.set(0, 0, 0);
  }

  private resize(): void {
    if (this.disposed) return;
    const rect = this.canvas.getBoundingClientRect();
    const width = Math.max(1, Math.floor(rect.width || 640));
    const height = Math.max(1, Math.floor(rect.height || 320));
    this.renderer.setSize(width, height, false);
    const top = this.layoutMode==='popover'?58:48;
    const bottom=this.layoutMode==='popover'?42:80;
    this.viewport = { x: 28, y: Math.min(top, height / 2), width: Math.max(1, width - 56), height: Math.max(1, height - Math.min(top, height / 2) - bottom) };
    this.renderer.setViewport(this.viewport.x, height - this.viewport.y - this.viewport.height, this.viewport.width, this.viewport.height);
    this.camera.aspect = this.viewport.width / this.viewport.height;
    this.camera.updateProjectionMatrix();
    if (!this.view.focusId) this.resetOverviewCamera();
    for (const orb of this.orbMeshes.values()) orb.setViewportHeight(this.viewport.height * this.renderer.getPixelRatio());
    for (const box of this.labelBounds) box.width = 0;
    // Overlay geometry is read only on resize/selection layout changes.
    // Labels remain available through the keyboard list when visually covered.
    const parent = this.canvas.parentElement?.getBoundingClientRect();
    if (parent) for (let overlay = 0; overlay < this.labelOverlays.length; overlay++) {
      const element = this.labelOverlays[overlay], bounds = this.excludedLabelBounds[overlay];
      if (element.hidden) { bounds.right = bounds.left = 0; continue; }
      const rect = element.getBoundingClientRect();
      bounds.left = rect.left - parent.left - 6; bounds.top = rect.top - parent.top - 6;
      bounds.right = rect.right - parent.left + 6; bounds.bottom = rect.bottom - parent.top + 6;
    }
    this.invalidateFrame();
  }

  private render(dt: number): void {
    if (this.disposed) return;
    const elapsed = Math.min(.25, Math.max(0, dt));
    this.ambientMotion=!this.motion.matches&&!this.paused&&this.requestedAnimation&&this.view.nodes.length>0;
    if(this.ambientMotion)this.visibleTime+=elapsed;
    if (this.motion.matches && this.plasticity.moving) this.plasticity.settle();
    else if (!this.paused) this.plasticity.advance(elapsed);
    this.plasticity.forEachPoint(this.syncPoint);
    for (const [id,mesh] of this.nodeMeshes) {
      const base=this.positions.get(id),position=this.displayedPositions.get(id);
      if(base&&position){const phase=this.phases.get(id)??0;const sway=this.ambientMotion&&id!==this.view.focusId&&id!==this.rootNodeId?0.035:0;
        position.set(base.x+Math.sin(this.visibleTime*.80+phase)*sway,base.y+Math.cos(this.visibleTime*.67+phase)*sway,base.z+Math.sin(this.visibleTime*.58+phase)*sway*.6);
        mesh.position.copy(position);this.orbMeshes.get(id)?.position.copy(position);
      }
    }
    this.cortex.scale.setScalar(this.ambientMotion?1+Math.sin(this.visibleTime*.80)*.008:1);
    this.synapses.setVisualMotion(this.visibleTime,this.ambientMotion);
    this.synapses.update(elapsed,this.displayedPositions,this.motion.matches||this.paused);
    const cameraMoving=this.camera.position.distanceToSquared(this.desiredCamera)>0.000001||this.lookAt.distanceToSquared(this.desiredLookAt)>0.000001;
    this.loop.setInteractive(this.ambientMotion||this.plasticity.moving||this.synapses.moving||this.orbitActive||cameraMoving);
    const alpha = this.motion.matches||this.paused ? 1 : 1 - Math.exp(-12 * elapsed);
    this.camera.position.lerp(this.desiredCamera, alpha);
    this.lookAt.lerp(this.desiredLookAt, alpha);
    this.camera.lookAt(this.lookAt);
    this.orbit.target.copy(this.lookAt);this.orbit.update();
    if (this.voiceEnvelope.needsFrame) this.voiceEnvelope.advance(dt);
    if (this.latestVoice && Date.now() / 1000 - this.latestVoice.updatedAt > 3) { this.latestVoice = null; this.updateOrbActivity(); }
    const orbActive = this.orbMeshes.get(this.rootNodeId)?.active === true;
    for (const orb of this.orbMeshes.values()) orb.advance(dt);
    this.loop.setVoiceActive(this.voiceEnvelope.displayedRms > 0 || orbActive);
    this.renderer.render(this.scene, this.camera);
    const { x, y, width, height } = this.viewport;
    let index = 0;
    for (const [id, label] of this.labels) {
      const box = this.labelBounds[index++];
      const wasVisible = box.visible, previousLeft = box.left, previousTop = box.top;
      box.visible = false;
      const position = this.displayedPositions.get(id) ?? this.positions.get(id);
      if (!position) continue;
      this.projected.copy(position).applyMatrix4(this.graphRoot.matrixWorld).project(this.camera);
      const outside = Math.abs(this.projected.x) > 1 || Math.abs(this.projected.y) > 1 || this.projected.z > 1 || this.projected.z < -1;
      if (outside) { if (!label.hidden) label.hidden = true; continue; }
      // Measure only after a rebuild/resize. Reuse at most 24 collision boxes.
      if (!box.width) { label.hidden = false; box.width = label.offsetWidth || Math.min(156, label.textContent!.length * 11 + 12); }
      box.left = Math.max(x, Math.min(x + width - box.width, x + (this.projected.x + 1) * width / 2 - box.width / 2));
      const mesh = this.nodeMeshes.get(id);
      const radius = mesh?.geometry instanceof THREE.SphereGeometry ? mesh.geometry.parameters.radius : 0;
      const offset = this.orbMeshes.has(id) ? Math.min(54, radius * height / (2 * Math.tan(this.camera.fov * Math.PI / 360) * Math.max(.1, this.camera.position.distanceTo(position))) + 8) : 10;
      box.top = Math.min(y + height - 24, y + (1 - this.projected.y) * height / 2 + offset);
      let collision = false;
      for (const overlay of this.excludedLabelBounds) {
        if (overlay.right > overlay.left && box.left < overlay.right && box.left + box.width > overlay.left && box.top < overlay.bottom && box.top + 24 > overlay.top) { collision = true; break; }
      }
      for (let previous = 0; previous < index - 1; previous++) {
        const other = this.labelBounds[previous];
        if (other.visible && box.left < other.left + other.width + 5 && box.left + box.width + 5 > other.left && box.top < other.top + 24 && box.top + 24 > other.top) {
          collision = true;
          break;
        }
      }
      box.visible = !collision;
      if (label.hidden === box.visible) label.hidden = !box.visible;
      if (box.visible && (!wasVisible || previousLeft !== box.left || previousTop !== box.top)) label.style.transform = `translate(${box.left}px, ${box.top}px)`;
    }
    // Ambient motion is a visible presentation style, never a runtime load signal.
    if (this.paused || this.motion.matches || (!this.ambientMotion && !this.plasticity.moving && !this.synapses.moving && !orbActive && !this.voiceEnvelope.needsFrame && !this.orbitActive && !cameraMoving)) this.loop.stop();
  }
  private readonly syncPoint = (id:string,x:number,y:number,z:number):void => {this.positions.get(id)?.set(x,y,z);};
}
