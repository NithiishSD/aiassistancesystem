"""
Phase 6.5: Dedicated intent classifier (replaces Llama for routing).

Uses a DeBERTa-v3 zero-shot classification model — CPU-only, small (~700MB),
never touches your 6GB VRAM at all. This model does ONE job: given text,
score how well it matches each candidate intent. It doesn't generate text,
doesn't hallucinate answers, doesn't get confused by multiple responsibilities.

Llama3.1 no longer decides routing — it's now used only for:
  - Extracting specific arguments once intent is already decided (narrow task)
  - Canonicalizing facts
  - Answering general questions

This narrowing of responsibility is the point: each model does less, so each
model does it more reliably.
"""

import os
import re
os.environ.setdefault("HF_HUB_OFFLINE", "1")  # use local cache only, skip network check
# (safe because the model is downloaded once on first successful run; if you ever
# need to re-download or switch models, temporarily unset this or delete the cache)

from transformers import pipeline
from zedek_logger import get_logger

log = get_logger("classifier")

MODEL_NAME = "MoritzLaurer/deberta-v3-base-zeroshot-v2.0"

_classifier = None


def _get_classifier():
    global _classifier
    if _classifier is None:
        log.info("loading_classifier_model", extra={"model": MODEL_NAME})
        _classifier = pipeline("zero-shot-classification", model=MODEL_NAME, device=-1)  # CPU
    return _classifier


# Each intent mapped to a natural-language description — the classifier
# scores how well the input "entails" each of these descriptions.
INTENT_LABELS = {
    "search_files": "a request to search for or find a file on the computer",
    "disk_usage_by_folder": "a request to check which folders are using the most disk space",
    "top_memory_processes": "a request to check which processes are using the most memory or RAM",
    "free_space_summary": "a request to check how much free disk space is available",
    "directory_size": "a request to check the total size of a specific folder or the current working directory",
    "remember_fact": "the user stating or declaring a new fact about themselves",
    "correct_fact": "the user correcting, retracting, or saying something previously stated is now false or outdated",
    "unsupported": "a request for the assistant to perform an action like booking, sending, playing media, or writing code",
    "general_question": "a general question, or a conversational message that is not a command",
}

DOMAIN_LABELS = {
    "personal": "about the user's personal life",
    "academic": "about the user's studies or academics",
}

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


def classify_intent(text: str) -> dict:
    """
    Returns: {"function": str|None, "confidence": "high"|"low", "score": float}
    "function" is None for general_question; for remember_fact/unsupported it
    returns those exact strings so the orchestrator can branch on them same as before.
    """
    if _is_acknowledgement_or_confirmation(text):
        log.info("intent_classified_acknowledgement", extra={"text": text})
        return {"function": None, "confidence": "high", "score": 0.0}

    clf = _get_classifier()
    label_keys = list(INTENT_LABELS.keys())
    candidate_descriptions = list(INTENT_LABELS.values())

    result = clf(text, candidate_descriptions)
    top_description = result["labels"][0]
    top_score = result["scores"][0]
    top_key = label_keys[candidate_descriptions.index(top_description)]

    confidence = "high" if top_score >= CONFIDENCE_THRESHOLD else "low"
    func_value = None if top_key == "general_question" else top_key

    log.info("intent_classified", extra={"text": text, "intent": top_key,
                                           "score": round(top_score, 3), "confidence": confidence})
    return {"function": func_value, "confidence": confidence, "score": round(top_score, 3)}


def classify_domain(text: str) -> str:
    """Returns 'personal' or 'academic'."""
    clf = _get_classifier()
    label_keys = list(DOMAIN_LABELS.keys())
    candidate_descriptions = list(DOMAIN_LABELS.values())

    result = clf(text, candidate_descriptions)
    top_description = result["labels"][0]
    top_key = label_keys[candidate_descriptions.index(top_description)]

    log.info("domain_classified", extra={"text": text, "domain": top_key})
    return top_key


if __name__ == "__main__":
    print("=== Classifier self-test (first run downloads the model, ~700MB) ===\n")
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