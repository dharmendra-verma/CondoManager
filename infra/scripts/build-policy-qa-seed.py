#!/usr/bin/env python3
"""Convert the policy Q&A CSV into the Knowledge-eval JSONL seed (CM-91).

Jira: CM-91  | Epic: Knowledge / RAG eval

Reads ``tests/eval/policy_qa_dataset.csv`` (columns: id, policy, category,
question, expected_answer, difficulty, question_type) and writes
``tests/eval/policy_qa_seed.jsonl`` in the shape the Knowledge eval already
consumes (see ``agents/eval/datasets.py`` + ``agents/eval/suite.py``):

    {"inputs": {"message", "tenant_id", "policy", "category", "id"},
     "outputs": {"answerable", "gold_doc_id", "gold_chunk_text", "expected_answer",
                 "difficulty", "question_type", "multi_hop"}}

``gold_chunk_text`` is the ``expected_answer`` so the offline ``LexicalRetriever``
retrieves it; ``multi_hop`` flags the cross-policy rows (``question_type`` ==
``multi_policy``). Deterministic — re-run after editing the CSV to regenerate the
committed seed.

Usage::

    python infra/scripts/build-policy-qa-seed.py
    python infra/scripts/build-policy-qa-seed.py --csv <path> --out <path>
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CSV = _REPO_ROOT / "tests" / "eval" / "policy_qa_dataset.csv"
_DEFAULT_OUT = _REPO_ROOT / "tests" / "eval" / "policy_qa_seed.jsonl"

_TENANT = "t-eval"


def _slug(text: str) -> str:
    """Slugify a policy name into a stable ``gold_doc_id`` (e.g. ``pet-policy``)."""
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-") or "policy"


def _row_to_example(row: dict[str, str]) -> dict[str, object]:
    expected = row["expected_answer"].strip()
    question_type = row.get("question_type", "").strip()
    return {
        "inputs": {
            "message": row["question"].strip(),
            "tenant_id": _TENANT,
            "policy": row["policy"].strip(),
            "category": row.get("category", "").strip(),
            "id": row.get("id", "").strip(),
        },
        "outputs": {
            "answerable": True,
            "gold_doc_id": _slug(row["policy"]),
            "gold_chunk_text": expected,
            "expected_answer": expected,
            "difficulty": row.get("difficulty", "").strip(),
            "question_type": question_type,
            "multi_hop": question_type == "multi_policy",
        },
    }


def build(csv_path: Path, out_path: Path) -> int:
    if not csv_path.is_file():
        sys.stderr.write(f"CSV not found: {csv_path}\n")
        return 1
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    lines = [json.dumps(_row_to_example(r), ensure_ascii=False) for r in rows]
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {len(lines)} examples -> {out_path}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Build the policy-QA Knowledge-eval seed.")
    p.add_argument("--csv", default=str(_DEFAULT_CSV), help="Source CSV path.")
    p.add_argument("--out", default=str(_DEFAULT_OUT), help="Output JSONL path.")
    args = p.parse_args()
    return build(Path(args.csv), Path(args.out))


if __name__ == "__main__":  # pragma: no cover  (manual / build-time script)
    sys.exit(main())
