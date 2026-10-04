#!/usr/bin/env python3
"""Read-only Alden metadata via CLI or the standard MCP stdio transport.

No daemon, configuration writes, credentials, conversations, vault notes, dot
state, model generation, process control, or arbitrary tool arguments. Only
the startup operator chooses paths. JSON-RPC errors never echo input text.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
from pathlib import Path
import plistlib
import queue
import re
import select
import socket
import stat
import sys
import threading
import time

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_WIRE_BYTES = 64 * 1024
MAX_HTTP_BYTES = 256 * 1024
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
TOOL_NAME = "alden_status"
TOOL_TIMEOUT_SECONDS = 1.5
MAX_PENDING_FRAMES = 16


class MetadataUnavailable(Exception):
    pass


class CallStopped(Exception):
    """A fixed internal stop condition; never retains the client's reason."""


class CallControl:
    def __init__(self, timeout: float = TOOL_TIMEOUT_SECONDS):
        self.deadline = time.monotonic() + timeout
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.closers = []

    def check(self):
        if self.stopped.is_set() or time.monotonic() >= self.deadline:
            raise CallStopped()

    def remaining(self):
        self.check()
        return max(0.001, self.deadline - time.monotonic())

    def attach(self, closer):
        with self.lock:
            self.check()
            self.closers.append(closer)

    def detach(self, closer):
        with self.lock:
            if closer in self.closers:
                self.closers.remove(closer)

    def stop(self):
        self.stopped.set()
        with self.lock:
            closers = tuple(self.closers)
        for closer in closers:
            try:
                closer()
            except Exception:
                pass


def _check(control):
    if control is not None:
        control.check()


def _safe_path(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise MetadataUnavailable()
    for component in [*reversed(path.parents), path]:
        if stat.S_ISLNK(component.lstat().st_mode):
            raise MetadataUnavailable()


def _read_bytes(path: Path, limit: int = MAX_FILE_BYTES, control=None) -> bytes:
    _check(control)
    _safe_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_size > limit:
            raise MetadataUnavailable()
        with os.fdopen(fd, "rb", closefd=False) as file:
            chunks, total = [], 0
            while True:
                _check(control)
                chunk = file.read(min(1024 * 1024, limit + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > limit:
                    raise MetadataUnavailable()
            data = b"".join(chunks)
        if len(data) > limit:
            raise MetadataUnavailable()
        _check(control)
        return data
    finally:
        os.close(fd)


def _strict_json(data: bytes):
    def reject_constant(_value):
        raise ValueError()
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    return json.loads(data, parse_constant=reject_constant, object_pairs_hook=unique_object)


def _read_object(path: Path, control=None) -> dict:
    result = _strict_json(_read_bytes(path, control=control))
    _check(control)
    if not isinstance(result, dict):
        raise MetadataUnavailable()
    return result


def _counter(value, maximum: int = 1_000_000_000):
    return value if type(value) is int and 0 <= value <= maximum else None


def _models(port: int, control=None) -> dict:
    _check(control)
    timeout = min(0.5, control.remaining()) if control is not None else 0.5
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    owned_socket = None

    def close_owned_connection():
        # Only a socket created by this read-only request can be interrupted.
        sock = owned_socket or getattr(connection, "sock", None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        # HTTPConnection.close can acquire a response-buffer lock. Only wake
        # the owned socket here; the worker closes its handles in finally.

    try:
        if control is not None:
            control.attach(close_owned_connection)
        # A fixed literal loopback address, fixed GET path, no redirects/proxy.
        connection.request("GET", "/v1/models", headers={"Accept": "application/json"})
        owned_socket = getattr(connection, "sock", None)
        _check(control)
        response = connection.getresponse()
        _check(control)
        if response.status != 200:
            raise MetadataUnavailable()
        body = response.read(MAX_HTTP_BYTES + 1)
        _check(control)
        if len(body) > MAX_HTTP_BYTES:
            raise MetadataUnavailable()
        value = _strict_json(body)
        records = value.get("data") if isinstance(value, dict) else None
        if not isinstance(records, list) or len(records) > 256:
            raise MetadataUnavailable()
        loaded = sum(isinstance(row, dict) and row.get("loaded") is True for row in records)
        # Do not echo model IDs, paths, errors, or arbitrary runtime strings.
        return {"state": "reachable", "listed_count": len(records), "loaded_count": loaded}
    except (OSError, ValueError, RecursionError, http.client.HTTPException, MetadataUnavailable):
        _check(control)
        return {"state": "unavailable", "listed_count": None, "loaded_count": None}
    finally:
        if control is not None:
            control.detach(close_owned_connection)
        connection.close()


class StatusCollector:
    def __init__(self, state_root: Path, app_dir: Path, *, offline: bool = False):
        self.state_root = state_root
        self.app_dir = app_dir
        self.offline = offline

    def collect(self, control=None) -> dict:
        _check(control)
        app = {"state": "unavailable", "version": None}
        try:
            value = plistlib.loads(_read_bytes(self.app_dir / "Contents/Info.plist", 512 * 1024, control))
            _check(control)
            version = value.get("CFBundleShortVersionString") if isinstance(value, dict) else None
            if isinstance(version, str) and re.fullmatch(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}", version):
                app = {"state": "installed", "version": version}
        except (OSError, ValueError, RecursionError, plistlib.InvalidFileException, MetadataUnavailable):
            pass
        osk = {"state": "unavailable", "pending": None, "conflicts": None,
               "generation": None, "age_seconds": None}
        try:
            value = _read_object(self.state_root / "knowledge/osk/sync.json", control)
            pending, conflicts = _counter(value.get("pending")), _counter(value.get("conflicts"))
            generation = _counter(value.get("generation"))
            synced = _counter(value.get("synced_at"), 9_999_999_999)
            age = int(time.time()) - synced if synced is not None else None
            if value.get("engine") == "v4.1.2" and pending is not None and conflicts is not None and age is not None and age >= 0 and type(value.get("stale")) is bool:
                osk = {"state": "stale" if value["stale"] or age > 180 else "ready",
                       "pending": pending, "conflicts": conflicts,
                       "generation": generation, "age_seconds": min(age, 1_000_000_000)}
                try:
                    producer=_read_object(self.state_root/'knowledge/osk/producer.json',control)
                    checked=_counter(producer.get('checked_at'),9_999_999_999)
                    checked_age=int(time.time())-checked if checked is not None else None
                    pointer=_read_object(self.state_root/'knowledge/corpus/current.json',control)
                    signature=value.get('source_signature')
                    verified=(isinstance(signature,str) and re.fullmatch('[0-9a-f]{64}',signature)
                              and producer.get('source_signature')==signature and producer.get('saved_corpus_ready') is True
                              and producer.get('archive_snapshot')==pointer.get('snapshot') and pending==0 and conflicts==0 and not value['stale']
                              and checked_age is not None and 0<=checked_age<=180)
                    if verified:
                        osk.update(state='ready',checked_age_seconds=checked_age,
                                   collection_state=producer.get('collection') if producer.get('collection') in ('complete','pending','waiting','not_requested') else 'unavailable',
                                   freshness_scope='current_published_corpus')
                except (OSError,ValueError,RecursionError,MetadataUnavailable):
                    pass
        except (OSError, ValueError, RecursionError, MetadataUnavailable):
            pass
        endpoint = {"state": "not_probed", "listed_count": None, "loaded_count": None}
        _check(control)
        return {"schema_version": 1, "scope": "read_only_metadata",
                "connection_state": "dot_not_attested",
                "app": app, "osk": osk,
                "generation_endpoint": dict(endpoint) if self.offline else _models(11234, control),
                "embedding_endpoint": dict(endpoint) if self.offline else _models(11236, control),
                "voice_validation": "not_tested", "primary_ui_validation": "not_tested"}


def _error(identifier, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": identifier, "error": {"code": code, "message": message}}


class StdioServer:
    def __init__(self, collector: StatusCollector, *, tool_timeout: float = TOOL_TIMEOUT_SECONDS):
        if not 0 < tool_timeout <= 60:
            raise ValueError("Invalid local tool timeout")
        self.collector = collector
        self.initialized = False
        self.ready = False
        self.tool_timeout = tool_timeout

    def dispatch(self, request, control=None, *, validate_only=False):
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
            return _error(None, -32600, "Invalid request")
        identifier = request.get("id")
        if "id" in request and not (type(identifier) is int and abs(identifier) <= 2**53 - 1 or isinstance(identifier, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", identifier)):
            return _error(None, -32600, "Invalid request id")
        method = request.get("method")
        params = request.get("params", {})
        if not isinstance(method, str) or not isinstance(params, dict):
            return None if "id" not in request else _error(identifier, -32600, "Invalid request")
        if "id" not in request:
            if method == "notifications/initialized" and self.initialized:
                self.ready = True
            return None
        if method == "initialize":
            if self.initialized or not isinstance(params.get("protocolVersion"), str) or not isinstance(params.get("capabilities"), dict) or not isinstance(params.get("clientInfo"), dict):
                return _error(identifier, -32602, "Invalid initialization")
            requested = params["protocolVersion"]
            self.initialized = True
            result = {"protocolVersion": requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                      "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "alden-readonly-status", "version": "0.2.0"}}
        elif method == "ping":
            result = {}
        elif not self.ready:
            return _error(identifier, -32002, "Initialization required")
        elif method == "tools/list":
            if set(params) - {"_meta"}:
                return _error(identifier, -32602, "No pagination or arguments supported")
            result = {"tools": [{"name": TOOL_NAME, "description": "Read Alden version, aggregate OSK freshness and fixed local endpoint reachability. Does not read conversations, vault notes, or dot state. Does not establish live voice/UI success.",
                                 "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                                 "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}}]}
        elif method == "tools/call":
            if set(params) - {"name", "arguments", "_meta"} or params.get("name") != TOOL_NAME or params.get("arguments", {}) != {}:
                return _error(identifier, -32602, "Unsupported tool or arguments")
            if validate_only:
                return None
            try:
                content = self.collector.collect(control=control) if control is not None else self.collector.collect()
                _check(control)
            except CallStopped:
                raise
            except Exception:
                # A collector bug must never export raw exception text or paths.
                return {"jsonrpc": "2.0", "id": identifier,
                        "result": {"content": [{"type": "text", "text": "metadata_unavailable"}], "isError": True}}
            result = {"content": [{"type": "text", "text": json.dumps(content, separators=(",", ":"), allow_nan=False)}], "isError": False}
        else:
            return _error(identifier, -32601, "Method not found")
        return {"jsonrpc": "2.0", "id": identifier, "result": result}

    def _frames(self, reader, stopped):
        try:
            descriptor = reader.fileno()
        except (AttributeError, OSError, ValueError):
            descriptor = None
        if descriptor is not None:
            # Raw fd reads avoid holding a Python stdio buffer lock at shutdown.
            buffer, oversized = b"", False
            while not stopped.is_set():
                if not select.select([descriptor], [], [], 0.1)[0]:
                    continue
                chunk = os.read(descriptor, 4096)
                if not chunk:
                    if buffer or oversized:
                        yield _error(None, -32700, "Incomplete message"), None
                    return
                for part in chunk.splitlines(keepends=True):
                    buffer += part if not oversized else b""
                    if len(buffer) > MAX_WIRE_BYTES:
                        buffer, oversized = b"", True
                    if part.endswith(b"\n"):
                        if oversized:
                            yield _error(None, -32700, "Message exceeds limit"), None
                        else:
                            yield self._decode(buffer)
                        buffer, oversized = b"", False
            return
        while not stopped.is_set():
            line = reader.readline(MAX_WIRE_BYTES + 1)
            if not line:
                return
            if len(line) > MAX_WIRE_BYTES:
                if not line.endswith(b"\n"):
                    while True:
                        rest = reader.readline(MAX_WIRE_BYTES + 1)
                        if not rest or rest.endswith(b"\n"):
                            break
                yield _error(None, -32700, "Message exceeds limit"), None
            else:
                yield self._decode(line)

    def _decode(self, line):
        try:
            return None, _strict_json(line)
        except (ValueError, UnicodeError, RecursionError):
            return _error(None, -32700, "Parse error"), None

    def serve(self, reader, writer) -> None:
        events, jobs = queue.Queue(MAX_PENDING_FRAMES), queue.Queue(1)
        stopped = threading.Event()
        active = None

        def emit(value):
            if value is not None:
                writer.write((json.dumps(value, separators=(",", ":"), allow_nan=False) + "\n").encode())
                writer.flush()

        def post(event):
            while not stopped.is_set():
                try:
                    events.put(event, timeout=0.1)
                    return
                except queue.Full:
                    pass

        def read_frames():
            try:
                for reply, request in self._frames(reader, stopped):
                    post(("input", reply, request))
            except (OSError, ValueError):
                pass
            finally:
                post(("eof", None, None))

        def work():
            while not stopped.is_set():
                job = jobs.get()
                if job is None or stopped.is_set():
                    return
                request, control = job
                try:
                    reply = self.dispatch(request, control)
                except CallStopped:
                    reply = None
                except Exception:
                    reply = {"jsonrpc": "2.0", "id": request["id"],
                             "result": {"content": [{"type": "text", "text": "metadata_unavailable"}], "isError": True}}
                post(("done", job, reply))

        feeder = threading.Thread(target=read_frames, daemon=True)
        worker = threading.Thread(target=work, daemon=True)
        feeder.start()
        worker.start()
        try:
            while True:
                wait = None
                if active is not None and not active[1].stopped.is_set():
                    wait = max(0, active[1].deadline - time.monotonic())
                    if wait == 0:
                        active[1].stop()
                        emit({"jsonrpc": "2.0", "id": active[0]["id"],
                              "result": {"content": [{"type": "text", "text": "metadata_deadline_exceeded"}], "isError": True}})
                        continue
                try:
                    kind, value, extra = events.get(timeout=wait)
                except queue.Empty:
                    continue
                if kind == "eof":
                    return
                if kind == "done":
                    if value is active:
                        if not active[1].stopped.is_set():
                            if extra is None or time.monotonic() >= active[1].deadline:
                                emit({"jsonrpc": "2.0", "id": active[0]["id"],
                                      "result": {"content": [{"type": "text", "text": "metadata_deadline_exceeded"}], "isError": True}})
                            else:
                                emit(extra)
                        active = None
                    continue
                if value is not None:
                    emit(value)
                    continue
                request = extra
                if isinstance(request, dict) and request.get("jsonrpc") == "2.0" and "id" not in request and request.get("method") == "notifications/cancelled":
                    params = request.get("params")
                    identifier = params.get("requestId") if isinstance(params, dict) else None
                    if active is not None and type(identifier) is type(active[0]["id"]) and identifier == active[0]["id"]:
                        # Do not retain or log the caller's optional reason.
                        active[1].stop()
                    continue
                is_call = isinstance(request, dict) and "id" in request and request.get("method") == "tools/call"
                if not is_call:
                    emit(self.dispatch(request))
                    continue
                invalid = self.dispatch(request, validate_only=True)
                if invalid is not None:
                    emit(invalid)
                elif active is not None:
                    emit(_error(request["id"], -32004, "Status request already in progress"))
                else:
                    active = (request, CallControl(self.tool_timeout))
                    jobs.put_nowait(active)
        finally:
            stopped.set()
            if active is not None:
                active[1].stop()
            try:
                jobs.put_nowait(None)
            except queue.Full:
                pass
            feeder.join(timeout=0.15)
            worker.join(timeout=0.05)


def bounded_collect(collector, timeout=TOOL_TIMEOUT_SECONDS):
    """One-shot caller deadline; an OS-stalled worker cannot block process exit."""
    control, results = CallControl(timeout), queue.Queue(1)

    def work():
        try:
            result = collector.collect(control=control)
            control.check()
            results.put((True, result))
        except Exception:
            results.put((False, None))

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    try:
        valid, result = results.get(timeout=control.remaining())
        if not valid:
            control.check()
            raise MetadataUnavailable()
        return result
    except queue.Empty:
        raise CallStopped() from None
    finally:
        control.stop()
        worker.join(timeout=0.05)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, default=Path.home() / "Library/Application Support/openkakao/bujamentor")
    parser.add_argument("--app-dir", type=Path, default=Path("/Applications/Alden.app"))
    parser.add_argument("--offline", action="store_true", help="Skip even the two fixed loopback metadata GETs")
    parser.add_argument("--once", action="store_true", help="Print the same collector result once, without MCP")
    args = parser.parse_args()
    if not args.state_root.is_absolute() or not args.app_dir.is_absolute() or ".." in args.state_root.parts or ".." in args.app_dir.parts:
        parser.error("startup paths must be absolute without parent traversal")
    collector = StatusCollector(args.state_root, args.app_dir, offline=args.offline)
    if args.once:
        try:
            value = bounded_collect(collector)
        except (CallStopped, MetadataUnavailable):
            value = {"schema_version": 1, "scope": "read_only_metadata", "state": "metadata_unavailable"}
        print(json.dumps(value, separators=(",", ":"), allow_nan=False))
    else:
        try:
            StdioServer(collector).serve(sys.stdin.buffer, sys.stdout.buffer)
        except (BrokenPipeError, KeyboardInterrupt):
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
