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
