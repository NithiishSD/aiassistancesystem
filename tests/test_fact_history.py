"""Facts stop being current without being lost (OpenSpec change: bitemporal-facts).

Runs on an in-memory Chroma collection with a trivial embedding, never the
live store. Retrieval quality with the real models is covered by
tests/test_retrieval_fixture.py.
"""

import hashlib
import os
import subprocess
import sys
import uuid
from unittest.mock import patch

import chromadb
import pytest

import llm_schemas
import memory
import memory_hygiene

USER = memory.DEFAULT_USER_ID
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _HashEmbedding:
    def __call__(self, input):
        return [[b / 255 for b in hashlib.sha256(text.encode()).digest()[:16]] for text in input]


@pytest.fixture
def collection(monkeypatch):
    client = chromadb.EphemeralClient()
    col = client.get_or_create_collection(f"hist_{uuid.uuid4().hex[:8]}", embedding_function=_HashEmbedding())
    monkeypatch.setattr(memory, "_get_collection", lambda domain: col)
    memory._keyword_indexes.clear()
    memory._index_versions.clear()
    yield col
    memory._keyword_indexes.clear()


def _ids(items):
    return {item["id"] for item in items}


def _meta(col, item_id):
    return col.get(ids=[item_id])["metadatas"][0]


class TestValidity:
    def test_new_fact_records_valid_at(self, collection):
        item_id = memory.store("User's college: Northwind Institute")
        meta = _meta(collection, item_id)
        assert meta["valid_at"] == meta["timestamp"] and "invalidated" not in meta

    def test_conversation_rows_carry_no_validity(self, collection):
        item_id = memory.store("user asked about disk space", content_type="conversation")
        assert "valid_at" not in _meta(collection, item_id)

    def test_row_without_validity_fields_is_current(self, collection):
        # Pins Chroma's `$ne` matching rows that lack the key: every fact
        # stored before this change depends on it.
        collection.add(ids=["legacy"], documents=["User's city: Rivertown"],
                       metadatas=[{"user_id": USER, "content_type": "fact"}])
        assert _ids(memory.retrieve("city", content_type="fact")) == {"legacy"}
        assert _ids(memory.retrieve("city")) == {"legacy"}
        assert _ids(memory._keyword_candidates("Rivertown city", "personal", USER, "fact")) == {"legacy"}


class TestInvalidate:
    def test_sets_fields_and_keeps_the_row(self, collection):
        old = memory.store("User's college: Northwind Institute")
        assert memory.invalidate([old], superseded_by="new-id") == [old]
        meta = _meta(collection, old)
        assert meta["invalidated"] is True and meta["superseded_by"] == "new-id"
        assert meta["invalid_at"] >= meta["valid_at"]
        assert meta["user_id"] == USER and meta["content_type"] == "fact"
        assert collection.get(ids=[old])["documents"] == ["User's college: Northwind Institute"]

    def test_retraction_has_no_superseded_by(self, collection):
        old = memory.store("User's city: Rivertown")
        memory.invalidate([old])
        assert "superseded_by" not in _meta(collection, old)

    def test_other_users_row_untouched(self, collection):
        other = memory.store("User's city: Rivertown", user_id="someone-else")
        assert memory.invalidate([other]) == []
        assert "invalidated" not in _meta(collection, other)

    def test_second_call_keeps_first_stamp(self, collection):
        old = memory.store("User's city: Rivertown")
        memory.invalidate([old])
        first = _meta(collection, old)["invalid_at"]
        assert memory.invalidate([old], superseded_by="later") == []
        meta = _meta(collection, old)
        assert meta["invalid_at"] == first and "superseded_by" not in meta

    def test_empty_ids(self, collection):
        assert memory.invalidate([]) == []


class TestRetrievalHidesHistory:
    def test_gone_from_dense_and_keyword_on_next_call(self, collection):
        old = memory.store("User's college: Northwind Institute")
        new = memory.store("User's college: Southgate University")
        assert old in _ids(memory._keyword_candidates("Northwind college", "personal", USER, "fact"))
        memory.invalidate([old], superseded_by=new)
        assert _ids(memory.retrieve("college", content_type="fact", top_k=5)) == {new}
        assert old not in _ids(memory._keyword_candidates("Northwind college", "personal", USER, "fact"))
        assert _ids(memory.retrieve("college", content_type="fact", top_k=5,
                                    include_invalidated=True)) == {old, new}

    def test_gone_from_relevance_ranked_results(self, collection):
        old = memory.store("User's college: Northwind Institute")
        new = memory.store("User's college: Southgate University")
        memory.invalidate([old], superseded_by=new)
        with patch("reranker.score", side_effect=lambda q, texts: [0.9] * len(texts)):
            assert _ids(memory.retrieve_relevant("what college do I study at")) == {new}

    def test_history_does_not_shrink_the_candidate_pool(self, collection):
        stale = [memory.store(f"User's old lab slot number {n}: Monday") for n in range(25)]
        memory.invalidate(stale)
        current = memory.store("User's hobby: cricket")
        assert _ids(memory.retrieve("lab slot", content_type="fact", top_k=20)) == {current}


class TestHistoryView:
    def test_labels(self, collection):
        old = memory.store("User's college: Northwind Institute")
        new = memory.store("User's college: Southgate University")
        gone = memory.store("User's city: Rivertown")
        memory.invalidate([old], superseded_by=new)
        memory.invalidate([gone])
        status = {item["id"]: item["status"] for item in memory.history("college")}
        assert status == {old: "superseded", new: "current", gone: "retracted"}
        text = memory.format_history(memory.history("college"))
        assert "[superseded] User's college: Northwind Institute" in text
        assert f"replaced by {new}" in text and "until " in text

    def test_no_matches(self, collection):
        assert memory.format_history(memory.history("anything")) == "No matching facts."

    def test_cli_history_writes_nothing(self, tmp_path):
        env = {**os.environ, "ZEDEK_CHROMA_PATH": str(tmp_path / "store")}
        run = subprocess.run([sys.executable, "memory.py", "--history", "college"],
                             cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
        assert run.returncode == 0, run.stderr[-500:]
        assert "No matching facts." in run.stdout
        probe = chromadb.PersistentClient(path=str(tmp_path / "store"))
        assert all(c.count() == 0 for c in probe.list_collections())


class TestHygiene:
    def test_reinstated_fact_survives_cleanup(self, collection):
        old = memory.store("User's city: Rivertown")
        memory.invalidate([old])
        again = memory.store("User's city: Rivertown")
        report = memory_hygiene.clean_store(dry_run=False)
        assert report.deleted_ids == [] and report.repaired == []
        assert set(collection.get()["ids"]) == {old, again}
        assert _meta(collection, old)["invalidated"] is True

    def test_purge_dry_run_deletes_nothing(self, collection):
        old = memory.store("User's city: Rivertown")
        memory.invalidate([old])
        assert memory_hygiene.purge_invalidated() == [old]
        assert collection.count() == 1

    def test_purge_applied_removes_only_history(self, collection):
        old = memory.store("User's city: Rivertown")
        keep = memory.store("User's hobby: cricket")
        memory.invalidate([old])
        assert memory_hygiene.purge_invalidated(dry_run=False) == [old]
        assert collection.get()["ids"] == [keep]
        assert memory_hygiene.purge_invalidated(dry_run=False) == []


class TestCorrection:
    """_handle_correction against a real (in-memory) store; only the model is faked."""

    def _correct(self, text, index, corrected):
        import orchestrator

        data = llm_schemas.FactCorrection(index=index, corrected_fact=corrected)
        with patch.object(orchestrator.llm_provider, "generate_structured", return_value={"data": data}), \
             patch.object(orchestrator, "_last_assistant_question", return_value=None):
            return orchestrator._handle_correction(text, "personal")

    def _index_of(self, query_text, item_id):
        return [c["id"] for c in memory.retrieve(query_text, content_type="fact", top_k=4)].index(item_id)

    def test_correction_supersedes(self, collection):
        old = memory.store("User's college: Northwind Institute")
        said = "actually my college is Southgate University"
        reply = self._correct(said, self._index_of(said, old), "User's college: Southgate University")
        meta = _meta(collection, old)
        current = memory.retrieve("college", content_type="fact")
        assert meta["invalidated"] is True and meta["superseded_by"] == current[0]["id"]
        assert [c["text"] for c in current] == ["User's college: Southgate University"]
        assert "updated" in reply and "kept as history" in reply

    def test_rejected_replacement_changes_nothing(self, collection):
        old = memory.store("User's college: Northwind Institute")
        said = "no that's wrong"
        reply = self._correct(said, self._index_of(said, old), "User's college: not stated")
        assert collection.count() == 1 and "invalidated" not in _meta(collection, old)
        assert "nothing was changed" in reply

    def test_outdated_is_kept_as_history(self, collection):
        old = memory.store("User's city: Rivertown")
        said = "that's outdated"
        reply = self._correct(said, self._index_of(said, old), None)
        assert _meta(collection, old)["invalidated"] is True
        assert "no longer true" in reply and "deleted" not in reply

    def test_delete_request_for_real_fact_keeps_history(self, collection):
        old = memory.store("User's city: Rivertown")
        said = "delete that from memory"
        reply = self._correct(said, self._index_of(said, old), None)
        assert collection.count() == 1 and _meta(collection, old)["invalidated"] is True
        assert memory.retrieve("city", content_type="fact") == []
        assert "Kept as history" in reply and "--purge-invalidated" in reply

    def test_junk_fact_is_deleted(self, collection):
        collection.add(ids=["junk"], documents=["User's college: not stated"],
                       metadatas=[{"user_id": USER, "content_type": "fact"}])
        reply = self._correct("that's wrong, remove it", 0, None)
        assert collection.count() == 0
        assert "permanently deleted" in reply

    def test_same_text_replacement_keeps_no_history(self, collection):
        old = memory.store("User's name: alex")
        said = "you got my name wrong"
        reply = self._correct(said, self._index_of(said, old), "User's Name: Alex.")
        assert collection.get()["documents"] == ["User's Name: Alex."]
        assert "permanently deleted" in reply

    def test_superseded_fact_is_not_offered_again(self, collection):
        old = memory.store("User's college: Northwind Institute")
        memory.invalidate([old])
        reply = self._correct("no, that college is wrong", 0, None)
        assert "don't have a stored fact" in reply
