# Spec Delta

## ADDED Requirements

### Requirement: Explanation-style questions are not turned into actions locally
When the local classification layer's best match for a request is an explanation-style or concept question (e.g. "what is ram used for", "how are threads different from processes"), the request SHALL be treated as a general question. The local layer SHALL NOT route it to an action. Recognizing a request as a general question SHALL NOT cause it to be answered without the same fallback it receives today.

#### Scenario: Concept question sharing an action's vocabulary
- **WHEN** the request is "explain what ram is"
- **THEN** the local layer does not route it to the memory-process action

#### Scenario: General question keeps today's fallback
- **WHEN** the local layer recognizes a request as a general question
- **THEN** it follows the same path as an unrecognized request (the language-model fallback), not a direct chat answer and not an action
