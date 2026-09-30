import pytest

from classifier import classify_intent
from orchestrator import should_ask_ambiguous_term_question, should_treat_as_disambiguation, tone_for_prompt


def test_should_treat_as_disambiguation_for_astro():
    previous = "hey i want to study astro would you please tell me what it is"
    current = "no i am talking about astro frontend framework"

    assert should_treat_as_disambiguation(previous, current) is True


def test_tone_for_prompt_is_playful_for_casual_user():
    tone = tone_for_prompt("hey bro i need a quick answer pls")

    assert "fun" in tone.lower() or "playful" in tone.lower() or "casual" in tone.lower()


def test_thank_you_is_not_correct_fact():
    result = classify_intent("okay thank you")

    assert result["function"] is None
    assert result["confidence"] == "high"


def test_unsupported_media_control_is_not_a_fact_correction():
    result = classify_intent("stop the music played in spotify")

    assert result["function"] == "unsupported"
    assert result["confidence"] == "high"


def test_open_application_routes_generic_app_request():
    result = classify_intent("can you open brave application")

    assert result["function"] == "open_application"
    assert result["confidence"] == "high"


def test_close_application_remains_unsupported():
    result = classify_intent("close brave application")

    assert result["function"] == "unsupported"


def test_meta_question_about_astro_is_not_ambiguity_request():
    question = "you confused astro with so many words like that what are other words you would get confused"

    assert should_ask_ambiguous_term_question(question) is False


def test_correction_not_blocked_by_ambiguous_term():
    text = "no you mistook astro frontend framework is not part of datastructure course and also there is no course of datastructure that i am currently learning so remove that from your memory"
    assert should_ask_ambiguous_term_question(text) is False


def test_session_flush_preserves_recent_turns(monkeypatch):
    import orchestrator
    orchestrator.SESSION_HISTORY = [
        {"role": "user", "content": f"msg {i}"} for i in range(12)
    ]
    monkeypatch.setattr("orchestrator.llm_provider.ollama.chat", lambda **kwargs: {"message": {"content": "NONE"}})
    orchestrator.summarize_and_flush_session(keep_recent=4)
    assert len(orchestrator.SESSION_HISTORY) == 4
    assert orchestrator.SESSION_HISTORY[-1]["content"] == "msg 11"



class TestSessionSummary:
    """Dropped turns leave a summary behind (OpenSpec change: session-summary-and-loop-guard)."""

    def _flush(self, monkeypatch, reply, turns=12, keep_recent=4, summary=""):
        import orchestrator

        seen = {}

        def fake_chat(**kwargs):
            seen["prompt"] = kwargs["messages"][0]["content"]
            return {"message": {"content": reply}}

        stored = []
        monkeypatch.setattr(orchestrator, "SESSION_HISTORY", [{"role": "user", "content": f"msg {i}"} for i in range(turns)])
        monkeypatch.setattr(orchestrator, "SESSION_SUMMARY", summary)
        monkeypatch.setattr("orchestrator.llm_provider.ollama.chat", fake_chat)
        monkeypatch.setattr(orchestrator.memory, "store", lambda text, **kwargs: stored.append(text))
        orchestrator.summarize_and_flush_session(keep_recent=keep_recent)
        return orchestrator, seen.get("prompt", ""), stored

    def test_summary_kept_and_facts_stored(self, monkeypatch):
        orch, prompt, stored = self._flush(
            monkeypatch, "User's editor: Neovim\nSUMMARY: We compared editors and settled on Neovim.")
        assert stored == ["User's editor: Neovim"]
        assert orch.SESSION_SUMMARY == "We compared editors and settled on Neovim."
        assert len(orch.SESSION_HISTORY) == 4

    def test_only_the_dropped_turns_are_reviewed(self, monkeypatch):
        _, prompt, _ = self._flush(monkeypatch, "NONE\nSUMMARY: Small talk.")
        assert "msg 7" in prompt and "msg 8" not in prompt and "msg 11" not in prompt

    def test_none_stores_nothing_but_still_summarises(self, monkeypatch):
        orch, _, stored = self._flush(monkeypatch, "NONE\nSUMMARY: Checked disk space.")
        assert stored == [] and orch.SESSION_SUMMARY == "Checked disk space."

    def test_earlier_summary_is_carried_into_the_next(self, monkeypatch):
        _, prompt, _ = self._flush(monkeypatch, "NONE\nSUMMARY: x", summary="We planned the week.")
        assert "Summary of the conversation before this transcript: We planned the week." in prompt

    def test_no_summary_line_keeps_the_old_summary(self, monkeypatch):
        orch, _, _ = self._flush(monkeypatch, "NONE", summary="We planned the week.")
        assert orch.SESSION_SUMMARY == "We planned the week."

    def test_summary_is_bounded(self, monkeypatch):
        orch, _, _ = self._flush(monkeypatch, "SUMMARY: " + "x" * 5000)
        assert len(orch.SESSION_SUMMARY) == orch.MAX_SESSION_SUMMARY_CHARS

    def test_session_end_clears_everything(self, monkeypatch):
        orch, prompt, _ = self._flush(monkeypatch, "NONE\nSUMMARY: x", keep_recent=0, summary="old")
        assert orch.SESSION_HISTORY == [] and orch.SESSION_SUMMARY == ""
        assert "msg 11" in prompt

    def test_model_failure_still_trims(self, monkeypatch):
        import orchestrator

        monkeypatch.setattr(orchestrator, "SESSION_HISTORY", [{"role": "user", "content": f"msg {i}"} for i in range(12)])
        monkeypatch.setattr(orchestrator, "SESSION_SUMMARY", "old")
        monkeypatch.setattr("orchestrator.llm_provider.ollama.chat", lambda **k: (_ for _ in ()).throw(RuntimeError("down")))
        orchestrator.summarize_and_flush_session(keep_recent=4)
        assert len(orchestrator.SESSION_HISTORY) == 4 and orchestrator.SESSION_SUMMARY == "old"

    def test_summary_reaches_the_next_answer(self, monkeypatch):
        import orchestrator

        captured = {}
        monkeypatch.setattr(orchestrator, "SESSION_HISTORY", [])
        monkeypatch.setattr(orchestrator, "SESSION_SUMMARY", "We compared editors.")
        monkeypatch.setattr(orchestrator.memory, "retrieve_relevant", lambda *a, **k: [])
        monkeypatch.setattr(orchestrator.llm_provider, "generate_chat",
                            lambda messages, **k: captured.update(messages=messages) or {"answer": "ok", "source": "stub"})
        orchestrator.answer_general_question("which one was faster", "personal")
        assert "Earlier in this conversation (summary): We compared editors." in captured["messages"][-1]["content"]
        assert captured["messages"][0]["content"] == orchestrator._GENERAL_QA_SYSTEM

        monkeypatch.setattr(orchestrator, "SESSION_SUMMARY", "")
        orchestrator.answer_general_question("which one was faster", "personal")
        assert "Earlier in this conversation" not in captured["messages"][-1]["content"]
