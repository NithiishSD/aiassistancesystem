# structured-output Specification

## Purpose
Defines how Zedek gets structured replies from language models: requests are constrained to a schema where the provider supports it, replies are validated, a wrong shape gets one correction attempt, and callers only ever receive validated data.

## Requirements

### Requirement: Structured replies are validated against a schema
A structured request SHALL return data that has been validated against the caller's schema. Callers SHALL NOT receive unvalidated data from a structured request.

#### Scenario: Valid reply
- **WHEN** the first provider returns JSON matching the schema
- **THEN** the caller receives the validated data and the provider's name

#### Scenario: No provider produces valid data
- **WHEN** every provider, including the local model, returns replies that fail validation
- **THEN** the request fails with a structured-output error, and the caller uses its own safe fallback

### Requirement: One correction attempt per provider
When a provider's reply fails validation, the system SHALL send that provider one follow-up request containing the validation error. If the follow-up also fails, it SHALL move to the next provider. The failure SHALL be logged without the reply text.

#### Scenario: Corrected on re-ask
- **WHEN** a provider's first reply is missing a required field and its second reply is valid
- **THEN** the caller receives the second reply's data from the same provider

#### Scenario: Still wrong after re-ask
- **WHEN** a provider's first and second replies both fail validation
- **THEN** the next provider in the chain is asked

### Requirement: Providers are constrained natively where supported
Each provider SHALL receive the schema in its native constrained-output form when it supports one. Where it does not, the schema SHALL be stated in the prompt and JSON output requested. A provider that rejects the native form SHALL be retried once without it, and SHALL NOT be sent the native form again in the same session.

#### Scenario: Native form rejected
- **WHEN** a provider answers a native-schema request with HTTP 400
- **THEN** the same request is retried in plain JSON mode with the schema in the prompt, and later requests to that provider skip the native form

#### Scenario: Local model
- **WHEN** the local model handles a structured request
- **THEN** it is given the schema as its output format

### Requirement: Intent fallback can only return a known intent
The language-model intent fallback SHALL constrain its answer to the set of valid intent names.

#### Scenario: Constrained intent
- **WHEN** the intent fallback runs
- **THEN** its schema permits only the valid intent names

### Requirement: Canonicalized facts keep their storage format
Facts extracted from a user statement SHALL be stored as `User's <attribute>: <value>`. A statement with no concrete fact SHALL produce no facts.

#### Scenario: Two facts
- **WHEN** the extraction returns college "PSG College of Technology" and department "AMCS"
- **THEN** the facts are "User's college: PSG College of Technology" and "User's department: AMCS"

#### Scenario: Placeholder value
- **WHEN** an extracted value is "unknown" or empty
- **THEN** that fact is dropped
