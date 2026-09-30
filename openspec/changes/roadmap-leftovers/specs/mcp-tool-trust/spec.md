# Spec Delta

## ADDED Requirements

### Requirement: A tool runs only when the request was matched to it
A request SHALL be handed to a connected tool only when the tool's own name appears in it, a whole-phrase shortcut for one of the bundled tools matches, or the tool-picking model names that tool. A keyword found inside another word SHALL NOT match. When no tool is named, no tool SHALL run and the reply SHALL say that none matched.

#### Scenario: Keyword inside another word
- **WHEN** the user asks "will it snow in shimla" or "today's headlines please"
- **THEN** the clock tool is not chosen and the tool-picking model is asked

#### Scenario: The model names no tool
- **WHEN** the tool-picking model fails or its answer names no registered tool
- **THEN** no tool runs and the reply is "No matching MCP tool found for this request."
