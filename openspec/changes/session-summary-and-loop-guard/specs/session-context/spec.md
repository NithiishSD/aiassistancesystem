# Spec Delta

## Purpose

Defines what Zedek keeps of a conversation once turns leave its short-term buffer, so that a session keeps its thread without storing raw conversation.

## ADDED Requirements

### Requirement: Dropped turns leave a summary
When turns are dropped from the session buffer mid-session, a short summary of what they covered SHALL be kept for the rest of the session and given to general question answering along with the remaining turns. The summary SHALL be bounded in length, SHALL incorporate the previous summary, and SHALL be produced by the same local model call that extracts long-term facts.

#### Scenario: Follow-up after a trim
- **WHEN** turns comparing two editors have been dropped and the user asks "which one was faster"
- **THEN** the answering model is told what the earlier conversation was about

#### Scenario: Summary length
- **WHEN** the model returns a very long summary
- **THEN** at most 600 characters are kept

#### Scenario: Model fails
- **WHEN** the summarising call fails
- **THEN** the buffer is still trimmed and the previous summary is kept

### Requirement: Only departing turns are reviewed
The review that extracts long-term facts SHALL cover only the turns leaving the buffer, so that no turn is reviewed twice.

#### Scenario: Four turns kept
- **WHEN** the buffer is trimmed keeping the latest four turns
- **THEN** those four turns are not part of the review

### Requirement: The summary ends with the session
When the session ends, all remaining turns SHALL be reviewed for long-term facts and the session summary SHALL be discarded. The summary SHALL NOT be written to long-term memory.

#### Scenario: Quit
- **WHEN** the user ends the session
- **THEN** the buffer and the summary are both empty
