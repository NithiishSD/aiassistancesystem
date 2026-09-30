# Spec Delta

## MODIFIED Requirements

### Requirement: Spoken requests remain questions only
A spoken request SHALL be answered through question answering only and SHALL NOT reach action execution, with or without a speaker, unless it is a recognized spoken command for a voice-enabled capability (see `voice-commands`).

#### Scenario: Spoken action request
- **WHEN** the user says "zedek open the calculator"
- **THEN** no application is opened

#### Scenario: Spoken read-only command
- **WHEN** the user says "zedek is my disk full"
- **THEN** the free-space report is spoken and nothing else runs
