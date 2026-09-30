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
