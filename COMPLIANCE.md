# Zedek — Compliance Position

**Last revised:** 2026-09-29. **Not legal advice** — this records the project's reasoning
so that a change in circumstances triggers a review instead of going unnoticed.

---

## Current position: out of scope, for two independent reasons

### India — Digital Personal Data Protection Act, 2023 (DPDP)

**1. The personal/domestic-purpose exemption applies.**
DPDP §3(c)(i) excludes personal data processed by an individual "for any personal or
domestic purpose." India's wording ("any") is deliberately broader than GDPR's ("purely").
Zedek is used by one person, for himself, on his own machine.

**2. The substantive obligations are not yet in force regardless.**
Definitions and Data Protection Board provisions operate now. The duties that would
actually bind a system like this — notice and consent, data-principal rights, breach
notification, retention limits — commence on **13 May 2027** under the DPDP Rules, 2025.

So today there is nothing to comply with. That is a genuine position, not a gap.

### EU AI Act — not applicable

Zedek is a *deployer* of general-purpose AI models, not a *provider* of one, and it is not
placed on the EU market. The GPAI obligations (Arts. 51–55) bind the model providers
(Google, Groq, NVIDIA, etc.), not the people calling their APIs. Art. 50 transparency
duties apply only to EU-market systems, and the high-risk tier has been deferred to
2 December 2027.

---

## Tripwires — any ONE of these ends the exemption

The DPDP exemption is about *how the data is used*, not how the software is built. It
lapses the moment any of these becomes true:

| Tripwire | Why it matters |
|---|---|
| **Zedek is distributed, published, or offered to anyone else** | Personal-use exemptions do not survive publication to an indefinite audience (cf. *Bodil Lindqvist*). |
| **Another person's data enters the store** | e.g. a classmate's details land in ChromaDB. Processing a non-consenting third party's data is no longer purely domestic (cf. *František Ryneš*). |
| **It generates revenue** | Commercial processing is not personal or domestic. |

**If a tripwire trips**, from 13 May 2027 the obligations attach: notice and consent,
correction and erasure rights, breach notification to the Data Protection Board, and
retention limits.

---

## What already exists that would make compliance cheap later

This is the payoff of building carefully early. None of it was built for compliance, but
it all maps onto what the obligations would require:

| DPDP obligation | Already in place |
|---|---|
| Data-principal separation | `user_id` is threaded through every memory row from day one. |
| Correction and erasure | `_handle_correction()` marks a corrected or retracted fact as no longer valid (`memory.invalidate()`), which keeps a dated history of changes and stops the fact being used. **History is still personal data:** erasure is `python memory_hygiene.py --purge-invalidated --apply` (or `memory.delete_by_ids()`), and a spoken "delete that" does not erase by itself — the reply says so. |
| Security safeguards | Encrypted vault (Fernet + PBKDF2, 0600), password gate with lockout, tier gate, sandboxed execution. |
| Audit trail | Watchdog writes an append-only JSONL trail with sensitive values redacted before they reach disk. |
| Data minimisation | Session turns are distilled rather than stored raw; memory hygiene rejects non-facts. |

## Honest residual exposure

- **Personal data is sent to cloud LLM providers.** Every Gemini/Groq/NVIDIA/OpenRouter/
  Cerebras call carries prompt content, which can include stored facts about the user.
  Exempt today because the user is the data principal and chose these providers. It would
  **not** be fine if the data were a third party's. `ALLOW_CLOUD=false` forces local-only
  and is the escape hatch. What is sent: facts that relevance selection returned for the
  question, plus, for questions about the user (I / my / me) only, a core profile of at most
  12 facts (name, college, degree, semester, subjects, goals). Location, contact details and
  identifiers are never in that profile. A general-knowledge question carries no stored facts.
- **36 raw conversation turns were persisted** before the tiered-memory redesign.
  `memory_hygiene.py --drop-conversations` removes them.

## Review triggers

Re-read this file when: Zedek is shared with anyone; any feature stores data about a
person other than the user; the DPDP Rules are amended; or **13 May 2027**.

---

**Sources:** [DPDP Rules 2025](https://www.dpdpa.com/dpdparules.html) ·
[TLH — DPDP §3(c)(i) personal/domestic exemption](https://tlh.law/insights/dpdp-acts-exemption-of-personal-and-domestic-purpose) ·
[Gibson Dunn — EU AI Act Omnibus](https://www.gibsondunn.com/eu-ai-act-omnibus-agreement-postponed-high-risk-deadlines-and-other-key-changes/)
