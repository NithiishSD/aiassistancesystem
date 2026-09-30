# Spec Delta

## ADDED Requirements

### Requirement: The routing decision is typed
The decision passed from routing to execution SHALL be a typed object carrying the user's input; an unknown field SHALL be an error, not a silent default.

#### Scenario: Misspelled field
- **WHEN** a caller passes a decision with the key `orignal_input`
- **THEN** execution raises instead of running with an empty input
