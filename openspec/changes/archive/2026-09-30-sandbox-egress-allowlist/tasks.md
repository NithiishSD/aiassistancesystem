# Tasks

- [x] 1.1 `net_policy.py` (address rules, host normalization, allowlist parsing/matching); online MCP server uses it.
- [x] 1.2 `sandbox_egress.EgressProxy` (CONNECT + SNI check, origin-form HTTP, connect-to-checked-IP, limits) and `sandbox_egress_shim.py`.
- [x] 1.3 `sandbox_runner`: always `--unshare-all`; proxy + shim for `allow_network`; allowlist config; refuse network runs without bubblewrap.
- [x] 1.4 Tests (47, real sockets; 3 end to end in bubblewrap), 5 repeat runs, live internet check.
- [x] 1.5 Full suite and routing gate, 0 failures (1126 → 1173). Update ROADMAP B4.
