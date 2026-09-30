# Spec Delta

## ADDED Requirements

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
