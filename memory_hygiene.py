"""Memory hygiene: validation on the way in, cleanup for what is already there.

An audit of the live store found that roughly half of the 135 stored "facts"
were not facts at all:

  40  markdown bullets that were never stripped   ("* User's Name: Nithiish")
  22  the LLM's own preamble line stored verbatim ("Here are the extracted facts:")
  19  exact duplicates                            ("User's name: Nithiish" x4)
   6  non-canonical free text
   4  null/placeholder values                     ("User's institution: not stated")

Semantic search always returns its top_k regardless of quality, so this junk
was being retrieved and fed to the model as though it were ground truth. The
fix has two halves, and both are necessary:

  1. `normalize_fact()` — a gate on the way IN, wired into memory.store(), so
     none of these shapes can be written again.
  2. `clean_store()` — a sweep of what is ALREADY stored, because fixing the
     writer does nothing about the 68 bad rows sitting in the database today.

Design note: this rejects rather than repairs anything ambiguous. A fact that
cannot be confidently normalized is dropped, because a wrong fact asserted
confidently is worse for this assistant than a missing one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from zedek_logger import get_logger

log = get_logger("memory_hygiene")

# The LLM's lead-in sentence before a list. Never a fact.
_PREAMBLE_RE = re.compile(
    r"^\s*(here are|here is|the following|these are|below are|sure[,!]|okay[,!])",
    re.IGNORECASE,
)

# Leading markdown bullets, numbering, and quoting that survived extraction.
_BULLET_RE = re.compile(r"^\s*(?:[-*•‣]|\d+[.)])\s*")

# Values that assert nothing. Matched against the VALUE half of "key: value".
_NULL_VALUE_RE = re.compile(
    r"^\s*(not stated|not specified|not provided|not given|not mentioned|"
    r"unknown|unclear|none|n/?a|null|undefined|tbd|"
    r"<[^>]*>)\s*\.?\s*$",
    re.IGNORECASE,
)

# A retraction that was stored as text instead of deleting the row.
_FALSE_SUFFIX_RE = re.compile(r":\s*false\s*$", re.IGNORECASE)

# Template placeholders the correction flow leaked into storage verbatim.
_PLACEHOLDER_RE = re.compile(r"<\s*(new value|value|attribute|name|.*?)\s*>")

MIN_FACT_LENGTH = 5
MAX_FACT_LENGTH = 500


@dataclass
class CleanupReport:
    """What a cleanup pass found and (optionally) removed."""

    scanned: int = 0
    rejected: int = 0
    duplicates: int = 0
    deleted_ids: list[str] = field(default_factory=list)
    repaired: list[tuple[str, str, str]] = field(default_factory=list)  # (id, before, after)
    reasons: dict[str, int] = field(default_factory=dict)
    samples: list[tuple[str, str]] = field(default_factory=list)

    def note(self, reason: str, text: str) -> None:
        self.reasons[reason] = self.reasons.get(reason, 0) + 1
        if len(self.samples) < 15:
            self.samples.append((reason, text[:90]))

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned": self.scanned,
            "rejected": self.rejected,
            "duplicates": self.duplicates,
            "deleted": len(self.deleted_ids),
            "repaired": len(self.repaired),
            "reasons": self.reasons,
        }


def normalize_fact(text: str) -> tuple[str | None, str]:
    """Clean a candidate fact, or reject it.

    Returns (normalized_text, reason). `normalized_text` is None when the
    candidate should not be stored at all, and `reason` says why — callers log
    it, and the cleanup sweep counts it.
    """
    if not text or not isinstance(text, str):
        return None, "empty"

    cleaned = text.strip()

    # Strip a leading bullet/number, then re-trim and drop wrapping quotes.
    cleaned = _BULLET_RE.sub("", cleaned).strip()
    cleaned = cleaned.strip('"').strip("'").strip()
    # Collapse internal whitespace so near-duplicates compare equal.
    cleaned = re.sub(r"\s+", " ", cleaned)

    if not cleaned:
        return None, "empty_after_normalization"
    if len(cleaned) < MIN_FACT_LENGTH:
        return None, "too_short"
    if len(cleaned) > MAX_FACT_LENGTH:
        return None, "too_long"
    if _PREAMBLE_RE.match(cleaned):
        return None, "llm_preamble"
    if _FALSE_SUFFIX_RE.search(cleaned):
        return None, "retraction_stored_as_text"
    if _PLACEHOLDER_RE.search(cleaned):
        return None, "unresolved_template_placeholder"

    # A line that is only a heading ("Extracted facts:") carries no value.
    if cleaned.endswith(":"):
        return None, "heading_without_value"

    # For canonical "key: value" facts, reject an empty or null-ish value.
    if ":" in cleaned:
        _, _, value = cleaned.partition(":")
        if not value.strip():
            return None, "empty_value"
        if _NULL_VALUE_RE.match(value):
            return None, "null_value"

    return cleaned, "ok"


def is_valid_fact(text: str) -> bool:
    """Convenience predicate for callers that only need a yes/no."""
    normalized, _ = normalize_fact(text)
    return normalized is not None


def _dedup_key(text: str) -> str:
    """Key for duplicate detection: case- and punctuation-insensitive."""
    return re.sub(r"[^\w\s]", "", (text or "").lower()).strip()


def clean_store(domain: str = "personal", dry_run: bool = True,
                drop_conversations: bool = False) -> CleanupReport:
    """Sweep an existing collection for junk facts and duplicates.

    Defaults to `dry_run=True`: it reports what it WOULD remove and deletes
    nothing. Deleting stored memories is not something to do implicitly, so the
    caller has to ask for it explicitly.

    `drop_conversations` additionally removes raw conversation turns, which the
    tiered-memory design says should never have been persisted — only distilled
    facts belong in long-term storage.
    """
    import memory

    report = CleanupReport()
    collection = memory._get_collection(domain)
    stored = collection.get()

    documents = stored.get("documents", []) or []
    metadatas = stored.get("metadatas", []) or []
    ids = stored.get("ids", []) or []

    seen: set[str] = set()
    to_delete: list[str] = []

    for document, metadata, item_id in zip(documents, metadatas, ids):
        report.scanned += 1
        content_type = (metadata or {}).get("content_type", "fact")

        if content_type != "fact":
            if drop_conversations and content_type == "conversation":
                to_delete.append(item_id)
                report.note("raw_conversation_turn", document)
            continue

        normalized, reason = normalize_fact(document)
        if normalized is None:
            to_delete.append(item_id)
            report.rejected += 1
            report.note(reason, document)
            continue

        key = _dedup_key(normalized)
        if key in seen:
            to_delete.append(item_id)
            report.duplicates += 1
            report.note("duplicate", document)
            continue
        seen.add(key)

        # Salvageable but dirty — e.g. "* User's Name: Nithiish". Deleting these
        # would lose real information, so rewrite them in place instead.
        if normalized != document:
            report.repaired.append((item_id, document, normalized))

    report.deleted_ids = to_delete

    if not dry_run:
        if to_delete:
            memory.delete_by_ids(to_delete, domain=domain)
        if report.repaired:
            collection.update(
                ids=[item_id for item_id, _, _ in report.repaired],
                documents=[after for _, _, after in report.repaired],
            )
            memory._invalidate_keyword_index(domain)  # in-place rewrite keeps the count
        log.info("memory_cleanup_applied", extra={
            "domain": domain, "deleted": len(to_delete), "repaired": len(report.repaired),
        })
    else:
        log.info("memory_cleanup_dry_run", extra={
            "domain": domain, "would_delete": len(to_delete),
            "would_repair": len(report.repaired),
        })

    return report


def format_cleanup_report(report: CleanupReport, domain: str, dry_run: bool) -> str:
    """Render a cleanup report for the terminal."""
    verb = "Would remove" if dry_run else "Removed"
    lines = [
        f"Memory hygiene — domain '{domain}'",
        f"  scanned: {report.scanned}",
        f"  {verb}: {len(report.deleted_ids)} "
        f"({report.rejected} invalid, {report.duplicates} duplicate)",
        f"  {'Would repair' if dry_run else 'Repaired'} in place: {len(report.repaired)}",
    ]
    if report.repaired:
        lines.append("  repairs:")
        for _, before, after in report.repaired[:5]:
            lines.append(f"    {before[:50]!r} -> {after[:50]!r}")
    if report.reasons:
        lines.append("  by reason:")
        for reason, count in sorted(report.reasons.items(), key=lambda kv: -kv[1]):
            lines.append(f"    {reason}: {count}")
    if report.samples:
        lines.append("  samples:")
        for reason, text in report.samples[:10]:
            lines.append(f"    [{reason}] {text}")
    if dry_run and report.deleted_ids:
        lines.append("\n  Dry run — nothing was deleted. Re-run with dry_run=False to apply.")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    apply_changes = "--apply" in sys.argv
    include_conversations = "--drop-conversations" in sys.argv

    for domain_name in ("personal", "academic"):
        result = clean_store(
            domain=domain_name,
            dry_run=not apply_changes,
            drop_conversations=include_conversations,
        )
        print(format_cleanup_report(result, domain_name, dry_run=not apply_changes))
        print()
