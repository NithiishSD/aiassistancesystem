# memory-retrieval Specification

## Purpose
Defines how Zedek selects stored personal facts to give the language model when answering a question: which facts are chosen, how many, and what happens when nothing is relevant or the ranking model is unavailable.

## Requirements

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

### Requirement: Keyword matches reach the relevance ranking
Candidate facts for relevance ranking SHALL include facts that share distinctive words with the question, not only those nearest by embedding. The same relevance gate SHALL apply to every candidate.

#### Scenario: Exact code in a large store
- **WHEN** the store holds hundreds of similar facts and the user asks "which course is 23XT51"
- **THEN** the fact containing "23XT51" is among the candidates, and it is returned if it passes the relevance gate

#### Scenario: Keyword match that is not relevant
- **WHEN** a general-knowledge question shares a word with a stored fact
- **THEN** that fact is still excluded unless it passes the relevance gate

### Requirement: Keyword search follows the store
The keyword index SHALL reflect facts added or deleted in a domain before that domain is next searched.

#### Scenario: New fact
- **WHEN** a fact is stored and a question matching its words is asked next
- **THEN** the new fact is among the keyword candidates

#### Scenario: Deleted fact
- **WHEN** a fact is deleted
- **THEN** it is no longer among the keyword candidates

### Requirement: Retrieval without keyword search
If keyword search is unavailable, retrieval SHALL continue with embedding candidates only, and log that it did so.

#### Scenario: Keyword library missing
- **WHEN** the keyword index cannot be built
- **THEN** relevant facts are still returned from embedding candidates, and a degradation is logged

### Requirement: Facts that are no longer valid are not retrieved
Facts marked as no longer valid SHALL be excluded from embedding candidates, keyword candidates, relevance-ranked results, and the candidates offered when the user corrects a fact. Excluding them SHALL NOT reduce the number of current candidates considered.

#### Scenario: Superseded fact is not used to answer
- **WHEN** `User's college: A` has been superseded by `User's college: B` and the question is "what college do I study at"
- **THEN** only the fact for B is returned

#### Scenario: Keyword match on a superseded fact
- **WHEN** a question shares a distinctive word with a fact that is no longer valid
- **THEN** that fact is not among the keyword candidates

#### Scenario: Retraction takes effect on the next question
- **WHEN** a fact is marked no longer valid and a matching question is asked next
- **THEN** the fact is not returned, by either embedding or keyword search

#### Scenario: Candidate pool is not shrunk by history
- **WHEN** the store holds more history facts near a question than the candidate limit, and one current relevant fact
- **THEN** the current fact is still among the candidates

#### Scenario: Correcting a fact twice
- **WHEN** a fact has been superseded and the user issues another correction on the same topic
- **THEN** the superseded fact is not offered as a candidate to correct
