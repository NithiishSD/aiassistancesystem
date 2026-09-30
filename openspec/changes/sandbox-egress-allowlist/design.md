# Design

- **Why a proxy and not slirp4netns/pasta:** neither is installed; a Unix-socket bridge needs only bubblewrap and the standard library, and lets the policy see host names (needed for an FQDN allowlist and SNI checks).
- **Why the shim is inside:** a new network namespace cannot reach the host's loopback; a Unix socket file crosses the namespace boundary through a bind mount.
- **Unix socket path:** kept under `/tmp` (AF_UNIX paths are limited to ~108 bytes).
- **Connect to the checked IP:** resolution and connection use the same address list, so a second DNS answer cannot redirect the connection.
- **SNI check placement:** after `200 Connection established`, the first TLS record is read and parsed before anything is forwarded.
