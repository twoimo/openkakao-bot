export interface JobEvent {
  jobId: string;
  kind: string;
  stage: string;
  load: number;
  time: number;
  errorCode: string | null;
}

export interface CancellationToken {
  id: string;
  cancelled: boolean;
}

export interface RoomSummary {
  chatId: number;
  title: string;
  live: boolean;
  autoReply: boolean;
  openJobs: number;
}

export interface RuntimeSnapshot {
  available: boolean;
  rooms: RoomSummary[];
  jobs: JobEvent[];
  jobLoad: number;
  terminal: {
    sent: number;
    skipped: number;
    deliveryUnknown: number;
    burstSuperseded: number;
  };
  contextSync: {
    mode: "async";
    waited: false;
  };
  replyModelId: string | null;
  voice: VoiceStatus;
  errorCode: string | null;
}

export interface VoiceStatus {
  available: boolean;
  state: string;
  rms: number;
  errorCode: string | null;
  wakeSource: "stock" | "custom" | "none";
  updatedAt: number;
}

type JsonRecord = Record<string, unknown>;
const JOB_EVENT_KEYS = new Set(["jobId", "kind", "stage", "load", "time", "errorCode"]);

function record(value: unknown): JsonRecord | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonRecord)
    : null;
}

function finiteNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function nonNegativeInt(value: unknown): number {
  return Math.max(0, Math.trunc(finiteNumber(value, 0)));
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

export function parseJobEvent(value: unknown): JobEvent | null {
  const input = record(value);
  if (!input) return null;
  if (Object.keys(input).some((key) => !JOB_EVENT_KEYS.has(key))) return null;
  if (typeof input.jobId !== "string" || typeof input.kind !== "string" || typeof input.stage !== "string") {
    return null;
  }
  if (typeof input.load !== "number" || !Number.isFinite(input.load)) return null;
  if (typeof input.time !== "number" || !Number.isFinite(input.time)) return null;
  if (input.errorCode !== null && typeof input.errorCode !== "string") return null;
  return {
    jobId: input.jobId,
    kind: input.kind,
    stage: input.stage,
    load: Math.min(1, Math.max(0, input.load)),
    time: input.time,
    errorCode: input.errorCode,
  };
}

export function serializeJobEvent(event: JobEvent): string {
  return JSON.stringify({
    jobId: event.jobId,
    kind: event.kind,
    stage: event.stage,
    load: event.load,
    time: event.time,
    errorCode: event.errorCode,
  });
}

export function unavailableSnapshot(errorCode: string | null = "snapshot_unavailable"): RuntimeSnapshot {
  return {
    available: false,
    rooms: [],
    jobs: [],
    jobLoad: 0,
    terminal: { sent: 0, skipped: 0, deliveryUnknown: 0, burstSuperseded: 0 },
    contextSync: { mode: "async", waited: false },
    replyModelId: null,
    voice: { available: false, state: "unavailable", rms: 0, errorCode: null, wakeSource: "none", updatedAt: 0 },
    errorCode,
  };
}

export function parseRuntimeSnapshot(value: unknown): RuntimeSnapshot {
  const input = record(value);
  if (!input) return unavailableSnapshot("snapshot_invalid");

  const roomsRaw = Array.isArray(input.rooms) ? input.rooms : [];
  const rooms = roomsRaw.flatMap((item): RoomSummary[] => {
    const room = record(item);
    if (!room) return [];
    const chatId = finiteNumber(room.chat_id ?? room.chatId, -1);
    if (!Number.isInteger(chatId) || chatId <= 0) return [];
    return [{
      chatId,
      title: text(room.title, `id:${chatId}`),
      live: room.live === true,
      autoReply: room.auto_reply === true || room.autoReply === true,
      openJobs: nonNegativeInt(room.open_jobs ?? room.openJobs),
    }];
  });

  const jobsRaw = Array.isArray(input.jobs) ? input.jobs : [];
  const jobs = jobsRaw.map(parseJobEvent).filter((item): item is JobEvent => item !== null);
  const terminal = record(input.terminal_counts ?? input.terminal) ?? {};
  const contextSync = record(input.context_sync ?? input.contextSync);
  const voice = record(input.voice);
  const contextValid = contextSync?.mode === "async" && contextSync.waited === false;

  return {
    available: input.available !== false && contextValid,
    rooms,
    jobs,
    jobLoad: Math.min(1, Math.max(0, finiteNumber(input.job_load ?? input.jobLoad, 0))),
    terminal: {
      sent: nonNegativeInt(terminal.sent),
      skipped: nonNegativeInt(terminal.skipped),
      deliveryUnknown: nonNegativeInt(terminal.delivery_unknown ?? terminal.deliveryUnknown),
      burstSuperseded: nonNegativeInt(terminal.burst_superseded ?? terminal.burstSuperseded),
    },
    contextSync: { mode: "async", waited: false },
    replyModelId: typeof input.reply_model_id === "string"
      ? input.reply_model_id
      : typeof input.replyModelId === "string"
        ? input.replyModelId
        : null,
    voice: {
      available: voice?.available === true,
      state: text(voice?.state, "unavailable"),
      rms: Math.min(1, Math.max(0, finiteNumber(voice?.rms, 0))),
      errorCode: typeof voice?.error_code === "string"
        ? voice.error_code
        : typeof voice?.errorCode === "string"
          ? voice.errorCode
          : null,
      wakeSource: voice?.wake_source === "stock" || voice?.wakeSource === "stock"
        ? "stock"
        : voice?.wake_source === "custom" || voice?.wakeSource === "custom"
          ? "custom"
          : "none",
      updatedAt: nonNegativeInt(voice?.updated_at ?? voice?.updatedAt),
    },
    errorCode: contextValid ? (typeof input.error_code === "string" ? input.error_code : null) : "context_sync_invalid",
  };
}

export function parseRuntimeSnapshotJson(json: string): RuntimeSnapshot {
  try {
    return parseRuntimeSnapshot(JSON.parse(json) as unknown);
  } catch {
    return unavailableSnapshot("snapshot_json_invalid");
  }
}

export function createCancellationToken(): CancellationToken {
  return { id: crypto.randomUUID(), cancelled: false };
}
