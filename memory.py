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

import chromadb
from chromadb.config import Settings
import uuid
import time
from zedek_logger import get_logger

log = get_logger("memory")

CHROMA_PATH = "./chroma_db"
DEFAULT_USER_ID = "nithiish"  # the owner; multi-user enrollment updates this later

client = chromadb.PersistentClient(path=CHROMA_PATH, settings=Settings(anonymized_telemetry=False))

_collections = {}


def _get_collection(domain: str):
    """Returns (creating if needed) the ChromaDB collection for a domain."""
    if domain not in _collections:
        _collections[domain] = client.get_or_create_collection(name=f"zedek_{domain}")
        log.info("collection_ready", extra={"domain": domain})
    return _collections[domain]


def store(text: str, domain: str = "personal", content_type: str = "fact",
          user_id: str = DEFAULT_USER_ID, extra_metadata: dict | None = None) -> str:
    """
    Stores a piece of text (a fact or a conversation turn) in memory.
    Returns the generated item ID.
    """
    if domain not in ("personal", "academic"):
        raise ValueError(f"Unknown domain '{domain}' — must be 'personal' or 'academic'.")

    collection = _get_collection(domain)
    item_id = str(uuid.uuid4())

    metadata = {
        "user_id": user_id,
        "content_type": content_type,
        "timestamp": time.time(),
    }
    if extra_metadata:
        metadata.update(extra_metadata)

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
    collection = _get_collection(domain)

    where = {"user_id": user_id}
    if content_type:
        where = {"$and": [{"user_id": user_id}, {"content_type": content_type}]}

    results = collection.query(query_texts=[query], n_results=top_k, where=where)

    items = []
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    for doc, meta in zip(docs, metas):
        items.append({"text": doc, "metadata": meta})

    log.info("memory_retrieved", extra={"domain": domain, "user_id": user_id,
                                          "query": query, "results_found": len(items)})
    return items


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