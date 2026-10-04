import * as THREE from "three";
import { describe, expect, it, vi } from "vitest";
import { createCosmosBackdrop } from "../knowledge/cosmos";
import { createCorticalBackdrop } from '../knowledge/cortex';
import { SynapticBridges } from '../knowledge/synapses';
import { KnowledgeHologram } from "../knowledge/hologram";
import { VoiceEnvelope } from "../knowledge/voice-envelope";

describe("decorative sky ownership", () => {
  it("releases the sky's retained GPU buffers exactly once with its graph", () => {
    const sky=new THREE.Group();sky.add(createCosmosBackdrop(),createCorticalBackdrop());
    const synapses=new SynapticBridges();
    const disposals: ReturnType<typeof vi.spyOn>[]=[];
    const seen=new Set<object>();
    const watch=(resource:THREE.BufferGeometry|THREE.Material):void=>{if(!seen.has(resource)){seen.add(resource);disposals.push(vi.spyOn(resource,'dispose'));}};
    sky.traverse(child=>{
      if(child instanceof THREE.Points || child instanceof THREE.Line || child instanceof THREE.Mesh){
        watch(child.geometry);
        for(const material of Array.isArray(child.material)?child.material:[child.material]) watch(material);
      }
    });
    const voiceEnvelope=new VoiceEnvelope();
    disposals.push(vi.spyOn(voiceEnvelope.line.geometry,"dispose"),vi.spyOn(voiceEnvelope.line.material,"dispose"));
    watch(synapses.mesh.geometry);watch(synapses.mesh.material as THREE.Material);
    const graphRoot=new THREE.Group();graphRoot.add(synapses.mesh);const scene=new THREE.Scene();scene.add(graphRoot,sky,voiceEnvelope.line);
    const rendererDispose=vi.fn();const stopped=vi.fn();const started=vi.fn();
    const graph=Object.create(KnowledgeHologram.prototype) as KnowledgeHologram;
    Object.defineProperties(graph,{
      disposed:{value:false,writable:true},onDispose:{value:vi.fn()},
      canvas:{value:{removeEventListener:vi.fn(),parentElement:null}},
      loop:{value:{stop:stopped,start:started,setVoiceActive:vi.fn()}},orbit:{value:{dispose:vi.fn()}},
      resizeObserver:{value:null},graphRoot:{value:graphRoot},scene:{value:scene},voiceEnvelope:{value:voiceEnvelope},
      synapses:{value:synapses},
      renderer:{value:{dispose:rendererDispose}},labels:{value:new Map()},labelBounds:{value:[]},
    });
    graph.dispose();graph.dispose();graph.start();
    expect(disposals.length).toBeGreaterThan(0);
    for(const dispose of disposals)expect(dispose).toHaveBeenCalledTimes(1);
    expect(rendererDispose).toHaveBeenCalledTimes(1);expect(stopped).toHaveBeenCalledTimes(1);expect(started).not.toHaveBeenCalled();
  });
});
