# Spec Delta

## ADDED Requirements

### Requirement: One configurable local model
Every local-model call SHALL use a single model setting, overridable with `ZEDEK_LOCAL_MODEL`.

#### Scenario: Rollback by setting
- **WHEN** `ZEDEK_LOCAL_MODEL=llama3.1:8b` is set
- **THEN** the provider chain's local fallback and the orchestrator's direct local calls all use `llama3.1:8b`

### Requirement: Local calls never think
Every local-model call SHALL request that the model not produce a reasoning trace.

#### Scenario: Structured local call
- **WHEN** the orchestrator extracts function arguments locally with JSON output
- **THEN** the request disables thinking and the reply is parsed as JSON
