"""The core profile block (OpenSpec change: core-profile-block)."""

from unittest.mock import patch

import pytest

import user_profile


def _fact(text, stamp=0, fact_id=None):
    return {"text": text, "metadata": {"timestamp": stamp}, "id": fact_id or text}


CORE = ["User's name: Alex", "User's college: Northwind Institute", "User's degree: BSc",
        "User's Current Semester: 5th", "User's subjects: Java, ML", "User's goal: build a compiler",
        "User's target companies: product companies", "User's placement preparation focus: DSA"]
NOT_CORE = ["User's hobby: cricket", "User's course code for Java: 23XT51", "User's lab partner's name: Sam",
            "User's favourite language: Python", "not a canonical fact", "User's exam frequency: weekly"]
SENSITIVE = ["User's Location: Rivertown", "User's hometown district: Riverside", "User's roll number: 22PT14",
             "User's hostel room: C-217", "User's GitHub username: someone42", "User's phone: 555-0100",
             "User's email: a@example.com", "User's home address: 1 Main St", "User's account name: alex"]


class TestSelect:
    def test_core_facts_in_stable_order(self):
        chosen = [f["text"] for f in user_profile.select([_fact(t) for t in reversed(CORE + NOT_CORE)])]
        assert chosen == CORE

    @pytest.mark.parametrize("text", NOT_CORE + SENSITIVE)
    def test_excluded(self, text):
        assert user_profile.select([_fact(text)]) == []

    def test_newest_wins_per_attribute(self):
        facts = [_fact("User's college: Old", 1), _fact("User's College: New", 5), _fact("User's college: Older", 0)]
        assert [f["text"] for f in user_profile.select(facts)] == ["User's College: New"]

    def test_size_limits(self):
        many = [_fact(f"User's {word} goal: {'x' * 150}") for word in "abcdefghijklmnopqrstuvwxyz"]
        chosen = user_profile.select(many)
        assert len(chosen) <= user_profile.MAX_PROFILE_FACTS
        assert sum(len(f["text"]) for f in chosen) <= user_profile.MAX_PROFILE_CHARS

    def test_distractors_do_not_crowd_the_block(self):
        noise = [_fact(f"User's course code for Subject {n}: 23XT{n}") for n in range(300)]
        assert [f["text"] for f in user_profile.select(noise + [_fact(t) for t in CORE])] == CORE


class TestForQuestion:
    @pytest.mark.parametrize("question", ["what do people call me", "where do I study", "what's my degree",
                                          "am I in my final year", "remind me what I'm aiming for"])
    def test_questions_about_the_user(self, question):
        assert user_profile.refers_to_user(question)

    @pytest.mark.parametrize("question", ["what is a binary search tree", "who invented Linux", "explain recursion",
                                          "how does the iPhone camera work", "is miami in florida", ""])
    def test_general_questions(self, question):
        assert not user_profile.refers_to_user(question)

    def test_general_question_reads_nothing(self):
        with patch("memory.current_facts") as read:
            assert user_profile.for_question("what is a binary search tree") == []
        read.assert_not_called()

    def test_store_failure_means_no_profile(self):
        with patch("memory.current_facts", side_effect=RuntimeError("store locked")):
            assert user_profile.for_question("what is my name") == []


class TestGeneralQa:
    def _ask(self, question, retrieved, stored):
        import orchestrator

        captured = {}

        def fake_chat(messages, **kwargs):
            captured["context"] = messages[-1]["content"]
            return {"answer": "ok", "source": "stub"}

        with patch.object(orchestrator.llm_provider, "generate_chat", side_effect=fake_chat), \
             patch.object(orchestrator.memory, "retrieve_relevant", return_value=retrieved), \
             patch("memory.current_facts", return_value=[_fact(t) for t in stored]), \
             patch.object(orchestrator, "SESSION_HISTORY", []):
            orchestrator.answer_general_question(question, "personal")
        return captured["context"]

    def test_reworded_question_gets_the_core_fact(self):
        context = self._ask("what do people call me", [], CORE + SENSITIVE)
        assert "- User's name: Alex" in context
        assert "Rivertown" not in context and "22PT14" not in context

    def test_general_question_gets_no_stored_facts(self):
        context = self._ask("what is a binary search tree", [], CORE)
        assert "(no relevant long-term facts found)" in context and "Alex" not in context

    def test_retrieved_fact_not_repeated(self):
        context = self._ask("what is my name", [{"text": "User's name: Alex", "score": 0.9}], CORE)
        assert context.count("User's name: Alex") == 1

    def test_sensitive_fact_still_arrives_when_retrieval_finds_it(self):
        context = self._ask("where do I live", [{"text": "User's Location: Rivertown", "score": 0.9}], CORE + SENSITIVE)
        assert "- User's Location: Rivertown" in context
