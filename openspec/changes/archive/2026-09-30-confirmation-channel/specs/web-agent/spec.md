# Spec Delta

## MODIFIED Requirements

### Requirement: Existing confirmation guarantees are preserved
Every browser action SHALL still require the user's explicit confirmation through the injected confirmation mechanism. The default mechanism SHALL still deny. An action refused without a reason SHALL end the browsing run. An action refused with a reason SHALL NOT be executed; the reason SHALL be given to the action-selecting model as the user's own words, and the run SHALL continue within the step limit, with every further action needing its own confirmation. If the same refused action is proposed again, the run SHALL end.

#### Scenario: No confirmation supplied
- **WHEN** browsing runs without an interactive confirmation mechanism
- **THEN** no browser action executes

#### Scenario: Refused without a reason
- **WHEN** the user refuses a browser action with "n"
- **THEN** the run ends and no further action is proposed

#### Scenario: Refused with a reason
- **WHEN** the user refuses with "not that page, open the docs"
- **THEN** the refused action is not executed, the next request to the action-selecting model includes the user's reason, and the next proposed action asks for confirmation again

#### Scenario: Same action proposed again
- **WHEN** the action-selecting model proposes the action the user just refused
- **THEN** the run ends without asking the user again
