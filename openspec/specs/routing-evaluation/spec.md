# routing-evaluation Specification

## Purpose
Defines how Zedek measures and protects intent-routing quality: a labelled golden set, per-class metrics, a regression gate against a committed baseline, and a list of requests that must never leave the local classifier.

## Requirements

### Requirement: Labelled golden set with a held-out slice
The project SHALL maintain a golden set of labelled requests covering every routable intent plus general questions, with at least 20 requests per class. Each request SHALL be assigned to either a development slice or a held-out test slice. No request SHALL duplicate a phrase the classifier was built from.

#### Scenario: Coverage check
- **WHEN** the golden set is loaded
- **THEN** every intent the classifier can route to, plus general questions, has at least 20 labelled requests

#### Scenario: No leakage from classifier phrases
- **WHEN** the golden set is compared against the classifier's built-in phrases (case- and whitespace-insensitive)
- **THEN** no golden request matches a built-in phrase

### Requirement: Per-class metrics and confusion matrix
Evaluating the golden set SHALL produce per-class precision, recall, and F1, and a confusion matrix over all classes plus an explicit "escalated to the language model" outcome. General questions SHALL count as correct when the local classifier escalates them rather than claiming them for an intent.

#### Scenario: Report produced
- **WHEN** the evaluation runs
- **THEN** it outputs per-class precision, recall, and F1, and a confusion matrix whose columns include the escalation outcome

#### Scenario: General question grabbed by an intent is an error
- **WHEN** a request labelled as a general question is resolved locally to some intent
- **THEN** it is counted as a misclassification, not as a correct result

### Requirement: The gated run is deterministic and offline
The evaluation that gates changes SHALL use a reproducible router (built-in phrases only), SHALL NOT call any language model or network service, and SHALL NOT depend on or modify runtime-learned phrases or the routing cache seen by normal operation.

#### Scenario: No network during the gated run
- **WHEN** the gated evaluation runs
- **THEN** no language-model call is made, and requests the local layer cannot resolve are recorded as escalated

#### Scenario: Runtime state untouched
- **WHEN** the gated evaluation finishes
- **THEN** the runtime-learned phrase file and the normal-operation router are unchanged

### Requirement: Regression gate against a committed baseline
The project SHALL keep a committed baseline of per-class metrics on the test slice. The test suite SHALL fail if any class's precision or recall on the test slice falls more than 2 percentage points below its baseline. Updating the baseline SHALL be an explicit, deliberate action.

#### Scenario: Regression detected
- **WHEN** a change lowers any class's test-slice recall from 0.90 in the baseline to 0.85
- **THEN** the test suite fails and names the class and metric

#### Scenario: Improvement does not fail
- **WHEN** a change raises a class's precision above its baseline
- **THEN** the gate passes

### Requirement: Requests that must stay local
The project SHALL maintain a list of requests, each with an expected intent, that the local classifier must resolve to that intent without escalating to the language model. The test suite SHALL fail if any of them escalates or resolves to a different intent.

#### Scenario: Local command starts escalating
- **WHEN** a must-stay-local request is escalated to the language model
- **THEN** the test suite fails and names the request

### Requirement: Optional end-to-end measurement
The project SHALL provide a separately invoked evaluation that includes the language-model fallback and reports how many escalated requests it resolves correctly. This evaluation SHALL NOT run as part of the test suite.

#### Scenario: Not part of the test suite
- **WHEN** the full test suite runs
- **THEN** no language-model call is made by any routing evaluation

### Requirement: Corrected misroutes can be promoted into the golden set
A command SHALL list the logged corrected misroutes that have a known intended intent and are neither in the golden set nor a router phrase, and SHALL add them only when asked, only to the DEV slice, leaving out any row the owner names. Listing SHALL change nothing, and the command SHALL remind the owner that the golden set is public.

#### Scenario: Listing
- **WHEN** the command is run without `--apply`
- **THEN** the candidate rows are printed and the golden set is unchanged

#### Scenario: Applying with a row left out
- **WHEN** the command is run with `--apply --skip 1`
- **THEN** every listed row except the first is appended to the DEV slice and the held-out slice is unchanged
