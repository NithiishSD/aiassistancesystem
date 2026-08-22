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
    "remember_fact": "the user stating or declaring a fact about themselves",
    "unsupported": "a request for the assistant to perform an action like booking, sending, playing media, or writing code",
    "general_question": "a general question, or a conversational message that is not a command",
}

DOMAIN_LABELS = {
    "personal": "about the user's personal life",
    "academic": "about the user's studies or academics",
}

CONFIDENCE_THRESHOLD = 0.35  # below this, treat as low confidence regardless of top label


def classify_intent(text: str) -> dict:
    """
    Returns: {"function": str|None, "confidence": "high"|"low", "score": float}
    "function" is None for general_question; for remember_fact/unsupported it
    returns those exact strings so the orchestrator can branch on them same as before.
    """
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