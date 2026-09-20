import { invoke } from "@tauri-apps/api/core";
import type { CancellationToken, RuntimeSnapshot } from "./contracts";
import { parseRuntimeSnapshot, unavailableSnapshot } from "./contracts";
import { RESIDENT_MODEL_ID, SWAP_MODEL_ID } from "./tokens";

type SettingsAction =
  | "models"
  | "dream-rsi-status"
  | "knowledge-graph-status"
  | "knowledge-graph"
  | "knowledge-graph-focus";

export interface SettingsActionInput {
  query?: string;
  nodeId?: string;
  chatId?: string;
}

export async function fetchRuntimeSnapshot(token: CancellationToken): Promise<RuntimeSnapshot> {
  if (token.cancelled) return unavailableSnapshot("cancelled");
  try {
    const value = await invoke<unknown>("fetch_runtime_snapshot", { tokenId: token.id });
    if (token.cancelled) return unavailableSnapshot("cancelled");
    return parseRuntimeSnapshot(value);
  } catch {
    return unavailableSnapshot("snapshot_unavailable");
  }
}

export async function cancelRuntimeRequest(token: CancellationToken): Promise<void> {
  if (token.cancelled) return;
  token.cancelled = true;
  try {
    await invoke("cancel_python", { tokenId: token.id });
  } catch {
    // Cancellation is best-effort and the Rust runner also enforces its timeout.
  }
}

export async function fetchSettingsAction(
  action: SettingsAction,
  input: SettingsActionInput = {},
): Promise<Record<string, unknown> | null> {
  try {
    return await invoke<Record<string, unknown>>("fetch_settings_action", {
      action,
      query: input.query,
      nodeId: input.nodeId,
      chatId: input.chatId,
    });
  } catch {
    return null;
  }
}

export type LocalModelId = typeof RESIDENT_MODEL_ID | typeof SWAP_MODEL_ID;
export type SettingsInvoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

type ModelAction = "model-set" | "model-prepare";

export interface ModelActionResult {
  ok: boolean;
  action: ModelAction;
  model: LocalModelId;
  stored: boolean;
  prepared: boolean;
  needsPrepare: boolean;
}

function failedModelAction(action: ModelAction, model: LocalModelId): ModelActionResult {
  return { ok: false, action, model, stored: false, prepared: false, needsPrepare: false };
}

function parseModelAction(
  value: unknown,
  action: ModelAction,
  model: LocalModelId,
): ModelActionResult {
  if (!value || typeof value !== "object" || Array.isArray(value)) return failedModelAction(action, model);
  const record = value as Record<string, unknown>;
  const contractMatches = record.ok === true && record.action === action && record.model === model;
  const stored = record.stored === true;
  const prepared = record.prepared === true;
  const needsPrepare = record.needs_prepare === true;
  const completed = action === "model-set" ? stored : prepared;
  if (!contractMatches || !completed) return failedModelAction(action, model);
  return { ok: true, action, model, stored, prepared, needsPrepare };
}

async function invokeLocalModelAction(
  action: ModelAction,
  model: LocalModelId,
  invokeFn: SettingsInvoke,
): Promise<ModelActionResult> {
  try {
    const value = await invokeFn<unknown>("fetch_settings_action", { action, model });
    return parseModelAction(value, action, model);
  } catch {
    return failedModelAction(action, model);
  }
}

export async function setResidentModel(invokeFn: SettingsInvoke = invoke): Promise<ModelActionResult> {
  return invokeLocalModelAction("model-set", RESIDENT_MODEL_ID, invokeFn);
}

export async function prepareSwapModel(invokeFn: SettingsInvoke = invoke): Promise<ModelActionResult> {
  return invokeLocalModelAction("model-prepare", SWAP_MODEL_ID, invokeFn);
}
