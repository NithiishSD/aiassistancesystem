"""Egress allowlist for network-enabled sandbox runs
(OpenSpec change: sandbox-egress-allowlist, ROADMAP B4).

Real sockets throughout; DNS and upstream servers are local fakes, so nothing
leaves the machine.
"""

import socket
import ssl
import textwrap
import threading

import pytest

import net_policy
import sandbox_egress as se
import sandbox_runner as sr
from sandbox_runner import SandboxRunner

PUBLIC = "93.184.216.34"


# ── Fakes ────────────────────────────────────────────────────────────────────

class Upstream:
    """A local TCP server standing in for the internet; records what it received."""

    def __init__(self, reply: bytes = b"", echo: bool = False):
        self.received = b""
        self.connections = 0
        self._reply, self._echo = reply, echo
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(8)
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            self.connections += 1
            conn.settimeout(2)
            try:
                data = conn.recv(65536)
                self.received += data
                conn.sendall(data if self._echo else self._reply)
            except OSError:
                pass
            finally:
                conn.close()

    @property
    def port(self):
        return self.server.getsockname()[1]


def proxy_for(allowlist, upstream, dns=None, **kw):
    """EgressProxy whose DNS is `dns` (default: everything public) and whose
    connections all land on `upstream`; records the (address, port) it dialled."""
    dialled = []

    def resolver(host, port):
        return (dns or {}).get(host, [PUBLIC])

    def connector(address, port, timeout):
        dialled.append((address, port))
        return socket.create_connection(("127.0.0.1", upstream.port), timeout=timeout)

    proxy = se.EgressProxy(allowlist, resolver=resolver, connector=connector, idle_timeout=2, **kw)
    proxy.dialled = dialled
    return proxy


def client_hello(server_name):
    ctx = ssl.create_default_context()
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    tls = ctx.wrap_bio(incoming, outgoing, server_hostname=server_name)
    with pytest.raises(ssl.SSLWantReadError):
        tls.do_handshake()
    return outgoing.read()


def talk(proxy, data, read_until_close=True):
    sock = socket.socket(socket.AF_UNIX)
    sock.settimeout(3)
    sock.connect(proxy.socket_path)
    sock.sendall(data)
    chunks = []
    try:
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    except socket.timeout:
        pass
    sock.close()
    return b"".join(chunks)


def connect_request(host, port=443):
    return f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode()


# ── Policy helpers ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("host,allowed", [
    ("pypi.org", True), ("PyPI.org.", True), ("files.pythonhosted.org", True),
    ("pythonhosted.org", False), ("evilpythonhosted.org", False), ("pypi.org.evil.io", False),
    ("evil.io", False),
])
def test_allowlist_matching(host, allowed):
    allow = net_policy.parse_allowlist(["pypi.org", "*.pythonhosted.org"])
    assert net_policy.host_allowed(net_policy.normalize_host(host), allow) is allowed


@pytest.mark.parametrize("bad", ["1.2.3.4", "*", "*.org.", "http://pypi.org", "pypi.org/x", "py pi.org", "localhost"])
def test_invalid_allowlist_entries_raise(bad):
    with pytest.raises(ValueError):
        net_policy.parse_allowlist([bad])


@pytest.mark.parametrize("host", ["1.2.3.4", "[::1]", "::1", "münchen.de", "a..b", "-a.com", "a_b.com", ""])
def test_non_hostnames_are_refused(host):
    assert net_policy.normalize_host(host) is None


def test_sni_parser_reads_real_client_hellos():
    assert se.tls_sni(client_hello("pypi.org")) == "pypi.org"
    assert se.tls_sni(b"GET / HTTP/1.1\r\n\r\n") is None
    assert se.tls_sni(client_hello("pypi.org")[:40]) is None  # truncated


# ── HTTPS (CONNECT) ──────────────────────────────────────────────────────────

def test_connect_to_allowed_host_relays_after_matching_sni():
    up = Upstream(echo=True)
    hello = client_hello("pypi.org")
    with proxy_for(["pypi.org"], up) as proxy:
        out = talk(proxy, connect_request("pypi.org") + hello)
    assert out.startswith(b"HTTP/1.1 200 Connection established\r\n\r\n")
    assert up.received == hello and out.endswith(hello)
    assert proxy.dialled == [(PUBLIC, 443)]  # the checked address, not the name


def test_sni_mismatch_closes_before_anything_is_sent():
    up = Upstream(echo=True)
    with proxy_for(["pypi.org"], up) as proxy:
        out = talk(proxy, connect_request("pypi.org") + client_hello("evil.example"))
    assert out == b"HTTP/1.1 200 Connection established\r\n\r\n"
    assert up.received == b""


def test_non_tls_through_connect_is_dropped():
    up = Upstream(echo=True)
    with proxy_for(["pypi.org"], up) as proxy:
        talk(proxy, connect_request("pypi.org") + b"GET / HTTP/1.1\r\nHost: pypi.org\r\n\r\n")
    assert up.received == b""


@pytest.mark.parametrize("target,reason", [
    ("evil.example:443", b"not on the sandbox egress allowlist"),
    ("93.184.216.34:443", b"not a DNS host name"),
    ("pypi.org:22", b"port 22 is not allowed"),
    ("pypi.org:80", b"port 80 is not allowed"),
])
def test_connect_denials(target, reason):
    up = Upstream(echo=True)
    with proxy_for(["pypi.org"], up) as proxy:
        out = talk(proxy, f"CONNECT {target} HTTP/1.1\r\n\r\n".encode())
    assert out.startswith(b"HTTP/1.1 403 Forbidden") and reason in out
    assert proxy.dialled == [] and up.connections == 0


@pytest.mark.parametrize("addresses", [["10.0.0.5"], [PUBLIC, "127.0.0.1"], ["::ffff:169.254.169.254"], ["0.0.0.0"]])
def test_allowed_name_resolving_to_internal_address_is_denied(addresses):
    up = Upstream(echo=True)
    with proxy_for(["pypi.org"], up, dns={"pypi.org": addresses}) as proxy:
        out = talk(proxy, connect_request("pypi.org"))
    assert b"403" in out and b"private or reserved" in out and proxy.dialled == []


# ── Plain HTTP ───────────────────────────────────────────────────────────────

def test_plain_http_is_reissued_in_origin_form_with_host_pinned():
    up = Upstream(reply=b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nhi")
    request = (b"GET http://pypi.org/simple/?q=1 HTTP/1.1\r\nHost: evil.example\r\n"
               b"Proxy-Authorization: x\r\nConnection: keep-alive\r\nAccept: */*\r\n\r\n")
    with proxy_for(["pypi.org"], up) as proxy:
        out = talk(proxy, request)
    assert out.endswith(b"hi")
    head = up.received.split(b"\r\n\r\n")[0].split(b"\r\n")
    assert head[0] == b"GET /simple/?q=1 HTTP/1.1"
    assert b"Host: pypi.org" in head and b"Host: evil.example" not in head
    assert b"Connection: close" in head and b"Accept: */*" in head
    assert not any(line.lower().startswith(b"proxy-") for line in head)
    assert proxy.dialled == [(PUBLIC, 80)]


@pytest.mark.parametrize("request_bytes", [
    b"GET https://pypi.org/ HTTP/1.1\r\n\r\n",                      # https must use CONNECT
    b"GET /relative HTTP/1.1\r\nHost: pypi.org\r\n\r\n",            # not a proxy request
    b"GET http://pypi.org:8080/ HTTP/1.1\r\n\r\n",                  # port
])
def test_plain_http_denials(request_bytes):
    up = Upstream(reply=b"x")
    with proxy_for(["pypi.org"], up) as proxy:
        out = talk(proxy, request_bytes)
    assert out.startswith(b"HTTP/1.1 403") and up.connections == 0


def test_header_smuggling_attempt_is_rejected():
    up = Upstream(reply=b"x")
    with proxy_for(["pypi.org"], up) as proxy:
        out = talk(proxy, b"GET http://pypi.org/ HTTP/1.1\r\nX: a\nHost: evil.example\r\n\r\n")
    assert out.startswith(b"HTTP/1.1 400") and up.received == b""


def test_oversized_head_is_rejected():
    up = Upstream(reply=b"x")
    with proxy_for(["pypi.org"], up) as proxy:
        out = talk(proxy, b"GET http://pypi.org/ HTTP/1.1\r\nX: " + b"a" * (se.MAX_HEAD_BYTES + 10) + b"\r\n\r\n")
    assert out.startswith(b"HTTP/1.1 400") and up.connections == 0


def test_connection_cap():
    up = Upstream(echo=True)
    with proxy_for(["pypi.org"], up, max_connections=1) as proxy:
        hold = socket.socket(socket.AF_UNIX)
        hold.connect(proxy.socket_path)  # occupies the only slot (no request yet)
        try:
            import time
            time.sleep(0.2)
            assert talk(proxy, connect_request("pypi.org")).startswith(b"HTTP/1.1 503")
        finally:
            hold.close()


def test_socket_is_removed_after_the_run():
    up = Upstream()
    with proxy_for([], up) as proxy:
        path = proxy.socket_path
    import os
    assert not os.path.exists(path)


# ── Runner integration ───────────────────────────────────────────────────────

def test_env_allowlist_parsing(monkeypatch):
    monkeypatch.setenv("ZEDEK_SANDBOX_EGRESS_ALLOWLIST", "pypi.org, *.pythonhosted.org")
    assert SandboxRunner().egress_allowlist == ("pypi.org", "*.pythonhosted.org")
    monkeypatch.setenv("ZEDEK_SANDBOX_EGRESS_ALLOWLIST", "pypi.org,10.0.0.1")
    with pytest.raises(ValueError):
        SandboxRunner()


def test_network_run_refused_without_bubblewrap_even_when_opted_in(monkeypatch):
    monkeypatch.setattr(sr, "_is_bwrap_functional", lambda: False)
    runner = SandboxRunner(allow_unisolated=True)
    monkeypatch.setattr(runner, "_execute_rlimit", lambda *a: pytest.fail("ran with open network"))
    res = runner.run_code("print(1)", allow_network=True)
    assert res.status == "unavailable" and "needs bubblewrap" in res.stderr


needs_bwrap = pytest.mark.skipif(not sr._is_bwrap_functional(), reason="bubblewrap unavailable")


@needs_bwrap
def test_no_network_run_has_no_route():
    code = textwrap.dedent("""
        import socket, os
        try:
            socket.create_connection(("1.1.1.1", 443), timeout=2); print("REACHED")
        except OSError as e:
            print("unreachable", e.errno)
        print("proxy" if os.environ.get("HTTPS_PROXY") else "no-proxy")
    """)
    res = SandboxRunner(timeout_seconds=10).run_code(code)
    assert res.status == "passed", res.stderr
    assert "unreachable" in res.stdout and "no-proxy" in res.stdout and "REACHED" not in res.stdout


@needs_bwrap
def test_network_run_reaches_only_allowlisted_hosts_through_the_proxy(monkeypatch):
    up = Upstream(reply=b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nConnection: close\r\n\r\nhello")
    monkeypatch.setattr(SandboxRunner, "_egress_proxy_factory",
                        staticmethod(lambda allowlist: proxy_for(allowlist, up)))
    code = textwrap.dedent("""
        import os, socket, urllib.request, urllib.error
        try:
            socket.create_connection(("1.1.1.1", 443), timeout=2); print("DIRECT REACHED")
        except OSError:
            print("direct blocked")
        print("allowed:", urllib.request.urlopen("http://allowed.example/path", timeout=5).read().decode())
        try:
            urllib.request.urlopen("http://denied.example/", timeout=5)
        except urllib.error.HTTPError as e:
            print("denied:", e.code)
    """)
    res = SandboxRunner(timeout_seconds=20, egress_allowlist=["allowed.example"]).run_code(code, allow_network=True)
    assert res.status == "passed", res.stderr
    assert "direct blocked" in res.stdout and "DIRECT REACHED" not in res.stdout
    assert "allowed: hello" in res.stdout and "denied: 403" in res.stdout
    assert up.received.startswith(b"GET /path HTTP/1.1") and b"Host: allowed.example" in up.received


@needs_bwrap
def test_https_through_the_proxy_requires_matching_sni(monkeypatch):
    up = Upstream(echo=True)
    monkeypatch.setattr(SandboxRunner, "_egress_proxy_factory",
                        staticmethod(lambda allowlist: proxy_for(allowlist, up)))
    code = textwrap.dedent("""
        import os, socket, ssl
        from urllib.parse import urlsplit
        proxy = urlsplit(os.environ["HTTPS_PROXY"])
        def tunnel(sni):
            s = socket.create_connection((proxy.hostname, proxy.port), timeout=5)
            s.sendall(b"CONNECT allowed.example:443 HTTP/1.1\\r\\n\\r\\n")
            assert s.recv(100).startswith(b"HTTP/1.1 200")
            inc, out = ssl.MemoryBIO(), ssl.MemoryBIO()
            tls = ssl.create_default_context().wrap_bio(inc, out, server_hostname=sni)
            try:
                tls.do_handshake()
            except ssl.SSLWantReadError:
                pass
            hello = out.read(); s.sendall(hello)
            got = b""
            try:
                while True:
                    c = s.recv(65536)
                    if not c: break
                    got += c
            except OSError:
                pass
            return got == hello
        print("matching sni echoed:", tunnel("allowed.example"))
        print("other sni echoed:", tunnel("evil.example"))
    """)
    res = SandboxRunner(timeout_seconds=20, egress_allowlist=["allowed.example"]).run_code(code, allow_network=True)
    assert res.status == "passed", res.stderr
    assert "matching sni echoed: True" in res.stdout and "other sni echoed: False" in res.stdout
