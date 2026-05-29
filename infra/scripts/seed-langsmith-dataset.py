#!/usr/bin/env python3
"""Seed the LangSmith eval dataset from tests/eval/triage_seed.jsonl.

Jira: CM-23  | Epic: Observability  | Phase 0

Operator-run, post-deploy. Reads the JSONL seed fixture under
tests/eval/ and uploads it to LangSmith as a dataset named
``condomanager-triage-seed-<env>``. Idempotent: skips example uploads
whose ``inputs`` fingerprint already exists in the dataset, and skips
dataset creation entirely if the dataset already exists.

Usage::

    # Prereqs:
    #   - LANGCHAIN_API_KEY set to a real key (populate KV first; then
    #     export from a local terminal)
    #   - LANGCHAIN_PROJECT optional; not used by this script directly
    export LANGCHAIN_API_KEY=<key>
    python infra/scripts/seed-langsmith-dataset.py --env dev

    # Re-runs are safe (no duplicate examples, no duplicate datasets):
    python infra/scripts/seed-langsmith-dataset.py --env dev

Exit codes: 0 = success, 1 = misconfiguration or upload failure.
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

from agents.observability.langsmith import (  # noqa: E402
    SECRET_PLACEHOLDER,
    ensure_dataset,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Seed CM-23 LangSmith eval dataset.")
    p.add_argument(
        "--env",
        choices=("dev", "prod"),
        required=True,
        help="Environment label. Becomes the suffix on the dataset name.",
    )
    p.add_argument(
        "--seed-file",
        default=str(_REPO_ROOT / "tests" / "eval" / "triage_seed.jsonl"),
        help="JSONL seed file. Default: tests/eval/triage_seed.jsonl",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()

    # Same env-var preflight as the demo, but only the key matters here.
    # Tracing V2 isn't needed because this script writes to the LangSmith
    # API directly; it doesn't itself produce a LangChain trace.
    key = os.environ.get("LANGCHAIN_API_KEY", "").strip()
    if not key or key == SECRET_PLACEHOLDER:
        sys.stderr.write(
            "LANGCHAIN_API_KEY is missing or holds REPLACE-ME. Populate the "
            "KV secret first (see docs/OBSERVABILITY.md), export the value "
            "locally, and re-run.\n"
        )
        return 1

    seed_path = Path(args.seed_file)
    if not seed_path.is_file():
        sys.stderr.write(f"Seed file not found: {seed_path}\n")
        return 1

    examples: list[dict[str, object]] = []
    with seed_path.open(encoding="utf-8") as f:
        for line_num, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                ex = json.loads(line)
            except json.JSONDecodeError as e:
                sys.stderr.write(
                    f"FAIL: {seed_path}:{line_num} is not valid JSON: {e}\n"
                )
                return 1
            if "inputs" not in ex:
                sys.stderr.write(
                    f"FAIL: {seed_path}:{line_num} missing required 'inputs' key\n"
                )
                return 1
            examples.append(ex)

    if not examples:
        sys.stderr.write(f"Seed file {seed_path} is empty.\n")
        return 1

    dataset_name = f"condomanager-triage-seed-{args.env}"
    try:
        dataset = ensure_dataset(
            dataset_name,
            examples,
            description=(
                f"CM-23 seed for the Triage Agent eval ({args.env}). "
                "Expanded to 200 examples by CM-30."
            ),
        )
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"FAIL: ensure_dataset raised: {e}\n")
        return 1

    print(f"OK   dataset '{dataset_name}' is in sync ({len(examples)} examples in source)")
    print(f"     LangSmith dataset id: {dataset.id}")
    return 0


if __name__ == "__main__":  # pragma: no cover  (manual operator run)
    sys.exit(main())
