# Spec Delta

## ADDED Requirements

### Requirement: Sandbox network access is limited to allowlisted hosts
A network-enabled sandbox run SHALL reach only allowlisted DNS names on ports 80/443, only at globally routable addresses, and only through the egress proxy; the sandbox SHALL have no other network route.

#### Scenario: Allowed host
- **WHEN** code in a run allowlisting `pypi.org` requests `https://pypi.org/simple/requests/`
- **THEN** the request succeeds with normal certificate verification

#### Scenario: Other host
- **WHEN** the same code requests `https://example.com/`
- **THEN** the proxy answers 403 and nothing is sent to example.com

#### Scenario: Bypassing the proxy
- **WHEN** the code opens a socket directly to any address
- **THEN** there is no route

#### Scenario: Allowed name, internal address
- **WHEN** an allowlisted name resolves to a private, loopback, link-local or mapped-loopback address
- **THEN** the connection is refused

#### Scenario: SNI mismatch
- **WHEN** a tunnel to an allowlisted host carries a ClientHello naming another host
- **THEN** the tunnel closes before any byte reaches the upstream

#### Scenario: No bubblewrap
- **WHEN** bubblewrap is unusable and a network run is requested
- **THEN** it is refused even with the unisolated opt-in
