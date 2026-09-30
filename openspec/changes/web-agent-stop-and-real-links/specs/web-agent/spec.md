# Spec Delta

## ADDED Requirements

### Requirement: Browsing stops when the goal is satisfied
When a successful step's page reading reports that the goal is satisfied, the browsing loop SHALL end without asking the action-selecting model for another action and without requesting another confirmation.

#### Scenario: First page answers the goal
- **WHEN** the first page read reports the goal satisfied
- **THEN** exactly one action is taken and the action-selecting model is consulted once

#### Scenario: Goal not yet satisfied
- **WHEN** a successful step reports the goal not satisfied
- **THEN** the loop continues as before, within the step limit

### Requirement: Page-proposed URLs must be links present on the page
A URL proposed from page content SHALL be offered as a target only if it is one of the links actually present on that page, in addition to the existing scheme and user-domain checks. A proposed URL that is not a link on the page SHALL be dropped and the drop SHALL be logged.

#### Scenario: Invented link
- **WHEN** the page reading proposes `https://example.com/learn-more` and the page's links do not include it
- **THEN** no such target is offered

#### Scenario: Real link
- **WHEN** the page reading proposes a URL that is among the page's links and on the user's domain
- **THEN** it is offered as a page-derived target

#### Scenario: Page reports no links
- **WHEN** the browser result carries no link list
- **THEN** no page-proposed URL is offered

#### Scenario: Link list never reaches the action-selecting model
- **WHEN** a page's link list contains an instruction-like string or an off-domain URL
- **THEN** neither appears in any prompt given to the action-selecting model
