# voice-replies Specification

## Purpose
Defines how answers to spoken questions are spoken aloud: sentence by sentence as the answer is generated, locally, without the assistant listening to itself.

## Requirements

### Requirement: Answers are spoken sentence by sentence as they are generated
When a speaker is attached, each sentence of a streamed answer SHALL be sent to speech as soon as the sentence is complete, without waiting for the rest of the answer. Sentences SHALL be spoken in order.

#### Scenario: First sentence is spoken before the answer is complete
- **WHEN** the first sentence of a three-sentence answer has been generated
- **THEN** it has been sent to speech while the rest is still being generated

#### Scenario: Numbers, domains and abbreviations
- **WHEN** the answer contains "3.5 metres", "example.com" or "e.g."
- **THEN** no sentence break is made inside them

#### Scenario: Answer that was not streamed
- **WHEN** the answer arrives only as a whole
- **THEN** the whole answer is spoken once

### Requirement: Only speakable text is spoken
Markdown symbols and list markers SHALL be removed and a URL SHALL be spoken as "a link".

#### Scenario: Formatted answer
- **WHEN** the answer is "**Heaps** are `trees`. See https://example.com/a for details."
- **THEN** what is spoken is "Heaps are trees." and "See a link for details."

### Requirement: Speech can be cut short
When a provider fails mid-answer and another starts over, when the user says a sleep phrase, or when answering fails, everything not yet spoken SHALL be dropped.

#### Scenario: Provider restarts
- **WHEN** half a sentence has been generated and the provider fails
- **THEN** that half is not spoken and the new answer is spoken from its start

#### Scenario: Sleep phrase
- **WHEN** the user says "stop listening" while an answer is queued
- **THEN** the queued speech is dropped

### Requirement: The assistant does not listen to itself
While speech is queued or playing, microphone input SHALL be discarded.

#### Scenario: Speaking during the activation window
- **WHEN** an answer is being spoken and the activation window is open
- **THEN** the spoken answer is not processed as a new question

### Requirement: Speech is local and cannot run commands
Text-to-speech SHALL run on the local machine, and the text to speak SHALL be passed so that it cannot be interpreted as an option or a command. A speech failure SHALL NOT stop the listener.

#### Scenario: Answer starting with a dash
- **WHEN** the text to speak is "--cancel everything"
- **THEN** it is spoken as text

#### Scenario: No audio device
- **WHEN** the speech backend fails for one sentence
- **THEN** later sentences are still attempted and the listener keeps running

### Requirement: Spoken requests remain questions only
A spoken request SHALL be answered through question answering only and SHALL NOT reach action execution, with or without a speaker.

#### Scenario: Spoken action request
- **WHEN** the user says "zedek open the calculator"
- **THEN** no application is opened
