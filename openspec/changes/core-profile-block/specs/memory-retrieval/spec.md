# Spec Delta

## MODIFIED Requirements

### Requirement: General question answering uses relevance-selected facts only
Long-term facts included as context when answering a general question SHALL be the relevance-selected facts, not an ungated top-N. When, and only when, the question refers to the user in the first person, a bounded core profile (the newest valid fact for each core attribute such as name, college, degree, semester, subjects and goals) SHALL be included as well, without repeating a fact already selected. The core profile SHALL NOT contain sensitive attributes (location or address, contact details, identifiers, account names); those are included only when relevance selection returns them.

#### Scenario: No irrelevant facts in the answer context
- **WHEN** a general question is answered and none of the stored facts are relevant
- **THEN** the answer context states that no relevant long-term facts were found and includes no stored facts

#### Scenario: Reworded question about the user
- **WHEN** the user asks "what do people call me" and relevance selection returns nothing
- **THEN** the answer context includes the stored name fact

#### Scenario: General knowledge question
- **WHEN** the user asks "what is a binary search tree"
- **THEN** no core profile fact is included and the stored facts are not read for that purpose

#### Scenario: Sensitive attribute
- **WHEN** the user asks a question about themselves and a location fact is stored but not relevance-selected
- **THEN** the location fact is not in the answer context

#### Scenario: Corrected fact
- **WHEN** a core fact has been superseded by a correction
- **THEN** only the current fact appears in the core profile

#### Scenario: Core profile is bounded
- **WHEN** hundreds of facts are stored
- **THEN** the core profile holds at most 12 facts and 1,200 characters
