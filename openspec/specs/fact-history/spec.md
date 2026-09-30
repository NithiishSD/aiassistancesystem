# fact-history Specification

## Purpose
Defines how a stored fact stops being current: what happens when the user corrects it, says it is no longer true, or asks for it to be erased, and what record of the change is kept.

## Requirements

### Requirement: Facts record when they became valid
Every fact stored SHALL record the time it became valid. Facts stored before this requirement existed SHALL be treated as valid, with no migration.

#### Scenario: New fact
- **WHEN** a fact is stored
- **THEN** it carries the time it became valid and is current

#### Scenario: Fact stored before this change
- **WHEN** a fact has no validity information at all
- **THEN** it is treated as current

### Requirement: A corrected fact is superseded, not deleted
When the user corrects a stored fact with a new value, the system SHALL store the new fact, then mark the old fact as no longer valid with the time of the change and a reference to the fact that replaced it. The old fact SHALL remain in the store as history.

#### Scenario: Correction with a new value
- **WHEN** the store holds `User's college: A` and the user corrects it to `User's college: B`
- **THEN** `User's college: B` is current, and `User's college: A` is kept, marked no longer valid, and references the new fact

#### Scenario: Replacement is rejected
- **WHEN** the corrected value is rejected on the way into the store (for example it is not a usable fact)
- **THEN** the old fact stays current, and the user is told that nothing was changed

### Requirement: A retracted fact is kept as history
When the user says a stored fact is no longer true without giving a new value, the system SHALL mark the fact as no longer valid with the time of the change and SHALL keep it as history.

#### Scenario: Fact is outdated
- **WHEN** the user says "that's outdated" about a stored fact
- **THEN** the fact is marked no longer valid and is still present in the store

#### Scenario: Delete request for a real fact
- **WHEN** the user says "delete that from memory" about a usable stored fact
- **THEN** the fact is marked no longer valid, is no longer used to answer, and is still present as history

### Requirement: Facts that are not useful are deleted, not kept as history
When a fact being corrected or retracted is not a usable fact under the store's own hygiene rules, or when its replacement says the same thing apart from case and punctuation, the system SHALL permanently delete it instead of keeping it as history. This decision SHALL be deterministic and SHALL NOT be made by a language model.

#### Scenario: Junk fact retracted
- **WHEN** the fact the user retracts is one the hygiene rules reject (for example `User's college: not stated`)
- **THEN** the fact no longer exists in the store in any form

#### Scenario: Replacement is the same fact
- **WHEN** `User's name: alex` is corrected to `User's Name: Alex.`
- **THEN** only the new fact exists; no history row is kept

### Requirement: The reply states what happened to the fact
The reply to a correction SHALL distinguish between a fact that was updated with its previous value kept as history, a fact marked no longer true and kept as history, and a fact permanently deleted.

#### Scenario: Retraction reply
- **WHEN** a fact is marked no longer valid
- **THEN** the reply says it will no longer be used and is kept as history, and does not claim it was deleted

#### Scenario: Deletion reply
- **WHEN** a fact is permanently deleted
- **THEN** the reply says it was permanently deleted

### Requirement: History can be inspected
The owner SHALL be able to list facts matching a query including those no longer valid, each shown with when it became valid, when it stopped being valid, and whether it was replaced.

#### Scenario: Viewing a superseded fact
- **WHEN** the owner asks for the history of "college" after a correction from A to B
- **THEN** both facts are listed, A marked as superseded with its dates and B as current

### Requirement: History can be purged
The owner SHALL be able to permanently remove all facts that are no longer valid in a domain. The purge SHALL report what it would remove without removing anything unless explicitly told to apply.

#### Scenario: Dry run
- **WHEN** the purge is run without the apply option
- **THEN** it reports the number of history facts and deletes nothing

#### Scenario: Applied
- **WHEN** the purge is applied
- **THEN** no facts marked no longer valid remain, and every current fact is untouched

### Requirement: Cleanup does not treat history as a duplicate
The store cleanup SHALL NOT delete a current fact because a fact with the same text exists as history, and SHALL NOT alter history facts.

#### Scenario: Fact reinstated after being retracted
- **WHEN** `User's city: X` was retracted and later stored again, and the cleanup is applied
- **THEN** the current `User's city: X` is kept

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
