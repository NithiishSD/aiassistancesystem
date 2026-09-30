# Spec Delta

## ADDED Requirements

### Requirement: Voice-enabled capabilities are declared and restricted
A capability MAY be marked `voice: true` only when it is a Tier 0 system function run by the gated native handler. Any other capability marked voice-enabled SHALL stop startup. The set of voice-enabled capabilities SHALL be pinned by a test.

#### Scenario: Tier 1 capability marked voice-enabled
- **WHEN** `open_application` (Tier 1) is marked `voice: true`
- **THEN** loading fails and Zedek does not start

#### Scenario: Enabling another capability
- **WHEN** a further capability is marked `voice: true`
- **THEN** the pinned-set test fails until it is edited, so the change is reviewed
