"""A newly remembered fact supersedes the one it replaces, and questions about
the past can read the history (OpenSpec change: supersede-and-recall-history).

Runs on an in-memory Chroma collection, never the live store. Every value here
is fictional.
"""

import pytest

import fact_attributes as fa
import memory
import orchestrator
from routing_decision import RoutingDecision
from tests.test_fact_history import _meta, collection  # noqa: F401  (fixture)


# ── Which attributes hold one value ──────────────────────────────────────────

@pytest.mark.parametrize("fact, key", [
    ("User's name: Alex Rivera", "name"),
    ("User's Preferred Name: Alex", "name"),
    ("User's college: Northwind Institute", "college"),
    ("User's Institution: Northwind Institute", "college"),
    ("User's Current Semester: 5th semester", "semester"),
    ("User's residence: Lakeview", "location"),
    ("User's Location: Lakeview", "location"),
    ("User's Field of Study: Computer Science", "field of study"),
    ("User's Operating System: Linux", "operating system"),
    ("User's preferred code editor: Vim", "code editor"),
])
def test_single_valued_attributes(fact, key):
    assert fa.single_valued_key(fact) == key


@pytest.mark.parametrize("fact", [
    "User's project: a chess engine", "User's subject: Operating Systems", "User's goal: a systems internship",
    "User's interest: astronomy", "User's target companies: Initech, Globex",
    "User's Location (alternate): Hillcrest",          # an explicit second value
    "User's lab partner's name: Sam", "User's target college: Eastfield", "User's course name: Compilers",
    "Alex likes tea", "",
])
def test_everything_else_may_hold_many_values(fact):
    assert fa.single_valued_key(fact) is None


def test_values_compare_without_case_punctuation_or_the_attribute_word():
    assert fa.value_key("User's Current Semester: 5th semester") == fa.value_key("User's semester: 5th.")
    assert fa.value_key("User's name: alex rivera") == fa.value_key("User's Name: Alex Rivera.")
    assert fa.value_key("User's college: Northwind") != fa.value_key("User's college: Eastfield")


# ── memory.remember ──────────────────────────────────────────────────────────

class TestRemember:
    def test_new_value_supersedes_the_old(self, collection):
        old = memory.remember("User's residence: Lakeview")["id"]
        outcome = memory.remember("User's Location: Hillcrest")

        assert [f["text"] for f in outcome["superseded"]] == ["User's residence: Lakeview"]
        assert [f["text"] for f in memory.current_facts()] == ["User's Location: Hillcrest"]
        meta = _meta(collection, old)
        assert meta["invalidated"] is True and meta["superseded_by"] == outcome["id"] and meta["invalid_at"]

    def test_every_conflicting_value_is_superseded(self, collection):
        memory.store("User's residence: Lakeview")
        memory.store("User's Location: Oldtown")           # a conflict from before this rule existed
        outcome = memory.remember("User's city: Hillcrest")
        assert len(outcome["superseded"]) == 2 and len(memory.current_facts()) == 1

    def test_same_value_is_not_stored_twice(self, collection):
        first = memory.remember("User's Current Semester: 5th semester")["id"]
        outcome = memory.remember("User's semester: 5th")
        assert outcome == {"id": first, "superseded": [], "duplicate": True}
        assert collection.count() == 1

    def test_multi_valued_attribute_keeps_both(self, collection):
        memory.remember("User's project: a chess engine")
        outcome = memory.remember("User's project: a ray tracer")
        assert outcome["superseded"] == [] and len(memory.current_facts()) == 2

    def test_different_attributes_do_not_touch_each_other(self, collection):
        memory.remember("User's college: Northwind Institute")
        memory.remember("User's residence: Lakeview")
        assert len(memory.current_facts()) == 2

    def test_rejected_fact_supersedes_nothing(self, collection):
        memory.remember("User's college: Northwind Institute")
        outcome = memory.remember("User's college: not stated")     # hygiene refuses null values
        assert outcome["id"] == "" and outcome["superseded"] == []
        assert [f["text"] for f in memory.current_facts()] == ["User's college: Northwind Institute"]

    def test_history_shows_the_change(self, collection):
        memory.remember("User's college: Northwind Institute")
        memory.remember("User's college: Eastfield College")
        statuses = {item["text"]: item["status"] for item in memory.history("college")}
        assert statuses == {"User's college: Northwind Institute": "superseded",
                            "User's college: Eastfield College": "current"}


class TestConflicts:
    def test_lists_single_valued_attributes_with_several_values(self, collection):
        for fact in ("User's residence: Lakeview", "User's Location: Hillcrest", "User's semester: 5th",
                     "User's Current Semester: 5th semester", "User's project: a", "User's project: b"):
            memory.store(fact)
        groups = memory.conflicts()
        assert set(groups) == {"location"} and len(groups["location"]) == 2
        assert "location: 2 current values" in memory.format_conflicts(groups)

    def test_no_conflicts(self, collection):
        memory.store("User's name: Alex Rivera")
        assert memory.conflicts() == {} and memory.format_conflicts({}).startswith("No single-valued")


# ── The remember-fact handler ────────────────────────────────────────────────

@pytest.fixture
def remember(collection, monkeypatch):  # noqa: F811
    monkeypatch.setattr(orchestrator, "_acknowledge_fact", lambda text: "Noted.")

    def run(statement, facts):
        monkeypatch.setattr(orchestrator, "canonicalize_fact", lambda text: facts)
        return orchestrator._run_remember_fact(RoutingDecision(function="remember_fact", user_input=statement),
                                               "personal")
    return run


def test_reply_says_what_was_replaced(remember):
    assert remember("i live in lakeview", ["User's residence: Lakeview"]) == "Noted."
    reply = remember("i moved to hillcrest", ["User's residence: Hillcrest"])
    assert reply.startswith("Noted.")
    assert '"User\'s residence: Lakeview" ➔ "User\'s residence: Hillcrest"' in reply
    assert "kept as history" in reply


def test_reply_is_plain_when_nothing_was_replaced(remember):
    remember("i'm building a chess engine", ["User's project: a chess engine"])
    assert remember("i'm also building a ray tracer", ["User's project: a ray tracer"]) == "Noted."
    assert remember("i'm building a ray tracer", ["User's project: a ray tracer"]) == "Noted."


# ── Questions about the past ─────────────────────────────────────────────────

class TestPastFacts:
    def test_returns_only_facts_no_longer_valid_with_their_dates(self, collection, monkeypatch):
        monkeypatch.setattr(memory, "FALLBACK_MAX_DISTANCE", 99.0)   # the test embedding is not semantic
        memory.remember("User's residence: Lakeview")
        memory.remember("User's residence: Hillcrest")
        past = memory.past_facts("where did i live before")
        assert [p["text"] for p in past] == ["User's residence: Lakeview"]
        assert past[0]["status"] == "superseded" and past[0]["valid_from"] <= past[0]["valid_until"]

    def test_distant_rows_are_left_out(self, collection, monkeypatch):
        monkeypatch.setattr(memory, "FALLBACK_MAX_DISTANCE", -1.0)
        memory.remember("User's residence: Lakeview")
        memory.remember("User's residence: Hillcrest")
        assert memory.past_facts("where did i live before") == []


@pytest.fixture
def asked(monkeypatch):
    """Runs general Q&A with stubs and returns the final user message sent to the model."""
    sent = {}
    monkeypatch.setattr(orchestrator.memory, "retrieve_relevant",
                        lambda *a, **k: [{"text": "User's residence: Hillcrest", "id": "n"}])
    monkeypatch.setattr(orchestrator.user_profile, "for_question", lambda *a, **k: [])
    monkeypatch.setattr(orchestrator.memory, "past_facts", lambda query, domain="personal": [
        {"text": "User's residence: Lakeview", "valid_from": 1_750_000_000.0, "valid_until": 1_790_000_000.0}])
    monkeypatch.setattr(orchestrator.llm_provider, "generate_chat",
                        lambda messages, **k: sent.update(prompt=messages[-1]["content"]) or
                        {"answer": "ok", "source": "stub"})

    def run(question):
        orchestrator.answer_general_question(question, "personal")
        return sent["prompt"]
    return run


@pytest.mark.parametrize("question", ["where did i live before", "what was my residence last year",
                                      "where did I use to live", "what college was I at previously"])
def test_past_question_about_the_user_gets_history_with_dates(asked, question):
    prompt = asked(question)
    assert "no longer true" in prompt and "User's residence: Lakeview (true from" in prompt
    assert "until" in prompt and "User's residence: Hillcrest" in prompt


@pytest.mark.parametrize("question", ["where do i live", "what is my college",
                                      "what was the capital of prussia before 1871", "how did rome fall"])
def test_other_questions_get_no_history(asked, question):
    assert "no longer true" not in asked(question)
