# Spec Delta

## ADDED Requirements

### Requirement: Research answers contain only sourced links
A research answer SHALL contain only links the user provided or that appear in a trusted (non-web-page) source; any other link SHALL be replaced by a non-clickable marker and noted in the report.

#### Scenario: Link planted in a fetched page
- **WHEN** a fetched page contains `https://evil.example/steal` and the answer repeats it
- **THEN** the answer shows `[unverified link removed: evil[.]example]` and a note says one link was removed

#### Scenario: Paper link from arXiv
- **WHEN** the answer cites an arXiv result's URL
- **THEN** the link is kept
