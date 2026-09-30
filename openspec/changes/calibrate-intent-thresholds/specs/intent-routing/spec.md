# Spec Delta

## ADDED Requirements

### Requirement: The Layer-1 trust threshold is set per intent
Each intent SHALL have its own score threshold above which a Layer-1 match is trusted without the language model, defaulting to the common threshold. An intent's threshold SHALL be lowered below the default only when the development slice of the golden set shows more correct matches and no wrong match within 0.05 below the new threshold, and only if no class loses more than the allowed tolerance on the held-out slice. Intents whose wrong match changes stored data or acts without confirmation SHALL NOT be lowered below the default.

#### Scenario: Calibrated intent
- **WHEN** a request's best route is `research_task` with a score of 0.47
- **THEN** it is handled locally as `research_task` without consulting the language model

#### Scenario: Uncalibrated intent at the same score
- **WHEN** a request's best route is `coding_task` with a score of 0.47
- **THEN** it is escalated to the language model

#### Scenario: Strict intent
- **WHEN** a request's best route is `open_application` with a score of 0.60
- **THEN** it is escalated to the language model

#### Scenario: Memory-changing intents keep the default
- **WHEN** thresholds are loaded
- **THEN** `remember_fact` and `correct_fact` use the common threshold

#### Scenario: Calibration reads only the development slice
- **WHEN** the calibration report is produced
- **THEN** no held-out row is read
