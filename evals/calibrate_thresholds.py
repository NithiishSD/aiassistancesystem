"""Per-intent Layer-1 thresholds from the golden DEV slice (ROADMAP A5).

For every route, shows how many DEV requests it would claim correctly and
wrongly at each threshold, and recommends the lowest threshold that is still
0.05 above the highest-scoring wrong match. The TEST slice is never read here:
it stays held out, and tests/test_routing_eval.py gates the result against it.

Thresholds do not change WHICH route wins (that is the sum of phrase scores),
only whether the winner is trusted without the LLM, so each route can be read
off on its own.

Usage:
    python evals/calibrate_thresholds.py
Apply a recommendation by setting `threshold:` in capabilities/<intent>.yaml,
then run the routing eval on both slices.
"""

from __future__ import annotations

import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "evals"))

import classifier as clf  # noqa: E402
import routing_eval as ev  # noqa: E402

GRID = (0.40, 0.45, 0.50, 0.55, 0.60, 0.65)
MARGIN = 0.05        # distance kept above the best-scoring wrong match
FLOOR = 0.45         # never recommend below this, however clean DEV looks
STEP = 0.05


def scored_rows(router, rows: list[dict[str, str]]) -> list[tuple[str, float, str]]:
    """(winning route, its best phrase score, true intent) for rows that reach Layer 1."""
    data = []
    for row in rows:
        text = row["utterance"]
        if clf.is_acknowledgement(text) or clf._is_unsupported_action_request(text):
            continue
        route, scores = router._retrieve_top_route(router._encode(text=text))
        if route is not None and scores:
            data.append((route.name, float(max(scores)), row["intent"]))
    return data


def recommend(matches: list[tuple[float, bool]], current: float) -> float:
    """Lowest grid threshold >= FLOOR that clears every wrong match by MARGIN,
    never above `current`, and only if it claims more correct requests. A
    route that is already wrong above its current threshold is left alone:
    its phrases attract the wrong requests, and a lower bar only adds more."""
    if any(not ok and score > current for score, ok in matches):
        return current
    worst_wrong = max((score for score, ok in matches if not ok), default=0.0)
    candidate = max(FLOOR, math.ceil((worst_wrong + MARGIN) / STEP - 1e-9) * STEP)
    candidate = round(candidate, 2)
    if candidate >= current:
        return current
    gained = sum(1 for score, ok in matches if ok and candidate < score <= current)
    return candidate if gained else current


def table(data: list[tuple[str, float, str]]) -> list[dict]:
    rows = []
    for name in sorted({top for top, _, _ in data}):
        if name == clf.DEFAULT_INTENT:
            continue
        matches = [(score, true == name) for top, score, true in data if top == name]
        current = clf.intent_threshold(name)
        rows.append({
            "intent": name, "current": current, "recommended": recommend(matches, current),
            "by_threshold": {t: (sum(1 for s, ok in matches if s > t and ok),
                                 sum(1 for s, ok in matches if s > t and not ok)) for t in GRID},
        })
    return rows


def main() -> int:
    data = scored_rows(ev.build_static_router(), ev.load_golden(split="dev"))
    print(f"DEV rows reaching Layer 1: {len(data)}   (correct/wrong claimed above each threshold)\n")
    print(f"{'intent':26} {'now':>5} {'rec':>5}   " + "  ".join(f"{t:.2f}" for t in GRID))
    for row in table(data):
        cells = "  ".join(f"{c:>2}/{w}" for c, w in row["by_threshold"].values())
        flag = "  <- lower" if row["recommended"] < row["current"] else ""
        print(f"{row['intent']:26} {row['current']:>5.2f} {row['recommended']:>5.2f}   {cells}{flag}")
    print("\nA recommendation is a candidate, not a decision: apply it, then run the routing eval on the "
          "TEST slice. top_memory_processes at 0.45 was tried and rejected there (precision 0.92 -> 0.85).")
    print("Not lowered by hand, whatever DEV says: intents whose wrong match acts on its own "
          "(remember_fact, correct_fact, open_application, unsupported).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
