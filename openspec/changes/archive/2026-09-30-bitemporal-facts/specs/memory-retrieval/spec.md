# Spec Delta

## ADDED Requirements

### Requirement: Facts that are no longer valid are not retrieved
Facts marked as no longer valid SHALL be excluded from embedding candidates, keyword candidates, relevance-ranked results, and the candidates offered when the user corrects a fact. Excluding them SHALL NOT reduce the number of current candidates considered.

#### Scenario: Superseded fact is not used to answer
- **WHEN** `User's college: A` has been superseded by `User's college: B` and the question is "what college do I study at"
- **THEN** only the fact for B is returned

#### Scenario: Keyword match on a superseded fact
- **WHEN** a question shares a distinctive word with a fact that is no longer valid
- **THEN** that fact is not among the keyword candidates

#### Scenario: Retraction takes effect on the next question
- **WHEN** a fact is marked no longer valid and a matching question is asked next
- **THEN** the fact is not returned, by either embedding or keyword search

#### Scenario: Candidate pool is not shrunk by history
- **WHEN** the store holds more history facts near a question than the candidate limit, and one current relevant fact
- **THEN** the current fact is still among the candidates

#### Scenario: Correcting a fact twice
- **WHEN** a fact has been superseded and the user issues another correction on the same topic
- **THEN** the superseded fact is not offered as a candidate to correct
