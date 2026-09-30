# Spec Delta

## ADDED Requirements

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
