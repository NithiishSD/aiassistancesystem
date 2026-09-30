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
When a provider rejects a request as unauthorized, forbidden, or payment required, it SHALL be skipped for one hour rather than retried on every request.

#### Scenario: Bad key
- **WHEN** a provider responds with an authorization error
- **THEN** subsequent requests within the hour skip it

#### Scenario: Payment required
- **WHEN** a provider responds with HTTP 402
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
The system SHALL report, per provider: calls, successes, failures, and rate-limit rejections in the current session; requests today; the daily budget, if any; the time remaining on any active cooldown; and the session's total input and output tokens.

#### Scenario: Stats after a rate limit
- **WHEN** one provider has been rate-limited and another has served requests
- **THEN** the report shows the first provider's rejection count and remaining cooldown, and the second provider's success count and requests today

#### Scenario: Token totals
- **WHEN** a provider has served two calls reporting 100/20 and 50/10 input/output tokens
- **THEN** the report shows 150 input and 30 output tokens for that provider

### Requirement: Requests go only to a known chat model
A provider SHALL only be sent requests for a model from its configured candidate list. When the provider's model catalog can be read and contains none of the candidates, the provider SHALL be skipped without being counted against its quota, and a warning SHALL name it.

#### Scenario: Candidates rotted
- **WHEN** a provider's catalog lists only models outside its candidate list
- **THEN** no request is sent to that provider, and the chain moves on

#### Scenario: Catalog unreachable
- **WHEN** a provider's catalog cannot be fetched
- **THEN** its candidates are tried in order

### Requirement: Unavailable models are retired for the session
When a provider answers HTTP 404 for a model, that model SHALL NOT be used again in the session, and the request SHALL be retried once with the provider's next available candidate.

#### Scenario: Listed but not served
- **WHEN** the first candidate returns 404 and the second is available
- **THEN** the request succeeds with the second, and later requests use the second directly

### Requirement: Error bodies are treated as errors
A provider response that carries no answer SHALL be treated as a failed request. If its body reports an error status, that status SHALL drive cooldowns and model retirement, exactly as the same HTTP status would.

#### Scenario: Rate limit reported in a successful response
- **WHEN** a provider answers HTTP 200 with an error body whose code is 429
- **THEN** the provider cools down as for an HTTP 429, and the chain moves on

### Requirement: One configurable local model
Every local-model call SHALL use a single model setting, overridable with `ZEDEK_LOCAL_MODEL`.

#### Scenario: Rollback by setting
- **WHEN** `ZEDEK_LOCAL_MODEL=llama3.1:8b` is set
- **THEN** the provider chain's local fallback and the orchestrator's direct local calls all use `llama3.1:8b`

### Requirement: Local calls never think
Every local-model call SHALL request that the model not produce a reasoning trace.

#### Scenario: Structured local call
- **WHEN** the orchestrator extracts function arguments locally with JSON output
- **THEN** the request disables thinking and the reply is parsed as JSON

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
