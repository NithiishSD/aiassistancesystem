# web-ui Specification

## Purpose
Defines the local browser front end for Zedek: who may reach it, how a conversation runs in it, and how approvals for risky actions are asked and answered there.

## Requirements

### Requirement: The UI is reachable only from this machine by its owner
The UI SHALL listen on the loopback address only. Every API request SHALL carry an access token generated at start-up; the token SHALL be handed over in a part of the address that is not sent to the server. A request whose Host is not the server's own loopback address, or whose Origin is another site, SHALL be refused even with a valid token.

#### Scenario: No token
- **WHEN** an API request arrives without the token or with a wrong one
- **THEN** it is refused and no turn starts

#### Scenario: DNS rebinding
- **WHEN** a request arrives with Host `evil.example` and a valid token
- **THEN** it is refused

#### Scenario: Cross-site request
- **WHEN** a request arrives with Origin `https://evil.example`
- **THEN** it is refused and no turn starts

#### Scenario: Token not in the page
- **WHEN** the page is served
- **THEN** its content does not contain the token

### Requirement: A message is handled exactly like a typed one
A message sent from the UI SHALL go through the same request pipeline as a typed message, and its answer SHALL be shown as it is generated and completed with the final answer. Only one message SHALL be handled at a time.

#### Scenario: Streamed answer
- **WHEN** a message is sent and the answer is generated in pieces
- **THEN** the pieces are delivered in order, followed by the final answer

#### Scenario: Second message while busy
- **WHEN** a message is sent while another is being handled
- **THEN** it is refused with an explanation and the first continues

#### Scenario: Failure
- **WHEN** handling a message raises an error
- **THEN** the turn ends with a message saying so, and the next message is accepted

### Requirement: Approvals are asked and answered in the page
When an action needs the user's approval during a turn started from the UI, the request SHALL be shown in the page. Approval SHALL require an explicit approve action; declining MAY carry a reason. A request not answered within the time limit, or answered without the token, SHALL be a refusal.

#### Scenario: Approve
- **WHEN** the user approves the request in the page
- **THEN** the action proceeds

#### Scenario: Decline with a reason
- **WHEN** the user declines and types "keep the backup"
- **THEN** the action is refused and the reason is passed on as with a typed refusal

#### Scenario: No answer
- **WHEN** nobody answers within the time limit
- **THEN** the action is refused

### Requirement: Shown text is never executed
Text shown in the page, including model answers and content from web pages, SHALL be inserted as text and SHALL NOT be interpreted as markup or script. The page SHALL forbid being framed by another site.

#### Scenario: Answer containing markup
- **WHEN** an answer contains `<script>` or an image tag with an error handler
- **THEN** it is displayed as those characters and nothing runs
