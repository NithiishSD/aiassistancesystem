"""One declarative definition per capability (ROADMAP E1).

Each ``<name>.yaml`` in this folder holds a capability's name, a one-line
user-facing summary, its handler (a function in orchestrator.py taking
``(decision, domain)``), its description (the only text an LLM sees about it),
its router utterances, and optionally its route threshold, tier, argument types
and ``runs_when_unsure``. ``index.yaml`` lists which exist and in
what order. The classifier routes, the Layer-2 tool list, the tier table, the argument
coercion, the dispatch in orchestrator.execute() and the REPL help are all
derived from here, so they cannot drift apart.

Loading fails closed: a malformed file, an unknown key, an out-of-range tier, or
a mismatch between index.yaml and the files raises at import, so Zedek does not
start with a partial or silently-defaulted capability set.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

import yaml

DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INTENT = "general_question"
_ALLOWED_KEYS = {"name", "summary", "handler", "description", "threshold", "tier", "arg_types",
                 "utterances", "short_examples", "runs_when_unsure"}
_ARG_TYPES = {"int"}


class CapabilityError(ValueError):
    """A capability definition is invalid; Zedek must not start with it."""


@dataclass(frozen=True)
class Capability:
    name: str
    summary: str
    handler: str
    description: str
    utterances: tuple[str, ...]
    short_examples: tuple[str, ...] = ()
    threshold: float | None = None
    tier: int | None = None  # None: gated inside its handler, never by intent name
    arg_types: Mapping[str, str] = field(default_factory=dict)
    runs_when_unsure: bool = False  # dispatched even on a low-confidence route


def _string_list(value: Any, where: str, allow_empty: bool) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise CapabilityError(f"{where} must be a list of non-empty strings")
    if not value and not allow_empty:
        raise CapabilityError(f"{where} must not be empty")
    return tuple(value)


def _parse(name: str, doc: Any) -> Capability:
    where = f"capabilities/{name}.yaml"
    if not isinstance(doc, dict):
        raise CapabilityError(f"{where}: expected a mapping")
    unknown = set(doc) - _ALLOWED_KEYS
    if unknown:
        raise CapabilityError(f"{where}: unknown keys {sorted(unknown)}")
    if doc.get("name") != name:
        raise CapabilityError(f"{where}: name must be {name!r}")
    summary = doc.get("summary")
    if not isinstance(summary, str) or not summary.strip() or "\n" in summary.strip():
        raise CapabilityError(f"{where}: summary must be one non-empty line")
    handler = doc.get("handler")
    if not isinstance(handler, str) or not handler.isidentifier():
        raise CapabilityError(f"{where}: handler must be a function name")
    runs_when_unsure = doc.get("runs_when_unsure", False)
    if not isinstance(runs_when_unsure, bool):
        raise CapabilityError(f"{where}: runs_when_unsure must be true or false")
    description = doc.get("description")
    if not isinstance(description, str) or not description.strip():
        raise CapabilityError(f"{where}: description is required")

    threshold = doc.get("threshold")
    if threshold is not None and (isinstance(threshold, bool) or not isinstance(threshold, (int, float))
                                  or not 0 < threshold <= 1):
        raise CapabilityError(f"{where}: threshold must be a number in (0, 1]")

    tier = doc.get("tier")
    if tier is not None and (isinstance(tier, bool) or not isinstance(tier, int) or not 0 <= tier <= 3):
        raise CapabilityError(f"{where}: tier must be an integer 0-3")

    arg_types = doc.get("arg_types", {})
    if not isinstance(arg_types, dict) or not all(
            isinstance(k, str) and v in _ARG_TYPES for k, v in arg_types.items()):
        raise CapabilityError(f"{where}: arg_types must map argument names to one of {sorted(_ARG_TYPES)}")

    return Capability(
        name=name,
        summary=summary.strip(),
        handler=handler,
        description=description,
        utterances=_string_list(doc.get("utterances"), f"{where}: utterances", allow_empty=False),
        short_examples=_string_list(doc.get("short_examples", []), f"{where}: short_examples", allow_empty=True),
        threshold=float(threshold) if threshold is not None else None,
        tier=tier,
        arg_types=MappingProxyType(dict(arg_types)),
        runs_when_unsure=runs_when_unsure,
    )


def _load(directory: str = DIR) -> tuple[dict[str, Capability], tuple[str, ...], tuple[str, ...]]:
    with open(os.path.join(directory, "index.yaml"), encoding="utf-8") as f:
        index = yaml.safe_load(f)
    if not isinstance(index, dict) or set(index) != {"routes", "llm_tools"}:
        raise CapabilityError("capabilities/index.yaml: expected exactly 'routes' and 'llm_tools'")
    routes = _string_list(index["routes"], "index.yaml: routes", allow_empty=False)
    llm_tools = _string_list(index["llm_tools"], "index.yaml: llm_tools", allow_empty=False)
    if len(set(routes)) != len(routes) or len(set(llm_tools)) != len(llm_tools):
        raise CapabilityError("capabilities/index.yaml: duplicate names")
    if DEFAULT_INTENT in routes or set(llm_tools) != set(routes) | {DEFAULT_INTENT}:
        raise CapabilityError(
            f"capabilities/index.yaml: llm_tools must be routes plus {DEFAULT_INTENT!r}, which is not a route")

    files = {f[:-5] for f in os.listdir(directory) if f.endswith(".yaml") and f != "index.yaml"}
    if files != set(llm_tools):
        raise CapabilityError(
            f"capabilities/: files and index.yaml disagree (unlisted: {sorted(files - set(llm_tools))}, "
            f"missing: {sorted(set(llm_tools) - files)})")

    loaded = {}
    for name in llm_tools:
        with open(os.path.join(directory, f"{name}.yaml"), encoding="utf-8") as f:
            loaded[name] = _parse(name, yaml.safe_load(f))
    return loaded, routes, llm_tools


CAPABILITIES, ROUTE_ORDER, LLM_TOOL_ORDER = _load()


# ── Derived tables (each consumer gets a fresh, mutable copy) ────────────────

def intent_utterances() -> dict[str, list[str]]:
    """Router utterances per routable intent, short examples last."""
    return {n: [*CAPABILITIES[n].utterances, *CAPABILITIES[n].short_examples] for n in ROUTE_ORDER}


def short_example_utterances() -> dict[str, list[str]]:
    return {n: list(CAPABILITIES[n].short_examples) for n in ROUTE_ORDER if CAPABILITIES[n].short_examples}


def general_anchor_utterances() -> list[str]:
    return list(CAPABILITIES[DEFAULT_INTENT].utterances)


def strict_thresholds() -> dict[str, float]:
    return {n: c.threshold for n, c in CAPABILITIES.items() if c.threshold is not None and n in ROUTE_ORDER}


def router_tools() -> list[dict[str, str]]:
    """The Layer-2 tool list: name and description only (the LLM sees nothing else)."""
    return [{"name": n, "description": CAPABILITIES[n].description} for n in LLM_TOOL_ORDER]


def function_tiers() -> dict[str, int]:
    return {n: c.tier for n, c in CAPABILITIES.items() if c.tier is not None}


def int_args() -> dict[str, list[str]]:
    return {n: [a for a, t in c.arg_types.items() if t == "int"] for n, c in CAPABILITIES.items() if c.arg_types}


def help_text() -> str:
    """What Zedek can do, one line per capability, for the REPL `help` command."""
    lines = [f"  - {CAPABILITIES[n].summary}" for n in LLM_TOOL_ORDER if n != "unsupported"]
    return "Here's what I can do:\n" + "\n".join(lines)
