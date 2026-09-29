"""Latency benchmark for the memory reranker (spec: median ≤ 300 ms for 20 candidates).

Run: ./zedek-env/bin/python evals/bench_reranker.py
"""

import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import reranker  # noqa: E402

FACTS = [
    "User's college: PSG College of Technology",
    "User's name: Nithiish",
    "User's Current Semester: 5th semester",
    "User's program: Software Systems",
    "User's department: AMCS",
    "User's subjects: Machine Learning, Java",
    "User's favorite programming language: Python",
    "User's Location: Greenfield, Riverside district, Hillview taluk",
    "User's goal: Building an AI assistant system.",
    "User's preferred application: Brave",
    "User's studying for: data structures exam",
    "User's exam frequency: weekly",
    "User's interest: Astro (Frontend Framework)",
    "User's current field of study: computer science",
    "User's personal assistant: Zedek",
    "User's Operating System: Linux",
    "User's target companies: product-based companies",
    "User's hobby: cricket",
    "User's preferred study time: late night",
    "User's placement preparation focus: DSA",
]
QUERIES = [
    "what college do I study at",
    "what is my name",
    "which semester am I in",
    "where do I live",
    "what languages do I like",
    "what am I building",
    "what exam am I preparing for",
    "which browser do I use",
    "what is a binary search tree",
    "what should I focus on for placements",
]
RUNS_PER_QUERY = 3
BUDGET_MS = 300.0


def main() -> int:
    t0 = time.perf_counter()
    if reranker.score("warm up", FACTS[:2]) is None:
        print("Reranker model not installed — run setup.sh step 7.")
        return 2
    load_ms = (time.perf_counter() - t0) * 1000

    timings = []
    for query in QUERIES:
        for _ in range(RUNS_PER_QUERY):
            start = time.perf_counter()
            reranker.score(query, FACTS)
            timings.append((time.perf_counter() - start) * 1000)

    timings.sort()
    median = statistics.median(timings)
    p95 = timings[int(0.95 * (len(timings) - 1))]
    print(f"first load + warm-up: {load_ms:.0f} ms")
    print(f"ranking {len(FACTS)} candidates, {len(timings)} runs: median {median:.1f} ms, p95 {p95:.1f} ms")
    print(f"budget {BUDGET_MS:.0f} ms median: {'PASS' if median <= BUDGET_MS else 'FAIL'}")
    return 0 if median <= BUDGET_MS else 1


if __name__ == "__main__":
    sys.exit(main())
