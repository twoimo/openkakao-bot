import * as THREE from "three";
import { describe, expect, it, vi } from "vitest";
import { unavailableSnapshot, type RuntimeSnapshot } from "../contracts";
import { KnowledgeHologram } from "../knowledge/hologram";
import { VoiceEnvelope } from "../knowledge/voice-envelope";
import { freshInputRms } from "../voice-amplitude";

function snapshot(patch: Partial<RuntimeSnapshot["voice"]> = {}): RuntimeSnapshot {
  const base = unavailableSnapshot();
  return { ...base, available: true, voice: { ...base.voice, available: true,
    state: "user_listen", rms: 0.25, updatedAt: 100, ...patch } };
}

describe("sampled input amplitude", () => {
  it("accepts recent measured input and bounds it", () => {
    expect(freshInputRms(snapshot(), 102)).toBe(0.25);
    expect(freshInputRms(snapshot({ rms: 5 }), 102)).toBe(1);
    expect(freshInputRms(snapshot({ rms: -1 }), 102)).toBe(0);
  });

  it("rejects unavailable, stale, future and invalid measurements", () => {
    for (const patch of [{ available: false }, { updatedAt: 98 }, { updatedAt: 104 },
      { updatedAt: 0 }, { rms: Number.NaN }, { errorCode: "mic_disconnected" }]) {
      expect(freshInputRms(snapshot(patch), 102)).toBe(0);
    }
    expect(freshInputRms(snapshot(), Number.NaN)).toBe(0);
    expect(freshInputRms(unavailableSnapshot(), 102)).toBe(0);
  });

  it("never labels stored microphone RMS as playback or inference amplitude", () => {
    for (const state of ["speaking", "generating", "transcribing", "aborted", "error", "ended"]) {
      expect(freshInputRms(snapshot({ state }), 102)).toBe(0);
    }
    expect(freshInputRms(snapshot({ state: "wake_listen" }), 102)).toBe(0.25);
  });
});

describe("bounded amplitude envelope", () => {
  it("remains invisible for silence and reuses its fixed geometry through updates", () => {
    const envelope = new VoiceEnvelope();
    const geometry = envelope.line.geometry;
    const buffer = geometry.getAttribute("position").array;
    expect(envelope.line.visible).toBe(false);
    expect(envelope.needsFrame).toBe(false);
    envelope.setRms(0.25);
    envelope.advance(1);
    const lowOpacity = envelope.line.material.opacity;
    envelope.setRms(1);
    envelope.advance(1);
    expect(envelope.line.visible).toBe(true);
    expect(envelope.line.material.opacity).toBeGreaterThan(lowOpacity);
    expect(envelope.line.material.opacity).toBeLessThanOrEqual(0.36);
    expect(envelope.line.geometry).toBe(geometry);
    expect(geometry.getAttribute("position").count).toBe(128);
    expect(geometry.getAttribute("position").array).toBe(buffer);
    for (let i = 0; i < buffer.length; i += 3) {
      expect(Math.hypot(buffer[i], buffer[i + 1], buffer[i + 2])).toBeLessThan(2.2);
    }
    expect(envelope.line.userData.nodeId).toBeUndefined();
    geometry.dispose();envelope.line.material.dispose();
  });

  it("uses elapsed time consistently and settles without ongoing oscillation", () => {
    const once = new VoiceEnvelope();const partitioned = new VoiceEnvelope();
    once.setRms(0.8);partitioned.setRms(0.8);
    once.advance(0.24);
    for (let i = 0; i < 6; i++) partitioned.advance(0.04);
    expect(once.displayedRms).toBeCloseTo(partitioned.displayedRms, 10);
    for (let i = 0; i < 20; i++) partitioned.advance(0.1);
    expect(partitioned.needsFrame).toBe(false);
    expect(partitioned.displayedRms).toBe(0.8);
    partitioned.setRms(0);
    for (let i = 0; i < 20; i++) partitioned.advance(0.1);
    expect(partitioned.needsFrame).toBe(false);
    expect(partitioned.line.visible).toBe(false);
    expect(partitioned.line.material.opacity).toBe(0);
    for (const envelope of [once, partitioned]) { envelope.line.geometry.dispose();envelope.line.material.dispose(); }
  });

  it("cannot restart a hidden or disposed graph from signals", () => {
    const envelope = new VoiceEnvelope();const start = vi.fn();
    const graph = Object.create(KnowledgeHologram.prototype) as KnowledgeHologram;
    Object.defineProperties(graph, {
      disposed: { value: false, writable: true }, requestedAnimation: { value: false, writable: true },
      graphRoot: { value: new THREE.Group() }, voiceEnvelope: { value: envelope },
      synapses: { value: {setVisualMotion:vi.fn()} },
      view: { value: { nodes: [{ id: "a" }] } }, loop: { value: { start, stop: vi.fn(), setLoad: vi.fn(), setVoiceActive: vi.fn() } },
    });
    graph.setSignals(1, 0.5);expect(start).not.toHaveBeenCalled();
    graph.stop();expect(envelope.targetRms).toBe(0);expect(envelope.line.visible).toBe(false);
    Object.defineProperty(graph, "disposed", { value: true });
    graph.setSignals(1, 1);expect(start).not.toHaveBeenCalled();expect(envelope.targetRms).toBe(0);
    envelope.line.geometry.dispose();envelope.line.material.dispose();
  });
});
