# Proposal

Implements the open ROADMAP D2 item (voice path: speak each sentence as it arrives).

## Why

The wake-word listener only ever printed its answers: there was no text-to-speech at all, and
nothing started the listener. A spoken answer that waits for the whole reply feels slow; Home
Assistant measured time-to-speech falling from about 6 s to about 0.5 s by speaking sentence by
sentence.

## What Changes

- New `speech.py`:
  - `SentenceStream`, a reply-stream sink that emits each sentence as soon as it is complete
    (not splitting inside numbers, domains or common abbreviations; very short pieces are joined
    to the next sentence; a run-on is cut at 300 characters), with markdown symbols and URLs
    removed from what is spoken.
  - `Speaker`, a queue with one worker thread that speaks sentences in order, can be cancelled,
    and reports whether it is still speaking.
  - Backend: speech-dispatcher's `spd-say`, local, already present on the owner's machine.
- `WakeWordListener` accepts a speaker. With one, the answer is spoken sentence by sentence while
  it is generated; "Listening." and "Going quiet." are spoken; a failure cancels speech and
  apologises. While Zedek is speaking, microphone audio is discarded, because inside the
  activation window its own voice would be taken as the user's next question.
- `python wake_word.py` starts the listener (`--no-speech` prints instead).
- Measured with the real pipeline and a timing speaker (local model, 3-sentence answer): first
  sentence ready at 2.5 s, whole answer at 4.0 s.

## Non-goals

- Voice stays questions-only: no spoken request can reach `execute()`. Unchanged and re-tested.
- No cloud TTS and no new Python dependency. Piper is not added; it can be another backend later.
- No hedged requests, no barge-in (interrupting by speaking), no two-tier recognition (F9).
- No voice confirmation channel: confirmations stay on the typed channel.

## Capabilities

### New Capabilities
- `voice-replies`: how answers to spoken questions are spoken.

### Modified Capabilities

## Impact

- Code: new `speech.py`; `wake_word.py` (speaker, streaming handler, entry point).
- Tier gate: none; the voice path still bypasses `execute()` entirely.
- Watchdog: none.
- Untrusted-input path: the spoken text is the model's answer. It is passed to `spd-say` as a
  single argument after `--`, never through a shell, so it cannot be read as an option or command.
- Privacy: answers, which may include personal facts, are spoken aloud in the room.
