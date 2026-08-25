"""ROUTER_TOOLS — JSON function-calling schema for the LLM escalation layer.

Used exclusively by ``classifier.py``'s Layer 2 path when the local
semantic-router score falls below the confidence threshold.  Each tool
maps to one of Zedek's 12 known intent categories.  The LLM receives
this schema and replies with a single tool-call JSON object identifying
which function best matches the user's input.

Do NOT import this from orchestrator.py or any other module; it is a
classifier-internal detail.
"""

ROUTER_TOOLS: list[dict] = [
    {
        "name": "search_files",
        "description": (
            "User wants to find, locate, or search for a file or directory on "
            "the local filesystem by name, extension, or path pattern. "
            "Examples: 'find my resume', 'where is notes.txt', 'search for .cpp files'."
        ),
    },
    {
        "name": "disk_usage_by_folder",
        "description": (
            "User wants to know which folders or directories are consuming the "
            "most disk space, or wants a breakdown of storage usage by directory. "
            "Examples: 'which folders are largest', 'show directory storage breakdown'."
        ),
    },
    {
        "name": "top_memory_processes",
        "description": (
            "User wants to see which running processes or applications are using "
            "the most RAM / memory. "
            "Examples: 'show memory hogs', 'what is eating my RAM', "
            "'list top RAM-consuming processes'."
        ),
    },
    {
        "name": "free_space_summary",
        "description": (
            "User wants to know how much free disk space remains overall, or a "
            "summary of used vs. available storage. "
            "Examples: 'how much space do I have left', 'is my drive full', "
            "'show free disk capacity'."
        ),
    },
    {
        "name": "directory_size",
        "description": (
            "User wants to know the total size of a specific folder or directory "
            "(not a system-wide breakdown). "
            "Examples: 'how big is my downloads folder', 'total size of project dir'."
        ),
    },
    {
        "name": "remember_fact",
        "description": (
            "User wants Zedek to remember, store, or note a personal or academic "
            "fact about themselves. "
            "Examples: 'remember I study at PSG College', 'note my reg number is 21BCE001', "
            "'save that my target company is Google'."
        ),
    },
    {
        "name": "correct_fact",
        "description": (
            "User wants to update, correct, negate, or retract/delete a previously stored fact "
            "that is wrong, mistaken, or outdated. "
            "Examples: 'that college info is wrong, update it', 'no its not correct', "
            "'no you mistook that, remove it from memory', 'that was incorrect, here is the real value'."
        ),
    },
    {
        "name": "coding_task",
        "description": (
            "User wants help with a programming, coding, or software development task: "
            "building websites, web pages, HTML/CSS/JS frontend, creating scripts, "
            "writing code, debugging, refactoring, explaining algorithms, building apps, "
            "or solving coding problems. "
            "Examples: 'build a static website using html css', 'create a landing page for my store', "
            "'build a website for grocery shop', 'fix this Python function', 'write a binary search', "
            "'create a FastAPI route'."
        ),
    },
    {
        "name": "list_processes_detailed",
        "description": (
            "User wants detailed information about one or more specific running "
            "processes, such as CPU usage, runtime, or threads — not just a "
            "top-N memory list. "
            "Examples: 'why is PID 4052 using so much CPU', "
            "'inspect this background process', 'show threads for python process'."
        ),
    },
    {
        "name": "open_application",
        "description": (
            "User wants to launch, open, or start a specific pre-installed desktop application "
            "or program already on their system (e.g. calculator, browser, text editor, terminal). "
            "NOT for building, creating, coding, or developing new websites, web apps, or programs. "
            "Examples: 'open VS Code', 'launch the browser', 'start VLC', "
            "'run the calculator app'."
        ),
    },
    {
        "name": "unsupported",
        "description": (
            "User is asking for an action that Zedek does not support: "
            "media/music control, IoT or smart-home control, sending messages, "
            "booking services, adjusting hardware settings like brightness or volume, "
            "or closing/quitting applications. "
            "Examples: 'play Spotify', 'set an alarm', 'turn off lights', "
            "'send a text', 'close Brave'."
        ),
    },
    {
        "name": "general_question",
        "description": (
            "User is asking a general knowledge question, a conversational question, "
            "or anything that does not clearly match any of the specific system "
            "actions above. This is the fallback — only choose it if none of the "
            "other tools are a clear match. "
            "Examples: 'what is a binary tree', 'who invented Linux', "
            "'explain recursion to me'."
        ),
    },
]

# Lookup set for fast membership checks (used in classifier.py to validate
# the LLM's tool-call response before trusting it).
VALID_INTENT_NAMES: frozenset[str] = frozenset(t["name"] for t in ROUTER_TOOLS)
