# Spec Delta

## ADDED Requirements

### Requirement: Clarifying questions only for bare ambiguous topics
A clarifying question SHALL pre-empt routing only when an ambiguous term is the only topic of the message.

#### Scenario: Request with context
- **WHEN** the user says "port this javascript to typescript"
- **THEN** no clarifying question is asked and the request is routed

#### Scenario: Bare topic
- **WHEN** the user says "tell me about python"
- **THEN** Zedek asks which meaning of "python" is meant
