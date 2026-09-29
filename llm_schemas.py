"""Schemas for structured LLM replies (ROADMAP F2).

Every model here is flat, forbids extra keys, and has every field required
(optional values are `X | None` without a default). That is the subset strict
provider modes accept: Cerebras and OpenAI-style strict json_schema need
`additionalProperties: false` and every property listed in `required`.

`json_schema_for(Model)` produces the one schema dict sent to every provider.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Fact(_Strict):
    attribute: str
    value: str


class FactList(_Strict):
    """Canonicalized facts; an empty list means the statement held no fact."""
    facts: list[Fact]


class FactCorrection(_Strict):
    index: int | None
    corrected_fact: str | None


class ResearchQueries(_Strict):
    queries: list[str] = Field(max_length=3)


class AcademicIntent(_Strict):
    action: Literal["log", "review", "summary"]
    topic: str
    result: Literal["solved", "failed", "partial", ""]
    problem: str
    difficulty: str
    minutes: int


@lru_cache(maxsize=1)
def intent_choice_model() -> type[BaseModel]:
    """Single-field enum over the router's intents: the cheapest, most reliable
    schema form, since provider schema coverage drops as schemas grow."""
    from classifier_tools import VALID_INTENT_NAMES

    names = tuple(sorted(VALID_INTENT_NAMES))
    return create_model(
        "IntentChoice",
        __config__=ConfigDict(extra="forbid"),
        function_name=(Literal[names], ...),
    )


def _inline(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            return _inline(defs[ref.split("/")[-1]], defs)
        return {k: _inline(v, defs) for k, v in node.items() if k not in ("title", "$defs")}
    if isinstance(node, list):
        return [_inline(item, defs) for item in node]
    return node


@lru_cache(maxsize=None)
def _schema_cached(model: type[BaseModel]) -> dict[str, Any]:
    raw = model.model_json_schema()
    return _inline(raw, raw.get("$defs", {}))


def json_schema_for(model: type[BaseModel]) -> dict[str, Any]:
    """Self-contained JSON Schema (no $ref, no titles) for a reply model."""
    import copy
    return copy.deepcopy(_schema_cached(model))
