"""Hybrid (dense + keyword) vs dense-only memory retrieval (ROADMAP F3).

Runs the production memory.retrieve_relevant() over evals/retrieval_fixture.json
plus near-duplicate distractor facts ("User's lab slot for X", "User's course
code for Y", ...), once with keyword candidates and once without, using the real
embedding model and cross-encoder in a throwaway in-memory Chroma collection.

Chroma's HNSW index is approximate, so dense-only numbers can move by a fact
between runs; the claim being checked is hybrid >= dense at every size.

Run: ./zedek-env/bin/python evals/bench_hybrid_retrieval.py
"""

import json
import logging
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chromadb  # noqa: E402

import memory  # noqa: E402
import reranker  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "retrieval_fixture.json")
SUBJECTS = ["Machine Learning", "Java", "Compiler Design", "Computer Networks", "Discrete Maths",
            "Probability", "Software Engineering", "Data Mining", "Cloud Computing", "Graph Theory",
            "Cryptography", "Web Technology", "Numerical Methods", "Linear Algebra", "Microprocessors"]
ATTRIBUTES = ["course code for", "assignment deadline for", "lab slot for", "faculty for",
              "internal marks in", "textbook for", "project topic in", "exam hall for",
              "credit count for", "tutorial time for", "seminar topic in", "reference book for",
              "quiz date for", "attendance in", "mini project in", "study group for",
              "notes source for", "doubt session for", "grade target in", "revision plan for",
              "mentor for", "online course for", "past paper source for", "lab record status in",
              "viva date for"]
VALUES = ["Monday 9am", "Dr. Priya", "Room 305", "42/50", "Tuesday", "Block F", "4 credits", "Dr. Kumar"]


def distractors(attribute_count: int, seed: int = 7) -> dict[str, str]:
    rng = random.Random(seed)
    facts = {}
    for a_index, attribute in enumerate(ATTRIBUTES[:attribute_count]):
        for subject in SUBJECTS:
            if attribute == "course code for":
                value = f"{rng.choice(['23', '22'])}{rng.choice(['XT', 'PT', 'XW', 'MA'])}{rng.randint(10, 99)}"
            else:
                value = rng.choice(VALUES)
            facts[f"d{len(facts):04d}"] = f"User's {attribute} {subject}: {value}"
    return facts


def run(facts: dict[str, str], queries: list[dict], keyword_k: int) -> dict:
    client = chromadb.EphemeralClient()
    name = f"bench_{len(facts)}_{keyword_k}"
    collection = client.get_or_create_collection(name, embedding_function=memory.embedding_function)
    collection.add(ids=list(facts), documents=list(facts.values()),
                   metadatas=[{"user_id": memory.DEFAULT_USER_ID, "content_type": "fact"}] * len(facts))

    original_collection, original_k = memory._get_collection, memory.KEYWORD_CANDIDATE_K
    memory._get_collection = lambda domain: collection
    memory.KEYWORD_CANDIDATE_K = keyword_k
    memory._keyword_indexes.clear()
    try:
        found = labelled = leaks = general = 0
        misses: list[str] = []
        started = time.perf_counter()
        for query in queries:
            kept = [item["id"] for item in memory.retrieve_relevant(query["q"], domain="personal", top_k=3)]
            if query["relevant"]:
                for fact_id in query["relevant"]:
                    labelled += 1
                    if fact_id in kept:
                        found += 1
                    else:
                        misses.append(query["q"])
            else:
                general += 1
                leaks += bool(kept)
        ms = (time.perf_counter() - started) * 1000 / len(queries)
    finally:
        memory._get_collection, memory.KEYWORD_CANDIDATE_K = original_collection, original_k
        memory._keyword_indexes.clear()
    return {"found": found, "labelled": labelled, "leaks": leaks, "general": general,
            "ms": ms, "misses": misses}


def main() -> int:
    logging.disable(logging.CRITICAL)  # here, not at import: tests import this module
    if reranker.score("warm up", ["a", "b"]) is None:
        print("Reranker model not installed — run setup.sh step 7.")
        return 2
    with open(FIXTURE, encoding="utf-8") as handle:
        data = json.load(handle)

    ok = True
    for attribute_count in (10, 25):
        facts = {**data["facts"], **distractors(attribute_count)}
        dense = run(facts, data["queries"], keyword_k=0)
        hybrid = run(facts, data["queries"], keyword_k=memory.KEYWORD_CANDIDATE_K)
        for label, r in (("dense only", dense), (f"dense + keyword {memory.KEYWORD_CANDIDATE_K}", hybrid)):
            print(f"store {len(facts):4d} | {label:18s} | recall {r['found']}/{r['labelled']} | "
                  f"leaks {r['leaks']}/{r['general']} | {r['ms']:.0f} ms/query | misses {r['misses']}")
        ok &= hybrid["found"] >= dense["found"] and hybrid["leaks"] <= dense["leaks"]
    print(f"hybrid >= dense at every size: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
