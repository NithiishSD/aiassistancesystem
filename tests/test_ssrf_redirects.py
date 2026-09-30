"""fetch_url re-checks every redirect hop and blocks every non-global address
(OpenSpec change: ssrf-redirect-guard)."""

import ipaddress
import socket
from unittest.mock import MagicMock, patch

import pytest

import mcp_online_server as mos

PUBLIC_IP = "93.184.216.34"


def _resolver(mapping):
    def fake(host, port, *a, **k):
        try:
            ip = str(ipaddress.ip_address(host))  # an IP literal resolves to itself, as in real DNS
        except ValueError:
            ip = mapping.get(host, PUBLIC_IP)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]
    return fake


def _resp(status, location=None, text="ok", ctype="text/plain"):
    r = MagicMock(status_code=status, text=text)
    r.headers = {"content-type": ctype, **({"location": location} if location else {})}
    return r


def _client(responses):
    client = MagicMock()
    client.get.side_effect = responses
    cls = MagicMock()
    cls.return_value.__enter__ = lambda s: client
    cls.return_value.__exit__ = MagicMock(return_value=False)
    return cls, client


@pytest.mark.parametrize("ip", ["0.0.0.0", "100.64.1.1", "198.18.0.1", "224.0.0.1", "::ffff:127.0.0.1",
                                "127.0.0.1", "169.254.169.254", "10.1.2.3"])
def test_non_global_addresses_blocked(ip):
    assert mos._ip_blocked(ipaddress.ip_address(ip))


def test_public_address_allowed():
    assert not mos._ip_blocked(ipaddress.ip_address(PUBLIC_IP))


def test_redirect_to_metadata_endpoint_is_blocked():
    cls, client = _client([_resp(302, "http://169.254.169.254/latest/meta-data/")])
    with patch.object(mos.socket, "getaddrinfo", _resolver({})), patch.object(mos.httpx, "Client", cls):
        out = mos.fetch_url("https://public.example/redirect")
    assert out.startswith("[blocked]") and "169.254.169.254" in out
    assert client.get.call_count == 1  # the internal address was never requested


def test_redirect_to_host_resolving_private_is_blocked():
    cls, client = _client([_resp(301, "https://sneaky.example/next")])
    with patch.object(mos.socket, "getaddrinfo", _resolver({"sneaky.example": "192.168.1.1"})), \
         patch.object(mos.httpx, "Client", cls):
        assert mos.fetch_url("https://public.example/a").startswith("[blocked]")
    assert client.get.call_count == 1


def test_public_redirects_are_followed_with_relative_locations():
    cls, client = _client([_resp(302, "/moved"), _resp(200, text="final page")])
    with patch.object(mos.socket, "getaddrinfo", _resolver({})), patch.object(mos.httpx, "Client", cls):
        assert mos.fetch_url("https://public.example/start") == "final page"
    assert [c.args[0] for c in client.get.call_args_list] == ["https://public.example/start", "https://public.example/moved"]
    assert all(c.kwargs["follow_redirects"] is False for c in client.get.call_args_list)


def test_redirect_loop_is_capped():
    cls, client = _client([_resp(302, "https://public.example/loop")] * 10)
    with patch.object(mos.socket, "getaddrinfo", _resolver({})), patch.object(mos.httpx, "Client", cls):
        assert "Too many redirects" in mos.fetch_url("https://public.example/loop")
    assert client.get.call_count == mos._MAX_REDIRECTS + 1
