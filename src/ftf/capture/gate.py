"""A forward proxy on loopback that lets the capture browser reach public addresses only.

Checking a URL before the browser opens it is not enough: redirects, scripts, WebSockets and
a name that resolves differently the second time (DNS rebinding) all get past such a check.
Here every connection the browser makes goes through the gate, which resolves the name
itself, refuses it if any address is not public, and connects to the address it checked.
The browser never resolves a name of its own.

Plain HTTP requests are forwarded one per connection (`Connection: close`), so a kept-alive
proxy connection cannot carry a second request to another host past the check.
"""

from __future__ import annotations

import select
import socket
import socketserver
import threading
from typing import Self
from urllib.parse import urlsplit

from .network import is_public, resolve

_HOP_HEADERS = {"connection", "keep-alive", "proxy-connection", "proxy-authorization"}
_MAX_HEADER_LINES = 200


class EgressGate:
    """Run with `with EgressGate() as gate:` and give the browser `gate.url` as its proxy."""

    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout
        self.blocked: dict[str, str] = {}  # host -> the non-public address it stood for
        self.unresolved: set[str] = set()
        self.pins: dict[tuple[str, int], str] = {}  # (host, port) -> address connected to
        self._lock = threading.Lock()
        self._server = _Server(("127.0.0.1", 0), _Handler)
        self._server.gate = self
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._server.shutdown()
        self._server.server_close()

    def connect(self, host: str, port: int) -> socket.socket | None:
        """A connection to `host` if every address it has is public, else None (recorded)."""
        host = host.strip("[]").rstrip(".").lower()
        addresses = resolve(host)
        if not addresses:
            with self._lock:
                self.unresolved.add(host)
            return None
        private = next((a for a in addresses if not is_public(a)), None)
        if private is not None:
            with self._lock:
                self.blocked[host] = private.split("%", 1)[0]
            return None
        for address in addresses:
            try:
                upstream = socket.create_connection((address, port), timeout=self.timeout)
            except OSError:
                continue
            with self._lock:
                self.pins[(host, port)] = address
            return upstream
        return None


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True
    gate: EgressGate


class _Handler(socketserver.StreamRequestHandler):
    rbufsize = 0  # unbuffered: nothing the client sends after the headers is read ahead
    server: _Server

    def handle(self) -> None:
        self.connection.settimeout(self.server.gate.timeout)
        try:
            request = self.rfile.readline(65537).decode("latin-1").split()
            headers = []
            for _ in range(_MAX_HEADER_LINES):
                line = self.rfile.readline(65537)
                if line in (b"\r\n", b"\n", b""):
                    break
                headers.append(line)
            if len(request) != 3:
                return
            method, target, version = request
            if method.upper() == "CONNECT":
                self.tunnel(target)
            else:
                self.forward(method, target, version, headers)
        except OSError:
            return

    def tunnel(self, target: str) -> None:
        host, _, port = target.rpartition(":")
        if not host or not port.isdigit():
            return
        upstream = self.server.gate.connect(host, int(port))
        if upstream is None:
            self.wfile.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            return
        with upstream:
            self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            _pipe(self.connection, upstream, self.server.gate.timeout)

    def forward(self, method: str, target: str, version: str, headers: list[bytes]) -> None:
        parts = urlsplit(target)
        if parts.scheme != "http" or not parts.hostname:
            return
        # A refusal closes the connection: a proxy's own error page would be kept as the page.
        upstream = self.server.gate.connect(parts.hostname, parts.port or 80)
        if upstream is None:
            return
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        kept = [
            h for h in headers if h.split(b":", 1)[0].strip().lower().decode() not in _HOP_HEADERS
        ]
        head = f"{method} {path} {version}\r\n".encode("latin-1")
        with upstream:
            upstream.sendall(head + b"".join(kept) + b"Connection: close\r\n\r\n")
            _pipe(self.connection, upstream, self.server.gate.timeout)


def _pipe(a: socket.socket, b: socket.socket, idle: float) -> None:
    """Copy both ways until either side closes or nothing moves for `idle` seconds."""
    peers = {a: b, b: a}
    while True:
        readable, _, broken = select.select(list(peers), [], list(peers), idle)
        if broken or not readable:
            return
        for sock in readable:
            try:
                data = sock.recv(65536)
            except OSError:
                return
            if not data:
                return
            peers[sock].sendall(data)
