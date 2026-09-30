# research-sources Specification

## Purpose
Defines how material gathered by the research agent is reduced to the evidence given to the answering model.

## Requirements

### Requirement: Evidence is selected by relevance from anywhere in a document
The research agent SHALL select the passages most relevant to the question from anywhere in each gathered document, rather than keeping each document's opening text.

#### Scenario: Answer deep in a page
- **WHEN** a fetched page states the answer after its first 4,000 characters, and the opening text is unrelated
- **THEN** the passage containing the answer is part of the evidence given to the answering model

### Requirement: Evidence size is bounded
The tool-sourced evidence given to the answering model SHALL NOT exceed the passage budget, however many or however long the gathered documents are.

#### Scenario: Several long pages
- **WHEN** six documents of 8,000 characters each are gathered
- **THEN** the tool-sourced evidence is at most the passage budget

### Requirement: Citations still identify documents
Each citation label SHALL refer to one gathered document, and its selected passages SHALL appear in their original order. Documents that contribute no passage SHALL NOT receive a label.

#### Scenario: Two passages from one page
- **WHEN** two passages are selected from the same page
- **THEN** they appear under one label, in page order

#### Scenario: Irrelevant document
- **WHEN** no passage of a document is selected
- **THEN** the document is not listed among the sources

### Requirement: Remembered facts are always kept
Facts from the user's memory that passed memory's relevance gate SHALL be kept in full and listed first.

#### Scenario: Memory plus pages
- **WHEN** one remembered fact and several pages are gathered
- **THEN** the fact is the first source, unchanged

### Requirement: Selection degrades to the previous behavior
If the relevance model is unavailable, each document SHALL be cut to its opening portion as before, and the degradation SHALL be logged.

#### Scenario: Reranker missing
- **WHEN** the relevance model cannot be loaded
- **THEN** each document contributes its first 4,000 characters, and a degradation is logged

### Requirement: Encyclopedia lookup accepts keyword queries
An encyclopedia lookup SHALL find articles for keyword-style queries whose words appear in an article but not at the start of its title.

#### Scenario: Keyword query
- **WHEN** the lookup query is "transformer attention"
- **THEN** articles about the transformer architecture are returned, not "no articles found"

### Requirement: Research answers contain only sourced links
A research answer SHALL contain only links the user provided or that appear in a trusted (non-web-page) source; any other link SHALL be replaced by a non-clickable marker and noted in the report.

#### Scenario: Link planted in a fetched page
- **WHEN** a fetched page contains `https://evil.example/steal` and the answer repeats it
- **THEN** the answer shows `[unverified link removed: evil[.]example]` and a note says one link was removed

#### Scenario: Paper link from arXiv
- **WHEN** the answer cites an arXiv result's URL
- **THEN** the link is kept

### Requirement: Fetches stay on public addresses across redirects
Every URL in a redirect chain SHALL pass the SSRF check before it is requested, and only globally routable unicast addresses SHALL pass.

#### Scenario: Redirect to cloud metadata
- **WHEN** a public URL redirects to `http://169.254.169.254/latest/meta-data/`
- **THEN** the fetch returns a blocked message and the internal address is never requested

#### Scenario: Mapped loopback
- **WHEN** a host resolves to `::ffff:127.0.0.1`
- **THEN** it is blocked
