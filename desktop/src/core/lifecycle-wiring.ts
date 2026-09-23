import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import type { LifecycleState } from "./animation-loop";

/** Event the Rust shell emits when it shows or hides a panel window. */
export const VISIBILITY_EVENT = "jarvis://visibility";

export type VisibilityHandler = (visible: boolean) => void;
export type VisibilitySubscriber = (handler: VisibilityHandler) => Promise<() => void>;
export type VisibilityReader = () => Promise<boolean>;

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

/**
 * Ask the shell whether the calling window is visible right now.
 *
 * The event above only reports changes, so a webview that boots while its
 * window is hidden never learns that from the stream. Until this answers, the
 * render lifecycle stays hidden (2026-09-22).
 */
export const tauriVisibilityReader: VisibilityReader = async () => {
  const visible = await invoke<unknown>("window_is_visible");
  return visible === true;
};

export interface LifecycleTarget {
  transition(state: LifecycleState): void;
}

export interface LifecycleWiringOptions {
  windowRef?: Window;
  documentRef?: Document;
  subscribeVisibility?: VisibilitySubscriber | null;
  readVisibility?: VisibilityReader | null;
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
  let detached = false;
  let release: (() => void) | null = null;
  // How many bridge events have reached `transition`. A snapshot read that was
  // issued before an event arrived is older than that event, so it must not
  // overwrite it.
  let bridgeEvents = 0;

  // A throwing transition would escape an OS event handler and could take the
  // panel down with it, so render state stays inside this boundary.
  const transition = (state: LifecycleState): void => {
    if (closed || detached) return;
    try {
      target.transition(state);
    } catch {
      // The panel keeps running; the next visibility signal still applies.
    }
  };

  const domState = (): LifecycleState => (doc.visibilityState === "visible" ? "visible" : "hidden");
  const hide = (): void => transition("hidden");
  const show = (): void => {
    if (doc.visibilityState === "visible") transition("visible");
  };
  const visibilityChanged = (): void => transition(domState());

  const detach = (): void => {
    // Blocks every later transition: a teardown that is not `pagehide` must not
    // let a late visibility event restart a renderer that is being disposed.
    detached = true;
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
  const read = options.readVisibility;
  // With a reader the shell's own answer decides the boot state, so the panel
  // starts hidden instead of trusting `document.visibilityState`.
  if (subscribe && read) transition("hidden");
  if (subscribe) {
    void Promise.resolve()
      .then(() => subscribe((visible) => {
        bridgeEvents += 1;
        transition(visible ? "visible" : "hidden");
      }))
      .then(async (unsubscribe) => {
        if (closed || detached) {
          try {
            unsubscribe();
          } catch {
            // Late subscription on a torn-down panel: nothing to release.
          }
          return;
        }
        release = unsubscribe;
        if (!read) return;
        // Read only once the listener is live: everything the shell did before
        // this point is covered by the snapshot, and everything after it
        // arrives as an event.
        const issuedAt = bridgeEvents;
        let visible: boolean;
        try {
          visible = await read();
        } catch {
          // A shell that cannot answer must not freeze the panel.
          transition(domState());
          return;
        }
        if (closed || detached || bridgeEvents !== issuedAt) return;
        transition(visible ? "visible" : "hidden");
      })
      .catch(() => {
        // A missing bridge (plain browser, no Tauri internals) leaves the
        // OS-derived behaviour in place instead of failing the panel.
        transition(domState());
      });
  }

  // Without a reader the boot state stays DOM-derived, which is what a plain
  // browser and the non-Tauri tests rely on.
  if (!(subscribe && read)) transition(domState());
  return detach;
}
