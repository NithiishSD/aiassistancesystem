# Spec Delta

## MODIFIED Requirements

### Requirement: Usage is reportable
The system SHALL report, per provider: calls, successes, failures, and rate-limit rejections in the current session; requests today; the daily budget, if any; the time remaining on any active cooldown; and the session's total input and output tokens.

#### Scenario: Stats after a rate limit
- **WHEN** one provider has been rate-limited and another has served requests
- **THEN** the report shows the first provider's rejection count and remaining cooldown, and the second provider's success count and requests today

#### Scenario: Token totals
- **WHEN** a provider has served two calls reporting 100/20 and 50/10 input/output tokens
- **THEN** the report shows 150 input and 30 output tokens for that provider
