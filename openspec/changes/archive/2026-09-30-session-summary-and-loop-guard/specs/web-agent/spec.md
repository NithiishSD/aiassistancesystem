# Spec Delta

## ADDED Requirements

### Requirement: A repeated identical action ends the run
If the action-selecting model proposes an action with the same tool and the same arguments as one that already ran in the current browsing run, whether it succeeded or failed, the run SHALL end without executing it and without asking the user to confirm it, and the report SHALL say why.

#### Scenario: Same page again
- **WHEN** a navigate to a URL succeeded and the same navigate is proposed again
- **THEN** it is not executed, no confirmation is requested, and the report notes the repeat

#### Scenario: Same failed action again
- **WHEN** an action failed and the identical action is proposed again
- **THEN** the run ends

#### Scenario: Different page
- **WHEN** the next action targets a different URL
- **THEN** the run continues within the step limit
