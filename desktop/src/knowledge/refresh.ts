/** One in-flight local read; visibility epochs discard late hidden responses. */
export class KnowledgeRefresh {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private active = false;
  private busy = false;
  private epoch = 0;
  private confirmedRevision: string | null = null;
  private lastRead = -Infinity;
  private retryAfter = 0;
  private needsRead = true;
  constructor(private readonly read: () => Promise<Record<string, unknown> | null>, private readonly apply: (payload: Record<string, unknown>) => void, private readonly interval = 15000,
    private readonly revision?: () => Promise<string | null>, private readonly probeInterval = 1000) {}
  start(): void {
    if (this.active) return;
    this.active = true;
    this.epoch += 1;
    this.needsRead = true;
    this.schedule(0);
  }
  stop(): void {
    this.active = false;
    this.epoch += 1;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }
  private schedule(delay: number): void {
    if (!this.active || this.timer !== null) return;
    this.timer = setTimeout(() => { this.timer = null; void this.tick(); }, delay);
  }
  private async tick(): Promise<void> {
    if (!this.active) return;
    const cadence = this.revision ? this.probeInterval : this.interval;
    if (this.busy) { this.schedule(cadence); return; }
    this.busy = true;
    const epoch = this.epoch;
    try {
      let revision: string | null = null;
      try { revision = await this.revision?.() ?? null; } catch { /* Fall back to the full read interval. */ }
      if (!this.active || epoch !== this.epoch) return;
      const now = Date.now();
      const changed = revision !== null && revision !== this.confirmedRevision;
      if (this.revision && !this.needsRead && !changed && now - this.lastRead < this.interval) return;
      if (now < this.retryAfter) return;
      this.retryAfter = now + Math.min(2000, this.interval);
      const payload = await this.read();
      if (payload && this.active && epoch === this.epoch) {
        this.apply(payload);
        if (payload.ok !== false) { this.confirmedRevision = revision; this.lastRead = Date.now(); this.needsRead = false; this.retryAfter = 0; }
      }
    } catch { /* Preserve the last confirmed graph; next bounded read can recover. */ }
    finally { this.busy = false; this.schedule(cadence); }
  }
}
