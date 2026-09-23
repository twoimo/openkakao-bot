#!/usr/bin/env python3
"""Rendered cross-check of the Jarvis menubar UI inside a real browser engine.

The desktop contract test (desktop/src/__tests__/ui-removal-contract.test.ts)
pins the panel and the settings window as *strings*. That is a different claim
from "the window the product ships draws only the core and the core actually
produces frames on a real GPU path". It cannot see the Three.js core or the
knowledge hologram at all.

This script loads the built bundle in Chromium, answers the Tauri bridge with a
deterministic stub, and records what the rendered document and the WebGL context
really are:

* interactive element inventory of the panel, read from the live DOM
* settings h2 section order, main count, iframe count
* banned-token scan over the rendered text and markup
* gl.getParameter version/renderer strings and the unmasked renderer
* frame counts sampled while idle and while a background job is reported
* source-level tray routing: left click toggles the panel, right click opens
  settings (Chromium cannot exercise a macOS menu-bar TrayIconEvent)
* the knowledge-graph drilldown, driven with real pointer events and a real
  +1 hop click

The banned-token list and the section list are read from the TypeScript
contract file so both checks share one source; the contract hash is recorded in
the receipt. No network, no account, and no live KakaoTalk send is involved.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIST = ROOT / "desktop" / "dist"
DEFAULT_CONTRACT = ROOT / "desktop" / "src" / "__tests__" / "ui-removal-contract.test.ts"
DEFAULT_TAURI_MAIN = ROOT / "desktop" / "src-tauri" / "src" / "main.rs"
DEFAULT_OUT = ROOT / "docs" / "architecture"
DEFAULT_PORT = 8712

# The on-device status line is produced by the product, not written here. The
# TypeScript contract cannot see text the Tauri bridge injects as status_label,
# and the installed settings window once rendered a banned token exactly that
# way, so the stub feeds the real formatter and the scan below sees its output.
sys.path.insert(0, str(ROOT / "scripts"))
from auto_reply_ondevice import ondevice_status_strings  # noqa: E402

STUB_ONDEVICE_MODEL = "mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit"
STUB_ONDEVICE_PROBE = {
    "ok": True,
    "engine": "mlx-serve-gateway",
    "model": STUB_ONDEVICE_MODEL,
    "latency_ms": 15617,
    "preview": "OK",
}
STUB_ONDEVICE_STATUS_BITS, STUB_ONDEVICE_DETAIL_BITS = ondevice_status_strings(
    chip="Apple M5 Max",
    memory_gb=128.0,
    reason="Apple Silicon MLX Core/Serve 경로를 사용합니다",
    recommended_model=STUB_ONDEVICE_MODEL,
    verified=True,
    last_probe=STUB_ONDEVICE_PROBE,
)
STUB_ONDEVICE_STATUS_LABEL = " · ".join(STUB_ONDEVICE_STATUS_BITS)
STUB_ONDEVICE_STATUS_DETAIL = " · ".join(bit for bit in STUB_ONDEVICE_DETAIL_BITS if bit)

PANEL_SIZE = (276, 260)
SETTINGS_SIZE = (760, 760)
CONTRACT_INTERACTIVE_TAGS = "button,select,input,textarea,a,details,summary"
# The Rust shell's window-visibility event. desktop/src/core/lifecycle-wiring.ts
# exports this as VISIBILITY_EVENT, and the contract tests pin the two together
# so a rename fails there instead of silently never firing here.
VISIBILITY_EVENT = "jarvis://visibility"


class ContractParseError(RuntimeError):
    """The TypeScript contract file no longer exposes what this script needs."""


def _array_literals(source: str, name: str) -> list[str]:
    """Pull the double-quoted string literals out of one const <name> = [...]."""
    match = re.search(r"const\s+" + re.escape(name) + r"\s*=\s*\[(.*?)\];", source, re.S)
    if match is None:
        raise ContractParseError("array not found: " + name)
    literals = re.findall(r'"((?:[^"\\]|\\.)*)"', match.group(1))
    if not literals:
        raise ContractParseError("array has no string literals: " + name)
    return literals


def parse_contract(path: Path) -> dict[str, Any]:
    """Read the banned tokens and the expected settings sections from the TS test."""
    source = path.read_text(encoding="utf-8")
    banned = _array_literals(source, "REMOVED_TOKENS")
    sections = _array_literals(source, "SETTINGS_SECTIONS")
    return {
        "path": str(path),
        "sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "banned_tokens": banned,
        "settings_sections": sections,
    }


def tray_click_routes(source: str) -> dict[str, bool]:
    """Detect the two menu-bar click routes in the Rust tray event match."""
    left_up = re.search(
        r"TrayIconEvent::Click\s*\{\s*"
        r"button:\s*MouseButton::Left,\s*"
        r"button_state:\s*MouseButtonState::Up,\s*"
        r"position,\s*\.\.\s*\}\s*=>\s*toggle_panel\s*\(",
        source,
        re.S,
    )
    right_up = re.search(
        r"TrayIconEvent::Click\s*\{\s*"
        r"button:\s*MouseButton::Right,\s*"
        r"button_state:\s*MouseButtonState::Up,\s*"
        r"\.\.\s*\}\s*=>\s*\{(?P<body>.*?)\n\s*\}",
        source,
        re.S,
    )
    right_body = right_up.group("body") if right_up else ""
    return {
        "left_up_toggles_panel": left_up is not None,
        "right_up_opens_settings": bool(right_up and re.search(r"\bopen_settings\s*\(", right_body)),
    }


def inspect_tray_source(path: Path) -> dict[str, Any]:
    """Read and fingerprint the Rust tray routing used for source-only checks."""
    source = path.read_text(encoding="utf-8")
    routes = tray_click_routes(source)
    return {
        "path": str(path),
        "sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        **routes,
        "browser_exercised": False,
    }


def scan_banned_tokens(haystacks: Iterable[str], tokens: Iterable[str]) -> list[str]:
    """Return the banned tokens that occur in any haystack, case-insensitively."""
    joined = "\n".join(haystacks).lower()
    return [token for token in tokens if token.lower() in joined]


def count_contract_interactive(markup: str) -> int:
    """Count the element tags the TypeScript contract treats as interactive."""
    return len(re.findall(r"<(button|select|input|textarea|a|details|summary)\b", markup, re.I))


def headings_from_markup(markup: str) -> list[str]:
    return [match.group(1).strip() for match in re.finditer(r"<h2[^>]*>([^<]+)</h2>", markup)]


def check(name: str, ok: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def verdict(checks: Iterable[dict[str, Any]]) -> str:
    return "pass" if all(item["ok"] for item in checks) else "fail"


# The stub answers the invoke commands main.ts and runtime.ts can issue. The
# snapshot fields mirror the safe shapes parseRuntimeSnapshot accepts.
IDLE_SNAPSHOT: dict[str, Any] = {
    "available": True,
    "rooms": [
        {"chat_id": 417780809780519, "title": "NIMDA \uc778\uc218\uc778\uacc4 \uc784\uc6d0\ubc29", "live": True, "auto_reply": True, "open_jobs": 0},
    ],
    "jobs": [],
    "recent_receipts": [
        {
            "chatId": 417780809780519,
            "title": "NIMDA \uc778\uc218\uc778\uacc4 \uc784\uc6d0\ubc29",
            "displayTime": "09:41",
            "clock": "09:41",
            "outcome": "sent",
            "outcomeText": "\uc804\uc1a1",
            "reasonCode": "ok",
            "reasonText": "\uc815\uc0c1",
            "retrievalState": "ok",
        },
    ],
    "job_load": 0.0,
    "background": {
        "activity": 0.0,
        "caption": "",
        "replyLoad": 0.0,
        "geeknews": {"state": "idle", "activity": 0.0, "caption": ""},
        "dbSync": {"state": "ready", "activity": 0.0, "caption": ""},
    },
    "onDevice": {
        "hardware": {"chip": "Apple M5 Max", "cores": 18, "memory_gb": 128.0, "is_apple_silicon": True},
        "recommendation": {"primary_engine": "MLX Core/Serve", "recommended_model": "Qwen3.8 Flash-Next", "recommended_quant": "4-8bit"},
        "verification": {"ok": True},
        "status_label": STUB_ONDEVICE_STATUS_LABEL,
        "status_detail": STUB_ONDEVICE_STATUS_DETAIL,
    },
    "pipeline": {"active": False, "stage": "none", "stageIndex": 0, "stageTotal": 8, "outcome": "none"},
    "terminal_counts": {"sent": 12, "skipped": 1, "delivery_unknown": 0, "burst_superseded": 1},
    "context_sync": {"mode": "async", "waited": False},
    "reply_model_id": "ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit",
    "voice": {
        "available": True,
        "state": "idle",
        "rms": 0.0,
        "wake_phrase": "\ud5e4\uc774 \uc790\ube44\uc2a4",
        "threshold": 0.65,
        "wake_source": "custom",
        "custom_model_selected": True,
    },
}

# The busy snapshot is the same room with a reply turn in the model stage plus a
# GeekNews send and a DB sync, which is the load the core is asked to render.
BUSY_SNAPSHOT: dict[str, Any] = json.loads(json.dumps(IDLE_SNAPSHOT))
BUSY_SNAPSHOT["job_load"] = 0.9
BUSY_SNAPSHOT["jobs"] = [
    {"jobId": "reply-417780809780519", "kind": "reply", "stage": "model", "load": 0.9, "time": 1790000000, "errorCode": None},
]
BUSY_SNAPSHOT["background"] = {
    "activity": 0.9,
    "caption": "\ub2f5\ubcc0 \uc0dd\uc131",
    "replyLoad": 0.9,
    "geeknews": {"state": "sending", "activity": 0.5, "caption": "TOP5 \uc804\uc1a1"},
    "dbSync": {"state": "syncing", "activity": 0.4, "caption": "\uc99d\ubd84 \ub3d9\uae30\ud654"},
}
BUSY_SNAPSHOT["pipeline"] = {"active": True, "stage": "model", "stageIndex": 4, "stageTotal": 8, "outcome": "none"}
BUSY_SNAPSHOT["voice"]["state"] = "listening"
BUSY_SNAPSHOT["voice"]["rms"] = 0.42

KNOWLEDGE_GRAPH: dict[str, Any] = {
    "stale": False,
    "nodes": [
        {"id": "person:\ucd5c\uc5f0\uc6b0", "label": "\ucd5c\uc5f0\uc6b0", "category": "person", "importance": 95, "updated_at": 1790000000},
        {"id": "org:\uc54c\ub9ac\ubc14\ubc14\ud074\ub77c\uc6b0\ub4dc", "label": "\uc54c\ub9ac\ubc14\ubc14 \ud074\ub77c\uc6b0\ub4dc", "category": "org", "importance": 80, "updated_at": 1790000000},
        {"id": "term:\uc54c\ucd00\ucfe0", "label": "\uc54c\ucd00\ucfe0", "category": "alias", "importance": 70, "updated_at": 1790000000},
        {"id": "doc:\ucfe0\ud3f0\uc815\ucc45", "label": "\ucfe0\ud3f0 \uc815\ucc45", "category": "doc", "importance": 60, "updated_at": 1790000000},
        {"id": "room:417780809780519", "label": "NIMDA \uc778\uc218\uc778\uacc4 \uc784\uc6d0\ubc29", "category": "room", "importance": 55, "updated_at": 1790000000},
    ],
    "edges": [
        {"source": "person:\ucd5c\uc5f0\uc6b0", "relation": "MENTIONS", "target": "term:\uc54c\ucd00\ucfe0", "weight": 3.0, "room_id": "417780809780519", "valid_from": "2026-09-20", "evidence_message_id": "log:901"},
        {"source": "term:\uc54c\ucd00\ucfe0", "relation": "ALIAS_OF", "target": "org:\uc54c\ub9ac\ubc14\ubc14\ud074\ub77c\uc6b0\ub4dc", "weight": 2.5, "room_id": "417780809780519", "valid_from": "2026-09-20", "evidence_message_id": "log:902"},
        {"source": "org:\uc54c\ub9ac\ubc14\ubc14\ud074\ub77c\uc6b0\ub4dc", "relation": "HAS_POLICY", "target": "doc:\ucfe0\ud3f0\uc815\ucc45", "weight": 2.0, "room_id": "417780809780519", "valid_from": "2026-09-21", "evidence_message_id": "log:903"},
        {"source": "room:417780809780519", "relation": "HAS_PARTICIPANT", "target": "person:\ucd5c\uc5f0\uc6b0", "weight": 1.0, "room_id": "417780809780519", "valid_from": "2026-09-19", "evidence_message_id": "log:900"},
    ],
}

SETTINGS_ACTIONS: dict[str, Any] = {
    "models": {"model": "ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit"},
    # The three model actions below report the state this host is really in: an
    # external mlx-serve owns port 11234, so the app refuses the 27B swap.
    "model-set": {"ok": True, "action": "model-set", "model": "ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit", "stored": True, "prepared": False, "needs_prepare": True},
    "model-prepare": {"ok": True, "action": "model-prepare", "model": "ddalcu/Qwen3.8-27B-MLX-Serve-4bit", "stored": True, "prepared": True, "needs_prepare": False},
    "model-swap": {"ok": False, "action": "model-swap", "model": "ddalcu/Qwen3.8-27B-MLX-Serve-4bit", "stage": "failed", "reason": "model_owner_unmanaged", "stages": [], "stored": False, "prepared": False},
    "model-owner-status": {"owner_state": "foreign_listener"},
    "mlx-server-status": {"owner_state": "foreign_listener", "model": "ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit"},
    "dream-rsi-status": {"status": "evaluated", "selected_policy": "mirror_prompt_tail", "gold_rows": 400, "gold_source_policy": "human_only"},
    "room-upsert": {"ok": True, "action": "room-upsert"},
    "knowledge-graph-status": {
        "stale": False,
        "snapshot_status": "copy_ok",
        "indexing_mode": "wal+isolated-copy+mode=ro+query_only",
        "indexed_at": 1790005911,
        "indexed_count": 31,
        "dense_status": "indexed:50",
    },
    "knowledge-graph": KNOWLEDGE_GRAPH,
    "knowledge-graph-focus": {
        "ok": True,
        "search_mode": "rrf",
        "facts": ["\uc54c\ucd00\ucfe0 = \uc54c\ub9ac\ubc14\ubc14 \ud074\ub77c\uc6b0\ub4dc \ucd95\uc57d", "\ucfe0\ud3f0 \ub9cc\ub8cc 2026-10-31"],
    },
}

STUB_SOURCE = r"""
(() => {
  window.__jarvisStub = {
    snapshot: null,
    actions: {},
    calls: [],
    failSnapshot: false,
    // Handler ids registered through plugin:event|listen, keyed by event name,
    // plus the answer window_is_visible should give.
    listeners: {},
    visible: true
  };
  window.__TAURI_INTERNALS__ = {
    transformCallback: function (callback, once) {
      const id = Math.floor(Math.random() * 1000000000);
      window["_" + id] = function () {
        if (once) { try { delete window["_" + id]; } catch (error) { window["_" + id] = undefined; } }
        return callback.apply(null, arguments);
      };
      return id;
    },
    unregisterCallback: function () {},
    convertFileSrc: function (path) { return path; },
    invoke: async function (command, args) {
      window.__jarvisStub.calls.push({ command: command, args: args || {} });
      if (command === "fetch_runtime_snapshot") {
        if (window.__jarvisStub.failSnapshot) throw new Error("stub_snapshot_disabled");
        return JSON.parse(JSON.stringify(window.__jarvisStub.snapshot));
      }
      if (command === "fetch_settings_action") {
        const action = args && args.action;
        if (!Object.prototype.hasOwnProperty.call(window.__jarvisStub.actions, action)) {
          throw new Error("stub_action_unknown:" + String(action));
        }
        return JSON.parse(JSON.stringify(window.__jarvisStub.actions[action]));
      }
      if (command === "cancel_python" || command === "cancel_model_swap") return true;
      if (command === "open_settings" || command === "start_voice_session") return null;
      // The visibility bridge is the authoritative pause path, so the stub
      // answers it instead of failing into the document-derived fallback. That
      // is what lets the check below drive a hide the way the Rust shell does.
      if (command === "plugin:event|listen") {
        window.__jarvisStub.listeners[(args && args.event) || ""] = args && args.handler;
        return 1;
      }
      if (command === "plugin:event|unlisten") return 1;
      if (command === "window_is_visible") return window.__jarvisStub.visible !== false;
      throw new Error("stub_command_unknown:" + String(command));
    }
  };
  // Deliver a buffered event the way the Rust shell would, through the handler
  // id plugin:event|listen registered. Returns false when nothing is subscribed,
  // so a caller can record "the bridge did not exist" instead of asserting on a
  // stray dispatch.
  window.__jarvisStub.emitEvent = function (eventName, payload) {
    const handlerId = window.__jarvisStub.listeners[eventName];
    const handler = handlerId === undefined ? undefined : window["_" + String(handlerId)];
    if (typeof handler !== "function") return false;
    handler({ event: eventName, id: 0, payload: payload });
    return true;
  };
})()
"""

PROBE_SOURCE = r"""
(() => {
  const all = (selector) => Array.from(document.querySelectorAll(selector));
  const attribute = (element, name) => (element && element.getAttribute(name)) || "";
  const text = (element) => (element && element.textContent) || "";
  const contractTags = "button,select,input,textarea,a,details,summary";
  const canvas = document.querySelector(".jarvis-core") || document.querySelector("#knowledge-graph-canvas");
  let glVersion = "";
  let glRenderer = "";
  let glUnmaskedRenderer = "";
  let glError = "";
  if (canvas) {
    try {
      const gl = canvas.getContext("webgl2") || canvas.getContext("webgl");
      if (gl) {
        glVersion = String(gl.getParameter(gl.VERSION) || "");
        glRenderer = String(gl.getParameter(gl.RENDERER) || "");
        const info = gl.getExtension("WEBGL_debug_renderer_info");
        if (info) glUnmaskedRenderer = String(gl.getParameter(info.UNMASKED_RENDERER_WEBGL) || "");
        glError = String(gl.getError());
      } else {
        glError = "no_webgl_context";
      }
    } catch (error) {
      glError = String((error && error.message) || error);
    }
  }
  const root = document.getElementById("app");
  return {
    state: root ? root.dataset.state || "" : "",
    interactive: all(contractTags).map((element) => ({
      tag: element.tagName.toLowerCase(),
      id: element.id || "",
      label: (attribute(element, "aria-label") || text(element)).trim().slice(0, 48),
      disabled: element.disabled === true
    })),
    extraFocusable: all("[tabindex]:not([tabindex='-1']), [contenteditable='true']").filter((element) => !element.matches(contractTags)).map((element) => element.tagName.toLowerCase()),
    headings: all("h2").map((element) => text(element).trim()),
    mains: all("main").length,
    mainClasses: all("main").map((element) => String(element.className || "")),
    iframes: all("iframe").length,
    canvases: all("canvas").map((element) => ({
      id: element.id || "",
      cls: String(element.className || ""),
      width: element.width,
      height: element.height,
      cssWidth: element.clientWidth,
      cssHeight: element.clientHeight
    })),
    renderCount: typeof window.__jarvisRenderCount === "number" ? window.__jarvisRenderCount : null,
    knowledgeRenderCount: typeof window.__knowledgeRenderCount === "number" ? window.__knowledgeRenderCount : null,
    glVersion: glVersion,
    glRenderer: glRenderer,
    glUnmaskedRenderer: glUnmaskedRenderer,
    glError: glError,
    text: String(document.body.innerText || ""),
    html: String(document.body.innerHTML || ""),
    focusTitle: text(document.getElementById("knowledge-focus-title")).trim(),
    focusMeta: text(document.getElementById("knowledge-focus-meta")).trim(),
    hopLabel: text(document.getElementById("knowledge-hop-label")).trim(),
    retrieve: text(document.getElementById("knowledge-retrieve")).trim(),
    relationRows: all("#knowledge-relations .knowledge-relation-row").length,
    expandDisabled: document.getElementById("knowledge-expand-hop") ? document.getElementById("knowledge-expand-hop").disabled === true : null,
    slotMorning: text(document.getElementById("settings-slot-morning")).trim()
  };
})()
"""

SCROLL_CANVAS_SOURCE = r"""
(() => {
  const canvas = document.querySelector("#knowledge-graph-canvas");
  if (!canvas) return null;
  canvas.scrollIntoView({ block: "center", inline: "nearest" });
  const box = canvas.getBoundingClientRect();
  return { left: box.left, top: box.top, width: box.width, height: box.height };
})()
"""

BRIGHTNESS_SOURCE = r"""
(imageDataUrl) => new Promise((resolve, reject) => {
  const image = new Image();
  image.onload = () => {
    const canvas = document.createElement("canvas");
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
    const context = canvas.getContext("2d");
    context.drawImage(image, 0, 0);
    const data = context.getImageData(0, 0, canvas.width, canvas.height).data;
    let sum = 0;
    let lit = 0;
    const pixels = canvas.width * canvas.height;
    for (let index = 0; index < data.length; index += 4) {
      const luminance = 0.2126 * data[index] + 0.7152 * data[index + 1] + 0.0722 * data[index + 2];
      sum += luminance;
      if (luminance > 40) lit += 1;
    }
    resolve({
      width: canvas.width,
      height: canvas.height,
      meanLuminance: Math.round((sum / pixels) * 100) / 100,
      litRatio: Math.round((lit / pixels) * 10000) / 10000
    });
  };
  image.onerror = () => reject(new Error("image_decode_failed"));
  image.src = imageDataUrl;
})
"""


def _serve(directory: Path, port: int) -> ThreadingHTTPServer:
    handler = partial(SimpleHTTPRequestHandler, directory=str(directory))
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sample_frames(page: Any, seconds: float, counter: str = "__jarvisRenderCount") -> dict[str, Any]:
    """Count the frames one render loop produced over a wall-clock window."""
    read = "() => window." + counter + " || 0"
    before = page.evaluate(read)
    started = time.monotonic()
    page.wait_for_timeout(int(seconds * 1000))
    elapsed = time.monotonic() - started
    after = page.evaluate(read)
    frames = int(after) - int(before)
    return {
        "counter": counter,
        "frames": frames,
        "seconds": round(elapsed, 3),
        "fps": round(frames / elapsed, 2) if elapsed > 0 else 0.0,
    }


def capture(page: Any, path: Path) -> dict[str, Any]:
    page.screenshot(path=str(path))
    return {"file": path.name, "bytes": path.stat().st_size, "sha256": sha256_of(path)}


def mean_luminance(page: Any, image_path: Path) -> dict[str, Any]:
    payload = "data:image/png;base64," + base64.b64encode(image_path.read_bytes()).decode("ascii")
    return page.evaluate(BRIGHTNESS_SOURCE, payload)


def scan_with_real_clicks(
    page: Any,
    box: dict[str, float],
    read_title: str,
    step: float = 22.0,
) -> dict[str, Any]:
    """Click a fixed grid with the real mouse until a node takes the focus.

    Real clicks are the only input whose effect the focus heading can attribute,
    and they are immune to the rotation that invalidates a coordinate recorded in
    an earlier frame. A 22 px pitch against node spheres drawn about 19 px across
    reaches every node an ordinary user could click.
    """
    start_title = page.evaluate(read_title)
    probes = 0
    y = box["top"] + 4.0
    while y < box["top"] + box["height"]:
        x = box["left"] + 4.0
        while x < box["left"] + box["width"]:
            page.mouse.click(x, y)
            probes += 1
            title = page.evaluate(read_title)
            if title.strip() and title.strip() != start_title.strip():
                return {
                    "probes": probes,
                    "title": title,
                    "start_title": start_title,
                    "hit": {"x": round(x, 1), "y": round(y, 1)},
                    "pitch": step,
                }
            x += step
        y += step
    return {"probes": probes, "title": start_title, "start_title": start_title, "hit": None, "pitch": step}


def drive_drilldown(page: Any, log: Callable[[str], None]) -> dict[str, Any]:
    read_title = "() => ((document.getElementById('knowledge-focus-title') || {}).textContent || '')"
    box = page.evaluate(SCROLL_CANVAS_SOURCE)
    result: dict[str, Any] = {"canvas_box": box, "probes": 0, "focused": False}
    if not box:
        result["error"] = "knowledge_canvas_missing"
        return result
    page.wait_for_timeout(300)
    result["overview_title"] = page.evaluate(read_title)
    result["overview_meta"] = page.evaluate("() => ((document.getElementById('knowledge-focus-meta') || {}).textContent || '')")

    # The hologram rotates and a missed click calls reset(), which rebuilds every
    # node mesh. A synthetic dispatch sequence therefore cannot be trusted to
    # describe where the nodes are by the time the recorded click arrives, so the
    # scan itself uses real browser mouse clicks at a grid that stays inside the
    # drawn node radius. Any title change is then evidence that a genuine click
    # selected a node.
    located = scan_with_real_clicks(page, box, read_title)
    result["locator"] = located
    result["probes"] = located.get("probes", 0)
    result["focused"] = bool(located.get("hit"))
    result["hit"] = located.get("hit")
    if not result["focused"]:
        result["error"] = "no_node_hit_by_real_click"
        return result
    probe = page.evaluate(PROBE_SOURCE)
    result["focus_title"] = probe["focusTitle"]
    result["focus_meta"] = probe["focusMeta"]
    result["hop_label"] = probe["hopLabel"]
    result["relation_rows"] = probe["relationRows"]
    result["retrieve"] = probe["retrieve"]
    result["expand_disabled"] = probe["expandDisabled"]
    page.wait_for_timeout(500)
    result["retrieve_after_settle"] = page.evaluate("() => ((document.getElementById('knowledge-retrieve') || {}).textContent || '')")
    if result["expand_disabled"] is False:
        page.click("#knowledge-expand-hop")
        page.wait_for_timeout(300)
        result["hop_label_after_expand"] = page.evaluate("() => ((document.getElementById('knowledge-hop-label') || {}).textContent || '')")
        result["focus_meta_after_expand"] = page.evaluate("() => ((document.getElementById('knowledge-focus-meta') || {}).textContent || '')")
        result["expand_disabled_after_expand"] = page.evaluate("() => document.getElementById('knowledge-expand-hop').disabled === true")
    page.wait_for_timeout(700)
    result["frames_after_focus"] = sample_frames(page, 1.5, "__knowledgeRenderCount")
    log("drilldown: " + json.dumps(
        {key: result[key] for key in ("focused", "probes", "focus_title", "focus_meta", "relation_rows", "hop_label", "retrieve_after_settle") if key in result},
        ensure_ascii=False,
    ))
    return result


def pause_checks(pause: dict[str, Any]) -> list[dict[str, Any]]:
    """Verdicts for the hide-pauses-rendering contract.

    The acceptance criterion is the app's own render-call count, not
    whole-machine GPU use: while the window is hidden a paused loop must produce
    zero frames, and the runtime poller must stop asking for snapshots. The
    blur/focus pair covers the DOM fallback a plain browser or a missed signal
    reaches, and the bridge pair covers the authoritative Rust signal.
    """
    listeners = list(pause.get("listeners") or [])
    hidden = pause.get("hidden") or {}
    visible = pause.get("visible") or {}
    blur = pause.get("blur") or {}
    focus = pause.get("focus") or {}
    return [
        check("panel.visibility_bridge_subscribed", bool(listeners), listeners),
        check("panel.visibility_bridge_emit_lands", bool(pause.get("hidden_emitted")), pause.get("hidden_emitted")),
        check("panel.render_stops_when_hidden", int(hidden.get("frames") or 0) == 0, hidden),
        check(
            "panel.poller_stops_when_hidden",
            int(pause.get("hidden_poll_delta") or 0) == 0,
            pause.get("hidden_poll_delta"),
        ),
        check("panel.render_resumes_when_visible", int(visible.get("frames") or 0) > 0, visible),
        check("panel.render_stops_on_blur", int(blur.get("frames") or 0) == 0, blur),
        check("panel.render_resumes_on_focus", int(focus.get("frames") or 0) > 0, focus),
    ]


def assertions(receipt: dict[str, Any], contract: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    panel = receipt["panel"]
    tray_source = receipt["tray_source"]
    settings = receipt["settings"]
    knowledge = receipt["knowledge"]

    checks.append(check("panel.view_state_ready", panel["view_state"] == "ready", panel["view_state"]))
    checks.append(check("panel.zero_interactive_elements", panel["interactive"] == [], panel["interactive"]))
    checks.append(check("panel.no_extra_focusable", panel["extra_focusable"] == [], panel["extra_focusable"]))
    checks.append(check("panel.single_main_shell", panel["mains"] == 1, {"mains": panel["mains"], "classes": panel["main_classes"]}))
    checks.append(check("panel.no_iframe", panel["iframes"] == 0, panel["iframes"]))
    checks.append(check("panel.webgl_context_live", bool(panel["webgl"]["version"]), panel["webgl"]))
    checks.append(check("panel.render_loop_advances_idle", (panel["frames"]["idle"]["frames"] or 0) > 0, panel["frames"]["idle"]))
    checks.append(check("panel.render_loop_advances_under_load", (panel["frames"]["busy"]["frames"] or 0) > 0, panel["frames"]["busy"]))
    checks.append(check(
        "panel.fps_within_cap",
        panel["frames"]["idle"]["fps"] <= 17.5 and panel["frames"]["busy"]["fps"] <= 32.5,
        {"idle": panel["frames"]["idle"]["fps"], "busy": panel["frames"]["busy"]["fps"]},
    ))
    checks.append(check(
        "panel.no_panel_side_open_settings_invocation",
        "open_settings" not in panel["actions"]["commands"],
        panel["actions"],
    ))
    checks.append(check("panel.no_removed_control_tokens", scan_banned_tokens([panel["text"], panel["html"]], contract["banned_tokens"]) == [], scan_banned_tokens([panel["text"], panel["html"]], contract["banned_tokens"])))
    checks.extend(pause_checks(panel.get("pause") or {}))
    checks.append(check(
        "tray_source.right_click_opens_settings_and_left_up_toggles_panel",
        tray_source["right_up_opens_settings"] is True and tray_source["left_up_toggles_panel"] is True,
        tray_source,
    ))

    checks.append(check("settings.view_state_ready", settings["view_state"] == "ready", settings["view_state"]))
    checks.append(check("settings.sections_in_order", settings["headings"] == contract["settings_sections"], {"actual": settings["headings"], "expected": contract["settings_sections"]}))
    checks.append(check("settings.single_main_shell", settings["mains"] == 1, {"mains": settings["mains"], "classes": settings["main_classes"]}))
    checks.append(check("settings.no_iframe", settings["iframes"] == 0, settings["iframes"]))
    checks.append(check("settings.knowledge_canvas_present", any(item["id"] == "knowledge-graph-canvas" for item in settings["canvases"]), settings["canvases"]))
    checks.append(check("settings.no_removed_control_tokens", scan_banned_tokens([settings["text"], settings["html"]], contract["banned_tokens"]) == [], scan_banned_tokens([settings["text"], settings["html"]], contract["banned_tokens"])))
    checks.append(check(
        "settings.ondevice_status_is_product_formatted",
        STUB_ONDEVICE_STATUS_LABEL in settings["text"],
        {"product_label": STUB_ONDEVICE_STATUS_LABEL[:160]},
    ))

    checks.append(check("knowledge.hologram_renders_frames", (knowledge.get("render_count_after_focus") or 0) > 0, knowledge.get("render_count_after_focus")))
    checks.append(check("knowledge.drilldown_focuses_a_node", bool(knowledge.get("focused")), {"probes": knowledge.get("probes"), "title": knowledge.get("focus_title")}))
    checks.append(check("knowledge.focus_meta_reports_hops", "2-hop" in str(knowledge.get("focus_meta", "")), knowledge.get("focus_meta")))
    checks.append(check("knowledge.relations_listed", (knowledge.get("relation_rows") or 0) > 0, knowledge.get("relation_rows")))
    checks.append(check("knowledge.retrieve_answered", "retrieve rrf" in str(knowledge.get("retrieve_after_settle", "")), knowledge.get("retrieve_after_settle")))
    return checks


def render_check(
    dist: Path,
    contract_path: Path,
    out_dir: Path,
    port: int,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    contract = parse_contract(contract_path)
    tray_source = inspect_tray_source(DEFAULT_TAURI_MAIN)
    out_dir.mkdir(parents=True, exist_ok=True)
    receipt: dict[str, Any] = {
        "schema": "jarvis-desktop-render-check/1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dist": {
            "path": str(dist),
            "index_sha256": sha256_of(dist / "index.html") if (dist / "index.html").exists() else None,
            "assets": sorted(item.name for item in (dist / "assets").glob("*")) if (dist / "assets").is_dir() else [],
        },
        "contract": {"path": str(contract_path), "sha256": contract["sha256"]},
        "tray_source": tray_source,
        "expected": {"banned_tokens": contract["banned_tokens"], "settings_sections": contract["settings_sections"]},
        "browser": {},
        "panel": {},
        "settings": {},
        "knowledge": {},
        "checks": [],
        "notes": ["macOS tray routing is source-inspected only; Chromium does not exercise TrayIconEvent"],
    }

    httpd = _serve(dist, port)
    origin = "http://127.0.0.1:" + str(port)
    log("serving " + str(dist) + " at " + origin)
    try:
        with sync_playwright() as playwright:
            launch_args = ["--force-color-profile=srgb"]
            try:
                browser = playwright.chromium.launch(channel="chrome", args=launch_args)
                receipt["browser"]["channel"] = "chrome"
            except Exception as error:
                receipt["notes"].append("chrome channel unavailable, using bundled chromium: " + str(error)[:200])
                browser = playwright.chromium.launch(args=launch_args)
                receipt["browser"]["channel"] = "chromium"
            receipt["browser"]["version"] = browser.version
            receipt["browser"]["executable"] = playwright.chromium.executable_path

            panel = browser.new_page(viewport={"width": PANEL_SIZE[0], "height": PANEL_SIZE[1]}, device_scale_factor=2)
            panel.add_init_script(STUB_SOURCE)
            panel.add_init_script("window.__jarvisStub.snapshot = " + json.dumps(IDLE_SNAPSHOT) + ";")
            panel.goto(origin + "/index.html", wait_until="load")
            panel.wait_for_timeout(1200)

            idle_probe = panel.evaluate(PROBE_SOURCE)
            idle_frames = sample_frames(panel, 2.0)
            idle_capture = capture(panel, out_dir / "jarvis-render-panel.idle.png")
            idle_luminance = mean_luminance(panel, out_dir / "jarvis-render-panel.idle.png")

            panel_light = capture(panel, out_dir / "jarvis-render-panel.light.png")
            panel.emulate_media(color_scheme="dark")
            panel.wait_for_timeout(250)
            panel_dark = capture(panel, out_dir / "jarvis-render-panel.dark.png")
            panel.emulate_media(color_scheme="light")

            panel.evaluate("() => { window.__jarvisStub.snapshot = " + json.dumps(BUSY_SNAPSHOT) + "; }")
            panel.wait_for_timeout(3200)
            busy_probe = panel.evaluate(PROBE_SOURCE)
            busy_frames = sample_frames(panel, 2.0)
            busy_capture = capture(panel, out_dir / "jarvis-render-panel.busy.png")
            busy_luminance = mean_luminance(panel, out_dir / "jarvis-render-panel.busy.png")

            panel_calls = panel.evaluate("() => window.__jarvisStub.calls.map((entry) => entry.command)")

            receipt["panel"] = {
                "view_state": idle_probe["state"],
                "interactive": idle_probe["interactive"],
                "extra_focusable": idle_probe["extraFocusable"],
                "headings": idle_probe["headings"],
                "mains": idle_probe["mains"],
                "main_classes": idle_probe["mainClasses"],
                "iframes": idle_probe["iframes"],
                "canvases": idle_probe["canvases"],
                "webgl": {
                    "version": idle_probe["glVersion"],
                    "renderer": idle_probe["glRenderer"],
                    "unmasked_renderer": idle_probe["glUnmaskedRenderer"],
                    "gl_error": idle_probe["glError"],
                },
                "frames": {"idle": idle_frames, "busy": busy_frames},
                "luminance": {"idle": idle_luminance, "busy": busy_luminance},
                "render_count": {"idle": idle_probe["renderCount"], "busy": busy_probe["renderCount"]},
                "actions": {
                    "snapshot_calls": panel_calls.count("fetch_runtime_snapshot"),
                    "commands": sorted(set(panel_calls)),
                },
                "captures": {"idle": idle_capture, "busy": busy_capture, "light": panel_light, "dark": panel_dark},
                "text": idle_probe["text"],
                "html": idle_probe["html"],
            }

            # Hide/stop proof. The stub answers the real visibility bridge, so
            # the hide below travels the authoritative path the Rust shell uses
            # (jarvis://visibility) before the blur/focus fallback is checked.
            def snapshot_calls() -> int:
                return int(
                    panel.evaluate(
                        "() => window.__jarvisStub.calls.filter("
                        "function (entry) { return entry.command === "
                        + json.dumps("fetch_runtime_snapshot")
                        + "; }).length"
                    )
                )

            hidden_emitted = panel.evaluate(
                "() => window.__jarvisStub.emitEvent("
                + json.dumps(VISIBILITY_EVENT)
                + ", { visible: false })"
            )
            panel.wait_for_timeout(250)
            hidden_polls_before = snapshot_calls()
            hidden_frames = sample_frames(panel, 1.5)
            hidden_poll_delta = snapshot_calls() - hidden_polls_before

            visible_emitted = panel.evaluate(
                "() => window.__jarvisStub.emitEvent("
                + json.dumps(VISIBILITY_EVENT)
                + ", { visible: true })"
            )
            panel.wait_for_timeout(250)
            visible_frames = sample_frames(panel, 1.5)

            panel.evaluate("() => window.dispatchEvent(new Event('blur'))")
            panel.wait_for_timeout(250)
            blur_frames = sample_frames(panel, 1.5)
            panel.evaluate("() => window.dispatchEvent(new Event('focus'))")
            panel.wait_for_timeout(250)
            focus_frames = sample_frames(panel, 1.5)

            receipt["panel"]["pause"] = {
                "listeners": panel.evaluate("() => Object.keys(window.__jarvisStub.listeners)"),
                "hidden_emitted": bool(hidden_emitted),
                "visible_emitted": bool(visible_emitted),
                "hidden": hidden_frames,
                "visible": visible_frames,
                "blur": blur_frames,
                "focus": focus_frames,
                "hidden_poll_delta": hidden_poll_delta,
            }
            log("pause: " + json.dumps(receipt["panel"]["pause"], ensure_ascii=False))

            settings = browser.new_page(viewport={"width": SETTINGS_SIZE[0], "height": SETTINGS_SIZE[1]}, device_scale_factor=2)
            settings.add_init_script(STUB_SOURCE)
            settings.add_init_script("window.__jarvisStub.snapshot = " + json.dumps(IDLE_SNAPSHOT) + ";")
            settings.add_init_script("window.__jarvisStub.actions = " + json.dumps(SETTINGS_ACTIONS) + ";")
            settings.goto(origin + "/index.html?view=settings", wait_until="load")
            settings.wait_for_timeout(1600)

            settings_probe = settings.evaluate(PROBE_SOURCE)
            settings_light = capture(settings, out_dir / "jarvis-render-settings.light.png")
            settings.emulate_media(color_scheme="dark")
            settings.wait_for_timeout(300)
            settings_dark = capture(settings, out_dir / "jarvis-render-settings.dark.png")
            settings.emulate_media(color_scheme="light")
            settings.wait_for_timeout(250)

            knowledge = drive_drilldown(settings, log)
            knowledge["render_count_after_focus"] = settings.evaluate("() => window.__knowledgeRenderCount || null")
            knowledge["settings_focus_capture"] = capture(settings, out_dir / "jarvis-render-settings.focus.png")

            receipt["settings"] = {
                "view_state": settings_probe["state"],
                "headings": settings_probe["headings"],
                "mains": settings_probe["mains"],
                "main_classes": settings_probe["mainClasses"],
                "iframes": settings_probe["iframes"],
                "canvases": settings_probe["canvases"],
                "webgl": {
                    "version": settings_probe["glVersion"],
                    "renderer": settings_probe["glRenderer"],
                    "unmasked_renderer": settings_probe["glUnmaskedRenderer"],
                    "gl_error": settings_probe["glError"],
                },
                "slot_morning": settings_probe["slotMorning"],
                "captures": {"light": settings_light, "dark": settings_dark},
                "text": settings_probe["text"],
                "html": settings_probe["html"],
            }
            receipt["knowledge"] = knowledge
            receipt["checks"] = assertions(receipt, contract)
            receipt["status"] = verdict(receipt["checks"])
            browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the Jarvis menubar UI in Chromium and record what it draws.")
    parser.add_argument("--dist", type=Path, default=DEFAULT_DIST)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args(argv)

    if not (arguments.dist / "index.html").exists():
        print("dist is missing index.html: run npm run build in desktop/ first", file=sys.stderr)
        return 2
    try:
        receipt = render_check(
            arguments.dist,
            arguments.contract,
            arguments.out_dir,
            arguments.port,
            log=(lambda message: None) if arguments.json else print,
        )
    except ContractParseError as error:
        print("contract parse failed: " + str(error), file=sys.stderr)
        return 2
    except Exception as error:
        if "playwright" in str(error).lower() or "executable" in str(error).lower():
            print("browser unavailable: " + str(error)[:400], file=sys.stderr)
            return 3
        raise
    receipt_path = arguments.out_dir / "jarvis-desktop-render-check.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    if arguments.json:
        print(json.dumps({"status": receipt["status"], "checks": receipt["checks"], "receipt": str(receipt_path)}, ensure_ascii=False))
    else:
        for item in receipt["checks"]:
            print(("PASS " if item["ok"] else "FAIL ") + item["name"])
        print("status: " + receipt["status"] + " -> " + str(receipt_path))
    return 0 if receipt["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
