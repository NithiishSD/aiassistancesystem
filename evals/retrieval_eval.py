"""Retrieval eval: recall, ranking and leaks per embedding model (ROADMAP A4 / F7).

Runs evals/retrieval_fixture.json ('queries' + 'paraphrases') through the
production memory.retrieve_relevant() in a throwaway in-memory Chroma
collection, at several store sizes (the fixture plus near-duplicate
distractor facts), once per embedding model. Nothing here touches the live
store.

Reported per model and size:
  pool    labelled facts that reach the dense top-20 (what the embedding controls)
  recall  labelled facts returned in the final top 3 (dense + keyword + reranker)
  first   questions whose first result is a labelled fact
  leaks   general-knowledge questions that returned any fact
  ms      mean time per question
  +profile  labelled facts present once the core profile block is added

With --reranker, the embedding stays the installed one and each cross-encoder
is scored at every relevance threshold instead, because each model needs its
own gate: the table shows recall and leaks per threshold.

Usage:
    python evals/retrieval_eval.py                                  # the installed model
    python evals/retrieval_eval.py --model NAME=PATH[::QUERY_PREFIX] ...
    python evals/retrieval_eval.py --reranker NAME=PATH ...
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "evals"))

import chromadb  # noqa: E402

import memory  # noqa: E402
import reranker  # noqa: E402
from bench_hybrid_retrieval import distractors  # noqa: E402

FIXTURE = os.path.join(_ROOT, "evals", "retrieval_fixture.json")
DISTRACTOR_ATTRIBUTES = (0, 10, 25)   # store sizes 37, 187, 412


class Embedder:
    """A sentence-transformers model with an optional query-side prefix."""

    def __init__(self, path: str, query_prefix: str = "") -> None:
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(path, device="cpu")
        self.query_prefix = query_prefix

    def documents(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(texts, normalize_embeddings=True).tolist()

    def __call__(self, input: list[str]) -> list[list[float]]:  # Chroma calls this for queries only
        return self.model.encode([self.query_prefix + text for text in input],
                                 normalize_embeddings=True).tolist()


def evaluate(embedder: Embedder, facts: dict[str, str], queries: list[dict]) -> dict:
    client = chromadb.EphemeralClient()
    collection = client.get_or_create_collection(
        f"reval_{abs(hash((id(embedder), len(facts))))}", embedding_function=embedder)
    ids, documents = list(facts), list(facts.values())
    collection.add(ids=ids, documents=documents, embeddings=embedder.documents(documents),
                   metadatas=[{"user_id": memory.DEFAULT_USER_ID, "content_type": "fact"}] * len(ids))

    original = memory._get_collection
    memory._get_collection = lambda domain: collection
    memory._keyword_indexes.clear()
    import user_profile

    profile = {fact["id"] for fact in user_profile.select(
        [{"text": text, "metadata": {}, "id": fact_id} for fact_id, text in facts.items()])}
    out = {"pool": 0, "recall": 0, "labelled": 0, "first": 0, "with_relevant": 0,
           "leaks": 0, "general": 0, "misses": [], "covered": 0, "profile_size": len(profile),
           "profile_sent_to_general": 0}
    try:
        started = time.perf_counter()
        for query in queries:
            pool = {item["id"] for item in memory.retrieve(
                query["q"], content_type="fact", top_k=min(memory.DEFAULT_CANDIDATE_K, len(ids)))}
            kept = [item["id"] for item in memory.retrieve_relevant(query["q"], top_k=3)]
            about_user = user_profile.refers_to_user(query["q"])
            if not query["relevant"]:
                out["general"] += 1
                out["leaks"] += bool(kept)
                out["profile_sent_to_general"] += about_user
                continue
            out["with_relevant"] += 1
            out["first"] += bool(kept and kept[0] in query["relevant"])
            for fact_id in query["relevant"]:
                out["labelled"] += 1
                out["pool"] += fact_id in pool
                out["covered"] += fact_id in kept or (about_user and fact_id in profile)
                if fact_id in kept:
                    out["recall"] += 1
                else:
                    out["misses"].append(query["q"])
        out["ms"] = (time.perf_counter() - started) * 1000 / len(queries)
    finally:
        memory._get_collection = original
        memory._keyword_indexes.clear()
    return out


THRESHOLDS = (0.01, 0.03, 0.1, 0.3, 0.5, 0.51, 0.52, 0.55, 0.6, 0.65, 0.7)


def sweep(embedder: Embedder, facts: dict[str, str], queries: list[dict]) -> dict:
    """Recall and leaks of the current reranker at each relevance threshold."""
    client = chromadb.EphemeralClient()
    collection = client.get_or_create_collection(
        f"rsweep_{abs(hash((reranker.MODEL_DIR, len(facts))))}", embedding_function=embedder)
    ids, documents = list(facts), list(facts.values())
    collection.add(ids=ids, documents=documents, embeddings=embedder.documents(documents),
                   metadatas=[{"user_id": memory.DEFAULT_USER_ID, "content_type": "fact"}] * len(ids))
    original = memory._get_collection
    memory._get_collection = lambda domain: collection
    memory._keyword_indexes.clear()
    try:
        started = time.perf_counter()
        scored = [(q, [(item["id"], item["score"]) for item in
                       memory.retrieve_relevant(q["q"], top_k=3, min_score=0.0)]) for q in queries]
        ms = (time.perf_counter() - started) * 1000 / len(queries)
    finally:
        memory._get_collection = original
        memory._keyword_indexes.clear()
    labelled = sum(len(q["relevant"]) for q, _ in scored)
    general = sum(1 for q, _ in scored if not q["relevant"])
    rows = {}
    for t in THRESHOLDS:
        found = sum(1 for q, items in scored for r in q["relevant"] if any(i == r and s >= t for i, s in items))
        leaks = sum(1 for q, items in scored if not q["relevant"] and any(s >= t for _, s in items))
        rows[t] = (found, leaks)
    return {"rows": rows, "labelled": labelled, "general": general, "ms": ms}


def use_reranker(path: str) -> None:
    reranker.MODEL_DIR = path
    reranker._model, reranker._load_failed = None, False


def parse_model(spec: str) -> tuple[str, str, str]:
    name, _, rest = spec.partition("=")
    path, _, prefix = rest.partition("::")
    return name, path, prefix


def main(argv: list[str] | None = None) -> int:
    logging.disable(logging.CRITICAL)  # here, not at import: tests import this module
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", action="append", default=[],
                        help="NAME=PATH[::QUERY_PREFIX]; default is the installed memory model")
    parser.add_argument("--reranker", action="append", default=[],
                        help="NAME=PATH; sweep relevance thresholds for each cross-encoder")
    parser.add_argument("--misses", action="store_true", help="print the questions each model missed")
    args = parser.parse_args(argv)

    if reranker.score("warm up", ["a", "b"]) is None:
        print("Reranker model not installed — run setup.sh step 7.")
        return 2
    with open(FIXTURE, encoding="utf-8") as handle:
        data = json.load(handle)
    queries = data["queries"] + data.get("paraphrases", [])

    default_path = (memory.EMBEDDING_MODEL_PATH
                    if os.path.isfile(os.path.join(memory.EMBEDDING_MODEL_PATH, "config.json"))
                    else memory.EMBEDDING_MODEL_ID)
    models = [parse_model(spec) for spec in args.model] or [("all-MiniLM-L6-v2", default_path, "")]

    if args.reranker:
        embedder = Embedder(default_path)
        installed = reranker.MODEL_DIR
        print(f"{len(queries)} questions; cells are recall/{sum(len(q['relevant']) for q in queries)} "
              f"and leaks/{sum(1 for q in queries if not q['relevant'])} at each relevance threshold\n")
        print(f"{'reranker':26} {'store':>5} {'ms':>5}  " + " ".join(f"{t:>7}" for t in THRESHOLDS))
        try:
            for name, path, _ in (parse_model(spec) for spec in args.reranker):
                use_reranker(path)
                for attribute_count in DISTRACTOR_ATTRIBUTES:
                    facts = {**data["facts"], **distractors(attribute_count)}
                    r = sweep(embedder, facts, queries)
                    cells = " ".join(f"{found:>4}/{leaks:<2}" for found, leaks in r["rows"].values())
                    print(f"{name:26} {len(facts):>5} {r['ms']:>5.0f}  {cells}")
        finally:
            use_reranker(installed)
        return 0

    print(f"{len(queries)} questions ({sum(1 for q in queries if not q['relevant'])} general)\n")
    print("+profile = labelled facts in the answer context once the core profile block (ROADMAP F6) is added\n")
    print(f"{'model':24} {'store':>5}  {'pool':>7} {'recall':>7} {'+profile':>8} {'first':>7} {'leaks':>6} {'ms':>5}")
    for name, path, prefix in models:
        embedder = Embedder(path, prefix)
        for attribute_count in DISTRACTOR_ATTRIBUTES:
            facts = {**data["facts"], **distractors(attribute_count)}
            r = evaluate(embedder, facts, queries)
            print(f"{name:24} {len(facts):>5}  {r['pool']:>3}/{r['labelled']:<3} {r['recall']:>3}/{r['labelled']:<3} "
                  f"{r['covered']:>4}/{r['labelled']:<3} {r['first']:>3}/{r['with_relevant']:<3} {r['leaks']:>2}/{r['general']:<3} {r['ms']:>5.0f}")
            if args.misses and r["misses"]:
                print(f"{'':24}        missed: {sorted(set(r['misses']))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
