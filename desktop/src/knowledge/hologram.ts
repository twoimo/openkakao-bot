import * as THREE from "three";
import { AnimationLoop } from "../core/animation-loop";
import {
  KnowledgeDrilldown,
  type KnowledgeGraph,
  type KnowledgeNode,
  type KnowledgeView,
} from "./graph-model";

export interface KnowledgeFocusEvent {
  node: KnowledgeNode;
  view: KnowledgeView;
}

type FocusHandler = (event: KnowledgeFocusEvent) => void;

function cssColor(name: string, fallback: string): THREE.Color {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return new THREE.Color(value || fallback);
}

function spherePoint(index: number, count: number, radius: number): THREE.Vector3 {
  const offset = 2 / Math.max(count, 1);
  const y = (index * offset - 1) + offset / 2;
  const ring = Math.sqrt(Math.max(0, 1 - y * y));
  const phi = index * Math.PI * (3 - Math.sqrt(5));
  return new THREE.Vector3(Math.cos(phi) * ring * radius, y * radius, Math.sin(phi) * ring * radius);
}

function disposeObject(object: THREE.Object3D): void {
  object.traverse((child) => {
    if (child instanceof THREE.Mesh || child instanceof THREE.LineSegments || child instanceof THREE.Line) {
      child.geometry.dispose();
      const materials = Array.isArray(child.material) ? child.material : [child.material];
      materials.forEach((material) => material.dispose());
    }
  });
}

export class KnowledgeHologram {
  private readonly renderer: THREE.WebGLRenderer;
  private readonly scene = new THREE.Scene();
  private readonly camera = new THREE.PerspectiveCamera(38, 2, 0.1, 30);
  private readonly graphRoot = new THREE.Group();
  private readonly raycaster = new THREE.Raycaster();
  private readonly pointer = new THREE.Vector2();
  private readonly loop: AnimationLoop;
  private readonly drilldown: KnowledgeDrilldown;
  private readonly positions = new Map<string, THREE.Vector3>();
  private readonly nodeMeshes = new Map<string, THREE.Mesh>();
  private readonly desiredCamera = new THREE.Vector3(0, 0, 5.2);
  private readonly desiredLookAt = new THREE.Vector3();
  private readonly lookAt = new THREE.Vector3();
  private readonly resizeObserver: ResizeObserver | null;
  private view: KnowledgeView;

  constructor(
    private readonly canvas: HTMLCanvasElement,
    private readonly graph: KnowledgeGraph,
    private readonly onFocus: FocusHandler,
  ) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: "low-power" });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.camera.position.copy(this.desiredCamera);
    this.scene.add(this.graphRoot);

    const ambient = new THREE.AmbientLight(0xfffbf2, 1.55);
    const key = new THREE.DirectionalLight(0xffe2a8, 1.45);
    key.position.set(2.5, 3.5, 5);
    this.scene.add(ambient, key);
    this.addGuidePlane();

    const sorted = [...graph.nodes].sort((left, right) => left.id.localeCompare(right.id));
    sorted.forEach((node, index) => this.positions.set(node.id, spherePoint(index, sorted.length, 1.42)));

    this.drilldown = new KnowledgeDrilldown(graph);
    this.view = this.drilldown.current();
    this.rebuildGraph();
    this.loop = new AnimationLoop((dt) => this.render(dt));
    this.canvas.addEventListener("pointerdown", this.handlePointerDown);

    this.resizeObserver = typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(() => this.resize());
    this.resizeObserver?.observe(canvas);
    this.resize();
  }

  start(): void {
    this.loop.start();
  }

  stop(): void {
    this.loop.stop();
  }

  get renderCount(): number {
    return this.loop.renderCount;
  }

  get currentView(): KnowledgeView {
    return this.view;
  }

  clickNode(nodeId: string): KnowledgeView {
    const node = this.graph.nodes.find((candidate) => candidate.id === nodeId);
    if (!node) return this.view;
    this.view = this.drilldown.clickNode(nodeId);
    this.rebuildGraph();
    this.focusCamera(nodeId);
    this.onFocus({ node, view: this.view });
    return this.view;
  }

  expandOneHop(): KnowledgeView {
    this.view = this.drilldown.expandOneHop();
    this.rebuildGraph();
    if (this.view.focusId) this.focusCamera(this.view.focusId);
    return this.view;
  }

  reset(): KnowledgeView {
    this.view = this.drilldown.reset();
    this.rebuildGraph();
    this.desiredCamera.set(0, 0, 5.2);
    this.desiredLookAt.set(0, 0, 0);
    return this.view;
  }

  dispose(): void {
    this.stop();
    this.canvas.removeEventListener("pointerdown", this.handlePointerDown);
    this.resizeObserver?.disconnect();
    disposeObject(this.graphRoot);
    this.scene.children
      .filter((child) => child !== this.graphRoot)
      .forEach((child) => disposeObject(child));
    this.renderer.dispose();
  }

  private addGuidePlane(): void {
    const accent = cssColor("--accent", "#B88A45");
    const muted = cssColor("--muted", "#6D655B");
    const grid = new THREE.GridHelper(4.8, 12, accent, muted);
    const gridMaterials = Array.isArray(grid.material) ? grid.material : [grid.material];
    gridMaterials.forEach((material) => {
      material.transparent = true;
      material.opacity = 0.08;
      material.depthWrite = false;
    });
    grid.position.y = -1.6;
    this.scene.add(grid);

    const ring = new THREE.Mesh(
      new THREE.TorusGeometry(1.85, 0.008, 4, 96),
      new THREE.MeshBasicMaterial({ color: accent, transparent: true, opacity: 0.13 }),
    );
    ring.rotation.x = Math.PI / 2;
    ring.position.y = -1.55;
    this.scene.add(ring);
  }

  private rebuildGraph(): void {
    while (this.graphRoot.children.length > 0) {
      const child = this.graphRoot.children.pop();
      if (child) disposeObject(child);
    }
    this.nodeMeshes.clear();
    const accent = cssColor("--accent", "#B88A45");
    const text = cssColor("--text", "#241F1A");
    const muted = cssColor("--muted", "#6D655B");
    const focusId = this.view.focusId;

    for (const edge of this.view.edges) {
      const start = this.positions.get(edge.source);
      const end = this.positions.get(edge.target);
      if (!start || !end) continue;
      const geometry = new THREE.BufferGeometry().setFromPoints([start, end]);
      const touchesFocus = focusId !== null && (edge.source === focusId || edge.target === focusId);
      const material = new THREE.LineBasicMaterial({
        color: touchesFocus ? accent : muted,
        transparent: true,
        opacity: touchesFocus ? 0.58 : 0.22,
      });
      this.graphRoot.add(new THREE.Line(geometry, material));
    }

    for (const node of this.view.nodes) {
      const position = this.positions.get(node.id);
      if (!position) continue;
      const focused = node.id === focusId;
      const radius = 0.065 + (node.importance / 100) * 0.055 + (focused ? 0.025 : 0);
      const geometry = new THREE.SphereGeometry(radius, 18, 12);
      const material = new THREE.MeshStandardMaterial({
        color: focused ? accent : text,
        roughness: 0.74,
        metalness: focused ? 0.16 : 0.04,
      });
      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.copy(position);
      mesh.userData.nodeId = node.id;
      this.nodeMeshes.set(node.id, mesh);
      this.graphRoot.add(mesh);
    }
  }

  private focusCamera(nodeId: string): void {
    const position = this.positions.get(nodeId);
    if (!position) return;
    this.desiredLookAt.copy(position);
    this.desiredCamera.set(position.x * 0.58, position.y * 0.58, position.z + 2.55);
  }

  private readonly handlePointerDown = (event: PointerEvent): void => {
    const rect = this.canvas.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    this.pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    this.pointer.y = -(((event.clientY - rect.top) / rect.height) * 2 - 1);
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const hit = this.raycaster.intersectObjects([...this.nodeMeshes.values()], false)[0];
    const nodeId = typeof hit?.object.userData.nodeId === "string" ? hit.object.userData.nodeId : "";
    if (nodeId) this.clickNode(nodeId);
    else this.reset();
  };

  private resize(): void {
    const rect = this.canvas.getBoundingClientRect();
    const width = Math.max(1, Math.floor(rect.width || 640));
    const height = Math.max(1, Math.floor(rect.height || 320));
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
  }

  private render(dt: number): void {
    const alpha = 1 - Math.exp(-6.2 * Math.min(dt, 0.05));
    this.camera.position.lerp(this.desiredCamera, alpha);
    this.lookAt.lerp(this.desiredLookAt, alpha);
    this.camera.lookAt(this.lookAt);
    this.renderer.render(this.scene, this.camera);
  }
}
