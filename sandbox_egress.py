"""Host-side egress proxy for network-enabled sandbox runs (ROADMAP B4).

A sandbox run with ``allow_network=True`` still gets its own empty network
namespace: it cannot reach anything directly. Its only way out is a Unix socket
bound into the sandbox, served here, and bridged to ``HTTP(S)_PROXY`` inside by
``sandbox_egress_shim.py``. Code that ignores the proxy simply has no route.

Policy, all fail-closed:
- ``CONNECT host:443`` (HTTPS) and absolute-form ``http://host/...`` (port 80)
  only; host must be a DNS name on the allowlist (IP literals are refused).
- Every address the name resolves to must be globally routable; the proxy then
  connects to the exact address it checked, so DNS rebinding cannot swap in an
  internal address between check and connect.
- HTTPS: the TLS ClientHello's SNI must name the approved host (no SNI, a
  different SNI, or non-TLS bytes close the tunnel before anything is sent).
- Plain HTTP: the request is re-issued in origin form with ``Host`` set to the
  approved host and ``Connection: close``; proxy headers are dropped.
- Bounded request heads, concurrent connections, and idle time.

Residual risk: inside TLS the HTTP ``Host`` header is invisible, so a CDN that
serves an allowed name and also routes by inner Host (domain fronting) could be
abused; major CDNs reject SNI/Host mismatches.
"""

from __future__ import annotations

import os
import shutil
import socket
import tempfile
import threading
from typing import Callable
from urllib.parse import urlsplit

import net_policy
from zedek_logger import get_logger

log = get_logger("sandbox_egress")

HTTPS_PORT, HTTP_PORT = 443, 80
MAX_HEAD_BYTES = 16 * 1024
MAX_TLS_RECORD = 16 * 1024 + 256
IDLE_TIMEOUT_S = 30.0
MAX_CONNECTIONS = 16

Resolver = Callable[[str, int], list[str]]
Connector = Callable[[str, int, float], socket.socket]


class EgressDenied(Exception):
    """A destination the policy refuses; the message is shown to sandboxed code."""


def _resolve(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(info[4][0] for info in infos))


def _connect(address: str, port: int, timeout: float) -> socket.socket:
    return socket.create_connection((address, port), timeout=timeout)


def tls_sni(record: bytes) -> str | None:
    """The server_name from a TLS ClientHello record, or None if `record` is not
    one complete ClientHello record carrying exactly one host_name."""
    try:
        if len(record) < 5 or record[0] != 0x16:
            return None
        length = int.from_bytes(record[3:5], "big")
        body = record[5:5 + length]
        if len(body) != length or not body or body[0] != 0x01:
            return None
        hello_len = int.from_bytes(body[1:4], "big")
        hello = body[4:4 + hello_len]
        if len(hello) != hello_len:
            return None
        pos = 2 + 32                                   # version, random
        pos += 1 + hello[pos]                          # session id
        pos += 2 + int.from_bytes(hello[pos:pos + 2], "big")   # cipher suites
        pos += 1 + hello[pos]                          # compression methods
        end = pos + 2 + int.from_bytes(hello[pos:pos + 2], "big")
        pos += 2
        names = []
        while pos + 4 <= end:
            ext_type = int.from_bytes(hello[pos:pos + 2], "big")
            ext_len = int.from_bytes(hello[pos + 2:pos + 4], "big")
            data = hello[pos + 4:pos + 4 + ext_len]
            pos += 4 + ext_len
            if ext_type != 0:
                continue
            p = 2
            while p + 3 <= len(data):
                name_type, name_len = data[p], int.from_bytes(data[p + 1:p + 3], "big")
                if name_type == 0:
                    names.append(data[p + 3:p + 3 + name_len].decode("ascii"))
                p += 3 + name_len
        return names[0] if len(names) == 1 else None
    except (IndexError, UnicodeDecodeError):
        return None


class EgressProxy:
    """Context manager: serves the policy on a Unix socket for one sandbox run."""

    def __init__(self, allowlist, *, resolver: Resolver = _resolve, connector: Connector = _connect,
                 idle_timeout: float = IDLE_TIMEOUT_S, max_connections: int = MAX_CONNECTIONS) -> None:
        self.allowlist = net_policy.parse_allowlist(allowlist)
        self._resolve = resolver
        self._connect = connector
        self._idle = idle_timeout
        self._slots = threading.BoundedSemaphore(max_connections)
        self._dir: str | None = None
        self._server: socket.socket | None = None
        self._open: set[socket.socket] = set()
        self._lock = threading.Lock()
        self._closing = False

    @property
    def socket_path(self) -> str:
        if self._dir is None:
            raise RuntimeError("egress proxy not started")
        return os.path.join(self._dir, "egress.sock")

    def __enter__(self) -> "EgressProxy":
        # AF_UNIX paths are limited to ~108 bytes: keep the directory short.
        self._dir = tempfile.mkdtemp(prefix="zedek-egress-", dir="/tmp")
        os.chmod(self._dir, 0o700)
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(self.socket_path)
        self._server.listen(MAX_CONNECTIONS)
        threading.Thread(target=self._accept_loop, name="egress-accept", daemon=True).start()
        return self

    def __exit__(self, *exc) -> None:
        self._closing = True
        if self._server is not None:
            try:
                self._server.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._server.close()
        with self._lock:
            for sock in list(self._open):
                _close(sock)
            self._open.clear()
        if self._dir:
            shutil.rmtree(self._dir, ignore_errors=True)

    # ── Connections ──────────────────────────────────────────────────────

    def _accept_loop(self) -> None:
        while not self._closing:
            try:
                client, _ = self._server.accept()
            except OSError:
                return
            if not self._slots.acquire(blocking=False):
                threading.Thread(target=_refuse_busy, args=(client,), daemon=True).start()
                continue
            self._track(client)
            threading.Thread(target=self._serve, args=(client,), name="egress-conn", daemon=True).start()

    def _track(self, sock: socket.socket) -> None:
        with self._lock:
            self._open.add(sock)

    def _serve(self, client: socket.socket) -> None:
        upstream = None
        try:
            client.settimeout(self._idle)
            head, rest = _read_head(client)
            request_line, _, header_block = head.partition(b"\r\n")
            method, target, _version = _split_request_line(request_line)
            if method == "CONNECT":
                host, port = _split_authority(target)
                upstream = self._open_upstream(host, port, HTTPS_PORT)
                _send(client, b"HTTP/1.1 200 Connection established\r\n\r\n")
                hello = _read_tls_record(client, rest)
                sni = tls_sni(hello)
                if sni is None or net_policy.normalize_host(sni) != host:
                    log.info("sandbox_egress_denied", extra={"host": host, "reason": "sni_mismatch"})
                    return
                upstream.sendall(hello)
            else:
                url = urlsplit(target)
                if url.scheme != "http":
                    raise EgressDenied("only CONNECT (https) or absolute http:// requests are allowed")
                host, port = url.hostname or "", url.port or HTTP_PORT
                upstream = self._open_upstream(host, port, HTTP_PORT)
                path = (url.path or "/") + (f"?{url.query}" if url.query else "")
                upstream.sendall(_origin_request(method, path, host, header_block) + rest)
            _relay(client, upstream, self._idle)
        except EgressDenied as denied:
            _reply(client, 403, str(denied))
        except (OSError, ValueError) as error:
            log.info("sandbox_egress_error", extra={"error": type(error).__name__})
            _reply(client, 400, "bad request")
        finally:
            _close(upstream)
            _close(client)
            with self._lock:
                self._open.discard(client)
                self._open.discard(upstream)
            self._slots.release()

    def _open_upstream(self, raw_host: str, port: int, required_port: int) -> socket.socket:
        host = net_policy.normalize_host(raw_host)
        shown = (raw_host or "")[:100]
        if host is None:
            raise self._deny(shown, port, "not a DNS host name (IP literals are refused)")
        if port != required_port:
            raise self._deny(host, port, f"port {port} is not allowed here")
        if not net_policy.host_allowed(host, self.allowlist):
            raise self._deny(host, port, "host is not on the sandbox egress allowlist")
        try:
            addresses = self._resolve(host, port)
        except OSError:
            raise self._deny(host, port, "host did not resolve")
        if not addresses or any(net_policy.address_blocked(a) for a in addresses):
            raise self._deny(host, port, "host resolves to a private or reserved address")
        last_error: OSError | None = None
        for address in addresses:  # connect to the checked address itself
            try:
                upstream = self._connect(address, port, self._idle)
                self._track(upstream)
                log.info("sandbox_egress_allowed", extra={"host": host, "port": port})
                return upstream
            except OSError as error:
                last_error = error
        raise self._deny(host, port, f"could not connect ({type(last_error).__name__})")

    @staticmethod
    def _deny(host: str, port: int, reason: str) -> EgressDenied:
        log.info("sandbox_egress_denied", extra={"host": host, "port": port, "reason": reason})
        return EgressDenied(f"{host}:{port}: {reason}")


# ── Wire helpers ─────────────────────────────────────────────────────────────

def _read_head(sock: socket.socket) -> tuple[bytes, bytes]:
    data = b""
    while b"\r\n\r\n" not in data:
        if len(data) > MAX_HEAD_BYTES:
            raise ValueError("request head too large")
        chunk = sock.recv(4096)
        if not chunk:
            raise ValueError("connection closed before the request head")
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    if len(head) > MAX_HEAD_BYTES:
        raise ValueError("request head too large")
    return head, rest


def _split_request_line(line: bytes) -> tuple[str, str, str]:
    parts = line.decode("ascii").split(" ")
    if len(parts) != 3 or not parts[0].isalpha() or not parts[2].startswith("HTTP/1."):
        raise ValueError("malformed request line")
    return parts[0].upper(), parts[1], parts[2]


def _split_authority(target: str) -> tuple[str, int]:
    host, sep, port = target.rpartition(":")
    if not sep or not port.isdigit():
        raise EgressDenied("CONNECT target must be host:port")
    return host, int(port)


_DROP_HEADERS = {"host", "connection", "keep-alive", "proxy-connection", "proxy-authorization",
                 "proxy-authenticate", "te", "trailer", "upgrade"}


def _origin_request(method: str, path: str, host: str, header_block: bytes) -> bytes:
    lines = [f"{method} {path} HTTP/1.1".encode("ascii"), f"Host: {host}".encode("ascii")]
    for line in header_block.split(b"\r\n") if header_block else []:
        # A bare CR or LF inside a line could smuggle a second Host header to
        # servers that accept bare line breaks; a line without ':' is malformed.
        if b"\r" in line or b"\n" in line or b":" not in line:
            raise ValueError("malformed header line")
        name = line.split(b":", 1)[0].strip().lower().decode("latin-1")
        if name not in _DROP_HEADERS:
            lines.append(line)
    lines.append(b"Connection: close")
    return b"\r\n".join(lines) + b"\r\n\r\n"


def _read_tls_record(sock: socket.socket, already: bytes) -> bytes:
    data = already
    while len(data) < 5:
        chunk = sock.recv(4096)
        if not chunk:
            return data
        data += chunk
    total = 5 + int.from_bytes(data[3:5], "big")
    if total > MAX_TLS_RECORD:
        return b""
    while len(data) < total:
        chunk = sock.recv(total - len(data))
        if not chunk:
            return data
        data += chunk
    return data  # may include bytes after the record; they are forwarded as-is


def _relay(a: socket.socket, b: socket.socket, idle: float) -> None:
    def pump(src: socket.socket, dst: socket.socket) -> None:
        try:
            src.settimeout(idle)
            while True:
                chunk = src.recv(65536)
                if not chunk:
                    break
                dst.sendall(chunk)
        except OSError:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    back = threading.Thread(target=pump, args=(b, a), daemon=True)
    back.start()
    pump(a, b)
    back.join(idle)


def _refuse_busy(client: socket.socket) -> None:
    """Read the request head (briefly) so the client sees the 503, not a reset."""
    try:
        client.settimeout(1.0)
        _read_head(client)
    except (OSError, ValueError):
        pass
    _reply(client, 503, "too many connections")
    _close(client)


def _send(sock: socket.socket, data: bytes) -> None:
    sock.sendall(data)


def _reply(sock: socket.socket, status: int, message: str) -> None:
    reason = {400: "Bad Request", 403: "Forbidden", 503: "Service Unavailable"}[status]
    body = f"Zedek sandbox egress: {message}\n".encode("utf-8", "replace")
    try:
        sock.sendall(f"HTTP/1.1 {status} {reason}\r\nContent-Type: text/plain\r\n"
                     f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode("ascii") + body)
    except OSError:
        pass


def _close(sock: socket.socket | None) -> None:
    if sock is None:
        return
    try:
        sock.close()
    except OSError:
        pass
