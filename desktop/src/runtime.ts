import { invoke } from "@tauri-apps/api/core";
import type { CancellationToken, RuntimeSnapshot } from "./contracts";
import { parseRuntimeSnapshot, unavailableSnapshot } from "./contracts";

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
