import { describe, expect, it, vi } from 'vitest';
import { MODE_FRAMES, resolvePreset, type OrbState } from 'thinking-orbs/engine';
import { OrbFrames, orbCacheBytes, orbFrames, orbPose } from '../orbs/frames';
import { runtimeOrb, voiceOrb } from '../orbs/activity';
import { parseVoiceStatus, unavailableSnapshot } from '../contracts';
import { VoiceAmplitudePoller } from '../voice-amplitude-poller';

describe('official Thinking Orbs integration', () => {
  it('preserves upstream geometry and bounds every shipped state and size', () => {
    const states: OrbState[] = ['working','searching','solving','listening','connecting','weaving','composing','breathing','shaping'];
    for (const state of states) for (const size of [20,64] as const) {
      const preset = resolvePreset(state,size);
      const original = MODE_FRAMES[preset.mode](size,.6*preset.speed,preset.opts);
      const pose = orbPose(state,size);
      expect(pose.dots.length).toBe(original.dots.length*5);
      expect(pose.lines.length).toBe(original.lines.length*7);
      for (let i=0;i<original.dots.length;i++) {
        const dot=original.dots[i];
        expect(pose.dots[i*5]).toBeCloseTo(dot.x,4);
        expect(pose.dots[i*5+2]).toBeCloseTo(dot.r,4);
        expect(pose.dots[i*5+3]).toBeCloseTo(1-dot.white,4);
      }
      const bank=orbFrames(state,size);
      for (let frame=0;frame<96;frame++) {
        const packed=bank.at(frame/30);
        expect(packed.dots.length).toBeLessThanOrEqual(1024*5);
        expect(packed.lines.length).toBeLessThanOrEqual(2048*7);
        expect(packed.dots.every(Number.isFinite)).toBe(true);
      }
    }
    expect(orbCacheBytes()).toBeLessThan(12*1024*1024);
  });
  it('reuses frames and reverses continuously without rebuilding at a loop boundary', () => {
    const bank = new OrbFrames('searching',64);
    expect(bank.at(95/30)).toBe(bank.at(95/30));
    expect(bank.at(96/30)).toBe(bank.at(94/30));
    expect(bank.at(190/30)).toBe(bank.at(0));
  });
  it('never equates idle, unavailable or stale voice with real work', () => {
    const snapshot={...unavailableSnapshot(),available:true,errorCode:null};
    for (const state of ['idle','wake_listen','ended','aborted','unknown']) {
      expect(voiceOrb(parseVoiceStatus({available:true,state,updated_at:100}),100).active).toBe(false);
    }
    expect(voiceOrb(parseVoiceStatus({available:true,state:'generating',updated_at:100}),104).active).toBe(false);
    expect(voiceOrb(parseVoiceStatus({available:true,state:'generating',updated_at:100}),100).state).toBe('solving');
    expect(runtimeOrb(snapshot).active).toBe(false);
    expect(runtimeOrb({...snapshot,pipeline:{...snapshot.pipeline,active:true,stage:'context'}}).state).toBe('searching');
  });
  it('shares the existing fenced voice read and rejects callbacks after hiding', async () => {
    let finish:(value: ReturnType<typeof parseVoiceStatus>)=>void=()=>undefined;
    const observer=vi.fn(),apply=vi.fn();
    const load=()=>new Promise<ReturnType<typeof parseVoiceStatus>>(resolve=>{finish=resolve;});
    const poller=new VoiceAmplitudePoller(apply,load,{setTimeout:vi.fn(),clearTimeout:vi.fn()},observer);
    poller.start();poller.stop();finish(parseVoiceStatus({available:true,state:'generating',updated_at:Date.now()/1000}));
    await Promise.resolve();await Promise.resolve();
    expect(observer).not.toHaveBeenCalled();expect(apply).not.toHaveBeenCalled();
  });
});
