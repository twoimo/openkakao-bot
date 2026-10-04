"""Cancellable HTTP for the two fixed local Alden model services."""
from __future__ import annotations
import http.client
import socket
import threading
import time
import urllib.request
import urllib.parse
from alden_abort import AbortToken

class CancellableLocalResponse:
    """Own one loopback socket and shut it down on per-turn/global cancellation.

    The socket watcher also covers waiting for response headers. Closing only
    a buffered urllib response after generation would leave that wait alive.
    """

    def __init__(self, request: urllib.request.Request, timeout: float, token: AbortToken, *, embedding: bool = False):
        self.request, self.timeout, self.token = request, timeout, token
        self.embedding = embedding
        self.connection: http.client.HTTPConnection | None = None
        self.response: http.client.HTTPResponse | None = None
        self._done = threading.Event()
        self._watcher: threading.Thread | None = None

    def __enter__(self):
        self.token.raise_if_cancelled()
        parsed = urllib.parse.urlsplit(self.request.full_url)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port != (11236 if self.embedding else 11234) or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ({"/v1/models", "/v1/embeddings"} if self.embedding else {"/v1/models", "/v1/chat/completions"}):
            raise ValueError("local_llm_endpoint_invalid")
        started = time.monotonic()
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=min(3.0, self.timeout))
        self.connection = connection
        try:
            connection.connect()
            transport = connection.sock
            if transport is None:
                raise OSError("local_llm_socket_missing")
            transport.settimeout(max(.001, self.timeout - (time.monotonic() - started)))

            def watch() -> None:
                while not self._done.wait(.02):
                    if self.token.is_cancelled() or time.monotonic() - started >= self.timeout:
                        try:
                            transport.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                        return

            self._watcher = threading.Thread(target=watch, daemon=True, name="alden-llm-cancel")
            self._watcher.start()
            self.token.raise_if_cancelled()
            connection.request(self.request.get_method(), parsed.path, body=self.request.data, headers=dict(self.request.header_items()))
            self.response = connection.getresponse()
            self.token.raise_if_cancelled()
            if self.response.status != 200:
                raise urllib.error.HTTPError(self.request.full_url, self.response.status, "local inference rejected", self.response.headers, None)
            return self
        except Exception:
            self.__exit__(None, None, None)
            self.token.raise_if_cancelled()
            raise

    def read(self, limit: int = -1) -> bytes:
        try:
            if self.response is None:
                raise OSError("local_llm_response_missing")
            raw = self.response.read(limit)
            self.token.raise_if_cancelled()
            return raw
        except Exception:
            self.token.raise_if_cancelled()
            raise

    @property
    def headers(self):
        return self.response.headers if self.response is not None else {}

    def readline(self, limit: int = -1) -> bytes:
        try:
            if self.response is None:
                raise OSError("local_llm_response_missing")
            raw = self.response.readline(limit)
            self.token.raise_if_cancelled()
            return raw
        except Exception:
            self.token.raise_if_cancelled()
            raise

    def __exit__(self, *_args: object) -> bool:
        self._done.set()
        if self._watcher is not None:
            self._watcher.join(timeout=.25)
        if self.response is not None:
            self.response.close()
        if self.connection is not None:
            self.connection.close()
        return False

