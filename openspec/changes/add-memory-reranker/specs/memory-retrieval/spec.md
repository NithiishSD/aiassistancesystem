# Spec Delta

## Purpose

Defines how Zedek selects stored personal facts to give the language model when answering a question: which facts are chosen, how many, and what happens when nothing is relevant or the ranking model is unavailable.

## ADDED Requirements

### Requirement: Facts are selected by relevance to the question
When facts are retrieved to answer a question, the system SHALL rank candidate facts by their relevance to the question text and return at most the requested number, most relevant first. The candidate pool considered SHALL be larger than the number returned.

#### Scenario: Most relevant fact ranked first
- **WHEN** the store contains `User's college: PSG College of Technology` and `User's Current Semester: 5th semester`, and the question is "what college do I study at"
- **THEN** the college fact is returned first

#### Scenario: Result count is bounded
- **WHEN** relevant facts are requested with a limit of 3 and more than 3 candidates are relevant
- **THEN** exactly 3 facts are returned

### Requirement: Irrelevant facts are excluded
The system SHALL exclude candidate facts whose relevance to the question falls below a relevance threshold, even if fewer than the requested number remain. It SHALL return an empty result rather than filling the limit with irrelevant facts.

#### Scenario: Unrelated question retrieves nothing personal
- **WHEN** the question is a general-knowledge query unrelated to any stored fact (e.g. "what is a binary search tree") and the store contains only personal facts about name, college, and location
- **THEN** no facts are returned

#### Scenario: Correct fact is not dropped because of phrasing
- **WHEN** a question is phrased such that the correct fact's embedding distance exceeds the previous 1.0 cutoff (observed: 1.06 for "what college do I study at")
- **THEN** the correct fact is still returned

### Requirement: General question answering uses relevance-selected facts only
Long-term facts included as context when answering a general question SHALL be the relevance-selected facts, not an ungated top-N.

#### Scenario: No irrelevant facts in the answer context
- **WHEN** a general question is answered and none of the stored facts are relevant
- **THEN** the answer context states that no relevant long-term facts were found and includes no stored facts

### Requirement: Retrieval degrades instead of failing
If relevance ranking is unavailable (e.g. the ranking model cannot be loaded), retrieval SHALL fall back to embedding-similarity ranking, SHALL record that it is operating in a degraded mode, and SHALL NOT raise an error to the caller.

#### Scenario: Ranking model missing
- **WHEN** the ranking model files are absent
- **THEN** facts are still returned, ordered by embedding similarity, and a degraded-mode log event is recorded

### Requirement: Retrieval works offline
Relevance ranking SHALL run entirely on the local machine and SHALL NOT require network access at query time.

#### Scenario: No network
- **WHEN** network access is unavailable and the ranking model has been installed locally
- **THEN** relevance-ranked retrieval succeeds

### Requirement: Ranking stays within a latency budget
Relevance ranking of up to 20 candidate facts SHALL add no more than 300 ms at the median on the owner's laptop CPU, measured after the model is loaded.

#### Scenario: Benchmark within budget
- **WHEN** 20 candidate facts are ranked for a question on the laptop CPU with the model already loaded
- **THEN** the median added time across repeated runs is at most 300 ms
