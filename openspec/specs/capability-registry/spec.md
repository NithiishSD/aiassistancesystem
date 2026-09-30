# capability-registry Specification

## Purpose
Defines the single source of Zedek's capability definitions.

## Requirements

### Requirement: One definition per capability
Router utterances, thresholds, the Layer-2 tool list, tiers and argument types SHALL all be derived from `capabilities/*.yaml`.

#### Scenario: Adding a capability
- **WHEN** a new capability file is added and listed in `capabilities/index.yaml`
- **THEN** it is routable, offered to the Layer-2 LLM, and tiered without editing any other table

### Requirement: Invalid definitions stop startup
A capability definition that is malformed, has unknown keys, an out-of-range tier or threshold, or disagrees with the index SHALL prevent Zedek from starting.

#### Scenario: Tier typo
- **WHEN** a capability file declares `tier: 4`
- **THEN** loading raises and no capability table is built

### Requirement: Tiers are pinned
The tier table SHALL be pinned by a test, so a tier change requires a reviewed test edit.

#### Scenario: Lowering a tier
- **WHEN** `open_application.yaml` is edited to `tier: 0`
- **THEN** the test suite fails until the pinned table is updated too

### Requirement: Dispatch comes from the registry
`execute()` SHALL call the handler named in the routed capability's definition; names that are not capabilities (other than qualified MCP tool names) SHALL be blocked.

#### Scenario: Unknown function
- **WHEN** a decision names a function that is not a capability
- **THEN** it is blocked and nothing runs

#### Scenario: Unsure router
- **WHEN** the router is unsure and the capability does not declare `runs_when_unsure`
- **THEN** the request is answered as a general question and no action runs

### Requirement: Help is generated from the registry
The REPL `help` command SHALL list every user-facing capability's summary.

#### Scenario: Asking for help
- **WHEN** the user types `help`
- **THEN** one line per capability is printed, from its `summary`
