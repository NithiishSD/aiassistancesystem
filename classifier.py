"""Offline semantic intent and domain routing for Zedek.

The Hugging Face encoder runs locally on CPU. ``semantic-router`` compares the
input embedding with example utterances for each route; it does not generate
text or use a model confidence claim. Populate the route utterance lists with
examples from real user conversations as the classifier is tuned.
"""

import os
import re
os.environ.setdefault("HF_HUB_OFFLINE", "1")  # use local cache only, skip network check
# (safe because the model is downloaded once on first successful run; if you ever
# need to re-download or switch models, temporarily unset this or delete the cache)

from semantic_router import Route, RouteLayer
from semantic_router.encoders import HuggingFaceEncoder
from zedek_logger import get_logger

log = get_logger("classifier")

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

_encoder = None
DEFAULT_INTENT = "general_question"
ROUTE_THRESHOLD = 0.45


def _get_encoder():
    global _encoder
    if _encoder is None:
        log.info("loading_local_embedding_model", extra={"model": MODEL_NAME, "device": "cpu"})
        _encoder = HuggingFaceEncoder(name=MODEL_NAME, device="cpu")
    return _encoder


# Replace these starter utterances with representative examples from Zedek.
INTENT_UTTERANCES = {
    "search_files": ["find my resume file"],
    "disk_usage_by_folder": ["which folders use the most disk space"],
    "top_memory_processes": ["show the processes using the most RAM"],
    "free_space_summary": ["how much free disk space do I have"],
    "directory_size": ["how large is this folder"],
    "remember_fact": ["remember that I study at PSG College of Technology"],
    "correct_fact": ["that fact is false, please correct it"],
    "coding_task": ["help me fix this Python code"],
    "unsupported": ["play some music"],
    "list_processes_detailed": ["why is this process using so much CPU"],
    "open_application": ["open the calculator application"],
}

DOMAIN_UTTERANCES = {
    "personal": ["what do you remember about my personal life"],
    "academic": ["help me with my studies"],
}


def _build_intent_router():
    routes = [Route(name=name, utterances=utterances,
                    score_threshold=ROUTE_THRESHOLD)
                  for name, utterances in INTENT_UTTERANCES.items()]
    return RouteLayer(encoder=_get_encoder(), routes=routes)


def _build_domain_router():
    routes = [Route(name=name, utterances=utterances,
                    score_threshold=ROUTE_THRESHOLD)
                  for name, utterances in DOMAIN_UTTERANCES.items()]
    return RouteLayer(encoder=_get_encoder(), routes=routes)


# Build both layers during startup so classification never initializes a model
# lazily on the first user request.
_intent_router = _build_intent_router()
_domain_router = _build_domain_router()

CONFIDENCE_THRESHOLD = 0.45  # below this, treat as low confidence regardless of top label
# NOTE: raised from 0.35 after observing a real misfire at score=0.403 that was
# incorrectly treated as "high" confidence. Revisit this value as more test data
# comes in — it may need further tuning in either direction.


def _is_acknowledgement_or_confirmation(text: str) -> bool:
    """Treat short gratitude/acknowledgment phrases as neutral and non-correction."""
    if text is None:
        return False

    cleaned = re.sub(r"[^a-z0-9\s]", " ", text.lower()).strip()
    if not cleaned:
        return False

    short_ack_phrases = {
        "ok",
        "okay",
        "alright",
        "thanks",
        "thank you",
        "thank u",
        "ty",
        "thx",
        "got it",
        "understood",
        "sounds good",
        "appreciate it",
        "okay thank you",
        "ok thank you",
    }

    if cleaned in short_ack_phrases:
        return True

    if any(phrase in cleaned for phrase in ["thank you", "thanks", "thank u", "thx", "ty", "got it", "understood", "appreciate it"]):
        return True

    # A brief acknowledgment should not be treated as a correction simply because it is short.
    return len(cleaned.split()) <= 4 and any(word in cleaned for word in ["okay", "ok", "thanks", "thank", "got", "understood"])


def _is_unsupported_action_request(text: str) -> bool:
    """Catch unsupported control commands before semantic classification."""
    cleaned = re.sub(r"[^a-z0-9\s]", " ", (text or "").lower()).strip()
    if not cleaned:
        return False

    media_targets = r"(?:music|song|audio|video|media|spotify|youtube)"
    media_action = rf"\b(?:play|pause|stop|resume|skip|next)\b.*\b{media_targets}\b"
    reverse_media_action = rf"\b{media_targets}\b.*\b(?:play|pause|stop|resume|skip|next)\b"
    app_control = r"\b(?:close|quit|kill|terminate|stop)\b.*\b(?:app|application|process|program)\b"
    return bool(re.search(media_action, cleaned) or re.search(reverse_media_action, cleaned)
                or re.search(app_control, cleaned))


def classify_intent(text: str) -> dict:
    """
    Returns: {"function": str|None, "confidence": "high"|"low", "score": float}
    "function" is None for general_question; for remember_fact/unsupported it
    returns those exact strings so the orchestrator can branch on them same as before.
    """
    if _is_acknowledgement_or_confirmation(text):
        log.info("intent_classified_acknowledgement", extra={"text": text})
        return {"function": None, "confidence": "high", "score": 0.0}

    if _is_unsupported_action_request(text):
        log.info("intent_classified_unsupported_action", extra={"text": text})
        return {"function": "unsupported", "confidence": "high", "score": 1.0}

    result = _intent_router(text)
    top_key = result.name or DEFAULT_INTENT
    # Static RouteChoice objects do not carry the retrieval score in the
    # pinned semantic-router version. A non-empty name means its route
    # threshold was already satisfied; expose that threshold through the
    # legacy score field.
    top_score = ROUTE_THRESHOLD if result.name else 0.0

    confidence = "high" if top_score >= CONFIDENCE_THRESHOLD else "low"
    func_value = None if top_key == DEFAULT_INTENT else top_key

    log.info("intent_classified", extra={"text": text, "intent": top_key,
                                           "score": round(top_score, 3), "confidence": confidence})
    return {"function": func_value, "confidence": confidence, "score": round(top_score, 3)}


def classify_domain(text: str) -> str:
    """Returns 'personal' or 'academic'."""
    result = _domain_router(text)
    top_key = result.name or "personal"

    log.info("domain_classified", extra={"text": text, "domain": top_key})
    return top_key


if __name__ == "__main__":
    print("=== Classifier self-test (first run downloads the local embedding model) ===\n")
    test_cases = [
        "how much free space do I have",
        "I study at PSG College of Technology",
        "what is my name",
        "find my resume file",
        "play some music",
        "no it is not in Coimbatore, it is in Erode district near Bhavanisagar",
    ]
    for text in test_cases:
        result = classify_intent(text)
        domain = classify_domain(text)
        print(f"'{text}'\n  -> function={result['function']}, confidence={result['confidence']}, "
              f"score={result['score']}, domain={domain}\n")