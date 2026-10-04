import * as THREE from 'three';
import {describe,expect,it} from 'vitest';
import {parseKnowledgeGraph} from '../knowledge/graph-model';
import {SynapticBridges} from '../knowledge/synapses';

describe('source synaptic bridges',()=>{
  it('grows and retracts inside one retained geometry without inventing relationships',()=>{
    const bridges=new SynapticBridges(),geometry=bridges.mesh.geometry;
    const points=new Map([['a',{x:0,y:0,z:0}],['b',{x:1,y:0,z:0}]]);
    bridges.set([{key:'a-b',source:'a',target:'b',strength:.8}],null,false);
    bridges.update(.1,points,false);
    expect(geometry.getAttribute('aGrowth').getX(0)).toBeGreaterThan(0);
    expect(geometry.getAttribute('aGrowth').getX(0)).toBeLessThan(1);
    for(let i=0;i<20;i++)bridges.update(.1,points,false);
    expect(bridges.diagnostics().active).toBe(1);expect(bridges.moving).toBe(false);
    bridges.set([],null,false);expect(bridges.diagnostics().retiring).toBe(1);
    for(let i=0;i<20;i++)bridges.update(.1,points,false);
    expect(bridges.mesh.geometry).toBe(geometry);expect(bridges.mesh.visible).toBe(false);
    expect(bridges.diagnostics().retiring).toBe(0);bridges.dispose();
  });
  it('snaps reduced motion and disables ambient flow without changing source weights',()=>{
    const bridges=new SynapticBridges();
    const edge={key:'a-b',source:'a',target:'b',strength:.8};
    bridges.set([edge],'a',true);bridges.update(0,new Map(),true);
    expect(bridges.mesh.geometry.getAttribute('aGrowth').getX(0)).toBe(1);
    bridges.setVisualMotion(1,true);bridges.setVisualMotion(1,false);
    expect((bridges.mesh.material as THREE.ShaderMaterial).uniforms.uAmbientFlow.value).toBe(0);
    expect(edge.strength).toBe(.8);expect(bridges.moving).toBe(false);bridges.dispose();
  });
  it('projects only current evidence and keeps the original payload intact',()=>{
    const payload={nodes:[{id:'a',label:'A'},{id:'b',label:'B'},{id:'retired',label:'Retired',evidence:{retracted:true}}],
      edges:[{source:'a',target:'b',valid_to:'2026-01-01T00:00:00Z'},{source:'a',target:'b',evidence:{retracted:true}},
        {source:'a',target:'retired'},{source:'a',target:'b',weight:2}]};
    const before=JSON.stringify(payload),graph=parseKnowledgeGraph(payload,Date.parse('2026-10-03T00:00:00Z'));
    expect(graph.nodes).toHaveLength(2);expect(graph.edges).toHaveLength(1);
    expect(graph.edges[0].weight).toBe(2);expect(JSON.stringify(payload)).toBe(before);
  });
});
