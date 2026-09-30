# Spec Delta

## ADDED Requirements

### Requirement: Fetches stay on public addresses across redirects
Every URL in a redirect chain SHALL pass the SSRF check before it is requested, and only globally routable unicast addresses SHALL pass.

#### Scenario: Redirect to cloud metadata
- **WHEN** a public URL redirects to `http://169.254.169.254/latest/meta-data/`
- **THEN** the fetch returns a blocked message and the internal address is never requested

#### Scenario: Mapped loopback
- **WHEN** a host resolves to `::ffff:127.0.0.1`
- **THEN** it is blocked
