# memory-hygiene Specification

## Purpose
Defines which candidate facts are refused before they reach long-term memory, so that guesses and non-facts are never replayed to the model as ground truth.

## Requirements

### Requirement: Hedged or speculative facts are refused
A candidate fact whose value contains a hedge (presumably, likely, probably, possibly, maybe, perhaps, apparently, "not explicitly stated", implied, inferred, assumed) SHALL NOT be stored, and the refusal SHALL be logged with its reason. The check SHALL be deterministic.

#### Scenario: Parenthetical guess
- **WHEN** the candidate is `User's Current Studies: Data Structures (presumably a course at Northwind Institute)`
- **THEN** nothing is stored and the reason recorded is a hedged value

#### Scenario: Guessed value
- **WHEN** the candidate is `User's Operating System: likely a Linux-based system`
- **THEN** nothing is stored

#### Scenario: Hedge word inside another word
- **WHEN** the candidate is `User's hobby: Maybelline collecting` or `User's favourite word: unlikely`
- **THEN** the fact is stored

#### Scenario: Plain fact
- **WHEN** the candidate is `User's college: Northwind Institute`
- **THEN** the fact is stored unchanged

### Requirement: The cleanup sweep reports hedged facts
The store cleanup SHALL count existing hedged facts among the rows it would remove, and SHALL remove them only when explicitly told to apply.

#### Scenario: Dry run
- **WHEN** the cleanup runs without the apply option on a store holding a hedged fact
- **THEN** the fact is reported as removable and is still present
