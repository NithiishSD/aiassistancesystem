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

MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "models", "all-MiniLM-L6-v2")

_encoder = None
DEFAULT_INTENT = "general_question"
ROUTE_THRESHOLD = 0.45
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
def _get_encoder():
    global _encoder
    if _encoder is None:
        model_name = MODEL_PATH if os.path.isfile(os.path.join(MODEL_PATH, "config.json")) else MODEL_ID
        log.info("loading_local_embedding_model", extra={"model": model_name, "device": "cpu"})
        try:
            _encoder = HuggingFaceEncoder(name=model_name, device="cpu")
        except OSError as exc:
            raise RuntimeError(
                f"The local embedding model is not cached at '{MODEL_PATH}'. "
                "Run setup.sh to download it once."
            ) from exc
    return _encoder



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