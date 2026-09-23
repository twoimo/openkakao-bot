import { describe, expect, it, vi } from "vitest";
import { unavailableSnapshot, type CancellationToken, type RuntimeSnapshot } from "../contracts";
import { RuntimeSnapshotPoller, type PollTimerScheduler } from "../runtime-poller";

interface Deferred<T> {
  promise: Promise<T>;
  resolve(value: T): void;
  reject(reason?: unknown): void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function runtimeSnapshot(jobLoad = 0.5, voiceRms = 0.25): RuntimeSnapshot {
  const snapshot = unavailableSnapshot(null);
  return {
    ...snapshot,
    available: true,
    jobLoad,
    voice: { ...snapshot.voice, available: true, rms: voiceRms },
  };
}

async function flushMicrotasks(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}

class FakePollScheduler implements PollTimerScheduler {
  private nextId = 1;
  readonly callbacks = new Map<number, () => void>();
  readonly delays = new Map<number, number>();

  setTimeout(callback: () => void, delayMs: number): number {
    const id = this.nextId++;
    this.callbacks.set(id, callback);
    this.delays.set(id, delayMs);
    return id;
  }

  clearTimeout(timerId: number): void {
    this.callbacks.delete(timerId);
    this.delays.delete(timerId);
  }

  runNext(): void {
    const entry = this.callbacks.entries().next();
    if (entry.done) throw new Error("no_timer_scheduled");
    const [id, callback] = entry.value;
    this.callbacks.delete(id);
    this.delays.delete(id);
    callback();
  }
}

function tokenFactory(): () => CancellationToken {
  let sequence = 0;
  return () => ({ id: `token-${++sequence}`, cancelled: false });
}

describe("RuntimeSnapshotPoller", () => {
  it("keeps duplicate start calls to one request and one follow-up timer", async () => {
    const request = deferred<RuntimeSnapshot>();
    const loadSnapshot = vi.fn(() => request.promise);
    const signalSink = { setSignals: vi.fn() };
    const scheduler = new FakePollScheduler();
    const poller = new RuntimeSnapshotPoller(
      signalSink,
      loadSnapshot,
      vi.fn(async () => undefined),
      tokenFactory(),
      scheduler,
      125,
    );

    poller.start();
    poller.start();

    expect(loadSnapshot).toHaveBeenCalledTimes(1);
    expect(scheduler.callbacks.size).toBe(0);

    request.resolve(runtimeSnapshot());
    await flushMicrotasks();
    poller.start();

    expect(signalSink.setSignals).toHaveBeenCalledTimes(1);
    expect(loadSnapshot).toHaveBeenCalledTimes(1);
    expect(scheduler.callbacks.size).toBe(1);
    expect([...scheduler.delays.values()]).toEqual([125]);
  });

  it("cancels an in-flight token on stop and does not schedule after it settles", async () => {
    const request = deferred<RuntimeSnapshot>();
    const token: CancellationToken = { id: "in-flight", cancelled: false };
    const cancelSnapshot = vi.fn(async () => undefined);
    const signalSink = { setSignals: vi.fn() };
    const scheduler = new FakePollScheduler();
    const poller = new RuntimeSnapshotPoller(
      signalSink,
      () => request.promise,
      cancelSnapshot,
      () => token,
      scheduler,
    );

    poller.start();
    poller.stop();

    expect(cancelSnapshot).toHaveBeenCalledOnce();
    expect(cancelSnapshot).toHaveBeenCalledWith(token);
    expect(token.cancelled).toBe(true);

    request.resolve(runtimeSnapshot());
    await flushMicrotasks();

    expect(signalSink.setSignals).not.toHaveBeenCalled();
    expect(scheduler.callbacks.size).toBe(0);
  });

  it("ignores a late snapshot from an invalidated generation", async () => {
    const oldRequest = deferred<RuntimeSnapshot>();
    const currentRequest = deferred<RuntimeSnapshot>();
    const requests = [oldRequest, currentRequest];
    const loadSnapshot = vi.fn(() => {
      const request = requests.shift();
      if (!request) throw new Error("unexpected_request");
      return request.promise;
    });
    const signalSink = { setSignals: vi.fn() };
    const scheduler = new FakePollScheduler();
    const poller = new RuntimeSnapshotPoller(
      signalSink,
      loadSnapshot,
      vi.fn(async () => undefined),
      tokenFactory(),
      scheduler,
    );

    poller.start();
    poller.stop();
    poller.start();

    oldRequest.resolve(runtimeSnapshot(0.2, 0.1));
    await flushMicrotasks();
    expect(signalSink.setSignals).not.toHaveBeenCalled();

    currentRequest.resolve(runtimeSnapshot(0.8, 0.4));
    await flushMicrotasks();

    expect(signalSink.setSignals).toHaveBeenCalledOnce();
    expect(signalSink.setSignals).toHaveBeenCalledWith(0.8, 0.4);
    expect(scheduler.callbacks.size).toBe(1);
  });

  it("contains loader and signal sink failures while polling remains usable", async () => {
    const loadSnapshot = vi.fn()
      .mockRejectedValueOnce(new Error("snapshot_failed"))
      .mockResolvedValue(runtimeSnapshot(0.7, 0.35));
    const signalSink = { setSignals: vi.fn(() => { throw new Error("render_failed"); }) };
    const scheduler = new FakePollScheduler();
    const poller = new RuntimeSnapshotPoller(
      signalSink,
      loadSnapshot,
      vi.fn(async () => undefined),
      tokenFactory(),
      scheduler,
      75,
    );

    poller.start();
    await flushMicrotasks();

    expect(signalSink.setSignals).toHaveBeenNthCalledWith(1, 0, 0);
    expect(scheduler.callbacks.size).toBe(1);

    scheduler.runNext();
    await flushMicrotasks();

    expect(loadSnapshot).toHaveBeenCalledTimes(2);
    expect(signalSink.setSignals).toHaveBeenNthCalledWith(2, 0.7, 0.35);
    expect(scheduler.callbacks.size).toBe(1);
    expect([...scheduler.delays.values()]).toEqual([75]);
  });
});
