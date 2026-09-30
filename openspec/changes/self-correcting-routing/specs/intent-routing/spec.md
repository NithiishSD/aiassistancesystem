# Spec Delta

## ADDED Requirements

### Requirement: Corrections distinguish wrong facts from wrong routing
When the user corrects Zedek, it SHALL decide whether a stored fact or the routing of the previous request was wrong, and fix that.

#### Scenario: Wrong fact
- **WHEN** the previous turn stored "User's exam day: monday" and the user says "no, it's tuesday"
- **THEN** the stored fact is updated and no routing phrase changes

#### Scenario: Wrong routing
- **WHEN** "what apps do I have" was routed to open_application and the user says "I didn't ask you to open an app"
- **THEN** Zedek asks to confirm, and on yes the phrase is removed from open_application, the misroute is logged, the request is re-run as the intended capability through the tier gate, and after success the phrase is learned there

#### Scenario: Misread new request
- **WHEN** the diagnosis proposes a routing fix and the user answers no
- **THEN** nothing is un-learned or logged and the message is handled as a new request

#### Scenario: Unclear intent
- **WHEN** a routing mistake is diagnosed but the intended capability is unclear
- **THEN** Zedek asks what the user wanted and runs nothing

### Requirement: Confirmed questions stay questions
A request the user confirmed was just a question SHALL be answered as a question when repeated exactly.

#### Scenario: Repeat after a heal
- **WHEN** "what is vlc" was healed to general_question and is asked again
- **THEN** it is answered without routing to any action

### Requirement: Diagnosis never sees Zedek's replies
The model that diagnoses a correction SHALL receive only the user's own messages and capability names.

#### Scenario: Previous reply contained web content
- **WHEN** the previous turn was a web or research task
- **THEN** its reply text is not in the diagnosis prompt
