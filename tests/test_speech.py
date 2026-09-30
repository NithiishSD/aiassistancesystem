"""Sentence-by-sentence speech (OpenSpec change: speak-replies-by-sentence). No audio is played."""

import threading
import time

import pytest

import speech
import wake_word
from speech import SentenceStream, Speaker
from wake_word import HANDLED, REFUSED, WakeWordListener


def _sentences(chunks):
    out = []
    stream = SentenceStream(out.append)
    for chunk in chunks:
        stream.delta(chunk)
    before_finish = list(out)
    stream.finish()
    return before_finish, out


class TestSentenceStream:
    def test_sentence_is_emitted_as_soon_as_it_is_complete(self):
        before, after = _sentences(["Paris is the capi", "tal of France. It has two mil", "lion people."])
        assert before == ["Paris is the capital of France."]
        assert after == ["Paris is the capital of France.", "It has two million people."]

    def test_character_by_character(self):
        text = "The answer is 3.5 metres. Visit example.com for more! Is that enough? Yes."
        _, after = _sentences(list(text))
        assert after == ["The answer is 3.5 metres.", "Visit example.com for more!", "Is that enough?", "Yes."]

    def test_abbreviations_and_short_pieces_do_not_split(self):
        _, after = _sentences(["Use a list, e.g. a Python list. Ok. Then sort it."])
        assert after == ["Use a list, e.g. a Python list.", "Ok. Then sort it."]

    def test_line_breaks_end_a_sentence(self):
        _, after = _sentences(["First point about heaps\nSecond point about tries\n"])
        assert after == ["First point about heaps", "Second point about tries"]

    def test_run_on_is_cut(self):
        _, after = _sentences(["word " * 200])
        assert len(after) > 1 and all(len(piece) <= speech.MAX_SENTENCE_CHARS for piece in after)
        assert " ".join(after).split() == ["word"] * 200

    def test_markdown_and_urls_are_not_read_out(self):
        _, after = _sentences(["**Heaps** are `trees`. See https://example.com/a?b=1 for details.\n- first item\n"])
        assert after == ["Heaps are trees.", "See a link for details.", "first item"]

    def test_restart_discards_the_unspoken_part(self):
        out, restarts = [], []
        stream = SentenceStream(out.append, on_restart=lambda: restarts.append(1))
        stream.delta("A complete first sentence. And half of a sec")
        stream.restart()
        stream.delta("The other provider answers here.")
        stream.finish()
        assert out == ["A complete first sentence.", "The other provider answers here."] and restarts == [1]

    def test_empty_reply(self):
        assert _sentences(["", "  "]) == ([], [])


class TestSpeaker:
    def test_speaks_in_order_and_reports_busy(self):
        spoken, release = [], threading.Event()

        def slow(text):
            release.wait(2)
            spoken.append(text)

        speaker = Speaker(speak_fn=slow, cancel_fn=lambda: None)
        assert not speaker.busy
        speaker.say("one")
        speaker.say("two")
        assert speaker.busy
        release.set()
        assert speaker.wait(2) and not speaker.busy
        assert spoken == ["one", "two"]

    def test_cancel_drops_the_queue(self):
        spoken, gate, cancelled = [], threading.Event(), []

        def slow(text):
            gate.wait(2)
            spoken.append(text)

        speaker = Speaker(speak_fn=slow, cancel_fn=lambda: cancelled.append(1))
        for text in ("one", "two", "three"):
            speaker.say(text)
        time.sleep(0.05)
        speaker.cancel()
        gate.set()
        assert speaker.wait(2)
        assert spoken == ["one"] and cancelled == [1]

    def test_backend_failure_does_not_kill_the_worker(self):
        spoken = []

        def flaky(text):
            if text == "boom":
                raise OSError("no audio device")
            spoken.append(text)

        speaker = Speaker(speak_fn=flaky, cancel_fn=lambda: None)
        speaker.say("boom")
        speaker.say("still here")
        assert speaker.wait(2) and spoken == ["still here"]

    def test_nothing_speakable_is_not_queued(self):
        speaker = Speaker(speak_fn=lambda text: None, cancel_fn=lambda: None)
        speaker.say("***")
        assert not speaker.busy

    def test_backend_command_cannot_be_given_options(self, monkeypatch):
        calls = []
        monkeypatch.setattr(speech.shutil, "which", lambda name: "/usr/bin/spd-say")
        monkeypatch.setattr(speech.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
        speech._speak_blocking("--cancel everything")
        assert calls == [["/usr/bin/spd-say", "--wait", "--", "--cancel everything"]]


class _FakeSpeaker:
    def __init__(self):
        self.said, self.cancelled, self.busy = [], 0, False

    def say(self, text):
        self.said.append(text)

    def cancel(self):
        self.cancelled += 1

    def stream(self):
        return SentenceStream(self.say, on_restart=self.cancel)


class TestListenerSpeaks:
    def test_streamed_reply_is_spoken_by_sentence_before_the_handler_returns(self):
        speaker = _FakeSpeaker()
        seen_mid_answer = []

        def handler(question, stream=None):
            stream.delta("Heaps are trees. ")
            seen_mid_answer.append(list(speaker.said))
            stream.delta("They keep the largest item on top.")
            return "Heaps are trees. They keep the largest item on top."

        result = WakeWordListener(handler=handler, speaker=speaker).process_utterance("zedek what is a heap")
        assert result.status == HANDLED
        assert seen_mid_answer == [["Heaps are trees."]]
        assert speaker.said == ["Heaps are trees.", "They keep the largest item on top."]

    def test_unstreamed_reply_is_spoken_whole(self):
        speaker = _FakeSpeaker()
        WakeWordListener(handler=lambda q, stream=None: "Forty two.", speaker=speaker).process_utterance("zedek answer")
        assert speaker.said == ["Forty two."]

    def test_handler_without_stream_parameter(self):
        speaker = _FakeSpeaker()
        WakeWordListener(handler=lambda q: "Plain answer.", speaker=speaker).process_utterance("zedek answer")
        assert speaker.said == ["Plain answer."]

    def test_failure_cancels_and_apologises(self):
        speaker = _FakeSpeaker()

        def handler(question, stream=None):
            stream.delta("Half an answer. ")
            raise RuntimeError("provider down")

        result = WakeWordListener(handler=handler, speaker=speaker).process_utterance("zedek answer")
        assert result.status == REFUSED and speaker.cancelled == 1
        assert speaker.said[-1] == "Sorry, I couldn't answer that."

    def test_sleep_cancels_speech(self):
        speaker = _FakeSpeaker()
        listener = WakeWordListener(handler=lambda q: "x", speaker=speaker)
        listener.activate()
        listener.process_utterance("go to sleep")
        assert speaker.cancelled == 1 and speaker.said == ["Going quiet."]

    def test_no_speaker_is_unchanged(self):
        result = WakeWordListener(handler=lambda q: "Plain answer.").process_utterance("zedek answer")
        assert result.status == HANDLED and result.response == "Plain answer."

    def test_default_handler_streams_through_general_qa_only(self, monkeypatch):
        import orchestrator

        seen = {}

        def fake_answer(question, domain):
            seen["sink"] = orchestrator._REPLY_STREAM.get()
            return "ok"

        monkeypatch.setattr(orchestrator, "answer_general_question", fake_answer)
        monkeypatch.setattr(orchestrator, "execute", lambda *a, **k: pytest.fail("voice must never execute"))
        sink = object()
        assert wake_word.qa_only_handler("open the calculator", stream=sink) == "ok"
        assert seen["sink"] is sink and orchestrator._REPLY_STREAM.get() is None
