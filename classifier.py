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

import capabilities
# pyrefly: ignore [missing-import]
from semantic_router import Route, RouteLayer
# pyrefly: ignore [missing-import]
from semantic_router.encoders import HuggingFaceEncoder
from text_sanitizer import model_facing_description
from zedek_logger import get_logger

log = get_logger("classifier")

MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "models", "all-MiniLM-L6-v2")

# Path for dynamically learned utterances (created at runtime on first save).
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DYNAMIC_UTTERANCES_PATH = os.getenv(
    "ZEDEK_DYNAMIC_UTTERANCES_PATH", os.path.join(_PROJECT_ROOT, "data", "dynamic_utterances.json"))

# Maximum word count for a prompt to be eligible for dynamic saving.
# Longer prompts are multi-sentence narratives and would pollute the local
# vector index with non-reusable phrasings.
MAX_DYNAMIC_WORDS = 15

_encoder = None
DEFAULT_INTENT = "general_question"

# Intent routes (and the general-question anchor). 0.55, not 0.65: on the golden
# DEV slice 0.55 raised local coverage with precision UP (96.2% -> 97.4% alone);
# below 0.55 the extra local decisions were only ~87% correct.
ROUTE_THRESHOLD = 0.55
CONFIDENCE_THRESHOLD = ROUTE_THRESHOLD  # used in classify_intent() to decide LLM escalation
# Domain (personal/academic) routes keep the original value: that router was not
# re-measured, so it must not move with the intent threshold.
DOMAIN_ROUTE_THRESHOLD = 0.65
# Intents whose wrong match costs more than an LLM call keep the stricter 0.65:
# open_application runs at Tier 1 without asking, unsupported refuses outright.
# Product names ("firefox", "spotify") dominate MiniLM embeddings and pulled app
# mentions into these routes; on DEV this raised local precision 94.4% -> 96.2%
# for -0.5 points of coverage.
STRICT_INTENT_THRESHOLDS = capabilities.strict_thresholds()  # `threshold:` in capabilities/*.yaml

# Requests the user confirmed were "just a question" after a misroute
# (orchestrator._heal_misroute). An exact repeat is answered directly: neither
# the router nor the LLM gets to turn it into an action again.
_CONFIRMED_QUESTIONS: set[str] = set()

# In-memory LRU fast-path cache for instant repeat classifications (< 1ms)
_ROUTING_CACHE: dict[str, dict] = {}
MAX_ROUTING_CACHE_SIZE = 256
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

# Router utterances live in capabilities/<intent>.yaml (ROADMAP E1): the
# descriptive phrases first, then the short examples.
# Short, realistic example phrases (OpenSpec change improve-layer1-coverage).
# The phrases above open with a 20-40 word description that real requests embed
# far from ("write a python function to reverse a linked list" scored 0.42 against
# coding_task). Measured on the golden DEV slice: local coverage 28.4% -> 55.7% at
# precision 96.2% -> 97.0%. None of these is within 0.90 cosine of a held-out test
# row (enforced by tests/test_routing_eval.py).
INTENT_UTTERANCES = capabilities.intent_utterances()
SHORT_EXAMPLE_UTTERANCES = capabilities.short_example_utterances()

# Negative anchor: explanation-style and concept questions. Routed as
# DEFAULT_INTENT, so classify_intent escalates them exactly as today and they never
# become an action. Without it, concept questions sharing an action's vocabulary
# ("explain what ram is") were claimed by that action. Kept out of INTENT_UTTERANCES
# because those keys define the routable intents.
GENERAL_ANCHOR_UTTERANCES = capabilities.general_anchor_utterances()  # capabilities/general_question.yaml


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

def _build_intent_router(include_dynamic: bool = True) -> RouteLayer:
    """Build the Layer 1 intent RouteLayer from static + dynamic utterances.

    Static utterances (from ``INTENT_UTTERANCES``) and runtime-learned
    utterances (from ``data/dynamic_utterances.json``) are merged per intent.
    Static phrases take precedence; duplicates are discarded.

    ``include_dynamic=False`` builds from the static phrases only. The dynamic
    file also holds MCP-registered tool descriptions and differs per machine,
    so the routing evaluation uses the static build to stay reproducible.
    """
    global _CONFIRMED_QUESTIONS
    dynamic = _load_dynamic_utterances() if include_dynamic else {}
    if include_dynamic:
        _CONFIRMED_QUESTIONS = {p.strip().lower() for p in dynamic.get(DEFAULT_INTENT, [])}
    merged: dict[str, list[str]] = {
        name: list(phrases) for name, phrases in INTENT_UTTERANCES.items()
    }
    anchor = list(GENERAL_ANCHOR_UTTERANCES)
    for phrase in dynamic.get(DEFAULT_INTENT, []):  # user-confirmed "just a question" phrases
        if phrase not in anchor:
            anchor.append(phrase)
    for intent, phrases in dynamic.items():
        if intent == DEFAULT_INTENT:
            continue
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
        Route(name=name, utterances=utterances,
              score_threshold=STRICT_INTENT_THRESHOLDS.get(name, ROUTE_THRESHOLD))
        for name, utterances in merged.items()
    ]
    routes.append(Route(name=DEFAULT_INTENT, utterances=anchor,
                        score_threshold=ROUTE_THRESHOLD))
    return RouteLayer(encoder=_get_encoder(), routes=routes)


def _build_domain_router() -> RouteLayer:
    routes = [
        Route(name=name, utterances=utterances, score_threshold=DOMAIN_ROUTE_THRESHOLD)
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

    if intent == "system_inspect":
        sys_signals = [
            "system", "package", "packages", "application", "applications", "app", "apps",
            "battery", "kernel", "os", "linux", "cpu", "gpu", "hardware", "spec", "specs",
            "ip", "network", "uptime", "hostname", "version", "installed", "exist", "count",
            "temperature", "temp", "memory", "ram", "swap", "disk", "distro", "ubuntu"
        ]
        return any(sig in lowered for sig in sys_signals)

    return True


def forget_routing(text: str) -> None:
    """Drop a cached routing decision so the next request is classified afresh."""
    _ROUTING_CACHE.pop((text or "").strip().lower(), None)


def remove_utterance_dynamically(text: str, intent: str | None = None) -> bool:
    """Purge a dynamically learned utterance from disk and hot-reload the router.

    Used for self-healing when a user corrects an action or an execution fails.
    Returns True if an entry was found and removed, False otherwise.
    """
    global _intent_router, _ROUTING_CACHE
    if not text:
        return False

    norm_key = text.strip().lower()
    _ROUTING_CACHE.pop(norm_key, None)

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


def add_utterance_dynamically(text: str, intent: str, *, user_confirmed: bool = False) -> bool:
    """Persist a newly learned phrase and hot-reload the in-memory router.

    Quality guardrails
    ------------------
    - Prompts > MAX_DYNAMIC_WORDS words are skipped (narrative / multi-sentence).
    - ``general_question`` is persisted only when the user confirmed it by
      correcting a misroute (``user_confirmed``); it then joins the anchor route.
    - Semantic intent sanity check (_is_semantically_valid_for_intent) must pass.
    - Duplicates (already in static or dynamic lists) are silently skipped.

    Returns True if persisted, False if skipped/rejected.
    """
    global _intent_router

    if not text or not intent:
        return False

    if intent == DEFAULT_INTENT and not user_confirmed:
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
    static_phrases = GENERAL_ANCHOR_UTTERANCES if intent == DEFAULT_INTENT else INTENT_UTTERANCES.get(intent, [])
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
    forget_routing(text)
    _intent_router = _build_intent_router()
    log.info("intent_router_rebuilt", extra={"intent": intent})
    return True


# ── Dynamic MCP tool registration (reversible) ───────────────────────────────

_MCP_REGISTERED_UTTERANCES: list[str] = []


def register_mcp_tools(tools: list) -> None:
    """Register discovered MCP tool descriptions into the mcp_tool intent router.

    - Reversibly removes any previously registered MCP phrases from dynamic storage.
    - Extracts descriptions and adds clean phrases for each MCP tool.
    - Atomically rebuilds the in-memory RouteLayer.
    - Preserves all static utterances and non-MCP dynamic utterances intact.
    """
    global _intent_router, _MCP_REGISTERED_UTTERANCES, _ROUTING_CACHE

    _ROUTING_CACHE.clear()
    data = _load_dynamic_utterances()

    # Remove previous MCP tool registrations
    if "mcp_tool" in data and _MCP_REGISTERED_UTTERANCES:
        data["mcp_tool"] = [
            phrase for phrase in data["mcp_tool"]
            if phrase not in _MCP_REGISTERED_UTTERANCES
        ]
        if not data["mcp_tool"]:
            del data["mcp_tool"]

    # Extract new utterances from tools, sorted deterministically
    new_utterances: list[str] = []
    for tool in sorted(tools, key=lambda t: getattr(t, "qualified_name", "")):
        # Model-facing text only: instruction-like sentences removed (ROADMAP B2).
        desc = model_facing_description(tool)
        tool_name = getattr(tool, "tool_name", "") or ""
        if desc:
            phrase = f"{tool_name}: {desc}"
            if len(phrase.split()) <= MAX_DYNAMIC_WORDS:
                new_utterances.append(phrase)
            elif desc and len(desc.split()) <= MAX_DYNAMIC_WORDS:
                new_utterances.append(desc)

    if new_utterances:
        existing = data.setdefault("mcp_tool", [])
        for u in new_utterances:
            if u not in existing:
                existing.append(u)

    _MCP_REGISTERED_UTTERANCES = new_utterances
    _save_dynamic_utterances(data)
    _intent_router = _build_intent_router()
    log.info("mcp_tools_registered_in_classifier", extra={"tool_count": len(tools), "utterance_count": len(new_utterances)})


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
        'exact format: {"function_name": "<tool name>"}.\n'
        "Do not add explanation. Do not add markdown. Output valid JSON only.\n\n"
        f"Available tools:\n{tool_schema_str}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text},
    ]

    try:
        # Single-field enum schema: the reply can only name a known intent (ROADMAP F2).
        from llm_schemas import intent_choice_model
        result = llm_provider.generate_structured(messages, intent_choice_model(), task="general_qa")
        func_name = result["data"].function_name

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
            "llm_args": {},
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

# Words that make a message an acknowledgement. A message short-circuits only if
# EVERY word is in ACK_CORE or ACK_FILLER and at least one core signal is present,
# so it can never also contain a request. Matching is by whole words: the old
# substring check read "type", "capacity", "facebook" and "book" as "ty"/"ok".
ACK_CORE = {
    "ok", "okay", "okie", "k", "kk", "alright", "thanks", "thank", "thanku", "thankyou",
    "thx", "ty", "tysm", "understood", "noted", "appreciate", "appreciated", "cheers",
}
ACK_FILLER = {
    "you", "u", "so", "much", "a", "lot", "very", "really", "for", "the", "that", "thats",
    "this", "it", "all", "again", "bro", "man", "dude", "buddy", "zedek", "cool", "great",
    "nice", "perfect", "awesome", "fine", "good", "sounds", "got", "now", "then", "oh", "ah",
}
_ACK_PAIRS = {("got", "it"), ("sounds", "good")}
MAX_ACK_WORDS = 8


def is_acknowledgement(text: str | None) -> bool:
    """True only for a pure "thanks / okay / got it" message.

    Every word must be acknowledgement or filler vocabulary, with at least one
    core signal ("cool" or "fine" alone may be answering Zedek's question, so
    they route normally). "thanks, now find my resume file" is a request.
    Shared by orchestrator.route_request() and classify_intent().
    """
    if not text:
        return False
    tokens = re.sub(r"[^a-z0-9\s]", " ", text.lower().replace("'", "")).split()
    if not tokens or len(tokens) > MAX_ACK_WORDS:
        return False
    if any(token not in ACK_CORE and token not in ACK_FILLER for token in tokens):
        return False
    has_core = any(token in ACK_CORE for token in tokens) or any(
        pair in _ACK_PAIRS for pair in zip(tokens, tokens[1:]))
    return has_core


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


# ── Layer 1 decision + real score ────────────────────────────────────────────

def _layer1_score(router, text: str, decided_name: str | None) -> tuple[float, bool]:
    """The similarity the router actually compared against the threshold.

    semantic-router 0.0.72 never fills ``RouteChoice.similarity_score``. The
    real value is the best single-phrase similarity of the winning route: the
    route is picked by the SUM of phrase scores, then accepted only if
    ``max(scores) > route.score_threshold``. That lives in private methods, so
    they are used for the score only and checked against the public decision.

    Returns (score, verified). If the internals disagree with the public
    decision or raise (e.g. a test's mock router), returns the previous
    constant (ROUTE_THRESHOLD on a hit, 0.0 on a miss) and verified=False.
    """
    fallback = ROUTE_THRESHOLD if decided_name else 0.0
    try:
        vector = router._encode(text=text)
        route, scores = router._retrieve_top_route(vector)
        passed = router._check_threshold(scores, route)
        internal_name = route.name if (route is not None and passed) else None
        score = float(max(scores)) if scores else 0.0
    except Exception as err:
        log.info("layer1_score_unverified", extra={"reason": type(err).__name__})
        return fallback, False
    if internal_name != decided_name:
        log.info("layer1_score_unverified", extra={
            "reason": "route_mismatch", "public": decided_name, "internal": internal_name,
        })
        return fallback, False
    return round(score, 4), True


def _layer1_route_with(router, text: str) -> tuple[str | None, float]:
    """Layer-1 decision (public router call, authoritative) plus its real score."""
    decided_name = router(text).name or None
    score, _verified = _layer1_score(router, text, decided_name)
    return decided_name, score


def _layer1_route(text: str) -> tuple[str | None, float]:
    return _layer1_route_with(_intent_router, text)


# ── Public classification API ─────────────────────────────────────────────────

def classify_intent(text: str) -> dict:
    """Hybrid two-layer intent classifier with in-memory fast caching.

    Returns a dict with keys:
        ``function``   — intent name string, or None for general_question / acknowledgements.
        ``confidence`` — ``"high"`` or ``"low"``.
        ``score``      — cosine similarity from Layer 1, or 1.0 when routed via Layer 2 LLM.
        ``via_llm``    — True when Layer 2 was used; False for Layer 1 hits.

    Pipeline:
    1. Pre-classification guards (acknowledgement, unsupported media).
    2. Fast memory cache (< 1ms).
    3. Layer 1 — description-based semantic router (< 5ms).
    4. Layer 2 — LLM structured tool-calling fallback (~150ms).
    """
    global _ROUTING_CACHE

    if is_acknowledgement(text):
        log.info("intent_classified_acknowledgement", extra={"text": text})
        return {"function": None, "confidence": "high", "score": 0.0, "via_llm": False}

    if _is_unsupported_action_request(text):
        log.info("intent_classified_unsupported_action", extra={"text": text})
        return {"function": "unsupported", "confidence": "high", "score": 1.0, "via_llm": False}

    # Fast in-memory cache lookup (< 1ms)
    norm_key = (text or "").strip().lower()
    if norm_key in _ROUTING_CACHE:
        log.info("intent_classified_cache_hit", extra={"text": text})
        return _ROUTING_CACHE[norm_key]

    if norm_key in _CONFIRMED_QUESTIONS:
        log.info("intent_classified_confirmed_question", extra={"text": text})
        return {"function": None, "confidence": "high", "score": 1.0, "via_llm": False}

    # ── Layer 1: semantic-router with description-based embeddings ───────
    layer1_name, top_score = _layer1_route_with(_intent_router, text)
    top_key = layer1_name or DEFAULT_INTENT

    if top_score >= CONFIDENCE_THRESHOLD and top_key != DEFAULT_INTENT:
        func_value = top_key
        log.info("intent_classified_layer1",
                 extra={"text": text, "intent": top_key,
                        "score": round(top_score, 3), "via_llm": False})
        decision = {
            "function": func_value,
            "confidence": "high",
            "score": round(top_score, 3),
            "via_llm": False,
        }
        # Populate fast cache
        if len(_ROUTING_CACHE) >= MAX_ROUTING_CACHE_SIZE:
            _ROUTING_CACHE.pop(next(iter(_ROUTING_CACHE)))
        _ROUTING_CACHE[norm_key] = decision
        return decision

    # ── Layer 2: LLM tool-calling fallback ────────────────────────────────
    log.info("intent_escalating_to_llm",
             extra={"text": text, "layer1_score": round(top_score, 3),
                    "layer1_intent": top_key})
    decision = query_llm_with_tools(text)

    # Populate fast cache with LLM decision
    if len(_ROUTING_CACHE) >= MAX_ROUTING_CACHE_SIZE:
        _ROUTING_CACHE.pop(next(iter(_ROUTING_CACHE)))
    _ROUTING_CACHE[norm_key] = decision

    return decision


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