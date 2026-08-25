"""Offline semantic intent and domain routing for Zedek.

The Hugging Face encoder runs locally on CPU. ``semantic-router`` compares the
input embedding with example utterances for each route; it does not generate
text or use a model confidence claim. Populate the route utterance lists with
examples from real user conversations as the classifier is tuned.

Hybrid Cascading Architecture
------------------------------
Layer 1  — semantic-router (CPU, sentence-transformers/all-MiniLM-L6-v2).
           Returns immediately if cosine similarity >= 0.65.
Layer 2  — LLM tool-calling via llm_provider.generate_chat().
           Triggered when Layer 1 score < 0.65 or returns no match.
Feedback — Successful LLM classifications are saved back to
           data/dynamic_utterances.json and merged into the in-memory
           RouteLayer so the same phrasing becomes a fast local hit
           on the next call.  Prompts > 15 words are excluded from
           saving to prevent vector index contamination.
"""

import json
import os
import re
os.environ.setdefault("HF_HUB_OFFLINE", "1")  # use local cache only, skip network check
# (safe because the model is downloaded once on first successful run; if you ever
# need to re-download or switch models, temporarily unset this or delete the cache)

from semantic_router import Route, RouteLayer
from semantic_router.encoders import HuggingFaceEncoder
from zedek_logger import get_logger

log = get_logger("classifier")

MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "models", "all-MiniLM-L6-v2")

# Path for dynamically learned utterances (created at runtime on first save).
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DYNAMIC_UTTERANCES_PATH = os.path.join(_PROJECT_ROOT, "data", "dynamic_utterances.json")

# Maximum word count for a prompt to be eligible for dynamic saving.
# Longer prompts are multi-sentence narratives and would pollute the local
# vector index with non-reusable phrasings.
MAX_DYNAMIC_WORDS = 15

_encoder = None
DEFAULT_INTENT = "general_question"

# ── Thresholds ──────────────────────────────────────────────────────────────
# Both constants are kept separate for clarity; they represent the same
# operational boundary: >= 0.65 → high-confidence local hit, < 0.65 → LLM.
ROUTE_THRESHOLD = 0.65       # passed to each Route so semantic-router only
                              # reports a name when cosine similarity is high enough
CONFIDENCE_THRESHOLD = 0.65  # used in classify_intent() to decide LLM escalation
# ────────────────────────────────────────────────────────────────────────────

AMBIGUOUS_TERMS = {
    # --- Existing Entries ---
    "astro": ["astronomy/astrology", "Astro frontend web framework"],
    "rust": ["Rust programming language", "iron oxidation/corrosion"],
    "go": ["Go/Golang programming language", "the board game Go"],
    "swift": ["Swift programming language", "SWIFT banking/financial network", "the bird"],
    "spark": ["Apache Spark Big Data framework", "electrical spark"],

    # --- Programming Languages vs. Common Words ---
    "python": ["Python programming language", "the snake"],
    "java": ["Java programming language", "Java island / coffee"],
    "ruby": ["Ruby programming language", "the gemstone"],
    "perl": ["Perl programming language", "pearl gemstone"],
    "julia": ["Julia programming language", "the name Julia"],
    "dart": ["Dart programming language", "thrown projectile game"],
    "r": ["R statistics programming language", "the letter R"],
    "c": ["C programming language", "the letter C / music note"],
    "processing": ["Processing graphics language", "CPU data processing"],
    "scratch": ["Scratch block coding tool", "physical mark/scratch"],

    # --- CS Frameworks/Tools vs. General Words ---
    "react": ["React.js frontend library", "chemical or human emotional reaction"],
    "angular": ["Angular web framework", "geometric angles/geometry"],
    "vue": ["Vue.js frontend framework", "view/sight (misspelling or French word)"],
    "flask": ["Flask Python web framework", "drinking container"],
    "django": ["Django Python framework", "the name Django / movie"],
    "spring": ["Spring Java/Boot framework", "elastic coil / season"],
    "express": ["Express.js Node framework", "fast transport / emotional expression"],
    "docker": ["Docker container tool", "port worker"],
    "git": ["Git version control system", "British slang term"],
    "bash": ["Bash shell terminal", "party / striking something hard"],
    "huggingface": ["Hugging Face AI library/hub", "literal hugging gesture/emoji"],

    # --- Dual CS Concepts (Hardware/OS vs. Concepts) ---
    "kernel": ["Operating System Kernel", "corn/nut kernel or math matrix kernel"],
    "thread": ["CPU Execution Thread", "sewing thread or forum discussion thread"],
    "process": ["OS Running Process", "general workflow or business step"],
    "bus": ["Computer Hardware Bus (PCI/Data)", "transit vehicle"],
    "port": ["Network TCP/UDP Port or Hardware Port", "seaport or wine"],
    "shell": ["Linux/Unix Shell Terminal", "seashell or outer casing"],
    "driver": ["Hardware Device Driver", "vehicle driver or golf club"],
    "terminal": ["Command-line Terminal application", "airport or bus terminal"],

    # --- CS Core Concepts vs. Everyday English ---
    "bug": ["Software Code Defect/Error", "biological insect"],
    "patch": ["Software Update/Code Patch", "fabric patch or eye patch"],
    "cache": ["Hardware/Memory Cache", "hidden store of objects"],
    "cookie": ["HTTP Browser Cookie", "baked snack"],
    "salt": ["Cryptographic Salt", "table salt / sodium chloride"],
    "hash": ["Hashing algorithm / SHA key", "hash brown food or hashtag"],
    "class": ["OOP Code Class / Blueprints", "school classroom or social class"],
    "string": ["Data type (text sequence)", "twine / musical instrument string"],
    "array": ["Data structure (contiguous memory)", "an arrangement/display of items"],
    "tree": ["Binary/Data Structure Tree", "botanical tree"],
    "stack": ["Call stack / Stack data structure", "pile of physical items"],
    "queue": ["Queue data structure (FIFO)", "line of people waiting"],
    "matrix": ["Mathematical/2D Array Matrix", "The Matrix movie / grid structure"],
    "socket": ["Network Socket (IP + Port)", "electrical wall outlet"],
}

INTENT_UTTERANCES = {
    "search_files": [
        "find my resume file",
        "where is my assignment stored",
        "search for a file named notes.txt",
        "locate the pdf in my downloads folder",
        "can you look for my python script",
        "find all files with extension .cpp",
        "check if I have a document called syllabus",
        "search my directory for project files",
        "where did I save my project report",
        "find files matching this name",
    ],
    "disk_usage_by_folder": [
        "which folders use the most disk space",
        "show me large directories on my drive",
        "what folder is taking up so much space",
        "check folder sizes in home directory",
        "find the largest folders on my system",
        "show breakdown of directory storage usage",
        "which directories are eating up my memory space",
        "list subfolders by their size",
    ],
    "top_memory_processes": [
        "show the processes using the most RAM",
        "which app is consuming all my memory",
        "list top RAM hogs",
        "what processes are taking up memory right now",
        "show high memory usage applications",
        "check RAM consumption by process",
        "display top active memory tasks",
        "what is using up my RAM",
    ],
    "free_space_summary": [
        "how much free disk space do I have",
        "check remaining storage space",
        "is my hard drive full",
        "show free disk capacity",
        "how many gigabytes are left on my drive",
        "check overall storage status",
        "do I have enough space left to download a file",
        "summary of free vs used disk space",
    ],
    "directory_size": [
        "how large is this folder",
        "check the size of my downloads directory",
        "what is the total size of my project folder",
        "how many megabytes is this folder taking",
        "calculate total space used by this directory",
        "how big is my documents folder",
        "get directory size for this path",
    ],
    "remember_fact": [
        "remember that I study at PSG College of Technology",
        "keep in mind my target company is Google",
        "save this fact: I am preparing for placement exams",
        "note down that my favorite programming language is Python",
        "remember my reg number is 21BCE001",
        "store this detail about my academic context",
        "make a note that I have an exam next week",
        "memorize that my professor for DSA is Dr. Smith",
        "save to my profile that I live in hostel block B",
    ],
    "correct_fact": [
        "that fact is false, please correct it",
        "update what you remember about my college",
        "no that is wrong, I study at a different university now",
        "change my stored information about my GPA",
        "that was incorrect, delete the old detail and update it",
        "I need to fix a mistake in what you remembered earlier",
        "update my profile: my exam was rescheduled",
        "that is no longer true, please correct your memory",
        "forget what I said earlier, here is the real context",
        "no its not correct",
        "you mistook that, remove that from memory",
        "no that is not part of my course, remove it",
        "that is mistaken, please delete that fact",
    ],
    "coding_task": [
        "help me fix this Python code",
        "write a C++ program to implement a binary tree",
        "debug this recursion function for me",
        "why am I getting a segmentation fault in this snippet",
        "refactor this code to make it faster",
        "write a bash script to automate backups",
        "how do I solve this LeetCode array problem",
        "create a REST API endpoint using FastAPI",
        "fix the syntax error on line 42",
        "write unit tests for this python class",
        "can you build me static webite using html css for jewelary store",
        "build a static website using html and css",
        "build a website for a grocery shop",
        "create an html css landing page",
        "develop a frontend web page",
        "write code to build a website",
        "create a fullstack application",
        "build an ecommerce web page",
    ],
    "unsupported": [
        "play some music on Spotify",
        "set an alarm for 7 AM",
        "turn off the room lights",
        "send a text message to my friend",
        "book a cab for me",
        "adjust my screen brightness",
        "control media playback",
        "what is the current room temperature",
    ],
    "list_processes_detailed": [
        "why is this process using so much CPU",
        "show detailed information for PID 4052",
        "inspect running process details",
        "why is my CPU usage at 100 percent",
        "analyze what this background process is doing",
        "list running process threads and CPU consumption",
        "tell me why python is taking so much processing power",
        "check status and runtime of active processes",
    ],
    "open_application": [
        "open the calculator application",
        "can you open Brave application",
        "launch the browser",
        "start VS Code editor",
        "open my terminal app",
        "launch VLC media player",
        "run the obsidian application",
        "start an installed application",
    ],
}

DOMAIN_UTTERANCES = {
    "personal": [
        "what do you remember about my personal life",
        "my daily routines and preferences",
        "hobbies and personal interests",
        "my home life and non-academic notes",
        "details about my personality or friends",
    ],
    "academic": [
        "help me with my studies",
        "placement preparation tracking and weakness",
        "DSA subject concepts and exams",
        "college assignments and coursework",
        "semester exam schedules and grades",
    ],
}


# ── Encoder ──────────────────────────────────────────────────────────────────

def _resolve_local_model_path():
    return MODEL_PATH if os.path.isfile(os.path.join(MODEL_PATH, "config.json")) else MODEL_ID

def _get_encoder() -> HuggingFaceEncoder:
    """Return the shared CPU HuggingFaceEncoder, creating it on first call.

    Points at the project-local models/ embedding cache so semantic-router and
    Chroma memory share the same weights without re-downloading.
    """
    global _encoder
    if _encoder is None:
        model_name = _resolve_local_model_path()
        _encoder = HuggingFaceEncoder(name=model_name, device="cpu")
    return _encoder


# ── Dynamic utterance persistence ────────────────────────────────────────────

def _load_dynamic_utterances() -> dict[str, list[str]]:
    """Read data/dynamic_utterances.json from disk, returning {} on error."""
    if not os.path.exists(DYNAMIC_UTTERANCES_PATH):
        return {}
    try:
        with open(DYNAMIC_UTTERANCES_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        log.info("dynamic_utterances_load_failed", extra={"error": str(exc)})
        return {}


def _save_dynamic_utterances(data: dict[str, list[str]]) -> None:
    """Write data to data/dynamic_utterances.json atomically via temp file."""
    os.makedirs(os.path.dirname(DYNAMIC_UTTERANCES_PATH), exist_ok=True)
    temp_file = f"{DYNAMIC_UTTERANCES_PATH}.tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.replace(temp_file, DYNAMIC_UTTERANCES_PATH)
    except OSError as exc:
        log.info("dynamic_utterances_save_failed", extra={"error": str(exc)})
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except OSError:
                pass


# ── Router construction (static + dynamic merged) ────────────────────────────

def _build_intent_router() -> RouteLayer:
    """Build the Layer 1 intent RouteLayer from static + dynamic utterances.

    Static utterances (from ``INTENT_UTTERANCES``) and runtime-learned
    utterances (from ``data/dynamic_utterances.json``) are merged per intent.
    Static phrases take precedence; duplicates are discarded.
    """
    dynamic = _load_dynamic_utterances()
    merged: dict[str, list[str]] = {
        name: list(phrases) for name, phrases in INTENT_UTTERANCES.items()
    }
    for intent, phrases in dynamic.items():
        if intent in merged:
            # Deduplicate while preserving order: static utterances first
            existing_set = set(merged[intent])
            for phrase in phrases:
                if phrase not in existing_set:
                    merged[intent].append(phrase)
                    existing_set.add(phrase)
        else:
            log.info("dynamic_utterance_unknown_intent", extra={"intent": intent})

    routes = [
        Route(name=name, utterances=utterances, score_threshold=ROUTE_THRESHOLD)
        for name, utterances in merged.items()
    ]
    return RouteLayer(encoder=_get_encoder(), routes=routes)


def _build_domain_router() -> RouteLayer:
    routes = [
        Route(name=name, utterances=utterances, score_threshold=ROUTE_THRESHOLD)
        for name, utterances in DOMAIN_UTTERANCES.items()
    ]
    return RouteLayer(encoder=_get_encoder(), routes=routes)


# Build both layers during startup so classification never initializes a model
# lazily on the first user request.
_intent_router = _build_intent_router()
_domain_router = _build_domain_router()


def _is_semantically_valid_for_intent(text: str, intent: str) -> bool:
    """Pre-ingestion sanity filter checking whether a phrase makes sense for an intent."""
    lowered = text.lower()

    if intent == "open_application":
        coding_signals = ["build", "create", "make", "write", "code", "develop", "website", "webpage", "html", "css", "script", "frontend", "backend", "api"]
        if any(sig in lowered for sig in coding_signals):
            return False
        launch_signals = ["open", "launch", "start", "run", "bring up"]
        return any(sig in lowered for sig in launch_signals)

    if intent == "search_files":
        search_signals = ["find", "search", "where", "locate", "look for", "list files", "file"]
        return any(sig in lowered for sig in search_signals)

    if intent in ("disk_usage_by_folder", "free_space_summary", "directory_size"):
        storage_signals = ["disk", "space", "free", "used", "size", "storage", "folder", "directory", "gb", "mb", "capacity"]
        return any(sig in lowered for sig in storage_signals)

    if intent in ("top_memory_processes", "list_processes_detailed"):
        proc_signals = ["memory", "ram", "process", "processes", "cpu", "eating", "usage", "pid", "threads", "consuming"]
        return any(sig in lowered for sig in proc_signals)

    return True


def remove_utterance_dynamically(text: str, intent: str | None = None) -> bool:
    """Purge a dynamically learned utterance from disk and hot-reload the router.

    Used for self-healing when a user corrects an action or an execution fails.
    Returns True if an entry was found and removed, False otherwise.
    """
    global _intent_router
    if not text:
        return False

    data = _load_dynamic_utterances()
    removed = False

    targets = [intent] if intent and intent in data else list(data.keys())
    for it in targets:
        if it in data and text in data[it]:
            data[it].remove(text)
            removed = True
            log.info("dynamic_utterance_pruned", extra={"text": text, "intent": it})
            if not data[it]:
                del data[it]

    if removed:
        _save_dynamic_utterances(data)
        _intent_router = _build_intent_router()
        log.info("intent_router_rebuilt_after_prune", extra={"text": text})

    return removed


def add_utterance_dynamically(text: str, intent: str) -> bool:
    """Persist a newly learned phrase and hot-reload the in-memory router.

    Quality guardrails
    ------------------
    - Prompts > MAX_DYNAMIC_WORDS words are skipped (narrative / multi-sentence).
    - ``general_question`` is never persisted.
    - Semantic intent sanity check (_is_semantically_valid_for_intent) must pass.
    - Duplicates (already in static or dynamic lists) are silently skipped.

    Returns True if persisted, False if skipped/rejected.
    """
    global _intent_router

    if not text or not intent:
        return False

    if intent == DEFAULT_INTENT:
        log.info("dynamic_learning_skipped_general_question", extra={"text": text})
        return False

    word_count = len(text.split())
    if word_count > MAX_DYNAMIC_WORDS:
        log.info("dynamic_learning_skipped_too_long",
                 extra={"text": text, "word_count": word_count, "limit": MAX_DYNAMIC_WORDS})
        return False

    if not _is_semantically_valid_for_intent(text, intent):
        log.info("dynamic_learning_skipped_semantic_sanity_check",
                 extra={"text": text, "intent": intent})
        return False

    # Check static list first
    static_phrases = INTENT_UTTERANCES.get(intent, [])
    if text in static_phrases:
        log.info("dynamic_learning_skipped_already_static", extra={"text": text, "intent": intent})
        return False

    # Load, deduplicate, save
    data = _load_dynamic_utterances()
    existing = data.setdefault(intent, [])
    if text in existing:
        log.info("dynamic_learning_skipped_already_dynamic", extra={"text": text, "intent": intent})
        return False

    existing.append(text)
    _save_dynamic_utterances(data)
    log.info("dynamic_utterance_saved", extra={"text": text, "intent": intent,
                                                "total_for_intent": len(existing)})

    # Hot-reload in-memory router so the phrase works immediately
    _intent_router = _build_intent_router()
    log.info("intent_router_rebuilt", extra={"intent": intent})
    return True


# ── LLM escalation (Layer 2) ─────────────────────────────────────────────────

def query_llm_with_tools(text: str) -> dict:
    """Layer 2: ask an LLM to identify the intent via structured tool-calling.
    """
    import llm_provider
    from classifier_tools import ROUTER_TOOLS, VALID_INTENT_NAMES

    tool_schema_str = json.dumps(ROUTER_TOOLS, indent=2)

    system_prompt = (
        "You are a strict intent-classification assistant for an AI personal assistant "
        "called Zedek. Given a user message, pick EXACTLY ONE tool from the list below "
        "that best describes the user's intent. Respond ONLY with a JSON object in this "
        'exact format: {"function_name": "<tool name>", "arguments": {}}.\n'
        "Do not add explanation. Do not add markdown. Output valid JSON only.\n\n"
        f"Available tools:\n{tool_schema_str}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text},
    ]

    try:
        result = llm_provider.generate_chat(messages, json_mode=True, task="general_qa")
        raw = result.get("answer", "")
        parsed = json.loads(raw)
        func_name = parsed.get("function_name", "").strip()
        llm_args = parsed.get("arguments", {})

        # Guard against LLM misclassifying "build website/app" into open_application
        if func_name == "open_application":
            coding_signals = ["build", "create", "write", "code", "develop", "html", "css", "website", "webpage", "script", "frontend", "backend"]
            if any(sig in text.lower() for sig in coding_signals):
                log.info("llm_tool_call_corrected_app_to_coding", extra={"text": text, "raw": func_name})
                func_name = "coding_task"

        if func_name not in VALID_INTENT_NAMES:
            log.info("llm_tool_call_unknown_intent",
                     extra={"raw_function_name": func_name, "text": text})
            func_name = DEFAULT_INTENT

        func_value = None if func_name == DEFAULT_INTENT else func_name
        log.info("llm_tool_call_classified",
                 extra={"text": text, "intent": func_name,
                        "provider": result.get("source", "unknown")})
        return {
            "function": func_value,
            "confidence": "high",
            "score": 1.0,
            "via_llm": True,
            "llm_args": llm_args if isinstance(llm_args, dict) else {},
        }

    except (json.JSONDecodeError, KeyError, Exception) as exc:
        log.info("llm_tool_call_failed",
                 extra={"text": text, "error": str(exc)})
        return {
            "function": None,
            "confidence": "low",
            "score": 0.0,
            "via_llm": True,
            "llm_args": {},
        }


# ── Pre-classification guards ─────────────────────────────────────────────────

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


# ── Public classification API ─────────────────────────────────────────────────

def classify_intent(text: str) -> dict:
    """Hybrid two-layer intent classifier.

    Returns a dict with keys:
        ``function``   — intent name string, or None for general_question /
                         acknowledgements.
        ``confidence`` — ``"high"`` or ``"low"``.
        ``score``      — cosine similarity from Layer 1, or 1.0 when routed
                         via Layer 2 LLM.
        ``via_llm``    — True when Layer 2 was used; False for Layer 1 hits.

    Pipeline
    --------
    1. Pre-classification guards (acknowledgement, unsupported media).
    2. Layer 1 — semantic-router (CPU).  If score >= 0.65, return immediately.
    3. Layer 2 — LLM tool-calling.  Triggered when score < 0.65.
    4. Dynamic learning — if Layer 2 identified a specific intent (not
       general_question) and the phrase is <= 15 words, persist it so future
       identical phrasing resolves via Layer 1.
    """
    if _is_acknowledgement_or_confirmation(text):
        log.info("intent_classified_acknowledgement", extra={"text": text})
        return {"function": None, "confidence": "high", "score": 0.0, "via_llm": False}

    if _is_unsupported_action_request(text):
        log.info("intent_classified_unsupported_action", extra={"text": text})
        return {"function": "unsupported", "confidence": "high", "score": 1.0, "via_llm": False}

    # ── Layer 1: semantic-router ──────────────────────────────────────────
    result = _intent_router(text)
    top_key = result.name or DEFAULT_INTENT
    # Static RouteChoice objects do not carry the retrieval score in the
    # pinned semantic-router version. A non-empty name means its route
    # threshold was already satisfied; expose that threshold through the
    # legacy score field.
    top_score = ROUTE_THRESHOLD if result.name else 0.0

    if top_score >= CONFIDENCE_THRESHOLD and top_key != DEFAULT_INTENT:
        # High-confidence local hit — return fast
        func_value = top_key  # already validated: never DEFAULT_INTENT here
        log.info("intent_classified_layer1",
                 extra={"text": text, "intent": top_key,
                        "score": round(top_score, 3), "via_llm": False})
        return {
            "function": func_value,
            "confidence": "high",
            "score": round(top_score, 3),
            "via_llm": False,
        }

    # ── Layer 2: LLM tool-calling ─────────────────────────────────────────
    log.info("intent_escalating_to_llm",
             extra={"text": text, "layer1_score": round(top_score, 3),
                    "layer1_intent": top_key})
    return query_llm_with_tools(text)


def classify_domain(text: str) -> str:
    """Returns 'personal' or 'academic'."""
    result = _domain_router(text)
    top_key = result.name or "personal"

    log.info("domain_classified", extra={"text": text, "domain": top_key})
    return top_key


if __name__ == "__main__":
    print("=== Classifier self-test ===\n")
    print(f"Layer 1 threshold : {ROUTE_THRESHOLD}")
    print(f"LLM escalation    : score < {CONFIDENCE_THRESHOLD}\n")

    test_cases = [
        ("Layer 1 expected hit",  "how much free space do I have"),
        ("Layer 1 expected hit",  "find my resume file"),
        ("Layer 1 expected hit",  "play some music"),
        ("Layer 1 expected hit",  "open Brave application"),
        ("Layer 1 expected hit",  "I study at PSG College of Technology"),
        ("Layer 1 expected hit",  "okay thank you"),
        ("Likely LLM escalation", "show me memory pigs running on CPU"),
        ("Likely LLM escalation", "yo how much juice is left on the disk"),
        ("General question",      "what is my name"),
    ]
    for label, text in test_cases:
        r = classify_intent(text)
        d = classify_domain(text)
        via = "LLM" if r.get("via_llm") else "L1"
        print(
            f"[{label}]\n"
            f"  input   : '{text}'\n"
            f"  intent  : {r['function']}  confidence={r['confidence']}  "
            f"score={r['score']}  via={via}\n"
            f"  domain  : {d}\n"
        )