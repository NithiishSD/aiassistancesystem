# Spec Delta

## Purpose

Defines how prompts are laid out so provider prefix caches can hit, and which LLM results may be reused without a provider call.

## ADDED Requirements

### Requirement: Instructions come first and are byte-identical
Orchestrator prompts SHALL send their instructions as a leading system message whose content does not depend on the request, and SHALL place user text, retrieved facts, and other per-request content after it.

#### Scenario: Two different fact statements
- **WHEN** two different statements are canonicalized
- **THEN** both requests start with the same system message, byte for byte, and each statement appears only in the final user message

#### Scenario: General question with history
- **WHEN** a general question is answered with session history and retrieved facts
- **THEN** the messages are the fixed system message, then the history, then one user message containing the facts and the question

### Requirement: Deterministic structured results may be reused
A structured-output call that opts into caching SHALL return a previously validated result for byte-identical input without calling any provider, and SHALL report its source as `cache`.

#### Scenario: Repeated statement
- **WHEN** the same statement is canonicalized twice
- **THEN** the second call makes no provider request and returns the same facts

### Requirement: Only trustworthy results are stored
The cache SHALL store only results validated against the schema and produced by a cloud provider; results from the local fallback and failed extractions SHALL NOT be stored.

#### Scenario: Local fallback answer
- **WHEN** every cloud provider fails and the local model answers
- **THEN** the result is returned but not cached

### Requirement: The cache never fails a request
Any cache error SHALL be treated as a miss, and entries older than the time-to-live SHALL NOT be returned.

#### Scenario: Corrupt cache file
- **WHEN** the cache file cannot be read as a database
- **THEN** the request proceeds through the provider chain and succeeds
