"""ROUTER_TOOLS — JSON function-calling schema for the LLM escalation layer.

Used exclusively by ``classifier.py``'s Layer 2 path when the local
semantic-router score falls below the confidence threshold.  Each tool
maps to one of Zedek's intent categories (capabilities/index.yaml).  The LLM receives
this schema and replies with a single tool-call JSON object identifying
which function best matches the user's input.

Do NOT import this from orchestrator.py or any other module; it is a
classifier-internal detail.
"""

import capabilities

# Name + description per capability, from capabilities/*.yaml in index.yaml's
# llm_tools order (ROADMAP E1). Nothing else about a capability reaches the LLM.
ROUTER_TOOLS: list[dict] = capabilities.router_tools()

# Lookup set for fast membership checks (used in classifier.py to validate
# the LLM's tool-call response before trusting it).
VALID_INTENT_NAMES: frozenset[str] = frozenset(t["name"] for t in ROUTER_TOOLS)

