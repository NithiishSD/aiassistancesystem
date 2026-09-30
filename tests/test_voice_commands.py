"""
Two-tier voice recognition (ROADMAP F9).

The important properties:
- Only Tier 0 system_agent functions can be voice-enabled, and the set is pinned.
- A spoken request is a command only when the grammar recognizer heard one
  whole known phrase, confidently, and the free-form recognizer mostly agrees.
- Everything else stays a question; the grammar recognizer still supplies the
  wake word the free-form recognizer mishears.
- A spoken command runs through execute() and the tier gate, with every
  confirmation refused.
- Spoken exchanges are kept for follow-ups but never reach the session buffer.
"""

import json

import pytest
import yaml

import capabilities
import confirmation
import orchestrator
import voice_commands as vc
import wake_word
from tests.test_capabilities import registry  # noqa: F401  (fixture)
from wake_word import HANDLED, IGNORED, REFUSED, SLEPT, WakeWordListener

WAKE = "zedek"
PHRASES = {"how much free disk space do i have": "free_space_summary",
           "what's eating my ram": "top_memory_processes"}


def words(text, conf=1.0, start=0.0, step=0.4):
    return [{"word": w, "conf": conf, "start": start + i * step, "end": start + (i + 1) * step}
            for i, w in enumerate(text.split())]


# ── Which capabilities may be spoken ─────────────────────────────────────────

def test_voice_capabilities_are_pinned():
    """Safety pin: enabling another capability for voice must edit this test."""
    assert set(capabilities.voice_capabilities()) == {"free_space_summary", "top_memory_processes"}


@pytest.mark.parametrize("name, changes", [
    ("open_application", {"voice": True}),                  # Tier 1
    ("system_inspect", {"voice": True}),                    # no tier, not a native function
    ("coding_task", {"voice": True}),                       # Tier 0 but not a native function
    ("free_space_summary", {"voice": "yes"}),
    ("free_space_summary", {"tier": 1}),                    # already voice-enabled
])
def test_voice_flag_refused_unless_tier_0_native(registry, name, changes):  # noqa: F811
    path = registry / f"{name}.yaml"
    doc = yaml.safe_load(path.read_text())
    doc.update(changes)
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    with pytest.raises(capabilities.CapabilityError):
        capabilities._load(str(registry))


# ── The phrase table and the grammar ─────────────────────────────────────────

def test_phrases_come_only_from_voice_capabilities():
    phrases = vc.command_phrases()
    assert phrases and set(phrases.values()) <= set(capabilities.voice_capabilities())
    assert phrases["how much free disk space do i have"] == "free_space_summary"
    assert not any("open" in p.split() for p in phrases)


def test_phrases_are_speakable():
    for phrase in vc.command_phrases():
        count = len(phrase.split())
        assert vc.MIN_PHRASE_WORDS <= count <= vc.MAX_PHRASE_WORDS, phrase
        assert phrase == vc.normalize_phrase(phrase) and not any(c.isdigit() for c in phrase)


def test_phrase_with_a_word_the_recognizer_lacks_is_left_out():
    phrases = vc.command_phrases(known_word=lambda w: w != "ram")
    assert phrases and not any("ram" in p.split() for p in phrases)


def test_normalize_keeps_apostrophes():
    assert vc.normalize_phrase("What's eating my RAM?") == "what's eating my ram"


def test_grammar_holds_wake_word_phrases_sleep_and_unknown():
    grammar = vc.build_grammar(PHRASES, WAKE, ["stop listening", "That's all"])
    assert grammar[0] == WAKE and grammar[-1] == vc.UNKNOWN
    assert set(PHRASES) <= set(grammar) and "stop listening" in grammar and "that's all" in grammar
    assert len(grammar) == len(set(grammar))
    assert set(vc.WAKE_DECOYS) <= set(grammar)


def test_decoy_heard_first_does_not_wake():
    """"said it could be later" reaches the grammar recognizer as a decoy, not the wake word."""
    heard = vc.decide(words("said it could be later"), words("said it [unk]"), PHRASES, WAKE)
    assert heard == vc.Heard("said it could be later")
    assert WakeWordListener(handler=lambda q: "x").process_utterance(heard.text).status == IGNORED


# ── decide(): one reading from two recognizers ───────────────────────────────

def test_command_heard_by_both():
    heard = vc.decide(words("she addict how much free disk space do i have"),
                      words("zedek how much free disk space do i have"), PHRASES, WAKE)
    assert heard == vc.Heard("zedek how much free disk space do i have", "free_space_summary", "grammar")


def test_command_inside_the_window_needs_no_wake_word():
    heard = vc.decide(words("what's eating my ram"), words("what's eating my ram"), PHRASES, WAKE)
    assert heard.capability == "top_memory_processes" and heard.text == "what's eating my ram"


def test_question_keeps_free_form_words_and_gets_the_wake_word():
    free = words("she addict what is a binary search tree")          # wake word misheard as two words
    grammar = [{"word": "zedek", "conf": 1.0, "start": 0.0, "end": 0.8},
               {"word": "[unk]", "conf": 1.0, "start": 0.8, "end": 2.8}]
    heard = vc.decide(free, grammar, PHRASES, WAKE)
    assert heard == vc.Heard("zedek what is a binary search tree", None, "free")


def test_unknown_word_in_the_grammar_reading_is_not_a_command():
    heard = vc.decide(words("zedek how much free time do i have"),
                      words("zedek how much free [unk] do i have"), PHRASES, WAKE)
    assert heard.capability is None


def test_mixed_phrase_words_are_not_a_command():
    """The grammar recognizer can chain words from different phrases."""
    heard = vc.decide(words("what time is the meeting"), words("what's eating my disk space"), PHRASES, WAKE)
    assert heard.capability is None and heard.text == "what time is the meeting"


def test_low_confidence_command_is_not_a_command():
    heard = vc.decide(words("zedek what's eating my ram"),
                      words("zedek what's eating my ram", conf=vc.WORD_CONFIDENCE - 0.1), PHRASES, WAKE)
    assert heard.capability is None


def test_command_needs_the_free_form_recognizer_to_agree():
    heard = vc.decide(words("zedek tell me about the weather today"),
                      words("zedek what's eating my ram"), PHRASES, WAKE)
    assert heard.capability is None and heard.text.startswith("zedek ")


def test_low_confidence_wake_word_does_not_wake():
    grammar = words("zedek [unk]", conf=0.5)
    heard = vc.decide(words("the deck is slippery"), grammar, PHRASES, WAKE)
    assert heard.text == "the deck is slippery"


def test_sleep_phrase_from_the_grammar():
    heard = vc.decide(words("stop glistening"), words("stop listening"), PHRASES, WAKE, ["stop listening"])
    assert heard == vc.Heard("stop listening", None, "grammar")


def test_no_grammar_words_falls_back_to_free_form():
    assert vc.decide(words("hello there"), [], PHRASES, WAKE) == vc.Heard("hello there")


# ── TwoTierRecognizer ────────────────────────────────────────────────────────

class FakeRecognizer:
    """Ends an utterance on the block numbers in `ends`; returns scripted words."""

    def __init__(self, results, ends=()):
        self.results = list(results)
        self.ends = set(ends)
        self.blocks = 0
        self.resets = 0

    def AcceptWaveform(self, data):
        self.blocks += 1
        return self.blocks in self.ends

    def _next(self):
        return json.dumps({"result": self.results.pop(0) if self.results else []})

    Result = FinalResult = _next

    def Reset(self):
        self.resets += 1


def test_recognizer_returns_nothing_until_the_utterance_ends():
    free = FakeRecognizer([words("zedek what's eating my ram")], ends={3})
    grammar = FakeRecognizer([words("zedek what's eating my ram")])
    recognizer = vc.TwoTierRecognizer(free, grammar, PHRASES, WAKE)
    assert recognizer.accept(b"a") is None and recognizer.accept(b"b") is None
    heard = recognizer.accept(b"c")
    assert heard.capability == "top_memory_processes"


def test_recognizer_keeps_grammar_words_from_an_early_segment():
    free = FakeRecognizer([words("zedek what's eating my ram")], ends={2})
    grammar = FakeRecognizer([words("zedek what's"), words("eating my ram", start=0.8)], ends={1})
    recognizer = vc.TwoTierRecognizer(free, grammar, PHRASES, WAKE)
    assert recognizer.accept(b"a") is None
    assert recognizer.accept(b"b").capability == "top_memory_processes"


def test_recognizer_ignores_silence_and_resets_both():
    free, grammar = FakeRecognizer([[]], ends={1}), FakeRecognizer([[]])
    recognizer = vc.TwoTierRecognizer(free, grammar, PHRASES, WAKE)
    assert recognizer.accept(b"a") is None
    recognizer.reset()
    assert free.resets == 1 and grammar.resets == 1


# ── The listener ─────────────────────────────────────────────────────────────

@pytest.fixture
def ran():
    return []


@pytest.fixture
def listener(ran):
    def command_handler(capability, text):
        ran.append((capability, text))
        return "120GB free"

    def handler(question, recent=()):
        ran.append(("question", question, tuple(recent)))
        return "an answer"

    return WakeWordListener(handler=handler, command_handler=command_handler)


def test_spoken_command_runs_the_command_handler(listener, ran):
    result = listener.process_utterance("zedek how much free disk space do i have",
                                        capability="free_space_summary")
    assert result.status == HANDLED and result.response == "120GB free"
    assert ran == [("free_space_summary", "how much free disk space do i have")]


def test_exact_phrase_in_a_transcript_is_a_command(listener, ran):
    listener.process_utterance("Zedek, what's eating my RAM?")
    assert ran == [("top_memory_processes", "whats eating my ram")]


def test_anything_else_is_a_question(listener, ran):
    for said in ("zedek open the calculator", "zedek how much free disk space does a server need"):
        listener.process_utterance(said)
    assert [entry[0] for entry in ran] == ["question", "question"]


def test_command_without_wake_word_outside_the_window_is_ignored(listener, ran):
    result = listener.process_utterance("how much free disk space do i have", capability="free_space_summary")
    assert result.status == IGNORED and ran == []


def test_failed_command_is_reported_and_keeps_the_window(ran):
    def broken(capability, text):
        raise RuntimeError("disk gone")

    listener = WakeWordListener(handler=lambda q: "x", command_handler=broken)
    result = listener.process_utterance("zedek is my disk full", capability="free_space_summary")
    assert result.status == REFUSED and result.active is True


def test_follow_up_question_receives_the_recent_exchanges(listener, ran):
    listener.process_utterance("zedek how much free disk space do i have", capability="free_space_summary")
    listener.process_utterance("is that enough for a game")
    assert ran[-1] == ("question", "is that enough for a game", (
        {"role": "user", "content": "how much free disk space do i have"},
        {"role": "assistant", "content": "120GB free"}))


def test_recent_exchanges_are_bounded_and_end_with_the_window(listener, ran):
    for n in range(5):
        listener.process_utterance(f"zedek question number {n}")
    assert len(ran[-1][2]) == 2 * wake_word.RECENT_VOICE_EXCHANGES

    assert listener.process_utterance("stop listening").status == SLEPT
    listener.process_utterance("zedek a new question")
    assert ran[-1][2] == ()


def test_long_reply_is_cut_in_the_recent_exchanges(ran):
    listener = WakeWordListener(handler=lambda q, recent=(): ran.append(recent) or "x" * 5000)
    listener.process_utterance("zedek first question")
    listener.process_utterance("second question")
    assert len(ran[-1][1]["content"]) == wake_word.MAX_RECENT_REPLY_CHARS


# ── The default handlers against the real orchestrator ───────────────────────

def test_run_voice_command_goes_through_the_gate_with_confirmations_refused(monkeypatch):
    seen = {}

    def fake_gate(name, args, user_input=""):
        seen["gate"] = (name, args)
        seen["channel"] = type(confirmation.get_channel()).__name__
        return {"action": "allow", "message": "", "tier": 0}

    monkeypatch.setattr(orchestrator, "gate", fake_gate)
    monkeypatch.setitem(orchestrator.AVAILABLE_FUNCTIONS, "free_space_summary",
                        lambda: {"free_gb": 120, "total_gb": 500, "used_gb": 380})
    monkeypatch.setattr(orchestrator.llm_provider, "local_chat", lambda *a, **k: pytest.fail("no model call"))
    monkeypatch.setattr(orchestrator.llm_provider, "generate_chat", lambda *a, **k: pytest.fail("no model call"))
    before = list(orchestrator.SESSION_HISTORY)

    reply = wake_word.run_voice_command("free_space_summary", "is my disk full")

    assert reply.startswith("You have 120GB free")
    assert seen == {"gate": ("free_space_summary", {}), "channel": "DenyChannel"}
    assert orchestrator.SESSION_HISTORY == before
    assert type(confirmation.get_channel()).__name__ == "TerminalChannel"


def test_run_voice_command_stops_when_the_gate_asks_for_confirmation(monkeypatch):
    monkeypatch.setattr(orchestrator, "gate", lambda *a, **k: {"action": "confirm", "message": "sure?", "tier": 2})
    monkeypatch.setitem(orchestrator.AVAILABLE_FUNCTIONS, "free_space_summary", lambda: pytest.fail("ran"))
    assert wake_word.run_voice_command("free_space_summary", "is my disk full") == "Cancelled."


@pytest.mark.parametrize("capability", ["open_application", "search_files", "web_task", "coding_task",
                                        "remember_fact", "mcp_tool", "not_a_capability"])
def test_run_voice_command_refuses_everything_else(monkeypatch, capability):
    monkeypatch.setattr(orchestrator, "execute", lambda *a, **k: pytest.fail("must not execute"))
    assert wake_word.run_voice_command(capability, "anything") == wake_word.VOICE_COMMAND_REFUSED


def test_default_listener_runs_a_spoken_command_without_question_answering(monkeypatch):
    monkeypatch.setattr(orchestrator, "answer_general_question", lambda *a, **k: pytest.fail("not a question"))
    monkeypatch.setattr(orchestrator, "execute", lambda decision: f"ran {decision.function} {decision.args}")
    result = WakeWordListener().process_utterance("zedek how much free disk space do I have")
    assert result.response == "ran free_space_summary {}"


def test_recent_exchanges_reach_the_model_but_not_the_session(monkeypatch):
    sent = {}

    def fake_chat(messages, task=None, **kwargs):
        sent["messages"] = messages
        return {"answer": "yes", "source": "test"}

    monkeypatch.setattr(orchestrator.llm_provider, "generate_chat", fake_chat)
    monkeypatch.setattr(orchestrator.memory, "retrieve_relevant", lambda *a, **k: [])
    monkeypatch.setattr(orchestrator.user_profile, "for_question", lambda *a, **k: [])
    before = list(orchestrator.SESSION_HISTORY)
    recent = ({"role": "user", "content": "is my disk full"}, {"role": "assistant", "content": "120GB free"})

    assert wake_word.qa_only_handler("is that enough", recent=recent) == "yes"

    contents = [m["content"] for m in sent["messages"]]
    assert "is my disk full" in contents and "120GB free" in contents
    assert contents.index("120GB free") < len(contents) - 1      # before the question itself
    assert orchestrator.SESSION_HISTORY == before and orchestrator._CHANNEL_TURNS.get() == ()


def test_slow_answer_does_not_use_up_the_window(monkeypatch):
    """The window is counted from the reply, so a follow-up after a slow answer is still heard."""
    clock = {"now": 1000.0}
    monkeypatch.setattr(wake_word.time, "time", lambda: clock["now"])

    def slow(question):
        clock["now"] += wake_word.ACTIVATION_WINDOW_SECONDS + 5
        return "done"

    listener = WakeWordListener(handler=slow)
    assert listener.process_utterance("zedek explain heaps").status == HANDLED
    assert listener.is_active
