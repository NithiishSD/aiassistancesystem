"""OPT-IN end-to-end routing evaluation, including the LLM fallback.

Spends free-tier LLM quota, so it is never part of pytest (no test_ prefix,
lives under evals/). It runs classify_intent() - with the Layer-2 LLM - on the
golden rows that Layer 1 escalates, and reports how many the LLM rescues.

Usage:
    python evals/routing_eval_e2e.py [--slice test] [--limit 60] [--sleep 1.5]
                                     [--router static|production]

--router static      evaluate against the static router (matches the gate)
--router production  use the live router (static + learned + MCP phrases)
"""

from __future__ import annotations

import argparse
import collections
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import classifier as clf  # noqa: E402
import routing_eval as rev  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--slice", choices=["dev", "test", "all"], default="test")
    parser.add_argument("--limit", type=int, default=60, help="max LLM calls (quota guard)")
    parser.add_argument("--sleep", type=float, default=1.5, help="seconds between LLM calls")
    parser.add_argument("--router", choices=["static", "production"], default="static")
    args = parser.parse_args(argv)

    router = rev.build_static_router() if args.router == "static" else clf._intent_router
    rows = rev.load_golden(split=args.slice)
    escalated = [r for r in rows if rev.classify_offline(router, r["utterance"])[0] == rev.ESCALATE]
    print(f"{len(escalated)}/{len(rows)} rows escalate at Layer 1 ({args.router} router); "
          f"sending up to {args.limit} to the LLM")

    per_class = collections.defaultdict(lambda: [0, 0])  # rescued, tried
    original_router = clf._intent_router
    clf._intent_router = router
    try:
        for row in escalated[: args.limit]:
            clf._ROUTING_CACHE.pop((row["utterance"] or "").strip().lower(), None)
            decision = clf.classify_intent(row["utterance"])
            got = decision.get("function") or rev.GENERAL
            per_class[row["intent"]][1] += 1
            if got == row["intent"]:
                per_class[row["intent"]][0] += 1
            else:
                print(f"  [{row['intent']} -> {got}] {row['utterance']}")
            time.sleep(args.sleep)
    finally:
        clf._intent_router = original_router

    rescued = sum(v[0] for v in per_class.values())
    tried = sum(v[1] for v in per_class.values())
    print(f"\nLLM rescued {rescued}/{tried} escalated rows")
    for label, (ok, n) in sorted(per_class.items()):
        print(f"  {label:24s} {ok}/{n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
