"""Promote corrected misroutes into the golden set (ROADMAP A4).

When the owner tells Zedek a request went to the wrong place, the correction is
logged to data/misroutes.jsonl (never committed). This lists the logged
requests that are not in the golden set yet and, with --apply, appends them to
the DEV slice under the intent the owner meant. The held-out TEST slice is
never touched.

The golden set is committed to a PUBLIC repository and these are the owner's
real words: read every row before applying, and leave out (--skip N) anything
that names a person, a place or an account.

Usage:
    python evals/promote_misroutes.py                 # list candidates
    python evals/promote_misroutes.py --apply --skip 2 5
    python evals/routing_eval.py --write-baseline     # afterwards: the set changed
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import routing_eval as rev  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def misroutes_path() -> str:
    return os.getenv("ZEDEK_MISROUTES_PATH", os.path.join(_ROOT, "data", "misroutes.jsonl"))


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def read_misroutes(path: str) -> list[dict]:
    """Logged corrections, oldest first. Unreadable lines are skipped."""
    if not os.path.exists(path):
        return []
    entries = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
    return entries


def candidates(entries: list[dict], golden: list[dict[str, str]], classes: list[str],
               router_phrases: set[str]) -> list[dict[str, str]]:
    """Rows worth adding: a known intended intent, and a request that is new to
    the golden set and is not already a router phrase."""
    seen = {_norm(row["utterance"]) for row in golden} | {_norm(p) for p in router_phrases}
    rows = []
    for entry in entries:
        utterance, intended = _norm(entry.get("utterance", "")), entry.get("intended")
        if not utterance or intended not in classes or utterance in seen:
            continue
        seen.add(utterance)
        rows.append({"utterance": utterance, "intent": intended, "split": "dev",
                     "routed_to": str(entry.get("routed_to", ""))})
    return rows


def append_rows(rows: list[dict[str, str]], path: str) -> None:
    with open(path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\r\n")  # the file's existing line ending
        for row in rows:
            writer.writerow([row["utterance"], row["intent"], row["split"]])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--apply", action="store_true", help="append the listed rows to the DEV slice")
    parser.add_argument("--skip", type=int, nargs="*", default=[], help="row numbers to leave out")
    args = parser.parse_args(argv)

    import classifier as clf
    phrases = {p for group in clf.INTENT_UTTERANCES.values() for p in group}
    rows = candidates(read_misroutes(misroutes_path()), rev.load_golden(), rev.classes(), phrases)
    if not rows:
        print("No new corrected misroutes to promote.")
        return 0

    print("This repository is public. Leave out anything that names a person, a place or an account.\n")
    for number, row in enumerate(rows, 1):
        mark = "skip" if number in args.skip else "    "
        print(f"{number:>3} {mark} {row['utterance']!r}: went to {row['routed_to']}, meant {row['intent']}")
    kept = [row for number, row in enumerate(rows, 1) if number not in args.skip]
    if not args.apply:
        print(f"\n{len(kept)} row(s) would be added to the DEV slice. Re-run with --apply to add them.")
        return 0
    append_rows(kept, rev.GOLDEN_PATH)
    print(f"\nAdded {len(kept)} row(s) to the DEV slice of {os.path.relpath(rev.GOLDEN_PATH, _ROOT)}.")
    print("Now run: python evals/routing_eval.py --write-baseline   (the golden set changed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
