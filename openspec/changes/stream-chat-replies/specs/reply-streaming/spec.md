# Spec Delta

## Purpose

Defines how chat replies are delivered incrementally and what happens when a provider fails part-way through.

## ADDED Requirements

### Requirement: General answers are delivered as they are generated
When a caller supplies a stream sink for a plain-text chat request, the provider chain SHALL deliver the answer's visible text to the sink incrementally, in order, and SHALL still return the complete answer.

#### Scenario: Streamed general question
- **WHEN** a general question is answered with a sink attached and the provider sends the answer in several chunks
- **THEN** the sink receives the chunks in order, their concatenation equals the returned answer, and the returned result has the same fields as a non-streamed call

#### Scenario: Structured request with a sink
- **WHEN** a JSON or schema-constrained request is made
- **THEN** nothing is streamed

### Requirement: Reasoning traces are never shown
Text inside `<think>…</think>` SHALL NOT reach the sink, including when a tag is split across chunks.

#### Scenario: Tag split across chunks
- **WHEN** a provider streams `<thi`, `nk>plan</th`, `ink>Answer`
- **THEN** the sink receives only `Answer`

### Requirement: Fallback survives a mid-stream failure
If a provider fails after text was streamed, the sink SHALL be told to restart before the next provider's text arrives; a provider that fails before any text SHALL fall through without a restart.

#### Scenario: Failure after partial output
- **WHEN** the first provider streams some text and then its connection drops
- **THEN** the sink is restarted once and then receives the next provider's full answer, which is also the returned answer

### Requirement: The sink cannot break a request
An exception raised by the sink SHALL NOT fail the request or trigger provider fallback.

#### Scenario: Broken terminal
- **WHEN** the sink raises on every delta
- **THEN** the request completes with the full answer from the same provider

### Requirement: Multi-step answers are not streamed
Requests decomposed into sub-tasks SHALL NOT stream sub-answers.

#### Scenario: Two-part request
- **WHEN** a request is decomposed into two sub-tasks and a sink is supplied
- **THEN** the sink receives nothing and the combined step list is returned
