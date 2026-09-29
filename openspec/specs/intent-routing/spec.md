# intent-routing Specification

## Purpose
Defines observable guarantees of Zedek's intent classification: what the reported confidence score means, and that measurement-related changes do not alter which intent a request is routed to.

## Requirements

### Requirement: Reported score reflects actual similarity
When a request is resolved by the local (embedding) classification layer, the reported score SHALL be the similarity between the request and the matched intent, not a fixed constant. Different requests matched to the same intent SHALL be able to report different scores.

#### Scenario: Two matches report their own scores
- **WHEN** two different requests are both resolved locally to the same intent
- **THEN** each reported score is that request's own similarity value, and the values are not required to be equal

#### Scenario: Local match never reports below the match threshold
- **WHEN** a request is resolved by the local layer
- **THEN** its reported score is at least the local match threshold

### Requirement: Score reporting does not change routing
Changing how the local-layer score is reported SHALL NOT change which intent any request is routed to, nor whether it escalates to the language-model fallback.

#### Scenario: Same decisions before and after
- **WHEN** the golden evaluation set is classified before and after the score-reporting change
- **THEN** every utterance receives the same intent or escalation outcome in both runs

### Requirement: Classification can run on a reproducible router
It SHALL be possible to build the local classification layer from only its built-in phrases, excluding phrases learned at runtime and phrases registered from external tool descriptions, without affecting the router used in normal operation.

#### Scenario: Learned phrases excluded on request
- **WHEN** a reproducible router is built on a machine that has runtime-learned phrases
- **THEN** those phrases play no part in its classifications, and the normal-operation router still includes them
