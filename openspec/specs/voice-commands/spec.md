# voice-commands Specification

## Purpose
Defines how a spoken request is recognized as a command or a question by two local recognizers, and what a spoken command is allowed to do.

## Requirements

### Requirement: Two local recognizers hear every utterance
The listener SHALL pass the same audio to a free-form recognizer and to a recognizer restricted to a grammar made of the wake word, the sleep phrases, the command phrases, decoy words that sound like the wake word, and a marker for any other speech. Both SHALL run on the local machine.

#### Scenario: Phrase with a word the recognizer does not know
- **WHEN** a command phrase contains a word missing from the recognizer's vocabulary
- **THEN** that phrase is left out of the grammar and the listener still starts

### Requirement: Command phrases come from the capability registry
The command phrases SHALL be the router utterances of the capabilities marked as voice-enabled, limited to phrases of three to ten words that contain no digits. A phrase that belongs to two capabilities SHALL be left out.

#### Scenario: Capability that is not voice-enabled
- **WHEN** the phrase table is built
- **THEN** it contains no phrase of `open_application` or any other capability not marked voice-enabled

### Requirement: A spoken request is a command only on a whole, confident phrase
A spoken request SHALL be treated as a command only when the restricted recognizer heard exactly one command phrase (after the wake word, if present), with no unknown-speech marker, every word at or above the confidence threshold, and at least a quarter of the phrase's words also heard by the free-form recognizer. Every other request SHALL be treated as a question, in the free-form recognizer's words.

#### Scenario: Known phrase
- **WHEN** the user says "zedek how much free disk space do i have"
- **THEN** the free-space capability runs and its result is spoken

#### Scenario: Question that shares words with a phrase
- **WHEN** the user says "zedek how much free disk space does a server need"
- **THEN** it is answered as a question and no capability runs

#### Scenario: Recognizers disagree completely
- **WHEN** the restricted recognizer hears a whole phrase and the free-form recognizer hears none of its words
- **THEN** the request is not a command

#### Scenario: Words from different phrases
- **WHEN** the restricted recognizer strings together words from two phrases
- **THEN** the request is not a command

### Requirement: The restricted recognizer supplies the wake word
When the restricted recognizer hears the wake word first, with confidence, the listener SHALL treat the utterance as addressed to it and SHALL take the question from the free-form recognizer's words that follow the wake word in time. Decoy words SHALL NOT wake the listener.

#### Scenario: Free-form recognizer mishears the wake word
- **WHEN** the free-form recognizer writes "she addict what is a binary search tree" and the restricted recognizer hears the wake word first
- **THEN** the question answered is "what is a binary search tree"

#### Scenario: Ordinary speech that sounds like the wake word
- **WHEN** someone says "said it could be a little later"
- **THEN** the listener does not wake

### Requirement: A spoken command cannot approve anything
A spoken command SHALL run with the capability's default arguments, without a router or a model call, through the same execution path and tier gate as a typed request, with every confirmation request refused. A capability that is not voice-enabled at Tier 0 SHALL be refused.

#### Scenario: Gate asks for confirmation
- **WHEN** the tier gate asks for confirmation during a spoken command
- **THEN** the command is cancelled and nothing runs

#### Scenario: Capability that is not voice-enabled
- **WHEN** a spoken command names `open_application`
- **THEN** it is refused and nothing runs

### Requirement: Follow-ups can refer to recent spoken exchanges
While the activation window stays open, the last two spoken exchanges (commands and questions) SHALL be given to question answering as recent conversation. They SHALL NOT be written to the session buffer or to long-term memory, and SHALL be dropped when the window closes.

#### Scenario: Follow-up after a command
- **WHEN** the user asks "is that enough for a game" after the free-space command
- **THEN** the question is answered with the command and its result as context

#### Scenario: Window closes
- **WHEN** the user says a sleep phrase and later asks a new question
- **THEN** the earlier exchanges are not included
