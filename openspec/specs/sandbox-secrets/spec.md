# sandbox-secrets Specification

## Purpose
Defines which secret material code running in the sandbox can reach.

## Requirements

### Requirement: Provider keys are not in the sandbox environment
Sandboxed code SHALL NOT see API keys from the parent's environment, including when a caller passes them in `extra_env`.

#### Scenario: Key in the parent environment
- **WHEN** `GROQ_API_KEY` is set in Zedek's process and sandboxed code prints its environment
- **THEN** the output contains neither the name nor the value

#### Scenario: Key forwarded by a caller
- **WHEN** a caller passes `{"OPENAI_API_KEY": "..."}` as `extra_env`
- **THEN** the variable is not set in the child and the drop is logged by name

### Requirement: Secret files are not readable from project modes
Under the copy-on-write mirror, and under bubblewrap's read-only project mode, secret files in the project SHALL NOT be readable.

#### Scenario: Test suite reads .env
- **WHEN** a test in a mirrored project opens `.env`
- **THEN** the file does not exist in the mirror, while `.env.example` does

#### Scenario: Read-only project reads .env
- **WHEN** code in read-only project mode under bubblewrap reads the project's `.env`
- **THEN** it reads nothing

### Requirement: No silent downgrade to unisolated execution
Without working bubblewrap, the sandbox SHALL NOT run code unless unisolated execution was explicitly allowed.

#### Scenario: Bubblewrap missing, no opt-in
- **WHEN** bubblewrap is unusable and `ZEDEK_SANDBOX_ALLOW_UNISOLATED` is not set
- **THEN** the run returns status `unavailable`, the code does not execute, and the message says how to fix it

#### Scenario: Bubblewrap missing, opted in
- **WHEN** bubblewrap is unusable and `ZEDEK_SANDBOX_ALLOW_UNISOLATED=1`
- **THEN** the code runs under the rlimit fallback and the run is logged as unisolated

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
