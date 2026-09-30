# Spec Delta

## ADDED Requirements

### Requirement: The routing prompt has a fixed size budget
Only a capability's name and description SHALL be given to the routing model. A description longer than 700 characters, or names and descriptions totalling more than 8,000 characters, SHALL prevent Zedek from starting.

#### Scenario: Description too long
- **WHEN** a capability's description is 701 characters
- **THEN** loading raises and no capability table is built

#### Scenario: New capability exceeds the budget
- **WHEN** adding a capability takes the total past 8,000 characters
- **THEN** loading raises, so the routing prompt cannot grow unnoticed
