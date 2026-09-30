"""Which network destinations Zedek's code may reach (shared by the online MCP
server's fetch tools and the sandbox egress proxy).

Only globally routable unicast addresses pass, and allowlists name hosts, never
IP addresses. Everything here fails closed: anything unparseable is refused.
"""

from __future__ import annotations

import ipaddress
import re

# Kept as a second check next to `is_global`, which already covers all of them.
BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),      # loopback
    ipaddress.ip_network("10.0.0.0/8"),       # RFC-1918 private
    ipaddress.ip_network("172.16.0.0/12"),    # RFC-1918 private
    ipaddress.ip_network("192.168.0.0/16"),   # RFC-1918 private
    ipaddress.ip_network("169.254.0.0/16"),   # link-local / cloud metadata
    ipaddress.ip_network("::1/128"),          # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),         # IPv6 ULA (private)
    ipaddress.ip_network("fe80::/10"),        # IPv6 link-local
]

_LABEL = r"(?!-)[a-z0-9-]{1,63}(?<!-)"
_HOSTNAME_RE = re.compile(rf"^{_LABEL}(?:\.{_LABEL})+$")
_ALLOW_ENTRY_RE = re.compile(rf"^(?:\*\.)?{_LABEL}(?:\.{_LABEL})+$")


def ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True unless `ip` is a globally routable unicast address. Unwraps
    IPv4-mapped IPv6 (::ffff:127.0.0.1), and catches 0.0.0.0 (reaches
    localhost on Linux), CGNAT, benchmarking ranges and multicast."""
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return (not ip.is_global) or ip.is_multicast or any(ip in net for net in BLOCKED_NETWORKS)


def address_blocked(raw: str) -> bool:
    """`ip_blocked` for a textual address; unparseable addresses are blocked."""
    try:
        return ip_blocked(ipaddress.ip_address(raw.split("%", 1)[0]))  # drop an IPv6 zone id
    except ValueError:
        return True


def normalize_host(host: str) -> str | None:
    """Lowercase ASCII DNS name without a trailing dot, or None if `host` is not
    one (IP literals, IDN/Unicode, empty labels and underscores are refused)."""
    host = (host or "").strip().lower().rstrip(".")
    if not host or len(host) > 253 or not _HOSTNAME_RE.match(host):
        return None
    try:
        ipaddress.ip_address(host)
        return None  # all-numeric dotted names are addresses, not hosts
    except ValueError:
        return host


def parse_allowlist(entries) -> tuple[str, ...]:
    """Validate allowlist entries: `example.org` (that host only) or
    `*.example.org` (subdomains only, not example.org itself). Raises
    ValueError on anything else, so a typo cannot silently widen access."""
    parsed = []
    for entry in entries or ():
        entry = str(entry).strip().lower().rstrip(".")
        if not entry:
            continue
        bare = entry[2:] if entry.startswith("*.") else entry
        if not _ALLOW_ENTRY_RE.match(entry) or normalize_host(bare) is None:
            # normalize_host also refuses IP literals such as 1.2.3.4.
            raise ValueError(f"invalid egress allowlist entry: {entry!r}")
        parsed.append(entry)
    return tuple(dict.fromkeys(parsed))


def host_allowed(host: str, allowlist: tuple[str, ...]) -> bool:
    """Exact match, or a `*.suffix` entry with a label boundary."""
    for entry in allowlist:
        if entry.startswith("*."):
            if host.endswith(entry[1:]) and host != entry[2:]:
                return True
        elif host == entry:
            return True
    return False
