# llm-provider-chain Specification

## Purpose
Defines how Zedek chooses among its language-model providers: the order it tries them in, when it skips one, how daily free-tier budgets are respected across restarts, when it falls back to the local model, and how usage is reported.

## Requirements

### Requirement: Rate-limited providers are skipped until their retry time
When a provider rejects a request for rate limiting, it SHALL NOT be contacted again until its retry time has passed. The retry time SHALL come from the provider's response when present and parseable, SHALL default to 60 seconds otherwise, and SHALL NOT exceed one hour.

#### Scenario: Retry-After honoured
- **WHEN** a provider responds with a rate-limit error and a retry time of 30 seconds
- **THEN** requests during the next 30 seconds skip that provider without contacting it, and requests after that may use it again

#### Scenario: Missing retry time
- **WHEN** a provider responds with a rate-limit error and no usable retry time
- **THEN** it is skipped for 60 seconds

### Requirement: Authentication failures stop repeated attempts
When a provider rejects a request as unauthorized or forbidden, it SHALL be skipped for one hour rather than retried on every request.

#### Scenario: Bad key
- **WHEN** a provider responds with an authorization error
- **THEN** subsequent requests within the hour skip it

### Requirement: Daily request budgets survive restarts
For each provider with a configured daily request budget, the number of requests sent to it each day SHALL be counted and persisted, and the provider SHALL be skipped for the rest of that day once the budget is reached, including after the assistant restarts. Requests that never reached the provider (for example, because no key is configured) SHALL NOT be counted.

#### Scenario: Budget reached
- **WHEN** a provider with a daily budget of 50 has already received 50 requests today
- **THEN** further requests today skip it

#### Scenario: Counts survive a restart
- **WHEN** the assistant restarts on the same day
- **THEN** the day's counts for each provider are the same as before the restart

#### Scenario: New day
- **WHEN** the date changes
- **THEN** budgets start again from zero

### Requirement: Skips fall through to the next provider and finally the local model
Skipping a provider SHALL move on to the next provider in the chain. If every cloud provider is skipped or fails, the local model SHALL be used, as it is today. Every skip SHALL be logged with its reason.

#### Scenario: Everything cooling down
- **WHEN** every cloud provider in a task's chain is in cooldown
- **THEN** the request is answered by the local model and no cloud provider is contacted

### Requirement: Usage is reportable
The system SHALL report, per provider: calls, successes, failures, and rate-limit rejections in the current session; requests today; the daily budget, if any; and the time remaining on any active cooldown.

#### Scenario: Stats after a rate limit
- **WHEN** one provider has been rate-limited and another has served requests
- **THEN** the report shows the first provider's rejection count and remaining cooldown, and the second provider's success count and requests today
