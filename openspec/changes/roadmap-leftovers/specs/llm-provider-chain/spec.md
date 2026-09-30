# Spec Delta

## ADDED Requirements

### Requirement: A provider that reports its allowance used up is skipped until it resets
When a successful reply reports that no requests remain, or fewer than 1,000 tokens remain, in the provider's current window, the provider SHALL NOT be contacted again until the reset time it reports (60 seconds when it reports none, at most one hour). A reply that reports room left, or reports nothing, SHALL change nothing, and an existing longer wait SHALL NOT be shortened.

#### Scenario: Last request of the window
- **WHEN** a reply reports 0 requests remaining and a reset in 30 seconds
- **THEN** the next request within 30 seconds skips that provider without contacting it, and a request after that may use it

#### Scenario: Room left
- **WHEN** a reply reports 999 requests and 7,418 tokens remaining
- **THEN** the provider stays available

### Requirement: The source of the last answer can be shown
The system SHALL keep which provider and model produced the most recent answer, and the `providers` command SHALL show it together with the per-provider usage report.

#### Scenario: After a fallback
- **WHEN** the first provider fails and the second answers
- **THEN** `providers` names the second provider as the source of the last answer
