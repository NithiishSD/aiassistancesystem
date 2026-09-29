"""Offline routing evaluation (OpenSpec change: add-routing-eval, ROADMAP A4).

Runs the golden set through the pre-LLM path of classifier.classify_intent()
(acknowledgement guard, unsupported guard, Layer 1) on a STATIC router built
only from the classifier's own phrases. Runtime-learned and MCP-registered
phrases live in data/dynamic_utterances.json and differ per machine, so they
are excluded to keep the numbers reproducible. The LLM fallback is never
called: requests Layer 1 cannot resolve are recorded as ESCALATE.

Usage:
    python evals/routing_eval.py [--slice dev|test|all]
    python evals/routing_eval.py --write-baseline         # deliberate: resets the gate
    python evals/routing_eval.py --seed-must-stay-local   # deliberate: rewrites the list
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
from datetime import date
from typing import Any

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import classifier as clf  # noqa: E402

EVALS_DIR = os.path.join(_ROOT, "evals")
GOLDEN_PATH = os.path.join(EVALS_DIR, "routing_golden.csv")
BASELINE_PATH = os.path.join(EVALS_DIR, "routing_baseline.json")
MUST_STAY_LOCAL_PATH = os.path.join(EVALS_DIR, "must_stay_local.csv")

ESCALATE = "ESCALATE"
GENERAL = clf.DEFAULT_INTENT
REGRESSION_TOLERANCE = 0.02

# Core local actions that must never need the LLM (design D6).
CORE_LOCAL_INTENTS = [
    "search_files", "disk_usage_by_folder", "top_memory_processes", "free_space_summary",
    "directory_size", "list_processes_detailed", "open_application", "system_inspect",
]


NEAR_DUPLICATE_THRESHOLD = 0.90


def near_duplicates(phrases: list[str], rows: list[dict[str, str]],
                    threshold: float = NEAR_DUPLICATE_THRESHOLD) -> list[tuple[str, str, float]]:
    """Router phrases that are near-copies of golden rows, by cosine similarity.

    Exact-match leakage checks miss paraphrases like "what's my ip" vs "what's my
    ip address" (0.948); a near-copy of a held-out row inflates the test score
    just as badly as an exact one.
    """
    import numpy as np

    if not phrases or not rows:
        return []
    encoder = clf._get_encoder()
    phrase_vecs = np.array(encoder(list(phrases)), dtype=float)
    row_vecs = np.array(encoder([r["utterance"] for r in rows]), dtype=float)
    phrase_vecs /= np.linalg.norm(phrase_vecs, axis=1, keepdims=True)
    row_vecs /= np.linalg.norm(row_vecs, axis=1, keepdims=True)
    sims = phrase_vecs @ row_vecs.T
    found = []
    for i, phrase in enumerate(phrases):
        j = int(sims[i].argmax())
        if sims[i, j] >= threshold:
            found.append((phrase, rows[j]["utterance"], round(float(sims[i, j]), 3)))
    return found


def classes() -> list[str]:
    return sorted(clf.INTENT_UTTERANCES) + [GENERAL]


def load_golden(path: str = GOLDEN_PATH, split: str = "all") -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if split != "all":
        rows = [r for r in rows if r["split"] == split]
    return rows


def golden_sha256(path: str = GOLDEN_PATH) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def build_static_router():
    return clf._build_intent_router(include_dynamic=False)


def classify_offline(router, text: str) -> tuple[str, float]:
    """Mirror classify_intent() up to, but never including, the LLM fallback."""
    if clf.is_acknowledgement(text):
        return GENERAL, 0.0
    if clf._is_unsupported_action_request(text):
        return "unsupported", 1.0
    name, score = clf._layer1_route_with(router, text)
    if name and name != GENERAL and score >= clf.CONFIDENCE_THRESHOLD:
        return name, score
    return ESCALATE, score


def _metric_label(true_label: str, predicted: str) -> str:
    # Escalating a general question is the correct local behaviour: Layer 1
    # should not claim it for an intent. It is counted as correct for metrics.
    if true_label == GENERAL and predicted == ESCALATE:
        return GENERAL
    return predicted


def evaluate(rows: list[dict[str, str]], router) -> dict[str, Any]:
    from sklearn.metrics import confusion_matrix, precision_recall_fscore_support

    labels = classes()
    y_true, y_raw, scores = [], [], []
    for row in rows:
        predicted, score = classify_offline(router, row["utterance"])
        y_true.append(row["intent"])
        y_raw.append(predicted)
        scores.append(score)
    y_metric = [_metric_label(t, p) for t, p in zip(y_true, y_raw)]

    precision, recall, f1, support = precision_recall_fscore_support(
        y_true, y_metric, labels=labels, zero_division=0)
    per_class = {
        label: {"precision": round(float(p), 4), "recall": round(float(r), 4),
                "f1": round(float(f), 4), "support": int(s)}
        for label, p, r, f, s in zip(labels, precision, recall, f1, support)
    }

    matrix_labels = labels + [ESCALATE]
    matrix = confusion_matrix(y_true, y_raw, labels=matrix_labels).tolist()
    errors = [
        {"utterance": row["utterance"], "expected": t, "got": p, "score": s}
        for row, t, p, m, s in zip(rows, y_true, y_raw, y_metric, scores) if m != t
    ]
    total = len(rows) or 1
    return {
        "per_class": per_class,
        "accuracy": round(sum(1 for t, m in zip(y_true, y_metric) if t == m) / total, 4),
        "escalation_rate": round(sum(1 for p in y_raw if p == ESCALATE) / total, 4),
        "confusion_labels": matrix_labels,
        "confusion_matrix": matrix,
        "errors": errors,
        "n": len(rows),
    }


def compare_to_baseline(result: dict[str, Any], baseline: dict[str, Any],
                        tolerance: float = REGRESSION_TOLERANCE) -> list[str]:
    """Names every class whose precision or recall fell more than `tolerance`."""
    failures = []
    for label, base in baseline["per_class"].items():
        current = result["per_class"].get(label)
        if current is None:
            failures.append(f"{label}: class missing from current results")
            continue
        for metric in ("precision", "recall"):
            if current[metric] < base[metric] - tolerance:
                failures.append(
                    f"{label}: {metric} fell {base[metric]:.3f} -> {current[metric]:.3f}")
    return failures


def load_must_stay_local(path: str = MUST_STAY_LOCAL_PATH) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def must_stay_local_failures(rows: list[dict[str, str]], router) -> list[str]:
    failures = []
    for row in rows:
        predicted, _ = classify_offline(router, row["utterance"])
        if predicted != row["intent"]:
            failures.append(f"{row['utterance']!r}: expected {row['intent']}, got {predicted}")
    return failures


def must_stay_local_gaps(rows: list[dict[str, str]], router) -> list[str]:
    """Core local intents with no dev row that Layer 1 resolves correctly today."""
    passing = {r["intent"] for r in rows if r["split"] == "dev"
               and classify_offline(router, r["utterance"])[0] == r["intent"]}
    return [intent for intent in CORE_LOCAL_INTENTS if intent not in passing]


def seed_must_stay_local(rows: list[dict[str, str]], router, per_intent: int = 2) -> list[dict[str, str]]:
    chosen: list[dict[str, str]] = []
    for intent in CORE_LOCAL_INTENTS:
        passing = [r for r in rows if r["split"] == "dev" and r["intent"] == intent
                   and classify_offline(router, r["utterance"])[0] == intent]
        chosen.extend({"utterance": r["utterance"], "intent": intent} for r in passing[:per_intent])
    return chosen


def baseline_payload(result: dict[str, Any]) -> dict[str, Any]:
    import semantic_router

    return {
        "meta": {
            "golden_sha256": golden_sha256(),
            "static_phrase_count": sum(len(v) for v in clf.INTENT_UTTERANCES.values()),
            "semantic_router_version": getattr(semantic_router, "__version__", "unknown"),
            "date": date.today().isoformat(),
            "slice": "test",
        },
        "per_class": result["per_class"],
        "accuracy": result["accuracy"],
        "escalation_rate": result["escalation_rate"],
    }


def format_report(result: dict[str, Any]) -> str:
    lines = [f"{'class':24s} {'prec':>6s} {'rec':>6s} {'f1':>6s} {'n':>4s}"]
    for label, m in result["per_class"].items():
        lines.append(f"{label:24s} {m['precision']:6.2f} {m['recall']:6.2f} {m['f1']:6.2f} {m['support']:4d}")
    lines.append(f"\naccuracy {result['accuracy']:.3f} | escalated to LLM {result['escalation_rate']:.1%} | n={result['n']}")

    abbrev = [label[:6] for label in result["confusion_labels"]]
    lines.append("\nconfusion matrix (rows = expected, cols = predicted; ESCALA = sent to LLM)")
    lines.append(" " * 24 + " ".join(f"{a:>6s}" for a in abbrev))
    for label, row in zip(result["confusion_labels"][:-1], result["confusion_matrix"]):
        lines.append(f"{label:24s}" + " ".join(f"{v if v else '.':>6}" for v in row))

    lines.append("\nerrors:")
    for err in result["errors"]:
        lines.append(f"  [{err['expected']} -> {err['got']} @ {err['score']:.2f}] {err['utterance']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--slice", choices=["dev", "test", "all"], default="all")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--seed-must-stay-local", action="store_true")
    args = parser.parse_args(argv)

    started = time.perf_counter()
    router = build_static_router()
    rows = load_golden(split="test" if args.write_baseline else args.slice)
    result = evaluate(rows, router)
    print(format_report(result))

    gaps = must_stay_local_gaps(load_golden(), router)
    print(f"\nmust-stay-local gaps (core intents with no locally-resolved dev row): {gaps or 'none'}")

    if args.write_baseline:
        with open(BASELINE_PATH, "w", encoding="utf-8") as handle:
            json.dump(baseline_payload(result), handle, indent=2)
        print(f"\nwrote baseline -> {BASELINE_PATH}")

    if args.seed_must_stay_local:
        seeded = seed_must_stay_local(load_golden(), router)
        with open(MUST_STAY_LOCAL_PATH, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["utterance", "intent"])
            writer.writeheader()
            writer.writerows(seeded)
        print(f"wrote {len(seeded)} must-stay-local rows -> {MUST_STAY_LOCAL_PATH}")

    print(f"\n({time.perf_counter() - started:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
