"""Keyword (BM25) candidates beside dense retrieval (OpenSpec change: add-hybrid-memory-search).

Index mechanics run on an in-memory Chroma collection with a trivial embedding
so they're fast; retrieval quality with the real models is covered by
tests/test_retrieval_fixture.py and evals/bench_hybrid_retrieval.py.
"""

import hashlib
import uuid
from unittest.mock import patch

import chromadb
import pytest

import memory

USER = memory.DEFAULT_USER_ID


class _HashEmbedding:
    def __call__(self, input):
        return [[b / 255 for b in hashlib.sha256(text.encode()).digest()[:16]] for text in input]


@pytest.fixture
def collection(monkeypatch):
    client = chromadb.EphemeralClient()
    col = client.get_or_create_collection(f"kw_{uuid.uuid4().hex[:8]}", embedding_function=_HashEmbedding())
    monkeypatch.setattr(memory, "_get_collection", lambda domain: col)
    memory._keyword_indexes.clear()
    memory._index_versions.clear()
    monkeypatch.setattr(memory, "_keyword_unavailable_logged", False)
    yield col
    memory._keyword_indexes.clear()


def _add(col, doc, user=USER, content_type="fact", item_id=None):
    item_id = item_id or uuid.uuid4().hex
    col.add(ids=[item_id], documents=[doc], metadatas=[{"user_id": user, "content_type": content_type}])
    return item_id


def _kw(query, content_type="fact", user=USER, k=None):
    return memory._keyword_candidates(query, "personal", user, content_type, k=k)


class TestKeywordIndex:
    def test_hits_shaped_like_retrieve_items(self, collection):
        target = _add(collection, "User's course code for Database Systems: 23XT51")
        _add(collection, "User's hobby: cricket")
        hits = _kw("which course is 23XT51")
        assert [h["id"] for h in hits] == [target]
        assert hits[0]["distance"] is None
        assert hits[0]["text"].endswith("23XT51") and hits[0]["metadata"]["user_id"] == USER

    def test_no_overlap_no_hits(self, collection):
        _add(collection, "User's hobby: cricket")
        assert _kw("photosynthesis") == []

    def test_stopword_only_query(self, collection):
        _add(collection, "User's hobby: cricket")
        assert _kw("the") == []

    def test_empty_scope(self, collection):
        assert _kw("anything") == []

    def test_k_limits_and_zero_disables(self, collection):
        for i in range(5):
            _add(collection, f"User's lab slot {i}: Monday")
        assert len(_kw("lab slot monday", k=2)) == 2
        assert _kw("lab slot monday", k=0) == []

    def test_other_users_and_content_types_excluded(self, collection):
        mine = _add(collection, "User's bus route: 17B")
        _add(collection, "User's bus route: 17B", user="someone_else")
        _add(collection, "we talked about bus route 17B", content_type="conversation")
        assert [h["id"] for h in _kw("bus 17B")] == [mine]

    def test_store_invalidates(self, collection):
        _add(collection, "User's hobby: cricket")
        assert _kw("roll number") == []
        new_id = memory.store("User's roll number: 22PT14", domain="personal")
        assert new_id and [h["id"] for h in _kw("roll number 22PT14")] == [new_id]

    def test_delete_invalidates(self, collection):
        doomed = _add(collection, "User's hostel room: C-217")
        assert _kw("hostel room")
        memory.delete_by_ids([doomed], domain="personal")
        assert _kw("hostel room") == []

    def test_direct_collection_write_invalidates(self, collection):
        _add(collection, "User's hobby: cricket")
        assert _kw("github") == []
        _add(collection, "User's GitHub username: nsd-dev42")  # bypasses store()
        assert len(_kw("github username")) == 1

    def test_index_reused_when_unchanged(self, collection):
        _add(collection, "User's hobby: cricket")
        _kw("cricket")
        with patch("bm25s.BM25") as rebuild:
            _kw("cricket")
        rebuild.assert_not_called()

    def test_failure_degrades_and_logs_once(self, collection):
        _add(collection, "User's hobby: cricket")
        with patch("bm25s.BM25", side_effect=RuntimeError("boom")), \
             patch.object(memory.log, "info") as info:
            assert _kw("cricket") == []
            assert _kw("cricket") == []
        events = [c.args[0] for c in info.call_args_list]
        assert events.count("memory_keyword_index_unavailable") == 1


# ── candidate union in retrieve_relevant ────────────────────────────────────

def _item(item_id, text, distance=0.5):
    return {"id": item_id, "text": text, "metadata": {}, "distance": distance}


class TestCandidateUnion:
    def test_keyword_only_candidate_reaches_reranker(self):
        dense = [_item("a", "User's hobby: cricket"), _item("b", "User's editor: VS Code")]
        keyword = [_item("b", "User's editor: VS Code", None), _item("c", "User's roll number: 22PT14", None)]
        seen = {}

        def score(query, texts):
            seen["texts"] = list(texts)
            return [0.9 if "22PT14" in t else 0.0 for t in texts]

        with patch.object(memory, "retrieve", return_value=dense), \
             patch.object(memory, "_keyword_candidates", return_value=keyword), \
             patch("reranker.score", side_effect=score), \
             patch.object(memory.log, "info") as info:
            kept = memory.retrieve_relevant("is 22PT14 my roll number")
        assert seen["texts"] == ["User's hobby: cricket", "User's editor: VS Code", "User's roll number: 22PT14"]
        assert [k["id"] for k in kept] == ["c"]
        extra = [c.kwargs["extra"] for c in info.call_args_list if c.args[0] == "memory_retrieved_relevant"][0]
        assert extra["keyword_added"] == 1 and extra["candidates"] == 3

    def test_relevance_gate_applies_to_keyword_hits(self):
        keyword = [_item("c", "User's hobby: Linux kernel hacking", None)]
        with patch.object(memory, "retrieve", return_value=[]), \
             patch.object(memory, "_keyword_candidates", return_value=keyword), \
             patch("reranker.score", return_value=[0.001]):
            assert memory.retrieve_relevant("who invented Linux") == []

    def test_reranker_unavailable_uses_dense_only(self):
        dense = [_item("a", "User's hobby: cricket", 0.4)]
        keyword = [_item("c", "User's roll number: 22PT14", None)]
        with patch.object(memory, "retrieve", return_value=dense), \
             patch.object(memory, "_keyword_candidates", return_value=keyword), \
             patch("reranker.score", return_value=None):
            assert [k["id"] for k in memory.retrieve_relevant("roll number")] == ["a"]

    def test_nothing_anywhere(self):
        with patch.object(memory, "retrieve", return_value=[]), \
             patch.object(memory, "_keyword_candidates", return_value=[]), \
             patch("reranker.score") as score:
            assert memory.retrieve_relevant("x") == []
        score.assert_not_called()
