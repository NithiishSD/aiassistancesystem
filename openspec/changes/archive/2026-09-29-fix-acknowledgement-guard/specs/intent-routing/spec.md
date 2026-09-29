# Spec Delta

## ADDED Requirements

### Requirement: Acknowledgements are recognized by whole words across the whole message
A message SHALL be treated as an acknowledgement (answered conversationally, never routed to an action) only if every word in it belongs to the acknowledgement-and-filler vocabulary. Matching SHALL be by whole words, never by substrings inside other words. A message containing any other word SHALL be routed normally, even if it also contains acknowledgement words. This SHALL be decided by a single implementation used by every entry point.

#### Scenario: Pure acknowledgement short-circuits
- **WHEN** the message is "okay thank you" or "thanks a lot bro" or "ok got it"
- **THEN** it is treated as an acknowledgement and no action is routed

#### Scenario: Acknowledgement word inside another word does not match
- **WHEN** the message is "type hello world into the textbox on example.com", "disk capacity and free space please", "port this javascript to typescript", or "open facebook"
- **THEN** it is not treated as an acknowledgement and is routed normally

#### Scenario: Request that also says thanks is routed
- **WHEN** the message is "thanks, now find my resume file"
- **THEN** it is not treated as an acknowledgement and is routed normally

#### Scenario: Correction containing "got it" is routed
- **WHEN** the message is "you got it wrong, my exam is on tuesday"
- **THEN** it is not treated as an acknowledgement and is routed normally

#### Scenario: Same answer from every entry point
- **WHEN** the same message is checked by the request router and by the intent classifier
- **THEN** both reach the same acknowledgement decision
