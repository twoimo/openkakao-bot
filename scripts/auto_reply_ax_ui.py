"""Read the currently open KakaoTalk chat through System Events.

This is an opt-in alternate source for the chat-list AX watcher. Its reader
never clicks, focuses, or opens anything; the separate send helper is called
only by the guarded auto-reply path after an exact incoming event.
"""

from __future__ import annotations

import os
import math
import re
import selectors
import subprocess
import time
from dataclasses import dataclass
from typing import Callable
from typing import Any
import auto_reply_metrics as perf
from alden_abort import AbortToken

CHAT = os.environ.get("OPENKAKAO_TARGET_CHAT_NAME", "부자멘토멘티").strip() or "부자멘토멘티"
_FIELD = chr(31)
_RECORD = chr(30)
MAX_AX_OUTPUT_BYTES = 128 * 1024
MAX_EXACT_WINDOW_ATTEMPTS = 3
EXACT_WINDOW_RETRY_DELAY_SECONDS = 0.05
AX_ABORT_FOCUS_REQUIRED = "ax_focus_steal_required"
AX_ABORT_GLOBAL = "global_abort"
AX_ERROR_TARGET_INVALID = "ax_target_invalid"
AX_ERROR_TARGET_MISSING = "ax_target_missing"
AX_ERROR_TARGET_AMBIGUOUS = "ax_target_ambiguous"
AX_ERROR_ACTION_UNSUPPORTED = "ax_action_unsupported"
AX_ERROR_TIMEOUT = "ax_timeout"
AX_ERROR_UNAVAILABLE = "ax_unavailable"
AX_ERROR_EFFECT_UNKNOWN = "ax_action_effect_unknown"
MAX_BACKGROUND_AX_TIMEOUT_SECONDS = 5.0
MAX_AX_TARGET_TEXT_BYTES = 512
BACKGROUND_AX_PRESS_ROLES = frozenset(
    {"AXButton", "AXCheckBox", "AXRadioButton", "AXDisclosureTriangle"}
)
_SAFE_BUNDLE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,254}")
_AX_FIELD = chr(31)


@dataclass(frozen=True)
class VirtualCursor:
    x: float
    y: float


@dataclass(frozen=True)
class BackgroundAxResult:
    ok: bool
    error_code: str = ""
    cursor: VirtualCursor | None = None


class FocusStealRequired(RuntimeError):
    pass


class BackgroundAxActionError(RuntimeError):
    """Fixed-code failure safe to expose through the Alden tool boundary."""

    def __init__(self, error_code: str):
        super().__init__(error_code)
        self.error_code = error_code


@dataclass(frozen=True)
class ExactAxTarget:
    """Exact running-process/window/element selector for one background AX action."""

    pid: int
    bundle_id: str
    window_title: str
    element_role: str
    element_identifier: str = ""
    element_title: str = ""
    element_description: str = ""


@dataclass(frozen=True)
class ExactAxResolvedElement:
    rect: tuple[float, float, float, float]


def _bounded_ax_text(value: object, *, required: bool = False) -> str | None:
    if not isinstance(value, str):
        return None
    if required and not value:
        return None
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        return None
    if len(encoded) > MAX_AX_TARGET_TEXT_BYTES:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    return value


def validate_exact_ax_target(target: object) -> bool:
    if not isinstance(target, ExactAxTarget):
        return False
    if isinstance(target.pid, bool) or not isinstance(target.pid, int) or not 1 <= target.pid <= 2**31 - 1:
        return False
    bundle_id = _bounded_ax_text(target.bundle_id, required=True)
    window_title = _bounded_ax_text(target.window_title, required=True)
    role = _bounded_ax_text(target.element_role, required=True)
    identifier = _bounded_ax_text(target.element_identifier)
    title = _bounded_ax_text(target.element_title)
    description = _bounded_ax_text(target.element_description)
    if None in {bundle_id, window_title, role, identifier, title, description}:
        return False
    if _SAFE_BUNDLE_ID.fullmatch(bundle_id or "") is None:
        return False
    if not (role or "").startswith("AX"):
        return False
    return bool(identifier or title or description)


def background_virtual_cursor_action(
    *,
    element_rect: tuple[float, float, float, float],
    perform_ax_action: Callable[[], bool],
    token: AbortToken,
    requires_frontmost_activation: bool = False,
    requires_real_pointer: bool = False,
    post_action_unknown: bool = False,
) -> BackgroundAxResult:
    """Run a background AX action while keeping the real mouse untouched.

    The returned cursor is an overlay/indicator coordinate only. It is never
    passed to CGEvent, cliclick, pyautogui, or another pointer-moving API.
    """

    x, y, width, height = element_rect
    cursor = VirtualCursor(x + max(0.0, width) / 2.0, y + max(0.0, height) / 2.0)
    if token.is_cancelled():
        return BackgroundAxResult(False, AX_ABORT_GLOBAL, cursor)
    if requires_frontmost_activation or requires_real_pointer:
        return BackgroundAxResult(False, AX_ABORT_FOCUS_REQUIRED, cursor)
    try:
        ok = bool(perform_ax_action())
    except FocusStealRequired:
        return BackgroundAxResult(False, AX_ABORT_FOCUS_REQUIRED, cursor)
    except BackgroundAxActionError as exc:
        return BackgroundAxResult(False, exc.error_code, cursor)
    except Exception:
        return BackgroundAxResult(False, "ax_action_failed", cursor)
    if token.is_cancelled():
        return BackgroundAxResult(
            False,
            AX_ERROR_EFFECT_UNKNOWN if post_action_unknown else AX_ABORT_GLOBAL,
            cursor,
        )
    return BackgroundAxResult(ok, "" if ok else "ax_action_failed", cursor)


_BACKGROUND_AX_ERROR_MARKERS = {
    "ALDEN_AX_TARGET_MISSING": AX_ERROR_TARGET_MISSING,
    "ALDEN_AX_TARGET_AMBIGUOUS": AX_ERROR_TARGET_AMBIGUOUS,
    "ALDEN_AX_FOCUS_REQUIRED": AX_ABORT_FOCUS_REQUIRED,
    "ALDEN_AX_ACTION_UNSUPPORTED": AX_ERROR_ACTION_UNSUPPORTED,
    "ALDEN_AX_ACTION_FAILED": "ax_action_failed",
    "ALDEN_AX_EFFECT_UNKNOWN": AX_ERROR_EFFECT_UNKNOWN,
}


def _exact_background_ax_script(target: ExactAxTarget, *, perform: bool) -> str:
    """Build a System Events AX script with no activation or pointer primitives."""

    if not validate_exact_ax_target(target):
        raise BackgroundAxActionError(AX_ERROR_TARGET_INVALID)
    if target.element_role not in BACKGROUND_AX_PRESS_ROLES:
        raise FocusStealRequired("background AXPress role is not allowlisted")

    identifier_check = ""
    if target.element_identifier:
        identifier_check = f'''
        set candidateIdentifier to ""
        try
          set candidateIdentifier to (value of attribute "AXIdentifier" of candidateElement) as text
        end try
        if candidateIdentifier is not {_apple_script_literal(target.element_identifier)} then set isMatch to false
'''
    title_check = ""
    if target.element_title:
        title_check = f'''
        set candidateTitle to ""
        try
          set candidateTitle to (name of candidateElement) as text
        end try
        if candidateTitle is not {_apple_script_literal(target.element_title)} then set isMatch to false
'''
    description_check = ""
    if target.element_description:
        description_check = f'''
        set candidateDescription to ""
        try
          set candidateDescription to (description of candidateElement) as text
        end try
        if candidateDescription is not {_apple_script_literal(target.element_description)} then set isMatch to false
'''
    perform_block = ""
    if perform:
        perform_block = r'''
    try
      perform action "AXPress" of targetElement
    on error
      error "ALDEN_AX_EFFECT_UNKNOWN"
    end try
    if frontmost of targetProcess then error "ALDEN_AX_EFFECT_UNKNOWN"
    return "ok"
'''
    else:
        perform_block = f'''
    set {{elementX, elementY}} to position of targetElement
    set {{elementWidth, elementHeight}} to size of targetElement
    return (elementX as text) & character id 31 & (elementY as text) & character id 31 & (elementWidth as text) & character id 31 & (elementHeight as text)
'''

    return f'''
tell application "System Events"
  set processMatches to every application process whose unix id is {target.pid}
  if (count of processMatches) is 0 then error "ALDEN_AX_TARGET_MISSING"
  if (count of processMatches) is not 1 then error "ALDEN_AX_TARGET_AMBIGUOUS"
  set targetProcess to item 1 of processMatches
  try
    if ((bundle identifier of targetProcess) as text) is not {_apple_script_literal(target.bundle_id)} then error "ALDEN_AX_TARGET_MISSING"
  on error errorMessage
    if errorMessage contains "ALDEN_AX_TARGET_MISSING" then error "ALDEN_AX_TARGET_MISSING"
    error "ALDEN_AX_TARGET_MISSING"
  end try
  if frontmost of targetProcess then error "ALDEN_AX_FOCUS_REQUIRED"

  set windowMatches to {{}}
  repeat with candidateWindow in every window of targetProcess
    try
      if ((name of candidateWindow) as text) is {_apple_script_literal(target.window_title)} then set end of windowMatches to contents of candidateWindow
    end try
  end repeat
  if (count of windowMatches) is 0 then error "ALDEN_AX_TARGET_MISSING"
  if (count of windowMatches) is not 1 then error "ALDEN_AX_TARGET_AMBIGUOUS"
  set targetWindow to item 1 of windowMatches

  set elementMatches to {{}}
  set candidateElements to entire contents of targetWindow
  repeat with candidateElement in candidateElements
    set isMatch to true
    try
      if ((role of candidateElement) as text) is not {_apple_script_literal(target.element_role)} then set isMatch to false
    on error
      set isMatch to false
    end try
{identifier_check}{title_check}{description_check}
    if isMatch then set end of elementMatches to contents of candidateElement
  end repeat
  if (count of elementMatches) is 0 then error "ALDEN_AX_TARGET_MISSING"
  if (count of elementMatches) is not 1 then error "ALDEN_AX_TARGET_AMBIGUOUS"
  set targetElement to item 1 of elementMatches

  set supportedActionNames to {{}}
  try
    repeat with candidateAction in every action of targetElement
      set end of supportedActionNames to ((name of candidateAction) as text)
    end repeat
  on error
    error "ALDEN_AX_ACTION_UNSUPPORTED"
  end try
  if supportedActionNames does not contain "AXPress" then error "ALDEN_AX_ACTION_UNSUPPORTED"
{perform_block}
end tell
'''


class SystemEventsBackgroundAxAdapter:
    """Bounded native adapter for one exact background AXPress action."""

    def __init__(self, *, script_runner: Callable[[list[str], str, float], tuple[int, bytes, bytes]] | None = None):
        self._script_runner = script_runner or _run_bounded_osascript

    @staticmethod
    def _timeout(value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise BackgroundAxActionError(AX_ERROR_TARGET_INVALID)
        timeout = float(value)
        if not math.isfinite(timeout) or timeout <= 0.0 or timeout > MAX_BACKGROUND_AX_TIMEOUT_SECONDS:
            raise BackgroundAxActionError(AX_ERROR_TARGET_INVALID)
        return timeout

    @staticmethod
    def _raise_script_failure(stderr: bytes, *, effect_uncertain: bool = False) -> None:
        try:
            message = stderr.decode("utf-8", errors="replace")
        except Exception:
            message = ""
        for marker, error_code in _BACKGROUND_AX_ERROR_MARKERS.items():
            if marker in message:
                if error_code == AX_ABORT_FOCUS_REQUIRED:
                    raise FocusStealRequired(marker)
                raise BackgroundAxActionError(error_code)
        raise BackgroundAxActionError(
            AX_ERROR_EFFECT_UNKNOWN if effect_uncertain else AX_ERROR_UNAVAILABLE
        )

    def _run(self, target: ExactAxTarget, *, timeout_seconds: float, perform: bool) -> bytes:
        timeout = self._timeout(timeout_seconds)
        script = _exact_background_ax_script(target, perform=perform)
        try:
            returncode, stdout, stderr = self._script_runner(
                ["/usr/bin/osascript", "-"], script, timeout
            )
        except subprocess.TimeoutExpired as exc:
            raise BackgroundAxActionError(
                AX_ERROR_EFFECT_UNKNOWN if perform else AX_ERROR_TIMEOUT
            ) from exc
        except (OSError, ValueError) as exc:
            raise BackgroundAxActionError(AX_ERROR_UNAVAILABLE) from exc
        if returncode != 0:
            self._raise_script_failure(stderr, effect_uncertain=perform)
        return stdout

    def resolve_exact(self, target: ExactAxTarget, *, timeout_seconds: float) -> ExactAxResolvedElement:
        stdout = self._run(target, timeout_seconds=timeout_seconds, perform=False)
        try:
            fields = stdout.decode("utf-8").strip().split(_AX_FIELD)
            if len(fields) != 4:
                raise ValueError("unexpected AX rect")
            rect = tuple(float(value) for value in fields)
        except (UnicodeDecodeError, ValueError) as exc:
            raise BackgroundAxActionError(AX_ERROR_UNAVAILABLE) from exc
        x, y, width, height = rect
        if not all(math.isfinite(value) for value in rect) or width < 0.0 or height < 0.0:
            raise BackgroundAxActionError(AX_ERROR_UNAVAILABLE)
        return ExactAxResolvedElement((x, y, width, height))

    def perform_exact(self, target: ExactAxTarget, *, timeout_seconds: float) -> bool:
        stdout = self._run(target, timeout_seconds=timeout_seconds, perform=True)
        try:
            if stdout.decode("utf-8").strip() == "ok":
                return True
        except UnicodeDecodeError:
            pass
        raise BackgroundAxActionError(AX_ERROR_EFFECT_UNKNOWN)

_EXACT_WINDOW_SCRIPT = r'''
tell application "System Events"
  tell process "KakaoTalk"
    set exactWindowCount to 0
    repeat with candidateWindow in every window
      try
        if (name of candidateWindow as text) is __OPENKAKAO_CHAT_LITERAL__ then
          set exactWindowCount to exactWindowCount + 1
        end if
      end try
    end repeat
    return exactWindowCount
  end tell
end tell
'''

_SCRIPT = r'''
tell application "System Events"
  tell process "KakaoTalk"
    set allWindows to every window
    set exactWindowCount to 0
    set w to missing value
    repeat with candidateWindow in allWindows
      try
        if (name of candidateWindow as text) is __OPENKAKAO_CHAT_LITERAL__ then
          set exactWindowCount to exactWindowCount + 1
          set w to contents of candidateWindow
        end if
      end try
    end repeat
    if exactWindowCount is not 1 then error "ambiguous or missing exact KakaoTalk chat window"
    set {wx, wy} to position of w
    set {ww, wh} to size of w
    set centerX to wx + (ww / 2)
    set t to first table of first scroll area of w
    set n to count of rows of t
    set firstIndex to n - 2
    if firstIndex < 1 then set firstIndex to 1
    set sep to character id 31
    set recsep to character id 30
    set out to {}
    repeat with i from firstIndex to n
      set r to row i of t
      set rowVisible to false
      try
        set {rx, ry} to position of r
        set {rw, rh} to size of r
        if (ry + rh > wy) and (ry < wy + wh) then set rowVisible to true
      end try
      if rowVisible then
        set fields to {(i as text)}
        set imageRect to ""
        set rowDirection to "unknown"
        try
          set {rx, ry} to position of r
          set {rw, rh} to size of r
          if (rx + (rw / 2)) > centerX then
            set rowDirection to "outgoing"
          else if (rx + (rw / 2)) < centerX then
            set rowDirection to "incoming"
          end if
        end try
        set direction to "unknown"
        set hasTextArea to false
        set hasImage to false
        repeat with c in (UI elements of r)
          repeat with e in (UI elements of c)
            try
              set erole to role of e as text
              if erole is "AXStaticText" then
                set end of fields to "static=" & (value of e as text)
              else if erole is "AXTextArea" then
                set hasTextArea to true
                try
                  set {ex, ey} to position of e
                  if ex >= centerX then
                    set direction to "outgoing"
                  else
                    set direction to "incoming"
                  end if
                end try
                set end of fields to "direction=" & direction
                set end of fields to "text=" & (value of e as text)
              else if erole is "AXImage" then
                set hasImage to true
                try
                  set {ix, iy} to position of e
                  set {iw, ih} to size of e
                  set imageRect to (ix as text) & "," & (iy as text) & "," & (iw as text) & "," & (ih as text)
                end try
                set end of fields to "direction=" & rowDirection
              end if
            end try
          end repeat
        end repeat
        if hasImage and not hasTextArea then
          set end of fields to "attachment=image"
          if imageRect is not "" then set end of fields to "image_rect=" & imageRect
        end if
        set AppleScript's text item delimiters to sep
        set end of out to (fields as text)
      end if
    end repeat
    set AppleScript's text item delimiters to recsep
    return out as text
  end tell
end tell
'''
_CONFIRM_SCRIPT = r'''
tell application "System Events"
  tell process "KakaoTalk"
    set allWindows to every window
    set exactWindowCount to 0
    set w to missing value
    repeat with candidateWindow in allWindows
      try
        if (name of candidateWindow as text) is __OPENKAKAO_CHAT_LITERAL__ then
          set exactWindowCount to exactWindowCount + 1
          set w to contents of candidateWindow
        end if
      end try
    end repeat
    if exactWindowCount is not 1 then error "ambiguous or missing exact KakaoTalk chat window"
    set {wx, wy} to position of w
    set {ww, wh} to size of w
    set centerX to wx + (ww / 2)
    set t to first table of first scroll area of w
    set n to count of rows of t
    set firstIndex to {min_row_index}
    set expectedMessage to {apple_script_message}
    set found to false
    if firstIndex < 1 then set firstIndex to 1
    if firstIndex <= n then
      repeat with i from firstIndex to n
        set r to row i of t
        set rowOutgoing to false
        try
          set {rx, ry} to position of r
          set {rw, rh} to size of r
          if rx >= centerX then set rowOutgoing to true
        end try
        repeat with c in (UI elements of r)
          repeat with e in (UI elements of c)
            try
              if (role of e as text) is "AXTextArea" then
                considering case, diacriticals
                  if ((value of e) as text) is expectedMessage then
                    set outgoingEvidence to rowOutgoing
                    try
                      set {ex, ey} to position of e
                      set {ew, eh} to size of e
                      if (ex + (ew / 2)) > centerX then set outgoingEvidence to true
                    end try
                    try
                      set outgoingMarker to (description of e as text)
                      if outgoingMarker is "outgoing" or outgoingMarker is "sent" or outgoingMarker is "보냄" or outgoingMarker is "발신" then set outgoingEvidence to true
                    end try
                    if outgoingEvidence then
                      set found to true
                      exit repeat
                    end if
                  end if
                end considering
              end if
            end try
          end repeat
          if found then exit repeat
        end repeat
        if found then exit repeat
      end repeat
    end if
    return found
  end tell
end tell
'''


def _run_bounded_osascript(
    command: list[str],
    script: str,
    timeout: float,
) -> tuple[int, bytes, bytes]:
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
    )
    if process.stdin is not None:
        process.stdin.write(script.encode("utf-8"))
        process.stdin.close()
    selector = selectors.DefaultSelector()
    outputs: dict[object, bytearray] = {}
    try:
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                outputs[stream] = bytearray()
                selector.register(stream, selectors.EVENT_READ)
        deadline = time.monotonic() + max(0.0, timeout)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                process.terminate()
                raise subprocess.TimeoutExpired(command, timeout)
            events = selector.select(remaining)
            if not events:
                process.terminate()
                raise subprocess.TimeoutExpired(command, timeout)
            for key, _ in events:
                stream = key.fileobj
                chunk = os.read(stream.fileno(), MAX_AX_OUTPUT_BYTES + 1)
                if not chunk:
                    selector.unregister(stream)
                    continue
                output = outputs[stream]
                if len(output) + len(chunk) > MAX_AX_OUTPUT_BYTES:
                    process.terminate()
                    raise ValueError("AX output exceeded bound")
                output.extend(chunk)
        process.wait(timeout=max(0.0, deadline - time.monotonic()))
        return (
            int(process.returncode or 0),
            bytes(outputs.get(process.stdout, b"")),
            bytes(outputs.get(process.stderr, b"")),
        )
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                try:
                    selector.unregister(stream)
                except Exception:
                    pass
                stream.close()
        selector.close()


@perf.timed("ax.exact_window_available")
def exact_window_available(limit_seconds: float = 3.0) -> bool:
    """Return whether exactly one target window exists without reading its content."""
    if limit_seconds <= 0.0:
        return False
    deadline = time.monotonic() + limit_seconds
    for attempt in range(MAX_EXACT_WINDOW_ATTEMPTS):
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            return False
        try:
            returncode, stdout_bytes, _ = _run_bounded_osascript(
                ["/usr/bin/osascript", "-"],
                _script_for_chat(_EXACT_WINDOW_SCRIPT),
                remaining,
            )
        except (OSError, subprocess.TimeoutExpired, ValueError):
            returncode, stdout_bytes = 1, b""
        if returncode == 0:
            try:
                count = int(stdout_bytes.decode("utf-8").strip())
            except (UnicodeDecodeError, ValueError):
                count = 0
            if count > 1:
                return False
            if count == 1:
                return True
        if attempt + 1 >= MAX_EXACT_WINDOW_ATTEMPTS:
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            return False
        time.sleep(min(EXACT_WINDOW_RETRY_DELAY_SECONDS, remaining))
    return False


@perf.timed("ax.snapshot")
def snapshot(limit_seconds: float = 3.0) -> list[dict[str, Any]]:
    """Return rendered rows, or an empty list when GUI access is unavailable."""
    try:
        returncode, stdout_bytes, _ = _run_bounded_osascript(
            ["/usr/bin/osascript"],
            _script_for_chat(_SCRIPT),
            limit_seconds,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return []
    if returncode != 0:
        return []
    try:
        stdout = stdout_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return []

    rows: list[dict[str, Any]] = []
    for record in stdout.split(_RECORD):
        record = record.strip()
        if not record:
            continue
        fields = record.split(_FIELD)
        try:
            row: dict[str, Any] = {"row_index": int(fields[0])}
        except (ValueError, IndexError):
            continue
        statics: list[str] = []
        for field in fields[1:]:
            key, separator, value = field.partition("=")
            if not separator:
                continue
            if value.strip().lower() == "missing value":
                value = ""
            if key == "static":
                if value:
                    statics.append(value)
            elif key in {"direction", "text", "attachment", "image_rect"}:
                row[key] = value
        row["static"] = statics
        if isinstance(row.get("text"), str) or row.get("attachment"):
            if row.get("attachment") and not row.get("text"):
                row["text"] = "[사진]"
            rows.append(row)
    return rows


def _apple_script_literal(value: str) -> str:
    parts: list[str] = []
    for line in value.splitlines(keepends=True) or [""]:
        if line.endswith("\r\n"):
            line, has_newline = line[:-2], True
        elif line.endswith(("\r", "\n")):
            line, has_newline = line[:-1], True
        else:
            has_newline = False
        escaped = line.replace("\\", "\\\\").replace('"', '\\"')
        parts.append(f'"{escaped}"')
        if has_newline:
            parts.append("return")
    return " & ".join(parts)


def _script_for_chat(script: str) -> str:
    if not CHAT or any(ord(char) < 32 for char in CHAT):
        raise ValueError("chat name contains unsupported control characters")
    return script.replace("__OPENKAKAO_CHAT_LITERAL__", _apple_script_literal(CHAT))


def _confirmation_script(message: str, min_row_index: int) -> str:
    """Build the compact exact-text AX confirmation query."""
    script = _script_for_chat(
        _CONFIRM_SCRIPT.replace("{min_row_index}", str(max(0, min_row_index)))
    )
    return script.replace("{apple_script_message}", _apple_script_literal(message))


def visible_outgoing(message: str, limit_seconds: float = 3.0, min_row_index: int = 0) -> bool:
    """Confirm exact text only in a post-baseline outgoing bubble.

    Matching text without outgoing geometry or an explicit outgoing marker is
    intentionally uncertain, so an identical incoming message cannot confirm
    delivery.
    """
    deadline = time.monotonic() + max(0.0, limit_seconds)
    remaining = deadline - time.monotonic()
    if remaining <= 0.0:
        return False
    script = _confirmation_script(message, min_row_index)
    try:
        returncode, stdout_bytes, _ = _run_bounded_osascript(
            ["/usr/bin/osascript", "-"],
            script,
            remaining,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False
    try:
        stdout = stdout_bytes.decode("utf-8").strip()
    except UnicodeDecodeError:
        return False
    return returncode == 0 and stdout == "true"


@perf.timed("ax.local_send")
def send_via_system_events(message: str, timeout_seconds: float = 5.0) -> bool:
    """Set and click the composer in the already-open exact KakaoTalk window."""
    script = r'''
tell application "System Events"
  tell process "KakaoTalk"
    set allWindows to every window
    set exactWindowCount to 0
    set w to missing value
    repeat with candidateWindow in allWindows
      try
        if (name of candidateWindow as text) is __OPENKAKAO_CHAT_LITERAL__ then
          set exactWindowCount to exactWindowCount + 1
          set w to contents of candidateWindow
        end if
      end try
    end repeat
    if exactWindowCount is not 1 then error "ambiguous or missing exact KakaoTalk chat window"
    set composer to missing value
    repeat with top in (UI elements of w)
      repeat with e in (UI elements of top)
        try
          if (role of e as text) is "AXTextArea" and (description of e as text) is "메시지 입력" then
            set composer to e
            exit repeat
          end if
        end try
      end repeat
      if composer is not missing value then exit repeat
    end repeat
    if composer is missing value then error "KakaoTalk message composer not found"
    set value of composer to {apple_script_message}
    set sendButton to missing value
    repeat with e in (UI elements of w)
      try
        if (role of e as text) is "AXButton" and (name of e as text) is "전송" then
          set sendButton to e
          exit repeat
        end if
      end try
    end repeat
    if sendButton is missing value then error "KakaoTalk send button not found"
    click sendButton
  end tell
end tell
'''
    script = _script_for_chat(script)
    script = script.replace("{apple_script_message}", _apple_script_literal(message))
    try:
        returncode, _, _ = _run_bounded_osascript(
            ["/usr/bin/osascript", "-"],
            script,
            timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False
    return returncode == 0
