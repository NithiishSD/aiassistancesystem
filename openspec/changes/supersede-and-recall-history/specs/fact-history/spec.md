# Spec Delta

## ADDED Requirements

### Requirement: A remembered fact supersedes the value it replaces
When the user states a fact about an attribute that holds one value at a time (for example name, college, semester, where they live), every current fact for that attribute with a different value SHALL be marked no longer valid, with the time of the change and a reference to the new fact, and kept as history. Which attributes hold one value SHALL be decided by a fixed table, not by a language model, and an attribute not in the table SHALL be treated as holding many values. The reply SHALL say what was replaced.

#### Scenario: The user moved
- **WHEN** the store holds `User's residence: Lakeview` and the user says "I moved to Hillcrest"
- **THEN** `User's residence: Hillcrest` is current, the Lakeview fact is kept as history pointing at it, and the reply names both

#### Scenario: Attribute that holds many values
- **WHEN** the store holds `User's project: a chess engine` and the user says they are also building a ray tracer
- **THEN** both project facts are current

#### Scenario: Same value again
- **WHEN** the store holds `User's Current Semester: 5th semester` and the user states `User's semester: 5th`
- **THEN** nothing new is stored and nothing is superseded

#### Scenario: New value cannot be stored
- **WHEN** the new fact is rejected on the way into the store
- **THEN** the old fact stays current

### Requirement: Existing conflicts can be listed
A read-only command SHALL list every single-valued attribute that currently holds more than one distinct value, so the owner can say which is right. It SHALL change nothing.

#### Scenario: Two places of residence stored before superseding existed
- **WHEN** `python memory.py --conflicts` is run
- **THEN** both residence facts are listed under one attribute and the store is unchanged

### Requirement: Questions about the user's past can use the history
When a question is about the user and refers to the past, question answering SHALL be given the matching facts that are no longer valid, each with the dates it was valid from and until, marked as no longer true. Other questions SHALL NOT be given history.

#### Scenario: Where the user lived before
- **WHEN** the residence fact was superseded and the user asks "where did I live before?"
- **THEN** the answer can name the earlier residence, and the current one is still given as current

#### Scenario: Present-tense question
- **WHEN** the user asks "where do I live?"
- **THEN** no superseded fact is included

#### Scenario: Past question that is not about the user
- **WHEN** the user asks "what was the capital of Prussia before 1871"
- **THEN** no stored history is included
