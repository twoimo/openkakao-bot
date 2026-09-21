import { invoke } from "@tauri-apps/api/core";
import type { CancellationToken, RuntimeSnapshot } from "./contracts";
import { parseRuntimeSnapshot, unavailableSnapshot } from "./contracts";
import { RESIDENT_MODEL_ID, SWAP_MODEL_ID } from "./tokens";

type SettingsAction =
  | "models"
  | "model-owner-status"
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

export type BrowserToolStatus = "completed" | "aborted" | "rejected" | "failed";

export interface BrowserToolInput {
  jobId: string;
  task: string;
}

export interface BrowserToolResult {
  ok: boolean;
  status: BrowserToolStatus;
  errorCode: string;
  result: string;
}

const BROWSER_TOOL_JOB_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const BROWSER_TOOL_TASK_LIMIT_BYTES = 16 * 1024;
const BROWSER_TOOL_RESULT_LIMIT_BYTES = 64 * 1024;
const BROWSER_TOOL_STATUSES = new Set<BrowserToolStatus>([
  "completed", "aborted", "rejected", "failed",
]);
const BROWSER_TOOL_ERROR_CODES = new Set([
  "global_abort", "state_root_invalid", "job_id_invalid", "browser_task_invalid",
  "browser_task_too_large", "browser_runtime_unavailable", "browser_job_failed",
  "browser_result_invalid", "browser_result_too_large",
]);

function failedBrowserTool(errorCode: string, status: BrowserToolStatus = "failed"): BrowserToolResult {
  return { ok: false, status, errorCode, result: "" };
}

function validBrowserToolInput(input: BrowserToolInput, token: CancellationToken): boolean {
  return BROWSER_TOOL_JOB_ID.test(input.jobId)
    && BROWSER_TOOL_JOB_ID.test(token.id)
    && input.task.trim().length > 0
    && !input.task.includes("\0")
    && new TextEncoder().encode(input.task).byteLength <= BROWSER_TOOL_TASK_LIMIT_BYTES;
}

function parseBrowserToolResult(value: unknown): BrowserToolResult {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return failedBrowserTool("browser_bridge_unavailable");
  }
  const record = value as Record<string, unknown>;
  const keys = Object.keys(record).sort();
  if (keys.join(",") !== "errorCode,ok,result,status") {
    return failedBrowserTool("browser_bridge_unavailable");
  }
  const status = typeof record.status === "string" && BROWSER_TOOL_STATUSES.has(record.status as BrowserToolStatus)
    ? record.status as BrowserToolStatus
    : null;
  const errorCode = typeof record.errorCode === "string" ? record.errorCode : "";
  const result = typeof record.result === "string" ? record.result : null;
  if (!status || result === null || new TextEncoder().encode(result).byteLength > BROWSER_TOOL_RESULT_LIMIT_BYTES) {
    return failedBrowserTool("browser_result_invalid");
  }
  if (record.ok === true && status === "completed" && errorCode === "") {
    return { ok: true, status, errorCode, result };
  }
  if (record.ok !== false || !BROWSER_TOOL_ERROR_CODES.has(errorCode) || result !== "") {
    return failedBrowserTool("browser_bridge_unavailable");
  }
  return { ok: false, status, errorCode, result: "" };
}

export async function runBrowserTool(
  input: BrowserToolInput,
  token: CancellationToken,
  invokeFn: SettingsInvoke = invoke,
): Promise<BrowserToolResult> {
  if (token.cancelled) return failedBrowserTool("browser_request_cancelled", "aborted");
  if (!validBrowserToolInput(input, token)) return failedBrowserTool("browser_input_invalid", "rejected");
  try {
    const value = await invokeFn<unknown>("run_browser_tool", {
      jobId: input.jobId,
      task: input.task,
      tokenId: token.id,
    });
    if (token.cancelled) return failedBrowserTool("browser_request_cancelled", "aborted");
    return parseBrowserToolResult(value);
  } catch {
    return failedBrowserTool("browser_bridge_unavailable");
  }
}

type ModelAction = "model-set" | "model-prepare";

export interface ModelActionResult {
  ok: boolean;
  action: ModelAction;
  model: LocalModelId;
  stored: boolean;
  prepared: boolean;
  needsPrepare: boolean;
}

export interface ModelSwapActionResult {
  ok: boolean;
  action: "model-swap";
  model: typeof SWAP_MODEL_ID;
  stage: string;
  reason: string;
  stages: string[];
  stored: boolean;
  prepared: boolean;
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

const MODEL_SWAP_STAGES = new Set([
  "idle", "drain", "unload", "memory_check", "load", "probe", "rollback", "ready", "aborted", "failed",
]);
const MODEL_SWAP_REASONS = new Set([
  "ready", "already_resident", "cancelled", "drain_timeout", "explicit_opt_in_required",
  "model_not_allowed", "model_owner_unknown", "model_owner_unmanaged", "model_owner_state_invalid", "model_owner_state_stale",
  "model_gateway_unavailable", "model_residency_mismatch", "model_drain_unverified",
  "memory_budget_unavailable", "insufficient_free_memory", "unload_failed", "load_failed", "probe_failed",
  "load_failed_rollback_failed", "probe_failed_rollback_failed", "cancelled_rollback_failed",
  "model_state_write_failed", "model_state_write_failed_rollback_failed", "model_override_write_failed",
  "model_override_write_failed_rollback_failed",
  "model_swap_busy", "model_residency_uncertain",
  "memory_budget_unavailable_rollback_failed", "insufficient_free_memory_rollback_failed",
]);

function failedModelSwap(reason = "model_owner_unknown", stage = "failed"): ModelSwapActionResult {
  return {
    ok: false,
    action: "model-swap",
    model: SWAP_MODEL_ID,
    stage,
    reason,
    stages: [],
    stored: false,
    prepared: false,
  };
}

function parseModelSwapAction(value: unknown): ModelSwapActionResult {
  if (!value || typeof value !== "object" || Array.isArray(value)) return failedModelSwap();
  const record = value as Record<string, unknown>;
  if (record.action !== "model-swap" || record.model !== SWAP_MODEL_ID) return failedModelSwap();
  const stage = typeof record.stage === "string" && MODEL_SWAP_STAGES.has(record.stage)
    ? record.stage
    : "failed";
  const reason = typeof record.reason === "string" && MODEL_SWAP_REASONS.has(record.reason)
    ? record.reason
    : "model_owner_unknown";
  const stages = Array.isArray(record.stages)
    ? record.stages.filter((item): item is string => typeof item === "string" && MODEL_SWAP_STAGES.has(item)).slice(0, 12)
    : [];
  const stored = record.stored === true;
  const prepared = record.prepared === true;
  return {
    ok: record.ok === true && stage === "ready" && stored && prepared
      && (reason === "ready" || reason === "already_resident"),
    action: "model-swap",
    model: SWAP_MODEL_ID,
    stage,
    reason,
    stages,
    stored,
    prepared,
  };
}

export async function swapToLargeModel(
  token: CancellationToken,
  invokeFn: SettingsInvoke = invoke,
): Promise<ModelSwapActionResult> {
  if (token.cancelled) return failedModelSwap("cancelled", "aborted");
  try {
    const value = await invokeFn<unknown>("fetch_settings_action", {
      action: "model-swap",
      model: SWAP_MODEL_ID,
      explicitOptIn: true,
      tokenId: token.id,
    });
    // The backend outcome includes rollback (or a commit that won the race).
    // A cancellation request alone cannot establish either result.
    return parseModelSwapAction(value);
  } catch {
    return failedModelSwap("model_residency_uncertain");
  }
}

export async function cancelModelSwap(
  token: CancellationToken,
  invokeFn: SettingsInvoke = invoke,
): Promise<void> {
  if (token.cancelled) return;
  try {
    const accepted = await invokeFn<unknown>("cancel_model_swap", { tokenId: token.id });
    if (accepted === true) token.cancelled = true;
  } catch {
    // Leave the request retryable; keep waiting for the authoritative outcome.
  }
}
