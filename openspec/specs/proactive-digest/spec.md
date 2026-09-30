# proactive-digest Specification

## Purpose
Defines what Zedek tells the user without being asked: the daily digest, how scheduled jobs run and are delivered, and the safety limits on work that runs with nobody present to approve it.

## Requirements

### Requirement: The digest reports only what the data shows
The daily digest SHALL be built from the practice tracker and stored facts without a language model. It SHALL list topics not practised for seven days or more, the weakest topics with the reason each was flagged, and the current practice streak. Every figure SHALL come from the tracker.

#### Scenario: Stale and weak topics
- **WHEN** graphs was last practised 12 days ago with 40% accuracy
- **THEN** the digest names graphs, says it was not practised in 12 days, and gives the accuracy

#### Scenario: No practice data
- **WHEN** no practice has been logged
- **THEN** the digest contains no practice section and invents no topics

### Requirement: The digest surfaces stored commitments with their age
The digest SHALL include stored facts about upcoming commitments (exams, deadlines, interviews, assignments) that are still valid, each with how long ago it was noted, because a stored "next week" does not say which week.

#### Scenario: Commitment noted earlier
- **WHEN** a valid fact `User's data structures exam: next week` was stored 5 days ago
- **THEN** the digest lists it and says it was noted 5 days ago

#### Scenario: Superseded commitment
- **WHEN** a commitment fact has been marked no longer valid
- **THEN** the digest does not list it

### Requirement: An empty digest is not delivered
When there is nothing to report, no digest SHALL be delivered.

#### Scenario: Nothing to say
- **WHEN** there is no practice data and no commitment facts
- **THEN** the scheduled digest adds nothing to the inbox

### Requirement: Jobs run once per scheduled day, including after downtime
A job scheduled `daily HH:MM` SHALL run at most once per calendar day, at the first opportunity at or after that time. A job whose time passed while the machine was off SHALL run once at the next opportunity, not once per missed day. A job limited to named weekdays SHALL NOT run on other days.

#### Scenario: Due
- **WHEN** a job is scheduled daily at 08:00, has not run today, and it is 09:30
- **THEN** the job runs and is recorded as run today

#### Scenario: Already run
- **WHEN** the same job is checked again at 10:00 the same day
- **THEN** it does not run

#### Scenario: Before its time
- **WHEN** it is 07:00
- **THEN** the job does not run

#### Scenario: Several days missed
- **WHEN** the job last ran four days ago
- **THEN** it runs once

#### Scenario: Invalid schedule
- **WHEN** a job's schedule cannot be understood
- **THEN** that job is skipped with a logged reason and other jobs still run

### Requirement: Scheduled work cannot approve risky actions
A scheduled prompt SHALL run through the same request pipeline and tier gate as a typed request, with a confirmation channel that refuses every request for approval.

#### Scenario: Scheduled prompt needing confirmation
- **WHEN** a scheduled prompt leads to an action that requires confirmation
- **THEN** the action is not executed and the refusal appears in the job's result

#### Scenario: A failing job
- **WHEN** a job raises an error
- **THEN** the error is recorded in the inbox entry, the job is marked as run for the day, and other jobs still run

### Requirement: Results are delivered through an inbox
Job results SHALL be appended to a local inbox and shown to the user as unread items at the next interactive session, after which they are marked read. The user SHALL be able to re-read the inbox and to request the digest on demand. A desktop notification, when sent, SHALL NOT contain the result text.

#### Scenario: Unread at startup
- **WHEN** an interactive session starts and the inbox holds an unread digest
- **THEN** the digest is shown and marked read

#### Scenario: Nothing unread
- **WHEN** a session starts with no unread items
- **THEN** nothing is shown
