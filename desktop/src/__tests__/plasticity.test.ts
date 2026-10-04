import { describe, expect, it } from 'vitest';
import { parseKnowledgeGraph, type KnowledgeView } from '../knowledge/graph-model';
import { PlasticityLayout, selectSynapses } from '../knowledge/plasticity';

const view = (weight = 1): KnowledgeView => ({
  ...parseKnowledgeGraph({ nodes: [{ id: 'a', label: 'A', importance: 50 }, { id: 'b', label: 'B', importance: 50 }], edges: [{ source: 'a', target: 'b', weight }] }),
  focusId: null, hops: 0,
});
const anchors = new Map([['a', { x: -.85, y: 0, z: 0 }], ['b', { x: .85, y: 0, z: 0 }]]);
const simulate = (graph: KnowledgeView, fps: number) => {
  const model = new PlasticityLayout(); model.setGraph(graph, anchors, anchors);
  for (let i = 0; i < fps * 8; i++) model.advance(1 / fps);
  return model;
};

describe('source-driven plastic remodeling', () => {
  it('sanitizes invalid targets, settles projected anchors and clears empty diagnostics', () => {
    const limits = new Map([['a', { x: 3, y: 0, z: 0 }], ['b', { x: NaN, y: Infinity, z: 0 }]]);
    const model = new PlasticityLayout(); model.setGraph(view(0), limits, limits);
    for (let i = 0; i < 480; i++) model.advance(1 / 60);
    expect([...model.coordinates].every(Number.isFinite)).toBe(true); expect(model.moving).toBe(false);
    expect(Math.abs(model.coordinates[0])).toBeLessThanOrEqual(2.3);
    model.setGraph({ nodes: [], edges: [], focusId: null, hops: 0 }, new Map(), new Map());
    expect(model.diagnostics()).toMatchObject({ nodes: 0, synapses: 0, maxSpeed: 0, maxForce: 0, moving: false });
  });
  it('uses the same elapsed clock at a slow frame rate within the bounded frame horizon', () => {
    const a = new PlasticityLayout(), b = new PlasticityLayout();
    a.setGraph(view(), anchors, anchors); b.setGraph(view(), anchors, anchors);
    for (let i = 0; i < 60; i++) a.advance(1 / 60);
    for (let i = 0; i < 10; i++) b.advance(.1);
    expect(a.diagnostics().simulatedSeconds).toBeCloseTo(1, 9);
    expect(b.diagnostics().simulatedSeconds).toBeCloseTo(1, 9);
    for (let i = 0; i < 6; i++) expect(Math.abs(a.coordinates[i] - b.coordinates[i])).toBeLessThan(.003);
  });
  it('is invariant to 30/60 fps and settles without a permanent animation', () => {
    const a = simulate(view(), 30), b = simulate(view(), 60);
    for (let i = 0; i < 6; i++) expect(a.coordinates[i]).toBeCloseTo(b.coordinates[i], 9);
    expect(a.moving).toBe(false); expect(b.moving).toBe(false);
    const settled = [...a.coordinates]; expect(a.advance(1)).toBe(false); expect([...a.coordinates]).toEqual(settled);
  });
  it('contracts a stronger real link and preserves coordinates when the target is unchanged', () => {
    const weak = simulate(view(.01), 60), strong = simulate(view(10), 60);
    expect(strong.coordinates[3] - strong.coordinates[0]).toBeLessThan(weak.coordinates[3] - weak.coordinates[0]);
    const before = [...strong.coordinates]; strong.setGraph(view(10), anchors, anchors);
    expect([...strong.coordinates]).toEqual(before); expect(strong.moving).toBe(false);
  });
  it('separates coincident nodes with finite positions and bounded steps', () => {
    const same = new Map([['a', { x: 0, y: 0, z: 0 }], ['b', { x: 0, y: 0, z: 0 }]]);
    const model = new PlasticityLayout(); model.setGraph(view(0), same, same);
    for (let i = 0; i < 480; i++) model.advance(i % 2 ? .001 : 3);
    expect([...model.coordinates].every(Number.isFinite)).toBe(true);
    expect(Math.hypot(model.coordinates[3] - model.coordinates[0], model.coordinates[4] - model.coordinates[1])).toBeGreaterThan(.12);
    expect(model.diagnostics().maxSpeed).toBeLessThanOrEqual(1.4);
  });
  it('bundles duplicate relations visually and removes withdrawn links without rewriting evidence', () => {
    const graph = view(); graph.edges.push({ ...graph.edges[0], source: 'b', target: 'a', weight: 3 });
    const original = JSON.stringify(graph); expect(selectSynapses(graph.edges)).toHaveLength(1); expect(JSON.stringify(graph)).toBe(original);
    graph.edges.forEach(edge => { edge.evidence = { ...edge.evidence, retracted: true }; });
    expect(selectSynapses(graph.edges)).toEqual([]);
    const model = new PlasticityLayout(); model.setGraph(graph, anchors, anchors);
    expect(model.diagnostics().synapses).toBe(0);
  });
});
