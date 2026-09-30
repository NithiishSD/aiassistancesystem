# Spec Delta

## Purpose

Defines how Zedek asks the user to approve a risky action, what counts as approval, and how a refusal and its reason are handled, independently of whether the user is at a terminal, a UI or a voice interface.

## ADDED Requirements

### Requirement: Confirmations go through one replaceable channel
Every request for the user's approval SHALL be made through a single confirmation channel that can be replaced without changing the code that asks. The terminal SHALL be the default channel.

#### Scenario: Replaced channel receives the request
- **WHEN** a different channel is active and a Tier 2 action needs approval
- **THEN** that channel receives the request and the terminal is not read

#### Scenario: Channel restored after a task
- **WHEN** a channel is activated for the duration of a task and the task ends, including by error
- **THEN** the previously active channel is active again

### Requirement: Only an explicit yes approves
An answer SHALL count as approval only when it is exactly "y" or "yes", ignoring case and surrounding spaces. Every other answer, including an empty one, SHALL be a refusal.

#### Scenario: Approval
- **WHEN** the user answers "Y" or " yes "
- **THEN** the action is approved

#### Scenario: Anything else refuses
- **WHEN** the user answers "", "n", "ok", "sure", or "yes please do"
- **THEN** the action is refused

### Requirement: A refusal can carry a reason
When the user's refusal contains text other than "n" or "no", that text SHALL be kept as the reason for the refusal, with a leading "n"/"no" and punctuation removed.

#### Scenario: Reason after no
- **WHEN** the user answers "no, use the staging file instead"
- **THEN** the refusal's reason is "use the staging file instead"

#### Scenario: Bare refusal
- **WHEN** the user answers "n"
- **THEN** the refusal has no reason

### Requirement: No channel means no approval
When no channel is able to ask the user, the answer SHALL be a refusal, and the refusal SHALL be logged.

#### Scenario: Non-interactive run
- **WHEN** the deny-all channel is active and a Tier 2 action is requested
- **THEN** the action is not executed

#### Scenario: Input stream closed
- **WHEN** the terminal channel cannot read an answer
- **THEN** the answer is a refusal

### Requirement: A refused coding plan is revised from the user's reason
When the user refuses a coding plan and gives a reason, the system SHALL produce a revised plan that takes the reason into account and ask for approval of the revised plan. It SHALL do this at most twice per request, and SHALL NOT generate or modify code until a plan is approved.

#### Scenario: Revised plan approved
- **WHEN** the user refuses a plan with "don't touch config.py" and approves the revised plan
- **THEN** code is generated against the revised plan only

#### Scenario: Bare refusal
- **WHEN** the user refuses a plan with "n"
- **THEN** no revised plan is produced and nothing is generated

#### Scenario: Revision limit
- **WHEN** the user refuses with a reason three times
- **THEN** no further plan is produced and nothing is generated
