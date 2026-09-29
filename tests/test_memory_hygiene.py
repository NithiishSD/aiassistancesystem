"""
Tests for memory hygiene (fact validation on write + cleanup of existing rows).

Every rejection case below was found in the LIVE store during an audit — these
are regression tests against real observed corruption, not hypotheticals:

  "Here are the extracted facts:"                 x22  (LLM preamble)
  "* User's Name: Nithiish"                       x40  (unstripped bullet)
  "User's educational institution: not stated"    x2   (null value)
  "User's college: <new value>"                   x1   (template placeholder)
  "...exam next week: false"                      x1   (retraction stored as text)
"""

import pytest

import memory_hygiene
from memory_hygiene import CleanupReport, is_valid_fact, normalize_fact


class TestRejection:
    @pytest.mark.parametrize("preamble", [
        "Here are the extracted facts:",
        "Here are the long-term facts worth remembering:",
        "Here are the genuinely useful long-term facts worth remembering:",
        "The following facts are worth keeping:",
        "These are the facts:",
        "Sure, here is what I found:",
    ])
    def test_llm_preamble_rejected(self, preamble):
        normalized, reason = normalize_fact(preamble)
        assert normalized is None
        assert reason in ("llm_preamble", "heading_without_value")

    @pytest.mark.parametrize("null_fact", [
        "User's educational institution: not stated",
        "User's knowledge of astronomy: unknown",
        "User's phone: n/a",
        "User's address: none",
        "User's college: not specified",
    ])
    def test_null_values_rejected(self, null_fact):
        normalized, reason = normalize_fact(null_fact)
        assert normalized is None
        assert reason == "null_value"

    def test_template_placeholder_rejected(self):
        normalized, reason = normalize_fact("User's college: <new value>")
        assert normalized is None
        assert reason == "unresolved_template_placeholder"

    def test_retraction_stored_as_text_rejected(self):
        normalized, reason = normalize_fact("I'm studying for my exam next week: false")
        assert normalized is None
        assert reason == "retraction_stored_as_text"

    def test_heading_without_value_rejected(self):
        normalized, reason = normalize_fact("Extracted facts:")
        assert normalized is None

    def test_empty_and_whitespace_rejected(self):
        assert normalize_fact("")[0] is None
        assert normalize_fact("   ")[0] is None
        assert normalize_fact(None)[0] is None

    def test_too_short_rejected(self):
        assert normalize_fact("ab")[0] is None

    def test_too_long_rejected(self):
        assert normalize_fact("x" * 600)[0] is None

    def test_empty_value_rejected(self):
        assert normalize_fact("User's college:")[0] is None


class TestNormalization:
    @pytest.mark.parametrize("bulleted,expected", [
        ("* User's Name: Nithiish", "User's Name: Nithiish"),
        ("- User's Name: Nithiish", "User's Name: Nithiish"),
        ("• User's Name: Nithiish", "User's Name: Nithiish"),
        ("1. User's Name: Nithiish", "User's Name: Nithiish"),
        ("2) User's Name: Nithiish", "User's Name: Nithiish"),
    ])
    def test_bullets_stripped(self, bulleted, expected):
        normalized, reason = normalize_fact(bulleted)
        assert normalized == expected
        assert reason == "ok"

    def test_wrapping_quotes_stripped(self):
        assert normalize_fact('"User\'s Name: Nithiish"')[0] == "User's Name: Nithiish"

    def test_internal_whitespace_collapsed(self):
        assert normalize_fact("User's    Name:   Nithiish")[0] == "User's Name: Nithiish"

    def test_valid_canonical_fact_passes_through(self):
        text = "User's college: PSG College of Technology"
        assert normalize_fact(text) == (text, "ok")

    def test_non_canonical_but_real_fact_kept(self):
        """A real statement that isn't in "User's X: Y" form is still information."""
        text = "My favorite programming language is Python."
        normalized, reason = normalize_fact(text)
        assert normalized == text
        assert reason == "ok"

    def test_is_valid_fact_predicate(self):
        assert is_valid_fact("User's college: PSG College of Technology") is True
        assert is_valid_fact("Here are the extracted facts:") is False


class TestDedupKey:
    def test_case_and_punctuation_insensitive(self):
        assert memory_hygiene._dedup_key("User's Name: Nithiish") == \
               memory_hygiene._dedup_key("users name nithiish")

    def test_bullet_variant_matches_after_normalization(self):
        a, _ = normalize_fact("* User's Name: Nithiish")
        b, _ = normalize_fact("User's name: Nithiish")
        assert memory_hygiene._dedup_key(a) == memory_hygiene._dedup_key(b)


class TestStoreIntegration:
    """memory.store() must refuse to write the shapes found in the audit."""

    def test_store_rejects_preamble(self, monkeypatch):
        import memory

        added = []
        monkeypatch.setattr(memory, "_get_collection",
                            lambda d: type("C", (), {"add": lambda self, **kw: added.append(kw)})())
        item_id = memory.store("Here are the extracted facts:", content_type="fact")
        assert item_id == ""
        assert added == []

    def test_store_normalizes_bullet_before_writing(self, monkeypatch):
        import memory

        added = []
        monkeypatch.setattr(memory, "_get_collection",
                            lambda d: type("C", (), {"add": lambda self, **kw: added.append(kw)})())
        item_id = memory.store("* User's Name: Nithiish", content_type="fact")
        assert item_id != ""
        assert added[0]["documents"] == ["User's Name: Nithiish"]

    def test_store_still_accepts_conversation_turns_unvalidated(self, monkeypatch):
        """Conversation turns are not facts and must not go through fact rules."""
        import memory

        added = []
        monkeypatch.setattr(memory, "_get_collection",
                            lambda d: type("C", (), {"add": lambda self, **kw: added.append(kw)})())
        item_id = memory.store("Here are the extracted facts:", content_type="conversation")
        assert item_id != ""
        assert len(added) == 1


class TestCleanupReport:
    def test_report_counts_and_samples(self):
        report = CleanupReport()
        report.note("llm_preamble", "Here are the extracted facts:")
        report.note("llm_preamble", "Here are the facts:")
        report.note("duplicate", "User's name: Nithiish")
        assert report.reasons["llm_preamble"] == 2
        assert report.reasons["duplicate"] == 1
        assert len(report.samples) == 3

    def test_samples_are_capped(self):
        report = CleanupReport()
        for i in range(40):
            report.note("duplicate", f"fact {i}")
        assert len(report.samples) <= 15
        assert report.reasons["duplicate"] == 40

    def test_to_dict_shape(self):
        report = CleanupReport(scanned=10, rejected=3, duplicates=2)
        payload = report.to_dict()
        assert payload["scanned"] == 10
        assert payload["rejected"] == 3
        assert payload["duplicates"] == 2


class TestCleanStore:
    """clean_store() against a fake collection, so no real data is touched."""

    class _FakeCollection:
        def __init__(self, rows):
            # rows: list of (id, document, content_type)
            self.rows = rows
            self.updated = None

        def get(self):
            return {
                "ids": [r[0] for r in self.rows],
                "documents": [r[1] for r in self.rows],
                "metadatas": [{"content_type": r[2]} for r in self.rows],
            }

        def update(self, ids, documents):
            self.updated = (ids, documents)

    def _patch(self, monkeypatch, rows):
        import memory

        fake = self._FakeCollection(rows)
        monkeypatch.setattr(memory, "_get_collection", lambda d: fake)
        deleted = []
        monkeypatch.setattr(memory, "delete_by_ids",
                            lambda ids, domain="personal": deleted.extend(ids))
        return fake, deleted

    def test_dry_run_deletes_nothing(self, monkeypatch):
        fake, deleted = self._patch(monkeypatch, [
            ("1", "Here are the extracted facts:", "fact"),
            ("2", "User's name: Nithiish", "fact"),
        ])
        report = memory_hygiene.clean_store(dry_run=True)
        assert len(report.deleted_ids) == 1
        assert deleted == []
        assert fake.updated is None

    def test_apply_deletes_junk(self, monkeypatch):
        fake, deleted = self._patch(monkeypatch, [
            ("1", "Here are the extracted facts:", "fact"),
            ("2", "User's name: Nithiish", "fact"),
        ])
        memory_hygiene.clean_store(dry_run=False)
        assert deleted == ["1"]

    def test_duplicates_removed_keeping_first(self, monkeypatch):
        fake, deleted = self._patch(monkeypatch, [
            ("1", "User's name: Nithiish", "fact"),
            ("2", "* User's Name: Nithiish", "fact"),
        ])
        memory_hygiene.clean_store(dry_run=False)
        assert deleted == ["2"]

    def test_salvageable_row_repaired_not_deleted(self, monkeypatch):
        fake, deleted = self._patch(monkeypatch, [
            ("1", "* User's Institution: PSG", "fact"),
        ])
        report = memory_hygiene.clean_store(dry_run=False)
        assert deleted == []
        assert fake.updated == (["1"], ["User's Institution: PSG"])
        assert len(report.repaired) == 1

    def test_conversations_kept_by_default(self, monkeypatch):
        fake, deleted = self._patch(monkeypatch, [
            ("1", "User: hello there", "conversation"),
        ])
        memory_hygiene.clean_store(dry_run=False)
        assert deleted == []

    def test_conversations_dropped_when_requested(self, monkeypatch):
        fake, deleted = self._patch(monkeypatch, [
            ("1", "User: hello there", "conversation"),
        ])
        memory_hygiene.clean_store(dry_run=False, drop_conversations=True)
        assert deleted == ["1"]

    def test_format_report_mentions_dry_run(self, monkeypatch):
        self._patch(monkeypatch, [("1", "Here are the extracted facts:", "fact")])
        report = memory_hygiene.clean_store(dry_run=True)
        rendered = memory_hygiene.format_cleanup_report(report, "personal", dry_run=True)
        assert "Dry run" in rendered
        assert "llm_preamble" in rendered
