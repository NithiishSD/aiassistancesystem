"""
Which stored facts describe the same single-valued thing (ROADMAP A2 follow-up).

A fact is stored as "User's <attribute>: <value>". Some attributes can hold
only one value at a time (a name, a college, where the user lives); others
hold many (projects, subjects, goals, interests). When a new value arrives for
a single-valued attribute, the old one stopped being true and is superseded;
for anything else both are kept.

The decision is a fixed table, not a model call, and it is deliberately
narrow: an attribute that is not listed here is treated as multi-valued, so
the worst a gap can do is leave two facts side by side, as before.
"""

from __future__ import annotations

import re

_FACT_RE = re.compile(r"^\s*user'?s\s+(?P<attribute>[^:]{1,80}):\s*(?P<value>.+)$", re.IGNORECASE | re.DOTALL)
_LEADING_RE = re.compile(r"^(current|present)\s+")

# key -> attribute names that mean it. Matched against the whole attribute name
# (after dropping a leading "current"), so "lab partner's name", "location
# (alternate)" or "target college" do not match.
_SINGLE_VALUED: dict[str, str] = {
    "name": r"(full |first |preferred )?name",
    "college": r"(college|university|institution|educational institution|school)( name)?",
    "degree": r"(degree|degree program|academic program|program|programme)",
    "field of study": r"(field of study|major|branch|department|specialization|specialisation)",
    "semester": r"semester",
    "year of study": r"(year|year of study|academic year)",
    "location": r"(location|residence|city|home city|place of residence)",
    "address": r"(home )?address",
    "hometown": r"(hometown|home town|native place)",
    "age": r"age",
    "date of birth": r"(date of birth|birthday|birth date|dob)",
    "email": r"(email|e-mail|email address)",
    "phone": r"(phone|phone number|mobile|mobile number)",
    "roll number": r"(roll number|roll no|register number|registration number)",
    "cgpa": r"(cgpa|gpa)",
    "operating system": r"(operating system|os)",
    "code editor": r"(preferred |default )?(code )?editor",
    "browser": r"(preferred |default )?(web )?browser( application)?",
    "tone": r"preferred tone",
}
_SINGLE_VALUED_RES = {key: re.compile(f"^(?:{pattern})$") for key, pattern in _SINGLE_VALUED.items()}


def parse(text: str) -> tuple[str, str] | None:
    """(attribute, value) of a stored fact, or None when it is not in that form."""
    match = _FACT_RE.match(text or "")
    if not match:
        return None
    return match.group("attribute").strip(), match.group("value").strip()


def single_valued_key(text: str) -> str | None:
    """The single-valued attribute a fact is about, or None when it may hold many values."""
    parsed = parse(text)
    if parsed is None:
        return None
    attribute = _LEADING_RE.sub("", " ".join(parsed[0].lower().split()))
    for key, pattern in _SINGLE_VALUED_RES.items():
        if pattern.match(attribute):
            return key
    return None


def value_key(text: str) -> str:
    """The fact's value, compared without case, punctuation, spacing, or the
    attribute's own words: "5th semester" and "5th" are the same semester."""
    parsed = parse(text)
    value = parsed[1] if parsed else (text or "")
    words = re.sub(r"[^\w\s]", "", value.lower()).split()
    own = set((single_valued_key(text) or "").split()) | {"a", "an", "the"}
    return " ".join(w for w in words if w not in own) or " ".join(words)
