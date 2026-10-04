import type { RuntimeSnapshot, VoiceStatus } from "./contracts";

export type VoiceAmplitudeSource = "input" | "output" | "none";

export function freshVoiceAmplitude(voice: VoiceStatus | null, nowSeconds = Date.now() / 1000): { rms: number; source: VoiceAmplitudeSource } {
  if (!Number.isFinite(nowSeconds) || !voice?.available || voice.errorCode
    || !Number.isFinite(voice.updatedAt) || voice.updatedAt <= 0
    || voice.updatedAt > nowSeconds + 1 || nowSeconds - voice.updatedAt > 3) return { rms: 0, source: "none" };
  const source = voice.state === "user_listen" ? "input" : voice.state === "speaking" ? "output" : "none";
  const value = source === "input" ? voice.rms : source === "output" ? voice.outputRms : 0;
  const rms = Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0;
  return { rms, source: rms > 0 ? source : "none" };
}

/** The status RMS belongs to microphone input, including during TTS playback. */
export function freshInputRms(snapshot: RuntimeSnapshot | null, nowSeconds = Date.now() / 1000): number {
  const voice = snapshot?.voice;
  if (!Number.isFinite(nowSeconds) || !snapshot?.available || !voice?.available || voice.errorCode
    || (voice.state !== "user_listen" && voice.state !== "wake_listen")
    || !Number.isFinite(voice.updatedAt) || voice.updatedAt <= 0
    || voice.updatedAt > nowSeconds + 1 || nowSeconds - voice.updatedAt > 3) return 0;
  return Number.isFinite(voice.rms) ? Math.max(0, Math.min(1, voice.rms)) : 0;
}
