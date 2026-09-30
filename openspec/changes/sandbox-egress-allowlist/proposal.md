# Proposal

Implements **ROADMAP B4** (egress allowlist for network-enabled sandbox runs).

## Why

With `allow_network=True`, bubblewrap shared the host's network namespace: sandboxed (often model-written) code could reach the whole internet and the local network, including cloud metadata and services on localhost. Nothing calls it with `True` yet; it must be safe before anything does.

## What Changes

- **No network of its own, ever:** every bubblewrap run uses `--unshare-all` (a private namespace with loopback only).
- **One way out:** with `allow_network`, a host-side proxy (`sandbox_egress.EgressProxy`) listens on a Unix socket bound into the sandbox; `sandbox_egress_shim.py` (stdlib only, run first inside the sandbox) bridges `127.0.0.1:<port>` to it and sets `HTTP(S)_PROXY`. Code that ignores the proxy has no route and no DNS.
- **Proxy policy (fail-closed):**
  - `CONNECT host:443` and absolute `http://host/...` on port 80 only; DNS names only (IP literals refused).
  - FQDN allowlist: `example.org` (exact) or `*.example.org` (subdomains only); invalid entries, including IP addresses, raise at startup.
  - Every resolved address must be globally routable, and the proxy connects to the address it checked, **closing DNS rebinding** for sandbox traffic.
  - HTTPS: the TLS ClientHello's SNI must equal the approved host; otherwise the tunnel closes before a byte reaches upstream.
  - Plain HTTP: re-issued in origin form with `Host` pinned to the approved host, `Connection: close`, proxy headers dropped; header lines with bare CR/LF are rejected (Host smuggling).
  - Bounded heads (16 KB), concurrent connections (16, then 503) and idle time (30 s); the socket lives in a 0700 temp dir removed after the run.
- **Allowlist source:** `SandboxRunner(egress_allowlist=[...])`, default `ZEDEK_SANDBOX_EGRESS_ALLOWLIST` (comma-separated); empty means no destination is allowed.
- **Without bubblewrap, network runs are refused**, even with `ZEDEK_SANDBOX_ALLOW_UNISOLATED=1`: the allowlist cannot be enforced without namespaces.
- `net_policy.py`: address and host rules shared with the online MCP server's fetch tools (one implementation).

## Verification

47 tests with real sockets (fake DNS and upstreams), 3 of them end to end inside bubblewrap; stable over 5 repeated runs. Live against the internet with `egress_allowlist=["pypi.org"]`: HTTPS to pypi.org works with normal certificate verification; example.com (http and https) and files.pythonhosted.org get 403; a direct socket has no DNS or route.

## Residual risk (documented)

Inside TLS the HTTP `Host` header is not visible, so domain fronting through a CDN that serves an allowed name remains possible where the CDN allows SNI/Host mismatches (major CDNs reject them). Wildcards over public suffixes (e.g. `*.github.io`) allow every site under them; configure with care. A ClientHello split across TLS records is refused (fail closed).

## Capabilities

### Modified Capabilities
- `sandbox-secrets`: sandbox network egress.

## Impact

- **Code:** new `net_policy.py`, `sandbox_egress.py`, `sandbox_egress_shim.py`; `sandbox_runner.py`, `mcp_online_server.py`.
- **Behavior:** runs without `allow_network` are unchanged (they already unshared the network).
