#!/usr/bin/env python3
"""Seed the LangSmith eval datasets from tests/eval/*_seed.jsonl.

Jira: CM-23 (triage) | CM-45 (generalized to all 5 agents) | Epic: Observability

Operator-run, post-deploy. Reads the JSONL seed fixtures under tests/eval/ and
uploads each to LangSmith as a dataset named ``condomanager-<name>-seed-<env>``.
By default it seeds all five agent datasets; ``--only <name>`` restricts to one.
Idempotent: ``ensure_dataset`` skips example uploads whose ``inputs`` fingerprint
already exists and skips dataset creation if the dataset already exists.

Usage::

    # Prereqs:
    #   - LANGCHAIN_API_KEY set to a real key (populate KV first; then
    #     export from a local terminal)
    export LANGCHAIN_API_KEY=<key>
    python infra/scripts/seed-langsmith-dataset.py --env dev            # all 5
    python infra/scripts/seed-langsmith-dataset.py --env dev --only knowledge

    # Re-runs are safe (no duplicate examples, no duplicate datasets).

Exit codes: 0 = success, 1 = misconfiguration or any upload failure.
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

_EVAL_DIR = _REPO_ROOT / "tests" / "eval"

#: Per-agent golden datasets. Key = dataset slug (becomes
#: ``condomanager-<key>-seed-<env>``); value = the JSONL fixture under tests/eval/.
#: Keep in sync with the offline eval suites (tests/eval/, agents/eval/).
DATASETS: dict[str, str] = {
    "triage": "triage_seed.jsonl",
    "maintenance-dedup": "maintenance_dedup_seed.jsonl",
    "escalation": "escalation_seed.jsonl",
    "knowledge": "knowledge_seed.jsonl",
    "vendor-match": "vendor_match_seed.jsonl",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Seed the LangSmith eval datasets (CM-23/CM-45).")
    p.add_argument(
        "--env",
        choices=("dev", "prod"),
        required=True,
        help="Environment label. Becomes the suffix on each dataset name.",
    )
    p.add_argument(
        "--only",
        choices=tuple(DATASETS),
        default=None,
        help="Seed just one dataset (default: all five).",
    )
    return p.parse_args()


def _load_examples(seed_path: Path) -> list[dict[str, object]]:
    """Parse + validate a JSONL seed file into a list of LangSmith examples.

    Raises ``ValueError`` with a file:line context on malformed/empty input so
    the caller can report which dataset failed and keep going / exit non-zero.
    """
    if not seed_path.is_file():
        raise ValueError(f"seed file not found: {seed_path}")
    examples: list[dict[str, object]] = []
    with seed_path.open(encoding="utf-8") as f:
        for line_num, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                ex = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{seed_path}:{line_num} is not valid JSON: {e}") from e
            if "inputs" not in ex:
                raise ValueError(f"{seed_path}:{line_num} missing required 'inputs' key")
            examples.append(ex)
    if not examples:
        raise ValueError(f"seed file {seed_path} is empty")
    return examples


def _seed_one(name: str, env: str) -> bool:
    """Seed a single dataset. Returns True on success, False on failure."""
    seed_path = _EVAL_DIR / DATASETS[name]
    try:
        examples = _load_examples(seed_path)
    except ValueError as e:
        sys.stderr.write(f"FAIL [{name}]: {e}\n")
        return False

    dataset_name = f"condomanager-{name}-seed-{env}"
    try:
        dataset = ensure_dataset(
            dataset_name,
            examples,
            description=f"CondoManager {name} eval seed ({env}) — source: tests/eval/{DATASETS[name]}.",
        )
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"FAIL [{name}]: ensure_dataset raised: {e}\n")
        return False

    print(f"OK   '{dataset_name}' in sync ({len(examples)} examples) — id {dataset.id}")
    return True


def main() -> int:
    args = parse_args()

    # Same env-var preflight as the demo, but only the key matters here. Tracing
    # V2 isn't needed because this script writes to the LangSmith API directly.
    key = os.environ.get("LANGCHAIN_API_KEY", "").strip()
    if not key or key == SECRET_PLACEHOLDER:
        sys.stderr.write(
            "LANGCHAIN_API_KEY is missing or holds REPLACE-ME. Populate the "
            "KV secret first (see docs/OBSERVABILITY.md), export the value "
            "locally, and re-run.\n"
        )
        return 1

    names = [args.only] if args.only else list(DATASETS)
    results = [_seed_one(name, args.env) for name in names]
    failed = results.count(False)
    print(f"\n{len(results) - failed}/{len(results)} datasets seeded for env={args.env}")
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover  (manual operator run)
    sys.exit(main())
