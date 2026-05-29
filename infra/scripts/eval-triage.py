#!/usr/bin/env python3
"""Run the GPT-4o-mini Triage eval and gate on >90% intent accuracy (CM-30 AC #7).

Jira: CM-30  | Epic: CM-5 (Agent 1 — Enhanced Triage Agent)  | Phase 1

Operator-run, post-deploy (mirrors infra/scripts/seed-langsmith-dataset.py).
Loads the labelled dataset (default: tests/eval/triage_seed.jsonl), runs the
real LLMTriageClassifier over every example, prints a per-dimension accuracy
report, and EXITS 1 IF INTENT ACCURACY IS NOT > 90% — so this doubles as a
CI-able regression gate once a Python job + OpenAI key are wired.

Usage::

    # Prereq: a real OpenAI key (populate the KV `azure-openai-key` secret /
    # set OPENAI_API_KEY locally; see docs/TRIAGE.md).
    export OPENAI_API_KEY=<key>
    python infra/scripts/eval-triage.py                 # uses the seed file
    python infra/scripts/eval-triage.py --seed-file <path> --limit 50

Exit codes: 0 = intent accuracy > 90%; 1 = below target or misconfiguration.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Make `agents` importable when run as a plain script from the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from agents.orchestrator.eval import (  # noqa: E402
    INTENT_ACCURACY_TARGET,
    run_eval,
)
from agents.orchestrator.triage import (  # noqa: E402
    SECRET_PLACEHOLDER,
    LLMTriageClassifier,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the CM-30 Triage eval.")
    p.add_argument(
        "--seed-file",
        default=str(_REPO_ROOT / "tests" / "eval" / "triage_seed.jsonl"),
        help="JSONL dataset. Default: tests/eval/triage_seed.jsonl",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Only score the first N examples (0 = all). Useful for a cheap smoke run.",
    )
    p.add_argument(
        "--model",
        default="gpt-4o-mini",
        help="OpenAI model id. Default: gpt-4o-mini (AC #8).",
    )
    p.add_argument(
        "--show-mismatches",
        action="store_true",
        help="Print every intent mismatch (predicted vs. reference) after the report.",
    )
    return p.parse_args()


def _load_examples(path: Path) -> list[dict]:
    examples: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line_num, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                ex = json.loads(line)
            except json.JSONDecodeError as e:
                sys.stderr.write(f"FAIL: {path}:{line_num} is not valid JSON: {e}\n")
                raise SystemExit(1) from e
            examples.append(ex)
    return examples


def main() -> int:
    args = parse_args()

    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key or key == SECRET_PLACEHOLDER:
        sys.stderr.write(
            "OPENAI_API_KEY is missing or holds REPLACE-ME. Populate the key "
            "(see docs/TRIAGE.md), export it locally, and re-run.\n"
        )
        return 1

    seed_path = Path(args.seed_file)
    if not seed_path.is_file():
        sys.stderr.write(f"Seed file not found: {seed_path}\n")
        return 1

    examples = _load_examples(seed_path)
    if args.limit > 0:
        examples = examples[: args.limit]
    if not examples:
        sys.stderr.write(f"No examples to score in {seed_path}.\n")
        return 1

    classifier = LLMTriageClassifier(model=args.model)
    report = run_eval(classifier, examples)

    print(f"Triage eval — {report.n} examples, model={args.model}")
    print(f"  intent  accuracy: {report.accuracy['intent']:.1%}  (gate: > {INTENT_ACCURACY_TARGET:.0%})")
    print(f"  urgency accuracy: {report.accuracy['urgency']:.1%}  (diagnostic)")
    print(f"  tone    accuracy: {report.accuracy['tone']:.1%}  (diagnostic)")

    if args.show_mismatches:
        for r in report.mismatches("intent"):
            print(
                f"  MISS intent: predicted={r.predicted['intent']!r} "
                f"ref={r.reference['intent']!r} :: {r.message!r}"
            )

    if report.passed:
        print(f"\nPASS — intent accuracy {report.intent_accuracy:.1%} > {INTENT_ACCURACY_TARGET:.0%}")
        return 0
    print(
        f"\nFAIL — intent accuracy {report.intent_accuracy:.1%} "
        f"did not clear the {INTENT_ACCURACY_TARGET:.0%} gate"
    )
    return 1


if __name__ == "__main__":  # pragma: no cover  (manual operator run)
    sys.exit(main())
