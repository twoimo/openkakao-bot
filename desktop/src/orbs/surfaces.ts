import { AnimationLoop } from '../core/animation-loop';
import type { EmergencyState, RuntimeSnapshot, VoiceStatus } from '../contracts';
import { databaseOrb, runtimeOrb, voiceOrb, PAUSED_ORB, QUIET_ORB, type OrbActivity } from './activity';
import { orbFrames, orbPose, orbCacheBytes, type OrbFrames, type PackedOrbFrame, type OrbState } from './frames';
const ACTIVITY_LABELS: Record<OrbState, string> = { working:'작업 중', searching:'기억 검색 중', solving:'답변 생각 중', listening:'말씀 듣는 중', connecting:'대화 수집 중', weaving:'지식 연결 중', composing:'답변 준비 중', breathing:'대기 중', shaping:'말씀 정리 중' };

class OrbCanvas {
  private readonly ctx: CanvasRenderingContext2D | null;
  private readonly palette = Array.from({ length: 256 }, (_, i) => `rgb(${Math.round(221*i/255)},${Math.round(231*i/255)},${Math.round(239*i/255)})`);
  private readonly size: 20 | 64;
  private readonly pixels: number;
  private readonly dpr = Math.min(window.devicePixelRatio || 1, 2);
  private state: OrbState = 'solving';
  private bank: OrbFrames | null = null;
  private elapsed = 0;
  private frame: PackedOrbFrame | null = null;
  active = false;

  constructor(readonly canvas: HTMLCanvasElement) {
    this.pixels = Number(canvas.dataset.orbSize) || 20;
    this.size = this.pixels > 24 ? 64 : 20;
    canvas.width = Math.round(this.pixels * this.dpr); canvas.height = Math.round(this.pixels * this.dpr);
    this.ctx = canvas.getContext('2d');
    this.paint(orbPose(this.state, this.size));
  }
  apply(activity: OrbActivity, visible: boolean, reduced: boolean): void {
    if (this.canvas.title !== activity.label) this.canvas.title = activity.label;
    if (this.canvas.dataset.orbState !== activity.state) this.canvas.dataset.orbState = activity.state;
    const active = activity.active && visible && !reduced && this.canvas.getClientRects().length > 0;
    if (this.state === activity.state && this.active === active) return;
    this.state = activity.state; this.active = active; this.elapsed = 0;
    this.bank = active ? orbFrames(this.state, this.size) : null;
    if (visible) this.paint(orbPose(this.state, this.size));
  }
  advance(dt: number): void {
    if (!this.active || !this.bank) return;
    this.elapsed += dt; this.paint(this.bank.at(this.elapsed));
  }
  private paint(frame: PackedOrbFrame): void {
    if (!this.ctx || frame === this.frame) return;
    this.frame = frame;
    const ctx = this.ctx, dots = frame.dots, lines = frame.lines;
    ctx.setTransform(this.pixels / this.size * this.dpr, 0, 0, this.pixels / this.size * this.dpr, 0, 0);
    ctx.clearRect(0, 0, this.size, this.size);
    for (let i = 0; i < lines.length; i += 7) {
      ctx.strokeStyle = this.palette[Math.max(0, Math.min(255, Math.round(lines[i + 4] * 255)))];
      ctx.globalAlpha = lines[i + 5]; ctx.lineWidth = lines[i + 6];
      ctx.beginPath(); ctx.moveTo(lines[i], lines[i + 1]); ctx.lineTo(lines[i + 2], lines[i + 3]); ctx.stroke();
    }
    for (let i = 0; i < dots.length; i += 5) {
      ctx.fillStyle = this.palette[Math.max(0, Math.min(255, Math.round(dots[i + 3] * 255)))];
      ctx.globalAlpha = dots[i + 4]; ctx.beginPath(); ctx.arc(dots[i], dots[i + 1], dots[i + 2], 0, Math.PI * 2); ctx.fill();
    }
    ctx.globalAlpha = 1;
  }
}

/** All UI orbs share one capped loop; no per-orb RAF, status poll or observer. */
export class OrbSurfaces {
  private readonly orbs: OrbCanvas[];
  private readonly caption: HTMLElement | null;
  private readonly idleCaption: string;
  private readonly loop = new AnimationLoop(dt => this.advance(dt));
  private readonly motion = window.matchMedia('(prefers-reduced-motion: reduce)');
  private snapshot: RuntimeSnapshot | null = null;
  private voice: VoiceStatus | null = null;
  private paused = false;
  private visible = false;
  private disposed = false;
  constructor(root: Document = document) {
    this.caption = root.getElementById('orb-activity'); this.idleCaption = this.caption?.textContent ?? '';
    this.orbs = [...root.querySelectorAll<HTMLCanvasElement>('[data-orb-role]')].map(canvas => new OrbCanvas(canvas));
    this.motion.addEventListener('change', this.refresh);
  }
  get renderCount(): number { return this.loop.renderCount; }
  setSnapshot(snapshot: RuntimeSnapshot | null): void { this.snapshot = snapshot; this.voice = snapshot?.voice ?? null; this.refresh(); }
  setVoice(voice: VoiceStatus | null): void { this.voice = voice; this.refresh(); }
  setEmergency(state: EmergencyState): void { this.paused = state.latched; this.refresh(); }
  start(): void { if (!this.disposed) { this.visible = true; this.refresh(); } }
  stop(): void { this.visible = false; this.loop.stop(); for (const orb of this.orbs) orb.active = false; }
  dispose(): void { if (this.disposed) return; this.stop(); this.disposed = true; this.motion.removeEventListener('change', this.refresh); }
  diagnostics(): { running: boolean; pendingFrame: boolean; renderCount: number; lastRenderAtMs: number | null; animated: number; cacheBytes: number } {
    return { ...this.loop.diagnostics(), animated: this.orbs.filter(orb => orb.active).length, cacheBytes: orbCacheBytes() };
  }
  readonly refresh = (): void => {
    if (this.disposed) return;
    const activity = this.paused ? PAUSED_ORB : runtimeOrb(this.snapshot, this.voice);
    const label = this.paused ? PAUSED_ORB.label : activity.active ? activity.label.includes('소식') ? '새 소식 전송 중' : activity.label.includes('들려') ? '말씀드리는 중' : ACTIVITY_LABELS[activity.state] : this.idleCaption;
    if (this.caption && this.caption.textContent !== label) this.caption.textContent = label;
    for (const orb of this.orbs) {
      const role = orb.canvas.dataset.orbRole;
      const signal = this.paused ? PAUSED_ORB : role === 'voice' ? voiceOrb(this.voice) : role === 'database' ? databaseOrb(this.snapshot) : activity;
      orb.apply(this.snapshot ? signal : QUIET_ORB, this.visible, this.motion.matches);
    }
    if (this.visible && this.orbs.some(orb => orb.active)) { this.loop.setVoiceActive(true); this.loop.start(); }
    else this.loop.stop();
  };
  private advance(dt: number): void {
    // Re-evaluate the timestamp each frame so frozen native voice status cannot animate forever.
    if (this.voice?.available && Date.now() / 1000 - this.voice.updatedAt > 3) { this.voice = null; this.refresh(); if (!this.loop.isRunning()) return; }
    for (const orb of this.orbs) orb.advance(dt);
  }
}
