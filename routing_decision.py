"""The typed routing decision passed from route_request to execute (ROADMAP E2).

Replaces a free-form dict whose keys were read in fifteen places with
``.get("_original_input", "")``-style defaults, where a misspelled key silently
became the default. Unknown keys are now an error.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, Literal

DOMAINS = ("personal", "academic")

# Old dict keys that map to a differently named field.
_RENAMED = {"_original_input": "user_input"}


@dataclass
class RoutingDecision:
    function: str | None  # a capability name, a qualified mcp_* tool, or None for a general question
    user_input: str = ""
    domain: str = "personal"
    confidence: Literal["high", "low"] = "high"
    score: float = 0.0
    via_llm: bool = False  # Layer-2 LLM chose the intent (enables execution-verified learning)
    args: dict[str, Any] = field(default_factory=dict)
    clarify: bool = False  # the ambiguity guard wants a clarifying question

    def __post_init__(self) -> None:
        if self.domain not in DOMAINS:
            self.domain = "personal"
        if self.confidence not in ("high", "low"):
            raise ValueError(f"confidence must be 'high' or 'low', not {self.confidence!r}")
        if self.args is None:
            self.args = {}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RoutingDecision":
        """Build from the legacy dict shape; unknown keys raise instead of being ignored."""
        known = {f.name for f in fields(cls)}
        values = {_RENAMED.get(k, k): v for k, v in data.items()}
        unknown = set(values) - known
        if unknown:
            raise TypeError(f"unknown routing decision keys: {sorted(unknown)}")
        values.setdefault("function", None)
        return cls(**values)

    def log_fields(self) -> dict[str, Any]:
        """What routing logs record: no args (they can hold user paths or text)."""
        return {"function": self.function, "domain": self.domain, "confidence": self.confidence,
                "score": self.score, "via_llm": self.via_llm}
