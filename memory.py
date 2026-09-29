"""
Phase 5: Memory module (ChromaDB), scoped by user_id and domain from day one.

Even with a single user right now, every stored item is tagged with a
user_id and domain so retrieval can be correctly restricted once a second
person is ever enrolled (per the multi-user design) — retrofitting this
scoping after data already exists would be far more painful than building
it in now.

Two content types share the same collections, distinguished by metadata:
  - "fact"         — something the user told Zedek to remember directly
  - "conversation" — a turn from a past chat

Domains: "personal", "academic" (kept as separate ChromaDB collections so
academic retrieval never surfaces personal context and vice versa).
"""

import os
import uuid
import time
import chromadb
from chromadb.config import Settings
from semantic_router.encoders import HuggingFaceEncoder
import memory_hygiene
from zedek_logger import get_logger

log = get_logger("memory")

CHROMA_PATH = "./chroma_db"
DEFAULT_USER_ID = "nithiish"  # the owner; multi-user enrollment updates this later
EMBEDDING_MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "models", "all-MiniLM-L6-v2")
ALLOWED_DOMAINS = {"personal", "academic"}
ALLOWED_CONTENT_TYPES = {"fact", "conversation"}

os.environ.setdefault("HF_HUB_OFFLINE", "1")

client = chromadb.PersistentClient(path=CHROMA_PATH, settings=Settings(anonymized_telemetry=False))


class _ChromaEmbeddingFunction:
    """Adapt semantic-router's local encoder to Chroma's strict call API."""

    def __init__(self) -> None:
        model_name = (EMBEDDING_MODEL_PATH
                      if os.path.isfile(os.path.join(EMBEDDING_MODEL_PATH, "config.json"))
                      else EMBEDDING_MODEL_ID)
        self._encoder = HuggingFaceEncoder(name=model_name, device="cpu")

    def __call__(self, input: list[str]) -> list[list[float]]:
        return self._encoder(input)


embedding_function = _ChromaEmbeddingFunction()

_collections = {}


def _get_collection(domain: str):
    """Returns (creating if needed) the ChromaDB collection for a domain."""
    _validate_domain(domain)
    if domain not in _collections:
        _collections[domain] = client.get_or_create_collection(
            name=f"zedek_{domain}", embedding_function=embedding_function
        )
        log.info("collection_ready", extra={"domain": domain})
    return _collections[domain]


def _validate_domain(domain: str) -> None:
    if domain not in ALLOWED_DOMAINS:
        raise ValueError(f"Unknown domain '{domain}' — must be 'personal' or 'academic'.")


def _validate_user_id(user_id: str) -> None:
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("user_id must be a non-empty string.")


def _validate_content_type(content_type: str) -> None:
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise ValueError("content_type must be 'fact' or 'conversation'.")


def store(text: str, domain: str = "personal", content_type: str = "fact",
          user_id: str = DEFAULT_USER_ID, extra_metadata: dict | None = None) -> str:
    """
    Stores a piece of text (a fact or a conversation turn) in memory.
    Returns the generated item ID, or "" when the item was rejected.

    Facts pass through memory_hygiene.normalize_fact() first. An audit of this
    store found ~half of its 135 "facts" were LLM preamble lines, unstripped
    markdown bullets, duplicates, or null values — all of which semantic search
    happily returned as though they were ground truth. Rejecting them here is
    what stops that recurring; memory_hygiene.clean_store() handles the rows
    that were written before this gate existed.
    """
    _validate_domain(domain)
    _validate_user_id(user_id)
    _validate_content_type(content_type)

    if content_type == "fact":
        normalized, reason = memory_hygiene.normalize_fact(text)
        if normalized is None:
            log.info("memory_store_rejected", extra={
                "domain": domain, "reason": reason, "text": (text or "")[:120],
            })
            return ""
        text = normalized

    collection = _get_collection(domain)
    item_id = str(uuid.uuid4())

    metadata = {
        "user_id": user_id,
        "content_type": content_type,
        "timestamp": time.time(),
    }
    if extra_metadata:
        reserved_keys = set(metadata)
        metadata.update({key: value for key, value in extra_metadata.items()
                         if key not in reserved_keys})

    collection.add(documents=[text], ids=[item_id], metadatas=[metadata])
    log.info("memory_stored", extra={"domain": domain, "content_type": content_type,
                                       "user_id": user_id, "item_id": item_id})
    return item_id


def retrieve(query: str, domain: str = "personal", user_id: str = DEFAULT_USER_ID,
             content_type: str | None = None, top_k: int = 5) -> list[dict]:
    """
    Semantic search over stored memory, restricted to this user_id and domain.
    Optionally filter further by content_type ("fact" or "conversation").
    """
    _validate_domain(domain)
    _validate_user_id(user_id)
    if not isinstance(top_k, int) or top_k < 1:
        raise ValueError("top_k must be a positive integer.")
    if content_type:
        _validate_content_type(content_type)

    collection = _get_collection(domain)

    where = {"user_id": user_id}
    if content_type:
        where = {"$and": [{"user_id": user_id}, {"content_type": content_type}]}

    results = collection.query(query_texts=[query], n_results=top_k, where=where)

    items = []
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    ids = results.get("ids", [[]])[0]
    # Chroma returns a distance per hit; surfacing it lets callers judge whether
    # a match is actually relevant. Semantic search always returns its top_k,
    # however poor the match, so without this every caller treats junk as a hit.
    # Lower is closer. Missing distances default to 0.0 (treated as relevant).
    distances = (results.get("distances") or [[]])[0]
    for index, (doc, meta, item_id) in enumerate(zip(docs, metas, ids)):
        distance = distances[index] if index < len(distances) else 0.0
        items.append({"text": doc, "metadata": meta, "id": item_id, "distance": distance})

    log.info("memory_retrieved", extra={"domain": domain, "user_id": user_id,
                                          "query": query, "results_found": len(items)})
    return items


# Relevance gate for retrieve_relevant(): sigmoid of the cross-encoder logit,
# scored against the third-person query. Tuned on evals/retrieval_fixture.json:
# 0.025-0.04 all gave 15/15 recall with 0/5 general-question leaks; 0.03 sits
# inside that band. Re-check with tests/test_retrieval_fixture.py when the fixture grows.
RELEVANCE_MIN_SCORE = 0.03

# Facts are stored as "User's X: Y" but questions arrive as "where do I live".
# The cross-encoder doesn't bridge "I/my" to "User's", so correct facts scored
# near zero until the query was rewritten into the same person as the facts.
_THIRD_PERSON_RULES = [
    (" am i ", " is the user "),
    (" do i ", " does the user "),
    (" did i ", " did the user "),
    (" have i ", " has the user "),
    (" i am ", " the user is "),
    (" i'm ", " the user is "),
    (" my ", " the user's "),
    (" mine ", " the user's "),
    (" me ", " the user "),
    (" i ", " the user "),
]


def _third_person_query(query: str) -> str:
    """Rewrite a first-person question to match how facts are stored."""
    text = " " + " ".join((query or "").lower().split()) + " "
    for first_person, third_person in _THIRD_PERSON_RULES:
        text = text.replace(first_person, third_person)
    return text.strip()
# Used only when the reranker is unavailable. Not 1.0: the correct answer to
# "what college do I study at" measured 1.06 on the live store, while clearly
# unrelated facts measured from 1.40 up.
FALLBACK_MAX_DISTANCE = 1.3
DEFAULT_CANDIDATE_K = 20


def retrieve_relevant(query: str, domain: str = "personal", content_type: str = "fact",
                      top_k: int = 3, candidate_k: int = DEFAULT_CANDIDATE_K,
                      min_score: float | None = None,
                      user_id: str = DEFAULT_USER_ID) -> list[dict]:
    """Facts relevant to `query`, most relevant first, at most `top_k`.

    Pulls a wider dense candidate pool, rescores each candidate against the
    query with the cross-encoder, and drops anything below the relevance gate.
    May return an empty list: filling the limit with irrelevant facts is worse
    than returning nothing. `retrieve()` stays the raw, ungated primitive.

    If the reranker is unavailable, degrades to embedding-distance ranking
    with FALLBACK_MAX_DISTANCE and logs it. Never raises for that reason.
    """
    import reranker

    threshold = RELEVANCE_MIN_SCORE if min_score is None else min_score
    candidates = retrieve(query, domain=domain, user_id=user_id,
                          content_type=content_type, top_k=max(candidate_k, top_k))
    if not candidates:
        return []

    scores = reranker.score(_third_person_query(query), [item["text"] for item in candidates])

    if scores is None:
        log.info("memory_retrieval_degraded", extra={
            "domain": domain, "reason": "reranker_unavailable",
            "fallback_max_distance": FALLBACK_MAX_DISTANCE,
        })
        kept = [item for item in sorted(candidates, key=lambda i: i.get("distance", 0.0))
                if item.get("distance", 0.0) <= FALLBACK_MAX_DISTANCE]
        return kept[:top_k]

    ranked = sorted(
        ({**item, "score": score} for item, score in zip(candidates, scores)),
        key=lambda item: item["score"], reverse=True,
    )
    kept = [item for item in ranked if item["score"] >= threshold][:top_k]
    log.info("memory_retrieved_relevant", extra={
        "domain": domain, "candidates": len(candidates), "kept": len(kept),
        "threshold": threshold,
        "top_score": round(ranked[0]["score"], 4) if ranked else None,
    })
    return kept


def delete_by_ids(ids: list[str], domain: str = "personal",
                  user_id: str = DEFAULT_USER_ID) -> None:
    """Deletes IDs only when they belong to the requested user and domain."""
    _validate_domain(domain)
    _validate_user_id(user_id)
    collection = _get_collection(domain)
    owned_ids = collection.get(ids=ids, where={"user_id": user_id}).get("ids", [])
    if owned_ids:
        collection.delete(ids=owned_ids)
    log.info("memory_deleted", extra={"domain": domain, "user_id": user_id,
                                        "ids": owned_ids})


if __name__ == "__main__":
    print("=== Memory module self-test ===\n")

    # Store a fact and a conversation turn
    store("My favorite programming language is Python.", domain="personal", content_type="fact")
    store("I'm studying for my data structures exam next week.", domain="academic", content_type="fact")
    store("User asked about free disk space, Zedek reported 62GB free.", domain="personal", content_type="conversation")

    print("Stored 3 test items.\n")

    # Retrieve
    print("Query: 'what programming language do I like'")
    for r in retrieve("what programming language do I like", domain="personal"):
        print(f"  [{r['metadata']['content_type']}] {r['text']}")

    print("\nQuery: 'what am I studying'")
    for r in retrieve("what am I studying", domain="academic"):
        print(f"  [{r['metadata']['content_type']}] {r['text']}")

    print("\n=== Phase 5 self-test complete ===")