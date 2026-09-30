# intent-routing Specification

## Purpose
Defines observable guarantees of Zedek's intent classification: what the reported confidence score means, and that measurement-related changes do not alter which intent a request is routed to.

## Requirements

### Requirement: Reported score reflects actual similarity
When a request is resolved by the local (embedding) classification layer, the reported score SHALL be the similarity between the request and the matched intent, not a fixed constant. Different requests matched to the same intent SHALL be able to report different scores.

#### Scenario: Two matches report their own scores
- **WHEN** two different requests are both resolved locally to the same intent
- **THEN** each reported score is that request's own similarity value, and the values are not required to be equal

#### Scenario: Local match never reports below the match threshold
- **WHEN** a request is resolved by the local layer
- **THEN** its reported score is at least the local match threshold

### Requirement: Score reporting does not change routing
Changing how the local-layer score is reported SHALL NOT change which intent any request is routed to, nor whether it escalates to the language-model fallback.

#### Scenario: Same decisions before and after
- **WHEN** the golden evaluation set is classified before and after the score-reporting change
- **THEN** every utterance receives the same intent or escalation outcome in both runs

### Requirement: Classification can run on a reproducible router
It SHALL be possible to build the local classification layer from only its built-in phrases, excluding phrases learned at runtime and phrases registered from external tool descriptions, without affecting the router used in normal operation.

#### Scenario: Learned phrases excluded on request
- **WHEN** a reproducible router is built on a machine that has runtime-learned phrases
- **THEN** those phrases play no part in its classifications, and the normal-operation router still includes them

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

### Requirement: Explanation-style questions are not turned into actions locally
When the local classification layer's best match for a request is an explanation-style or concept question (e.g. "what is ram used for", "how are threads different from processes"), the request SHALL be treated as a general question. The local layer SHALL NOT route it to an action. Recognizing a request as a general question SHALL NOT cause it to be answered without the same fallback it receives today.

#### Scenario: Concept question sharing an action's vocabulary
- **WHEN** the request is "explain what ram is"
- **THEN** the local layer does not route it to the memory-process action

#### Scenario: General question keeps today's fallback
- **WHEN** the local layer recognizes a request as a general question
- **THEN** it follows the same path as an unrecognized request (the language-model fallback), not a direct chat answer and not an action

### Requirement: Clarifying questions only for bare ambiguous topics
A clarifying question SHALL pre-empt routing only when an ambiguous term is the only topic of the message.

#### Scenario: Request with context
- **WHEN** the user says "port this javascript to typescript"
- **THEN** no clarifying question is asked and the request is routed

#### Scenario: Bare topic
- **WHEN** the user says "tell me about python"
- **THEN** Zedek asks which meaning of "python" is meant

### Requirement: The routing decision is typed
The decision passed from routing to execution SHALL be a typed object carrying the user's input; an unknown field SHALL be an error, not a silent default.

#### Scenario: Misspelled field
- **WHEN** a caller passes a decision with the key `orignal_input`
- **THEN** execution raises instead of running with an empty input

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
