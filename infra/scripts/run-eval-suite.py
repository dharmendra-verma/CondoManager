#!/usr/bin/env python3
"""Run the unified PRD eval suite and print the scorecard (CM-39).

Offline (default) uses each agent's deterministic stand-in — no key, no network
— and gates the deterministic PRD metrics (dedup precision, vendor accuracy,
escalation legal recall, knowledge self-service + hallucination). ``--live``
additionally gates the model-dependent metrics (Triage >90% intent accuracy)
and requires ``OPENAI_API_KEY``.

    python infra/scripts/run-eval-suite.py            # offline gates
    OPENAI_API_KEY=sk-... python infra/scripts/run-eval-suite.py --live

Exit 0 == all enforced gates passed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agents.eval import run_suite  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="CondoManager PRD eval suite.")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Gate model-dependent metrics (needs OPENAI_API_KEY).",
    )
    args = parser.parse_args()

    report = run_suite(live=args.live)
    print(report.scorecard())
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
