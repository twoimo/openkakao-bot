import io
import json
import os
from pathlib import Path
import plistlib
import select
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import alden_status_mcp as m


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.app = self.root / "Alden.app"
        (self.app / "Contents").mkdir(parents=True)
        (self.app / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": "0.3.10", "private": "bearer-secret-canary"}))
        self.file = self.root / "state/knowledge/osk/sync.json"
        self.file.parent.mkdir(parents=True)
        self.value = {"engine": "v4.1.2", "pending": 3, "conflicts": 1, "generation": 17,
                      "synced_at": 1700000000, "stale": False,
                      "managed": {"private": {"source": {"description": "private-conversation-canary"}}},
                      "token": "bearer-secret-canary"}
        self.file.write_text(json.dumps(self.value))
        self.collector = m.StatusCollector(self.root / "state", self.app, offline=True)

    def tearDown(self):
        self.tmp.cleanup()

    def collect(self):
        with patch.object(m.time, "time", return_value=1700000010):
            return self.collector.collect()

    def test_allowlist_omits_every_private_content_field(self):
        output = self.collect()
        self.assertEqual(output["osk"], {"state": "ready", "pending": 3, "conflicts": 1, "generation": 17, "age_seconds": 10})
        self.assertEqual(output["app"], {"state": "installed", "version": "0.3.10"})
        raw = json.dumps(output)
        for canary in ("private-conversation-canary", "bearer-secret-canary", str(self.root)):
            self.assertNotIn(canary, raw)
        self.assertEqual(output["connection_state"], "dot_not_attested")

    def test_stale_and_future_observations_do_not_claim_ready(self):
        self.value["synced_at"] -= 181
        self.file.write_text(json.dumps(self.value))
        self.assertEqual(self.collect()["osk"]["state"], "stale")
        self.value["synced_at"] = 1800000000
        self.file.write_text(json.dumps(self.value))
        self.assertEqual(self.collect()["osk"]["state"], "unavailable")

    def test_verified_saved_corpus_is_distinct_from_waiting_original_collection(self):
        self.value.update(pending=0,conflicts=0,source_signature='a'*64,synced_at=1699999000)
        self.file.write_text(json.dumps(self.value))
        producer=self.file.with_name('producer.json')
        data={'checked_at':1700000005,'source_signature':'a'*64,'saved_corpus_ready':True,'archive_snapshot':'v1','collection':'waiting'}
        producer.write_text(json.dumps(data));pointer=self.root/'state/knowledge/corpus/current.json';pointer.parent.mkdir()
        pointer.write_text(json.dumps({'snapshot':'v1'}))
        result=self.collect()['osk'];self.assertEqual(result['state'],'ready');self.assertEqual(result['age_seconds'],1010)
        self.assertEqual(result['checked_age_seconds'],5);self.assertEqual(result['collection_state'],'waiting')
        self.assertEqual(result['freshness_scope'],'current_published_corpus')
        pointer.write_text(json.dumps({'snapshot':'v2'}));self.assertEqual(self.collect()['osk']['state'],'stale')

    def test_invalid_numeric_counts_fail_closed(self):
        for invalid in (True, -1, "private-conversation-canary", 10**20, 1.5):
            with self.subTest(invalid=invalid):
                self.value["pending"] = invalid
                self.file.write_text(json.dumps(self.value))
                self.assertEqual(self.collect()["osk"]["state"], "unavailable")

    def test_duplicate_keys_nan_and_nonobject_fail_closed(self):
        for data in (b'{"pending":1,"pending":2}', b'{"pending":NaN}', b'[]', b'\xff', b'{'):
            self.file.write_bytes(data)
            self.assertEqual(self.collect()["osk"]["state"], "unavailable")

    def test_symlink_file_and_symlink_parent_rejected(self):
        target = self.root / "elsewhere.json"
        target.write_text(json.dumps(self.value))
        self.file.unlink()
        self.file.symlink_to(target)
        self.assertEqual(self.collect()["osk"]["state"], "unavailable")
        self.file.unlink()
        self.file.parent.rmdir()
        (self.root / "other").mkdir()
        (self.root / "other/sync.json").write_text(json.dumps(self.value))
        self.file.parent.symlink_to(self.root / "other", target_is_directory=True)
        self.assertEqual(self.collect()["osk"]["state"], "unavailable")

    def test_size_limit_rejected_without_loading_payload(self):
        with self.file.open("wb") as file:
            file.truncate(m.MAX_FILE_BYTES + 1)
        self.assertEqual(self.collect()["osk"]["state"], "unavailable")

    def test_private_version_is_not_echoed(self):
        (self.app / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": "bearer-secret-canary"}))
        self.assertEqual(self.collect()["app"]["state"], "unavailable")
        self.assertNotIn("bearer-secret-canary", json.dumps(self.collect()))

    def test_missing_paths_create_no_files(self):
        before = {str(p) for p in self.root.rglob("*")}
        m.StatusCollector(self.root / "absent", self.root / "absent-app", offline=True).collect()
        self.assertEqual(before, {str(p) for p in self.root.rglob("*")})

    def test_live_model_reader_uses_only_fixed_get_no_private_output(self):
        class Response:
            status = 200
            def read(self, limit):
                return json.dumps({"data": [{"id": "private-model-canary", "loaded": True}], "runtime": {"prompt": "private-conversation-canary"}}).encode()
        class Connection:
            def __init__(self, host, port, timeout):
                self.assertion = (host, port, timeout)
                assert host == "127.0.0.1" and port == 11236 and timeout == .5
            def request(self, method, path, headers):
                assert method == "GET" and path == "/v1/models"
            def getresponse(self): return Response()
            def close(self): pass
        with patch.object(m.http.client, "HTTPConnection", Connection):
            self.assertEqual(m._models(11236), {"state": "reachable", "listed_count": 1, "loaded_count": 1})

    def test_cli_result_matches_direct_and_has_no_writes(self):
        args = [sys.executable, "-B", "-s", str(SCRIPTS / "alden_status_mcp.py"), "--once", "--offline", "--state-root", str(self.root / "state"), "--app-dir", str(self.app)]
        before = self.file.read_bytes()
        result = subprocess.run(args, capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        direct = self.collector.collect()
        self.assertEqual(json.loads(result.stdout), direct)
        self.assertEqual(self.file.read_bytes(), before)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        class Collector:
            def collect(self): return {"scope": "read_only_metadata"}
        self.server = m.StdioServer(Collector())

    def request(self, method, params=None, identifier=1):
        return self.server.dispatch({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params or {}})

    def initialize(self, version="2025-11-25"):
        reply = self.request("initialize", {"protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}})
        self.server.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return reply

    def test_lifecycle_and_protocol_negotiation(self):
        self.assertEqual(self.request("tools/list")["error"]["code"], -32002)
        self.assertEqual(self.initialize("2025-06-18")["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(self.request("initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {}})["error"]["code"], -32602)

    def test_only_readonly_no_argument_tool_advertised(self):
        self.initialize()
        tools = self.request("tools/list")["result"]["tools"]
        self.assertEqual([tool["name"] for tool in tools], ["alden_status"])
        self.assertFalse(tools[0]["inputSchema"]["additionalProperties"])
        self.assertTrue(tools[0]["annotations"]["readOnlyHint"])
        self.assertEqual(self.request("tools/list", {"_meta": {"progressToken": "test"}})["result"]["tools"], tools)

    def test_path_query_private_text_and_write_tools_rejected(self):
        self.initialize()
        for params in ({"name": "send", "arguments": {}}, {"name": "alden_status", "arguments": {"path": "/etc/passwd"}}, {"name": "alden_status", "arguments": {"query": "private-conversation-canary"}}):
            reply = self.request("tools/call", params)
            self.assertEqual(reply["error"]["code"], -32602)
            self.assertNotIn("private-conversation-canary", json.dumps(reply))

    def test_valid_call_duplicate_ids_are_not_replayed_or_cached(self):
        self.initialize()
        for i in range(30):
            reply = self.request("tools/call", {"name": "alden_status", "arguments": {}}, i)
            self.assertEqual(reply["id"], i)
            self.assertEqual(json.loads(reply["result"]["content"][0]["text"]), {"scope": "read_only_metadata"})

    def test_malformed_and_oversized_frames_recover(self):
        reader = io.BytesIO(b'not-json\n' + b'x' * (m.MAX_WIRE_BYTES + 1000) + b'\n' + b'{"jsonrpc":"2.0","id":3,"method":"ping"}\n')
        writer = io.BytesIO()
        self.server.serve(reader, writer)
        rows = [json.loads(row) for row in writer.getvalue().splitlines()]
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["error"]["code"], -32700)
        self.assertEqual(rows[1]["error"]["code"], -32700)
        self.assertEqual(rows[2], {"jsonrpc": "2.0", "id": 3, "result": {}})

    def test_notification_and_batch_handling(self):
        self.assertIsNone(self.server.dispatch({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {}}))
        self.assertEqual(self.server.dispatch([])["error"]["code"], -32600)
        self.assertEqual(self.request("ping", identifier=True)["error"]["code"], -32600)


class PipeClient:
    """Exercise real streaming pipes without a TCP listener or live app."""
    def __init__(self, collector, timeout=.1):
        incoming, self.input = os.pipe()
        self.output, outgoing = os.pipe()
        self.reader = os.fdopen(incoming, "rb", buffering=0)
        self.writer = os.fdopen(outgoing, "wb", buffering=0)
        self.errors = []
        self.server = m.StdioServer(collector, tool_timeout=timeout)

        def serve():
            try:
                self.server.serve(self.reader, self.writer)
            except Exception as error:
                self.errors.append(type(error).__name__)
            finally:
                self.reader.close()
                self.writer.close()
        self.thread = threading.Thread(target=serve, daemon=True)
        self.thread.start()
        self.request("initialize", 0, {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "pipe-test", "version": "1"}})
        self.send("notifications/initialized")

    def send(self, method, identifier=None, params=None):
        value = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if identifier is not None:
            value["id"] = identifier
        os.write(self.input, (json.dumps(value) + "\n").encode())

    def read(self, timeout=1):
        if not select.select([self.output], [], [], timeout)[0]:
            raise AssertionError("pipe reply timeout")
        data = b""
        while not data.endswith(b"\n"):
            value = os.read(self.output, 1)
            if not value:
                raise AssertionError("unexpected pipe EOF")
            data += value
        return json.loads(data)

    def request(self, method, identifier, params=None):
        self.send(method, identifier, params)
        reply = self.read()
        if reply.get("id") != identifier:
            raise AssertionError("unexpected response id")
        return reply

    def next_status(self, identifier=10):
        expires = time.monotonic() + 1
        while time.monotonic() < expires:
            reply = self.request("tools/call", identifier, {"name": "alden_status", "arguments": {}})
            if reply.get("error", {}).get("code") != -32004:
                return reply
            identifier += 1
            time.sleep(.001)
        raise AssertionError("worker slot did not recover")

    def close(self):
        if self.input is not None:
            os.close(self.input)
            self.input = None
        self.thread.join(timeout=1)
        os.close(self.output)
        if self.thread.is_alive() or self.errors:
            raise AssertionError("server did not exit cleanly")


class BlockingCollector:
    def __init__(self, cooperative=True):
        self.started, self.release, self.finished = (threading.Event() for _ in range(3))
        self.cooperative = cooperative
        self.calls = 0

    def collect(self, control=None):
        self.calls += 1
        self.started.set()
        if control is not None and self.cooperative:
            control.attach(self.release.set)
        try:
            self.release.wait(timeout=1)
            m._check(control)
            return {"scope": "read_only_metadata"}
        finally:
            if control is not None and self.cooperative:
                control.detach(self.release.set)
            self.finished.set()


class StreamingControlTests(unittest.TestCase):
    def test_cancel_releases_work_and_suppresses_reply_without_logging_reason(self):
        collector = BlockingCollector()
        client = PipeClient(collector, timeout=.5)
        try:
            client.send("tools/call", 1, {"name": "alden_status", "arguments": {}})
            self.assertTrue(collector.started.wait(1))
            client.send("notifications/cancelled", params={"requestId": 1, "reason": "private-cancel-canary"})
            self.assertTrue(collector.finished.wait(1))
            reply = client.request("ping", 2)
            self.assertEqual(reply, {"jsonrpc": "2.0", "id": 2, "result": {}})
            self.assertNotIn("private-cancel-canary", json.dumps(reply))
            self.assertFalse(client.next_status()["result"]["isError"])
        finally:
            collector.release.set()
            client.close()

    def test_wrong_id_boolean_id_and_malformed_cancel_do_not_stop_work(self):
        collector = BlockingCollector()
        client = PipeClient(collector, timeout=.5)
        try:
            client.send("tools/call", 1, {"name": "alden_status", "arguments": {}})
            self.assertTrue(collector.started.wait(1))
            for params in ({"requestId": 99}, {"requestId": True}, {}):
                client.send("notifications/cancelled", params=params)
            self.assertEqual(client.request("ping", 2)["result"], {})
            self.assertFalse(collector.finished.is_set())
            collector.release.set()
            self.assertEqual(client.read()["id"], 1)
            client.send("notifications/cancelled", params={"requestId": 1})
            self.assertEqual(client.request("ping", 3)["id"], 3)
        finally:
            collector.release.set()
            client.close()

    def test_deadline_returns_once_and_suppresses_late_worker_result(self):
        collector = BlockingCollector(cooperative=False)
        client = PipeClient(collector, timeout=.05)
        try:
            started = time.monotonic()
            client.send("tools/call", 1, {"name": "alden_status", "arguments": {}})
            reply = client.read()
            self.assertLess(time.monotonic() - started, .5)
            self.assertEqual(reply["id"], 1)
            self.assertTrue(reply["result"]["isError"])
            self.assertEqual(reply["result"]["content"][0]["text"], "metadata_deadline_exceeded")
            self.assertEqual(client.request("tools/call", 2, {"name": "alden_status", "arguments": {}})["error"]["code"], -32004)
            collector.release.set()
            self.assertTrue(collector.finished.wait(1))
            self.assertFalse(client.next_status()["result"]["isError"])
            self.assertFalse(select.select([client.output], [], [], .02)[0])
        finally:
            collector.release.set()
            client.close()

    def test_busy_backpressure_bounds_worker_count_and_ping_remains_available(self):
        collector = BlockingCollector()
        client = PipeClient(collector, timeout=.5)
        try:
            client.send("tools/call", 1, {"name": "alden_status", "arguments": {}})
            self.assertTrue(collector.started.wait(1))
            for identifier in range(2, 22):
                client.send("tools/call", identifier, {"name": "alden_status", "arguments": {}})
            for identifier in range(2, 22):
                reply = client.read()
                self.assertEqual(reply["id"], identifier)
                self.assertEqual(reply["error"]["code"], -32004)
            self.assertEqual(collector.calls, 1)
            self.assertEqual(client.request("ping", 30)["result"], {})
            client.send("notifications/cancelled", params={"requestId": 1})
            self.assertTrue(collector.finished.wait(1))
        finally:
            collector.release.set()
            client.close()

    def test_inflight_eof_cancels_owned_work_and_exits(self):
        collector = BlockingCollector()
        client = PipeClient(collector, timeout=.5)
        client.send("tools/call", 1, {"name": "alden_status", "arguments": {}})
        self.assertTrue(collector.started.wait(1))
        client.close()
        self.assertTrue(collector.finished.wait(1))

    def test_collector_exception_exports_only_fixed_error_and_recovers(self):
        class Collector:
            def collect(self, control=None):
                raise RuntimeError("private-exception-canary")
        client = PipeClient(Collector())
        try:
            reply = client.next_status()
            self.assertTrue(reply["result"]["isError"])
            self.assertNotIn("private-exception-canary", json.dumps(reply))
            self.assertEqual(client.request("ping", 2)["result"], {})
        finally:
            client.close()

    def test_header_and_connection_close_body_trickles_obey_total_deadline(self):
        for phase in ("headers", "body"):
            with self.subTest(phase=phase):
                original = m.http.client.HTTPConnection
                peers, senders = [], []
                quit_sender = threading.Event()

                def connection(host, port, timeout):
                    self.assertEqual((host, port), ("127.0.0.1", 11234))
                    left, right = socket.socketpair()
                    left.settimeout(timeout)
                    peers.append(right)
                    result = original(host, port, timeout=timeout)
                    result.sock = left
                    headers = b"HTTP/1.1 200 OK\r\nContent-Length: 101\r\nConnection: close\r\n\r\n"

                    def trickle():
                        try:
                            if phase == "body":
                                right.sendall(headers)
                                data = b"x" * 101
                            else:
                                data = headers + b"x" * 101
                            for byte in data:
                                if quit_sender.wait(.005):
                                    return
                                right.sendall(bytes([byte]))
                        except OSError:
                            pass
                    thread = threading.Thread(target=trickle, daemon=True)
                    senders.append(thread)
                    thread.start()
                    return result

                class Collector:
                    def collect(self, control=None):
                        return {"generation_endpoint": m._models(11234, control)}
                with patch.object(m.http.client, "HTTPConnection", connection):
                    client = PipeClient(Collector(), timeout=.08)
                    try:
                        started = time.monotonic()
                        reply = client.next_status()
                        self.assertLess(time.monotonic() - started, .5)
                        self.assertTrue(reply["result"]["isError"])
                        self.assertEqual(reply["result"]["content"][0]["text"], "metadata_deadline_exceeded")
                    finally:
                        client.close()
                        quit_sender.set()
                        for peer in peers:
                            peer.close()
                        for thread in senders:
                            thread.join(timeout=1)

    def test_one_shot_deadline_returns_without_waiting_for_stalled_worker(self):
        collector = BlockingCollector(cooperative=False)
        try:
            started = time.monotonic()
            with self.assertRaises(m.CallStopped):
                m.bounded_collect(collector, timeout=.04)
            self.assertLess(time.monotonic() - started, .5)
        finally:
            collector.release.set()
            self.assertTrue(collector.finished.wait(1))


if __name__ == "__main__":
    unittest.main()
