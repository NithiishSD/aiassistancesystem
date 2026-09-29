# request-tracing Specification

## Purpose
Defines how Zedek makes a single user turn traceable across modules, and records every language-model call in a standard, backend-neutral format with token counts.

## Requirements

### Requirement: One trace identifier per user turn
Every log record written while a user message is being handled SHALL carry the same trace identifier, whichever module writes it. Different turns SHALL have different identifiers. Log records written outside a turn SHALL NOT carry a turn identifier.

#### Scenario: Same turn, different modules
- **WHEN** handling one message produces log records from the orchestrator and from the language-model provider layer
- **THEN** all of those records carry the same trace identifier

#### Scenario: Two turns
- **WHEN** two messages are handled one after the other
- **THEN** their records carry different trace identifiers

#### Scenario: Outside a turn
- **WHEN** a module logs while no message is being handled
- **THEN** the record carries no trace identifier

### Requirement: Session identifier on every turn
All turns in one run of the assistant SHALL share a conversation identifier, recorded as `gen_ai.conversation.id`.

#### Scenario: Two turns, one session
- **WHEN** two messages are handled in the same run
- **THEN** their records carry different trace identifiers and the same conversation identifier

### Requirement: Every language-model call is recorded in the standard format
Each successful language-model call SHALL produce one record with `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, and the call's duration. The record SHALL NOT contain prompt or response text.

#### Scenario: Cloud call with usage
- **WHEN** a cloud provider answers and reports 120 input and 40 output tokens
- **THEN** the call's record has `gen_ai.usage.input_tokens` 120 and `gen_ai.usage.output_tokens` 40, plus the provider and model names

#### Scenario: Provider omits usage
- **WHEN** a provider answers without usage information
- **THEN** the record is still written, with null token counts, and the call succeeds

#### Scenario: No content in the record
- **WHEN** any call is recorded
- **THEN** neither the prompt text nor the response text appears in the record
