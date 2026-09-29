# Spec Delta

## MODIFIED Requirements

### Requirement: Authentication failures stop repeated attempts
When a provider rejects a request as unauthorized, forbidden, or payment required, it SHALL be skipped for one hour rather than retried on every request.

#### Scenario: Bad key
- **WHEN** a provider responds with an authorization error
- **THEN** subsequent requests within the hour skip it

#### Scenario: Payment required
- **WHEN** a provider responds with HTTP 402
- **THEN** subsequent requests within the hour skip it

## ADDED Requirements

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
