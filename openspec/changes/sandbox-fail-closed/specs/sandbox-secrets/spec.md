# Spec Delta

## ADDED Requirements

### Requirement: No silent downgrade to unisolated execution
Without working bubblewrap, the sandbox SHALL NOT run code unless unisolated execution was explicitly allowed.

#### Scenario: Bubblewrap missing, no opt-in
- **WHEN** bubblewrap is unusable and `ZEDEK_SANDBOX_ALLOW_UNISOLATED` is not set
- **THEN** the run returns status `unavailable`, the code does not execute, and the message says how to fix it

#### Scenario: Bubblewrap missing, opted in
- **WHEN** bubblewrap is unusable and `ZEDEK_SANDBOX_ALLOW_UNISOLATED=1`
- **THEN** the code runs under the rlimit fallback and the run is logged as unisolated
