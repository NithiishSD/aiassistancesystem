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

### Requirement: Relative dates in a stored fact are anchored to the day it was stored
When a fact is stored, the first relative time expression in it SHALL be kept as written and followed by what it meant on that day: a date for today, tomorrow, yesterday, a named coming weekday, and "in N days"; the week, month or year for "this / next / last week, month, year" and "N weeks, months, years ago". An expression with no single reading (such as "next Friday" or "next semester") SHALL be followed by the day it was said instead of a guessed date. A fact with no relative expression, or one already anchored, SHALL be stored unchanged. The anchoring SHALL be rule-based, with no model call.

#### Scenario: Exam next week
- **WHEN** "User's exam: next week" is stored on Wednesday 30 September 2026
- **THEN** the stored fact is "User's exam: next week (week of 5 October 2026)"

#### Scenario: Ambiguous weekday
- **WHEN** "User's presentation: next Friday" is stored on Wednesday 30 September 2026
- **THEN** the stored fact is "User's presentation: next Friday (said on Wednesday 30 September 2026)"

#### Scenario: No relative date
- **WHEN** "User's weekly meeting: every Monday" is stored
- **THEN** it is stored unchanged
