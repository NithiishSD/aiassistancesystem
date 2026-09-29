# Spec Delta

## ADDED Requirements

### Requirement: Keyword matches reach the relevance ranking
Candidate facts for relevance ranking SHALL include facts that share distinctive words with the question, not only those nearest by embedding. The same relevance gate SHALL apply to every candidate.

#### Scenario: Exact code in a large store
- **WHEN** the store holds hundreds of similar facts and the user asks "which course is 23XT51"
- **THEN** the fact containing "23XT51" is among the candidates, and it is returned if it passes the relevance gate

#### Scenario: Keyword match that is not relevant
- **WHEN** a general-knowledge question shares a word with a stored fact
- **THEN** that fact is still excluded unless it passes the relevance gate

### Requirement: Keyword search follows the store
The keyword index SHALL reflect facts added or deleted in a domain before that domain is next searched.

#### Scenario: New fact
- **WHEN** a fact is stored and a question matching its words is asked next
- **THEN** the new fact is among the keyword candidates

#### Scenario: Deleted fact
- **WHEN** a fact is deleted
- **THEN** it is no longer among the keyword candidates

### Requirement: Retrieval without keyword search
If keyword search is unavailable, retrieval SHALL continue with embedding candidates only, and log that it did so.

#### Scenario: Keyword library missing
- **WHEN** the keyword index cannot be built
- **THEN** relevant facts are still returned from embedding candidates, and a degradation is logged
