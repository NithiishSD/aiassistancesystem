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
    Returns the generated item ID.
    """
    _validate_domain(domain)
    _validate_user_id(user_id)
    _validate_content_type(content_type)
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
    for doc, meta, item_id in zip(docs, metas, ids):
        items.append({"text": doc, "metadata": meta, "id": item_id})

    log.info("memory_retrieved", extra={"domain": domain, "user_id": user_id,
                                          "query": query, "results_found": len(items)})
    return items


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