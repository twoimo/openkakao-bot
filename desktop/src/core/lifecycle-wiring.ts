import { listen } from "@tauri-apps/api/event";
import type { LifecycleState } from "./animation-loop";

/** Event the Rust shell emits when it shows or hides a panel window. */
export const VISIBILITY_EVENT = "jarvis://visibility";

export type VisibilityHandler = (visible: boolean) => void;
export type VisibilitySubscriber = (handler: VisibilityHandler) => Promise<() => void>;

/**
 * Subscribe to the authoritative window-visibility signal from Rust.
 *
 * A hidden menubar window cannot be trusted to report itself: whether
 * `document.visibilityState` and `blur` fire at all depends on how AppKit
 * ordered the window out. The 3D loop has to stop on hide, not "usually", so
 * Rust emits the state after `show()`/`hide()` has already returned
 * (2026-09-22).
 */
export const tauriVisibilitySubscriber: VisibilitySubscriber = async (handler) => {
  const unlisten = await listen<{ visible?: unknown }>(VISIBILITY_EVENT, (event) => {
    handler(event.payload?.visible === true);
  });
  return () => unlisten();
};

export interface LifecycleTarget {
  transition(state: LifecycleState): void;
}

export interface LifecycleWiringOptions {
  windowRef?: Window;
  documentRef?: Document;
  subscribeVisibility?: VisibilitySubscriber | null;
  /** Runs once after the closed transition, while the page is going away. */
  onClosed?: () => void;
}

/**
 * Drive one render lifecycle from every signal that can end or resume it.
 *
 * Returns a detach function that removes the OS listeners and unsubscribes the
 * bridge. It is idempotent, so a teardown path and `pagehide` may both run it.
 */
export function wireRenderLifecycle(
  target: LifecycleTarget,
  options: LifecycleWiringOptions = {},
): () => void {
  const win = options.windowRef ?? window;
  const doc = options.documentRef ?? document;
  let closed = false;
  let release: (() => void) | null = null;

  // A throwing transition would escape an OS event handler and could take the
  // panel down with it, so render state stays inside this boundary.
  const transition = (state: LifecycleState): void => {
    if (closed) return;
    try {
      target.transition(state);
    } catch {
      // The panel keeps running; the next visibility signal still applies.
    }
  };

  const hide = (): void => transition("hidden");
  const show = (): void => {
    if (doc.visibilityState === "visible") transition("visible");
  };
  const visibilityChanged = (): void => {
    transition(doc.visibilityState === "visible" ? "visible" : "hidden");
  };

  const detach = (): void => {
    win.removeEventListener("blur", hide);
    win.removeEventListener("focus", show);
    win.removeEventListener("pagehide", close);
    doc.removeEventListener("visibilitychange", visibilityChanged);
    const unsubscribe = release;
    release = null;
    if (!unsubscribe) return;
    try {
      unsubscribe();
    } catch {
      // A bridge that is already gone needs no cleanup.
    }
  };

  const close = (): void => {
    if (closed) return;
    try {
      target.transition("closed");
    } catch {
      // Page teardown must not surface render errors.
    }
    closed = true;
    detach();
    try {
      options.onClosed?.();
    } catch {
      // Teardown is best effort once the page is going away.
    }
  };

  win.addEventListener("blur", hide);
  win.addEventListener("focus", show);
  win.addEventListener("pagehide", close);
  doc.addEventListener("visibilitychange", visibilityChanged);

  const subscribe = options.subscribeVisibility;
  if (subscribe) {
    // A missing bridge (plain browser, no Tauri internals) must leave the
    // OS-derived behaviour above in place instead of failing the panel.
    void Promise.resolve()
      .then(() => subscribe((visible) => transition(visible ? "visible" : "hidden")))
      .then((unsubscribe) => {
        if (closed) {
          try {
            unsubscribe();
          } catch {
            // Late subscription on a closed panel: nothing left to release.
          }
          return;
        }
        release = unsubscribe;
      })
      .catch(() => undefined);
  }

  transition(doc.visibilityState === "visible" ? "visible" : "hidden");
  return detach;
}
