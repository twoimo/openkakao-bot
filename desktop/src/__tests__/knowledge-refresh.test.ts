import { afterEach, describe, expect, it, vi } from "vitest";
import { KnowledgeRefresh } from "../knowledge/refresh";
afterEach(() => vi.useRealTimers());
describe("local knowledge refresh", () => {
  it('probes metadata once per second and reads only on revision changes or fallback', async () => {
    vi.useFakeTimers();
    let revision = 'first';
    const probe = vi.fn(async () => revision), read = vi.fn(async () => ({ok:true})), apply = vi.fn();
    const refresh = new KnowledgeRefresh(read, apply, 15000, probe);
    refresh.start(); await vi.advanceTimersByTimeAsync(5000);
    expect(probe).toHaveBeenCalledTimes(6); expect(read).toHaveBeenCalledTimes(1);
    revision = 'replacement'; await vi.advanceTimersByTimeAsync(1000);
    expect(read).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(15000); expect(read).toHaveBeenCalledTimes(3);
    refresh.stop(); const probes=probe.mock.calls.length;
    await vi.advanceTimersByTimeAsync(20000); expect(probe).toHaveBeenCalledTimes(probes);
    expect(vi.getTimerCount()).toBe(0);
  });
  it('discards a hidden metadata response before starting a full graph read', async () => {
    vi.useFakeTimers();
    let resolve!: (revision:string) => void;
    const probe=vi.fn(()=>new Promise<string>(done=>{resolve=done;})),read=vi.fn(async()=>({ok:true}));
    const refresh=new KnowledgeRefresh(read,vi.fn(),15000,probe);
    refresh.start(); await vi.advanceTimersByTimeAsync(0); refresh.stop();
    resolve('late'); await Promise.resolve(); await Promise.resolve();
    expect(read).not.toHaveBeenCalled(); expect(vi.getTimerCount()).toBe(0);
  });
  it('backs off failed changed revisions and recovers from unavailable metadata', async () => {
    vi.useFakeTimers();
    const probe=vi.fn().mockRejectedValueOnce(new Error('missing')).mockResolvedValue('first');
    const read=vi.fn().mockResolvedValueOnce(null).mockResolvedValue({ok:true}),apply=vi.fn();
    const refresh=new KnowledgeRefresh(read,apply,15000,probe);
    refresh.start(); await vi.advanceTimersByTimeAsync(1000); expect(read).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1000); expect(read).toHaveBeenCalledTimes(2); expect(apply).toHaveBeenCalledTimes(1);
    refresh.stop();
  });
  it("has one timer and stops all future reads when hidden", async () => {
    vi.useFakeTimers();
    const read = vi.fn(async () => ({ ok: true })), apply = vi.fn();
    const refresh = new KnowledgeRefresh(read, apply, 100);
    refresh.start(); refresh.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(read).toHaveBeenCalledTimes(1); expect(apply).toHaveBeenCalledTimes(1);
    refresh.stop(); await vi.advanceTimersByTimeAsync(1000);
    expect(read).toHaveBeenCalledTimes(1); expect(vi.getTimerCount()).toBe(0);
  });
  it("discards a response from before hide and resumes without overlapping", async () => {
    vi.useFakeTimers();
    let resolve!: (payload: Record<string, unknown>) => void;
    const read = vi.fn(() => new Promise<Record<string, unknown>>(done => { resolve = done; }));
    const apply = vi.fn(), refresh = new KnowledgeRefresh(read, apply, 100);
    refresh.start(); await vi.advanceTimersByTimeAsync(0);
    refresh.stop(); refresh.start(); await vi.advanceTimersByTimeAsync(0);
    expect(read).toHaveBeenCalledTimes(1);
    resolve({ version: "old" }); await Promise.resolve();
    expect(apply).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(100); expect(read).toHaveBeenCalledTimes(2);
    resolve({ version: "new" }); await Promise.resolve();
    expect(apply).toHaveBeenCalledWith({ version: "new" });
    refresh.stop();
  });
  it("recovers from unavailable reads without discarding the confirmed graph", async () => {
    vi.useFakeTimers();
    const read = vi.fn().mockRejectedValueOnce(new Error("unavailable")).mockResolvedValueOnce({ ok: true });
    const apply = vi.fn(), refresh = new KnowledgeRefresh(read, apply, 100);
    refresh.start(); await vi.advanceTimersByTimeAsync(0); expect(apply).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(100); expect(apply).toHaveBeenCalledWith({ ok: true });
    refresh.stop();
  });
});
