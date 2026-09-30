# Spec Delta

## Purpose

How the tier gate combines its rule lanes with the choosing model's own risk label.

## ADDED Requirements

### Requirement: Model risk can only raise a tier
The tier gate SHALL accept the choosing model's risk label and use the higher of the rule tier and the label's tier; the label SHALL never lower a tier or turn a block into anything else.

#### Scenario: HIGH on a read-only function
- **WHEN** a Tier 0 call carries risk HIGH
- **THEN** the gate asks for confirmation

#### Scenario: LOW on a blocked call
- **WHEN** a call matching a Tier 3 pattern carries risk LOW
- **THEN** it is still blocked

### Requirement: MCP calls carry a risk label
Argument extraction for an MCP tool SHALL return a risk label from the same model reply, and the call SHALL be gated with it.

#### Scenario: Labelled call
- **WHEN** the model returns `{"args": {...}, "risk": "HIGH"}`
- **THEN** the arguments are validated against the tool schema and the gate receives HIGH
