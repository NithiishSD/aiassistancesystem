"""Retrieval quality on the labelled fixture (OpenSpec change: add-memory-reranker).

Runs the real embedding model and the real cross-encoder against
evals/retrieval_fixture.json in a throwaway in-memory Chroma collection, and
asserts the memory-retrieval spec:
  - every labelled-relevant fact is returned (recall = 1.0 at top_k=3), and
  - general-knowledge queries with no relevant fact return nothing.

This is the seed of the ROADMAP A4 retrieval eval set. When a real retrieval
miss happens, add it to the fixture and re-run this test before re-tuning
memory.RELEVANCE_MIN_SCORE.
"""

import json
import os

import pytest

import memory
import reranker

FIXTURE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "evals", "retrieval_fixture.json")

pytestmark = pytest.mark.skipif(
    not os.path.isfile(os.path.join(reranker.MODEL_DIR, "config.json")),
    reason="reranker model not installed (run setup.sh step 7)",
)


@pytest.fixture(scope="module")
def fixture_results():
    import chromadb

    with open(FIXTURE, encoding="utf-8") as handle:
        data = json.load(handle)

    client = chromadb.EphemeralClient()
    collection = client.get_or_create_collection(
        "retrieval_fixture", embedding_function=memory.embedding_function)
    collection.add(
        ids=list(data["facts"]),
        documents=list(data["facts"].values()),
        metadatas=[{"user_id": memory.DEFAULT_USER_ID, "content_type": "fact"}] * len(data["facts"]),
    )

    original = memory._get_collection
    memory._get_collection = lambda domain: collection
    try:
        results = []
        for query in data["queries"]:
            kept = memory.retrieve_relevant(query["q"], domain="personal", top_k=3)
            results.append((query, [item["id"] for item in kept]))
    finally:
        memory._get_collection = original

    labelled = sum(len(q["relevant"]) for q, _ in results)
    found = sum(1 for q, ids in results for r in q["relevant"] if r in ids)
    general = [(q, ids) for q, ids in results if not q["relevant"]]
    leaks = [(q["q"], ids) for q, ids in general if ids]
    first = sum(1 for q, ids in results if q["relevant"] and ids and ids[0] in q["relevant"])
    with_rel = sum(1 for q, _ in results if q["relevant"])

    print(f"\nretrieval fixture @ threshold {memory.RELEVANCE_MIN_SCORE}: "
          f"recall {found}/{labelled}, relevant-first {first}/{with_rel}, "
          f"general-question leaks {len(leaks)}/{len(general)}")
    return {"results": results, "labelled": labelled, "found": found, "leaks": leaks}


def test_every_labelled_fact_is_returned(fixture_results):
    missed = [(q["q"], r) for q, ids in fixture_results["results"]
              for r in q["relevant"] if r not in ids]
    assert not missed, f"relevant facts not returned: {missed}"


def test_general_questions_return_no_personal_facts(fixture_results):
    assert not fixture_results["leaks"], f"general questions leaked facts: {fixture_results['leaks']}"


def test_the_1_06_case_is_found(fixture_results):
    """The live-store case that motivated this change."""
    for query, ids in fixture_results["results"]:
        if query["q"] == "what college do I study at":
            assert ids and ids[0] == "f02"
            return
    pytest.fail("fixture is missing the 'what college do I study at' query")
