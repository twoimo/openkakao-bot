import { describe, expect, it, vi } from "vitest";
import { parseVoiceStatus } from "../contracts";
import { VoiceAmplitudePoller } from "../voice-amplitude-poller";
import { freshVoiceAmplitude } from "../voice-amplitude";

const voice = (state = "speaking", output = 0.25) => parseVoiceStatus({
  available: true, state, rms: 0.9, output_rms: output, updated_at: Math.floor(Date.now() / 1000),
});
const flush = async () => { await Promise.resolve();await Promise.resolve(); };

function scheduler() {
  const tasks = new Map<number, () => void>();let id = 0;
  return { tasks, setTimeout: vi.fn((fn: () => void) => { tasks.set(++id, fn);return id; }),
    clearTimeout: vi.fn((key: number) => { tasks.delete(key); }),
    run: () => { const [key, fn] = tasks.entries().next().value!;tasks.delete(key);fn(); } };
}

describe("owned amplitude status", () => {
  it("keeps microphone and rendered playback PCM distinct", () => {
    expect(freshVoiceAmplitude(voice())).toEqual({ rms: 0.25, source: "output" });
    expect(freshVoiceAmplitude(voice("user_listen"))).toEqual({ rms: 0.9, source: "input" });
    expect(freshVoiceAmplitude(parseVoiceStatus({ ...voice(), outputRms: Number.NaN }))).toEqual({ rms: 0, source: "none" });
    for (const state of ["aborted", "error", "ended", "generating", "transcribing", "wake_listen"]) {
      expect(freshVoiceAmplitude(voice(state))).toEqual({ rms: 0, source: "none" });
    }
    expect(freshVoiceAmplitude(voice(), Date.now() / 1000 + 5)).toEqual({ rms: 0, source: "none" });
    expect(freshVoiceAmplitude(parseVoiceStatus({ available: true, state: "speaking", rms: 0.9,
      updated_at: Math.floor(Date.now() / 1000) }))).toEqual({ rms: 0, source: "none" });
  });

  it("owns one status read and timer; hidden stop cannot receive its late reply", async () => {
    let resolve!: (value: ReturnType<typeof voice>) => void;
    const load = vi.fn(() => new Promise<ReturnType<typeof voice>>(r => { resolve = r; }));
    const apply = vi.fn();const timer = scheduler();const poller = new VoiceAmplitudePoller(apply, load, timer);
    poller.start();poller.start();expect(load).toHaveBeenCalledTimes(1);
    poller.stop();resolve(voice());await flush();
    expect(apply).not.toHaveBeenCalled();expect(timer.tasks.size).toBe(0);
  });

  it("discards a prior generation even after reopen", async () => {
    const resolves: Array<(value: ReturnType<typeof voice>) => void> = [];
    const load = vi.fn(() => new Promise<ReturnType<typeof voice>>(r => resolves.push(r)));
    const apply = vi.fn();const timer = scheduler();const poller = new VoiceAmplitudePoller(apply, load, timer);
    poller.start();poller.stop();poller.start();
    resolves[1](voice("speaking", 0.4));await flush();
    resolves[0](voice("speaking", 0.9));await flush();
    expect(apply).toHaveBeenCalledExactlyOnceWith(0.4, "output");expect(timer.tasks.size).toBe(1);
    poller.stop();expect(timer.tasks.size).toBe(0);
  });

  it("contains failed reads and stops all timers across repeated lifecycle changes", async () => {
    const apply = vi.fn();const timer = scheduler();
    const load = vi.fn().mockRejectedValueOnce(new Error("missing")).mockResolvedValue(voice());
    const poller = new VoiceAmplitudePoller(apply, load, timer);
    poller.start();await flush();expect(apply).toHaveBeenLastCalledWith(0, "none");
    expect(timer.setTimeout).toHaveBeenLastCalledWith(expect.any(Function), 750);
    timer.run();await flush();expect(timer.setTimeout).toHaveBeenLastCalledWith(expect.any(Function), 100);
    for (let i = 0; i < 50; i++) { poller.stop();expect(timer.tasks.size).toBe(0);poller.start();await flush();expect(timer.tasks.size).toBe(1); }
    poller.stop();expect(timer.tasks.size).toBe(0);
  });
});
