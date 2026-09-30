# Spec Delta

## ADDED Requirements

### Requirement: The digest can be asked for in plain language
A request for today's briefing or what to focus on today SHALL be routed to the digest and answered with the same text the scheduled job delivers, built without a model call. When there is nothing to report, the reply SHALL say so.

#### Scenario: Asking what to focus on
- **WHEN** the user types "what should I focus on today"
- **THEN** the reply is today's digest and no language model is called

#### Scenario: Nothing to report
- **WHEN** the user asks for the digest and no topic is stale or weak and no commitment is stored
- **THEN** the reply is "Nothing to report today."

#### Scenario: A news request is not a digest request
- **WHEN** the user types "today's headlines please"
- **THEN** the request goes to the connected news tool, not the digest
