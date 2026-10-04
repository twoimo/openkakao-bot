import type { RuntimeSnapshot, VoiceStatus } from '../contracts';
import type { OrbState } from './frames';

export interface OrbActivity { state: OrbState; active: boolean; label: string }
export const QUIET_ORB: OrbActivity = Object.freeze({ state: 'solving', active: false, label: '대기 중' });
export const PAUSED_ORB: OrbActivity = Object.freeze({ state: 'breathing', active: false, label: '일시 중지됨' });

export function voiceOrb(voice: VoiceStatus | null, now = Date.now() / 1000): OrbActivity {
  if (!voice?.available || voice.errorCode || !Number.isFinite(now) || !Number.isFinite(voice.updatedAt)
    || voice.updatedAt <= 0 || voice.updatedAt > now + 1 || now - voice.updatedAt > 3) return QUIET_ORB;
  switch (voice.state) {
    case 'user_listen': return { state: 'listening', active: true, label: '말씀을 듣고 있습니다' };
    case 'stt': case 'transcribing': return { state: 'shaping', active: true, label: '말씀을 정리하고 있습니다' };
    case 'context': case 'retrieving': return { state: 'searching', active: true, label: '기억을 찾고 있습니다' };
    case 'llm': case 'thinking': case 'generating': return { state: 'solving', active: true, label: '답변을 생각하고 있습니다' };
    case 'tts': case 'speaking': return { state: 'composing', active: true, label: '답변을 들려드리고 있습니다' };
    default: return QUIET_ORB;
  }
}

export function databaseOrb(snapshot: RuntimeSnapshot | null): OrbActivity {
  if (!snapshot?.available || snapshot.errorCode) return QUIET_ORB;
  const job = snapshot.jobs.find(job => !job.errorCode && job.load > 0 && /db|graph|knowledge|index|sync/.test(job.kind));
  if (job && /graph|index|embed/.test(job.stage + job.kind)) return { state: 'weaving', active: true, label: '지식으로 연결하고 있습니다' };
  if (snapshot.background.dbSync.state === 'syncing' || job) return { state: 'connecting', active: true, label: '대화를 가져오고 있습니다' };
  return QUIET_ORB;
}

export function runtimeOrb(snapshot: RuntimeSnapshot | null, voice: VoiceStatus | null = snapshot?.voice ?? null): OrbActivity {
  if (!snapshot?.available || snapshot.errorCode) return QUIET_ORB;
  const speaking = voiceOrb(voice);
  if (speaking.active) return speaking;
  if (snapshot.pipeline.active) {
    const stage = snapshot.pipeline.stage;
    if (stage === 'context') return { state: 'searching', active: true, label: '기억을 찾고 있습니다' };
    if (stage === 'model') return { state: 'solving', active: true, label: '답변을 생각하고 있습니다' };
    if (stage === 'send' || stage === 'confirm') return { state: 'composing', active: true, label: '답변을 전달하고 있습니다' };
    return { state: 'working', active: true, label: '대화를 처리하고 있습니다' };
  }
  const db = databaseOrb(snapshot);
  if (db.active) return db;
  if (snapshot.background.geeknews.state === 'sending') return { state: 'composing', active: true, label: '새 소식을 전달하고 있습니다' };
  if (snapshot.jobs.some(job => !job.errorCode && Number.isFinite(job.load) && job.load > 0)) return { state: 'working', active: true, label: '작업 중' };
  return QUIET_ORB;
}
