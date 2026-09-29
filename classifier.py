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

# pyrefly: ignore [missing-import]
from semantic_router import Route, RouteLayer
# pyrefly: ignore [missing-import]
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
STRICT_INTENT_THRESHOLDS = {"open_application": 0.65, "unsupported": 0.65}

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

INTENT_UTTERANCES = {
    "search_files": [
        "Search, find, locate, discover, or list files, documents, scripts, and directories on the local disk by filename, path, extension, or pattern.",
        "find where my file or document is stored on my computer",
        "search the filesystem for files matching a name or extension",
        "locate my notes, assignments, pdfs, code files, or downloads",
        "find my resume file",
        "where did I save my project report",
        "find all files with extension .cpp",
    ],
    "disk_usage_by_folder": [
        "Analyze, inspect, breakdown, and list largest directories or subfolders consuming the most disk storage space on the system.",
        "show which folders and directories are taking up the most storage space",
        "breakdown of disk space usage by directory in home folder",
        "find large folders and disk hogs on my hard drive",
        "which folders use the most disk space",
    ],
    "top_memory_processes": [
        "List, inspect, and monitor active running system processes consuming the highest RAM and physical memory usage.",
        "show top RAM hogs and high memory usage applications running right now",
        "which process or app is eating all my system memory and RAM",
        "check RAM and memory consumption by active processes",
        "show the processes using the most RAM",
    ],
    "free_space_summary": [
        "Check and summarize total, used, and available remaining free disk storage space and drive capacity.",
        "how much free disk storage space do I have left on my drive",
        "is my hard drive full and what is the available free storage space",
        "check remaining gigabytes and disk capacity",
        "how much free disk space do I have",
    ],
    "directory_size": [
        "Calculate, measure, and report the total size and disk space occupied by a specific named folder or directory.",
        "how big is this specific folder or project directory in megabytes or gigabytes",
        "check the total size of my downloads or project folder",
        "calculate exact disk size of a folder path",
        "how large is this folder",
    ],
    "remember_fact": [
        "Remember, memorize, store, and record a personal fact, preference, academic detail, schedule, profile information, or statement about the user.",
        "remember or note down this personal detail or fact about me",
        "save to my profile that I study at this college or have this interest",
        "store this fact about my subjects, professors, exams, or life",
        "remember that I study at PSG College of Technology",
    ],
    "correct_fact": [
        "Correct, update, retract, delete, or negate a previously stored fact, assumption, or detail about the user that is wrong or outdated.",
        "that fact is false or incorrect, please update or delete it",
        "no that is wrong, you mistook that, remove that from your memory",
        "fix what you remembered earlier, change my stored information",
        "no its not correct",
        "you mistook that, remove that from memory",
        "that fact is false, please correct it",
    ],
    "coding_task": [
        "Write, generate, code, build, debug, fix, refactor, implement, or test computer programs, scripts, algorithms, data structures, HTML CSS web pages, web applications, frontend, backend, APIs, or solve software engineering problems in Python, JavaScript, C++, Java, or any programming language.",
        "help me write, fix, debug, or refactor this code or function",
        "build a static website, web page, or web app using HTML CSS and JavaScript",
        "implement a data structure, algorithm, or script for this problem",
        "create a REST API, web scraper, or programming solution with unit tests",
        "help me fix this Python code",
        "write a C++ program to implement a binary tree",
        "can you build me static webite using html css for jewelary store",
        "build a website for grocery shop",
    ],
    "unsupported": [
        "Requests for unsupported capabilities such as media playback, music control on Spotify, alarms, timers, smart home IoT, cabs, texting, hardware brightness volume controls, or closing quitting applications.",
        "play music on Spotify or control audio media playback",
        "set an alarm, timer, or smart home light controls",
        "close or terminate an application on my computer",
        "play some music on Spotify",
    ],
    "list_processes_detailed": [
        "Provide detailed diagnostic analysis and thread CPU inspection for a specific running process ID or explain why a process is using 100 percent CPU.",
        "why is this specific PID or process consuming so much CPU and threads",
        "inspect detailed runtime metrics and threads for a background process",
        "diagnose why CPU usage is pegged at 100 percent by a task",
        "why is this process using so much CPU",
    ],
    "open_application": [
        "Launch, open, execute, or start a pre-installed desktop software application, tool, GUI program, terminal, browser, files manager, trash, app center, or editor already present on the user's computer. Command to launch a specific program only, not questions about installed software.",
        "open or launch an installed desktop app like Brave browser, VS Code, App Center, Files, or VLC",
        "start the calculator, terminal, trash, or text editor application",
        "run an existing installed program on my Linux machine",
        "can you open Brave application",
        "open the calculator application",
        "open app center",
        "open files app",
        "open trash",
    ],
    "system_inspect": [
        "Inspect, query, check, or report system environment metrics, hardware specifications, installed software package counts, battery percentage, OS kernel version, CPU GPU details, network IP, or system status.",
        "how many applications or packages exist on this system",
        "how many installed packages do I have",
        "what is my laptop battery level and percentage",
        "what Linux kernel version is currently running",
        "what is my CPU model and hardware specs",
        "show system uptime and hostname",
        "what is my local IP address",
    ],
    "research_task": [
        "Research, investigate, look up, or gather sources on a topic from the internet, Wikipedia, arXiv, or academic papers, and give a cited, evidence-backed explanation.",
        "research this topic and tell me what the sources say",
        "find academic papers or published studies about this subject",
        "look this up online and summarize what you find with citations",
        "what does the latest research say about this area",
        "search arxiv or semantic scholar for papers on this topic",
        "read this url and summarize what it says",
    ],
    "academic_tracking": [
        "Log, record, or review DSA and aptitude practice progress, problem-solving attempts, weak topics, study streaks, and placement preparation performance.",
        "log that I solved a dynamic programming problem today",
        "record my practice attempt on graphs, I failed it",
        "what are my weakest topics that I should practice",
        "show my placement prep progress and accuracy so far",
        "what should I study next for my placement preparation",
        "how is my DSA practice going and what is my streak",
    ],
    "web_task": [
        "Drive a web browser to interact with a live web page: open a site, click a button or link, fill in and type into a form field, or take a screenshot of a page.",
        "open this website in a browser and click the button for me",
        "fill in this form field on the page with my details",
        "take a screenshot of this web page",
        "navigate to this site and press the submit link",
        "type this text into the search box on that website",
    ],
    "mcp_tool": [
        "Use an external connected MCP tool or service to get information, query data, count words, check time, or run a task.",
        "what time is it right now",
        "tell me the current date and time",
        "count the words in this text",
        "how many words are in this paragraph",
        "give me a brief summary of this text",
        "what is the current time and date",
    ],
}

# Short, realistic example phrases (OpenSpec change improve-layer1-coverage).
# The phrases above open with a 20-40 word description that real requests embed
# far from ("write a python function to reverse a linked list" scored 0.42 against
# coding_task). Measured on the golden DEV slice: local coverage 28.4% -> 55.7% at
# precision 96.2% -> 97.0%. None of these is within 0.90 cosine of a held-out test
# row (enforced by tests/test_routing_eval.py).
SHORT_EXAMPLE_UTTERANCES = {
    'search_files': [
        'find my notes pdf',
        'where is my offer letter',
        'search for my report file',
        'locate the spreadsheet i made',
        'find all my python files',
        'where did i save the slides',
        'look for a file called draft',
        'find the pdf i downloaded',
        'search my laptop for photos',
    ],
    'disk_usage_by_folder': [
        'which folders take up the most space',
        'biggest folders on my laptop',
        'what is filling my disk',
        'show folder sizes in my home',
        'largest directories please',
        'where is my space going',
    ],
    'top_memory_processes': [
        "what's eating my ram",
        'which apps use the most memory',
        'top ram users',
        'why is my memory full',
        'show memory hungry processes',
        'which process uses most ram',
    ],
    'free_space_summary': [
        'how much storage do i have left',
        'is my disk full',
        'free space left?',
        'how much room on my drive',
        'remaining disk space',
        'any storage left on this machine',
    ],
    'directory_size': [
        'how big is this folder',
        'how big is my photos folder',
        'how large is downloads',
        'folder size please',
        'how much space does this directory use',
        'check size of my videos folder',
    ],
    'remember_fact': [
        'remember that i like chess',
        "my brother's name is arjun",
        'i prefer tea over coffee',
        'note that my exam is next friday',
        "i'm vegetarian",
        'my favourite colour is blue',
        'just so you know i work at night',
        "keep in mind i'm left handed",
        'i study computer science',
        'my phone is a pixel',
        # Statements that mention an app are facts, not launch requests.
        'i use firefox for college work',
        'my favourite editor is vim',
        'i listen to music while coding',
        'i keep my todo list in notion',
    ],
    'correct_fact': [
        "that's wrong",
        "no, that's not right",
        'you remembered that wrong',
        'forget what i told you',
        'update that, it changed',
        'that fact is outdated',
        'delete that from memory',
        "correction, it's actually wednesday",
        'no i said tuesday not monday',
        'you got my name wrong',
    ],
    'coding_task': [
        'write a function to sort a list',
        'fix this bug in my code',
        'write a python script',
        'debug my program',
        'code a calculator in java',
        'implement merge sort',
        'write a sql query for this',
        'refactor my function',
        'make a website with html',
        'write unit tests for this code',
        'why does my code crash',
        'convert this code to python',
        # Building something that names an app is still coding.
        'build an app like spotify',
        'write a bot for telegram',
        'create a browser extension',
        'make a clone of an app in react',
    ],
    'unsupported': [
        'play a song',
        'set a reminder alarm',
        'send a text to my friend',
        'turn off the wifi',
        'call my mom',
        'order food',
        'book a cab',
        'increase the volume',
    ],
    'list_processes_detailed': [
        'why is this process using so much cpu',
        'what is pid 1234 doing',
        'which process is hogging the cpu',
        'why is my cpu at 100 percent',
        'show threads for this process',
        'details about a running process',
        "my fan is loud, what's using cpu",
    ],
    'open_application': [
        'open chrome',
        'launch spotify app',
        'start vs code',
        'open the terminal app',
        'open my browser',
        'launch discord',
        'open settings',
        'start the calculator',
    ],
    'system_inspect': [
        'battery level?',
        'which kernel version',
        'show my network address',
        'how many cores do i have',
        'what gpu is in this laptop',
        'system uptime',
        'ubuntu version',
        'how much ram is installed',
    ],
    'research_task': [
        'find research papers on this topic',
        'what do studies say about this',
        'look this up with sources',
        'search arxiv for papers',
        'give me academic sources on',
        'research this and cite sources',
        'find studies about sleep',
        'summarize this article url with citations',
    ],
    'academic_tracking': [
        'log that i solved a problem',
        "i failed today's dsa question",
        'what are my weak topics',
        'what should i practice next',
        'show my practice progress',
        'how is my prep going',
        'record my leetcode attempt',
        'my practice streak',
    ],
    'web_task': [
        'click the login button on this site',
        'fill in the form on this website',
        'take a screenshot of this page',
        'type into the search box on the site',
        'go to the website and click next',
        'submit the form on that page',
        'press the button on the webpage',
    ],
    'mcp_tool': [
        'what time is it now',
        'what date is it',
        'count words in this text',
        'weather in mumbai',
        'latest news headlines',
        'codeforces rating of a user',
        'search github repos',
        'summarize this paragraph',
    ],
}
for _intent, _phrases in SHORT_EXAMPLE_UTTERANCES.items():
    INTENT_UTTERANCES[_intent].extend(_phrases)

# Negative anchor: explanation-style and concept questions. Routed as
# DEFAULT_INTENT, so classify_intent escalates them exactly as today and they never
# become an action. Without it, concept questions sharing an action's vocabulary
# ("explain what ram is") were claimed by that action. Kept out of INTENT_UTTERANCES
# because those keys define the routable intents.
GENERAL_ANCHOR_UTTERANCES = [
    'what is photosynthesis',
    'explain how the internet works',
    'who was albert einstein',
    'what does an api mean',
    'how do airplanes fly',
    'what is machine learning',
    'tell me something interesting',
    'how are you',
    'what is the difference between ram and rom',
    'explain object oriented programming',
    'why do we dream',
    'what is a linked list',
    'how does encryption work',
    "what's the history of india",
    'give me a fun fact',
    'what is an algorithm',
    'what is ram used for',
    'how does virtual memory work',
    'what is a process in an os',
    'how are threads different from processes',
    'what is a file system',
    'how does a hard disk store data',
    'what does the cpu cache do',
    'what is a kernel',
    'how does garbage collection work',
    'what is cloud storage',
    'what are trees in data structures',
    'explain how a heap works',
    'what is a graph in computer science',
]


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
    dynamic = _load_dynamic_utterances() if include_dynamic else {}
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
        Route(name=name, utterances=utterances,
              score_threshold=STRICT_INTENT_THRESHOLDS.get(name, ROUTE_THRESHOLD))
        for name, utterances in merged.items()
    ]
    routes.append(Route(name=DEFAULT_INTENT, utterances=list(GENERAL_ANCHOR_UTTERANCES),
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
        desc = getattr(tool, "description", "") or ""
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