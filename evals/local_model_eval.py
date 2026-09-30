"""OPT-IN head-to-head of local models on Layer-2 routing (ROADMAP D3).

Cloud is forced off, so every Layer-2 call goes to the local model. Runs the
DEV slice only (the test slice stays held out): for each golden row that Layer 1
escalates, the local model picks an intent via the production structured call.
Reports intent accuracy, structured-output validity and median latency. Never
part of pytest; it takes minutes per model on a laptop CPU.

Usage:
    python evals/local_model_eval.py --models llama3.1:8b qwen3:8b [--limit 60]
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["ALLOW_CLOUD"] = "false"

import classifier as clf  # noqa: E402
import llm_provider as lp  # noqa: E402
import routing_eval as rev  # noqa: E402


def evaluate(model: str, rows: list[dict[str, str]]) -> dict[str, float]:
    lp.LOCAL_MODEL = model
    lp._SCHEMA_UNSUPPORTED.discard(("local", model))
    failures = {"n": 0}
    real_structured = lp.generate_structured

    def counting_structured(*args, **kwargs):
        try:
            return real_structured(*args, **kwargs)
        except Exception:
            failures["n"] += 1
            raise

    lp.local_chat([{"role": "user", "content": "ok"}])  # load the model before timing
    lp.generate_structured = counting_structured
    correct, latencies = 0, []
    try:
        for row in rows:
            clf._ROUTING_CACHE.pop((row["utterance"] or "").strip().lower(), None)
            started = time.perf_counter()
            decision = clf.query_llm_with_tools(row["utterance"])
            latencies.append(time.perf_counter() - started)
            got = decision.get("function") or rev.GENERAL
            if got == row["intent"]:
                correct += 1
            else:
                print(f"  [{model}] [{row['intent']} -> {got}] {row['utterance']}")
    finally:
        lp.generate_structured = real_structured
    n = len(rows)
    return {"n": n, "accuracy": correct / n, "valid": 1 - failures["n"] / n,
            "median_s": statistics.median(latencies), "p90_s": sorted(latencies)[int(0.9 * (n - 1))]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--limit", type=int, default=60)
    args = parser.parse_args(argv)

    router = rev.build_static_router()
    rows = [r for r in rev.load_golden(split="dev")
            if rev.classify_offline(router, r["utterance"])[0] == rev.ESCALATE][: args.limit]
    print(f"{len(rows)} escalated dev rows")
    results = {model: evaluate(model, rows) for model in args.models}
    print(f"\n{'model':16s} {'accuracy':>9s} {'valid':>7s} {'median':>8s} {'p90':>7s}")
    for model, r in results.items():
        print(f"{model:16s} {r['accuracy']:9.3f} {r['valid']:7.3f} {r['median_s']:7.2f}s {r['p90_s']:6.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
